"""us.filings and the EDGAR adapter (job US-02, #401; Spec 02A.1 §5.2; Data Audit 02a finding 14).

The adapter's rows are checked against SEC's company facts saved in data/fixtures/equity/edgar (the private fixtures
repo: the audit kit's 10 E1 names) and against E1's own results (`equity/audit/E1_latest.json`: the kit's annual and
quarterly figures, its derived fourth quarters and differenced cash flows). The task runs through the runner on the
test database with a mock SEC (the saved files; a made inline XBRL cover page for Alphabet) and a mock object store.
"""
import copy
import gzip
import itertools
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import equity_edgar as helper
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import edgar as E
from ft.equity import us_universe as us
from ft.jobs import config
from ft.jobs.fetch import HttpClient, site_for
from ft.jobs.runner import run
from ft.jobs.tasks import equity_us_filings as task_module
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_us_filings import UsFilings, companyfacts_url, document_url, parse_recent

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
EDGAR = FIX / "edgar"
needs_fixtures = pytest.mark.skipif(not EDGAR.exists(), reason="the saved files aren't checked out (data/fixtures)")
CIK = {"AAPL": 320193, "MSFT": 789019, "NVDA": 1045810, "WMT": 104169, "JPM": 19617, "BRK-B": 1067983,
       "GOOGL": 1652044, "O": 726728, "GEV": 1996810, "XOM": 2115436}
PRED = {"XOM": [34088]}
DOW = ("AAPL", "MSFT", "NVDA", "WMT", "JPM", "GOOGL")          # the E1 names in the Dow
START = date(2019, 1, 1)
D = date


def read(cik: int) -> dict:
    return json.loads(gzip.decompress((EDGAR / f"companyfacts_CIK{cik:010d}.json.gz").read_bytes()))


def statements(ticker: str, tags: E.TagConfig | None = None) -> E.Statements:
    return E.Statements([(c, read(c)) for c in [CIK[ticker], *PRED.get(ticker, [])]], tags or E.load_tags())


def window(s: E.Statements) -> list[E.Filing]:
    return sorted((f for f in s.filings.values() if f.period_end and f.period_end >= START), key=lambda f: (f.filed, f.accn))


def book(s: E.Statements) -> dict:
    """The rows of every filing, the latest filed winning per period (as E1 read company facts)."""
    out = {}
    for f in sorted(s.filings.values(), key=lambda f: (f.filed, f.accn)):
        b = s.build(f)
        for r in (b.rows if b else []):
            out[(r.period_kind, r.period_end)] = r
    return out


E1 = {"revenue": "revenue", "net_income": "pat_owners", "operating_income": "operating_income",
      "total_assets": "total_assets", "total_equity": "equity_owners", "ocf": "cfo", "capex": "capex", "cash": "cash"}
INSTANT = {"total_assets", "equity_owners", "cash"}


def e1_differences(ticker: str) -> list[tuple]:
    e1 = json.loads((FIX / "audit" / "E1_latest.json").read_text())["info"]["per_name"][ticker]["fields"]
    rows = book(statements(ticker))
    out = []
    for field, col in E1.items():
        for when, kind in (("annual", "year"), ("quarters", "quarter")):
            for x in (e1.get(field) or {}).get(when, []):
                end = D.fromisoformat(x["end"])
                r = rows.get(("instant" if col in INSTANT else kind, end))
                v = r.values.get(col) if r else None
                if v is None or v != Decimal(str(x["value"])).quantize(Decimal(1)):
                    out.append((field, when, x["end"]))
    return out


# ---------------------------------------------------------------------------------------------- the rules moved here
def test_the_helper_imports_its_rules_from_the_adapter():
    assert helper.pick_tags is E.pick_tags and helper.load_facts is E.load_facts and helper.F is E.F
    assert helper.fiscal_year_ends is E.fiscal_year_ends and helper.cover_shares is E.cover_shares
    assert helper.PERIODIC_FORMS == E.PERIODIC_FORMS


def test_the_column_map_loads_and_refuses_a_broken_file(tmp_path):
    t = E.load_tags()
    assert t.start == D(2019, 1, 1) and t.tolerance == Decimal("0.005")
    assert [e.tag for e in t.columns["pat_owners"].chain] == ["NetIncomeLoss"]          # tighter than the helper's
    assert t.columns["total_equity"].chain[1] == E.Entry("StockholdersEquity", 1)
    assert t.columns["equity_owners"].chain[1].nci == -1
    assert "InterestPaidNet" not in [e.tag for e in t.columns["finance_cost"].chain]
    assert t.columns["borrowings_current"].parts[:3] == ("LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper")
    assert sorted(t.share_classes) == ["GOOGL", "NKE", "V"]
    assert set(t.columns) <= set(task_module.columns(t)) and "shares_outstanding" in task_module.columns(t)
    text = E.TAGS_FILE.read_text()
    for bad, message in ((text.replace("kind: flow", "kind: stream", 1), "kind 'stream'"),
                         (text.replace("from: 2019-01-01", "from: soon"), "needs `from`"),
                         (text.replace("CommonClassBMember: 1, CapitalClassCMember: 1", "CommonClassBMember: 0, CapitalClassCMember: 1"),
                          "positive number")):
        (tmp_path / "t.yaml").write_text(bad)
        with pytest.raises(E.EdgarError, match=message):
            E.load_tags(tmp_path / "t.yaml")


# ---------------------------------------------------------------------------------------------- against E1
@needs_fixtures
@pytest.mark.parametrize("ticker", [*DOW, "BRK-B", "XOM"])
def test_the_rows_equal_e1s_figures(ticker):
    """Every annual and quarterly figure E1 built (its fourth quarters from the year less nine months, its quarterly
    cash flow differenced) equals the adapter's rows, to the dollar."""
    assert e1_differences(ticker) == []


@needs_fixtures
def test_where_the_rows_differ_from_e1_by_design():
    # Realty Income: NetIncomeLoss stops in 2026, and the column map doesn't fall back to net income after preferred
    # dividends (E1's chain did); its capex tag (capital improvements) isn't in the map
    o = e1_differences("O")
    assert {x for x in o if x[0] == "net_income"} == {("net_income", "quarters", "2026-03-31"),
                                                       ("net_income", "quarters", "2026-06-30")}
    assert {x[0] for x in o} == {"net_income", "capex"}
    # GE Vernova: its FY2023 figures are only comparatives in its first 10-K (it was spun off in April 2024), and a
    # row is a filing's own period
    assert {x[2] for x in e1_differences("GEV")} == {"2023-12-31"}


# ---------------------------------------------------------------------------------------------- the rules
@needs_fixtures
def test_apples_year_ends_in_september_and_its_fourth_quarter_is_derived():
    s = statements("AAPL")
    fy = next(f for f in window(s) if f.form == "10-K" and f.period_end == D(2025, 9, 27))
    b = s.build(fy)
    assert (fy.accn, b.fy_start) == ("0000320193-25-000079", D(2024, 9, 29))
    assert fy.link() == "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"
    q, y, bs = b.rows
    assert (q.period_kind, q.period_start, q.period_end) == ("quarter", D(2025, 6, 29), D(2025, 9, 27))
    assert q.values["revenue"] == 102466000000 and q.tags["revenue"].startswith(
        "derived: RevenueFromContractWithCustomerExcludingAssessedTax FY − 9M filed 2025-08-01")
    assert "eps_basic" not in q.values                                    # a per-share figure is never derived
    assert (y.period_kind, y.values["revenue"], y.values["cfo"]) == ("year", 416161000000, 111482000000)
    assert (bs.period_kind, bs.period_start, bs.values["total_assets"]) == ("instant", None, 359241000000)
    assert b.balance == (True, "assets 359241000000 vs liabilities 285508000000 + temporary equity 0 + equity 73733000000")
    q2 = s.build(next(f for f in window(s) if f.period_end == D(2026, 3, 28)))
    assert [(r.period_kind, r.period_start) for r in q2.rows] == [("quarter", D(2025, 12, 28)), ("half_year", D(2025, 9, 28)),
                                                                  ("instant", None)]
    assert (q2.rows[0].values["cfo"], q2.rows[0].tags["cfo"]) == (
        28702000000, "derived: NetCashProvidedByUsedInOperatingActivities 6M − Q filed 2026-01-30")


@needs_fixtures
def test_jpmorgan_files_no_operating_income():
    s = statements("JPM")
    rows = [r for f in window(s) for r in s.build(f).rows]
    assert len(rows) > 80 and not any("operating_income" in r.values for r in rows)
    assert all(r.values.get("revenue") for r in rows if r.period_kind in ("quarter", "year"))


@needs_fixtures
def test_exxons_history_stays_under_its_predecessors_cik():
    s = statements("XOM")
    w = window(s)
    assert [(f.accn, f.cik, f.form) for f in w[-3:]] == [("0000034088-26-000045", 34088, "10-K"),
                                                          ("0000034088-26-000067", 34088, "10-Q"),
                                                          ("0000034088-26-000093", 2115436, "10-Q")]
    assert w[-1].link().startswith("https://www.sec.gov/Archives/edgar/data/2115436/")
    assert len({f.period_end for f in w if f.annual}) == 7               # 2019 to 2025, across both CIKs


@needs_fixtures
@pytest.mark.parametrize("ticker", list(CIK))
def test_every_year_end_balance_sheet_ties(ticker):
    s = statements(ticker)
    got = [s.build(f).balance for f in window(s) if f.annual]
    assert got and all(ok for ok, _ in got), [x for x in got if not x[0]]
    if ticker == "WMT":                                                   # files no total-liabilities line
        assert all("no total-liabilities line" in how for _, how in got)


@needs_fixtures
@pytest.mark.parametrize("ticker", DOW)
def test_the_period_end_is_secs_report_date(ticker):
    s = statements(ticker)
    listed = parse_recent(gzip.decompress((EDGAR / f"submissions_CIK{CIK[ticker]:010d}.json.gz").read_bytes()))
    both = [f for f in s.filings.values() if f.accn in listed]
    assert len(both) >= 4
    assert [(f.accn, f.period_end, f.form) for f in both if (f.period_end, f.form) !=
            (listed[f.accn].report_date, listed[f.accn].form)] == []


@needs_fixtures
def test_current_debt_is_one_filed_line_or_held():
    def latest(ticker):
        s = statements(ticker)
        r = next(r for r in s.build(window(s)[-1]).rows if r.period_kind == "instant")
        return r.values.get("borrowings_current"), r.tags.get("borrowings_current")
    assert latest("NVDA") == (1000000000, "DebtCurrent")                  # a filed total
    assert latest("MSFT") == (9227000000, "LongTermDebtCurrent")          # the one line it files
    assert latest("AAPL") == (None, ("held: filed in parts (LongTermDebtCurrent 11007000000, CommercialPaper 1997000000) "
                                    "and no total is filed"))
    assert latest("WMT")[0] is None and "ShortTermBorrowings 10479000000" in latest("WMT")[1]


@needs_fixtures
def test_single_class_shares_come_from_the_filings_cover_page():
    s = statements("MSFT")
    r = next(r for r in s.build(window(s)[-1]).rows if r.period_kind == "instant")
    assert (r.values["shares_outstanding"], r.tags["shares_outstanding"]) == (
        7425545491, "dei:EntityCommonStockSharesOutstanding (cover page, as of 2026-07-23)")


@needs_fixtures
def test_tag_switches_are_named():
    s = statements("JPM")
    used: dict[str, set[str]] = {}
    for f in window(s):
        for r in s.build(f).rows:
            for k, v in r.tags.items():
                used.setdefault(k, set()).add(v)
    assert s.switched(used) == ["revenue: Revenues primary, also RevenuesNetOfInterestExpense"]


# ---------------------------------------------------------------------------------------------- made-up facts
def fact(val, end, start=None, accn="0000000001-26-000001", form="10-Q", filed="2026-08-01"):
    e = {"val": val, "end": end, "accn": accn, "form": form, "filed": filed}
    if start:
        e["start"] = start
    return e


def facts(us_gaap: dict, dei: list | None = None) -> dict:
    out = {"cik": 1, "entityName": "Test", "facts": {"us-gaap": {t: {"units": u} for t, u in us_gaap.items()}}}
    if dei is not None:
        out["facts"]["dei"] = {E.SHARES_TAG: {"units": {"shares": dei}}}
    return out


def test_money_in_another_currency_is_never_read_and_equity_fills_adjust_for_nci():
    cf = facts({
        "Revenues": {"EUR": [fact(100, "2026-06-30", "2026-04-01")]},
        "NetIncomeLoss": {"USD": [fact(10, "2026-06-30", "2026-04-01"),
                                  fact(9, "2026-06-30", "2026-04-01", accn="0000000001-26-000002")]},
        "StockholdersEquity": {"USD": [fact(50, "2026-06-30"), fact(40, "2026-06-30", accn="0000000001-26-000002")]},
        "MinorityInterest": {"USD": [fact(5, "2026-06-30")]},
    })
    s = E.Statements([(1, cf)], E.load_tags())
    b = s.build(s.filings["0000000001-26-000001"])
    q_row, bs = b.rows
    assert "revenue" not in q_row.values and q_row.values["pat_owners"] == 10          # EUR is never read
    assert (bs.values["total_equity"], bs.tags["total_equity"]) == (55, "StockholdersEquity+MinorityInterest")
    assert (bs.values["equity_owners"], bs.values["nci"]) == (50, 5)
    other = s.build(s.filings["0000000001-26-000002"]).rows[-1]           # no MinorityInterest at its date: no fill
    assert "total_equity" not in other.values and other.values["equity_owners"] == 40


def test_two_values_for_one_period_in_one_filing_are_held():
    cf = facts({"NetIncomeLoss": {"USD": [fact(10, "2026-06-30", "2026-04-01"), fact(11, "2026-06-30", "2026-04-01")]},
                "IncomeTaxExpenseBenefit": {"USD": [fact(3, "2026-06-30", "2026-04-01")]}})
    s = E.Statements([(1, cf)], E.load_tags())
    r = s.build(next(iter(s.filings.values()))).rows[0]
    assert r.values == {"tax": 3} and r.tags["pat_owners"].startswith("held: NetIncomeLoss has 2 different values")


def test_an_ifrs_filer_is_refused():
    cf = facts({"NetIncomeLoss": {"USD": [fact(1, "2026-06-30", "2026-04-01")]}})
    cf["facts"]["ifrs-full"] = {"Revenue": {"units": {"USD": []}}}
    with pytest.raises(E.EdgarError, match="IFRS"):
        E.Statements([(1, cf)], E.load_tags())


# ---------------------------------------------------------------------------------------------- cover pages
def cover_page(end: str, classes: dict[str, str], *, scale: str | None = "6", cik: str = "0001652044") -> bytes:
    ctx, figs = [], []
    for i, (member, text) in enumerate(classes.items()):
        seg = ("" if member == "total" else
               '<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
               f"us-gaap:{member}</xbrldi:explicitMember></xbrli:segment>")
        ctx.append(f'<xbrli:context id="c-{i}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}'
                   f"</xbrli:identifier>{seg}</xbrli:entity><xbrli:period><xbrli:instant>{end}</xbrli:instant>"
                   "</xbrli:period></xbrli:context>")
        sc = f' scale="{scale}"' if scale else ""
        figs.append(f'<p>{member}: <ix:nonFraction unitRef="shares" contextRef="c-{i}" decimals="-6"{sc} '
                    f'name="dei:EntityCommonStockSharesOutstanding" format="ixt:num-dot-decimal">{text}</ix:nonFraction></p>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:dei="http://xbrl.sec.gov/dei/2025" '
            'xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">'
            '<head><title>10-Q</title></head><body><div style="display:none"><ix:header><ix:resources>'
            + "".join(ctx) + "</ix:resources></ix:header></div>" + "".join(figs) + "</body></html>").encode()


GOOGL_COVER = {"CommonClassAMember": "5,868", "CommonClassBMember": "835", "CapitalClassCMember": "5,527"}


def test_a_multi_class_cover_page_is_summed_by_its_curated_ratios():
    cov = E.read_cover(cover_page("2026-07-15", GOOGL_COVER))
    assert cov == E.Cover(D(2026, 7, 15), {"CommonClassAMember": Decimal(5868000000), "CommonClassBMember": Decimal(835000000),
                                          "CapitalClassCMember": Decimal(5527000000)})
    total, how = E.cover_total(cov, E.load_tags().share_classes["GOOGL"])
    assert total == 12230000000                                                   # E1's cover-page total
    assert how.startswith("cover page, as of 2026-07-15: CapitalClassCMember 5527000000 × 1 + CommonClassAMember")
    # Visa: a class with no curated conversion ratio holds the field, naming it
    v = E.read_cover(cover_page("2026-10-30", {"CommonClassAMember": "1,700", "CommonClassB1Member": "4"}))
    total, how = E.cover_total(v, E.load_tags().share_classes["V"])
    assert total is None and how.startswith("held: no curated conversion ratio for CommonClassB1Member")
    assert E.cover_total(v, None)[0] is None
    assert E.cover_total(E.read_cover(cover_page("2026-10-30", {"total": "7,425,545,491"}, scale=None)), None) == (
        Decimal(7425545491), "cover page, as of 2026-10-30")


def test_a_cover_page_without_a_share_figure_or_with_an_entity_is_refused():
    with pytest.raises(E.EdgarError, match="no cover-page share figure"):
        E.read_cover(cover_page("2026-07-15", {}))
    evil = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b'<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>&e;</body></html>')
    with pytest.raises(E.EdgarError):
        E.read_cover(evil)


def test_the_task_is_wired_as_module_us_after_the_universe():
    t = config.load()["us.filings"]
    assert t.module == "us" and t.rules == {"weekly": "every 6 days"} and not t.uses_nse
    assert t.run_after == ("us.universe",)
    assert registry()["us.filings"] is UsFilings
    assert site_for(companyfacts_url(320193)).key == "sec"
    assert site_for(document_url(1652044, "0001652044-26-000071", "goog-20260630.htm")).key == "sec"


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5641, "us-filings-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.us_filings"
NOW = datetime(2026, 11, 12, 1, 0, tzinfo=UTC)
UA = "Finance Terminal test sec-ua@example.invalid"
GOOGL_DOC = "/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm"


FULL_CONFIG = us.load_us_config


def dow_config() -> us.UsConfig:
    full = FULL_CONFIG()
    return us.UsConfig(full.index, full.as_of, [c for c in full.companies if c.ticker in DOW], full.groups)


class Sec:
    """data.sec.gov and www.sec.gov as a mock transport: the saved submissions and company facts of the six Dow names,
    and Alphabet's latest filing as a made inline XBRL cover page."""

    def __init__(self, change=None):
        self.files: dict[str, bytes] = {GOOGL_DOC: cover_page("2026-07-15", GOOGL_COVER)}
        for t in DOW:
            cik = CIK[t]
            self.files[f"/submissions/CIK{cik:010d}.json"] = gzip.decompress(
                (EDGAR / f"submissions_CIK{cik:010d}.json.gz").read_bytes())
            cf = read(cik)
            if change is not None:
                cf = change(t, cf)
            self.files[f"/api/xbrl/companyfacts/CIK{cik:010d}.json"] = json.dumps(cf).encode()
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        body = self.files.get(request.url.path)
        kind = "text/html" if request.url.path.endswith(".htm") else "application/json"
        return httpx.Response(404) if body is None else httpx.Response(200, content=body, headers={"content-type": kind})


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'us filings test', '{weekly}', '6 days', '10 days', false)
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


US_IDS = "(select id from core.entity where kind = 'company' and market = 'US' and slug like 'us-%')"


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for sql in (f"delete from equity.financials where entity_id in {US_IDS}",
                    f"delete from equity.filing where entity_id in {US_IDS}",
                    f"delete from core.document where entity_id in {US_IDS}",
                    f"delete from core.entity_identifier where entity_id in {US_IDS}",
                    f"delete from core.entity where id in {US_IDS}"):
            c.execute(sql)


def master():
    """The six Dow names in the master, as us.universe leaves them: unpublished, with their CIKs."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        sec = c.execute("select id from core.source where key = 'sec'").fetchone()[0]
        for x in dow_config().companies:
            eid = c.execute("""insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
                               values ('company', %s, %s, %s, 'US', 'USD', false) returning id""",
                            (x.slug, x.name, x.short_name)).fetchone()[0]
            c.execute("""insert into core.entity_identifier (entity_id, scheme, value, valid, source_id)
                         values (%s, 'cik', %s, daterange(null, null, '[)'), %s)""", (eid, str(x.cik), sec))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    master()
    yield
    wipe()


_runs = itertools.count(1)


def go(sec, monkeypatch, ua=UA, version=None):
    monkeypatch.setenv("SEC_USER_AGENT", ua)
    monkeypatch.setattr(task_module.us, "load_us_config", dow_config)

    class T(UsFilings):
        key = KEY
    if version is not None:
        T.version = version
    t = T()
    t.workflows = ("weekly",)
    http = HttpClient(transport=httpx.MockTransport(sec), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "weekly", [t], only=KEY, force=True, http=http, storage=storage, clock=lambda: NOW)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: (c["passed"], c["blocking"]) for c in tr[2] or []}


def detail(tr, name):
    return next(c["detail"] for c in tr[2] if c["name"] == name)


def counts():
    return q(f"""select (select count(*) from equity.filing where entity_id in {US_IDS}),
                        (select count(*) from equity.financials where entity_id in {US_IDS}),
                        (select count(*) from core.document where entity_id in {US_IDS}),
                        (select count(*) from core.document where entity_id in {US_IDS} and is_published),
                        (select count(*) from core.entity where id in {US_IDS} and is_published)""")[0]


def snapshot():
    return q(f"""select d.external_key, fn.period_kind, fn.period_end, fn.period_start, fn.revenue, fn.pat_owners,
                        fn.cfo, fn.total_equity, fn.shares_outstanding, fn.tags
                   from equity.financials fn join core.document d on d.id = fn.document_id
                  where fn.entity_id in {US_IDS} order by 1, 2, 3""")


FILINGS = {"AAPL": 30, "MSFT": 30, "NVDA": 31, "WMT": 31, "JPM": 30, "GOOGL": 30}


@pytest.mark.db
@needs_fixtures
def test_the_first_load_registers_every_filing_since_2019_unpublished(s3, monkeypatch):
    sec = Sec()
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr) == {"company_facts": (True, True), "period_end": (True, True), "balance_sheet": (True, True),
                          "coverage": (True, True), "shares": (True, True), "shares_held": (True, False),
                          "tag_switch": (False, False), "debt_held": (False, False), "loaded": (True, False)}
    assert "AAPL 2026-06-27: filed in parts (LongTermDebtCurrent" in detail(tr, "debt_held")
    assert "WMT" in detail(tr, "balance_sheet") and "no total-liabilities line" in detail(tr, "balance_sheet")
    n_filings = sum(FILINGS.values())
    assert counts()[0] == n_filings and counts()[2:] == (n_filings, 0, 0)     # nothing published
    assert detail(tr, "loaded").startswith(f"{n_filings} filings registered, 0 rebuilt")
    by = dict(q(f"""select e.slug, count(*) from equity.filing fl join core.entity e on e.id = fl.entity_id
                     where fl.entity_id in {US_IDS} group by 1"""))
    assert by == {f"us-{t.lower()}": n for t, n in FILINGS.items()}
    # Apple's FY2025 10-K: the filing, its document and its rows
    assert q("""select d.doc_type, s.key, d.source_url, d.title, d.published_at, f.source_url
                  from core.document d join core.source s on s.id = d.source_id join core.raw_file f on f.id = d.raw_file_id
                 where d.external_key = '0000320193-25-000079'""") == [(
        "results_xbrl", "sec", "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm",
        "Apple Inc. 10-K, period ended 2025-09-27", datetime(2025, 10, 31, 14, 1, 26, tzinfo=UTC),
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")]
    assert q("""select fl.exchange, fl.format, fl.basis, fl.period_end, fl.fy_start, fl.fy_end, fl.audited, fl.parse_status,
                       fl.parser_version
                  from equity.filing fl join core.document d on d.id = fl.document_id
                 where d.external_key = '0000320193-25-000079'""") == [
        ("SEC", "us_gaap", "consolidated", D(2025, 9, 27), D(2024, 9, 29), D(2025, 9, 27), True, "parsed", "1")]
    assert q("""select fn.period_kind, fn.period_start, fn.currency, fn.revenue, fn.operating_income, fn.pat_owners,
                       fn.tags->>'revenue'
                  from equity.financials fn join core.document d on d.id = fn.document_id
                 where d.external_key = '0000320193-25-000079' and fn.period_kind in ('quarter', 'year') order by 1""") == [
        ("quarter", D(2025, 6, 29), "USD", 102466000000, 32427000000, 27466000000,
         "derived: RevenueFromContractWithCustomerExcludingAssessedTax FY − 9M filed 2025-08-01"),
        ("year", D(2024, 9, 29), "USD", 416161000000, 133050000000, 112010000000,
         "RevenueFromContractWithCustomerExcludingAssessedTax")]
    # Alphabet's latest filing: its cover page's three classes; its earlier filings hold the field with the reason
    googl = q("""select fn.shares_outstanding, fn.tags->>'shares_outstanding' from equity.financials fn
                   join core.document d on d.id = fn.document_id
                  where d.external_key = '0001652044-26-000071' and fn.period_kind = 'instant'""")
    assert googl[0][0] == 12230000000 and "CapitalClassCMember 5527000000 × 1" in googl[0][1]
    assert q("""select count(*) from equity.financials fn join core.entity e on e.id = fn.entity_id
                  where e.slug = 'us-googl' and fn.period_kind = 'instant' and fn.shares_outstanding is null
                    and fn.tags->>'shares_outstanding' like 'held: a multi-class%%'""")[0][0] == FILINGS["GOOGL"] - 1
    # every SEC request declared the secret's User-Agent; each reply is a sec raw file
    assert {r.headers["user-agent"] for r in sec.seen} == {UA}
    assert sorted({r.url.host for r in sec.seen}) == ["data.sec.gov", "www.sec.gov"]
    assert len(sec.seen) == 2 * len(DOW) + 1
    assert q("""select count(*) from core.raw_file f join core.source s on s.id = f.source_id
                 where s.key = 'sec' and f.source_url like 'https://data.sec.gov/api/xbrl/companyfacts/%'""")[0][0] >= 6


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_load_writes_nothing(s3, monkeypatch):
    sec = Sec()
    go(sec, monkeypatch)
    before, rows = counts(), snapshot()
    sec.seen.clear()
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert counts() == before and snapshot() == rows
    assert len(sec.seen) == 2 * len(DOW)                         # Alphabet's latest cover page isn't read again


@pytest.mark.db
@needs_fixtures
def test_a_newer_parser_version_rebuilds_the_rows_and_keeps_the_cover_pages_shares(s3, monkeypatch):
    sec = Sec()
    go(sec, monkeypatch)
    rows = snapshot()
    code, tr = go(sec, monkeypatch, version=E.PARSER_VERSION + 1)
    assert code == 0 and tr[0] == "success", tr
    assert detail(tr, "loaded").startswith(f"0 filings registered, {sum(FILINGS.values())} rebuilt")
    assert snapshot() == rows
    assert q(f"select distinct parser_version from equity.filing where entity_id in {US_IDS}") == [(str(E.PARSER_VERSION + 1),)]


@pytest.mark.db
@needs_fixtures
def test_a_year_end_that_doesnt_tie_fails_the_load_and_writes_nothing(s3, monkeypatch):
    def change(t, cf):
        if t != "AAPL":
            return cf
        cf = copy.deepcopy(cf)
        for e in cf["facts"]["us-gaap"]["Assets"]["units"]["USD"]:
            if e["accn"] == "0000320193-25-000079" and e["end"] == "2025-09-27":
                e["val"] = int(e["val"] * 1.02)
        return cf
    _code, tr = go(Sec(change), monkeypatch)
    assert tr[0] == "checks_failed" and checks(tr)["balance_sheet"] == (False, True), tr
    assert "AAPL 2025-09-27: assets" in detail(tr, "balance_sheet")
    assert counts()[:3] == (0, 0, 0)


@pytest.mark.db
@needs_fixtures
def test_a_company_missing_from_the_master_fails_the_load(s3, monkeypatch):
    q("delete from core.entity_identifier where value = '19617' and scheme = 'cik'")
    _code, tr = go(Sec(), monkeypatch)
    assert tr[0] == "checks_failed" and checks(tr)["company_facts"] == (False, True), tr
    assert "JPM (not in the company master" in detail(tr, "company_facts") and counts()[0] == 0


@pytest.mark.db
def test_the_due_rule_waits_for_migration_0039_and_for_the_first_recorded_run(s3):
    t = UsFilings()
    t.rules = {"weekly": "every 6 days"}
    with psycopg.connect(JOB, autocommit=True) as c:
        d = t.due(c, "weekly", NOW)
        assert not d.due and "first recorded run" in d.reason
        assert t.due(c, "refresh", NOW).due
    with psycopg.connect(ADMIN) as c:
        c.execute("update core.source set key = 'secgone' where key = 'sec'")
        d = t.due(c, "refresh", NOW)
        c.rollback()
    assert not d.due and "0039" in d.reason
