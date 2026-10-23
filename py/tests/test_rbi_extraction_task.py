"""policy.ai_batch's extractions on MPC resolutions (Prompt 01 B7 part 2; Spec 01 §2, §3.2-§3.3; Architecture 03 §1.2,
§1.4): `stance_extraction` and `projection_extraction` through the runner against the test database as job_writer.
policy.rbi_documents (B6) loads synthetic resolutions first, so each has its raw file (a local S3-protocol server,
moto), its parsed stance, votes and projections; then the AI step runs on them through the Batch API path (daily)
and in real time (a decision day). The Messages and Message Batches APIs and Voyage are fakes (tests/ai_fakes.py).
Meetings are synthetic, in 2013 (no other test uses that year), each test with its own; the cross-check is Anand's
decision of 24 Oct 2026 (issue #16). This file runs after test_calendar_task (its MPC numbering counts every stored
meeting)."""
import json
from datetime import UTC, date, datetime

import psycopg
import pytest
from ai_fakes import FakeClaude, FakeVoyage, claude, documents_of, good_stance, schema_of
from conftest import dsn
from test_rbi_documents_task import (  # noqa: F401 - s3 and roster are fixtures
    PR,
    SIX,
    Sites,
    annual,
    forecasts,
    meeting,
    named,
    old,
    resolution,
    roster,
    s3,
    storage,
)
from test_rbi_documents_task import go as rbi_go
from test_rbi_documents_task import task as rbi_task

from ft.ai.client import Embedder
from ft.ai.extraction import Parsed
from ft.jobs import due as due_rules
from ft.jobs.runner import JobRun, run
from ft.jobs.tasks.ai_batch import AiBatch
from ft.policy import rbi_calendar

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
KEY = "test.b7_extraction"
YEARS = {"2013-2014": "2014", "2012-2013": "2013"}
DISSENT = ("While the decision on keeping the policy rate unchanged was unanimous, Dr. Ben Iyer voted to change the "
           "stance to neutral.")
CPI = "CPI inflation is projected at 4.4 per cent for 2013-14: Q2 4.6 per cent; Q3 4.0 per cent."
GDP = "Real GDP growth for 2013-14 is projected at 7.1 per cent."
HOLD_STANCE = "The MPC also decided to maintain the stance of calibrated tightening."
CUT_STANCE = "The MPC also decided to change the monetary policy stance from calibrated tightening to neutral."


def keep(stance: str = HOLD_STANCE, votes=(DISSENT,), projections=()) -> str:
    """A hold at 6.5 per cent (a baseline: its changes come from its own sentences)."""
    return old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.5 per cent.",
               "Consequently, the reverse repo rate under the LAF remains at 6.25 per cent, and the marginal standing "
               "facility (MSF) rate and the Bank Rate at 6.75 per cent.", stance, list(votes), list(projections))


CUT_BODY = old("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points from 6.5 per "
               "cent to 6.25 per cent with immediate effect.",
               "Consequently, the reverse repo rate under the LAF stands adjusted to 6.0 per cent, and the marginal "
               "standing facility (MSF) rate and the Bank Rate to 6.5 per cent.",
               CUT_STANCE,
               [("The decision to change the monetary policy stance was unanimous. As regards the reduction in the policy "
                f"repo rate, {named(SIX[:4])} voted in favour of the decision. {named(SIX[4:])} voted to keep the policy "
                "rate unchanged.")],
               [CPI, GDP])
CPI_AI = [{"variable": "cpi", "target_label": "FY 2013-14", "target_start": "2013-04-01", "target_end": "2014-03-31",
           "value": 4.4, "low": None, "high": None, "quote": "4.4 per cent for 2013-14"},
          {"variable": "cpi", "target_label": "Q2 2013-14", "target_start": "2013-07-01", "target_end": "2013-09-30",
           "value": 4.6, "low": None, "high": None, "quote": "Q2 4.6 per cent"},
          {"variable": "cpi", "target_label": "Q3 2013-14", "target_start": "2013-10-01", "target_end": "2013-12-31",
           "value": 4.0, "low": None, "high": None, "quote": "Q3 4.0 per cent"}]


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        cur = c.execute(sql, a)
        return cur.fetchall() if cur.description else cur.rowcount


def as_anon(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("set role anon")
        return c.execute(sql, a).fetchall()


@pytest.fixture(scope="module", autouse=True)
def ai_task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'B7 extraction test task', '{daily,watch}', '20 hours', '3 days', false)
                     on conflict (key) do nothing""", (KEY,))


class Ticker:
    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def answers(projections=None, stance=None):
    """The fake's JSON answers: the stance (default: the sentence that says what the MPC decided) and the projections."""
    def answer(body):
        docs, _ = documents_of(body)
        if "projections" in schema_of(body)["properties"]:
            return {"projections": projections if projections is not None else []}
        return stance(docs) if callable(stance) else stance or good_stance(docs)
    return answer


def ai_task(slugs, fake: FakeClaude) -> AiBatch:
    class T(AiBatch):
        key = KEY
    t = T()
    t.scope = frozenset(slugs)
    t.workflows, t.rules = ("daily", "watch"), {"daily": "every 20 hours", "watch": "after_decision_document"}
    t.claude, t.embedder = claude(fake), Embedder(client=FakeVoyage())
    tick = Ticker()
    t.sleep, t.monotonic = tick.sleep, tick.monotonic
    return t


def daily(t: AiBatch, now: datetime) -> int:
    return run(JOB, "daily", [t], only=KEY, force=True, storage=storage, clock=lambda: now)


def decision_day(t: AiBatch, d: date, now: datetime) -> int:
    """The watch's AI step on the meeting's decision day, in real time (due forced: the resolution is stored)."""
    def due(db, workflow, now=None):
        m = due_rules.decision_meeting(db, due_rules.BANKS["mpc_decision_day"], d)
        return due_rules.Due(True, "test: decision day", meeting=m, until=m.closes_at)
    t.due = due
    with JobRun(JOB, "watch", clock=lambda: now, storage=storage) as job:
        job.run_pass([t])
        return job.finish()


def last_run():
    r = q("""select status, rows_written, checks, coalesce(error, '') from ops.task_run where task_key = %s
              order by id desc limit 1""", KEY)[0]
    return {"status": r[0], "rows": r[1], "checks": {c["name"]: c for c in r[2] or []}, "error": r[3]}


def load(*items: tuple[date, int, str], first_day: date, roster_path, current: tuple = (), earlier: tuple = ()):
    """policy.rbi_documents' backfill over these meetings (date, prid, body): `current` on the Annual Policy page as
    served, `earlier` on fiscal year 2012-13's page (by form post)."""
    slugs = [meeting(d) for d, _, _ in items]
    pages = {rbi_calendar.ANNUAL_POLICY: annual(*current, years=YEARS)}
    pages |= {PR + str(prid): resolution(d, body) for d, prid, body in items}
    sites = Sites(pages, {"2013": annual(*earlier, years=YEARS)} if earlier else {})
    code, r = rbi_go(sites, rbi_task(roster_path, slugs, first_day), "backfill", datetime(2014, 3, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    return slugs


def requests(slug: str):
    return q("""select r.ai_task, r.status, coalesce(r.reject_reason, '') from ops.ai_request r
                  join core.document d on d.id = r.document_id join core.event e on e.id = d.event_id
                 where e.slug = %s order by r.created_at, r.custom_id""", slug)


def stance_outputs(slug: str):
    return q("""select o.status, o.label, o.body, o.document_id is null, coalesce(o.status_reason, '')
                  from core.ai_output o join core.event e on e.id = o.event_id
                 where o.task = 'stance_extraction' and e.slug = %s order by o.id""", slug)


def alert(key: str):
    """(open, message) of the latest alert with this key."""
    return q("select resolved_at is null, message from ops.alert where dedupe_key = %s order by id desc limit 1",
             key)[0]


def resolution_id(slug: str) -> int:
    return q("""select d.id from core.document d join core.event e on e.id = d.event_id
                 where e.slug = %s and d.doc_type = 'mpc_resolution'""", slug)[0][0]


# ---------------------------------------------------------------------------------------------- the batch path
@pytest.mark.db
def test_a_daily_run_publishes_each_stance_and_the_projections_the_regex_missed_through_a_batch(s3, roster):  # noqa: F811
    feb, apr = date(2013, 2, 6), date(2013, 4, 3)
    base, cut = load((feb, 83001, keep()), (apr, 83101, CUT_BODY), first_day=date(2013, 3, 1), roster_path=roster,
                     current=((apr, 83101),), earlier=((feb, 83001),))
    # B6 read the cut's CPI sentence only in part: the year's 4.4 per cent, not its quarters.
    assert [x[:6] for x in forecasts(cut)] == [("rbi.proj_cpi", "FY 2013-14", "4.4", None, None, "parsed"),
                                               ("rbi.proj_gdp", "FY 2013-14", "7.1", None, None, "parsed")]
    fake = FakeClaude(["ok"], answer=answers(CPI_AI))
    assert daily(ai_task([base, cut], fake), datetime(2014, 3, 2, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    for name in ("outputs_stored", "extractions_held", "citation_integrity", "batches"):
        assert r["checks"][name]["passed"], (name, r["checks"][name]["detail"])

    # One batch: the summaries, the cut's "what changed", a stance for each resolution and the cut's projections.
    assert [len(b["requests"]) for b in fake.batches.values()] == [6]
    assert sorted(k for k, _ in fake.answered) == ["projections", "stance", "stance"]
    assert sorted(x[:2] for x in requests(base)) == [("doc_summary", "succeeded"), ("stance_extraction", "succeeded")]
    assert sorted(x[:2] for x in requests(cut)) == [("doc_summary", "succeeded"), ("projection_extraction", "succeeded"),
                                                   ("stance_extraction", "succeeded"),
                                                   ("statement_diff_summary", "succeeded")]

    # The stance is the meeting's, labelled and cited to the resolution's own sentence; visitors see it.
    assert stance_outputs(base) == [("published", "calibrated tightening", HOLD_STANCE, True, "")]
    assert stance_outputs(cut)[0][:2] == ("published", "neutral")
    vis = as_anon("""select slug, stance, stance_text, stance_display_label from api.policy_meetings
                      where slug = any(%s) order by slug""", [base, cut])
    assert vis == [(base, "calibrated tightening", HOLD_STANCE, "AI-extracted"),
                   (cut, "neutral", CUT_STANCE, "AI-extracted")]
    cites = as_anon("""select c.quote, c.claim_path from api.ai_citations c join api.ai_outputs o on o.id = c.ai_output_id
                        where o.task = 'stance_extraction' and o.event_slug = %s""", base)
    assert cites == [(HOLD_STANCE, "/quote")]

    # The quarters the regex missed are AI-extracted rows on the quote's span; the year it read stays parsed.
    assert [x[:7] for x in forecasts(cut)] == [
        ("rbi.proj_cpi", "FY 2013-14", "4.4", None, None, "parsed", "4.4 per cent for 2013-14"),
        ("rbi.proj_cpi", "Q2 2013-14", "4.6", None, None, "ai_extracted", "Q2 4.6 per cent"),
        ("rbi.proj_cpi", "Q3 2013-14", "4.0", None, None, "ai_extracted", "Q3 4.0 per cent"),
        ("rbi.proj_gdp", "FY 2013-14", "7.1", None, None, "parsed", "2013-14 is projected at 7.1 per cent")]
    vis = as_anon("""select target_label, value::text, label from api.forecasts
                      where vintage_date = %s and series_key = 'rbi.proj_cpi' order by target_start, target_end""", apr)
    assert vis == [("FY 2013-14", "4.4", None), ("Q2 2013-14", "4.6", "AI-extracted"), ("Q3 2013-14", "4.0",
                                                                                         "AI-extracted")]
    with psycopg.connect(dsn("sql_reader"), autocommit=True) as c:            # the command bar's login
        assert c.execute("""select extraction from semantic.policy_projections where meeting_date = %s
                             and period_start = '2013-07-01'""", (apr,)).fetchall() == [("AI-extracted",)]

    # Run again: nothing is sent (never twice) and nothing changes.
    n = fake.n
    assert daily(ai_task([base, cut], fake), datetime(2014, 3, 3, tzinfo=UTC)) == 0
    assert fake.n == n and last_run()["rows"] == 0 and last_run()["status"] == "success"


@pytest.mark.db
def test_a_stance_that_disagrees_with_the_parse_is_held_and_published_once_the_parse_agrees(s3, roster, monkeypatch):  # noqa: F811
    aug = date(2013, 8, 7)
    slug, = load((aug, 83201, keep()), first_day=date(2013, 9, 1), roster_path=roster, current=((aug, 83201),))
    # The AI quotes the dissent: it states "neutral", but the MPC decided calibrated tightening (the parser's reading).
    fake = FakeClaude(["ok"], answer=answers(stance={"stance": "neutral", "quote": DISSENT}))
    assert daily(ai_task([slug], fake), datetime(2014, 3, 2, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success" and not r["checks"]["extractions_held"]["passed"]
    assert "mismatch: the AI read 'neutral', the resolution's parsed stance is 'calibrated tightening'" in \
        r["checks"]["extractions_held"]["detail"]
    out = stance_outputs(slug)
    assert [(o[0], o[1], o[3]) for o in out] == [("draft", "neutral", True)] and out[0][4].startswith("held: mismatch")
    assert sorted(x[:2] for x in requests(slug)) == [("doc_summary", "succeeded"), ("stance_extraction", "succeeded")]
    key = f"ai_held:stance_extraction:{resolution_id(slug)}"
    open_, message = alert(key)
    assert open_ and "mismatch" in message
    assert as_anon("select stance from api.policy_meetings where slug = %s", slug) == [(None,)]
    assert q("select count(*) from core.ai_citation c join core.ai_output o on o.id = c.ai_output_id join core.event e "
             "on e.id = o.event_id where e.slug = %s", slug) == [(0,)]

    # A later run sends nothing for it (the same input was answered) and it stays held.
    n = fake.n
    assert daily(ai_task([slug], fake), datetime(2014, 3, 3, tzinfo=UTC)) == 0
    assert fake.n == n and stance_outputs(slug)[0][0] == "draft" and alert(key)[0]

    # Once the parse agrees (a fixed parser, say), the next run publishes the held draft without a new request.
    monkeypatch.setattr(AiBatch, "_parsed_stance", lambda self, d: Parsed(stance="neutral"))
    assert daily(ai_task([slug], fake), datetime(2014, 3, 4, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success" and r["checks"]["outputs_stored"]["passed"], r["checks"]["outputs_stored"]
    assert fake.n == n and [(o[0], o[1], o[4]) for o in stance_outputs(slug)] == [("published", "neutral", "")]
    assert as_anon("select stance, stance_text from api.policy_meetings where slug = %s", slug) == [("neutral", DISSENT)]
    assert not alert(key)[0]                                                # resolved


# ---------------------------------------------------------------------------------------------- real time
@pytest.mark.db
def test_on_a_decision_day_a_bad_quote_is_retried_other_is_held_and_a_wrong_figure_is_rejected(s3, roster):  # noqa: F811
    oct_ = date(2013, 10, 30)
    slug, = load((oct_, 83301, keep(projections=(CPI,))), first_day=date(2013, 11, 1), roster_path=roster,
                 current=((oct_, 83301),))
    calls = []

    def stance(docs):
        calls.append(1)
        if len(calls) == 1:
            return {"stance": "calibrated tightening", "quote": "The MPC decided to keep the stance of calibrated "
                                                                "tightening."}
        return {"stance": "other", "quote": HOLD_STANCE}

    wrong = [{**CPI_AI[0], "value": 4.5}, CPI_AI[1]]          # its quote states 4.4 per cent
    fake = FakeClaude(["ok"], answer=answers(wrong, stance))
    assert decision_day(ai_task([slug], fake), oct_, datetime(2013, 10, 30, 10, 5, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert not fake.batches                                                  # real time: no batch
    # The stance's first quote isn't in the resolution: retried once with a note, and the retry says "other".
    stance_reqs = [b for b in fake.requests if b.get("output_config", {}).get("format")
                   and "stance" in b["output_config"]["format"]["schema"]["properties"]]
    assert len(stance_reqs) == 2 and "isn't in the resolution exactly once" in \
        stance_reqs[1]["messages"][0]["content"][-1]["text"]
    out = stance_outputs(slug)
    assert [(o[0], o[1]) for o in out] == [("rejected", "calibrated tightening"), ("draft", "other")]
    assert out[0][4].startswith("retried: the quote") and out[1][4].startswith("held: other: the stance isn't in the "
                                                                                "vocabulary")
    held = json.loads(r["checks"]["extractions_held"]["detail"])["held"]
    assert len(held) == 1 and held[0].startswith(f"stance_extraction:doc-{resolution_id(slug)}: other:")
    # The projections: the AI's 4.5 per cent for the year isn't what its quote states, so the reply is retried, and
    # the retry (the same answer) is rejected with an alert. Nothing AI-extracted is stored.
    proj = q("""select status, coalesce(status_reason, '') from core.ai_output
                 where task = 'projection_extraction' and document_id = %s order by id""", resolution_id(slug))
    assert [p[0] for p in proj] == ["rejected", "rejected"] and "doesn't state FY 2013-14 at 4.5 per cent" in proj[0][1]
    assert proj[0][1].startswith("retried: ") and proj[1][1].startswith("rejected: ")
    assert alert(f"ai_rejected:projection_extraction:{resolution_id(slug)}")[0]
    assert alert(f"ai_held:stance_extraction:{resolution_id(slug)}")[0]
    assert [x[5] for x in forecasts(slug)] == ["parsed"]
