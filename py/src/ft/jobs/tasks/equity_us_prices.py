"""us.prices: the Dow 30's daily prices from Yahoo Finance (Spec 02A.1 §5.3; job US-03, #402).

The US tasks are module `us` (decided on #400), so the hidden slice moves no count on /accuracy. Weekly (`every 6
days`, after `us.universe`); its first run on a project is a backfill (`equity_due`).

Fetch, per US company (`market = 'US'`; India's companies are never read): one chart request to Yahoo's chart API for
its current ticker (`ft.equity.us_prices`), through the shared HTTP client with Chrome's fingerprint and Yahoo's crumb,
as `policy.market_series` does. Each response is stored as a raw file before the load and fetched whole every run
(`conditional=False`), so the task never reads the raw bucket back (Plan 05 §0.5 N10). The window:
- a backfill: `--since` (floored at 1 Jan 2019) to `--until`;
- otherwise: the session after the company's latest stored one; a company with none (a new member) from 1 Jan 2019;
each clipped to the last NYSE session that has closed (`ft/calendars.py`, `xnys`, plus three hours). The request also
asks for the stored session just before the window (the overlap), so every run proves its prices sit on the stored
basis.

Yahoo returns history divided by every later split; `parse_chart` multiplies each bar back by the splits in the same
response, so `equity.price_daily` holds the prices as traded on each day (one basis, like India's), and
`us.corporate_actions` gives each split after the first stored session its ratio factor. Rows are written once (the
table's guard): a stored row that a re-load would change is a blocking check, never an update. `exchange` is the
company's listing exchange (NYSE or NASDAQ), `series` EQ (the common stock: Yahoo has no exchange series code). The
task **never publishes a company** (Spec 02A.1 §3: the go-live flip is Anand's SQL).

Checks, B blocking, W warning:
- (B) `charts_read`: every company's chart read and parsed;
- (B) `responses`: each chart is the ticker asked for, in USD;
- (B) `sessions_present`: every NYSE session in each company's window has a bar (a gap is retried by the next run,
  never skipped over);
- (B) `calendar_proven`: no bar on a day the calendar calls closed;
- (B) `sane_prices`: close > 0 and low <= close <= high;
- (B) `cents`: every price, multiplied back, within 0.2 of a cent of a whole cent;
- (B) `unchanged`: a stored row re-loaded with a different value is named with both values;
- (B) `overlap`: the stored session before the window comes back (multiplied back) at its stored close;
- (W) `exchange`: Yahoo's exchange against the company's listing exchange;
- (W) `rescaled`: the companies whose bars were multiplied back, with the splits;
- (W) `held_back`: bars sent twice with different values, or listed without a close, named;
- (W) `yahoo_session`: whether Yahoo's crumb was received.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from ft import calendars
from ft.equity import us_prices as yp
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register
from ft.jobs.tasks.market_series import yahoo_crumb

CALENDAR = "xnys"
FLOOR = date(2019, 1, 1)                # the backfill's start (Spec 02A.1 §5.3)
PUBLISH_LAG = timedelta(hours=3)        # a session's bar is final well after its 16:00 close
TOLERANCE = Decimal("0.01")
SERIES = "EQ"
SHOWN = 12


def last_closed_session(now: datetime) -> date:
    d = now.astimezone(yp.NEW_YORK).date()
    for _ in range(14):
        if calendars.is_session(CALENDAR, d) and calendars.close_time(CALENDAR, d) + PUBLISH_LAG <= now:
            return d
        d -= timedelta(days=1)
    raise RuntimeError("no closed NYSE session in the last 14 days: is the calendar right?")


def close_at(d: date) -> datetime | None:
    """The session's close, or None for a day that wasn't one (or a year the calendar doesn't cover)."""
    try:
        return calendars.close_time(CALENDAR, d) if calendars.is_session(CALENDAR, d) else None
    except calendars.CalendarYearError:
        return None


def _names(items: list[str], shown: int = SHOWN) -> str:
    return "; ".join(items[:shown]) + (f"; and {len(items) - shown} more" if len(items) > shown else "")


@dataclass
class Company:
    entity_id: int
    slug: str
    ticker: str | None
    exchange: str                       # price_exchange: NYSE or NASDAQ
    listed_on: date | None
    last: date | None                   # latest stored trade date on its exchange


@dataclass
class Got:
    company: Company
    start: date                         # first session of the window
    end: date
    anchor: date | None                 # the stored session before the window, asked for again (the overlap)
    anchor_close: Decimal | None
    raw_file_id: int
    chart: yp.Chart
    bars: list[yp.Bar]                  # the chart's bars from the anchor to the end


@dataclass
class Fetched:
    end: date
    companies: dict[int, Company]
    got: list[Got] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    idle: list[str] = field(default_factory=list)      # companies with nothing new to ask for
    yahoo_session: str | None = None


@register
class UsPrices(Task):
    key = "us.prices"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        if not db.execute("select exists (select 1 from core.source where key = 'sec')").fetchone()[0]:
            return Due(False, "not set up: migration 0039 (us_market) isn't pushed to this project")
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f, db = ctx.fetcher, ctx.fetcher.db
        companies = {r[0]: Company(*r) for r in db.execute(
            """select c.entity_id, e.slug,
                      (select i.value from core.entity_identifier i
                        where i.entity_id = c.entity_id and i.scheme = 'ticker' and upper_inf(i.valid)
                        order by lower(i.valid) desc nulls last limit 1),
                      c.price_exchange, c.listed_on,
                      (select max(p.trade_date) from equity.price_daily p
                        where p.entity_id = c.entity_id and p.exchange = c.price_exchange)
                 from equity.company c join core.entity e on e.id = c.entity_id
                where e.kind = 'company' and e.market = 'US' order by e.slug""").fetchall()}
        if not companies:
            raise RuntimeError("no US company in the master: us.universe runs first")
        end = min(ctx.until or date.max, last_closed_session(ctx.now))
        out = Fetched(end, companies)
        crumb = None
        for c in companies.values():
            if not c.ticker:
                out.failed.append(f"{c.slug}: no current ticker in core.entity_identifier")
                continue
            if ctx.since is not None:
                start = max(ctx.since, FLOOR)
            elif c.last is not None:
                start = c.last + timedelta(days=1)
            else:
                start = FLOOR
            if c.listed_on and c.listed_on > start:
                start = c.listed_on
            if start > end:
                out.idle.append(c.ticker)
                continue
            row = db.execute("""select trade_date, close from equity.price_daily
                                 where entity_id = %s and exchange = %s and trade_date < %s
                                 order by trade_date desc limit 1""", (c.entity_id, c.exchange, start)).fetchone()
            anchor, anchor_close = (row[0], row[1]) if row else (None, None)
            if out.yahoo_session is None:
                crumb, out.yahoo_session = yahoo_crumb(f)
            try:
                got = f.get(yp.yahoo_url(c.ticker), source="yfinance", params=yp.chart_params(anchor or start, end),
                            secret_params={"crumb": crumb} if crumb else None, media_type="application/json",
                            conditional=False, content_date=end)
                chart = yp.parse_chart(got.body, c.ticker, fetched_at=got.fetched_at, close_at=close_at)
            except (FetchError, yp.SourceError) as e:
                out.failed.append(f"{c.ticker}: {str(e)[:160]}")
                continue
            bars = [b for b in chart.bars if (anchor or start) <= b.day <= end]
            out.got.append(Got(c, start, end, anchor, anchor_close, got.raw_file_id, chart, bars))
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        inserts, changed, insane, off = [], [], [], []
        for g in f.got:
            c = g.company
            stored = {r[0]: tuple(r[1:]) for r in tx.execute(
                """select trade_date, series, open, high, low, close, volume from equity.price_daily
                    where entity_id = %s and exchange = %s and trade_date between %s and %s""",
                (c.entity_id, c.exchange, g.start, g.end)).fetchall()}
            for b in g.bars:
                if b.day < g.start:
                    continue                                   # the overlap: compared in the checks, never written
                if b.off_cents:
                    off.append(f"{c.ticker} {b.day} ({', '.join(b.off_cents)})")
                if not (b.close > 0 and (b.high is None or b.low is None or b.high >= b.low)
                        and (b.low is None or b.close >= b.low) and (b.high is None or b.close <= b.high)):
                    insane.append(f"{c.ticker} {b.day}: open {b.open}, high {b.high}, low {b.low}, close {b.close}")
                    continue
                vals = (SERIES, b.open, b.high, b.low, b.close, b.volume)
                old = stored.get(b.day)
                if old is None:
                    inserts.append((c.entity_id, b.day, c.exchange, *vals, g.raw_file_id, task_run_id))
                elif old != vals:
                    changed.append(f"{c.ticker} {b.day}: stored {_fmt(old[1:])}, fetched {_fmt(vals[1:])}")
        if inserts:
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close,
                           volume, raw_file_id, task_run_id)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", inserts)
        self.result = {"inserted": len(inserts), "changed": changed, "insane": insane, "off_cents": off}
        return len(inserts)

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        out: list[Check] = []
        n = len(f.got)
        out.append(Check("charts_read", not f.failed, _names(f.failed) if f.failed else
                         f"{n} charts read" + (f"; nothing new for {len(f.idle)}" if f.idle else "")))

        wrong = [f"{g.company.ticker}: symbol {g.chart.symbol!r}" for g in f.got if g.chart.symbol != g.company.ticker]
        wrong += [f"{g.company.ticker}: currency {g.chart.currency!r}" for g in f.got if g.chart.currency != "USD"]
        out.append(Check("responses", not wrong, _names(wrong) if wrong else f"{n} charts, each its ticker, in USD"))

        gaps, extra, sessions = [], [], 0
        for g in f.got:
            have = {b.day for b in g.bars if b.day >= g.start}
            want = calendars.sessions(CALENDAR, g.start, g.end)
            sessions += len(want)
            miss = [d for d in want if d not in have]
            if miss:
                shown = ", ".join(str(d) for d in miss[:3]) + (f" and {len(miss) - 3} more" if len(miss) > 3 else "")
                gaps.append(f"{g.company.ticker} ({len(miss)}: {shown})")
            extra += [f"{g.company.ticker} {d}" for d in sorted(have - set(want))]
        out.append(Check("sessions_present", not gaps, ("no bar for: " + _names(gaps)) if gaps
                         else f"{sessions} company sessions, each with its bar" if sessions else "no session to load"))
        out.append(Check("calendar_proven", not extra,
                         ("a bar on a day the NYSE calendar calls closed: " + _names(extra)) if extra
                         else "no bar on a closed day"))

        out.append(Check("sane_prices", not r["insane"], _names(r["insane"]) if r["insane"]
                         else "close > 0 and low <= close <= high"))
        out.append(Check("cents", not r["off_cents"],
                         ("prices further than 0.2 of a cent from a whole cent: " + _names(r["off_cents"]))
                         if r["off_cents"] else "every price on a whole cent"))
        out.append(Check("unchanged", not r["changed"], _names(r["changed"]) if r["changed"]
                         else "no stored row differs from its re-load"))

        bad, compared = [], 0
        for g in f.got:
            if g.anchor is None:
                continue
            compared += 1
            back = next((b for b in g.bars if b.day == g.anchor), None)
            if back is None:
                bad.append(f"{g.company.ticker}: no bar for the stored {g.anchor}")
            elif abs(back.close - g.anchor_close) > TOLERANCE:
                bad.append(f"{g.company.ticker} {g.anchor}: stored {g.anchor_close}, fetched {back.close}"
                           + (f" (multiplied back by {back.scale})" if back.scale != 1 else ""))
        out.append(Check("overlap", not bad, _names(bad) if bad else
                         f"{compared} companies' last stored close came back unchanged" if compared
                         else "no stored session before the window (a first load)"))

        ex = [f"{g.company.ticker}: Yahoo {g.chart.exchange or 'none'}, listed {g.company.exchange}"
              for g in f.got if g.chart.price_exchange != g.company.exchange]
        out.append(Check("exchange", not ex, _names(ex) if ex else "each chart from the company's listing exchange",
                         blocking=False))
        resc = [f"{g.company.ticker}: " + ", ".join(f"{s.label} on {s.ex_date}" for s in g.chart.splits)
                for g in f.got if any(b.scale != 1 for b in g.bars)]
        out.append(Check("rescaled", True, ("bars multiplied back by their later splits: " + _names(resc)) if resc
                         else "no bar needed multiplying back", blocking=False))
        held = [f"{g.company.ticker} {d} (sent twice)" for g in f.got for d in g.chart.repeated]
        held += [f"{g.company.ticker} {d} (no close)" for g in f.got for d in g.chart.skipped
                 if g.start <= d <= g.end]
        out.append(Check("held_back", not held, _names(held) if held else "none", blocking=False))
        if f.yahoo_session is not None:
            out.append(Check("yahoo_session", f.yahoo_session == "crumb received", f.yahoo_session, blocking=False))
        return out


def _fmt(vals: tuple) -> str:
    return "(" + ", ".join("-" if v is None else str(v) for v in vals) + ")"
