"""policy.ai_batch, part 1 (Prompt 01 B7; Architecture 03 §1.1-§1.5, §1.10; Spec 01 §3.2-§3.3): the cited summaries
of published documents (`doc_summary`, `statement_diff_summary`) and the chunk embeddings. RBI stance and projection
extraction, the Batch API path and the AI backfill are part 2.

**When it sends what:**
- watch, on a decision day (`after_decision_document`), with `ai_realtime_on_decision_days` on: the meeting's newly
  published decision documents (statement, resolution, Governor's statement) through the real-time API, so the
  summary and "what changed" are up within about half an hour; and their chunks' embeddings.
- daily, refresh and backfill: the Batch API (submitted, then polled for up to 15 minutes, otherwise collected by the
  next run). That path is part 2: here these runs count the requests waiting for it (`batch_path`, W) and send none.
  They do embed every published document's chunks that lack an embedding.

**Never twice, never lost:** each request is keyed by its input hash (ft/ai/client.py) in ops.ai_request, written
'pending' before it's sent, on the fetch's own autocommit connection. A request already `succeeded` or `rejected`
is never sent again; one that `errored` (refused, not billed) or stayed `pending` because its load rolled back may
be. The outputs, their citations and the final request status are written in the load's one transaction. The run's
spend goes on ops.task_run.ai_cost_usd as each reply arrives, so it's counted even if the load rolls back.

**Checking and publishing** (ft/ai/summaries.py): a reply is published only when every bullet has a verified
citation and only numbers from its cited passages, and the count is in range; otherwise it's retried once with a note
naming the problems, then rejected with an alert (`ai_rejected:<task>:<document>`). Every billed reply is stored as an
output (published or rejected), with its model, prompt version, tokens and cost. Publishing supersedes the target's
earlier published output in the same transaction (one published output per task and target: Architecture 01 §1.4).
A "what changed" summary runs only when both documents are published, so every citation points into published text.

**Spend:** before each request, the month's spend (ops.task_run.ai_cost_usd) plus the request's worst case must stay
under `ai_monthly_budget_usd` (alert `ai_budget:<month>`), and a run whose estimate passes $5 sends nothing
(CLAUDE.md §7; alert `ai_run_estimate`).

**Embeddings:** `voyage-4`, 512 dimensions as halfvec (model key 'voyage-4@512'), with a context line ("Fed · FOMC
statement · 28 Oct 2026 · …") added at embedding time only, so citations still point into the stored text. Missing
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
    AIError,
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

    @property
    def ok(self) -> bool:
        return self.checked is not None and self.checked.ok and self.failed is None

    def reasons(self) -> list[str]:
        if self.failed:
            return [f"the reply stopped with {self.failed}"]
        return self.checked.reasons() if self.checked else []


@dataclass
class Outcome:
    work: Work
    status: str                       # published | rejected | errored | done (sent before) | waiting | too_long | budget
    calls: list[Call] = field(default_factory=list)
    reason: str = ""


@dataclass
class Fetched:
    mode: str                         # 'realtime' | 'batch'
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
        self.result: dict = {}
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
    def _sent(db, custom_id: str) -> tuple[str, int | None] | None:
        return db.execute("select status, ai_batch_id from ops.ai_request where custom_id = %s",
                          (custom_id,)).fetchone()

    @staticmethod
    def custom_id(req: Request, doc_id: int) -> str:
        return f"{req.task}:doc-{doc_id}:{req.input_sha256.hex()[:16]}"

    def _claude(self) -> Client:
        if self.claude is None:
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise CallError("NoKey", None, "ANTHROPIC_API_KEY is not set")
            self.claude = Client(key)
        return self.claude

    # ------------------------------------------------------------------ fetch (network; no transaction)
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
        todo: list[tuple[Work, Request, str]] = []
        for w in self._work(db, docs):
            req = w.request(prompts[w.task])
            cid = self.custom_id(req, w.doc.document_id)
            sent = self._sent(db, cid)
            if sent and (sent[0] in ("succeeded", "rejected") or sent[1] is not None):
                f.outcomes.append(Outcome(w, "done", reason=f"{cid} is {sent[0]}"))
            elif mode == "batch":
                f.waiting[w.task] += 1
            else:
                todo.append((w, req, cid))

        if todo:
            self._send(ctx, f, todo, prompts)
        if mode == "realtime" and todo and not any(o.status in ("published", "rejected") for o in f.outcomes) \
                and any(o.status == "errored" for o in f.outcomes):
            # Nothing came back: fail the task, so the next watch poll tries again (the window is still open).
            raise CallError("NoReply", None, "; ".join(o.reason for o in f.outcomes if o.status == "errored"))
        self._embed(db, f, docs if meeting is not None else None)
        return f

    def _worst_case(self, req: Request) -> Decimal:
        m, _, _ = price_entry(req.model_id, self._claude().today(), path=self._claude().prices)
        p = m["standard"]
        return (Decimal(req.estimated_tokens()) * Decimal(str(p["input"]))
                + Decimal(req.spec.max_tokens) * Decimal(str(p["output"]))) / Decimal(1_000_000)

    def _record_spend(self, ctx: Context, f: Fetched, reply: Reply) -> None:
        f.spend += reply.cost_usd
        ctx.fetcher.db.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                               (f.spend.quantize(Decimal("0.0001")), ctx.task_run_id))

    def _send(self, ctx: Context, f: Fetched, todo: list[tuple[Work, Request, str]], prompts) -> None:
        db = ctx.fetcher.db
        claude = self._claude()
        f.run_estimate = sum((2 * self._worst_case(req) for _, req, _ in todo), Decimal(0))   # a retry each, at worst
        if f.run_estimate > RUN_LIMIT_USD:
            f.stopped = "run_estimate"
            f.outcomes += [Outcome(w, "budget", reason=f"run estimate ${f.run_estimate:.2f}") for w, _, _ in todo]
            return
        for i, (w, req, cid) in enumerate(todo):
            o = Outcome(w, "rejected")
            note = None
            for attempt in (1, 2):
                if attempt == 2:
                    req = w.request(prompts[w.task], note=note)
                    cid = self.custom_id(req, w.doc.document_id)
                try:
                    claude.check(req)
                except TooLong as e:
                    o.status, o.reason = "too_long", str(e)
                    o.calls.append(Call(cid, req, attempt, error=str(e)))
                    break
                if f.month_spend + f.spend + self._worst_case(req) > f.budget:
                    f.stopped = "budget"
                    if attempt == 1:
                        o.status, o.reason = "budget", f"month-to-date ${f.month_spend + f.spend:.4f} of ${f.budget}"
                    else:                                       # the first reply stands as the rejection
                        o.status, o.reason = "rejected", o.reason + " (not retried: the monthly budget is reached)"
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
                if not fresh:                                   # sent before and answered: never twice
                    o.status, o.reason = ("done" if attempt == 1 else "rejected"), f"{cid} was sent before"
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
                    o.status, o.reason = "errored", f"{w.target}: {e}"
                    break
                self._record_spend(ctx, f, call.reply)
                if call.failed is None:
                    call.checked = w.check(req, call.reply)
                if call.ok:
                    o.status = "published"
                    break
                note = "; ".join(call.reasons()) if call.failed else summaries.retry_note(call.checked)
                o.reason = "; ".join(call.reasons())
            f.outcomes.append(o)
            if o.status == "errored" or f.stopped:
                # The API refused or the money ran out: the rest wait for the next run.
                f.outcomes += [Outcome(w2, "waiting", reason=f"after {f.stopped or o.status}")
                               for w2, _, _ in todo[i + 1:]]
                return

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
        try:
            got = self.embedder.embed_documents(texts)
        except AIError as e:
            f.embed_error = str(e)
            return
        f.embeds = [(r[0], r[1], _md5(r[2]), v) for r, v in zip(rows, got.vectors, strict=True)]
        f.embed_tokens, f.embed_requests = got.tokens, got.requests
        f.embed_pending = 0

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        n = 0
        published: list[tuple[Outcome, int]] = []
        for o in f.outcomes:
            for call in o.calls:
                if call.reply is None:
                    if o.status == "too_long":
                        n += tx.execute("""insert into ops.ai_request (custom_id, ai_task, document_id, input_sha256,
                                                                       model, prompt_version, status, reject_reason)
                                           values (%s, %s, %s, %s, %s, %s, 'rejected', 'too_long')
                                           on conflict (custom_id) do nothing""",
                                        (call.custom_id, call.request.task, o.work.doc.document_id,
                                         call.request.input_sha256, call.request.model_id,
                                         call.request.prompt.version)).rowcount
                    continue
                out_id, rows = self._store(tx, o, call, task_run_id)
                n += rows
                if call.ok:
                    published.append((o, out_id))
        for did, no, md5, vec in f.embeds:
            n += tx.execute("""insert into core.chunk_embedding (document_id, chunk_no, model, embedding)
                               select c.document_id, c.chunk_no, %s, %s::extensions.halfvec(512)
                                 from core.chunk c where c.document_id = %s and c.chunk_no = %s and md5(c.body) = %s
                               on conflict (document_id, chunk_no, model) do nothing""",
                            (EMBED_MODEL_KEY, halfvec_literal(vec), did, no, md5)).rowcount
        self.result = {"published": published}
        self._last = f
        return n

    def _store(self, tx, o: Outcome, call: Call, task_run_id: int) -> tuple[int, int]:
        """An output for one billed reply: draft, its citations, then published (superseding) or rejected."""
        req, reply = call.request, call.reply
        if call.checked is not None:
            content, body, cites = summaries.content(call.checked, attempt=call.attempt, reply=reply)
        else:
            content, body, cites = {"bullets": [], "attempt": call.attempt, "stop_reason": reply.stop_reason,
                                    "problems": call.reasons()}, None, []
        content["message_id"] = reply.message_id
        out_id = tx.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd, task_run_id)
               values (%s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (req.task, o.work.doc.document_id, json.dumps(content, ensure_ascii=False), body, reply.model,
             req.prompt.key, req.prompt.version, req.input_sha256, reply.usage.prompt_tokens, reply.usage.output,
             reply.cost_usd, task_run_id)).fetchone()[0]
        n = 1
        if call.ok:
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id,
                                                     char_start, char_end, verified)
                       values (%s, %s, %s, 'document_span', %s, %s, %s, true)""",
                    [(out_id, i + 1, path, c.document_id, c.start, c.end) for i, (path, c) in enumerate(cites)])
            n += len(cites)
            n += tx.execute("""update core.ai_output set status = 'superseded', status_reason = %s
                                where task = %s and document_id = %s and scope = '' and status = 'published'""",
                            (f"superseded by output {out_id}", req.task, o.work.doc.document_id)).rowcount
            tx.execute("update core.ai_output set status = 'published' where id = %s", (out_id,))
            status, reason = "succeeded", None
        else:
            later = o.calls[o.calls.index(call) + 1:]
            final = not any(c.reply is not None for c in later)
            reason = ("rejected: " if final else "retried: ") + "; ".join(call.reasons())
            tx.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s",
                       (reason[:1000], out_id))
            status = "rejected"
        n += tx.execute("""update ops.ai_request set status = %s, ai_output_id = %s, reject_reason = %s
                            where custom_id = %s""", (status, out_id, reason and reason[:1000], call.custom_id)).rowcount
        return out_id, n

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out: list[Check] = []
        wrong = []
        for o, out_id in self.result["published"]:
            row = tx.execute(
                """select o.status, jsonb_array_length(o.content -> 'bullets'),
                          (select count(distinct c.claim_path) from core.ai_citation c
                             join core.document d on d.id = c.document_id
                            where c.ai_output_id = o.id and c.verified and d.is_published and c.char_end <= d.char_count)
                     from core.ai_output o where o.id = %s""", (out_id,)).fetchone()
            if row is None or row[0] != "published" or row[1] != row[2] or not row[1]:
                wrong.append(f"{o.work.target}: {row}")
        out.append(Check("outputs_stored", not wrong, "; ".join(wrong) or
                         f"{len(self.result['published'])} published, every bullet cited"))

        c = Counter(o.status for o in f.outcomes)
        mapping = Counter()
        for o in f.outcomes:
            for call in o.calls:
                if call.checked:
                    mapping.update(call.checked.mapping)
        rejected = [o for o in f.outcomes if o.status == "rejected"]
        month = tx.execute(
            """select count(*) filter (where r.status = 'rejected' and r.reject_reason like 'rejected:%'),
                      count(*) filter (where r.status = 'succeeded' or r.reject_reason like 'rejected:%')
                 from ops.ai_request r where r.ai_task in ('doc_summary', 'statement_diff_summary')
                  and r.created_at >= date_trunc('month', now())""").fetchone()
        rate = Decimal(month[0]) / Decimal(month[1]) if month[1] else Decimal(0)
        detail = {"run": dict(sorted(c.items())), "citations": dict(sorted(mapping.items())),
                  "month": {"rejected": month[0], "targets": month[1], "rate": f"{rate:.3f}"},
                  "rejected": [f"{o.work.target}: {o.reason}" for o in rejected]}
        out.append(Check("citation_integrity", not rejected and not (month[1] >= INTEGRITY_MIN_TARGETS
                                                                    and rate >= INTEGRITY_LIMIT),
                         json.dumps(detail), blocking=False))

        errored = [o for o in f.outcomes if o.status in ("errored", "too_long")]
        out.append(Check("ai_calls", not errored, "; ".join(f"{o.work.target}: {o.reason}" for o in errored),
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
        stored = sum(1 for _ in f.embeds)
        out.append(Check("embeddings", f.embed_pending == 0 and f.embed_error is None, json.dumps({
            "embedded": stored, "pending": f.embed_pending, "tokens": f.embed_tokens, "requests": f.embed_requests,
            "host": f.embed_host, "error": f.embed_error}), blocking=False))
        return out

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        out = []
        f: Fetched | None = self._last
        if f is None:
            return out
        for o in f.outcomes:
            key = f"ai_rejected:{o.work.task}:{o.work.doc.document_id}"
            if o.status == "rejected":
                out.append(Alert(key, True, "warn", f"{o.work.target} ({o.work.doc.title}): {o.reason}"))
            elif o.status == "published":
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

