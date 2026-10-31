"""equity.filings (job 2A-07): the listing parsers and format detection on the saved NSE replies and XBRL instances, the
document-job interface, and the task run through the runner on mock replies built from those files.

The files are in data/fixtures/equity (the private fixtures repo; the tests skip without it, and CI checks it out):
`api/results_lists/` (NSE's replies for 20 symbols, both endpoints) and `xbrl/<SYMBOL>/` (the instances the lists point
to, 26 companies). No NSE data is kept in this repo (CLAUDE.md §4)."""
import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import filings as fl
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks.equity_filings import EquityFilings

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
LISTS, XBRL = FIX / "api" / "results_lists", FIX / "xbrl"
needs_fixtures = pytest.mark.skipif(not LISTS.exists(), reason="the saved files aren't checked out (data/fixtures)")
D = date


def unzipped(path: Path) -> bytes:
    with gzip.open(path) as f:
        return f.read()


def reply(name: str):
    return json.loads(unzipped(LISTS / f"{name}.json.gz"))


def instance(symbol: str, file_name: str) -> bytes:
    return unzipped(XBRL / symbol / f"{file_name}.gz")


# Instances in the folder that the saved listing doesn't hold: filings from before the Dec 2018 quarter (Bajaj Finance's
# Oct 2018 Ind AS pair and two May 2018 NONINDAS files) and a second copy HDFC Bank filed for the Mar 2019 quarter.
EXTRA = {"HDFCBANK": {"BANKING_43594_98455_22042019125125_WEB_2.xml"},
         "BAJFINANCE": {"INDAS_39422_54486_24102018094951_WEB.xml", "INDAS_39423_54488_24102018095228_WEB.xml",
                        "NONINDAS_35272_9888_17052018060424_WEB.xml", "NONINDAS_35272_9888_17052018060424_WEB_2.xml"}}


def listing(symbol: str) -> fl.Listing:
    q = fl.parse_results(fl.rows_of(reply(f"nse_results_{symbol}_Quarterly_20190101-20261019")), symbol)
    i = fl.parse_integrated(fl.rows_of(reply(f"nse_integrated_{symbol}_p1")), symbol)
    return fl.merge(q, i)


# ---------------------------------------------------------------------------------------------- the listings
@needs_fixtures
def test_reliance_lists_49_quarterly_and_12_integrated_filings_and_ignores_the_inline_duplicates():
    q = fl.parse_results(fl.rows_of(reply("nse_results_RELIANCE_Quarterly_20190101-20261019")), "RELIANCE")
    i = fl.parse_integrated(fl.rows_of(reply("nse_integrated_RELIANCE_p1")), "RELIANCE")
    assert (len(q.filings), q.empty_links, q.cumulative, q.unreadable) == (49, 0, 0, [])
    assert (len(i.filings), i.ixbrl_duplicates, i.ixbrl_only, i.empty_links) == (12, 12, 0, 0)
    assert {f.route for f in q.filings} == {"results"} and {f.route for f in i.filings} == {"integrated"}
    assert all(f.file_name.endswith(".xml") for f in q.filings + i.filings)
    m = fl.merge(q, i)
    assert len(m.filings) == 61                                   # the endpoints don't overlap for Reliance
    # 4 quarters a year on both bases, from the Dec 2018 quarter; 2022 has one consolidated filing fewer (a missing
    # listing row: the quarters_filed check names it), and 2026 has two quarters so far
    by = {}
    for f in m.filings:
        by[(f.period_end.year, f.basis)] = by.get((f.period_end.year, f.basis), 0) + 1
    assert by[(2018, "consolidated")] == by[(2018, "standalone")] == 1
    assert by[(2019, "consolidated")] == by[(2021, "standalone")] == 4
    assert (by[(2022, "consolidated")], by[(2022, "standalone")]) == (3, 4)
    assert (by[(2026, "consolidated")], by[(2026, "standalone")]) == (2, 2)
    first = min(m.filings, key=lambda f: f.period_end)
    assert first.period_end == D(2018, 12, 31)


@needs_fixtures
def test_a_listing_row_is_read_to_its_basis_period_audit_flag_and_dissemination_time():
    rows = fl.rows_of(reply("nse_results_RELIANCE_Quarterly_20190101-20261019"))
    got = {f.file_name: f for f in fl.parse_results(rows, "RELIANCE").filings}
    f = got["INDAS_117298_1348254_16012025082021.xml"]            # the Dec 2024 quarter, standalone ("Non-Consolidated")
    assert (f.period_end, f.basis, f.audited) == (D(2024, 12, 31), "standalone", False)
    assert f.filed_at == datetime(2025, 1, 16, 20, 20, 54, tzinfo=fl.INDIA)
    assert f.filed_at.astimezone(UTC) == datetime(2025, 1, 16, 14, 50, 54, tzinfo=UTC)
    assert (f.fy_start, f.fy_end) == (D(2024, 4, 1), D(2025, 3, 31))
    ig = {f.file_name: f for f in fl.parse_integrated(fl.rows_of(reply("nse_integrated_RELIANCE_p1")), "RELIANCE").filings}
    g = ig["INTEGRATED_FILING_INDAS_1695741_17072026075004_WEB.xml"]
    assert (g.period_end, g.basis, g.audited, g.revision) == (D(2026, 6, 30), "consolidated", False, False)
    assert g.fy_start is None                                     # the integrated list has none: derived from the company


@needs_fixtures
def test_nse_listing_a_filing_with_the_empty_link_is_counted_and_not_a_candidate():
    q = fl.parse_results(fl.rows_of(reply("nse_results_BAJFINANCE_Quarterly_20190101-20261019")), "BAJFINANCE")
    assert q.empty_links == 10 and len(q.filings) == 44
    assert all(not f.url.endswith("/-") for f in q.filings)


def test_year_to_date_rows_and_unreadable_rows_are_set_aside_with_a_reason():
    ok = {"xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/INDAS_1_2_3.xml", "toDate": "31-Dec-2024",
          "consolidated": "Consolidated", "audited": "Audited", "exchdisstime": "16-Jan-2025 20:20:54"}
    lst = fl.parse_results([ok, {**ok, "cumulative": "Cumulative"}, {**ok, "consolidated": "?", "xbrl": ok["xbrl"] + "x.xml"},
                            {**ok, "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/-"}], "X")
    assert (len(lst.filings), lst.cumulative, lst.empty_links, len(lst.unreadable)) == (1, 1, 1, 1)
    assert "basis unreadable" in lst.unreadable[0]


def test_an_integrated_row_with_only_an_inline_page_is_counted_and_a_revision_is_flagged():
    base = {"qe_Date": "30-JUN-2026", "consolidated": "Standalone", "audited": "Un-Audited",
            "broadcast_Date": "17-Jul-2026 19:49:04", "type_Sub": "Revision",
            "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1_2_WEB.xml",
            "ixbrl": "https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_1_2_iXBRL_WEB.html"}
    lst = fl.parse_integrated([base, {**base, "xbrl": None}, {**base, "xbrl": None, "ixbrl": None}], "X")
    assert (len(lst.filings), lst.ixbrl_duplicates, lst.ixbrl_only, lst.empty_links) == (1, 1, 1, 1)
    assert lst.filings[0].revision is True and lst.filings[0].basis == "standalone"


def test_the_financial_year_of_a_period_and_the_names_of_events():
    assert fl.fy_of(D(2024, 12, 31), 3) == (D(2024, 4, 1), D(2025, 3, 31))
    assert fl.fy_of(D(2025, 3, 31), 3) == (D(2024, 4, 1), D(2025, 3, 31))
    assert fl.fy_of(D(2025, 3, 31), 12) == (D(2025, 1, 1), D(2025, 12, 31))
    assert fl.event_slug("reliance", D(2024, 12, 31), 1) == "reliance-results-2024-12-31"
    assert fl.event_slug("reliance", D(2024, 12, 31), 2) == "reliance-results-2024-12-31-r2"
    assert fl.parse_date("30-JUN-2026") == D(2026, 6, 30) and fl.parse_date("31-Dec-2024") == D(2024, 12, 31)
    assert fl.parse_time("16-Jan-2025 20:20") == datetime(2025, 1, 16, 20, 20, tzinfo=fl.INDIA)
    now = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
    assert fl.window_start(now, None) == D(2025, 9, 23)           # the last 400 days
    assert fl.window_start(now, D(2019, 1, 1)) == D(2018, 12, 31)  # the first backfill window reaches the Dec 2018 quarter
    assert fl.window_start(now, D(2020, 1, 1)) == D(2020, 1, 1)


# ---------------------------------------------------------------------------------------------- the files
@needs_fixtures
@pytest.mark.parametrize("symbol, file_name, expected", [
    ("RELIANCE", "INTEGRATED_FILING_INDAS_1695741_17072026075004_WEB.xml", "ind_as"),
    ("RELIANCE", "INDAS_117298_1348254_16012025082021.xml", "ind_as"),
    ("HDFCBANK", "INTEGRATED_FILING_BANKING_1740913_17102026054812_WEB.xml", "bank"),
    ("HDFCBANK", "BANKING_101078_1023175_16012024051131.xml", "bank"),
    ("BAJFINANCE", "INTEGRATED_FILING_NBFC_INDAS_1742040_19102026075918_WEB.xml", "nbfc"),
    ("BAJFINANCE", "NBFC_INDAS_104720_1108490_25042024091121.xml", "nbfc"),
    ("BAJFINANCE", "INDAS_41835_78261_29012019042329_WEB.xml", "ind_as"),   # filed in the plain Ind AS taxonomy
    ("SBILIFE", "INTEGRATED_FILING_LI_1700867_24072026045312_WEB.xml", "insurer_life"),
    ("ICICIGI", "INTEGRATED_FILING_GI_1740432_16102026073544_WEB.xml", "insurer_general"),
])
def test_the_five_formats_are_told_apart_from_what_the_file_holds(symbol, file_name, expected):
    assert fl.format_of(file_name, fl.read_instance(instance(symbol, file_name))) == expected


@needs_fixtures
def test_every_saved_instance_of_the_universe_reads_and_is_one_of_the_five_formats():
    seen, odd = {}, []
    for p in sorted(XBRL.glob("*/*.xml.gz")):
        name = p.name[:-3]
        try:
            fmt = fl.format_of(name, fl.read_instance(unzipped(p)))
        except fl.FilingError as e:
            odd.append((p.parent.name, name, str(e)))
            continue
        seen[fmt] = seen.get(fmt, 0) + 1
    assert set(seen) == set(fl.FORMATS)
    # the only files that are none of the five are Bajaj Finance's two May 2018 `NONINDAS_` filings, which are before the
    # Dec 2018 quarter and are not in any window
    assert [(s, n.split("_")[0]) for s, n, _ in odd] == [("BAJFINANCE", "NONINDAS")] * 2


def test_a_page_that_is_not_an_xbrl_instance_is_refused():
    with pytest.raises(fl.FilingError, match="not XML"):
        fl.read_instance(b"<html><body>Access Denied")
    with pytest.raises(fl.FilingError, match="not an XBRL instance"):
        fl.read_instance(b"<html xmlns='http://www.w3.org/1999/xhtml'><body/></html>")
    with pytest.raises(fl.FilingError, match="no facts"):
        fl.read_instance(b"<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance'/>")
    with pytest.raises(fl.FilingError, match="other than the five"):
        fl.format_of("NONINDAS_1_2_3.xml", fl.Xbrl(frozenset({"Revenue"}), 1))
    assert fl.read_instance(b"<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance' xmlns:a='urn:a'>"
                            b"<a:Revenue contextRef='One'>5</a:Revenue></xbrli:xbrl>").names == {"Revenue"}


@needs_fixtures
def test_a_saved_listing_agrees_with_the_saved_instances():
    """By hand: Reliance, HDFC Bank, SBI Life and Bajaj Finance list a file for every instance the folder holds."""
    for symbol, listed, in_folder in (("RELIANCE", 61, 61), ("HDFCBANK", 62, 63), ("SBILIFE", 6, 6), ("BAJFINANCE", 58, 62)):
        names = {f.file_name for f in listing(symbol).filings}
        files = {p.name[:-3] for p in (XBRL / symbol).glob("*.xml.gz")}
        assert len(names) == listed and len(files) == in_folder, symbol
        assert names <= files, (symbol, sorted(names - files))
        assert files - names == EXTRA.get(symbol, set()), symbol


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5599, "equity-filings-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_filings"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
CO = {"RELIANCE": 3, "HDFCBANK": 3, "SBILIFE": 3, "BAJFINANCE": 3}       # symbol -> fiscal year end month


class Nse:
    """NSE's API and archive as a mock transport: the saved listing replies and the saved instances. `files` overrides an
    instance (a file name -> bytes, None for 404); `rows` appends listing rows (symbol -> [(endpoint, row)]); `seen`
    records the requests."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.files: dict[str, bytes | None] = {}
        self.rows: dict[str, list[tuple[str, dict]]] = {}
        self.bytes = {p.name[:-3]: p for p in XBRL.glob("*/*.xml.gz")}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path, q = request.url.path, dict(request.url.params)
        if path == "/":
            return httpx.Response(200, content=b"<html>home</html>", headers={"set-cookie": "nsit=1; Path=/"})
        if path == fl.RESULTS_PATH:
            sym = q["symbol"]
            f = LISTS / f"nse_results_{sym}_Quarterly_20190101-20261019.json.gz"
            rows = json.loads(unzipped(f)) if f.exists() else []
            rows = rows + [r for ep, r in self.rows.get(sym, []) if ep == "results"]
            return httpx.Response(200, json=rows)
        if path == fl.INTEGRATED_PATH:
            sym = q["symbol"]
            f = LISTS / f"nse_integrated_{sym}_p1.json.gz"
            if q["page"] != "1" or not f.exists():
                return httpx.Response(200, json={"data": [], "size": 20, "page": int(q["page"]), "totalCount": 0})
            reply_ = json.loads(unzipped(f))
            reply_["data"] = reply_["data"] + [r for ep, r in self.rows.get(sym, []) if ep == "integrated"]
            return httpx.Response(200, json=reply_)
        name = path.rsplit("/", 1)[-1]
        if name in self.files:
            data = self.files[name]
        else:
            data = unzipped(self.bytes[name]) if name in self.bytes else None
        return httpx.Response(404) if data is None else httpx.Response(200, content=data)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity filings test', '{weekly}', '6 days', '10 days', false)
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
        c.execute(f"delete from equity.financials where entity_id in {ids}")
        c.execute(f"delete from equity.filing where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zf-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))


def make_companies(names=tuple(CO), listed_on=None):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for n in names:
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                            (f"zf-{n.lower()}", f"{n} test")).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, nse_symbol, fy_end_month, listed_on)
                         values (%s, %s, %s, %s)""", (eid, n, CO[n], listed_on))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def task() -> EquityFilings:
    class T(EquityFilings):
        key = KEY
    t = T()
    t.workflows = ("weekly", "backfill")
    return t


WINDOW = {"since": D(2019, 1, 1), "until": D(2026, 10, 27)}


def go(ex, since=None, until=None, now=NOW, workflow="backfill"):
    http = HttpClient(transport=httpx.MockTransport(ex), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, workflow, [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now,
               since=since, until=until)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s
              order by id desc limit 1""", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def filings():
    return q("""select e.slug, f.period_end, f.basis, f.format, f.parse_status, f.fy_start, f.fy_end, d.external_key,
                       d.version_no, d.is_published, d.doc_type, ev.slug, ev.status
                  from equity.filing f join core.document d on d.id = f.document_id
                  join core.entity e on e.id = f.entity_id join core.event ev on ev.id = f.event_id
                 where e.slug like 'zf-%%' order by 1, 2, 3, 8, 9""")


@pytest.mark.db
@needs_fixtures
def test_a_backfill_registers_every_served_filing_with_its_event_and_format(s3):
    make_companies()
    ex = Nse()
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert {n: (v["passed"], v["blocking"]) for n, v in c.items()} == {
        "lists_read": (True, True), "basis_known": (True, True), "instances_parse": (True, True),
        "formats_known": (True, True), "files_served": (True, False), "quarters_filed": (False, False),
        "skipped_links": (True, False)}
    got = filings()
    per_company = {}
    for r in got:
        per_company[r[0]] = per_company.get(r[0], 0) + 1
    # 61 + 62 + 6 + 58 listed filings in the window, all served
    assert per_company == {"zf-reliance": 61, "zf-hdfcbank": 62, "zf-sbilife": 6, "zf-bajfinance": 58}
    assert tr[1] == 187 and "10 rows listed with the empty link" in c["skipped_links"]["detail"]
    assert all(r[4] == "pending" and r[9] is False and r[10] == "results_xbrl" and r[8] == 1 for r in got)
    by = {(r[0], r[1], r[2]): r for r in got}
    r = by[("zf-reliance", D(2024, 12, 31), "standalone")]
    assert (r[3], r[5], r[6], r[11], r[12]) == ("ind_as", D(2024, 4, 1), D(2025, 3, 31),
                                                "zf-reliance-results-2024-12-31", "filed")
    assert by[("zf-hdfcbank", D(2026, 9, 30), "consolidated")][3] == "bank"
    assert by[("zf-sbilife", D(2026, 6, 30), "standalone")][3] == "insurer_life"
    assert by[("zf-bajfinance", D(2026, 9, 30), "consolidated")][3] == "nbfc"
    assert by[("zf-bajfinance", D(2018, 12, 31), "consolidated")][3] == "ind_as"      # the Dec 2018 quarter is in
    # one event per company and period, whose time is the earliest dissemination of its documents (UTC)
    ev = q("""select count(*), count(distinct slug) from core.event where event_type = 'results_filing'
               and entity_id in (select id from core.entity where slug like 'zf-%')""")[0]
    assert ev[0] == ev[1] == 31 + 32 + 6 + 29
    t = q("""select ev.occurs_at, min(f.filed_at) from core.event ev join equity.filing f on f.event_id = ev.id
              where ev.slug = 'zf-reliance-results-2024-12-31' group by 1""")[0]
    first = min(f.filed_at for f in listing("RELIANCE").filings if f.period_end == D(2024, 12, 31))
    assert t[0] == t[1] == first.astimezone(UTC) == datetime(2025, 1, 16, 14, 45, 46, tzinfo=UTC)   # the consolidated one
    # every document points at the raw file whose bytes it is
    assert q("""select count(*) from core.document d join core.raw_file r on r.id = d.raw_file_id
                 where d.entity_id in (select id from core.entity where slug like 'zf-%') and r.media_type like '%xml%'
                   and d.doc_type = 'results_xbrl'""")[0][0] == 187
    # the listings were stored as raw files too, and the homepage was visited first and not stored
    assert ex.seen[0].url.path == "/" and sum(1 for r in ex.seen if r.url.path == "/") == 1
    assert q("select count(*) from core.raw_file where media_type = 'application/json'")[0][0] >= 8


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_run_writes_nothing(s3):
    make_companies(("SBILIFE", "RELIANCE"))
    go(Nse(), **WINDOW)
    before = filings()
    docs = q("select count(*) from core.document where entity_id in (select id from core.entity where slug like 'zf-%')")
    code, tr = go(Nse(), **WINDOW)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert filings() == before
    assert q("select count(*) from core.document where entity_id in (select id from core.entity where slug like 'zf-%')") == docs


@pytest.mark.db
@needs_fixtures
def test_other_bytes_under_one_file_name_make_a_new_version_and_an_alert(s3):
    make_companies(("SBILIFE",))
    go(Nse(), **WINDOW)
    name = "INTEGRATED_FILING_LI_1700867_24072026045312_WEB.xml"
    ex = Nse()
    ex.files[name] = instance("SBILIFE", name).replace(b"<xbrli:xbrl", b"<!-- changed --><xbrli:xbrl", 1)
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success" and tr[1] == 1, tr
    rows = [r for r in filings() if r[7] == name]
    assert [r[8] for r in rows] == [1, 2]
    # both versions point at the one announcement, and the earlier version is kept, not overwritten
    assert len({r[11] for r in rows}) == 1 and all(r[12] == "filed" for r in rows)
    assert q("""select supersedes_id is not null from core.document where external_key = %s and version_no = 2""",
             name) == [(True,)]
    assert q("select resolved_at is null from ops.alert where dedupe_key = %s",
             f"document_changed:{KEY}:{name}") == [(True,)]
    code, tr = go(ex, **WINDOW)                                                      # and a third run is quiet again
    assert tr[1] == 0 and len([r for r in filings() if r[7] == name]) == 2


@pytest.mark.db
@needs_fixtures
def test_a_revised_filing_gets_a_second_announcement_and_supersedes_the_first(s3):
    make_companies(("RELIANCE",))
    ex = Nse()
    row = {"audited": "Un-Audited", "broadCastDate": "20-Jan-2025 10:00:00", "consolidated": "Non-Consolidated",
           "exchdisstime": "20-Jan-2025 10:00:05", "fromDate": "01-Oct-2024", "toDate": "31-Dec-2024",
           "financialYear": "01-Apr-2024 To 31-Mar-2025", "period": "Quarterly", "cumulative": "Non-cumulative",
           "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/INDAS_117298_9999999_20012025100005.xml"}
    ex.rows["RELIANCE"] = [("results", row)]
    ex.files["INDAS_117298_9999999_20012025100005.xml"] = instance("RELIANCE", "INDAS_117298_1348254_16012025082021.xml")
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success", tr
    got = {(r[11], r[12]) for r in filings() if r[1] == D(2024, 12, 31)}
    assert got == {("zf-reliance-results-2024-12-31", "superseded"), ("zf-reliance-results-2024-12-31-r2", "filed")}
    revised = [r for r in filings() if r[7].endswith("20012025100005.xml")]
    assert [r[11] for r in revised] == ["zf-reliance-results-2024-12-31-r2"]
    # the consolidated filing of the original announcement stays attached to the (now superseded) first event
    assert [r[11] for r in filings() if r[1] == D(2024, 12, 31) and r[2] == "consolidated"] == [
        "zf-reliance-results-2024-12-31"]


@pytest.mark.db
@needs_fixtures
def test_a_listed_file_that_is_not_served_is_a_warning_and_an_error_page_is_a_blocking_failure(s3):
    make_companies(("SBILIFE",))
    gone = "INTEGRATED_FILING_LI_1424277_24042025082603_WEB.xml"
    ex = Nse()
    ex.files[gone] = None
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success" and tr[1] == 5, tr
    c = checks(tr)
    assert not c["files_served"]["passed"] and gone in c["files_served"]["detail"] and not c["files_served"]["blocking"]
    wipe()
    make_companies(("SBILIFE",))
    ex = Nse()
    ex.files[gone] = b"<html><body>Access Denied</body></html>"                  # served with 200
    code, tr = go(ex, **WINDOW)
    assert code != 0 and tr[0] == "checks_failed", tr
    assert not checks(tr)["instances_parse"]["passed"] and gone in checks(tr)["instances_parse"]["detail"]
    assert filings() == []                                                           # the load rolled back


@pytest.mark.db
@needs_fixtures
def test_a_format_other_than_the_five_fails_the_run(s3):
    make_companies(("RELIANCE",))
    odd = "INTEGRATED_FILING_NONINDAS_1_2_WEB.xml"
    ex = Nse()
    ex.rows["RELIANCE"] = [("integrated", {
        "qe_Date": "31-MAR-2025", "consolidated": "Consolidated", "audited": "Audited", "type_Sub": "Original",
        "broadcast_Date": "25-Apr-2025 10:00:00", "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/" + odd,
        "ixbrl": None})]
    ex.files[odd] = instance("RELIANCE", "INDAS_117298_1348254_16012025082021.xml")
    code, tr = go(ex, **WINDOW)
    assert code != 0 and tr[0] == "checks_failed" and not checks(tr)["formats_known"]["passed"], tr
    assert odd in checks(tr)["formats_known"]["detail"] and filings() == []


@pytest.mark.db
@needs_fixtures
def test_a_listing_refused_by_nse_fails_the_run_and_names_the_company(s3):
    make_companies(("SBILIFE", "RELIANCE"))
    base = Nse()

    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == fl.INTEGRATED_PATH and request.url.params.get("symbol") == "RELIANCE":
            return httpx.Response(403, content=b"Access Denied")
        return base(request)
    code, tr = go(refuse, **WINDOW)
    assert code != 0 and tr[0] == "checks_failed", tr
    c = checks(tr)["lists_read"]
    assert not c["passed"] and "RELIANCE" in c["detail"] and "SBILIFE" not in c["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_quarter_without_a_filing_is_named_after_its_deadline(s3):
    """Bajaj Finance's listing has the empty link for Mar, Jun and Sep 2019 (NSE lists the filings without a file);
    Reliance has every quarter, and the Sep 2026 quarter isn't due until 60 days after 30 Sep."""
    make_companies(("RELIANCE", "BAJFINANCE"))
    code, tr = go(Nse(), **WINDOW)
    assert code == 0, tr
    c = checks(tr)["quarters_filed"]
    assert not c["passed"] and not c["blocking"]
    assert "zf-bajfinance (3: 2019-03-31, 2019-06-30, 2019-09-30)" in c["detail"] and "zf-reliance" not in c["detail"]


@pytest.mark.db
def test_the_production_guard_holds_a_scheduled_first_run_but_not_a_refresh():
    class Never(EquityFilings):
        key = "test.equity_filings_never_run"
    t = Never()
    with psycopg.connect(ADMIN, autocommit=True) as c:
        weekly = t.due(c, "weekly", NOW)
        refresh = t.due(c, "refresh", NOW)
    assert weekly.due is False and "waiting for its first recorded run" in weekly.reason
    assert refresh.due is True
