"""The results XBRL reader (Spec 02A §5.4; job 2A-09), ported from the audit kit's `kit/xbrl.py` (contexts, facts, the
header, the basis and `redate_result_columns`), for the plain instances the filings task stores (`…_WEB.xml`; the
inline pages are duplicates and aren't stored, Spec 02A §5.3).

What the files hold (Data Audit 02a findings 2–3; the saved filings in the fixtures repo, 1,375 files, read 29 Oct 2026):

- **Contexts are named by the column of SEBI's results format:** `One` = the quarter, `Four` = the year to date
  (`OneD`, `FourD` for durations, `OneI` for the instant at the period end; the dimensioned ones add a suffix,
  `FourReportableSegmentRevenue01D`). No file carries the comparative columns (Two, Three, Five, Six).
- **Files before the Integrated Filing format (to the Dec 2024 quarter) date every column with the quarter's dates**,
  so a Q4 file's year figures sit in a `Four` context dated like the quarter (RELIANCE Q4 FY24's `FourD`, revenue
  ₹9,14,472 cr, is dated 1 Jan–31 Mar 2024). Integrated Filing files (2025 on) date them correctly, so re-dating must
  leave them unchanged (`Dating.changed` is empty for them; the statements check says so).
- **Files to about early 2023 reference `OneD`, `FourD` and `OneI` without defining them** (found 29 Oct 2026 in
  622 of the saved files, every company that filed before 2023: the instance has the segment and expense contexts
  but not the three main ones). The kit skipped facts in undefined contexts, so it read nothing from those files'
  statements. The column says the period here too, so `date_columns` dates them from the header: `OneD` the
  quarter, `FourD` the year to date, `OneI` the period end (`Dating.synthesized`).
- Values are in **full rupees** (the rounding level, "Crores" or "Lakhs", is only the precision): a plain instance
  states every value in its unit, with `decimals` giving the precision. Facts are kept as `Decimal` (as filed, no
  float rounding).
- The basis comes from the header (`NatureOfReportStandaloneConsolidated`); the kit's dimension hints are the fallback
  (no saved file needs them).

The parser refuses DTDs, entities and the network (the same settings as `ft/equity/filings.py`).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from lxml import etree

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
COLUMNS = ("One", "Two", "Three", "Four", "Five", "Six")
COLUMN_RE = re.compile(r"^(One|Two|Three|Four|Five|Six)(?=[A-Z_]|$)")
MAIN = re.compile(r"^(One|Four)(D|I)$")       # the main contexts, the ones old files reference without defining

# The header facts, in ranked order: the first name present wins (the kit's 19 Oct fix: a filing carries both
# DateOfStartOfReportingPeriod and DateOfStartOfFinancialYear, and document order must not decide). Unlike the kit,
# the reporting period's start never falls back to the financial year's: the old bank files have no reporting-period
# start, and the kit's fallback made their quarter look like the year to date.
HEADER = {
    "nature": ("NatureOfReportStandaloneConsolidated", "NatureOfReport", "ConsolidatedOrStandalone", "TypeOfReport"),
    "audited": ("WhetherResultsAreAuditedOrUnaudited", "AuditedOrUnaudited", "NatureOfResults"),
    "period_start": ("DateOfStartOfReportingPeriod",),
    "period_end": ("DateOfEndOfReportingPeriod",),
    "fy_start": ("DateOfStartOfFinancialYear",),
    "fy_end": ("DateOfEndOfFinancialYear",),
    "symbol": ("Symbol", "NSESymbol"),
    "isin": ("ISIN", "Isin"),
    "rounding": ("LevelOfRoundingUsedInFinancialStatements", "LevelOfRounding"),
    "currency": ("DescriptionOfPresentationCurrency", "PresentationCurrency", "ReportingCurrency"),
}
DATE_KEYS = ("period_start", "period_end", "fy_start", "fy_end")


class XbrlError(ValueError):
    """An instance that can't be read; the message says why (it becomes the filing's parse note)."""


@dataclass
class Context:
    id: str
    start: date | None = None
    end: date | None = None
    instant: date | None = None
    dims: dict[str, str] = field(default_factory=dict)        # axis local name -> member local name

    @property
    def column(self) -> str | None:
        m = COLUMN_RE.match(self.id)
        return m.group(1) if m else None


@dataclass(frozen=True)
class Fact:
    name: str                      # local name
    context: str
    unit: str | None
    decimals: str | None
    value: Decimal | None          # a numeric fact's value, as filed; None for text facts and nil values
    text: str


@dataclass
class Dating:
    """What `date_columns` did: contexts re-dated (pre-2025 files) and contexts dated from the header (old files that
    don't define their main contexts), by column."""
    quarter: tuple[date, date]
    year_to_date: tuple[date, date] | None
    changed: dict[str, int] = field(default_factory=dict)
    synthesized: list[str] = field(default_factory=list)


@dataclass
class Instance:
    facts: list[Fact]
    contexts: dict[str, Context]
    header: dict[str, str]
    basis: str | None              # consolidated | standalone | None
    undefined: dict[str, int]      # context ids facts point at but the file doesn't define -> facts
    dating: Dating | None = None


def to_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


def to_decimal(text: str) -> Decimal | None:
    """A numeric fact's value. A plain instance holds xsd:decimal text; anything else is None (counted by the caller)."""
    t = text.strip()
    if not t:
        return None
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return v if v.is_finite() else None


def _local(qname: str) -> str:
    """'{ns}Name' or 'prefix:Name' -> 'Name'."""
    if qname.startswith("{"):
        return qname.split("}", 1)[1]
    return qname.split(":", 1)[1] if ":" in qname else qname


PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def parse(body: bytes) -> Instance:
    """Read a plain XBRL instance. Refuses anything that isn't XML with an xbrli:xbrl root, and an instance with no
    facts."""
    try:
        root = etree.fromstring(body, PARSER)
    except etree.XMLSyntaxError as e:
        raise XbrlError(f"not XML ({str(e)[:80]})") from None
    if etree.QName(root).namespace != XBRLI or etree.QName(root).localname != "xbrl":
        raise XbrlError(f"the root is {etree.QName(root).localname}, not an XBRL instance")
    contexts: dict[str, Context] = {}
    for el in root.iter(f"{{{XBRLI}}}context"):
        c = Context(el.get("id", ""))
        for sub in el.iter():
            if not isinstance(sub.tag, str):
                continue
            n = etree.QName(sub).localname
            if n == "startDate":
                c.start = to_date(sub.text)
            elif n == "endDate":
                c.end = to_date(sub.text)
            elif n == "instant":
                c.instant = to_date(sub.text)
            elif n in ("explicitMember", "typedMember"):
                axis = _local(sub.get("dimension", ""))
                member = (sub.text or "").strip()
                c.dims[axis] = _local(member) if member else "".join(sub.itertext()).strip()
        contexts[c.id] = c
    facts: list[Fact] = []
    undefined: dict[str, int] = {}
    for el in root:
        if not isinstance(el.tag, str):
            continue
        ref = el.get("contextRef")
        if ref is None:
            continue
        text = "".join(el.itertext()).strip()
        unit = el.get("unitRef")
        numeric = unit is not None or el.get("decimals") is not None
        value = None if (el.get(XSI_NIL) == "true" or not numeric) else to_decimal(text)
        facts.append(Fact(etree.QName(el).localname, ref, unit, el.get("decimals"), value, text))
        if ref not in contexts:
            undefined[ref] = undefined.get(ref, 0) + 1
    if not facts:
        raise XbrlError("an XBRL instance with no facts")
    inst = Instance(facts, contexts, {}, None, undefined)
    inst.header = header_of(inst)
    inst.basis = basis_of(inst)
    return inst


def header_of(inst: Instance) -> dict[str, str]:
    """The header facts (as text; dates as ISO), each from its first ranked name present, preferring a fact whose
    context has no dimensions."""
    by_name: dict[str, list[Fact]] = {}
    for f in inst.facts:
        by_name.setdefault(f.name, []).append(f)
    out: dict[str, str] = {}
    for key, names in HEADER.items():
        for n in names:
            fs = by_name.get(n)
            if fs:
                fs = sorted(fs, key=lambda f: len(inst.contexts[f.context].dims) if f.context in inst.contexts else 0)
                out[key] = fs[0].text
                break
    for k in DATE_KEYS:
        if k in out:
            d = to_date(out[k])
            out[k] = d.isoformat() if d else out[k]
    return out


def basis_of(inst: Instance) -> str | None:
    """consolidated | standalone, from the header's nature of report (the kit's rule), else from the dimension
    members (the kit's dimension hints)."""
    nature = inst.header.get("nature", "").lower()
    if "standalone" in nature or "non-consol" in nature or "nonconsol" in nature:
        return "standalone"
    if "consol" in nature:
        return "consolidated"
    members = {m.lower() for c in inst.contexts.values() for m in c.dims.values()}
    if any("consolidated" in m for m in members) and not any("standalone" in m for m in members):
        return "consolidated"
    if any("standalone" in m for m in members):
        return "standalone"
    return None


def quarter_of(period_end: date) -> tuple[date, date]:
    """The three months ending at a month-end `period_end`."""
    m = period_end.month - 2
    y = period_end.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1), period_end


def months_between(start: date, end: date) -> int | None:
    """Whole months from `start` (a first of the month) to `end` (a month-end), or None if they aren't."""
    if start.day != 1 or end.day != calendar.monthrange(end.year, end.month)[1]:
        return None
    return (end.year - start.year) * 12 + end.month - start.month + 1


def date_columns(inst: Instance, period_end: date, fy_start: date | None) -> Dating:
    """Date the One and Four columns by what they are (the kit's `redate_result_columns`, keyed on the quarter
    derived from the period end rather than the header's reporting-period start, which the old bank files lack):

    - a defined `Four…` duration dated like the quarter is re-dated to the year to date (pre-2025 files);
    - `OneD`, `FourD` and `OneI` referenced but not defined are created: the quarter, the year to date, the period end.

    Records what changed. Other contexts are left as filed."""
    q = quarter_of(period_end)
    ytd = (fy_start, period_end) if fy_start and fy_start <= q[0] else None
    d = Dating(q, ytd)
    for c in inst.contexts.values():
        if c.column != "Four" or c.instant is not None or (c.start, c.end) != q or ytd is None or ytd == q:
            continue
        c.start, c.end = ytd
        d.changed["Four"] = d.changed.get("Four", 0) + 1
    for ref in sorted(inst.undefined):
        m = MAIN.match(ref)
        if not m:
            continue
        col, kind = m.groups()
        if kind == "I":
            inst.contexts[ref] = Context(ref, instant=period_end)
        elif col == "One":
            inst.contexts[ref] = Context(ref, start=q[0], end=q[1])
        elif ytd is not None:
            inst.contexts[ref] = Context(ref, start=ytd[0], end=ytd[1])
        else:
            continue
        d.synthesized.append(ref)
    for ref in d.synthesized:
        inst.undefined.pop(ref, None)
    inst.dating = d
    return d


def period_kind(start: date, end: date) -> str | None:
    """quarter | half_year | nine_months | year, by whole months; None for anything else."""
    return {3: "quarter", 6: "half_year", 9: "nine_months", 12: "year"}.get(months_between(start, end) or 0)


def facts_on(inst: Instance, name: str, axis: str, members: tuple[str, ...] | str) -> list[Fact]:
    """The numeric facts called `name` whose context has exactly one dimension, `axis`, set to one of `members` (local
    names, case-insensitive). The second use of the reader (the shareholding pattern, job 2A-10): a fact is found by its
    element and its dimension member, not by the column of a results context. Facts with no value are left out."""
    wanted = {m.lower() for m in ([members] if isinstance(members, str) else members)}
    out = []
    for f in inst.facts:
        c = inst.contexts.get(f.context)
        if f.name == name and f.value is not None and c is not None and len(c.dims) == 1 \
                and c.dims.get(axis, "").lower() in wanted:
            out.append(f)
    return out
