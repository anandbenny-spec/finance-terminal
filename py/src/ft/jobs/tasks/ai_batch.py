"""policy.ai_batch, part 1 (Prompt 01 B7; Architecture 03 §1.1-§1.5, §1.10; Spec 01 §3.2-§3.3): the cited summaries
of published documents (`doc_summary`, `statement_diff_summary`) and the chunk embeddings. RBI stance and projection
extraction, the Batch API path and the AI backfill are part 2.

**When it sends what:**
- watch, on a decision day (`after_decision_document`), with `ai_realtime_on_decision_days` on: the meeting's newly
  published decision documents (statement, resolution, Governor's statement) through the real-time API, so the
  summary and "what changed" are up within about half an hour; and their chunks' embeddings. A poll that leaves a
  target unfinished (the API refused or failed) fails, so the next poll tries again while the window is open; the
  targets it finished are skipped then.
- daily, refresh and backfill: the Batch API (submitted, then polled for up to 15 minutes, otherwise collected by the
  next run). That path is part 2: here these runs count the requests waiting for it (`batch_path`, W) and send none.
  They do embed every published document's chunks that lack an embedding.

**Never twice, never lost.** Each request is keyed by its input hash (ft/ai/client.py) in ops.ai_request, written
'pending' before it's sent. Each billed reply is stored at once, in its own small transaction on the fetch's
connection: the output (with its model, prompt version, tokens and cost), its citations, publication (superseding the
target's earlier published output: one per task and target, Architecture 01 §1.4) or rejection, and the request's
final status. Like the raw files a fetch registers (Architecture 02 §1.2), a billed reply is evidence that must
survive a failed load, so it isn't held for the load's transaction; the database's own checks (a published output
needs a verified citation inside a published document's text) run at each of these commits. A request `succeeded`
or `rejected` is never sent again; one that `errored` (refused, not billed) may be. The run's spend goes on
ops.task_run.ai_cost_usd as each reply arrives. The load holds the embeddings.

**Checking** (ft/ai/summaries.py): a reply is published only when every bullet has a verified citation and only
figures from its cited passages, nothing stands outside the bullets, and the count is in range. Otherwise the first
reply is stored rejected ("retried: …", with the note for the retry) and the request is sent once more with that
note; a second failure is final ("rejected: …") and raises `ai_rejected:<task>:<document>`. A retry that couldn't be
sent (an API error, the budget) is sent by a later run from the stored note. A "what changed" summary runs only when
both documents are published, so every citation points into published text.

**Spend:** before each request, the month's spend (ops.task_run.ai_cost_usd) plus the request's worst case must stay
under `ai_monthly_budget_usd` (alert `ai_budget:<month>`), and a run whose estimate passes $5 sends nothing
(CLAUDE.md §7; alert `ai_run_estimate`).

**Embeddings:** `voyage-4`, 512 dimensions as halfvec (model key 'voyage-4@512'), with a context line ("Fed · FOMC
statement · 28 Oct 2026 · …") added at embedding time only, so citations still point into the stored text. A missing
key or a Voyage error leaves the chunks for the next run (`embeddings`, W); it never fails the summaries.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from ft.ai import summaries
from ft.ai.client import (
    CallError,
    Client,
    Embedder,
    Failed,
    Reply,
    Request,
    TooLong,
    halfvec_literal,
    load_prompt,
    price_entry,
)
from ft.ai.summaries import Checked, Doc
from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register

PROMPT_VERSION = {"doc_summary": "v1", "statement_diff_summary": "v1"}
REALTIME_DOC_TYPES = ("fomc_statement", "mpc_resolution", "rbi_governor_statement")
EMBED_MODEL_KEY = "voyage-4@512"
RUN_LIMIT_USD = Decimal(5)            # CLAUDE.md §7: an AI run estimated above $5 is Anand's call
INTEGRITY_LIMIT = Decimal("0.10")     # citation_integrity: rejections stay under 10% (Architecture 03 §1.9)
INTEGRITY_MIN_TARGETS = 10            # the month's rate is judged once it covers this many targets
BANK_TZ = {"fed": ZoneInfo("America/New_York"), "rbi": ZoneInfo("Asia/Kolkata")}
SUMMARY_TASKS = ("doc_summary", "statement_diff_summary")


@dataclass
class Work:
    """One target: a task on a document (and, for the diff summary, its base and the redline)."""
    task: str
    doc: Doc
    base: Doc | None = None
    segments: dict | None = None

    @property
    def target(self) -> str:
        return f"{self.task}:doc-{self.doc.document_id}"

    def request(self, prompt, note: str | None = None) -> Request:
        if self.task == "doc_summary":
            return summaries.doc_summary_request(self.doc, prompt, note=note)
        return summaries.diff_summary_request(self.doc, self.base, self.segments, prompt, note=note)

    def check(self, req: Request, reply: Reply) -> Checked:
        changed = None
        if self.task == "statement_diff_summary":
            old, new = summaries.changed_spans(self.segments)
            changed = {self.base.document_id: old, self.doc.document_id: new}
        return summaries.check(self.task, reply, req.sources, changed=changed)


@dataclass(frozen=True)
class Step:
    """What a target needs next: attempt 1, or attempt 2 with the note the stored first reply left."""
    attempt: int
    note: str | None = None


@dataclass
class Call:
    """One request sent (or refused before sending)."""
    custom_id: str
    request: Request
    attempt: int
    reply: Reply | None = None
    checked: Checked | None = None
    failed: str | None = None         # the stop reason of a reply that didn't end its turn
    error: str | None = None          # CallError: not billed, no output
    output_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.checked is not None and self.checked.ok and self.failed is None

    def reasons(self) -> list[str]:
        if self.failed:
            return [f"the reply stopped with {self.failed}"]
        return self.checked.reasons() if self.checked else []

    def note(self) -> str:
        """The retry's note about this reply."""
        return "; ".join(self.reasons()) + "." if self.failed else summaries.retry_note(self.checked)


@dataclass
class Outcome:
    work: Work
    status: str                       # published | rejected | errored | done | waiting | too_long | budget
    calls: list[Call] = field(default_factory=list)
    reason: str = ""


@dataclass
class Fetched:
    mode: str                         # 'realtime' | 'batch'
    targets: list[Work] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    waiting: Counter = field(default_factory=Counter)     # batch mode: requests waiting for part 2, by task
    spend: Decimal = Decimal(0)
    month_spend: Decimal = Decimal(0)
    budget: Decimal = Decimal(0)
    stopped: str | None = None        # 'budget' | 'run_estimate'
    run_estimate: Decimal = Decimal(0)
    embeds: list[tuple[int, int, str, list[float]]] = field(default_factory=list)   # doc, chunk_no, body md5, vector
    embed_pending: int = 0            # chunks still without an embedding after this run's attempt
    embed_tokens: int = 0
    embed_requests: int = 0
    embed_host: str | None = None
    embed_error: str | None = None


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else ""


@register
class AiBatch(Task):
    key = "policy.ai_batch"
    scope: frozenset[str] | None = None   # tests: only documents of these meetings (event slugs)
    claude: Client | None = None          # tests inject; otherwise built from ANTHROPIC_API_KEY on first use
    embedder: Embedder | None = None      # tests inject; otherwise built from VOYAGE_API_KEY

    def __init__(self):
        self._last: Fetched | None = None

    # ------------------------------------------------------------------ reading what's there
    def _docs(self, db, *, ids: list[int] | None = None, event_id: int | None = None,
              doc_types: tuple[str, ...] | None = None) -> list[Doc]:
        where, args = ["d.is_published", "d.chunked_at is not null",
                       "not exists (select 1 from core.document v where v.supersedes_id = d.id and v.is_published)"], []
        if ids is not None:
            where.append("d.id = any(%s)"); args.append(ids)
        elif self.scope is not None:
            where.append("e.slug = any(%s)"); args.append(sorted(self.scope))
        if event_id is not None:
            where.append("d.event_id = %s"); args.append(event_id)
        if doc_types is not None:
            where.append("d.doc_type = any(%s)"); args.append(list(doc_types))
        rows = db.execute(
            f"""select d.id, d.doc_type, dt.label, d.title, n.short_name, n.slug, d.published_at,
                       coalesce(e.ends_on, e.starts_on), d.char_count,
                       (select string_agg(c.body, '' order by c.chunk_no) from core.chunk c where c.document_id = d.id)
                  from core.document d
                  join core.doc_type dt on dt.key = d.doc_type
                  join core.entity n on n.id = d.entity_id
                  left join core.event e on e.id = d.event_id
                 where {' and '.join(where)}
                 order by d.published_at nulls last, d.id""", args).fetchall()
        out = []
        for did, dtype, label, title, bank, bank_slug, published_at, event_day, chars, text in rows:
            if text is None or len(text) != chars:
                continue                                       # the database refuses this at commit; never send it
            tz = BANK_TZ.get(bank_slug)
            day = published_at.astimezone(tz).date() if published_at and tz else event_day
            out.append(Doc(did, dtype, label, title, bank or bank_slug, day, text))
        return out

    def _work(self, db, docs: list[Doc]) -> list[Work]:
        work = [Work("doc_summary", d) for d in docs]
        diffable = {r[0] for r in db.execute("select key from core.doc_type where diffable").fetchall()}
        ids = [d.document_id for d in docs if d.doc_type in diffable]
        if not ids:
            return work
        rows = db.execute("""select x.document_id, x.base_document_id, x.segments, x.diff_version
                               from core.document_diff x where x.document_id = any(%s)""", (ids,)).fetchall()
        diffs = {r[0]: r[1:] for r in rows if r[3] == "2"}
        bases = {d.document_id: d for d in self._docs(db, ids=[b for b, _, _ in diffs.values()])}  # published only
        by_id = {d.document_id: d for d in docs}
        for did, (base_id, segments, _) in diffs.items():
            if base_id not in bases:
                continue                                       # the base isn't published: its citations couldn't show
            if not any(p.get("kind") in ("changed", "added", "removed") for p in segments.get("pairs", [])):
                continue                                       # nothing changed: the redline says so
            work.append(Work("statement_diff_summary", by_id[did], bases[base_id], segments))
        return work

    @staticmethod
    def _settings(db) -> tuple[bool, Decimal]:
        s = dict(db.execute("""select key, value from ops.setting
                                where key in ('ai_realtime_on_decision_days', 'ai_monthly_budget_usd')""").fetchall())
        realtime = str(s.get("ai_realtime_on_decision_days", "false")).strip().lower() == "true"
        return realtime, Decimal(str(s.get("ai_monthly_budget_usd", "0")))

    @staticmethod
    def _month_spend(db) -> Decimal:
        return Decimal(db.execute("""select coalesce(sum(ai_cost_usd), 0) from ops.task_run
                                      where started_at >= date_trunc('month', now())""").fetchone()[0])

    @staticmethod
    def _request_row(db, custom_id: str):
        """(status, batch id, reject reason, the output's content) of a request sent before, or None."""
        return db.execute("""select r.status, r.ai_batch_id, coalesce(r.reject_reason, ''), o.content
                               from ops.ai_request r left join core.ai_output o on o.id = r.ai_output_id
                              where r.custom_id = %s""", (custom_id,)).fetchone()

    @staticmethod
    def custom_id(req: Request, doc_id: int) -> str:
        return f"{req.task}:doc-{doc_id}:{req.input_sha256.hex()[:16]}"

    @staticmethod
    def _sendable(row) -> bool:
        """Never sent, or sent without an answer (errored; pending outside a batch: its reply wasn't stored)."""
        return row is None or row[0] == "errored" or (row[0] == "pending" and row[1] is None)

    def _next_step(self, db, w: Work, prompt) -> tuple[Step | None, str]:
        """What the target needs next, from its requests so far: (the step, or None when it's done; why)."""
        req = w.request(prompt)
        row = self._request_row(db, self.custom_id(req, w.doc.document_id))
        if self._sendable(row):
            return Step(1), "not sent yet" if row is None else f"attempt 1 {row[0]}"
        status, batch, reason, content = row
        if batch is not None and status == "pending":
            return None, "in a batch"
        if status == "rejected" and reason.startswith("retried:") and (content or {}).get("retry_note"):
            note = content["retry_note"]
            row2 = self._request_row(db, self.custom_id(w.request(prompt, note=note), w.doc.document_id))
            if self._sendable(row2):
                return Step(2, note), "the retry wasn't answered yet"
            return None, f"attempt 2 {row2[0]}"
        return None, f"attempt 1 {status}"

    def _claude(self) -> Client:
        if self.claude is None:
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise CallError("NoKey", None, "ANTHROPIC_API_KEY is not set")
            self.claude = Client(key)
        return self.claude

    # ------------------------------------------------------------------ fetch (network; outputs stored as they come)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        realtime_on, budget = self._settings(db)
        meeting = ctx.meeting if ctx.workflow == "watch" else None
        mode = "realtime" if meeting is not None and realtime_on else "batch"
        f = Fetched(mode, budget=budget, month_spend=self._month_spend(db))
        prompts = {t: load_prompt(t, v) for t, v in PROMPT_VERSION.items()}

        if meeting is not None:
            docs = self._docs(db, event_id=meeting.event_id, doc_types=REALTIME_DOC_TYPES)
        else:
            docs = self._docs(db)
        f.targets = self._work(db, docs)
        todo: list[tuple[Work, Step]] = []
        for w in f.targets:
            step, why = self._next_step(db, w, prompts[w.task])
            if step is None:
                f.outcomes.append(Outcome(w, "done", reason=why))
            elif mode == "batch":
                f.waiting[w.task] += 1
            else:
                todo.append((w, step))

        if todo:
            self._send(ctx, f, todo, prompts)
            unfinished = [o for o in f.outcomes if o.status in ("errored", "waiting")]
            if unfinished:
                # Fail the poll, so the next one tries again while the window is open. What finished is stored.
                raise CallError("Unfinished", None, "; ".join(f"{o.work.target}: {o.reason}" for o in unfinished))
        self._embed(db, f, docs if meeting is not None else None)
        return f

    def _worst_case(self, req: Request) -> Decimal:
        """An upper bound on the request's cost: every input token at the dearest input rate (a cache write), and
        max_tokens of output."""
        claude = self._claude()
        m, _, _ = price_entry(req.model_id, claude.today(), path=claude.prices)
        p = m["standard"]
        rate_in = max(Decimal(str(p[k])) for k in ("input", "cache_write_5m", "cache_write_1h"))
        return (Decimal(req.estimated_tokens()) * rate_in
                + Decimal(req.spec.max_tokens) * Decimal(str(p["output"]))) / Decimal(1_000_000)

    def _send(self, ctx: Context, f: Fetched, todo: list[tuple[Work, Step]], prompts) -> None:
        db = ctx.fetcher.db
        claude = self._claude()
        f.run_estimate = sum((Decimal(3 - s.attempt) * self._worst_case(w.request(prompts[w.task], note=s.note))
                              for w, s in todo), Decimal(0))     # a retry each, at worst
        if f.run_estimate > RUN_LIMIT_USD:
            f.stopped = "run_estimate"
            f.outcomes += [Outcome(w, "budget", reason=f"run estimate ${f.run_estimate:.2f}") for w, _ in todo]
            return
        for i, (w, step) in enumerate(todo):
            o = Outcome(w, "rejected")
            attempt, note = step.attempt, step.note
            while True:
                req = w.request(prompts[w.task], note=note)
                cid = self.custom_id(req, w.doc.document_id)
                try:
                    claude.check(req)
                except TooLong as e:
                    db.execute("""insert into ops.ai_request (custom_id, ai_task, document_id, input_sha256, model,
                                                              prompt_version, status, reject_reason)
                                  values (%s, %s, %s, %s, %s, %s, 'rejected', 'rejected: too_long')
                                  on conflict (custom_id) do nothing""",
                               (cid, req.task, w.doc.document_id, req.input_sha256, req.model_id, req.prompt.version))
                    o.status, o.reason = "too_long", str(e)
                    break
                if f.month_spend + f.spend + self._worst_case(req) > f.budget:
                    f.stopped = "budget"
                    o.status, o.reason = "budget", f"month-to-date ${f.month_spend + f.spend:.4f} of ${f.budget}"
                    break
                fresh = db.execute("""insert into ops.ai_request (custom_id, ai_task, document_id, input_sha256,
                                                                  model, prompt_version, status)
                                      values (%s, %s, %s, %s, %s, %s, 'pending')
                                      on conflict (custom_id) do update set status = 'pending', reject_reason = null,
                                        created_at = now()
                                      where ops.ai_request.status in ('pending', 'errored')
                                        and ops.ai_request.ai_batch_id is null""",
                                   (cid, req.task, w.doc.document_id, req.input_sha256, req.model_id,
                                    req.prompt.version)).rowcount
                if not fresh:                                   # answered meanwhile: never twice
                    o.status, o.reason = "done", f"{cid} was answered before"
                    break
                call = Call(cid, req, attempt)
                o.calls.append(call)
                try:
                    call.reply = claude.send(req)
                except Failed as e:
                    call.reply, call.failed = e.reply, e.reason
                except CallError as e:
                    call.error = str(e)
                    db.execute("update ops.ai_request set status = 'errored', reject_reason = %s where custom_id = %s",
                               (f"{e.kind}" + (f" {e.status}" if e.status else ""), cid))
                    o.status, o.reason = "errored", str(e)
                    break
                f.spend += call.reply.cost_usd
                db.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                           (f.spend.quantize(Decimal("0.0001")), ctx.task_run_id))
                if call.failed is None:
                    call.checked = w.check(req, call.reply)
                retry_note = None if call.ok or attempt == 2 else call.note()
                with db.transaction():                          # the reply is stored before anything else happens
                    self._store(db, w, call, ctx.task_run_id, retry_note)
                if call.ok:
                    o.status = "published"
                    break
                o.reason = "; ".join(call.reasons())
                if attempt == 2:
                    o.status = "rejected"
                    break
                attempt, note = 2, retry_note
            f.outcomes.append(o)
            if o.status == "errored" or f.stopped:
                # The API refused or the money ran out: the rest wait for the next run.
                f.outcomes += [Outcome(w2, "waiting" if o.status == "errored" else "budget",
                                       reason=f"after {f.stopped or o.status}") for w2, _ in todo[i + 1:]]
                return

    def _store(self, tx, w: Work, call: Call, task_run_id: int, retry_note: str | None) -> None:
        """One billed reply: its output, and either its citations and publication (superseding the earlier published
        output) or its rejection; the request's final status. One transaction."""
        req, reply = call.request, call.reply
        if call.checked is not None:
            content, body, cites = summaries.content(call.checked, attempt=call.attempt, reply=reply)
        else:
            content, body, cites = {"bullets": [], "attempt": call.attempt, "stop_reason": reply.stop_reason,
                                    "problems": call.reasons()}, None, []
        content["message_id"] = reply.message_id
        if retry_note:
            content["retry_note"] = retry_note
        out_id = tx.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd, task_run_id)
               values (%s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (req.task, w.doc.document_id, json.dumps(content, ensure_ascii=False), body, reply.model,
             req.prompt.key, req.prompt.version, req.input_sha256, reply.usage.prompt_tokens, reply.usage.output,
             reply.cost_usd, task_run_id)).fetchone()[0]
        call.output_id = out_id
        if call.ok:
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id,
                                                     char_start, char_end, verified)
                       values (%s, %s, %s, 'document_span', %s, %s, %s, true)""",
                    [(out_id, i + 1, path, c.document_id, c.start, c.end) for i, (path, c) in enumerate(cites)])
            tx.execute("""update core.ai_output set status = 'superseded', status_reason = %s
                           where task = %s and document_id = %s and scope = '' and status = 'published'""",
                       (f"superseded by output {out_id}", req.task, w.doc.document_id))
            tx.execute("update core.ai_output set status = 'published' where id = %s", (out_id,))
            status, reason = "succeeded", None
        else:
            reason = (("retried: " if retry_note else "rejected: ") + "; ".join(call.reasons()))[:1000]
            tx.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s",
                       (reason, out_id))
            status = "rejected"
        tx.execute("update ops.ai_request set status = %s, ai_output_id = %s, reject_reason = %s where custom_id = %s",
                   (status, out_id, reason, call.custom_id))

    def _embed(self, db, f: Fetched, docs: list[Doc] | None) -> None:
        """Embed published chunks that lack an embedding (watch: only the meeting's documents)."""
        where, args = ["d.is_published", "e.document_id is null"], [EMBED_MODEL_KEY]
        if docs is not None:
            where.append("d.id = any(%s)"); args.append([d.document_id for d in docs])
        elif self.scope is not None:
            where.append("ev.slug = any(%s)"); args.append(sorted(self.scope))
        rows = db.execute(
            f"""select c.document_id, c.chunk_no, c.body, c.section, c.speaker, n.short_name, n.slug, dt.label,
                       d.published_at, coalesce(ev.ends_on, ev.starts_on)
                  from core.chunk c
                  join core.document d on d.id = c.document_id
                  join core.doc_type dt on dt.key = d.doc_type
                  join core.entity n on n.id = d.entity_id
                  left join core.event ev on ev.id = d.event_id
                  left join core.chunk_embedding e on e.document_id = c.document_id and e.chunk_no = c.chunk_no
                                                  and e.model = %s
                 where {' and '.join(where)}
                 order by c.document_id, c.chunk_no""", args).fetchall()
        f.embed_pending = len(rows)
        if not rows:
            return
        try:
            if self.embedder is None:
                key = os.environ.get("VOYAGE_API_KEY", "").strip()
                if not key:
                    f.embed_error = "VOYAGE_API_KEY is not set"
                    return
                self.embedder = Embedder(key)
            f.embed_host = self.embedder.host
            texts = []
            for did, no, body, section, speaker, bank, bank_slug, label, published_at, event_day in rows:
                tz = BANK_TZ.get(bank_slug)
                day = published_at.astimezone(tz).date() if published_at and tz else event_day
                texts.append(context_line(bank or bank_slug, label, day, section, speaker) + "\n\n" + body)
            got = self.embedder.embed_documents(texts)
        except Exception as e:  # noqa: BLE001 - embeddings never fail the summaries; the next run tries again
            f.embed_error = f"{type(e).__name__}: {e}"
            return
        f.embeds = [(r[0], r[1], _md5(r[2]), v) for r, v in zip(rows, got.vectors, strict=True)]
        f.embed_tokens, f.embed_requests = got.tokens, got.requests
        f.embed_pending = 0

    # ------------------------------------------------------------------ load (one transaction): the embeddings
    def load(self, tx, f: Fetched, task_run_id) -> int:
        self._last = f
        n = sum(1 for o in f.outcomes for c in o.calls if c.output_id is not None)   # stored as they came
        for did, no, md5, vec in f.embeds:
            n += tx.execute("""insert into core.chunk_embedding (document_id, chunk_no, model, embedding)
                               select c.document_id, c.chunk_no, %s, %s::extensions.halfvec(512)
                                 from core.chunk c where c.document_id = %s and c.chunk_no = %s and md5(c.body) = %s
                               on conflict (document_id, chunk_no, model) do nothing""",
                            (EMBED_MODEL_KEY, halfvec_literal(vec), did, no, md5)).rowcount
        return n

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out: list[Check] = []
        wrong = []
        published = [(o, c.output_id) for o in f.outcomes if o.status == "published" for c in o.calls if c.ok]
        for o, out_id in published:
            row = tx.execute(
                """select o.status, jsonb_array_length(o.content -> 'bullets'),
                          (select count(distinct c.claim_path) from core.ai_citation c
                             join core.document d on d.id = c.document_id
                            where c.ai_output_id = o.id and c.verified and d.is_published and c.char_end <= d.char_count)
                     from core.ai_output o where o.id = %s""", (out_id,)).fetchone()
            # A later run may have superseded it already; what matters is that it was published with every bullet cited.
            if row is None or row[0] not in ("published", "superseded") or row[1] != row[2] or not row[1]:
                wrong.append(f"{o.work.target}: {row}")
        out.append(Check("outputs_stored", not wrong, "; ".join(wrong) or f"{len(published)} published, every bullet "
                         "cited"))

        c = Counter(o.status for o in f.outcomes)
        mapping = Counter()
        for o in f.outcomes:
            for call in o.calls:
                if call.checked:
                    mapping.update(call.checked.mapping)
        rejected = [o for o in f.outcomes if o.status == "rejected"]
        month = tx.execute(
            """select count(*) filter (where r.status = 'rejected' and r.reject_reason like 'rejected:%%'),
                      count(*) filter (where r.status = 'succeeded' or r.reject_reason like 'rejected:%%')
                 from ops.ai_request r where r.ai_task = any(%s) and r.created_at >= date_trunc('month', now())""",
            (list(SUMMARY_TASKS),)).fetchone()
        rate = Decimal(month[0]) / Decimal(month[1]) if month[1] else Decimal(0)
        detail = {"run": dict(sorted(c.items())), "citations": dict(sorted(mapping.items())),
                  "month": {"rejected": month[0], "targets": month[1], "rate": f"{rate:.3f}"},
                  "rejected": [f"{o.work.target}: {o.reason}" for o in rejected]}
        out.append(Check("citation_integrity", not rejected and not (month[1] >= INTEGRITY_MIN_TARGETS
                                                                    and rate >= INTEGRITY_LIMIT),
                         json.dumps(detail), blocking=False))

        too_long = [o for o in f.outcomes if o.status == "too_long"]
        out.append(Check("ai_calls", not too_long, "; ".join(f"{o.work.target}: {o.reason}" for o in too_long),
                         blocking=False))
        out.append(Check("ai_spend", f.stopped is None, json.dumps({
            "run_usd": str(f.spend), "month_usd": str(f.month_spend + f.spend), "budget_usd": str(f.budget),
            "stopped": f.stopped, "run_estimate_usd": str(f.run_estimate.quantize(Decimal("0.0001")))}),
            blocking=False))
        if f.mode == "batch":
            out.append(Check("batch_path", not f.waiting, (
                f"{sum(f.waiting.values())} requests wait for the Batch API path (B7 part 2): "
                + ", ".join(f"{k} {v}" for k, v in sorted(f.waiting.items()))) if f.waiting else "nothing waiting",
                blocking=False))
        out.append(Check("embeddings", f.embed_pending == 0 and f.embed_error is None, json.dumps({
            "embedded": len(f.embeds), "pending": f.embed_pending, "tokens": f.embed_tokens,
            "requests": f.embed_requests, "host": f.embed_host, "error": f.embed_error}), blocking=False))
        return out

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """From the stored state, not just this run: a target whose request was finally rejected (citations, or too
        long) and that has no published output raises `ai_rejected:<task>:<document>`; publishing resolves it."""
        f = self._last
        if f is None:
            return []
        out = []
        ids = sorted({w.doc.document_id for w in f.targets})
        if ids:
            rows = db.execute(
                """select r.ai_task, r.document_id, max(r.reject_reason),
                          exists (select 1 from core.ai_output o where o.task = r.ai_task
                                    and o.document_id = r.document_id and o.status = 'published')
                     from ops.ai_request r
                    where r.document_id = any(%s) and r.ai_task = any(%s)
                      and r.status = 'rejected' and r.reject_reason like 'rejected:%%'
                    group by r.ai_task, r.document_id""", (ids, list(SUMMARY_TASKS))).fetchall()
            bad = {(t, d): reason for t, d, reason, has in rows if not has}
            titles = {w.doc.document_id: w.doc.title for w in f.targets}
            for w in f.targets:
                key = f"ai_rejected:{w.task}:{w.doc.document_id}"
                if (w.task, w.doc.document_id) in bad:
                    out.append(Alert(key, True, "warn", f"{w.target} ({titles[w.doc.document_id]}): "
                                     f"{bad[(w.task, w.doc.document_id)]}"))
                else:
                    published = db.execute("""select 1 from core.ai_output where task = %s and document_id = %s
                                                and status = 'published'""", (w.task, w.doc.document_id)).fetchone()
                    if published:
                        out.append(Alert(key, False))
        month = datetime.now(UTC).strftime("%Y-%m")
        if f.stopped == "budget":
            out.append(Alert(f"ai_budget:{month}", True, "error",
                             f"AI spend reached the ${f.budget} monthly budget; requests stopped"))
        elif f.spend > 0:                                  # a run spent within the budget: it's been raised
            out.append(Alert(f"ai_budget:{month}", False))
        out.append(Alert("ai_run_estimate", f.stopped == "run_estimate", "error",
                         f"an AI run estimated at ${f.run_estimate:.2f} (over ${RUN_LIMIT_USD}) sent nothing"))
        return out


def context_line(bank: str, label: str, day: date | None, section: str | None, speaker: str | None) -> str:
    """The line added before a chunk at embedding time only (Architecture 03 §1.5): 'RBI · MPC minutes · 1 Oct 2026 ·
    Statement by …'."""
    parts = [bank, label]
    if day:
        parts.append(_when(day))
    if section:
        parts.append(section)
    if speaker and (not section or speaker not in section):
        parts.append(speaker)
    return " · ".join(parts)
