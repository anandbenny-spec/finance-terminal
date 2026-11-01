"""equity.call_transcripts, part 2 (job 2B-07), through the runner on the test database: the exchange and the company hosts
are a mock transport serving the saved replies and PDFs from data/fixtures/equity/calls (the private fixtures repo; these
tests skip without it). The pure decisions are in test_equity_call_text.py."""
import gzip
import itertools
import json
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
        self.chrome_seen: list[httpx.Request] = []

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
            return httpx.Response(200, json=nse_rows(q["symbol"]) if q["symbol"] in ("APOLLOHOSP", "HINDUNILVR", "MARUTI") else [])
        if host == "nsearchives.nseindia.com":
            name = path.rsplit("/", 1)[-1]
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
            return httpx.Response(200, content=b"<html>company reports</html>", headers={"content-type": "text/html"})
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
def test_a_dead_company_link_is_logged_by_host_then_a_later_run_upgrades_the_call(s3):
    make_company("APOLLOHOSP", "Apollo Hospitals Enterprise Limited")
    web = Web()
    web.company_pdf_status = 404
    code, tr = go(web)
    assert code == 0 and tr[0] == "success", tr
    [a] = calls("zf-apollohosp")
    assert a[1] == "on_company_website" and a[6] == "none" and a[8] is None
    c = checks(tr)
    assert not c["company_hosts"]["passed"] and "www.apollohospitals.com HTTP 404" in c["company_hosts"]["detail"]
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
