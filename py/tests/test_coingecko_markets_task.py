"""crypto.coingecko_markets (issue #106): CoinGecko's daily top-500 snapshot, stored as raw files and nothing else.

The responses are synthetic, written from the documented fields: no CoinGecko data is kept in the repo (its terms,
and CLAUDE.md §4). The checks are tested as a pure function; the task runs through the runner against the test
database as job_writer, with a mock transport and a local S3 server (moto), like the other tasks' tests."""
import itertools
from datetime import UTC, datetime, timedelta

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.jobs import config
from ft.jobs.fetch import SITES, HttpClient, site_for
from ft.jobs.runner import run
from ft.jobs.tasks import coingecko_markets as cg
from ft.jobs.tasks.coingecko_markets import CoingeckoMarkets, Page, evaluate

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5596, "coingecko-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.coingecko_markets"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
DEMO_KEY = "CG-demo-secret-key-106"
_runs = itertools.count(1)


def coin(i: int, now: datetime = NOW) -> dict:
    return {"id": "bitcoin" if i == 0 else f"coin-{i}", "symbol": f"s{i}", "name": f"Coin {i}",
            "current_price": 100.5 / (i + 1), "market_cap": 1_000_000.0 * (500 - i), "market_cap_rank": i + 1,
            "fully_diluted_valuation": 1_500_000.0 * (500 - i), "total_volume": 5000.0,
            "circulating_supply": 1000.0, "total_supply": 1200.0, "max_supply": None,
            "last_updated": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}


def good_pages() -> list[Page]:
    return [Page(n, NOW, [coin(i) for i in range((n - 1) * 250, n * 250)]) for n in (1, 2)]


def named(checks):
    return {c.name: c for c in checks}


# ---------------------------------------------------------------------------------------------- the checks
def test_good_pages_pass_every_check():
    checks = named(evaluate(good_pages(), NOW))
    assert set(checks) == {"shape", "universe", "page_overlap", "fresh", "supply", "nulls"}
    assert all(c.passed for c in checks.values()), {k: c.detail for k, c in checks.items() if not c.passed}
    assert [c.name for c in checks.values() if not c.blocking] == ["page_overlap", "supply", "nulls"]


def test_a_null_value_is_fine_but_a_missing_key_is_not():
    pages = good_pages()
    assert pages[0].rows[1]["max_supply"] is None and named(evaluate(pages, NOW))["shape"].passed
    del pages[1].rows[7]["fully_diluted_valuation"]
    shape = named(evaluate(pages, NOW))["shape"]
    assert not shape.passed and shape.blocking and "fully_diluted_valuation" in shape.detail
    assert list(named(evaluate(pages, NOW))) == ["shape"]          # nothing below is read from a changed response


@pytest.mark.parametrize("bad", [
    lambda pages: pages[1].rows.pop(),                              # 249 rows
    lambda pages: setattr(pages[0], "rows", {"error": "rate limit"}),
    lambda pages: setattr(pages[0], "parse_error", "Expecting value"),
    lambda pages: pages.pop(),                                      # one page only
])
def test_a_broken_response_fails_shape(bad):
    pages = good_pages()
    bad(pages)
    assert not named(evaluate(pages, NOW))["shape"].passed


def test_fewer_than_495_distinct_ids_fails_universe():
    pages = good_pages()
    for i in range(10):                                             # ten page-2 rows repeat page-1 ids: 490 distinct
        pages[1].rows[i]["id"] = pages[0].rows[i + 1]["id"]
    checks = named(evaluate(pages, NOW))
    assert not checks["universe"].passed and checks["universe"].blocking and "490 distinct" in checks["universe"].detail
    assert not checks["page_overlap"].passed and not checks["page_overlap"].blocking


def test_no_bitcoin_fails_universe():
    pages = good_pages()
    pages[0].rows[0]["id"] = "not-bitcoin"
    assert not named(evaluate(pages, NOW))["universe"].passed


def test_a_two_coin_overlap_passes_with_a_warning():
    pages = good_pages()
    pages[1].rows[0]["id"], pages[1].rows[1]["id"] = pages[0].rows[1]["id"], pages[0].rows[2]["id"]
    checks = named(evaluate(pages, NOW))
    assert checks["universe"].passed                                # 498 distinct
    assert not checks["page_overlap"].passed and not checks["page_overlap"].blocking
    assert "2 on both pages" in checks["page_overlap"].detail
    assert all(c.passed for c in checks.values() if c.blocking)


def test_six_percent_stale_rows_fail_fresh_and_five_percent_pass():
    stale = (NOW - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for n, ok in ((30, False), (25, True)):                         # 6% and 5% of 500
        pages = good_pages()
        for r in pages[0].rows[:n]:
            r["last_updated"] = stale
        fresh = named(evaluate(pages, NOW))["fresh"]
        assert fresh.passed is ok and fresh.blocking
    pages = good_pages()
    for r in pages[0].rows[:30]:
        r["last_updated"] = None                                    # no timestamp counts as stale
    assert not named(evaluate(pages, NOW))["fresh"].passed


def test_supply_oddities_warn_without_blocking():
    pages = good_pages()
    pages[0].rows[3].update(circulating_supply=2000.0)              # above total (1200)
    pages[0].rows[4].update(max_supply=1000.0)                      # total (1200) above max
    pages[0].rows[5].update(fully_diluted_valuation=1.0)            # below market cap
    supply = named(evaluate(pages, NOW))["supply"]
    assert not supply.passed and not supply.blocking
    assert "coin-3" in supply.detail and "coin-4" in supply.detail and "coin-5" in supply.detail


def test_nulls_reports_shares():
    pages = good_pages()
    detail = named(evaluate(pages, NOW))["nulls"].detail
    assert "max_supply 100.0%" in detail and "fully_diluted_valuation 0.0%" in detail


# ---------------------------------------------------------------------------------------------- wiring
def test_tasks_yaml_has_the_entry_and_the_site_is_listed():
    t = config.load()["crypto.coingecko_markets"]
    assert t.rules == {"daily": "every 20 hours"} and t.overdue_after == timedelta(days=3)
    assert site_for("https://api.coingecko.com/api/v3/coins/markets", SITES).key == "coingecko"
    assert cg.CoingeckoMarkets.key == "crypto.coingecko_markets"


# ---------------------------------------------------------------------------------------------- through the runner
class Api:
    """CoinGecko's /coins/markets as a mock transport: 250 coins a page."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.pages = {"1": [coin(i) for i in range(250)], "2": [coin(i) for i in range(250, 500)]}
        n = next(_runs)                                         # each instance's bytes are its own: no dedupe by hash
        for rows in self.pages.values():
            for r in rows:
                r["total_volume"] += n

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        assert request.headers.get(cg.KEY_HEADER) == DEMO_KEY
        return httpx.Response(200, json=self.pages[request.url.params["page"]])


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'coingecko test', '{daily}', '20 hours', '3 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


@pytest.fixture(autouse=True)
def demo_key(monkeypatch):
    monkeypatch.setenv(cg.KEY_ENV, DEMO_KEY)


def storage():
    from ft.storage import Storage
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def task() -> CoingeckoMarkets:
    class T(CoingeckoMarkets):
        key = KEY
    t = T()
    t.workflows = ("daily",)
    return t


def go(api, now=NOW):
    http = HttpClient(transport=httpx.MockTransport(api), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, "daily", [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now)
    tr = q("""select status, rows_written, checks, error, http_stats from ops.task_run where task_key = %s
              order by id desc limit 1""", KEY)[0]
    return code, tr


@pytest.mark.db
def test_two_pages_are_stored_as_two_raw_files_with_todays_content_date(s3):
    before = q("select count(*) from core.raw_file f join core.source s on s.id = f.source_id where s.key = 'coingecko'")
    api = Api()
    code, tr = go(api)
    assert code == 0 and tr[0] == "success", tr
    assert tr[1] == 0                                              # no load: the raw files are the record
    assert [(c["name"], c["passed"]) for c in tr[2] if c["blocking"]] == [
        ("shape", True), ("universe", True), ("fresh", True)]
    assert tr[4]["new_raw_files"] == 2 and tr[4]["200"] == 2
    after = q("select count(*) from core.raw_file f join core.source s on s.id = f.source_id where s.key = 'coingecko'")
    assert after[0][0] - before[0][0] == 2
    files = q("""select f.content_date, f.media_type, f.source_url from core.raw_file f
                   join core.source s on s.id = f.source_id where s.key = 'coingecko' order by f.id desc limit 2""")
    assert {r[0] for r in files} == {NOW.date()} and {r[1] for r in files} == {"application/json"}
    assert sorted(httpx.URL(r[2]).params["page"] for r in files) == ["1", "2"]
    asked = [dict(r.url.params) for r in api.seen]
    assert asked[0] == {"vs_currency": "usd", "order": "market_cap_desc", "per_page": "250", "sparkline": "false",
                        "precision": "full", "page": "1"} and asked[1]["page"] == "2"


@pytest.mark.db
def test_the_demo_key_is_sent_as_a_header_and_never_recorded(s3):
    api = Api()
    go(api)
    assert all(r.headers[cg.KEY_HEADER] == DEMO_KEY and DEMO_KEY not in str(r.url) for r in api.seen)
    leaks = q("""select (select count(*) from ops.http_cache where url like %s)
                      + (select count(*) from core.raw_file where source_url like %s)
                      + (select count(*) from ops.task_run where coalesce(error, '') || checks::text like %s)""",
              *[f"%{DEMO_KEY}%"] * 3)
    assert leaks == [(0,)]
    assert q("select count(*) from ops.http_cache where url like %s", "%api.coingecko.com/api/v3/coins/markets%")[0][0] >= 2


@pytest.mark.db
def test_a_bad_page_fails_the_run_but_keeps_the_raw_files(s3):
    api = Api()
    api.pages["2"] = api.pages["2"][:240]                           # a short page: the shape changed
    before = q("select count(*) from core.raw_file f join core.source s on s.id = f.source_id where s.key = 'coingecko'")
    code, tr = go(api)
    assert code == 1 and tr[0] == "checks_failed" and "shape" in tr[3], tr
    after = q("select count(*) from core.raw_file f join core.source s on s.id = f.source_id where s.key = 'coingecko'")
    assert after[0][0] - before[0][0] == 2                         # store once, never lose


@pytest.mark.db
def test_without_the_key_the_run_fails_by_name(s3, monkeypatch):
    monkeypatch.delenv(cg.KEY_ENV)
    code, tr = go(Api())
    assert code == 1 and tr[0] == "failed" and cg.KEY_ENV in tr[3]


@pytest.mark.db
def test_due_skips_with_its_reason_when_the_source_row_or_the_key_is_missing(s3, monkeypatch):
    class NoRow(CoingeckoMarkets):
        source = "coingecko_not_seeded"
    with psycopg.connect(JOB, autocommit=True) as c:
        d = NoRow().due(c, "daily", NOW)
        assert not d.due and "not set up" in d.reason and "migration 0018" in d.reason
        monkeypatch.delenv(cg.KEY_ENV)
        d = CoingeckoMarkets().due(c, "daily", NOW)
        assert not d.due and "not set up" in d.reason and cg.KEY_ENV in d.reason

