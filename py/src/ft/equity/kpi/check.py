"""The Sonnet check of each KPI-pilot flag (DD-05 part 2, #513; Plan 05 §1.3; Data Audit 10 §8 finding 1). Pure: the
flags, the pages of the documents they rest on (read this run, never stored) and the dictionary in; the requests, and
each flag's verdict out. The network (sending) and the database are the task's.

Data Audit 10 found that most false flags were metrics the current document still states under another label, or that
the extraction missed there. So each flag that rules 1 to 6 leave is read once more, by Sonnet 5.5, against the pages it
rests on, with three questions (structured output):

1. **present:** does the current document state this metric for the quarter, under any label? "yes" drops the flag.
2. **previous_value:** does the previous document's page state the value we extracted? Anything but "yes" holds back
   the flag's last value (the field, not the flag; CLAUDE.md §1).
3. **reason_in_release:** do the current pages say the metric is no longer reported, is reported differently, or why?
   "yes" drops the flag: the release isn't stored (Plan 05 D7), so its words can't be shown as a verified quote, and the
   flag's wording "No reason found in the release or the call" (D8) would then be untrue.

"unsure" or no answer keeps the flag for the judge (DD-07), who reads both documents. Sonnet's answers are never shown;
they only remove flags or hold back a value.

A "dropped" flag (rule 6: the second absence) whose "missing" flag the check removed is withdrawn too: if the metric was
there at the first absence, or that release said why, "dropped" would be untrue, and the flag's last value and quarter
(carried from the first absence) would be wrong, so it is held back rather than re-labelled (CLAUDE.md §1).

**What Sonnet reads** (kept small: the check runs inside the pilot's $5):
- for each flag, the previous document's page that held the last value: an excerpt of up to `EXCERPT_CHARS` around the
  line that names it (the whole page if shorter; the page image for an image document);
- the current document's pages most likely to hold it, at most `PICK` a flag: the pages where the metric's neighbours on
  that previous page now sit (the other metrics the same page reported, still reported), then the pages that name its
  words, then the page with the same number; each cut to `PAGE_CHARS`;
- a document's flags go together, at most `MAX_FLAGS` a request and `MAX_PAGES` current pages a request.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from ft.ai.client import Prompt, Request, Source
from ft.equity.kpi import dictionary as kdict
from ft.equity.kpi import flags as kf

TASK = "kpi_check"
MAX_PAGES = 4                   # current-document pages in one request
MAX_FLAGS = 10                  # flags in one request
PICK = 2                        # current pages chosen for one flag
PAGE_CHARS = 6_000              # a current page's text, cut
EXCERPT_CHARS = 2_000           # the previous page's excerpt around the metric
YES, NO, UNSURE = "yes", "no", "unsure"
ANSWERS = (YES, NO, UNSURE)
# what happens to a flag
KEEP = "keep"
DROP_PRESENT = "present"        # the current document states it
DROP_REASON = "reason_in_release"   # the current document says why, which can't be quoted
UNCHECKED = "unchecked"         # not asked (no pages this run, the cap, AI unavailable) or no usable answer
WITHDRAWN = "escalation_withdrawn"   # a "dropped" flag whose first absence the check removed (module doc)


@dataclass(frozen=True)
class Page:
    """One page of a document as the check sends it: its text (cut), or its image (base64 PNG)."""
    page: int
    text: str | None = None
    image: str | None = None


@dataclass
class Item:
    """One flag as the check asks about it."""
    flag: kf.Flag
    metric: kdict.Metric
    printed: str                    # the previous row's label as printed
    member: str | None              # the previous row's member as printed
    previous: Page | None           # the excerpt of the page that held the last value (None: not read this run)
    pages: list[int] = field(default_factory=list)   # the current pages chosen for it
    renamed_to: list[str] = field(default_factory=list)   # rule 4's new metrics, by their curated names


@dataclass
class Verdict:
    present: str = UNSURE
    previous_value: str = UNSURE
    reason_in_release: str = UNSURE

    @property
    def outcome(self) -> str:
        if self.present == YES:
            return DROP_PRESENT
        if self.reason_in_release == YES:
            return DROP_REASON
        return KEEP

    @property
    def value_ok(self) -> bool:
        return self.previous_value == YES


def _tokens(s: str) -> set[str]:
    return {t for t in kdict.norm_label(s).split() if len(t) >= 3}


def _digits(v: Decimal | None) -> str:
    if v is None:
        return ""
    s = format(abs(v), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return re.sub(r"\D", "", s)


def excerpt(text: str, label: str, value: Decimal | None, limit: int = EXCERPT_CHARS) -> str:
    """Up to `limit` characters of a page around the line that best names the metric (the label's words, and the value's
    digits); the whole page when it is shorter, the page's start when no line names it."""
    if len(text) <= limit:
        return text
    lines = text.splitlines(keepends=True)
    want, digits = _tokens(label), _digits(value)
    best, score = None, 0
    for i, line in enumerate(lines):
        s = len(want & _tokens(line)) + (2 if len(digits) >= 2 and digits in re.sub(r"\D", "", line) else 0)
        if s > score:
            best, score = i, s
    if best is None:
        return text[:limit]
    lo = hi = best
    size = len(lines[best])
    while True:                                                   # grow the window a line at a time, both ways
        grew = False
        for j in (lo - 1, hi + 1):
            if 0 <= j < len(lines) and size + len(lines[j]) <= limit:
                size += len(lines[j])
                lo, hi = min(lo, j), max(hi, j)
                grew = True
        if not grew:
            break
    return "".join(lines[lo:hi + 1])[:limit]


def _mentions(text: str, words: tuple[str, ...]) -> int:
    low = text.lower()
    return sum(len(re.findall(r"(?<![a-z])" + re.escape(w), low)) for w in words if w)


def pick_pages(flag: kf.Flag, metric: kdict.Metric, current: dict[int, Page]) -> list[int]:
    """The current pages most likely to state the metric (module doc): neighbours first, then its words, then the page
    with the same number; at most PICK."""
    if not current:
        return []
    page = flag.last_reported.page
    votes: Counter = Counter()
    for k, r in flag.last.present.items():
        if k != flag.key and r.page == page and k in flag.current.present:
            votes[flag.current.present[k].page] += 1
    words = tuple(metric.terms) + tuple(metric.member_terms)
    hits = {p: _mentions(pg.text or "", words) for p, pg in current.items()}
    ranked = sorted(current, key=lambda p: (-votes.get(p, 0), -hits.get(p, 0), p != page, p))
    chosen = [p for p in ranked if votes.get(p, 0) or hits.get(p, 0)][:PICK]
    if not chosen:
        chosen = [page] if page in current else [ranked[0]]
    return chosen


def groups(items: list[Item]) -> list[list[Item]]:
    """One document's items in requests: at most MAX_FLAGS each, whose chosen pages together are at most MAX_PAGES."""
    out: list[list[Item]] = []
    pages: set[int] = set()
    for it in sorted(items, key=lambda x: (tuple(x.pages), x.flag.key)):
        if out and len(out[-1]) < MAX_FLAGS and len(pages | set(it.pages)) <= MAX_PAGES:
            out[-1].append(it)
            pages |= set(it.pages)
        else:
            out.append([it])
            pages = set(it.pages)
    return out


def _value(v: Decimal | None, unit: str | None) -> str:
    if v is None:
        return "no single value (the rows disagreed)"
    return f"{format(v, 'f')}{' ' + unit if unit else ''}"


def _source(title: str, pg: Page) -> Source:
    if pg.image is not None:
        return Source(0, "", title, image_base64=pg.image, image_media_type="image/png")
    return Source(0, pg.text or "", title)


def request(company: str, document_class: str, group: list[Item], current: dict[int, Page], prompt: Prompt,
            schema: dict) -> Request:
    """One request: the current pages the group's flags chose, then each flag's previous excerpt, then the questions."""
    quarter = group[0].flag.current.fiscal_label
    kind = document_class.replace("_", " ")
    pages = sorted({p for it in group for p in it.pages})
    sources = [_source(f"Current {kind} ({quarter}), page {p}", current[p]) for p in pages]
    lines = [(f"The current document is {company}'s {quarter} results {kind}; its pages most likely to hold these metrics "
              f"are the documents titled \"Current {kind} ({quarter}), page …\". For each flag below, answer the three "
              "questions as the rules say.")]
    for n, it in enumerate(group, 1):
        f = it.flag
        prev = it.previous
        if prev is not None:
            sources.append(_source(f"Flag {n}: previous {kind} ({f.last.fiscal_label}), page {prev.page}", prev))
        member = f" for \"{it.member}\"" if it.member else ""
        lines.append(
            f"Flag {n}: the metric \"{it.metric.label}\" (measure: {', '.join(it.metric.measures) or 'unknown'}; "
            f"unit: {it.metric.unit}). In the {f.last.fiscal_label} {kind} it was printed as \"{it.printed}\"{member} "
            f"with the value {_value(f.last_value, f.last_reported.unit)}"
            + (f" (the document titled \"Flag {n}: previous {kind} ({f.last.fiscal_label}), page {prev.page}\")."
               if prev is not None else " (its page isn't shown: answer previous_value \"unsure\").")
            + (f" New in {quarter}, with the same unit: {'; '.join(it.renamed_to)}." if it.renamed_to else ""))
    return Request(TASK, prompt, tuple(sources), "\n".join(lines), schema=schema)


def outcome(flag: kf.Flag, verdicts: dict[int, Verdict | None]) -> str:
    """A flag's outcome from the check's verdicts (by `id(flag)`): its own verdict's, or UNCHECKED; and a "dropped" flag
    whose first absence was removed is WITHDRAWN (module doc)."""
    v = verdicts.get(id(flag))
    own = v.outcome if v is not None else UNCHECKED
    if own == KEEP or own == UNCHECKED:
        first = flag.escalates
        fv = verdicts.get(id(first)) if first is not None else None
        if fv is not None and fv.outcome in (DROP_PRESENT, DROP_REASON):
            return WITHDRAWN
    return own


def parse(answer: dict, n: int) -> dict[int, Verdict]:
    """The reply's verdicts by flag number (1..n). A flag with no valid answer gets none (it stays unchecked)."""
    out: dict[int, Verdict] = {}
    for x in (answer or {}).get("flags") or []:
        if not isinstance(x, dict):
            continue
        k = x.get("flag")
        if not isinstance(k, int) or not 1 <= k <= n or k in out:
            continue
        vals = [x.get(q) for q in ("present", "previous_value", "reason_in_release")]
        if not all(v in ANSWERS for v in vals):
            continue
        out[k] = Verdict(*vals)
    return out
