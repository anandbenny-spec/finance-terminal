"""equity.calls_ai: the cited summaries and the extracted figures of the stored earnings calls, precomputed through the Batch API
(Spec 02B §5.5-§5.6, §5.9, §13.3, §13.5-§13.6; jobs 2B-11, #174, and 2B-13, #176). The one task of module (c) that spends
(`CLAUDE.md` §6's AI-run rules apply to its `refresh` and `backfill`); the embeddings and the rolling window (§5.4) are after
18 Nov.

**What it asks for.** Two requests for every published, stored transcript (a call whose event is `stored` and published, not held,
with its document published and chunked, and neither source suppressed): the document the call row points at, never a revised
version registered later (§13.3).
- `call_summary`: for a call that lacks a published summary from the current prompt version. A new prompt version therefore asks
  again for the calls summarised under the old one, and a published summary of the current version is never asked for twice.
- `guidance_extraction` (`ft/ai/guidance_extraction.py`, the prompt `config/prompts/guidance_extraction/v1.md`): for a call that
  has neither a published output of the current prompt version nor a draft one marked `no_targets` (a call on which nothing was
  found is final for that version). Its call needs the company's fiscal-year end month (`equity.company`), a call date and its
  stored speaker turns, which every call from 2B-09 has; one without any is named in the `call_guidance` line and not asked for.

A backfill's `--since` and `--until` limit the run to calls filed in that range (a weekly run has none). Each request is sent
through the shared batch steps (`ft/ai/batch.py`: submit, poll for up to 15 minutes, collect, store each billed reply at once,
retry once with a note, then reject with an alert, never resend an unchanged input). Roles don't gate either task (Spec 02B.1
C1, C10): every stored call is summarised and extracted; a role-held call (`unknown_role_share` above `turns.ROLE_HOLD_SHARE`)
may leave a summary list out, and what participants whose role couldn't be read said goes in a third list (the summary) or in the
separate guidance list (`equity.guidance.speaker_role = 'unknown'`), never attributed to management and never scored. One
published output per task and transcript (target: the document; scope `''`); a later one supersedes it.

**Writing the extraction.** A reply that passes the checks and holds at least one item with a verified citation is published
with its citations, and its rows are written in the same transaction: an `equity.guidance` row per target (every row states its
`speaker_role`, set from the turn its quote lies in), an `equity.stated_figure` row per stated figure, and
`equity.earnings_call.guidance_targets` (the management targets shown, duplicates out). A reply with no item in either list (or
every item dropped) stays a draft with `status_reason = 'no_targets'` (the database refuses to publish an output without a
verified citation), sets `guidance_targets = 0` unless the call already has a published output, and shows "No measurable targets
found on this call". Items that fail a check are dropped and counted by reason, never retried; only a reply that isn't the
schema's JSON is retried. No `core.forecast` row is written (that is #177, after 18 Nov). **The rows of a superseded output stay**
(store once, never lose): a reader of `equity.guidance` or `equity.stated_figure` joins `core.ai_output` and takes the published
one, as the `api` views do.

**The summary's route (2B-11c, #301; Anand, 6 Nov 2026).** Prompts v1 to v5 ran on the citations route; **v6 to v9 are the
structured route**: the model returns each bullet as its own sentence plus the passages that state its point, as the schema's JSON
(`config/prompts/call_summary/v6.schema.json`, which v7 to v9 share), and `ft/ai/call_summary.py`'s `check_json` finds each quote in
our copy of the transcript. The stored output, its citations and its checks are the same on both routes. v7 (#178, 8 Nov 2026)
is v6 plus a rule that a guidance verb ("reiterated", "raised", "cut" and the like) is in a bullet only if its quote says so; v8 (the v8 round,
8 Nov 2026) adds four rules (a speaker's hedge stays a hedge, a recalled target is not a fresh expectation, no weight word the
speaker did not use, a cause applies only to the part it was attached to) and a check in code (`ft/ai/call_summary.py`,
`firm_verb_problem`); v9 (#178, the v9 round, 8 Nov 2026) adds three rules (a change announced for a later date
stays in the future, an exclusion stays with its figure, an extent word the speaker used stays) and a second check in code
(`extent_word_problem`); **v10, the current one on this branch (#178, the Sonnet round, 9 Nov 2026), is v9's text and checks run on
Sonnet 5.5** (`ft/ai/call_summary.py`, `MODEL_OF`) after three Haiku rounds did not pass the summary gate. A call whose summary is a
published v5, v6, v7, v8 or v9 is asked for again under v10 and keeps that summary unless the v10 reply is published.

**Copied text (2B-11c, #301).** A summary bullet may not carry the transcript's own words: `ft/ai/call_summary.py`'s rule cuts a
copied end off a new reply's bullet before anything is stored, and fails a bullet that copies anywhere else (the usual retry
path). Every run also **re-checks the published summaries of the current prompt version that were stored before the rule**
(their content has no `echo_rule`), with no AI call and no request: a bullet's copied end is cut and the output is
**superseded** by a new one (same prompt version, same citations, `cost_usd` 0, `"recheck": "echo-1"` in its content; the old row
stays as the record), or, if a bullet fails, replaced by a **rejection** with the reason (the call then shows "Summary not
generated yet"), or, if nothing changes, left alone and nothing is written, so a second re-check writes nothing. With `ai=off`
(the workflow leaves the keys out) a run does only this. It honours a backfill's `--since` and `--until`.

**One task only (2B-13b, #302; Anand, 5 Nov 2026).** A backfill can be limited to one of the two tasks: the environment
variable `CALLS_AI_TASK` (the backfill workflow's `calls_ai_task` input) is `both` (the default, and what every other run uses),
`call_summary` or `guidance_extraction`. The limit applies to what a run *asks for* (and to the summaries' re-check, which an
extraction-only run skips); the open batches of an earlier run are still read for both tasks. `--task` was taken by the runner's
task key, so the option has its own name.

**Limits (§5.5).** A transcript whose request is estimated above 60,000 tokens is skipped with a warning naming it
(`calls_too_long`); the wrapper itself refuses anything over 100,000.

**Degrade (S2, #197).** With no key, a refused key, no credit, or `ai=off` (the workflow leaves the key out),
`AIUnavailable` is caught before anything is sent and between requests: every target without an outcome is skipped ("skipped:
AI unavailable", the `ai_available` warning and one alert a month), nothing new is published, every stored reply and every
published output stays, and the run is green. A call without a summary then shows "Summary not generated yet" (§13.3).

Checks, B blocking, W warning:
- (B) `outputs_stored`: each summary published in this run is on its transcript, has every bullet cited, and each bullet's
  citations are verified and inside the published text;
- (B) `citation_roles`: the role rules, run again on what is stored (the output's citations against `equity.call_turn`);
- (B) `echo_clean`: the re-check's summaries and the summaries published in this run, read back from the database against their
  transcripts: no published bullet holds a verbatim run over `MAX_ECHO_CHARS` characters (its detail names the longest run left);
- (W) `echo_recheck`: what the re-check found and wrote (counts, bullets cut and failed by list, the calls that lost their
  summary by company and quarter); a warning when a call lost its summary or a transcript couldn't be read back whole;
- (B) `guidance_stored`: each extraction stored in this run has its citations and its rows, one each per item (or, with no item,
  is a `no_targets` draft with no row), and the call's `guidance_targets` is the count of its management targets shown;
- (B) `guidance_items`: each stored row again, against the transcript: its span is at most 400 code points and inside one turn
  whose role is the row's `speaker_role` (a stated figure's: management), a row of the separate list passes the content test,
  and its figures are in its quote;
- (W) `guidance_dropped_<reason>`: items dropped, one line per reason (the four of Spec 02B.1 C3 always; the others when
  there are any);
- (W) `citation_integrity`: no reply finally rejected in this run, and the month's rejected share under 10% (Arch 03 §1.9);
- (W) `calls_too_long`: transcripts skipped for their length, named;
- (W) `ai_spend`, `ai_available`, `batches`: as `policy.ai_batch`'s;
- (W) `call_summaries`, `call_guidance`: what the run found and did (counts; always pass).
"""
from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from ft.ai import batch, summaries
from ft.ai import call_summary as cs
from ft.ai import call_tone as ct
from ft.ai import guidance_extraction as ge
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
from ft.ai.client import Reply, Request, load_prompt, load_schema
from ft.ai.guidance_extraction import Metrics
from ft.equity.calls.turns import ROLE_HOLD_SHARE
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register
from ft.jobs.tasks.ai_batch import AI_REASONS, INTEGRITY_LIMIT, INTEGRITY_MIN_TARGETS

# v10 runs on Sonnet 5.5, 20 times Haiku's price (config/ai_prices.yaml): a run that asks for it on production would cost about 15 to 17 USD
# for the whole set, far past #178's approved 1.50 USD. So this branch (claude/2b-15-summary-v10-sonnet, #178) is run on dev only and is not
# merged to main until the summary gate has passed and Anand has approved a production run at that price; main stays on v9.
PROMPT_VERSION = {cs.TASK: "v10", ge.TASK: "v1", ct.TASK: "v1"}
IST = "Asia/Kolkata"
SHOWN = 12
ONLY_ENV = "CALLS_AI_TASK"            # both (the default) | call_summary | guidance_extraction | call_tone: limits what a run asks for (#302)


def only_task(env=None) -> str | None:
    """The one task a run is limited to, or None for both. A bad value stops the run before anything is sent."""
    v = ((os.environ if env is None else env).get(ONLY_ENV) or "both").strip()
    if v not in ("both", cs.TASK, ge.TASK, ct.TASK):
        raise ValueError(f"{ONLY_ENV} must be both, {cs.TASK} or {ge.TASK} (or {ct.TASK}), not {v!r}")
    return None if v == "both" else v


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Work:
    """One target: the summary, or the extracted figures, of one stored call."""
    task: str
    call: CallDoc
    slug: str                         # the call's event slug, for the checks' messages
    event_id: int = 0                 # the call's event and its company: the extraction's rows
    entity_id: int = 0
    schema: dict | None = None        # the JSON schema of a request on the json route: the extraction's, or the summary's (v6 on)
    metrics: Metrics | None = None    # the extraction's metric map and switch
    passages: tuple = ()              # the tone request's management passages (`ft.ai.call_tone.Passage`)

    @property
    def target(self) -> str:
        return f"{self.task}:doc-{self.call.document_id}"

    @property
    def document_id(self) -> int:
        return self.call.document_id

    def request(self, prompt, note: str | None = None) -> Request:
        if self.task == ge.TASK:
            return ge.guidance_request(self.call, prompt, self.schema, note=note)
        if self.task == ct.TASK:
            return ct.tone_request(self.call, self.passages, prompt, self.schema, note=note)
        return cs.call_summary_request(self.call, prompt, note=note, schema=self.schema)

    def check(self, req: Request, reply: Reply):
        if self.task == ge.TASK:
            return ge.check(ge.answer_of(reply), self.call, self.metrics)
        if self.task == ct.TASK:
            return ct.check(ct.answer_of(reply), self.passages)
        if req.spec.route == "json":                      # the summary's structured route (prompts from v6)
            return cs.check_json(cs.answer_of(reply), self.call)
        return cs.check(reply, req.sources, self.call.turns, role_held=self.call.role_held)


@dataclass
class Fetched(RunState):
    """The batch steps' run state (`ft.ai.batch.RunState`) and what is this task's own."""
    wanted: int = 0                                          # calls that lacked a published summary at the start
    wanted_guidance: int = 0                                 # calls that lacked their extraction at the start
    no_guidance: list[str] = field(default_factory=list)     # calls that can't be extracted (no month, date or turns): slugs
    too_big: list[tuple[Work, int]] = field(default_factory=list)   # skipped for their length: (work, estimated tokens)
    since: date | None = None                                # the run's range (a backfill), for the re-check of stored summaries
    until: date | None = None
    only: str | None = None                                  # the one task this run asks for (None: both), #302


@dataclass
class EchoRun:
    """What one run's re-check of the stored summaries found and wrote (`EquityCallsAi._echo_recheck`)."""
    looked: int = 0                                          # published summaries of the current version without the stamp
    unchanged: int = 0
    superseded: int = 0                                      # re-written with their copied ends cut
    rejected: int = 0                                        # replaced by a rejection: a bullet failed
    cut: Counter = field(default_factory=Counter)            # bullets cut, by list
    failed: Counter = field(default_factory=Counter)         # bullets that failed, by list
    unclean: int = 0                                         # cut bullets whose lead-in doesn't end at a clause end
    reasons: Counter = field(default_factory=Counter)        # failures by reason, up to its first bracket
    lost: list[str] = field(default_factory=list)            # the calls that lost their summary: 'company quarter'
    unreadable: list[str] = field(default_factory=list)      # calls whose transcript couldn't be read back whole
    docs: list[int] = field(default_factory=list)            # the transcripts looked at (`echo_clean` reads them back)

    @property
    def written(self) -> int:
        return self.superseded + self.rejected


def state(o) -> str:
    """An outcome's state for the counts: an extraction that found nothing is `no_targets`, not `published`."""
    if o.status == "published" and o.work.task == ge.TASK and all(c.checked.empty for c in o.calls if c.ok):
        return "no_targets"
    return o.status


@register
class EquityCallsAi(BatchSteps, Task):
    key = "equity.calls_ai"
    requires = "equity.earnings_call"
    # `scope` (tests: only the calls with these event slugs), `claude`, `sleep` and `monotonic` (tests inject) are
    # `BatchSteps`'.

    def __init__(self):
        self._last: Fetched | None = None
        self._echo = EchoRun()

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    def _scoped_documents(self) -> tuple[str, list] | None:
        if self.scope is None:
            return None
        return ("""select ec.transcript_document_id from equity.earnings_call ec join core.event e on e.id = ec.event_id
                    where e.slug = any(%s)""", [sorted(self.scope)])

    # ------------------------------------------------------------------ reading what's there
    metrics: Metrics | None = None        # tests inject; otherwise config/equity/guidance_metrics.yaml, read at each run

    def _calls(self, db, *, doc_ids: list[int] | None = None, lacking: bool = True,
               since: date | None = None, until: date | None = None, skipped: list[str] | None = None,
               tone: bool = True) -> list[Work]:
        """The stored calls as targets, a summary and an extraction each. `lacking`: only those without a published summary, or
        without an extraction (published, or a draft that found nothing) from the current prompt version (what to ask for);
        otherwise every call that could be asked for (a late result looks its target up so). `skipped` collects the slugs of
        the calls that can't be extracted. `tone`: also the tone targets (`call_tone`, a published output of the current
        prompt version lacking); `fetch` passes it only for a run limited to that task, so no other run reads the chunks."""
        where = ["ev.status = 'stored'", "ev.is_published", "ec.status_reason is null", "d.is_published",
                 "d.chunked_at is not null", "(es.id is null or not es.is_suppressed)", "not ds.is_suppressed"]
        guidance = ge.TASK in PROMPT_VERSION            # a task with no prompt version listed isn't asked for
        tone = tone and ct.TASK in PROMPT_VERSION
        args: list = [PROMPT_VERSION[cs.TASK], PROMPT_VERSION.get(ge.TASK, ""), PROMPT_VERSION.get(ct.TASK, "")]   # the lateral's placeholders come first
        if lacking:
            done = "f.has_summary and f.has_extraction" if guidance else "f.has_summary"
            where.append(f"not ({done} and f.has_tone)" if tone else f"not ({done})")
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
                       d.id, d.char_count, n.name, ec.unknown_role_share,
                       (select string_agg(c.body, '' order by c.chunk_no) from core.chunk c where c.document_id = d.id),
                       ec.entity_id, co.fy_end_month, f.has_summary, f.has_extraction, f.has_tone
                  from equity.earnings_call ec
                  join core.event ev on ev.id = ec.event_id
                  join core.document d on d.id = ec.transcript_document_id
                  join core.entity n on n.id = ec.entity_id
                  left join equity.company co on co.entity_id = ec.entity_id
                  left join core.source es on es.id = ev.source_id
                  join core.source ds on ds.id = d.source_id
                  cross join lateral (
                    select exists (select 1 from core.ai_output o where o.task = 'call_summary' and o.document_id = d.id
                                      and o.status = 'published' and o.prompt_version = %s) as has_summary,
                           exists (select 1 from core.ai_output o where o.task = 'guidance_extraction' and o.document_id = d.id
                                      and o.prompt_version = %s
                                      and (o.status = 'published' or (o.status = 'draft' and o.status_reason = 'no_targets')))
                             as has_extraction,
                           exists (select 1 from core.ai_output o where o.task = 'call_tone' and o.document_id = d.id
                                      and o.status = 'published' and o.prompt_version = %s) as has_tone) f
                 where {' and '.join(where)}
                 order by ec.filed_at, ec.event_id""", args).fetchall()
        turns: dict[int, list[Turn]] = {}
        if rows:
            for did, no, a, b, speaker, role, section in db.execute(
                    """select document_id, turn_no, char_start, char_end, speaker, role, section from equity.call_turn
                        where document_id = any(%s) order by document_id, turn_no""", ([r[5] for r in rows],)).fetchall():
                turns.setdefault(did, []).append(Turn(no, a, b, speaker, role, section))
        chunks: dict[int, list[ct.Chunk]] = {}
        if rows and tone:
            for did, no, a, b in db.execute(
                    "select document_id, chunk_no, char_start, char_end from core.chunk where document_id = any(%s) order by 1, 2",
                    ([r[5] for r in rows],)).fetchall():
                chunks.setdefault(did, []).append(ct.Chunk(no, a, b))
        tone_schema = load_schema(ct.TASK, PROMPT_VERSION[ct.TASK]) if tone else None
        metrics = self.metrics or ge.load_metrics()
        schema = load_schema(ge.TASK, PROMPT_VERSION[ge.TASK]) if guidance else None
        summary_schema = (load_schema(cs.TASK, PROMPT_VERSION[cs.TASK])
                          if cs.route_of(PROMPT_VERSION[cs.TASK]) == "json" else None)     # v1-v5 have none
        out = []
        for event_id, slug, label, call_date, filed, did, chars, company, share, text, entity_id, fy_end, has_s, has_x, has_t in rows:
            if text is None or len(text) != chars:
                continue                                       # the database refuses this at commit; never send it
            title = f"{company} · {label or 'earnings call'} transcript"
            held = share is not None and float(share) > ROLE_HOLD_SHARE         # strictly greater, as the rules and the views
            call = CallDoc(did, text, title, company, label, call_date or filed, tuple(turns.get(did, ())), held, fy_end)
            if not (lacking and has_s):
                out.append(Work(cs.TASK, call, slug, event_id, entity_id, summary_schema))
            if tone and not held and not (lacking and has_t):
                items = ct.passages(call, chunks.get(did, ()))
                if items:                                  # never for a role-held call (Spec 02B.1); none to score: no request
                    out.append(Work(ct.TASK, call, slug, event_id, entity_id, tone_schema, passages=tuple(items)))
            if not guidance or (lacking and has_x):
                continue
            if call.day is None or fy_end is None or not call.turns:
                if skipped is not None:
                    skipped.append(slug)
                continue
            out.append(Work(ge.TASK, call, slug, event_id, entity_id, schema, metrics))
        return out

    # ------------------------------------------------------------------ fetch (network; outputs stored as they come)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        f = Fetched("batch", budget=batch.monthly_budget(db), month_spend=batch.month_spend(db), since=ctx.since,
                    until=ctx.until, only=only_task())
        prompts = {t: load_prompt(t, v) for t, v in PROMPT_VERSION.items()}
        works = [w for w in self._calls(db, since=ctx.since, until=ctx.until, skipped=f.no_guidance, tone=f.only == ct.TASK)
                 if (f.only is None and w.task != ct.TASK) or f.only == w.task]       # tone only when the run is limited to it
        f.wanted = sum(1 for w in works if w.task == cs.TASK)
        f.wanted_guidance = sum(1 for w in works if w.task == ge.TASK)
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
        if req.task == ge.TASK:
            return self._store_guidance(tx, w, call, task_run_id, retry_note)
        if req.task == ct.TASK:
            return self._store_tone(tx, w, call, task_run_id, retry_note)
        if call.checked is None:                       # the reply didn't end its turn
            content, body, cites = {"attempt": call.attempt, "stop_reason": reply.stop_reason,
                                    "problems": call.reasons(), "bullets": []}, None, []
        else:
            content, body, cites = summaries.content(call.checked, attempt=call.attempt, reply=reply)
            content["echo_rule"] = cs.ECHO_RULE              # checked under the copied-text rule (the re-check skips it)
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

    def _store_tone(self, tx, w: Work, call: Call, task_run_id: int, retry_note: str | None) -> None:
        """One billed tone reply: its output, and either its citations and publication (superseding the earlier published
        output) or its rejection. The call's tone itself is `equity.call_scores`'s, read from the published output."""
        req, reply, checked = call.request, call.reply, call.checked
        if checked is None:                            # the reply didn't end its turn
            content = {"scores": [], "evidence": [], "attempt": call.attempt, "stop_reason": reply.stop_reason,
                       "problems": call.reasons()}
        else:
            content = ct.content(checked, attempt=call.attempt, reply=reply)
        content["message_id"] = reply.message_id
        if retry_note:
            content["retry_note"] = retry_note
        out_id = tx.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd, task_run_id)
               values (%s, %s, %s, null, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (req.task, w.document_id, json.dumps(content, ensure_ascii=False), reply.model, req.prompt.key,
             req.prompt.version, req.input_sha256, reply.usage.prompt_tokens, reply.usage.output, reply.cost_usd,
             task_run_id)).fetchone()[0]
        call.output_id = out_id
        reason = None
        if call.ok:
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id,
                                                     char_start, char_end, verified)
                       values (%s, %s, %s, 'document_span', %s, %s, %s, true)""",
                    [(out_id, i + 1, path, w.document_id, a, b) for i, (path, (a, b)) in enumerate(checked.citations)])
            tx.execute("""update core.ai_output set status = 'superseded', status_reason = %s
                           where task = %s and document_id = %s and scope = '' and status = 'published' and id <> %s""",
                       (f"superseded by output {out_id}", req.task, w.document_id, out_id))
            tx.execute("update core.ai_output set status = 'published' where id = %s", (out_id,))
            status = "succeeded"
        else:
            reason = (("retried: " if retry_note else "rejected: ") + "; ".join(call.reasons()))[:1000]
            tx.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s", (reason, out_id))
            status = "rejected"
        tx.execute("update ops.ai_request set status = %s, ai_output_id = %s, reject_reason = %s where custom_id = %s",
                   (status, out_id, reason, call.custom_id))

    def _store_guidance(self, tx, w: Work, call: Call, task_run_id: int, retry_note: str | None) -> None:
        """One billed extraction reply: its output; then, if it holds an item, its citations, its rows and its publication
        (superseding the earlier published output), or, if it holds none, a draft marked `no_targets`; or its rejection."""
        req, reply, checked = call.request, call.reply, call.checked
        if checked is None:                            # the reply didn't end its turn
            content = {"targets": [], "stated_figures": [], "attempt": call.attempt, "stop_reason": reply.stop_reason,
                       "problems": call.reasons()}
        else:
            content = ge.content(checked, attempt=call.attempt, reply=reply, metrics=w.metrics)
        content["message_id"] = reply.message_id
        if retry_note:
            content["retry_note"] = retry_note
        out_id = tx.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd, task_run_id)
               values (%s, %s, %s, null, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (req.task, w.document_id, json.dumps(content, ensure_ascii=False), reply.model, req.prompt.key,
             req.prompt.version, req.input_sha256, reply.usage.prompt_tokens, reply.usage.output, reply.cost_usd,
             task_run_id)).fetchone()[0]
        call.output_id = out_id
        reason = None
        if call.ok and not checked.empty:
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id,
                                                     char_start, char_end, verified)
                       values (%s, %s, %s, 'document_span', %s, %s, %s, true)""",
                    [(out_id, i + 1, path, c.document_id, c.start, c.end) for i, (path, c) in enumerate(checked.citations)])
            self._insert_items(tx, w, out_id, checked)
            tx.execute("""update core.ai_output set status = 'superseded', status_reason = %s
                           where task = %s and document_id = %s and scope = '' and status = 'published' and id <> %s""",
                       (f"superseded by output {out_id}", req.task, w.document_id, out_id))
            tx.execute("update core.ai_output set status = 'published' where id = %s", (out_id,))
            tx.execute("update equity.earnings_call set guidance_targets = %s where event_id = %s",
                       (checked.management_targets, w.event_id))
            status = "succeeded"
        elif call.ok:                                  # nothing found (or everything dropped): a draft, never published
            tx.execute("update core.ai_output set status_reason = 'no_targets' where id = %s", (out_id,))
            tx.execute("""update equity.earnings_call set guidance_targets = 0
                           where event_id = %s and not exists (select 1 from core.ai_output o where o.task = %s
                                                                and o.document_id = %s and o.status = 'published')""",
                       (w.event_id, req.task, w.document_id))
            status = "succeeded"
        else:
            reason = (("retried: " if retry_note else "rejected: ") + "; ".join(call.reasons()))[:1000]
            tx.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s", (reason, out_id))
            status = "rejected"
        tx.execute("update ops.ai_request set status = %s, ai_output_id = %s, reject_reason = %s where custom_id = %s",
                   (status, out_id, reason, call.custom_id))

    @staticmethod
    def _insert_items(tx, w: Work, out_id: int, checked: ge.Checked) -> None:
        """The rows of a checked extraction: management targets first, then the separate list, then the stated figures, each in
        document order, so a duplicate's row refers to a row already written. No `core.forecast` row (#177)."""
        d, call = (lambda v: None if v is None else Decimal(v)), w.call
        ids: dict[int, int] = {}
        for i, t in enumerate(checked.targets):
            ids[i] = tx.execute(
                """insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start,
                                                char_end, metric, metric_label, metric_text, kind, value, low, high, unit,
                                                direction, comparison, period_text, period_kind, target_start, target_end,
                                                basis, growth_basis, scope_text, vintage_date, duplicate_of, not_scored_reason,
                                                speaker_role)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s) returning id""",
                (w.event_id, w.entity_id, out_id, t["citation_no"], call.document_id, *t["span"], t["metric"],
                 t["metric_label"], t["metric_text"], t["kind"], d(t["value"]), d(t["low"]), d(t["high"]), t["unit"],
                 t["direction"], t["comparison"], t["period_text"], t["period_kind"],
                 date.fromisoformat(t["target_start"]) if t["target_start"] else None,
                 date.fromisoformat(t["target_end"]) if t["target_end"] else None, t["basis"], t["growth_basis"],
                 t["scope_text"], call.day, None if t["duplicate_of"] is None else ids[t["duplicate_of"]],
                 t["not_scored_reason"], t["speaker_role"])).fetchone()[0]
        ids = {}
        for i, s in enumerate(checked.stated):
            ids[i] = tx.execute(
                """insert into equity.stated_figure (call_event_id, entity_id, ai_output_id, citation_no, document_id,
                                                     char_start, char_end, metric, metric_label, metric_text, value, unit,
                                                     period_text, period_kind, period_start, period_end, basis, growth_basis,
                                                     scope_text, duplicate_of)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                (w.event_id, w.entity_id, out_id, s["citation_no"], call.document_id, *s["span"], s["metric"],
                 s["metric_label"], s["metric_text"], Decimal(s["value"]), s["unit"], s["period_text"], s["period_kind"],
                 date.fromisoformat(s["period_start"]), date.fromisoformat(s["period_end"]), s["basis"], s["growth_basis"],
                 s["scope_text"], None if s["duplicate_of"] is None else ids[s["duplicate_of"]])).fetchone()[0]

    def _entry(self, db, cid: str, doc_id: int | None, prompts, index: dict):
        """(work, request, attempt) for a stored custom_id, from its call as it is now; None if the request no longer
        matches (a new prompt version, a changed transcript, a call no longer eligible: stale)."""
        if doc_id is None:
            return None
        if doc_id not in index:
            index[doc_id] = {}
            for w in self._calls(db, doc_ids=[doc_id], lacking=False, tone=True):
                for c, req, attempt in self._next_step(db, w, prompts[w.task]).built:
                    index[doc_id][c] = (w, req, attempt)
        return index[doc_id].get(cid)

    @staticmethod
    def _insert_bare(db, cid: str, reply: Reply, task_run_id: int, spend: Decimal, reason: str, extra: dict,
                     retry_note: str | None) -> None:
        task, doc_id, sha, version = db.execute(
            "select ai_task, document_id, input_sha256, prompt_version from ops.ai_request where custom_id = %s",
            (cid,)).fetchone()
        empty = ({"targets": [], "stated_figures": []} if task == ge.TASK
                 else {"scores": [], "evidence": []} if task == ct.TASK else {"bullets": []})
        content = {**empty, **extra, "stop_reason": reply.stop_reason, "message_id": reply.message_id}
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
        self._echo = self._echo_recheck(tx, f, task_run_id) if f.only not in (ge.TASK, ct.TASK) else EchoRun()    # extraction only: no summary work
        return n + f.batch.results["stale"] + f.batch.results["unstorable"] + self._echo.written   # the stale and unstorable ones stored bare

    # ------------------------------------------------------------------ the copied-text re-check (no AI)
    def _echo_where(self, f: Fetched) -> tuple[list[str], list]:
        where = ["o.task = %s", "o.status = 'published'", "o.scope = ''", "o.prompt_version = %s"]
        args: list = [cs.TASK, PROMPT_VERSION[cs.TASK]]
        if self.scope is not None:
            where.append("ev.slug = any(%s)"); args.append(sorted(self.scope))
        if f.since is not None:
            where.append(f"(ec.filed_at at time zone '{IST}')::date >= %s"); args.append(f.since)
        if f.until is not None:
            where.append(f"(ec.filed_at at time zone '{IST}')::date <= %s"); args.append(f.until)
        return where, args

    @staticmethod
    def _transcript(tx, doc_id: int, chars: int) -> str | None:
        """The canonical text of a document as its chunks hold it, or None if it isn't whole (`char_count`)."""
        text = tx.execute("select string_agg(c.body, '' order by c.chunk_no) from core.chunk c where c.document_id = %s",
                          (doc_id,)).fetchone()[0]
        return text if text is not None and len(text) == chars else None

    def _echo_recheck(self, tx, f: Fetched, task_run_id: int) -> EchoRun:
        """The published summaries of the current prompt version that were stored before the copied-text rule (no `echo_rule`
        in their content), each checked against its transcript: unchanged, or superseded by the same bullets with their copied
        ends cut, or replaced by a rejection when a bullet fails (see the module docstring). One at a time, so only one
        transcript is held."""
        where, args = self._echo_where(f)
        where.append("o.content ->> 'echo_rule' is distinct from %s"); args.append(cs.ECHO_RULE)
        rows = tx.execute(
            f"""select o.id, o.document_id, o.content, o.model, o.prompt_key, o.prompt_version, o.input_sha256, n.name,
                       ec.fiscal_label, d.char_count
                  from core.ai_output o
                  join equity.earnings_call ec on ec.transcript_document_id = o.document_id
                  join core.event ev on ev.id = ec.event_id
                  join core.entity n on n.id = ec.entity_id
                  join core.document d on d.id = o.document_id
                 where {' and '.join(where)}
                 order by o.id""", args).fetchall()
        run = EchoRun()
        for out_id, doc_id, content, model, prompt_key, version, sha, company, label, chars in rows:
            name = f"{company} {label or 'call'}"
            text = self._transcript(tx, doc_id, chars)
            if text is None:
                run.unreadable.append(name)
                continue
            run.looked += 1
            run.docs.append(doc_id)
            spans = {no: (a, b) for no, a, b in tx.execute(
                "select citation_no, char_start, char_end from core.ai_citation where ai_output_id = %s and verified",
                (out_id,)).fetchall()}
            r = cs.recheck_content(content, spans, text, summaries.match_form(text))
            lists = [b.get("list") or "none" for b in content.get("bullets") or []]
            run.cut.update(lists[i] for i in r.cut)
            run.failed.update(lists[i] for i in r.failed)
            run.unclean += len(r.unclean)
            run.reasons.update(why.split(" (")[0] for why in r.failed.values())
            if r.verdict == "unchanged":
                run.unchanged += 1
                continue
            self._echo_write(tx, out_id, doc_id, content, (model, prompt_key, version, sha), r, task_run_id)
            if r.failed:
                run.rejected += 1
                run.lost.append(name)
            else:
                run.superseded += 1
        return run

    @staticmethod
    def _echo_write(tx, old_id: int, doc_id: int, content: dict, parts: tuple, r: cs.Rechecked, task_run_id: int) -> None:
        """One re-checked output replaced (store once, never lose: the old row stays, superseded). Written like a stored reply
        (`_store`): a draft, its citations (copied from the old output), then the old one superseded and this one published;
        or, if a bullet fails, a rejected output and the old one superseded, so the call has no published summary."""
        model, prompt_key, version, sha = parts
        new = {**content, "bullets": r.bullets, "recheck": cs.ECHO_RULE, "echo_rule": cs.ECHO_RULE}
        reasons = [f"bullet {i + 1}: {why}" for i, why in sorted(r.failed.items())]
        if r.failed:
            new["bullets"] = []
            new["problems"] = reasons
            new["dropped"] = [{"text": r.bullets[i]["text"], "problem": why} for i, why in sorted(r.failed.items())]
        else:
            new["echo_cut"] = r.cut
        out_id = tx.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd, task_run_id)
               values (%s, %s, %s, %s, 'draft', %s, %s, %s, %s, null, null, 0, %s) returning id""",
            (cs.TASK, doc_id, json.dumps(new, ensure_ascii=False), None if r.failed else "\n".join(b["text"] for b in r.bullets),
             model, prompt_key, version, sha, task_run_id)).fetchone()[0]
        if r.failed:
            reason = ("rejected: re-check " + cs.ECHO_RULE + ": " + "; ".join(reasons))[:1000]
            tx.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s", (reason, out_id))
            tx.execute("update core.ai_output set status = 'superseded', status_reason = %s where id = %s",
                       (f"superseded by output {out_id} (re-check {cs.ECHO_RULE}: a bullet copies transcript text)", old_id))
            return
        tx.execute("""insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end,
                                                    verified)
                      select %s, citation_no, claim_path, kind, document_id, char_start, char_end, verified
                        from core.ai_citation where ai_output_id = %s""", (out_id, old_id))
        tx.execute("update core.ai_output set status = 'superseded', status_reason = %s where id = %s",
                   (f"superseded by output {out_id} (re-check {cs.ECHO_RULE}: copied text cut)", old_id))
        tx.execute("update core.ai_output set status = 'published' where id = %s", (out_id,))

    def _echo_checks(self, tx, f: Fetched, fresh: Sequence[int] = ()) -> list[Check]:
        """The re-check's two checks: what is stored for each transcript looked at, read back against its text (blocking), and
        the counts (a warning when a call lost its summary or a transcript couldn't be read). `fresh`: the summaries published in
        this run (a reply of the current prompt version), read back the same way."""
        run = self._echo
        where, args = self._echo_where(f)
        where.append("o.document_id = any(%s)"); args.append(run.docs)
        left, longest, bad = 0, 0, []
        if run.docs:
            rows = tx.execute(
                f"""select o.id, o.document_id, o.content, n.name, ec.fiscal_label, d.char_count
                      from core.ai_output o
                      join equity.earnings_call ec on ec.transcript_document_id = o.document_id
                      join core.event ev on ev.id = ec.event_id
                      join core.entity n on n.id = ec.entity_id
                      join core.document d on d.id = o.document_id
                     where {' and '.join(where)}
                     order by o.id""", args).fetchall()
            for out_id, doc_id, content, company, label, chars in rows:
                text = self._transcript(tx, doc_id, chars)
                if text is None:
                    bad.append(f"{company} {label}: its transcript can't be read back whole")
                    continue
                spans = {no: (a, b) for no, a, b in tx.execute(
                    "select citation_no, char_start, char_end from core.ai_citation where ai_output_id = %s and verified",
                    (out_id,)).fetchall()}
                r = cs.recheck_content(content, spans, text, summaries.match_form(text))
                left += 1
                longest = max(longest, r.longest)
                if r.verdict != "unchanged":
                    bad.append(f"{company} {label} (output {out_id}): bullets {sorted({*r.cut, *r.failed})} still hold copied text")
        for out_id in fresh:
            row = tx.execute(
                """select o.document_id, o.content, n.name, ec.fiscal_label, d.char_count
                     from core.ai_output o
                     join equity.earnings_call ec on ec.transcript_document_id = o.document_id
                     join core.entity n on n.id = ec.entity_id
                     join core.document d on d.id = o.document_id
                    where o.id = %s""", (out_id,)).fetchone()
            if row is None:
                bad.append(f"output {out_id}: not found on a transcript")
                continue
            doc_id, content, company, label, chars = row
            text = self._transcript(tx, doc_id, chars)
            if text is None:
                bad.append(f"{company} {label}: its transcript can't be read back whole")
                continue
            form = summaries.match_form(text)
            left += 1
            for i, b in enumerate((content or {}).get("bullets") or []):
                run_len = summaries.longest_copied(summaries.match_form(b["text"]), form)
                longest = max(longest, run_len)
                if run_len > cs.MAX_ECHO_CHARS:
                    bad.append(f"{company} {label} (output {out_id}): bullet {i + 1} holds a verbatim run of {run_len} characters")
        clean = Check("echo_clean", not bad,
                      "; ".join(bad[:SHOWN]) or f"{left} published summaries looked at: no bullet holds a verbatim run over "
                      f"{cs.MAX_ECHO_CHARS} characters (the longest left: {longest})")
        detail = {"rule": cs.ECHO_RULE, "looked": run.looked, "unchanged": run.unchanged, "superseded": run.superseded,
                  "rejected": run.rejected, "bullets_cut": dict(sorted(run.cut.items())),
                  "bullets_failed": dict(sorted(run.failed.items())), "cut_without_clause_end": run.unclean,
                  "failures_by_reason": dict(sorted(run.reasons.items())), "lost_publication": sorted(run.lost),
                  "transcript_unreadable": sorted(run.unreadable), "published_left": left, "longest_verbatim_run_left": longest}
        return [clean, Check("echo_recheck", not (run.rejected or run.unreadable), json.dumps(detail), blocking=False)]

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out: list[Check] = []
        published = [(o.work, c.output_id) for o in f.outcomes if o.status == "published" and o.work.task == cs.TASK
                     for c in o.calls if c.ok]
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
        out += self._echo_checks(tx, f, [out_id for _, out_id in published])

        c = Counter(o.status for o in f.outcomes if o.work.task == cs.TASK)
        mapping: Counter = Counter()
        for o in f.outcomes:
            for call in o.calls:
                if call.checked:
                    mapping.update(call.checked.mapping)
        rejected = [o for o in f.outcomes if o.status == "rejected"]
        month = tx.execute(
            """select count(*) filter (where r.status = 'rejected' and r.reject_reason like 'rejected:%%'),
                      count(*) filter (where r.status = 'succeeded' or r.reject_reason like 'rejected:%%')
                 from ops.ai_request r where r.ai_task = any(%s) and r.created_at >= date_trunc('month', now())""",
            ([cs.TASK, ge.TASK, ct.TASK],)).fetchone()
        rate = Decimal(month[0]) / Decimal(month[1]) if month[1] else Decimal(0)
        detail = {"run": dict(sorted(c.items())), "citations": dict(sorted(mapping.items())),
                  "month": {"rejected": month[0], "targets": month[1], "rate": f"{rate:.3f}"},
                  "rejected": [f"{o.work.target}: {o.reason}" for o in rejected]}
        out.append(Check("citation_integrity", not rejected and not (month[1] >= INTEGRITY_MIN_TARGETS
                                                                    and rate >= INTEGRITY_LIMIT),
                         json.dumps(detail), blocking=False))
        # A call too long for its summary is too long for its extraction too: named once.
        too_long, named = [], set()
        for w, est in f.too_big:
            if w.slug not in named:
                named.add(w.slug)
                too_long.append(f"{w.slug} (about {est:,} tokens, over {cs.MAX_ESTIMATED_TOKENS:,}; skipped)")
        for o in f.outcomes:
            if o.status == "too_long" and o.work.slug not in named:
                named.add(o.work.slug)
                too_long.append(f"{o.work.slug} ({o.reason})")
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
        out += self._guidance_checks(tx, f)
        out += self._tone_checks(tx, f)
        return out

    def _tone_checks(self, tx, f: Fetched) -> list[Check]:
        """The tone reply's checks: each output stored in this run, read back, has a score for every passage it was sent, every
        score in range, and verified citations; drops counted by reason (numbers only)."""
        done = [(o.work, c) for o in f.outcomes if o.status == "published" and o.work.task == ct.TASK for c in o.calls if c.ok]
        if not done and not any(o.work.task == ct.TASK for o in f.outcomes):
            return []
        wrong = []
        for w, call in done:
            row = tx.execute("""select o.content, (select count(*) from core.ai_citation c where c.ai_output_id = o.id and c.verified)
                                  from core.ai_output o where o.id = %s and o.status = 'published'""", (call.output_id,)).fetchone()
            if row is None:
                wrong.append(f"{w.slug}: not published")
                continue
            scores = row[0].get("scores") or []
            if len(scores) != len(w.passages) or any(s.get("score") not in ct.SCORES for s in scores):
                wrong.append(f"{w.slug}: {len(scores)} scores for {len(w.passages)} passages, or one out of range")
            elif row[1] < 1:
                wrong.append(f"{w.slug}: no verified citation")
        drops: Counter = Counter()
        for o in f.outcomes:
            if o.work.task == ct.TASK:
                for call in o.calls:
                    if call.checked is not None:
                        drops.update(call.checked.drops)
        out = [Check("tone_stored", not wrong, "; ".join(wrong) or f"{len(done)} stored, each with a score per passage and its citations")]
        for key, label in ct.DROPS.items():
            if drops[key]:
                out.append(Check(f"tone_dropped_{key}", True, f"{drops[key]} evidence items dropped: {label}", blocking=False))
        counts = Counter(o.status for o in f.outcomes if o.work.task == ct.TASK)
        out.append(Check("call_tone", True, "this run: " + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing to do"),
                         blocking=False))
        return out

    def _guidance_checks(self, tx, f: Fetched) -> list[Check]:
        """The extraction's checks: what is stored for each reply stored in this run, each stored row again against the
        transcript, and the drops counted by reason."""
        out: list[Check] = []
        done = [(o.work, c) for o in f.outcomes if o.status == "published" and o.work.task == ge.TASK
                for c in o.calls if c.ok]
        wrong, bad = [], []
        for w, call in done:
            chk, text = call.checked, w.call.text
            row = tx.execute(
                """select o.status, o.status_reason,
                          (select count(*) from core.ai_citation c join core.document d on d.id = c.document_id
                            where c.ai_output_id = o.id and c.verified and d.is_published and c.char_end <= d.char_count),
                          (select count(*) from equity.guidance g where g.ai_output_id = o.id),
                          (select count(*) from equity.stated_figure s where s.ai_output_id = o.id),
                          (select ec.guidance_targets from equity.earnings_call ec where ec.event_id = %s)
                     from core.ai_output o where o.id = %s""", (w.event_id, call.output_id)).fetchone()
            if row is None:
                wrong.append(f"{w.target}: no output {call.output_id}")
                continue
            status, why, cited, n_g, n_s, shown = row
            if chk.empty:
                good = (status, why, cited, n_g, n_s) == ("draft", "no_targets", 0, 0, 0)
            else:
                good = (status in ("published", "superseded") and cited == n_g + n_s == len(chk.targets) + len(chk.stated)
                        and n_g == len(chk.targets) and n_s == len(chk.stated)
                        and (status != "published" or shown == chk.management_targets))
            if not good:
                wrong.append(f"{w.target}: {row}")
                continue
            turns = [Turn(*r) for r in tx.execute(
                """select turn_no, char_start, char_end, speaker, role, section from equity.call_turn
                    where document_id = %s order by turn_no""", (w.document_id,)).fetchall()]
            rows = [(f"target {i}", r, "target") for i, r in enumerate(tx.execute(
                """select speaker_role, char_start, char_end, metric, kind, value, low, high from equity.guidance
                    where ai_output_id = %s order by id""", (call.output_id,)).fetchall())]
            rows += [(f"stated figure {i}", ("management", a, b, m, "point", v, None, None), "stated") for i, (a, b, m, v)
                     in enumerate(tx.execute("""select char_start, char_end, metric, value from equity.stated_figure
                                                 where ai_output_id = %s order by id""", (call.output_id,)).fetchall())]
            for name, (role, a, b, metric, kind, value, low, high), _ in rows:
                quote, hit = text[a:b], [t for t in turns if t.char_start < b and a < t.char_end]
                if b - a > ge.MAX_QUOTE:
                    problem = f"its quote is {b - a} code points, over {ge.MAX_QUOTE}"
                elif len(hit) != 1 or a < hit[0].char_start or b > hit[0].char_end or hit[0].role != role:
                    problem = f"its quote isn't inside one turn whose role is {role}"
                elif role == "unknown" and not ge.passes_content_test(quote):
                    problem = "its quote fails the content test of the separate list"
                elif not ge.figures_ok(quote, metric, kind, value, low, high):
                    problem = "a figure isn't in its quote"
                else:
                    continue
                bad.append(f"{w.target}: {name} {problem}")
        out.append(Check("guidance_stored", not wrong,
                         "; ".join(wrong) or f"{len(done)} stored, each with its citations, its rows and its count"))
        out.append(Check("guidance_items", not bad,
                         "; ".join(bad) or f"{len(done)} stored, every row's quote in a turn its list allows, its figures in it"))
        drops: Counter = Counter()
        for o in f.outcomes:
            if o.work.task == ge.TASK:
                for call in o.calls:
                    if call.checked is not None:
                        drops.update(call.checked.drops)
        always = ("analyst_turn", "moderator_turn", "across_turns", "content_test")      # Spec 02B.1 C3
        for key, label in ge.DROPS.items():
            if key in always or drops[key]:
                out.append(Check(f"guidance_dropped_{key}", True, f"{drops[key]} items dropped: {label}", blocking=False))
        counts = Counter(state(o) for o in f.outcomes if o.work.task == ge.TASK)
        skipped = (f"; not requested, no fiscal-year end month, call date or speaker turns: {_names(f.no_guidance)}"
                   if f.no_guidance else "")
        out.append(Check("call_guidance", True,
                         f"{f.wanted_guidance} calls lacked their extraction at the start; this run: "
                         + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing to do") + skipped,
                         blocking=False))
        return out

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """From the stored state, not just this run, for each target: a request finally rejected raises
        `ai_rejected:<task>:<document>`; a published output (an extraction that found nothing counts) resolves it."""
        f = self._last
        if f is None:
            return []
        out: list[Alert] = []
        for task in (cs.TASK, ge.TASK, ct.TASK):
            targets = [w for w in f.targets if w.task == task]
            if not targets:
                continue
            ids = sorted({w.document_id for w in targets})
            bad = dict(db.execute(
                """select r.document_id, max(r.reject_reason) from ops.ai_request r
                    where r.document_id = any(%s) and r.ai_task = %s and r.status = 'rejected'
                      and r.reject_reason like 'rejected:%%' group by r.document_id""", (ids, task)).fetchall())
            for w in targets:
                done = db.execute("""select 1 from core.ai_output where task = %s and document_id = %s
                                      and (status = 'published' or (status = 'draft' and status_reason = 'no_targets'))""",
                                  (task, w.document_id)).fetchone() is not None
                key = f"ai_rejected:{task}:{w.document_id}"
                if done:
                    out.append(Alert(key, False))
                elif w.document_id in bad:
                    out.append(Alert(key, True, "warn", f"{w.target} ({w.call.title}): {bad[w.document_id]}"))
        if f.targets:
            out += spend_alerts(f, RUN_LIMIT_USD)
        month = datetime.now(UTC).strftime("%Y-%m")
        out.append(Alert(f"ai_unavailable:calls:{month}", f.unavailable is not None, "warn",
                         f"AI paused ({AI_REASONS.get(f.unavailable, f.unavailable)}): call summaries and extracted figures are skipped; "
                         "published summaries and the data are unchanged"))
        return out
