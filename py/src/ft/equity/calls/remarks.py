"""Opening-remarks changes of an earnings call against the previous quarter's (Spec 02B §5.13, D9; job 2B-17, #180).
Pure functions: the call's text and turns in; a similarity, labels and at most five sentence pairs out. No AI.

- **Text:** the management turns of the `opening` section, each on its own line. For matching, each run of digits becomes
  `#` (the figures are in the results beside the call); `masked` keeps a map back to the transcript's own offsets.
- **Matching:** module 1's sentence-matched differ (`ft.engines.document.diff.diff_texts`), unchanged, on the masked texts.
  **Similarity** = 1 - (words added + words removed) / (the previous opening's words + this opening's words).
- **Labels:** no similarity threshold and no `largely_rewritten` label (Anand, 8 Nov 2026: the 0.5 check failed, 94% of
  calls fell below it; openings are reworded every quarter). A management speaker of this opening who didn't speak in the
  previous one and has at least `NEW_SPEAKER_SHARE` (30%) of its words is listed in `new_speakers` and the label is
  `new_speaker`; otherwise the label is null. The similarity stays a stored number.
- **Pairs:** the `MAX_PAIRS` (5) with the most changed words, sentences as filed (numbers included), each side at most
  `MAX_SIDE` (400) code points, with spans in the transcript's canonical text.
- **Verified quotes (`remarks-3`, #414):** `verify_pairs` turns each pair side into a citation into the call's stored text, the way
  the AI blocks cite it: the side's leading contiguous run of sentences is re-read from the stored text by its code-point
  offsets, must equal the side's own text (whitespace collapsed), and is stored as `{text, document_id, start, end, page,
  page_last, verified: true}`, cut at a word to at most `MAX_SIDE` code points. A side that does not verify is dropped, a pair
  with no verified side is dropped, and a pair that lost a side keeps its kind. Nothing a visitor sees is free text.
- **No comparison** (None) when either call has no opening words. Role-held calls and a missing previous call are the task's.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from ft.engines.document.diff import diff_texts
from ft.engines.document.text import words
from ft.equity.calls.turns import name_key, same_person

RULE_VERSION = "remarks-3"
NEW_SPEAKER_SHARE = 0.30
MAX_PAIRS = 5
MAX_SIDE = 400

_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class TurnRef:
    """What the comparison needs of a stored turn (`equity.call_turn`)."""
    char_start: int
    char_end: int
    speaker: str | None
    role: str
    section: str
    words: int


@dataclass(frozen=True)
class Opening:
    masked: str                    # the matched text: management opening turns, one per line, digits masked
    offsets: tuple[int, ...]       # for each masked character, its offset in the transcript's text
    ends: tuple[int, ...]          # and the offset one past it there (a `#` stands for a whole run of digits)
    text: str                      # the same turns unmasked, in the same line layout (for the sentences as filed)
    speakers: dict[str, int]       # name key -> words in the opening
    names: dict[str, str]          # name key -> the label as written (first seen)
    words: int


@dataclass
class Change:
    words_base: int
    words_new: int
    words_added: int
    words_removed: int
    similarity: float
    new_speakers: list[str] = field(default_factory=list)
    label: str | None = None
    pairs: list[dict] = field(default_factory=list)


def mask(text: str) -> str:
    """Each run of digits becomes one `#`."""
    return _DIGITS.sub("#", text)


def opening(text: str, turns: Sequence[TurnRef]) -> Opening | None:
    """The management turns of the opening section, or None when there are none (or they hold no words)."""
    masked: list[str] = []
    offsets: list[int] = []
    ends: list[int] = []
    plain: list[str] = []
    speakers: dict[str, int] = {}
    names: dict[str, str] = {}

    def put(ch: str, a: int, b: int) -> None:
        masked.append(ch); offsets.append(a); ends.append(b)

    for t in sorted(turns, key=lambda t: t.char_start):
        if t.section != "opening" or t.role != "management":
            continue
        body = text[t.char_start:t.char_end]
        if masked:
            put("\n", t.char_start, t.char_start)
            plain.append("\n")
        pos = 0
        for m in _DIGITS.finditer(body):
            for i in range(pos, m.start()):
                put(body[i], t.char_start + i, t.char_start + i + 1)
            put("#", t.char_start + m.start(), t.char_start + m.end())
            pos = m.end()
        for i in range(pos, len(body)):
            put(body[i], t.char_start + i, t.char_start + i + 1)
        plain.append(body)
        if t.speaker:
            key = name_key(t.speaker)
            if key:
                speakers[key] = speakers.get(key, 0) + t.words
                names.setdefault(key, " ".join(t.speaker.split()))
    joined = "".join(masked)
    n = len(words(joined))
    if n == 0:
        return None
    return Opening(joined, tuple(offsets), tuple(ends),"".join(plain), speakers, names, n)


def _side(text: str, spans: list[list[int]], o: Opening) -> dict | None:
    """One side of a pair: its sentences as filed, joined by a space, capped at MAX_SIDE code points, with the spans in
    the transcript's text (spans on the masked text mapped back through the offsets)."""
    if not spans:
        return None
    real = [[o.offsets[s], o.ends[e - 1]] for s, e in spans]
    shown = " ".join(" ".join(text[a:b].split()) for a, b in real)
    if len(shown) > MAX_SIDE:
        shown = shown[:MAX_SIDE - 1].rstrip() + "…"
    return {"text": shown, "spans": real}


def compare(new_text: str, new: Opening, old_text: str, old: Opening) -> Change:
    """The opening `new` (of `new_text`) against the previous quarter's `old` (of `old_text`)."""
    d = diff_texts(old.masked, new.masked)
    changed = d.words_added + d.words_removed
    similarity = max(0.0, min(1.0, 1 - changed / (old.words + new.words)))
    ranked = []
    for i, p in enumerate(d.segments["pairs"]):
        if p["kind"] == "same":
            continue
        if p["kind"] == "changed":
            n = sum(len(words(x["text"])) for x in p["ops"] if x["op"] != "eq")
        else:
            n = len(words(p["text"]))
        if n:
            ranked.append((-n, i, n, p))
    ranked.sort(key=lambda r: (r[0], r[1]))
    pairs = [{"kind": p["kind"], "changed_words": n,
              "old": _side(old_text, p["old"], old), "new": _side(new_text, p["new"], new)}
             for _, _, n, p in ranked[:MAX_PAIRS]]
    fresh = sorted(
        (new.names[k] for k, w in new.speakers.items()
         if w >= NEW_SPEAKER_SHARE * sum(new.speakers.values())
         and not any(same_person(k, ok) for ok in old.speakers)),
        key=str.lower)
    label = "new_speaker" if fresh else None
    return Change(old.words, new.words, d.words_added, d.words_removed, round(similarity, 4), fresh, label, pairs)


def previous_label(label: str) -> str | None:
    """The fiscal quarter before 'Q2 FY27' ('Q1 FY27'); Q1's is the previous year's Q4 ('Q4 FY26')."""
    m = re.fullmatch(r"Q([1-4]) FY(\d{2})", label or "")
    if not m:
        return None
    q, y = int(m[1]), int(m[2])
    if q > 1:
        return f"Q{q - 1} FY{y:02d}"
    return f"Q4 FY{(y - 1) % 100:02d}"


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _page_at(pages: Sequence[tuple[int, int, int]] | None, point: int) -> int | None:
    """The physical PDF page holding code point `point`, from (pdf_page, char_start, char_end) rows; None without a page map."""
    for page, a, b in pages or ():
        if a <= point < b:
            return page
    return None


def verify_side(doc_text: str, side: dict | None, document_id: int, pages: Sequence[tuple[int, int, int]] | None = None) -> dict | None:
    """One pair side as a verified citation, or None. The side's spans are on the stored text; the leading run of spans with
    only whitespace between them is re-read from `doc_text` and must equal the side's own text (an ellipsis from the cut
    allowed), else the side is dropped. The range is cut at a word so it is at most MAX_SIDE code points."""
    if not side or not isinstance(side.get("text"), str) or not side.get("spans"):
        return None
    spans = [s for s in side["spans"] if isinstance(s, list) and len(s) == 2 and 0 <= s[0] < s[1] <= len(doc_text)]
    if len(spans) != len(side["spans"]):
        return None
    start = spans[0][0]
    end = spans[0][1]
    for a, b in spans[1:]:
        if a < end or doc_text[end:a].strip():
            break
        end = b
    if end - start > MAX_SIDE:
        cut = doc_text.rfind(" ", start, start + MAX_SIDE)
        cut = max(doc_text.rfind("\n", start, start + MAX_SIDE), cut)
        end = cut if cut > start else start + MAX_SIDE
    quote = _collapse(doc_text[start:end])
    claimed = side["text"][:-1].rstrip() if side["text"].endswith("…") else side["text"]
    if not quote or len(quote) > MAX_SIDE or not _collapse(claimed).startswith(quote):
        return None
    last = max(start, end - 1)
    return {"text": quote, "document_id": document_id, "start": start, "end": end,
            "page": _page_at(pages, start), "page_last": _page_at(pages, last), "verified": True}


def verify_pairs(pairs: Sequence[dict], new_text: str, new_doc: int, new_pages: Sequence[tuple[int, int, int]] | None,
                 old_text: str, old_doc: int, old_pages: Sequence[tuple[int, int, int]] | None) -> list[dict]:
    """The pairs with every side a verified citation; unverified sides and pairs left with no verified side are dropped."""
    out = []
    for p in pairs:
        old = verify_side(old_text, p.get("old"), old_doc, old_pages)
        new = verify_side(new_text, p.get("new"), new_doc, new_pages)
        if old is None and new is None:
            continue
        out.append({"kind": p["kind"], "changed_words": p["changed_words"], "old": old, "new": new})
    return out
