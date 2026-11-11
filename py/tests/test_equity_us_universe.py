"""us.universe (job US-01, #400; Spec 02A.1 §5.1): the Dow 30's master from the curated YAML, checked against SEC.

The YAML and its rules are tested on their own and against SEC's files saved in data/fixtures/equity/edgar (the private
fixtures repo: company_tickers_exchange.json and the submissions of the six Dow names the audit kit read); the task
runs through the runner on the test database with a mock SEC (the saved submissions where there are some, replies made
from the YAML for the rest) and a mock object store.
"""
import gzip
import itertools
import json
from datetime import UTC, date, datetime
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
import yaml
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import us_universe as us
from ft.equity.inputs import parse_beta_india
from ft.jobs import config
from ft.jobs.fetch import HttpClient, site_for
from ft.jobs.runner import run
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_us_universe import UsUniverse

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
needs_fixtures = pytest.mark.skipif(not (FIX / "edgar").exists(), reason="the saved files aren't checked out (data/fixtures)")
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5639, "us-universe-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.us_universe"
NOW = datetime(2026, 11, 12, 1, 0, tzinfo=UTC)          # 20:00 on 11 Nov in New York
UA = "Finance Terminal test sec-ua@example.invalid"

# The audit kit's curated Dow 30 (config/curated/dow30.csv, 19 Oct 2026): confirmed on 11 Nov 2026, unchanged.
DOW = ["AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS", "GOOGL", "GS", "HD", "HON", "IBM", "JNJ",
       "JPM", "KO", "MCD", "MMM", "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT"]
SAVED = {"AAPL": 320193, "MSFT": 789019, "NVDA": 1045810, "WMT": 104169, "JPM": 19617, "GOOGL": 1652044}


def cfg() -> us.UsConfig:
    return us.load_us_config()


def saved_submission(cik: int) -> bytes:
    return gzip.decompress((FIX / "edgar" / f"submissions_CIK{cik:010d}.json.gz").read_bytes())


def made_submission(c: us.UsCompany, **over) -> bytes:
    """SEC's submissions reply for a company, as the YAML says it should read (what a correct YAML meets)."""
    mmdd = {1: "0131", 5: "0531", 6: "0630", 7: "0726", 9: "0930", 12: "1231"}[c.fy_end_month]
    d = {"cik": f"{c.cik:010d}", "name": c.name.upper(), "sic": str(c.sic), "sicDescription": "As filed",
         "fiscalYearEnd": mmdd, "tickers": [c.ticker], "exchanges": ["Nasdaq" if c.exchange == "NASDAQ" else "NYSE"],
         "filings": {"recent": {"form": ["4", "10-Q", "8-K", "10-K"],
                                "filingDate": ["2026-11-06", "2026-10-30", "2026-10-02", "2026-02-12"]}}}
    d.update(over)
    return json.dumps(d).encode()


# ---------------------------------------------------------------------------------------------- the curated files
def test_the_yaml_is_the_dow_30_with_its_groups_families_and_notes():
    c = cfg()
    assert c.index == "dow-jones" and c.as_of == date(2026, 11, 11)
    assert sorted(x.ticker for x in c.companies) == DOW
    assert all(x.slug == "us-" + x.ticker.lower() for x in c.companies)
    fam = {}
    for x in c.companies:
        fam.setdefault(x.valuation_family, []).append(x.ticker)
    assert {k: sorted(v) for k, v in fam.items() if k != "operating"} == {
        "lender": ["AXP", "GS", "JPM"], "insurer": ["TRV"], "mixed": ["CAT", "IBM", "UNH"]}
    assert all(x.family_note for x in c.companies if x.valuation_family != "operating")
    assert {x.ticker: x.predecessor_ciks for x in c.companies if x.predecessor_ciks} == {"DIS": (1001039,)}
    assert sorted(x.ticker for x in c.companies if x.multi_class) == ["GOOGL", "NKE", "V"]
    sizes = {g: sum(x.peer_group == g for x in c.companies) for g in c.groups}
    assert len(c.groups) == 13 and min(sizes.values()) == 1 and sum(sizes.values()) == 30
    assert all(k.startswith("us-") for k in c.groups)


@needs_fixtures
def test_every_damodaran_industry_is_a_us_betas_name():
    _, rows = parse_beta_india((FIX / "damodaran" / "betas_us_betas.xls").read_bytes())
    names = {r.industry for r in rows}
    assert len(names) > 90
    assert sorted(g.damodaran_industry for g in cfg().groups.values() if g.damodaran_industry not in names) == []


@needs_fixtures
def test_every_cik_ticker_and_exchange_matches_secs_ticker_file():
    d = json.loads(gzip.decompress((FIX / "edgar" / "company_tickers_exchange.json.gz").read_bytes()))
    sec = {r[d["fields"].index("ticker")]: r for r in d["data"]}
    i_cik, i_ex = d["fields"].index("cik"), d["fields"].index("exchange")
    wrong = [f"{c.ticker}: {sec.get(c.ticker)}" for c in cfg().companies
             if c.ticker not in sec or sec[c.ticker][i_cik] != c.cik
             or us.SEC_EXCHANGE.get(sec[c.ticker][i_ex]) != c.exchange]
    assert wrong == []


@needs_fixtures
@pytest.mark.parametrize("ticker", sorted(SAVED))
def test_the_saved_submissions_agree_with_the_yaml(ticker):
    c = next(x for x in cfg().companies if x.ticker == ticker)
    s = us.parse_submissions(saved_submission(SAVED[ticker]))
    found = us.compare(c, s, date(2026, 10, 20))                   # the files were saved in October 2026
    assert (found.blocking, found.warnings) == ([], [])


def write_yaml(tmp_path, companies=None, groups=None, **top):
    base_c = yaml.safe_load(us.COMPANIES_FILE.read_text())
    base_g = yaml.safe_load(us.PEER_GROUPS_FILE.read_text())
    if companies is not None:
        base_c["companies"] = companies(base_c["companies"])
    if groups is not None:
        base_g["groups"] = groups(base_g["groups"])
    base_c.update(top)
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(base_c))
    (tmp_path / "g.yaml").write_text(yaml.safe_dump(base_g))
    return tmp_path / "c.yaml", tmp_path / "g.yaml"


def edit(which, **over):
    def fn(rows):
        return [{**r, **over} if r["ticker"] == which else r for r in rows]
    return fn


@pytest.mark.parametrize("change, message", [
    ({"companies": edit("JPM", family_note=None)}, "JPM: a lender needs its family_note"),
    ({"companies": edit("AAPL", peer_group="us-nope")}, "peer group 'us-nope' isn't in"),
    ({"companies": edit("AAPL", exchange="LSE")}, "exchange 'LSE' isn't one of NYSE, NASDAQ"),
    ({"companies": edit("AAPL", fy_end_month=13)}, "AAPL: fy_end_month must be 1 to 12"),
    ({"companies": edit("AAPL", cik=789019)}, "the CIK 789019 appears twice"),
    ({"companies": edit("DIS", predecessor_ciks=[1744489])}, "a predecessor isn't the company's own CIK"),
    ({"companies": lambda rows: rows[:-1]}, "lists 29 companies; the Dow has 30"),
    ({"companies": lambda rows: rows + [rows[0]]}, "the ticker AAPL appears twice"),
    ({"groups": lambda g: {**g, "us-spare": {"label": "x", "damodaran_industry": "Machinery", "source_note": "x"}}},
     "no company is in us-spare"),
    ({"groups": lambda g: {**g, "banks": g["us-banks"]}}, "keys are us- plus"),
    ({"index": "nifty-50"}, "the US slice is the Dow"),
])
def test_the_validator_refuses_a_broken_file(tmp_path, change, message):
    c, g = write_yaml(tmp_path, **change)
    with pytest.raises(us.UniverseError, match=message.replace("(", r"\(").replace(")", r"\)")):
        us.load_us_config(c, g)


# ---------------------------------------------------------------------------------------------- SEC's record
@pytest.mark.parametrize("mmdd, month", [("1231", 12), ("0926", 9), ("1003", 9), ("0201", 1), ("0103", 12),
                                         ("0131", 1), ("0726", 7), ("0531", 5), (None, None), ("13xx", None)])
def test_the_fiscal_year_end_month(mmdd, month):
    assert us.fy_end_month(mmdd) == month


def company(ticker="AAPL"):
    return next(x for x in cfg().companies if x.ticker == ticker)


def test_compare_blocks_a_wrong_ticker_or_cik_a_foreign_filer_and_no_10k():
    c = company()
    today = date(2026, 11, 11)
    s = us.parse_submissions(made_submission(c, tickers=["APLE"], exchanges=["Nasdaq"]))
    assert us.compare(c, s, today).blocking == ["AAPL: not among CIK 320193's tickers on SEC (APLE)"]
    s = us.parse_submissions(made_submission(c, cik="0000000001"))
    assert "SEC answered for CIK 1, not 320193" in us.compare(c, s, today).blocking[0]
    s = us.parse_submissions(made_submission(c, filings={"recent": {"form": ["20-F", "6-K"],
                                                                   "filingDate": ["2026-03-01", "2026-02-01"]}}))
    assert us.compare(c, s, today).blocking == ["AAPL: files 20-F (the slice reads 10-K filers only)",
                                                "AAPL: no 10-K among its recent SEC filings"]


def test_compare_warns_on_exchange_sic_fiscal_year_and_an_old_last_filing():
    c = company()
    s = us.parse_submissions(made_submission(c, exchanges=["NYSE"], sic="3572", fiscalYearEnd="1231",
                                             filings={"recent": {"form": ["10-K"], "filingDate": ["2025-10-31"]}}))
    found = us.compare(c, s, date(2026, 11, 11))
    assert found.blocking == []
    assert found.warnings == [
        "AAPL: listed on NYSE at SEC, NASDAQ in the YAML",
        "AAPL: SIC 3572 (As filed) at SEC, 3571 in the YAML",
        "AAPL: fiscal year ends 1231 at SEC (month 12), month 9 in the YAML",
        "AAPL: no 10-K or 10-Q filed under CIK 320193 since 2026-04-25 (a new holding company's CIK?)"]


def test_a_reply_that_isnt_secs_json_is_refused():
    for body in (b"<html>", b"[]", b'{"cik": "1"}'):
        with pytest.raises(us.UniverseError):
            us.parse_submissions(body)


def test_the_user_agent_comes_from_the_secret_and_its_absence_names_it():
    assert us.sec_user_agent({"SEC_USER_AGENT": f"  {UA} "}) == UA
    for env in ({}, {"SEC_USER_AGENT": "  "}):
        with pytest.raises(us.UniverseError, match="SEC_USER_AGENT is empty.*gh secret set SEC_USER_AGENT"):
            us.sec_user_agent(env)


def test_the_task_is_wired_as_module_us_and_sec_is_a_listed_site():
    t = config.load()["us.universe"]
    assert t.module == "us" and t.rules == {"weekly": "every 6 days"} and not t.uses_nse
    assert registry()["us.universe"] is UsUniverse
    assert site_for(us.submissions_url(320193)).key == "sec"
    assert site_for("https://www.sec.gov/Archives/edgar/data/320193/x.htm").key == "sec"
    quality = yaml.safe_load((config.CONFIG.parent / "quality.yaml").read_text())
    assert "us" not in quality["modules"]          # the hidden slice moves no count on /accuracy (US-09 adds it)


def test_slugs_never_look_indian():
    assert us.slug("BRK.B") == "us-brk-b" and us.slug("GOOGL") == "us-googl"


# ---------------------------------------------------------------------------------------------- through the runner
class Sec:
    """data.sec.gov as a mock transport: the saved submissions where there are some, else made from the YAML."""

    def __init__(self, fixtures: bool = True):
        self.files: dict[str, bytes] = {}
        for c in cfg().companies:
            body = saved_submission(SAVED[c.ticker]) if fixtures and c.ticker in SAVED else made_submission(c)
            self.files[f"/submissions/CIK{c.cik:010d}.json"] = body
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        body = self.files.get(request.url.path)
        return httpx.Response(404) if body is None else httpx.Response(200, content=body,
                                                                       headers={"content-type": "application/json"})


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'us universe test', '{weekly}', '6 days', '10 days', false)
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
        us_ids = "(select id from core.entity where kind = 'company' and market = 'US' and slug like 'us-%')"
        for sql in ("delete from equity.index_membership where index_entity_id = (select id from core.entity where slug = 'dow-jones')",
                    f"delete from equity.company where entity_id in {us_ids}",
                    f"delete from core.entity_identifier where entity_id in {us_ids}",
                    f"delete from core.entity where id in {us_ids}",
                    "delete from equity.peer_group where key like 'us-%'"):
            c.execute(sql)


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


_runs = itertools.count(1)


def go(sec, monkeypatch, ua=UA):
    if ua is None:
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("SEC_USER_AGENT", ua)

    class T(UsUniverse):
        key = KEY
    t = T()
    t.workflows = ("weekly",)
    http = HttpClient(transport=httpx.MockTransport(sec), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "weekly", [t], only=KEY, force=True, http=http, storage=storage, clock=lambda: NOW)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: (c["passed"], c["blocking"]) for c in tr[2] or []}


def counts():
    return q("""select (select count(*) from core.entity where kind = 'company' and market = 'US'),
                       (select count(*) from core.entity where kind = 'company' and market = 'US' and is_published),
                       (select count(*) from equity.company c join core.entity e on e.id = c.entity_id where e.market = 'US'),
                       (select count(*) from core.entity_identifier i join core.entity e on e.id = i.entity_id
                         where e.market = 'US' and e.kind = 'company'),
                       (select count(*) from equity.index_membership m join core.entity i on i.id = m.index_entity_id
                         where i.slug = 'dow-jones'),
                       (select count(*) from equity.peer_group where key like 'us-%')""")[0]


@pytest.mark.db
@needs_fixtures
def test_the_first_load_makes_thirty_unpublished_us_companies_and_the_dow(s3, monkeypatch):
    sec = Sec()
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr) == {"submissions": (True, True), "identity": (True, True), "master": (True, True),
                          "membership": (True, True), "exchange": (True, False), "sic_fy": (True, False),
                          "recent_filing": (True, False)}
    assert counts() == (30, 0, 30, 60, 30, 13)
    assert q("""select e.slug, e.name, e.short_name, e.market, e.currency, c.isin, c.nse_symbol, c.peer_group_key,
                       c.valuation_family, c.fy_end_month, c.price_exchange
                  from core.entity e join equity.company c on c.entity_id = e.id where e.slug = 'us-aapl'""") == [
        ("us-aapl", "Apple Inc.", "Apple", "US", "USD", None, None, "us-hardware", "operating", 9, "NASDAQ")]
    assert q("""select i.scheme, i.value, lower(i.valid), upper(i.valid), s.key from core.entity_identifier i
                  join core.entity e on e.id = i.entity_id join core.source s on s.id = i.source_id
                 where e.slug = 'us-dis' order by 1""") == [("cik", "1744489", None, None, "sec"),
                                                            ("ticker", "DIS", None, None, "sec")]
    assert q("select market, label from equity.peer_group where key = 'us-banks'") == [
        ("US", "Banks, investment banks and card lenders")]
    # one Dow snapshot, dated the YAML's as_of, pointing at the curated file's raw file
    assert q("""select m.snapshot_date, count(*), min(s.key), min(f.media_type), min(f.source_url)
                  from equity.index_membership m join core.raw_file f on f.id = m.raw_file_id
                  join core.source s on s.id = f.source_id
                 where m.index_entity_id = (select id from core.entity where slug = 'dow-jones') group by 1""") == [
        (date(2026, 11, 11), 30, "curated", "text/yaml",
         "https://github.com/anandbenny-spec/finance-terminal/blob/main/py/config/equity/us_companies.yaml")]
    # every SEC request: one per company, declaring the secret's User-Agent; each reply stored as a sec raw file
    assert len(sec.seen) == 30 and {r.headers["user-agent"] for r in sec.seen} == {UA}
    assert {r.url.host for r in sec.seen} == {"data.sec.gov"}
    assert q("""select count(distinct f.source_url) from core.raw_file f join core.source s on s.id = f.source_id
                 where s.key = 'sec' and f.source_url like 'https://data.sec.gov/submissions/CIK%.json'""")[0][0] == 30
    # India's universe would see none of them (the market filter)
    assert q("""select count(*) from core.entity_identifier i join core.entity e on e.id = i.entity_id
                 where e.kind = 'company' and e.market = 'IN' and i.scheme in ('cik', 'ticker')""") == [(0,)]


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_load_writes_nothing_and_a_flip_survives_it(s3, monkeypatch):
    sec = Sec()
    go(sec, monkeypatch)
    before = counts()
    q("update core.entity set is_published = true where kind = 'company' and market = 'US' returning id")   # the flip
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert counts() == (30, 30, *before[2:])                     # still published: the task never touches the flag


@pytest.mark.db
@needs_fixtures
def test_a_ticker_change_closes_the_old_range_and_a_list_change_needs_a_new_snapshot(s3, monkeypatch, tmp_path):
    sec = Sec()
    go(sec, monkeypatch)
    rows = yaml.safe_load(us.COMPANIES_FILE.read_text())
    rows["companies"] = edit("HON", ticker="HONT")(rows["companies"])
    rows["as_of"] = date(2026, 11, 20)
    path = tmp_path / "us_companies.yaml"
    path.write_text(yaml.safe_dump(rows))
    monkeypatch.setattr(us, "COMPANIES_FILE", path)
    hon = next(c for c in us.load_us_config(path).companies if c.ticker == "HONT")
    sec.files[f"/submissions/CIK{hon.cik:010d}.json"] = made_submission(hon)
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success", tr
    assert q("""select i.value, upper(i.valid) from core.entity_identifier i join core.entity e on e.id = i.entity_id
                 where e.slug = 'us-hon' and i.scheme = 'ticker' order by 2 nulls last""") == [
        ("HON", date(2026, 11, 11)), ("HONT", None)]              # the slug stays: fixed at creation
    assert q("""select snapshot_date, count(*) from equity.index_membership
                 where index_entity_id = (select id from core.entity where slug = 'dow-jones') group by 1 order by 1""") == [
        (date(2026, 11, 11), 30)]                                  # the members didn't change: no new snapshot


@pytest.mark.db
@needs_fixtures
def test_a_ticker_secs_record_doesnt_list_fails_the_load_and_writes_nothing(s3, monkeypatch):
    sec = Sec()
    c = company("KO")
    sec.files[f"/submissions/CIK{c.cik:010d}.json"] = made_submission(c, tickers=["COKE"])
    _code, tr = go(sec, monkeypatch)
    assert tr[0] == "checks_failed" and checks(tr)["identity"] == (False, True), tr
    assert "KO: not among CIK 21344's tickers on SEC (COKE)" in next(x["detail"] for x in tr[2] if x["name"] == "identity")
    assert counts() == (0, 0, 0, 0, 0, 0)


@pytest.mark.db
@needs_fixtures
def test_a_mismatched_sic_or_year_end_is_a_warning_naming_the_company(s3, monkeypatch):
    sec = Sec()
    c = company("MMM")
    sec.files[f"/submissions/CIK{c.cik:010d}.json"] = made_submission(c, sic="2670", sicDescription="Converted Paper")
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success"
    assert checks(tr)["sic_fy"] == (False, False)
    assert next(x["detail"] for x in tr[2] if x["name"] == "sic_fy") == \
        "MMM: SIC 2670 (Converted Paper) at SEC, 3841 in the YAML"


@pytest.mark.db
@needs_fixtures
def test_an_unreadable_record_fails_the_load(s3, monkeypatch):
    sec = Sec()
    del sec.files[f"/submissions/CIK{company('V').cik:010d}.json"]
    _code, tr = go(sec, monkeypatch)
    assert tr[0] == "checks_failed" and checks(tr)["submissions"] == (False, True)
    assert counts()[0] == 0


@pytest.mark.db
def test_without_the_secret_the_task_fails_at_once_naming_it(s3, monkeypatch):
    sec = Sec(fixtures=False)
    _code, tr = go(sec, monkeypatch, ua=None)
    assert tr[0] == "failed" and "SEC_USER_AGENT is empty" in tr[3] and sec.seen == []


@pytest.mark.db
def test_the_due_rule_waits_for_migration_0039_and_for_the_first_recorded_run(s3):
    t = UsUniverse()
    t.rules = {"weekly": "every 6 days"}
    with psycopg.connect(JOB, autocommit=True) as c:
        d = t.due(c, "weekly", NOW)
        assert not d.due and "first recorded run" in d.reason      # equity_due: no scheduled run before US-07's
        assert t.due(c, "refresh", NOW).due
    with psycopg.connect(ADMIN) as c:                               # rolled back: the source missing
        c.execute("update core.source set key = 'secgone' where key = 'sec'")
        d = t.due(c, "refresh", NOW)
        c.rollback()
    assert not d.due and "0039" in d.reason
