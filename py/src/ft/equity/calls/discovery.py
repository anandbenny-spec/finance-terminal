"""Finding earnings-call transcript filings and call intimations in the exchanges' announcements (Spec 02B §5.1, §13.3;
job 2B-06; ported from the audit kit's `kit/concalls.py`, V1.1 and V1.2).

Pure logic, no network and no database: the task (`ft/jobs/tasks/equity_call_transcripts.py`) supplies the fetch
functions, which store every reply as a raw file before anything here reads it.

- **NSE first** (D3): `/api/corporate-announcements` per company and window. A window that fails is split once; a 401,
  403 or 429 is never retried by splitting; a suspiciously round row count is split in halves (down to 31 days). The
  run stops when NSE fails for `STOP_AFTER` companies in a row.
- **BSE fallback** (D10): when NSE fails for a company, BSE's `AnnSubCategoryGetData/w` is asked per scrip under both
  "Earnings Call Transcript" and "Analyst / Investor Meet - Outcome", through the same classifier, on the headline.
- **Seasons** (§4): a filing's season is the season of its dissemination date minus 7 days. Calls held Oct-Dec discuss
  Q2, Jan-Mar Q3, Apr-Jun Q4, Jul-Sep Q1; the fiscal year ends in March and is named for the year it ends in.
- **Windows** (§13.3; Plan 04b P1): a backfill reads from `--since`, else from `history_from` in
  `config/equity/calls.yaml` (1 Jan 2025), to the run's date; a weekly run from the latest stored dissemination time
  minus 14 days. No window is longer than 12 months.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ft.equity.calls.classify import classify, file_name

INDIA = ZoneInfo("Asia/Kolkata")
NSE_ANN_PATH = "/api/corporate-announcements"
NSE_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_ATTACH = ("https://www.bseindia.com/xml-data/corpfiling/AttachLive/",
              "https://www.bseindia.com/xml-data/corpfiling/AttachHis/")
BSE_CATEGORY = "Company Update"
BSE_SUBCATEGORIES = ("Earnings Call Transcript", "Analyst / Investor Meet - Outcome")

BACKFILL_START = date(2026, 7, 1)          # §13.3: the last two seasons, Q1 and Q2 FY27
WEEKLY_OVERLAP = timedelta(days=14)
SEASON_LAG = timedelta(days=7)             # §4
MAX_WINDOW_DAYS = 365                      # §5.1: windows of 12 months or less
MIN_SPLIT_DAYS = 31
SUSPECT_COUNTS = frozenset({20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000})
REFUSED = frozenset({401, 403, 418, 429})  # blocked or rate-limited: never multiply the calls
STOP_AFTER = 10                            # companies in a row for which NSE fails
MIN_ANSWERED = 48                          # of the 50: the blocking check
BSE_MAX_PAGES = 20


class NseStopped(Exception):
    """NSE failed for STOP_AFTER companies in a row: the run stops (a source problem, not a company's)."""


# ---------------------------------------------------------------------------------------------- seasons and windows
def season_of_call(day: date) -> str:
    """The season a call held on `day` belongs to: 'Q1 FY27' for a call in Jul-Sep 2026."""
    q = {7: 1, 8: 1, 9: 1, 10: 2, 11: 2, 12: 2, 1: 3, 2: 3, 3: 3, 4: 4, 5: 4, 6: 4}[day.month]
    fy = day.year + 1 if day.month >= 7 else day.year          # the fiscal year of the quarter the call discusses
    return f"Q{q} FY{fy % 100:02d}"


def season_of_filing(disseminated: datetime | date) -> str:
    """A filing's season: that of its dissemination date minus 7 days (§4)."""
    d = disseminated.astimezone(INDIA).date() if isinstance(disseminated, datetime) else disseminated
    return season_of_call(d - SEASON_LAG)


CALLS_CONFIG = Path(__file__).resolve().parents[4] / "config" / "equity" / "calls.yaml"


@dataclass(frozen=True)
class CallsConfig:
    """`py/config/equity/calls.yaml` (Plan 04b P1, P3, P5): values the history job needs, never literals in code."""
    history_from: date           # a backfill without --since reads announcements from here
    first_pages_from: str        # filings of an earlier season are classified without opening their first pages ("Q1 FY27")
    raw_storage_stop_mb: int     # the stop for a project's stored raw files
    call_text_integrity_slices: int   # equity.call_text re-reads one of this many slices of the unchanged calls in each run
    raw_storage_hold_mb: int          # F3-02 (#387): at or over it, no new transcript file is fetched or stored


def load_calls_config(path: Path = CALLS_CONFIG) -> CallsConfig:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    slices = int(doc["call_text_integrity_slices"])
    if slices < 1:
        raise ValueError("call_text_integrity_slices must be at least 1")
    warn, hold = int(doc["raw_storage_stop_mb"]), int(doc["raw_storage_hold_mb"])
    if hold <= warn:
        raise ValueError("raw_storage_hold_mb must be above raw_storage_stop_mb: the warning comes first")
    return CallsConfig(doc["history_from"], str(doc["first_pages_from"]), warn, slices, hold)


def window_start(since: date | None, latest_stored: datetime | None, *, backfill: bool = False,
                 history_from: date | None = None) -> date:
    """The first day a run reads: its `--since`; else, for a backfill, the configured `history_from` (Plan 04b P1); else a
    weekly run's latest stored dissemination minus 14 days; else the backfill start of 1 Jul 2026."""
    if since is not None:
        return since
    if backfill and history_from is not None:
        return history_from
    if latest_stored is not None:
        return max(BACKFILL_START, (latest_stored.astimezone(INDIA) - WEEKLY_OVERLAP).date())
    return BACKFILL_START


def split_range(start: date, end: date, days: int = MAX_WINDOW_DAYS) -> list[tuple[date, date]]:
    """[start, end] cut into windows of `days` or fewer."""
    out, cur = [], start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


# ---------------------------------------------------------------------------------------------- announcements
@dataclass(frozen=True)
class Announcement:
    """One exchange announcement of a company, classified."""
    exchange: str                 # NSE | BSE
    filing_key: str               # the source's own id: NSE seq_id, BSE NEWSID
    symbol: str | None            # NSE symbol of the row (NSE rows)
    isin: str | None              # the row's ISIN (NSE rows)
    scrip: str | None             # BSE scrip code (BSE rows)
    disseminated: datetime        # India time
    subject: str
    file_name: str
    url: str | None               # the attachment (BSE: the live archive; 2B-07 also tries the history archive)
    kind: str                     # transcript | intimation | audio_video | other
    why: str
    season: str
    entity_id: int | None = None  # joined by `resolve`; None = matches no universe company
    queried: str | None = None    # the NSE symbol this row was asked for (NSE rows; a renamed company is asked under each symbol)
    joined_by: str | None = None  # how it joined: isin | earlier_isin | symbol | scrip | asked (None = not joined)

    @property
    def is_transcript(self) -> bool:
        return self.kind == "transcript"


def parse_time(s) -> datetime | None:
    """NSE's '24-Sep-2026 19:03:02' or BSE's '2026-07-29T11:55:19.473' (both India time) as an aware datetime."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    for f in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
              "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(s.title() if "%b" in f else s, f).replace(tzinfo=INDIA)
        except ValueError:
            continue
    return None


def rows_of(reply) -> list[dict] | None:
    """The row list of an NSE reply (a list, or a dict with a list under data/rows/Table/announcements); None when the
    reply isn't one (an error page served with 200)."""
    if isinstance(reply, list):
        return [r for r in reply if isinstance(r, dict)]
    if isinstance(reply, dict):
        for k in ("data", "rows", "Table", "announcements"):
            if isinstance(reply.get(k), list):
                return [r for r in reply[k] if isinstance(r, dict)]
    return None


def norm_nse(row: dict) -> Announcement | None:
    """An NSE row as an announcement; None for a row with no id or no dissemination time (counted by the caller)."""
    when = (parse_time(row.get("exchdisstime")) or parse_time(row.get("an_dt")) or parse_time(row.get("sort_date")))
    key = row.get("seq_id")
    if when is None or key in (None, ""):
        return None
    url = row.get("attchmntFile")
    url = url.strip() if isinstance(url, str) and url.startswith("http") and not url.rstrip().endswith("/-") else None
    subject = str(row.get("attchmntText") or row.get("subject") or "").strip()
    name = file_name(url)
    c = classify(subject, name)
    return Announcement("NSE", str(key), str(row.get("symbol") or "").strip().upper() or None,
                        str(row.get("sm_isin") or "").strip().upper() or None, None, when, subject, name, url,
                        c.kind, c.why, season_of_filing(when))


def norm_bse(row: dict) -> Announcement | None:
    """A BSE row as an announcement, classified on its headline (the kit's V2 rule: the subcategory's own name,
    'Earnings Call Transcript', is in NEWSSUB and would make every audio notice under it a transcript)."""
    when = parse_time(row.get("NEWS_DT")) or parse_time(row.get("DissemDT")) or parse_time(row.get("DT_TM"))
    key = row.get("NEWSID")
    if when is None or not key:
        return None
    att = str(row.get("ATTACHMENTNAME") or "").strip()
    headline = str(row.get("HEADLINE") or "").strip()
    subject = headline or str(row.get("NEWSSUB") or "").strip()
    c = classify(subject, att)
    return Announcement("BSE", str(key), None, None, str(row.get("SCRIP_CD") or "") or None, when, subject, att,
                        BSE_ATTACH[0] + att if att else None, c.kind, c.why, season_of_filing(when))


def dedupe(items: Iterable[Announcement]) -> list[Announcement]:
    seen, out = set(), []
    for a in items:
        k = (a.exchange, a.filing_key)
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out


# ---------------------------------------------------------------------------------------------- the company join
@dataclass(frozen=True)
class Company:
    entity_id: int
    slug: str
    symbol: str                       # the current NSE symbol
    isin: str | None
    bse_code: str | None
    listed_on: date | None = None
    symbols: frozenset[str] = frozenset()    # every NSE symbol the company has had (the identifier history)
    isins: frozenset[str] = frozenset()      # every ISIN it has had


ISIN_SHAPE = re.compile(r"IN[A-Z0-9]{9}[0-9]")
STEM_LENGTH = 9


def isin_stem(isin: str | None) -> str | None:
    """The issuer-and-class part of an Indian ISIN: its first nine characters ('INE090A01' of 'INE090A01021'). The next two
    digits number the issue and change when a split or bonus brings a new ISIN for the same shares, so an earlier ISIN
    of a security shares its stem with the current one. None for a value that isn't ISIN-shaped (a placeholder)."""
    return isin[:STEM_LENGTH] if isin and ISIN_SHAPE.fullmatch(isin) else None


class CompanyIndex:
    """Joins announcement rows to universe companies on ISIN and symbol through the identifier history, never on the
    file name or the company's name (§5.1). A row's ISIN decides; the symbol decides when the row has no ISIN.

    NSE's announcement rows can carry an earlier ISIN of the same shares (from before a split or bonus: ICICI Bank's
    rows say INE090A01013, its universe record INE090A01021), which the identifier history doesn't hold (#264). Such a
    row joins only when its ISIN shares the company's issuer-and-class stem AND its symbol is one of the company's
    symbols: two identifiers agree. A stranger ISIN, or a stem that fits no company that holds the symbol, stays
    unmatched even when the symbol is a company's (a symbol is reused after a demerger)."""

    def __init__(self, companies: Iterable[Company]):
        self.by_isin: dict[str, Company] = {}
        self.by_symbol: dict[str, Company] = {}
        self.by_scrip: dict[str, Company] = {}
        self.by_stem: dict[str, dict[int, Company]] = {}
        for c in companies:
            for i in c.isins | ({c.isin} if c.isin else set()):
                self.by_isin[i] = c
                if (stem := isin_stem(i)) is not None:
                    self.by_stem.setdefault(stem, {})[c.entity_id] = c
            for s in c.symbols | {c.symbol}:
                self.by_symbol[s] = c
            if c.bse_code:
                self.by_scrip[c.bse_code] = c

    def _earlier_isin(self, a: Announcement) -> Company | None:
        stem = isin_stem(a.isin)
        if stem is None or not a.symbol:
            return None
        fits = [c for c in self.by_stem.get(stem, {}).values() if a.symbol == c.symbol or a.symbol in c.symbols]
        return fits[0] if len(fits) == 1 else None

    def match(self, a: Announcement) -> tuple[Company | None, str | None]:
        """The company a row belongs to and how it was found: isin, earlier_isin, symbol (the row has no ISIN) or scrip."""
        if a.isin:
            if (c := self.by_isin.get(a.isin)) is not None:
                return c, "isin"
            if (c := self._earlier_isin(a)) is not None:
                return c, "earlier_isin"
        elif a.symbol and (c := self.by_symbol.get(a.symbol)) is not None:
            return c, "symbol"
        if a.scrip and (c := self.by_scrip.get(a.scrip)) is not None:
            return c, "scrip"
        return None, None

    def resolve(self, a: Announcement) -> int | None:
        c, _ = self.match(a)
        return c.entity_id if c else None


# ---------------------------------------------------------------------------------------------- NSE windows
@dataclass(frozen=True)
class Answer:
    """One request's outcome. `reply` is the parsed JSON (None if the reply wasn't JSON)."""
    status: int | str            # HTTP status, or a short error name
    reply: object = None


NseFetch = Callable[[str, date, date], Answer]


@dataclass
class WindowLog:
    symbol: str
    start: date
    end: date
    status: int | str
    answered: bool
    rows: int
    depth: int = 0
    split: str = ""
    superseded: bool = False


def nse_window(fetch: NseFetch, symbol: str, start: date, end: date, depth: int = 0) -> tuple[list[dict], list[WindowLog]]:
    """Rows for one window, split in halves when it fails (once) or returns a suspiciously round count (down to 31
    days). A window is 'answered' when NSE replied 200 with a row list. A refused window (401, 403, 418, 429) is never
    split."""
    ans = fetch(symbol, start, end)
    rows = rows_of(ans.reply) if ans.status == 200 else None
    ok = rows is not None
    rows = rows or []
    log = WindowLog(symbol, start, end, ans.status, ok, len(rows), depth)
    span = (end - start).days
    refused = ans.status in REFUSED
    split_failed = not ok and not refused and depth < 1
    split_round = ok and len(rows) in SUSPECT_COUNTS and depth < 3
    if (split_failed or split_round) and span >= 2 * MIN_SPLIT_DAYS:
        mid = start + timedelta(days=span // 2)
        log.split = "failed" if not ok else f"round count {len(rows)}"
        a, la = nse_window(fetch, symbol, start, mid, depth + 1)
        b, lb = nse_window(fetch, symbol, mid + timedelta(days=1), end, depth + 1)
        halves = la + lb
        if all(x.answered for x in halves):
            log.superseded = True
            return dedupe_rows(a + b), [log] + halves
        if ok:                                      # the halves failed but the whole window had answered: keep it
            for x in halves:
                x.superseded = True
            return rows, [log] + halves
        log.superseded = True
        return dedupe_rows(a + b), [log] + halves
    return rows, [log]


def dedupe_rows(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        k = str(r.get("seq_id") or (r.get("attchmntFile"), r.get("an_dt"), r.get("attchmntText")))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


@dataclass
class CompanyResult:
    company: Company
    announcements: list[Announcement] = field(default_factory=list)
    nse_answered: bool = False          # every leaf window answered
    nse_rows: int = 0
    bse_used: bool = False
    bse_answered: bool = False
    refused: int = 0                    # 401, 403, 418 and 429 leaf windows
    windows: list[WindowLog] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        """Counts toward the blocking check: NSE answered, or the BSE fallback did."""
        return self.nse_answered or (self.bse_used and self.bse_answered)


def nse_symbol(fetch: NseFetch, symbol: str, start: date, end: date) -> tuple[list[dict], list[WindowLog], bool, int]:
    """Every window of `start`..`end` for one symbol: (rows, window log, all leaf windows answered, refused leaves).
    A renamed company is asked under each symbol it had in the window (the caller)."""
    rows: list[dict] = []
    logs: list[WindowLog] = []
    ok, refused = True, 0
    for a, b in split_range(start, end):
        got, log = nse_window(fetch, symbol, a, b)
        rows += got
        logs += log
        leaves = [x for x in log if not x.superseded]
        ok &= all(x.answered for x in leaves)
        refused += sum(1 for x in leaves if x.status in REFUSED)
    return dedupe_rows(rows), logs, ok, refused


class RunGuard:
    """Counts companies in a row for which NSE failed; `STOP_AFTER` of them stop the run."""

    def __init__(self, limit: int = STOP_AFTER):
        self.limit, self.streak = limit, 0

    def record(self, nse_answered: bool) -> None:
        self.streak = 0 if nse_answered else self.streak + 1
        if self.streak >= self.limit:
            raise NseStopped(f"NSE failed for {self.streak} companies in a row")


# ---------------------------------------------------------------------------------------------- BSE
BseFetch = Callable[[str, date, date, str, int], Answer]


def bse_company(fetch: BseFetch, scrip: str, start: date, end: date) -> tuple[list[dict], bool, list[str]]:
    """All BSE rows for one scrip under both subcategories, windows of 12 months or less, paged until the row count
    BSE reports is reached. Returns (rows, answered, problems)."""
    rows: list[dict] = []
    problems: list[str] = []
    ok = True
    for sub in BSE_SUBCATEGORIES:
        for a, b in split_range(start, end):
            got: list[dict] = []
            total = None
            for page in range(1, BSE_MAX_PAGES + 1):
                ans = fetch(scrip, a, b, sub, page)
                reply = ans.reply
                if ans.status != 200 or not (isinstance(reply, dict) and isinstance(reply.get("Table"), list)):
                    ok = False
                    problems.append(f"{sub} {a}..{b}: {ans.status}")
                    break
                page_rows = [x for x in reply["Table"] if isinstance(x, dict)]
                t1 = reply.get("Table1")
                if isinstance(t1, list) and t1 and isinstance(t1[0], dict):
                    try:
                        total = int(t1[0].get("ROWCNT"))
                    except (TypeError, ValueError):
                        pass
                got += page_rows
                if not page_rows or total is None or len(got) >= total:
                    break
            rows += got
    seen, out = set(), []
    for r in rows:
        k = str(r.get("NEWSID") or (r.get("ATTACHMENTNAME"), r.get("NEWS_DT")))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out, ok, problems


def results_filed_in(window: tuple[date, date], filed: Iterable[datetime]) -> bool:
    """Whether any results filing was disseminated in [start, end] (the condition for the BSE fallback on 'no rows')."""
    lo, hi = window
    return any(lo <= f.astimezone(INDIA).date() <= hi for f in filed)

