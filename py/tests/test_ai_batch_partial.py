"""A run over the $5 run limit sends what fits (#373, 2B-15c; Anand's option 1, 17 Nov 2026): `ft/ai/batch.py`'s `_submit` with
`partial_send`, as `equity.calls_ai` sets it. The real task class, real v11 and v1 requests priced from config/ai_prices.yaml, and
the real wrapper's batch create against the fake Batch API (tests/ai_fakes.py); the database is a fake that keeps the request
rows. No Postgres, no network, no AI credit. `policy.ai_batch` keeps all-or-nothing (its own tests are unchanged)."""
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from ai_fakes import FakeClaude, claude
from call_fixtures import build, calldoc

from ft.ai import batch
from ft.ai import call_summary as cs
from ft.ai import call_tone as ct
from ft.ai import guidance_extraction as ge
from ft.ai.batch import RUN_LIMIT_USD, Step
from ft.ai.client import load_prompt, load_schema
from ft.jobs.tasks.ai_batch import AiBatch
from ft.jobs.tasks.equity_calls_ai import EquityCallsAi, Fetched, Work

V11 = 75          # the fixture call's text repeated this often: about 84,000 characters, a v11 worst case near $0.23 a request
PROMPTS = {cs.TASK: load_prompt(cs.TASK, "v11"), ge.TASK: load_prompt(ge.TASK, "v1")}
DAY = date(2026, 10, 1)


class Db:
    """The request rows the steps read and write: custom_id -> [status, batch id, reason, content, batch status, output id,
    output status] (`_request_row`'s columns)."""

    def __init__(self):
        self.rows: dict[str, list] = {}
        self.batches = 0

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, args=()):
        s = " ".join(sql.split())
        if s.startswith("insert into ops.ai_request"):          # `_claim`: never-sent rows only, in these tests
            new = args[0] not in self.rows
            if new:
                self.rows[args[0]] = ["pending", None, "", None, None, None, None]
            return SimpleNamespace(rowcount=int(new))
        if s.startswith("insert into ops.ai_batch"):
            self.batches += 1
            return SimpleNamespace(fetchone=lambda: (self.batches, datetime.now(UTC)))
        if s.startswith("update ops.ai_request set ai_batch_id"):
            for cid in args[1]:
                self.rows[cid][1], self.rows[cid][4] = args[0], "submitted"
            return SimpleNamespace(rowcount=len(args[1]))
        if "from ops.ai_request r left join core.ai_output" in s:   # `_request_row`
            row = self.rows.get(args[0])
            return SimpleNamespace(fetchone=lambda: tuple(row) if row else None)
        if "join ops.ai_batch b on b.id = r.ai_batch_id" in s:      # `_exposure`: nothing waits in an open batch here
            return SimpleNamespace(fetchall=list)
        raise AssertionError(f"unexpected SQL: {s[:80]}")


def task(fake: FakeClaude | None = None) -> EquityCallsAi:
    t = EquityCallsAi()
    t.claude = claude(fake or FakeClaude())
    return t


def works(n: int, kind: str = cs.TASK, *, first_doc: int = 1, first_day: date = DAY) -> list[Work]:
    """`n` v11-sized calls, one a day: document `first_doc + i` filed on `first_day + i` (the last one is the newest)."""
    base = calldoc(build())
    out = []
    for i in range(n):
        call = replace(base, document_id=first_doc + i, text=base.text * V11, fy_end_month=3)
        version = "v11" if kind == cs.TASK else "v1"
        out.append(Work(kind, call, f"call-{first_doc + i}", event_id=first_doc + i, entity_id=1,
                        schema=load_schema(kind, version), filed=first_day + timedelta(days=i)))
    return out


def run(t: EquityCallsAi, db: Db, targets: list[Work], f: Fetched | None = None, *, budget: Decimal = Decimal(15)) -> Fetched:
    """One round of a run: plan every target, then submit what it needs (`_batch_rounds` without the collect and the poll)."""
    f = f or Fetched("batch", targets=list(targets), budget=budget)
    ctx = SimpleNamespace(fetcher=SimpleNamespace(db=db), task_run_id=9)
    t._submit(ctx, f, t._plan(ctx, f, PROMPTS), PROMPTS)
    return f


def sent(f: Fetched) -> list[int]:
    return sorted(o.work.document_id for o in f.outcomes if o.status == "submitted")


def by_status(f: Fetched, status: str) -> list[int]:
    return sorted(o.work.document_id for o in f.outcomes if o.status == status)


# ---------------------------------------------------------------------------------------------- the run limit
def test_25_v11_calls_go_ten_newest_a_run_each_run_under_the_limit_and_the_next_run_takes_the_rest():
    t, db, calls = task(), Db(), works(25)
    worst = t._worst_case(calls[0].request(PROMPTS[cs.TASK]), batch=True)
    assert Decimal("0.21") < worst < Decimal("0.24")          # the card's v11 sizing: $0.42 to $0.48 a call with its retry
    f1 = run(t, db, calls)
    assert sent(f1) == list(range(16, 26))                    # the ten newest filings: ten calls fit, an eleventh would pass $5
    assert f1.run_estimate <= RUN_LIMIT_USD < f1.run_estimate + 2 * worst
    assert f1.stopped == "run_estimate" and by_status(f1, "budget") == list(range(1, 16))
    assert f1.limit_left == {f"call_summary:doc-{i}" for i in range(1, 16)} and f1.limit_estimate > RUN_LIMIT_USD
    assert all("left for the next run" in o.reason for o in f1.outcomes if o.status == "budget")
    assert [b["requests"] for b in f1.batch.submitted] == [10]
    f2 = run(t, db, calls)                                    # the next run: the ten sent wait in their batch, so the rest go
    assert sent(f2) == list(range(6, 16)) and by_status(f2, "in_batch") == list(range(16, 26))
    assert f2.run_estimate <= RUN_LIMIT_USD and by_status(f2, "budget") == list(range(1, 6))
    f3 = run(t, db, calls)
    assert sent(f3) == list(range(1, 6)) and f3.stopped is None and not f3.limit_left    # the last five fit: no stop
    assert sum(1 for r in db.rows.values() if r[0] == "pending") == 25                    # each asked for once, never twice


def test_extractions_go_before_summaries_newest_first_and_the_summaries_take_what_is_left():
    t, db = task(), Db()
    summaries, extractions = works(12, first_doc=1), works(12, ge.TASK, first_doc=1)
    ge_worst = t._worst_case(extractions[0].request(PROMPTS[ge.TASK]), batch=True)
    f = run(t, db, summaries + extractions)
    kinds = {(o.work.task, o.status) for o in f.outcomes}
    assert (ge.TASK, "budget") not in kinds                    # every extraction went, ahead of every summary
    assert sorted(o.work.document_id for o in f.outcomes if o.work.task == ge.TASK and o.status == "submitted") == list(range(1, 13))
    went = sorted(o.work.document_id for o in f.outcomes if o.work.task == cs.TASK and o.status == "submitted")
    assert went and went == list(range(13 - len(went), 13))   # the summaries newest first, as many as the rest of the $5 holds
    assert f.run_estimate <= RUN_LIMIT_USD and f.stopped == "run_estimate"
    assert ge_worst < t._worst_case(summaries[0].request(PROMPTS[cs.TASK]), batch=True)


def test_the_send_order_is_extractions_summaries_tone_then_retries_each_newest_filing_first():
    t = EquityCallsAi()
    old, new = works(1, first_day=DAY)[0], works(1, first_doc=2, first_day=DAY + timedelta(days=30))[0]
    old_x, new_x = replace(old, task=ge.TASK), replace(new, task=ge.TASK)
    old_t, new_t = replace(old, task=ct.TASK), replace(new, task=ct.TASK)
    keyed = [(w, s) for w in (old, new, old_x, new_x, old_t, new_t) for s in (Step(1), Step(2, "note"))]
    order = [(w.task, w.document_id, s.attempt) for w, s in sorted(keyed, key=lambda ws: t._send_order(*ws))]
    assert order == [(ge.TASK, 2, 1), (ge.TASK, 1, 1), (cs.TASK, 2, 1), (cs.TASK, 1, 1), (ct.TASK, 2, 1), (ct.TASK, 1, 1),
                     (ge.TASK, 2, 2), (ge.TASK, 1, 2), (cs.TASK, 2, 2), (cs.TASK, 1, 2), (ct.TASK, 2, 2), (ct.TASK, 1, 2)]
    undated = replace(new, filed=None, call=replace(new.call, day=None))   # no day at all: after every dated call of its group
    assert t._send_order(undated, Step(1)) > t._send_order(old, Step(1))


def test_a_run_whose_first_target_alone_passes_the_limit_sends_nothing_as_before():
    fake = FakeClaude()
    t, db = task(fake), Db()
    t.run_limit = lambda: Decimal("0.30")                     # under one call's request and retry (about $0.46)
    f = run(t, db, works(3))
    assert sent(f) == [] and fake.create_calls == 0 and by_status(f, "budget") == [1, 2, 3]
    assert f.stopped == "run_estimate" and f.run_estimate == 0 and db.rows == {}
    alerts = {a.dedupe_key: a for a in batch.spend_alerts(f, Decimal("0.30"), partial=True)}
    assert alerts["ai_run_estimate"].active and "0 requests sent within it, 3 targets wait" in alerts["ai_run_estimate"].message


def test_a_runs_own_retry_already_in_its_estimate_still_goes_after_a_partial_send():
    t, db, calls = task(), Db(), works(25)
    f = run(t, db, calls)
    estimate = f.run_estimate
    newest = next(o for o in f.outcomes if o.work.document_id == 25)
    cid = t.custom_id(newest.work.request(PROMPTS[cs.TASK]), 25)
    db.rows[cid] = ["rejected", 1, "retried: bullet 1", {"retry_note": "fix bullet 1"}, "collected", 7, "rejected"]
    run(t, db, calls, f)                                      # the same run's next round, after collecting that reply
    assert newest.status == "submitted" and [b["requests"] for b in f.batch.submitted] == [10, 1]
    assert f.run_estimate == estimate and by_status(f, "budget") == list(range(1, 16))   # the rest still wait


def test_a_task_without_partial_send_still_sends_nothing_over_the_limit():
    assert AiBatch.partial_send is False and EquityCallsAi.partial_send is True
    fake = FakeClaude()
    t, db = task(fake), Db()
    t.partial_send = False                                    # the old rule, as `policy.ai_batch` keeps it
    f = run(t, db, works(25))
    assert fake.create_calls == 0 and sent(f) == [] and by_status(f, "budget") == list(range(1, 26))
    assert f.stopped == "run_estimate" and not f.limit_left and f.run_estimate > RUN_LIMIT_USD
    assert {o.reason for o in f.outcomes} == {f"run estimate ${f.run_estimate:.2f}"}
    alert = next(a for a in batch.spend_alerts(f, RUN_LIMIT_USD) if a.dedupe_key == "ai_run_estimate")
    assert alert.active and alert.message == f"an AI run estimated at ${f.run_estimate:.2f} (over $5) sent nothing more"
    assert "stopped_by" not in json.loads(batch.spend_check(f).detail)    # the old check's detail, unchanged


# ---------------------------------------------------------------------------------------------- which stop left them
def test_the_warning_names_how_many_went_and_how_many_wait_under_the_run_limit():
    t, db = task(), Db()
    f = run(t, db, works(25))
    check = batch.spend_check(f, partial=True)
    detail = json.loads(check.detail)
    assert not check.passed and detail["stopped_by"] == ["run_limit"]
    assert (detail["sent_requests"], detail["waiting_run_limit"], detail["waiting_monthly_switch"]) == (10, 15, 0)
    assert Decimal(detail["estimate_of_all_usd"]) > RUN_LIMIT_USD >= Decimal(detail["run_estimate_usd"])
    alerts = {a.dedupe_key.split(":")[0]: a for a in batch.spend_alerts(f, RUN_LIMIT_USD, partial=True)}
    assert alerts["ai_run_estimate"].active and "10 requests sent within it, 15 targets wait for the next run" in (
        alerts["ai_run_estimate"].message)
    assert "ai_budget" not in alerts                           # nothing spent yet in this run: the month alert isn't touched


def test_a_monthly_switch_lower_than_the_run_limit_is_what_stops_sending_and_says_so():
    t, db = task(), Db()
    f = run(t, db, works(5), budget=Decimal("0.50"))          # five calls fit the $5, but the switch holds two requests
    assert sent(f) == [1, 2] and f.stopped == "budget" and not f.limit_left    # under the limit the run's order holds, as before
    detail = json.loads(batch.spend_check(f, partial=True).detail)
    assert detail["stopped_by"] == ["monthly_switch"] and (detail["sent_requests"], detail["waiting_monthly_switch"]) == (2, 3)
    alerts = {a.dedupe_key.split(":")[0]: a for a in batch.spend_alerts(f, RUN_LIMIT_USD, partial=True)}
    assert alerts["ai_budget"].active and not alerts["ai_run_estimate"].active
    assert "the monthly switch, not the $5 run limit, left 3 targets waiting (2 requests sent in this run)" in (
        alerts["ai_budget"].message)


def test_when_both_stops_cut_each_alert_counts_its_own_targets():
    t, db = task(), Db()
    f = run(t, db, works(25), budget=Decimal("0.50"))         # the run limit keeps ten, the switch sends two of them
    assert sent(f) == [24, 25]
    detail = json.loads(batch.spend_check(f, partial=True).detail)
    assert detail["stopped_by"] == ["run_limit", "monthly_switch"]
    assert (detail["sent_requests"], detail["waiting_run_limit"], detail["waiting_monthly_switch"]) == (2, 15, 8)
    alerts = {a.dedupe_key.split(":")[0]: a for a in batch.spend_alerts(f, RUN_LIMIT_USD, partial=True)}
    assert alerts["ai_budget"].active and alerts["ai_run_estimate"].active
    assert "left 8 targets waiting" in alerts["ai_budget"].message and "15 targets wait" in alerts["ai_run_estimate"].message
