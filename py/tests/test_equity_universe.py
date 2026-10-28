"""equity.universe (job 2A-04): the company master, index membership and curated peer groups.

The parsers and rules are tested on the saved files in data/fixtures/equity/universe (the private fixtures repo; the
tests skip without it, and CI checks it out). The task runs through the runner against the test database as
job_writer, with a mock transport serving those files and a local S3 server (moto), like the other tasks' tests.
No NSE or BSE data is kept in this repo (CLAUDE.md §4)."""
import csv
import gzip
import io
import itertools
from datetime import UTC, date, datetime
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
import yaml
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import guard
from ft.equity import universe as u
from ft.jobs import config
from ft.jobs.due import Due
from ft.jobs.fetch import SITES, HttpClient, site_for
from ft.jobs.runner import run
from ft.jobs.tasks import equity_universe as eu
from ft.jobs.tasks.equity_universe import Close, EquityUniverse, Insert, plan_identifier

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "universe"
needs_fixtures = pytest.mark.skipif(not FIX.exists(), reason="the saved files aren't checked out (data/fixtures)")

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5597, "equity-universe-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_universe"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)          # 11:30 in India: the snapshot date is 28 Oct 2026
SNAP = date(2026, 10, 28)

# Damodaran's India industry names, from the "Industry Name" column of data/fixtures/equity/damodaran/
# beta_india_betaIndia.xls (edition of 5 Jan 2026): peer_groups.yaml must use them exactly.
DAMODARAN_INDIA = frozenset("""Advertising|Aerospace/Defense|Air Transport|Apparel|Auto & Truck|Auto Parts|Bank (Money Center)|
Banks (Regional)|Beverage (Alcoholic)|Beverage (Soft)|Broadcasting|Brokerage & Investment Banking|Building Materials|
Business & Consumer Services|Cable TV|Chemical (Basic)|Chemical (Diversified)|Chemical (Specialty)|Coal & Related Energy|
Computer Services|Computers/Peripherals|Construction Supplies|Diversified|Drugs (Biotechnology)|Drugs (Pharmaceutical)|
Education|Electrical Equipment|Electronics (Consumer & Office)|Electronics (General)|Engineering/Construction|
Entertainment|Environmental & Waste Services|Farming/Agriculture|Financial Svcs. (Non-bank & Insurance)|
Food Processing|Food Wholesalers|Furn/Home Furnishings|Green & Renewable Energy|Healthcare Products|
Healthcare Support Services|Heathcare Information and Technology|Homebuilding|Hospitals/Healthcare Facilities|
Hotel/Gaming|Household Products|Information Services|Insurance (General)|Insurance (Life)|Insurance (Prop/Cas.)|
Investments & Asset Management|Machinery|Metals & Mining|Office Equipment & Services|Oil/Gas (Integrated)|
Oil/Gas (Production and Exploration)|Oil/Gas Distribution|Oilfield Svcs/Equip.|Packaging & Container|
Paper/Forest Products|Power|Precious Metals|Publishing & Newspapers|R.E.I.T.|Real Estate (Development)|
Real Estate (General/Diversified)|Real Estate (Operations & Services)|Recreation|Reinsurance|Restaurant/Dining|
Retail (Automotive)|Retail (Building Supply)|Retail (Distributors)|Retail (General)|Retail (Grocery and Food)|
Retail (REITs)|Retail (Special Lines)|Rubber& Tires|Semiconductor|Semiconductor Equip|Shipbuilding & Marine|Shoe|
Software (Entertainment)|Software (Internet)|Software (System & Application)|Steel|Telecom (Wireless)|
Telecom. Equipment|Telecom. Services|Tobacco|Transportation|Transportation (Railroads)|Trucking|Utility (General)|
Utility (Water)""".replace("\n", "").split("|"))

LISTS = {"nifty-50": "nifty50", "nifty-100": "nifty100", "nifty-200": "nifty200", "nifty-next-50": "niftynext50",
         "nifty-midcap-100": "niftymidcap100"}


def read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def nifty(index: str) -> list[u.NiftyRow]:
    return u.parse_nifty_list(read(f"{LISTS[index]}_20261019.csv"))


# ---------------------------------------------------------------------------------------------- the files
@needs_fixtures
def test_the_nifty_lists_have_their_row_counts_and_valid_isins():
    cfg = u.load_universe_config()
    for key, idx in cfg.indices.items():
        rows = nifty(key)
        assert len(rows) == idx.rows, key
        assert all(r.series == "EQ" for r in rows)
    assert nifty("nifty-50")[0] == u.NiftyRow("Adani Enterprises Ltd.", "Metals & Mining", "ADANIENT", "EQ", "INE423A01024")


@needs_fixtures
def test_nifty_200_is_nifty_100_plus_midcap_100_in_the_saved_lists():
    n100, mid, n200 = ({r.isin for r in nifty(k)} for k in ("nifty-100", "nifty-midcap-100", "nifty-200"))
    assert n200 == n100 | mid and not n100 & mid


def test_an_html_page_or_a_changed_header_is_refused():
    with pytest.raises(u.UniverseError, match="HTML"):
        u.parse_nifty_list(b"<!DOCTYPE html><html><body>Access Denied</body></html>")
    with pytest.raises(u.UniverseError, match="columns"):
        u.parse_nifty_list(b"Name,Code\nA,B\n")
    with pytest.raises(u.UniverseError, match="ISIN"):
        u.parse_nifty_list(b"Company Name,Industry,Symbol,Series,ISIN Code\nA Ltd.,X,A,EQ,NOTANISIN\n")


@needs_fixtures
def test_equity_l_is_keyed_by_isin_with_its_leading_spaces_stripped():
    eq = u.parse_equity_l(read("EQUITY_L_20261019.csv"))
    infy = eq["INE009A01021"]
    assert (infy.symbol, infy.listing_date) == ("INFY", date(1995, 2, 8)) and len(eq) > 2000
    padded = b"SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n" \
             b"ABC,Abc Ltd,EQ,06-OCT-2008,5,1,INE144J01027,5\n"
    got = u.parse_equity_l(padded)["INE144J01027"]
    assert got.symbol == "ABC" and got.listing_date == date(2008, 10, 6) and got.face_value == "5"


@needs_fixtures
def test_symbolchange_has_a_header_and_dates():
    ch = u.parse_symbolchange(read("symbolchange.csv"))
    assert len(ch) > 1000
    assert u.SymbolChange("Tata Motors Passenger Vehicles Limited", "TATAMOTORS", "TMPV", date(2025, 10, 24)) in ch


@needs_fixtures
def test_the_bse_list_is_matched_by_isin_and_bse_ltd_has_no_code():
    bse, dup = u.parse_bse_scrips(gzip.decompress(read("bse_ListofScripData.json.gz")))
    assert bse["INE117A01022"].code == "500002"                         # ABB India
    assert "INE118H01025" not in bse                                    # BSE Ltd lists only on NSE
    n50 = {r.isin for r in nifty("nifty-50")}
    assert len(n50 - bse.keys()) == 1
    assert all(len(v) > 1 for v in dup.values())


def test_a_bse_reply_that_is_empty_or_not_json_is_refused():
    for body in (b"[]", b"<html>", b'{"Table": []}'):
        with pytest.raises(u.UniverseError):
            u.parse_bse_scrips(body)


# ---------------------------------------------------------------------------------------------- slugs, history, plan
@pytest.mark.parametrize("symbol, slug", [("M&M", "m-and-m"), ("BAJAJ-AUTO", "bajaj-auto"), ("INFY", "infy"),
                                          ("L&TFH", "l-and-tfh"), ("20MICRONS", "20microns")])
def test_slug_rule(symbol, slug):
    assert u.slugify(symbol) == slug


def test_symbol_history_follows_the_changes_back():
    ch = [u.SymbolChange("Tata Motors", "TATAMOTORS", "TMPV", date(2025, 10, 24)),
          u.SymbolChange("Tata Motors", "TELCO", "TATAMOTORS", date(2003, 5, 1)),
          u.SymbolChange("Other", "OLD", "NEW", date(2020, 1, 1))]
    assert u.symbol_history("TMPV", ch) == [u.IdRange("TELCO", None, date(2003, 5, 1)),
                                            u.IdRange("TATAMOTORS", date(2003, 5, 1), date(2025, 10, 24)),
                                            u.IdRange("TMPV", date(2025, 10, 24), None)]
    assert u.symbol_history("INFY", ch) == [u.IdRange("INFY", None, None)]


def test_symbol_history_stops_at_a_loop():
    ch = [u.SymbolChange("A", "X", "Y", date(2020, 1, 1)), u.SymbolChange("A", "Y", "X", date(2021, 1, 1))]
    assert [r.value for r in u.symbol_history("X", ch)] == ["Y", "X"]


@needs_fixtures
def test_the_saved_symbol_changes_give_the_nifty_50_their_history():
    ch = u.parse_symbolchange(read("symbolchange.csv"))
    eternal = u.symbol_history("ETERNAL", ch)
    assert [(r.value, r.start, r.end) for r in eternal] == [("ZOMATO", None, date(2025, 4, 9)),
                                                            ("ETERNAL", date(2025, 4, 9), None)]
    assert [r.value for r in u.symbol_history("TMPV", ch)] == ["TELCO", "TATAMOTORS", "TMPV"]


def test_plan_identifier_writes_nothing_when_nothing_changed():
    existing = [u.IdRange("INFY", None, None)]
    assert plan_identifier(existing, "INFY", [u.IdRange("INFY", None, None)], SNAP) == []


def test_plan_identifier_for_a_new_company_opens_the_current_value_and_its_history():
    hist = [u.IdRange("ZOMATO", None, date(2025, 4, 9)), u.IdRange("ETERNAL", date(2025, 4, 9), None)]
    assert plan_identifier([], "ETERNAL", hist, SNAP) == [Insert(u.IdRange("ETERNAL", date(2025, 4, 9), None)),
                                                          Insert(u.IdRange("ZOMATO", None, date(2025, 4, 9)))]
    assert plan_identifier([], "INE009A01021", [], SNAP) == [Insert(u.IdRange("INE009A01021", None, None))]


def test_plan_identifier_closes_the_old_range_where_the_new_one_starts():
    stored = [u.IdRange("INFY", None, None)]
    hist = [u.IdRange("INFY", None, date(2026, 11, 2)), u.IdRange("INFYX", date(2026, 11, 2), None)]
    assert plan_identifier(stored, "INFYX", hist, SNAP) == [Close("INFY", None, date(2026, 11, 2)),
                                                            Insert(u.IdRange("INFYX", date(2026, 11, 2), None))]
    # no date in the file: the change is dated by the run
    assert plan_identifier(stored, "NEWISIN", [], SNAP) == [Close("INFY", None, SNAP),
                                                            Insert(u.IdRange("NEWISIN", SNAP, None))]


# ---------------------------------------------------------------------------------------------- the curated YAML
def test_the_curated_yaml_loads_and_every_damodaran_industry_is_a_betaindia_name():
    cfg = u.load_universe_config()
    groups = u.load_peer_groups()
    companies = u.load_companies(groups)
    assert cfg.index == "nifty-50" and cfg.indices["nifty-50"].rows == 50
    assert {g.damodaran_industry for g in groups.values()} <= DAMODARAN_INDIA
    assert {c.peer_group for c in companies.values()} == set(groups)         # no group without a company
    assert all(c.family_note for c in companies.values())


@needs_fixtures
def test_every_nifty_50_company_is_curated_and_the_family_counts_are_as_drafted():
    companies = u.load_companies(u.load_peer_groups())
    slugs = {u.slugify(r.symbol) for r in nifty("nifty-50")}
    assert set(companies) == slugs and "m-and-m" in companies
    by = {}
    for c in companies.values():
        by.setdefault(c.valuation_family, []).append(c.slug)
    assert len(by["lender"]) == 8 and len(by["insurer"]) == 2
    # Bajaj Auto moved to mixed in Anand's spot check (#109, 28 Oct 2026): Bajaj Auto Credit, its wholly owned NBFC.
    assert sorted(by["mixed"]) == ["bajaj-auto", "bajajfinsv", "grasim", "lt", "m-and-m"]
    assert companies["sbin"].valuation_family == "lender" and companies["hdfclife"].valuation_family == "insurer"


def write_yaml(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data))
    return p


def test_the_validator_refuses_a_group_without_a_damodaran_industry(tmp_path):
    p = write_yaml(tmp_path, "g.yaml", {"banks": {"label": "Banks", "source_note": "curated"}})
    with pytest.raises(u.UniverseError, match="banks.*Damodaran industry"):
        u.load_peer_groups(p)


def test_the_validator_refuses_a_company_without_a_family_or_with_an_unknown_group(tmp_path):
    groups = {"banks": u.PeerGroup("banks", "Banks", "Banks (Regional)", "curated")}
    p = write_yaml(tmp_path, "c.yaml", {"sbin": {"peer_group": "banks", "family_note": "a bank"},
                                        "xyz": {"peer_group": "nope", "valuation_family": "operating",
                                                "family_note": "x"},
                                        "abc": {"peer_group": "banks", "valuation_family": "lender"},
                                        "bad": {"peer_group": "banks", "valuation_family": "bank", "family_note": "x",
                                                "fy_end_month": 13}})
    with pytest.raises(u.UniverseError) as e:
        u.load_companies(groups, p)
    msg = str(e.value)
    assert "company sbin: valuation_family must be one of" in msg
    assert "company xyz: peer_group 'nope' isn't in peer_groups.yaml" in msg
    assert "company abc: needs a family_note" in msg
    assert "company bad" in msg and "fy_end_month" in msg


def test_the_universe_config_must_name_a_snapshot_index(tmp_path):
    p = write_yaml(tmp_path, "u.yaml", {"index": "nifty-500", "snapshot_indices": {"nifty-50": {"file": "a.csv", "rows": 50}}})
    with pytest.raises(u.UniverseError, match="nifty-500"):
        u.load_universe_config(p)


# ---------------------------------------------------------------------------------------------- wiring
def test_tasks_yaml_has_the_entry_and_the_sites_are_listed():
    t = config.load()["equity.universe"]
    assert t.rules == {"weekly": "every 6 days"} and t.overdue_after.days == 10
    assert t.uses_nse and not t.visitor_refreshable and t.run_after == ()
    for url, site in ((u.nifty_url("ind_nifty50list.csv"), "nse"), ("https://www.nseindia.com/api/x", "nse"),
                      (u.BSE_SCRIPS_URL, "bse")):
        assert site_for(url, SITES).key == site
    assert EquityUniverse.key == "equity.universe"


# ---------------------------------------------------------------------------------------------- the production guard
class FakeDb:
    def __init__(self, table: bool, ran: bool):
        self.table, self.ran = table, ran

    def execute(self, sql, params=()):
        class R:
            def __init__(s, v): s.v = v
            def fetchone(s): return (s.v,)
        return R(self.table if "to_regclass" in sql else self.ran)


RULE = Due(True, "never succeeded")


def test_the_guard_holds_a_task_whose_table_is_not_on_the_project():
    d = guard.equity_due(FakeDb(False, True), "equity.universe", "weekly", RULE)
    assert not d.due and "migration isn't pushed" in d.reason
    assert not guard.equity_due(FakeDb(False, True), "equity.universe", "refresh", RULE).due


@pytest.mark.parametrize("workflow", ["weekly", "daily", "watch"])
def test_the_guard_holds_a_scheduled_run_until_the_task_has_run_once(workflow):
    d = guard.equity_due(FakeDb(True, False), "equity.universe", workflow, RULE)
    assert not d.due and "first recorded run" in d.reason and "2A-16" in d.reason


@pytest.mark.parametrize("workflow", ["refresh", "backfill"])
def test_the_guard_never_holds_a_refresh_or_a_backfill(workflow):
    assert guard.equity_due(FakeDb(True, False), "equity.universe", workflow, RULE) is RULE


def test_after_a_first_run_the_tasks_own_rule_decides():
    assert guard.equity_due(FakeDb(True, True), "equity.universe", "weekly", RULE) is RULE
    own = Due(False, "last success 1 day ago")
    assert guard.equity_due(FakeDb(True, True), "equity.universe", "weekly", own) is own


@pytest.mark.db
def test_the_guard_on_the_test_database_uses_the_real_tables():
    with psycopg.connect(JOB, autocommit=True) as c:
        t = EquityUniverse()
        assert not guard.equity_due(c, "equity.nothing", "weekly", RULE).due             # never ran
        assert guard.equity_due(c, "equity.nothing", "refresh", RULE) is RULE
        assert "migration isn't pushed" in guard.equity_due(c, "x", "weekly", RULE, requires="equity.nope").reason
        t.rules = {"weekly": "every 6 days"}
        assert not t.due(c, "weekly", NOW).due                                           # no recorded run yet


# ---------------------------------------------------------------------------------------------- through the runner
_runs = itertools.count(1)


class Exchange:
    """NSE's archive and BSE's API as a mock transport serving the saved files; a test changes `files` to change what
    the exchanges publish."""

    def __init__(self):
        self.files = {
            "/content/indices/ind_nifty50list.csv": read("nifty50_20261019.csv"),
            "/content/indices/ind_nifty100list.csv": read("nifty100_20261019.csv"),
            "/content/indices/ind_nifty200list.csv": read("nifty200_20261019.csv"),
            "/content/indices/ind_niftynext50list.csv": read("niftynext50_20261019.csv"),
            "/content/indices/ind_niftymidcap100list.csv": read("niftymidcap100_20261019.csv"),
            "/content/equities/EQUITY_L.csv": read("EQUITY_L_20261019.csv"),
            "/content/equities/symbolchange.csv": read("symbolchange.csv"),
            "/BseIndiaAPI/api/ListofScripData/w": gzip.decompress(read("bse_ListofScripData.json.gz")),
        }
        self.seen: list[httpx.Request] = []

    def edit(self, path: str, fn) -> None:
        self.files[path] = fn(self.files[path].decode("utf-8")).encode("utf-8")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        body = self.files.get(request.url.path)
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity universe test', '{weekly}', '6 days', '10 days', false)
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
        return c.execute(sql, a).fetchall()


@pytest.fixture(autouse=True)
def clean(request):
    """Each database test starts from no companies (job_writer can't delete; the postgres role can)."""
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for sql in ("delete from equity.index_membership", "delete from equity.company",
                    "delete from core.entity_identifier where entity_id in (select id from core.entity where kind = 'company')",
                    "delete from core.entity where kind = 'company'"):
            try:
                c.execute(sql)
            except psycopg.errors.ForeignKeyViolation:      # another module's rows hold the company: leave them
                return


def task() -> EquityUniverse:
    class T(EquityUniverse):
        key = KEY
    t = T()
    t.workflows = ("weekly",)
    return t


def go(ex, now=NOW):
    http = HttpClient(transport=httpx.MockTransport(ex), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, "weekly", [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s
              order by id desc limit 1""", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def counts():
    return q("""select (select count(*) from core.entity where kind = 'company'),
                       (select count(*) from equity.company),
                       (select count(*) from core.entity_identifier i join core.entity e on e.id = i.entity_id
                         where e.kind = 'company'),
                       (select count(*) from equity.index_membership),
                       (select count(*) from equity.peer_group)""")[0]


@pytest.mark.db
@needs_fixtures
def test_the_first_load_creates_fifty_companies_with_identifiers_and_snapshots(s3):
    ex = Exchange()
    code, tr = go(ex)
    assert code == 0 and tr[0] == "success", tr
    assert {n: (c["passed"], c["blocking"]) for n, c in checks(tr).items()} == {
        "index_files": (True, True), "nifty200_union": (True, True), "in_equity_l": (True, True),
        "curated": (True, True), "bse_codes": (True, False), "membership_changes": (True, False)}
    assert "expected without one: BSE" in checks(tr)["bse_codes"]["detail"]
    earlier = sum(len(u.symbol_history(r.symbol, u.parse_symbolchange(read("symbolchange.csv")))) - 1
                  for r in nifty("nifty-50"))                      # symbols a company had before today's
    assert counts()[:3] == (50, 50, 50 + 50 + 49 + earlier)         # ISINs, symbols, BSE codes, earlier symbols
    assert q("select count(*) from equity.peer_group")[0][0] == 18
    # companies: unpublished, India/INR, ISIN and symbol identifiers, BSE codes for all but BSE Ltd
    assert q("select count(*) from core.entity where kind = 'company' and not is_published and market = 'IN' "
             "and currency = 'INR'")[0][0] == 50
    assert q("select scheme, count(distinct entity_id) from core.entity_identifier group by 1 having scheme in "
             "('isin', 'nse_symbol', 'bse_code') order by 1") == [("bse_code", 49), ("isin", 50), ("nse_symbol", 50)]
    assert q("select slug, name from core.entity where slug = 'm-and-m'") == [("m-and-m", "Mahindra & Mahindra Ltd.")]
    sbin = q("""select c.isin, c.nse_symbol, c.bse_code, c.nse_industry, c.peer_group_key, c.valuation_family,
                       c.fy_end_month, c.price_exchange, c.listed_on
                  from equity.company c join core.entity e on e.id = c.entity_id where e.slug = 'sbin'""")[0]
    assert sbin == ("INE062A01020", "SBIN", "500112", "Financial Services", "banks", "lender", 3, "NSE", date(1995, 3, 1))
    # TMPV carries TATAMOTORS (and TELCO before it) as earlier symbols, from symbolchange.csv
    assert q("""select i.value, lower(i.valid), upper(i.valid) from core.entity_identifier i
                  join core.entity e on e.id = i.entity_id where e.slug = 'tmpv' and scheme = 'nse_symbol'
                 order by lower(i.valid) nulls first""") == [("TELCO", None, date(2003, 12, 26)),
                                                             ("TATAMOTORS", date(2003, 12, 26), date(2025, 10, 24)),
                                                             ("TMPV", date(2025, 10, 24), None)]
    # only master companies can be index members: three of the five lists hold any of the Nifty 50
    snaps = q("""select e.slug, s.snapshot_date, count(*) from equity.index_membership s
                   join core.entity e on e.id = s.index_entity_id group by 1, 2 order by 1""")
    assert snaps == [("nifty-100", SNAP, 50), ("nifty-200", SNAP, 50), ("nifty-50", SNAP, 50)]
    # every raw file is stored before the load, and the snapshots point at theirs
    assert q("""select count(*) from equity.index_membership m join core.raw_file f on f.id = m.raw_file_id
                 where f.source_url like %s""", "%/ind_nifty%list.csv")[0][0] == 150


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_load_writes_nothing(s3):
    ex = Exchange()
    go(ex)
    before = counts()
    code, tr = go(ex)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert counts() == before


@pytest.mark.db
@needs_fixtures
def test_a_symbol_change_closes_one_range_and_opens_the_next(s3):
    ex = Exchange()
    go(ex)
    ex.edit("/content/indices/ind_nifty50list.csv", lambda t: t.replace(",INFY,EQ,", ",INFYX,EQ,"))
    ex.edit("/content/equities/symbolchange.csv", lambda t: t.rstrip("\n") + "\nInfosys Limited,INFY,INFYX,02-NOV-2026\n")
    code, tr = go(ex)
    assert code == 0 and tr[0] == "success", tr
    assert tr[1] == 3                                         # the closed range, the new one, the company row
    assert q("""select i.value, lower(i.valid), upper(i.valid) from core.entity_identifier i
                  join core.entity e on e.id = i.entity_id where e.slug = 'infy' and scheme = 'nse_symbol'
                 order by lower(i.valid) nulls first""") == [("INFOSYSTCH", None, date(2011, 6, 29)),
                                                             ("INFY", date(2011, 6, 29), date(2026, 11, 2)),
                                                             ("INFYX", date(2026, 11, 2), None)]
    assert q("select count(*) from core.entity where slug in ('infy', 'infyx')")[0][0] == 1       # same company, same slug
    assert q("select c.nse_symbol from equity.company c join core.entity e on e.id = c.entity_id where e.slug='infy'") \
        == [("INFYX",)]
    assert counts()[3] == 150                                 # the same people, so no new snapshot


@pytest.mark.db
@needs_fixtures
def test_an_isin_change_closes_the_old_isin_the_day_the_run_sees_it(s3):
    ex = Exchange()
    go(ex)
    new_isin = "INE009A01099"
    ex.edit("/content/indices/ind_nifty50list.csv", lambda t: t.replace("INE009A01021", new_isin))
    for p in ("nifty100", "nifty200"):
        ex.edit(f"/content/indices/ind_{p}list.csv", lambda t: t.replace("INE009A01021", new_isin))
    ex.edit("/content/equities/EQUITY_L.csv", lambda t: t.rstrip("\n") + f"\nINFY,Infosys Limited,EQ,08-FEB-1995,5,1,{new_isin},5,1995-02-08\n")
    code, tr = go(ex)
    assert code == 0 and tr[0] == "success", tr
    assert q("""select i.value, lower(i.valid), upper(i.valid) from core.entity_identifier i
                  join core.entity e on e.id = i.entity_id where e.slug = 'infy' and scheme = 'isin'
                 order by lower(i.valid) nulls first""") == [("INE009A01021", None, SNAP), (new_isin, SNAP, None)]
    assert q("select c.isin, c.bse_code from equity.company c join core.entity e on e.id = c.entity_id "
             "where e.slug='infy'") == [(new_isin, "500209")]      # no BSE row for the new ISIN: the code stays
    assert not checks(tr)["bse_codes"]["passed"] and "INFY" in checks(tr)["bse_codes"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_company_that_leaves_the_list_keeps_its_rows_and_the_next_snapshot_omits_it(s3, monkeypatch):
    ex = Exchange()
    go(ex)
    swap = next(r for r in nifty("nifty-next-50") if r.isin in u.parse_equity_l(read("EQUITY_L_20261019.csv")))
    old = next(r for r in nifty("nifty-50") if r.symbol == "ADANIENT")
    ex.edit("/content/indices/ind_nifty50list.csv", lambda t: t.replace(
        f"{old.name},{old.industry},{old.symbol},EQ,{old.isin}", f"{swap.name},{swap.industry},{swap.symbol},EQ,{swap.isin}"))
    curation = u.load_companies(u.load_peer_groups())
    curation[u.slugify(swap.symbol)] = u.Curation(u.slugify(swap.symbol), "banks", "operating", "test", 3, "NSE")
    monkeypatch.setattr(eu.u, "load_companies", lambda groups, path=None: curation)
    code, tr = go(ex, now=datetime(2026, 11, 4, 6, 0, tzinfo=UTC))
    assert code == 0 and tr[0] == "success", tr
    assert counts()[:2] == (51, 51)                                   # ADANIENT keeps its entity and company rows
    assert q("select count(*) from core.entity_identifier i join core.entity e on e.id = i.entity_id "
             "where e.slug = 'adanient'")[0][0] == 4      # ISIN, ADANIENT, ADANIEXPO, BSE code
    nov4 = date(2026, 11, 4)
    members = q("""select m.snapshot_date, array_agg(e.slug order by e.slug) filter (where e.slug in ('adanient', %s))
                     from equity.index_membership m join core.entity ix on ix.id = m.index_entity_id
                     join core.entity e on e.id = m.entity_id where ix.slug = 'nifty-50' group by 1 order by 1""",
                u.slugify(swap.symbol))
    assert members == [(SNAP, ["adanient"]), (nov4, [u.slugify(swap.symbol)])]
    detail = checks(tr)["membership_changes"]
    assert not detail["passed"] and not detail["blocking"]
    assert f"added {u.slugify(swap.symbol)}; removed adanient" in detail["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_nifty_50_file_with_the_wrong_row_count_fails_and_rolls_the_load_back(s3):
    ex = Exchange()
    ex.edit("/content/indices/ind_nifty50list.csv", lambda t: "\n".join(t.splitlines()[:-1]) + "\n")
    code, tr = go(ex)
    assert code == 1 and tr[0] == "checks_failed" and "index_files" in tr[3], tr
    assert "nifty-50 has 49 rows, not 50" in checks(tr)["index_files"]["detail"]
    assert counts()[:4] == (0, 0, 0, 0)                              # nothing published
    assert q("select count(*) from core.raw_file where source_url like %s", "%ind_nifty50list.csv")[0][0] >= 1  # kept


@pytest.mark.db
@needs_fixtures
def test_a_nifty_200_that_is_not_the_union_fails(s3):
    ex = Exchange()
    first = nifty("nifty-midcap-100")[0]
    ex.edit("/content/indices/ind_nifty200list.csv", lambda t: t.replace(first.isin, "INE000000001"))
    code, tr = go(ex)
    assert code == 1 and not checks(tr)["nifty200_union"]["passed"], tr


@pytest.mark.db
@needs_fixtures
def test_a_universe_isin_missing_from_equity_l_fails(s3):
    ex = Exchange()
    ex.edit("/content/equities/EQUITY_L.csv", lambda t: "\n".join(
        line for line in t.splitlines() if "INE009A01021" not in line) + "\n")
    code, tr = go(ex)
    assert code == 1 and "INFY (INE009A01021)" in checks(tr)["in_equity_l"]["detail"], tr


@pytest.mark.db
@needs_fixtures
def test_a_company_missing_from_the_curated_yaml_fails(s3, monkeypatch):
    ex = Exchange()
    curation = u.load_companies(u.load_peer_groups())
    del curation["tcs"]
    monkeypatch.setattr(eu.u, "load_companies", lambda groups, path=None: curation)
    code, tr = go(ex)
    assert code == 1 and "tcs" in checks(tr)["curated"]["detail"], tr


@pytest.mark.db
@needs_fixtures
def test_bse_being_unreachable_only_warns_and_keeps_the_codes(s3):
    ex = Exchange()
    go(ex)
    del ex.files["/BseIndiaAPI/api/ListofScripData/w"]               # BSE now answers 404
    code, tr = go(ex)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    bse = checks(tr)["bse_codes"]
    assert not bse["passed"] and not bse["blocking"] and "couldn't be read" in bse["detail"]
    assert q("select count(*) from equity.company where bse_code is not null")[0][0] == 49


@pytest.mark.db
@needs_fixtures
def test_the_files_are_asked_for_with_the_nse_and_bse_headers(s3):
    ex = Exchange()
    go(ex)
    nse = [r for r in ex.seen if r.url.host == "nsearchives.nseindia.com"]
    assert len(nse) == 7 and all(r.headers["referer"] == "https://www.nseindia.com/" for r in nse)
    bse = next(r for r in ex.seen if r.url.host == "api.bseindia.com")
    assert bse.headers["origin"] == "https://www.bseindia.com" and bse.url.params["segment"] == "Equity"
    # a one-line proof that the csv module reads what the mock served
    assert len(list(csv.DictReader(io.StringIO(ex.files["/content/indices/ind_nifty50list.csv"].decode())))) == 50
