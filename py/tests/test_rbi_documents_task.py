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
from policy_docs import mpc_new, mpc_old, mpc_title
from policy_docs import rbi_page as press_release
from test_policy_calendar import rbi_group, rbi_item
from test_policy_calendar import rbi_page as annual_page

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import JobRun, run
from ft.jobs.tasks.rbi_documents import RbiDocuments
from ft.jobs.watch import watch
from ft.policy import rbi_calendar, rbi_resolution

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
    return press_release(body, title=mpc_title(f"{d:%B} {d.day - 2} to {d.day}, {d.year}", f"{d.year}-{d.year % 100 + 1:02d}"),
                         day=f"{d:%b %d, %Y}")


def annual(*items: tuple[date, int], years: dict[str, str]) -> str:
    """An Annual Policy page whose groups are these decisions (date, prid), newest first, above a report's group."""
    groups = [rbi_group(f"{d:%b %d, %Y}", "Bi-monthly Monetary Policy Statement",
                        rbi_item("Governor’s Statement", PR + str(prid + 1)) + rbi_item(RESOLUTION, PR + str(prid), True))
              for d, prid in sorted(items, reverse=True)]
    groups.append(rbi_group("Jan 02, 2000", "Monetary Policy Report",
                            rbi_item("Monetary Policy Report", "https://rbi.org.in/Scripts/PublicationsView.aspx?id=1")))
    return annual_page(*groups, years=years)


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


@pytest.mark.db
def test_backfill_stores_resolutions_rates_votes_projections_and_diffs_and_reruns_clean(s3, roster):
    slugs = [meeting(d) for d in (BASE, FEB, APR)]
    years = {"2012-2013": "2013", "2011-2012": "2012"}
    fy12 = annual((BASE, 81001), (FEB, 81101), years=years)
    pages = {rbi_calendar.ANNUAL_POLICY: annual((APR, 81201), years=years),
             PR + "81001": resolution(BASE, BASE_BODY), PR + "81101": resolution(FEB, FEB_BODY),
             PR + "81201": resolution(APR, APR_BODY)}
    sites = Sites(pages, {"2012": fy12})
    t = task(roster, slugs, date(2012, 1, 1))
    code, r = go(sites, t, "backfill", datetime(2012, 7, 1, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert r["checks"]["resolutions_stored"]["passed"], r["checks"]["resolutions_stored"]["detail"]
    assert r["checks"]["meetings_failed"]["passed"]
    assert f"POST {rbi_calendar.ANNUAL_POLICY} hdnYear=2012" in sites.seen     # the earlier fiscal year, by form post

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
                 from core.document d join core.event e on e.id = d.event_id where e.slug = %s""", slugs[2])[0]
    assert doc == ("MPC resolution, 4 Apr 2012", datetime(2012, 4, 4, 4, 30, tzinfo=UTC), PR + "81201",
                   "html-1/rbi_press_release", "/Scripts/BS_PressReleaseDisplay.aspx?prid=81201")

    # A re-run reads the stored resolutions back: no request, no row, no raw file.
    sites.seen.clear()
    code, r2 = go(sites, task(roster, slugs, date(2012, 1, 1)), "backfill", datetime(2012, 7, 1, tzinfo=UTC))
    assert (code, r2["status"], r2["rows"], sites.seen) == (0, "success", 0, [])
    assert r2["http"].get("new_raw_files", 0) == 0 and r2["checks"]["resolutions_stored"]["passed"]


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


# ---------------------------------------------------------------------------------------------- the saved pages
FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "rbi"


@pytest.mark.db
@pytest.mark.skipif(not (FIXTURES / "INDEX.tsv").exists(), reason="the saved pages are only on the Mac (app/data/fixtures)")
def test_a_backfill_over_the_saved_pages_loads_every_resolution_from_the_2018_baseline(s3):
    """The B6 done-when counts over the 49 saved resolutions (Dec 2018 to Oct 2026) and the real roster: the meetings
    come from the saved Annual Policy pages through the calendar's own rules, the resolutions from their saved pages."""
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
        if r["kind"] == "resolution":
            pages[rbi_resolution.page_url(r["url"])] = (FIXTURES / r["file"]).read_text(encoding="utf-8", errors="replace")
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
    t = task(members_mod.CONFIG, slugs, meetings.FIRST_DAY)
    code, r = go(sites, t, "backfill", datetime(2026, 10, 23, 12, tzinfo=UTC))
    assert (code, r["status"]) == (0, "success"), r["error"]
    assert r["checks"]["meetings_failed"]["passed"], r["checks"]["meetings_failed"]["detail"]
    counts = __import__("json").loads(r["checks"]["resolutions_stored"]["detail"])
    assert counts | {"roster": 21} == {"targets": 49, "loaded": 49, "waiting": 0, "failed": 0, "rates": 196,
                                       "votes": 289, "rate_votes": 289, "dissents": 44, "projections": 419,
                                       "from_rss": 0, "roster": 21}, counts
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
