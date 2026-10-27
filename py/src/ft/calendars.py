"""Trading calendars (Spec 02A §4; 2A-03): the one place that answers "was this a trading session, and when did it close?".

Exchanges: ``xnse`` (NSE; BSE's equity segment shares its holidays) and ``xnys`` (NYSE). The curated files are
config/calendars/xnse.yaml and xnys.yaml. A weekday is a session unless it is a listed holiday; Saturdays and Sundays are
closed by rule, unless a special session (Muhurat, Budget day, a Saturday DR session) is listed for the date. A special
session wins over a holiday entry (Diwali Laxmi Pujan is a holiday of the regular session and has an evening session).

A year the file doesn't cover raises ``CalendarYearError``: the calendar never guesses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "calendars"

_FILES = {"xnse": "xnse.yaml", "xnys": "xnys.yaml"}
_ZONES = {"xnse": "Asia/Kolkata", "xnys": "America/New_York"}
_REGULAR_CLOSE = {"xnse": time(15, 30), "xnys": time(16, 0)}


class CalendarYearError(ValueError):
    """The calendar file doesn't cover this year."""


@dataclass(frozen=True)
class _Calendar:
    years: frozenset[int]
    holidays: frozenset[date]
    special: dict[date, tuple[time, time, str]]  # date -> (open, close, kind)
    early_close: dict[date, time]


def _t(text: str) -> time:
    hh, mm = text.split(":")
    return time(int(hh), int(mm))


@cache
def _load(exchange: str) -> _Calendar:
    if exchange not in _FILES:
        raise ValueError(f"unknown exchange {exchange!r}; known: {sorted(_FILES)}")
    raw = yaml.safe_load((CONFIG_DIR / _FILES[exchange]).read_text(encoding="utf-8"))
    years, holidays = set(), set()
    special: dict[date, tuple[time, time, str]] = {}
    early: dict[date, time] = {}
    for year, body in raw.items():
        years.add(int(year))
        holidays.update(h["date"] for h in body.get("holidays") or [])
        for s in body.get("special_sessions") or []:
            special[s["date"]] = (_t(s["open"]), _t(s["close"]), s["kind"])
        for e in body.get("early_closes") or []:
            early[e["date"]] = _t(e["close"])
    return _Calendar(frozenset(years), frozenset(holidays), special, early)


def covered_years(exchange: str) -> list[int]:
    """The years the exchange's file covers, ascending."""
    return sorted(_load(exchange).years)


def _check(cal: _Calendar, exchange: str, d: date) -> None:
    if d.year not in cal.years:
        raise CalendarYearError(f"{exchange} calendar does not cover {d.year} (covers {sorted(cal.years)})")


def is_session(exchange: str, d: date) -> bool:
    """True if the exchange held a trading session on ``d`` (a special session counts)."""
    cal = _load(exchange)
    _check(cal, exchange, d)
    if d in cal.special:
        return True
    return d.weekday() < 5 and d not in cal.holidays


def sessions(exchange: str, start: date, end: date) -> list[date]:
    """Every session from ``start`` to ``end`` inclusive."""
    out, d = [], start
    while d <= end:
        if is_session(exchange, d):
            out.append(d)
        d += timedelta(days=1)
    return out


def previous_session(exchange: str, d: date) -> date:
    """The last session strictly before ``d``."""
    p = d - timedelta(days=1)
    while not is_session(exchange, p):
        p -= timedelta(days=1)
    return p


def next_session(exchange: str, d: date) -> date:
    """The first session strictly after ``d``."""
    n = d + timedelta(days=1)
    while not is_session(exchange, n):
        n += timedelta(days=1)
    return n


def special_session(exchange: str, d: date) -> str | None:
    """The kind of special session on ``d`` (muhurat, budget, special, dr), or None."""
    cal = _load(exchange)
    _check(cal, exchange, d)
    return cal.special[d][2] if d in cal.special else None


def close_time(exchange: str, d: date) -> datetime:
    """When the session on ``d`` closed, as an aware datetime in the exchange's zone.

    15:30 Asia/Kolkata for NSE and 16:00 America/New_York for NYSE, unless the day's entry says otherwise (a special
    session's close, or an early close). Raises if ``d`` was not a session.
    """
    if not is_session(exchange, d):
        raise ValueError(f"{exchange} had no session on {d}")
    cal = _load(exchange)
    if d in cal.special:
        at = cal.special[d][1]
    else:
        at = cal.early_close.get(d, _REGULAR_CLOSE[exchange])
    return datetime.combine(d, at, tzinfo=ZoneInfo(_ZONES[exchange]))
