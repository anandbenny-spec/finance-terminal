"""The extraction's re-run (#511, 2B-15d; the #445 pattern): `CALLS_AI_GUIDANCE_RERUN` on a dev backfill limited to the extraction
sends `GUIDANCE_RERUN_VERSION` for each call whose published extraction was made with another version, and nothing else. `price`
chooses and prices the same requests and sends nothing. A re-run reply supersedes the published output only when it passes the
checks and holds an item; the old output and its rows stay. The helpers are test_equity_calls_ai_task.py's; the switch's rules and
prompt v5's are in test_guidance_rerun_rules.py (no database)."""
import json
from decimal import Decimal

import psycopg
import pytest
from ai_fakes import FakeClaude
from test_equity_calls_ai_task import (
    CAPEX_T,
    JOB,
    KEY,
    LINES,
    OUTLOOK_T,
    REV_S,
    T,
    answers,
    citations_route,  # noqa: F401  (autouse: the summary on the citations route, as that module's tests drive it)
    go,
    grows,
    gx,
    last_run,
    make_call,
    q,
    replies,
    task,
    wipe,
)

from ft.ai import guidance_extraction as ge
from ft.jobs.tasks import equity_calls_ai as eca

DEV_RERUN = {eca.ONLY_ENV: ge.TASK, eca.GUIDANCE_RERUN_ENV: "all"}
RV = eca.GUIDANCE_RERUN_VERSION            # the re-run's version: v5 for #511, v6 since #533


# ---------------------------------------------------------------------------------------------- the task (test database)
@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '#511 test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


@pytest.fixture(scope="module", autouse=True)
def clean():
    wipe()
    yield
    wipe()


def extracted(c, targets=(CAPEX_T,), stated=(REV_S,)) -> None:
    """A call with a published v1 extraction (and its summary), as dev has today."""
    fake = FakeClaude(["ok"], reply=replies(), answer=answers(list(targets), list(stated)))
    assert go(task([c["slug"]], fake)) == 0
    assert [(g[0], g[2]) for g in gx(c["doc"])] == [("published", "v1")]


def rerun_check():
    return json.loads(last_run()["checks"]["guidance_rerun"]["detail"])


def gversions(doc_id: int):
    return q("""select prompt_version, status from ops.ai_request where document_id = %s and ai_task = 'guidance_extraction'
                 order by created_at, custom_id""", doc_id)


@pytest.mark.db
def test_the_price_mode_chooses_the_published_older_outputs_prices_them_and_sends_nothing(monkeypatch):
    c, none_found = make_call(fy_end=3), make_call(fy_end=3)
    extracted(c)
    empty = FakeClaude(["ok"], reply=replies(), answer=answers())
    assert go(task([none_found["slug"]], empty)) == 0
    assert gx(none_found["doc"]) == [("draft", "no_targets", "v1", 0, 0)]
    out_id = q("select id from core.ai_output where document_id = %s and task = 'guidance_extraction'", c["doc"])[0][0]
    monkeypatch.setenv(eca.ONLY_ENV, ge.TASK)
    monkeypatch.setenv(eca.GUIDANCE_RERUN_ENV, "price")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)                     # the price needs no key
    fake = FakeClaude(["ok"], answer=answers([CAPEX_T]))
    assert go(task([c["slug"], none_found["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 0 and fake.batches == {}                         # nothing sent
    rec = rerun_check()
    assert rec["mode"] == "price" and rec["version"] == RV and rec["sent"] == 0
    assert rec["published"] == 1 and rec["older"] == 1 and rec["outputs"] == [out_id] and rec["older_by_version"] == {"v1": 1}
    assert rec["requests"] == 1 and rec["estimated_input_tokens"] > 0                # the call that found nothing isn't asked
    worst, run_estimate = Decimal(rec["worst_case_usd"]), Decimal(rec["run_estimate_usd"])
    assert worst > 0 and abs(run_estimate - 2 * worst) <= Decimal("0.0001")         # each rounded to four places
    assert gx(c["doc"]) == [("published", "", "v1", 1, 1)] and gversions(c["doc"]) == [("v1", "succeeded")]


@pytest.mark.db
def test_the_rerun_sends_its_version_once_supersedes_the_old_output_keeps_its_rows_and_no_run_asks_again(monkeypatch):
    c = make_call(fy_end=3)
    extracted(c)
    old_rows = grows(c["doc"])
    for k, v in DEV_RERUN.items():
        monkeypatch.setenv(k, v)
    fake = FakeClaude(["ok"], answer=answers([CAPEX_T, OUTLOOK_T], [REV_S]))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert gversions(c["doc"]) == [("v1", "succeeded"), (RV, "succeeded")]
    assert [(g[0], g[2], g[3]) for g in gx(c["doc"])] == [("superseded", "v1", 1), ("published", RV, 2)]
    assert grows(c["doc"])[:len(old_rows)] == old_rows and len(grows(c["doc"])) == len(old_rows) + 2   # the old rows stay
    assert q("select count(*) from core.ai_output where document_id = %s and task = 'call_summary'", c["doc"]) == [(1,)]
    rec = rerun_check()
    assert rec["mode"] == "all" and rec["older"] == 1 and rec["outcomes"] == {"published": 1}
    # the same re-run again: the call's published extraction is the re-run's version now, so nothing is chosen or sent
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 1 and rerun_check()["older"] == 0
    # an ordinary run (v1, no re-run) doesn't ask for it either
    monkeypatch.delenv(eca.GUIDANCE_RERUN_ENV)
    monkeypatch.delenv(eca.ONLY_ENV)
    plain = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T]))
    assert go(task([c["slug"]], plain)) == 0
    assert plain.create_calls == 0 and "guidance_rerun" not in last_run()["checks"]
    assert gx(c["doc"])[-1][:3] == ("published", "", RV)


@pytest.mark.db
def test_a_rerun_that_finds_nothing_or_is_rejected_leaves_the_old_output_published(monkeypatch):
    empty_call, broken_call = make_call(fy_end=3), make_call(fy_end=3)
    extracted(empty_call)
    extracted(broken_call)
    for k, v in DEV_RERUN.items():
        monkeypatch.setenv(k, v)
    assert go(task([empty_call["slug"]], FakeClaude(["ok"], answer=answers()))) == 0
    assert [(g[0], g[1], g[2]) for g in gx(empty_call["doc"])] == [("published", "", "v1"), ("draft", "no_targets", RV)]
    assert go(task([broken_call["slug"]], FakeClaude(["not_json", "not_json"]))) == 0
    states = [(g[0], g[2]) for g in gx(broken_call["doc"])]
    assert states[0] == ("published", "v1") and {s for s, v in states[1:]} == {"rejected"}
    assert all(v == RV for _, v in states[1:])
    # the old output still counts: its published rows are the ones the views read
    for c in (empty_call, broken_call):
        assert q("""select count(*) from equity.guidance g join core.ai_output o on o.id = g.ai_output_id
                     where g.document_id = %s and o.status = 'published'""", c["doc"]) == [(1,)]


# ---------------------------------------------------------------------------------------------- the checks that came with v6 (#533)
GLUED = "We will also invest INR3,000 crores in the new plant in FY27."
GLUED_T = T(GLUED, metric="capex", metric_text="investment in the new plant", value=3000, unit="inr", growth_basis="level")


@pytest.mark.db
def test_a_v6_rerun_keeps_a_figure_written_against_its_currency_code_which_v1_dropped(monkeypatch):
    # #533's diagnosis: v1 to v5's reader takes "INR3,000" for 000 and drops the item. The re-run's version is checked with
    # `ge.V6_RULES`' reader, when the reply is stored and again by the blocking `guidance_items` check on the stored rows.
    assert RV in ge.V6_RULES
    c = make_call(lines=LINES[:10] + [("Ravi Rao", "management", f"Ravi Rao: {GLUED}")] + LINES[10:], fy_end=3)
    extracted(c, targets=(CAPEX_T, GLUED_T))                                    # v1: the glued figure is dropped
    assert gx(c["doc"]) == [("published", "", "v1", 1, 1)]
    assert q("select content->'dropped' from core.ai_output where document_id = %s and task = 'guidance_extraction'",
             c["doc"]) == [({"figure_not_in_quote": 1},)]
    for k, v in DEV_RERUN.items():
        monkeypatch.setenv(k, v)
    assert go(task([c["slug"]], FakeClaude(["ok"], answer=answers([CAPEX_T, GLUED_T], [REV_S])))) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert [(g[0], g[2], g[3]) for g in gx(c["doc"])] == [("superseded", "v1", 1), ("published", RV, 2)]
    assert q("""select g.value, g.unit, g.metric from equity.guidance g join core.ai_output o on o.id = g.ai_output_id
                 where g.document_id = %s and o.status = 'published' order by g.id""", c["doc"]) == [
        (Decimal(800), "inr", "capex"), (Decimal(3000), "inr", "capex")]
    assert q("select content->'dropped' from core.ai_output where document_id = %s and status = 'published' and task = 'guidance_extraction'",
             c["doc"]) == [({},)]


@pytest.mark.db
def test_a_call_published_at_an_earlier_rounds_version_is_left_alone_by_an_ordinary_run_and_taken_by_the_rerun(monkeypatch):
    # Dev after #511: every call's published extraction is v5. An ordinary run (v1) must not ask for it again; #533's re-run does.
    assert eca.GUIDANCE_RERUN_EARLIER == ("v5",) and RV not in eca.GUIDANCE_RERUN_EARLIER
    c = make_call(fy_end=3)
    extracted(c)
    for k, v in DEV_RERUN.items():
        monkeypatch.setenv(k, v)
    with monkeypatch.context() as m:                                           # #511's round, as it ran: the re-run to v5
        m.setattr(eca, "GUIDANCE_RERUN_VERSION", "v5")
        m.setattr(eca, "GUIDANCE_RERUN_EARLIER", ())
        assert go(task([c["slug"]], FakeClaude(["ok"], answer=answers([CAPEX_T], [REV_S])))) == 0
    assert [(g[0], g[2]) for g in gx(c["doc"])] == [("superseded", "v1"), ("published", "v5")]
    monkeypatch.delenv(eca.GUIDANCE_RERUN_ENV)
    monkeypatch.delenv(eca.ONLY_ENV)
    plain = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T]))
    assert go(task([c["slug"]], plain)) == 0
    assert plain.create_calls == 0 and gx(c["doc"])[-1][:3] == ("published", "", "v5")          # nothing asked, v5 stays
    for k, v in DEV_RERUN.items():
        monkeypatch.setenv(k, v)
    fake = FakeClaude(["ok"], answer=answers([CAPEX_T, OUTLOOK_T], [REV_S]))
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 1 and rerun_check()["older_by_version"] == {"v5": 1}
    assert [(g[0], g[2]) for g in gx(c["doc"])] == [("superseded", "v1"), ("superseded", "v5"), ("published", RV)]
