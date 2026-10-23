"""The model wrapper (Architecture 03 §1.1): every AI call goes through here (CLAUDE.md §6), from the jobs, the
evaluations and, later, the live API service.

**Claude** (`Client.send`): a `Request` names its task, prompt, documents and instruction; the wrapper builds the
Messages API call and returns the reply with its usage and cost.
- Settings per task (`TASKS`): Haiku 5.5 by default; effort `low` for summaries and extraction; `max_tokens` with
  headroom for adaptive thinking, which counts towards it (8,000 for summaries, 4,000 for extraction). No
  `temperature`, `top_p` or `top_k`, and no assistant prefill.
- Two output routes (§1.2): `citations` sends the documents as plain-text document blocks with citations on;
  `json` sends them without citations and asks for a JSON schema through `output_config.format`. The API refuses a
  request that combines the two, so a request is one or the other.
- A reply that didn't end its turn (`max_tokens`, `refusal`, anything but `end_turn`) is a failure (`Failed`): its
  output may be cut off or not match the schema. It's still billed, so `Failed` carries the reply's usage and cost.
- 408, 409, 429 and 5xx answers (and lost connections) are retried up to 3 times, waiting what `Retry-After` asks
  (up to `max_wait`), else backing off with jitter. Anything else raises `CallError` at once.
- Prompt caching, in real time only: one top-level `cache_control` (5 minutes); the API caches the prefix once it
  passes the model's minimum (512 tokens on Haiku 5.5 and Sonnet 5.5). A retry's note goes in its own last block, so
  the retry, sent seconds later, reads the first attempt's cached prefix. **Batch requests carry no cache_control**
  (decided by Claude where Anand delegated, 23 Oct 2026; Architecture 03 §1.1 had them on the 1-hour cache): the cached
  prefix would hold the request's own document (the prompts alone, about 380 and 490 tokens, don't reach the 512-token
  minimum, so no prefix is shared between documents), so a 1-hour cache write, at twice the batch input price, would
  be read back only by that request's retry within the hour, and a batch's retry goes in a later batch, often a later
  run. Uncached, a batch request's input costs half as much.
- Every request stays under 100,000 input tokens (Haiku costs five times as much above that): an estimate over the
  limit is refused before sending (`TooLong`).
- Cost comes from the dated price table (config/ai_prices.yaml); a model without a price is refused before sending.
- `Request.input_sha256` hashes the model, task, prompt (version and text), settings, documents and instruction:
  ops.ai_request keys on it, so an unchanged input is never sent twice.

**Voyage** (`Embedder`): `voyage-4` for documents and `voyage-4-lite` for questions (one embedding space), 512
dimensions (stored as halfvec). The client is left to pick its endpoint from the key: an unscoped MongoDB Atlas model
API key goes to https://ai.mongodb.com/v1, a Voyage key to https://api.voyageai.com/v1 (voyageai 0.5.0's
`util.get_default_base_url`; Anand, 23 Oct 2026). Retries honour `Retry-After` the same way.

**The Batch API** (Architecture 03 §1.3; B7 part 2): `create_batch` sends many requests as one batch, each with the
same `Request.params(batch=True)` as real time but uncached, under a batch `custom_id` (`batch_custom_id`: the
API allows only `^[a-zA-Z0-9_-]{1,64}$`, so ops.ai_request's `<task>:<target>:<hash16>` is sent with `_` for `:`
and mapped back by the caller). `batch` reads its status; `batch_results` streams its results once it has ended, each
succeeded reply priced at the batch rate on the batch's creation date. A batch is created only once: `create_batch` is
retried only on 429, 503 and 529, where the API refused it, never after a lost connection, a timeout or another 5xx,
which might have created it (`maybe_created`: the caller then looks for it among `recent_batches` before its requests
may be sent again). Its timeout is 10 minutes, so a large upload isn't cut off after the API has accepted it. Reading
a batch, the list or the results is idempotent and retried like a real-time call.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config"
PRICES = CONFIG / "ai_prices.yaml"
PROMPTS = CONFIG / "prompts"

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
MAX_REQUEST_TOKENS = 100_000          # CLAUDE.md §6; Architecture 03 §1.1
CHARS_PER_TOKEN = 3.0                 # a deliberate over-estimate (English runs about 4 characters a token)
REQUEST_OVERHEAD_TOKENS = 1_500       # citations' own system additions, document framing
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
CREATE_RETRY_STATUS = frozenset({429, 503, 529})   # a batch create the API refused: safe to send again
CREATE_REFUSED = frozenset({400, 401, 403, 404, 413, 422, 429, 503, 529})   # a create these answer didn't happen
BATCH_CREATE_TIMEOUT = 600.0                        # seconds: a large upload mustn't time out after it's accepted
BATCH_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")      # the Batch API's custom_id (docs, checked 23 Oct 2026)
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class TaskSpec:
    route: str                        # 'citations' | 'json'
    effort: str                       # output_config.effort
    max_tokens: int                   # headroom for thinking plus the reply
    model: str = HAIKU


# Module 1's tasks that run (Architecture 03 §1.4; Spec 01 §3.2). The hawk/dove scorers are Pass 2 and absent.
TASKS: dict[str, TaskSpec] = {
    "doc_summary": TaskSpec("citations", "low", 8_000),
    "statement_diff_summary": TaskSpec("citations", "low", 8_000),
    "stance_extraction": TaskSpec("json", "low", 4_000),
    "projection_extraction": TaskSpec("json", "low", 4_000),
}


# ---------------------------------------------------------------------------------------------- errors
class AIError(Exception):
    """Base class: the call didn't give a usable reply."""


class TooLong(AIError):
    """The request's estimated input is over MAX_REQUEST_TOKENS; nothing was sent."""


class NoPrice(AIError):
    """The price table has no price for this model on this date; nothing was sent."""


class CallError(AIError):
    """The API refused the call, or it failed after the retries. Not billed. `kind` is the error's class (for logs and
    ops.ai_request.reject_reason), never shown to visitors."""

    def __init__(self, kind: str, status: int | None, message: str):
        super().__init__(f"{kind} ({status}): {message}" if status else f"{kind}: {message}")
        self.kind, self.status = kind, status


class Failed(AIError):
    """The reply didn't end its turn (max_tokens, refusal, ...). Billed: `reply` has the usage and cost."""

    def __init__(self, reason: str, reply: Reply):
        super().__init__(f"stop_reason {reason}")
        self.reason, self.reply = reason, reply


# ---------------------------------------------------------------------------------------------- prices
@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0

    @property
    def prompt_tokens(self) -> int:
        """Every input token, cached or not: what the long-context threshold counts."""
        return self.input + self.cache_write_5m + self.cache_write_1h + self.cache_read

    @classmethod
    def of(cls, u) -> Usage:
        """From the API's usage object (anthropic.types.Usage)."""
        cc = getattr(u, "cache_creation", None)
        w5 = getattr(cc, "ephemeral_5m_input_tokens", None) if cc is not None else None
        w1 = getattr(cc, "ephemeral_1h_input_tokens", None) if cc is not None else None
        writes = getattr(u, "cache_creation_input_tokens", None) or 0
        if w5 is None and w1 is None:          # no breakdown: count every write at the 5-minute rate
            w5, w1 = writes, 0
        return cls(u.input_tokens or 0, u.output_tokens or 0, w5 or 0, w1 or 0, getattr(u, "cache_read_input_tokens", 0) or 0)


@cache
def _price_table(path: Path = PRICES) -> dict:
    return yaml.safe_load(path.read_text())


def price_entry(model: str, on: date, *, path: Path = PRICES) -> tuple[dict, Decimal, int | None]:
    """(the model's prices, the batch factor, the long-context threshold) in force on `on`. NoPrice if none."""
    entries = sorted((e for e in _price_table(path)["anthropic"] if e["from"] <= on), key=lambda e: e["from"])
    for e in reversed(entries):
        if model in e["models"]:
            m = e["models"][model]
            return m, Decimal(str(e.get("batch", 1))), m.get("long_context_over")
    raise NoPrice(f"no price for {model} on {on} in config/ai_prices.yaml")


def cost_usd(model: str, usage: Usage, *, batch: bool = False, on: date | None = None, path: Path = PRICES) -> Decimal:
    """The call's cost from the dated price table, rounded to the micro-dollar (core.ai_output.cost_usd)."""
    m, batch_factor, over = price_entry(model, on or datetime.now(UTC).date(), path=path)
    tier = m["standard"]
    if over is not None and usage.prompt_tokens > over:
        if "long" not in m:
            raise NoPrice(f"no long-context price for {model}")
        tier = m["long"]
    p = {k: Decimal(str(v)) for k, v in tier.items()}
    total = (usage.input * p["input"] + usage.output * p["output"] + usage.cache_write_5m * p["cache_write_5m"]
             + usage.cache_write_1h * p["cache_write_1h"] + usage.cache_read * p["cache_read"]) / MILLION
    if batch:
        total *= batch_factor
    return total.quantize(Decimal("0.000001"))


# ---------------------------------------------------------------------------------------------- prompts and requests
@dataclass(frozen=True)
class Prompt:
    task: str
    version: str                      # 'v1': core.ai_output.prompt_version
    text: str

    @property
    def key(self) -> str:             # core.ai_output.prompt_key
        return self.task

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def load_prompt(task: str, version: str = "v1", *, root: Path = PROMPTS) -> Prompt:
    """config/prompts/<task>/<version>.md: the system prompt. Lines starting with '<!--' are notes, not sent."""
    raw = (root / task / f"{version}.md").read_text(encoding="utf-8")
    text = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("<!--")).strip() + "\n"
    return Prompt(task, version, text)


def load_schema(task: str, *, root: Path = PROMPTS) -> dict:
    """config/prompts/<task>/schema.json: the json route's JSON schema (structured outputs: no length or number
    limits, so the task's checks hold those). It's part of every request's input hash."""
    return json.loads((root / task / "schema.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Source:
    """A document (or a segment of one) sent with a request. Citations map back to the document through seg_start."""
    document_id: int
    text: str                         # what is sent: the canonical text, or the segment [seg_start, seg_start+len)
    title: str                        # shown to the model; never cited
    seg_start: int = 0
    context: str | None = None        # shown to the model; never cited

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Request:
    task: str
    prompt: Prompt
    sources: tuple[Source, ...]
    instruction: str                  # the user turn's text, after the documents
    model: str | None = None          # default: the task's (Haiku 5.5)
    schema: dict | None = None        # the json route's JSON schema
    note: str | None = None           # a retry's note: its own last block, so the prefix before it stays cached

    def __post_init__(self):
        if self.task not in TASKS:
            raise ValueError(f"{self.task} isn't a task the wrapper runs")
        if self.prompt.task != self.task:
            raise ValueError(f"prompt for {self.prompt.task}, request for {self.task}")
        if (self.spec.route == "json") != (self.schema is not None):
            raise ValueError(f"{self.task}: the json route needs a schema, the citations route none")

    @property
    def spec(self) -> TaskSpec:
        return TASKS[self.task]

    @property
    def model_id(self) -> str:
        return self.model or self.spec.model

    def _canonical(self) -> dict:
        return {"model": self.model_id, "task": self.task, "prompt_version": self.prompt.version,
                "prompt_sha256": self.prompt.sha256, "route": self.spec.route, "effort": self.spec.effort,
                "max_tokens": self.spec.max_tokens, "instruction": self.instruction, "note": self.note,
                "schema": self.schema,
                "sources": [{"document_id": s.document_id, "seg_start": s.seg_start, "title": s.title,
                             "context": s.context, "text_sha256": s.text_sha256} for s in self.sources]}

    @property
    def input_sha256(self) -> bytes:
        """What ops.ai_request and core.ai_output store: an unchanged input is never sent twice."""
        return hashlib.sha256(json.dumps(self._canonical(), sort_keys=True, ensure_ascii=False).encode()).digest()

    def estimated_tokens(self) -> int:
        chars = len(self.prompt.text) + len(self.instruction) + len(self.note or "") + sum(
            len(s.text) + len(s.title) + len(s.context or "") for s in self.sources)
        if self.schema is not None:
            chars += len(json.dumps(self.schema))
        return math.ceil(chars / CHARS_PER_TOKEN) + REQUEST_OVERHEAD_TOKENS

    def params(self, *, batch: bool = False) -> dict:
        """The Messages API parameters: real time, or a batch request's `params`, which are the same but uncached
        (module docstring, "Prompt caching")."""
        cite = self.spec.route == "citations"
        content: list[dict] = []
        for s in self.sources:
            block = {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": s.text},
                     "title": s.title}
            if s.context:
                block["context"] = s.context
            if cite:
                block["citations"] = {"enabled": True}
            content.append(block)
        content.append({"type": "text", "text": self.instruction})
        if self.note:
            content.append({"type": "text", "text": self.note})
        output_config: dict = {"effort": self.spec.effort}
        if not cite:
            output_config["format"] = {"type": "json_schema", "schema": self.schema}
        params = {"model": self.model_id, "max_tokens": self.spec.max_tokens, "system": self.prompt.text,
                  "messages": [{"role": "user", "content": content}], "output_config": output_config}
        if not batch:
            params["cache_control"] = {"type": "ephemeral", "ttl": "5m"}
        return params


# ---------------------------------------------------------------------------------------------- replies
@dataclass(frozen=True)
class ApiCitation:
    """A native citation as the API returned it (char_location for plain-text documents)."""
    kind: str                         # the API's type: 'char_location' (others aren't usable evidence here)
    document_index: int | None
    start: int | None
    end: int | None
    cited_text: str


@dataclass(frozen=True)
class TextPart:
    text: str
    citations: tuple[ApiCitation, ...]


@dataclass
class Reply:
    model: str
    message_id: str
    stop_reason: str | None
    usage: Usage
    cost_usd: Decimal
    content: list = field(repr=False)          # the API's content blocks

    def parts(self) -> list[TextPart]:
        """The reply's text blocks in order, each with its citations (thinking blocks are skipped)."""
        out = []
        for b in self.content:
            if getattr(b, "type", None) != "text":
                continue
            cites = []
            for c in getattr(b, "citations", None) or []:
                cites.append(ApiCitation(getattr(c, "type", ""), getattr(c, "document_index", None),
                                         getattr(c, "start_char_index", None), getattr(c, "end_char_index", None),
                                         getattr(c, "cited_text", "") or ""))
            out.append(TextPart(b.text, tuple(cites)))
        return out

    def text(self) -> str:
        return "".join(p.text for p in self.parts())

    def json(self) -> dict:
        """The json route's output (structured outputs return it as the reply's text)."""
        return json.loads(self.text())


def batch_custom_id(custom_id: str) -> str:
    """ops.ai_request's custom_id as the Batch API accepts it: every character outside [A-Za-z0-9_-] becomes '_'.
    `doc_summary:doc-123:0123456789abcdef` → `doc_summary_doc-123_0123456789abcdef`."""
    out = re.sub(r"[^A-Za-z0-9_-]", "_", custom_id)[:64]
    if not out:
        raise ValueError("an empty custom_id")
    return out


def maybe_created(e: CallError) -> bool:
    """Whether a batch create that raised `e` might still have created the batch: a lost connection or timeout, or a
    5xx other than the refusals (503, 529), or 408/409. Then the caller looks for it (`Client.recent_batches`) before
    letting its requests be sent again."""
    if e.status is None:
        return e.kind not in ("RetryAfterTooLong", "NoKey")
    return e.status not in CREATE_REFUSED and (e.status >= 500 or e.status in (408, 409))


@dataclass(frozen=True)
class BatchInfo:
    """A batch as the API reports it."""
    id: str                           # msgbatch_…: ops.ai_batch.provider_batch_id
    status: str                       # in_progress | canceling | ended
    created_at: datetime
    ended_at: datetime | None
    counts: dict[str, int]            # processing, succeeded, errored, canceled, expired
    archived_at: datetime | None = None   # set once its results can no longer be downloaded

    @classmethod
    def of(cls, b) -> BatchInfo:
        c = b.request_counts
        return cls(b.id, str(b.processing_status), b.created_at, b.ended_at,
                   {k: int(getattr(c, k, 0) or 0) for k in ("processing", "succeeded", "errored", "canceled", "expired")},
                   getattr(b, "archived_at", None))

    @property
    def ended(self) -> bool:
        return self.status == "ended"


@dataclass(frozen=True)
class BatchResult:
    """One request's result in an ended batch."""
    custom_id: str                    # the batch custom_id it was sent with
    kind: str                         # succeeded | errored | canceled | expired (only succeeded is billed)
    reply: Reply | None = None        # succeeded: the reply, whatever its stop reason
    error: str | None = None          # errored: the error's type ('invalid_request_error', …)


def _retry_after(headers) -> float | None:
    if headers is None:
        return None
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        v = headers.get(name)
        if v is not None:
            try:
                return max(0.0, float(v) / scale)
            except ValueError:
                pass
    return None


def _backoff(attempt: int) -> float:
    return min(30.0, 2.0 * 2 ** attempt) * (0.5 + random.random() / 2)


class Client:
    """Claude through the Anthropic SDK (its own retries off: this wrapper retries, honouring Retry-After)."""

    def __init__(self, api_key: str | None = None, *, anthropic_client=None, sleep: Callable[[float], None] = time.sleep,
                 retries: int = 3, max_wait: float = 120.0, timeout: float = 300.0,
                 today: Callable[[], date] = lambda: datetime.now(UTC).date(), prices: Path = PRICES):
        if anthropic_client is None:
            import anthropic
            anthropic_client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=timeout)
        self._api, self.sleep, self.retries, self.max_wait = anthropic_client, sleep, retries, max_wait
        self.today, self.prices = today, prices
        self.calls = 0                              # requests sent, retries included

    def check(self, req: Request, *, batch: bool = False) -> None:
        """What's refused before sending: no price for the model, or an input over the token limit."""
        price_entry(req.model_id, self.today(), path=self.prices)
        if (n := req.estimated_tokens()) > MAX_REQUEST_TOKENS:
            raise TooLong(f"{req.task}: about {n:,} input tokens, over the {MAX_REQUEST_TOKENS:,} limit")

    def _call(self, fn: Callable[[], object], *, retry_status: frozenset[int] = RETRY_STATUS,
              retry_lost: bool = True):
        """Call the API, retrying `retry_status` answers (and lost connections, if `retry_lost`) up to `retries` times,
        waiting what Retry-After asks (up to max_wait), else backing off with jitter. CallError otherwise."""
        import anthropic
        attempt = 0
        while True:
            try:
                self.calls += 1
                return fn()
            except anthropic.APIStatusError as e:
                status = e.status_code
                if status not in retry_status or attempt >= self.retries:
                    raise CallError(type(e).__name__, status, str(getattr(e, "message", e))) from None
                wait = _retry_after(getattr(e.response, "headers", None))
            except anthropic.APIConnectionError as e:   # includes timeouts
                if not retry_lost or attempt >= self.retries:
                    raise CallError(type(e).__name__, None, str(e)) from None
                wait = None
            wait = _backoff(attempt) if wait is None else wait
            if wait > self.max_wait:
                raise CallError("RetryAfterTooLong", None, f"the API asked to wait {wait:.0f} s")
            self.sleep(wait)
            attempt += 1

    def send(self, req: Request) -> Reply:
        """One real-time call. Returns the reply (stop_reason end_turn); Failed if it stopped otherwise (billed);
        CallError if the API refused it or the retries ran out (not billed); TooLong / NoPrice before sending."""
        self.check(req)
        params = req.params()
        msg = self._call(lambda: self._api.messages.create(**params))
        usage = Usage.of(msg.usage)
        reply = Reply(msg.model or req.model_id, msg.id, msg.stop_reason, usage,
                      cost_usd(req.model_id, usage, on=self.today(), path=self.prices), list(msg.content))
        if msg.stop_reason != "end_turn":
            raise Failed(str(msg.stop_reason), reply)
        return reply

    # ------------------------------------------------------------------ the Batch API (Architecture 03 §1.3)
    def create_batch(self, items: Sequence[tuple[str, Request]]) -> BatchInfo:
        """Send `items` ((batch custom_id, request)) as one batch. Each request is checked first (price, token limit:
        nothing is sent if one fails). Retried only where the API refused the batch (module docstring)."""
        if not items:
            raise ValueError("an empty batch")
        seen: set[str] = set()
        requests = []
        for cid, req in items:
            if not BATCH_ID.fullmatch(cid) or cid in seen:
                raise ValueError(f"batch custom_id {cid!r} isn't valid or isn't unique in the batch")
            seen.add(cid)
            self.check(req)
            requests.append({"custom_id": cid, "params": req.params(batch=True)})
        b = self._call(lambda: self._api.messages.batches.create(requests=requests, timeout=BATCH_CREATE_TIMEOUT),
                       retry_status=CREATE_RETRY_STATUS, retry_lost=False)
        return BatchInfo.of(b)

    def recent_batches(self, *, limit: int = 20) -> list[BatchInfo]:
        """The workspace's most recently created batches, newest first (one page): to find a batch whose create
        failed ambiguously (`maybe_created`)."""
        page = self._call(lambda: self._api.messages.batches.list(limit=limit))
        return [BatchInfo.of(b) for b in page.data]

    def batch(self, batch_id: str) -> BatchInfo:
        """A batch's status (idempotent)."""
        return BatchInfo.of(self._call(lambda: self._api.messages.batches.retrieve(batch_id)))

    def batch_results(self, info: BatchInfo, model_of: Callable[[str], str | None] = lambda cid: None
                      ) -> Iterator[BatchResult]:
        """An ended batch's results, streamed (in any order: match them by custom_id). A succeeded reply is returned
        whatever its stop reason (the caller treats anything but end_turn as a billed failure), priced at the batch
        rate for `model_of(custom_id)` (the model the request named; else the reply's) on the batch's creation date.
        A stream that breaks off raises CallError: what was read before it stands."""
        import anthropic
        import httpx2
        on = info.created_at.astimezone(UTC).date()
        try:
            stream = self._call(lambda: self._api.messages.batches.results(info.id))
            for line in stream:
                r = line.result
                if r.type == "succeeded":
                    msg = r.message
                    model = model_of(line.custom_id) or msg.model
                    usage = Usage.of(msg.usage)
                    yield BatchResult(line.custom_id, "succeeded", Reply(
                        msg.model or model, msg.id, msg.stop_reason, usage,
                        cost_usd(model, usage, batch=True, on=on, path=self.prices), list(msg.content)))
                elif r.type == "errored":
                    err = getattr(getattr(r, "error", None), "error", None)
                    yield BatchResult(line.custom_id, "errored", error=str(getattr(err, "type", None) or "error"))
                else:                                   # canceled, expired: not billed
                    yield BatchResult(line.custom_id, str(r.type))
        except (anthropic.AnthropicError, httpx2.HTTPError, ValueError) as e:
            # (A CallError from _call, a 404 say, passes through as it is.) The SDK raises its base AnthropicError
            # when the batch has no results_url.
            raise CallError("ResultsStream", None, f"{type(e).__name__}: {e}") from None


# ---------------------------------------------------------------------------------------------- embeddings
DOC_MODEL, QUERY_MODEL, DIMENSION = "voyage-4", "voyage-4-lite", 512
EMBED_MAX_TEXTS = 128
EMBED_MAX_TOKENS = 100_000            # per request, estimated (voyage-4 allows far more; this keeps requests small)


@dataclass(frozen=True)
class Embedded:
    vectors: list[list[float]]
    tokens: int
    model: str
    requests: int


class Embedder:
    """Voyage embeddings: documents with voyage-4, questions with voyage-4-lite, 512 dimensions. `client` is a
    voyageai.Client (or a test double with the same `embed`)."""

    def __init__(self, api_key: str | None = None, *, client=None, sleep: Callable[[float], None] = time.sleep,
                 retries: int = 3, max_wait: float = 120.0):
        if client is None:
            import voyageai
            client = voyageai.Client(api_key=api_key, max_retries=0)   # base_url left to the key (module docstring)
        self._vo, self.sleep, self.retries, self.max_wait = client, sleep, retries, max_wait

    @property
    def host(self) -> str | None:
        """The endpoint's host the client picked (for the run log; never the key)."""
        base = (getattr(self._vo, "_params", None) or {}).get("base_url")
        return urlsplit(base).hostname if base else None

    def _embed(self, texts: list[str], model: str, input_type: str):
        from voyageai import error as verr
        attempt = 0
        while True:
            try:
                return self._vo.embed(texts, model=model, input_type=input_type, truncation=False,
                                      output_dtype="float", output_dimension=DIMENSION)
            except (verr.RateLimitError, verr.ServiceUnavailableError, verr.ServerError, verr.Timeout,
                    verr.APIConnectionError, verr.TryAgain) as e:
                if attempt >= self.retries:
                    raise CallError(type(e).__name__, getattr(e, "http_status", None), str(e)) from None
                wait = _retry_after(getattr(e, "headers", None) or {})
            except verr.APIError as e:
                status = getattr(e, "http_status", None)
                if status not in RETRY_STATUS or attempt >= self.retries:
                    raise CallError(type(e).__name__, status, str(e)) from None
                wait = _retry_after(getattr(e, "headers", None) or {})
            except verr.VoyageError as e:
                raise CallError(type(e).__name__, getattr(e, "http_status", None), str(e)) from None
            wait = _backoff(attempt) if wait is None else wait
            if wait > self.max_wait:
                raise CallError("RetryAfterTooLong", None, f"Voyage asked to wait {wait:.0f} s")
            self.sleep(wait)
            attempt += 1

    def embed_documents(self, texts: Sequence[str]) -> Embedded:
        """Embed document texts (each a chunk with its context line) in small requests; vectors in input order."""
        vectors: list[list[float]] = []
        tokens = requests = 0
        batch: list[str] = []
        size = 0

        def flush():
            nonlocal tokens, requests, size
            if not batch:
                return
            r = self._embed(list(batch), DOC_MODEL, "document")
            if len(r.embeddings) != len(batch) or any(len(v) != DIMENSION for v in r.embeddings):
                raise CallError("BadEmbeddings", None, f"asked for {len(batch)} vectors of {DIMENSION}, got "
                                f"{len(r.embeddings)}")
            vectors.extend(r.embeddings)
            tokens += r.total_tokens
            requests += 1
            batch.clear()
            size = 0

        for t in texts:
            n = math.ceil(len(t) / CHARS_PER_TOKEN)
            if batch and (len(batch) >= EMBED_MAX_TEXTS or size + n > EMBED_MAX_TOKENS):
                flush()
            batch.append(t)
            size += n
        flush()
        return Embedded(vectors, tokens, DOC_MODEL, requests)

    def embed_query(self, text: str) -> list[float]:
        """A question, with voyage-4-lite (the command bar's retrieval; Architecture 03 §1.5)."""
        r = self._embed([text], QUERY_MODEL, "query")
        return r.embeddings[0]


def halfvec_literal(v: Sequence[float]) -> str:
    """A vector as pgvector's text form, for an explicit `::extensions.halfvec(512)` cast."""
    if len(v) != DIMENSION:
        raise ValueError(f"expected {DIMENSION} dimensions, got {len(v)}")
    return "[" + ",".join(repr(float(x)) for x in v) + "]"
