"""CI's counts check (CLAUDE.md §2): neither the SQL nor the pytest pass count may fall below main's.

Main's counts are read from the annotations of ci's successful `test` job on the baseline commit: a pull request's
base commit, a push to main's parent, and for `workflow_dispatch` the merge base with main (the parent, on main).
A lower count, or no baseline to read, fails the job with an annotation. The only override is the
`counts-drop-approved` label, added to the pull request by Anand himself, since dropping tests is his call
(CLAUDE.md §7): a label added through Claude's app carries the app on its event and doesn't count.

Standard library only. The test job runs `python -m ft.ci_counts` with its token (contents, checks and
pull-requests read) and the two summaries its test steps annotated.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TextIO

LABEL = "counts-drop-approved"
CHECK_NAME = "test"  # ci.yml's job, whose notices carry the counts
TITLE = "Counts vs main"
WAIT_SECONDS = 480  # a baseline job still running (main has just moved) gets this long to finish
POLL_SECONDS = 20


def sql_passed(summary: str) -> int | None:
    """The pass count from the SQL step's notice, "passed 84, failed 0"."""
    m = re.fullmatch(r"passed (\d+), failed \d+", summary.strip())
    return int(m.group(1)) if m else None


def pytest_passed(summary: str) -> int | None:
    """The pass count from pytest's last line, "507 passed, 8 skipped in 31.29s"."""
    m = re.search(r"\b(\d+) passed\b", summary)
    return int(m.group(1)) if m else None


# Notice title in ci.yml -> (count name, parser)
NOTICES: dict[str, tuple[str, Callable[[str], int | None]]] = {
    "SQL tests": ("SQL", sql_passed),
    "pytest": ("pytest", pytest_passed),
}


def counts_from_annotations(annotations: Iterable[dict[str, Any]]) -> dict[str, int | None]:
    counts: dict[str, int | None] = {name: None for name, _ in NOTICES.values()}
    for a in annotations:
        if a.get("annotation_level") == "notice" and a.get("title") in NOTICES:
            name, parse = NOTICES[a["title"]]
            counts[name] = parse(a.get("message") or "")
    return counts


class GitHub:
    """The few REST calls the check makes, relative to /repos/{owner}/{repo}/."""

    def __init__(self, api_url: str, repo: str, token: str) -> None:
        self.base = f"{api_url.rstrip('/')}/repos/{repo}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get(self, path: str, **params: Any) -> Any:
        url = f"{self.base}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers=self.headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == 2:
                    raise
            except urllib.error.URLError:
                if attempt == 2:
                    raise
            time.sleep(5)
        raise AssertionError("unreachable")

    def paged(self, path: str, key: str | None = None, **params: Any) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            data = self.get(path, per_page=100, page=page, **params)
            items = data[key] if key else data
            yield from items
            if len(items) < 100:
                return
            page += 1


def baseline_commit(gh: GitHub, event_name: str, event: dict[str, Any], sha: str, ref: str) -> str:
    if event_name == "pull_request":
        return event["pull_request"]["base"]["sha"]
    if event_name == "push" or ref == "refs/heads/main":
        parents = gh.get(f"commits/{sha}")["parents"]
        if not parents:
            raise LookupError(f"{sha[:7]} has no parent")
        return parents[0]["sha"]
    return gh.get(f"compare/main...{sha}")["merge_base_commit"]["sha"]


def baseline_counts(
    gh: GitHub,
    commit: str,
    wait: float = WAIT_SECONDS,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, int | None] | None:
    """Both counts from the latest successful `test` job on `commit` that annotated them, or None."""
    deadline = clock() + wait
    while True:
        jobs = [
            j
            for j in gh.paged(f"commits/{commit}/check-runs", key="check_runs", check_name=CHECK_NAME)
            if (j.get("app") or {}).get("slug") == "github-actions"
        ]
        passed = [j for j in jobs if j.get("conclusion") == "success"]
        for job in sorted(passed, key=lambda j: j.get("completed_at") or "", reverse=True):
            counts = counts_from_annotations(gh.paged(f"check-runs/{job['id']}/annotations"))
            if all(n is not None for n in counts.values()):
                return counts
        if all(j.get("status") == "completed" for j in jobs) or clock() >= deadline:
            return None
        sleep(poll)


def pull_request_number(gh: GitHub, event_name: str, event: dict[str, Any], sha: str) -> int | None:
    if event_name == "pull_request":
        return event["pull_request"]["number"]
    if event_name == "push":  # the pull request this merge commit came from
        for pr in gh.get(f"commits/{sha}/pulls"):
            if pr.get("merge_commit_sha") == sha:
                return pr["number"]
    return None


def approved_by_owner(gh: GitHub, number: int, owner: str) -> bool:
    """The label's latest event is its being added by the owner in person, not through an app."""
    last = None
    for e in gh.paged(f"issues/{number}/events"):
        if e.get("event") in ("labeled", "unlabeled") and (e.get("label") or {}).get("name") == LABEL:
            last = e
    return (
        last is not None
        and last["event"] == "labeled"
        and ((last.get("actor") or {}).get("login") or "").casefold() == owner.casefold()
        and not last.get("performed_via_github_app")
    )


def main(env: dict[str, str] | None = None, gh: GitHub | None = None, out: TextIO = sys.stdout, **wait: Any) -> int:
    env = dict(os.environ) if env is None else env
    event_name, sha, owner = env["GITHUB_EVENT_NAME"], env["GITHUB_SHA"], env["GITHUB_REPOSITORY_OWNER"]
    with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    if gh is None:
        gh = GitHub(env.get("GITHUB_API_URL", "https://api.github.com"), env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"])

    current = {"SQL": sql_passed(env.get("SQL_SUMMARY", "")), "pytest": pytest_passed(env.get("PYTEST_SUMMARY", ""))}
    problems = [f"couldn't read this run's {name} pass count" for name, n in current.items() if n is None]

    commit, baseline, why = "", None, ""
    try:
        commit = baseline_commit(gh, event_name, event, sha, env.get("GITHUB_REF", ""))
        baseline = baseline_counts(gh, commit, **wait)
    except (OSError, LookupError, ValueError) as e:  # HTTP and network errors are OSErrors
        why = f" ({type(e).__name__}: {e})"
    if baseline is None:
        where = f"main's {commit[:7]}" if commit else "main"
        problems.append(f"no baseline: no successful ci test job with both counts on {where}{why}")
    else:
        for name, n in current.items():
            b = baseline[name]
            if n is not None and b is not None and n < b:
                problems.append(f"{name} pass count fell from {b} on main ({commit[:7]}) to {n}")

    def annotate(level: str, message: str) -> None:
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level} title={TITLE}::{message}", file=out)

    summary = ", ".join(f"{name} {current[name]} (main {baseline[name] if baseline else '?'})" for name in current)
    if not problems:
        annotate("notice", summary)
        return 0

    number, note = None, ""
    try:
        number = pull_request_number(gh, event_name, event, sha)
        if number is not None and approved_by_owner(gh, number, owner):
            annotate("warning", f"{'; '.join(problems)}. Approved: {owner} added {LABEL} to #{number}. {summary}")
            return 0
    except (OSError, LookupError, ValueError) as e:
        note = f" (couldn't read the label: {type(e).__name__}: {e})"
    for p in problems:
        annotate("error", p)
    target = f"#{number}" if number is not None else "the pull request"
    annotate("error", f"Dropping tests is Anand's call (CLAUDE.md §7): only {LABEL}, added by him ({owner}) to "
             f"{target}, overrides this; then re-run this job{note}. {summary}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
