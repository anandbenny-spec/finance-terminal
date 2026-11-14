"""The re-run of the calls whose turns changed (#554, 2B-09c; #542's step 8): `CALLS_AI_TURNS_RERUN` on a backfill limited to one
task asks again, at the task's current prompt version, for each call whose published output was made from another input than the
one its turns give now, and for no other call. `price` chooses and prices the same requests and sends nothing. A reply supersedes
the published output only when it passes every check; otherwise the old one stays published.

The change these tests make is #551's (an inline "Management:" inside an analyst's turn opens management's own turn, the text
unchanged): the call's `equity.call_turn` rows are rewritten as the re-derive writes them. All text is synthetic. The helpers are
test_equity_calls_ai_task.py's and test_equity_calls_ai_tone.py's; the rules without a database are in
test_turns_rerun_rules.py."""
import json

import psycopg
import pytest
from ai_fakes import FakeClaude
from test_equity_calls_ai_task import (
    ADMIN,
    CAPEX_T,
    JOB,
    KEY,
    OUTLOOK_T,
    REV_S,
    answers,
    citations_route,  # noqa: F401  (autouse: the summary on the citations route, as that module's tests drive it)
    go,
    grows,
    gx,
    last_run,
    make_call,
    outputs,
    q,
    replies,
    task,
    wipe,
)
from test_equity_calls_ai_tone import LONG, UP, rechunk, tone_answer, tone_outputs
from test_turns_rerun_rules import ENV, LABEL, MERGED

from ft.ai import call_summary as cs
from ft.ai import call_tone as ct
from ft.ai import guidance_extraction as ge
from ft.jobs.tasks import equity_calls_ai as eca


# ---------------------------------------------------------------------------------------------- the task (test database)
@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '#554 test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


@pytest.fixture(scope="module", autouse=True)
def clean():
    wipe()
    yield
    wipe()


def split_turn(c: dict, at: str = LABEL, role: str = "management", speaker: str = "Management") -> None:
    """#551's change in the database, as the re-derive writes it: the call's turns rewritten, the turn that holds `at` cut at its
    offset and the second part a turn of `role`; the text and its chunks unchanged."""
    text = c["built"].text
    cut = text.index(at)
    rows = q("""select char_start, char_end, speaker, role, section from equity.call_turn where document_id = %s
                 order by turn_no""", c["doc"])
    new = []
    for a, b, sp, r, sec in rows:
        new += [(a, cut, sp, r, sec), (cut, b, speaker, role, sec)] if a < cut < b else [(a, b, sp, r, sec)]
    assert len(new) == len(rows) + 1
    set_turns(c, new)


def set_role(c: dict, speaker: str, role: str) -> None:
    """A role change on the speaker's turns (the re-derive's other kind of change), the turns' offsets unchanged."""
    rows = q("""select char_start, char_end, speaker, role, section from equity.call_turn where document_id = %s
                 order by turn_no""", c["doc"])
    set_turns(c, [(a, b, sp, role if sp == speaker else r, sec) for a, b, sp, r, sec in rows])


def set_turns(c: dict, turns: list[tuple]) -> None:
    text = c["built"].text
    with psycopg.connect(ADMIN, autocommit=True) as conn, conn.transaction():
        conn.execute("delete from equity.call_turn where document_id = %s", (c["doc"],))
        for no, (a, b, sp, r, sec) in enumerate(turns):
            conn.execute("""insert into equity.call_turn (document_id, turn_no, char_start, char_end, speaker, role, section, words)
                            values (%s, %s, %s, %s, %s, %s, %s, %s)""", (c["doc"], no, a, b, sp, r, sec, len(text[a:b].split())))


def extract(monkeypatch, *calls, script=("ok",)) -> None:
    """A published v1 extraction on each call, from a run limited to the extraction. On a merged call v1 drops OUTLOOK_T: its quote
    lies in an analyst's turn (Spec 02B.1 C3)."""
    with monkeypatch.context() as m:
        m.setenv(eca.ONLY_ENV, ge.TASK)
        assert go(task([c["slug"] for c in calls], FakeClaude(list(script), answer=answers([CAPEX_T, OUTLOOK_T], [REV_S])))) == 0
    assert last_run()["status"] == "success", last_run()["error"]
    for c in calls:
        assert gx(c["doc"])[-1][:3] == ("published", "", "v1")


def turns_check():
    return json.loads(last_run()["checks"]["turns_rerun"]["detail"])


def out_id(c: dict, t: str = ge.TASK) -> int:
    return q("select id from core.ai_output where document_id = %s and task = %s and status = 'published'", c["doc"], t)[0][0]


def rerun(monkeypatch, mode: str, only: str = ge.TASK) -> None:
    monkeypatch.setenv(eca.ONLY_ENV, only)
    monkeypatch.setenv(ENV, mode)


@pytest.mark.db
def test_price_chooses_only_the_call_whose_turns_changed_prices_it_and_sends_nothing(monkeypatch):
    changed, plain = make_call(lines=MERGED, fy_end=3), make_call(fy_end=3)
    extract(monkeypatch, changed, plain)
    assert [g[3] for g in gx(changed["doc"])] == [1] and [g[3] for g in gx(plain["doc"])] == [2]   # v1 dropped the inline answer
    old = out_id(changed)
    rerun(monkeypatch, "price")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)                     # the price needs no key
    fake = FakeClaude(["ok"], answer=answers([CAPEX_T, OUTLOOK_T], [REV_S]))
    assert go(task([changed["slug"], plain["slug"]], fake)) == 0
    assert turns_check()["changed"] == 0 and turns_check()["unchanged"] == 2      # before the re-derive: nothing to ask
    split_turn(changed)
    assert go(task([changed["slug"], plain["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 0 and fake.batches == {}                         # nothing sent
    rec = turns_check()
    assert rec["mode"] == "price" and rec["task"] == ge.TASK and rec["version"] == "v1" and rec["refused"] is None
    assert rec["published"] == 2 and rec["current_version"] == 2 and rec["other_versions"] == {}
    assert rec["changed"] == 1 and rec["unchanged"] == 1 and rec["outputs"] == [old] and rec["calls"] == [changed["slug"]]
    assert rec["requests"] == 1 and rec["estimated_input_tokens"] > 0 and rec["sent"] == 0 and rec["outcomes"] == {}
    assert float(rec["run_estimate_usd"]) > 0
    assert [g[0] for g in gx(changed["doc"])] == ["published"] and [g[0] for g in gx(plain["doc"])] == ["published"]


@pytest.mark.db
def test_all_asks_the_changed_call_once_at_the_same_version_supersedes_keeps_the_old_rows_and_nothing_asks_again(monkeypatch):
    changed, plain = make_call(lines=MERGED, fy_end=3), make_call(fy_end=3)
    extract(monkeypatch, changed, plain)
    old_rows, plain_before = grows(changed["doc"]), gx(plain["doc"])
    split_turn(changed)
    rerun(monkeypatch, "all")
    fake = FakeClaude(["ok"], answer=answers([CAPEX_T, OUTLOOK_T], [REV_S]))
    assert go(task([changed["slug"], plain["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    ((_, sent),) = fake.batches.items()
    assert len(sent["requests"]) == 1                                            # the changed call only
    assert [(g[0], g[2], g[3]) for g in gx(changed["doc"])] == [("superseded", "v1", 1), ("published", "v1", 2)]
    assert grows(changed["doc"])[:len(old_rows)] == old_rows and len(grows(changed["doc"])) == len(old_rows) + 2
    assert [r[0] for r in grows(changed["doc"])[len(old_rows):]] == ["management", "management"]   # the inline answer is kept now
    assert gx(plain["doc"]) == plain_before                                      # the unchanged call: not asked, not touched
    rec = turns_check()
    assert rec["mode"] == "all" and rec["changed"] == 1 and rec["outcomes"] == {"published": 1}
    assert q("select count(*) from core.ai_output where document_id = %s and task <> %s", changed["doc"], ge.TASK) == [(0,)]
    # the same re-run again: the published output is the new input's now, so nothing is chosen or sent
    assert go(task([changed["slug"], plain["slug"]], fake)) == 0
    assert fake.create_calls == 1 and turns_check()["changed"] == 0 and turns_check()["unchanged"] == 2
    # an ordinary run doesn't ask for either call (each has a published output of the current version)
    monkeypatch.delenv(ENV)
    plain_run = FakeClaude(["ok"], answer=answers([CAPEX_T]))
    assert go(task([changed["slug"], plain["slug"]], plain_run)) == 0
    assert plain_run.create_calls == 0 and "turns_rerun" not in last_run()["checks"]


@pytest.mark.db
def test_a_reply_that_is_rejected_or_finds_nothing_leaves_the_old_output_published_and_is_never_resent(monkeypatch):
    broken, empty, plain = make_call(lines=MERGED, fy_end=3), make_call(lines=MERGED, fy_end=3), make_call(fy_end=3)
    extract(monkeypatch, broken, empty, plain)
    for c in (broken, empty):
        split_turn(c)
    rerun(monkeypatch, "all")
    fake = FakeClaude(["not_json", "not_json"])
    assert go(task([broken["slug"], plain["slug"]], fake)) == 0
    states = [(g[0], g[2]) for g in gx(broken["doc"])]
    assert states == [("published", "v1"), ("rejected", "v1"), ("rejected", "v1")] and fake.create_calls == 2   # retried once
    assert go(task([empty["slug"], plain["slug"]], FakeClaude(["ok"], answer=answers()))) == 0
    assert [(g[0], g[1], g[2]) for g in gx(empty["doc"])] == [("published", "", "v1"), ("draft", "no_targets", "v1")]
    for c in (broken, empty):                                                   # the old output's rows are still the ones shown
        assert q("""select count(*) from equity.guidance g join core.ai_output o on o.id = g.ai_output_id
                     where g.document_id = %s and o.status = 'published'""", c["doc"]) == [(1,)]
    # still chosen (its published output has the old input), but never sent again: the new input was answered
    assert go(task([broken["slug"], empty["slug"], plain["slug"]], fake)) == 0
    assert fake.create_calls == 2 and turns_check()["changed"] == 2 and turns_check()["outcomes"] == {"done": 2}


@pytest.mark.db
def test_an_output_published_by_its_retry_is_matched_through_the_retry_and_not_chosen_while_its_turns_stand(monkeypatch):
    retried, plain = make_call(lines=MERGED, fy_end=3), make_call(fy_end=3)
    extract(monkeypatch, retried, script=("not_json", "ok"))                    # attempt 1 retried with a note, attempt 2 published
    assert [g[0] for g in gx(retried["doc"])] == ["rejected", "published"]
    extract(monkeypatch, plain)
    rerun(monkeypatch, "price")
    assert go(task([retried["slug"], plain["slug"]], FakeClaude(["ok"]))) == 0
    assert turns_check()["changed"] == 0 and turns_check()["unchanged"] == 2
    split_turn(retried)
    assert go(task([retried["slug"], plain["slug"]], FakeClaude(["ok"]))) == 0
    assert turns_check()["calls"] == [retried["slug"]]


@pytest.mark.db
def test_a_run_where_every_output_would_be_chosen_is_refused_and_sends_nothing(monkeypatch):
    changed = make_call(lines=MERGED, fy_end=3)
    extract(monkeypatch, changed)
    split_turn(changed)
    rerun(monkeypatch, "all")
    fake = FakeClaude(["ok"], answer=answers([CAPEX_T, OUTLOOK_T], [REV_S]))
    go(task([changed["slug"]], fake))
    r = last_run()
    assert r["status"] == "checks_failed" and fake.create_calls == 0
    ck = r["checks"]["turns_rerun"]
    assert not ck["passed"] and "changed prompt file or request format" in json.loads(ck["detail"])["refused"]
    assert [g[0] for g in gx(changed["doc"])] == ["published"]


@pytest.mark.db
def test_a_scheduled_run_with_the_switch_stops_before_anything_is_sent(monkeypatch):
    c = make_call(lines=MERGED, fy_end=3)
    extract(monkeypatch, c)
    split_turn(c)
    rerun(monkeypatch, "all")
    fake = FakeClaude(["ok"], answer=answers([CAPEX_T, OUTLOOK_T], [REV_S]))
    go(task([c["slug"]], fake), workflow="weekly")
    r = last_run()
    assert r["status"] == "failed" and "runs only in a backfill" in r["error"] and fake.create_calls == 0
    assert [g[0] for g in gx(c["doc"])] == ["published"]


@pytest.mark.db
def test_the_summary_of_the_call_whose_turns_changed_is_asked_again_and_supersedes_the_old_one(monkeypatch):
    changed, plain = make_call(lines=MERGED), make_call()
    with monkeypatch.context() as m:
        m.setenv(eca.ONLY_ENV, cs.TASK)
        assert go(task([changed["slug"], plain["slug"]], FakeClaude(["ok"], reply=replies()))) == 0
    assert [o[0] for o in outputs(changed["doc"])] == ["published"] and [o[0] for o in outputs(plain["doc"])] == ["published"]
    split_turn(changed)
    rerun(monkeypatch, "all", cs.TASK)
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task([changed["slug"], plain["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    ((_, sent),) = fake.batches.items()
    assert len(sent["requests"]) == 1
    v = eca.PROMPT_VERSION[cs.TASK]                                              # the citations route's v5 in these tests
    assert [(o[0], o[4]) for o in outputs(changed["doc"])] == [("superseded", v), ("published", v)]
    assert [o[0] for o in outputs(plain["doc"])] == ["published"]
    assert r["checks"]["citation_roles"]["passed"] and turns_check()["outcomes"] == {"published": 1}


@pytest.mark.db
def test_the_tone_of_the_call_whose_management_turn_changed_is_asked_again_and_the_other_is_not(monkeypatch):
    changed, plain = rechunk(make_call(lines=LONG, fy_end=3)), rechunk(make_call(lines=LONG, fy_end=3))
    with monkeypatch.context() as m:
        m.setenv(eca.ONLY_ENV, ct.TASK)
        both = FakeClaude(["ok"], answer=tone_answer([2, -1], [(1, UP)]))
        assert go(task([changed["slug"], plain["slug"]], both)) == 0
    assert [o[0] for o in tone_outputs(changed["doc"])] == ["published"]
    set_role(changed, "Meera Iyer", "analyst")                                  # her answer isn't management's any more
    rerun(monkeypatch, "all", ct.TASK)
    fake = FakeClaude(["ok"], answer=tone_answer([2], [(1, UP)]))               # one passage left to score
    assert go(task([changed["slug"], plain["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    ((_, sent),) = fake.batches.items()
    assert len(sent["requests"]) == 1
    assert [(o[0], o[2]) for o in tone_outputs(changed["doc"])] == [("superseded", "v3"), ("published", "v3")]
    assert [o[0] for o in tone_outputs(plain["doc"])] == ["published"]
    assert turns_check()["changed"] == 1 and turns_check()["outcomes"] == {"published": 1}
