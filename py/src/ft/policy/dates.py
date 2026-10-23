"""Meeting-day phrases as the banks write them, e.g. "February 4 to 6, 2020", "October 1, 3 and 4, 2019",
"July 30-August 1, 2018", "September 29, 30 and October 1, 2025", "April 6 - 8, 2022", "September 29-30 & October 1,
2020", "May 2 and 4, 2022". A month name sets the month for the day numbers after it; a four-digit year closes every
day before it that has none, so a span across a year end ("December 30, 2019 to January 1, 2020") reads right."""
from __future__ import annotations

import re
from datetime import date
from itertools import pairwise

MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"), start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9

_TOKEN = re.compile(r"[A-Za-z]+|\d+")


class DateParseError(ValueError):
    pass


def meeting_days(text: str, *, year: int | None = None) -> list[date]:
    """Every day the phrase names, in order. `year` applies to days the phrase leaves without one (the Fed's calendar
    rows, whose year is the panel's). Raises DateParseError if a day has no month or no year, or isn't a date."""
    month, pending, days = None, [], []
    for tok in _TOKEN.findall(text):
        low = tok.lower()
        if low in MONTHS:
            month = MONTHS[low]
        elif tok.isdigit() and len(tok) == 4:
            days += [(int(tok), m, d) for m, d in pending]
            pending = []
        elif tok.isdigit() and len(tok) <= 2:
            if month is None:
                raise DateParseError(f"a day before any month in {text!r}")
            pending.append((month, int(tok)))
    if pending:
        if year is None:
            raise DateParseError(f"no year for {pending} in {text!r}")
        days += [(year, m, d) for m, d in pending]
    if not days:
        raise DateParseError(f"no meeting days in {text!r}")
    try:
        out = [date(y, m, d) for y, m, d in days]
    except ValueError as e:
        raise DateParseError(f"{e} in {text!r}") from None
    # A span across a year end written with one year ("December 30 to January 1, 2020") gives the December days the
    # later year: step them back a year.
    for i in range(len(out) - 1):
        if out[i].month == 12 and out[i + 1].month == 1 and out[i].year == out[i + 1].year:
            out[:i + 1] = [d.replace(year=d.year - 1) for d in out[:i + 1]]
            break
    if any(a >= b for a, b in pairwise(out)):
        raise DateParseError(f"days out of order in {text!r}")
    return out


def span(text: str, *, year: int | None = None) -> tuple[date, date]:
    """(first day, last day) of a meeting-day phrase."""
    days = meeting_days(text, year=year)
    return days[0], days[-1]
