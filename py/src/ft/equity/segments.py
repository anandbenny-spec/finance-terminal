"""Segment line-up changes (Plan 05 §1.2, D6, D8; job DD-01, #409): a company's reportable segments by quarter, read from
the consolidated results filings 2A stores, and the rule that flags a segment of the previous four quarters missing from the
latest. The reader is `ft/equity/xbrl.py` with 2A-09's column dating (`date_columns`); this module holds the line-up's
reading and the rule, nothing else. Pure: no database, no network.

**Why a history read.** VC-05 (#190) stores one fiscal year's segment revenue per results event (Plan 05 §0.5 N5). A line-up
change needs every quarter's segments, which are in every quarterly results XBRL 2A-07 stores (Data Audit 08, VC14).

**Reading a quarter** (the saved filings in the fixtures repo, 26 companies, 2018 to 2026, read 11 Nov 2026):

- Segments are members of the explicit `ReportableSegmentsAxis`, each with `DescriptionOfReportableSegment` and
  `SegmentRevenue` in its own context. The quarter's members are the contexts of the `One` column (Data Audit 02a finding 2)
  that `date_columns` dates as the quarter; a Q1 file's `Four` column is the quarter too and is read only if `One` is absent.
  Pre-2025 files date the `Four` column like the quarter and `date_columns` re-dates it to the year to date, so it never
  stands in for the quarter. Every saved filing with segment facts has them in quarter-dated `One` contexts.
- `IsCompanyReportingMultisegmentOrSingleSegment` says multi or single. Banks' filings (the bank format) carry no such flag
  but do carry segment members: segment members make a filing multi-segment. Eternal's 2021 filings read "Geographical
  segment": members, so multi. A single-segment filer is one line with its filed description and no revenue.
- A filing whose flag says multi with no segment members for the quarter, or with no flag and no members (an insurer's
  format), gives no line-up and is named by the task's warning check: never filled from the standalone filing.
- Revenue is `SegmentRevenue` as filed (it includes inter-segment sales), in rupees. Nothing is rescaled or summed across
  bases.
- **Reconciling lines are not segments.** Filers put the segment table's reconciling rows on the same axis: "Less: Inter
  segment revenue", "Add: Exceptional items", "(Add)/Less: Inter-segment margins" (L&T, Tata Motors, Grasim), and the
  unallocated items, which Ind AS 108 reconciles rather than reports ("Unallocated", HDFC Bank and SBI; "(c)
  Corporate/Unallocable", Tata Motors). A member whose description starts with "Add" or "Less", or names unallocated or
  unallocable items, is left out of the line-up. Other rows filed with no revenue or a zero revenue
  ("Unallocated 0.00", Grasim's "Depreciation and Amortisation Expense 0.00", Tata Motors' "Revenue from operations 0") stay in
  the line-up as filed but never make a flag on their own: a segment counts as absent only if it had a revenue other than
  zero in one of the four earlier quarters, and as new only with a revenue other than zero (below). Both decided by Claude under the standing delegation (24 Oct 2026) on 11 Nov 2026,
  recorded on #409, from the fixture run in its handoff; the alternative was every member as filed, which flagged 9
  reconciling rows in the fixtures.

**Which names are one segment.** Descriptions are compared after case, spacing, punctuation and footnote marks (`*`, `#`,
`$`, `^`), "&" and "and", an enumerator ("(a)", "(1)", "b)"), and a plural "s" at the end of a word are set aside: Bajaj
Finserv files "Investment and others" and "Investments and others" in turn (2019 to 2025), Infosys "Financial Services (1)"
(Sep 2024), Tata Motors "(a) Commercial Vehicle" then "(a) Passenger Vehicle" after the demerger. A segment of the previous
quarter and a new name in the latest that differ by at most two letters, both of at least ten, are one segment too (typos as
filed: HUL's "Beaty & Personal care", SBI's "Other Banking oprations"). Any other difference is a different name ("Quick
commerce business" and "Quick commerce" are two names). Decided by Claude under the standing delegation (24 Oct 2026) on
11 Nov 2026, recorded on #409; the alternative was the exact text as filed, which flagged 6 spelling changes in the fixtures.

**The rule** (`RULE_VERSION` 1; #409 step 3), the latest quarter against the four before it, all consolidated:

- the four previous quarters must each have a line-up (consecutive quarter ends), or nothing is compared;
- a segment is *absent* when it was filed in each of the four previous quarters, with a revenue other than zero in at least
  one of them, and is not in the latest at all; *new* when it is filed with a revenue other than zero in the latest and is in
  none of the four (Tata Motors filed "(a) Commercial Vehicle" at 0 in the Sep 2025 quarter, the demerger's, and dropped it
  in Dec 2025: flagged; a reconciling row filed at 0 every quarter never is);
- **dropped:** one or more absent and none new (one flag per absent segment);
- **merged:** two or more absent and exactly one new, whose revenue in the latest quarter is within ±25% (`MERGE_BAND`) of
  the absent segments' summed revenue in the previous quarter;
- **renamed** (renamed or redefined): exactly one absent and exactly one new;
- **multi_to_single**, **single_to_multi:** the latest quarter reports one segment and the previous quarter several, or the
  reverse (one flag; the set rule doesn't apply in that quarter);
- **other:** any other change that leaves a segment of the previous four absent (two absent and two new; one absent and two
  new, a split; two absent and one new outside the band). Shown as the segments missing and the segments new, with no kind
  claimed. Decided by Claude under the standing delegation (24 Oct 2026) on 11 Nov 2026, recorded on #409; the alternative
  was to flag nothing and name it in a warning, which would let the page read "none flagged" over a change;
- a single-segment filer is never flagged while it stays single; a new segment alone is not a flag.

The wording (D8, for DD-02): "Not in the latest results; reported in the previous four." Never a motive.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ft.equity import xbrl

PARSER_VERSION = 1
RULE_VERSION = 1
AXIS = "ReportableSegmentsAxis"
PREVIOUS = 4                                # quarters the latest is compared with
HISTORY = 6                                 # quarters read per company: the latest and the five before it
MERGE_BAND = Decimal("0.25")                # merged: the new segment's revenue within ±25% of the absent ones' sum
KINDS = ("dropped", "merged", "renamed", "multi_to_single", "single_to_multi", "other")

FLAG = "IsCompanyReportingMultisegmentOrSingleSegment"
DESC = "DescriptionOfReportableSegment"
SINGLE_DESC = "DescriptionOfSingleSegment"
REVENUE = "SegmentRevenue"


class SegmentError(ValueError):
    """A filing that gives no line-up. `check` is the class (the task's warning check that names it); the message says why."""

    def __init__(self, check: str, message: str):
        super().__init__(message)
        self.check = check


@dataclass(frozen=True)
class Filing:
    """What equity.filing says about the file."""
    basis: str                      # consolidated (the only basis read)
    period_end: date
    fy_start: date | None


@dataclass(frozen=True)
class Line:
    line_no: int
    description: str                # as filed
    revenue: Decimal | None         # rupees, the quarter's SegmentRevenue as filed; None for a single-segment filer


@dataclass
class Lineup:
    period_end: date
    reporting: str                  # multi | single
    lines: list[Line] = field(default_factory=list)
    column: str = "One"             # the context column the quarter's members came from

    def keyed(self) -> dict[str, Line]:
        return {key(ln.description): ln for ln in self.lines}


@dataclass
class Change:
    kind: str
    before: list[str]               # as filed in the previous quarter
    after: list[str]                # as filed in the latest quarter
    revenue_ratio: Decimal | None = None    # merged: the new segment's revenue ÷ the absent ones' in the previous quarter


@dataclass
class Outcome:
    state: str                      # changed | none_flagged | single_segment | not_compared
    changes: list[Change] = field(default_factory=list)
    reason: str = ""


# ----------------------------------------------------------------------------------------------------------------------
_WORD = re.compile(r"[0-9a-z]+")
_ENUM_LEAD = re.compile(r"^\s*(\(\s*[0-9a-z]{1,3}\s*\)|[0-9a-z]{1,3}[.)])\s*")      # "(a) ", "b) ", "1. ", "(ii) "
_ENUM_ANY = re.compile(r"\(\s*[0-9]{1,2}\s*\)")                                     # a footnote number: "(5)"
_RECONCILING = re.compile(r"^\W*(add|less)\b|\bunalloca(ted|ble)\b", re.IGNORECASE)    # "Less: ...", "(c) Corporate/Unallocable"
TYPO_LETTERS = 2                            # one segment when two names differ by at most this many letters ...
TYPO_MIN_LENGTH = 10                        # ... and both are at least this long


def key(description: str) -> str:
    """The spelling-free form of a segment name: case, spacing, punctuation and footnote marks, "&"/"and", an enumerator and
    a word's plural "s" set aside."""
    t = _ENUM_ANY.sub(" ", _ENUM_LEAD.sub("", description.casefold())).replace("&", " and ")
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in _WORD.findall(t))


def is_reconciling(description: str) -> bool:
    """A reconciling row of the segment table filed on the segment axis ("Less: Inter segment revenue")."""
    return bool(_RECONCILING.match(description))


def _distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, stopping once it is above `limit` (returns limit + 1)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def same_but_typo(a: str, b: str) -> bool:
    """Two keys that differ by at most TYPO_LETTERS letters, both at least TYPO_MIN_LENGTH long."""
    return min(len(a), len(b)) >= TYPO_MIN_LENGTH and _distance(a, b, TYPO_LETTERS) <= TYPO_LETTERS


def quarter_end_before(p: date, n: int = 1) -> date:
    """The quarter end `n` quarters before the quarter end `p` (30 Sep 2020, 1 -> 30 Jun 2020)."""
    m = p.month - 3 * n
    y = p.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


def _flag(inst: xbrl.Instance) -> str:
    for f in inst.facts:
        c = inst.contexts.get(f.context)
        if f.name == FLAG and c is not None and not c.dims and f.text.strip():
            return f.text.strip()
    return ""


def _single_description(inst: xbrl.Instance) -> str:
    for f in inst.facts:
        c = inst.contexts.get(f.context)
        if f.name == SINGLE_DESC and c is not None and not c.dims and _clean(f.text):
            return _clean(f.text)
    return "Single segment"


def read_lineup(body: bytes, filing: Filing) -> Lineup:
    """One quarter's line-up from a stored consolidated results filing. Raises SegmentError for a filing that gives none."""
    try:
        inst = xbrl.parse(body)
    except xbrl.XbrlError as e:
        raise SegmentError("unreadable", str(e)) from None
    if inst.basis != filing.basis:
        raise SegmentError("basis", f"the file's basis is {inst.basis}, the filing's {filing.basis}")
    pe = xbrl.to_date(inst.header.get("period_end"))
    if pe != filing.period_end:
        raise SegmentError("period", f"the file's period ends {inst.header.get('period_end') or '(not given)'}, "
                                     f"the filing's {filing.period_end.isoformat()}")
    fs = xbrl.to_date(inst.header.get("fy_start")) or filing.fy_start
    quarter = xbrl.date_columns(inst, pe, fs).quarter                 # 2A-09's dating rule
    flag = _flag(inst).lower()
    if "single" in flag:
        return Lineup(pe, "single", [Line(1, _single_description(inst), None)], column="")
    # the axis's members by column, in contexts dated exactly as the quarter
    by_col: dict[str, dict[str, dict[str, list[xbrl.Fact]]]] = {}
    dated_otherwise = 0
    for f in inst.facts:
        c = inst.contexts.get(f.context)
        if c is None or set(c.dims) != {AXIS} or c.instant is not None or f.name not in (DESC, REVENUE):
            continue
        if f.value is None and not f.text.strip():
            continue
        if (c.start, c.end) != quarter:
            dated_otherwise += 1
            continue
        by_col.setdefault(c.column or "", {}).setdefault(c.dims[AXIS], {}).setdefault(f.name, []).append(f)
    col = "One" if "One" in by_col else (min(by_col) if by_col else None)
    if col is None:
        if dated_otherwise:
            raise SegmentError("undated", f"{dated_otherwise} segment facts, none dated as the quarter "
                                          f"{quarter[0].isoformat()} to {quarter[1].isoformat()}")
        if "multi" in flag:
            raise SegmentError("no_segment_facts", "a multi-segment filing with no segment facts for the quarter")
        raise SegmentError("no_disclosure", "the filing carries no segment disclosure"
                                            + (f" (its flag reads {_flag(inst)!r})" if flag else ""))
    lines: list[Line] = []
    for member in sorted(by_col[col], key=_member_order):
        facts = by_col[col][member]
        descs = {_clean(f.text) for f in facts.get(DESC, []) if _clean(f.text)}
        revs = {f.value for f in facts.get(REVENUE, []) if f.value is not None}
        if len(descs) > 1 or len(revs) > 1:
            raise SegmentError("conflict", f"segment member {member} states its description or revenue twice")
        if not descs:
            if revs:
                raise SegmentError("incomplete", f"segment member {member} has revenue and no description")
            continue
        if is_reconciling(next(iter(descs))):
            continue
        for f in facts.get(REVENUE, []):
            if f.value is not None and (f.unit or "").upper() != "INR":
                raise SegmentError("unit", f"segment member {member}'s revenue is in {f.unit or 'no unit'}, not INR")
        lines.append(Line(len(lines) + 1, descs.pop(), revs.pop() if revs else None))
    if not lines:
        raise SegmentError("no_segment_facts", "the segment members carry no description for the quarter")
    if len({key(ln.description) for ln in lines}) != len(lines):
        raise SegmentError("duplicate", "two segments share a name")
    return Lineup(pe, "multi", lines, column=col)


def _member_order(m: str) -> tuple:
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", m))


# ----------------------------------------------------------------------------------------------------------------------
def compare(latest: Lineup, previous: list[Lineup | None]) -> Outcome:
    """The rule: `latest` against the four quarters before it, newest first (`previous[0]` is the quarter just before).
    A missing quarter (None, or fewer than four) means nothing is compared."""
    if len(previous) < PREVIOUS or any(p is None for p in previous[:PREVIOUS]):
        have = sum(1 for p in previous[:PREVIOUS] if p is not None)
        if latest.reporting == "single":
            return Outcome("single_segment", reason=f"{have} of the {PREVIOUS} earlier quarters read")
        return Outcome("not_compared", reason=f"{have} of the {PREVIOUS} earlier quarters read")
    prev: list[Lineup] = [p for p in previous[:PREVIOUS] if p is not None]
    for i, p in enumerate(prev, 1):
        if p.period_end != quarter_end_before(latest.period_end, i):
            raise ValueError(f"previous[{i - 1}] is {p.period_end}, not the quarter end {i} before {latest.period_end}")
    last_reporting = prev[0].reporting
    if latest.reporting == "single":
        if last_reporting == "single":
            return Outcome("single_segment")
        return Outcome("changed", [Change("multi_to_single", [ln.description for ln in prev[0].lines], [])])
    if last_reporting == "single":
        return Outcome("changed", [Change("single_to_multi", [], [ln.description for ln in latest.lines])])
    now = latest.keyed()
    filed = [set(p.keyed()) if p.reporting == "multi" else set() for p in prev]
    earned = {k for p in prev if p.reporting == "multi" for k, ln in p.keyed().items() if ln.revenue}
    every = set.intersection(*filed) & earned                         # filed in each of the four, with revenue in one
    anyq = set().union(*(p.keyed() for p in prev))                   # filed at all in any of the four
    last = prev[0].keyed()
    absent = [k for k in last if k in every and k not in now]         # the previous quarter's filed order
    new = [k for k, ln in now.items() if k not in anyq and ln.revenue]    # the latest quarter's filed order
    for a in list(absent):                                            # a typo as filed is the same segment
        twin = next((n for n in new if same_but_typo(a, n)), None)
        if twin is not None:
            absent.remove(a)
            new.remove(twin)
    if not absent:
        return Outcome("none_flagged")
    before = [last[k].description for k in absent]
    after = [now[k].description for k in new]
    if not new:
        return Outcome("changed", [Change("dropped", [d], []) for d in before])
    if len(absent) == 1 and len(new) == 1:
        return Outcome("changed", [Change("renamed", before, after)])
    if len(absent) >= 2 and len(new) == 1:
        base = [last[k].revenue for k in absent]
        got = now[new[0]].revenue
        if got is not None and all(b is not None for b in base) and sum(base) > 0:
            ratio = got / sum(base)
            if abs(ratio - 1) <= MERGE_BAND:
                return Outcome("changed", [Change("merged", before, after, ratio)])
    return Outcome("changed", [Change("other", before, after)])
