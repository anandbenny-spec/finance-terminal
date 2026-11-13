"""US prices, splits and dividends from Yahoo Finance's chart API (Spec 02A.1 §5.3; job US-03, #402). Pure: nothing here
touches the network or the database.

The chart API (`/v8/finance/chart/<ticker>`, interval 1d, `events=div,splits`) is the endpoint the yfinance library
reads, and the one `policy.market_series` already calls through the shared HTTP client with Chrome's fingerprint and
Yahoo's crumb (Data Audit 02 B3: US prices from yfinance under the non-commercial framing). One response holds a
ticker's daily bars (open, high, low, close, volume), each session's bar stamped at its open in New York, and the
splits and dividends whose ex-dates fall inside the window asked for.

**Yahoo scales history to its latest split** (Data Audit 02a finding 12): a bar dated before a split's ex-date comes
back divided by the split's ratio (its volume multiplied by it), and so does a dividend's amount, as of the fetch. The
split is in the same response whenever the window reaches past its ex-date, because the window always runs to the
latest session. `parse_chart` undoes that scaling with the response's own split events: each bar and dividend dated
before a split's ex-date is multiplied back by the ratio (volume divided), so every value is the price as traded on its
day, whenever it was fetched. That makes the stored series one basis, like India's bhavcopy prices:
- a split on or before a company's first stored session never touches a stored bar, so its factor is 1 (`method`
  `source_adjusted`: Spec 02A.1 §5.3);
- a split after it gets its ratio factor and Spec 02A §5.2's plausibility check, so `equity.price_adjusted` removes the
  step once, whether the split came before the backfill (Apple's 4:1 of 31 Aug 2020) or after a weekly run had
  already stored the days before it (a week's response that runs across the ex-date is unscaled the same way).
Decided by Claude (standing delegation, 24 Oct 2026; #402): storing Yahoo's scaled values as fetched would double-adjust
every split since 2019 under §5.3's rule, and a weekly window that runs across an ex-date would store a step at the
week's start that no factor can remove.

Prices are rounded to cents: a NYSE or Nasdaq trade in a stock priced at $1 or more is quoted in cents, and Yahoo's
values are single-precision floats (271.489990234375 for 271.49), so an unscaled value lands within a few thousandths
of a cent of the traded price. `CENTS_SLACK` bounds how far from a cent a value may sit; one further away is named by
the task (it would mean the unscaling, or Yahoo's own scaling, is not what this module assumes). Dividends keep up to
six decimals (some are declared in tenths of a cent).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from math import gcd
from zoneinfo import ZoneInfo

from ft.policy.series_sources import SourceError, yahoo_url

__all__ = [
    "CENTS_SLACK",
    "EXCHANGES",
    "Bar",
    "Chart",
    "Dividend",
    "SourceError",
    "Split",
    "chart_params",
    "parse_chart",
    "yahoo_url",
]

NEW_YORK = ZoneInfo("America/New_York")
CENT = Decimal("0.01")
CENTS_SLACK = Decimal("0.002")             # an unscaled price further than this from a whole cent is named
DIVIDEND_PLACES = Decimal("0.000001")
SIMPLE = 50                                # a split ratio reduces to this or less on each side
# Yahoo's `meta.exchangeName` -> equity.company.price_exchange (Nasdaq's three tiers are one exchange)
EXCHANGES = {"NYQ": "NYSE", "NYS": "NYSE", "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ"}


def chart_params(start: date, end: date) -> dict[str, str]:
    """Daily bars and events from the start of `start` to the end of `end` (UTC days: a New York session's bar is
    stamped 09:30 local, inside its own UTC day)."""
    p1 = int(datetime.combine(start, time(0), ZoneInfo("UTC")).timestamp())
    p2 = int(datetime.combine(end, time(0), ZoneInfo("UTC")).timestamp()) + 86400
    return {"period1": str(p1), "period2": str(p2), "interval": "1d", "events": "div,splits",
            "includePrePost": "false"}


@dataclass(frozen=True)
class Bar:
    day: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    scale: Decimal = Decimal(1)        # the product of the later splits' ratios it was multiplied back by
    off_cents: tuple[str, ...] = ()    # fields whose unscaled value sat more than CENTS_SLACK from a cent


@dataclass(frozen=True)
class Split:
    ex_date: date
    numerator: Decimal                 # shares after (4 in a 4:1 split; 1 in a 1:10 reverse split)
    denominator: Decimal               # shares before
    label: str                         # Yahoo's splitRatio, e.g. "4:1"

    @property
    def ratio(self) -> Decimal:
        """Shares after per share before: what Yahoo divides an earlier price by."""
        return self.numerator / self.denominator

    @property
    def whole(self) -> bool:
        """Both sides whole numbers."""
        return self.numerator == self.numerator.to_integral_value() and \
            self.denominator == self.denominator.to_integral_value()

    @property
    def simple(self) -> bool:
        """A ratio a split is declared in: whole numbers that reduce to SIMPLE or less on each side (4:1, 3:2, 20:1,
        1:10). Yahoo also lists a spin-off as a 'split' whose ratio is the price drop it caused, such as Merck's
        1048:1000 (Organon, 3 Jun 2021), IBM's 1046:1000 (Kyndryl, 4 Nov 2021), 3M's 1196:1000 (Solventum, 1 Apr
        2024) and Honeywell's 1061:1000 (30 Oct 2025): those are not splits (the share count doesn't change), so
        the task records them as `other` and holds their factor for review (the first dev backfill, 11 Nov 2026)."""
        if not self.whole or self.numerator == self.denominator:
            return False
        n, d = int(self.numerator), int(self.denominator)
        g = gcd(n, d)
        return max(n // g, d // g) <= SIMPLE


@dataclass(frozen=True)
class Dividend:
    ex_date: date
    amount: Decimal                    # per share, as traded on its ex-date (unscaled)
    scale: Decimal = Decimal(1)


@dataclass
class Chart:
    symbol: str
    currency: str | None
    exchange: str | None               # Yahoo's code (NMS, NYQ, ...)
    timezone: str
    bars: list[Bar]
    splits: list[Split]
    dividends: list[Dividend]
    dropped_live: list[date] = field(default_factory=list)   # bars whose session hadn't closed at the fetch time
    repeated: list[date] = field(default_factory=list)       # dates sent twice with different values: held back
    skipped: list[date] = field(default_factory=list)        # sessions listed without a close

    @property
    def price_exchange(self) -> str | None:
        return EXCHANGES.get(self.exchange or "")


def _dec(v, what: str, symbol: str) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(repr(float(v)))
    except (TypeError, ValueError, InvalidOperation):
        raise SourceError(f"Yahoo {symbol}: unreadable {what} {v!r}"[:200]) from None
    if not d.is_finite():
        raise SourceError(f"Yahoo {symbol}: non-finite {what}")
    return d


def _cents(v: Decimal | None) -> tuple[Decimal | None, bool]:
    """(the value rounded to cents, whether it sat within CENTS_SLACK of that cent)."""
    if v is None:
        return None, True
    r = v.quantize(CENT)
    return r, abs(v - r) <= CENTS_SLACK


def _strip(d: Decimal) -> Decimal:
    """Trailing zeros dropped, never in exponent form (0.820000 -> 0.82, 1.000000 -> 1)."""
    return d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()


def _later(splits: list[Split], d: date) -> Decimal:
    """The product of the ratios of the splits whose ex-date is after `d`: what Yahoo divided `d`'s price by."""
    s = Decimal(1)
    for x in splits:
        if x.ex_date > d:
            s *= x.ratio
    return s


def _day(ts, tz: ZoneInfo, symbol: str) -> date:
    try:
        return datetime.fromtimestamp(int(ts), tz).date()
    except (TypeError, ValueError, OverflowError, OSError):
        raise SourceError(f"Yahoo {symbol}: unreadable timestamp {ts!r}"[:200]) from None


def parse_chart(body: bytes, symbol: str, *, fetched_at: datetime, close_at) -> Chart:
    """One chart response. `close_at(day)` gives a session's close as an aware datetime (ft.calendars.close_time for
    xnys), or None for a day that wasn't a session: a bar whose close hadn't passed at `fetched_at` is a live price and
    is left out (`dropped_live`). A bar dated on a non-session is kept and left for the task's calendar check."""
    try:
        doc = json.loads(body)
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise SourceError(f"Yahoo {symbol}: the response isn't a chart") from None
    if chart.get("error"):
        raise SourceError(f"Yahoo {symbol}: {str(chart['error'])[:200]}")
    try:
        res = chart["result"][0]
        meta = res["meta"]
        tzname = meta["exchangeTimezoneName"]
        tz = ZoneInfo(tzname)
    except (KeyError, IndexError, TypeError, ValueError):
        raise SourceError(f"Yahoo {symbol}: the chart has no result or exchange time zone") from None

    events = res.get("events") or {}
    splits: list[Split] = []
    for ev in (events.get("splits") or {}).values():
        num, den = _dec(ev.get("numerator"), "split numerator", symbol), _dec(ev.get("denominator"), "split denominator",
                                                                               symbol)
        if not num or not den or num <= 0 or den <= 0:
            raise SourceError(f"Yahoo {symbol}: a split without a positive ratio ({ev!r})"[:200])
        splits.append(Split(_day(ev.get("date"), tz, symbol), num, den, str(ev.get("splitRatio") or f"{num}:{den}")))
    splits.sort(key=lambda s: s.ex_date)
    if len({s.ex_date for s in splits}) != len(splits):
        raise SourceError(f"Yahoo {symbol}: two splits on one ex-date")

    dividends: list[Dividend] = []
    for ev in (events.get("dividends") or {}).values():
        amt = _dec(ev.get("amount"), "dividend", symbol)
        if amt is None or amt < 0:
            raise SourceError(f"Yahoo {symbol}: a dividend without an amount ({ev!r})"[:200])
        d = _day(ev.get("date"), tz, symbol)
        k = _later(splits, d)
        dividends.append(Dividend(d, _strip((amt * k).quantize(DIVIDEND_PLACES)), k))
    dividends.sort(key=lambda x: x.ex_date)

    stamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = {k: quote.get(k) or [] for k in ("open", "high", "low", "close", "volume")}
    if any(len(v) != len(stamps) for k, v in cols.items() if k == "close" or v):
        raise SourceError(f"Yahoo {symbol}: {len(stamps)} timestamps but the quote columns differ in length")

    seen: dict[date, Bar] = {}
    repeated: set[date] = set()
    live: list[date] = []
    skipped: list[date] = []
    for i, ts in enumerate(stamps):
        d = _day(ts, tz, symbol)
        c = cols["close"][i]
        if c is None:
            skipped.append(d)
            continue
        closes = close_at(d)
        if closes is not None and closes > fetched_at:
            live.append(d)
            continue
        k = _later(splits, d)
        vals, off = {}, []
        for name in ("open", "high", "low", "close"):
            raw = _dec(cols[name][i] if cols[name] else None, name, symbol)
            v, ok = _cents(None if raw is None else raw * k)
            vals[name] = v
            if not ok:
                off.append(name)
        vol = cols["volume"][i] if cols["volume"] else None
        volume = None if vol is None else int((Decimal(int(vol)) / k).to_integral_value())
        bar = Bar(d, vals["open"], vals["high"], vals["low"], vals["close"], volume, k, tuple(off))
        if d in seen and seen[d] != bar:
            repeated.add(d)
        seen.setdefault(d, bar)
    bars = [b for d, b in sorted(seen.items()) if d not in repeated]
    return Chart(str(meta.get("symbol", "")), meta.get("currency"), meta.get("exchangeName"), tzname, bars, splits,
                 dividends, live, sorted(repeated), skipped)
