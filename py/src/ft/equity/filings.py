"""Results filings: reading NSE's two listing endpoints, telling a filing's format from its file, and naming its event
(Spec 02A §5.3; job 2A-07; ported from the audit kit's `kit/nse.py` and the header logic of `kit/xbrl.py`).

Two endpoints list a company's results filings (Data Audit 02a findings 1, 6, 7). Both are undocumented and both are
accepted (red flag 4 of Data Audit 02): every reply is stored as a raw file by the task before it is read here.

- `/api/corporates-financial-results?index=equities&period=Quarterly&symbol=…` lists the quarters up to the one ended
  Dec 2024. A row carries `xbrl` (the instance link, or `…/corporate/xbrl/-` when NSE lists a filing without a file),
  `consolidated` ("Consolidated" or "Non-Consolidated"), `audited`, `fromDate`/`toDate` (the period),
  `exchdisstime` (the exchange's dissemination time, India time), `financialYear` and `cumulative`.
- `/api/integrated-filing-results?type=Integrated Filing- Financials&index=equities&symbol=…&page=n&size=20` lists the
  filings from the integrated format (SEBI's circular of 31 Dec 2024). A row carries `xbrl` (the instance) and `ixbrl`
  (an inline page of the same filing, a duplicate we do not store), `qe_Date`, `consolidated`, `audited`,
  `broadcast_Date`, `type_Sub` (Original or Revision) and `revised_Date`.

The XBRL file's name says its family (`INDAS_`, `BANKING_`, `NBFC_INDAS_`, `INTEGRATED_FILING_LI_` …), but not
always: a non-bank NBFC filed some quarters under plain `INDAS_`. So the format is decided by what the file holds
(`format_of`), with the name as the tie-break, and a filing that is none of the five formats is reported, never guessed
(Spec 02A §9.4).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from lxml import etree

INDIA = ZoneInfo("Asia/Kolkata")
API = "https://www.nseindia.com"
HOME = API + "/"
RESULTS_PATH = "/api/corporates-financial-results"
INTEGRATED_PATH = "/api/integrated-filing-results"
INTEGRATED_TYPE = "Integrated Filing- Financials"
PAGE_SIZE = 20
MAX_PAGES = 12                              # 240 filings: more than any company has filed since 2019
FIRST_PERIOD = date(2018, 12, 31)           # the Dec 2018 quarter: the earliest period 2A stores (Spec 02A §1)
FORMATS = ("ind_as", "bank", "nbfc", "insurer_life", "insurer_general")
XBRL_NS = "http://www.xbrl.org/2003/instance"

# kit/xbrl.py's insurer markers (config/xbrl_tags.json, checked against 1,226 files on 19 Oct 2026): any insurer-only
# element makes the filing an insurer's; a general insurer also carries premium-earned or combined-ratio elements.
INSURER_MARKERS = frozenset({"SolvencyRatio", "AssetsHeldToCoverLinkedLiabilities", "PolicyLiabilities",
                             "GrossPremiumsWritten", "PremiumEarned", "NetPremiumIncome", "GrossPremiumIncome"})
GENERAL_MARKERS = frozenset({"GrossPremiumsWritten", "PremiumEarned", "CombinedRatio"})
# A bank's taxonomy has InterestExpended; an NBFC's has fee and commission income and impairment of financial
# instruments but no InterestExpended (checked on BAJFINANCE's Ind AS, NBFC and bank-format files, 28 Oct 2026).
BANK_MARKERS = frozenset({"InterestExpended"})
NBFC_MARKERS = frozenset({"FeesAndCommissionIncome", "ImpairmentOnFinancialInstruments", "FinanicalAssets",
                          "FinancialAssets"})


class FilingError(ValueError):
    """A listing row or file that can't be read; the message names why (it goes into a check's detail)."""


@dataclass(frozen=True)
class Listed:
    """One filing, as a listing row describes it."""
    symbol: str
    url: str
    file_name: str
    period_end: date
    basis: str                       # consolidated | standalone
    audited: bool | None
    filed_at: datetime               # the exchange's dissemination time (timezone-aware)
    route: str                       # results | integrated
    fy_start: date | None = None
    fy_end: date | None = None
    revision: bool = False           # the listing itself calls it a revision


@dataclass
class Listing:
    filings: list[Listed]
    empty_links: int = 0             # rows NSE lists with the empty link `…/corporate/xbrl/-` (finding 6)
    ixbrl_only: int = 0              # integrated rows with an inline page but no instance
    ixbrl_duplicates: int = 0        # integrated rows that also list an inline page of the same filing (not stored)
    cumulative: int = 0              # year-to-date rows of the quarterly list (not a quarter's filing)
    unreadable: list[str] = field(default_factory=list)    # rows whose basis or period couldn't be read, with why


# ---------------------------------------------------------------------------------------- listing rows
_DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d")


def parse_date(s) -> date | None:
    if not isinstance(s, str):
        return None
    s = s.strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s.title() if "%b" in f or "%B" in f else s, f).date()
        except ValueError:
            continue
    return None


def parse_time(s) -> datetime | None:
    """NSE's `16-Jan-2025 20:20:54` (India time), or without seconds; None if it isn't one."""
    if not isinstance(s, str):
        return None
    for f in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(s.strip().title(), f).replace(tzinfo=INDIA)
        except ValueError:
            continue
    return None


def basis_of(raw) -> str | None:
    """`Consolidated` → consolidated; `Non-Consolidated` / `Standalone` → standalone (the kit's rule)."""
    c = str(raw or "").strip().lower()
    if c.startswith("non") or "standalone" in c:
        return "standalone"
    if c.startswith("consol"):
        return "consolidated"
    return None


def audited_of(raw) -> bool | None:
    a = str(raw or "").strip().lower()
    if a.startswith("un") or "un-audited" in a or "unaudited" in a:
        return False
    return True if a.startswith("audit") else None


def rows_of(reply) -> list[dict]:
    """The row list of a reply: the list itself, or its `data` list."""
    if isinstance(reply, list):
        return [r for r in reply if isinstance(r, dict)]
    if isinstance(reply, dict) and isinstance(reply.get("data"), list):
        return [r for r in reply["data"] if isinstance(r, dict)]
    return []


def _file_name(url: str) -> str:
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _empty_link(url: str) -> bool:
    return url.split("?")[0].rstrip().endswith("/-") or _file_name(url) in ("", "-")


def fy_of(period_end: date, fy_end_month: int) -> tuple[date, date]:
    """(start, end) of the financial year that contains `period_end`, for a year ending in `fy_end_month`."""
    year = period_end.year if period_end.month <= fy_end_month else period_end.year + 1
    end = date(year, fy_end_month, calendar.monthrange(year, fy_end_month)[1])
    prev = date(year - 1, fy_end_month, calendar.monthrange(year - 1, fy_end_month)[1])
    return prev + timedelta(days=1), end


def parse_results(rows: list[dict], symbol: str) -> Listing:
    """The quarterly list: one filing per row that carries an instance link."""
    out = Listing([])
    for r in rows:
        url = r.get("xbrl")
        if r.get("cumulative") == "Cumulative":
            out.cumulative += 1
            continue
        if not isinstance(url, str) or not url.strip() or _empty_link(url):
            out.empty_links += 1
            continue
        url = url.strip()
        period_end, basis = parse_date(r.get("toDate")), basis_of(r.get("consolidated"))
        filed = parse_time(r.get("exchdisstime")) or parse_time(r.get("broadCastDate"))
        if period_end is None or basis is None or filed is None:
            out.unreadable.append(f"{symbol} {_file_name(url)}: " + ", ".join(
                n for n, v in (("period", period_end), ("basis", basis), ("dissemination time", filed)) if v is None)
                + " unreadable")
            continue
        fy_start = fy_end = None
        m = re.match(r"(\d\d-\w{3}-\d{4})\s+To\s+(\d\d-\w{3}-\d{4})$", str(r.get("financialYear") or "").strip(), re.IGNORECASE)
        if m:
            fy_start, fy_end = parse_date(m.group(1)), parse_date(m.group(2))
        out.filings.append(Listed(symbol, url, _file_name(url), period_end, basis, audited_of(r.get("audited")), filed,
                                  "results", fy_start, fy_end))
    return out


def parse_integrated(rows: list[dict], symbol: str) -> Listing:
    """The integrated list: the instance (`.xml`) of each row; an inline page is a duplicate and isn't stored."""
    out = Listing([])
    for r in rows:
        url, inline = r.get("xbrl"), r.get("ixbrl")
        has_inline = isinstance(inline, str) and inline.strip() != "" and not _empty_link(inline)
        if not isinstance(url, str) or not url.strip() or _empty_link(url) or not url.lower().split("?")[0].endswith(".xml"):
            out.ixbrl_only += 1 if has_inline else 0
            out.empty_links += 0 if has_inline else 1
            continue
        out.ixbrl_duplicates += 1 if has_inline else 0
        url = url.strip()
        period_end, basis = parse_date(r.get("qe_Date")), basis_of(r.get("consolidated"))
        filed = parse_time(r.get("broadcast_Date")) or parse_time(r.get("creation_Date"))
        if period_end is None or basis is None or filed is None:
            out.unreadable.append(f"{symbol} {_file_name(url)}: " + ", ".join(
                n for n, v in (("period", period_end), ("basis", basis), ("dissemination time", filed)) if v is None)
                + " unreadable")
            continue
        out.filings.append(Listed(symbol, url, _file_name(url), period_end, basis, audited_of(r.get("audited")), filed,
                                  "integrated", revision=str(r.get("type_Sub") or "").lower().startswith("rev")))
    return out


def merge(*listings: Listing) -> Listing:
    """Several listings of one company as one, each file name once (the two endpoints can overlap at Dec 2024)."""
    out, seen = Listing([]), set()
    for lst in listings:
        out.empty_links += lst.empty_links
        out.ixbrl_only += lst.ixbrl_only
        out.ixbrl_duplicates += lst.ixbrl_duplicates
        out.cumulative += lst.cumulative
        out.unreadable += lst.unreadable
        for f in lst.filings:
            if f.file_name not in seen:
                seen.add(f.file_name)
                out.filings.append(f)
    return out


# ---------------------------------------------------------------------------------------- the file itself
@dataclass(frozen=True)
class Xbrl:
    names: frozenset[str]            # local names of the elements that carry a contextRef (the facts)
    n_facts: int


def read_instance(body: bytes) -> Xbrl:
    """Parse an XBRL instance. Refuses a document that isn't XML or whose root isn't xbrli:xbrl (an HTML error page
    served with status 200 is the usual case), and one with no facts."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.fromstring(body, parser)
    except etree.XMLSyntaxError as e:
        raise FilingError(f"not XML ({str(e)[:80]})") from None
    if etree.QName(root).namespace != XBRL_NS or etree.QName(root).localname != "xbrl":
        raise FilingError(f"the root is {etree.QName(root).localname}, not an XBRL instance")
    names, n = set(), 0
    for el in root:
        if not isinstance(el.tag, str) or el.get("contextRef") is None:
            continue
        names.add(etree.QName(el).localname)
        n += 1
    if n == 0:
        raise FilingError("an XBRL instance with no facts")
    return Xbrl(frozenset(names), n)


def format_of(file_name: str, x: Xbrl) -> str:
    """One of the five formats (Spec 02A §3.2). The file's contents decide; the name breaks ties; none of the five
    raises (`NONINDAS_`, `INDAS_DEBT_` and anything else are reported, not guessed)."""
    base = file_name.upper()
    if x.names & INSURER_MARKERS:
        return "insurer_general" if x.names & GENERAL_MARKERS else "insurer_life"
    if "_LI_" in base or "_GI_" in base:
        return "insurer_general" if "_GI_" in base else "insurer_life"
    if "NONINDAS" in base or "INDAS_DEBT" in base:
        raise FilingError(f"{file_name} is a format other than the five (Ind AS, bank, NBFC, life or general insurer)")
    if x.names & BANK_MARKERS or "BANKING" in base:
        return "bank"
    if "NBFC" in base or x.names & NBFC_MARKERS:
        return "nbfc"
    return "ind_as"


# ---------------------------------------------------------------------------------------- events
def event_slug(company_slug: str, period_end: date, rank: int) -> str:
    """`<company-slug>-results-<period-end>`; a revised filing's announcement gets `-r2` (and so on)."""
    return f"{company_slug}-results-{period_end.isoformat()}" + ("" if rank <= 1 else f"-r{rank}")


def window_start(now: datetime, since: date | None) -> date:
    """The earliest period end a run reads: its `--since` (the first backfill window reaches back to the Dec 2018
    quarter), else the last 400 days (the weekly run)."""
    if since is not None:
        return FIRST_PERIOD if since <= date(2019, 1, 1) else since
    return (now.astimezone(INDIA).date() - timedelta(days=400))


def to_utc(t: datetime) -> datetime:
    return t.astimezone(UTC)
