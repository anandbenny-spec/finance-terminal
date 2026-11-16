"""equity.call_transcripts, part 2 (job 2B-07), through the runner on the test database: the exchange and the company hosts
are a mock transport serving the saved replies and PDFs from data/fixtures/equity/calls (the private fixtures repo; these
tests skip without it). The pure decisions are in test_equity_call_text.py."""
import gzip
import itertools
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity.calls import discovery as dc
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks.equity_call_transcripts import EquityCallTranscripts

CALLS = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "calls"
needs_fixtures = pytest.mark.skipif(not (CALLS / "audit").exists(), reason="the saved files aren't checked out (data/fixtures)")

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5597, "equity-call-fetch-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_call_fetch"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
TASKS: list = []

APOLLO_LETTER = "APOLLOHOSP_19082026180025_transcript_Q1FY27.pdf"
APOLLO_PDF = "https://www.apollohospitals.com/sites/default/files/2026-08/transcript-of-apollo-hospitals---q1-fy27-earnings-call.pdf"
HUL_FILE = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"
MARUTI_LETTER_Q1 = "MARUTIASHISH_06082026173039_STxIntimation_Transcript_6Aug2026.pdf"      # served as the saved web-page-only letter
MARUTI_SAVED = "MARUTIASHISH_02022026205924_Transcript_02022026.pdf"
MARUTI_PAGE = "https://www.marutisuzuki.com/corporate/investors/company-reports"


def load(path):
    p = Path(path)
    return json.loads((gzip.open(p, "rt") if p.suffix == ".gz" else open(p)).read())


def nse_rows(symbol):
    return load(next((CALLS / "nse").glob(f"nse_ann_{symbol}_*.json.gz")))


def pdf(name):
    return next(CALLS.glob(f"pdf/*/{name}")).read_bytes()


class Web:
    """NSE's announcements API, the NSE archive and two company hosts, as a mock transport."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.company_pdf_status = 200
        self.refuse_plain = False                      # the company host refuses scripts: only Chrome's fingerprint is served
        self.vary_pages = False                        # the company's web page carries a new token on every request
        self.page_no = 0
        self.chrome_seen: list[httpx.Request] = []
        self.extra_rows: dict[str, list[dict]] = {}    # NSE announcement rows added to a symbol's saved reply
        self.aliases: dict[str, str] = {}              # an exchange file name served with another saved file's bytes

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.handle(request, chrome=False)

    def chrome(self, request: httpx.Request) -> httpx.Response:
        self.chrome_seen.append(request)
        return self.handle(request, chrome=True)

    def handle(self, request: httpx.Request, chrome: bool) -> httpx.Response:
        self.seen.append(request)
        host, path, q = request.url.host, request.url.path, dict(request.url.params)
        if host == "www.nseindia.com" and path == "/":
            return httpx.Response(200, content=b"<html>home</html>", headers={"set-cookie": "nsit=1; Path=/"})
        if path == dc.NSE_ANN_PATH:
            saved = nse_rows(q["symbol"]) if q["symbol"] in ("APOLLOHOSP", "HINDUNILVR", "MARUTI") else []
            return httpx.Response(200, json=saved + self.extra_rows.get(q["symbol"], []))
        if host == "nsearchives.nseindia.com":
            name = self.aliases.get(path.rsplit("/", 1)[-1], path.rsplit("/", 1)[-1])
            if name == MARUTI_LETTER_Q1:
                name = MARUTI_SAVED
            found = next(CALLS.glob(f"pdf/exchange/{name}"), None)
            return httpx.Response(200, content=found.read_bytes(), headers={"content-type": "application/pdf"}) if found \
                else httpx.Response(404)
        if host == "www.apollohospitals.com" and path.endswith(".pdf"):
            if self.refuse_plain and not chrome:
                return httpx.Response(403)
            if self.company_pdf_status != 200:
                return httpx.Response(self.company_pdf_status)
            return httpx.Response(200, content=pdf("apollohospitals.com_transcript-of-apollo-hospitals---q1-fy27-earnings-call.pdf"),
                                  headers={"content-type": "application/pdf"})
        if host == "www.marutisuzuki.com":
            page = b"<html>company reports</html>"
            if self.vary_pages:
                self.page_no += 1
                page += f"<!-- {self.page_no} -->".encode()
            return httpx.Response(200, content=page, headers={"content-type": "text/html"})
        return httpx.Response(404)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'call fetch test', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


def storage():
    from ft.storage import Storage
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zf-%')"
        c.execute(f"delete from equity.transcript_page where document_id in (select id from core.document where entity_id in {ids})")
        c.execute(f"delete from equity.earnings_call where entity_id in {ids}")
        c.execute(f"update core.document set supersedes_id = null where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zf-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))


def make_company(symbol: str, name: str) -> int:
    isin = next(r["sm_isin"] for r in nse_rows(symbol) if r.get("sm_isin"))
    with psycopg.connect(ADMIN, autocommit=True) as c:
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                        (f"zf-{symbol.lower()}", name)).fetchone()[0]
        c.execute("insert into equity.company (entity_id, isin, nse_symbol) values (%s,%s,%s)", (eid, isin, symbol))
        return eid


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    TASKS.clear()
    yield
    wipe()


def task() -> EquityCallTranscripts:
    class T(EquityCallTranscripts):
        key = KEY
    t = T()
    t.workflows = ("weekly", "backfill")
    TASKS.append(t)
    return t


def go(web, workflow="backfill", now=NOW):
    def site_transport(site):                          # the impersonating sites (NSE, BSE) and the Chrome retry
        return httpx.MockTransport(web.chrome if site.key == "chrome" else web)
    http = HttpClient(transport=httpx.MockTransport(web), site_transport=site_transport, sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, workflow, [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1", KEY)[0]
    assert tr[0] == "success", "RUN FAILED " + (tr[3] or "")[-700:].replace("\n", " | ")      # one line, so CI's annotation shows it
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def calls(slug):
    return q("""select e.slug, e.status, e.is_published, c.fiscal_label, c.quarter_source, c.season_key, c.route, c.status_reason,
                       c.transcript_document_id, c.cover_letter_document_id, c.company_url, c.words, c.exchange, c.filing_key
                  from equity.earnings_call c join core.event e on e.id = c.event_id
                  join core.entity en on en.id = c.entity_id where en.slug = %s order by e.slug""", slug)


@pytest.mark.db
@needs_fixtures
def test_an_exchange_transcript_and_a_followed_letter_become_unpublished_stored_calls_with_text_hashes_and_page_rows(s3):
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")
    web = Web()
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert c["transcripts_full"]["passed"] and c["no_link_to_our_copy"]["passed"] and c["nse_answered"]["passed"]
    [hul] = calls("zf-hindunilvr")
    assert hul[:8] == ("zf-hindunilvr-call-q1-fy27", "stored", False, "Q1 FY27", "text", "q1-fy27", "exchange_pdf", None)
    assert hul[9] is None and hul[10] is None and hul[11] > 3000 and hul[12] == "NSE"
    doc = q("""select d.doc_type, s.key, d.external_key, d.source_url, d.is_published, d.text_sha256 is not null, d.char_count,
                      d.extractor_version, d.event_id from core.document d join core.source s on s.id = d.source_id where d.id = %s""", hul[8])[0]
    assert doc[:5] == ("call_transcript", "nse", f"NSE/{HUL_FILE}", f"https://nsearchives.nseindia.com/corporate/{HUL_FILE}", False)
    assert doc[5] and doc[6] > 10000 and doc[7] == "pdf-1/calls-1" and doc[8] is not None
    pages = q("select pdf_page, char_start, char_end from equity.transcript_page where document_id = %s order by char_start", hul[8])
    assert len(pages) == 26 and pages[0][1] == 0 and pages[-1][2] == doc[6]
    assert all(a[2] == b[1] for a, b in itertools.pairwise(pages))
    [apollo] = calls("zf-apollohosp")
    assert apollo[:8] == ("zf-apollohosp-call-q1-fy27", "stored", False, "Q1 FY27", "text", "q1-fy27", "company_link", None)
    assert apollo[9] is not None and apollo[10] == APOLLO_PDF and apollo[12] == "NSE"
    t = q("""select d.doc_type, s.key, d.external_key, d.source_url from core.document d join core.source s on s.id = d.source_id
              where d.id = %s""", apollo[8])[0]
    assert t[:2] == ("call_transcript", "company_web") and t[2].startswith(APOLLO_PDF + "#") and t[3] == APOLLO_PDF
    assert q("select doc_type, source_url from core.document where id = %s", apollo[9])[0][0] == "call_cover_letter"
    assert q("select count(*) from equity.transcript_page where document_id = %s", apollo[9])[0][0] == 0
    assert q("select count(*) from equity.transcript_page where document_id = %s", apollo[8])[0][0] == 18
    assert q("select count(*) from core.document where entity_id in (select id from core.entity where slug like 'zf-%') and is_published")[0][0] == 0
    assert not any(r.url.host == "127.0.0.1" for r in web.seen if r.url.path.endswith(".pdf"))     # never our copy


@pytest.mark.db
@needs_fixtures
def test_a_second_run_registers_nothing(s3):
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")
    make_company("MARUTI", "Maruti Suzuki India Limited")
    code, tr = go(Web())
    assert code == 0 and tr[1] > 0, tr
    snapshot = [q(f"select count(*) from {t}")[0][0] for t in
                ("core.raw_file", "core.document", "core.event", "equity.earnings_call", "equity.transcript_page")]
    code, tr = go(Web())
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert snapshot == [q(f"select count(*) from {t}")[0][0] for t in
                        ("core.raw_file", "core.document", "core.event", "equity.earnings_call", "equity.transcript_page")]


@pytest.mark.db
@needs_fixtures
def test_a_backfill_reports_coverage_by_season_and_the_raw_storage(s3):
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")
    code, tr = go(Web())
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    cov = c["coverage_by_season"]
    assert cov["passed"] and not cov["blocking"]                                  # history is reported, never stopped on
    assert ("Q1 FY27: 2 companies with a transcript filing, 2 stored (100%), 0 on_company_website, 0 unreadable, 0 held, "
            "0 with no call") in cov["detail"]
    assert "with no call" in cov["detail"] and cov["detail"].index("Q2 FY26") < cov["detail"].index("Q1 FY27")      # oldest first
    st = c["raw_storage"]
    files, nbytes = q("""select count(*), coalesce(sum(bytes), 0) from core.raw_file
                          where task_run_id = (select max(id) from ops.task_run where task_key = %s)""", KEY)[0]
    assert st["passed"] and not st["blocking"]            # files may be 0: earlier tests stored the same replies and PDFs (once by hash)
    assert st["detail"].startswith(f"this run stored {files} files, {nbytes / 1048576:.1f} MB at source size; the raw bucket holds ")
    assert st["detail"].endswith("MB of stored objects, under the 850 MB stop")


@pytest.mark.db
@needs_fixtures
def test_a_letter_that_links_only_to_a_web_page_is_on_the_company_website_and_is_named(s3):
    make_company("MARUTI", "Maruti Suzuki India Limited")
    code, tr = go(Web())
    assert code == 0 and tr[0] == "success", tr
    [m] = calls("zf-maruti")
    assert m[1] == "on_company_website" and m[6] == "none" and m[8] is None and m[9] is not None and m[10] == MARUTI_PAGE
    c = checks(tr)
    assert not c["unresolved_letters"]["passed"] and "zf-maruti" in c["unresolved_letters"]["detail"]
    assert "marutisuzuki.com web page" in c["unresolved_letters"]["detail"]
    assert q("select count(*) from equity.transcript_page where document_id = %s", m[9])[0][0] == 0


@pytest.mark.db
@needs_fixtures
def test_a_letters_web_page_is_named_but_never_stored_even_when_it_differs_on_every_request(s3):
    """#173: a cover letter's link that leads to a web page, not a PDF, is read by nothing (D12), and such pages differ on
    every request, so storing them added a raw file to every re-run. The attempt is still named; a PDF is stored as ever."""
    make_company("MARUTI", "Maruti Suzuki India Limited")
    web_pages = """select count(*) from core.raw_file f join core.source s on s.id = f.source_id
                    where s.key = 'company_web' and f.media_type = 'text/html'"""
    pages_before = q(web_pages)[0][0]
    web = Web()
    web.vary_pages = True
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    assert "marutisuzuki.com web page" in checks(tr)["unresolved_letters"]["detail"]          # still named
    assert web.page_no >= 1 and q(web_pages)[0][0] == pages_before                            # fetched, not stored
    assert q("select count(*) from ops.http_cache where url like %s", f"{MARUTI_PAGE}%")[0][0] == 0
    raw = q("select count(*) from core.raw_file")[0][0]
    code, tr = go(web)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert q("select count(*) from core.raw_file")[0][0] == raw and q(web_pages)[0][0] == pages_before


@pytest.mark.db
@needs_fixtures
def test_a_dead_company_link_is_logged_by_host_then_a_later_run_upgrades_the_call(s3):
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")
    web = Web()
    web.company_pdf_status = 404
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    [a] = calls("zf-apollohosp")
    assert a[1] == "on_company_website" and a[6] == "none" and a[8] is None
    c = checks(tr)
    assert not c["company_hosts"]["passed"] and "apollohospitals.com HTTP 404" in c["company_hosts"]["detail"]
    assert not c["unresolved_letters"]["passed"]
    events = q("select id from core.event where slug = %s", "zf-apollohosp-call-q1-fy27")
    code, tr = go(Web())                                     # the file is there now (or a curated row names it)
    assert code == 0 and tr[0] == "success" and tr[1] > 0, tr
    [a] = calls("zf-apollohosp")
    assert a[1] == "stored" and a[6] == "company_link" and a[8] is not None and a[10] == APOLLO_PDF
    assert q("select id from core.event where slug = %s", "zf-apollohosp-call-q1-fy27") == events      # the same event, upgraded
    assert q("select count(*) from core.document where entity_id = (select id from core.entity where slug = 'zf-apollohosp')")[0][0] == 2
    assert q("select count(*) from equity.transcript_page where document_id = %s", a[8])[0][0] == 18


@pytest.mark.db
@needs_fixtures
def test_a_weekly_run_skips_a_season_that_is_not_newer_than_the_companys_newest_call(s3):
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    go(Web())
    web = Web()
    code, tr = go(web, workflow="weekly", now=datetime(2026, 11, 1, 6, 0, tzinfo=UTC))
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert not any(r.url.path.endswith(".pdf") for r in web.seen)                  # no PDF was fetched again
    assert len(calls("zf-hindunilvr")) == 1 and date(2026, 8, 4)


AGM_SEQ = "900000001"
AGM_FILE = "HINDUNILVR_20072026120000_SEIntimation80thAGMTranscriptandVideosigned.pdf"


def agm_row(symbol: str) -> dict:
    """An NSE row for the company's AGM transcript, filed on 20 Jul 2026, before its Q1 FY27 call (4 Aug)."""
    return {"an_dt": "20-Jul-2026 17:30:00", "exchdisstime": "20-Jul-2026 17:30:01", "sort_date": "2026-07-20 17:30:00",
            "attchmntFile": "https://nsearchives.nseindia.com/corporate/" + AGM_FILE, "seq_id": AGM_SEQ, "symbol": symbol,
            "attchmntText": "Video recording and Transcript of the 80th AGM of the Company", "desc": "Updates",
            "sm_isin": next(r["sm_isin"] for r in nse_rows(symbol) if r.get("sm_isin")), "sm_name": "Hindustan Unilever Limited"}


def seed_call(entity_id: int, slug: str, season: str, label: str | None, filing_key: str, *, published: bool, reason: str | None = None):
    """A call row an earlier run left: a stored call (published or not), or a held one (no label, a reason)."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        source = c.execute("select id from core.source where key = 'nse'").fetchone()[0]
        event = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
                             values ('earnings_call', %s, %s, 'seeded', '2026-07-20', 'stored', %s, %s) returning id""",
                          (entity_id, slug, source, published)).fetchone()[0]
        c.execute("""insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source,
                                                       filed_at, exchange, filing_key, route, status_reason)
                     values (%s, %s, %s, %s, %s, %s, '2026-07-20 12:00+00', 'NSE', %s, 'exchange_pdf', %s)""",
                  (event, entity_id, label, date(2026, 6, 30) if label else None, season, "text" if label else None, filing_key, reason))
    return event


@pytest.mark.db
@needs_fixtures
def test_a_held_row_on_the_slug_is_replaced_by_the_call_whose_quarter_is_decided(s3):
    eid = make_company("HINDUNILVR", "Hindustan Unilever Limited")
    seeded = seed_call(eid, "zf-hindunilvr-call-q1-fy27", "q1-fy27", None, "an-earlier-filing", published=False, reason="no_quarter")
    code, tr = go(Web())
    assert code == 0 and tr[0] == "success", tr
    [hul] = calls("zf-hindunilvr")
    assert hul[:8] == ("zf-hindunilvr-call-q1-fy27", "stored", False, "Q1 FY27", "text", "q1-fy27", "exchange_pdf", None)
    assert hul[8] is not None and hul[13] != "an-earlier-filing"                  # the real filing's transcript, not the held one's
    assert q("select id from core.event where entity_id = %s and event_type = 'earnings_call'", eid) == [(seeded,)]     # the same event
    assert "call_clashes" not in checks(tr)


@pytest.mark.db
@needs_fixtures
def test_a_stored_call_built_on_a_general_meeting_is_unpublished_and_the_real_call_takes_its_row(s3):
    eid = make_company("HINDUNILVR", "Hindustan Unilever Limited")
    web = Web()
    web.extra_rows["HINDUNILVR"] = [agm_row("HINDUNILVR")]
    web.aliases[AGM_FILE] = HUL_FILE
    # an earlier run took the AGM transcript for the quarter's call and published it (the Asian Paints case)
    seeded = seed_call(eid, "zf-hindunilvr-call-q1-fy27", "q1-fy27", "Q1 FY27", AGM_SEQ, published=True)
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    [row] = calls("zf-hindunilvr")                                                # still one row: nothing deleted, nothing renamed
    assert row[:8] == ("zf-hindunilvr-call-q1-fy27", "stored", False, "Q1 FY27", "text", "q1-fy27", "exchange_pdf", None)
    assert row[8] is not None and row[13] != AGM_SEQ                              # the real filing's transcript now; unpublished until call_text
    assert q("select id from core.event where entity_id = %s and event_type = 'earnings_call'", eid) == [(seeded,)]     # the same event
    c = checks(tr)
    assert AGM_FILE in c["general_meetings"]["detail"] and not c["general_meetings"]["passed"]
    assert "general meeting" in c["other_meetings"]["detail"]
    assert c["general_meeting_calls_unpublished"]["detail"].startswith("1 stored calls")
    assert "call_clashes" not in c
    assert q("""select count(*) from core.document where entity_id = %s and event_id is null
                   and title like '%%general meeting transcript%%'""", eid)[0][0] == 1          # the AGM transcript, another meeting's
    code, tr = go(web)                                                            # a second run changes nothing
    assert code == 0 and tr[1] == 0 and "general_meeting_calls_unpublished" not in checks(tr), tr


@pytest.mark.db
@needs_fixtures
def test_a_call_the_rules_set_aside_by_mistake_is_restored_when_its_own_filing_is_decided_again(s3):
    eid = make_company("HINDUNILVR", "Hindustan Unilever Limited")
    go(Web())
    [before] = calls("zf-hindunilvr")
    with psycopg.connect(ADMIN, autocommit=True) as c:                            # what the first detector did to NTPC's call
        c.execute("""update equity.earnings_call set status_reason = 'general_meeting', fiscal_label = null, period_end = null,
                            quarter_source = null, results_event_id = null where filing_key = %s""", (before[13],))
        c.execute("update core.event set is_published = false where entity_id = %s", (eid,))
    [set_aside] = calls("zf-hindunilvr")
    assert set_aside[3] is None and set_aside[7] == "general_meeting"
    code, tr = go(Web())
    assert code == 0 and tr[0] == "success", tr
    [after] = calls("zf-hindunilvr")
    assert after == before                                                        # the same row, label, transcript and filing again
    assert "call_clashes" not in checks(tr)


@pytest.mark.db
@needs_fixtures
def test_a_stored_call_the_rules_would_now_build_on_another_filing_is_named_and_left_as_stored(s3):
    eid = make_company("HINDUNILVR", "Hindustan Unilever Limited")
    seeded = seed_call(eid, "zf-hindunilvr-call-q1-fy27", "q1-fy27", "Q1 FY27", "an-earlier-filing", published=True)
    code, tr = go(Web())
    assert code == 0 and tr[0] == "success", tr
    [row] = calls("zf-hindunilvr")                                                # not rewritten, not renamed, still published
    assert row[:3] == ("zf-hindunilvr-call-q1-fy27", "stored", True) and row[13] == "an-earlier-filing"
    assert q("select id from core.event where entity_id = %s and event_type = 'earnings_call'", eid) == [(seeded,)]
    differ = checks(tr)["stored_calls_differ"]
    assert not differ["passed"] and not differ["blocking"]
    assert "zf-hindunilvr-call-q1-fy27" in differ["detail"] and "an-earlier-filing" in differ["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_stored_call_built_on_the_filing_the_rules_pick_is_not_named_as_differing(s3):
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    go(Web())
    code, tr = go(Web())                                                          # the second run finds the call stored from the same filing
    assert code == 0 and checks(tr)["stored_calls_differ"]["passed"]


@pytest.mark.db
@needs_fixtures
def test_a_company_host_that_refuses_scripts_is_retried_once_with_chromes_fingerprint(s3):
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")
    web = Web()
    web.refuse_plain = True
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    [a] = calls("zf-apollohosp")
    assert a[1] == "stored" and a[6] == "company_link"
    assert len(web.chrome_seen) == 1 and web.chrome_seen[0].url.host == "www.apollohospitals.com"
    assert checks(tr)["company_hosts"]["passed"]                                   # the retry worked: nothing to log


# ---------------------------------------------------------------------------------------------- the storage stop (F3-02, #387)
STOP_ALERT = f"storage_stop:{KEY}"


def open_alerts(dedupe_key):
    return q("select severity, message from ops.alert where dedupe_key = %s and resolved_at is null", dedupe_key)


def record_puts(monkeypatch) -> list[str]:
    """The media type of every `Storage.put` (the fetcher's store of each body), in order."""
    from ft.storage import Storage
    puts: list[str] = []
    real = Storage.put

    def put(self, data, *, source, media_type, ext):
        puts.append(media_type)
        return real(self, data, source=source, media_type=media_type, ext=ext)
    monkeypatch.setattr(Storage, "put", put)
    return puts


@pytest.mark.db
@needs_fixtures
def test_over_the_storage_stop_discovery_is_recorded_but_no_pdf_is_fetched_or_stored_and_an_error_alert_opens(s3, monkeypatch):
    from ft.storage import MB, Storage
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")
    puts = record_puts(monkeypatch)
    monkeypatch.setattr(Storage, "stored_bytes", lambda self: (1, 950 * MB))
    web = Web()
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr                                   # held, not failed: discovery is kept
    hosts = {r.url.host for r in web.seen}
    assert "nsearchives.nseindia.com" not in hosts and "www.apollohospitals.com" not in hosts          # no PDF fetched
    assert any(r.url.path == dc.NSE_ANN_PATH for r in web.seen)                                       # discovery ran
    assert "application/json" in puts and "application/pdf" not in puts         # its replies recorded; no PDF stored
    assert calls("zf-hindunilvr") == [] and calls("zf-apollohosp") == []         # no call row: a later run builds it
    c = checks(tr)
    stop = c["storage_stop"]
    assert not stop["passed"] and not stop["blocking"]
    assert stop["detail"].startswith("storage stop reached: the raw bucket holds 950.0 MB of stored objects, at or over the "
                                     "900 MB stop for new files; held: ")
    n_held, n_checked = map(int, re.search(r"held: (\d+) of (\d+) companies", stop["detail"]).groups())
    assert n_held == n_checked >= 2                                               # over at the start: every company is held
    assert "zf-" not in stop["detail"]                                            # counts and sizes only
    held = c["storage_held"]
    assert held["passed"] and not held["blocking"]
    assert re.search(r"zf-hindunilvr Q1 FY27: \d+ transcript filings?, held: storage stop", held["detail"]), held["detail"]
    assert re.search(r"zf-apollohosp Q1 FY27: \d+ transcript filings?, held: storage stop", held["detail"]), held["detail"]
    raw = c["raw_storage"]                                                        # the 850 MB warning is unchanged
    assert not raw["passed"] and not raw["blocking"] and raw["detail"].endswith("950.0 MB of stored objects, OVER the 850 MB stop")
    [(severity, message)] = open_alerts(STOP_ALERT)
    assert severity == "error" and message.startswith(f"{KEY}: storage stop reached: ") and "zf-" not in message
    assert open_alerts(f"check:{KEY}:storage_stop")                               # the failed check's own warning too

    monkeypatch.undo()                                                            # space freed: the next run builds the calls
    code, tr = go(Web())
    assert code == 0 and tr[0] == "success" and tr[1] > 0, tr
    assert calls("zf-hindunilvr")[0][1] == "stored" and calls("zf-apollohosp")[0][1] == "stored"
    c = checks(tr)
    assert c["storage_stop"]["passed"] and c["storage_stop"]["detail"].startswith("not reached: the raw bucket holds ")
    assert "storage_held" not in c
    assert open_alerts(STOP_ALERT) == [] and open_alerts(f"check:{KEY}:storage_stop") == []             # both resolved


@pytest.mark.db
@needs_fixtures
def test_the_stop_is_kept_current_with_each_file_stored_so_a_company_after_it_is_held(s3, monkeypatch):
    from ft.storage import MB, Storage
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")              # zf-apollohosp comes first (by slug)
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    monkeypatch.setattr(Storage, "stored_bytes", lambda self: (1, 900 * MB - 1))  # one byte under: the first company may store
    web = Web()
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    assert calls("zf-apollohosp")[0][1] == "stored"                               # its letter and PDF took the bucket over
    assert calls("zf-hindunilvr") == []
    assert not any(r.url.path.endswith(HUL_FILE) for r in web.seen)
    stop = checks(tr)["storage_stop"]
    assert not stop["passed"] and "at or over the 900 MB stop for new files; held: " in stop["detail"]
    n_held, n_checked = map(int, re.search(r"held: (\d+) of (\d+) companies", stop["detail"]).groups())
    assert 1 <= n_held < n_checked                                                # the companies before the stop weren't held


@pytest.mark.db
@needs_fixtures
def test_a_bucket_that_cant_be_listed_holds_new_files_with_its_own_message(s3, monkeypatch):
    from ft.storage import Storage
    make_company("HINDUNILVR", "Hindustan Unilever Limited")

    def refuse(self):
        raise RuntimeError("listing refused")
    monkeypatch.setattr(Storage, "stored_bytes", refuse)
    web = Web()
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    assert "nsearchives.nseindia.com" not in {r.url.host for r in web.seen} and calls("zf-hindunilvr") == []
    stop = checks(tr)["storage_stop"]
    assert not stop["passed"] and stop["detail"].startswith(
        "storage stop reached: the raw bucket's size couldn't be read, so new files are held (the stop fails closed; ")
    assert "listing refused" not in stop["detail"]
    [(severity, message)] = open_alerts(STOP_ALERT)
    assert severity == "error" and "couldn't be read" in message and "listing refused" not in message
