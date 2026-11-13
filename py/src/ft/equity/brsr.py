"""The BRSR reader and rules for the value chain card (Spec 02B §12.4; Data Audit 08 §3.2–§3.5, §5–§7; job VC-04, #189).

Pure functions and small value types: the exchanges' list replies, one filing's identity check and values, and the
restatement comparison. The task (`ft/jobs/tasks/equity_brsr.py`) does the fetching and the writing. The XBRL contexts and
facts come from `ft/equity/xbrl.py` (VC-01); the field list is `config/equity/brsr_fields.yaml`. The audit kit's
`kit/brsr.py` is the model for the list shapes and the identity check (ported, never imported: CLAUDE.md §8).

Rules kept here, each from the spec:

- **Facts:** the current-year facts without dimensions; the `Percentage…` and `NumberOf…` elements only, never `Amount…`.
  A share is the filed fraction (0.7933 = 79.33%) and is never rescaled; a share outside 0–1 is `held` ("outside 0–100% as
  filed"); a count must be a whole number >= 0; two different values for one element hold that field.
- **Identity:** the file's `NSESymbol` or `ScripCode` (FY 2025-26 taxonomy on), else its company name, must match the
  company; its fiscal year's start and end years must match the listing. Otherwise the filing is `held`.
- **Format:** every card field needs an entry point of 2024-04-30 or later; an older filing is stored and not shown. An entry
  point the config doesn't know holds every value (a W check names it): only a new mapping lets its values show (§12.10).
- **Restatements:** year t + 1's prior-year comparative for a field is stored on year t's row; a difference above 0.5 pp
  for a share, or any difference for a count, sets `restated`. Year t's own value is never replaced (D3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ft.equity import xbrl as x

VERSION = "brsr-1"                                   # the reader's version, stored on each filing (parser_version)
INDIA = ZoneInfo("Asia/Kolkata")
CONFIG = Path(__file__).resolve().parents[3] / "config" / "equity" / "brsr_fields.yaml"

NSE_HOME = "https://www.nseindia.com"
NSE_LIST_PATH = "/api/corporate-bussiness-sustainabilitiy"          # NSE's own spelling
NSE_REFERER = NSE_HOME + "/companies-listing/corporate-filings-bussiness-sustainabilitiy-reports"
BSE_LIST_URL = "https://api.bseindia.com/BseIndiaAPI/api/XbrlBrsrDetails/w"
BSE_FILES = "https://www.bseindia.com"
FIRST_FY = 2023                                      # FY 2023-24 on (Data Audit 08 D1)
BACKFILL_START = date(2024, 4, 1)                    # the backfill's first filing date (FY 2023-24's filings)
SHARE_TOLERANCE = Decimal("0.005")                   # 0.5 pp as a fraction

PRODUCT_AXIS = "ProductsOrServicesSoldByTheEntityAccountingForNinetyPercentOfTheTurnoverAxis"
GROUP_AXIS = "HoldingSubsidiaryAssociateCompaniesAndJointVenturesAxis"
PRODUCT_NAME, PRODUCT_NIC, PRODUCT_SHARE = (
    "ProductOrServiceSoldByTheEntity", "NICCodeOfProductOrServiceSoldByTheEntity",
    "PercentageOfTotalTurnoverForProductOrServiceSold")
GROUP_NAME, GROUP_RELATION, GROUP_SHARE = (
    "NameOfTheHoldingOrSubsidiaryAssociateCompaniesOrJointVentures", "CategoryOfCompany", "PercentageOfSharesHeldByListedEntity")
# Principle 1, EI 9 (openness of business): the filing's own assurance flag and its sub-type (Data Audit 08 D7).
ASSURED_ELEMENT = ("WhetherDetailsOfConcentrationOfPurchasesAndSalesWithTradingHousesDealersAndRelatedPartiesAlong"
                   "WithLoansAndAdvancesAndInvestmentsWithRelatedPartiesIsAssuredByAssurer")
ASSURANCE_ELEMENT = ("AssuranceSubTypeForDetailsOfConcentrationOfPurchasesAndSalesWithTradingHousesDealersAndRelated"
                     "PartiesAlongWithLoansAndAdvancesAndInvestmentsWithRelatedParties")

_NAME_STOP = {"limited", "ltd", "company", "corporation", "private", "public"}


class BrsrError(ValueError):
    """A file that can't be used: `kind` is `not_xbrl` (not an instance, or not a BRSR) or `no_format` (no entry point)."""

    def __init__(self, msg: str, kind: str = "not_xbrl"):
        super().__init__(msg)
        self.kind = kind


# ---------------------------------------------------------------------------------------------------------------------
# the field list
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldSpec:
    key: str
    element: str
    item_ref: str
    kind: str                                         # share | count
    parent: str | None
    valid_from: date


@dataclass(frozen=True)
class Config:
    fields: tuple[FieldSpec, ...]
    shown: frozenset[date]                            # entry points the card shows
    not_shown: frozenset[date]                        # entry points that are stored and not shown (the 2021 format)


def load_config(path: Path | None = None) -> Config:
    raw = yaml.safe_load((path or CONFIG).read_text("utf-8"))
    eps = raw["entry_points"]
    return Config(
        tuple(FieldSpec(f["key"], f["element"], f["item_ref"], f["kind"], f.get("parent"), date.fromisoformat(str(f["valid_from"])))
              for f in raw["fields"]),
        frozenset(date.fromisoformat(str(d)) for d in eps["shown"]),
        frozenset(date.fromisoformat(str(d)) for d in eps["stored_not_shown"]))


# ---------------------------------------------------------------------------------------------------------------------
# the exchanges' lists
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Listed:
    """One filing in an exchange's list (an NSE row, or one BSE version)."""
    exchange: str                                     # NSE | BSE
    fy_from: int
    fy_to: int
    filed: datetime                                   # the filing's time, India time, aware
    revised: datetime | None
    url: str                                          # the XBRL file as listed (BSE: possibly .html)
    company: str | None = None                        # the list's own company name
    status: str | None = None                         # BSE: New | Revised
    reason: str | None = None                         # BSE: the resubmission reason

    @property
    def stamp(self) -> datetime:
        """The version's time: the revision's, else the filing's."""
        return self.revised or self.filed

    @property
    def file_name(self) -> str:
        return file_name(self.url)


def file_name(url: str) -> str:
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def clean_url(u) -> str | None:
    """A listing's link, or None for the placeholders the exchanges use (empty, `-`, `.../-`, `.../null`)."""
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u or u == "-" or u.endswith(("/-", "/null")):
        return None
    return u


_DT_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y")


def parse_time(s) -> datetime | None:
    """An exchange's date or time stamp (India time) as an aware datetime; a date alone is midnight."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s or s == "-":
        return None
    for f in _DT_FORMATS:
        try:
            t = datetime.strptime(s.title() if "%b" in f else s, f)
        except ValueError:
            continue
        return t.replace(tzinfo=INDIA)
    return None


def rows_of(reply, key: str) -> list[dict]:
    """The row list of a reply: the list itself, or the list under `key` (`data` for NSE, `Table` for BSE)."""
    if isinstance(reply, list):
        return [r for r in reply if isinstance(r, dict)]
    if isinstance(reply, dict) and isinstance(reply.get(key), list):
        return [r for r in reply[key] if isinstance(r, dict)]
    return []


@dataclass
class Listing:
    filings: list[Listed] = field(default_factory=list)
    no_link: int = 0
    unreadable: list[str] = field(default_factory=list)


def parse_nse(rows: list[dict]) -> Listing:
    """NSE's BRSR list (`/api/corporate-bussiness-sustainabilitiy`): one row a year, the latest version. A row without an
    XBRL link is counted; a row whose year or date can't be read is named."""
    out = Listing()
    for r in rows:
        url = clean_url(r.get("xbrlFile"))
        if url is None:
            out.no_link += 1
            continue
        try:
            fy_from, fy_to = int(r.get("fyFrom")), int(r.get("fyTo"))
        except (TypeError, ValueError):
            out.unreadable.append(f"{r.get('symbol') or '?'}: fiscal year unreadable")
            continue
        filed = parse_time(r.get("submissionDate"))
        if filed is None:
            out.unreadable.append(f"{r.get('symbol') or '?'} FY {fy_from}: submission date unreadable")
            continue
        out.filings.append(Listed("NSE", fy_from, fy_to, filed, parse_time(r.get("revisionDate")), url, r.get("companyName")))
    return out


_BSE_YEAR = re.compile(r"\s*(\d{4})\s*-\s*(\d{4})\s*$")


def parse_bse(rows: list[dict]) -> Listing:
    """BSE's BRSR list (`XbrlBrsrDetails/w`): every version, with its time and reason. The link is relative to bseindia.com."""
    out = Listing()
    for r in rows:
        rel = clean_url(r.get("xbrlurl") or r.get("Fld_XMLName"))
        if rel is None:
            out.no_link += 1
            continue
        m = _BSE_YEAR.match(str(r.get("yr") or ""))
        filed = parse_time(r.get("filing_date_time"))
        if not m or filed is None:
            out.unreadable.append("a BSE row's year or filing time is unreadable")
            continue
        url = BSE_FILES + rel if rel.startswith("/") else rel
        out.filings.append(Listed("BSE", int(m.group(1)), int(m.group(2)), filed, parse_time(r.get("revised_date_time")), url,
                                  None, r.get("status"), ((r.get("Fld_ResubReason") or "")[:300] or None)))
    return out


def latest_by_year(filings: list[Listed]) -> dict[int, Listed]:
    """The latest version of each fiscal year (by revision time, else filing time); the first listed wins a tie."""
    best: dict[int, Listed] = {}
    for f in filings:
        if f.fy_from not in best or f.stamp > best[f.fy_from].stamp:
            best[f.fy_from] = f
    return best


def xml_variants(url: str) -> list[str]:
    """BSE lists recent years as `.html` (inline XBRL); the plain `.xml` of the same name is served too and is the only one
    the reader takes. A `.html` link has one variant: its `.xml`. Anything else is its own."""
    if url.lower().endswith(".html"):
        return [url[:-5] + ".xml"]
    return [url]


# ---------------------------------------------------------------------------------------------------------------------
# one filing
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CompanyId:
    """Who the filing must belong to: the company's names and codes, every symbol it has had."""
    name: str
    symbols: frozenset[str]
    scrip: str | None
    listing_name: str | None = None                   # the exchange list's own name for it (ZOMATO era files)


@dataclass(frozen=True)
class Value:
    key: str
    value: Decimal | None
    kind: str
    element: str
    item_ref: str
    status: str                                       # ok | held
    held_reason: str | None = None


@dataclass(frozen=True)
class Line:
    description: str
    nic_code: str | None
    share: Decimal | None


@dataclass(frozen=True)
class GroupLine:
    name: str
    relation: str | None
    share_held: Decimal | None


@dataclass
class Filing:
    fy_start: date
    fy_end: date
    months: int
    entry_point: date
    boundary: str | None                              # standalone | consolidated | None (not recognised)
    identity: str                                     # how the company was matched, for the notes
    identity_ok: bool
    identity_note: str
    values: list[Value] = field(default_factory=list)
    prior: dict[str, Decimal] = field(default_factory=dict)        # field key -> the prior-year comparative, as filed
    products: list[Line] = field(default_factory=list)
    group: list[GroupLine] = field(default_factory=list)
    openness_assured: bool | None = None
    openness_assurance: str | None = None
    format_status: str = "shown"                      # shown | not_shown (the 2021 format) | unknown (a new entry point)
    notes: list[str] = field(default_factory=list)


def words(name: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (name or "").lower()) if w not in _NAME_STOP}


def check_identity(header: dict[str, str], company: CompanyId) -> tuple[bool, str]:
    """The file's own header names the company: its NSE symbol (any symbol the company has had), else its scrip code, else
    its name sharing a word of 4+ letters with the master's or the listing's name."""
    sym = header.get("nse_symbol", "").strip().upper()
    code = header.get("scrip_code", "").strip()
    if sym:
        if sym in {s.upper() for s in company.symbols}:
            return True, f"NSESymbol {sym}"
        if code and company.scrip and code == str(company.scrip):
            return True, f"ScripCode {code}"
        return False, f"NSESymbol {sym} is not this company's"
    if code and company.scrip:
        return (code == str(company.scrip)), f"ScripCode {code}" + ("" if code == str(company.scrip) else " is not this company's")
    name = header.get("name", "")
    if not name.strip():
        return False, "the file names no company"
    ours = words(company.name) | words(company.listing_name)
    if words(name) & ours:
        return True, "company name"
    return False, "the file's company name matches nothing we know"


def _boundary(text: str | None) -> str | None:
    t = (text or "").lower()
    if "consolidated" in t and "standalone" not in t:
        return "consolidated"
    if "standalone" in t and "consolidated" not in t:
        return "standalone"
    return None


def _months(start: date, end: date) -> int:
    m = x.months_between(start, end)
    if m is None:
        m = round(((end - start).days + 1) / 30.4375)
    return max(1, min(24, m))


def _whole(v: Decimal) -> bool:
    return v == v.to_integral_value()


def read_value(spec: FieldSpec, fact: x.Fact | None, conflict: list[x.Fact] | None) -> Value | None:
    """One card field of the current year: None when the filing doesn't carry it (absent)."""
    if conflict:
        return Value(spec.key, None, spec.kind, spec.element, spec.item_ref, "held", "two different values in this filing")
    if fact is None or fact.value is None:
        return None
    v = fact.value
    if spec.kind == "share" and not (0 <= v <= 1):
        return Value(spec.key, v, spec.kind, spec.element, spec.item_ref, "held", "outside 0–100% as filed")
    if spec.kind == "count" and not (v >= 0 and _whole(v)):
        return Value(spec.key, v, spec.kind, spec.element, spec.item_ref, "held", "not a whole number of at least 0 as filed")
    return Value(spec.key, v, spec.kind, spec.element, spec.item_ref, "ok")


def read_filing(body: bytes, company: CompanyId, fy_from: int | None, fy_to: int | None, cfg: Config) -> Filing:
    """Read one BRSR instance for `company` as listed for the fiscal year `fy_from`–`fy_to`. Raises BrsrError for a file
    that isn't a BRSR instance or names no format. Identity and format problems don't raise: they are on the result, so
    the task can fall back to the other exchange and, failing that, store the filing held."""
    try:
        inst = x.parse(body)
        view = x.brsr_view(inst)
    except x.XbrlError as e:
        raise BrsrError(str(e)) from None
    if not view.entry_point:
        raise BrsrError("no taxonomy entry point (schemaRef) in the file", "no_format")
    entry = date.fromisoformat(view.entry_point)
    start, end = view.fy
    ok, why = check_identity(view.header, company)
    year_ok = fy_from is not None and fy_to is not None and start.year == fy_from and end.year == fy_to
    if not ok:
        note = why
    elif not year_ok:
        note = f"the file's fiscal year {start.isoformat()}..{end.isoformat()} is not the listing's {fy_from}-{fy_to}"
    else:
        note = why
    f = Filing(start, end, _months(start, end), entry, _boundary(view.header.get("boundary")), why, ok and year_ok, note)
    f.format_status = "shown" if entry in cfg.shown else ("not_shown" if entry in cfg.not_shown or entry < min(cfg.shown) else "unknown")
    if f.format_status == "unknown":
        f.notes.append(f"entry point {entry.isoformat()} is not in brsr_fields.yaml: every value held")

    # the card's fields: the current year's undimensioned facts (a share is the filed fraction)
    for spec in cfg.fields:
        if f.format_status == "not_shown" or (f.format_status == "shown" and entry < spec.valid_from):
            continue
        val = read_value(spec, view.current.get(spec.element), view.conflicts.get(spec.element))
        if val is None:
            continue
        if f.format_status == "unknown":
            val = Value(val.key, val.value, val.kind, val.element, val.item_ref, "held",
                        f"unknown taxonomy entry point {entry.isoformat()}")
        f.values.append(val)
        pf = view.prior.get(spec.element)
        if pf is not None and pf.value is not None and spec.element not in view.prior_conflicts:
            f.prior[spec.key] = pf.value

    if f.format_status != "not_shown":
        # Principle 1 EI 9's own assurance flag (D7); a flag that is not a clear true or false stays unread
        a = view.current.get(ASSURED_ELEMENT)
        if a is not None and a.text.strip().lower() in ("true", "false"):
            f.openness_assured = a.text.strip().lower() == "true"
            sub = view.current.get(ASSURANCE_ELEMENT)
            kind = (sub.text.strip().lower() if sub is not None else "")
            f.openness_assurance = kind if f.openness_assured and kind in ("reasonable", "limited") else None
        for row in x.typed_rows(inst, PRODUCT_AXIS):
            if row.period != ("duration", start, end) or row.conflicts:
                continue
            desc = row.facts.get(PRODUCT_NAME)
            if desc is None or not desc.text.strip():
                continue
            nic, share = row.facts.get(PRODUCT_NIC), row.facts.get(PRODUCT_SHARE)
            sv = share.value if share is not None and share.value is not None and 0 <= share.value <= 1 else None
            f.products.append(Line(desc.text.strip(), nic.text.strip() if nic is not None and nic.text.strip() else None, sv))
        for row in x.typed_rows(inst, GROUP_AXIS):
            if row.period != ("duration", start, end) or row.conflicts:
                continue
            nm = row.facts.get(GROUP_NAME)
            if nm is None or not nm.text.strip():
                continue
            rel, share = row.facts.get(GROUP_RELATION), row.facts.get(GROUP_SHARE)
            sv = share.value if share is not None and share.value is not None and 0 <= share.value <= 1 else None
            f.group.append(GroupLine(nm.text.strip(), rel.text.strip() if rel is not None and rel.text.strip() else None, sv))
    return f


# ---------------------------------------------------------------------------------------------------------------------
# labels, restatements, links
# ---------------------------------------------------------------------------------------------------------------------
def fy_label(start: date, end: date) -> str:
    """`FY 2025-26`: the filing's own start and end years (a 15-month year starting Jan 2023 and ending Mar 2024 is FY 2023-24)."""
    return f"FY {start.year}-{end.year % 100:02d}"


def event_slug(company_slug: str, start: date, end: date, version: int) -> str:
    return f"{company_slug}-brsr-fy{start.year}-{end.year % 100:02d}" + ("" if version == 1 else f"-r{version}")


def is_restated(kind: str, value: Decimal, comparative: Decimal) -> bool:
    """A difference above 0.5 pp for a share, or any difference for a count."""
    return abs(value - comparative) > (SHARE_TOLERANCE if kind == "share" else Decimal(0))


def list_url(exchange: str, bse_code: str | None, slug: str) -> str:
    """The exchange's BRSR list for the company (Data Audit 08 red flag 10: not the PDF attachment). BSE's company BRSR page
    is keyed by the scrip code (`/stock-share-price/<slug>/<code>/brsr`; the slug is cosmetic); NSE's is its BRSR filings
    page, where the company is searched. Neither pattern could be opened from the build workspace: VC-06's hand check
    opens one (decided by Claude, standing delegation, 11 Nov 2026)."""
    if exchange == "BSE" and bse_code:
        return f"{BSE_FILES}/stock-share-price/{slug}/{bse_code}/brsr"
    return NSE_REFERER


def utc(t: datetime) -> datetime:
    return t.astimezone(UTC)
