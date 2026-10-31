"""The calendar parsers and meeting rules (Prompt 01 B3; Spec 01 §2 "Meetings"), on short excerpts of the real pages
(CI never sees whole pages). The last test reads the full saved pages when they're on this machine (app/data/fixtures,
git-ignored: setup/save_fed_fixtures.sh and the module 1 kit) and checks the counts B3's done-when records."""
import os
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ft.policy import dates, fed_calendar, meetings, rbi_calendar
from ft.policy.meetings import CalendarError

NY, IST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")
FIXTURES = Path(os.environ.get("FT_FIXTURES", Path(__file__).resolve().parents[2] / "data" / "fixtures"))


# ---------------------------------------------------------------------------------------------- meeting-day phrases
@pytest.mark.parametrize("text, first, last", [
    ("February 4 to 6, 2020", date(2020, 2, 4), date(2020, 2, 6)),
    ("October 1, 3 and 4, 2019", date(2019, 10, 1), date(2019, 10, 4)),
    ("July 30-August 1, 2018", date(2018, 7, 30), date(2018, 8, 1)),
    ("September 29, 30 and October 1, 2025", date(2025, 9, 29), date(2025, 10, 1)),
    ("September 29 to October 1, 2025", date(2025, 9, 29), date(2025, 10, 1)),
    ("September 29-30 & October 1, 2020", date(2020, 9, 29), date(2020, 10, 1)),
    ("April 6 - 8, 2022", date(2022, 4, 6), date(2022, 4, 8)),
    ("May 2 and 4, 2022", date(2022, 5, 2), date(2022, 5, 4)),
    ("March 24, 26 and 27, 2020", date(2020, 3, 24), date(2020, 3, 27)),
    ("December 30, 2019 to January 1, 2020", date(2019, 12, 30), date(2020, 1, 1)),
    ("December 30 to January 1, 2020", date(2019, 12, 30), date(2020, 1, 1)),
])
def test_meeting_days(text, first, last):
    assert dates.span(text) == (first, last)


@pytest.mark.parametrize("text", ["4 to 6, 2020", "February 30, 2020", "February 6 and 4, 2020", "no dates here"])
def test_meeting_days_refuses_what_it_cant_read(text):
    with pytest.raises(dates.DateParseError):
        dates.span(text)


# ---------------------------------------------------------------------------------------------- RBI Annual Policy page
def rbi_group(posted: str, heading: str, cells: str) -> str:
    return (f'<tr><td class="tableheader"><b>{posted}</b></td></tr><tr><td class="tableheader"><b>{heading}</b></td></tr>'
            f'<tr><td><table width="80%" class="tablebg">{cells}</table></td></tr>')


def rbi_item(title: str, href: str, full_document: bool = False) -> str:
    link = f'<a href="{href}" target="_blank" class=\'link2\'>{"Full Document" if full_document else title}</a>'
    body = f"{title} <BR><DIR><li>{link}</li></DIR>" if full_document else link
    return (f"<TR vAlign='top' class='tablecontent1'><TD width=\"6%\" align=\"center\"><img src='../Images/bullet3.gif'>"
            f"</TD><TD width=\"376%\" colSpan='2'>{body}</TD></TR>")


def rbi_page(*groups: str, years: dict[str, str] | None = None) -> str:
    years = years or {"2026-2027": "2027", "2025-2026": "2026", "2019-2020": "2020", "2018-2019": "2019",
                      "2017-2018": "2018"}
    tree = "".join(f"<li><a class=\"year_tree\" href=\"#\" onclick='GetYear(\"{v}\")'>{k}</a></li>" for k, v in years.items())
    return ("<html><body><form><input type=\"hidden\" name=\"__VIEWSTATE\" value=\"vs\"/>"
            "<input type=\"hidden\" name=\"hdnYear\" id=\"hdnYear\" value=\"\"/>"
            "<input type=\"submit\" name=\"UsrFontCntr$btn\" value=\"\" id=\"btn\"/>"
            "<div id=\"annualPolicy\"><table width=\"100%\">" + "".join(groups) + "</table></div>"
            f"<div class=\"grid_tree\"><ul>{tree}</ul></div></form></body></html>")


PR = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid="

# Excerpts of the 2019-20, 2020-21 and 2026-27 pages (the kit's annualpolicy_*.html).
RBI_2019_20 = rbi_page(
    rbi_group("Mar 27, 2020", "Seventh Bi-monthly Monetary Policy Statement, 2019-20",
              rbi_item("RBI Governor’s address to the media", "https://youtu.be/99i3lVow5gQ")
              + rbi_item("Resolution of the Monetary Policy Committee (MPC), Reserve Bank of India", PR + "49581", True)),
    rbi_group("Feb 20, 2020", "Minutes of the Monetary Policy Committee",
              rbi_item("Minutes of the Monetary Policy Committee Meeting February 4 to 6, 2020", PR + "49424")),
    rbi_group("Feb 06, 2020", "Sixth Bi-monthly Monetary Policy Statement for 2019-20",
              rbi_item("Webcasting of RBI Press Conference", "https://youtu.be/TDvHa6WdL20")
              + rbi_item("Resolution of the Monetary Policy Committee (MPC), Reserve Bank of India", PR + "49342", True)),
)
RBI_2020_21 = rbi_page(
    rbi_group("Apr 13, 2020", "Minutes of the Monetary Policy Committee",
              rbi_item("Minutes of the Monetary Policy Committee Meeting March 24, 26 and 27, 2020", PR + "49659")))
RBI_2026_27 = rbi_page(
    rbi_group("Oct 07, 2026", "Bi-monthly Monetary Policy Statement, 2026-2027",
              rbi_item("Resolution of the Monetary Policy Committee (MPC) October 5 to 7, 2026", PR + "63742", True)),
    rbi_group("Aug 19, 2026", "Minutes of the Monetary Policy Committee",
              rbi_item("Minutes of the Monetary Policy Committee Meeting, August 3 to 5, 2026", PR + "63403")),
    rbi_group("Aug 05, 2026", "Bi-monthly Monetary Policy Statement, 2026-2027",
              rbi_item("Resolution of the Monetary Policy Committee (MPC) August 3 to 5, 2026", PR + "63287", True)),
    rbi_group("Jun 06, 2025", "Meeting of the Monetary Policy Committee",
              rbi_item("Meeting of the Monetary Policy Committee (MPC) – Rescheduled", PR + "60612")),
    rbi_group("Mar 23, 2026", "Meeting Schedule of the Monetary Policy Committee for 2026-2027",
              "".join(f"<tr><td>{d}</td></tr>" for d in (
                  "August 3, 4 and 5, 2026", "October 5, 6 and 7, 2026", "December 2, 3 and 4, 2026",
                  "February 3, 4 and 5, 2027"))),
)


def test_rbi_page_groups_items_and_form():
    page = rbi_calendar.parse_page(RBI_2019_20)
    assert [(g.posted_on, g.heading) for g in page.groups][:2] == [
        (date(2020, 3, 27), "Seventh Bi-monthly Monetary Policy Statement, 2019-20"),
        (date(2020, 2, 20), "Minutes of the Monetary Policy Committee")]
    res = page.groups[0].items[1]
    assert res.title == "Resolution of the Monetary Policy Committee (MPC), Reserve Bank of India"
    assert res.url == PR + "49581"
    assert page.years["2019-2020"] == "2020" and page.button == "UsrFontCntr$btn"
    form = rbi_calendar.year_form(page, "2020")
    assert form["hdnYear"] == "2020" and form["UsrFontCntr$btn"] == "" and form["__VIEWSTATE"] == "vs"
    assert rbi_calendar.fiscal_years(page, 2019) == [("2018-2019", "2019"), ("2019-2020", "2020"),
                                                     ("2025-2026", "2026"), ("2026-2027", "2027")]


def test_rbi_calendar_resolutions_minutes_schedule_notices():
    cal = rbi_calendar.calendar([rbi_calendar.parse_page(RBI_2019_20), rbi_calendar.parse_page(RBI_2026_27),
                                 rbi_calendar.parse_page(RBI_2026_27)])          # the same year twice collapses
    assert [r.decision_date for r in cal.resolutions] == [date(2020, 2, 6), date(2020, 3, 27), date(2026, 8, 5),
                                                         date(2026, 10, 7)]
    by = {r.decision_date: r for r in cal.resolutions}
    assert by[date(2020, 2, 6)].days is None and by[date(2020, 2, 6)].has_press_conference
    assert by[date(2026, 10, 7)].days == (date(2026, 10, 5), date(2026, 10, 7))
    assert cal.minutes == {date(2020, 2, 6): date(2020, 2, 4), date(2026, 8, 5): date(2026, 8, 3)}
    assert cal.minutes_links == {date(2020, 2, 6): PR + "49424", date(2026, 8, 5): PR + "63403"}   # B6 part 2
    assert [s.days for s in cal.schedule][-2:] == [(date(2026, 12, 2), date(2026, 12, 4)),
                                                   (date(2027, 2, 3), date(2027, 2, 5))]
    assert [n.url for n in cal.reschedules] == [PR + "60612"]


@pytest.mark.parametrize("title,days", [
    ("Minutes of the Monetary Policy Committee Meeting December 3-5, 2018", (date(2018, 12, 3), date(2018, 12, 5))),
    ("Minutes of the Monetary Policy Committee Meeting, September 29, 30 and October 1, 2025",
     (date(2025, 9, 29), date(2025, 10, 1))),
    ("Minutes of the  Monetary Policy Committee Meeting, May 2 and 4, 2022", (date(2022, 5, 2), date(2022, 5, 4)))])
def test_rbi_minutes_days(title, days):
    assert rbi_calendar.minutes_days(title) == days


def test_rbi_minutes_days_refuses_another_title():
    with pytest.raises(dates.DateParseError):
        rbi_calendar.minutes_days("Resolution of the Monetary Policy Committee (MPC) October 5 to 7, 2026")


def test_rbi_page_template_change_fails():
    with pytest.raises(rbi_calendar.PageError):
        rbi_calendar.parse_page("<html><body><p>Access denied</p></body></html>")
    with pytest.raises(rbi_calendar.PageError):      # a resolution whose title disagrees with its group's date
        rbi_calendar.calendar([rbi_calendar.parse_page(rbi_page(rbi_group(
            "Oct 08, 2026", "Bi-monthly Monetary Policy Statement, 2026-2027",
            rbi_item("Resolution of the Monetary Policy Committee (MPC) October 5 to 7, 2026", PR + "1", True))))])


def test_rbi_next_meeting_line():
    text = ("... 29. The minutes of the MPC’s meeting will be published on October 21, 2026.\n\n30. The next meeting of "
            "the MPC is scheduled during December 2 to 4, 2026.\n\n(Puneet Pancholy) Chief General Manager")
    assert rbi_calendar.next_meeting(text) == (date(2026, 12, 2), date(2026, 12, 4))
    assert rbi_calendar.next_meeting("The next meeting of the MPC will be held from February 3 to 5, 2027.") == \
        (date(2027, 2, 3), date(2027, 2, 5))
    assert rbi_calendar.next_meeting("No such line here.") is None


# ---------------------------------------------------------------------------------------------- Fed calendars
def fed_row(month: str, day: str, links: str = "") -> str:
    return (f'<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>{month}</strong></div>'
            f'<div class="fomc-meeting__date col-xs-4">{day}</div><div class="col-xs-12">{links}</div></div>')


def fed_year(year: int, *rows: str) -> str:
    return (f'<div class="panel panel-default"><div class="panel-heading"><h4><a id="1">{year} FOMC Meetings</a></h4>'
            f'</div>{"".join(rows)}<div class="panel-footer">* Meeting associated with a Summary of Economic '
            "Projections.</div></div>")


def fed_links(d: str, presconf: str = "presconf") -> str:
    return (f'<strong>Statement:</strong><br><a href="/monetarypolicy/files/monetary{d}a1.pdf">PDF</a> | '
            f'<a href="/newsevents/pressreleases/monetary{d}a.htm">HTML</a><br>'
            f'<a href="/newsevents/pressreleases/monetary{d}a1.htm">Implementation Note</a>'
            f'<a href="/monetarypolicy/fomc{presconf}{d}.htm">Press Conference</a><br>'
            f'<a href="/newsevents/pressreleases/monetary{d}b.htm">Statement on Longer-Run Goals</a>'
            f'<strong>Minutes:</strong><br><a href="/monetarypolicy/fomcminutes{d}.htm">HTML</a>'
            "<br> (Released February 18, 2026)")


# Excerpt of fomccalendars.htm (23 Oct 2026): Jan 2026 spells its press-conference page "fomcpressconf".
FED_CURRENT = "<html><body>" + fed_year(
    2026, fed_row("January", "27-28", fed_links("20260128", "pressconf")), fed_row("October", "27-28"),
    fed_row("December", "8-9*")) + fed_year(
    2025, fed_row("August", "22 (notation vote)",
                  '<a href="/newsevents/pressreleases/monetary20250822a.htm">Statement on Longer-Run Goals</a>')) + \
    fed_year(2024, fed_row("Apr/May", "30-1", fed_links("20240501"))) + "</body></html>"


def fed_panel(heading: str, body: str) -> str:
    return (f'<div class="panel panel-default panel-padded"><h5 class="panel-heading panel-heading--shaded">{heading}'
            f'</h5><div class="row"><div class="col-xs-12">{body}</div></div></div>')


# Excerpts of fomchistorical2018.htm and fomchistorical2020.htm.
FED_2018 = "<html><body><div id=\"article\"><h3>2018</h3>" + fed_panel(
    "November 7-8 Meeting - 2018", '<p><a href="/newsevents/pressreleases/monetary20181108a.htm">Statement</a></p>') + \
    fed_panel("December 18-19 Meeting - 2018",
              '<p><a href="/newsevents/pressreleases/monetary20181219a.htm">Statement</a></p>'
              '<p><a href="/monetarypolicy/fomcpresconf20181219.htm">Press Conference</a></p>'
              '<p>Minutes (Released January 09, 2019):<br><a href="/monetarypolicy/fomcminutes20181219.htm">HTML</a></p>') + \
    "</div></body></html>"
FED_2020 = "<html><body><div id=\"article\"><h3>2020</h3>" + fed_panel(
    "March 2 (unscheduled) Meeting - 2020",
    '<p><a href="/newsevents/pressreleases/monetary20200303a.htm">Statement</a> (Released March 3, 2020)</p>'
    '<p>Minutes: <em>See end of minutes of March 15 meeting</em></p>'
    '<p><a href="/monetarypolicy/fomcpresconf20200303.htm">Press Conference</a></p>') + fed_panel(
    "March 15 (unscheduled) Meeting - 2020",
    '<p><a href="/newsevents/pressreleases/monetary20200315a.htm">Statement</a></p>') + fed_panel(
    "March 17-18 (cancelled) Meeting - 2020", "<p></p>") + fed_panel(
    "March 19 (notation vote) - 2020", '<p><a href="/newsevents/pressreleases/monetary20200319b.htm">Press Release</a></p>') + \
    "</div></body></html>"


def test_fed_current_page_rows():
    rows = fed_calendar.parse_page(FED_CURRENT, fed_calendar.CALENDAR)
    jan, oct_, dec, vote, aprmay = rows
    assert (jan.first, jan.last, jan.sep, jan.label) == (date(2026, 1, 27), date(2026, 1, 28), False, None)
    assert jan.statement_url == "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260128a.htm"
    assert jan.presconf_url.endswith("/fomcpressconf20260128.htm")      # the one misspelt link is still found
    assert jan.minutes_url.endswith("/fomcminutes20260128.htm") and jan.minutes_released == date(2026, 2, 18)
    assert oct_.statement_url is None and dec.sep and (dec.first, dec.last) == (date(2026, 12, 8), date(2026, 12, 9))
    assert vote.label == "notation vote" and vote.statement_url is None     # a longer-run statement isn't a statement
    assert (aprmay.first, aprmay.last) == (date(2024, 4, 30), date(2024, 5, 1))


def test_fed_historical_page_rows():
    rows = fed_calendar.parse_page(FED_2020, fed_calendar.historical(2020))
    assert [(r.first, r.label) for r in rows] == [(date(2020, 3, 2), "unscheduled"), (date(2020, 3, 15), "unscheduled"),
                                                  (date(2020, 3, 17), "cancelled"), (date(2020, 3, 19), "notation vote")]
    assert rows[0].statement_date == date(2020, 3, 3)


@pytest.mark.parametrize("bad", [
    fed_year(2026, fed_row("Octobre", "27-28")),
    fed_year(2026, fed_row("October", "27-28 (postponed)")),
    fed_year(2026, fed_row("October", "27-31")),
    "<html><body><p>Page not found</p></body></html>",
])
def test_fed_page_template_change_fails(bad):
    with pytest.raises(fed_calendar.PageError):
        fed_calendar.parse_page(bad, fed_calendar.CALENDAR)


# ---------------------------------------------------------------------------------------------- meeting rules
CURATED = meetings.load_curated()


def test_curated_config_is_valid_and_sourced():
    assert CURATED.rbi_anchor == (date(2018, 12, 5), 14)
    fed = {d: c for (b, d), c in CURATED.rows.items() if b == "fed"}
    assert fed[date(2020, 3, 3)].held == date(2020, 3, 2) and fed[date(2020, 3, 3)].release == time(10, 0)
    assert fed[date(2020, 3, 15)].release == time(17, 0)
    rbi = {d: c for (b, d), c in CURATED.rows.items() if b == "rbi"}
    assert rbi[date(2022, 11, 3)].kind == "special" and not rbi[date(2022, 11, 3)].is_rate_decision
    assert rbi[date(2022, 11, 3)].meeting_no == 39
    assert all(c.source for c in CURATED.rows.values())


NUMBERING = "rbi_numbering: {anchor: '2018-12-05', meeting_no: 14, source: x}"


@pytest.mark.parametrize("yaml_text, message", [
    ("fed:\n  '2020-03-15': {kind: unscheduled}\n" + NUMBERING, "source note"),
    ("fed:\n  '2020-03-15': {kind: unscheduled, source: x}\n" + NUMBERING, "release time"),
    ("fed:\n  '2020-03-15': {kind: offcycle, source: x, release: '17:00'}\n" + NUMBERING, "kind"),
    ("fed: {}\n", "rbi_numbering"),
])
def test_curated_config_refuses_bad_rows(tmp_path, yaml_text, message):
    p = tmp_path / "meeting_times.yaml"
    p.write_text(yaml_text)
    with pytest.raises(CalendarError, match=message):
        meetings.load_curated(p)


def test_fed_meetings_rules():
    rows = (fed_calendar.parse_page(FED_2018, fed_calendar.historical(2018))
            + fed_calendar.parse_page(FED_2020, fed_calendar.historical(2020))
            + fed_calendar.parse_page(FED_CURRENT, fed_calendar.CALENDAR))
    ms, votes = meetings.fed_meetings(rows, CURATED, full=True)
    by = {m.slug: m for m in ms}
    assert ms[0].slug == "fomc-2018-12-19"                                   # the baseline; Nov 2018 is cut
    assert "fomc-2018-11-08" not in by and len(votes) == 2                   # notation votes aren't meetings
    base = by["fomc-2018-12-19"]
    assert (base.starts_on, base.ends_on, base.occurs_at) == (date(2018, 12, 18), date(2018, 12, 19),
                                                              datetime(2018, 12, 19, 14, 0, tzinfo=NY))
    assert base.minutes_due_on == date(2019, 1, 9) and base.has_press_conference
    call = by["fomc-2020-03-03"]                                             # the 2 Mar call, announced 3 Mar
    assert (call.starts_on, call.kind, call.occurs_at) == (date(2020, 3, 2), "unscheduled",
                                                           datetime(2020, 3, 3, 10, 0, tzinfo=NY))
    assert call.minutes_due_on is None                                       # the three-week rule is for scheduled
    assert by["fomc-2020-03-15"].occurs_at == datetime(2020, 3, 15, 17, 0, tzinfo=NY)
    cancelled = by["fomc-2020-03-18"]
    assert (cancelled.status, cancelled.is_rate_decision, cancelled.occurs_at, cancelled.minutes_due_on) == \
        ("cancelled", False, None, None)
    oct_ = by["fomc-2026-10-28"]
    assert (oct_.status, oct_.from_schedule, oct_.has_press_conference, oct_.minutes_due_on) == \
        ("scheduled", True, None, date(2026, 11, 18))
    assert oct_.occurs_at == datetime(2026, 10, 28, 14, 0, tzinfo=NY)
    assert by["fomc-2024-05-01"].starts_on == date(2024, 4, 30)


def test_fed_unscheduled_meeting_needs_a_curated_row():
    rows = fed_calendar.parse_page(fed_year(2026, fed_row("November", "13 (unscheduled)")), fed_calendar.CALENDAR)
    with pytest.raises(CalendarError, match="no meeting_times.yaml row"):
        meetings.fed_meetings(rows, CURATED, full=False)


def test_rbi_meetings_rules():
    cal = rbi_calendar.calendar([rbi_calendar.parse_page(p) for p in (RBI_2019_20, RBI_2020_21, RBI_2026_27)])
    ms = meetings.rbi_meetings(cal, CURATED, date(2026, 10, 23), full=False)
    by = {m.slug: m for m in ms}
    off = by["mpc-2020-03-27"]                                               # curated off-cycle
    assert (off.kind, off.starts_on, off.minutes_due_on) == ("unscheduled", date(2020, 3, 24), date(2020, 4, 10))
    feb = by["mpc-2020-02-06"]
    assert (feb.starts_on, feb.kind, feb.has_press_conference) == (date(2020, 2, 4), "scheduled", True)
    assert by["mpc-2026-10-07"].minutes_due_on == date(2026, 10, 21)       # RBI Act s.45ZL: day 14
    assert by["mpc-2026-10-07"].occurs_at == datetime(2026, 10, 7, 10, 0, tzinfo=IST)
    up = [m.slug for m in ms if m.from_schedule]
    assert up == ["mpc-2026-12-04", "mpc-2027-02-05"]                       # past schedule rows aren't upcoming
    assert by["mpc-2026-12-04"].starts_on == date(2026, 12, 2)
    # A "next meeting" line from a stored resolution adds a meeting the schedule doesn't list yet.
    ms = meetings.rbi_meetings(cal, CURATED, date(2027, 2, 6), full=False,
                               next_lines=[(date(2027, 4, 5), date(2027, 4, 7))])
    assert [m.slug for m in ms if m.from_schedule] == ["mpc-2027-04-07"]


def test_the_calendar_publishes_upcoming_scheduled_meetings_and_a_held_special_one_only():
    """Launch check #87: RBI's special meeting no. 39 is held from the start and has no decision document to publish it
    later, so the calendar publishes it itself; every other past meeting still waits for its document (B5, B6)."""
    from ft.jobs.tasks.calendar import publishes_at_once

    def meeting(kind: str, status: str, on: date, rate: bool = True) -> meetings.Meeting:
        return meetings.Meeting(bank="rbi", decision_date=on, starts_on=on, kind=kind, is_rate_decision=rate, status=status,
                                occurs_at=None, minutes_due_on=None, has_press_conference=None, source_url="test")

    today = date(2026, 10, 31)
    assert publishes_at_once(meeting("scheduled", "scheduled", date(2026, 12, 4)), today)
    assert publishes_at_once(meeting("scheduled", "scheduled", today), today)
    assert not publishes_at_once(meeting("scheduled", "scheduled", date(2026, 10, 7)), today)     # overdue: not by itself
    assert not publishes_at_once(meeting("scheduled", "held", date(2026, 10, 7)), today)          # waits for its document
    assert not publishes_at_once(meeting("unscheduled", "held", date(2020, 3, 27)), today)
    assert not publishes_at_once(meeting("scheduled", "cancelled", date(2026, 12, 4)), today)
    assert publishes_at_once(meeting("special", "held", date(2022, 11, 3), rate=False), today)
    assert not publishes_at_once(meeting("special", "cancelled", date(2022, 11, 3), rate=False), today)


def test_rbi_special_meeting_and_numbering():
    # The curated special meeting joins the resolutions; numbers count from the anchor (Dec 2018 = 14), so 3 Nov 2022
    # is no. 39. Expected numbers: Data Audit 01's test R2 read them from the minutes.
    page = rbi_page(*[rbi_group(d.strftime("%b %d, %Y"), "Bi-monthly Monetary Policy Statement",
                                rbi_item(f"Resolution of the Monetary Policy Committee (MPC) {d:%B} {d.day - 2} to "
                                         f"{d.day}, {d.year}", PR + str(i), True))
                      for i, d in enumerate([date(2018, 12, 5), date(2022, 9, 30), date(2022, 12, 7)])])
    cal = rbi_calendar.calendar([rbi_calendar.parse_page(page)])
    curated = meetings.CuratedConfig({k: v for k, v in CURATED.rows.items() if k == ("rbi", date(2022, 11, 3))},
                                     CURATED.rbi_anchor, "test")
    ms = meetings.rbi_meetings(cal, curated, date(2026, 10, 23), full=True)
    stored = {date(2019, 2, 7) + (date(2022, 9, 30) - date(2019, 2, 7)) * i / 23: None for i in range(23)}
    numbered = meetings.number_rbi(ms, stored, curated)
    by = {m.slug: m for m in numbered}
    assert by["mpc-2018-12-05"].meeting_no == 14 and by["mpc-2022-09-30"].meeting_no == 38
    special = by["mpc-2022-11-03"]
    assert (special.meeting_no, special.kind, special.status, special.is_rate_decision, special.occurs_at,
            special.minutes_due_on) == (39, "special", "held", False, None, None)
    assert by["mpc-2022-12-07"].meeting_no == 40
    # Without the anchor in view, numbers already stored are kept and nothing is guessed.
    later = [m for m in ms if m.decision_date > date(2022, 1, 1)]
    kept = meetings.number_rbi(later, {date(2022, 9, 30): 38}, curated)
    assert [m.meeting_no for m in kept] == [38, None, None]


def test_baseline_must_exist():
    with pytest.raises(CalendarError, match="baseline"):
        meetings.baseline_cut([date(2019, 1, 30)])


# ---------------------------------------------------------------------------------------------- the full saved pages
@pytest.mark.skipif(not (FIXTURES / "fed" / "fomccalendars.htm").exists(),
                    reason="the saved pages aren't checked out (data/fixtures: the private fixtures repo)")
def test_full_calendars_from_the_saved_pages():
    """B3's done-when counts, from the pages saved on 23 Oct 2026: every meeting both calendars list from 1 Jan 2019,
    the two 2018 baselines and every upcoming meeting. The Fed's regular statements are all released at 2:00 p.m.
    New York time ("For release at" on each saved statement), and the curated times match their releases."""
    fed_dir, rbi_dir = FIXTURES / "fed", FIXTURES / "rbi" / "annualpolicy"
    rows = fed_calendar.parse_page((fed_dir / "fomccalendars.htm").read_bytes(), fed_calendar.CALENDAR)
    for y in (2018, 2019, 2020):
        rows += fed_calendar.parse_page((fed_dir / f"fomchistorical{y}.htm").read_bytes(), fed_calendar.historical(y))
    fed, votes = meetings.fed_meetings(rows, CURATED, full=True)
    cal = rbi_calendar.calendar([rbi_calendar.parse_page(p.read_bytes()) for p in sorted(rbi_dir.glob("annualpolicy_*"))])
    today = date(2026, 10, 23)
    rbi = meetings.number_rbi(meetings.rbi_meetings(cal, CURATED, today, full=True), {}, CURATED)

    def count(ms, test):
        return sum(1 for m in ms if test(m))
    assert (len(fed), fed[0].slug, len(votes)) == (76, "fomc-2018-12-19", 5)
    assert count(fed, lambda m: m.decision_date.year >= 2019 and m.is_rate_decision and m.decision_date < today) == 63
    assert count(fed, lambda m: m.status == "scheduled" and m.decision_date >= today) == 10
    assert [m.slug for m in fed if m.status != "scheduled"] == ["fomc-2019-10-04", "fomc-2020-03-18"]
    assert (len(rbi), rbi[0].slug, rbi[0].meeting_no, rbi[-1].slug, rbi[-1].meeting_no) == \
        (52, "mpc-2018-12-05", 14, "mpc-2027-02-05", 65)
    assert count(rbi, lambda m: m.decision_date.year >= 2019 and m.is_rate_decision and m.decision_date < today) == 48
    assert [m.slug for m in rbi if m.status == "scheduled" and m.decision_date >= today] == \
        ["mpc-2026-12-04", "mpc-2027-02-05"]
    # MPC numbers as Data Audit 01's R2 read them from the minutes (decision date: number).
    r2 = {date(2019, 2, 7): 15, date(2020, 3, 27): 22, date(2020, 5, 22): 23, date(2022, 5, 4): 35,
          date(2022, 9, 30): 38, date(2022, 12, 7): 40, date(2025, 2, 7): 53, date(2026, 8, 5): 62}
    assert {m.decision_date: m.meeting_no for m in rbi if m.decision_date in r2} == r2
    # Each statement's own "For release at" line against occurs_at.
    import re
    for m in fed:
        page = fed_dir / f"monetary{m.decision_date:%Y%m%d}a.htm"
        if not m.is_rate_decision or not page.exists():
            continue
        said = re.search(r"For release at (\d{1,2}):(\d{2}) ([ap])\.m\.", page.read_text(errors="replace"))
        hour = int(said[1]) % 12 + (12 if said[3] == "p" else 0)
        assert m.occurs_at.astimezone(NY).time() == time(hour, int(said[2])), m.slug
