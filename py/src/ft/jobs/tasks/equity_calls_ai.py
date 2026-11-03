"""equity.calls_ai: the cited summaries of the stored earnings calls, precomputed through the Batch API (Spec 02B §5.5-§5.6,
§13.3, §13.6; job 2B-11, #174). The one task of module (c) that spends (`CLAUDE.md` §6's AI-run rules apply to its
`refresh` and `backfill`); the extracted figures (2B-13, #176) join it later, and the embeddings and the rolling window
(§5.4) are after 18 Nov.

**What it asks for.** Every published, stored transcript (a call whose event is `stored` and published, not held, with its
document published and chunked, and neither source suppressed) that lacks a published `call_summary`: the document the call
row points at, never a revised version registered later (§13.3). A backfill's `--since` and `--until` limit the run to calls
filed in that range (a weekly run has none). Each is one request (`ft/ai/call_summary.py`: the prompt
`config/prompts/call_summary/v1.md`, the checks, the role rules from the call's stored turns), sent through the shared batch
steps (`ft/ai/batch.py`: submit, poll for up to 15 minutes, collect, store each billed reply at once, retry once with a note,
then reject with an alert, never resend an unchanged input). Roles don't gate it: a role-held call's summary runs too (§5.3),
and its bullets can rest only on turns whose roles are known. One published output per transcript (target: the document;
scope `''`); a later one supersedes it.

**Limits (§5.5).** A transcript whose request is estimated above 60,000 tokens is skipped with a warning naming it
(`calls_too_long`); the wrapper itself refuses anything over 100,000.

**Degrade (S2, #197).** With no key, a refused key, no credit, or `ai=off` (the workflow leaves the key out),
`AIUnavailable` is caught before anything is sent and between requests: every target without an outcome is skipped ("skipped:
AI unavailable", the `ai_available` warning and one alert a month), nothing new is published, every stored reply and every
published output stays, and the run is green. A call without a summary then shows "Summary not generated yet" (§13.3).

Checks, B blocking, W warning:
- (B) `outputs_stored`: each output published in this run is on its transcript, has every bullet cited, and each bullet's
  citations are verified and inside the published text;
- (B) `citation_roles`: the role rules, run again on what is stored (the output's citations against `equity.call_turn`);
- (W) `citation_integrity`: no reply finally rejected in this run, and the month's rejected share under 10% (Arch 03 §1.9);
- (W) `calls_too_long`: transcripts skipped for their length, named;
- (W) `ai_spend`, `ai_available`, `batches`: as `policy.ai_batch`'s;
- (W) `call_summaries`: what the run found and did (counts; always passes).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from ft.ai import batch, summaries
from ft.ai import call_summary as cs
from ft.ai.batch import (
    BATCH_POLL_SECONDS,
    RUN_LIMIT_USD,
    BatchSteps,
    Call,
    RunState,
    batches_check,
    spend_alerts,
    spend_check,
)
from ft.ai.call_summary import CallDoc, Turn
from ft.ai.client import Reply, Request, load_prompt
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register
from ft.jobs.tasks.ai_batch import AI_REASONS, INTEGRITY_LIMIT, INTEGRITY_MIN_TARGETS

PROMPT_VERSION = {cs.TASK: "v1"}
IST = "Asia/Kolkata"
SHOWN = 12


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Work:
    """One target: the summary of one stored call."""
    task: str
    call: CallDoc
    slug: str                         # the call's event slug, for the checks' messages

    @property
    def target(self) -> str:
        return f"{self.task}:doc-{self.call.document_id}"

    @property
    def document_id(self) -> int:
        return self.call.document_id

    def request(self, prompt, note: str | None = None) -> Request:
        return cs.call_summary_request(self.call, prompt, note=note)

    def check(self, req: Request, reply: Reply) -> summaries.Checked:
        return cs.check(reply, req.sources, self.call.turns)


@dataclass
class Fetched(RunState):
    """The batch steps' run state (`ft.ai.batch.RunState`) and what is this task's own."""
    wanted: int = 0                                          # calls that lacked a published summary at the start
    too_big: list[tuple[Work, int]] = field(default_factory=list)   # skipped for their length: (work, estimated tokens)


@register
class EquityCallsAi(BatchSteps, Task):
    key = "equity.calls_ai"
    requires = "equity.earnings_call"
    # `scope` (tests: only the calls with these event slugs), `claude`, `sleep` and `monotonic` (tests inject) are
    # `BatchSteps`'.

    def __init__(self):
        self._last: Fetched | None = None

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    def _scoped_documents(self) -> tuple[str, list] | None:
        if self.scope is None:
            return None
        return ("""select ec.transcript_document_id from equity.earnings_call ec join core.event e on e.id = ec.event_id
                    where e.slug = any(%s)""", [sorted(self.scope)])

    # ------------------------------------------------------------------ reading what's there
    def _calls(self, db, *, doc_ids: list[int] | None = None, lacking: bool = True,
               since: date | None = None, until: date | None = None) -> list[Work]:
        """The stored calls as targets. `lacking`: only those without a published summary (what to ask for);
        otherwise every call that could be asked for (a late result looks its target up so)."""
        where = ["ev.status = 'stored'", "ev.is_published", "ec.status_reason is null", "d.is_published",
                 "d.chunked_at is not null", "(es.id is null or not es.is_suppressed)", "not ds.is_suppressed"]
        args: list = []
        if lacking:
            where.append("""not exists (select 1 from core.ai_output o where o.task = 'call_summary'
                                          and o.document_id = d.id and o.status = 'published')""")
        if doc_ids is not None:
            where.append("d.id = any(%s)"); args.append(doc_ids)
        elif self.scope is not None:
            where.append("ev.slug = any(%s)"); args.append(sorted(self.scope))
        if since is not None:
            where.append(f"(ec.filed_at at time zone '{IST}')::date >= %s"); args.append(since)
        if until is not None:
            where.append(f"(ec.filed_at at time zone '{IST}')::date <= %s"); args.append(until)
        rows = db.execute(
            f"""select ec.event_id, ev.slug, ec.fiscal_label, ec.call_date, (ec.filed_at at time zone '{IST}')::date,
                       d.id, d.char_count, n.name,
                       (select string_agg(c.body, '' order by c.chunk_no) from core.chunk c where c.document_id = d.id)
                  from equity.earnings_call ec
                  join core.event ev on ev.id = ec.event_id
                  join core.document d on d.id = ec.transcript_document_id
                  join core.entity n on n.id = ec.entity_id
                  left join core.source es on es.id = ev.source_id
                  join core.source ds on ds.id = d.source_id
                 where {' and '.join(where)}
                 order by ec.filed_at, ec.event_id""", args).fetchall()
        turns: dict[int, list[Turn]] = {}
        if rows:
            for did, no, a, b, speaker, role, section in db.execute(
                    """select document_id, turn_no, char_start, char_end, speaker, role, section from equity.call_turn
                        where document_id = any(%s) order by document_id, turn_no""", ([r[5] for r in rows],)).fetchall():
                turns.setdefault(did, []).append(Turn(no, a, b, speaker, role, section))
        out = []
        for _, slug, label, call_date, filed, did, chars, company, text in rows:
            if text is None or len(text) != chars:
                continue                                       # the database refuses this at commit; never send it
            title = f"{company} · {label or 'earnings call'} transcript"
            out.append(Work(cs.TASK, CallDoc(did, text, title, company, label, call_date or filed,
                                             tuple(turns.get(did, ()))), slug))
        return out

    # ------------------------------------------------------------------ fetch (network; outputs stored as they come)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        f = Fetched("batch", budget=batch.monthly_budget(db), month_spend=batch.month_spend(db))
        prompts = {t: load_prompt(t, v) for t, v in PROMPT_VERSION.items()}
        works = self._calls(db, since=ctx.since, until=ctx.until)
        f.wanted = len(works)
        for w in works:
            est = w.request(prompts[w.task]).estimated_tokens()
            if est > cs.MAX_ESTIMATED_TOKENS:
                f.too_big.append((w, est))                      # §5.5: skipped with a warning naming it
            else:
                f.targets.append(w)
        # Weekly, refresh and backfill all use the Batch API: submitted, polled for up to 15 minutes, otherwise
        # collected by the next run. The open batches of an earlier run are read first, even with nothing new to ask.
        self._batch_path(ctx, f, prompts, poll=BATCH_POLL_SECONDS)
        return f

    # ------------------------------------------------------------------ storing a billed reply (one transaction)
    def _store(self, tx, w: Work, call: Call, task_run_id: int, retry_note: str | None) -> None:
        """One billed reply: its output, and either its citations and publication (superseding the earlier published
        output: one per task and target, Architecture 01 §1.4) or its rejection; the request's final status."""
        req, reply = call.request, call.reply
        if call.checked is None:                       # the reply didn't end its turn
            content, body, cites = {"attempt": call.attempt, "stop_reason": reply.stop_reason,
                                    "problems": call.reasons(), "bullets": []}, None, []
        else:
            content, body, cites = summaries.content(call.checked, attempt=call.attempt, reply=reply)
        content["message_id"] = reply.message_id
        if retry_note:
            content["retry_note"] = retry_note
        out_id = tx.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd, task_run_id)
               values (%s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (req.task, w.document_id, json.dumps(content, ensure_ascii=False), body, reply.model, req.prompt.key,
             req.prompt.version, req.input_sha256, reply.usage.prompt_tokens, reply.usage.output, reply.cost_usd,
             task_run_id)).fetchone()[0]
        call.output_id = out_id
        if call.ok:
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id,
                                                     char_start, char_end, verified)
                       values (%s, %s, %s, 'document_span', %s, %s, %s, true)""",
                    [(out_id, i + 1, path, c.document_id, c.start, c.end) for i, (path, c) in enumerate(cites)])
            tx.execute("""update core.ai_output set status = 'superseded', status_reason = %s
                           where task = %s and document_id = %s and scope = '' and status = 'published' and id <> %s""",
                       (f"superseded by output {out_id}", req.task, w.document_id, out_id))
            tx.execute("update core.ai_output set status = 'published' where id = %s", (out_id,))
            status, reason = "succeeded", None
        else:
            reason = (("retried: " if retry_note else "rejected: ") + "; ".join(call.reasons()))[:1000]
            tx.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s", (reason, out_id))
            status = "rejected"
        tx.execute("update ops.ai_request set status = %s, ai_output_id = %s, reject_reason = %s where custom_id = %s",
                   (status, out_id, reason, call.custom_id))

    def _entry(self, db, cid: str, doc_id: int | None, prompts, index: dict):
        """(work, request, attempt) for a stored custom_id, from its call as it is now; None if the request no longer
        matches (a new prompt version, a changed transcript, a call no longer eligible: stale)."""
        if doc_id is None:
            return None
        if doc_id not in index:
            index[doc_id] = {}
            for w in self._calls(db, doc_ids=[doc_id], lacking=False):
                for c, req, attempt in self._next_step(db, w, prompts[w.task]).built:
                    index[doc_id][c] = (w, req, attempt)
        return index[doc_id].get(cid)

    @staticmethod
    def _insert_bare(db, cid: str, reply: Reply, task_run_id: int, spend: Decimal, reason: str, extra: dict,
                     retry_note: str | None) -> None:
        task, doc_id, sha, version = db.execute(
            "select ai_task, document_id, input_sha256, prompt_version from ops.ai_request where custom_id = %s",
            (cid,)).fetchone()
        content = {"bullets": [], **extra, "stop_reason": reply.stop_reason, "message_id": reply.message_id}
        if retry_note:
            content["retry_note"] = retry_note
        out_id = db.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd, task_run_id)
               values (%s, %s, %s, null, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (task, doc_id, json.dumps(content), reply.model, task, version, sha, reply.usage.prompt_tokens,
             reply.usage.output, reply.cost_usd, task_run_id)).fetchone()[0]
        db.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s", (reason, out_id))
        db.execute("""update ops.ai_request set status = 'rejected', ai_output_id = %s, reject_reason = %s
                       where custom_id = %s""", (out_id, reason, cid))
        db.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                   (spend.quantize(Decimal("0.0001")), task_run_id))

    # ------------------------------------------------------------------ load: everything was stored as it came
    def load(self, tx, f: Fetched, task_run_id) -> int:
        self._last = f
        n = sum(1 for o in f.outcomes for c in o.calls if c.output_id is not None)
        return n + f.batch.results["stale"] + f.batch.results["unstorable"]    # the last two stored bare

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out: list[Check] = []
        published = [(o.work, c.output_id) for o in f.outcomes if o.status == "published" for c in o.calls if c.ok]
        wrong, roles = [], []
        for w, out_id in published:
            # A later run may have superseded it already; what matters is that it was published on its transcript with
            # every bullet cited by verified citations inside the published text.
            row = tx.execute(
                """select o.status, o.document_id, o.content,
                          (select count(distinct c.claim_path) from core.ai_citation c
                             join core.document d on d.id = c.document_id
                            where c.ai_output_id = o.id and c.verified and d.is_published and c.char_end <= d.char_count)
                     from core.ai_output o where o.id = %s""", (out_id,)).fetchone()
            bullets = (row[2] or {}).get("bullets") if row else None
            good = (row is not None and row[0] in ("published", "superseded") and bullets and len(bullets) == row[3]
                    and row[1] == w.document_id)
            if not good:
                wrong.append(f"{w.target}: {row[:2] if row else None}")
                continue
            # The role rules again, on what is stored: each citation against the call's turns in the database.
            turns = [Turn(*r) for r in tx.execute(
                """select turn_no, char_start, char_end, speaker, role, section from equity.call_turn
                    where document_id = %s order by turn_no""", (w.document_id,)).fetchall()]
            answers = cs.answer_turns(turns)
            for path, a, b in tx.execute("""select claim_path, char_start, char_end from core.ai_citation
                                             where ai_output_id = %s order by citation_no""", (out_id,)).fetchall():
                kind = bullets[int(path.rsplit("/", 1)[1])].get("list")
                if kind not in cs.LISTS:
                    roles.append(f"{w.target}: {path} sits under no list")
                elif problem := cs.role_problem(kind, [summaries.Cited(w.document_id, a, b, "stored")], turns, answers):
                    roles.append(f"{w.target}: {path}: {problem}")
        out.append(Check("outputs_stored", not wrong, "; ".join(wrong) or f"{len(published)} published, every bullet cited"))
        out.append(Check("citation_roles", not roles, "; ".join(roles) or
                         f"{len(published)} published, every citation on a turn its list allows"))

        c = Counter(o.status for o in f.outcomes)
        mapping: Counter = Counter()
        for o in f.outcomes:
            for call in o.calls:
                if call.checked:
                    mapping.update(call.checked.mapping)
        rejected = [o for o in f.outcomes if o.status == "rejected"]
        month = tx.execute(
            """select count(*) filter (where r.status = 'rejected' and r.reject_reason like 'rejected:%%'),
                      count(*) filter (where r.status = 'succeeded' or r.reject_reason like 'rejected:%%')
                 from ops.ai_request r where r.ai_task = %s and r.created_at >= date_trunc('month', now())""",
            (cs.TASK,)).fetchone()
        rate = Decimal(month[0]) / Decimal(month[1]) if month[1] else Decimal(0)
        detail = {"run": dict(sorted(c.items())), "citations": dict(sorted(mapping.items())),
                  "month": {"rejected": month[0], "targets": month[1], "rate": f"{rate:.3f}"},
                  "rejected": [f"{o.work.target}: {o.reason}" for o in rejected]}
        out.append(Check("citation_integrity", not rejected and not (month[1] >= INTEGRITY_MIN_TARGETS
                                                                    and rate >= INTEGRITY_LIMIT),
                         json.dumps(detail), blocking=False))
        too_long = [f"{w.slug} (about {est:,} tokens, over {cs.MAX_ESTIMATED_TOKENS:,}; skipped)" for w, est in f.too_big]
        too_long += [f"{o.work.slug} ({o.reason})" for o in f.outcomes if o.status == "too_long"]
        out.append(Check("calls_too_long", not too_long, _names(too_long) or "no transcript over the length limit",
                         blocking=False))
        out.append(spend_check(f))
        reason = f.unavailable
        out.append(Check("ai_available", reason is None, json.dumps({
            "outcome": None if reason is None else f"skipped: AI unavailable ({reason})",
            "claude": f.unavailable, "skipped_targets": [o.work.target for o in f.outcomes if o.status == "skipped"],
            "budget_stopped": f.stopped == "budget"}), blocking=False))
        out.append(batches_check(f))
        out.append(Check("call_summaries", True,
                         f"{f.wanted} calls lacked a published summary at the start; this run: "
                         + (", ".join(f"{n} {s}" for s, n in sorted(c.items())) or "nothing to do"), blocking=False))
        return out

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """From the stored state, not just this run, for each target: a request finally rejected raises
        `ai_rejected:call_summary:<document>`; a published summary resolves it."""
        f = self._last
        if f is None:
            return []
        out: list[Alert] = []
        if ids := sorted({w.document_id for w in f.targets}):
            bad = dict(db.execute(
                """select r.document_id, max(r.reject_reason) from ops.ai_request r
                    where r.document_id = any(%s) and r.ai_task = %s and r.status = 'rejected'
                      and r.reject_reason like 'rejected:%%' group by r.document_id""", (ids, cs.TASK)).fetchall())
            for w in f.targets:
                published = db.execute("""select 1 from core.ai_output where task = %s and document_id = %s
                                           and status = 'published'""", (cs.TASK, w.document_id)).fetchone() is not None
                key = f"ai_rejected:{cs.TASK}:{w.document_id}"
                if published:
                    out.append(Alert(key, False))
                elif w.document_id in bad:
                    out.append(Alert(key, True, "warn", f"{w.target} ({w.call.title}): {bad[w.document_id]}"))
            out += spend_alerts(f, RUN_LIMIT_USD)
        month = datetime.now(UTC).strftime("%Y-%m")
        out.append(Alert(f"ai_unavailable:calls:{month}", f.unavailable is not None, "warn",
                         f"AI paused ({AI_REASONS.get(f.unavailable, f.unavailable)}): call summaries are skipped; "
                         "published summaries and the data are unchanged"))
        return out
