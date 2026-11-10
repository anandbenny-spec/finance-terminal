"""equity.calls_ai's summary scope (#370; Review 02c F3-04): a run that names no window (`daily`, `weekly`, `watch`, `refresh`)
asks for the `call_summary` of each company's newest stored call only, so a new prompt version can't make an unattended run
re-summarise the history; a backfill that names a window asks for every call in it, as before. The guidance extraction and
the tone targets don't change. Against the test database as job_writer, the Batch API faked (tests/ai_fakes.py); the helpers
are test_equity_calls_ai_task.py's, with the summary on the citations route (v5) for the runs, as that file's own tests.

The fixture: company A has three stored calls (Q4 FY26, Q1 FY27 and Q2 FY27, filed in that order); company B has two, its
Q2 FY27 call filed on 15 Oct and its Q1 FY27 call filed late, on 25 Oct. The newest are A's Q2 FY27 and B's Q2 FY27: by the
quarter, as the site orders a company's calls, never by the filing date."""
from datetime import date

import psycopg
import pytest
from ai_fakes import FakeClaude
from test_equity_calls_ai_task import (
    JOB,
    KEY,
    Ticker,
    answers,
    go,
    gx,
    last_run,
    make_call,
    outputs,
    q,
    replies,
    requests,
    task,
    wipe,
)

from ft.ai import call_summary as cs
from ft.ai import call_tone as ct
from ft.ai import guidance_extraction as ge
from ft.jobs.tasks import equity_calls_ai as eca

CURRENT = eca.PROMPT_VERSION[cs.TASK]          # v9 on main; whatever the current version is on a branch that changes it


@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '2B-15b test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


@pytest.fixture(scope="module", autouse=True)
def clean():
    wipe()
    yield
    wipe()


@pytest.fixture(autouse=True)
def citations_route(monkeypatch):
    """The runs here answer the summary on the citations route (v5), as test_equity_calls_ai_task.py's; the scope rule doesn't
    read the version (`test_the_targets_are_the_same_whatever_the_summary_version` checks it for the current one too)."""
    monkeypatch.setattr(eca, "PROMPT_VERSION", {**eca.PROMPT_VERSION, cs.TASK: "v5"})


def two_companies(**kw) -> dict:
    """The module docstring's five calls (fiscal year ending in March, so each is extracted too): name -> make_call's dict."""
    a1 = make_call(filed=date(2026, 5, 12), quarter=("Q4 FY26", date(2026, 3, 31)), fy_end=3, **kw)
    a2 = make_call(filed=date(2026, 8, 6), quarter=("Q1 FY27", date(2026, 6, 30)), company=a1["entity"], **kw)
    a3 = make_call(filed=date(2026, 10, 20), company=a1["entity"], **kw)
    b1 = make_call(filed=date(2026, 10, 15), fy_end=3, **kw)
    b2 = make_call(filed=date(2026, 10, 25), quarter=("Q1 FY27", date(2026, 6, 30)), company=b1["entity"], **kw)
    return {"a1": a1, "a2": a2, "a3": a3, "b1": b1, "b2": b2}


def slugs(calls: dict) -> list[str]:
    return [c["slug"] for c in calls.values()]


def asked(calls: dict, task_key: str = cs.TASK) -> list[str]:
    """The calls (by name) with a request of the task stored, in name order."""
    return [n for n, c in sorted(calls.items())
            if q("select count(*) from ops.ai_request where document_id = %s and ai_task = %s", c["doc"], task_key)[0][0]]


def targets(calls: dict, **kw) -> dict[str, list[str]]:
    """`_calls` itself, scoped to the fixture's calls: task -> the calls (by name) it would make a target of."""
    t = task(slugs(calls), FakeClaude())
    name = {c["doc"]: n for n, c in calls.items()}
    with psycopg.connect(JOB, autocommit=True) as db:
        works = t._calls(db, **kw)
    out: dict[str, list[str]] = {}
    for w in works:
        out.setdefault(w.task, []).append(name[w.document_id])
    return {k: sorted(v) for k, v in out.items()}


def test_a_run_that_names_no_window_asks_for_the_newest_calls_and_one_that_names_one_for_every_call():
    assert eca.summary_scope(None, None) == eca.NEWEST                       # daily, weekly, watch, refresh
    assert eca.summary_scope(date(2019, 1, 1), None) == eca.ALL               # the backfill workflow always sends --since
    assert eca.summary_scope(None, date(2026, 11, 1)) == eca.ALL
    assert eca.summary_scope(date(2026, 10, 1), date(2026, 10, 31)) == eca.ALL


@pytest.mark.db
def test_a_weekly_asks_for_one_summary_a_company_and_extracts_every_call_as_before():
    calls = two_companies()
    fake = FakeClaude(["ok"], reply=replies(), answer=answers())
    assert go(task(slugs(calls), fake), workflow="weekly") == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 1 and len(next(iter(fake.batches.values()))["requests"]) == 7      # 2 summaries, 5 extractions
    assert asked(calls) == ["a3", "b1"]                                     # B's newest by quarter, though its Q1 was filed later
    assert asked(calls, ge.TASK) == ["a1", "a2", "a3", "b1", "b2"]          # the extraction's targeting is unchanged
    assert [g[0] for g in outputs(calls["a3"]["doc"])] == ["published"] and outputs(calls["a1"]["doc"]) == []
    detail = r["checks"]["call_summaries"]["detail"]
    assert "2 calls lacked a published summary" in detail and "each company's newest call only" in detail
    # the newest calls have a summary of the current version: the next weekly asks for none, though three older calls lack one
    assert go(task(slugs(calls), fake), workflow="weekly") == 0
    assert fake.create_calls == 1 and last_run()["rows"] == 0
    assert "0 calls lacked a published summary" in last_run()["checks"]["call_summaries"]["detail"]
    assert asked(calls) == ["a3", "b1"] and [len(gx(c["doc"])) for c in calls.values()] == [1] * 5


@pytest.mark.db
@pytest.mark.parametrize("workflow", ["daily", "refresh"])
def test_every_run_without_a_window_is_held_to_the_newest_calls(workflow, monkeypatch):
    monkeypatch.setenv("CALLS_AI_TASK", cs.TASK)
    calls = two_companies()
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task(slugs(calls), fake), workflow=workflow) == 0
    assert last_run()["status"] == "success" and asked(calls) == ["a3", "b1"]
    assert go(task(slugs(calls), fake), workflow="backfill") == 0          # a backfill with neither --since nor --until: the same
    assert fake.create_calls == 1 and asked(calls) == ["a3", "b1"]


@pytest.mark.db
def test_a_backfill_that_names_a_window_still_asks_for_every_call_filed_in_it(monkeypatch):
    monkeypatch.setenv("CALLS_AI_TASK", cs.TASK)
    calls = two_companies()
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task(slugs(calls), fake), since=date(2026, 8, 1), until=date(2026, 10, 31)) == 0
    r = last_run()
    assert r["status"] == "success" and len(next(iter(fake.batches.values()))["requests"]) == 4
    assert asked(calls) == ["a2", "a3", "b1", "b2"]                         # A's Q4 FY26 was filed before the window
    assert all([g[0] for g in outputs(calls[n]["doc"])] == ["published"] for n in ("a2", "a3", "b1", "b2"))
    assert "every call in the run's window" in r["checks"]["call_summaries"]["detail"]
    assert go(task(slugs(calls), fake), since=date(2026, 5, 1)) == 0        # --since alone is a window too: A's Q4 FY26 now
    assert asked(calls) == ["a1", "a2", "a3", "b1", "b2"]


@pytest.mark.db
def test_a_backfills_batch_for_an_older_call_is_still_collected_by_the_next_weekly(monkeypatch):
    monkeypatch.setenv("CALLS_AI_TASK", cs.TASK)
    calls = two_companies()
    fake, tick = FakeClaude(["ok"], reply=replies(), ends_after=None), Ticker()
    assert go(task(slugs(calls), fake, tick), since=date(2026, 5, 12), until=date(2026, 5, 12)) == 0
    assert requests(calls["a1"]["doc"]) == [("pending", "", False, True)] and outputs(calls["a1"]["doc"]) == []
    fake.ends_after = 1
    assert go(task(slugs(calls), fake), workflow="weekly") == 0
    assert last_run()["status"] == "success" and fake.create_calls == 2      # the old batch read, the newest calls sent
    assert [g[0] for g in outputs(calls["a1"]["doc"])] == ["published"]      # the late result found its target and was stored
    assert asked(calls) == ["a1", "a3", "b1"]


@pytest.mark.db
def test_the_newest_call_is_one_a_visitor_can_see(monkeypatch):
    monkeypatch.setenv("CALLS_AI_TASK", cs.TASK)
    older = make_call(filed=date(2026, 8, 6), quarter=("Q1 FY27", date(2026, 6, 30)))
    hidden = make_call(filed=date(2026, 10, 20), company=older["entity"], publish_doc=False)    # its transcript not published
    calls = {"older": older, "hidden": hidden}
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task(slugs(calls), fake), workflow="weekly") == 0
    assert asked(calls) == ["older"]                                         # the call the site opens on, as it stands
    x_publish(hidden["doc"])
    assert go(task(slugs(calls), fake), workflow="weekly") == 0              # published now: it is the newest, and asked for
    assert asked(calls) == ["hidden", "older"]


def x_publish(doc_id: int) -> None:
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("update core.document set is_published = true where id = %s", (doc_id,))


@pytest.mark.db
@pytest.mark.parametrize("version", ["v5", CURRENT])
def test_the_targets_are_the_same_whatever_the_summary_version(version, monkeypatch):
    monkeypatch.setattr(eca, "PROMPT_VERSION", {**eca.PROMPT_VERSION, cs.TASK: version})
    calls = two_companies()
    every = targets(calls, tone=True)
    newest = targets(calls, tone=True, newest_only=True)
    assert every[cs.TASK] == ["a1", "a2", "a3", "b1", "b2"] and newest[cs.TASK] == ["a3", "b1"]
    for other in (ge.TASK, ct.TASK):                                         # extraction and tone: as before, every call
        assert newest.get(other) == every.get(other)
    assert newest[ge.TASK] == ["a1", "a2", "a3", "b1", "b2"]
    # a stored result is looked up with `lacking` off: every call is a target, so the scope never makes one stale
    assert targets(calls, lacking=False, newest_only=True)[cs.TASK] == ["a1", "a2", "a3", "b1", "b2"]
