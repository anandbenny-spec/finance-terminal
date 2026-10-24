"""The FOMC's meeting calendars (Spec 01 §1.1, "Fed calendars"; Data Audit 01 #1, #18): `fomccalendars.htm` (the last
five years and the next, 2021-2027 in Oct 2026) and the historical pages `fomchistoricalYYYY.htm` (2018-2020 here).
The calendar is authoritative for which document is a meeting's statement (Spec 01 §1.1).

Two templates:
- the current page: a panel per year ("2026 FOMC Meetings") of rows, each a month cell ("January", "Apr/May"), a date
  cell ("27-28", "16-17*", "22 (notation vote)") and the meeting's links ("Statement: PDF | HTML", "Press Conference",
  "Minutes: PDF | HTML (Released February 18, 2026)"); upcoming meetings have no links yet;
- a historical page: a panel per meeting headed "January 29-30 Meeting - 2019", "April/May 30-1 Meeting - 2019",
  "March 2 (unscheduled) Meeting - 2020", "March 17-18 (cancelled) Meeting - 2020", "October 4 (unscheduled) - 2019"
  or "March 19 (notation vote) - 2020", with links labelled "Statement", "Press Conference", "Minutes (Released …)".

A `*` marks a meeting with a Summary of Economic Projections. A notation vote is a vote without a meeting (the Committee
voting on a press release or its longer-run statement): listed on the calendar, but not a meeting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from lxml import html as lxml_html

from ft.policy.dates import MONTHS

SITE = "https://www.federalreserve.gov"
CALENDAR = SITE + "/monetarypolicy/fomccalendars.htm"


def historical(year: int) -> str:
    return f"{SITE}/monetarypolicy/fomchistorical{year}.htm"


_YEAR_PANEL = re.compile(r"^(\d{4}) FOMC Meetings$")
_HEADING = re.compile(r"^(?P<month>[A-Za-z/]+)\s+(?P<days>\d{1,2}(?:-\d{1,2})?)(?P<sep>\*)?\s*"
                      r"(?:\((?P<label>[a-z ]+)\))?\s*(?:Meeting)?\s*-\s*(?P<year>\d{4})$")
_ROW_DATE = re.compile(r"^(?P<days>\d{1,2}(?:-\d{1,2})?)(?P<sep>\*)?\s*(?:\((?P<label>[a-z ]+)\))?$")
_STATEMENT = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm$")
_MINUTES = re.compile(r"/monetarypolicy/fomcminutes(\d{8})\.htm$")
_PRESCONF = re.compile(r"/monetarypolicy/fomcpres+conf(\d{8})\.htm$")     # "fomcpressconf" once (Jan 2026)
_RELEASED = re.compile(r"Released\s+([A-Z][a-z]+ \d{1,2}, \d{4})")
LABELS = ("unscheduled", "cancelled", "notation vote")


class PageError(ValueError):
    """The page doesn't have the shape this parser expects (a template change): fail the task, load nothing."""


@dataclass(frozen=True)
class Row:
    """One calendar entry as the page writes it."""
    page_url: str
    text: str                        # the month and date as written, for messages
    first: date
    last: date
    label: str | None                # 'unscheduled' | 'cancelled' | 'notation vote' | None
    sep: bool
    statement_url: str | None
    minutes_url: str | None
    minutes_released: date | None
    presconf_url: str | None

    @property
    def statement_date(self) -> date | None:
        """The date in the statement's URL (monetaryYYYYMMDDa.htm): the day it was released."""
        m = _STATEMENT.search(self.statement_url or "")
        return datetime.strptime(m[1], "%Y%m%d").date() if m else None


def _text(el) -> str:
    return " ".join(el.text_content().split())


def _days(month_text: str, days_text: str, year: int, where: str) -> tuple[date, date]:
    months = [m.strip().lower() for m in month_text.split("/")]
    if not months or any(m not in MONTHS for m in months) or len(months) > 2:
        raise PageError(f"{where}: unknown month {month_text!r}")
    nums = [int(d) for d in days_text.split("-")]
    m1, m2 = MONTHS[months[0]], MONTHS[months[-1]]
    try:
        first = date(year, m1, nums[0])
        last = date(year, m2, nums[-1])
    except ValueError as e:
        raise PageError(f"{where}: {e} ({month_text} {days_text}, {year})") from None
    if last < first or (last - first).days > 3:
        raise PageError(f"{where}: {month_text} {days_text}, {year} isn't a meeting span")
    return first, last


def _links(el, page_url: str) -> tuple[str | None, str | None, date | None, str | None]:
    statement = minutes = presconf = None
    for a in el.xpath(".//a[@href]"):
        href = urljoin(page_url, a.get("href").strip())
        path = href.split("?", 1)[0]
        if _STATEMENT.search(path) and _text(a) in ("HTML", "Statement") and statement is None:
            statement = path
        elif _MINUTES.search(path) and minutes is None:
            minutes = path
        elif _PRESCONF.search(path) and presconf is None:
            presconf = path
    released = None
    if (m := _RELEASED.search(_text(el))) and minutes:
        released = datetime.strptime(m[1], "%B %d, %Y").date()
    return statement, minutes, released, presconf


def parse_page(content: bytes | str, page_url: str) -> list[Row]:
    """Every entry on a calendar page (current or historical), in page order. Raises PageError on an entry it can't
    read, so a template change fails the task instead of dropping meetings."""
    doc = lxml_html.fromstring(content)
    rows: list[Row] = []
    for panel in doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' panel ')]"):
        h4 = panel.xpath("./div[contains(@class,'panel-heading')]//h4")
        if h4 and (m := _YEAR_PANEL.match(_text(h4[0]))):
            year = int(m[1])
            for r in panel.xpath(".//div[contains(concat(' ', normalize-space(@class), ' '), ' fomc-meeting ')]"):
                month = r.xpath(".//div[contains(@class,'fomc-meeting__month')]")
                day = r.xpath(".//div[contains(@class,'fomc-meeting__date')]")
                if not month or not day:
                    raise PageError(f"{page_url}: a {year} row without its month or date cell")
                text = f"{_text(month[0])} {_text(day[0])}, {year}"
                dm = _ROW_DATE.match(_text(day[0]))
                if not dm or (dm["label"] and dm["label"] not in LABELS):
                    raise PageError(f"{page_url}: can't read the date cell of {text!r}")
                first, last = _days(_text(month[0]), dm["days"], year, page_url)
                rows.append(Row(page_url, text, first, last, dm["label"], bool(dm["sep"]), *_links(r, page_url)))
            continue
        h5 = panel.xpath(".//h5[contains(@class,'panel-heading')]")
        if not h5:
            continue
        text = _text(h5[0])
        hm = _HEADING.match(text)
        if not hm or (hm["label"] and hm["label"] not in LABELS):
            raise PageError(f"{page_url}: can't read the panel heading {text!r}")
        first, last = _days(hm["month"], hm["days"], int(hm["year"]), page_url)
        rows.append(Row(page_url, text, first, last, hm["label"], bool(hm["sep"]), *_links(panel, page_url)))
    if not rows:
        raise PageError(f"{page_url}: no meetings found (not a calendar page, or its template changed)")
    # FAILURE DRILL (Prompt 01 D2, issue #25): a deliberately broken parser. Every decision day comes out one day
    # late, so the blocking check `statement_dates` must fail and the load roll back. This branch is never merged.
    rows = [replace(r, last=r.last + timedelta(days=1)) for r in rows]
    return rows
