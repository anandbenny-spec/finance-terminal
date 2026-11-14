"""The KPI-table pilot's grid and Plan 05 §1.3's six rules, `rule_version` 1 (DD-05, #513). Pure: one company's documents,
each with the curated metric keys its kept rows carry, in; the candidate flags out. No AI, no database.

The rules as Plan 05 §1.3 writes them, with the thresholds Data Audit 10 fixed:
1. **Quarterly metrics only, present in at least three of the previous four documents.** The dictionary holds quarterly
   metrics only; the previous four are the four fiscal quarters before, and a quarter that isn't usable (below) is neither
   present nor absent, so with one held the metric needs all three of the others (Anand, 14 Nov 2026, option 1 on #512).
2. **Like with like:** only documents of the company's curated class (release against release, fact sheet against fact
   sheet) are compared; another class is not usable.
3. **A document whose metric count is below 70% of the previous quarter's is held whole** (Data Audit 10 decision 8): it is
   not usable, so it neither makes nor answers a flag, and the next quarter compares against the last usable one.
4. **A dropped metric and a new one with the same unit in the same quarter is "renamed or redefined", not dropped.** New:
   present in this document and in none of the usable previous four. A renamed metric is not escalated by rule 6.
   "The same unit" is the curated unit together with the measure kind (`dictionary.Metric.kind`; #513 part 2): a share
   and a growth rate both print "%", and dev run 13 paired TCS's LTM attrition with a new QoQ growth rate.
5. **Corporate actions annotate the flag:** a merger or demerger effective after the metric's last report and by the
   quarter's end is named on the flag.
6. **A first absence is "missing this quarter"; it becomes "dropped" at the second:** the next usable quarter after a
   "missing" flag that still lacks the metric is flagged "dropped" (rule 1 qualified the metric at its first absence).

**Usable** (#513's note of 14 Nov 2026, from #512's fix round): a `read` document of the company's class with at least one
kept row the dictionary maps, not held by rule 3. A `held` document, or a read one with nothing mapped, is treated as
missing data: no comparison is made against it, and it never makes a "missing" or "dropped" flag.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

RULE_VERSION = 1
PREVIOUS = 4                      # rule 1: the previous four documents
PRESENT_MIN = 3                   # rule 1: present in at least three of them
SHARP_FALL = Decimal("0.70")      # rule 3: below 70% of the previous quarter's metric count (DA-10 decision 8)
MISSING = "missing"
DROPPED = "dropped"
RENAMED = "renamed_or_redefined"
_LABEL = re.compile(r"Q([1-4]) FY(\d{2})")


def quarter_index(label: str) -> int:
    """'Q2 FY26' → a running quarter number (consecutive quarters differ by one)."""
    m = _LABEL.fullmatch(label or "")
    if not m:
        raise ValueError(f"not a fiscal quarter label: {label!r}")
    return int(m[2]) * 4 + int(m[1]) - 1


@dataclass(frozen=True)
class Reported:
    """A metric as one document reported it: its value (None when the document's mapped rows disagree), the unit as
    printed, the page, and the row numbers it rests on."""
    value: Decimal | None
    unit: str | None
    page: int
    rows: tuple[int, ...]


@dataclass
class Doc:
    """One company-quarter's pilot document, as the grid reads it."""
    id: int | None
    fiscal_label: str
    period_end: date
    document_class: str
    status: str                                     # 'read' | 'held'
    present: dict[str, Reported] = field(default_factory=dict)   # metric key → as reported (kept, mapped rows)
    sha256: bytes | None = None


@dataclass(frozen=True)
class Break:
    """A merger or demerger (the corporate-action record or `structural_breaks.yaml`), for rule 5."""
    effective: date
    kind: str                                       # 'merger' | 'demerger'
    what: str                                       # a short description, shown on the flag


@dataclass
class Flag:
    key: str
    kind: str                                       # MISSING | DROPPED | RENAMED
    current: Doc
    previous: list[Doc]                             # the documents it was reported in (rule 1's window)
    last: Doc                                       # the last document that reported it
    last_reported: Reported
    renamed_to: list[str] = field(default_factory=list)   # rule 4: the new metrics with the same unit
    note: str | None = None                         # rule 5
    escalates: Flag | None = None                   # rule 6: the "missing" flag a "dropped" one escalates

    @property
    def last_value(self) -> Decimal | None:
        return self.last_reported.value


@dataclass
class Grid:
    flags: list[Flag]
    usable: dict[str, bool]                         # fiscal label → usable
    held_rule3: list[str]                           # fiscal labels held whole by rule 3
    before_rules: int                               # metrics in the previous usable document and absent from this one
    by_kind: Counter


def reported(rows: list[tuple[int, str, Decimal | None, str | None, int]]) -> dict[str, Reported]:
    """A document's kept, mapped rows (row number, key, value, unit, page) as one `Reported` per key. A key whose rows
    disagree on the value keeps no value (held back: the field, not the flag)."""
    by: dict[str, list[tuple[int, Decimal | None, str | None, int]]] = {}
    for row_no, key, value, unit, page in rows:
        if key:
            by.setdefault(key, []).append((row_no, value, unit, page))
    out: dict[str, Reported] = {}
    for key, xs in by.items():
        xs.sort()
        values = {v for _, v, _, _ in xs if v is not None}
        units = {u for _, _, u, _ in xs if u}
        one = len(values) == 1 and len(units) <= 1
        out[key] = Reported(next(iter(values)) if one else None, (next(iter(units)) if units else None) if one else None,
                            xs[0][3], tuple(r for r, _, _, _ in xs))
    return out


def grid(docs: list[Doc], units: dict[str, object], document_class: str, breaks: list[Break] = ()) -> Grid:
    """The flags for one company. `units` maps each metric key to what rule 4 compares: its curated unit kind (the task
    passes `dictionary.Metric.kind`, the unit and the measure kinds; any value compared by equality)."""
    docs = sorted(docs, key=lambda d: d.period_end)
    by_q = {quarter_index(d.fiscal_label): d for d in docs}
    usable: dict[str, bool] = {}
    held3: list[str] = []
    last_count: int | None = None
    for d in docs:                                                     # rules 2 and 3, and #513's guard
        ok = d.status == "read" and d.document_class == document_class and bool(d.present)
        if ok and last_count is not None and Decimal(len(d.present)) < SHARP_FALL * last_count:
            ok = False
            held3.append(d.fiscal_label)
        if ok:
            last_count = len(d.present)
        usable[d.fiscal_label] = ok

    def ok(d: Doc | None) -> bool:
        return d is not None and usable[d.fiscal_label]

    flags: list[Flag] = []
    before = 0
    keys = sorted({k for d in docs if ok(d) for k in d.present})
    for key in keys:
        open_missing: Flag | None = None                               # rule 6: the flag a second absence escalates
        prior: Doc | None = None                                       # the last usable document before this one
        for d in docs:
            if not ok(d):
                continue
            if key in d.present:
                open_missing, prior = None, d
                continue
            if prior is None:                                          # never seen yet: nothing to compare
                continue
            if key in prior.present:                                   # a first absence
                before += 1
                f = _first_absence(key, d, by_q, ok, units)
                if f is not None:
                    f.note = _note(breaks, f.last.period_end, d.period_end)
                    flags.append(f)
                    open_missing = f if f.kind == MISSING else None
            elif open_missing is not None:                             # the second absence: rule 6
                flags.append(Flag(key, DROPPED, d, open_missing.previous, open_missing.last, open_missing.last_reported,
                                  note=_note(breaks, open_missing.last.period_end, d.period_end), escalates=open_missing))
                open_missing = None
            prior = d
    flags.sort(key=lambda f: (f.current.period_end, f.key))
    return Grid(flags, usable, held3, before, Counter(f.kind for f in flags))


def _first_absence(key: str, d: Doc, by_q: dict[int, Doc], ok, units: dict[str, object]) -> Flag | None:
    """Rule 1 at a first absence, then rule 4."""
    q = quarter_index(d.fiscal_label)
    window = [by_q.get(q - i) for i in range(1, PREVIOUS + 1)]
    known = [x for x in window if ok(x)]
    present = [x for x in known if key in x.present]
    if len(present) < PRESENT_MIN:
        return None
    last = max(present, key=lambda x: x.period_end)
    unit = units.get(key)
    new = sorted(k for k in d.present
                 if k != key and unit is not None and units.get(k) == unit and not any(k in x.present for x in known))
    kind = RENAMED if new else MISSING
    return Flag(key, kind, d, sorted(present, key=lambda x: x.period_end), last, last.present[key], new)


def _note(breaks, after: date, until: date) -> str | None:
    hits = [b for b in breaks if after < b.effective <= until]
    if not hits:
        return None
    return "; ".join(f"{b.kind.capitalize()} effective {b.effective:%-d %b %Y}: {b.what}" for b in hits)
