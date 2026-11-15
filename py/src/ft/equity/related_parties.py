"""The value chain card's related-party rows and segment revenue, read from a stored results filing (Spec 02B §12.4; job
VC-05). The reader is `ft/equity/xbrl.py` (typed rows: its third use, VC-01); this module holds the card's rules and
nothing else. Pure: no database, no network.

What the filings hold (the saved integrated filings in the fixtures repo, read 11 Nov 2026):

- **Related-party rows** are in the second- and fourth-quarter filings only (Regulation 23(9) is half-yearly). Each row is
  one typed member of `RelatedPartyTransactionAxis` with a duration context (the value in the period) and two instants (the
  opening and closing balances, which are not row values and are never read here). The duration context is dated like the
  filing's quarter (1 Jul to 30 Sep), but the company's own PDF heads the table "for the half year ended 30 September", so
  `period_start` and `period_end` are the filing's half-year, not the context's dates (Anand, 29 Oct 2026, 22:49 IST;
  recorded on #190). A Q4 filing is 1 Oct to 31 Mar. The Q4 reading is inferred from the format's wording, not from a
  filing's PDF: the first dev run is its check.
- **PANs are never read** (`CLAUDE.md` §4): the elements `PANOfCounterParty` and
  `PANOfListedEntityOrSubsidiaryEnteringIntoTheTransaction` appear nowhere in this module, and no column holds an identity
  number. A test scans every stored text column.
- **Who entered into a row** (`entered_by`): `company` when the row's `NameOfListedEntityOrSubsidiaryEnteringIntoTheTransaction`
  equals the same file's `NameOfTheCompany` after normalising case, "&" and "and", "Ltd" and "Limited", punctuation and
  spaces; otherwise `subsidiary`. A filing with rows where no row matches is held and named, never guessed.
- **Segments** are members of the explicit `ReportableSegmentsAxis`: `DescriptionOfReportableSegment` and `SegmentRevenue`
  in the year-to-date duration (the fiscal year, in a Q4 filing). `SegmentRevenue` includes inter-segment sales, and the
  filing states the eliminated total (`InterSegmentRevenue`): the segments less that total tie to the filing's revenue
  (`RevenueFromOperations`, or `Income` for a bank, which has no revenue-from-operations line) within 0.1% for M&M, and for
  most Nifty 50 companies. Where they do not (Reliance's segment revenue is 4% above its revenue from operations, ITC's 1%,
  NTPC's standalone 2.5%) the segments are held with the gap named: the field is held back, not the module (Brief §1).
  Decided by Claude under the standing delegation (24 Oct 2026) on 11 Nov 2026; the card's stop condition reads "no element
  ties", which holds for a company only, not for the element.

Values are in full rupees as filed. Nothing is rescaled, summed across bases or guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ft.equity import xbrl

PARSER_VERSION = 1
RPT_AXIS = "RelatedPartyTransactionAxis"
SEG_AXIS = "ReportableSegmentsAxis"
TIE = Decimal("0.001")                     # segments tie to revenue within 0.1% (the card)
# The two transaction types the card's top 5 shows (api.related_party_top matches them case-insensitively)

NAME_ELS = ("NameOfTheCompany", "NameOfBank")             # a bank's filing names itself NameOfBank
COUNTERPARTY = "NameOfCounterParty"
RELATIONSHIP = "RelationshipOfTheCounterpartyWithTheListedEntityOrItsSubsidiary"
TXN_TYPE = "TypeOfRelatedPartyTransaction"
ENTERING = "NameOfListedEntityOrSubsidiaryEnteringIntoTheTransaction"
AMOUNT = "AmountOfRelatedPartyTransactionDuringTheReportingPeriod"
ANY_RPT = "WhetherTheCompanyHasEnteredIntoAnyRelatedPartyTransactionDuringThePeriod"
SEGMENT_FLAG = "IsCompanyReportingMultisegmentOrSingleSegment"
SEGMENT_DESC = "DescriptionOfReportableSegment"
SINGLE_DESC = "DescriptionOfSingleSegment"
SEGMENT_REVENUE = "SegmentRevenue"
INTER_SEGMENT = "InterSegmentRevenue"
REVENUE_TOTALS = ("RevenueFromOperations", "Income")        # what the segments tie to, in order


class RptError(ValueError):
    """A filing that can't give its rows. `check` is the class (it picks the check that names the filing); the message
    says why. A `value` error is blocking (a related-party amount must be numeric; it may be negative); the rest hold the
    filing and are named."""

    def __init__(self, check: str, message: str):
        super().__init__(message)
        self.check = check


@dataclass(frozen=True)
class Filing:
    """What equity.filing says about the file."""
    basis: str                      # consolidated | standalone
    period_end: date
    fy_start: date | None
    fy_end: date | None
    year_end: bool                  # a Q4 filing: its period ends the fiscal year


@dataclass
class RptRow:
    line_no: int
    counterparty: str
    relationship: str | None
    txn_type: str
    entering_party: str
    entered_by: str                 # company | subsidiary
    value: Decimal                  # rupees


@dataclass
class SegRow:
    line_no: int
    reporting: str                  # multi | single
    description: str
    revenue: Decimal | None         # rupees; None for a single-segment filer


@dataclass
class Segments:
    rows: list[SegRow] = field(default_factory=list)
    held: str | None = None         # why a filing that has a disclosure gave no rows
    note: str = ""


@dataclass
class Parsed:
    company: str | None
    basis: str
    period_start: date              # the half-year
    period_end: date
    fy_end: date | None             # the header's fiscal year end, for the "one fiscal year" check
    rows: list[RptRow] = field(default_factory=list)
    declared_none: bool = False     # the filing says it entered into no related-party transaction
    segments: Segments | None = None
    negatives: dict[str, int] = field(default_factory=dict)     # rows filed with a negative amount, by type: stored signed


# ----------------------------------------------------------------------------------------------------------------------
def norm_name(s: str) -> str:
    """Case, "&" and "and", "Ltd" and "Limited", punctuation and spaces normalised away (Spec 02B §12 card, VC-05)."""
    t = s.casefold().replace("&", " and ")
    toks = [("limited" if w in ("ltd", "ltd.") else w) for w in re.sub(r"[^\w\s]", " ", t).split()]
    return "".join(toks)


def half_year(period_end: date) -> tuple[date, date]:
    """The six months ending at a month-end `period_end` (30 Sep -> 1 Apr to 30 Sep; 31 Mar -> 1 Oct to 31 Mar)."""
    m = period_end.month - 5
    y = period_end.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1), period_end


def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


def _fact(inst: xbrl.Instance, name: str):
    """The undimensioned fact called `name`, preferring the quarter's contexts (the first in the file otherwise)."""
    best = None
    for f in inst.facts:
        c = inst.contexts.get(f.context)
        if f.name == name and c is not None and not c.dims and (f.value is not None or f.text):
            if best is None:
                best = f
            if f.context in ("OneD", "OneI"):
                return f
    return best


# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class Rows:
    rows: list[RptRow] = field(default_factory=list)
    declared_none: bool = False
    negatives: dict[str, int] = field(default_factory=dict)


def read_rows(inst: xbrl.Instance, company: str | None) -> Rows:
    """The related-party rows of one filing: one per typed member, values from the duration context only. Raises
    RptError for a filing that can't give its rows.

    **Negative amounts** (reversals and adjustments: about half of the saved filings carry some, mostly under "Any other
    transaction") are stored signed (Anand, 11 Nov 2026, #190; migration 0037 dropped the `>= 0` check). The view sums by
    counterparty, so a reversal nets out exactly; the counts, by type, are returned and named by a warning check."""
    members = [r for r in xbrl.typed_rows(inst, RPT_AXIS) if r.period[0] == "duration"]
    flag = _fact(inst, ANY_RPT)
    out = Rows(declared_none=flag is not None and flag.text.strip().lower() in ("no", "false"))
    if not members:
        return out
    seen: set[str] = set()
    rows: list[RptRow] = []
    for r in members:
        if r.member in seen:
            raise RptError("period", f"related-party member {r.member} has more than one duration context")
        seen.add(r.member)
        if r.conflicts:
            raise RptError("conflict", f"related-party member {r.member} states {min(r.conflicts)} twice")
        amt = r.facts.get(AMOUNT)
        if amt is None or amt.value is None:
            raise RptError("value", f"{r.member} has no numeric {AMOUNT}")
        if (amt.unit or "").upper() != "INR":
            raise RptError("value", f"{r.member} is in {amt.unit or 'no unit'}, not INR")
        who = _clean(r.facts[COUNTERPARTY].text) if COUNTERPARTY in r.facts else ""
        kind = _clean(r.facts[TXN_TYPE].text) if TXN_TYPE in r.facts else ""
        entering = _clean(r.facts[ENTERING].text) if ENTERING in r.facts else ""
        if not who or not kind:
            raise RptError("incomplete", f"{r.member} has no counterparty or no transaction type")
        if not entering:
            raise RptError("incomplete", f"{r.member} names no entering party")
        rel = _clean(r.facts[RELATIONSHIP].text) if RELATIONSHIP in r.facts else ""
        rows.append(RptRow(len(rows) + 1, who, rel or None, kind, entering, "subsidiary", amt.value))
    if company is None:
        raise RptError("unmatched", "the filing names no company, so no row can be matched to it")
    own = norm_name(company)
    for row in rows:
        if norm_name(row.entering_party) == own:
            row.entered_by = "company"
    if not any(r.entered_by == "company" for r in rows):
        raise RptError("unmatched", f"none of {len(rows)} rows names the company ({company!r}) as its entering party")
    for r in rows:
        if r.value < 0:
            out.negatives[r.txn_type] = out.negatives.get(r.txn_type, 0) + 1
    out.rows = rows
    return out


def _total(inst: xbrl.Instance, name: str, start: date, end: date) -> Decimal | None:
    for f in inst.facts:
        c = inst.contexts.get(f.context)
        if f.name == name and f.value is not None and c is not None and not c.dims and c.instant is None \
                and (c.start, c.end) == (start, end):
            return f.value
    return None


def _member_order(m: str) -> tuple:
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", m))


def read_segments(inst: xbrl.Instance, start: date, end: date) -> Segments:
    """The fiscal year's revenue by reportable segment (a Q4 filing's year-to-date contexts). A single-segment filer
    gives one `single` row with its filed description and no revenue. A multi-segment filing's segments must tie to the
    filing's revenue within 0.1% after the inter-segment total is taken out, or they are held with the gap named."""
    flag = _fact(inst, SEGMENT_FLAG)
    kind = (flag.text.strip().lower() if flag is not None else "")
    if not kind:
        return Segments(held="the filing carries no segment disclosure")
    if "single" in kind:
        d = _fact(inst, SINGLE_DESC)
        return Segments([SegRow(1, "single", _clean(d.text) if d is not None and _clean(d.text) else "Single segment", None)],
                        note="single segment, as filed")
    if "multi" not in kind:
        return Segments(held=f"the segment flag reads {flag.text.strip()!r}")
    by_member: dict[str, dict[str, xbrl.Fact]] = {}
    for f in inst.facts:
        c = inst.contexts.get(f.context)
        if c is None or set(c.dims) != {SEG_AXIS} or c.instant is not None or (c.start, c.end) != (start, end):
            continue
        if f.name in (SEGMENT_REVENUE, SEGMENT_DESC) and (f.value is not None or f.text):
            by_member.setdefault(c.dims[SEG_AXIS], {})[f.name] = f
    segs = [(m, d) for m, d in sorted(by_member.items(), key=lambda kv: _member_order(kv[0])) if SEGMENT_REVENUE in d]
    if not segs:
        return Segments(held="a multi-segment filing with no segment revenue facts for the fiscal year")
    rows: list[SegRow] = []
    for m, d in segs:
        desc = _clean(d[SEGMENT_DESC].text) if SEGMENT_DESC in d else ""
        if not desc:
            return Segments(held=f"segment {m} has no description")
        rows.append(SegRow(len(rows) + 1, "multi", desc, d[SEGMENT_REVENUE].value))
    if len({r.description for r in rows}) != len(rows):
        return Segments(held="two segments share a description")
    gross = sum((r.revenue for r in rows), Decimal(0))
    inter = _total(inst, INTER_SEGMENT, start, end) or Decimal(0)
    for name in REVENUE_TOTALS:
        ref = _total(inst, name, start, end)
        if ref is None:
            continue
        net = gross - inter
        if ref != 0 and abs(net - ref) <= TIE * abs(ref):
            return Segments(rows, note=f"segments less inter-segment revenue tie to {name} within 0.1%")
        if ref != 0 and abs(gross - ref) <= TIE * abs(ref):          # Eternal: the segments are already external revenue
            return Segments(rows, note=f"segments tie to {name} within 0.1% (inter-segment revenue is not in them)")
        gap = (net - ref) / ref * 100 if ref else Decimal(0)
        return Segments(held=f"the segments less inter-segment revenue differ from {name} by {gap:+.2f}%")
    return Segments(held="the filing has no revenue total to tie the segments to")


# ----------------------------------------------------------------------------------------------------------------------
def parse_filing(body: bytes, filing: Filing) -> Parsed:
    """Read one stored results filing. Raises RptError for a filing that can't be read (named by the task's checks)."""
    try:
        inst = xbrl.parse(body)
    except xbrl.XbrlError as e:
        raise RptError("unreadable", str(e)) from None
    if inst.basis != filing.basis:
        raise RptError("basis", f"the file's basis is {inst.basis}, the filing's {filing.basis}")
    pe = xbrl.to_date(inst.header.get("period_end"))
    if pe is not None and pe != filing.period_end:
        raise RptError("period", f"the file's period ends {pe.isoformat()}, the filing's {filing.period_end.isoformat()}")
    company = None
    for el in NAME_ELS:
        name = _fact(inst, el)
        if name is not None and _clean(name.text):
            company = _clean(name.text)
            break
    start, end = half_year(filing.period_end)
    fy_end = xbrl.to_date(inst.header.get("fy_end")) or filing.fy_end
    got = read_rows(inst, company)
    out = Parsed(company, filing.basis, start, end, fy_end, got.rows, got.declared_none, None, got.negatives)
    if filing.year_end and filing.basis == "consolidated":
        fy_start = filing.fy_start or xbrl.to_date(inst.header.get("fy_start"))
        out.segments = read_segments(inst, fy_start, filing.period_end) if fy_start else \
            Segments(held="the filing has no fiscal year start")
    return out
