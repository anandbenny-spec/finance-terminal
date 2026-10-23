"""The RBI's Annual Policy page on the old site (Spec 01 §1.1, "RBI Annual Policy page", "RBI schedule"; Data Audit 01
#6, #18): one page per fiscal year, the current year at `Annualpolicy.aspx`, earlier years by a form post of the page's
own year selector (`hdnYear` plus the hidden submit button, with the page's hidden ASP.NET fields).

The page is a list of groups, newest first, each a date header, a heading and its items:
- a decision: "Bi-monthly Monetary Policy Statement, 2025-2026" (older years "Sixth Bi-monthly …", off-cycle
  "Monetary Policy Statement, 2022-23"), whose items include "Resolution of the Monetary Policy Committee (MPC) …"
  with a "Full Document" link (BS_PressReleaseDisplay.aspx?prid=…). The group's date is the decision date;
- minutes: "Minutes of the Monetary Policy Committee Meeting, February 4 to 6, 2026" (the meeting's days);
- the year's schedule: "Meeting Schedule of the Monetary Policy Committee for 2026-2027", whose rows are plain cells
  ("December 2, 3 and 4, 2026"), kept up to date when a meeting moves (Aug 2025: 5–7 became 4–6);
- notices: "Meeting of the Monetary Policy Committee – Rescheduled", "Additional Meeting of …", and the special
  meeting's release ("Meeting of the Monetary Policy Committee, November 3, 2022").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin

from lxml import html as lxml_html

from ft.policy import dates

BASE = "https://www.rbi.org.in/Scripts/"
ANNUAL_POLICY = BASE + "Annualpolicy.aspx"

_RESOLUTION = re.compile(r"Resolution of the Monetary Policy Committee", re.IGNORECASE)
_MINUTES = re.compile(r"^Minutes of the Monetary Policy Committee Meeting[,\s]+(?P<days>.+)$", re.IGNORECASE)
_SCHEDULE = re.compile(r"^Meeting Schedule of the Monetary Policy Committee for (?P<fy>\d{4})-(?P<end>\d{4})$", re.IGNORECASE)
_RESCHEDULE = re.compile(r"Reschedul", re.IGNORECASE)
_PRESS_CONFERENCE = re.compile(r"Press Conference", re.IGNORECASE)
_FULL_DOCUMENT = re.compile(r"\s*Full Document\s*$", re.IGNORECASE)
_GET_YEAR = re.compile(r"""GetYear\(["'](\d{4})["']\)""")
_NEXT_MEETING = re.compile(
    r"next meeting of the (?:MPC|Monetary Policy Committee)\s+(?:is|will be)\s+(?:scheduled|held)\s+"
    r"(?:during|on|from|for)\s+(?P<days>[A-Z][a-z]+\.?\s+\d[^.]*?\d{4})", re.IGNORECASE)


class PageError(ValueError):
    """The page doesn't have the shape this parser expects (a template change): fail the task, load nothing."""


@dataclass(frozen=True)
class Item:
    title: str
    url: str | None


@dataclass(frozen=True)
class Group:
    posted_on: date
    heading: str
    items: tuple[Item, ...] = ()
    rows: tuple[str, ...] = ()        # plain cells without a link (a schedule's dates)


@dataclass(frozen=True)
class Page:
    groups: tuple[Group, ...]
    years: dict[str, str] = field(default_factory=dict)      # "2019-2020" -> the hdnYear value ("2020")
    form: dict[str, str] = field(default_factory=dict)       # hidden fields, for the year selector's form post
    button: str | None = None                                # the hidden submit button's name


@dataclass(frozen=True)
class Resolution:
    decision_date: date
    url: str
    title: str
    days: tuple[date, date] | None    # from the title (2021-22 on: "… (MPC) February 8-10, 2022")
    has_press_conference: bool        # the decision's group lists a press conference


@dataclass(frozen=True)
class ScheduleRow:
    fiscal_year: str                  # "2026-2027"
    days: tuple[date, date]
    text: str


@dataclass(frozen=True)
class Notice:
    posted_on: date
    title: str
    url: str | None


@dataclass(frozen=True)
class Calendar:
    """What one or more pages say about meetings."""
    resolutions: tuple[Resolution, ...]
    minutes: dict[date, date]         # last day -> first day, from the minutes titles
    schedule: tuple[ScheduleRow, ...]
    reschedules: tuple[Notice, ...]


def _text(el) -> str:
    return " ".join(el.text_content().split())


def _date(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%b %d, %Y").date()
    except ValueError:
        raise PageError(f"not a group date: {text!r}") from None


def parse_page(content: bytes | str) -> Page:
    doc = lxml_html.fromstring(content)
    roots = doc.xpath('//div[@id="annualPolicy"]')
    if not roots:
        raise PageError("no div#annualPolicy: not the Annual Policy page, or its template changed")
    # Document order: a date header cell, a heading cell, then the cell holding the group's own table, whose cells
    # are the items (a bullet image cell, then the title cell with its link) or, for a schedule, plain date rows.
    groups: list[Group] = []
    posted: date | None = None
    for td in roots[0].iter("td"):
        if "tableheader" in (td.get("class") or ""):
            text = _text(td)
            if posted is None:
                posted = _date(text)
            else:
                groups.append(Group(posted, text))
                posted = None
            continue
        if not groups or td.xpath(".//table") or not (text := _text(td)):
            continue                    # before the first group, a group's container cell, or a bullet cell
        g = groups[-1]
        links = td.xpath(".//a[@href]")
        if links:
            title = _FULL_DOCUMENT.sub("", text) or _text(links[0])
            item = Item(title, urljoin(BASE, links[-1].get("href").strip()))
            groups[-1] = Group(g.posted_on, g.heading, g.items + (item,), g.rows)
        else:
            groups[-1] = Group(g.posted_on, g.heading, g.items, g.rows + (text,))
    if posted is not None:
        raise PageError(f"a date header ({posted}) without its heading")
    if not groups:
        raise PageError("the Annual Policy page lists no groups")
    years = {_text(a): m[1] for a in doc.xpath("//a[@onclick]") if (m := _GET_YEAR.search(a.get("onclick")))}
    form = {i.get("name"): i.get("value") or "" for i in doc.xpath("//input[@type='hidden'][@name]")}
    button = doc.xpath("string(//input[@id='btn']/@name)") or None
    return Page(tuple(groups), years, form, button)


def calendar(pages: list[Page]) -> Calendar:
    """The meetings the pages list: every resolution, the minutes' meeting days, the schedules and reschedule notices.
    A page's year can be fetched more than once (the current year is also a selectable year): duplicates collapse."""
    resolutions: dict[date, Resolution] = {}
    minutes: dict[date, date] = {}
    schedule: dict[tuple[str, date], ScheduleRow] = {}
    notices: dict[tuple[date, str], Notice] = {}
    for page in pages:
        for g in page.groups:
            sched = _SCHEDULE.match(g.heading)
            if sched:
                fy = f"{sched['fy']}-{sched['end']}"
                for row in g.rows:
                    try:
                        days = dates.span(row)
                    except dates.DateParseError as e:
                        raise PageError(f"schedule {fy}: {e}") from None
                    schedule[(fy, days[1])] = ScheduleRow(fy, days, row)
                continue
            press = any(_PRESS_CONFERENCE.search(i.title) for i in g.items)
            for item in g.items:
                if _RESOLUTION.search(item.title) and item.url and "prid=" in item.url:
                    days = None
                    if re.search(r"\d{4}\s*$", item.title):
                        try:
                            days = dates.span(_RESOLUTION.split(item.title, 1)[1])
                        except dates.DateParseError:
                            days = None
                    if days is not None and days[1] != g.posted_on:
                        raise PageError(f"resolution posted {g.posted_on} names meeting days ending {days[1]}: "
                                        f"{item.title!r}")
                    resolutions[g.posted_on] = Resolution(g.posted_on, item.url, item.title, days, press)
                elif (m := _MINUTES.match(item.title)):
                    try:
                        first, last = dates.span(m["days"])
                    except dates.DateParseError as e:
                        raise PageError(f"minutes title: {e}") from None
                    minutes[last] = first
                elif _RESCHEDULE.search(item.title):
                    notices[(g.posted_on, item.title)] = Notice(g.posted_on, item.title, item.url)
    return Calendar(tuple(sorted(resolutions.values(), key=lambda r: r.decision_date)), minutes,
                    tuple(sorted(schedule.values(), key=lambda r: r.days[1])),
                    tuple(sorted(notices.values(), key=lambda n: n.posted_on)))


def year_form(page: Page, year_value: str) -> dict[str, str]:
    """The form post that asks the page for another fiscal year (the kit's R1 route: Data Audit 01 #6)."""
    if not page.button:
        raise PageError("the Annual Policy page has no year-selector button (input#btn)")
    form = dict(page.form)
    form.update({"__EVENTTARGET": "", "__EVENTARGUMENT": "", "hdnYear": year_value, page.button: ""})
    return form


def fiscal_years(page: Page, first_end_year: int) -> list[tuple[str, str]]:
    """(label, hdnYear value) for each fiscal year the selector offers whose end year is first_end_year or later,
    oldest first: "2018-2019" ends in 2019."""
    out = []
    for label, value in page.years.items():
        m = re.fullmatch(r"(\d{4})-(\d{4})", label)
        if m and int(m[2]) >= first_end_year:
            out.append((label, value))
    return sorted(out, key=lambda lv: lv[0])


def next_meeting(text: str) -> tuple[date, date] | None:
    """The "next meeting" line of a resolution: "The next meeting of the MPC is scheduled during December 3 to 5,
    2026." -> its first and last day; None if the resolution has no such line."""
    m = _NEXT_MEETING.search(" ".join(text.split()))
    if not m:
        return None
    try:
        return dates.span(m["days"])
    except dates.DateParseError:
        return None
