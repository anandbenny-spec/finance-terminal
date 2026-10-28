"""equity.prices: daily prices of the universe companies from the exchange's bhavcopies (Spec 02A §5.1; job 2A-06).

Weekly, under the NSE posture (CLAUDE.md §1): one file per calendar session (`ft/calendars.py`, special sessions
included) since the latest stored trade date, or, on a backfill, from `--since` to `--until` (a window is clipped to the
last session that has closed and been published, so a future file is never asked for). Each file is stored as a raw
file before the load (`content_date` = the session) and counts only if it unzips, parses and carries the session's own
trade date: BSE answers a missing day with HTTP 200 and a page, and an archive can serve the previous day's file.

Rows kept: the universe companies (`equity.company`), matched on the exchange's own key: NSE by ISIN (any ISIN the
master holds for the company) and, for a row whose ISIN it doesn't hold, by the symbol the company had on the day (an
ISIN that changed, such as HDFC Bank's in 2019); BSE by scrip code. Two rows for one company in a file fail the run. Series EQ, BE and BZ on
NSE. A company's prices come from its `price_exchange` only, never mixed day by day (Data Audit 02 finding 11): BSE is
a whole-series fallback, used by no Nifty 50 company today but built and tested. Rows are written once
(`equity.price_daily`'s guard); a company is published (`core.entity.is_published`) once its first price load passes.

Checks, B blocking, W warning:
- (B) `sessions_present`: every calendar session in the window has a usable file, else the sessions are named;
- (B) `calendar_proven`: in a year before the run's own, every weekday the calendar calls a holiday is asked for and has
  no file with its own date (the calendar proven by the exchange's files);
- (B) `rows_matched`: every session matched at least one company, and no company twice;
- (B) `sane_prices`: close > 0, high >= low, low <= close <= high;
- (B) `unchanged`: a stored row re-loaded with a different value is named with both values (the guard would refuse it);
- (B) `continuity`: PREV_CLOSE on a day equals the stored close of the previous session (± 0.01); more than half of a
  day's rows failing means a missed session;
- (W) `continuity_rows`: single rows failing, named;
- (W) `no_gaps`: a listed universe company without a row on a session (a suspension), named.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ft import calendars
from ft.equity import bhavcopy as bh
from ft.equity import universe as u
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

INDIA = ZoneInfo("Asia/Kolkata")
CALENDAR = "xnse"                       # NSE's calendar; BSE's equity segment shares it
FLOOR = date(2019, 1, 1)                # the backfill's start (Spec 02A §5.1)
PUBLISH_LAG = timedelta(hours=3)        # NSE posts a day's file in the evening: close 15:30 + 3 h
TOLERANCE = Decimal("0.01")
SHOWN = 12                              # names listed in a check's detail


def last_closed_session(now: datetime) -> date:
    """The latest session whose file can be up: its close plus the publishing lag has passed."""
    d = now.astimezone(INDIA).date()
    for _ in range(14):
        if calendars.is_session(CALENDAR, d) and calendars.close_time(CALENDAR, d) + PUBLISH_LAG <= now:
            return d
        d -= timedelta(days=1)
    raise RuntimeError("no closed session in the last 14 days: is the calendar right?")


def _names(items: list[str]) -> str:
    return ", ".join(items[:SHOWN]) + (f" and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Company:
    entity_id: int
    slug: str
    exchange: str                       # price_exchange
    listed_on: date | None


@dataclass
class DayFile:
    exchange: str
    day: date
    raw_file_id: int
    file_rows: int
    rows: dict[int, bh.PriceRow]                       # entity_id -> row
    ambiguous: list[str] = field(default_factory=list)  # entities two rows matched


@dataclass
class Fetched:
    windows: dict[str, tuple[date, date]]              # exchange -> first and last session asked for
    days: list[DayFile]
    missing: dict[str, list[str]]                      # exchange -> "date (reason)" for sessions without a usable file
    probed: dict[str, int]                             # exchange -> holidays asked for
    holiday_files: dict[str, list[date]]               # exchange -> holidays NSE/BSE published a file for
    companies: dict[int, Company]
    note: str = ""


@register
class EquityPrices(Task):
    key = "equity.prices"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f, db = ctx.fetcher, ctx.fetcher.db
        companies = {r[0]: Company(*r) for r in db.execute(
            """select c.entity_id, e.slug, c.price_exchange, c.listed_on
                 from equity.company c join core.entity e on e.id = c.entity_id order by e.slug""").fetchall()}
        by_isin: dict[str, int] = {}
        by_code: dict[str, int] = {}
        symbols: dict[str, list[tuple[int, date | None, date | None]]] = {}
        for eid, scheme, value, lo, hi in db.execute(
                """select entity_id, scheme, value, lower(valid), upper(valid) from core.entity_identifier
                    where scheme in ('isin', 'nse_symbol', 'bse_code') and entity_id = any(%s)""",
                (list(companies),)).fetchall():
            if scheme == "isin":
                by_isin[value] = eid
            elif scheme == "bse_code":
                by_code[value] = eid
            else:
                symbols.setdefault(value, []).append((eid, lo, hi))
        stored = dict(db.execute("select exchange, max(trade_date) from equity.price_daily group by 1").fetchall())
        used = sorted({c.exchange for c in companies.values()})
        end_cap = min(ctx.until or date.max, last_closed_session(ctx.now))
        this_year = ctx.now.astimezone(INDIA).year

        windows: dict[str, tuple[date, date]] = {}
        days: list[DayFile] = []
        missing: dict[str, list[str]] = {x: [] for x in used}
        probed: dict[str, int] = {x: 0 for x in used}
        holiday_files: dict[str, list[date]] = {x: [] for x in used}
        note = ""

        for ex in used:
            if ctx.since is not None:
                start = max(ctx.since, FLOOR)
            elif ex in stored:
                start = stored[ex] + timedelta(days=1)
            else:
                raise ValueError(f"no {ex} prices are stored and the run has no --since: start with a backfill")
            if start > end_cap:
                note += f"{ex}: nothing new since {start - timedelta(days=1)}. "
                continue
            windows[ex] = (start, end_cap)
            mine = {eid for eid, c in companies.items() if c.exchange == ex}
            for d in calendars.sessions(CALENDAR, start, end_cap):
                bhav, raw_id, reason = self._file(f, ex, d)
                if bhav is None:
                    missing[ex].append(f"{d} ({reason})")
                    continue
                days.append(self._match(ex, d, raw_id, bhav, mine, by_isin, by_code, symbols))
            # the calendar proven by the exchange's own files, for every year but the run's own
            d = start
            while d <= end_cap:
                if d.year < this_year and d.weekday() < 5 and not calendars.is_session(CALENDAR, d):
                    probed[ex] += 1
                    bhav, _, _ = self._file(f, ex, d)
                    if bhav is not None:
                        holiday_files[ex].append(d)
                d += timedelta(days=1)
        return Fetched(windows, days, missing, probed, holiday_files, companies, note.strip())

    @staticmethod
    def _file(f, ex: str, d: date) -> tuple[bh.Bhav | None, int | None, str]:
        """(parsed file, raw file id, why not) for the session `d`: usable only if it is a bhavcopy of that date."""
        _, url = bh.url_for(ex, d)
        headers = u.NSE_HEADERS if ex == "NSE" else bh.BSE_ORIGIN_HEADERS
        got = f.get(url, source=ex.lower(), media_type="application/zip" if url.lower().endswith(".zip") else "text/csv",
                    headers=headers, content_date=d, allow=(404,))
        if got.raw_file_id is None:
            return None, None, "no such file"
        body = got.body if got.body is not None else f.read(got.raw_file_id)
        try:
            bhav = bh.parse_bhavcopy(body)
        except bh.BhavError as e:
            return None, got.raw_file_id, str(e)
        if bhav.exchange != ex:
            return None, got.raw_file_id, f"a {bhav.exchange} file"
        if bhav.trade_date != d:
            return None, got.raw_file_id, f"the file is for {bhav.trade_date}"
        return bhav, got.raw_file_id, ""

    @staticmethod
    def _match(ex, d, raw_id, bhav, mine, by_isin, by_code, symbols) -> DayFile:
        try:
            picked = bh.pick_rows(bhav)
        except bh.BhavError as e:
            return DayFile(ex, d, raw_id, len(bhav.rows), {}, [f"{d}: {e}"])
        rows: dict[int, bh.PriceRow] = {}
        ambiguous: list[str] = []
        for r in picked.values():
            if ex == "BSE":
                eid = by_code.get(r.code)
            else:
                eid = by_isin.get(r.isin) if r.isin else None
                if eid is None:                             # an ISIN the master doesn't hold (HDFC Bank's, before its
                    eid = next((e for e, lo, hi in symbols.get(r.symbol, [])   # 2019 split) or none: the symbol it had
                                if (lo is None or lo <= d) and (hi is None or d < hi)), None)   # on the day
            if eid is None or eid not in mine:
                continue
            if eid in rows:
                ambiguous.append(f"{d}: two rows ({rows[eid].isin or rows[eid].symbol}, {r.isin or r.symbol}) for one company")
            else:
                rows[eid] = r
        return DayFile(ex, d, raw_id, len(bhav.rows), rows, ambiguous)

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        dates = sorted({x.day for x in f.days})
        existing: dict[tuple[int, date, str], tuple] = {}
        if dates:
            for eid, d, ex, *vals in tx.execute(
                    """select entity_id, trade_date, exchange, series, open, high, low, close, prev_close, volume,
                              traded_value, trades from equity.price_daily where trade_date between %s and %s""",
                    (dates[0], dates[-1])).fetchall():
                existing[(eid, d, ex)] = tuple(vals)
        slug = {eid: c.slug for eid, c in f.companies.items()}
        inserts, changed, insane = [], [], []
        for day in f.days:
            for eid, r in day.rows.items():
                if not (r.close > 0 and (r.high is None or r.low is None or r.high >= r.low)
                        and (r.low is None or r.close >= r.low) and (r.high is None or r.close <= r.high)):
                    insane.append(f"{slug[eid]} {day.day}: open {r.open}, high {r.high}, low {r.low}, close {r.close}")
                    continue
                vals = (r.series, r.open, r.high, r.low, r.close, r.prev_close, r.volume, r.traded_value, r.trades)
                old = existing.get((eid, day.day, day.exchange))
                if old is None:
                    inserts.append((eid, day.day, day.exchange, *vals, day.raw_file_id, task_run_id))
                elif old != vals:
                    changed.append(f"{slug[eid]} {day.day} {day.exchange}: stored {_fmt(old)}, file {_fmt(vals)}")
        if inserts:
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close,
                           prev_close, volume, traded_value, trades, raw_file_id, task_run_id)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", inserts)
        published = []
        if any(x.rows for x in f.days):
            published = [r[0] for r in tx.execute(
                """update core.entity e set is_published = true
                    where e.kind = 'company' and not e.is_published and e.id = any(%s)
                      and exists (select 1 from equity.price_daily p where p.entity_id = e.id)
                returning e.slug""", (list(f.companies),)).fetchall()]
        self.result = {"inserted": len(inserts), "changed": changed, "insane": insane, "published": sorted(published)}
        return len(inserts) + len(published)

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        out: list[Check] = []
        slug = {eid: c.slug for eid, c in f.companies.items()}

        gone = [m for ms in f.missing.values() for m in ms]
        asked = sum(1 for _ in f.days) + len(gone)
        out.append(Check("sessions_present", not gone,
                         ("no usable file for: " + _names(gone)) if gone
                         else (f"{asked} sessions, each with its file" if asked else f.note or "no session to load")))

        probed = sum(f.probed.values())
        wrong = [f"{ex} {d}" for ex, ds in f.holiday_files.items() for d in ds]
        out.append(Check("calendar_proven", not wrong,
                         ("the calendar calls these holidays but the exchange published a file: " + _names(wrong))
                         if wrong else f"{probed} weekday holidays asked for, none has a file" if probed
                         else "no weekday holiday of an earlier year in the window"))

        empty = [str(x.day) for x in f.days if not x.rows]
        ambiguous = [a for x in f.days for a in x.ambiguous]
        out.append(Check("rows_matched", not empty and not ambiguous,
                         ("; ".join(filter(None, [("no universe company in the file of " + _names(empty)) if empty else "",
                                                  _names(ambiguous)]))
                          if empty or ambiguous else f"{len(f.days)} files matched to the universe")))

        out.append(Check("sane_prices", not r["insane"],
                         ("; ".join(r["insane"][:SHOWN])) if r["insane"] else "close > 0 and low <= close <= high"))
        out.append(Check("unchanged", not r["changed"],
                         ("; ".join(r["changed"][:SHOWN])) if r["changed"]
                         else "no stored row differs from its file"))

        # continuity: PREV_CLOSE on a day is the stored close of the previous session (unadjusted, also on ex-dates)
        if f.days:
            first = min(x.day for x in f.days)
            closes = {(eid, ex, d): c for eid, ex, d, c in tx.execute(
                "select entity_id, exchange, trade_date, close from equity.price_daily where trade_date between %s and %s",
                (calendars.previous_session(CALENDAR, first), max(x.day for x in f.days))).fetchall()}
        else:
            closes = {}
        blocked, single, compared = [], [], 0
        for x in sorted(f.days, key=lambda y: (y.exchange, y.day)):
            prev = calendars.previous_session(CALENDAR, x.day)
            seen = bad = 0
            names = []
            for eid, row in x.rows.items():
                before = closes.get((eid, x.exchange, prev))
                if before is None or row.prev_close is None:
                    continue
                seen += 1
                if abs(row.prev_close - before) > TOLERANCE:
                    bad += 1
                    names.append(f"{slug[eid]} {x.day} (PREV_CLOSE {row.prev_close}, stored close {before} on {prev})")
            compared += seen
            if bad and bad * 2 > seen:
                blocked.append(f"{x.exchange} {x.day}: {bad} of {seen} rows break (a session missed before {x.day}?)")
            else:
                single += names
        out.append(Check("continuity", not blocked,
                         "; ".join(blocked) if blocked else f"{compared} rows agree with the previous close"))
        out.append(Check("continuity_rows", not single, _names(single) if single else "no single row breaks",
                         blocking=False))

        gaps: dict[str, list[date]] = {}
        for x in f.days:
            for eid, c in f.companies.items():
                if c.exchange == x.exchange and eid not in x.rows and (c.listed_on is None or c.listed_on <= x.day):
                    gaps.setdefault(c.slug, []).append(x.day)
        text = [f"{s} ({len(ds)} session{'s' if len(ds) != 1 else ''}: {_names([str(d) for d in ds[:3]])})"
                for s, ds in sorted(gaps.items())]
        out.append(Check("no_gaps", not gaps, ("no row for: " + _names(text)) if gaps
                         else "every listed universe company has a row on every session", blocking=False))
        return out


def _fmt(vals: tuple) -> str:
    return "(" + ", ".join("-" if v is None else str(v) for v in vals) + ")"
