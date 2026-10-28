"""equity.valuation_inputs (job 2A-05): Damodaran's implied ERP, India's default spread, country risk premium and
industry betas, and the IMF's India growth and inflation.

The parsers are tested on the saved files in data/fixtures/equity/damodaran and /imf (private fixtures repo; tests skip
without it and CI checks it out). The task runs through the runner against the test database as job_writer with a mock
transport serving those files and a local S3 server (moto), like the other tasks' tests."""
import itertools
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import inputs as inp
from ft.jobs import config
from ft.jobs.fetch import SITES, HttpClient, site_for
from ft.jobs.runner import run
from ft.jobs.tasks.equity_valuation_inputs import EquityValuationInputs

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
needs_fixtures = pytest.mark.skipif(not (FIX / "damodaran").exists(), reason="the saved files aren't checked out")

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5598, "equity-inputs-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_valuation_inputs"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
D = FIX / "damodaran"


def read(name: str) -> bytes:
    return (D / name).read_bytes()


def imf_india(code: str) -> bytes:
    """The saved all-country reply cut down to India, which is what `/<indicator>/IND` returns."""
    js = json.loads((FIX / "imf" / f"imf_datamapper_{code}.json").read_text())
    js["values"][code] = {"IND": js["values"][code]["IND"]}
    return json.dumps(js).encode()


# ---------------------------------------------------------------------------------------------- Damodaran's files
@needs_fixtures
def test_the_monthly_erp_runs_from_jan_2019_with_text_cells_read_and_no_gap():
    erp = dict(inp.parse_erp_by_month(read("erp_monthly_ERPbymonth.xlsx")))
    assert min(erp) == date(2019, 1, 1) and max(erp) == date(2026, 10, 1) and len(erp) == 94
    assert erp[date(2026, 10, 1)] == Decimal("4.2")                       # the figure on Damodaran's page
    assert erp[date(2024, 9, 1)] == Decimal("4.05")                       # a text date ('1-Sep-24') and a text '4.05%'
    assert inp.month_gaps(list(erp)) == []


def test_month_gaps_names_the_missing_months():
    assert inp.month_gaps([date(2026, 1, 1), date(2026, 4, 1), date(2026, 3, 1)]) == [date(2026, 2, 1)]


@needs_fixtures
def test_the_july_2026_edition_gives_india_1_75_and_2_72():
    c = inp.parse_ctryprem(read("ctrypremJuly26.xlsx"))
    assert c.edition == date(2026, 7, 1) and c.rating == "Baa3"
    assert (round(c.default_spread, 2), round(c.crp, 2)) == (Decimal("1.75"), Decimal("2.72"))
    assert round(c.total_erp, 2) == Decimal("6.92") and c.mature_erp == Decimal("4.2")


@needs_fixtures
def test_the_january_file_gives_1_87_and_2_85_and_is_dated_by_its_content_not_its_name():
    c = inp.parse_ctryprem(read("ctryprem_ctryprem.xlsx"))              # the standing ctryprem.xlsx: January's edition
    assert c.edition == date(2026, 1, 1)
    assert (round(c.default_spread, 2), round(c.crp, 2)) == (Decimal("1.87"), Decimal("2.85"))


@needs_fixtures
def test_betaindia_has_its_edition_date_and_the_industries():
    edition, rows = inp.parse_beta_india(read("beta_india_betaIndia.xls"))
    names = {r.industry for r in rows}
    assert edition == date(2026, 1, 5)
    assert len([r for r in rows if not r.industry.startswith("Total Market")]) == 94 and "Total Market" in names
    banks = next(r for r in rows if r.industry == "Banks (Regional)")
    assert banks.n_firms and banks.unlevered_beta_cash_corrected and banks.de_ratio is not None
    adv = next(r for r in rows if r.industry == "Advertising")
    assert adv.beta == Decimal("1.402911987812592") and adv.n_firms == 24


def test_a_page_or_workbook_that_is_not_what_it_should_be_is_refused():
    html = b"<html><body>Access Denied</body></html>"
    for fn in (inp.parse_erp_by_month, inp.parse_ctryprem, inp.parse_beta_india):
        with pytest.raises(inp.InputsError):
            fn(html)


@needs_fixtures
def test_a_workbook_without_the_expected_sheet_or_country_is_refused():
    with pytest.raises(inp.InputsError, match="no sheet 'ERPs by country'"):
        inp.parse_ctryprem(read("erp_monthly_ERPbymonth.xlsx"))
    with pytest.raises(inp.InputsError, match="no row for Atlantis"):
        inp.parse_ctryprem(read("ctrypremJuly26.xlsx"), "Atlantis")


PAGE = """<a href="../pc/ctrypremJuly26.xlsx">July</a> <a href="https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctryprem.xlsx">
current</a> <a href="/~adamodar/pc/ctrypremJuly26.xlsx">again</a> <a href="datafile/ctryprem.html">the page</a>
<a href="../pc/datasets/betaIndia.xls?x=1">India betas</a>"""


def test_the_page_links_are_made_absolute_and_the_html_page_is_not_a_workbook():
    assert inp.find_ctryprem_links(PAGE) == ["https://pages.stern.nyu.edu/~adamodar/pc/ctrypremJuly26.xlsx",
                                             "https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctryprem.xlsx"]
    assert inp.find_beta_india_link(PAGE) == "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betaIndia.xls"
    assert inp.find_beta_india_link("<p>none</p>") is None


def test_a_dated_file_name_gives_its_month_and_a_plain_name_none():
    assert inp.edition_from_name("https://x/pc/ctrypremJuly26.xlsx") == date(2026, 7, 1)
    assert inp.edition_from_name("ctrypremJan27.xls") == date(2027, 1, 1)
    assert inp.edition_from_name("https://x/pc/datasets/ctryprem.xlsx") is None


# ---------------------------------------------------------------------------------------------- the IMF
@needs_fixtures
def test_imf_india_cpi_2027_to_2031_and_real_gdp_2026_from_the_saved_replies():
    cpi = inp.parse_datamapper(imf_india("PCPIPCH"), "PCPIPCH")
    gdp = inp.parse_datamapper(imf_india("NGDP_RPCH"), "NGDP_RPCH")
    assert [cpi[y] for y in range(2027, 2032)] == [Decimal(x) for x in ("4.4", "3.9", "4.0", "4.0", "4.0")]
    assert gdp[2026] == Decimal("7.0") and min(gdp) == 1980 and max(gdp) == 2031
    # the same parser reads the all-country reply the audit kit saved
    full = (FIX / "imf" / "imf_datamapper_PCPIPCH.json").read_bytes()
    assert inp.parse_datamapper(full, "PCPIPCH", "IND") == cpi


def test_an_imf_reply_without_india_or_with_text_values_is_refused():
    with pytest.raises(inp.InputsError, match="no values"):
        inp.parse_datamapper(b'{"values": {"PCPIPCH": {"USA": {"2026": 2.0}}}}', "PCPIPCH")
    with pytest.raises(inp.InputsError, match="isn't a year and a number"):
        inp.parse_datamapper(b'{"values": {"PCPIPCH": {"IND": {"2026": "n/a"}}}}', "PCPIPCH")
    with pytest.raises(inp.InputsError, match="not JSON"):
        inp.parse_datamapper(b"<html>", "PCPIPCH")


OCT, APR = date(2026, 10, 13), date(2027, 4, 13)


def test_the_first_load_is_stored_under_the_newest_released_edition():
    v = inp.choose_vintage([OCT], date(2026, 10, 28), None, changed=False)
    assert (v.action, v.vintage) == ("write", OCT)
    assert inp.choose_vintage([OCT, APR], date(2026, 12, 1), None, False).vintage == OCT      # April isn't out yet
    assert inp.choose_vintage([APR], date(2026, 12, 1), None, False).action == "error"


def test_unchanged_values_write_nothing_and_a_late_api_is_noted():
    v = inp.choose_vintage([OCT, APR], date(2027, 4, 20), OCT, changed=False)
    assert v.action == "none" and "still serves" in v.note
    assert inp.choose_vintage([OCT], date(2027, 1, 5), OCT, changed=False).note == "the stored edition's values are unchanged"


def test_changed_values_are_the_next_editions_and_an_unlisted_edition_is_an_error():
    v = inp.choose_vintage([OCT, APR], date(2027, 4, 20), OCT, changed=True)
    assert (v.action, v.vintage) == ("write", APR)
    e = inp.choose_vintage([OCT], date(2027, 4, 20), OCT, changed=True)
    assert e.action == "error" and "weo_editions.yaml" in e.note


def test_weo_editions_yaml_loads_with_a_source_per_edition(tmp_path):
    assert inp.load_weo_editions()[-1] == OCT
    p = tmp_path / "w.yaml"
    p.write_text("editions:\n  - released: 2027-04-13\n")
    with pytest.raises(inp.InputsError, match="source"):
        inp.load_weo_editions(p)


# ---------------------------------------------------------------------------------------------- wiring
def test_tasks_yaml_has_the_entry_and_the_sites_are_listed():
    t = config.load()["equity.valuation_inputs"]
    assert t.rules == {"weekly": "every 6 days"} and t.overdue_after.days == 40
    assert not t.uses_nse and not t.visitor_refreshable and t.run_after == ()
    assert site_for(inp.ERP_MONTHLY_URL, SITES).key == "damodaran"
    imf = site_for(inp.IMF_URL.format(indicator="PCPIPCH", country="IND"), SITES)
    assert imf.key == "imf" and imf.impersonate == "chrome"


# ---------------------------------------------------------------------------------------------- through the runner
class Sources:
    """Damodaran's server and the IMF as a mock transport serving the saved files; a test changes `files`."""

    def __init__(self):
        base = "/~adamodar/"
        self.files = {
            base + "New_Home_Page/datacurrent.html": (
                b'<a href="https://pages.stern.nyu.edu/~adamodar/pc/ctrypremJuly26.xlsx">Country risk premiums</a>'
                b'<a href="https://pages.stern.nyu.edu/~adamodar/pc/datasets/betaIndia.xls">India</a>'),
            base + "pc/implprem/ERPbymonth.xlsx": read("erp_monthly_ERPbymonth.xlsx"),
            base + "pc/ctrypremJuly26.xlsx": read("ctrypremJuly26.xlsx"),
            base + "pc/datasets/ctryprem.xlsx": read("ctryprem_ctryprem.xlsx"),
            base + "pc/datasets/betaIndia.xls": read("beta_india_betaIndia.xls"),
            "/external/datamapper/api/v1/NGDP_RPCH/IND": imf_india("NGDP_RPCH"),
            "/external/datamapper/api/v1/PCPIPCH/IND": imf_india("PCPIPCH"),
        }
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request.url.host + request.url.path)
        body = self.files.get(request.url.path)
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)

    def imf_edit(self, code: str, fn) -> None:
        path = f"/external/datamapper/api/v1/{code}/IND"
        js = json.loads(self.files[path])
        fn(js["values"][code]["IND"])
        self.files[path] = json.dumps(js).encode()


_runs = itertools.count(1)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'valuation inputs test', '{weekly}', '6 days', '40 days', false)
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
        cur = c.execute(sql, a)
        return cur.fetchall() if cur.description else []


SERIES = ("damodaran.ERP_IMPLIED", "damodaran.IND_DEFAULT_SPREAD", "damodaran.IND_CRP", "imf.NGDP_RPCH.IND",
          "imf.PCPIPCH.IND")


@pytest.fixture(autouse=True)
def clean(request):
    """Each database test starts from no valuation-input rows (job_writer can't delete; the postgres role can)."""
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = [r[0] for r in c.execute("select id from core.series where key = any(%s)", (list(SERIES),)).fetchall()]
        c.execute("delete from core.forecast where series_id = any(%s)", (ids,))
        c.execute("delete from core.observation_revision where series_id = any(%s)", (ids,))
        c.execute("delete from core.observation where series_id = any(%s)", (ids,))
        c.execute("delete from core.observation where series_id = (select id from core.series where key = 'fred.INDIRLTLT01STM') "
                  "and obs_date = '2026-09-01'")
        c.execute("delete from equity.industry_benchmark")
        c.execute("update core.series set is_published = false where key = any(%s)", (list(SERIES),))


def task() -> EquityValuationInputs:
    class T(EquityValuationInputs):
        key = KEY
    t = T()
    t.workflows = ("weekly",)
    return t


def go(src, now=NOW):
    http = HttpClient(transport=httpx.MockTransport(src), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, "weekly", [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s
              order by id desc limit 1""", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def counts():
    return q("""select (select count(*) from core.observation o join core.series s on s.id = o.series_id
                         where s.key = 'damodaran.ERP_IMPLIED'),
                       (select count(*) from core.observation o join core.series s on s.id = o.series_id
                         where s.key = 'damodaran.IND_DEFAULT_SPREAD'),
                       (select count(*) from core.observation o join core.series s on s.id = o.series_id
                         where s.key = 'damodaran.IND_CRP'),
                       (select count(*) from core.forecast f join core.series s on s.id = f.series_id
                         where left(s.key, 4) = 'imf.'),
                       (select count(*) from equity.industry_benchmark)""")[0]


@pytest.mark.db
@needs_fixtures
def test_the_first_load_stores_the_series_the_editions_the_betas_and_the_forecasts(s3):
    src = Sources()
    code, tr = go(src)
    assert code == 0 and tr[0] == "success", tr
    assert all(c["passed"] for c in checks(tr).values()), checks(tr)
    assert {n for n, c in checks(tr).items() if not c["blocking"]} >= {"erp_fresh", "imf_fresh", "revisions"}
    assert counts() == (94, 2, 2, 104, 96)                    # months, two editions each, 52 years x 2, 94 industries and 2 Total Market rows
    assert q("""select o.obs_date, o.value from core.observation o join core.series s on s.id = o.series_id
                 where s.key = 'damodaran.IND_DEFAULT_SPREAD' order by 1""") == [
        (date(2026, 1, 1), Decimal("1.867795")), (date(2026, 7, 1), Decimal("1.749881"))]
    assert q("""select o.value from core.observation o join core.series s on s.id = o.series_id
                 where s.key = 'damodaran.ERP_IMPLIED' and o.obs_date = '2026-10-01'""") == [(Decimal("4.2"),)]
    # IMF: one vintage, the edition the YAML gives; actuals and projections stored alike; the API method, no document
    assert q("""select distinct f.vintage_date, f.method, f.document_id from core.forecast f
                 join core.series s on s.id = f.series_id where left(s.key, 4) = 'imf.'""") == [(date(2026, 10, 13), "api", None)]
    assert q("""select f.value, f.target_start, f.target_end from core.forecast f join core.series s on s.id = f.series_id
                 where s.key = 'imf.PCPIPCH.IND' and f.target_label = '2028'""") == [
        (Decimal("3.9"), date(2028, 1, 1), date(2028, 12, 31))]
    assert q("select edition, market, count(*) from equity.industry_benchmark group by 1, 2") == [
        (date(2026, 1, 5), "IN", 96)]
    # every response was stored before the load: 7 requests, 7 raw files for the 5 series' sources
    assert len(src.seen) == 7
    assert q("select count(*) from core.series where key = any(%s) and is_published", list(SERIES))[0][0] == 5


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_load_writes_nothing(s3):
    src = Sources()
    go(src)
    before = counts()
    code, tr = go(src)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert counts() == before
    assert "unchanged" in checks(tr)["imf_vintage"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_new_weo_edition_adds_a_vintage_and_never_changes_the_old_one(s3, monkeypatch):
    src = Sources()
    go(src)
    monkeypatch.setattr(inp, "load_weo_editions", lambda path=None: [OCT, APR])
    src.imf_edit("PCPIPCH", lambda v: v.update({"2027": 4.1, "2032": 4.0}))
    code, tr = go(src, now=datetime(2027, 4, 20, 6, 0, tzinfo=UTC))
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr)["imf_vintage"]["passed"] and "13 Apr 2027" in checks(tr)["imf_vintage"]["detail"]
    got = dict(q("""select f.vintage_date, count(*) from core.forecast f join core.series s on s.id = f.series_id
                     where s.key = 'imf.PCPIPCH.IND' group by 1"""))
    assert got == {OCT: 52, APR: 53}                                # the second vintage holds the new year too
    assert q("""select f.value from core.forecast f join core.series s on s.id = f.series_id
                 where s.key = 'imf.PCPIPCH.IND' and f.target_label = '2027' order by f.vintage_date""") == [
        (Decimal("4.4"),), (Decimal("4.1"),)]
    assert q("select count(*) from core.forecast f join core.series s on s.id = f.series_id where s.key = 'imf.NGDP_RPCH.IND'")[0][0] == 52


@pytest.mark.db
@needs_fixtures
def test_changed_imf_values_with_no_new_edition_listed_fail_the_run_and_store_nothing(s3):
    src = Sources()
    go(src)
    before = counts()
    src.imf_edit("NGDP_RPCH", lambda v: v.update({"2026": 6.5}))
    code, tr = go(src, now=datetime(2027, 4, 20, 6, 0, tzinfo=UTC))
    assert code != 0 and tr[0] == "checks_failed", tr
    c = checks(tr)["imf_vintage"]
    assert not c["passed"] and c["blocking"] and "weo_editions.yaml" in c["detail"]
    assert counts() == before


@pytest.mark.db
@needs_fixtures
def test_a_listed_workbook_that_cannot_be_read_fails_the_edition_check(s3):
    src = Sources()
    src.files["/~adamodar/pc/ctrypremJuly26.xlsx"] = b"<html>Access Denied</html>"
    code, tr = go(src)
    assert code != 0 and tr[0] == "checks_failed", tr
    c = checks(tr)["ctryprem_edition"]
    assert not c["passed"] and "ctrypremJuly26.xlsx" in c["detail"]
    assert counts()[:2] == (0, 0)                                   # the whole load rolled back
    assert q("select count(*) from core.series where key = any(%s) and is_published", list(SERIES))[0][0] == 0


@pytest.mark.db
@needs_fixtures
def test_an_unreadable_archived_edition_older_than_the_newest_does_not_fail(s3):
    """Regression (28 Oct dev run): datacurrent.html links ctrypremJune13.xls, a 2013 file that isn't an .xlsx."""
    src = Sources()
    src.files["/~adamodar/New_Home_Page/datacurrent.html"] += \
        b'<a href="https://pages.stern.nyu.edu/~adamodar/pc/ctrypremJune13.xls">June 2013</a>'
    src.files["/~adamodar/pc/ctrypremJune13.xls"] = b"\xd0\xcf\x11\xe0 not an xlsx"
    code, tr = go(src)
    c = checks(tr)["ctryprem_edition"]
    assert code == 0 and c["passed"] and "ctrypremJune13.xls" in c["detail"], tr


@pytest.mark.db
@needs_fixtures
def test_a_dated_file_name_newer_than_every_edition_read_fails(s3):
    src = Sources()
    src.files["/~adamodar/New_Home_Page/datacurrent.html"] += \
        b'<a href="https://pages.stern.nyu.edu/~adamodar/pc/ctrypremJan27.xlsx">Jan 2027</a>'
    src.files["/~adamodar/pc/ctrypremJan27.xlsx"] = read("ctrypremJuly26.xlsx")       # named Jan 2027, dated July 2026
    code, tr = go(src)
    c = checks(tr)["ctryprem_edition"]
    assert code != 0 and not c["passed"] and "ctrypremJan27.xlsx" in c["detail"], tr


@pytest.mark.db
@needs_fixtures
def test_a_value_corrected_in_place_is_updated_and_logged_not_lost(s3):
    src = Sources()
    go(src)
    q("""update core.observation set value = 4.0 where series_id = (select id from core.series where key =
         'damodaran.ERP_IMPLIED') and obs_date = '2026-10-01'""")        # as if an earlier run had stored 4.0
    code, tr = go(src)
    assert code == 0 and tr[0] == "success" and tr[1] == 1, tr
    assert "ERP_IMPLIED 1" in checks(tr)["revisions"]["detail"] and not checks(tr)["revisions"]["passed"]
    assert q("select count(*) from core.observation_revision where series_id = (select id from core.series where key = "
             "'damodaran.ERP_IMPLIED')")[0][0] >= 1


@pytest.mark.db
@needs_fixtures
def test_a_republished_beta_edition_keeps_its_stored_rows_and_warns(s3):
    src = Sources()
    go(src)
    q("update equity.industry_benchmark set beta = 9 where industry = 'Advertising'")
    code, tr = go(src)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert "Advertising" in checks(tr)["revisions"]["detail"]
    assert q("select beta from equity.industry_benchmark where industry = 'Advertising'") == [(Decimal(9),)]


@pytest.mark.db
@needs_fixtures
def test_the_risk_free_rate_and_india_erp_helpers_read_the_stored_rows(s3):
    go(Sources())
    task_run = q("select max(id) from ops.task_run where task_key = %s", KEY)[0][0]
    q("""insert into core.observation (series_id, obs_date, value, task_run_id)
         select id, '2026-09-01', 7.06, %s from core.series where key = 'fred.INDIRLTLT01STM'""", task_run)
    with psycopg.connect(JOB, autocommit=True) as c:
        rf = inp.inr_risk_free(c, date(2026, 9, 30))
        assert round(rf.value, 2) == Decimal("5.31")                              # 7.06 - 1.75 (the July edition)
        assert rf.parts["spread_edition"] == "2026-07-01" and rf.parts["india_10y_month"] == "2026-09-01"
        erp = inp.india_erp(c, date(2026, 10, 28))
        assert round(erp.value, 2) == Decimal("6.92")                             # October's 4.20 + 2.72
        assert erp.parts["mature_erp_month"] == "2026-10-01" and erp.parts["crp_edition"] == "2026-07-01"
        # an older edition never wins over a newer one: as of March the January edition is the newest
        assert inp.india_erp(c, date(2026, 3, 31)).parts["crp_edition"] == "2026-01-01"
        assert round(inp.india_erp(c, date(2026, 3, 31)).value, 2) == Decimal("4.38") + Decimal("2.85")
        with pytest.raises(inp.InputsError, match="no damodaran.IND_CRP"):
            inp.india_erp(c, date(2025, 12, 31))


@pytest.mark.db
def test_the_guard_holds_the_task_until_it_has_a_recorded_run():
    with psycopg.connect(JOB, autocommit=True) as c:
        t = EquityValuationInputs()
        t.rules = {"weekly": "every 6 days"}
        d = t.due(c, "weekly", NOW)
        assert not d.due and "first recorded run" in d.reason
