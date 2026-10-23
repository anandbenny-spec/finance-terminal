"""policy.calendar through the runner, against the test database as job_writer (Prompt 01 B3; Architecture 02 §2):
pages served by a mock transport (excerpt templates from test_policy_calendar), raw files into a local S3-protocol
server (moto), the run's clock set by the test. Upcoming meetings in 2031-2034, so nothing here touches the fixture's
2026 meetings or test_watch's; the backfill test uses 2018-2021 with its own curated file."""
import json
import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer
from test_document_chunks import plain
from test_policy_calendar import PR, fed_links, fed_panel, fed_row, fed_year, rbi_group, rbi_item, rbi_page

from ft.engines.document.chunk import chunk_text
from ft.engines.document.store import DocumentRef, save_chunks, save_version
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import connect, run
from ft.jobs.tasks.calendar import PolicyCalendar
from ft.policy import fed_calendar, rbi_calendar
from ft.storage import Storage

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NY, IST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")
PORT, BUCKET = 5597, "calendar-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.b3_calendar"      # the task under its own key, so other tests' view of policy.calendar's runs is untouched


class Calendar(PolicyCalendar):
    key = KEY


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'B3 calendar test', '{weekly}', '25 days', '40 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


def storage():
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def x(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql, a)


class Sites:
    """GET pages by URL (with ETags, so an unchanged page answers 304) and the Annual Policy page's form posts by
    hdnYear. Records every request."""

    def __init__(self, pages: dict[str, str], posts: dict[str, str] | None = None):
        self.pages, self.posts, self.seen = pages, posts or {}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = str(request.url)
        if request.method == "POST":
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form.get("UsrFontCntr$btn") == "" and form.get("__VIEWSTATE") == "vs"
            return httpx.Response(200, text=self.posts[form["hdnYear"]], headers={"Content-Type": "text/html"})
        if url not in self.pages:
            return httpx.Response(404)
        body = self.pages[url]
        etag = '"' + uuid.uuid5(uuid.NAMESPACE_URL, body).hex + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, text=body, headers={"Content-Type": "text/html; charset=utf-8", "ETag": etag})


def calendar_run(sites: Sites, now: datetime, workflow: str = "weekly", task: PolicyCalendar | None = None):
    task = task or Calendar()
    task.workflows = ("weekly",)
    http = HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    code = run(JOB, workflow, [task], only=KEY, force=True, http=http, storage=storage, clock=lambda: now)
    tr = q("""select id, status, rows_written, checks, error from ops.task_run where task_key=%s
              order by id desc limit 1""", KEY)[0]
    return code, tr, task


def meeting(slug):
    rows = q("""select e.id, e.status, e.is_published, e.starts_on, e.ends_on, e.occurs_at, m.kind, m.is_rate_decision,
                       m.minutes_due_on, m.meeting_no, e.title
                  from core.event e join policy.meeting m on m.event_id = e.id where e.slug = %s""", slug)
    return rows[0] if rows else None


def check(tr, name):
    return next(c for c in tr[3] if c["name"] == name)


def fed_page(*years: str) -> str:
    return "<html><body>" + "".join(years) + "</body></html>"


def schedule(fy: str, *rows: str) -> str:
    return "".join(f"<tr><td>{r}</td></tr>" for r in rows)


# ---------------------------------------------------------------------------------------------- weekly runs
@pytest.mark.db
def test_weekly_run_loads_the_calendars_publishes_upcoming_and_reruns_clean(s3):
    fed = fed_page(fed_year(2031, fed_row("January", "28-29", fed_links("20310129")), fed_row("March", "18-19*"),
                            fed_row("April", "29-30")),
                   fed_year(2032, fed_row("January", "27-28")))
    rbi = rbi_page(
        rbi_group("Feb 06, 2031", "Bi-monthly Monetary Policy Statement, 2030-2031",
                  rbi_item("Resolution of the Monetary Policy Committee (MPC) February 4 to 6, 2031", PR + "90001", True)
                  + rbi_item("Webcasting of RBI Press Conference", "https://youtu.be/x")),
        rbi_group("Mar 20, 2031", "Meeting Schedule of the Monetary Policy Committee for 2031-2032",
                  schedule("2031-32", "April 7 to 9, 2031", "June 4, 5 and 6, 2031")))
    sites = Sites({fed_calendar.CALENDAR: fed, rbi_calendar.ANNUAL_POLICY: rbi})
    now = datetime(2031, 3, 1, 6, 0, tzinfo=UTC)
    code, tr, _ = calendar_run(sites, now)
    assert (code, tr[1]) == (0, "success"), tr[4]
    assert tr[2] == 7

    held = meeting("fomc-2031-01-29")              # past: kept unpublished until its statement passes (B5)
    assert (held[1], held[2], held[3], held[4]) == ("scheduled", False, date(2031, 1, 28), date(2031, 1, 29))
    up = meeting("fomc-2031-03-19")                 # upcoming: published at once (the countdown)
    assert (up[1], up[2], up[6], up[7], up[8]) == ("scheduled", True, "scheduled", True, date(2031, 4, 9))
    assert up[5] == datetime(2031, 3, 19, 14, 0, tzinfo=NY)
    assert meeting("fomc-2031-04-30")[3] == date(2031, 4, 29)
    assert meeting("fomc-2032-01-28")[2] is True
    mpc = meeting("mpc-2031-04-09")
    assert (mpc[2], mpc[3], mpc[5], mpc[8], mpc[10]) == (True, date(2031, 4, 7), datetime(2031, 4, 9, 10, 0, tzinfo=IST),
                                                         date(2031, 4, 23), "MPC meeting")
    assert meeting("mpc-2031-02-06")[2] is False and meeting("mpc-2031-06-06")[2] is True
    assert q("select has_press_conference from policy.meeting m join core.event e on e.id = m.event_id "
             "where e.slug = 'mpc-2031-02-06'")[0][0] is True
    counts = json.loads(check(tr, "meetings_stored")["detail"])
    assert counts["fed"]["upcoming"] == 3 and counts["rbi"]["upcoming"] == 2 and counts["pages"] == 2
    assert all(c["passed"] for c in tr[3]), tr[3]

    # A re-run with nothing changed writes nothing: no rows, no raw files (the pages answer 304).
    files = q("select count(*) from core.raw_file")[0][0]
    code, tr, _ = calendar_run(sites, now)
    assert (code, tr[1], tr[2]) == (0, "success", 0)
    assert q("select count(*) from core.raw_file")[0][0] == files


@pytest.mark.db
def test_a_moved_meeting_keeps_its_row_and_a_dropped_one_blocks_the_load(s3):
    def pages(*rows):
        return Sites({fed_calendar.CALENDAR: fed_page(fed_year(2033, *rows)),
                      rbi_calendar.ANNUAL_POLICY: rbi_page(rbi_group(
                          "Mar 20, 2033", "Meeting Schedule of the Monetary Policy Committee for 2033-2034",
                          schedule("2033-34", "April 6 to 8, 2033")))})
    now = datetime(2033, 2, 1, 6, 0, tzinfo=UTC)
    code, tr, _ = calendar_run(pages(fed_row("March", "15-16*"), fed_row("May", "3-4"), fed_row("June", "14-15*")), now)
    assert (code, tr[1]) == (0, "success"), tr[4]
    eid = meeting("fomc-2033-05-04")[0]

    # The May meeting moves a week: same row, new slug and dates, and a warning to look at.
    code, tr, _ = calendar_run(pages(fed_row("March", "15-16*"), fed_row("May", "10-11"), fed_row("June", "14-15*")), now)
    assert (code, tr[1]) == (0, "success"), tr[4]
    assert meeting("fomc-2033-05-04") is None
    moved = meeting("fomc-2033-05-11")
    assert (moved[0], moved[3], moved[8]) == (eid, date(2033, 5, 10), date(2033, 6, 1))
    assert check(tr, "meetings_moved") == {"name": "meetings_moved", "passed": False, "blocking": False,
                                           "detail": "fomc-2033-05-04 -> fomc-2033-05-11"}

    # The May meeting vanishes with nothing near it: a parse that lost a meeting must not cancel a decision day.
    code, tr, _ = calendar_run(pages(fed_row("March", "15-16*"), fed_row("June", "14-15*")), now)
    assert (code, tr[1]) == (1, "checks_failed")
    assert "fomc-2033-05-11" in check(tr, "no_meeting_dropped")["detail"]
    assert meeting("fomc-2033-05-11")[1] == "scheduled"           # rolled back: the last good data stays
    assert q("select count(*) from ops.alert where dedupe_key = 'task_failed:test.b3_calendar' and resolved_at is null")[0][0] == 1
    code, tr, _ = calendar_run(pages(fed_row("March", "15-16*"), fed_row("May", "10-11"), fed_row("June", "14-15*")), now)
    assert (code, tr[1]) == (0, "success")
    assert q("select count(*) from ops.alert where dedupe_key = 'task_failed:test.b3_calendar' and resolved_at is null")[0][0] == 0


@pytest.mark.db
def test_statuses_only_move_forward_and_a_cancelled_meeting_is_kept(s3):
    now = datetime(2034, 1, 10, 6, 0, tzinfo=UTC)
    rbi = rbi_page(rbi_group("Mar 20, 2034", "Meeting Schedule of the Monetary Policy Committee for 2034-2035",
                             schedule("2034-35", "April 5 to 7, 2034")))
    sites = Sites({fed_calendar.CALENDAR: fed_page(fed_year(2034, fed_row("January", "24-25", fed_links("20340125")),
                                                            fed_row("March", "14-15*"))),
                   rbi_calendar.ANNUAL_POLICY: rbi})
    assert calendar_run(sites, now)[0] == 0
    x("update core.event set status = 'held', is_published = true where slug = 'fomc-2034-01-25'")   # as B5 would
    sites.pages[fed_calendar.CALENDAR] = fed_page(fed_year(
        2034, fed_row("January", "24-25", fed_links("20340125")), fed_row("March", "14-15* (cancelled)")))
    code, tr, _ = calendar_run(sites, now)
    assert (code, tr[1]) == (0, "success"), tr[4]
    assert meeting("fomc-2034-01-25")[1:3] == ("held", True)                      # never back to scheduled
    cancelled = meeting("fomc-2034-03-15")
    assert (cancelled[1], cancelled[5], cancelled[7], cancelled[8], cancelled[10]) == \
        ("cancelled", None, False, None, "FOMC meeting (cancelled)")


@pytest.mark.db
def test_the_next_meeting_line_of_the_latest_stored_resolution_adds_a_meeting(s3):
    rbi_entity, rbi_source = q("select (select id from core.entity where slug='rbi'), (select id from core.source where key='rbi')")[0]
    doc = plain(["The MPC decided to keep the policy repo rate unchanged at 5.50 per cent.",
                 f"The next meeting of the MPC is scheduled during August 1 to 3, 2035. ({uuid.uuid4().hex})"])
    key = f"/test/b3/{uuid.uuid4().hex}"
    with connect(JOB) as c, c.transaction():
        saved = save_version(c, DocumentRef("mpc_resolution", rbi_entity, rbi_source, key, "https://rbi.org.in" + key,
                                            "Resolution", published_at=datetime(2035, 6, 5, 4, 30, tzinfo=UTC)), doc)
        save_chunks(c, saved.document_id, doc.text, chunk_text(doc))
    sites = Sites({fed_calendar.CALENDAR: fed_page(fed_year(2035, fed_row("July", "24-25"))),
                   rbi_calendar.ANNUAL_POLICY: rbi_page(rbi_group(
                       "Jun 05, 2035", "Bi-monthly Monetary Policy Statement, 2035-2036",
                       rbi_item("Resolution of the Monetary Policy Committee (MPC) June 3 to 5, 2035", PR + "90002", True)))})
    code, tr, _ = calendar_run(sites, datetime(2035, 6, 10, 6, 0, tzinfo=UTC))
    assert (code, tr[1]) == (0, "success"), tr[4]
    m = meeting("mpc-2035-08-03")
    assert (m[1], m[2], m[3]) == ("scheduled", True, date(2035, 8, 1))


@pytest.mark.db
def test_a_published_meeting_still_scheduled_two_days_on_raises_an_alert():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, is_published)
                     select 'policy_meeting', id, 'fomc-2017-01-04', 'FOMC meeting', '2017-01-04',
                            '2017-01-04 19:00+00', 'scheduled', true from core.entity where slug = 'fed'
                     on conflict do nothing""")
        c.execute("""insert into policy.meeting (event_id, kind, is_rate_decision)
                     select id, 'scheduled', true from core.event where slug = 'fomc-2017-01-04' on conflict do nothing""")
        alerts = {a.dedupe_key: a.active for a in Calendar().alerts(c)}
        assert alerts["meeting_not_held:fomc-2017-01-04"] is True
        c.execute("insert into ops.alert (task_key, severity, message, dedupe_key) values (%s, 'warn', 'x', %s) "
                  "on conflict do nothing", (KEY, "meeting_not_held:fomc-2017-01-04"))
        c.execute("update core.event set status = 'held' where slug = 'fomc-2017-01-04'")
        alerts = {a.dedupe_key: a.active for a in Calendar().alerts(c)}
        assert alerts["meeting_not_held:fomc-2017-01-04"] is False               # cleared: resolved by the runner


# ---------------------------------------------------------------------------------------------- the backfill
CURATED_TEST = """
fed:
  "2020-03-03":
    kind: unscheduled
    held: "2020-03-02"
    release: "10:00"
    source: test row (the real file's row, abridged)
rbi:
  "2018-12-05":
    release: "14:30"
    source: test row
rbi_numbering:
  anchor: "2018-12-05"
  meeting_no: 14
  source: test row
"""


def rbi_resolution(posted: str, days: str, prid: str) -> str:
    return rbi_group(posted, "Bi-monthly Monetary Policy Statement",
                     rbi_item(f"Resolution of the Monetary Policy Committee (MPC) {days}", PR + prid, True))


@pytest.mark.db
def test_backfill_reads_the_historical_pages_posts_earlier_years_and_reruns_from_stored_copies(s3, tmp_path):
    years = {"2020-2021": "2021", "2019-2020": "2020", "2018-2019": "2019", "2017-2018": "2018"}
    pages = {
        fed_calendar.CALENDAR: fed_page(fed_year(2021, fed_row("January", "26-27", fed_links("20210127")),
                                                 fed_row("June", "15-16*"))),
        fed_calendar.historical(2018): "<html><body>" + fed_panel(
            "November 7-8 Meeting - 2018", '<a href="/newsevents/pressreleases/monetary20181108a.htm">Statement</a>')
        + fed_panel("December 18-19 Meeting - 2018",
                    '<a href="/newsevents/pressreleases/monetary20181219a.htm">Statement</a>') + "</body></html>",
        fed_calendar.historical(2019): "<html><body>" + fed_panel(
            "January 29-30 Meeting - 2019", '<a href="/newsevents/pressreleases/monetary20190130a.htm">Statement</a>')
        + "</body></html>",
        fed_calendar.historical(2020): "<html><body>" + fed_panel(
            "March 2 (unscheduled) Meeting - 2020",
            '<a href="/newsevents/pressreleases/monetary20200303a.htm">Statement</a> (Released March 3, 2020)')
        + fed_panel("March 19 (notation vote) - 2020", "") + "</body></html>",
        rbi_calendar.ANNUAL_POLICY: rbi_page(rbi_resolution("Apr 07, 2021", "April 5-7, 2021", "51381"),
                                             rbi_group("Mar 31, 2021", "Meeting Schedule of the Monetary Policy "
                                                       "Committee for 2021-2022",
                                                       schedule("2021-22", "April 5 to 7, 2021", "June 2 to 4, 2021")),
                                             years=years),
    }
    posts = {
        "2019": rbi_page(rbi_resolution("Feb 07, 2019", "February 5-7, 2019", "46235"),
                         rbi_resolution("Dec 05, 2018", "December 3-5, 2018", "45656"),
                         rbi_resolution("Oct 05, 2018", "October 3-5, 2018", "45152"), years=years),
        "2020": rbi_page(rbi_resolution("Feb 06, 2020", "February 4 to 6, 2020", "49342"), years=years),
    }
    sites = Sites(pages, posts)
    cfg = tmp_path / "meeting_times.yaml"
    cfg.write_text(CURATED_TEST)

    class Task(Calendar):
        curated_path = cfg

    now = datetime(2021, 5, 1, 6, 0, tzinfo=UTC)
    code, tr, _ = calendar_run(sites, now, "backfill", Task())
    assert (code, tr[1]) == (0, "success"), (tr[4], tr[3])
    assert sorted(r.content.decode().split("hdnYear=")[1][:4] for r in sites.seen if r.method == "POST") == ["2019", "2020"]
    assert meeting("fomc-2018-11-08") is None and meeting("mpc-2018-10-05") is None     # before the baselines
    base = meeting("fomc-2018-12-19")
    assert (base[2], base[3]) == (False, date(2018, 12, 18))
    call = meeting("fomc-2020-03-03")
    assert (call[3], call[5], call[6], call[8]) == (date(2020, 3, 2), datetime(2020, 3, 3, 10, 0, tzinfo=NY),
                                                    "unscheduled", None)
    mpc = meeting("mpc-2018-12-05")
    assert (mpc[5], mpc[9]) == (datetime(2018, 12, 5, 14, 30, tzinfo=IST), 14)
    assert [meeting(s)[9] for s in ("mpc-2019-02-07", "mpc-2020-02-06", "mpc-2021-04-07", "mpc-2021-06-04")] == \
        [15, 16, 17, 18]
    counts = json.loads(check(tr, "meetings_stored")["detail"])
    assert counts["fed"]["baseline"] == "2018-12-19" and counts["rbi"]["baseline"] == "2018-12-05"
    assert counts["fed"]["notation_votes_skipped"] == 1 and counts["pages"] == 7
    assert check(tr, "curated_and_baselines")["passed"]
    assert q("select count(*) from ops.http_cache where url like %s", rbi_calendar.ANNUAL_POLICY + "#hdnYear=%")[0][0] == 2

    # The re-run: closed fiscal years come from their stored copies (no post), the rest answer 304: no new files.
    files, n = q("select count(*) from core.raw_file")[0][0], len(sites.seen)
    code, tr, _ = calendar_run(sites, now, "backfill", Task())
    assert (code, tr[1], tr[2]) == (0, "success", 0), tr[4]
    assert [r.method for r in sites.seen[n:]] == ["GET"] * 5
    assert q("select count(*) from core.raw_file")[0][0] == files
