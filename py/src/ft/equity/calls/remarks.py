"""Opening-remarks changes of an earnings call against the previous quarter's (Spec 02B §5.13, D9; job 2B-17, #180).
Pure functions: the call's text and turns in; a similarity, labels and at most five sentence pairs out. No AI.

- **Text:** the management turns of the `opening` section, each on its own line. For matching, each run of digits becomes
  `#` (the figures are in the results beside the call); `masked` keeps a map back to the transcript's own offsets.
- **Matching:** module 1's sentence-matched differ (`ft.engines.document.diff.diff_texts`), unchanged, on the masked texts.
  **Similarity** = 1 - (words added + words removed) / (the previous opening's words + this opening's words).
- **Labels:** similarity below `REWRITTEN_BELOW` (0.5) is `largely_rewritten`; a management speaker of this opening who
  didn't speak in the previous one and has at least `NEW_SPEAKER_SHARE` (30%) of its words is `new_speaker` (and listed);
  a new speaker wins the single label, since the opening is then treated as rewritten.
- **Pairs:** the `MAX_PAIRS` (5) with the most changed words, sentences as filed (numbers included), each side at most
  `MAX_SIDE` (400) code points, with spans in the transcript's canonical text.
- **No comparison** (None) when either call has no opening words. Role-held calls and a missing previous call are the task's.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from ft.engines.document.diff import diff_texts
from ft.engines.document.text import words
from ft.equity.calls.turns import name_key, same_person

RULE_VERSION = "1"
REWRITTEN_BELOW = 0.5
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
    label = "new_speaker" if fresh else ("largely_rewritten" if similarity < REWRITTEN_BELOW else None)
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
