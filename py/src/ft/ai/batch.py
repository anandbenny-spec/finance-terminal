"""The AI batch steps (Architecture 03 §1.3; Spec 02B §5.5, decision 19, §13.3), generalised out of
policy.ai_batch at their second use (2B-03, #166; Plan 02 §7.1 rule 2) with no change in behaviour.

The steps: submit through the Batch API; poll for up to 15 minutes; collect each ended batch; store each billed reply
at once (its own small transaction, locking its request's row); track the spend (the run's, the month's, the open
batches' worst case; the $5 run estimate and the monthly switch); retry once with a note, then reject (the task's
alert); and S2's skip path (#197: `AIUnavailable` caught before sending and between requests, nothing sent or lost,
the outcome "skipped: AI unavailable", a green run).

A task mixes in `BatchSteps` and supplies what is its own. Its targets (`Target`) say which document they are on and
build their request and check a reply (`check`); its state (`RunState`) is the run's outcomes, batch log and spend. The task provides:
`_entry` (a stored custom_id back to its target), `_store` (one billed reply:
the output, citations, publication or rejection, and the request's final status; in one transaction), `_insert_bare`
(a billed reply stored without its text), `_recheck` (a held draft checked again, without a request),
`_scoped_documents_sql` (tests only) and `_prompt_weight`; and `run_limit` (the $5 stop, read at call time).
The real-time path (`policy.ai_batch`'s watch on a decision day) stays in the task and uses the same helpers.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import psycopg

from ft.ai.client import (
    CHARS_PER_TOKEN,
    MAX_REQUEST_TOKENS,
    PRICES,
    REQUEST_OVERHEAD_TOKENS,
    TASKS,
    AIUnavailable,
    BatchInfo,
    CallError,
    Client,
    Reply,
    Request,
    TooLong,
    batch_custom_id,
    maybe_created,
    price_entry,
)
from ft.jobs.runner import Alert, Check

RUN_LIMIT_USD = Decimal(5)            # CLAUDE.md §7: an AI run estimated above $5 is Anand's call

BATCH_POLL_SECONDS = 15 * 60          # Architecture 03 §1.3: polled for up to 15 minutes in the run
BATCH_WAITS = (30.0, 60.0)            # the first wait between reads, then every later one
BATCH_MAX_REQUESTS = 5_000            # per batch (the API allows 100,000)
BATCH_MAX_BYTES = 20_000_000          # per batch, as JSON (the API allows 256 MB; smaller uploads finish well inside
                                      # the create timeout, so an accepted batch is rarely left unacknowledged)
BATCH_STUCK = timedelta(hours=26)     # the API ends every batch within 24 hours: one open longer isn't being read
BATCH_RESULTS_KEPT = timedelta(days=29)   # the API keeps results this long; a batch gone before then isn't given up
CLOCK_SLACK = timedelta(minutes=2)    # between our clock and the API's, when looking for a batch after a lost create
EXPOSURE_PROMPT_CHARS = 6_000         # the prompt, instruction and a retry note, for the open batches' worst case

class Skip(Exception):
    """The request isn't pending any more: another run stored its reply."""

@dataclass(frozen=True)
class Step:
    """What a target needs next: attempt 1, or attempt 2 with the note the stored first reply left."""
    attempt: int
    note: str | None = None


class Target(Protocol):
    """What the steps need of a target (policy.ai_batch's `Work`): a task on a document."""
    task: str

    @property
    def target(self) -> str: ...
    @property
    def document_id(self) -> int: ...
    def request(self, prompt, note: str | None = None) -> Request: ...
    def check(self, req: Request, reply: Reply): ...


@dataclass
class Plan:
    """A target's state, from its requests so far."""
    work: Target
    step: Step | None                 # None: nothing to send now
    why: str
    built: list[tuple[str, Request, int]]   # the requests built for it: (custom_id, request, attempt)
    in_batch: Request | None = None   # the request waiting in an open batch
    held: tuple[int, dict] | None = None    # an extraction held as a draft: (its output's id, its content)

    @property
    def custom_id(self) -> str:       # the step's request
        return self.built[-1][0]

@dataclass
class Call:
    """One request sent (or refused before sending)."""
    custom_id: str
    request: Request
    attempt: int
    reply: Reply | None = None
    checked: object | None = None     # the task's check of the reply: `.ok`, `.reasons()`, `.note()`; `.hold` if it can hold
    failed: str | None = None         # the stop reason of a reply that didn't end its turn
    error: str | None = None          # CallError: not billed, no output
    output_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.checked is not None and self.checked.ok and self.failed is None

    @property
    def held(self) -> bool:
        """A check that passed but is held as a draft with an alert (an extraction's `other`, or a disagreement with
        B6's parse): never retried."""
        c = self.checked
        return self.failed is None and getattr(c, "hold", None) is not None and not c.problems

    def reasons(self) -> list[str]:
        if self.failed:
            return [f"the reply stopped with {self.failed}"]
        return self.checked.reasons() if self.checked else []

    def note(self) -> str:
        """The retry's note about this reply."""
        return "; ".join(self.reasons()) + "." if self.failed else self.checked.note()


@dataclass
class Outcome:
    work: Target
    # real time: published | rejected | held (an extraction kept as a draft with an alert) | errored | done | waiting |
    # too_long | budget | skipped (AI unavailable: no key, refused key, no credits; nothing sent, nothing lost)
    # batch, also: in_batch (waiting in an earlier run's batch) | submitted | retrying (its retry is next) | expired |
    # left (sendable, but already sent once in this run: the next run)
    status: str
    calls: list[Call] = field(default_factory=list)
    reason: str = ""


@dataclass
class BatchLog:
    """What a batch-mode run did with the Batch API (the `batches` check)."""
    submitted: list[dict] = field(default_factory=list)   # {"batch", "requests"}
    adopted: list[str] = field(default_factory=list)      # batches found after a create that failed ambiguously
    held_for_watch: list[int] = field(default_factory=list)   # decision documents left to the open watch window
    collected: list[dict] = field(default_factory=list)   # {"batch", "requests", "turnaround_s"}
    open: list[tuple[int, str, datetime]] = field(default_factory=list)   # still processing: (row id, batch, sent)
    results: Counter = field(default_factory=Counter)     # succeeded, errored, canceled, expired, stale, unknown
    errors: list[str] = field(default_factory=list)       # creates and status reads that failed
    read_errors: dict[str, str] = field(default_factory=dict)   # results reads that broke off and weren't finished
    sent_ids: set[str] = field(default_factory=set)       # custom_ids sent in this run: never twice in one run
    counted: set[str] = field(default_factory=set)        # targets already in the run estimate
    exposure: Decimal = Decimal(0)                        # worst case of the requests waiting in open batches


def chunks(items: list, *, max_requests: int = BATCH_MAX_REQUESTS, max_bytes: int = BATCH_MAX_BYTES):
    """Split (work, request, custom_id, worst case) items into batches under the request and size limits."""
    chunk, size = [], 0
    for it in items:
        n = len(json.dumps(it[1].params(batch=True), ensure_ascii=False).encode()) + 200
        if chunk and (len(chunk) >= max_requests or size + n > max_bytes):
            yield chunk
            chunk, size = [], 0
        chunk.append(it)
        size += n
    if chunk:
        yield chunk


@dataclass
class RunState:
    """A run's state, as far as the steps go (`policy.ai_batch`'s `Fetched` adds its own)."""
    mode: str                         # 'realtime' | 'batch'
    targets: list = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    batch: BatchLog = field(default_factory=BatchLog)
    spend: Decimal = Decimal(0)
    month_spend: Decimal = Decimal(0)
    budget: Decimal = Decimal(0)
    stopped: str | None = None        # 'budget' | 'run_estimate'
    run_estimate: Decimal = Decimal(0)
    unavailable: str | None = None    # Claude can't be used: 'no_key' | 'auth' | 'billing' (AIUnavailable.reason, #197)
    _by_target: dict[str, Outcome] = field(default_factory=dict, repr=False)

    def outcome(self, w: Target, status: str | None = None, reason: str = "", *, first: bool = False) -> Outcome:
        """The target's outcome in this run, created on first use (and the target added, for the alerts). With
        `first`, a status is set only on a new outcome (planning doesn't overwrite what the run did)."""
        o = self._by_target.get(w.target)
        if o is None:
            o = self._by_target[w.target] = Outcome(w, status or "done", reason=reason)
            self.outcomes.append(o)
            if w.target not in {t.target for t in self.targets}:
                self.targets.append(w)
        elif status is not None and not first:
            o.status, o.reason = status, reason
        return o


def batches_check(f: RunState):
    """The `batches` check (non-blocking): what a batch-mode run did with the Batch API."""
    b = f.batch
    now = datetime.now(UTC)
    stuck = [pid for _, pid, at in b.open if now - at > BATCH_STUCK]
    errors = list(dict.fromkeys(b.errors + list(b.read_errors.values())))   # a poll repeats its reads
    trouble = (errors or stuck or b.results["errored"] or b.results["canceled"] or b.results["expired"]
               or b.results["unknown"] or b.results["unstorable"])
    return Check("batches", not trouble, json.dumps({
        "submitted": b.submitted, "adopted": b.adopted, "collected": b.collected,
        "open": [pid for _, pid, _ in b.open], "stuck": stuck, "results": dict(sorted(b.results.items())),
        "left_for_next_run": sum(1 for o in f.outcomes if o.status == "left"),
        "held_for_watch": b.held_for_watch, "errors": errors}), blocking=False)


def spend_check(f: RunState):
    """The `ai_spend` check (non-blocking)."""
    return Check("ai_spend", f.stopped is None, json.dumps({
        "run_usd": str(f.spend), "month_usd": str(f.month_spend + f.spend), "budget_usd": str(f.budget),
        "in_batches_worst_usd": str(f.batch.exposure.quantize(Decimal("0.0001"))),
        "stopped": f.stopped, "run_estimate_usd": str(f.run_estimate.quantize(Decimal("0.0001")))}),
        blocking=False)


def spend_alerts(f: RunState, limit: Decimal) -> list:
    """The monthly-budget and run-estimate alerts: raised by the stop, resolved by a run that finds room."""
    out = []
    month = datetime.now(UTC).strftime("%Y-%m")
    if f.stopped == "budget":
        out.append(Alert(f"ai_budget:{month}", True, "error",
                         f"AI spend reached the ${f.budget} monthly budget; requests stopped"))
    elif f.spend > 0:                                  # a run spent within the budget: it's been raised
        out.append(Alert(f"ai_budget:{month}", False))
    return out + [Alert("ai_run_estimate", f.stopped == "run_estimate", "error",
                        f"an AI run estimated at ${f.run_estimate:.2f} (over ${limit}) sent nothing more")]


class BatchSteps:
    """The mixin: see the module docstring for the hooks a task provides."""
    key: str
    scope: frozenset[str] | None = None   # tests: only documents of these meetings (event slugs)
    claude: Client | None = None          # tests inject; otherwise built from ANTHROPIC_API_KEY on first use
    sleep: Callable[[float], None] = staticmethod(time.sleep)       # tests inject (the batch poll)
    monotonic: Callable[[], float] = staticmethod(time.monotonic)

    # ------------------------------------------------------------------ what the task provides
    def run_limit(self) -> Decimal:
        """The run estimate over which nothing more is sent (read at call time)."""
        return RUN_LIMIT_USD

    def _scoped_documents(self) -> tuple[str, list] | None:
        """Tests only: (a select of document ids, its arguments) limiting the steps to part of the data, or None."""
        return None

    def _prompt_weight(self, task: str) -> int:
        """How many times a document's length a request of this task can hold (the open batches' worst case)."""
        return 1

    def _entry(self, db, cid: str, doc_id: int | None, prompts, index: dict):
        """(target, request, attempt) for a stored custom_id, from its document's targets as they are now; None if
        the request no longer matches any of them (stale)."""
        raise NotImplementedError

    def _store(self, tx, w, call: Call, task_run_id: int, retry_note: str | None) -> None:
        """One billed reply, stored in the given transaction (see the module docstring)."""
        raise NotImplementedError

    def _insert_bare(self, db, cid: str, reply: Reply, task_run_id: int, spend: Decimal, reason: str, extra: dict,
                     retry_note: str | None) -> None:
        """A billed reply stored without its text (stale, or refused by the database), its request rejected."""
        raise NotImplementedError

    def _recheck(self, ctx, f: RunState, p: Plan) -> None:
        """A held draft's stored answer, checked again without a request."""
        raise NotImplementedError

    @staticmethod
    def _request_row(db, custom_id: str):
        """(status, batch id, reject reason, the output's content, the batch's status, the output's id, the output's
        status) of a request sent before, or None."""
        return db.execute("""select r.status, r.ai_batch_id, coalesce(r.reject_reason, ''), o.content, b.status, o.id,
                                    o.status
                               from ops.ai_request r left join core.ai_output o on o.id = r.ai_output_id
                               left join ops.ai_batch b on b.id = r.ai_batch_id
                              where r.custom_id = %s""", (custom_id,)).fetchone()

    @staticmethod
    def custom_id(req: Request, doc_id: int) -> str:
        return f"{req.task}:doc-{doc_id}:{req.input_sha256.hex()[:16]}"

    @staticmethod
    def _sendable(row) -> bool:
        """Never sent, or sent without a usable answer: errored or expired (not billed), or stale (its target couldn't
        be built when the reply came: it's sent again only if that same input comes back), each once its batch, if
        any, has been read to the end; or pending outside a batch (its reply wasn't stored). `_claim` holds the same
        rule."""
        if row is None:
            return True
        status, batch, reason, _, batch_status = row[:5]
        if status in ("errored", "expired") or (status == "rejected" and reason.startswith("stale:")):
            return batch is None or batch_status in ("collected", "failed")
        return status == "pending" and batch is None

    def _next_step(self, db, w: Target, prompt) -> Plan:
        """What the target needs next, from its requests so far."""
        req = w.request(prompt)
        cid = self.custom_id(req, w.document_id)
        row = self._request_row(db, cid)
        built = [(cid, req, 1)]
        if self._sendable(row):
            return Plan(w, Step(1), "not sent yet" if row is None else f"attempt 1 {row[0]}", built)
        status, batch, reason, content = row[:4]
        if batch is not None and status == "pending":
            return Plan(w, None, "in a batch", built, in_batch=req)
        if status == "rejected" and reason.startswith("retried:") and (content or {}).get("retry_note"):
            note = content["retry_note"]
            req2 = w.request(prompt, note=note)
            cid2 = self.custom_id(req2, w.document_id)
            row2 = self._request_row(db, cid2)
            built.append((cid2, req2, 2))
            if self._sendable(row2):
                return Plan(w, Step(2, note), "the retry wasn't answered yet", built)
            if row2[1] is not None and row2[0] == "pending":
                return Plan(w, None, "the retry is in a batch", built, in_batch=req2)
            return self._held_plan(Plan(w, None, f"attempt 2 {row2[0]}", built), row2)
        return self._held_plan(Plan(w, None, f"attempt 1 {status}", built), row)

    @staticmethod
    def _held_plan(p: Plan, row) -> Plan:
        """An extraction whose answer was held as a draft (`succeeded`, its output still a draft with a hold)."""
        status, _, _, content, _, out_id, out_status = row
        if status == "succeeded" and out_status == "draft" and (content or {}).get("hold"):
            p.why, p.held = f"held: {content['hold']}", (out_id, content)
        return p

    def _claude(self) -> Client:
        if self.claude is None:
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise AIUnavailable("no_key", "NoKey", None, "ANTHROPIC_API_KEY is not set")
            self.claude = Client(key)
        return self.claude

    @staticmethod
    def _claim(db, w: Target, req: Request, cid: str) -> bool:
        """The request's 'pending' row, written before it's sent: inserted, or taken over from a row that may be sent
        again (`_sendable`'s rule). False if it was answered (or batched) meanwhile."""
        return bool(db.execute(
            """insert into ops.ai_request (custom_id, ai_task, document_id, input_sha256, model, prompt_version, status)
               values (%s, %s, %s, %s, %s, %s, 'pending')
               on conflict (custom_id) do update set status = 'pending', reject_reason = null, ai_batch_id = null,
                 ai_output_id = null, created_at = now()
               where ((ops.ai_request.status in ('errored', 'expired')
                       or (ops.ai_request.status = 'rejected' and ops.ai_request.reject_reason like 'stale:%%'))
                      and (ops.ai_request.ai_batch_id is null
                           or exists (select 1 from ops.ai_batch b where b.id = ops.ai_request.ai_batch_id
                                         and b.status in ('collected', 'failed'))))
                  or (ops.ai_request.status = 'pending' and ops.ai_request.ai_batch_id is null)""",
            (cid, req.task, w.document_id, req.input_sha256, req.model_id, req.prompt.version)).rowcount)

    @staticmethod
    def _pending(db, cid: str) -> bool:
        """Lock the request's row for this transaction: is it still waiting for its reply?"""
        row = db.execute("select status from ops.ai_request where custom_id = %s for update", (cid,)).fetchone()
        return row is not None and row[0] == "pending"

    @staticmethod
    def _too_long(db, w: Target, req: Request, cid: str) -> None:
        db.execute("""insert into ops.ai_request (custom_id, ai_task, document_id, input_sha256, model, prompt_version,
                                                  status, reject_reason)
                      values (%s, %s, %s, %s, %s, %s, 'rejected', 'rejected: too_long')
                      on conflict (custom_id) do nothing""",
                   (cid, req.task, w.document_id, req.input_sha256, req.model_id, req.prompt.version))

    @staticmethod
    def _skip_rest(f: RunState, e: AIUnavailable) -> None:
        """AI can't be used (#197): every target without an outcome yet is skipped, not failed. What was stored stays,
        nothing new is published, and the run goes on (the embeddings, the other tasks' loads, the checks)."""
        f.unavailable = f.unavailable or e.reason
        for w in list(f.targets):
            f.outcome(w, "skipped", f"AI unavailable ({e.reason})", first=True)

    def _bound(self, model: str, tokens: int, max_tokens: int, *, batch: bool) -> Decimal:
        """An upper bound on a request's cost: every input token at the dearest rate it can be billed at (a 5-minute
        cache write in real time; plain input in a batch, which is uncached), and max_tokens of output; in a batch, at
        the batch price."""
        today = self.claude.today() if self.claude is not None else datetime.now(UTC).date()
        m, factor, _ = price_entry(model, today, path=self.claude.prices if self.claude is not None else PRICES)
        p = m["standard"]
        rate_in = Decimal(str(p["input"])) if batch else max(Decimal(str(p[k])) for k in ("input", "cache_write_5m"))
        cost = (Decimal(tokens) * rate_in + Decimal(max_tokens) * Decimal(str(p["output"]))) / Decimal(1_000_000)
        return cost * factor if batch else cost

    def _worst_case(self, req: Request, *, batch: bool = False) -> Decimal:
        return self._bound(req.model_id, req.estimated_tokens(), req.spec.max_tokens, batch=batch)

    def _exposure(self, db) -> Decimal:
        """The worst case of every request of this task still waiting in an open batch, whatever its target is now
        (a request whose prompt or document has changed since is still billed): its document's length (four times
        over for a diff summary: both documents and the redline's quotes), plus the prompt, at the batch price."""
        where, args = ["r.status = 'pending'", "b.status in ('submitted', 'ended')", "tr.task_key = %s"], [self.key]
        if (scoped := self._scoped_documents()) is not None:
            where.append(f"r.document_id in ({scoped[0]})")
            args += scoped[1]
        rows = db.execute(f"""select r.ai_task, r.model, coalesce(d.char_count, 0)
                                from ops.ai_request r
                                join ops.ai_batch b on b.id = r.ai_batch_id
                                join ops.task_run tr on tr.id = b.task_run_id
                                left join core.document d on d.id = r.document_id
                               where {' and '.join(where)}""", args).fetchall()
        total = Decimal(0)
        for task, model, chars in rows:
            k = self._prompt_weight(task)
            tokens = min(MAX_REQUEST_TOKENS, math.ceil((k * chars + EXPOSURE_PROMPT_CHARS) / CHARS_PER_TOKEN)
                         + REQUEST_OVERHEAD_TOKENS)
            spec = TASKS.get(task)
            total += self._bound(model, tokens, spec.max_tokens if spec else 8_000, batch=True)
        return total

    # ------------------------------------------------------------------ the Batch API (daily, refresh, backfill)
    def _batch_path(self, ctx, f: RunState, prompts, *, poll: float) -> None:
        """Collect what's ended, submit what's needed, and read the open batches until they end or `poll` seconds
        pass; a round that stored something plans again (retries, resends) and submits those too."""
        try:
            self._batch_rounds(ctx, f, prompts, poll=poll)
        except AIUnavailable as e:                          # no key, key refused or no credits: skip, don't fail
            self._skip_rest(f, e)

    def _batch_rounds(self, ctx, f: RunState, prompts, *, poll: float) -> None:
        deadline = self.monotonic() + poll
        wait = BATCH_WAITS[0]
        todo = self._plan(ctx, f, prompts)
        while True:
            if self._collect(ctx, f, prompts):
                todo = self._plan(ctx, f, prompts)
            if todo:
                self._submit(ctx, f, todo, prompts)
                todo = []
            if not f.batch.open or self.monotonic() + wait > deadline:
                return
            self.sleep(wait)
            wait = BATCH_WAITS[1]

    def _plan(self, ctx, f: RunState, prompts) -> list[tuple[Target, Step]]:
        """Every target's next step (a held extraction is checked again); the worst case of what waits in open
        batches (for the budget)."""
        db = ctx.fetcher.db
        todo: list[tuple[Target, Step]] = []
        f.batch.exposure = self._exposure(db)
        for w in list(f.targets):
            p = self._next_step(db, w, prompts[w.task])
            if p.held is not None:
                self._recheck(ctx, f, p)
            elif p.step is None:
                f.outcome(w, "in_batch" if p.in_batch is not None else "done", p.why, first=True)
            elif p.custom_id in f.batch.sent_ids:
                f.outcome(w, "left", f"{p.why}; sent once in this run already: the next run sends it")
            else:
                todo.append((w, p.step))
        return todo

    def _submit(self, ctx, f: RunState, todo: list[tuple[Target, Step]], prompts) -> None:
        db = ctx.fetcher.db
        if f.stopped:
            for w, _ in todo:
                f.outcome(w, "budget", f"after {f.stopped}")
            return
        claude = self._claude()                             # no key: AIUnavailable, up to `_batch_path`
        items = []                                          # (work, step, request, custom_id, worst case)
        for w, step in todo:
            req = w.request(prompts[w.task], note=step.note)
            cid = self.custom_id(req, w.document_id)
            try:
                claude.check(req)
            except TooLong as e:
                self._too_long(db, w, req, cid)
                f.outcome(w, "too_long", str(e))
                continue
            items.append((w, step, req, cid, self._worst_case(req, batch=True)))
        new = sum((Decimal(3 - s.attempt) * wc for w, s, _, _, wc in items if w.target not in f.batch.counted),
                  Decimal(0))                               # a retry each, at worst, counted once per target
        if f.run_estimate + new > self.run_limit():
            f.run_estimate += new
            f.stopped = "run_estimate"
            for w, *_ in items:
                f.outcome(w, "budget", f"run estimate ${f.run_estimate:.2f}")
            return
        f.run_estimate += new
        f.batch.counted |= {w.target for w, *_ in items}
        chosen = []
        committed = f.month_spend + f.spend + f.batch.exposure
        for w, step, req, cid, wc in items:
            if f.stopped or committed + wc > f.budget:
                f.stopped = "budget"
                f.outcome(w, "budget", f"month-to-date ${f.month_spend + f.spend:.4f}, ${f.batch.exposure:.4f} at "
                                       f"worst in open batches, of ${f.budget}")
                continue
            if not self._claim(db, w, req, cid):            # answered or batched meanwhile: never twice
                f.outcome(w, "done", f"{cid} was answered before")
                continue
            committed += wc
            chosen.append((w, req, cid, wc))
        for chunk in chunks(chosen):
            cids = [cid for _, _, cid, _ in chunk]
            f.batch.sent_ids |= set(cids)
            started = datetime.now(UTC)
            try:
                info = claude.create_batch([(batch_custom_id(cid), req) for _, req, cid, _ in chunk])
            except AIUnavailable as e:                      # nothing was made: the claimed requests go back to errored
                db.execute("""update ops.ai_request set status = 'errored', reject_reason = %s
                               where custom_id = any(%s)""", (f"batch unavailable {e.reason}", cids))
                raise
            except CallError as e:
                info = self._find_created(db, f, claude, len(chunk), started, e) if maybe_created(e) else None
                if info is None:
                    db.execute("""update ops.ai_request set status = 'errored', reject_reason = %s
                                   where custom_id = any(%s)""",
                               ("batch " + e.kind + (f" {e.status}" if e.status else ""), cids))
                    f.batch.errors.append(f"create: {e}")
                    for w, *_ in chunk:
                        f.outcome(w, "errored", str(e))
                    continue
                f.batch.adopted.append(info.id)
            # In the run's log before it's recorded, so a batch whose row can't be written can still be found.
            print(f"  {self.key}: batch {info.id} holds {len(chunk)} requests")
            with db.transaction():
                bid, at = db.execute("""insert into ops.ai_batch (provider_batch_id, task_run_id, request_count)
                                        values (%s, %s, %s) returning id, submitted_at""",
                                     (info.id, ctx.task_run_id, len(chunk))).fetchone()
                db.execute("update ops.ai_request set ai_batch_id = %s where custom_id = any(%s)", (bid, cids))
            f.batch.submitted.append({"batch": info.id, "requests": len(chunk)})
            f.batch.open.append((bid, info.id, at))
            f.batch.exposure += sum((wc for *_, wc in chunk), Decimal(0))
            for w, *_ in chunk:
                f.outcome(w, "submitted", info.id)

    def _find_created(self, db, f: RunState, claude: Client, n: int, started: datetime,
                      err: CallError) -> BatchInfo | None:
        """After a create that failed ambiguously (`client.maybe_created`): the batch it may have made, if exactly one
        of the workspace's newest batches was created since, holds `n` requests and isn't one of ours already."""
        try:
            recent = claude.recent_batches()
        except AIUnavailable:
            raise
        except CallError as e:
            f.batch.errors.append(f"create: {err}; looking for it: {e}")
            return None
        known = {r[0] for r in db.execute("select provider_batch_id from ops.ai_batch where submitted_at >= %s",
                                          (started - timedelta(days=1),)).fetchall()}
        found = [b for b in recent if b.id not in known and b.created_at >= started - CLOCK_SLACK
                 and sum(b.counts.values()) == n]
        if len(found) == 1:
            return found[0]
        if found:
            f.batch.errors.append(f"create: {err}; {len(found)} batches could be it, none adopted")
        return None

    def _open_batches(self, db) -> list[tuple[int, str, datetime]]:
        """This task's batches not yet collected (another task's batches are its own to read)."""
        where, args = ["b.status in ('submitted', 'ended')",
                       "exists (select 1 from ops.task_run tr where tr.id = b.task_run_id and tr.task_key = %s)"], \
            [self.key]
        if (scoped := self._scoped_documents()) is not None:     # tests: only batches holding requests in scope
            where.append(f"""exists (select 1 from ops.ai_request r
                                    where r.ai_batch_id = b.id and r.document_id in ({scoped[0]}))""")
            args += scoped[1]
        return db.execute(f"""select b.id, b.provider_batch_id, b.submitted_at from ops.ai_batch b
                               where {' and '.join(where)} order by b.id""", args).fetchall()

    def _collect(self, ctx, f: RunState, prompts) -> int:
        """Read every open batch; collect each one that has ended. Returns how many requests changed state."""
        db = ctx.fetcher.db
        rows = self._open_batches(db)
        f.batch.open = []
        if not rows:
            return 0
        claude = self._claude()                             # no key: AIUnavailable, up to `_batch_path`
        changed = 0
        index: dict[int, dict] = {}          # document id -> {custom_id: (work, request, attempt)}, this pass
        for bid, provider_id, at in rows:
            try:
                info = claude.batch(provider_id)
            except AIUnavailable:
                f.batch.open.append((bid, provider_id, at))   # it stays open: the next run reads it
                raise
            except CallError as e:
                f.batch.errors.append(f"read {provider_id}: {e}")
                if e.status == 404 and self._gone_for_good(at):
                    changed += self._give_up(db, bid, "batch not found")
                else:                           # perhaps passing (or the key's workspace changed): never resend yet
                    f.batch.open.append((bid, provider_id, at))
                continue
            if info.archived_at is not None:    # its results can't be downloaded any more
                f.batch.errors.append(f"read {provider_id}: archived")
                changed += self._give_up(db, bid, "batch archived")
                continue
            if not info.ended:
                f.batch.open.append((bid, provider_id, at))
                continue
            c = info.counts
            db.execute("""update ops.ai_batch set status = 'ended', succeeded = %s, errored = %s, expired = %s
                           where id = %s and status = 'submitted'""",
                       (c["succeeded"], c["errored"] + c["canceled"], c["expired"], bid))
            n, finished = self._collect_one(ctx, f, prompts, claude, bid, at, info, index)
            changed += n
            if not finished:                                    # the read broke off: read it again after the wait
                f.batch.open.append((bid, provider_id, at))
        return changed

    @staticmethod
    def _gone_for_good(submitted_at: datetime) -> bool:
        """A 404 counts as the batch being gone only once the API would have dropped its results anyway (29 days):
        sooner, its replies may have been billed, so its requests aren't sent again (the `batches` check fails,
        and an open batch past 26 hours is reported stuck: Anand's call)."""
        return datetime.now(UTC) - submitted_at > BATCH_RESULTS_KEPT

    @staticmethod
    def _give_up(db, bid: int, why: str) -> int:
        """Mark a batch failed and its unanswered requests errored, so they may be sent again."""
        with db.transaction():
            db.execute("update ops.ai_batch set status = 'failed' where id = %s", (bid,))
            return db.execute("""update ops.ai_request set status = 'errored', reject_reason = %s
                                  where ai_batch_id = %s and status = 'pending'""", (why, bid)).rowcount

    def _collect_one(self, ctx, f: RunState, prompts, claude: Client, bid: int, at: datetime,
                     info: BatchInfo, index: dict) -> tuple[int, bool]:
        """Store an ended batch's results that aren't stored yet. (Requests that changed state, whether every result
        was read: a read that breaks off, or ends before the batch's count, leaves the batch 'ended' for the next
        read, and its errored or expired requests wait for that before they may be sent again.)"""
        db = ctx.fetcher.db
        reqs = {batch_custom_id(cid): (cid, model, doc_id, status) for cid, model, doc_id, status in db.execute(
            "select custom_id, model, document_id, status from ops.ai_request where ai_batch_id = %s",
            (bid,)).fetchall()}
        changed = seen = 0
        try:
            for res in claude.batch_results(info, model_of=lambda c: (reqs.get(c) or (None, None))[1]):
                seen += 1
                if res.custom_id not in reqs:
                    f.batch.results["unknown"] += 1                  # not a request we sent in this batch
                    continue
                cid, _, doc_id, status = reqs[res.custom_id]
                if status != "pending":
                    continue                                          # stored by an earlier read
                reqs[res.custom_id] = (cid, None, doc_id, "read")
                f.batch.results[res.kind] += 1
                if res.kind == "succeeded":
                    self._store_result(ctx, f, prompts, cid, doc_id, res.reply, index)
                else:
                    status = "expired" if res.kind == "expired" else "errored"
                    db.execute("""update ops.ai_request set status = %s, reject_reason = %s
                                   where custom_id = %s and status = 'pending'""",
                               (status, f"batch {res.error or res.kind}", cid))
                    if (e := self._entry(db, cid, doc_id, prompts, index)) is not None:
                        f.outcome(e[0], status, f"batch {res.error or res.kind}")
                changed += 1
        except AIUnavailable:
            raise
        except CallError as e:
            if e.status == 404 and self._gone_for_good(at):
                f.batch.errors.append(f"results {info.id}: {e}")
                return changed + self._give_up(db, bid, "batch results not found"), True
            f.batch.read_errors[info.id] = f"results {info.id}: {e}"
            return changed, False                           # the batch stays 'ended': the next read finishes it
        if seen < (total := sum(info.counts.values())):     # ended cleanly but short: read it again
            f.batch.read_errors[info.id] = f"results {info.id}: the read ended after {seen} of {total} results"
            return changed, False
        f.batch.read_errors.pop(info.id, None)              # an earlier broken read, finished now
        with db.transaction():
            changed += db.execute("""update ops.ai_request set status = 'errored', reject_reason = 'batch: no result'
                                      where ai_batch_id = %s and status = 'pending'""", (bid,)).rowcount
            db.execute("""update ops.ai_batch set status = 'collected', collected_at = now(),
                                 cost_usd = (select coalesce(sum(o.cost_usd), 0) from ops.ai_request r
                                               join core.ai_output o on o.id = r.ai_output_id
                                              where r.ai_batch_id = %s)
                           where id = %s""", (bid, bid))
        turnaround = (info.ended_at - info.created_at).total_seconds() if info.ended_at else None
        f.batch.collected.append({"batch": info.id, "requests": sum(info.counts.values()),
                                  "turnaround_s": round(turnaround) if turnaround is not None else None})
        return changed, True

    def _store_result(self, ctx, f: RunState, prompts, cid: str, doc_id: int | None, reply: Reply,
                      index: dict) -> None:
        """One succeeded batch result: checked and stored like a real-time reply, with the run's spend, in one
        transaction that first locks the request's row and stores nothing unless it's still pending (another run may
        have stored it). If its target can't be built from the request any more, it's stored rejected as stale. If the
        database refuses the reply itself (a character it can't hold, say), it's stored bare, with its cost, as a
        failed attempt: retried once like any other, so one bad reply never stalls the rest of the batch."""
        db = ctx.fetcher.db
        entry = self._entry(db, cid, doc_id, prompts, index)
        spend = f.spend + reply.cost_usd
        if entry is None:
            if self._store_bare(db, cid, reply, ctx.task_run_id, spend,
                                "stale: the target's input changed before its batch was collected", {"stale": True}):
                f.batch.results["stale"] += 1
                f.spend = spend
            return
        w, req, attempt = entry
        call = Call(cid, req, attempt, reply=reply)
        if reply.stop_reason != "end_turn":
            call.failed = str(reply.stop_reason)
        else:
            call.checked = w.check(req, reply)      # an extraction reads B6's parse here (a storage error fails the
                                                    # run: the batch stays 'ended' and the next run reads it again)
        retry_note = None if call.ok or call.held or attempt == 2 else call.note()
        try:
            with db.transaction():
                if not self._pending(db, cid):
                    raise Skip
                self._store(db, w, call, ctx.task_run_id, retry_note)
                db.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                           (spend.quantize(Decimal("0.0001")), ctx.task_run_id))
        except Skip:
            return
        except psycopg.Error as e:
            if db.closed or db.broken:
                raise
            why = f"the reply couldn't be stored ({type(e).__name__})"
            note = None if attempt == 2 else why + "."
            if self._store_bare(db, cid, reply, ctx.task_run_id, spend, ("retried: " if note else "rejected: ") + why,
                                {"attempt": attempt, "unstorable": type(e).__name__}, retry_note=note):
                f.batch.results["unstorable"] += 1
                f.spend = spend
                f.outcome(w, "retrying" if note else "rejected", why)
            return
        f.spend = spend
        reason = "; ".join(call.reasons())
        o = f.outcome(w, "published" if call.ok else "held" if call.held else "rejected" if attempt == 2
                      else "retrying", reason)
        o.calls.append(call)

    def _store_bare(self, db, cid: str, reply: Reply, task_run_id: int, spend: Decimal, reason: str, extra: dict,
                    *, retry_note: str | None = None) -> bool:
        """A billed reply stored without its text (stale, or refused by the database): a rejected output with its
        tokens and cost, the request rejected with `reason`, and the run's spend. False if the request wasn't pending
        any more."""
        try:
            with db.transaction():
                if not self._pending(db, cid):
                    raise Skip
                self._insert_bare(db, cid, reply, task_run_id, spend, reason, extra, retry_note)
        except Skip:
            return False
        return True

