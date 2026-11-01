"""ft/ai/batch.py on its own (2B-03, #166): the batch steps a task mixes in, with a fake client and a fake database.
No database, no network, no AI credit. `policy.ai_batch`'s own tests (test_ai_batch_task.py) run the same steps
against the test database and stay unchanged."""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ft.ai import batch
from ft.ai.batch import BatchSteps, Call, Outcome, Plan, RunState, Step, chunks
from ft.ai.client import AIUnavailable


class Tick:
    """The poll's clock: sleeping moves it on at once."""

    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


class Req:
    def __init__(self, cid: str, note: str | None = None):
        self.task, self.cid, self.note = "t", cid, note


class Tgt:
    """A target: a task on a document; its request's custom_id comes from the document and the retry note."""
    task = "t"

    def __init__(self, n: int):
        self.n = n

    @property
    def target(self) -> str:
        return f"t:doc-{self.n}"

    @property
    def document_id(self) -> int:
        return self.n

    def request(self, prompt, note=None):
        return Req(f"{self.n}:{note or ''}", note)


class Steps(BatchSteps):
    """The mixin with the task's hooks faked: requests are keyed by `custom_id`, stored in a dict."""
    key = "test.steps"

    def __init__(self, tick: Tick | None = None):
        self.rows: dict[str, tuple] = {}     # custom_id -> the ops.ai_request row `_request_row` returns
        self.stored: list[tuple] = []        # (custom_id, in a transaction?)
        self.bare: list[str] = []
        self.in_tx = False
        self.tick = tick or Tick()
        self.sleep, self.monotonic = self.tick.sleep, self.tick.monotonic
        self.entries: dict[str, tuple] = {}

    @staticmethod
    def custom_id(req, doc_id):
        return req.cid

    def _request_row(self, db, custom_id):     # (status, batch id, reason, content, batch status, output id, out status)
        return self.rows.get(custom_id)

    def _entry(self, db, cid, doc_id, prompts, index):
        return self.entries.get(cid)

    def _store(self, tx, w, call, task_run_id, retry_note):
        self.stored.append((call.custom_id, self.in_tx, retry_note))
        call.output_id = 1

    def _insert_bare(self, db, cid, reply, task_run_id, spend, reason, extra, retry_note):
        self.bare.append(reason)

    def _recheck(self, ctx, f, p):
        raise AssertionError("no held drafts here")


class Checked:
    def __init__(self, ok: bool, note: str = "n"):
        self.ok, self._note, self.problems = ok, note, [] if ok else ["bad"]

    def reasons(self):
        return self.problems

    def note(self):
        return self._note


# ---------------------------------------------------------------------------------------------- retry, never twice
def test_an_unsent_input_is_step_one_and_an_answered_one_is_never_sent_again():
    t, w = Steps(), Tgt(1)
    assert t._next_step(None, w, None).step == Step(1)
    for status in ("succeeded", "rejected"):                       # billed, final: the same input is never resent
        t.rows["1:"] = (status, None, "rejected: x", {}, None, 5, "rejected")
        assert t._next_step(None, w, None).step is None
    t.rows["1:"] = ("errored", None, "batch errored", None, None, None, None)   # not billed: may be sent again
    assert t._next_step(None, w, None).step == Step(1)
    t.rows["1:"] = ("pending", 7, "", None, "submitted", None, None)            # waiting in an open batch
    p = t._next_step(None, w, None)
    assert p.step is None and p.in_batch is not None


def test_a_first_reply_stored_with_a_note_gets_one_retry_with_that_note_then_nothing():
    t, w = Steps(), Tgt(1)
    t.rows["1:"] = ("rejected", None, "retried: bullet 1", {"retry_note": "fix it"}, None, 5, "rejected")
    p = t._next_step(None, w, None)
    assert p.step == Step(2, "fix it") and p.custom_id == "1:fix it"
    t.rows["1:fix it"] = ("rejected", None, "rejected: bullet 1", {}, None, 6, "rejected")   # the second failure is final
    assert t._next_step(None, w, None).step is None
    t.rows["1:fix it"] = ("pending", 8, "", None, "submitted", None, None)
    assert t._next_step(None, w, None).in_batch is not None


# ---------------------------------------------------------------------------------------------- stored at once
class FakeDb:
    def __init__(self, steps: Steps, status="pending"):
        self.steps, self.status, self.executed, self.closed, self.broken = steps, status, [], False, False

    @contextmanager
    def transaction(self):
        self.steps.in_tx = True
        try:
            yield
        finally:
            self.steps.in_tx = False

    def execute(self, sql, args=()):
        self.executed.append((" ".join(sql.split()), args))
        return SimpleNamespace(fetchone=lambda: (self.status,), rowcount=1)


def ctx_of(db):
    return SimpleNamespace(fetcher=SimpleNamespace(db=db), task_run_id=9)


def reply(cost="0.002", stop="end_turn"):
    return SimpleNamespace(cost_usd=Decimal(cost), stop_reason=stop, message_id="m", model="m", usage=None)


def test_a_billed_reply_is_stored_in_its_own_transaction_with_the_run_spend_the_moment_it_arrives():
    t, w, f = Steps(), Tgt(1), RunState("batch")
    t.entries["1:"] = (w, w.request(None), 1)
    w.check = lambda req, rep: Checked(True)
    db = FakeDb(t)
    t._store_result(ctx_of(db), f, {}, "1:", 1, reply(), {})
    assert t.stored == [("1:", True, None)]                         # inside a transaction, no retry note
    assert f.spend == Decimal("0.002") and f.outcomes[0].status == "published"
    assert any("update ops.task_run set ai_cost_usd" in sql and args[0] == Decimal("0.0020") for sql, args in db.executed)


def test_a_failed_check_on_the_first_reply_stores_a_retry_note_and_the_second_failure_is_rejected():
    t, w, f = Steps(), Tgt(1), RunState("batch")
    w.check = lambda req, rep: Checked(False, "note one.")
    t.entries["1:"] = (w, w.request(None), 1)
    t._store_result(ctx_of(FakeDb(t)), f, {}, "1:", 1, reply(), {})
    assert t.stored[-1][2] == "note one." and f.outcomes[0].status == "retrying"
    t.entries["1:note one."] = (w, w.request(None, "note one."), 2)
    t._store_result(ctx_of(FakeDb(t)), f, {}, "1:note one.", 1, reply(), {})
    assert t.stored[-1][2] is None and f.outcomes[0].status == "rejected"


def test_a_reply_another_run_already_stored_is_not_stored_again_nor_billed_twice():
    t, w, f = Steps(), Tgt(1), RunState("batch")
    w.check = lambda req, rep: Checked(True)
    t.entries["1:"] = (w, w.request(None), 1)
    t._store_result(ctx_of(FakeDb(t, status="succeeded")), f, {}, "1:", 1, reply(), {})
    assert t.stored == [] and f.spend == 0


def test_a_reply_whose_target_cant_be_built_any_more_is_stored_bare_as_stale_with_its_cost():
    t, f = Steps(), RunState("batch")
    t._store_result(ctx_of(FakeDb(t)), f, {}, "gone", 1, reply("0.003"), {})
    assert t.bare and t.bare[0].startswith("stale:") and f.spend == Decimal("0.003") and f.batch.results["stale"] == 1


def test_a_reply_that_didnt_end_its_turn_is_a_failed_attempt_not_a_checked_one():
    t, w, f = Steps(), Tgt(1), RunState("batch")
    w.check = lambda req, rep: pytest.fail("a cut-off reply isn't checked")
    t.entries["1:"] = (w, w.request(None), 1)
    t._store_result(ctx_of(FakeDb(t)), f, {}, "1:", 1, reply(stop="max_tokens"), {})
    assert t.stored[-1][2] and "max_tokens" in t.stored[-1][2]


# ---------------------------------------------------------------------------------------------- poll limit
def test_a_poll_that_reaches_fifteen_minutes_stops_with_the_batch_still_open_for_the_next_run():
    t, f = Steps(), RunState("batch")
    t._plan = lambda ctx, f, prompts: []
    t._collect = lambda ctx, f, prompts: (f.batch.open.append((1, "msgbatch_1", None)) or 0)
    t._batch_rounds(ctx_of(None), f, {}, poll=batch.BATCH_POLL_SECONDS)
    assert t.tick.sleeps[:2] == [30.0, 60.0] and sum(t.tick.sleeps) <= batch.BATCH_POLL_SECONDS
    assert sum(t.tick.sleeps) > batch.BATCH_POLL_SECONDS - 60 and f.batch.open       # resumes: it's still open


def test_a_watch_run_with_no_poll_reads_once_and_never_sleeps():
    t, f = Steps(), RunState("batch")
    t._plan = lambda ctx, f, prompts: []
    t._collect = lambda ctx, f, prompts: (f.batch.open.append((1, "msgbatch_1", None)) or 0)
    t._batch_rounds(ctx_of(None), f, {}, poll=0)
    assert t.tick.sleeps == []


# ---------------------------------------------------------------------------------------------- S2's skip path
def test_ai_unavailable_before_the_first_request_sends_nothing_and_skips_every_target():
    t, f = Steps(), RunState("batch")
    targets = [Tgt(1), Tgt(2)]
    f.targets = list(targets)
    t._plan = lambda ctx, f, prompts: [(w, Step(1)) for w in targets]
    t._collect = lambda ctx, f, prompts: 0

    def no_key():
        raise AIUnavailable("no_key", "NoKey", None, "ANTHROPIC_API_KEY is not set")
    t._claude = no_key
    t._batch_path(ctx_of(FakeDb(t)), f, {"t": None}, poll=900)
    assert f.unavailable == "no_key" and {o.status for o in f.outcomes} == {"skipped"}
    assert f.batch.submitted == [] and t.stored == [] and t.tick.sleeps == []


def test_ai_unavailable_mid_batch_stops_further_requests_and_keeps_what_was_stored():
    t, f = Steps(), RunState("batch")
    a, b, c = Tgt(1), Tgt(2), Tgt(3)
    f.targets = [a, b, c]
    calls = {"collect": 0}

    def collect(ctx, f, prompts):
        calls["collect"] += 1
        if calls["collect"] == 1:
            f.outcome(a, "published", "stored the moment it was billed")
            return 1
        raise AIUnavailable("billing", "BillingError", 400, "no credits")

    def submit(ctx, f, todo, prompts):
        f.outcome(b, "submitted", "msgbatch_1")
        f.batch.open.append((1, "msgbatch_1", None))

    t._collect, t._submit = collect, submit
    t._plan = lambda ctx, f, prompts: [(b, Step(1))]
    t._batch_path(ctx_of(None), f, {}, poll=900)
    status = {o.work.target: o.status for o in f.outcomes}
    assert status == {a.target: "published", b.target: "submitted", c.target: "skipped"}   # nothing stored is undone
    assert f.unavailable == "billing"


# ---------------------------------------------------------------------------------------------- spend and limits
def test_the_run_estimate_over_the_limit_sends_nothing_and_raises_the_alert():
    t, f = Steps(), RunState("batch", budget=Decimal(15))
    w = Tgt(1)
    t.run_limit = lambda: Decimal("0.0001")
    t._claude = lambda: SimpleNamespace(check=lambda req: None)
    t._worst_case = lambda req, batch=False: Decimal(1)
    t._submit(ctx_of(FakeDb(t)), f, [(w, Step(1))], {"t": None})
    assert f.stopped == "run_estimate" and f.outcomes[0].status == "budget" and f.batch.submitted == []
    assert [a.dedupe_key for a in batch.spend_alerts(f, Decimal("0.0001")) if a.active] == ["ai_run_estimate"]
    assert not batch.spend_check(f).passed


def test_the_monthly_budget_stops_a_batch_before_it_is_sent():
    t, f = Steps(), RunState("batch", budget=Decimal("0.50"), month_spend=Decimal("0.49"))
    t._claude = lambda: SimpleNamespace(check=lambda req: None)
    t._worst_case = lambda req, batch=False: Decimal("0.05")
    t._submit(ctx_of(FakeDb(t)), f, [(Tgt(1), Step(1))], {"t": None})
    assert f.stopped == "budget" and f.batch.submitted == []
    assert any(a.dedupe_key.startswith("ai_budget:") and a.active for a in batch.spend_alerts(f, Decimal(5)))


# ---------------------------------------------------------------------------------------------- small parts
def test_batches_are_split_under_the_request_and_size_limits():
    items = [(None, SimpleNamespace(params=lambda batch: {"x": "y" * 100}), f"c{i}", Decimal(1)) for i in range(5)]
    assert [len(c) for c in chunks(items, max_requests=2)] == [2, 2, 1]
    assert [len(c) for c in chunks(items, max_bytes=500)] == [1, 1, 1, 1, 1]
    assert [len(c) for c in chunks(items)] == [5] and list(chunks([])) == []


def test_a_call_is_held_only_when_the_check_passed_with_a_hold_and_has_no_problems():
    c = Call("x", None, 1, checked=SimpleNamespace(ok=False, hold="other", problems=[]))
    assert c.held and not c.ok
    assert not Call("x", None, 1, checked=SimpleNamespace(ok=False, hold=None, problems=["p"])).held
    assert Call("x", None, 1, failed="max_tokens").reasons() == ["the reply stopped with max_tokens"]


def test_an_outcome_is_created_once_per_target_and_first_does_not_overwrite_what_the_run_did():
    f = RunState("batch")
    w = Tgt(1)
    f.outcome(w, "submitted", "b1")
    f.outcome(w, "left", "planning", first=True)
    assert [(o.status, o.reason) for o in f.outcomes] == [("submitted", "b1")] and f.targets == [w]
    f.outcome(w, "published", "stored")
    assert f.outcomes[0].status == "published" and isinstance(f.outcomes[0], Outcome) and Plan
