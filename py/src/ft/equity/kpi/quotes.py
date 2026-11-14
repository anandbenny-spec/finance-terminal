""""Reason stated" and "An analyst asked" for each KPI-pilot flag (DD-05 part 2, #513; Plan 05 §1.3, D8; Spec 02B §3.2,
§5.3). Pure: the stored transcript of the flag's quarter (its canonical text, turns and page map) and the metric in; the
candidate sentences, the Haiku request that picks one, and the verified quote out. The database and the network are the
task's.

- **Candidates:** sentences (`sentence_spans`) in management turns (for "reason stated") and analyst turns (for "an
  analyst asked") that hold one of the metric's `terms` (a word starting with it); for a breakdown member, also one of
  its `member_terms` (the whole word). Each is cut at a word to at most `MAX_QUOTE` (400) code points, and at most
  `MAX_CANDIDATES` a role are offered, those naming the most of the metric's words first, then in transcript order.
- **The pick:** Haiku 5.5 (`kpi_quote`, structured output) names at most one management candidate that says the company
  stopped reporting the metric, changed how it reports it, or why it isn't in the release, and at most one analyst
  candidate that asks about the metric. It picks by id: it writes no text.
- **The quote:** the picked span is re-read from the stored text by its code-point offsets (`remarks.verify_side`, #414's
  rule), at most 400 code points, with the PDF page it starts on. A pick that doesn't verify, or has no page, is "no".
- **The wording (D8):** "Reason stated: yes" with its quote, or "No reason found in the release or the call"; "An analyst
  asked: yes" with its quote, or "An analyst asked: no". "Found", never "given", and never a motive.

A turn whose role is `unknown` or `moderator` offers no candidate (who said it can't be shown), so a role-held call is
searched in its known turns only. Nothing here is shown but a verified quote (CLAUDE.md §1, transcript privacy).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ft.ai.client import Prompt, Request, Source
from ft.engines.document.text import sentence_spans
from ft.equity.calls import remarks as rm

TASK = "kpi_quote"
MAX_QUOTE = rm.MAX_SIDE         # 400 code points (CLAUDE.md §1: verified quotes of at most 400 characters)
MAX_CANDIDATES = 30             # a role, a flag
REASON_YES = "Reason stated: yes"
REASON_NONE = "No reason found in the release or the call"
ASKED_YES = "An analyst asked: yes"
ASKED_NO = "An analyst asked: no"
ROLES = {"management": "M", "analyst": "A"}


@dataclass(frozen=True)
class Turn:
    char_start: int
    char_end: int
    role: str


@dataclass(frozen=True)
class Candidate:
    id: str                         # 'M3', 'A1'
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Quote:
    document_id: int
    start: int
    end: int
    text: str
    page: int


def wording(reason: Quote | None, asked: Quote | None) -> tuple[str, str]:
    """The two lines D8 fixes, for a flag's reason and analyst quotes."""
    return (REASON_YES if reason else REASON_NONE, ASKED_YES if asked else ASKED_NO)


def _term(t: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(t.lower()))


def _word(t: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])")


def _cut(text: str, start: int, end: int) -> int:
    """The end of [start, end) cut at a word so the span is at most MAX_QUOTE code points."""
    if end - start <= MAX_QUOTE:
        return end
    cut = max(text.rfind(" ", start, start + MAX_QUOTE), text.rfind("\n", start, start + MAX_QUOTE))
    return cut if cut > start else start + MAX_QUOTE


def candidates(text: str, turns: Sequence[Turn], role: str, terms: Sequence[str], member_terms: Sequence[str] = ()
               ) -> list[Candidate]:
    """The candidate sentences of one role (module doc). Empty when the metric has no terms."""
    pats = [_term(t) for t in terms if t]
    members = [_word(m) for m in member_terms if m]
    if not pats:
        return []
    found: list[tuple[int, int, int]] = []          # (score, start, end)
    for t in turns:
        if t.role != role:
            continue
        for a, b in sentence_spans(text, t.char_start, t.char_end):
            low = text[a:b].lower()
            hit = sum(1 for p in pats if p.search(low))
            if not hit:
                continue
            mem = sum(1 for p in members if p.search(low))
            if members and not mem:
                continue
            found.append((hit + mem, a, _cut(text, a, b)))
    best = sorted(found, key=lambda x: (-x[0], x[1]))[:MAX_CANDIDATES]
    best.sort(key=lambda x: x[1])
    prefix = ROLES[role]
    return [Candidate(f"{prefix}{i}", a, b, text[a:b]) for i, (_s, a, b) in enumerate(best, 1)]


def request(company: str, quarter: str, metric_label: str, kind: str, reason: list[Candidate], asked: list[Candidate],
            prompt: Prompt, schema: dict, document_id: int) -> Request:
    """One flag's request: the candidates as numbered lines in one plain-text source, then the question."""
    gone = {"missing": "is not in the company's results release for", "dropped": "has been absent from the company's "
            "results releases for two quarters, the latest", "renamed_or_redefined": "is not in the company's results "
            "release, and a new metric with the same unit appeared, for"}.get(kind, "is not in the results release for")
    body = "\n".join([f"{c.id}: {' '.join(c.text.split())}" for c in reason + asked])
    src = Source(document_id, body, f"{company} earnings call, {quarter}: candidate sentences")
    instruction = (f"The metric \"{metric_label}\" {gone} {quarter}. The candidate sentences come from that quarter's "
                   f"earnings call: ids starting with M are said by the company's management, ids starting with A by "
                   "analysts. Pick as the rules say.")
    return Request(TASK, prompt, (src,), instruction, schema=schema)


def parse(answer: dict, reason: list[Candidate], asked: list[Candidate]) -> tuple[Candidate | None, Candidate | None]:
    """The picked candidates (an id of the wrong role, or not offered, is no pick)."""
    by_id = {c.id: c for c in reason + asked}
    r, a = (answer or {}).get("reason"), (answer or {}).get("asked")
    rc = by_id.get(r) if isinstance(r, str) and r.startswith("M") else None
    ac = by_id.get(a) if isinstance(a, str) and a.startswith("A") else None
    return rc, ac


def verify(text: str, c: Candidate | None, document_id: int, pages: Sequence[tuple[int, int, int]] | None) -> Quote | None:
    """A pick as a verified quote into the stored transcript, or None (module doc)."""
    if c is None:
        return None
    v = rm.verify_side(text, {"text": c.text, "spans": [[c.start, c.end]]}, document_id, pages)
    if v is None or v["page"] is None or len(v["text"]) > MAX_QUOTE:
        return None
    return Quote(document_id, v["start"], v["end"], v["text"], v["page"])
