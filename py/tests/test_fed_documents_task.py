"""policy.fed_documents, part 1 (the statement path), through the runner against the test database as job_writer
(Prompt 01 B5; Architecture 02 §1.8, §2; Spec 01 §2, §3.1). Pages come from a mock transport (synthetic statements
in the real template: policy_docs.fed_page), raw files go to a local S3-protocol server (moto), the clock is the
test's. Meetings are synthetic, 2039-2044 (`fomc-…` slugs the other tests don't use), and the task sees only each
test's own meetings (`scope`), with its own roster file and baseline cut-off."""
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer
from policy_docs import (
    fed_minutes_page,
    fed_named,
    fed_page,
    fed_presconf_page,
    fed_rss,
    fed_tally,
    fed_transcript_pdf,
)
from test_policy_calendar import fed_links, fed_row, fed_year

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import JobRun, run
from ft.jobs.tasks.fed_documents import FedDocuments
from ft.jobs.watch import watch
from ft.policy import fed_calendar, fed_statement, members
from ft.storage import Storage

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NY = ZoneInfo("America/New_York")
PORT, BUCKET = 5598, "fed-docs-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.b5_fed_documents"
PR = "https://www.federalreserve.gov/newsevents/pressreleases/"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December"]

# Twelve fictional members (one alternate) for these tests' roster.
PEOPLE = ["Ada B. Lane", "Ben C. Ford", "Cara D. Hill", "Dev E. Rao", "Eva F. Shaw", "Finn G. Park", "Gia H. Ross",
          "Hal I. Webb", "Ida J. Kerr", "Jon K. Moss", "Kai L. Burt", "Lea M. Dunn"]
ALTERNATE = "Max N. Vale"


def slug(name: str) -> str:
    return "b5-" + name.lower().replace(".", "").replace(" ", "-")


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'B5 fed documents test', '{daily,watch}', '20 hours', '3 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


@pytest.fixture
def roster(tmp_path) -> Path:
    path = tmp_path / "members.yaml"
    lines = ["fed:"]
    for i, name in enumerate(PEOPLE + [ALTERNATE]):
        role = "fed_chair" if i == 0 else ("fed_governor" if i < 7 else "fed_bank_president")
        voter = "false" if name == ALTERNATE else "true"
        lines += [f"  {slug(name)}:", f"    name: {name}", "    terms:",
                  f"      - {{role: {role}, affiliation: Test, voter: {voter}, from: 2039-01-01, source: test roster}}"]
    path.write_text("\n".join(lines) + "\n")
    return path


def storage():
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def meeting(d: date, *, at: str = "14:00", press: bool | None = None) -> str:
    """A scheduled FOMC meeting held the day before and on `d` (the calendar task's rows); `press`: it has a press
    conference (the calendar lists one)."""
    s = f"fomc-{d.isoformat()}"
    hh, mm = map(int, at.split(":"))
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at, status,
                                             source_id, source_url, is_published)
                     select 'policy_meeting', b.id, %s, 'FOMC meeting', %s, %s, %s, 'scheduled', src.id, %s, false
                       from core.entity b, core.source src where b.kind = 'central_bank' and b.slug = 'fed'
                        and src.key = 'fed'
                     on conflict (event_type, slug) do nothing""",
                  (s, d - timedelta(days=1), d, datetime(d.year, d.month, d.day, hh, mm, tzinfo=NY), fed_calendar.CALENDAR))
        c.execute("""insert into policy.meeting (event_id, kind, is_rate_decision, minutes_due_on, has_press_conference)
                     select id, 'scheduled', true, %s, %s from core.event where event_type = 'policy_meeting' and slug = %s
                     on conflict do nothing""", (d + timedelta(days=21), press, s))
    return s


def fred(d: date, lower: str, upper: str):
    """FRED's target-range bounds on a day (policy.market_series stores them in the real runs)."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        tr = c.execute("select min(id) from ops.task_run").fetchone()[0]
        for key, v in (("fred.DFEDTARL", lower), ("fred.DFEDTARU", upper)):
            c.execute("""insert into core.observation (series_id, obs_date, value, task_run_id)
                         select id, %s, %s, %s from core.series where key = %s
                         on conflict (series_id, obs_date) do update set value = excluded.value""", (d, v, tr, key))


def when(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def url(d: date) -> str:
    return f"{PR}monetary{d:%Y%m%d}a.htm"


def named(d: date, decision: str, *, voters=PEOPLE, against: str = "", extra: str = "") -> str:
    listed = "; ".join(f"{n}, Chair" if i == 0 else n for i, n in enumerate(voters[:-1])) + f"; and {voters[-1]}"
    voting = f"Voting for the monetary policy action were {listed}." + (f" {against}" if against else "") + extra
    return fed_page(fed_named(decision, voting), day=when(d))


def statement_links(d8: str) -> str:
    """A calendar row's links before the minutes and the press conference are posted: the statement only."""
    return (f'<strong>Statement:</strong><br><a href="/monetarypolicy/files/monetary{d8}a1.pdf">PDF</a> | '
            f'<a href="/newsevents/pressreleases/monetary{d8}a.htm">HTML</a><br>'
            f'<a href="/newsevents/pressreleases/monetary{d8}a1.htm">Implementation Note</a>')


def calendar(*dates: date, docs: tuple[date, ...] = ()) -> str:
    """The calendar page listing these meetings: each with its statement, and those in `docs` with their press
    conference and minutes too (fed_links), as the real page lists them once they're posted."""
    years = {}
    for d in dates:
        links = fed_links(f"{d:%Y%m%d}") if d in docs else statement_links(f"{d:%Y%m%d}")
        years.setdefault(d.year, []).append(fed_row(MONTHS[d.month - 1], f"{d.day - 1}-{d.day}", links))
    return "<html><body>" + "".join(fed_year(y, *rows) for y, rows in sorted(years.items())) + "</body></html>"


class Sites:
    """GET pages by URL (text, or bytes such as a PDF), with ETags (an unchanged page answers 304). Records every
    request."""

    def __init__(self, pages: dict[str, str | bytes]):
        self.pages, self.seen = pages, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        self.seen.append(u)
        if u not in self.pages:
            return httpx.Response(404)
        body = self.pages[u]
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        etag = '"' + uuid.uuid5(uuid.NAMESPACE_URL, hashlib.sha256(data).hexdigest()).hex + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        kind = "application/pdf" if u.endswith(".pdf") else "text/html; charset=utf-8"
        return httpx.Response(200, content=data, headers={"Content-Type": kind, "ETag": etag})


def task(roster_path: Path, scope, first_day: date) -> FedDocuments:
    class Docs(FedDocuments):
        key = KEY
    t = Docs()
    t.members_path, t.scope, t.first_day = roster_path, frozenset(scope), first_day
    t.workflows = ("daily", "watch")
    t.rules = {"daily": "every 20 hours", "watch": "fomc_decision_day"}
    return t


def go(sites: Sites, t: FedDocuments, workflow: str, now: datetime, **kw):
    http = HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    code = run(JOB, workflow, [t], only=KEY, force=True, http=http, storage=storage, clock=lambda: now, **kw)
    return code, last_run()


def last_run():
    r = q("""select id, status, rows_written, checks, coalesce(error, ''), http_stats from ops.task_run
              where task_key = %s order by id desc limit 1""", KEY)[0]
    return {"id": r[0], "status": r[1], "rows": r[2], "checks": {c["name"]: c for c in r[3] or []}, "error": r[4],
            "http": r[5] or {}}


def rates(s: str):
    return q("""select r.instrument, r.value_pct::text, r.change_bps, r.crosscheck from policy.rate_setting r
                  join core.event e on e.id = r.meeting_event_id where e.slug = %s order by 1""", s)


def votes(s: str):
    return {r[0]: r[1:] for r in q("""select p.slug, v.voted_change_bps, v.is_dissent, v.dissent_text
                                        from policy.vote v join core.event e on e.id = v.meeting_event_id
                                        join policy.member p on p.id = v.member_id where e.slug = %s""", s)}


def state(s: str):
    """(event status, event published, statement stored, statement published, its diff's base slug)."""
    rows = q("""select e.status, e.is_published, d.id is not null, coalesce(d.is_published, false),
                       (select be.slug from core.document_diff x join core.document bd on bd.id = x.base_document_id
                          join core.event be on be.id = bd.event_id where x.document_id = d.id)
                  from core.event e
                  left join core.document d on d.event_id = e.id and d.doc_type = 'fomc_statement'
                 where e.slug = %s order by d.version_no desc nulls last limit 1""", s)
    return rows[0]


# ---------------------------------------------------------------------------------------------- backfill
@pytest.mark.db
def test_backfill_stores_statements_rates_votes_and_diffs_and_reruns_clean(s3, roster):
    base, a, b, c = date(2039, 12, 14), date(2040, 1, 25), date(2040, 3, 14), date(2040, 4, 25)
    slugs = [meeting(d) for d in (base, a, b, c)]
    fred(base - timedelta(days=1), "4", "4.25")            # the baseline's change: against FRED the day before
    fred(a + timedelta(days=1), "4", "4.25")               # A's effective date: matches
    fred(b + timedelta(days=1), "4.25", "4.5")             # B's: FRED disagrees (W)
    voters_a = PEOPLE[:9] + [ALTERNATE]
    pages = {
        fed_calendar.CALENDAR: calendar(base, a, b, c),
        url(base): named(base, "decided to raise the target range for the federal funds rate to 4-1/4 to 4-1/2 percent"),
        url(a): named(a, "decided to lower the target range for the federal funds rate by 1/4 percentage point to 4 to "
                      "4-1/4 percent", voters=voters_a,
                      against=f"Voting against this action were {PEOPLE[9]}, who preferred no change to the target "
                              f"range for the federal funds rate at this meeting; and {PEOPLE[10]}, who supported the "
                              "decision on the target range but preferred to slow the decline in securities holdings.",
                      extra=f" {ALTERNATE} voted as an alternate member at this meeting."),
        url(b): named(b, "decided to maintain the target range for the federal funds rate at 4 to 4-1/4 percent"),
        url(c): fed_page(fed_tally("11 – 1", "decided to raise the target range for the federal funds rate by 1/4 "
                                   "percentage point to 4-1/4 to 4-1/2 percent",
                                   f"Voting against the monetary policy action was {PEOPLE[3]}, who preferred to "
                                   "maintain the target range for the federal funds rate at this meeting."),
                         day=when(c)),
    }
    sites = Sites(pages)
    t = task(roster, slugs, date(2040, 1, 1))
    code, r = go(sites, t, "backfill", datetime(2040, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert r["checks"]["statements_stored"]["passed"], r["checks"]["statements_stored"]["detail"]
    assert r["checks"]["meetings_failed"]["passed"] and r["checks"]["voter_terms"]["passed"]

    assert rates(slugs[0]) == [("fed_target_lower", "4.250", 25, None), ("fed_target_upper", "4.500", 25, None)]
    assert rates(slugs[1]) == [("fed_target_lower", "4.000", -25, "matches FRED DFEDTARL"),
                               ("fed_target_upper", "4.250", -25, "matches FRED DFEDTARU")]
    assert [x[2] for x in rates(slugs[2])] == [0, 0] and [x[2] for x in rates(slugs[3])] == [25, 25]
    fr = r["checks"]["fred_range"]
    assert not fr["passed"] and not fr["blocking"] and slugs[2] in fr["detail"]

    va = votes(slugs[1])
    assert len(va) == 12 and va[slug(ALTERNATE)] == (-25, False, None)
    assert va[slug(PEOPLE[9])] == (0, True, ("preferred no change to the target range for the federal funds rate at "
                                            "this meeting"))
    assert va[slug(PEOPLE[10])][:2] == (None, True)                          # not about the rate: no bps
    assert len(votes(slugs[0])) == 12 and len(votes(slugs[2])) == 12
    assert votes(slugs[3]) == {}                                             # a tally statement: votes from the minutes

    assert state(slugs[0]) == ("held", True, True, True, None)               # the baseline has no diff
    assert [state(s)[4] for s in slugs[1:]] == slugs[:3]                     # each diffed against the one before
    assert all(state(s)[:4] == ("held", True, True, True) for s in slugs)
    doc = q("""select d.title, d.published_at, d.source_url, d.extractor_version, d.external_key
                 from core.document d join core.event e on e.id = d.event_id where e.slug = %s""", slugs[3])[0]
    assert doc == ("FOMC statement, 25 Apr 2040", datetime(2040, 4, 25, 18, 0, tzinfo=UTC), url(c),
                   "html-1/fed_press_release", "/newsevents/pressreleases/monetary20400425a.htm")

    # A re-run reads the stored statements back: no row, no raw file, and one request, the calendar's (conditional, 304),
    # read again for the minutes and press conferences it doesn't link yet (part 2).
    sites.seen.clear()
    code, r2 = go(sites, task(roster, slugs, date(2040, 1, 1)), "backfill", datetime(2040, 5, 1, tzinfo=UTC))
    assert (code, r2["status"], r2["rows"], sites.seen) == (0, "success", 0, [fed_calendar.CALENDAR])
    assert r2["http"].get("new_raw_files", 0) == 0 and r2["checks"]["statements_stored"]["passed"]


@pytest.mark.db
def test_a_partial_backfill_stores_the_previous_statement_and_the_baseline_waits_for_fred(s3, roster):
    z, y, x = date(2040, 12, 12), date(2041, 1, 30), date(2041, 3, 20)
    slugs = [meeting(d) for d in (z, y, x)]
    pages = {fed_calendar.CALENDAR: calendar(z, y, x),
             url(z): named(z, "decided to maintain the target range for the federal funds rate at 3 to 3-1/4 percent"),
             url(y): named(y, "decided to raise the target range for the federal funds rate to 3-1/4 to 3-1/2 percent"),
             url(x): named(x, "decided to raise the target range for the federal funds rate by 1/4 percentage point to "
                           "3-1/2 to 3-3/4 percent")}
    sites = Sites(pages)
    # From March only: Y's statement is fetched as X's base (for the change and the diff), stored unpublished.
    code, r = go(sites, task(roster, slugs, date(2041, 1, 1)), "backfill", datetime(2041, 4, 1, tzinfo=UTC),
                 since=date(2041, 3, 1))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert [x2[2] for x2 in rates(slugs[2])] == [25, 25] and state(slugs[2])[4] == slugs[1]
    assert state(slugs[1]) == ("scheduled", False, True, False, None)

    # The whole range: the baseline has no FRED value the day before, so its rates wait (W) and it stays unpublished;
    # Y's change comes from the baseline's stored statement, and its stored document is published.
    code, r = go(sites, task(roster, slugs, date(2041, 1, 1)), "backfill", datetime(2041, 4, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    bc = r["checks"]["baseline_change"]
    assert not bc["passed"] and not bc["blocking"] and "fred.DFEDTARU/L on 2040-12-11" in bc["detail"]
    assert rates(slugs[0]) == [] and votes(slugs[0]) == {}
    assert state(slugs[0]) == ("held", False, True, False, None)
    assert [x2[2] for x2 in rates(slugs[1])] == [25, 25] and state(slugs[1]) == ("held", True, True, True, slugs[0])
    assert len(votes(slugs[2])) == 12

    # Once FRED has the day before, the baseline's rates are stored and it is published.
    fred(z - timedelta(days=1), "3", "3.25")
    code, r = go(sites, task(roster, slugs, date(2041, 1, 1)), "backfill", datetime(2041, 4, 1, tzinfo=UTC))
    assert r["checks"]["baseline_change"]["passed"] and [x2[2] for x2 in rates(slugs[0])] == [0, 0]
    assert state(slugs[0])[:4] == ("held", True, True, True)


@pytest.mark.db
def test_a_backfill_isolates_a_bad_meeting_alerts_and_resolves_when_fixed(s3, roster, tmp_path):
    m1, m2, m3, m4 = date(2041, 12, 10), date(2042, 1, 28), date(2042, 3, 18), date(2042, 4, 29)
    slugs = [meeting(d) for d in (m1, m2, m3, m4)]
    fred(m1 - timedelta(days=1), "2", "2.25")
    stranger = PEOPLE[:11] + ["Zed Q. Nobody"]
    pages = {fed_calendar.CALENDAR: calendar(m1, m2, m3),                      # M4 isn't linked yet
             url(m1): named(m1, "decided to maintain the target range for the federal funds rate at 2 to 2-1/4 percent"),
             url(m2): named(m2, "decided to maintain the target range for the federal funds rate at 2 to 2-1/4 percent",
                            voters=stranger),
             url(m3): named(m3, "decided to lower the target range for the federal funds rate to 1-3/4 to 2 percent"),
             url(m4): named(m4, "decided to maintain the target range for the federal funds rate at 1-3/4 to 2 percent")}
    sites = Sites(pages)
    t = task(roster, slugs, date(2042, 1, 1))
    code, r = go(sites, t, "backfill", datetime(2042, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]               # one bad meeting doesn't block the rest
    mf = r["checks"]["meetings_failed"]
    assert not mf["passed"] and not mf["blocking"]
    assert f"{slugs[1]}: voters_on_roster (not in config/members.yaml: Zed Q. Nobody)" in mf["detail"]
    assert f"{slugs[3]}: statement_link" in mf["detail"]
    assert state(slugs[1]) == ("scheduled", False, True, False, None)       # stored as M3's base only
    assert state(slugs[2])[:4] == ("held", True, True, True) and state(slugs[2])[4] == slugs[1]
    assert state(slugs[3]) == ("scheduled", False, False, False, None)
    alerts = {k for (k,) in q("select dedupe_key from ops.alert where dedupe_key like 'fed_statement_failed:%%' "
                              "and resolved_at is null")}
    assert {f"fed_statement_failed:{slugs[1]}", f"fed_statement_failed:{slugs[3]}"} <= alerts

    # The roster gains the member: a daily run within 60 days of M2 loads it from its stored statement (no fetch:
    # it was stored as M3's base) and its alert resolves.
    roster.write_text(roster.read_text() + "  b5-zed-q-nobody:\n    name: Zed Q. Nobody\n    terms:\n      - {role: "
                      "fed_bank_president, affiliation: Test, voter: true, from: 2039-01-01, source: test roster}\n")
    sites.seen.clear()
    code, r = go(sites, task(roster, slugs, date(2042, 1, 1)), "daily", datetime(2042, 3, 20, 12, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert state(slugs[1]) == ("held", True, True, True, slugs[0]) and len(votes(slugs[1])) == 12
    # The calendar's one conditional read, and the stored M3 statement's (re-checked for changed text: unchanged, 304).
    assert sites.seen == [fed_calendar.CALENDAR, url(m3)]
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"fed_statement_failed:{slugs[1]}")[0][0] == 0

    # The calendar links M4: the backfill loads it and its alert resolves.
    pages[fed_calendar.CALENDAR] = calendar(m1, m2, m3, m4)
    code, r = go(sites, task(roster, slugs, date(2042, 1, 1)), "backfill", datetime(2042, 5, 1, tzinfo=UTC))
    assert r["checks"]["meetings_failed"]["passed"], r["checks"]["meetings_failed"]["detail"]
    assert all(state(s)[:4] == ("held", True, True, True) for s in slugs)
    open_ = {k for (k,) in q("select dedupe_key from ops.alert where dedupe_key = any(%s) and resolved_at is null",
                             [f"fed_statement_failed:{s}" for s in slugs])}
    assert open_ == set()


# ---------------------------------------------------------------------------------------------- decision day
class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


@pytest.mark.db
def test_watch_waits_then_takes_the_rss_link_before_the_calendar_lists_it(s3, roster):
    before, day = date(2043, 1, 28), date(2043, 3, 18)
    slugs = [meeting(d) for d in (before, day)]
    fred(before - timedelta(days=1), "1", "1.25")
    pages = {fed_calendar.CALENDAR: calendar(before),
             url(before): named(before, "decided to maintain the target range for the federal funds rate at 1 to 1-1/4 "
                                "percent"),
             fed_statement.RSS: fed_rss(("Minutes of the Federal Open Market Committee, January 27-28, 2043",
                                         f"{PR}monetary20430218a.htm", "Wed, 18 Feb 2043 19:00:00 GMT"))}
    sites = Sites(pages)
    code, _ = go(sites, task(roster, slugs, date(2043, 3, 1)), "backfill", datetime(2043, 3, 1, tzinfo=UTC))
    assert code == 0 and state(slugs[0])[:2] == ("held", True)

    # Decision day, 14:01 New York: the first poll finds nothing; then the feed lists the statement, the calendar not.
    clock = Clock(datetime(2043, 3, 18, 14, 1, tzinfo=NY))
    statement = named(day, "decided to lower the target range for the federal funds rate by 1/4 percentage point to "
                      "3/4 to 1 percent")

    def publish():
        pages[url(day)] = statement
        pages[fed_statement.RSS] = fed_rss(("Federal Reserve issues FOMC statement", url(day),
                                            "Wed, 18 Mar 2043 18:00:00 GMT"))
    t = task(roster, slugs, date(2043, 3, 1))
    http = HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    with JobRun(JOB, "watch", http=http, storage=storage, clock=clock) as job:
        polls = 0

        def sleep(seconds):
            nonlocal polls
            polls += 1
            publish()
            clock.sleep(seconds)
        assert watch(job, [t], sleep=sleep) == 2
        assert job.finish() == 0
    runs = q("select status, rows_written from ops.task_run where task_key = %s and job_run_id = %s order by id",
             KEY, job.id)
    assert runs[0] == ("success", 0) and runs[1][0] == "success" and runs[1][1] > 0
    assert state(slugs[1]) == ("held", True, True, True, slugs[0])
    assert [x[2] for x in rates(slugs[1])] == [-25, -25] and len(votes(slugs[1])) == 12
    r = last_run()
    cl = r["checks"]["calendar_link"]
    assert not cl["passed"] and "taken from the RSS item" in cl["detail"]
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"check:{KEY}:calendar_link")[0][0] == 1

    # The next daily run reads the calendar, which now links the same page: the warning resolves.
    pages[fed_calendar.CALENDAR] = calendar(before, day)
    code, r = go(sites, task(roster, slugs, date(2043, 3, 1)), "daily", datetime(2043, 3, 19, 11, tzinfo=UTC))
    assert (code, r["status"], r["rows"]) == (0, "success", 0) and r["checks"]["calendar_link"]["passed"]
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"check:{KEY}:calendar_link")[0][0] == 0


@pytest.mark.db
def test_a_decision_day_statement_that_fails_its_checks_is_blocked_and_the_daily_run_catches_up(s3, roster):
    before, day = date(2044, 1, 27), date(2044, 3, 16)
    slugs = [meeting(d) for d in (before, day)]
    fred(before - timedelta(days=1), "5", "5.25")
    pages = {fed_calendar.CALENDAR: calendar(before, day),
             url(before): named(before, "decided to maintain the target range for the federal funds rate at 5 to "
                                "5-1/4 percent"),
             url(day): named(day, "decided to raise the target range for the federal funds rate to 5-1/4 to 5-3/4 "
                             "percent"),                                     # 50 bps wide: not a statement we store
             fed_statement.RSS: fed_rss(("Federal Reserve issues FOMC statement", url(day),
                                         "Wed, 16 Mar 2044 18:00:00 GMT"))}
    sites = Sites(pages)
    go(sites, task(roster, slugs, date(2044, 2, 1)), "backfill", datetime(2044, 3, 1, tzinfo=UTC), until=date(2044, 2, 1))
    t = task(roster, slugs, date(2044, 2, 1))
    http = HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    with JobRun(JOB, "watch", http=http, storage=storage, clock=lambda: datetime(2044, 3, 16, 14, 2, tzinfo=NY)) as job:
        job.run_pass([t])
        assert job.finish() == 1                                             # GitHub's failure email
    r = last_run()
    assert r["status"] == "checks_failed" and "target_range" in r["error"]
    assert state(slugs[1]) == ("scheduled", False, False, False, None)       # nothing stored, nothing published
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"task_failed:{KEY}")[0][0] == 1

    # The page is corrected; the next daily run (the statement released within 60 days, not stored) stores it.
    pages[url(day)] = named(day, "decided to raise the target range for the federal funds rate to 5-1/4 to 5-1/2 "
                            "percent")
    code, r = go(sites, task(roster, slugs, date(2044, 2, 1)), "daily", datetime(2044, 3, 17, 11, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert state(slugs[1])[:4] == ("held", True, True, True) and [x[2] for x in rates(slugs[1])] == [25, 25]
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"task_failed:{KEY}")[0][0] == 0


# ---------------------------------------------------------------------------------------------- minutes and transcripts
SITE = fed_calendar.SITE
WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def minutes_url(d: date) -> str:
    return f"{SITE}/monetarypolicy/fomcminutes{d:%Y%m%d}.htm"


def presconf_url(d: date) -> str:
    return f"{SITE}/monetarypolicy/fomcpresconf{d:%Y%m%d}.htm"


def pdf_url(d: date) -> str:
    return f"{SITE}/mediacenter/files/FOMCpresconf{d:%Y%m%d}.pdf"


def names_list(names) -> str:
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def minutes_page(d: date, voters, against: str | None = "None.", *, extra=(), closing: str = "") -> str:
    """The minutes of the meeting held the day before and on `d`: "Voting for this action: VOTERS." and, unless None,
    "Voting against this action: AGAINST"."""
    first = d - timedelta(days=1)
    voting = [f"<strong>Voting for this action:</strong> {names_list(list(voters))}."]
    if against is not None:
        voting.append(f"<strong>Voting against this action:</strong> {against}")
    return fed_minutes_page(days=f"{MONTHS[d.month - 1]} {first.day}-{d.day}, {d.year}",
                            held=[f"{WEEKDAY[first.weekday()]}, {when(first)}", f"{WEEKDAY[d.weekday()]}, {when(d)}"],
                            voting=voting + list(extra), closing=closing)


def doc_rows(s: str, doc_type: str):
    """(version, published, supersedes another, chunks, speakers) of each stored version of the meeting's document."""
    return q("""select d.version_no, d.is_published, d.supersedes_id is not null,
                       (select count(*) from core.chunk c where c.document_id = d.id),
                       (select count(distinct c.speaker) from core.chunk c where c.document_id = d.id)
                  from core.document d join core.event e on e.id = d.event_id
                 where e.slug = %s and d.doc_type = %s order by d.version_no""", s, doc_type)


def open_alert(key: str) -> bool:
    return q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null", key)[0][0] == 1


@pytest.mark.db
def test_backfill_stores_minutes_and_transcripts_and_takes_a_tally_statements_votes_from_its_minutes(s3, roster):
    base, a, b = date(2052, 12, 13), date(2053, 1, 29), date(2053, 3, 19)
    slugs = [meeting(d, press=True) for d in (base, a, b)]
    fred(base - timedelta(days=1), "3", "3.25")
    dev = PEOPLE[3]
    pages = {
        fed_calendar.CALENDAR: calendar(base, a, b, docs=(a, b)),
        url(base): named(base, "decided to maintain the target range for the federal funds rate at 3 to 3-1/4 percent"),
        url(a): named(a, "decided to raise the target range for the federal funds rate to 3-1/4 to 3-1/2 percent",
                      voters=PEOPLE[:11], against=f"Voting against this action was {PEOPLE[11]}, who preferred to "
                      "maintain the target range for the federal funds rate at this meeting."),
        url(b): fed_page(fed_tally("11 – 1", "decided to raise the target range for the federal funds rate by 1/4 "
                                   "percentage point to 3-1/2 to 3-3/4 percent",
                                   f"Voting against the monetary policy action was {dev}, who preferred to maintain "
                                   "the target range for the federal funds rate at this meeting."), day=when(b)),
        minutes_url(a): minutes_page(a, PEOPLE[:11], f"{PEOPLE[11]}."),
        minutes_url(b): minutes_page(b, [n for n in PEOPLE if n != dev], f"{dev}."),
        presconf_url(a): fed_presconf_page("January 28-29, 2053", pdf_url(a).removeprefix(SITE)),
        presconf_url(b): fed_presconf_page("March 18-19, 2053", pdf_url(b).removeprefix(SITE)),
        pdf_url(a): fed_transcript_pdf(when(a)),
        pdf_url(b): fed_transcript_pdf(when(b)),
    }
    sites = Sites(pages)
    code, r = go(sites, task(roster, slugs, date(2053, 1, 1)), "backfill", datetime(2053, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    for name in ("meetings_failed", "minutes_failed", "transcripts_failed", "documents_stored", "minutes_votes",
                 "minutes_roster", "voter_terms", "votes_wait", "documents_missing"):
        assert r["checks"][name]["passed"], (name, r["checks"][name]["detail"])
    counts = json.loads(r["checks"]["statements_stored"]["detail"])
    assert {k: counts[k] for k in ("loaded", "named_votes", "tally_statements", "minutes", "minutes_votes",
                                   "transcripts")} == {"loaded": 3, "named_votes": 24, "tally_statements": 1,
                                                       "minutes": 2, "minutes_votes": 12, "transcripts": 2}
    assert doc_rows(slugs[1], "fomc_minutes") == [(1, True, False, doc_rows(slugs[1], "fomc_minutes")[0][3], 0)]
    assert doc_rows(slugs[0], "fomc_minutes") == [] and doc_rows(slugs[0], "fomc_presconf_transcript") == []
    tr = doc_rows(slugs[2], "fomc_presconf_transcript")
    assert len(tr) == 1 and tr[0][1] and tr[0][4] == 3                       # chunks by speaker: three speakers
    meta = q("""select d.title, d.published_at, d.source_url, d.extractor_version from core.document d
                  join core.event e on e.id = d.event_id where e.slug = %s order by d.doc_type""", slugs[2])
    assert meta == [("FOMC minutes, 19 Mar 2053", datetime(2026, 2, 18, 19, 0, tzinfo=UTC), minutes_url(b),
                     "html-1/fed_minutes"),                                 # the calendar's "Released …" day, 2 p.m.
                    ("FOMC press conference transcript, 19 Mar 2053", None, pdf_url(b), "pdf-1"),
                    ("FOMC statement, 19 Mar 2053", datetime(2053, 3, 19, 18, 0, tzinfo=UTC), url(b),
                     "html-1/fed_press_release")]

    # The tally statement's votes, from its minutes: eleven for the decision, the dissent from the statement.
    vb = q("""select p.slug, v.voted_change_bps, v.is_dissent, v.dissent_text, d.doc_type
                from policy.vote v join core.event e on e.id = v.meeting_event_id
                join policy.member p on p.id = v.member_id join core.document d on d.id = v.document_id
               where e.slug = %s""", slugs[2])
    assert len(vb) == 12
    assert {x[1:] for x in vb if x[0] != slug(dev)} == {(25, False, None, "fomc_minutes")}
    assert [x[1:] for x in vb if x[0] == slug(dev)] == [
        (0, True, "preferred to maintain the target range for the federal funds rate at this meeting", "fomc_statement")]
    assert len(votes(slugs[1])) == 12                                         # the named statement's, confirmed

    # A re-run reads everything back: no request, no row, no raw file.
    sites.seen.clear()
    code, r2 = go(sites, task(roster, slugs, date(2053, 1, 1)), "backfill", datetime(2053, 5, 1, tzinfo=UTC))
    assert (code, r2["status"], r2["rows"], sites.seen) == (0, "success", 0, []), r2["error"]
    assert r2["http"].get("new_raw_files", 0) == 0


@pytest.mark.db
def test_daily_takes_the_minutes_once_linked_and_alerts_on_a_transcript_missing_after_30_days(s3, roster):
    x = date(2054, 1, 28)
    slugs = [meeting(d, press=True) for d in (date(2053, 12, 10), x)]
    fred(date(2053, 12, 9), "3", "3.25")
    pages = {fed_calendar.CALENDAR: calendar(date(2053, 12, 10), x),
             url(date(2053, 12, 10)): named(date(2053, 12, 10), "decided to maintain the target range for the "
                                            "federal funds rate at 3 to 3-1/4 percent"),
             url(x): named(x, "decided to maintain the target range for the federal funds rate at 3 to 3-1/4 percent")}
    sites = Sites(pages)
    code, _ = go(sites, task(roster, slugs, date(2054, 1, 1)), "backfill", datetime(2054, 2, 1, tzinfo=UTC))
    assert code == 0 and state(slugs[1])[:2] == ("held", True)

    # Three weeks on, the calendar links the minutes and the press conference; the transcript isn't posted yet.
    pages[fed_calendar.CALENDAR] = calendar(date(2053, 12, 10), x, docs=(x,))
    pages[minutes_url(x)] = minutes_page(x, PEOPLE)
    pages[presconf_url(x)] = fed_presconf_page("January 27-28, 2054", None)
    sites.seen.clear()
    code, r = go(sites, task(roster, slugs, date(2054, 1, 1)), "daily", datetime(2054, 2, 18, 20, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert sites.seen == [fed_calendar.CALENDAR, url(x), minutes_url(x), presconf_url(x)]
    assert doc_rows(slugs[1], "fomc_minutes")[0][:3] == (1, True, False) and r["checks"]["minutes_votes"]["passed"]
    assert json.loads(r["checks"]["statements_stored"]["detail"])["transcripts_waiting"] == 1
    assert not open_alert(f"fed_transcript_missing:{slugs[1]}")

    # 35 days on: the daily run no longer looks for the transcript, and the alert opens.
    sites.seen.clear()
    code, r = go(sites, task(roster, slugs, date(2054, 1, 1)), "daily", datetime(2054, 3, 4, 20, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert presconf_url(x) not in sites.seen and open_alert(f"fed_transcript_missing:{slugs[1]}")

    # The Fed posts it: a backfill takes it, and the alert resolves.
    pages[presconf_url(x)] = fed_presconf_page("January 27-28, 2054", pdf_url(x).removeprefix(SITE))
    pages[pdf_url(x)] = fed_transcript_pdf(when(x))
    code, r = go(sites, task(roster, slugs, date(2054, 1, 1)), "backfill", datetime(2054, 3, 5, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert doc_rows(slugs[1], "fomc_presconf_transcript")[0][:2] == (1, True)
    assert not open_alert(f"fed_transcript_missing:{slugs[1]}")


@pytest.mark.db
def test_a_stored_document_whose_text_changes_becomes_a_new_version_with_an_alert(s3, roster):
    y = date(2055, 1, 27)
    slugs = [meeting(d) for d in (date(2054, 12, 9), y)]
    fred(date(2054, 12, 8), "2", "2.25")
    decision = "decided to maintain the target range for the federal funds rate at 2 to 2-1/4 percent"
    pages = {fed_calendar.CALENDAR: calendar(date(2054, 12, 9), y, docs=(y,)),
             url(date(2054, 12, 9)): named(date(2054, 12, 9), decision), url(y): named(y, decision),
             minutes_url(y): minutes_page(y, PEOPLE)}
    sites = Sites(pages)
    code, r = go(sites, task(roster, slugs, date(2055, 1, 1)), "backfill", datetime(2055, 2, 20, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert [x[:3] for x in doc_rows(slugs[1], "fomc_minutes")] == [(1, True, False)]

    # Only the page's furniture changes: fetched again, but the same text writes nothing.
    pages[minutes_url(y)] = pages[minutes_url(y)].replace("Last Update:", "Last update:")
    code, r = go(sites, task(roster, slugs, date(2055, 1, 1)), "daily", datetime(2055, 2, 21, tzinfo=UTC))
    assert (code, r["status"], r["rows"]) == (0, "success", 0), r["error"]
    # The minutes and the statement are corrected at the source: each becomes a new version, with an alert.
    pages[minutes_url(y)] = pages[minutes_url(y)].replace("The meeting adjourned at 10:30 a.m.",
                                                          "The meeting adjourned at 10:45 a.m.")
    pages[url(y)] = pages[url(y)].replace("Uncertainty about the economic outlook remains elevated.",
                                          "Uncertainty about the economic outlook has diminished.")
    code, r = go(sites, task(roster, slugs, date(2055, 1, 1)), "daily", datetime(2055, 2, 22, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert [x[:3] for x in doc_rows(slugs[1], "fomc_minutes")] == [(1, True, False), (2, True, True)]
    assert [x[:3] for x in doc_rows(slugs[1], "fomc_statement")] == [(1, True, False), (2, True, True)]
    latest = q("""select max(d.id) from core.document d join core.event e on e.id = d.event_id
                   where e.slug = %s and d.doc_type = 'fomc_statement'""", slugs[1])[0][0]
    assert q("""select distinct r.document_id from policy.rate_setting r join core.event e on e.id = r.meeting_event_id
                 where e.slug = %s""", slugs[1]) == [(latest,)]               # the rates point at the new version
    assert open_alert("fed_document_changed:/monetarypolicy/fomcminutes20550127.htm")
    assert open_alert("fed_document_changed:/newsevents/pressreleases/monetary20550127a.htm")


@pytest.mark.db
def test_minutes_that_disagree_with_the_statement_warn_or_for_a_tally_statement_fail(s3, roster):
    n1, n2, t1 = date(2055, 12, 10), date(2056, 1, 28), date(2056, 3, 18)
    slugs = [meeting(d) for d in (n1, n2, t1)]
    fred(n1 - timedelta(days=1), "2", "2.25")
    keep = "decided to maintain the target range for the federal funds rate at 2 to 2-1/4 percent"
    pages = {fed_calendar.CALENDAR: calendar(n1, n2, t1, docs=(n2, t1)),
             url(n1): named(n1, keep), url(n2): named(n2, keep),
             url(t1): fed_page(fed_tally("11 – 1", keep.replace("decided", "decided today"),
                                         f"Voting against the monetary policy action was {PEOPLE[5]}, who preferred to "
                                         "lower the target range for the federal funds rate by 1/4 percentage point "
                                         "at this meeting."), day=when(t1)),
             # N2's minutes name a dissenter the statement doesn't; T1's name the wrong dissenter.
             minutes_url(n2): minutes_page(n2, PEOPLE[:11], f"{PEOPLE[11]}."),
             minutes_url(t1): minutes_page(t1, [n for n in PEOPLE if n != PEOPLE[6]], f"{PEOPLE[6]}.")}
    sites = Sites(pages)
    code, r = go(sites, task(roster, slugs, date(2056, 1, 1)), "backfill", datetime(2056, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    mv = r["checks"]["minutes_votes"]
    assert not mv["passed"] and not mv["blocking"] and f"{slugs[1]}: the minutes and the stored votes differ" in mv["detail"]
    assert doc_rows(slugs[1], "fomc_minutes")[0][:2] == (1, True)            # stored all the same: it's the Fed's text
    mf = r["checks"]["minutes_failed"]
    assert not mf["passed"] and f"{slugs[2]}: dissents_match" in mf["detail"]
    assert doc_rows(slugs[2], "fomc_minutes") == [] and votes(slugs[2]) == {}
    assert open_alert(f"fed_minutes_failed:{slugs[2]}")

    # In a daily run the same minutes fail the task (the last good data stays); corrected, they load.
    code, r = go(sites, task(roster, slugs, date(2056, 1, 1)), "daily", datetime(2056, 4, 8, 20, tzinfo=UTC))
    assert (code, r["status"]) == (1, "checks_failed") and "dissents_match" in r["error"]
    pages[minutes_url(t1)] = minutes_page(t1, [n for n in PEOPLE if n != PEOPLE[5]], f"{PEOPLE[5]}.")
    code, r = go(sites, task(roster, slugs, date(2056, 1, 1)), "daily", datetime(2056, 4, 9, 20, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert len(votes(slugs[2])) == 12 and votes(slugs[2])[slug(PEOPLE[5])][:2] == (-25, True)
    code, r = go(sites, task(roster, slugs, date(2056, 1, 1)), "backfill", datetime(2056, 5, 1, tzinfo=UTC))
    assert not open_alert(f"fed_minutes_failed:{slugs[2]}")


@pytest.mark.db
def test_a_closing_section_checks_the_votes_of_the_meeting_it_records(s3, roster):
    """15 Mar 2020's minutes hold the 2-3 Mar 2020 call's vote under "Videoconference meeting of March 2"."""
    z, call, m = date(2056, 12, 9), date(2057, 3, 3), date(2057, 3, 18)
    slugs = [meeting(d) for d in (z, call, m)]
    fred(z - timedelta(days=1), "1", "1.25")
    first = call - timedelta(days=1)

    def closing(voters):
        return (f"<p><strong>Videoconference meeting of {MONTHS[first.month - 1]} {first.day}</strong><br />\nA joint "
                "meeting of the Federal Open Market Committee and the Board of Governors was held by videoconference "
                f"on {when(first)}, at 7:30 p.m.</p><p><strong>Voting for this action:</strong> {names_list(voters)}.</p>")
    pages = {fed_calendar.CALENDAR: calendar(z, call, m, docs=(m,)),
             url(z): named(z, "decided to maintain the target range for the federal funds rate at 1 to 1-1/4 percent"),
             url(call): named(call, "decided to lower the target range for the federal funds rate by 1/2 percentage "
                              "point, to 1/2 to 3/4 percent"),
             url(m): named(m, "decided to lower the target range for the federal funds rate to 0 to 1/4 percent"),
             minutes_url(m): minutes_page(m, PEOPLE, closing=closing(PEOPLE))}
    sites = Sites(pages)
    code, r = go(sites, task(roster, slugs, date(2057, 1, 1)), "backfill", datetime(2057, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert r["checks"]["minutes_votes"]["passed"], r["checks"]["minutes_votes"]["detail"]
    # The call's vote in the minutes leaves a member out: a warning naming the call.
    pages[minutes_url(m)] = minutes_page(m, PEOPLE, closing=closing(PEOPLE[:11]))     # the daily run fetches it again
    code, r = go(sites, task(roster, slugs, date(2057, 1, 1)), "daily", datetime(2057, 4, 8, 20, tzinfo=UTC))
    mv = r["checks"]["minutes_votes"]
    assert (code, r["status"]) == (0, "success") and not mv["passed"]
    assert f"{slugs[1]} ({slugs[2]}'s minutes, 'Videoconference meeting of March 2'): the minutes and the stored" \
        in mv["detail"]
    assert not r["checks"]["minutes_roster"]["passed"] and "2057-03-02" in r["checks"]["minutes_roster"]["detail"]


# ---------------------------------------------------------------------------------------------- the saved pages
FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "fed"


def saved_site() -> dict[str, bytes]:
    """Every saved Fed page at the URL it came from (setup/save_fed_fixtures.sh)."""
    pages = {}
    for p in FIXTURES.iterdir():
        n = p.name
        if n.startswith("monetary"):
            pages[f"{SITE}/newsevents/pressreleases/{n}"] = p.read_bytes()
        elif n.endswith(".pdf"):                              # FOMCpresconf…, or fomcpresconf20240731.pdf
            pages[f"{SITE}/mediacenter/files/{n}"] = p.read_bytes()
        elif n.startswith(("fomcminutes", "fomcpres", "fomccalendars", "fomchistorical")):
            pages[f"{SITE}/monetarypolicy/{n}"] = p.read_bytes()
    return pages


def votes_full(s: str):
    return {r[0]: r[1:] for r in q("""select p.slug, v.voted_change_bps, v.is_dissent, v.dissent_text, d.doc_type
                                        from policy.vote v join core.event e on e.id = v.meeting_event_id
                                        join policy.member p on p.id = v.member_id
                                        join core.document d on d.id = v.document_id where e.slug = %s""", s)}


@pytest.mark.db
@pytest.mark.skipif(not (FIXTURES / "fomcminutes20260916.htm").exists(),
                    reason="the saved pages are only on the Mac (app/data/fixtures, git-ignored)")
def test_a_backfill_over_the_saved_pages_loads_every_statement_minutes_and_transcript_from_the_2018_baseline(s3):
    """B5's done-when over the pages saved 23 Oct 2026 and the real roster (config/members.yaml): every statement from
    the Dec 2018 baseline to Jul 2026 passes its blocking checks, 2019-20 and the unscheduled 3 and 15 Mar 2020 included;
    every voter is stored, the dissents match the statements (Sep 2020's two without bps), the tally statements' votes
    come from the minutes; the roster matches every minutes' vote; 61 minutes and every saved transcript load. The meetings come
    from the saved calendar pages through the calendar's own rules."""
    from ft.policy import meetings

    rows = fed_calendar.parse_page((FIXTURES / "fomccalendars.htm").read_bytes(), fed_calendar.CALENDAR)
    for y in (2018, 2019, 2020):
        rows += fed_calendar.parse_page((FIXTURES / f"fomchistorical{y}.htm").read_bytes(), fed_calendar.historical(y))
    ms, _ = meetings.fed_meetings(rows, meetings.load_curated(), full=True)
    real = [m for m in ms if m.is_rate_decision and m.decision_date <= date(2026, 9, 16)]
    assert len(real) == 64 and real[0].slug == "fomc-2018-12-19"
    # The SQL fixture (supabase/tests/test_fixture.sql) holds a synthetic statement under fomc-2026-09-16, so that
    # meeting is left out here (its statement, minutes and press-conference page are checked by the parser tests).
    real = [m for m in real if m.slug != "fomc-2026-09-16"]
    slugs = [m.slug for m in real]
    # test_calendar_task's backfill leaves events under some of these slugs, built from its synthetic pages: set each to
    # the real calendar's values. Its synthetic calendar pages are stored under the real URLs, so forget them too.
    with psycopg.connect(JOB, autocommit=True) as c:
        for m in real:
            c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at,
                                                 status, source_id, source_url, is_published)
                         select 'policy_meeting', b.id, %s, 'FOMC meeting', %s, %s, %s, 'scheduled', src.id, %s, false
                           from core.entity b, core.source src where b.slug = 'fed' and src.key = 'fed'
                         on conflict (event_type, slug) do update
                            set starts_on = excluded.starts_on, ends_on = excluded.ends_on, occurs_at = excluded.occurs_at""",
                      (m.slug, m.starts_on, m.ends_on, m.occurs_at, m.source_url))
            c.execute("""insert into policy.meeting (event_id, kind, is_rate_decision, has_press_conference, minutes_due_on)
                         select id, %s, true, %s, %s from core.event where event_type = 'policy_meeting' and slug = %s
                         on conflict (event_id) do update set kind = excluded.kind, is_rate_decision = true,
                                has_press_conference = excluded.has_press_conference,
                                minutes_due_on = excluded.minutes_due_on""",
                      (m.kind, m.has_press_conference, m.minutes_due_on, m.slug))
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("delete from ops.http_cache where url like %s", (SITE + "/monetarypolicy/fomc%",))
    fred(date(2018, 12, 18), "2", "2.25")                    # the baseline's change: +25 bps (FRED, the day before)
    sites = Sites(saved_site())
    t = task(members.CONFIG, slugs, meetings.FIRST_DAY)
    code, r = go(sites, t, "backfill", datetime(2026, 10, 23, 12, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    for name in ("meetings_failed", "minutes_failed", "transcripts_failed", "voter_terms", "minutes_votes",
                 "minutes_roster", "votes_wait", "baseline_change"):
        assert r["checks"][name]["passed"], (name, r["checks"][name]["detail"])
    # Jul 2024's transcript link is lower-case (fomcpresconf20240731.pdf): the fixtures script saves it only since
    # 24 Oct 2026, so it's expected when the file is there and reported missing when it isn't.
    jul24 = (FIXTURES / "fomcpresconf20240731.pdf").exists()
    counts = json.loads(r["checks"]["statements_stored"]["detail"])
    assert counts == {"targets": 63, "loaded": 63, "waiting": 0, "failed": 0, "named_votes": 673, "dissents": 31,
                      "tally_statements": 2, "rates_pending": 0, "from_rss": 0, "roster": 52, "minutes": 61,
                      "minutes_votes": 24, "transcripts": 61 + jul24, "transcripts_waiting": 0,
                      "documents_failed": 0}, counts
    assert r["checks"]["documents_missing"]["detail"] == ("" if jul24 else (
        "fomc-2024-07-31: transcript https://www.federalreserve.gov/mediacenter/files/fomcpresconf20240731.pdf"))

    # Every voter of every meeting is stored, each with a document and a span.
    n = q("""select count(*), count(distinct e.slug) from policy.vote v join core.event e on e.id = v.meeting_event_id
              where e.slug = any(%s)""", slugs)[0]
    assert n == (673 + 24, 63)
    sep20 = votes_full("fomc-2020-09-16")                     # two dissents about guidance, not the rate: no bps
    assert {k: v[:2] for k, v in sep20.items() if v[1]} == {"robert-s-kaplan": (None, True), "neel-kashkari": (None, True)}
    assert {k: v[:2] for k, v in votes_full("fomc-2020-03-15").items() if v[1]} == {"loretta-j-mester": (-50, True)}
    assert len(votes_full("fomc-2020-03-03")) == 10 and len(votes_full("fomc-2018-12-19")) == 10
    jul26 = votes_full("fomc-2026-07-29")                     # a tally statement: the minutes name the nine for
    assert len(jul26) == 12 and sum(v[0] == 0 and not v[1] and v[3] == "fomc_minutes" for v in jul26.values()) == 9
    assert {k: v[:2] + (v[3],) for k, v in jul26.items() if v[1]} == {
        s: (25, True, "fomc_statement") for s in ("beth-m-hammack", "neel-kashkari", "lorie-k-logan")}
    assert jul26["beth-m-hammack"][2].startswith("preferred to raise the target range")
    assert len(votes_full("fomc-2026-06-17")) == 12
    harker = votes_full("fomc-2022-03-16")["patrick-harker"]  # Boston's seat, empty to Jul 2022: its alternate voted
    assert harker[:2] == (25, False)                          # the first hike of 2022, +25 bps

    docs = dict(q("""select d.doc_type, count(*) from core.document d join core.event e on e.id = d.event_id
                      where e.slug = any(%s) and d.is_published group by 1""", slugs))
    assert docs == {"fomc_statement": 63, "fomc_minutes": 61, "fomc_presconf_transcript": 61 + jul24}
    speakers = q("""select count(distinct c.speaker) from core.chunk c join core.document d on d.id = c.document_id
                     join core.event e on e.id = d.event_id
                    where e.slug = 'fomc-2026-07-29' and d.doc_type = 'fomc_presconf_transcript'""")[0][0]
    assert speakers >= 5
    alerts = {k for (k,) in q("select dedupe_key from ops.alert where resolved_at is null and dedupe_key like 'fed_%%'")}
    assert ("fed_transcript_missing:fomc-2024-07-31" in alerts) != jul24
    assert not {a for a in alerts if a.startswith(("fed_statement_failed:", "fed_minutes_failed:",
                                                   "fed_transcript_failed:")) and a.split(":", 1)[1] in slugs}

    # A re-run reads everything back: no row and no raw file (pages it lacks are asked for again, conditionally).
    code, r2 = go(sites, task(members.CONFIG, slugs, meetings.FIRST_DAY), "backfill", datetime(2026, 10, 23, 12, tzinfo=UTC))
    assert (code, r2["status"], r2["rows"]) == (0, "success", 0), r2["error"]
    assert r2["http"].get("new_raw_files", 0) == 0
