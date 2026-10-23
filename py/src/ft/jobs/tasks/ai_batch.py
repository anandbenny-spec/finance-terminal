"""policy.ai_batch (Prompt 01 B7; Architecture 03 §1.1-§1.5, §1.10; Spec 01 §3.2-§3.3): the cited summaries of
published documents (`doc_summary`, `statement_diff_summary`), the RBI stance of each MPC resolution
(`stance_extraction`), the projection fallback where the regex couldn't read a resolution's projection sentence
(`projection_extraction`), and the chunk embeddings. Part 1 built real time on decision days; part 2 the Batch API
path, then the two extractions (ft/ai/extraction.py). The AI backfill run is Anand's to start.

**Extractions** (JSON route): only on MPC resolutions, so no Fed stance label exists anywhere (Spec 01 D2). The stance
is the meeting's output (core.ai_output.event_id, which the views read), cited to the resolution; it's published only
if it agrees with the stance B6's parser reads in the same resolution (its raw file parsed again; Anand, 24 Oct 2026,
issue #16). A projection output's targets become core.forecast rows (method 'ai_extracted', the quote as the span); a
target the regex stored must agree. `other` and any disagreement are held: the output stays a draft, the request
counts as answered (never resent for the same input), `ai_held:<task>:<document>` is raised, and each run checks the
held answer again, so a fixed parser or a corrected figure publishes it without a new request.

**When it sends what:**
- watch, on a decision day (`after_decision_document`), with `ai_realtime_on_decision_days` on: the meeting's newly
  published decision documents (statement, resolution, Governor's statement) through the real-time API, so the
  summary and "what changed" are up within about half an hour; and their chunks' embeddings. A poll that leaves a
  target unfinished (the API refused or failed) fails, so the next poll tries again while the window is open; the
  targets it finished are skipped then.
- daily, refresh and backfill: every published document's targets that still need a request, through the Batch API
  (Architecture 03 §1.3): submitted, then polled for up to 15 minutes in the run, otherwise collected by the next run.
  Each run first reads the batches still open (from any run) and collects the ended ones; a reply that needs its
  retry, or a request that errored or expired in an earlier run's batch, goes into a new batch in the same run while
  the poll lasts. A backfill's --since and --until don't apply: the step is idempotent, so a run's targets are every
  published document still missing an output, the 2018 baselines included, except the decision documents of a
  meeting whose watch window is open (with real time on, they're the watch's: a batch would hold them for an hour and
  the watch never sends twice). Watch with the real-time switch off uses this path too, without polling (the next
  run collects). Each run reads only this task's batches. These runs also embed every published document's chunks
  that lack an embedding.

**Never twice, never lost.** Each request is keyed by its input hash (ft/ai/client.py) in ops.ai_request, written
'pending' before it's sent. Each billed reply is stored at once, in its own small transaction on the fetch's
connection: the output (with its model, prompt version, tokens and cost), its citations, publication (superseding the
target's earlier published output: one per task and target, Architecture 01 §1.4) or rejection, the request's final
status and, for a batch reply, the run's spend. Like the raw files a fetch registers (Architecture 02 §1.2), a billed
reply is evidence that must survive a failed load, so it isn't held for the load's transaction; the database's own
checks (a published output needs a verified citation inside a published document's text) run at each of these
commits. A request `succeeded` or `rejected` is never sent again; one that `errored` or `expired` (not billed) may
be, by a later request or batch, once the batch it was in has been read to the end (never twice in one batch run, so
a request the API keeps refusing can't loop). A request waiting in a batch (`pending`, with its ops.ai_batch row)
isn't sent again while the batch is open. An ended batch is collected once: each result is stored in a transaction
that first locks its request's row and skips it unless it's still pending, and a read that breaks off, or ends short
of the batch's count, is finished by the next read. A batch the API answers 404 for is given up (marked failed, its
requests sendable) only once its results would have expired anyway (29 days); sooner, it stays open and the `batches`
check fails, since its replies may have been billed. A create that fails ambiguously (a lost connection, a timeout, a
5xx that isn't a refusal: `client.maybe_created`) is looked for among the workspace's newest batches and adopted if
exactly one matches; otherwise its requests are marked errored. Two windows are accepted: an ambiguous create whose
batch isn't found, and a run that dies between the API creating a batch and its ops.ai_batch row being written (each
batch's id is printed to the run's log first). Either way a later run sends those requests again (as in real time,
where a reply lost before it's stored is resent). The run's spend goes on ops.task_run.ai_cost_usd as each reply
arrives; a batch's total goes on ops.ai_batch.cost_usd once it's collected. The load holds the embeddings.

**Batch ids.** The Batch API takes only `^[a-zA-Z0-9_-]{1,64}$`, so each request goes with its stored custom_id's `:`
replaced (`client.batch_custom_id`), and a result maps back through its batch's own ops.ai_request rows. A result
whose request no longer matches a target that can be built now (a new prompt version or document version since it
was sent, or a document unpublished meanwhile) is still billed: it's stored rejected ("stale: …", outside the
citation_integrity count) with its cost, and sent again only if that same input comes back. A reply the database
refuses (a character it can't hold) is stored bare, with its cost, as a failed attempt, so one bad reply never stalls
the rest of the batch.

**Checking** (ft/ai/summaries.py): a reply is published only when every bullet has a verified citation and only
figures from its cited passages, nothing stands outside the bullets, and the count is in range. Otherwise the first
reply is stored rejected ("retried: …", with the note for the retry) and the request is sent once more with that
note; a second failure is final ("rejected: …") and raises `ai_rejected:<task>:<document>`. A retry that couldn't be
sent (an API error, the budget) is sent by a later run from the stored note. A "what changed" summary runs only when
both documents are published, so every citation points into published text.

**Spend:** before each request, the month's spend (ops.task_run.ai_cost_usd), plus in batch mode the worst case of
every request of this task still waiting in an open batch (from its document's length, whatever its target is now),
plus the request's own worst case must stay under `ai_monthly_budget_usd`
(alert `ai_budget:<month>`). A run whose estimate (each request and its retry at their worst, at the batch price in a
batch) passes $5 sends nothing more (CLAUDE.md §7; alert `ai_run_estimate`).

**Embeddings:** `voyage-4`, 512 dimensions as halfvec (model key 'voyage-4@512'), with a context line ("Fed · FOMC
statement · 28 Oct 2026 · …") added at embedding time only, so citations still point into the stored text. A missing
key or a Voyage error leaves the chunks for the next run (`embeddings`, W); it never fails the summaries.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from zoneinfo import ZoneInfo

import psycopg

from ft.ai import extraction, summaries
from ft.ai.client import (
    CHARS_PER_TOKEN,
    MAX_REQUEST_TOKENS,
    PRICES,
    REQUEST_OVERHEAD_TOKENS,
    TASKS,
    BatchInfo,
    CallError,
    Client,
    Embedder,
    Failed,
    Reply,
    Request,
    TooLong,
    batch_custom_id,
    halfvec_literal,
    load_prompt,
    load_schema,
    maybe_created,
    price_entry,
)
from ft.ai.summaries import Checked, Doc
from ft.engines.document.text import ExtractError
from ft.jobs import due as due_rules
from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register
from ft.policy import rbi_resolution

PROMPT_VERSION = {"doc_summary": "v1", "statement_diff_summary": "v1", "stance_extraction": "v1",
                  "projection_extraction": "v1"}
REALTIME_DOC_TYPES = ("fomc_statement", "mpc_resolution", "rbi_governor_statement")
EXTRACTION_DOC_TYPE = "mpc_resolution"   # the stance and projection fallback run on MPC resolutions only (Spec 01 D2)
EMBED_MODEL_KEY = "voyage-4@512"
RUN_LIMIT_USD = Decimal(5)            # CLAUDE.md §7: an AI run estimated above $5 is Anand's call
INTEGRITY_LIMIT = Decimal("0.10")     # citation_integrity: rejections stay under 10% (Architecture 03 §1.9)
INTEGRITY_MIN_TARGETS = 10            # the month's rate is judged once it covers this many targets
BANK_TZ = {"fed": ZoneInfo("America/New_York"), "rbi": ZoneInfo("Asia/Kolkata")}
SUMMARY_TASKS = ("doc_summary", "statement_diff_summary")
EXTRACTION_TASKS = extraction.TASKS
AI_TASKS = SUMMARY_TASKS + EXTRACTION_TASKS
PROJECTION_SERIES = {"cpi": "rbi.proj_cpi", "gdp": "rbi.proj_gdp"}   # as policy.rbi_documents

BATCH_POLL_SECONDS = 15 * 60          # Architecture 03 §1.3: polled for up to 15 minutes in the run
BATCH_WAITS = (30.0, 60.0)            # the first wait between reads, then every later one
BATCH_MAX_REQUESTS = 5_000            # per batch (the API allows 100,000)
BATCH_MAX_BYTES = 20_000_000          # per batch, as JSON (the API allows 256 MB; smaller uploads finish well inside
                                      # the create timeout, so an accepted batch is rarely left unacknowledged)
BATCH_STUCK = timedelta(hours=26)     # the API ends every batch within 24 hours: one open longer isn't being read
BATCH_RESULTS_KEPT = timedelta(days=29)   # the API keeps results this long; a batch gone before then isn't given up
CLOCK_SLACK = timedelta(minutes=2)    # between our clock and the API's, when looking for a batch after a lost create
EXPOSURE_PROMPT_CHARS = 6_000         # the prompt, instruction and a retry note, for the open batches' worst case


class _Skip(Exception):
    """The request isn't pending any more: another run stored its reply."""


@cache
def _schema(task: str) -> dict:
    return load_schema(task)


@dataclass
class Work:
    """One target: a task on a document (for the diff summary, also its base and the redline; for the projection
    fallback, the sentences the regex couldn't read). An extraction's cross-check reads B6's parse (`parse`) lazily:
    once, and only when a reply is checked."""
    task: str
    doc: Doc
    base: Doc | None = None
    segments: dict | None = None
    sentences: tuple[tuple[int, int], ...] = ()
    parse: Callable[[], extraction.Parsed] | None = field(default=None, repr=False)
    _parsed: extraction.Parsed | None = field(default=None, repr=False)

    @property
    def target(self) -> str:
        return f"{self.task}:doc-{self.doc.document_id}"

    def request(self, prompt, note: str | None = None) -> Request:
        if self.task == "doc_summary":
            return summaries.doc_summary_request(self.doc, prompt, note=note)
        if self.task == "statement_diff_summary":
            return summaries.diff_summary_request(self.doc, self.base, self.segments, prompt, note=note)
        if self.task == "stance_extraction":
            return extraction.stance_request(self.doc, prompt, _schema(self.task), note=note)
        return extraction.projection_request(self.doc, self.sentences, prompt, _schema(self.task), note=note)

    def parsed(self) -> extraction.Parsed:
        """B6's reading for the cross-check (read once; storage errors propagate, so nothing is held for them)."""
        if self._parsed is None:
            self._parsed = self.parse() if self.parse is not None else extraction.Parsed()
        return self._parsed

    def recheck(self, answer) -> extraction.Checked:
        """An extraction's checks on an answer (a reply's, or a held draft's stored one)."""
        if self.task == "stance_extraction":
            return extraction.check_stance(answer, self.doc.text, self.doc.document_id, self.parsed())
        return extraction.check_projections(answer, self.doc.text, self.doc.document_id, self.sentences, self.parsed())

    def check(self, req: Request, reply: Reply) -> Checked | extraction.Checked:
        if self.task in EXTRACTION_TASKS:
            return self.recheck(extraction.answer_of(reply))
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
class Plan:
    """A target's state, from its requests so far."""
    work: Work
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
    checked: Checked | extraction.Checked | None = None
    failed: str | None = None         # the stop reason of a reply that didn't end its turn
    error: str | None = None          # CallError: not billed, no output
    output_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.checked is not None and self.checked.ok and self.failed is None

    @property
    def held(self) -> bool:
        """An extraction whose checks passed but that is held as a draft with an alert (`other`, or a disagreement
        with B6's parse): never retried."""
        c = self.checked
        return self.failed is None and isinstance(c, extraction.Checked) and c.hold is not None and not c.problems

    def reasons(self) -> list[str]:
        if self.failed:
            return [f"the reply stopped with {self.failed}"]
        return self.checked.reasons() if self.checked else []

    def note(self) -> str:
        """The retry's note about this reply."""
        return "; ".join(self.reasons()) + "." if self.failed else self.checked.note()


@dataclass
class Outcome:
    work: Work
    # real time: published | rejected | held (an extraction kept as a draft with an alert) | errored | done | waiting |
    # too_long | budget
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


@dataclass
class Fetched:
    mode: str                         # 'realtime' | 'batch'
    targets: list[Work] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    batch: BatchLog = field(default_factory=BatchLog)
    spend: Decimal = Decimal(0)
    month_spend: Decimal = Decimal(0)
    budget: Decimal = Decimal(0)
    stopped: str | None = None        # 'budget' | 'run_estimate'
    run_estimate: Decimal = Decimal(0)
    rechecked: list = field(default_factory=list)   # held drafts published on a re-check: (work, output id, checked)
    embeds: list[tuple[int, int, str, list[float]]] = field(default_factory=list)   # doc, chunk_no, body md5, vector
    embed_pending: int = 0            # chunks still without an embedding after this run's attempt
    embed_tokens: int = 0
    embed_requests: int = 0
    embed_host: str | None = None
    embed_error: str | None = None
    _by_target: dict[str, Outcome] = field(default_factory=dict, repr=False)

    def outcome(self, w: Work, status: str | None = None, reason: str = "", *, first: bool = False) -> Outcome:
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
    sleep: Callable[[float], None] = staticmethod(time.sleep)       # tests inject (the batch poll)
    monotonic: Callable[[], float] = staticmethod(time.monotonic)

    def __init__(self):
        self._last: Fetched | None = None
        self._fetcher = None                  # the run's fetcher: the stance cross-check reads raw files through it
        self._parsed_stances: dict[int, extraction.Parsed] = {}

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
                       (select string_agg(c.body, '' order by c.chunk_no) from core.chunk c where c.document_id = d.id),
                       d.event_id, d.raw_file_id, d.source_url
                  from core.document d
                  join core.doc_type dt on dt.key = d.doc_type
                  join core.entity n on n.id = d.entity_id
                  left join core.event e on e.id = d.event_id
                 where {' and '.join(where)}
                 order by d.published_at nulls last, d.id""", args).fetchall()
        out = []
        for did, dtype, label, title, bank, bank_slug, published_at, event_day, chars, text, eid, raw, url in rows:
            if text is None or len(text) != chars:
                continue                                       # the database refuses this at commit; never send it
            tz = BANK_TZ.get(bank_slug)
            day = published_at.astimezone(tz).date() if published_at and tz else event_day
            out.append(Doc(did, dtype, label, title, bank or bank_slug, day, text, eid, event_day, raw, url))
        return out

    def _work(self, db, docs: list[Doc]) -> list[Work]:
        work = [Work("doc_summary", d) for d in docs]
        diffable = {r[0] for r in db.execute("select key from core.doc_type where diffable").fetchall()}
        ids = [d.document_id for d in docs if d.doc_type in diffable]
        if ids:
            work += self._diff_work(db, docs, ids)
        # MPC resolutions only (Spec 01 D2: no Fed stance label anywhere): the stance, and the projection fallback
        # where the regex couldn't fully read a projection sentence (re-read from the stored text).
        for d in docs:
            if d.doc_type != EXTRACTION_DOC_TYPE or d.event_id is None:
                continue
            work.append(Work("stance_extraction", d, parse=lambda d=d: self._parsed_stance(d)))
            unparsed = rbi_resolution.read_projections(d.text)[1]
            if unparsed:
                work.append(Work("projection_extraction", d, sentences=unparsed,
                                 parse=lambda d=d: self._stored_projections(db, d)))
        return work

    def _parsed_stance(self, d: Doc) -> extraction.Parsed:
        """The stance B6's parser reads in this resolution (its raw file, parsed again: the decision part needs the
        page's headings): what the AI's label must equal (Anand, 24 Oct 2026). A storage error propagates (the reply
        stays pending: a real-time target fails its poll, a batch is read again by the next run)."""
        if d.raw_file_id is None or self._fetcher is None:
            return extraction.Parsed(error="the resolution has no stored raw file")
        if d.document_id not in self._parsed_stances:
            data = self._fetcher.read(d.raw_file_id)
            try:
                res = rbi_resolution.parse(data, d.source_url or "")
            except (rbi_resolution.ResolutionError, ExtractError, ValueError) as e:
                got = extraction.Parsed(error=f"{type(e).__name__}: {e}")
            else:
                if res.text.text != d.text:
                    got = extraction.Parsed(error="its raw file no longer reads as the stored text")
                else:
                    label = res.stance.label if res.stance and res.stance.label in rbi_resolution.VOCABULARY else None
                    got = extraction.Parsed(stance=label)
            self._parsed_stances[d.document_id] = got
        return self._parsed_stances[d.document_id]

    @staticmethod
    def _stored_projections(db, d: Doc) -> extraction.Parsed:
        """The projections the regex stored for this meeting (policy.rbi_documents: method 'parsed', vintage = the
        decision date), which an AI-extracted target must agree with."""
        rows = db.execute("""select s.key, f.target_start, f.target_end, f.value, f.value_low, f.value_high
                               from core.forecast f join core.series s on s.id = f.series_id
                              where f.vintage_date = %s and s.key = any(%s) and f.method = 'parsed'""",
                          (d.event_day, list(PROJECTION_SERIES.values()))).fetchall()
        var = {v: k for k, v in PROJECTION_SERIES.items()}
        return extraction.Parsed(stored={(var[k], s, e): (v, lo, hi) for k, s, e, v, lo, hi in rows})

    def _diff_work(self, db, docs: list[Doc], ids: list[int]) -> list[Work]:
        work: list[Work] = []
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

    def _next_step(self, db, w: Work, prompt) -> Plan:
        """What the target needs next, from its requests so far."""
        req = w.request(prompt)
        cid = self.custom_id(req, w.doc.document_id)
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
            cid2 = self.custom_id(req2, w.doc.document_id)
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
                raise CallError("NoKey", None, "ANTHROPIC_API_KEY is not set")
            self.claude = Client(key)
        return self.claude

    @staticmethod
    def _claim(db, w: Work, req: Request, cid: str) -> bool:
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
            (cid, req.task, w.doc.document_id, req.input_sha256, req.model_id, req.prompt.version)).rowcount)

    @staticmethod
    def _pending(db, cid: str) -> bool:
        """Lock the request's row for this transaction: is it still waiting for its reply?"""
        row = db.execute("select status from ops.ai_request where custom_id = %s for update", (cid,)).fetchone()
        return row is not None and row[0] == "pending"

    @staticmethod
    def _too_long(db, w: Work, req: Request, cid: str) -> None:
        db.execute("""insert into ops.ai_request (custom_id, ai_task, document_id, input_sha256, model, prompt_version,
                                                  status, reject_reason)
                      values (%s, %s, %s, %s, %s, %s, 'rejected', 'rejected: too_long')
                      on conflict (custom_id) do nothing""",
                   (cid, req.task, w.doc.document_id, req.input_sha256, req.model_id, req.prompt.version))

    # ------------------------------------------------------------------ fetch (network; outputs stored as they come)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        realtime_on, budget = self._settings(db)
        meeting = ctx.meeting if ctx.workflow == "watch" else None
        mode = "realtime" if meeting is not None and realtime_on else "batch"
        f = Fetched(mode, budget=budget, month_spend=self._month_spend(db))
        prompts = {t: load_prompt(t, v) for t, v in PROMPT_VERSION.items()}
        self._fetcher, self._parsed_stances = ctx.fetcher, {}

        if meeting is not None:
            docs = self._docs(db, event_id=meeting.event_id, doc_types=REALTIME_DOC_TYPES)
        else:
            docs = self._docs(db)
            if realtime_on:                 # a meeting's watch window is open: its decision documents are the watch's
                held = self._watch_documents(db, ctx.now)
                f.batch.held_for_watch = sorted(held & {d.document_id for d in docs})
                docs = [d for d in docs if d.document_id not in held]
        f.targets = self._work(db, docs)
        if mode == "batch":
            self._batch_path(ctx, f, prompts, poll=0 if ctx.workflow == "watch" else BATCH_POLL_SECONDS)
        else:
            todo: list[tuple[Work, Step]] = []
            for w in list(f.targets):
                p = self._next_step(db, w, prompts[w.task])
                if p.held is not None:
                    self._recheck(ctx, f, p)
                elif p.step is None:
                    f.outcome(w, "done", p.why)
                else:
                    todo.append((w, p.step))
            if todo:
                self._send(ctx, f, todo, prompts)
            unfinished = [o for o in f.outcomes if o.status in ("errored", "waiting")]
            if unfinished:
                # Fail the poll, so the next one tries again while the window is open. What finished is stored.
                raise CallError("Unfinished", None, "; ".join(f"{o.work.target}: {o.reason}" for o in unfinished))
        self._embed(db, f, docs if meeting is not None else None)
        return f

    @staticmethod
    def _watch_documents(db, now: datetime) -> set[int]:
        """The decision documents (statement, resolution, Governor's statement) of a meeting whose watch window is open
        now: the watch summarises them in real time, so a batch run leaves them alone (they'd otherwise wait in a
        batch, and the watch never sends a request twice). Once the window closes, batch runs take them over."""
        ids: set[int] = set()
        for bank in due_rules.BANKS.values():
            m = due_rules.decision_meeting(db, bank, now.astimezone(bank.tz).date())
            if m is not None and m.is_open(now):
                ids |= {r[0] for r in db.execute("""select id from core.document where event_id = %s
                                                      and doc_type = any(%s)""",
                                                  (m.event_id, list(REALTIME_DOC_TYPES))).fetchall()}
        return ids

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
        if self.scope is not None:
            where.append("""r.document_id in (select d.id from core.document d join core.event e on e.id = d.event_id
                                               where e.slug = any(%s))""")
            args.append(sorted(self.scope))
        rows = db.execute(f"""select r.ai_task, r.model, coalesce(d.char_count, 0)
                                from ops.ai_request r
                                join ops.ai_batch b on b.id = r.ai_batch_id
                                join ops.task_run tr on tr.id = b.task_run_id
                                left join core.document d on d.id = r.document_id
                               where {' and '.join(where)}""", args).fetchall()
        total = Decimal(0)
        for task, model, chars in rows:
            k = 4 if task == "statement_diff_summary" else 1
            tokens = min(MAX_REQUEST_TOKENS, math.ceil((k * chars + EXPOSURE_PROMPT_CHARS) / CHARS_PER_TOKEN)
                         + REQUEST_OVERHEAD_TOKENS)
            spec = TASKS.get(task)
            total += self._bound(model, tokens, spec.max_tokens if spec else 8_000, batch=True)
        return total

    # ------------------------------------------------------------------ real time (watch, on a decision day)
    def _send(self, ctx: Context, f: Fetched, todo: list[tuple[Work, Step]], prompts) -> None:
        db = ctx.fetcher.db
        claude = self._claude()
        f.run_estimate = sum((Decimal(3 - s.attempt) * self._worst_case(w.request(prompts[w.task], note=s.note))
                              for w, s in todo), Decimal(0))     # a retry each, at worst
        if f.run_estimate > RUN_LIMIT_USD:
            f.stopped = "run_estimate"
            for w, _ in todo:
                f.outcome(w, "budget", f"run estimate ${f.run_estimate:.2f}")
            return
        for i, (w, step) in enumerate(todo):
            o = f.outcome(w, "rejected")
            attempt, note = step.attempt, step.note
            if w.task in EXTRACTION_TASKS:
                try:
                    w.parsed()          # B6's reading, before anything is billed: a reply must be storable at once
                except Exception as e:  # noqa: BLE001 - storage: this poll fails for the target and the next one retries
                    o.status, o.reason = "errored", f"the resolution couldn't be read back: {type(e).__name__}: {e}"
                    continue
            while True:
                req = w.request(prompts[w.task], note=note)
                cid = self.custom_id(req, w.doc.document_id)
                try:
                    claude.check(req)
                except TooLong as e:
                    self._too_long(db, w, req, cid)
                    o.status, o.reason = "too_long", str(e)
                    break
                if f.month_spend + f.spend + self._worst_case(req) > f.budget:
                    f.stopped = "budget"
                    o.status, o.reason = "budget", f"month-to-date ${f.month_spend + f.spend:.4f} of ${f.budget}"
                    break
                if not self._claim(db, w, req, cid):            # answered meanwhile: never twice
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
                retry_note = None if call.ok or call.held or attempt == 2 else call.note()
                with db.transaction():                          # the reply is stored before anything else happens
                    self._store(db, w, call, ctx.task_run_id, retry_note)
                if call.ok:
                    o.status = "published"
                    break
                o.reason = "; ".join(call.reasons())
                if call.held:
                    o.status = "held"
                    break
                if attempt == 2:
                    o.status = "rejected"
                    break
                attempt, note = 2, retry_note
            if o.status == "errored" or f.stopped:
                # The API refused or the money ran out: the rest wait for the next run.
                for w2, _ in todo[i + 1:]:
                    f.outcome(w2, "waiting" if o.status == "errored" else "budget", f"after {f.stopped or o.status}")
                return

    @staticmethod
    def _target_of(w: Work) -> tuple[int | None, int | None]:
        """(document_id, event_id) of the target's output: the stance is the meeting's (the views read it by event),
        everything else its document's."""
        return (None, w.doc.event_id) if w.task == "stance_extraction" else (w.doc.document_id, None)

    def _on_target(self, w: Work) -> tuple[str, tuple]:
        """A condition on core.ai_output matching the target's outputs (task and target), and its arguments."""
        doc_id, event_id = self._target_of(w)
        if event_id is not None:
            return "task = %s and event_id = %s and document_id is null", (w.task, event_id)
        return "task = %s and document_id = %s", (w.task, doc_id)

    def _store(self, tx, w: Work, call: Call, task_run_id: int, retry_note: str | None) -> None:
        """One billed reply: its output, and either its citations and publication (superseding the earlier published
        output; for projections, their core.forecast rows), its hold (an extraction kept as a draft, its request
        succeeded: never sent again unless its input changes) or its rejection; the request's final status. One
        transaction."""
        req, reply = call.request, call.reply
        label = None
        if call.checked is None:                       # the reply didn't end its turn
            content, body, cites = {"attempt": call.attempt, "stop_reason": reply.stop_reason,
                                    "problems": call.reasons()}, None, []
            content.update({"bullets": []} if w.task in SUMMARY_TASKS else {"answer": None})
        elif w.task in SUMMARY_TASKS:
            content, body, cites = summaries.content(call.checked, attempt=call.attempt, reply=reply)
        else:
            content, body, label = extraction.content(call.checked, attempt=call.attempt, reply=reply)
            cites = call.checked.citations if call.ok else []
        content["message_id"] = reply.message_id
        if retry_note:
            content["retry_note"] = retry_note
        doc_id, event_id = self._target_of(w)
        out_id = tx.execute(
            """insert into core.ai_output (task, document_id, event_id, content, body, label, status, model, prompt_key,
                                           prompt_version, input_sha256, input_tokens, output_tokens, cost_usd,
                                           task_run_id)
               values (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (req.task, doc_id, event_id, json.dumps(content, ensure_ascii=False), body, label, reply.model,
             req.prompt.key, req.prompt.version, req.input_sha256, reply.usage.prompt_tokens, reply.usage.output,
             reply.cost_usd, task_run_id)).fetchone()[0]
        call.output_id = out_id
        if call.ok:
            self._publish(tx, w, out_id, cites, call.checked, task_run_id)
            status, reason = "succeeded", None
        elif call.held:
            tx.execute("update core.ai_output set status_reason = %s where id = %s",
                       (("held: " + call.checked.hold)[:1000], out_id))
            status, reason = "succeeded", None
        else:
            reason = (("retried: " if retry_note else "rejected: ") + "; ".join(call.reasons()))[:1000]
            tx.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s",
                       (reason, out_id))
            status = "rejected"
        tx.execute("update ops.ai_request set status = %s, ai_output_id = %s, reject_reason = %s where custom_id = %s",
                   (status, out_id, reason, call.custom_id))

    def _publish(self, tx, w: Work, out_id: int, cites, checked, task_run_id: int) -> None:
        """A draft output's citations and its publication, superseding the target's earlier published output (one
        published output per task and target: Architecture 01 §1.4); a projection output's forecast rows with it."""
        with tx.cursor() as cur:
            cur.executemany(
                """insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id,
                                                 char_start, char_end, verified)
                   values (%s, %s, %s, 'document_span', %s, %s, %s, true)""",
                [(out_id, i + 1, path, c.document_id, c.start, c.end) for i, (path, c) in enumerate(cites)])
        cond, args = self._on_target(w)
        tx.execute(f"""update core.ai_output set status = 'superseded', status_reason = %s
                        where {cond} and scope = '' and status = 'published' and id <> %s""",
                   (f"superseded by output {out_id}", *args, out_id))
        tx.execute("update core.ai_output set status = 'published' where id = %s", (out_id,))
        if w.task == "projection_extraction":
            self._forecasts(tx, w, out_id, checked.items, task_run_id)

    def _forecasts(self, tx, w: Work, out_id: int, items: list[dict], task_run_id: int) -> None:
        """The published projection output's targets as core.forecast rows (method 'ai_extracted', the quote as the
        span, vintage = the decision date: as policy.rbi_documents writes the parsed ones). A target the regex stored
        keeps its parsed row (the check made sure the two agree); rows of an output this one superseded move to it, and
        its targets this output no longer lists are removed."""
        series = dict(tx.execute("select key, id from core.series where key = any(%s)",
                                 (list(PROJECTION_SERIES.values()),)).fetchall())
        keep = set()
        for it in items:
            sid = series[PROJECTION_SERIES[it["variable"]]]
            start, end = date.fromisoformat(it["target_start"]), date.fromisoformat(it["target_end"])
            value, low, high = (None if it[k] is None else Decimal(it[k]) for k in ("value", "low", "high"))
            keep.add((sid, start, end))
            tx.execute(
                """insert into core.forecast (series_id, vintage_date, target_start, target_end, target_label, value,
                                              value_low, value_high, method, document_id, char_start, char_end,
                                              ai_output_id, task_run_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, 'ai_extracted', %s, %s, %s, %s, %s)
                   on conflict (series_id, vintage_date, target_start, target_end) do update set
                     target_label = excluded.target_label, value = excluded.value, value_low = excluded.value_low,
                     value_high = excluded.value_high, document_id = excluded.document_id,
                     char_start = excluded.char_start, char_end = excluded.char_end,
                     ai_output_id = excluded.ai_output_id, task_run_id = excluded.task_run_id
                   where core.forecast.method = 'ai_extracted'""",
                (sid, w.doc.event_day, start, end, it["target_label"], value, low, high,
                 w.doc.document_id, it["span"][0], it["span"][1], out_id, task_run_id))
        for sid, start, end in tx.execute(
                """select series_id, target_start, target_end from core.forecast
                    where vintage_date = %s and series_id = any(%s) and method = 'ai_extracted' and document_id = %s""",
                (w.doc.event_day, list(series.values()), w.doc.document_id)).fetchall():
            if (sid, start, end) not in keep:
                tx.execute("""delete from core.forecast where series_id = %s and vintage_date = %s
                               and target_start = %s and target_end = %s and method = 'ai_extracted'""",
                           (sid, w.doc.event_day, start, end))
        tx.execute("update core.series set is_published = true where key = any(%s) and not is_published",
                   (sorted({PROJECTION_SERIES[it["variable"]] for it in items}),))

    def _recheck(self, ctx: Context, f: Fetched, p: Plan) -> None:
        """A held extraction's stored answer, checked again without a new request: a disagreement or an unreadable
        parse that has since been resolved (a fixed parser, a corrected stored figure) publishes the draft; anything
        else stays held. The answer is the one billed before, so nothing is spent."""
        db, w = ctx.fetcher.db, p.work
        out_id, content = p.held
        try:
            checked = w.recheck(content.get("answer"))
        except Exception as e:  # noqa: BLE001 - storage: the next run checks it again
            f.outcome(w, "errored" if f.mode == "realtime" else "held", f"{p.why}; not checked again: "
                      f"{type(e).__name__}: {e}")
            return
        if not checked.ok:
            f.outcome(w, "held", f"held: {checked.hold or '; '.join(checked.problems)}")
            return
        new, body, label = extraction.content(checked, attempt=content.get("attempt") or 1, reply=None)
        new.update({k: content[k] for k in ("message_id", "stop_reason") if k in content})
        new["rechecked"] = {"task_run_id": ctx.task_run_id, "was": content.get("hold")}
        with db.transaction():
            row = db.execute("select status from core.ai_output where id = %s for update", (out_id,)).fetchone()
            if row is None or row[0] != "draft":
                f.outcome(w, "done", "re-checked by another run")
                return
            db.execute("""update core.ai_output set content = %s, body = %s, label = %s, status_reason = null
                           where id = %s""", (json.dumps(new, ensure_ascii=False), body, label, out_id))
            self._publish(db, w, out_id, checked.citations, checked, ctx.task_run_id)
        f.outcome(w, "published", f"re-checked: was held ({content.get('hold')})")
        f.rechecked.append((w, out_id, checked))

    # ------------------------------------------------------------------ the Batch API (daily, refresh, backfill)
    def _batch_path(self, ctx: Context, f: Fetched, prompts, *, poll: float) -> None:
        """Collect what's ended, submit what's needed, and read the open batches until they end or `poll` seconds
        pass; a round that stored something plans again (retries, resends) and submits those too."""
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

    def _plan(self, ctx: Context, f: Fetched, prompts) -> list[tuple[Work, Step]]:
        """Every target's next step (a held extraction is checked again); the worst case of what waits in open
        batches (for the budget)."""
        db = ctx.fetcher.db
        todo: list[tuple[Work, Step]] = []
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

    def _submit(self, ctx: Context, f: Fetched, todo: list[tuple[Work, Step]], prompts) -> None:
        db = ctx.fetcher.db
        if f.stopped:
            for w, _ in todo:
                f.outcome(w, "budget", f"after {f.stopped}")
            return
        try:
            claude = self._claude()
        except CallError as e:
            f.batch.errors.append(str(e))
            for w, _ in todo:
                f.outcome(w, "errored", str(e))
            return
        items = []                                          # (work, step, request, custom_id, worst case)
        for w, step in todo:
            req = w.request(prompts[w.task], note=step.note)
            cid = self.custom_id(req, w.doc.document_id)
            try:
                claude.check(req)
            except TooLong as e:
                self._too_long(db, w, req, cid)
                f.outcome(w, "too_long", str(e))
                continue
            items.append((w, step, req, cid, self._worst_case(req, batch=True)))
        new = sum((Decimal(3 - s.attempt) * wc for w, s, _, _, wc in items if w.target not in f.batch.counted),
                  Decimal(0))                               # a retry each, at worst, counted once per target
        if f.run_estimate + new > RUN_LIMIT_USD:
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
        for chunk in _chunks(chosen):
            cids = [cid for _, _, cid, _ in chunk]
            f.batch.sent_ids |= set(cids)
            started = datetime.now(UTC)
            try:
                info = claude.create_batch([(batch_custom_id(cid), req) for _, req, cid, _ in chunk])
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
            print(f"  policy.ai_batch: batch {info.id} holds {len(chunk)} requests")
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

    def _find_created(self, db, f: Fetched, claude: Client, n: int, started: datetime,
                      err: CallError) -> BatchInfo | None:
        """After a create that failed ambiguously (`client.maybe_created`): the batch it may have made, if exactly one
        of the workspace's newest batches was created since, holds `n` requests and isn't one of ours already."""
        try:
            recent = claude.recent_batches()
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
        if self.scope is not None:          # tests: only batches holding requests for this task's meetings
            where.append("""exists (select 1 from ops.ai_request r join core.document d on d.id = r.document_id
                                     join core.event e on e.id = d.event_id
                                    where r.ai_batch_id = b.id and e.slug = any(%s))""")
            args.append(sorted(self.scope))
        return db.execute(f"""select b.id, b.provider_batch_id, b.submitted_at from ops.ai_batch b
                               where {' and '.join(where)} order by b.id""", args).fetchall()

    def _collect(self, ctx: Context, f: Fetched, prompts) -> int:
        """Read every open batch; collect each one that has ended. Returns how many requests changed state."""
        db = ctx.fetcher.db
        rows = self._open_batches(db)
        f.batch.open = []
        if not rows:
            return 0
        try:
            claude = self._claude()
        except CallError as e:
            f.batch.errors.append(str(e))
            f.batch.open = list(rows)
            return 0
        changed = 0
        index: dict[int, dict] = {}          # document id -> {custom_id: (work, request, attempt)}, this pass
        for bid, provider_id, at in rows:
            try:
                info = claude.batch(provider_id)
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

    def _collect_one(self, ctx: Context, f: Fetched, prompts, claude: Client, bid: int, at: datetime,
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

    def _entry(self, db, cid: str, doc_id: int | None, prompts, index: dict):
        """(work, request, attempt) for a stored custom_id, from its document's targets as they are now; None if
        the request no longer matches any of them (stale)."""
        if doc_id is None:
            return None
        if doc_id not in index:
            index[doc_id] = {}
            docs = self._docs(db, ids=[doc_id])
            for w in self._work(db, docs):
                for c, req, attempt in self._next_step(db, w, prompts[w.task]).built:
                    index[doc_id][c] = (w, req, attempt)
        return index[doc_id].get(cid)

    def _store_result(self, ctx: Context, f: Fetched, prompts, cid: str, doc_id: int | None, reply: Reply,
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
                    raise _Skip
                self._store(db, w, call, ctx.task_run_id, retry_note)
                db.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                           (spend.quantize(Decimal("0.0001")), ctx.task_run_id))
        except _Skip:
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
                    raise _Skip
                self._insert_bare(db, cid, reply, task_run_id, spend, reason, extra, retry_note)
        except _Skip:
            return False
        return True

    @staticmethod
    def _insert_bare(db, cid: str, reply: Reply, task_run_id: int, spend: Decimal, reason: str, extra: dict,
                     retry_note: str | None) -> None:
        task, doc_id, sha, version, event_id = db.execute(
            """select r.ai_task, r.document_id, r.input_sha256, r.prompt_version, d.event_id
                 from ops.ai_request r left join core.document d on d.id = r.document_id where r.custom_id = %s""",
            (cid,)).fetchone()
        if task == "stance_extraction" and event_id is not None:
            doc_id = None                               # the stance is the meeting's (`_target_of`)
        else:
            event_id = None
        content = {**({"bullets": []} if task in SUMMARY_TASKS else {"answer": None}), **extra,
                   "stop_reason": reply.stop_reason, "message_id": reply.message_id}
        if retry_note:
            content["retry_note"] = retry_note
        out_id = db.execute(
            """insert into core.ai_output (task, document_id, event_id, content, body, status, model, prompt_key,
                                           prompt_version, input_sha256, input_tokens, output_tokens, cost_usd,
                                           task_run_id)
               values (%s, %s, %s, %s, null, 'draft', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (task, doc_id, event_id, json.dumps(content), reply.model, task, version, sha, reply.usage.prompt_tokens,
             reply.usage.output, reply.cost_usd, task_run_id)).fetchone()[0]
        db.execute("update core.ai_output set status = 'rejected', status_reason = %s where id = %s", (reason, out_id))
        db.execute("""update ops.ai_request set status = 'rejected', ai_output_id = %s, reject_reason = %s
                       where custom_id = %s""", (out_id, reason, cid))
        db.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                   (spend.quantize(Decimal("0.0001")), task_run_id))

    # ------------------------------------------------------------------ embeddings
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
        n += f.batch.results["stale"] + f.batch.results["unstorable"]       # stored bare
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
        published = [(o.work, c.output_id, c.checked) for o in f.outcomes if o.status == "published" for c in o.calls
                     if c.ok] + f.rechecked
        for w, out_id, checked in published:
            # A later run may have superseded it already; what matters is that it was published with every claim (a
            # bullet, the stance's quote, each projection) cited, on its target, and a projection's rows stored.
            row = tx.execute(
                """select o.status, o.document_id, o.event_id, o.label,
                          jsonb_array_length(coalesce(o.content -> 'bullets', o.content -> 'items')),
                          (select count(distinct c.claim_path) from core.ai_citation c
                             join core.document d on d.id = c.document_id
                            where c.ai_output_id = o.id and c.verified and d.is_published and c.char_end <= d.char_count),
                          (select count(*) from core.forecast fc where fc.ai_output_id = o.id)
                     from core.ai_output o where o.id = %s""", (out_id,)).fetchone()
            doc_id, event_id = self._target_of(w)
            good = (row is not None and row[0] in ("published", "superseded") and row[4] and row[4] == row[5]
                    and (row[1], row[2]) == (doc_id, event_id))
            if good and w.task == "stance_extraction":
                good = row[3] in rbi_resolution.VOCABULARY
            if good and w.task == "projection_extraction" and row[0] == "published":
                stored = self._stored_projections(tx, w.doc).stored   # the regex's targets keep their parsed rows
                good = row[6] == sum((it["variable"], date.fromisoformat(it["target_start"]),
                                      date.fromisoformat(it["target_end"])) not in stored for it in checked.items)
            if not good:
                wrong.append(f"{w.target}: {row}")
        out.append(Check("outputs_stored", not wrong, "; ".join(wrong) or f"{len(published)} published, every claim "
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
            (list(AI_TASKS),)).fetchone()
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

        # (W) The extractions (Spec 01 §2: "the stance label is in the vocabulary, else the output stays a draft with
        # an alert"; the cross-check with B6's parse, Anand, 24 Oct 2026): what's held as a draft, and why.
        ext = [o for o in f.outcomes if o.work.task in EXTRACTION_TASKS]
        held = [o for o in ext if o.status == "held"]
        out.append(Check("extractions_held", not held, json.dumps({
            "published": sorted(o.work.target for o in ext if o.status == "published"),
            "held": [f"{o.work.target}: {o.reason}" for o in held],
            "projection_targets": sum(o.work.task == "projection_extraction" for o in ext)}), blocking=False))
        out.append(Check("ai_spend", f.stopped is None, json.dumps({
            "run_usd": str(f.spend), "month_usd": str(f.month_spend + f.spend), "budget_usd": str(f.budget),
            "in_batches_worst_usd": str(f.batch.exposure.quantize(Decimal("0.0001"))),
            "stopped": f.stopped, "run_estimate_usd": str(f.run_estimate.quantize(Decimal("0.0001")))}),
            blocking=False))
        if f.mode == "batch":
            b = f.batch
            now = datetime.now(UTC)
            stuck = [pid for _, pid, at in b.open if now - at > BATCH_STUCK]
            errors = list(dict.fromkeys(b.errors + list(b.read_errors.values())))   # a poll repeats its reads
            trouble = (errors or stuck or b.results["errored"] or b.results["canceled"] or b.results["expired"]
                       or b.results["unknown"] or b.results["unstorable"])
            out.append(Check("batches", not trouble, json.dumps({
                "submitted": b.submitted, "adopted": b.adopted, "collected": b.collected,
                "open": [pid for _, pid, _ in b.open], "stuck": stuck, "results": dict(sorted(b.results.items())),
                "left_for_next_run": sum(1 for o in f.outcomes if o.status == "left"),
                "held_for_watch": b.held_for_watch, "errors": errors}), blocking=False))
        out.append(Check("embeddings", f.embed_pending == 0 and f.embed_error is None, json.dumps({
            "embedded": len(f.embeds), "pending": f.embed_pending, "tokens": f.embed_tokens,
            "requests": f.embed_requests, "host": f.embed_host, "error": f.embed_error}), blocking=False))
        return out

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """From the stored state, not just this run, for each target: a request finally rejected (citations, or too
        long) raises `ai_rejected:<task>:<document>`; an extraction held as a draft (`other`: extend the vocabulary;
        a disagreement with B6's parse: Anand's call) raises `ai_held:<task>:<document>`. A published output for the
        target resolves both."""
        f = self._last
        if f is None:
            return []
        out = []
        ids = sorted({w.doc.document_id for w in f.targets})
        if ids:
            rows = db.execute(
                """select r.ai_task, r.document_id, max(r.reject_reason)
                     from ops.ai_request r
                    where r.document_id = any(%s) and r.ai_task = any(%s)
                      and r.status = 'rejected' and r.reject_reason like 'rejected:%%'
                    group by r.ai_task, r.document_id""", (ids, list(AI_TASKS))).fetchall()
            bad = {(t, d): reason for t, d, reason in rows}
            held = {(t, d): reason for t, d, reason in db.execute(
                """select r.ai_task, r.document_id, max(o.status_reason)
                     from ops.ai_request r join core.ai_output o on o.id = r.ai_output_id
                    where r.document_id = any(%s) and r.ai_task = any(%s) and r.status = 'succeeded'
                      and o.status = 'draft' and o.status_reason like 'held:%%'
                    group by r.ai_task, r.document_id""", (ids, list(EXTRACTION_TASKS))).fetchall()}
            for w in f.targets:
                cond, args = self._on_target(w)
                published = db.execute(f"select 1 from core.ai_output where {cond} and status = 'published'",
                                       args).fetchone() is not None
                kinds = (("ai_rejected", bad), ("ai_held", held)) if w.task in EXTRACTION_TASKS else \
                    (("ai_rejected", bad),)
                for kind, reasons in kinds:
                    key = f"{kind}:{w.task}:{w.doc.document_id}"
                    if published:
                        out.append(Alert(key, False))
                    elif (w.task, w.doc.document_id) in reasons:
                        out.append(Alert(key, True, "warn", f"{w.target} ({w.doc.title}): "
                                         f"{reasons[(w.task, w.doc.document_id)]}"))
        month = datetime.now(UTC).strftime("%Y-%m")
        if f.stopped == "budget":
            out.append(Alert(f"ai_budget:{month}", True, "error",
                             f"AI spend reached the ${f.budget} monthly budget; requests stopped"))
        elif f.spend > 0:                                  # a run spent within the budget: it's been raised
            out.append(Alert(f"ai_budget:{month}", False))
        out.append(Alert("ai_run_estimate", f.stopped == "run_estimate", "error",
                         f"an AI run estimated at ${f.run_estimate:.2f} (over ${RUN_LIMIT_USD}) sent nothing more"))
        return out


def _chunks(items: list, *, max_requests: int = BATCH_MAX_REQUESTS, max_bytes: int = BATCH_MAX_BYTES):
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
