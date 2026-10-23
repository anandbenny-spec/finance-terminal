"""policy.fed_documents, part 1 (the statement path), through the runner against the test database as job_writer
(Prompt 01 B5; Architecture 02 §1.8, §2; Spec 01 §2, §3.1). Pages come from a mock transport (synthetic statements
in the real template: policy_docs.fed_page), raw files go to a local S3-protocol server (moto), the clock is the
test's. Meetings are synthetic, 2039-2044 (`fomc-…` slugs the other tests don't use), and the task sees only each
test's own meetings (`scope`), with its own roster file and baseline cut-off."""
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
from policy_docs import fed_named, fed_page, fed_rss, fed_tally
from test_policy_calendar import fed_links, fed_row, fed_year

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import JobRun, run
from ft.jobs.tasks.fed_documents import FedDocuments
from ft.jobs.watch import watch
from ft.policy import fed_calendar, fed_statement
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


def meeting(d: date, *, at: str = "14:00") -> str:
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
        c.execute("""insert into policy.meeting (event_id, kind, is_rate_decision, minutes_due_on)
                     select id, 'scheduled', true, %s from core.event where event_type = 'policy_meeting' and slug = %s
                     on conflict do nothing""", (d + timedelta(days=21), s))
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


def calendar(*dates: date) -> str:
    years = {}
    for d in dates:
        years.setdefault(d.year, []).append(fed_row(MONTHS[d.month - 1], f"{d.day - 1}-{d.day}", fed_links(f"{d:%Y%m%d}")))
    return "<html><body>" + "".join(fed_year(y, *rows) for y, rows in sorted(years.items())) + "</body></html>"


class Sites:
    """GET pages by URL, with ETags (an unchanged page answers 304). Records every request."""

    def __init__(self, pages: dict[str, str]):
        self.pages, self.seen = pages, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        self.seen.append(u)
        if u not in self.pages:
            return httpx.Response(404)
        body = self.pages[u]
        etag = '"' + uuid.uuid5(uuid.NAMESPACE_URL, body).hex + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, text=body, headers={"Content-Type": "text/html; charset=utf-8", "ETag": etag})


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

    # A re-run reads the stored statements back: no request, no row, no raw file.
    sites.seen.clear()
    code, r2 = go(sites, task(roster, slugs, date(2040, 1, 1)), "backfill", datetime(2040, 5, 1, tzinfo=UTC))
    assert (code, r2["status"], r2["rows"], sites.seen) == (0, "success", 0, [])
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
    assert sites.seen == [fed_calendar.CALENDAR]                             # the calendar's one conditional read
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
