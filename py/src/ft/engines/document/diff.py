"""The sentence-matched differ (Spec 01 §2, "Diff (S)"; Design 01 §1.16): a statement against the previous statement,
a resolution against the previous resolution.

1. Both canonical texts are split into sentences (text.sentence_spans, with its abbreviation list).
2. Sentences are paired in order (an alignment, so a moved sentence reads as removed and added) by word-level
   similarity: difflib's ratio over the sentences' lower-cased words, at least `PAIR_MIN` (0.55). One old sentence
   may pair with two adjacent new ones (1→2), or two old with one new (2→1), when the joined ratio beats the best
   single pair by at least `SPLIT_GAIN` (0.10). Among alignments, the one with the highest total ratio wins.
3. Unpaired sentences are added or removed; inside each changed pair, a word-level diff.
Starting values, tuned on the backfill (Design 01 §10.6); the values used are stored with each diff.

`segments`, diff version 2 (core.document_diff.segments; no migration: the column is versioned JSON):
    {"version": 2,
     "params": {"pair_min": 0.55, "split_gain": 0.1},
     "words": {"old": <words in the old text>, "new": <words in the new text>},
     "pairs": [{"kind": "same" | "changed" | "added" | "removed",
                "topic": null,                       # Pass 1b's topic map (topics.yaml) fills this
                "old": [[start, end], ...],          # code-point spans in the old text ([] when added)
                "new": [[start, end], ...],          # and in the new text ([] when removed)
                "old_para": [n, ...], "new_para": [n, ...],   # the spans' ¶ numbers (1-based blocks)
                "ratio": 0.82,                       # changed pairs
                "text": "...",                       # same, added and removed: the sentence
                "ops": [{"op": "eq" | "del" | "ins", "text": "..."}]}]}   # changed: eq+del = old, eq+ins = new
The text is stored, not only the spans, so the redline renders from this row alone; the spans and ¶ numbers make
the ¶ links and citations. words_added and words_removed count the words in ins and added, del and removed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from ft.engines.document.text import paragraph_of, paragraph_starts, sentence_spans, words

DIFF_VERSION = "2"
PAIR_MIN = 0.55
SPLIT_GAIN = 0.10

_TOKEN = re.compile(r"\s+|\w+(?:[.,'/-]\w+)*|[^\w\s]")


@dataclass(frozen=True)
class Diff:
    segments: dict
    words_added: int
    words_removed: int
    diff_version: str = DIFF_VERSION


def _sm(a: list[str], b: list[str]) -> SequenceMatcher:
    return SequenceMatcher(None, a, b, autojunk=False)


class _Sims:
    """Word-level similarity between old and new sentences, computed on demand. Pairs that can't reach PAIR_MIN are
    ruled out by difflib's cheap upper bounds first."""

    def __init__(self, old: list[list[str]], new: list[list[str]]):
        self.old, self.new, self._memo = old, new, {}

    def ratio(self, a: list[str], b: list[str]) -> float:
        if not a and not b:
            return 1.0
        sm = _sm(a, b)
        if sm.real_quick_ratio() < PAIR_MIN or sm.quick_ratio() < PAIR_MIN:
            return 0.0
        return sm.ratio()

    def get(self, olds: tuple[int, ...], news: tuple[int, ...]) -> float:
        key = (olds, news)
        if key not in self._memo:
            a = [w for i in olds for w in self.old[i]]
            b = [w for j in news for w in self.new[j]]
            self._memo[key] = self.ratio(a, b)
        return self._memo[key]


def _align(old: list[list[str]], new: list[list[str]]) -> list[tuple[tuple[int, ...], tuple[int, ...], float]]:
    """The in-order alignment with the highest total similarity. Steps: a 1-1 pair, a 1→2 or 2→1 pair (allowed when
    it beats the best single pair by SPLIT_GAIN), a removed or an added sentence."""
    n, m = len(old), len(new)
    sims = _Sims(old, new)
    neg = float("-inf")
    best = [[neg] * (m + 1) for _ in range(n + 1)]
    step: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            cands = []
            if i >= 1 and j >= 1 and (r := sims.get((i - 1,), (j - 1,))) >= PAIR_MIN:
                cands.append((best[i - 1][j - 1] + r, ((i - 1,), (j - 1,), r), (i - 1, j - 1)))
            if i >= 1 and j >= 2:
                single = max(sims.get((i - 1,), (j - 2,)), sims.get((i - 1,), (j - 1,)))
                r = sims.get((i - 1,), (j - 2, j - 1))
                if r >= PAIR_MIN and r >= single + SPLIT_GAIN:
                    cands.append((best[i - 1][j - 2] + r, ((i - 1,), (j - 2, j - 1), r), (i - 1, j - 2)))
            if i >= 2 and j >= 1:
                single = max(sims.get((i - 2,), (j - 1,)), sims.get((i - 1,), (j - 1,)))
                r = sims.get((i - 2, i - 1), (j - 1,))
                if r >= PAIR_MIN and r >= single + SPLIT_GAIN:
                    cands.append((best[i - 2][j - 1] + r, ((i - 2, i - 1), (j - 1,), r), (i - 2, j - 1)))
            if j >= 1:
                cands.append((best[i][j - 1], ((), (j - 1,), 0.0), (i, j - 1)))
            if i >= 1:
                cands.append((best[i - 1][j], ((i - 1,), (), 0.0), (i - 1, j)))
            # The first of equal scores wins, in the order above; preferring "added" when tracing back from the end
            # puts a removed sentence before the sentence added in its place.
            score, move, prev = max(cands, key=lambda c: c[0])
            best[i][j], step[i][j] = score, (move, prev)
    out, i, j = [], n, m
    while (i, j) != (0, 0):
        move, (i, j) = step[i][j]
        out.append(move)
    out.reverse()
    return out


def _word_ops(a: str, b: str) -> list[dict]:
    """A word-level diff of two sentences: eq, del and ins runs. Whitespace between two changes joins them, so a
    rewritten phrase reads as one deletion and one insertion."""
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    raw: list[tuple[str, str, str]] = []                 # (op, old text, new text)
    for tag, i1, i2, j1, j2 in _sm(ta, tb).get_opcodes():
        x, y = "".join(ta[i1:i2]), "".join(tb[j1:j2])
        raw.append(("eq" if tag == "equal" else "chg", x, y))
    merged: list[list[str]] = []
    for k, (op, x, y) in enumerate(raw):
        between = 0 < k < len(raw) - 1 and raw[k - 1][0] == "chg" and raw[k + 1][0] == "chg"
        if op == "eq" and between and not x.strip():
            op, y = "chg", x                              # absorb the space into both sides of the change
        if merged and merged[-1][0] == op == "chg":
            merged[-1][1] += x
            merged[-1][2] += y
        else:
            merged.append([op, x, y])
    ops = []
    for op, x, y in merged:
        if op == "eq":
            ops.append({"op": "eq", "text": x})
            continue
        if x:
            ops.append({"op": "del", "text": x})
        if y:
            ops.append({"op": "ins", "text": y})
    return ops


def diff_texts(old: str, new: str) -> Diff:
    """Diff the new canonical text against the old (its baseline)."""
    os_, ns_ = sentence_spans(old), sentence_spans(new)
    ow = [[w.lower() for w in words(old[s:e])] for s, e in os_]
    nw = [[w.lower() for w in words(new[s:e])] for s, e in ns_]
    opara, npara = paragraph_starts(old), paragraph_starts(new)
    pairs, added, removed = [], 0, 0
    for oi, ni, r in _align(ow, nw):
        o = [list(os_[i]) for i in oi]
        n = [list(ns_[j]) for j in ni]
        pair = {"kind": "", "topic": None, "old": o, "new": n,
                "old_para": [paragraph_of(opara, s) for s, _ in o], "new_para": [paragraph_of(npara, s) for s, _ in n]}
        otext = old[o[0][0]:o[-1][1]] if o else ""
        ntext = new[n[0][0]:n[-1][1]] if n else ""
        if not n:
            pair.update(kind="removed", text=otext)
            removed += len(words(otext))
        elif not o:
            pair.update(kind="added", text=ntext)
            added += len(words(ntext))
        elif otext == ntext:
            pair.update(kind="same", text=ntext)
        else:
            ops = _word_ops(otext, ntext)
            pair.update(kind="changed", ratio=round(r, 4), ops=ops)
            added += sum(len(words(x["text"])) for x in ops if x["op"] == "ins")
            removed += sum(len(words(x["text"])) for x in ops if x["op"] == "del")
        pairs.append(pair)
    segments = {"version": int(DIFF_VERSION), "params": {"pair_min": PAIR_MIN, "split_gain": SPLIT_GAIN},
                "words": {"old": len(words(old)), "new": len(words(new))}, "pairs": pairs}
    return Diff(segments, added, removed)
