"""Tone of an earnings call (Spec 02B §5.7, §5.8; Data Audit 05 D8; job 2B-12, #175): the request for one stored call, the checks on
the reply, the aggregation of its passage scores into a call tone and the change against the previous call. The task that sends
the request, `equity.calls_ai` (only when `CALLS_AI_TASK=call_tone`), is in ft/jobs/tasks/equity_calls_ai.py; the task that
aggregates, `equity.call_scores`, is in ft/jobs/tasks/equity_call_scores.py.

**What is scored.** Each *management passage*: a chunk of the transcript (`core.chunk`) that lies wholly inside a management turn
and holds at least `MIN_WORDS` (40) words. Analysts and the moderator are never scored (D8), and a call whose roles are held
(`unknown_role_share` over `turns.ROLE_HOLD_SHARE`, Spec 02B.1 option C) isn't requested at all. One request per call, the
passages as a numbered list with their section and speaker, and nothing else.

**The reply** (JSON route, `config/prompts/call_tone/schema.json`): `{scores: [{p, score}], evidence: [{p, quote}]}`.
- Every passage appears exactly once and each score is a whole number from -2 to +2 (a schema can't set number limits, so the
  range is checked here). A reply that fails either is a failed reply: retried once with a note, then rejected, as every
  extraction in `ft/ai/batch.py`.
- Evidence: at most `MAX_EVIDENCE` (3) quotes, each at most `MAX_QUOTE` (300) characters, found exactly once inside its own
  passage, and only from a passage scored +-1 or +-2. An item that fails is dropped and counted by reason; the scores stand.
  The kept quotes are the output's citations (kind `document_span`, verified).
- **A call with no kept evidence** (every passage scored 0, or every quote dropped) cites its first passage's span, so the
  output can still be published: the database refuses to publish an output with no verified citation, and a neutral call is a
  real result. Decided by Claude under the standing delegation, 8 Nov 2026; alternative: leave such calls without a tone,
  which would drop exactly the calls that read most neutral and bias the aggregate positive or negative.

**The output's content** holds each passage's score with its section, speaker and word count, the kept evidence (quotes at most
300 characters, with spans and citation numbers), the drops counted by reason, and no other transcript text.

**Aggregation** (`aggregate`, run by `equity.call_scores`): the call tone is the mean of the passage scores with n and the sample
standard deviation, **held when n < `MIN_PASSAGES` (8)**. The opening's mean and n use the `opening` section's passages, the
answers' the `qa` section's; the closing counts in the call's mean only. The change against the previous call (the immediately
preceding fiscal quarter's) is delta = this mean - the previous mean, SE = sqrt(sd1^2 / n1 + sd2^2 / n2): **more positive** if
delta > 2 SE, **more negative** if delta < -2 SE, otherwise **little changed**; no label when the previous call is missing or held.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from ft.ai.call_summary import CallDoc, Turn, _when
from ft.ai.client import Prompt, Reply, Request, Source

TASK = "call_tone"
RULE_VERSION = "1"
MIN_WORDS = 40                        # a passage: a chunk inside a management turn with at least this many words
MIN_PASSAGES = 8                      # a call with fewer scored passages is held (Spec 02B §5.7)
MAX_EVIDENCE = 3
MAX_QUOTE = 300                       # characters (the spec's cap; the private-text cap is 400)
SCORES = (-2, -1, 0, 1, 2)
SECTIONS = ("opening", "qa", "closing")
SIGMAS = 2                            # the change is called when |delta| exceeds this many standard errors
DROPS: dict[str, str] = {
    "malformed": "evidence item isn't the schema's object",
    "unknown_passage": "evidence names a passage that isn't in the list",
    "zero_passage": "evidence from a passage scored 0",
    "too_long": f"quote over {MAX_QUOTE} characters",
    "not_found": "quote not found exactly once inside its passage",
    "over_limit": f"more than {MAX_EVIDENCE} evidence quotes",
}


# ---------------------------------------------------------------------------------------------- the passages
@dataclass(frozen=True)
class Chunk:
    """A stored chunk's place in the transcript (`core.chunk`): its number and code-point span."""
    chunk_no: int
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Passage:
    p: int                            # 1-based position in the list the model sees
    chunk_no: int
    char_start: int
    char_end: int
    section: str                      # opening | qa | closing
    speaker: str
    text: str
    words: int


def passages(call: CallDoc, chunks: Sequence[Chunk]) -> list[Passage]:
    """The call's management passages in document order: a chunk wholly inside one management turn, of at least MIN_WORDS words."""
    mgmt = sorted((t for t in call.turns if t.role == "management"), key=lambda t: t.char_start)
    out: list[Passage] = []
    for c in sorted(chunks, key=lambda c: c.char_start):
        text = call.text[c.char_start:c.char_end].rstrip()          # the whitespace between two blocks belongs to the chunk before
        end = c.char_start + len(text)
        turn: Turn | None = next((t for t in mgmt if t.char_start <= c.char_start and end <= t.char_end), None)
        if turn is None or turn.section not in SECTIONS:
            continue
        n = len(text.split())
        if n < MIN_WORDS:
            continue
        out.append(Passage(len(out) + 1, c.chunk_no, c.char_start, end, turn.section, turn.speaker or "", text, n))
    return out


SECTION_LABEL = {"opening": "opening remarks", "qa": "answer", "closing": "closing remarks"}


def listing(items: Sequence[Passage]) -> str:
    """The numbered list the model sees: each passage with its section and speaker, nothing else."""
    parts = []
    for ps in items:
        who = f", {ps.speaker}" if ps.speaker else ""
        parts.append(f"[{ps.p}] ({SECTION_LABEL[ps.section]}{who})\n{ps.text}")
    return "\n\n".join(parts)


def tone_request(call: CallDoc, items: Sequence[Passage], prompt: Prompt, schema: dict, *, note: str | None = None) -> Request:
    if not items:
        raise ValueError(f"{call.title}: no management passage to score")
    quarter = f" · {call.fiscal_label}" if call.fiscal_label else ""
    src = Source(call.document_id, listing(items), call.title, context=f"{call.company} · Earnings call{quarter} · {_when(call.day)}")
    instruction = (f"Score each of the {len(items)} numbered management passages above ({call.title}), following the rubric. "
                   f"Give every passage exactly one score, then the evidence quotes.")
    return Request(TASK, prompt, (src,), instruction, schema=schema, note=note if note else None)


# ---------------------------------------------------------------------------------------------- checking a reply
@dataclass
class Checked:
    answer: dict | None
    scores: list[dict] = field(default_factory=list)          # one per passage, in order: p, score, chunk_no, section, speaker, words
    evidence: list[dict] = field(default_factory=list)        # kept: p, quote, span, citation_no
    citations: list[tuple[str, tuple[int, int]]] = field(default_factory=list)   # (claim_path, (start, end)) in citation order
    drops: Counter = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)
    fallback_citation: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems

    def reasons(self) -> list[str]:
        return list(self.problems)

    def note(self) -> str:
        """The retry's note about this reply (what the batch steps send back on the second attempt)."""
        return "; ".join(self.problems) + "."


def _whole(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def check(answer, items: Sequence[Passage]) -> Checked:
    """Check a reply against the passages sent. A reply that breaks the score rules has `problems` (retry, then reject);
    an evidence item that fails is dropped and counted."""
    c = Checked(answer if isinstance(answer, dict) else None)
    if (not isinstance(answer, dict) or not isinstance(answer.get("scores"), list)
            or not isinstance(answer.get("evidence"), list)):
        c.problems.append("the reply isn't the schema's JSON ({scores: [...], evidence: [...]})")
        return c
    by_p = {ps.p: ps for ps in items}
    seen: dict[int, int] = {}
    for raw in answer["scores"]:
        p = _whole(raw.get("p")) if isinstance(raw, dict) else None
        s = _whole(raw.get("score")) if isinstance(raw, dict) else None
        if p is None or p not in by_p:
            c.problems.append("a score names a passage that isn't in the list")
        elif s is None or s not in SCORES:
            c.problems.append(f"passage {p}: the score isn't a whole number from -2 to +2")
        elif p in seen:
            c.problems.append(f"passage {p} is scored more than once")
        else:
            seen[p] = s
    missing = sorted(set(by_p) - set(seen))
    if missing:
        c.problems.append(f"{len(missing)} of {len(by_p)} passages have no score")
    if c.problems:
        c.problems = sorted(set(c.problems))[:6]
        return c
    c.scores = [{"p": ps.p, "score": seen[ps.p], "chunk_no": ps.chunk_no, "section": ps.section, "speaker": ps.speaker,
                 "words": ps.words} for ps in items]
    for raw in answer["evidence"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("quote"), str):
            c.drops["malformed"] += 1
            continue
        p, quote = _whole(raw.get("p")), raw["quote"]
        if p is None or p not in by_p:
            c.drops["unknown_passage"] += 1
        elif seen[p] == 0:
            c.drops["zero_passage"] += 1
        elif len(quote) > MAX_QUOTE:
            c.drops["too_long"] += 1
        elif not quote or by_p[p].text.count(quote) != 1:
            c.drops["not_found"] += 1
        elif len(c.evidence) >= MAX_EVIDENCE:
            c.drops["over_limit"] += 1
        else:
            a = by_p[p].char_start + by_p[p].text.index(quote)
            c.evidence.append({"p": p, "quote": quote, "span": [a, a + len(quote)]})
    for i, e in enumerate(c.evidence):
        e["citation_no"] = i + 1
        c.citations.append((f"/evidence/{i}", (e["span"][0], e["span"][1])))
    if not c.evidence:
        first = items[0]
        c.fallback_citation = True
        c.citations.append(("/scores/0", (first.char_start, first.char_end)))
    return c


def answer_of(reply: Reply):
    try:
        return reply.json()
    except (ValueError, TypeError):
        return None


def content(c: Checked, *, attempt: int, reply: Reply | None) -> dict:
    """core.ai_output.content: the scores (no passage text), the kept evidence (quotes at most 300 characters), the drops."""
    doc: dict = {"scores": c.scores, "evidence": c.evidence, "dropped": dict(sorted(c.drops.items())), "attempt": attempt,
                 "stop_reason": reply.stop_reason if reply is not None else None, "rule_version": RULE_VERSION,
                 "min_words": MIN_WORDS, "fallback_citation": c.fallback_citation}
    if c.problems:
        doc["problems"] = list(c.problems)
    return doc


# ---------------------------------------------------------------------------------------------- aggregation
@dataclass(frozen=True)
class Tone:
    n: int
    mean: Decimal | None
    sd: Decimal | None
    opening_n: int
    opening_mean: Decimal | None
    answers_n: int
    answers_mean: Decimal | None
    held_reason: str | None

    @property
    def held(self) -> bool:
        return self.held_reason is not None


@dataclass(frozen=True)
class Change:
    prev_event_id: int | None
    delta: Decimal | None
    se_delta: Decimal | None
    label: str | None


def _q(x: float) -> Decimal:
    return Decimal(repr(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def _mean(xs: Sequence[int]) -> float:
    return sum(xs) / len(xs)


def _sd(xs: Sequence[int]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def aggregate(scores: Sequence[dict]) -> Tone:
    """A call's tone from its stored passage scores (`content['scores']`)."""
    vals = [int(s["score"]) for s in scores]
    opening = [int(s["score"]) for s in scores if s["section"] == "opening"]
    answers = [int(s["score"]) for s in scores if s["section"] == "qa"]
    om = _q(_mean(opening)) if opening else None
    am = _q(_mean(answers)) if answers else None
    if len(vals) < MIN_PASSAGES:
        return Tone(len(vals), None, None, len(opening), om, len(answers), am, "too little management text to score")
    return Tone(len(vals), _q(_mean(vals)), _q(_sd(vals)), len(opening), om, len(answers), am, None)


def change(now: Tone, prev: Tone | None, prev_event_id: int | None) -> Change:
    """The change against the previous call. No label (and no delta) when either call is held or there is no previous call."""
    if prev is None or prev.held or now.held:
        return Change(prev_event_id, None, None, None)
    delta = _q(float(now.mean) - float(prev.mean))                 # decided on the stored (rounded) figures, so a check can repeat it
    se = _q(math.sqrt(float(now.sd) ** 2 / now.n + float(prev.sd) ** 2 / prev.n))
    if delta > SIGMAS * se:
        label = "more_positive"
    elif delta < -SIGMAS * se:
        label = "more_negative"
    else:
        label = "little_changed"
    return Change(prev_event_id, delta, se, label)
