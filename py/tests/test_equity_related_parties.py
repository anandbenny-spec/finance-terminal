"""Related-party rows and segment revenue for the value chain card (job VC-05; Spec 02B §12.4): the reader's rules, then the
task through the runner on the test database.

The saved results filings are in data/fixtures/equity/xbrl (the private fixtures repo; the tests that use them skip without it,
and CI checks it out). The synthetic tests build small instances and always run."""
from __future__ import annotations

import gzip
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import related_parties as rp
from ft.equity import xbrl
from ft.jobs import config
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_related_parties import EquityRelatedParties

D = date
XBRL = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "xbrl"
needs_fixtures = pytest.mark.skipif(not XBRL.exists(), reason="the saved files aren't checked out (data/fixtures)")

MM_H1_SA, MM_H1_CO = "M&M/INTEGRATED_FILING_INDAS_1565188_04112025075712_WEB.xml", "M&M/INTEGRATED_FILING_INDAS_1565191_04112025075824_WEB.xml"
MM_Q4_SA, MM_Q4_CO = "M&M/INTEGRATED_FILING_INDAS_1663643_05052026042738_WEB.xml", "M&M/INTEGRATED_FILING_INDAS_1663644_05052026042813_WEB.xml"
MARUTI_H1_SA = "MARUTI/INTEGRATED_FILING_INDAS_1562963_31102025080837_WEB.xml"
MARUTI_Q4_SA = "MARUTI/INTEGRATED_FILING_INDAS_1660694_28042026092749_WEB.xml"
MARUTI_Q4_CO = "MARUTI/INTEGRATED_FILING_INDAS_1660590_28042026074740_WEB.xml"
PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")


def raw(rel: str) -> bytes:
    with gzip.open(XBRL / (rel + ".gz")) as f:
        return f.read()


def mm_h1(basis="standalone") -> rp.Parsed:
    return rp.parse_filing(raw(MM_H1_SA if basis == "standalone" else MM_H1_CO),
                           rp.Filing(basis, D(2025, 9, 30), D(2025, 4, 1), D(2026, 3, 31), False))


def mm_q4(basis="consolidated") -> rp.Parsed:
    return rp.parse_filing(raw(MM_Q4_CO if basis == "consolidated" else MM_Q4_SA),
                           rp.Filing(basis, D(2026, 3, 31), D(2025, 4, 1), D(2026, 3, 31), True))


# ---------------------------------------------------------------------------------------------- the rules, synthetic
NS = ('xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
      'xmlns:c="http://example.org/c"')


def instance(rows: list[dict], *, name="Acme Limited", basis="Standalone", extra="", period_end="2025-09-30") -> bytes:
    """A small results instance: a header, the quarter context, and one typed related-party member per row (a duration
    context with its facts, and an instant context with a larger amount that must never be read)."""
    ctxs = [('<xbrli:context id="OneD"><xbrli:entity><xbrli:identifier scheme="s">x</xbrli:identifier></xbrli:entity>'
             f'<xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>{period_end}</xbrli:endDate></xbrli:period></xbrli:context>')]
    facts = [f'<c:NameOfTheCompany contextRef="OneD">{name}</c:NameOfTheCompany>',
             f'<c:NatureOfReportStandaloneConsolidated contextRef="OneD">{basis}</c:NatureOfReportStandaloneConsolidated>',
             f'<c:DateOfEndOfReportingPeriod contextRef="OneD">{period_end}</c:DateOfEndOfReportingPeriod>',
             '<c:DateOfEndOfFinancialYear contextRef="OneD">2026-03-31</c:DateOfEndOfFinancialYear>']
    for i, r in enumerate(rows, 1):
        m = f"RelatedPartyTransactionDomain{i}"
        for suffix, period in (("D", "<xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate>"),
                               ("I", "<xbrli:instant>2025-09-30</xbrli:instant>")):
            ctxs.append(
                f'<xbrli:context id="R{i}{suffix}"><xbrli:entity><xbrli:identifier scheme="s">x</xbrli:identifier></xbrli:entity>'
                f'<xbrli:period>{period}</xbrli:period><xbrli:scenario><xbrldi:typedMember dimension="c:RelatedPartyTransactionAxis">'
                f'<c:RelatedPartyTransactionDomain>{m}</c:RelatedPartyTransactionDomain></xbrldi:typedMember></xbrli:scenario></xbrli:context>')
        unit = r.get("unit", "INR")
        facts += [f'<c:NameOfCounterParty contextRef="R{i}D">{r["who"]}</c:NameOfCounterParty>',
                  f'<c:TypeOfRelatedPartyTransaction contextRef="R{i}D">{r.get("type", "Sale of goods or services")}</c:TypeOfRelatedPartyTransaction>',
                  f'<c:NameOfListedEntityOrSubsidiaryEnteringIntoTheTransaction contextRef="R{i}D">{r.get("by", "ACME LTD.")}</c:NameOfListedEntityOrSubsidiaryEnteringIntoTheTransaction>',
                  f'<c:PANOfCounterParty contextRef="R{i}D">AAAPA1234A</c:PANOfCounterParty>',
                  f'<c:AmountOfRelatedPartyTransactionDuringTheReportingPeriod contextRef="R{i}D" unitRef="{unit}" decimals="-5">{r["v"]}</c:AmountOfRelatedPartyTransactionDuringTheReportingPeriod>',
                  f'<c:AmountOfRelatedPartyTransaction contextRef="R{i}I" unitRef="INR" decimals="-5">999999999999</c:AmountOfRelatedPartyTransaction>']
    return (f'<?xml version="1.0"?><xbrli:xbrl {NS}>' + "".join(ctxs) + '<xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>'
            + "".join(facts) + extra + "</xbrli:xbrl>").encode()


def parse(body: bytes, basis="standalone", period_end=None, year_end=False) -> rp.Parsed:
    pe = period_end or D(2025, 9, 30)
    return rp.parse_filing(body, rp.Filing(basis, pe, D(2025, 4, 1), D(2026, 3, 31), year_end))


def test_names_match_after_case_ampersand_ltd_and_punctuation():
    assert rp.norm_name("Mahindra & Mahindra Limited") == rp.norm_name("MAHINDRA AND MAHINDRA LIMITED")
    assert rp.norm_name("Acme Ltd.") == rp.norm_name("ACME  LIMITED") == rp.norm_name("acme limited")
    assert rp.norm_name("Acme Limited") != rp.norm_name("Acme Private Limited")


def test_the_period_is_the_filings_half_year_not_the_context_dates():
    assert rp.half_year(D(2025, 9, 30)) == (D(2025, 4, 1), D(2025, 9, 30))
    assert rp.half_year(D(2026, 3, 31)) == (D(2025, 10, 1), D(2026, 3, 31))
    assert rp.half_year(D(2025, 12, 31)) == (D(2025, 7, 1), D(2025, 12, 31))
    p = parse(instance([{"who": "Beta Pvt Ltd", "v": "5000000"}]))
    assert (p.period_start, p.period_end) == (D(2025, 4, 1), D(2025, 9, 30))


def test_values_come_from_the_duration_context_and_the_entering_party_sets_entered_by():
    p = parse(instance([{"who": "Beta Pvt Ltd", "v": "5000000"},
                        {"who": "Gamma Ltd", "v": "7", "by": "ACME SUBSIDIARY LIMITED"}]))
    assert [(r.counterparty, r.value, r.entered_by) for r in p.rows] == [
        ("Beta Pvt Ltd", Decimal(5000000), "company"), ("Gamma Ltd", Decimal(7), "subsidiary")]
    assert all(r.value < Decimal(999999999999) for r in p.rows)         # the instant context's balance is never a row value


def test_no_pan_is_read_or_stored():
    p = parse(instance([{"who": "Beta Pvt Ltd", "v": "5"}]))
    texts = [t for r in p.rows for t in (r.counterparty, r.relationship, r.txn_type, r.entering_party, r.entered_by) if t]
    assert not any(PAN.search(t) or "AAAPA1234A" in t for t in texts)
    assert not hasattr(p.rows[0], "pan")


def test_a_filing_with_rows_but_none_naming_the_company_is_held():
    with pytest.raises(rp.RptError) as e:
        parse(instance([{"who": "Beta", "v": "5", "by": "SOMEONE ELSE LIMITED"}]))
    assert e.value.check == "unmatched"


def test_a_banks_filing_names_itself_with_nameofbank():
    body = instance([{"who": "Beta", "v": "5", "by": "Acme Bank Limited"}], name="x").replace(
        b"NameOfTheCompany", b"NameOfBank").replace(b">x<", b">Acme Bank Limited<")
    assert parse(body).company == "Acme Bank Limited" and parse(body).rows[0].entered_by == "company"


@pytest.mark.parametrize("row, check", [({"v": "5", "unit": "USD"}, "value"), ({"v": "abc"}, "value"), ({"v": "5", "who": ""}, "incomplete")])
def test_an_amount_that_is_not_rupees_or_not_a_number_is_a_blocking_value_error(row, check):
    row = {"who": "Beta", **row}
    body = instance([row]).replace(b'unitRef="USD"', b'unitRef="USD"')
    if row["v"] == "abc":
        body = body.replace(b">abc<", b">abc<")
    with pytest.raises(rp.RptError) as e:
        parse(body)
    assert e.value.check == check


def test_a_negative_amount_is_stored_signed_and_counted():
    p = parse(instance([{"who": "A", "v": "10"}, {"who": "B", "v": "-3"},
                        {"who": "C", "v": "4", "type": "Loan"}, {"who": "D", "v": "-1", "type": "Any other transaction"},
                        {"who": "E", "v": "6", "type": "Purchase of goods or services"}]))
    assert [(r.counterparty, r.value) for r in p.rows] == [("A", Decimal(10)), ("B", Decimal(-3)), ("C", Decimal(4)),
                                                           ("D", Decimal(-1)), ("E", Decimal(6))]
    assert p.negatives == {"Sale of goods or services": 1, "Any other transaction": 1}


def test_a_file_of_the_wrong_basis_or_period_is_refused():
    body = instance([{"who": "A", "v": "1"}])
    with pytest.raises(rp.RptError) as e:
        parse(body, basis="consolidated")
    assert e.value.check == "basis"
    with pytest.raises(rp.RptError) as e:
        parse(body, period_end=D(2025, 12, 31))
    assert e.value.check == "period"


# ---------------------------------------------------------------------------------------------- the saved filings
@needs_fixtures
def test_mm_h1_fy26_gives_1953_rows_636_of_them_its_own_with_the_half_year_as_the_period():
    p = mm_h1()
    assert (len(p.rows), sum(r.entered_by == "company" for r in p.rows)) == (1953, 636)
    assert (p.period_start, p.period_end) == (D(2025, 4, 1), D(2025, 9, 30)) and p.company == "Mahindra & Mahindra Limited"
    sale, purchase = p.rows[16], p.rows[71]                                          # Sr. No. 17 and 72 of M&M's own table
    assert (sale.line_no, sale.counterparty, sale.txn_type, sale.value, sale.entered_by) == (
        17, "MAHINDRA ELECTRIC AUTOMOBILE LIMITED", "Sale of goods or services", Decimal(61793800000), "company")   # 6,179.38 crore
    assert (purchase.line_no, purchase.counterparty, purchase.txn_type, purchase.value) == (
        72, "MAHINDRA LOGISTICS LIMITED", "Purchase of goods or services", Decimal(16217300000))                    # 1,621.73 crore
    assert p.negatives == {}


@needs_fixtures
def test_mm_h1_values_are_the_duration_facts_only_and_every_row_is_a_distinct_member():
    inst = xbrl.parse(raw(MM_H1_SA))
    durations = [r for r in xbrl.typed_rows(inst, rp.RPT_AXIS) if r.period[0] == "duration"]
    p = mm_h1()
    assert sum(r.value for r in p.rows) == sum(r.facts[rp.AMOUNT].value for r in durations)
    assert len({r.line_no for r in p.rows}) == len(p.rows)


@needs_fixtures
def test_maruti_h1_fy26_gives_249_rows_205_of_them_its_own_and_the_consolidated_filing_has_none():
    p = rp.parse_filing(raw(MARUTI_H1_SA), rp.Filing("standalone", D(2025, 9, 30), D(2025, 4, 1), D(2026, 3, 31), False))
    assert (len(p.rows), sum(r.entered_by == "company" for r in p.rows)) == (249, 205)


@needs_fixtures
def test_no_stored_text_in_the_saved_filings_holds_a_pan():
    for p in (mm_h1(), rp.parse_filing(raw(MARUTI_H1_SA), rp.Filing("standalone", D(2025, 9, 30), D(2025, 4, 1), D(2026, 3, 31), False))):
        for r in p.rows:
            assert not any(PAN.search(t) for t in (r.counterparty, r.relationship or "", r.txn_type, r.entering_party))


@needs_fixtures
def test_mm_q4_fy26_has_negative_rows_that_are_counted_and_stored_signed():
    inst = xbrl.parse(raw(MM_Q4_SA))
    durations = [r for r in xbrl.typed_rows(inst, rp.RPT_AXIS) if r.period[0] == "duration"]
    negative = [r for r in durations if r.facts[rp.AMOUNT].value < 0]
    assert negative                                                                   # the saved filing does carry reversals
    p = mm_q4("standalone")
    assert sum(p.negatives.values()) == len(negative) and sum(r.value < 0 for r in p.rows) == len(negative)
    assert len(p.rows) == len(durations)
    assert (p.period_start, p.period_end) == (D(2025, 10, 1), D(2026, 3, 31))


@needs_fixtures
def test_mm_segments_come_from_the_consolidated_year_and_tie_to_revenue():
    s = mm_q4().segments
    assert s is not None and s.held is None
    assert [(r.description, r.revenue) for r in s.rows][:2] == [("Automotive", Decimal(1178341300000)),
                                                              ("Farm Equipment", Decimal(425683900000))]
    assert len(s.rows) == 4 and all(r.reporting == "multi" for r in s.rows)
    # the segments less the eliminated inter-segment revenue are the filing's revenue from operations
    assert sum(r.revenue for r in s.rows) - Decimal(54622200000) == Decimal(1986385500000)
    assert mm_q4("standalone").segments is None                                        # only the consolidated filing is read


@needs_fixtures
def test_a_single_segment_filer_gets_one_single_row_with_its_filed_description_and_no_revenue():
    s = rp.parse_filing(raw(MARUTI_Q4_CO), rp.Filing("consolidated", D(2026, 3, 31), D(2025, 4, 1), D(2026, 3, 31), True)).segments
    assert [(r.reporting, r.revenue) for r in s.rows] == [("single", None)]
    assert s.rows[0].description.startswith("The group is primarily in the business of manufacturing")


@needs_fixtures
@pytest.mark.parametrize("sym, ok, why", [
    ("RELIANCE", False, "differ from RevenueFromOperations by +9."),
    ("HINDUNILVR", False, "differ from RevenueFromOperations by +1.95%"),
    ("HDFCBANK", True, "tie to Income"),
    ("ETERNAL", True, "inter-segment revenue is not in them"),
    ("HDFCLIFE", False, "no segment disclosure"),
])
def test_segments_that_do_not_tie_are_held_with_the_gap_and_a_bank_ties_to_income(sym, ok, why):
    import glob
    hits = []
    for p in sorted(glob.glob(str(XBRL / sym / "INTEGRATED*"))):
        with gzip.open(p) as f:
            body = f.read()
        i = xbrl.parse(body)
        if i.header.get("period_end") == "2026-03-31" and i.basis == "consolidated":
            hits.append(body)
    if not hits:
        pytest.skip(f"{sym} has no consolidated Q4 FY26 file saved")
    s = rp.parse_filing(hits[0], rp.Filing("consolidated", D(2026, 3, 31), D(2025, 4, 1), D(2026, 3, 31), True)).segments
    assert (s.held is None) is ok and why in (s.note if ok else s.held)


# ---------------------------------------------------------------------------------------------- wiring
def test_the_task_is_wired_in_tasks_yaml():
    t = config.load()["equity.related_parties"]
    assert t.rules == {"weekly": "every 6 days"} and t.run_after == ("equity.filings",)
    assert t.overdue_after.days == 10 and t.uses_nse is False
    assert registry()["equity.related_parties"] is EquityRelatedParties


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5601, "equity-related-parties-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_related_parties"
NOW = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity related parties test', '{weekly}', '6 days', '10 days', false)
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
        cur = c.execute(sql, a or None)
        return cur.fetchall() if cur.description else []


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zr-%')"
        evs = f"(select id from core.event where entity_id in {ids})"
        c.execute(f"delete from equity.rpt_line where results_event_id in {evs}")
        c.execute(f"delete from equity.segment_revenue where results_event_id in {evs}")
        c.execute(f"delete from equity.filing where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zr-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))
        c.execute("delete from core.raw_file where source_url like 'https://example.test/%' and media_type = 'application/xml'")


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def task() -> EquityRelatedParties:
    class T(EquityRelatedParties):
        key = KEY
    t = T()
    t.workflows = ("weekly", "refresh")
    return t


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"equity.related_parties fetched {request.url}")


def go():
    http = HttpClient(transport=httpx.MockTransport(refuse), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "refresh", [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: NOW)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1""", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


# (symbol, slug, isin, file, basis, period_end, fy_start, fy_end, filed_at)
FILES = [
    ("M&M", "zr-mm", "INE101A01026", MM_H1_SA, "standalone", D(2025, 9, 30), "2025-11-04T07:57:12+00:00"),
    ("M&M", "zr-mm", "INE101A01026", MM_H1_CO, "consolidated", D(2025, 9, 30), "2025-11-04T07:58:24+00:00"),
    ("M&M", "zr-mm", "INE101A01026", MM_Q4_SA, "standalone", D(2026, 3, 31), "2026-05-05T04:27:38+00:00"),
    ("M&M", "zr-mm", "INE101A01026", MM_Q4_CO, "consolidated", D(2026, 3, 31), "2026-05-05T04:28:13+00:00"),
    ("MARUTI", "zr-maruti", "INE585B01010", MARUTI_H1_SA, "standalone", D(2025, 9, 30), "2025-10-31T08:08:37+00:00"),
    ("MARUTI", "zr-maruti", "INE585B01010", MARUTI_Q4_SA, "standalone", D(2026, 3, 31), "2026-04-28T09:27:49+00:00"),
    ("MARUTI", "zr-maruti", "INE585B01010", MARUTI_Q4_CO, "consolidated", D(2026, 3, 31), "2026-04-28T07:47:40+00:00"),
]


def seed(files=FILES, edit=None):
    """Two companies, their results announcements, documents and stored instances, as 2A's equity.filings leaves them."""
    go()                                            # a task run to hang the raw files on
    st = storage()
    ids = {}
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for sym, slug, isin, name, basis, pe, filed in files:
            if slug not in ids:
                eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                                   values ('company', %s, %s, 'IN', 'INR', false) returning id""", (slug, f"{sym} test")).fetchone()[0]
                c.execute("insert into equity.company (entity_id, isin, nse_symbol, fy_end_month) values (%s, %s, %s, 3)", (eid, isin, sym))
                ids[slug] = eid
            body = raw(name) if edit is None else edit(name, raw(name))
            put = st.put(body, source="nse", media_type="application/xml", ext="xml")
            rf = c.execute("""insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
                              values (%s, (select id from core.source where key = 'nse'), %s, %s, %s, 'application/xml', now(),
                                      (select max(id) from ops.task_run)) returning id""",
                           (put.object_key, "https://example.test/" + name, put.sha256, put.bytes)).fetchone()[0]
            ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
                              values ('results_filing', %s, %s, 'results', %s, %s, 'filed', (select id from core.source where key = 'nse'), true)
                              on conflict (event_type, slug) do update set updated_at = now() returning id""",
                           (ids[slug], f"{slug}-results-{pe.isoformat()}", pe, filed)).fetchone()[0]
            doc = c.execute("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, raw_file_id,
                                                          published_at)
                               values ('results_xbrl', %s, %s, 'xbrl', (select id from core.source where key = 'nse'), %s, %s, %s, %s)
                               returning id""", (ids[slug], ev, name.rsplit("/", 1)[-1], "https://example.test/" + name, rf, filed)).fetchone()[0]
            fy_start = D(pe.year - (1 if pe.month <= 3 else 0), 4, 1)
            c.execute("""insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end, filed_at,
                                                    source_url, exchange, parse_status)
                         values (%s, %s, %s, %s, 'ind_as', %s, %s, %s, %s, %s, 'NSE', 'pending')""",
                      (doc, ids[slug], ev, basis, pe, fy_start, D(fy_start.year + 1, 3, 31), filed, "https://example.test/" + name))


def stored_counts():
    return q("""select e.slug, f.basis, f.period_end, count(*), count(*) filter (where r.entered_by = 'company')
                  from equity.rpt_line r join core.event ev on ev.id = r.results_event_id
                  join core.entity e on e.id = ev.entity_id
                  join equity.filing f on f.event_id = ev.id and f.basis = r.basis
                 where e.slug like 'zr-%%' group by 1, 2, 3 order by 1, 2, 3""")


@pytest.mark.db
@needs_fixtures
def test_a_refresh_stores_the_latest_years_rows_and_segments(s3):
    seed()
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr[3]
    got = {(s, b, pe): (n, own) for s, b, pe, n, own in stored_counts()}
    assert got[("zr-mm", "standalone", D(2025, 9, 30))] == (1953, 636)
    assert got[("zr-maruti", "standalone", D(2025, 9, 30))] == (249, 205)
    period = q("""select distinct r.period_start, r.period_end from equity.rpt_line r join core.event ev on ev.id = r.results_event_id
                   where ev.slug = 'zr-mm-results-2025-09-30'""")
    assert period == [(D(2025, 4, 1), D(2025, 9, 30))]
    segs = q("""select ev.slug, s.reporting, s.description, s.revenue, s.period_start, s.period_end from equity.segment_revenue s
                  join core.event ev on ev.id = s.results_event_id order by 1, s.line_no""")
    assert [(r[0], r[2], r[3]) for r in segs if r[0] == "zr-mm-results-2026-03-31"][:1] == [
        ("zr-mm-results-2026-03-31", "Automotive", Decimal(1178341300000))]
    assert len([r for r in segs if r[0] == "zr-mm-results-2026-03-31"]) == 4 and segs[0][5] == D(2026, 3, 31)
    assert [(r[1], r[3]) for r in segs if r[0] == "zr-maruti-results-2026-03-31"] == [("single", None)]
    c = checks(tr)
    assert c["values"]["passed"] and c["one_fiscal_year"]["passed"]
    assert "zr-mm" in c["negative_amounts"]["detail"]                                 # M&M's Q4 reversals are named and stored signed
    assert c["parsed"]["passed"]


@pytest.mark.db
@needs_fixtures
def test_a_second_run_writes_nothing_and_a_changed_row_is_replaced(s3):
    seed()
    go()
    before = (stored_counts(), q("select count(*), sum(value_in_period) from equity.rpt_line where results_event_id in "
                                 "(select id from core.event where slug like 'zr-%%')"))
    code, tr = go()
    assert code == 0 and tr[1] == 0
    assert (stored_counts(), q("select count(*), sum(value_in_period) from equity.rpt_line where results_event_id in "
                               "(select id from core.event where slug like 'zr-%%')")) == before
    q("update equity.rpt_line set value_in_period = value_in_period + 1 where results_event_id = "
      "(select id from core.event where slug = 'zr-maruti-results-2025-09-30') and line_no = 1")
    code, tr = go()
    assert code == 0 and tr[1] == 249                                                 # the filing's rows are written again
    assert q("select count(*), sum(value_in_period) from equity.rpt_line where results_event_id in "
             "(select id from core.event where slug like 'zr-%%')") == before[1]


@pytest.mark.db
@needs_fixtures
def test_no_stored_text_column_holds_a_pan(s3):
    seed()
    go()
    rows = q("""select counterparty, relationship, txn_type, entering_party from equity.rpt_line r
                 where r.results_event_id in (select id from core.event where slug like 'zr-%%')""")
    assert len(rows) > 2000 and not any(PAN.search(v or "") for row in rows for v in row)


@pytest.mark.db
@needs_fixtures
def test_a_filing_where_no_row_names_the_company_is_held_named_and_stores_nothing(s3):
    def edit(name, body):
        return body.replace(b"MARUTI SUZUKI INDIA LIMITED", b"SOMEONE ELSE LIMITED") if name == MARUTI_H1_SA else body
    seed(edit=edit)
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr[3]
    assert ("zr-maruti", "standalone", D(2025, 9, 30)) not in {(s, b, pe) for s, b, pe, *_ in stored_counts()}
    d = checks(tr)["unmatched_company"]
    assert d["passed"] is False and "zr-maruti" in d["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_superseded_announcement_is_not_read(s3):
    seed()
    q("update core.event set status = 'superseded' where slug = 'zr-maruti-results-2025-09-30'")
    code, tr = go()
    assert code == 0
    assert ("zr-maruti", "standalone", D(2025, 9, 30)) not in {(s, b, pe) for s, b, pe, *_ in stored_counts()}
    assert "zr-maruti" in checks(tr)["no_rows"]["detail"]                              # one half-year only


def test_the_production_guard_holds_a_scheduled_first_run_and_a_missing_table():
    from ft.jobs.due import Due

    class Db:
        def __init__(self, present, ran):
            self.present, self.ran = present, ran

        def execute(self, sql, *a):
            outer = self

            class R:
                def fetchone(self_):
                    return (outer.present if "to_regclass" in sql else outer.ran,)
            return R()

    t = EquityRelatedParties()
    assert t.requires == "equity.rpt_line"
    from ft.equity.guard import equity_due
    assert not equity_due(Db(False, False), t.key, "weekly", Due(True, ""), requires=t.requires).due
    assert not equity_due(Db(True, False), t.key, "weekly", Due(True, ""), requires=t.requires).due
    assert equity_due(Db(True, False), t.key, "refresh", Due(True, ""), requires=t.requires).due


def test_load_sends_its_writes_in_pipelined_batches_not_one_round_trip_per_row():
    """Production's first run (11 Nov 2026, #190) sent one round trip per row from a US runner to Mumbai and passed the
    workflow's 30 minutes. The reads stay one per filing; the writes go as pipelined `executemany` batches, deletes first."""
    from ft.jobs.tasks.equity_related_parties import Fetched, Item

    class Cur:
        def __init__(self, tx):
            self.tx = tx

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def executemany(self, sql, rows):
            self.tx.batches.append((sql.split()[0], len(list(rows))))

    class Res:
        def fetchall(self):
            return []

    class Tx:
        def __init__(self):
            self.executes, self.batches = [], []

        def execute(self, sql, params=None):
            self.executes.append(sql.split()[0])
            return Res()

        def cursor(self):
            return Cur(self)

    def item(i, basis):
        rows = [rp.RptRow(n, f"C{n}", None, "Sale of goods or services", "Co", "company", Decimal(n)) for n in range(1, 301)]
        parsed = rp.Parsed(company="Co", basis=basis, period_start=D(2025, 10, 1), period_end=D(2026, 3, 31),
                           fy_end=D(2026, 3, 31), rows=rows,
                           segments=rp.Segments([rp.SegRow(1, "multi", "S", Decimal(5))]))
        filing = rp.Filing(basis, D(2026, 3, 31), D(2025, 4, 1), D(2026, 3, 31), True)
        return Item(i, i, f"zr-{i}", 100 + i, filing, D(2026, 3, 31), parsed)

    tx = Tx()
    n = EquityRelatedParties().load(tx, Fetched(items=[item(1, "standalone"), item(2, "consolidated")]), None)
    assert n == 601                                                 # 300 + 300 related-party rows and the one segment row
    assert tx.executes == ["select", "select", "select"]            # reads only: two filings, and the consolidated year-end's segments
    assert tx.batches == [("delete", 2), ("delete", 1), ("insert", 600), ("insert", 1)]
