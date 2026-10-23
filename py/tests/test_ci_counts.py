"""CI's counts check against main (ft/ci_counts.py; CLAUDE.md §2), with the GitHub API faked."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from ft import ci_counts
from ft.ci_counts import GitHub

OWNER = "anandbenny-spec"
BASE, PARENT, HEAD, MERGE = "b" * 40, "p" * 40, "h" * 40, "m" * 40


class FakeGitHub(GitHub):
    """Routes path -> response (or a callable of the query); the real `paged` runs on top of `get`."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(path)
        if path not in self.routes:
            raise LookupError(f"404 {path}")
        route = self.routes[path]
        return route(params) if callable(route) else route


def notice(title: str, message: str, level: str = "notice") -> dict[str, Any]:
    return {"annotation_level": level, "title": title, "message": message}


def job(job_id: int, conclusion: str | None = "success", status: str = "completed", app: str = "github-actions"):
    return {"id": job_id, "status": status, "conclusion": conclusion, "completed_at": f"2026-10-24T00:00:{job_id:02d}Z",
            "app": {"slug": app}}


def baseline_routes(commit: str = BASE, sql: int = 84, py: int = 507) -> dict[str, Any]:
    return {
        f"commits/{commit}/check-runs": {"check_runs": [job(7)]},
        "check-runs/7/annotations": [
            notice("pytest", "tests/test_x.py::test_y - AssertionError", level="failure"),
            notice("pytest", f"{py} passed, 8 skipped in 31.29s"),
            notice("SQL tests", f"passed {sql}, failed 0"),
        ],
    }


def label_event(event: str = "labeled", login: str = OWNER, app: str | None = None, name: str = ci_counts.LABEL):
    return {"event": event, "label": {"name": name}, "actor": {"login": login},
            "performed_via_github_app": {"slug": app} if app else None}


def run(tmp_path, routes, event_name="pull_request", event=None, sql="passed 84, failed 0",
        pytest_line="515 passed, 8 skipped in 33.10s", ref="refs/pull/48/merge", sha=MERGE):
    if event is None:
        event = {"pull_request": {"number": 48, "base": {"sha": BASE}}}
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    env = {"GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(path), "GITHUB_SHA": sha, "GITHUB_REF": ref,
           "GITHUB_REPOSITORY_OWNER": OWNER, "SQL_SUMMARY": sql, "PYTEST_SUMMARY": pytest_line}
    out = io.StringIO()
    code = ci_counts.main(env, gh=FakeGitHub(routes), out=out, wait=0, sleep=lambda s: None)
    return code, out.getvalue()


def test_parsers_read_the_annotated_summaries():
    assert ci_counts.sql_passed("passed 84, failed 0") == 84
    assert ci_counts.sql_passed("passed 84") is None
    assert ci_counts.pytest_passed("507 passed, 8 skipped in 31.29s") == 507
    assert ci_counts.pytest_passed("1 failed, 506 passed in 3.00s") == 506
    assert ci_counts.pytest_passed("8 skipped in 1.00s") is None


def test_counts_come_from_notices_only():
    counts = ci_counts.counts_from_annotations(baseline_routes()["check-runs/7/annotations"])
    assert counts == {"SQL": 84, "pytest": 507}
    assert ci_counts.counts_from_annotations([notice("pytest", "507 passed", level="failure")]) == {
        "SQL": None, "pytest": None}


def test_baseline_commit_per_event():
    gh = FakeGitHub({f"commits/{HEAD}": {"parents": [{"sha": PARENT}, {"sha": "x" * 40}]},
                     f"compare/main...{HEAD}": {"merge_base_commit": {"sha": BASE}}})
    pr = {"pull_request": {"base": {"sha": BASE}}}
    assert ci_counts.baseline_commit(gh, "pull_request", pr, MERGE, "refs/pull/48/merge") == BASE
    assert ci_counts.baseline_commit(gh, "push", {}, HEAD, "refs/heads/main") == PARENT  # first parent
    assert ci_counts.baseline_commit(gh, "workflow_dispatch", {}, HEAD, "refs/heads/main") == PARENT
    assert ci_counts.baseline_commit(gh, "workflow_dispatch", {}, HEAD, "refs/heads/claude/x") == BASE
    with pytest.raises(LookupError):
        ci_counts.baseline_commit(FakeGitHub({f"commits/{HEAD}": {"parents": []}}), "push", {}, HEAD, "")


def test_baseline_skips_failed_jobs_other_apps_and_jobs_without_both_counts():
    routes = {
        f"commits/{BASE}/check-runs": {"check_runs": [
            job(1, "failure"), job(2, app="someone-else"), job(3), job(4)]},
        "check-runs/4/annotations": [notice("SQL tests", "passed 90, failed 0")],  # newest, but no pytest notice
        "check-runs/3/annotations": baseline_routes()["check-runs/7/annotations"],
    }
    gh = FakeGitHub(routes)
    assert ci_counts.baseline_counts(gh, BASE, wait=0) == {"SQL": 84, "pytest": 507}
    assert "check-runs/1/annotations" not in gh.calls and "check-runs/2/annotations" not in gh.calls


def test_baseline_waits_for_a_running_job_then_gives_up():
    polls = iter([[job(7, None, "in_progress")], [job(7)]])
    routes = baseline_routes() | {f"commits/{BASE}/check-runs": lambda q: {"check_runs": next(polls)}}
    slept: list[float] = []
    assert ci_counts.baseline_counts(FakeGitHub(routes), BASE, wait=60, poll=20, sleep=slept.append) == {
        "SQL": 84, "pytest": 507}
    assert slept == [20]

    clock = iter([0.0, 30.0, 61.0])
    routes = {f"commits/{BASE}/check-runs": {"check_runs": [job(7, None, "queued")]}}
    assert ci_counts.baseline_counts(FakeGitHub(routes), BASE, wait=60, poll=20, sleep=slept.append,
                                     clock=lambda: next(clock)) is None
    assert ci_counts.baseline_counts(FakeGitHub({f"commits/{BASE}/check-runs": {"check_runs": []}}), BASE) is None


def test_paging_follows_full_pages():
    pages = {1: [{"n": i} for i in range(100)], 2: [{"n": 100}]}
    gh = FakeGitHub({"issues/48/events": lambda q: pages[q["page"]]})
    assert len(list(gh.paged("issues/48/events"))) == 101


@pytest.mark.parametrize(("events", "ok"), [
    ([label_event()], True),
    ([label_event(app="claude")], False),                                   # added through Claude's app
    ([label_event(login="someone-else")], False),
    ([label_event(), label_event("unlabeled")], False),                     # since removed
    ([label_event(app="claude"), label_event("unlabeled"), label_event()], True),
    ([label_event(name="in-progress")], False),
    ([], False),
])
def test_only_the_owner_adding_the_label_in_person_approves(events, ok):
    assert ci_counts.approved_by_owner(FakeGitHub({"issues/48/events": events}), 48, OWNER) is ok


def test_equal_or_higher_counts_pass(tmp_path):
    code, out = run(tmp_path, baseline_routes())
    assert code == 0
    assert out == "::notice title=Counts vs main::SQL 84 (main 84), pytest 515 (main 507)\n"


def test_a_lower_count_fails_with_annotations(tmp_path):
    code, out = run(tmp_path, baseline_routes() | {"issues/48/events": []}, sql="passed 83, failed 0")
    assert code == 1
    assert "::error title=Counts vs main::SQL pass count fell from 84 on main (bbbbbbb) to 83\n" in out
    assert "only counts-drop-approved, added by him (anandbenny-spec) to #48, overrides this" in out


def test_the_owners_label_overrides_a_drop(tmp_path):
    code, out = run(tmp_path, baseline_routes() | {"issues/48/events": [label_event()]}, pytest_line="500 passed")
    assert code == 0
    assert out.startswith("::warning title=Counts vs main::pytest pass count fell from 507")


def test_a_label_added_through_claude_does_not_override(tmp_path):
    routes = baseline_routes() | {"issues/48/events": [label_event(app="claude")]}
    assert run(tmp_path, routes, pytest_line="500 passed")[0] == 1


def test_no_baseline_fails(tmp_path):
    code, out = run(tmp_path, {f"commits/{BASE}/check-runs": {"check_runs": []}, "issues/48/events": []})
    assert code == 1
    assert "no baseline: no successful ci test job with both counts on main's bbbbbbb" in out
    code, out = run(tmp_path, {"issues/48/events": []})  # the API call itself fails
    assert code == 1 and "(LookupError: 404 commits/" in out


def test_an_unreadable_count_on_this_run_fails(tmp_path):
    code, out = run(tmp_path, baseline_routes() | {"issues/48/events": []}, pytest_line="no tests ran")
    assert code == 1 and "couldn't read this run's pytest pass count" in out


def test_push_to_main_uses_the_parent_and_the_merged_pull_requests_label(tmp_path):
    routes = baseline_routes(PARENT) | {
        f"commits/{HEAD}": {"parents": [{"sha": PARENT}]},
        f"commits/{HEAD}/pulls": [{"number": 47, "merge_commit_sha": "z" * 40}, {"number": 48, "merge_commit_sha": HEAD}],
        "issues/48/events": [label_event()],
    }
    kwargs = {"event_name": "push", "event": {}, "ref": "refs/heads/main", "sha": HEAD}
    assert run(tmp_path, routes, **kwargs)[0] == 0
    assert run(tmp_path, routes, pytest_line="500 passed", **kwargs)[0] == 0  # approved on #48
    routes["issues/48/events"] = []
    code, out = run(tmp_path, routes, pytest_line="500 passed", **kwargs)
    assert code == 1 and "fell from 507 on main (ppppppp) to 500" in out
