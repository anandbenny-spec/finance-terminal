"""equity.brsr (job VC-04, #189): the BRSR reader's rules on the saved filings, the listing parsers, and the task run through the
runner on mock replies built from the audit's listing summary (`w1_listing.json`: W1 did not save the raw list replies, so the
mock builds replies in the documented shapes, Data Audit 08 §3.2, from the real rows' values).

The files are in data/fixtures/equity/brsr (the private fixtures repo; the tests skip without it, and CI checks it out):
`nse/<SYMBOL>/`, `bse/<SYMBOL>/` and `audit/` (W1's values and the listing summary). No NSE data is kept in this repo
(CLAUDE.md §4)."""
import gzip
import json
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

from ft.equity import brsr as br
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks.equity_brsr import EquityBrsr

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "brsr"
needs_fixtures = pytest.mark.skipif(not (FIX / "audit" / "w1_listing.json").exists(),
                                    reason="the saved files aren't checked out (data/fixtures)")
D = date
CFG = br.load_config()


def unzipped(path: Path) -> bytes:
    with gzip.open(path) as f:
        return f.read()


def find(name: str) -> Path | None:
    """A saved file by the name the exchange served (without .gz), under nse/ or bse/."""
    for p in FIX.glob(f"*/*/{name}.gz"):
        return p
    return None


def read(rel: str, company=None, fy=(None, None)):
    cid = company or br.CompanyId("x", frozenset(), None)
    return br.read_filing(unzipped(FIX / rel), cid, fy[0], fy[1], CFG)


def values(f: br.Filing) -> dict[str, Decimal]:
    return {v.key: v.value for v in f.values if v.status == "ok"}


MM = br.CompanyId("Mahindra & Mahindra Limited", frozenset({"M&M"}), "500520")
MM23 = "bse/M&M/BRSR_500520_2962024195928.xml.gz"           # FY 2023-24: NSE answers 404 (W1.3), BSE has the only copy
MM24_NSE, MM24_BSE = "nse/M&M/BRSR_1474151_28062025114611_WEB.xml.gz", "bse/M&M/BRSR_500520_286202523432.xml.gz"


# ---------------------------------------------------------------------------------------------- the reader
@needs_fixtures
def test_the_known_case_mm_fy2023_24_gives_the_w1_6_values():
    f = read(MM23, MM, (2023, 2024))
    v = values(f)
    assert (v["dealer_share"], v["dealers"], v["top10_dealer_share"], v["within_india_share"], v["msme_share"]) == (
        Decimal("0.7933"), Decimal(740), Decimal("0.1296"), Decimal("0.84"), Decimal("0.10"))
    assert f.identity_ok and f.boundary == "standalone" and f.months == 12 and f.entry_point == D(2024, 4, 30)
    assert (f.openness_assured, f.openness_assurance) == (True, "reasonable")
    assert br.fy_label(f.fy_start, f.fy_end) == "FY 2023-24" and f.format_status == "shown"
    assert len(f.products) == 2 and len(f.group) == 14 and f.products[0].share is not None


@needs_fixtures
def test_every_value_of_the_w1_sample_equals_the_file_filed(tmp_path):
    w1 = json.loads((FIX / "audit" / "W1_latest.json").read_text())["info"]["values_sample"]
    checked = 0
    for p in sorted(FIX.glob("*/*/*.xml.gz")):
        f = br.read_filing(unzipped(p), br.CompanyId("x", frozenset(), None), None, None, CFG)
        sym = p.parent.name
        expect = w1.get(f"{sym} {f.fy_start.year}-{f.fy_end.year}")
        if not expect or f.format_status != "shown":
            continue
        got = {k: v for k, v in values(f).items()}
        for k, v in expect["current"].items():
            if k in {s.key for s in CFG.fields}:
                assert got.get(k) == Decimal(str(v)), (p.name, k)
        checked += 1
    assert checked >= 12


@needs_fixtures
def test_both_exchanges_copies_of_a_sample_company_year_give_identical_values_field_by_field():
    pairs = 0
    for d in sorted((FIX / "bse").iterdir()):
        mine = {}
        for p in list(d.glob("*.gz")) + list((FIX / "nse" / d.name).glob("*.gz")):
            f = br.read_filing(unzipped(p), br.CompanyId("x", frozenset(), None), None, None, CFG)
            mine.setdefault((f.fy_start, f.fy_end), []).append(({v.key: (v.value, v.status) for v in f.values}, f.boundary))
        for copies in mine.values():
            if len(copies) == 2:
                assert copies[0] == copies[1], d.name
                pairs += 1
    assert pairs >= 20                                  # W1.7: 23 pairs


@needs_fixtures
def test_restatements_follow_the_half_point_rule_for_shares_and_any_change_for_counts():
    mm23 = read(MM23, MM, (2023, 2024))
    mm24 = read(MM24_NSE, MM, (2024, 2025))
    cur = values(mm23)
    assert mm24.prior["rpt_purchases_share"] == Decimal("0.1218") and cur["rpt_purchases_share"] == Decimal("0.1505")
    assert br.is_restated("share", cur["rpt_purchases_share"], mm24.prior["rpt_purchases_share"])          # 15.05% to 12.18%
    assert not br.is_restated("share", cur["rpt_sales_share"], mm24.prior["rpt_sales_share"])               # 6.11% to 6.02%: 0.09 pp
    assert br.is_restated("count", Decimal(740), Decimal(741)) and not br.is_restated("count", Decimal(740), Decimal(740))
    assert not br.is_restated("share", Decimal("0.500"), Decimal("0.505")) and br.is_restated("share", Decimal("0.5"), Decimal("0.5051"))
    infy24 = read("nse/INFY/BRSR_1477079_03072025095250_WEB.xml.gz")
    infy25 = next(p for p in (FIX / "bse" / "INFY").glob("*.gz")
                  if br.read_filing(unzipped(p), br.CompanyId("x", frozenset(), None), None, None, CFG).fy_end.year == 2026)
    nxt = br.read_filing(unzipped(infy25), br.CompanyId("x", frozenset(), None), None, None, CFG)
    assert br.is_restated("share", values(infy24)["msme_share"], nxt.prior["msme_share"])                   # 9.7% to 37.98%
    maruti24 = read("nse/MARUTI/BRSR_1503469_05082025092147_WEB.xml.gz")
    maruti25 = read("nse/MARUTI/BRSR_2143_WebXMLFile_20260808_220104161.xml.gz")
    assert br.is_restated("share", values(maruti24)["rpt_purchases_share"], maruti25.prior["rpt_purchases_share"])   # 45.67% to 16.94%


@needs_fixtures
def test_coal_indias_msme_share_filed_as_59_62_is_held_never_rescaled():
    f = read("nse/COALINDIA/BRSR_1501398_04082025110417_WEB.xml.gz")
    (v,) = [v for v in f.values if v.key == "msme_share"]
    assert (v.status, v.value, v.held_reason) == ("held", Decimal("59.62"), "outside 0–100% as filed")


@needs_fixtures
def test_nestles_fifteen_month_year_is_recorded_with_its_length():
    f = read("nse/NESTLEIND/BRSR_1158818_15062024115331_WEB.xml.gz", br.CompanyId("Nestle India Limited", frozenset({"NESTLEIND"}), "500790"), (2023, 2024))
    assert f.months == 15 and f.identity_ok and br.fy_label(f.fy_start, f.fy_end) == "FY 2023-24"


@needs_fixtures
def test_an_old_format_filing_is_stored_not_shown_and_carries_no_values():
    f = read("nse/M&M/BRSR_872798_03072023012828_WEB.xml.gz", MM, (2022, 2023))
    assert f.format_status == "not_shown" and f.values == [] and f.entry_point < D(2024, 4, 30)


@needs_fixtures
def test_tata_consumers_boundary_change_is_recorded_per_year():
    tc = br.CompanyId("Tata Consumer Products Limited", frozenset({"TATACONSUM"}), "500800")
    a = read("nse/TATACONSUM/BRSR_1133300_22052024032848_WEB.xml.gz", tc, (2023, 2024))
    b = read("nse/TATACONSUM/BRSR_1473177_27062025043658_WEB.xml.gz", tc, (2024, 2025))
    assert a.boundary in ("standalone", "consolidated") and b.boundary in ("standalone", "consolidated")
    assert {a.boundary, b.boundary} == {"standalone", "consolidated"}


@needs_fixtures
def test_the_zomato_era_files_belong_to_eternal_through_its_symbol_history_and_the_listings_name():
    eternal = br.CompanyId("Eternal Limited", frozenset({"ETERNAL", "ZOMATO"}), "543320", "Zomato Limited")
    f = read("nse/ETERNAL/BRSR_1212062_03082024103834_WEB.xml.gz", eternal, (2023, 2024))
    assert f.identity_ok
    other = br.CompanyId("Eternal Limited", frozenset({"ETERNAL"}), "543320")        # no scrip match in the file, no shared word
    assert not read("nse/ETERNAL/BRSR_1212062_03082024103834_WEB.xml.gz", other, (2023, 2024)).identity_ok


@needs_fixtures
def test_identity_fails_for_another_company_or_another_year():
    hdfc = read("nse/HDFCBANK/BRSR_1192809_18072024070337_WEB.xml.gz", MM, (2023, 2024))
    assert not hdfc.identity_ok and "matches nothing" in hdfc.identity_note
    wrong_year = read(MM23, MM, (2024, 2025))
    assert not wrong_year.identity_ok and "not the listing's 2024-2025" in wrong_year.identity_note
    new = read("nse/M&M/BRSR_500520_472026182143_BRSR_WebXMLFile_20260704_182159464.xml.gz",
               br.CompanyId("Mahindra & Mahindra Limited", frozenset({"OTHER"}), "999999"), (2025, 2026))
    assert not new.identity_ok and "NSESymbol" in new.identity_note                       # FY 2025-26 carries the symbol


@needs_fixtures
def test_two_different_values_for_one_element_hold_that_field():
    body = unzipped(FIX / MM23)
    view_ctx = re.search(rb'<in-capmkt:PercentageOfDirectlySourcedFromMSMEsOrSmallProducers contextRef="(\w+)"', body).group(1)
    extra = (b'<in-capmkt:PercentageOfDirectlySourcedFromMSMEsOrSmallProducers contextRef="' + view_ctx +
             b'" decimals="4" unitRef="pure">0.55</in-capmkt:PercentageOfDirectlySourcedFromMSMEsOrSmallProducers></xbrli:xbrl>')
    f = br.read_filing(body.replace(b"</xbrli:xbrl>", extra), MM, 2023, 2024, CFG)
    (v,) = [v for v in f.values if v.key == "msme_share"]
    assert v.status == "held" and "two different values" in v.held_reason


@needs_fixtures
def test_an_unknown_entry_point_holds_every_value_and_a_file_that_is_not_a_brsr_is_refused():
    body = unzipped(FIX / MM23).replace(b"in-capmkt-ent-2024-04-30.xsd", b"in-capmkt-ent-2031-01-01.xsd")
    f = br.read_filing(body, MM, 2023, 2024, CFG)
    assert f.format_status == "unknown" and f.values and all(v.status == "held" for v in f.values)
    for bad in (b"<html><body>Access Denied</body></html>", b"not xml", b"<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance'/>"):
        with pytest.raises(br.BrsrError):
            br.read_filing(bad, MM, 2023, 2024, CFG)


def test_the_field_list_is_the_cards_13_fields_valid_from_the_2024_format():
    assert len(CFG.fields) == 13 and {f.valid_from for f in CFG.fields} == {D(2024, 4, 30)}
    assert D(2026, 2, 28) in CFG.shown and D(2021, 9, 30) in CFG.not_shown


# ---------------------------------------------------------------------------------------------- the listings
def test_nse_rows_are_read_to_a_year_a_time_and_a_link_and_a_row_without_a_link_is_counted():
    rows = [{"symbol": "X", "companyName": "X Ltd", "fyFrom": "2025", "fyTo": "2026", "submissionDate": "04-Jul-2026",
             "revisionDate": "-", "xbrlFile": "https://nsearchives.nseindia.com/corporate/xbrl/B.xml"},
            {"symbol": "X", "fyFrom": 2024, "fyTo": 2025, "submissionDate": "28-Jun-2025 11:46:11", "revisionDate": "30-Jun-2025",
             "xbrlFile": "https://nsearchives.nseindia.com/corporate/xbrl/A.xml"},
            {"symbol": "X", "fyFrom": 2023, "fyTo": 2024, "submissionDate": "29-Jun-2024", "xbrlFile": "-"},
            {"symbol": "X", "fyFrom": "x", "fyTo": 2024, "submissionDate": "29-Jun-2024", "xbrlFile": "https://h/C.xml"},
            {"symbol": "X", "fyFrom": 2022, "fyTo": 2023, "submissionDate": "soon", "xbrlFile": "https://h/D.xml"}]
    lst = br.parse_nse(rows)
    assert [(f.fy_from, f.file_name) for f in lst.filings] == [(2025, "B.xml"), (2024, "A.xml")]
    assert lst.filings[0].filed == datetime(2026, 7, 4, tzinfo=br.INDIA) and lst.filings[0].revised is None
    assert lst.filings[1].revised == datetime(2025, 6, 30, tzinfo=br.INDIA) and lst.filings[1].stamp == lst.filings[1].revised
    assert lst.no_link == 1 and len(lst.unreadable) == 2


def test_bse_versions_are_kept_and_the_latest_by_time_is_taken_per_year():
    row = {"yr": "2023 - 2024", "status": "New", "filing_date_time": "2024-06-29T19:59:00", "revised_date_time": None,
           "Fld_ResubReason": None, "xbrlurl": "/XBRLFILES/BRSRUploadDocument/A.xml"}
    rev = {**row, "status": "Revised", "revised_date_time": "2024-06-29T20:01:39.973", "Fld_ResubReason": "upload error",
           "xbrlurl": "/XBRLFILES/BRSRUploadDocument/B.xml"}
    older = {**row, "yr": "2022 - 2023", "xbrlurl": "/XBRLFILES/BRSRUploadDocument/C.html"}
    lst = br.parse_bse([row, rev, older, {**row, "xbrlurl": "/x/-"}, {**row, "yr": "never"}])
    assert len(lst.filings) == 3 and lst.no_link == 1 and len(lst.unreadable) == 1
    best = br.latest_by_year(lst.filings)
    assert best[2023].file_name == "B.xml" and best[2023].reason == "upload error" and best[2023].url.startswith("https://www.bseindia.com/")
    assert br.xml_variants("https://h/p/A.html") == ["https://h/p/A.xml"] and br.xml_variants("https://h/p/A.xml") == ["https://h/p/A.xml"]


def test_an_event_slug_names_the_company_and_year_and_a_revision_ends_r2():
    assert br.event_slug("mahindra", D(2025, 4, 1), D(2026, 3, 31), 1) == "mahindra-brsr-fy2025-26"
    assert br.event_slug("nestle-india", D(2023, 1, 1), D(2024, 3, 31), 2) == "nestle-india-brsr-fy2023-24-r2"


# ---------------------------------------------------------------------------------------------- the task
ADMIN, JOB = dsn("postgres"), dsn("job_writer")
PORT, BUCKET = 5597, "equity-brsr-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_brsr"
NOW = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)
NSE_FILES, BSE_FILES = "https://nsearchives.nseindia.com/corporate/xbrl/", "https://www.bseindia.com/XBRLFILES/BRSRUploadDocument/"


def listing() -> dict:
    return json.loads((FIX / "audit" / "w1_listing.json").read_text())


def nse_row(symbol: str, r: dict) -> dict:
    return {"symbol": symbol, "companyName": r["company"], "fyFrom": str(r["fy_from"]), "fyTo": str(r["fy_to"]),
            "submissionDate": r["submitted"], "revisionDate": r["revised"] or "-", "xbrlFile": r["xbrl"]}


def bse_row(r: dict) -> dict:
    rel = r["xbrl"].removeprefix("https://www.bseindia.com")
    return {"yr": f"{r['fy_from']} - {r['fy_to']}", "status": r["status"], "filing_date_time": r["filed"],
            "revised_date_time": r["revised"], "Fld_ResubReason": r["reason"], "xbrlurl": rel, "AttachmentName": r["pdf"]}


class Exchanges:
    """NSE's list API, BSE's list API and both file servers as a mock transport. `files` overrides a file name -> bytes (None: 404);
    `nse_rows` / `bse_rows` append rows (symbol or scrip -> [row]); `fail_lists` makes the named list answer 500; `seen` records requests."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.files: dict[str, bytes | None] = {}
        self.nse_rows: dict[str, list[dict]] = {}
        self.bse_rows: dict[str, list[dict]] = {}
        self.fail_lists: set[str] = set()
        self.drop_nse: set[str] = set()                 # file names NSE's list leaves out
        self.w1 = listing()

    def nse_list(self, symbol: str) -> list[dict]:
        out = []
        for s, years in self.w1["nse"].items():
            for r in years.values():
                if (r.get("queried_as") or s) == symbol and r["xbrl"].rsplit("/", 1)[-1] not in self.drop_nse:
                    out.append(nse_row(symbol, r))
        return out + self.nse_rows.get(symbol, [])

    def bse_list(self, scrip: str) -> list[dict]:
        out = []
        for s, code in self.w1["scrip"].items():
            if code == scrip:
                out += [bse_row(r) for r in self.w1["bse_latest"].get(s, {}).values()]
        return out + self.bse_rows.get(scrip, [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path, q = request.url.path, dict(request.url.params)
        if path == "/":
            return httpx.Response(200, content=b"<html>home</html>", headers={"set-cookie": "nsit=1; Path=/"})
        if path == br.NSE_LIST_PATH:
            if q["symbol"] in self.fail_lists:
                return httpx.Response(500)
            return httpx.Response(200, json={"data": self.nse_list(q["symbol"])})
        if path.endswith("XbrlBrsrDetails/w"):
            if q["scripcode"] in self.fail_lists:
                return httpx.Response(500)
            return httpx.Response(200, json={"Table": self.bse_list(q["scripcode"])})
        name = path.rsplit("/", 1)[-1]
        if name in self.files:
            data = self.files[name]
        else:
            p = find(name)
            data = unzipped(p) if p else None
        return httpx.Response(404) if data is None else httpx.Response(200, content=data)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity brsr test', '{weekly}', '6 days', '10 days', false)
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
        ids = "(select id from core.entity where slug like 'zb-%')"
        for t in ("brsr_value", "brsr_product", "brsr_group_company"):
            c.execute(f"delete from equity.{t} where event_id in (select event_id from equity.brsr_filing where entity_id in {ids})")
        c.execute(f"delete from equity.brsr_filing where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zb-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))


COMPANIES = {"M&M": ("Mahindra & Mahindra Limited", "500520", []), "HDFCBANK": ("HDFC Bank Limited", "500180", []),
             "ETERNAL": ("Eternal Limited", "543320", ["ZOMATO"])}


def make_companies(names=tuple(COMPANIES), listed_on=None):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for n in names:
            name, scrip, older = COMPANIES[n]
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                            (f"zb-{n.lower().replace('&', '')}", name)).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, nse_symbol, bse_code, fy_end_month, listed_on)
                         values (%s, %s, %s, 3, %s)""", (eid, n, scrip, listed_on))
            for s in older:
                c.execute("""insert into core.entity_identifier (entity_id, scheme, value, valid)
                             values (%s, 'nse_symbol', %s, daterange(null, '2025-03-01'))""", (eid, s))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def task() -> EquityBrsr:
    class T(EquityBrsr):
        key = KEY
    t = T()
    t.workflows = ("weekly", "backfill")
    return t


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
    return q("""select e.slug, f.fy_label, f.exchange, f.version_no, ev.slug, ev.status, f.boundary, f.months, f.status_reason,
                       d.external_key, f.format_version, f.openness_assurance
                  from equity.brsr_filing f join core.event ev on ev.id = f.event_id join core.document d on d.id = f.document_id
                  join core.entity e on e.id = f.entity_id where e.slug like 'zb-%%' order by 1, f.fy_start, f.version_no""")


def card(slug: str, fy: str, version: int = 1) -> dict:
    return {k: (v, s, r, n) for k, v, s, r, n in q(
        """select v.field_key, v.value, v.status, v.restated, v.next_comparative
             from equity.brsr_value v join equity.brsr_filing f on f.event_id = v.event_id
             join core.entity e on e.id = f.entity_id where e.slug = %s and f.fy_label = %s and f.version_no = %s""",
        slug, fy, version)}


@pytest.mark.db
@needs_fixtures
def test_a_backfill_registers_each_year_from_nse_with_bse_after_nses_404_and_the_zomato_era_on_eternal(s3):
    make_companies()
    code, tr = go(Exchanges(), since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[0] == "success", tr[3]
    by = {(r[0], r[1]): r for r in filings()}
    # M&M: FY 2023-24 only on BSE (NSE answers 404), then NSE for FY 2024-25 and FY 2025-26
    assert by[("zb-mm", "FY 2023-24")][2] == "BSE" and by[("zb-mm", "FY 2024-25")][2] == "NSE" and by[("zb-mm", "FY 2025-26")][2] == "NSE"
    assert by[("zb-mm", "FY 2023-24")][4] == "zb-mm-brsr-fy2023-24" and by[("zb-mm", "FY 2023-24")][5] == "stored"
    assert by[("zb-mm", "FY 2023-24")][9] == "BRSR_500520_2962024195928.xml" and by[("zb-mm", "FY 2023-24")][10] == D(2024, 4, 30)
    c = checks(tr)
    assert c["fallbacks"]["passed"] is False and "zb-mm FY 2023-24: BSE (NSE HTTP 404)" in c["fallbacks"]["detail"]
    assert c["nse_answered"]["passed"] is True
    # the known case, exactly as filed
    v = card("zb-mm", "FY 2023-24")
    assert (v["dealer_share"][0], v["dealers"][0], v["top10_dealer_share"][0], v["within_india_share"][0], v["msme_share"][0]) == (
        Decimal("0.7933"), Decimal(740), Decimal("0.1296"), Decimal("0.84"), Decimal("0.10"))
    # the restatement: FY 2024-25's comparative for FY 2023-24's related-party purchases (15.05% to 12.18%); sales 0.09 pp: not
    assert v["rpt_purchases_share"][2:] == (True, Decimal("0.1218")) and v["rpt_sales_share"][2] is False
    # the ZOMATO-era file (queried under the old symbol) lands on ETERNAL
    assert by[("zb-eternal", "FY 2023-24")][9] == "BRSR_1212062_03082024103834_WEB.xml"
    assert ("zb-hdfcbank", "FY 2025-26") in by and by[("zb-hdfcbank", "FY 2025-26")][2] == "NSE"
    # products and group companies; the assurance flag
    assert q("select count(*) from equity.brsr_product p join equity.brsr_filing f on f.event_id = p.event_id "
             "join core.entity e on e.id = f.entity_id where e.slug = 'zb-mm'")[0][0] > 0
    assert by[("zb-mm", "FY 2023-24")][11] == "reasonable"
    # documents: unpublished, without text, source = the exchange the file came from
    docs = q("""select d.doc_type, d.is_published, s.key from core.document d join core.source s on s.id = d.source_id
                 join core.entity e on e.id = d.entity_id where e.slug = 'zb-mm' and d.external_key like 'BRSR_500520_296%%'""")
    assert docs == [("brsr_xbrl", False, "bse")]


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_run_registers_nothing(s3):
    make_companies(("M&M",))
    ex = Exchanges()
    go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    before = (filings(), q("select count(*) from core.raw_file")[0][0], q("select count(*) from core.document")[0][0])
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[1] == 0
    assert (filings(), q("select count(*) from core.raw_file")[0][0], q("select count(*) from core.document")[0][0]) == before


@pytest.mark.db
@needs_fixtures
def test_a_revised_filing_is_a_new_version_the_earlier_event_is_superseded_and_a_changed_value_alerts(s3):
    make_companies(("M&M",))
    ex = Exchanges()
    go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    name = "BRSR_1474151_28062025114611_WEB.xml"
    body = unzipped(FIX / MM24_NSE)
    cur = re.search(rb'<in-capmkt:PercentageOfDirectlySourcedFromMSMEsOrSmallProducers contextRef="(\w+)"[^>]*>([^<]*)<', body)
    revised = body.replace(cur.group(0), cur.group(0).replace(b">" + cur.group(2) + b"<", b">0.5<"))
    new_name = "BRSR_1474151_30062025114611_WEB.xml"
    ex.files[new_name] = revised
    ex.drop_nse.add(name)
    ex.nse_rows["M&M"] = [{"symbol": "M&M", "companyName": "Mahindra & Mahindra Limited", "fyFrom": "2024", "fyTo": "2025",
                           "submissionDate": "28-Jun-2025", "revisionDate": "30-Jun-2025", "xbrlFile": NSE_FILES + new_name}]
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0, tr[3]
    rows = [r for r in filings() if r[1] == "FY 2024-25"]
    assert [(r[3], r[4], r[5]) for r in rows] == [(1, "zb-mm-brsr-fy2024-25", "superseded"), (2, "zb-mm-brsr-fy2024-25-r2", "stored")]
    assert card("zb-mm", "FY 2024-25", 2)["msme_share"][0] == Decimal("0.5")
    assert q("select resolved_at is null from ops.alert where dedupe_key = %s", "brsr_values_changed:zb-mm:FY 2024-25") == [(True,)]


@pytest.mark.db
@needs_fixtures
def test_nses_file_naming_another_company_falls_back_to_bse_and_a_file_wrong_on_every_route_is_stored_held(s3):
    make_companies(("M&M",))
    ex = Exchanges()
    hdfc = unzipped(FIX / "nse/HDFCBANK/BRSR_797_WebXMLFile_20260911_195404900.xml.gz")
    nse25 = "BRSR_500520_472026182143_BRSR_WebXMLFile_20260704_182159464.xml"
    ex.files[nse25] = hdfc                                           # NSE's FY 2025-26 file is another company's
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0, tr[3]
    by = {r[1]: r for r in filings()}
    assert by["FY 2025-26"][2] == "BSE" and by["FY 2025-26"][5] == "stored"
    assert "identity" in checks(tr)["fallbacks"]["detail"]
    # now wrong on BSE as well (a fresh database): held, no values, nothing shown
    wipe()
    make_companies(("M&M",))
    ex.files["BRSR_500520_472026182143.xml"] = hdfc
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    by = {r[1]: r for r in filings()}
    assert by["FY 2025-26"][5] == "held" and by["FY 2025-26"][8].startswith("identity") and card("zb-mm", "FY 2025-26") == {}
    assert checks(tr)["held_filings"]["passed"] is False


@pytest.mark.db
@needs_fixtures
def test_a_bse_html_entry_is_fetched_as_xml_and_one_with_no_xml_is_named_not_read(s3):
    make_companies(("M&M",))
    ex = Exchanges()
    ex.drop_nse.update({"BRSR_1474151_28062025114611_WEB.xml"})                # FY 2024-25 only on BSE, listed as .html
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0, tr[3]
    got = {r[1]: r for r in filings()}["FY 2024-25"]
    assert got[2] == "BSE" and got[9] == "BRSR_500520_286202523432.xml"
    assert any(str(r.url).endswith("BRSR_500520_286202523432.xml") for r in ex.seen)
    wipe()
    make_companies(("M&M",))
    ex.files["BRSR_500520_286202523432.xml"] = None
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert "FY 2024-25" not in {r[1] for r in filings()}
    assert "BSE serves no .xml for BRSR_500520_286202523432.html" in checks(tr)["files_served"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_the_run_fails_when_neither_exchange_answers_for_more_than_two_companies(s3):
    make_companies()
    ex = Exchanges()
    ex.fail_lists = {"M&M", "HDFCBANK", "ETERNAL", "500520", "500180", "543320"}
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code != 0 and tr[0] != "success"
    assert q("select count(*) from equity.brsr_filing f join core.entity e on e.id = f.entity_id where e.slug like 'zb-%%'")[0][0] == 0


@pytest.mark.db
@needs_fixtures
def test_a_year_whose_season_ended_with_no_stored_filing_is_named(s3):
    make_companies(("HDFCBANK",))
    ex = Exchanges()
    ex.drop_nse.update(r["xbrl"].rsplit("/", 1)[-1] for r in ex.w1["nse"]["HDFCBANK"].values())
    ex.w1["bse_latest"]["HDFCBANK"] = {}
    _code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert "zb-hdfcbank (FY 2023-24" in checks(tr)["filings_filed"]["detail"]


def test_the_weekly_window_starts_30_days_before_the_latest_stored_filing_and_a_backfill_at_1_apr_2024():
    t = task()

    class Db:
        def __init__(self, last):
            self.last = last

        def execute(self, sql):
            class R:
                def fetchone(s):
                    return (self.last,)
            return R()

    class Ctx:
        def __init__(self, workflow, since=None):
            self.workflow, self.since, self.until, self.now = workflow, since, None, NOW

    assert t._window(Ctx("weekly"), Db(None)) == (D(2024, 4, 1), D(2026, 11, 11))
    assert t._window(Ctx("weekly"), Db(datetime(2026, 7, 4, tzinfo=UTC)))[0] == D(2026, 6, 4)
    assert t._window(Ctx("backfill"), Db(datetime(2026, 7, 4, tzinfo=UTC)))[0] == D(2024, 4, 1)
    assert t._window(Ctx("weekly", D(2025, 1, 1)), Db(None))[0] == D(2025, 1, 1)


# ---------------------------------------------------------------------------------------------- the storage stop (F3-02, #496)
STOP_ALERT = f"storage_stop:{KEY}"
FILE_PATHS = (".xml", ".html")


def open_alerts(dedupe_key):
    return q("select severity, message from ops.alert where dedupe_key = %s and resolved_at is null", dedupe_key)


def filing_requests(ex: Exchanges) -> list[str]:
    """The filing files asked for (not the lists, not the homepage)."""
    return [r.url.path for r in ex.seen if r.url.path.endswith(FILE_PATHS)]


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
def test_under_the_stop_files_are_fetched_and_stored_as_before_and_the_check_passes(s3, monkeypatch):
    from ft.storage import MB, Storage
    make_companies(("M&M",))
    monkeypatch.setattr(Storage, "stored_bytes", lambda self: (1, 10 * MB))
    ex = Exchanges()
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[0] == "success" and tr[1] > 0, tr
    assert filing_requests(ex) and len(filings()) >= 2
    stop = checks(tr)["storage_stop"]
    assert stop["passed"] and not stop["blocking"]
    assert re.match(r"not reached: the raw bucket holds 1\d\.\d MB of stored objects, under the 900 MB stop", stop["detail"]), stop
    assert open_alerts(STOP_ALERT) == []


@pytest.mark.db
@needs_fixtures
def test_over_the_stop_no_file_is_fetched_or_stored_the_lists_are_recorded_and_the_check_and_an_error_alert_open(s3, monkeypatch):
    from ft.storage import MB, Storage
    make_companies(("M&M",))
    puts = record_puts(monkeypatch)
    monkeypatch.setattr(Storage, "stored_bytes", lambda self: (1, 950 * MB))
    ex = Exchanges()
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[0] == "success", tr                                  # held, not failed: discovery is kept
    assert filing_requests(ex) == []                                             # no filing file fetched
    assert any(r.url.path == br.NSE_LIST_PATH for r in ex.seen)                  # both lists were read
    assert "application/json" in puts and "application/xml" not in puts          # the list replies recorded; no file stored
    assert filings() == [] and q("select count(*) from core.document where entity_id in "
                                 "(select id from core.entity where slug like 'zb-%%')")[0][0] == 0
    c = checks(tr)
    stop = c["storage_stop"]
    assert not stop["passed"] and not stop["blocking"]
    assert stop["detail"].startswith("storage stop reached: the raw bucket holds 950.0 MB of stored objects, at or over the "
                                     "900 MB stop for new files; held: ")
    n_held, n_checked = map(int, re.search(r"held: (\d+) of (\d+) company-years", stop["detail"]).groups())
    assert n_held == n_checked >= 2                                              # over at the start: every company-year is held
    assert "zb-" not in stop["detail"]                                           # counts and sizes only
    served = c["files_served"]                                                   # the held company-years are named here
    assert not served["passed"] and not served["blocking"]
    assert "zb-mm FY 2023-24 (held by the storage stop" in served["detail"]
    [(severity, message)] = open_alerts(STOP_ALERT)
    assert severity == "error" and message.startswith(f"{KEY}: storage stop reached: ") and "zb-" not in message
    assert open_alerts(f"check:{KEY}:storage_stop")                              # the failed check's own warning too

    monkeypatch.undo()                                                           # space freed: the next run reads the filings
    code, tr = go(Exchanges(), since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[0] == "success" and tr[1] > 0 and len(filings()) >= 2, tr
    c = checks(tr)
    assert c["storage_stop"]["passed"] and c["files_served"]["passed"]
    assert open_alerts(STOP_ALERT) == [] and open_alerts(f"check:{KEY}:storage_stop") == []              # both resolved


@pytest.mark.db
@needs_fixtures
def test_the_stop_is_kept_current_with_each_file_stored_so_a_later_company_year_is_held(s3, monkeypatch):
    from ft.storage import MB, Storage
    make_companies(("M&M",))
    monkeypatch.setattr(Storage, "stored_bytes", lambda self: (1, 900 * MB - 1))  # one byte under: the first file may be stored
    ex = Exchanges()
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[0] == "success", tr
    years = [r[1] for r in filings()]
    assert years == ["FY 2023-24"], years                                        # the oldest year took the bucket over
    stop = checks(tr)["storage_stop"]
    assert not stop["passed"] and "at or over the 900 MB stop for new files; held: " in stop["detail"]
    n_held, n_checked = map(int, re.search(r"held: (\d+) of (\d+) company-years", stop["detail"]).groups())
    assert 1 <= n_held < n_checked                                               # the year before the stop wasn't held


@pytest.mark.db
@needs_fixtures
def test_a_bucket_that_cant_be_listed_holds_new_files_with_its_own_message(s3, monkeypatch):
    from ft.storage import Storage
    make_companies(("M&M",))

    def refuse(self):
        raise RuntimeError("listing refused")
    monkeypatch.setattr(Storage, "stored_bytes", refuse)
    ex = Exchanges()
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[0] == "success", tr
    assert filing_requests(ex) == [] and filings() == []
    stop = checks(tr)["storage_stop"]
    assert not stop["passed"] and stop["detail"].startswith(
        "storage stop reached: the raw bucket's size couldn't be read, so new files are held (the stop fails closed; ")
    assert "listing refused" not in stop["detail"]
    [(severity, message)] = open_alerts(STOP_ALERT)
    assert severity == "error" and "couldn't be read" in message and "listing refused" not in message


@pytest.mark.db
@needs_fixtures
def test_a_filing_already_registered_keeps_its_rows_and_is_not_held_when_the_stop_is_reached_later(s3, monkeypatch):
    from ft.storage import MB, Storage
    make_companies(("M&M",))
    ex = Exchanges()
    go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    before = (filings(), q("select count(*) from core.document")[0][0])
    monkeypatch.setattr(Storage, "stored_bytes", lambda self: (1, 950 * MB))       # the bucket is over the stop now
    code, tr = go(ex, since=D(2024, 4, 1), until=D(2026, 11, 11))
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr                   # the same files: nothing new, nothing held
    assert (filings(), q("select count(*) from core.document")[0][0]) == before
    c = checks(tr)
    assert c["storage_stop"]["passed"] and c["files_served"]["passed"]
    assert open_alerts(STOP_ALERT) == []


def test_only_equity_brsr_among_the_document_tasks_builds_a_storage_stop():
    """`equity.filings` and `equity.shareholding` never construct a StorageStop (the stop is opt-in, F3-02)."""
    from pathlib import Path as P
    tasks = P(__file__).resolve().parents[1] / "src" / "ft" / "jobs" / "tasks"
    users = {p.name for p in tasks.glob("*.py") if "StorageStop(" in p.read_text(encoding="utf-8")}
    assert users == {"equity_brsr.py", "equity_call_transcripts.py"}, sorted(users)
    for task_file in ("equity_filings.py", "equity_shareholding.py"):
        assert (tasks / task_file).exists(), task_file                             # the scan would pass vacuously for a rename
