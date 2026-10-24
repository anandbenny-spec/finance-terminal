"""Parsers for policy.market_series' two sources (Prompt 01 B4; Spec 01 §1.1): FRED's API and Yahoo Finance's chart
API, the endpoint the yfinance library reads. Both return (date, Decimal) points; nothing here touches the network
or the database.

- **FRED** (`series/observations`, JSON): each observation's `value` is a decimal string, or "." where FRED has no
  value for that day (a market holiday); "." is skipped, never stored as a number.
- **Yahoo** (`/v8/finance/chart/<symbol>`, interval 1d): one bar per session, its `timestamp` the session's start in
  UTC. A bar's date is that instant in the exchange's own zone (`meta.exchangeTimezoneName`). Closes come as binary
  floats (6687.31982421875 for 6687.32), so each is rounded back to the precision Yahoo quotes: 2 decimals for index
  points, 4 for an exchange rate. A bar whose session hasn't closed by the fetch time (the series' fixing time on its
  date, or for a series without one, a bar dated the exchange's today) is left out: it's a live price, not a close.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from zoneinfo import ZoneInfo

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
YAHOO_URL = "https://query2.finance.yahoo.com/v8/finance/chart/"
# Yahoo's session, as yfinance 1.7 gets it: a request to fc.yahoo.com sets the A3 cookie, then getcrumb returns the
# crumb that goes on each chart request.
YAHOO_COOKIE_URL = "https://fc.yahoo.com/"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
DECIMALS = {"index_points": 2, "inr_per_usd": 4}


class SourceError(Exception):
    """A response that isn't what was asked for, or can't be read."""


Point = tuple[date, Decimal]


def fred_params(code: str, start: date, end: date) -> dict[str, str]:
    """Query parameters (the API key goes separately, as a secret parameter)."""
    return {"series_id": code, "observation_start": start.isoformat(), "observation_end": end.isoformat(),
            "file_type": "json"}


def parse_fred(body: bytes, code: str) -> list[Point]:
    try:
        doc = json.loads(body)
    except ValueError:
        raise SourceError(f"FRED {code}: the response isn't JSON") from None
    if not isinstance(doc, dict) or "observations" not in doc:
        raise SourceError(f"FRED {code}: no observations in the response ({str(doc.get('error_message', ''))[:200]})"
                          if isinstance(doc, dict) else f"FRED {code}: unexpected response")
    out: dict[date, Decimal] = {}
    for o in doc["observations"]:
        raw = str(o.get("value", "")).strip()
        if raw == ".":
            continue
        try:
            d, v = date.fromisoformat(o["date"]), Decimal(raw)
        except (KeyError, ValueError, InvalidOperation):
            raise SourceError(f"FRED {code}: unreadable observation {o!r}"[:300]) from None
        if not v.is_finite():
            raise SourceError(f"FRED {code}: non-finite value on {d}")
        out[d] = v
    return sorted(out.items())


def parse_crumb(status: int, body: bytes) -> str | None:
    """getcrumb's answer: a short token on a 200, else None (an HTML page or "Too Many Requests" is no crumb)."""
    if status != 200:
        return None
    try:
        crumb = body.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if not crumb or len(crumb) > 64 or not crumb.isprintable() or any(c in crumb for c in " <>"):
        return None
    return crumb


def yahoo_url(symbol: str) -> str:
    return YAHOO_URL + quote(symbol, safe="")


def yahoo_params(start: date, end: date) -> dict[str, str]:
    """From the start of `start` to the start of the day after `end` (UTC), daily bars."""
    p1 = int(datetime.combine(start, time(0), ZoneInfo("UTC")).timestamp())
    p2 = int(datetime.combine(end, time(0), ZoneInfo("UTC")).timestamp()) + 86400
    return {"period1": str(p1), "period2": str(p2), "interval": "1d", "includePrePost": "false"}


@dataclass(frozen=True)
class YahooChart:
    symbol: str
    currency: str | None
    timezone: str
    points: list[Point]
    dropped_live: list[date]          # bars left out because their session hadn't closed at the fetch time


def parse_yahoo(body: bytes, symbol: str, unit: str, *, fetched_at: datetime, fixing_time: time | None,
                fixing_tz: str | None) -> YahooChart:
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
        tz = ZoneInfo(meta["exchangeTimezoneName"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise SourceError(f"Yahoo {symbol}: the chart has no result or exchange time zone") from None
    stamps = res.get("timestamp") or []
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    if len(stamps) != len(closes):
        raise SourceError(f"Yahoo {symbol}: {len(stamps)} timestamps but {len(closes)} closes")
    places = DECIMALS.get(unit)
    if places is None:
        raise SourceError(f"Yahoo {symbol}: no quoting precision for unit {unit}")
    q = Decimal(1).scaleb(-places)
    today = fetched_at.astimezone(tz).date()
    out: dict[date, Decimal] = {}
    live: list[date] = []
    for ts, c in zip(stamps, closes, strict=True):
        if c is None:
            continue                  # a session Yahoo lists without a close
        d = datetime.fromtimestamp(int(ts), tz).date()
        if fixing_time is not None and fixing_tz is not None:
            closed = datetime.combine(d, fixing_time, ZoneInfo(fixing_tz)) <= fetched_at
        else:
            closed = d < today
        if not closed:
            live.append(d)
            continue
        v = Decimal(repr(float(c))).quantize(q)
        if not v.is_finite():
            raise SourceError(f"Yahoo {symbol}: non-finite close on {d}")
        out[d] = v                    # a repeated date keeps the later bar
    return YahooChart(str(meta.get("symbol", "")), meta.get("currency"), meta["exchangeTimezoneName"],
                      sorted(out.items()), live)
