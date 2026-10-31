"""policy.rbi_documents, part 1 (MPC resolutions), through the runner against the test database as job_writer
(Prompt 01 B6; Architecture 02 §1.8, §2; Spec 01 §2, §3.1). Pages come from a mock transport (synthetic resolutions in
the real template: policy_docs; the Annual Policy page as test_policy_calendar builds it), raw files go to a local
S3-protocol server (moto), the clock is the test's. Meetings are synthetic (`mpc-…` slugs no other test uses: 2011-12,
two fictional 2022 meetings either side of the SDF's start, 2045-46), and the task sees only each test's own meetings
(`scope`), with its own roster file and baseline cut-off."""
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer
from policy_docs import (
    mpc_minutes,
    mpc_new,
    mpc_old,
    mpc_title,
    rbi_speech_page,
    rbi_statement_body,
    rbi_transcript_speech,
    rbi_transcript_view,
    rbi_view_page,
)
from policy_docs import rbi_page as press_release
from test_policy_calendar import rbi_group, rbi_item
from test_policy_calendar import rbi_page as annual_page

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import JobRun, run
from ft.jobs.tasks.rbi_documents import RbiDocuments
from ft.jobs.watch import watch
from ft.policy import rbi_calendar, rbi_resolution, rbi_statements

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
IST = ZoneInfo("Asia/Kolkata")
PORT, BUCKET = 5596, "rbi-docs-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.b6_rbi_documents"
PR = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid="
RESOLUTION = "Resolution of the Monetary Policy Committee (MPC), Reserve Bank of India"
# Six fictional members, and a seventh who joins later.
SIX = ["Dr. Ada Rao", "Dr. Ben Iyer", "Prof. Cara N. Das", "Dr. Dev K. Shah", "Shri Eli Paul", "Shri Fay Roy"]
NEWCOMER = "Smt. Gia Menon"


def slug_of(name: str) -> str:
    return "b6-" + rbi_resolution.name_key(name).replace(" ", "-")


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'B6 rbi documents test', '{daily,watch}', '20 hours', '3 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()
    # The stored copies behind this module's cache rows went with its S3 server, and another module's task would read a
    # closed fiscal year's page back from them (test_calendar_task's backfill does), so forget them.
    q("""delete from ops.http_cache where task_run_id in (select id from ops.task_run where task_key = %s)""", KEY)


@pytest.fixture
def roster(tmp_path) -> Path:
    path = tmp_path / "members.yaml"
    roles = ["rbi_governor", "rbi_deputy_governor", "rbi_executive_director"] + ["mpc_external_member"] * 3
    lines = ["rbi:"]
    for name, role in zip(SIX, roles, strict=True):
        bare = name.split(" ", 1)[1]
        lines += [f"  {slug_of(name)}:", f"    name: {bare}", "    terms:",
                  f"      - {{role: {role}, affiliation: Test, voter: true, from: 2011-01-01, source: test roster}}"]
    lines += [f"  {slug_of(NEWCOMER)}:", f"    name: {NEWCOMER.split(' ', 1)[1]}", "    terms:",
              "      - {role: mpc_external_member, affiliation: Test, voter: true, from: 2048-01-01, source: test}"]
    path.write_text("\n".join(lines) + "\n")
    return path


def storage():
    from ft.storage import Storage
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        cur = c.execute(sql, a)
        return cur.fetchall() if cur.description else cur.rowcount


def meeting(d: date, *, kind: str = "scheduled", no: int | None = None) -> str:
    s = f"mpc-{d.isoformat()}"
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at, status,
                                             source_id, source_url, is_published)
                     select 'policy_meeting', b.id, %s, 'MPC meeting', %s, %s, %s, 'scheduled', src.id, %s, false
                       from core.entity b, core.source src where b.kind = 'central_bank' and b.slug = 'rbi'
                        and src.key = 'rbi'
                     on conflict (event_type, slug) do nothing""",
                  (s, d - timedelta(days=2), d, datetime(d.year, d.month, d.day, 10, 0, tzinfo=IST),
                   rbi_calendar.ANNUAL_POLICY))
        c.execute("""insert into policy.meeting (event_id, meeting_no, kind, is_rate_decision, minutes_due_on)
                     select id, %s, %s, true, %s from core.event where event_type = 'policy_meeting' and slug = %s
                     on conflict do nothing""", (no, kind, d + timedelta(days=14), s))
    return s


def resolution(d: date, body: str) -> str:
    return press_release(body, title=mpc_title(days_of(d), f"{d.year}-{d.year % 100 + 1:02d}"), day=f"{d:%b %d, %Y}")


def statement_link(prid: int) -> str:
    """The Governor's statement the decision group with this resolution prid lists (its own prid: `prid` and a 9)."""
    return PR + f"{prid}9"


def annual(*items: tuple[date, int], years: dict[str, str], minutes: tuple[tuple[date, int], ...] = (),
           statements: dict[date, str] | None = None,
           transcripts: tuple[tuple[date, str, int, bool], ...] = ()) -> str:
    """An Annual Policy page whose groups are these decisions (date, prid), each listing its Governor's statement
    (statement_link(prid), or `statements`' link for the date: None lists none), minutes (the meeting's decision date,
    prid; posted 14 days on) and press-conference transcripts (the decision date, the link, days after it, whether the
    title names the date), newest first, above a report's group."""
    statements = statements or {}

    def statement(d: date, prid: int) -> str:
        link = statements.get(d, statement_link(prid))
        return rbi_item(f"Governor’s Statement: {d:%B} {d.day}, {d.year}", link) if link else ""
    groups = [(d, rbi_group(f"{d:%b %d, %Y}", "Bi-monthly Monetary Policy Statement",
                            statement(d, prid) + rbi_item(RESOLUTION, PR + str(prid), True)))
              for d, prid in items]
    groups += [(d + timedelta(days=14), rbi_group(f"{d + timedelta(days=14):%b %d, %Y}",
                                                  "Minutes of the Monetary Policy Committee",
                                                  rbi_item(f"Minutes of the Monetary Policy Committee Meeting, {days_of(d)}",
                                                           PR + str(prid))))
               for d, prid in minutes]
    groups += [(d + timedelta(days=after), rbi_group(
        f"{d + timedelta(days=after):%b %d, %Y}", "Bi-monthly Monetary Policy Statement",
        rbi_item("Edited Transcript of the Reserve Bank of India’s Monetary Policy Press Conference"
                 + (f": {d:%B} {d.day}, {d.year}" if dated else ""), url)))
        for d, url, after, dated in transcripts]
    out = [g for _, g in sorted(groups, key=lambda x: x[0], reverse=True)]
    out.append(rbi_group("Jan 02, 2000", "Monetary Policy Report",
                         rbi_item("Monetary Policy Report", "https://rbi.org.in/Scripts/PublicationsView.aspx?id=1")))
    return annual_page(*out, years=years)


def days_of(d: date) -> str:
    """A meeting's days as the titles write them: the two days before the decision and the decision day."""
    first = d - timedelta(days=2)
    return (f"{first:%B} {first.day} to {d.day}, {d.year}" if first.month == d.month
            else f"{first:%B} {first.day} to {d:%B} {d.day}, {d.year}")


def minutes(d: date, resolution: list[str], rows, *, heading: str | None = "Voting on the Resolution", no: int = 7,
            **kw) -> str:
    """A minutes page for the meeting decided on `d`, posted 14 days on."""
    posted = d + timedelta(days=14)
    body = mpc_minutes(no=no, days=days_of(d), members=SIX, resolution=resolution, heading=heading, rows=rows, **kw)
    return press_release(body, title=f"Minutes of the Monetary Policy Committee Meeting, {days_of(d)}",
                         day=f"{posted:%b %d, %Y}")


def old(decision: str, consequently: str, stance: str, votes: list[str], projections: list[str] = ()) -> str:
    return mpc_old(decision=decision, consequently=consequently, stance=stance, votes=votes, projections=projections)


def named(subject: list[str]) -> str:
    return ", ".join(subject[:-1]) + f" and {subject[-1]}"


def rss(*items: tuple[str, str, str]) -> str:
    body = "".join(f"<item><title><![CDATA[{t}]]></title><link>{u}</link><pubDate>{p}</pubDate></item>"
                   for t, u, p in items)
    return f'﻿<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>RBI</title>{body}</channel></rss>'


class Sites:
    """GET pages by URL (with ETags), and the Annual Policy page's year selector by form post. Records requests."""

    def __init__(self, pages: dict[str, str], years: dict[str, str] | None = None):
        self.pages, self.years, self.seen = pages, years or {}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if request.method == "POST":
            year = parse_qs(request.content.decode())["hdnYear"][0]
            self.seen.append(f"POST {u} hdnYear={year}")
            return httpx.Response(200, text=self.years[year], headers={"Content-Type": "text/html; charset=utf-8"})
        self.seen.append(u)
        if u not in self.pages:
            return httpx.Response(404)
        body = self.pages[u]
        etag = '"' + uuid.uuid5(uuid.NAMESPACE_URL, body).hex + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, text=body, headers={"Content-Type": "text/html; charset=utf-8", "ETag": etag})


def task(roster_path: Path, scope, first_day: date, *, d4: bool = False) -> RbiDocuments:
    class Docs(RbiDocuments):
        key = KEY
        D4_ROSTER = d4
    t = Docs()
    t.members_path, t.scope, t.first_day = roster_path, frozenset(scope), first_day
    t.workflows = ("daily", "watch")
    t.rules = {"daily": "every 20 hours", "watch": "mpc_decision_day"}
    return t


def go(sites: Sites, t: RbiDocuments, workflow: str, now: datetime, **kw):
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
    return {r[0]: r[1:] for r in q("""select p.slug, v.voted_change_bps, v.stance_vote, v.is_dissent, v.dissent_text
                                        from policy.vote v join core.event e on e.id = v.meeting_event_id
                                        join policy.member p on p.id = v.member_id where e.slug = %s""", s)}


def forecasts(s: str):
    return q("""select s.key, f.target_label, f.value::text, f.value_low::text, f.value_high::text, f.method,
                       substr(t.full_text, f.char_start + 1, f.char_end - f.char_start)
                  from core.forecast f join core.series s on s.id = f.series_id
                  join core.document d on d.id = f.document_id join core.event e on e.id = d.event_id
                  join lateral (select string_agg(c.body, '' order by c.chunk_no) as full_text from core.chunk c
                                 where c.document_id = d.id) t on true
                 where e.slug = %s order by 1, f.target_start, f.target_end""", s)


def minutes_docs(s: str):
    """(title, published_at, published, the chunks' speakers) of each minutes version stored for the meeting."""
    return [(r[0], r[1], r[2], set(r[3]) - {None}) for r in q(
        """select d.title, d.published_at, d.is_published, array_agg(distinct c.speaker)
             from core.document d join core.event e on e.id = d.event_id
             left join core.chunk c on c.document_id = d.id
            where e.slug = %s and d.doc_type = 'mpc_minutes' group by d.id order by d.version_no""", s)]


def vote_docs(s: str):
    """{member slug: the doc type each vote row cites}."""
    return {r[0]: r[1] for r in q("""select p.slug, d.doc_type from policy.vote v join core.event e on e.id = v.meeting_event_id
                                      join policy.member p on p.id = v.member_id join core.document d on d.id = v.document_id
                                     where e.slug = %s""", s)}


def alerts(prefix: str):
    return sorted(r[0] for r in q("select dedupe_key from ops.alert where dedupe_key like %s and resolved_at is null",
                                  prefix + "%"))


def state(s: str):
    """(event status, event published, resolution stored, resolution published, its diff's base slug)."""
    return q("""select e.status, e.is_published, d.id is not null, coalesce(d.is_published, false),
                       (select be.slug from core.document_diff x join core.document bd on bd.id = x.base_document_id
                          join core.event be on be.id = bd.event_id where x.document_id = d.id)
                  from core.event e
                  left join core.document d on d.event_id = e.id and d.doc_type = 'mpc_resolution'
                 where e.slug = %s order by d.version_no desc nulls last limit 1""", s)[0]


# ---------------------------------------------------------------------------------------------- backfill
BASE, FEB, APR, JUN = date(2011, 12, 7), date(2012, 2, 8), date(2012, 4, 4), date(2012, 6, 6)

BASE_BODY = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.5 per cent.",
                "Consequently, the reverse repo rate under the LAF remains at 6.25 per cent, and the marginal standing "
                "facility (MSF) rate and the Bank Rate at 6.75 per cent.",
                "The decision of the MPC is consistent with the stance of calibrated tightening of monetary policy.",
                [("While the decision on keeping the policy rate unchanged was unanimous, Dr. Ben Iyer voted to change the "
                 "stance to neutral.")])
FEB_BODY = old("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points from 6.5 per "
               "cent to 6.25 per cent with immediate effect.",
               "Consequently, the reverse repo rate under the LAF stands adjusted to 6.0 per cent, and the marginal "
               "standing facility (MSF) rate and the Bank Rate to 6.5 per cent.",
               "The MPC also decided to change the monetary policy stance from calibrated tightening to neutral.",
               [("The decision to change the monetary policy stance was unanimous. As regards the reduction in the policy "
                f"repo rate, {named(SIX[:4])} voted in favour of the decision. {named(SIX[4:])} voted to keep the policy "
                "rate unchanged.")],
               [("Taking all these factors into consideration, CPI inflation for 2012-13 is projected at 4.5 per cent "
                "with Q1 at 4.9 per cent; Q2 at 5.0 per cent; Q3 at 4.0 per cent; and Q4 at 4.2 per cent (Chart 1)."),
                ("Real GDP growth for 2012-13 is projected at 7.4 per cent – in the range of 7.2-7.4 per cent in H1, and "
                "7.5 per cent in Q3 – with risks evenly balanced.")])
APR_BODY = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.25 per cent.",
               "Consequently, the reverse repo rate under the LAF remains unchanged at 5.90 per cent and the marginal "
               "standing facility (MSF) rate and the Bank Rate at 6.50 per cent.",
               "The MPC also decided to maintain the neutral stance of monetary policy.",
               [(f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged at "
                "6.25 per cent and to maintain the neutral stance.")],
               [("Taking into account these factors, CPI inflation is projected at 4.4 per cent for 2012-13, with Q2 at "
                "4.6 per cent, Q3 at 4.0 per cent and Q4 at 4.4 per cent."),
                "Real GDP growth for 2012-13 is projected at 7.1 per cent."])


# The minutes' copies of FEB's and APR's resolutions (their vote sentences, as the resolutions state them).
FEB_COPY = [("On the basis of an assessment, the MPC decided to reduce the policy repo rate by 25 basis points from 6.5 "
             "per cent to 6.25 per cent with immediate effect."),
            "The MPC also decided to change the monetary policy stance from calibrated tightening to neutral.",
            (f"The decision to change the monetary policy stance was unanimous. As regards the reduction in the policy "
             f"repo rate, {named(SIX[:4])} voted in favour of the decision. {named(SIX[4:])} voted to keep the policy "
             "rate unchanged.")]
APR_COPY = ["On the basis of an assessment, the MPC decided to keep the policy repo rate unchanged at 6.25 per cent.",
            "The MPC also decided to maintain the neutral stance of monetary policy.",
            (f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged at 6.25 "
             "per cent and to maintain the neutral stance.")]
FEB_MINUTES = minutes(FEB, FEB_COPY, [(n, "Yes") for n in SIX[:4]] + [(n, "No") for n in SIX[4:]],
                      heading="Voting on the Resolution to reduce the policy repo rate by 25 bps to 6.25 per cent",
                      press_release="2011-2012/1501")
APR_MINUTES = minutes(APR, APR_COPY, [(n, "Yes") for n in SIX], heading_in_table=True,
                      heading="Voting on the Resolution to keep the policy repo rate unchanged at 6.25 per cent")

# Part 3: the Governor's statements (a press release; a viewcontent page without a date) and the transcripts (a
# viewcontent page dated on its first line; a speech page dated by its title). SIX[0], Ada Rao, is the Governor.
VIEW, SPEECH = "https://www.rbi.org.in/Scripts/bs_viewcontent.aspx?Id=", "https://www.rbi.org.in/Scripts/BS_SpeechesView.aspx?Id="
TURNS = ["Ada Rao:", "Latha Venkatesh (CNBC-TV18):", "Ada Rao:", "Dr. Ben Iyer:", "Ada Rao:"] * 4
CAST = ["Dr. Ada Rao – Governor", "Dr. Ben Iyer – Deputy Governor"]


def gov_statement(d: date, *, number: str | None = None) -> str:
    return press_release(rbi_statement_body(f"The Monetary Policy Committee (MPC) met on {days_of(d)}.",
                                            press_release=number),
                         title=f"Governor’s Statement: {d:%B} {d.day}, {d.year}", day=f"{d:%b %d, %Y}")


def view_transcript(d: date, turns=TURNS) -> str:
    return rbi_view_page(rbi_transcript_view(turns, date_line=f"{d:%B} {d.day:02d}, {d.year}", participants=CAST),
                         title="Edited Transcript of Reserve Bank of India’s Fifth Bi-monthly Monetary Policy Press "
                               "Conference")


def speech_transcript(d: date, posted: date, turns=TURNS) -> str:
    return rbi_speech_page(rbi_transcript_speech(turns, participants=CAST), day=f"{posted:%b %d, %Y}",
                           title="Edited Transcript of the Reserve Bank of India’s Post-Monetary Policy Press "
                                 f"Conference: {d:%B} {d.day}, {d.year} (Wednesday)")


def part3_docs(s: str):
    """(doc type, title, published_at, source_url, extractor version, the chunks' speakers) of the meeting's statement
    and transcript."""
    return [(r[0], r[1], r[2], r[3], r[4], set(r[5]) - {None}) for r in q(
        """select d.doc_type, d.title, d.published_at, d.source_url, d.extractor_version, array_agg(distinct c.speaker)
             from core.document d join core.event e on e.id = d.event_id
             left join core.chunk c on c.document_id = d.id
            where e.slug = %s and d.doc_type in ('rbi_governor_statement', 'rbi_presconf_transcript') and d.is_published
            group by d.id order by d.doc_type, d.version_no""", s)]


@pytest.mark.db
def test_backfill_stores_resolutions_rates_votes_projections_and_diffs_and_reruns_clean(s3, roster):
    slugs = [meeting(d) for d in (BASE, FEB, APR)]
    years = {"2012-2013": "2013", "2011-2012": "2012"}
    fy12 = annual((BASE, 81001), (FEB, 81101), years=years, minutes=((FEB, 81102),),
                  transcripts=((BASE, VIEW + "81000", 4, False), (FEB, SPEECH + "81100", 2, True)))
    pages = {rbi_calendar.ANNUAL_POLICY: annual((APR, 81201), years=years, minutes=((APR, 81202),),
                                                statements={APR: "https://rbi.org.in/scripts/bs_viewcontent.aspx?Id=81200"}),
             PR + "81001": resolution(BASE, BASE_BODY), PR + "81101": resolution(FEB, FEB_BODY),
             PR + "81201": resolution(APR, APR_BODY), PR + "81102": FEB_MINUTES, PR + "81202": APR_MINUTES,
             statement_link(81101): gov_statement(FEB, number="2011-2012/1499"),
             VIEW + "81200": rbi_view_page(rbi_statement_body("Over the past three days the MPC reviewed conditions."),
                                           title="Statement by Governor - First Bi-monthly Monetary Policy, 2012-13"),
             VIEW + "81000": view_transcript(BASE), SPEECH + "81100": speech_transcript(FEB, FEB + timedelta(days=2))}
    sites = Sites(pages, {"2012": fy12})
    t = task(roster, slugs, date(2012, 1, 1))
    code, r = go(sites, t, "backfill", datetime(2012, 7, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert r["checks"]["resolutions_stored"]["passed"], r["checks"]["resolutions_stored"]["detail"]
    assert r["checks"]["meetings_failed"]["passed"]
    assert f"POST {rbi_calendar.ANNUAL_POLICY} hdnYear=2012" in sites.seen     # the earlier fiscal year, by form post

    # Part 3: the Governor's statements after the baseline (never the baseline's: its decision document only), the
    # transcripts from it (Anand, 23 Oct 2026), each published with its chunks; a transcript's chunks carry its speakers.
    for name in ("statements_stored", "transcripts_stored", "statements_failed", "transcripts_failed",
                 "statement_link", "annual_policy_docs", "documents_missing", "annual_policy_link"):
        assert r["checks"][name]["passed"], (name, r["checks"][name]["detail"])
    assert statement_link(81001) not in sites.seen
    assert {k: v for k, v in json_counts(r).items() if k.startswith(("statements", "transcripts"))} == {
        "statements": 2, "statements_failed": 0, "transcripts": 2, "transcripts_failed": 0}
    speakers = {"Ada Rao", "Latha Venkatesh", "Dr. Ben Iyer"}
    assert part3_docs(slugs[0]) == [
        ("rbi_presconf_transcript", "RBI press conference transcript, 7 Dec 2011", datetime(2011, 12, 11, tzinfo=IST),
         VIEW + "81000", "html-1/rbi_view_content", speakers)]
    assert part3_docs(slugs[1]) == [
        ("rbi_governor_statement", "Governor's statement, 8 Feb 2012 · Press Release: 2011-2012/1499",
         datetime(2012, 2, 8, 10, tzinfo=IST), statement_link(81101), "html-1/rbi_press_release", set()),
        ("rbi_presconf_transcript", "RBI press conference transcript, 8 Feb 2012", datetime(2012, 2, 10, tzinfo=IST),
         SPEECH + "81100", "html-1/rbi_speech", speakers)]
    assert part3_docs(slugs[2]) == [
        ("rbi_governor_statement", "Governor's statement, 4 Apr 2012", datetime(2012, 4, 4, 10, tzinfo=IST),
         VIEW + "81200", "html-1/rbi_view_content", set())]

    # The minutes (never the baseline's: Spec 01 §2), each member's statement its own speaker; they agree with the
    # resolutions, so the votes stand as the resolutions stated them, and the roster matches both (D4's check).
    assert r["checks"]["minutes_stored"]["passed"] and r["checks"]["minutes_failed"]["passed"], r["checks"]["minutes_failed"]
    assert minutes_docs(slugs[0]) == [] and [x[0] for x in minutes_docs(slugs[1])] == [
        "MPC minutes, 8 Feb 2012 · Press Release: 2011-2012/1501"]
    assert minutes_docs(slugs[2])[0][1:] == (datetime(2012, 4, 18, tzinfo=IST), True, {n for n in SIX})
    assert r["checks"]["minutes_changed_vote"]["passed"] and r["checks"]["minutes_roster"]["passed"]
    assert r["checks"]["roster_matches_minutes"]["passed"], r["checks"]["roster_matches_minutes"]

    # Rates: the baseline's changes from its own sentences, then against the previous meeting.
    assert rates(slugs[0]) == [("bank_rate", "6.750", 0, None), ("msf", "6.750", 0, None), ("repo", "6.500", 0, None),
                               ("reverse_repo", "6.250", 0, None)]
    assert [(x[0], x[2]) for x in rates(slugs[1])] == [("bank_rate", -25), ("msf", -25), ("repo", -25),
                                                      ("reverse_repo", -25)]
    assert [(x[0], x[2]) for x in rates(slugs[2])] == [("bank_rate", 0), ("msf", 0), ("repo", 0), ("reverse_repo", -10)]
    assert r["checks"]["between_meetings"]["passed"]          # nothing stated "from" a value other than the last meeting

    # Votes (Spec 01 §2; issue #15): named and "all members"; the baseline names one member and its unanimous vote
    # waits for the minutes (D4's roster inference is off).
    feb = votes(slugs[1])
    assert len(feb) == 6 and feb[slug_of(SIX[4])] == (0, "neutral", True, "voted to keep the policy rate unchanged")
    assert feb[slug_of(SIX[0])] == (-25, "neutral", False, None)
    assert len(votes(slugs[2])) == 6 and not any(v[2] for v in votes(slugs[2]).values())
    assert votes(slugs[0]) == {slug_of(SIX[1]): (0, "neutral", True, "voted to change the stance to neutral")}
    vw = r["checks"]["votes_wait"]
    assert not vw["passed"] and not vw["blocking"] and slugs[0] in vw["detail"] and slugs[1] not in vw["detail"]

    # Projections, with their spans in the stored text.
    fc = forecasts(slugs[1])
    assert [x[:5] for x in fc] == [
        ("rbi.proj_cpi", "Q1 2012-13", "4.9", None, None), ("rbi.proj_cpi", "FY 2012-13", "4.5", None, None),
        ("rbi.proj_cpi", "Q2 2012-13", "5.0", None, None), ("rbi.proj_cpi", "Q3 2012-13", "4.0", None, None),
        ("rbi.proj_cpi", "Q4 2012-13", "4.2", None, None), ("rbi.proj_gdp", "H1 2012-13", None, "7.2", "7.4"),
        ("rbi.proj_gdp", "FY 2012-13", "7.4", None, None), ("rbi.proj_gdp", "Q3 2012-13", "7.5", None, None)]
    assert all(x[5] == "parsed" for x in fc) and fc[5][6] == "in the range of 7.2-7.4 per cent in H1"
    assert q("select bool_and(is_published) from core.series where key in ('rbi.proj_cpi', 'rbi.proj_gdp')")[0][0]
    assert r["checks"]["cpi_projection"]["passed"] is False and slugs[0] in r["checks"]["cpi_projection"]["detail"]

    assert state(slugs[0]) == ("held", True, True, True, None)               # the baseline has no diff
    assert [state(s)[4] for s in slugs[1:]] == slugs[:2]
    doc = q("""select d.title, d.published_at, d.source_url, d.extractor_version, d.external_key
                 from core.document d join core.event e on e.id = d.event_id
                where e.slug = %s and d.doc_type = 'mpc_resolution'""", slugs[2])[0]   # the event has 3 documents
    # The title names RBI's press-release number (Brief §6.2: the site names RBI's documents, never links them).
    assert doc == ("MPC resolution, 4 Apr 2012 · Press Release: 2040-2041/123", datetime(2012, 4, 4, 4, 30, tzinfo=UTC),
                   PR + "81201",
                   "html-1/rbi_press_release", "/Scripts/BS_PressReleaseDisplay.aspx?prid=81201")

    # A re-run reads the stored resolutions, minutes, statements and transcripts back: no row, no raw file. APR has no
    # transcript (as 2020's COVID meetings), so the run looks again: one conditional request for the current year's page
    # (unchanged: 304), the closed year from its stored copy (no form post), and no document page.
    sites.seen.clear()
    code, r2 = go(sites, task(roster, slugs, date(2012, 1, 1)), "backfill", datetime(2012, 7, 1, tzinfo=UTC))
    assert (code, r2["status"], r2["rows"], sites.seen) == (0, "success", 0, [rbi_calendar.ANNUAL_POLICY])
    assert r2["http"].get("new_raw_files", 0) == 0 and r2["checks"]["resolutions_stored"]["passed"]
    assert r2["checks"]["statements_stored"]["passed"] and r2["checks"]["transcripts_stored"]["passed"]
    assert json_counts(r2)["statements"] == 2 and json_counts(r2)["transcripts"] == 2


@pytest.mark.db
def test_the_first_sdf_is_measured_against_the_reverse_repo_and_a_between_meetings_move_is_noted(s3, roster):
    mar, apr = date(2022, 3, 15), date(2022, 4, 20)        # fictional meetings either side of the SDF's start
    slugs = [meeting(d) for d in (mar, apr)]
    mar_body = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 4.0 per cent.",
                   "The reverse repo rate under the LAF remains unchanged at 3.35 per cent and the marginal standing "
                   "facility (MSF) rate and the Bank Rate at 4.25 per cent.",
                   "The MPC also decided to continue with the accommodative stance.",
                   [f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged."])
    apr_body = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 4.0 per cent.",
                   "The marginal standing facility (MSF) rate and the Bank Rate remain unchanged at 4.25 per cent. The "
                   "standing deposit facility (SDF) rate, which will now be the floor of the LAF corridor, will be at 3.75 "
                   "per cent.",
                   "The MPC also decided to remain accommodative while focusing on withdrawal of accommodation.",
                   [(f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate at 4.0 per "
                    "cent and to remain accommodative while focusing on withdrawal of accommodation.")])
    years = {"2022-2023": "2023", "2021-2022": "2022"}
    sites = Sites({rbi_calendar.ANNUAL_POLICY: annual((apr, 82201), years=years),
                   PR + "82101": resolution(mar, mar_body), PR + "82201": resolution(apr, apr_body)},
                  {"2022": annual((mar, 82101), years=years)})
    code, r = go(sites, task(roster, slugs, date(2022, 4, 1)), "backfill", datetime(2022, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    sdf = next(x for x in rates(slugs[1]) if x[0] == "sdf")
    assert sdf[:3] == ("sdf", "3.750", 40) and sdf[3].startswith("first SDF: change measured against the reverse repo")
    assert [x[0] for x in rates(slugs[1])] == ["bank_rate", "msf", "repo", "sdf"]          # no reverse repo row
    assert {v[1] for v in votes(slugs[1]).values()} == {"accommodative, focused on withdrawal of accommodation"}


@pytest.mark.db
def test_a_floor_moved_between_meetings_changes_from_the_rate_in_force_with_a_note(s3, roster):
    a, b = date(2045, 3, 27), date(2045, 5, 22)
    slugs = [meeting(a, kind="unscheduled"), meeting(b, kind="unscheduled")]
    a_body = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 4.40 per cent.",
                 "Consequently, the standing deposit facility (SDF) rate remains at 4.15 per cent and the marginal "
                 "standing facility (MSF) rate and the Bank Rate at 4.65 per cent.",
                 "The MPC also decided to continue with the accommodative stance.",
                 [(f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged "
                  "and to continue with the accommodative stance.")])
    b_body = old("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 40 bps to 4.0 per cent "
                 "from 4.40 per cent with immediate effect;",
                 "accordingly, the marginal standing facility (MSF) rate and the Bank Rate stand reduced to 4.25 per "
                 "cent from 4.65 per cent; and the standing deposit facility (SDF) rate stands reduced to 3.75 per cent "
                 "from 3.95 per cent.",
                 "The MPC also decided to continue with the accommodative stance.",
                 [(f"All members of the MPC – {named(SIX)} – unanimously voted to reduce the policy repo rate by 40 basis "
                  "points and to continue with the accommodative stance.")])
    years = {"2045-2046": "2046", "2044-2045": "2045"}
    sites = Sites({rbi_calendar.ANNUAL_POLICY: annual((b, 83201), years=years),
                   PR + "83101": resolution(a, a_body), PR + "83201": resolution(b, b_body)},
                  {"2045": annual((a, 83101), years=years)})
    code, r = go(sites, task(roster, slugs, date(2045, 4, 1)), "backfill", datetime(2045, 6, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    sdf = next(x for x in rates(slugs[1]) if x[0] == "sdf")
    # 3.95 -> 3.75 as the resolution states it, not 4.15 -> 3.75 against the previous meeting (issue #15, 23 Oct 2026)
    assert sdf[:3] == ("sdf", "3.750", -20) and "the previous MPC meeting set 4.15 per cent (-40 bps from it)" in sdf[3]
    bm = r["checks"]["between_meetings"]
    assert not bm["passed"] and not bm["blocking"] and slugs[1] in bm["detail"]
    assert r["checks"]["cpi_projection"]["passed"]                  # off-cycle meetings publish no projections


@pytest.mark.db
def test_a_failed_meeting_rolls_back_alone_with_an_alert_that_resolves_once_fixed(s3, roster):
    a, b, c = date(2045, 8, 6), date(2045, 10, 8), date(2045, 12, 5)
    slugs = [meeting(d) for d in (a, b, c)]
    hold = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.50 per cent.",
               "Consequently, the standing deposit facility (SDF) rate remains unchanged at 5.25 per cent and the "
               "marginal standing facility (MSF) rate and the Bank Rate at 5.75 per cent.",
               "The MPC also decided to continue with the neutral stance.",
               [f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged."])
    wrong = hold.replace("the Bank Rate at 5.75 per cent", "the Bank Rate at 6.00 per cent")       # MSF = repo + 50
    years = {"2045-2046": "2046"}
    pages = {rbi_calendar.ANNUAL_POLICY: annual((a, 84101), (b, 84201), (c, 84301), years=years),
             PR + "84101": resolution(a, hold), PR + "84201": resolution(b, wrong), PR + "84301": resolution(c, hold)}
    sites = Sites(pages)
    code, r = go(sites, task(roster, slugs, date(2045, 9, 1)), "backfill", datetime(2046, 1, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    mf = r["checks"]["meetings_failed"]
    assert not mf["passed"] and f"{slugs[1]}: msf_spread" in mf["detail"]
    assert state(slugs[0])[:4] == ("held", True, True, True) and state(slugs[1])[:3] == ("scheduled", False, False)
    # The next meeting's changes would be measured against the failed resolution's rates: it waits too.
    assert f"{slugs[2]}: previous_rates" in mf["detail"] and state(slugs[2])[:3] == ("scheduled", False, False)
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"rbi_resolution_failed:{slugs[1]}")[0][0] == 1

    pages[PR + "84201"] = resolution(b, hold)
    code, r = go(sites, task(roster, slugs, date(2045, 9, 1)), "backfill", datetime(2046, 1, 2, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success") and r["checks"]["meetings_failed"]["passed"]
    assert state(slugs[1])[:4] == state(slugs[2])[:4] == ("held", True, True, True)
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"rbi_resolution_failed:{slugs[1]}")[0][0] == 0


@pytest.mark.db
def test_votes_need_every_name_on_the_roster_and_a_stated_change_that_agrees(s3, roster):
    a, b, c = date(2046, 2, 6), date(2046, 4, 8), date(2046, 6, 5)
    slugs = [meeting(d) for d in (a, b, c)]
    hold = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.25 per cent.",
               "Consequently, the standing deposit facility (SDF) rate remains unchanged at 5.00 per cent and the "
               "marginal standing facility (MSF) rate and the Bank Rate at 5.50 per cent.",
               "The MPC also decided to continue with the neutral stance.",
               [f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged."])
    stranger = hold.replace("Shri Fay Roy", "Shri Zed Quill")
    says_25 = old("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points to 4.75 "
                  "per cent with immediate effect.",                               # 5.25 -> 4.75 is 50 bps
                  "Consequently, the standing deposit facility (SDF) rate stands adjusted to 4.50 per cent and the "
                  "marginal standing facility (MSF) rate and the Bank Rate to 5.00 per cent.",
                  "The MPC also decided to continue with the neutral stance.",
                  [(f"All members of the MPC – {named(SIX)} – unanimously voted to reduce the policy repo rate by 25 "
                   "basis points.")])
    years = {"2046-2047": "2047", "2045-2046": "2046"}
    sites = Sites({rbi_calendar.ANNUAL_POLICY: annual((b, 85201), (c, 85301), years=years),
                   PR + "85101": resolution(a, hold), PR + "85201": resolution(b, stranger),
                   PR + "85301": resolution(c, says_25)}, {"2046": annual((a, 85101), years=years)})
    code, r = go(sites, task(roster, slugs, date(2046, 3, 1)), "backfill", datetime(2046, 7, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    mf = r["checks"]["meetings_failed"]["detail"]
    assert f"{slugs[1]}: voters_on_roster (not in config/members.yaml: Shri Zed Quill)" in mf
    assert f"{slugs[2]}: repo_change" in mf and "by -25 bps, computed -50" in mf
    assert state(slugs[0])[:2] == ("held", True) and state(slugs[2])[:3] == ("scheduled", False, False)


@pytest.mark.db
def test_d4s_roster_inference_gives_a_nameless_unanimous_vote_to_the_six_only_when_switched_on(s3, roster):
    a, b, c = date(2047, 6, 4), date(2047, 9, 30), date(2048, 8, 5)
    slugs = [meeting(d) for d in (a, b, c)]
    body = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.25 per cent.",
               "Consequently, the standing deposit facility (SDF) rate remains unchanged at 5.00 per cent and the "
               "marginal standing facility (MSF) rate and the Bank Rate at 5.50 per cent.",
               "The MPC also decided to continue with the neutral stance.",
               ["Accordingly, the MPC unanimously voted to keep the policy repo rate unchanged."])
    years = {"2048-2049": "2049", "2047-2048": "2048"}
    sites = Sites({rbi_calendar.ANNUAL_POLICY: annual((c, 86301), years=years), PR + "86101": resolution(a, body),
                   PR + "86201": resolution(b, body), PR + "86301": resolution(c, body)},
                  {"2048": annual((a, 86101), (b, 86201), years=years)})
    code, r = go(sites, task(roster, slugs, date(2047, 7, 1)), "backfill", datetime(2048, 9, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success") and votes(slugs[1]) == votes(slugs[2]) == {}
    assert not r["checks"]["votes_wait"]["passed"]                       # off: the votes wait for the minutes
    code, r = go(sites, task(roster, slugs, date(2047, 7, 1), d4=True), "backfill", datetime(2048, 9, 1, tzinfo=UTC))
    v = votes(slugs[1])
    # The rate vote only: a stance the MPC "decided" goes to members by elimination only when the resolution names them.
    assert len(v) == 6 and set(v) == {slug_of(n) for n in SIX} and {x[:3] for x in v.values()} == {(0, None, False)}
    # From 2048 the roster lists seven (the newcomer's term has begun): the vote can't be assigned, so it waits.
    assert votes(slugs[2]) == {} and not r["checks"]["roster_six"]["passed"] and slugs[2] in r["checks"]["roster_six"]["detail"]


# ---------------------------------------------------------------------------------------------- watch and daily
class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


@pytest.mark.db
def test_watch_waits_then_takes_the_rss_link_before_the_annual_policy_page_lists_it(s3, roster):
    before, day = date(2045, 2, 7), date(2045, 4, 9)
    slugs = [meeting(before), meeting(day)]
    years = {"2045-2046": "2046", "2044-2045": "2045"}
    hold = old("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.25 per cent.",
               "Consequently, the standing deposit facility (SDF) rate remains unchanged at 6.00 per cent and the "
               "marginal standing facility (MSF) rate and the Bank Rate at 6.50 per cent.",
               "The MPC also decided to continue with the neutral stance.",
               [f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged."])
    fy45 = annual((before, 87101), years=years)
    pages = {rbi_calendar.ANNUAL_POLICY: annual(years=years), PR + "87101": resolution(before, hold),
             rbi_resolution.RSS: rss(("Auction of State Government Securities", PR + "87100",
                                      "Wed, 09 Apr 2045 09:30:00"))}
    sites = Sites(pages, {"2045": fy45})
    code, _ = go(sites, task(roster, slugs, date(2045, 3, 1)), "backfill", datetime(2045, 3, 1, tzinfo=UTC))
    assert code == 0 and state(slugs[0])[:2] == ("held", True)

    # Decision day, 10:01 India time: the first poll finds nothing; then the feed lists the resolution, the page not.
    cut = mpc_new(no=54, days="April 7 to 9, 2045", chair="Shri Fay Roy", members=SIX[:5],
                  decision="voted unanimously to reduce the policy repo rate under the liquidity adjustment facility "
                           "(LAF) by 25 basis points to 6.00 per cent with immediate effect. Consequently, the standing "
                           "deposit facility (SDF) rate shall stand adjusted to 5.75 per cent and the marginal standing "
                           "facility (MSF) rate and the Bank Rate to 6.25 per cent.",
                  stance="change the stance from neutral to accommodative.",
                  projections=[("Considering all these factors, CPI inflation for 2045-46 is projected at 4.0 per cent, "
                               "with Q1 at 3.6 per cent; Q2 at 3.9 per cent; Q3 at 3.8 per cent; and Q4 at 4.4 per cent.")])
    clock = Clock(datetime(2045, 4, 9, 10, 1, tzinfo=IST))

    def publish():
        pages[PR + "87201"] = resolution(day, cut)
        pages[rbi_resolution.RSS] = rss((("Monetary Policy Statement, 2045-46 Resolution of the Monetary Policy "
                                         "Committee (MPC) April 7 to 9, 2045"), PR.replace("Scripts", "scripts") + "87201",
                                         "Wed, 09 Apr 2045 10:00:00"))
    t = task(roster, slugs, date(2045, 3, 1))
    http = HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    with JobRun(JOB, "watch", http=http, storage=storage, clock=clock) as job:
        def sleep(seconds):
            publish()
            clock.sleep(seconds)
        assert watch(job, [t], sleep=sleep) == 2
        assert job.finish() == 0
    runs = q("select status, rows_written from ops.task_run where task_key = %s and job_run_id = %s order by id",
             KEY, job.id)
    assert runs[0] == ("success", 0) and runs[1][0] == "success" and runs[1][1] > 0
    assert state(slugs[1]) == ("held", True, True, True, slugs[0])
    assert [(x[0], x[2]) for x in rates(slugs[1])] == [("bank_rate", -25), ("msf", -25), ("repo", -25), ("sdf", -25)]
    v = votes(slugs[1])
    assert len(v) == 6 and {x[:3] for x in v.values()} == {(-25, "accommodative", False)}
    assert len(forecasts(slugs[1])) == 5
    r = last_run()
    al = r["checks"]["annual_policy_link"]
    assert not al["passed"] and "taken from the RSS item" in al["detail"]

    # The next daily run reads the page, which now links the same resolution: the warning resolves.
    pages[rbi_calendar.ANNUAL_POLICY] = annual((day, 87201), years=years)
    code, r = go(sites, task(roster, slugs, date(2045, 3, 1)), "daily", datetime(2045, 4, 10, 1, tzinfo=UTC))
    assert (code, r["status"], r["rows"]) == (0, "success", 0) and r["checks"]["annual_policy_link"]["passed"]


# ---------------------------------------------------------------------------------------------- the minutes (part 2)
def hold_body(rate: str, sdf: str, msf: str, votes: list[str], stance: str = "continue with the accommodative stance") -> str:
    return old(f"keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at {rate} per cent.",
               f"Consequently, the standing deposit facility (SDF) rate remains unchanged at {sdf} per cent and the "
               f"marginal standing facility (MSF) rate and the Bank Rate at {msf} per cent.",
               f"The MPC also decided to {stance}.", votes)


UNANIMOUS = f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged."


@pytest.mark.db
def test_the_minutes_table_replaces_a_vote_it_contradicts_and_fills_one_the_resolution_left(s3, roster):
    base, aug, oct_ = date(2044, 6, 4), date(2044, 8, 6), date(2044, 10, 8)
    slugs = [meeting(d) for d in (base, aug, oct_)]
    cut = old("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 35 basis points from 6.00 per "
              "cent to 5.65 per cent with immediate effect.",
              "Consequently, the standing deposit facility (SDF) rate stands adjusted to 5.40 per cent and the marginal "
              "standing facility (MSF) rate and the Bank Rate to 5.90 per cent.",
              "The MPC also decided to continue with the accommodative stance.",
              [(f"{named(SIX[:4])} voted to reduce the policy repo rate by 35 basis points, while Shri Eli Paul voted to "
               "reduce the policy repo rate by 25 basis points.")])        # Fay Roy's vote isn't stated
    stance_all = (f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate unchanged "
                  "and to continue with the accommodative stance.")
    hold = hold_body("5.65", "5.40", "5.90", [stance_all])
    aug_minutes = minutes(aug, ["The MPC decided to reduce the policy repo rate by 35 basis points to 5.65 per cent.",
                                "The MPC also decided to continue with the accommodative stance.",
                                (f"{named(SIX[:4])} voted to reduce the policy repo rate by 35 basis points, while Shri "
                                 "Eli Paul voted to reduce the policy repo rate by 25 basis points.")],
                          [(n, "Yes", 35) for n in SIX[:4]] + [(SIX[4], "Yes", 35), (SIX[5], "Yes", 25)],
                          heading="Voting on the Resolution to reduce the policy repo rate", heading_in_table=True)
    # October: the table records Ada Rao against the hold the resolution says was unanimous, and the resolution's copy
    # names Fay Roy as voting against the stance.
    oct_minutes = minutes(oct_, ["The MPC decided to keep the policy repo rate unchanged at 5.65 per cent.",
                                 "The MPC also decided to continue with the accommodative stance.",
                                 (f"All members of the MPC – {named(SIX)} – unanimously voted to keep the policy repo rate "
                                  f"unchanged. {named(SIX[:5])} voted to continue with the accommodative stance, while "
                                  "Shri Fay Roy voted against this part of the resolution.")],
                          [(SIX[0], "No")] + [(n, "Yes") for n in SIX[1:]],
                          heading="Voting on the Resolution to keep the policy repo rate unchanged at 5.65 per cent")
    years = {"2044-2045": "2045", "2043-2044": "2044"}
    pages = {rbi_calendar.ANNUAL_POLICY: annual((base, 88101), (aug, 88201), (oct_, 88301), years=years,
                                                minutes=((aug, 88202), (oct_, 88302))),
             PR + "88101": resolution(base, hold_body("6.00", "5.75", "6.25", [UNANIMOUS])),
             PR + "88201": resolution(aug, cut), PR + "88301": resolution(oct_, hold),
             PR + "88202": aug_minutes, PR + "88302": oct_minutes}
    sites = Sites(pages)
    code, r = go(sites, task(roster, slugs, date(2044, 7, 1)), "backfill", datetime(2044, 11, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert r["checks"]["meetings_failed"]["passed"], r["checks"]["meetings_failed"]["detail"]
    assert r["checks"]["minutes_failed"]["passed"], r["checks"]["minutes_failed"]["detail"]
    assert r["checks"]["minutes_stored"]["passed"], r["checks"]["minutes_stored"]["detail"]

    # August: Eli Paul's 25 bps (the resolution) becomes 35 (the table: the statutory record); Fay Roy, whom the
    # resolution leaves out, gets the table's 25 bps, a dissent on the size of the cut.
    aug_v, aug_d = votes(slugs[1]), vote_docs(slugs[1])
    assert aug_v[slug_of(SIX[4])][:3] == (-35, None, False) and aug_d[slug_of(SIX[4])] == "mpc_minutes"
    assert aug_v[slug_of(SIX[5])] == (-25, None, True, "Shri Fay Roy | Yes | 25") and aug_d[slug_of(SIX[5])] == "mpc_minutes"
    assert aug_v[slug_of(SIX[0])][:3] == (-35, None, False) and aug_d[slug_of(SIX[0])] == "mpc_resolution"  # agreed

    # October: Ada Rao's "No" replaces the resolution's unanimous hold (no change stated, so none stored); Fay Roy's
    # stance vote follows the minutes' copy.
    oct_v, oct_d = votes(slugs[2]), vote_docs(slugs[2])
    assert oct_v[slug_of(SIX[0])] == (None, "accommodative", True, "Dr. Ada Rao | No")
    assert oct_v[slug_of(SIX[5])][1:3] == ("against", True) and oct_d[slug_of(SIX[5])] == "mpc_minutes"
    assert "against this part of the resolution" in oct_v[slug_of(SIX[5])][3]
    assert oct_v[slug_of(SIX[1])] == (0, "accommodative", False, None) and oct_d[slug_of(SIX[1])] == "mpc_resolution"
    assert q("select dissent_count from api.policy_meetings where slug = %s", slugs[2]) in ([(2,)], [])

    mc = r["checks"]["minutes_changed_vote"]
    assert not mc["passed"] and not mc["blocking"]
    assert f"{slugs[1]}: the minutes changed the stored votes of {slug_of(SIX[4])}" in mc["detail"]
    assert slug_of(SIX[5]) not in mc["detail"].split(slugs[2])[0]                     # a fill isn't a change
    assert alerts("votes_changed:mpc-2044") == [f"votes_changed:{slugs[1]}", f"votes_changed:{slugs[2]}"]

    assert json_counts(r)["minutes_differ"] == 3

    # A re-run changes nothing (the resolution never overwrites a row the minutes wrote), and says so; the differences
    # stand, counted.
    code, r2 = go(sites, task(roster, slugs, date(2044, 7, 1)), "backfill", datetime(2044, 11, 1, tzinfo=UTC))
    assert (code, r2["status"], r2["rows"]) == (0, "success", 0) and votes(slugs[2]) == oct_v
    assert r2["checks"]["minutes_changed_vote"]["passed"] and json_counts(r2)["minutes_differ"] == 3

    # The alert stays open for a week from the minutes' load, then resolves.
    q("""update core.document set extracted_at = extracted_at - interval '8 days'
          where doc_type = 'mpc_minutes' and event_id in (select id from core.event where slug = any(%s))""", slugs)
    code, _ = go(sites, task(roster, slugs, date(2044, 7, 1)), "backfill", datetime(2044, 11, 1, tzinfo=UTC))
    assert code == 0 and alerts("votes_changed:mpc-2044") == []


@pytest.mark.db
def test_minutes_that_fail_their_checks_roll_back_alone_with_an_alert_that_resolves_once_fixed(s3, roster):
    base, feb, apr = date(2042, 12, 5), date(2043, 2, 6), date(2043, 4, 8)
    slugs = [meeting(d) for d in (base, feb, apr)]
    hold = hold_body("6.25", "6.00", "6.50", [UNANIMOUS])
    copy = ["The MPC decided to keep the policy repo rate unchanged at 6.25 per cent.", UNANIMOUS]
    head = "Voting on the Resolution to keep the policy repo rate unchanged at 6.25 per cent"
    four_no = minutes(feb, copy, [(n, "No") for n in SIX[:4]] + [(n, "Yes") for n in SIX[4:]], heading=head)
    five_rows = minutes(apr, copy, [(n, "Yes") for n in SIX[:5]], heading=head)
    years = {"2043-2044": "2044", "2042-2043": "2043"}
    pages = {rbi_calendar.ANNUAL_POLICY: annual((apr, 89301), years=years, minutes=((apr, 89302),)),
             PR + "89101": resolution(base, hold), PR + "89201": resolution(feb, hold), PR + "89301": resolution(apr, hold),
             PR + "89202": four_no, PR + "89302": five_rows}
    sites = Sites(pages, {"2043": annual((base, 89101), (feb, 89201), years=years, minutes=((feb, 89202),))})
    code, r = go(sites, task(roster, slugs, date(2043, 1, 1)), "backfill", datetime(2043, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    mf = r["checks"]["minutes_failed"]
    assert not mf["passed"] and f"{slugs[1]}: minutes_decision" in mf["detail"] and "only 2 of 6" in mf["detail"]
    assert f"{slugs[2]}: minutes_votes_table" in mf["detail"]
    assert minutes_docs(slugs[1]) == minutes_docs(slugs[2]) == []                   # neither stored
    assert state(slugs[1])[:4] == ("held", True, True, True)                         # the resolution stands
    assert {k: v[:3] for k, v in votes(slugs[1]).items()} == {slug_of(n): (0, "accommodative", False) for n in SIX}
    assert alerts("rbi_minutes_failed:mpc-2043") == [f"rbi_minutes_failed:{s}" for s in slugs[1:]]
    assert not r["checks"]["roster_matches_minutes"]["passed"]                      # D4's check can't pass either

    pages[PR + "89202"] = minutes(feb, copy, [(n, "Yes") for n in SIX], heading=head)
    pages[PR + "89302"] = minutes(apr, copy, [(n, "Yes") for n in SIX], heading=head)
    code, r = go(sites, task(roster, slugs, date(2043, 1, 1)), "backfill", datetime(2043, 5, 2, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success") and r["checks"]["minutes_failed"]["passed"]
    assert len(minutes_docs(slugs[1])) == len(minutes_docs(slugs[2])) == 1
    assert alerts("rbi_minutes_failed:mpc-2043") == []


@pytest.mark.db
def test_daily_takes_the_minutes_from_the_feed_or_the_next_years_page_and_alerts_on_day_15(s3, roster):
    base, mar = date(2041, 2, 7), date(2041, 3, 27)
    slugs = [meeting(base), meeting(mar, kind="unscheduled")]
    hold = hold_body("6.25", "6.00", "6.50", [UNANIMOUS])
    years = {"2041-2042": "2042", "2040-2041": "2041"}
    fy41 = annual((base, 90101), (mar, 90201), years=years)
    pages = {rbi_calendar.ANNUAL_POLICY: annual(years=years), PR + "90101": resolution(base, hold),
             PR + "90201": resolution(mar, hold),
             rbi_resolution.RSS: rss(("Auction of State Government Securities", PR + "90200", "Fri, 04 Apr 2041 09:30:00"))}
    sites = Sites(pages, {"2041": fy41})
    def t():
        return task(roster, slugs, date(2041, 3, 1))
    code, r = go(sites, t(), "backfill", datetime(2041, 3, 28, tzinfo=UTC))
    assert code == 0 and state(slugs[1])[:2] == ("held", True)

    # Day 14 (10 Apr): due, not out yet: no alert. Day 15: the alert.
    code, r = go(sites, t(), "daily", datetime(2041, 4, 10, 12, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success") and alerts(f"rbi_minutes_missing:{slugs[1]}") == []
    assert rbi_resolution.RSS in sites.seen                                         # looked in the feed too
    code, r = go(sites, t(), "daily", datetime(2041, 4, 11, 12, tzinfo=UTC))
    assert alerts(f"rbi_minutes_missing:{slugs[1]}") == [f"rbi_minutes_missing:{slugs[1]}"]

    # 13 Apr: the feed lists them before the page does (the next fiscal year's page: they came out after 31 March).
    pages[PR + "90202"] = minutes(mar, ["The MPC decided to keep the policy repo rate unchanged at 6.25 per cent.",
                                        UNANIMOUS], [(n, "Yes") for n in SIX],
                                  heading="Voting on the Resolution to keep the policy repo rate unchanged at 6.25 per cent")
    pages[rbi_resolution.RSS] = rss((f"Minutes of the Monetary Policy Committee Meeting, {days_of(mar)}",
                                     PR.replace("Scripts", "scripts") + "90202", "Thu, 10 Apr 2041 17:00:00"))
    code, r = go(sites, t(), "daily", datetime(2041, 4, 13, 12, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert len(minutes_docs(slugs[1])) == 1 and alerts(f"rbi_minutes_missing:{slugs[1]}") == []
    assert r["checks"]["minutes_stored"]["passed"] and r["checks"]["annual_policy_link"]["passed"]
    # The next daily run, once the page links the same minutes, doesn't look again.
    pages[rbi_calendar.ANNUAL_POLICY] = annual(years=years, minutes=((mar, 90202),))
    sites.seen.clear()
    code, r = go(sites, t(), "daily", datetime(2041, 4, 14, 12, tzinfo=UTC))
    assert (code, r["status"], r["rows"]) == (0, "success", 0) and PR + "90202" not in sites.seen
    assert r["checks"]["annual_policy_link"]["passed"]


@pytest.mark.db
def test_d4s_inference_switches_on_once_a_backfill_finds_the_roster_in_every_minutes(s3, roster):
    base, feb, apr, jun = date(2040, 12, 4), date(2041, 2, 5), date(2041, 4, 4), date(2041, 6, 6)
    nameless = hold_body("6.25", "6.00", "6.50", ["Accordingly, the MPC unanimously voted to keep the policy repo rate unchanged."])
    copy = ["The MPC decided to keep the policy repo rate unchanged at 6.25 per cent.",
            "Accordingly, the MPC unanimously voted to keep the policy repo rate unchanged."]
    head = "Voting on the Resolution to keep the policy repo rate unchanged at 6.25 per cent"
    slugs = [meeting(d) for d in (base, feb, apr)]
    years = {"2041-2042": "2042", "2040-2041": "2041"}
    pages = {rbi_calendar.ANNUAL_POLICY: annual((apr, 91301), years=years, minutes=((apr, 91302),)),
             PR + "91101": resolution(base, nameless), PR + "91201": resolution(feb, nameless),
             PR + "91301": resolution(apr, nameless),
             PR + "91202": minutes(feb, copy, [(n, "Yes") for n in SIX], heading=head),
             PR + "91302": minutes(apr, copy, [(n, "Yes") for n in SIX], heading=head)}
    sites = Sites(pages, {"2041": annual((base, 91101), (feb, 91201), years=years, minutes=((feb, 91202),))})
    # A roster that's wrong from March: it has Fay Roy leave and the newcomer join, which the April minutes contradict.
    wrong = roster.parent / "members_wrong.yaml"
    lines = roster.read_text().splitlines()
    fay = next(i for i, ln in enumerate(lines) if ln.strip() == f"{slug_of(SIX[5])}:")
    lines[fay + 3] = lines[fay + 3].replace("from: 2011-01-01,", "from: 2011-01-01, to: 2041-03-01,")
    lines[-1] = lines[-1].replace("from: 2048-01-01", "from: 2041-03-02")
    wrong.write_text("\n".join(lines) + "\n")

    def t(path=roster):
        return task(path, slugs, date(2041, 1, 1), d4=None)                     # the minutes' check decides
    code, r = go(sites, t(wrong), "backfill", datetime(2041, 5, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    gate = r["checks"]["roster_matches_minutes"]
    assert not gate["passed"] and not gate["blocking"] and slugs[2] in gate["detail"] and slugs[1] not in gate["detail"]
    assert "gia-menon" in gate["detail"] and not r["checks"]["minutes_roster"]["passed"]
    # Off: the nameless baseline's votes wait; the minutes give the others theirs.
    assert votes(slugs[0]) == {} and len(votes(slugs[1])) == len(votes(slugs[2])) == 6
    assert set(vote_docs(slugs[1]).values()) == {"mpc_minutes"}

    # The roster fixed: a backfill holding every minutes finds it in all of them and switches the inference on, in that
    # run: the baseline's unanimous vote goes to the roster's six.
    code, r = go(sites, t(), "backfill", datetime(2041, 5, 2, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success") and r["checks"]["roster_matches_minutes"]["passed"]
    assert len(votes(slugs[0])) == 6 and set(vote_docs(slugs[0]).values()) == {"mpc_resolution"}
    assert json_counts(r)["d4"] is True

    # A daily run uses the backfill's result: a new nameless resolution gets its six on decision day.
    slugs.append(meeting(jun))
    pages[rbi_calendar.ANNUAL_POLICY] = annual((apr, 91301), (jun, 91401), years=years, minutes=((apr, 91302),))
    pages[PR + "91401"] = resolution(jun, nameless)
    pages[rbi_resolution.RSS] = rss()
    code, r = go(sites, t(), "daily", datetime(2041, 6, 6, 12, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success") and "roster_matches_minutes" not in r["checks"]
    assert len(votes(slugs[3])) == 6 and json_counts(r)["d4"] is True


def json_counts(r) -> dict:
    return __import__("json").loads(r["checks"]["resolutions_stored"]["detail"])


class Down(Sites):
    """rbi.org.in answering 503 to everything (after the client's retries)."""
    down = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            self.seen.append(str(request.url))
            return httpx.Response(503)
        return super().__call__(request)


@pytest.mark.db
def test_the_old_site_unreachable_for_3_days_raises_an_alert_that_the_next_good_run_resolves(s3, roster):
    site_key = "test.b6_rbi_site"
    q("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
         values (%s, 'test', 'B6 site test', '{daily}', '20 hours', '3 days', false) on conflict (key) do nothing""",
      site_key)
    d = date(2041, 8, 6)
    slugs = [meeting(d)]
    years = {"2041-2042": "2042"}
    sites = Down({rbi_calendar.ANNUAL_POLICY: annual((d, 92101), years=years), rbi_resolution.RSS: rss(),
                  PR + "92101": resolution(d, hold_body("6.25", "6.00", "6.50", [UNANIMOUS]))})

    def t():
        x = task(roster, slugs, date(2041, 9, 1))                              # the meeting is the baseline
        x.key = site_key
        return x

    def last(key=site_key):
        return q("select status, coalesce(error, '') from ops.task_run where task_key = %s order by id desc limit 1",
                 key)[0]
    def http():
        return HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    code = run(JOB, "daily", [t()], only=site_key, force=True, http=http(), storage=storage,
               clock=lambda: datetime(2041, 8, 7, tzinfo=UTC))
    status, error = last()
    assert (code, status) == (1, "failed") and "SiteUnreachable" in error and "HTTP 503" in error
    assert alerts("rbi_site_unreachable") == []                                     # a day isn't three
    # Three days of failures (the first one backdated), then another: the alert opens.
    q("update ops.task_run set started_at = started_at - interval '4 days' where task_key = %s", site_key)
    code = run(JOB, "daily", [t()], only=site_key, force=True, http=http(), storage=storage,
               clock=lambda: datetime(2041, 8, 10, tzinfo=UTC))
    assert code == 1 and alerts("rbi_site_unreachable") == ["rbi_site_unreachable"]
    assert q("select severity from ops.alert where dedupe_key = 'rbi_site_unreachable' and resolved_at is null") == [
        ("error",)]
    # Back up: the next good run resolves it.
    sites.down = False
    code = run(JOB, "daily", [t()], only=site_key, force=True, http=http(), storage=storage,
               clock=lambda: datetime(2041, 8, 11, tzinfo=UTC))
    assert (code, last()[0]) == (0, "success") and alerts("rbi_site_unreachable") == []
    # A parser bug isn't the site being down.
    assert RbiDocuments().failure_alerts(None, "failed", "ResolutionError: no tableheader cells") == []


# ---------------------------------------------------------------------------------------------- part 3, daily
@pytest.mark.db
def test_daily_takes_the_statement_from_the_feed_and_the_transcript_days_later_and_a_bad_one_waits_with_an_alert(
        s3, roster):
    """Part 3 (issue #15): on the day after a decision the Annual Policy page lists no statement yet, so the feed's is
    taken (W until the page links it); the transcript comes days later on its own group; one the Governor doesn't
    speak in fails its checks (a daily run rolls back; a backfill lists it with an alert), and the fixed page loads and
    resolves the alert."""
    base, d = date(2015, 2, 3), date(2015, 4, 7)            # fictional, in the reverse repo's era (before the SDF)
    slugs = [meeting(x) for x in (base, d)]
    years = {"2015-2016": "2016", "2014-2015": "2015"}
    fy15 = annual((base, 93001), years=years)
    stmt = PR + "93105"
    resolutions = {PR + "93001": resolution(base, BASE_BODY), PR + "93101": resolution(d, FEB_BODY)}
    feed = rss((f"Governor’s Statement: {d:%B} {d.day}, {d.year}", stmt.replace("https://www.", "https://"),
                f"{d:%a, %d %b %Y} 10:05:00"))
    pages = {rbi_calendar.ANNUAL_POLICY: annual((d, 93101), years=years, statements={d: None}), **resolutions,
             rbi_resolution.RSS: feed, stmt: gov_statement(d, number="2015-2016/12")}
    sites = Sites(pages, {"2015": fy15})

    def t():
        return task(roster, slugs, date(2015, 3, 1))
    code, r = go(sites, t(), "daily", datetime(2015, 4, 8, 1, 7, tzinfo=UTC))          # 06:37 IST the next day
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert [x[:2] for x in part3_docs(slugs[1])] == [
        ("rbi_governor_statement", "Governor's statement, 7 Apr 2015 · Press Release: 2015-2016/12")]
    link = r["checks"]["annual_policy_link"]
    assert not link["passed"] and "statement taken from the RSS item" in link["detail"]
    assert r["checks"]["statement_link"]["passed"] and r["checks"]["transcripts_stored"]["passed"]

    # Four days on the page lists the statement and the transcript, which the Governor (Ada Rao) doesn't speak in.
    later = d + timedelta(days=4)
    bad = speech_transcript(d, later, turns=["Dr. Ben Iyer:", "Latha Venkatesh:"] * 10)
    tr = SPEECH + "93100"
    pages[rbi_calendar.ANNUAL_POLICY] = annual((d, 93101), years=years, statements={d: stmt},
                                               transcripts=((d, tr, 4, True),))
    pages[tr] = bad
    code, r = go(sites, t(), "daily", datetime(2015, 4, 12, 1, 7, tzinfo=UTC))
    assert code != 0 and r["status"] == "checks_failed"
    assert not r["checks"]["transcript_governor"]["passed"], r["checks"]
    assert "not among them" in r["checks"]["transcript_governor"]["detail"]
    assert [x[0] for x in part3_docs(slugs[1])] == ["rbi_governor_statement"]        # the statement stays

    # A backfill lists it and alerts; the fixed page loads, and the next good run resolves the alert.
    code, r = go(sites, t(), "backfill", datetime(2015, 4, 12, 2, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert not r["checks"]["transcripts_failed"]["passed"] and slugs[1] in r["checks"]["transcripts_failed"]["detail"]
    assert alerts("rbi_transcript_failed:") == [f"rbi_transcript_failed:{slugs[1]}"]
    assert r["checks"]["annual_policy_link"]["passed"]                                 # the page links the stored one
    pages[tr] = speech_transcript(d, later)
    code, r = go(sites, t(), "daily", datetime(2015, 4, 12, 3, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert [x[:4] for x in part3_docs(slugs[1])] == [
        ("rbi_governor_statement", "Governor's statement, 7 Apr 2015 · Press Release: 2015-2016/12",
         datetime(2015, 4, 7, 10, tzinfo=IST), stmt),
        ("rbi_presconf_transcript", "RBI press conference transcript, 7 Apr 2015", datetime(2015, 4, 11, tzinfo=IST), tr)]
    assert alerts("rbi_transcript_failed:") == []
    # Both stored: the daily run no longer looks for them.
    sites.seen.clear()
    code, r = go(sites, t(), "daily", datetime(2015, 4, 13, 3, tzinfo=UTC))
    assert (code, r["rows"]) == (0, 0) and stmt not in sites.seen and tr not in sites.seen


# ---------------------------------------------------------------------------------------------- the saved pages
FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "rbi"


@pytest.mark.db
@pytest.mark.skipif(not (FIXTURES / "INDEX.tsv").exists(), reason="the saved pages aren't checked out (data/fixtures)")
def test_a_backfill_over_the_saved_pages_loads_every_resolution_and_minutes_from_the_2018_baseline(s3):
    """The B6 done-when counts over the 49 saved resolutions and 49 minutes (Dec 2018 to Oct 2026) and the real roster:
    the meetings come from the saved Annual Policy pages through the calendar's own rules, the documents from their
    saved pages."""
    import csv

    from ft.policy import meetings
    from ft.policy import members as members_mod
    pages = {}
    ap = sorted((FIXTURES / "annualpolicy").glob("annualpolicy_*.html"))
    parsed = [rbi_calendar.parse_page(p.read_bytes()) for p in ap]
    current = (FIXTURES / "annualpolicy" / "annualpolicy_current.html").read_text(encoding="utf-8", errors="replace")
    pages[rbi_calendar.ANNUAL_POLICY] = current
    years = {}
    for p in ap:
        if p.stem != "annualpolicy_current":
            label = p.stem.removeprefix("annualpolicy_").replace("_", "-")
            years[label.split("-")[1]] = p.read_text(encoding="utf-8", errors="replace")
    for r in csv.DictReader((FIXTURES / "INDEX.tsv").open(encoding="utf-8"), delimiter="\t"):
        if r["kind"] in ("resolution", "minutes"):
            pages[rbi_resolution.page_url(r["url"])] = (FIXTURES / r["file"]).read_text(encoding="utf-8", errors="replace")
        elif r["kind"] in ("governor_statement", "transcript"):                     # part 3 (and the pages' others)
            pages[rbi_statements.doc_url(r["url"])] = (FIXTURES / r["file"]).read_text(encoding="utf-8",
                                                                                      errors="replace")
    cal = rbi_calendar.calendar(parsed)
    curated = meetings.load_curated()
    ms = meetings.number_rbi(meetings.rbi_meetings(cal, curated, date(2026, 10, 23), full=True), {}, curated)
    real = [m for m in ms if m.is_rate_decision and m.decision_date <= date(2026, 10, 7)]
    slugs = [m.slug for m in real]
    assert len(slugs) == 49 and slugs[0] == "mpc-2018-12-05"
    # These 49 meetings stay in the database, and the calendar numbers MPC meetings across every one stored, so
    # test_calendar_task's backfill (synthetic pages under some of these slugs) has to run first, as it does in file order.
    # test_calendar_task's backfill leaves events under some of these real slugs, built from its synthetic pages (some
    # without a meeting row, some holding another meeting's number), so set each one to the real calendar's values.
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("update policy.meeting set meeting_no = null where meeting_no = any(%s)", ([m.meeting_no for m in real],))
        for m in real:
            c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at,
                                                 status, source_id, source_url, is_published)
                         select 'policy_meeting', b.id, %s, 'MPC meeting', %s, %s, %s, 'scheduled', src.id, %s, false
                           from core.entity b, core.source src where b.slug = 'rbi' and src.key = 'rbi'
                         on conflict (event_type, slug) do update
                            set starts_on = excluded.starts_on, ends_on = excluded.ends_on, occurs_at = excluded.occurs_at""",
                      (m.slug, m.starts_on, m.ends_on, m.occurs_at, m.source_url))
            c.execute("""insert into policy.meeting (event_id, meeting_no, kind, is_rate_decision, minutes_due_on)
                         select id, %s, %s, true, %s from core.event where event_type = 'policy_meeting' and slug = %s
                         on conflict (event_id) do update set meeting_no = excluded.meeting_no, kind = excluded.kind,
                                is_rate_decision = true, minutes_due_on = excluded.minutes_due_on""",
                      (m.meeting_no, m.kind, m.minutes_due_on, m.slug))
    # Earlier tests' synthetic fiscal-year pages are stored under the same record URLs; a closed year's stored copy is
    # read back instead of posting the form, so forget them here.
    q("delete from ops.http_cache where url like %s", rbi_calendar.ANNUAL_POLICY + "#hdnYear=%")
    sites = Sites(pages, years)
    t = task(members_mod.CONFIG, slugs, meetings.FIRST_DAY, d4=None)       # the minutes' check decides D4
    code, r = go(sites, t, "backfill", datetime(2026, 10, 23, 12, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert r["checks"]["meetings_failed"]["passed"], r["checks"]["meetings_failed"]["detail"]
    assert r["checks"]["minutes_failed"]["passed"], r["checks"]["minutes_failed"]["detail"]
    assert r["checks"]["minutes_stored"]["passed"], r["checks"]["minutes_stored"]["detail"]
    # D4 (Spec 01 §5.1): the roster matches every 2019→ minutes, so the inference is on, and the Dec 2018 baseline's
    # unanimous rate vote goes to the six on the roster that day (its minutes aren't loaded: the decision document only).
    gate = r["checks"]["roster_matches_minutes"]
    assert gate["passed"] and "all 48 minutes since 2019-02-07" in gate["detail"], gate["detail"]
    assert r["checks"]["minutes_roster"]["passed"] and r["checks"]["minutes_resolution_copy"]["passed"]
    counts = json_counts(r)
    # Resolution-vs-minutes differences: zero (Spec 01 §5.1), in the table and in the resolutions' copies.
    assert counts | {"roster": 21} == {"targets": 49, "loaded": 49, "waiting": 0, "failed": 0, "rates": 196,
                                       "votes": 294, "rate_votes": 294, "dissents": 44, "projections": 419,
                                       "from_rss": 0, "roster": 21, "minutes": 48, "minutes_failed": 0,
                                       "minutes_votes": 0, "minutes_differ": 0, "d4": True,
                                       "statements": 48, "statements_failed": 0, "transcripts": 44,
                                       "transcripts_failed": 0}, counts
    assert r["checks"]["minutes_changed_vote"]["passed"]
    # Part 3: every decision since 2019 has its Governor's statement, every press conference from Dec 2018 its transcript
    # (none for the four 2020 COVID meetings or 4 May 2022); the pages' other statements are left out; nothing 404s.
    for name in ("statements_stored", "transcripts_stored", "statements_failed", "transcripts_failed", "statement_link",
                 "annual_policy_docs", "documents_missing", "annual_policy_link"):
        assert r["checks"][name]["passed"], (name, r["checks"][name]["detail"])
    part3 = q("""select d.doc_type, count(*), count(*) filter (where d.title like '%% · Press Release: %%'),
                        min(e.slug), count(distinct e.id)
                   from core.document d join core.event e on e.id = d.event_id
                  where e.slug = any(%s) and d.doc_type in ('rbi_governor_statement', 'rbi_presconf_transcript')
                    and d.is_published group by 1 order by 1""", slugs)
    assert part3 == [("rbi_governor_statement", 48, 38, "mpc-2019-02-07", 48),
                     ("rbi_presconf_transcript", 44, 0, "mpc-2018-12-05", 44)], part3
    assert sorted(s for s in slugs if not part3_docs(s) or part3_docs(s)[-1][0] != "rbi_presconf_transcript") == [
        "mpc-2020-03-27", "mpc-2020-05-22", "mpc-2020-08-06", "mpc-2020-10-09", "mpc-2022-05-04"]
    oct26 = part3_docs("mpc-2026-10-07")
    assert [x[:4] for x in oct26] == [
        ("rbi_governor_statement", "Governor's statement, 7 Oct 2026 · Press Release: 2026-2027/1266",
         datetime(2026, 10, 7, 10, tzinfo=IST), PR + "63744"),
        ("rbi_presconf_transcript", "RBI press conference transcript, 7 Oct 2026", datetime(2026, 10, 10, tzinfo=IST),
         "https://www.rbi.org.in/Scripts/BS_SpeechesView.aspx?Id=1583")]
    assert "Sanjay Malhotra" in oct26[1][5]
    assert "Dr. Urjit R. Patel" in part3_docs("mpc-2018-12-05")[0][5]                # the baseline's press conference
    assert sorted(votes("mpc-2018-12-05")) == ["chetan-ghate", "michael-debabrata-patra", "pami-dua",
                                               "ravindra-h-dholakia", "urjit-r-patel", "viral-v-acharya"]
    oct26 = minutes_docs("mpc-2026-10-07")
    assert oct26[0][0] == "MPC minutes, 7 Oct 2026 · Press Release: 2026-2027/1361" and len(oct26[0][3]) == 6
    assert q("""select d.title from core.document d join core.event e on e.id = d.event_id
                 where e.slug = 'mpc-2026-10-07' and d.doc_type = 'mpc_resolution'""") == [
        ("MPC resolution, 7 Oct 2026 · Press Release: 2026-2027/1264",)]
    assert q("""select count(*), count(*) filter (where d.title like '%% · Press Release: %%')
                  from core.document d join core.event e on e.id = d.event_id
                 where e.slug = any(%s) and d.doc_type in ('mpc_resolution', 'mpc_minutes')""", slugs) == [(97, 97)]
    # The spot checks (Prompt 01 B6, done when), from the stored rows.
    def chg(s, i="repo"):
        return next(x[2] for x in rates(s) if x[0] == i)
    assert [chg(s) for s in ("mpc-2019-08-07", "mpc-2020-03-27", "mpc-2020-05-22", "mpc-2022-05-04")] == [-35, -75, -40, 40]
    assert [x[1:3] for x in rates("mpc-2022-04-08") if x[0] == "sdf"] == [("3.750", 40)]
    assert [x[1:3] for x in rates("mpc-2026-10-07") if x[0] == "repo"] == [("5.500", 25)]
    aug24 = votes("mpc-2024-08-08")
    assert sorted(k for k, v in aug24.items() if v[0] == -25) == ["ashima-goyal", "jayanth-r-varma"]
    assert sum(v[0] == 0 for v in aug24.values()) == 4
    oct26 = votes("mpc-2026-10-07")
    assert sorted(k for k, v in oct26.items() if v[2]) == ["nagesh-kumar", "ram-singh"]
    assert q("select dissent_count from api.policy_meetings where slug = 'mpc-2026-10-07'") in ([(2,)], [])
    may20 = next(x for x in rates("mpc-2020-05-22") if x[0] == "reverse_repo")
    assert may20[2] == -40 and "the previous MPC meeting set 4 per cent (-65 bps from it)" in may20[3]   # 17 Apr 2020
