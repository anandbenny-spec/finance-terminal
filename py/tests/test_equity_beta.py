"""equity.beta (job 2A-12): the regression beta, the bottom-up beta, the checks, and the task through the runner.

The prices are synthetic (the saved fixtures hold only sample months): a price path built as 1.3 × the Nifty's weekly
returns plus small noise, flat within each week so that every week's last session carries that week's value."""
import json
import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer
from test_equity_inputs import FIX, needs_fixtures

from ft.equity import beta as bt
from ft.equity import inputs as inp
from ft.jobs import config
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_beta import EquityBeta

D = date
LAST_FRIDAY = D(2026, 10, 23)                    # the newest Nifty date: its ISO week isn't complete
LAST_WEEK_END = D(2026, 10, 16)                  # so the window ends with the week to Friday 16 Oct


def weekdays(first_monday: D, weeks: int) -> list[D]:
    return [first_monday + timedelta(days=7 * w + d) for w in range(weeks) for d in range(5)]


def paths(weeks: int, beta: float = 1.3, noise: float = 0.0005):
    """(index, stock) closes by weekday for `weeks` weeks ending Friday 23 Oct 2026, both flat within a week."""
    first = LAST_FRIDAY - timedelta(days=4) - timedelta(days=7 * (weeks - 1))
    index, stock, i, s = {}, {}, 1000.0, 500.0
    for w in range(weeks):
        if w:
            r = 0.012 * math.sin(w * 1.7) + 0.006 * math.cos(w * 0.31)
            i *= 1 + r
            s *= 1 + beta * r + noise * math.cos(w * 2.9)
        for d in weekdays(first + timedelta(days=7 * w), 1):
            index[d], stock[d] = Decimal(repr(i)), Decimal(repr(s))
    return index, stock


def test_a_stock_built_as_1_3_times_the_nifty_gets_a_slope_of_1_3_over_104_weeks():
    index, stock = paths(125)
    r = bt.regress(stock, index, LAST_FRIDAY)
    assert r.held is None and r.n == 104
    assert abs(r.slope - Decimal("1.3")) < Decimal("0.05")
    assert Decimal("0.99") < r.r2 <= 1 and 0 < r.stderr < Decimal("0.02")
    assert r.last_date == LAST_WEEK_END and r.inputs["last_week_close"] == "2026-10-16"
    assert r.inputs["weeks_without_common_date"] == 0 and r.inputs["returns"] == 104


def test_the_window_ends_with_the_last_complete_iso_week():
    assert bt.last_complete_week(D(2026, 10, 23)) == (2026, 42)          # Friday of week 43: week 42 is the last whole
    assert bt.last_complete_week(D(2026, 10, 25)) == (2026, 43)          # a Sunday closes its own week
    assert bt.last_complete_week(D(2027, 1, 1)) == (2026, 52)            # across a year end
    assert bt.week_index((2027, 1)) - bt.week_index((2026, 53)) == 1 and bt.week_index((2026, 53)) - bt.week_index(
        (2026, 52)) == 1                                                  # 2026 has 53 ISO weeks


def test_a_special_saturday_session_missing_from_the_nifty_pairs_on_the_latest_common_date():
    """1 Feb 2020 was a Saturday budget session: the stock has a close, Yahoo's ^NSEI doesn't (Data Audit 02a finding
    23). The week pairs on its Friday, so a Saturday close five times the week's other days leaves the slope alone."""
    index, stock = paths(125)
    saturday = D(2026, 8, 1)                                             # Saturday of an ISO week inside the window
    friday = saturday - timedelta(days=1)
    stock[saturday] = stock[friday] * 5
    r = bt.regress(stock, index, LAST_FRIDAY)
    assert r.n == 104 and abs(r.slope - Decimal("1.3")) < Decimal("0.05")
    assert r.inputs["weeks_on_an_earlier_common_date"] == 1 and r.inputs["weeks_without_common_date"] == 0


def test_a_week_with_no_common_date_is_dropped_and_counted_with_the_returns_across_it():
    index, stock = paths(125)
    for d in weekdays(D(2026, 8, 3), 1):                                 # Monday to Friday of one week: no Nifty
        del index[d]
    r = bt.regress(stock, index, LAST_FRIDAY)
    assert r.inputs["weeks_without_common_date"] == 1 and r.n == 102     # the returns into and out of that week go
    assert r.held is None


def test_80_returns_are_enough_and_79_are_held():
    index, stock = paths(82)                         # the last of the weeks is incomplete: 81 closes, 80 returns
    r = bt.regress(stock, index, LAST_FRIDAY)
    assert r.n == 80 and r.held is None
    index, stock = paths(81)
    r = bt.regress(stock, index, LAST_FRIDAY)
    assert r.n == 79 and r.held == "Fewer than 80 weeks of prices." and r.slope is None
    assert r.inputs["returns"] == 79


def test_a_stock_that_moves_against_the_nifty_has_a_negative_beta_and_r2_is_a_fraction():
    index, stock = paths(125, beta=-0.6, noise=0.002)
    r = bt.regress(stock, index, LAST_FRIDAY)
    assert Decimal("-0.7") < r.slope < Decimal("-0.5") and 0 < r.r2 < 1


def test_relevering_by_hand():
    """Auto & Truck in the saved betaIndia (edition 5 Jan 2026): unlevered beta corrected for cash 1.107021332292788.
    At debt 500 and market cap 1,000 (D/E 0.5) and the 25.168% rate: 1 + (1 − 0.25168) × 0.5 = 1.37416, and
    1.107021332292788 × 1.37416 = 1.107021332 + 1.107021332 × 0.37416 = 1.107021332 + 0.414203101 = 1.521224433."""
    b, de = bt.relever(Decimal("1.107021332292788"), Decimal(500), Decimal(1000), Decimal("0.25168"))
    assert de == Decimal("0.5") and abs(b - Decimal("1.521224")) < Decimal("0.000002")


@needs_fixtures
def test_relevering_uses_the_saved_betaindia_row_and_the_configured_rate():
    _, rows = inp.parse_beta_india((FIX / "damodaran" / "beta_india_betaIndia.xls").read_bytes())
    auto = next(r for r in rows if r.industry == "Auto & Truck")
    rate, source = bt.tax_rate()
    assert rate == Decimal("0.25168") and "Income-tax Act 2025" in source
    b, _ = bt.relever(auto.unlevered_beta_cash_corrected, Decimal(500), Decimal(1000), rate)
    assert abs(b - Decimal("1.521224")) < Decimal("0.000002")


@needs_fixtures
def test_every_curated_peer_group_s_industry_is_in_the_saved_betaindia_edition():
    """The stop condition of the card: a group whose Damodaran industry isn't in the edition needs Anand's look."""
    import yaml
    _, rows = inp.parse_beta_india((FIX / "damodaran" / "beta_india_betaIndia.xls").read_bytes())
    names = {r.industry for r in rows}
    groups = yaml.safe_load((bt.CONFIG / "peer_groups.yaml").read_text("utf-8"))
    assert [k for k, v in groups.items() if v["damodaran_industry"] not in names] == []


def test_the_tax_rate_needs_a_source_and_a_rate_between_0_and_1(tmp_path):
    p = tmp_path / "v.yaml"
    p.write_text("india_marginal_tax_rate: {rate: 1.5, source: x}\n")
    with pytest.raises(ValueError):
        bt.tax_rate(p)
    p.write_text("india_marginal_tax_rate: {rate: 0.25}\n")
    with pytest.raises(ValueError):
        bt.tax_rate(p)


def test_the_task_is_wired_in_tasks_yaml():
    t = config.load()["equity.beta"]
    assert t.rules == {"weekly": "every 6 days"}
    assert t.run_after == ("equity.metrics", "equity.valuation_inputs", "policy.market_series")
    assert t.uses_nse is False and registry()["equity.beta"] is EquityBeta


def test_the_metrics_task_never_deletes_the_betas_this_task_stores():
    """2A-11's stale-row delete skips the two beta keys (they are market values with history)."""
    import inspect

    from ft.jobs.tasks.equity_metrics import EquityMetrics
    assert '"beta_regression"' in inspect.getsource(EquityMetrics.load)


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5601, "equity-beta-raw"
KEY = "test.equity_beta"
NOW = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
EDITION = D(2026, 1, 5)


def storage():
    from ft.storage import Storage
    return Storage(f"http://127.0.0.1:{PORT}", BUCKET, "test", "test", region="us-east-1")


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity beta test', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=f"http://127.0.0.1:{PORT}", region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zb-%')"
        c.execute("delete from ops.alert where task_key = %s", (KEY,))
        c.execute("""delete from core.observation where task_run_id in
                        (select id from ops.task_run where task_key = 'test.equity_beta_seed')""")
        for sql in (f"delete from equity.metric_value where entity_id in {ids}",
                    f"delete from equity.price_daily where entity_id in {ids}",
                    f"delete from equity.company where entity_id in {ids}",
                    f"delete from core.entity_identifier where entity_id in {ids}",
                    "delete from core.entity where slug like 'zb-%'",
                    "delete from equity.industry_benchmark where industry like 'ZB %'",
                    "delete from equity.peer_group where key like 'zb-%'",
                    "delete from core.raw_file where object_key = 'zb-raw'"):
            c.execute(sql)


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def seed():
    """Four companies on synthetic prices and a synthetic Nifty: ZA operating with debt and market cap, ZL a lender,
    ZN operating with no balance-sheet figure, ZS listed 60 weeks ago."""
    index, stock = paths(125)
    _, short = paths(60)
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values ('test.equity_beta_seed', 'test', 'seed', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""")
        job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('manual', 'cli', 'local') "
                        "returning id").fetchone()[0]
        run_id = c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, "
                           "'test.equity_beta_seed', 'success') returning id", (job,)).fetchone()[0]
        raw = c.execute("""insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at,
                                                       task_run_id)
                           select 'zb-raw', s.id, sha256('zb'::bytea), 1, 'text/csv', now(), %s from core.source s
                            where s.key = 'nse' returning id""", (run_id,)).fetchone()[0]
        c.executemany("""insert into core.observation (series_id, obs_date, value, task_run_id)
                         select s.id, %s, %s, %s from core.series s where s.key = 'yf.^NSEI'
                         on conflict do nothing""", [(d, v, run_id) for d, v in index.items()])
        for industry, unlevered, levered in (("ZB Operating", "1.0", "1.3"), ("ZB Lending", "0.5", "0.9")):
            c.execute("""insert into equity.industry_benchmark (edition, market, industry, unlevered_beta_cash_corrected,
                                                                beta, raw_file_id) values (%s, 'IN', %s, %s, %s, %s)""",
                      (EDITION, industry, unlevered, levered, raw))
        for key, industry in (("zb-op", "ZB Operating"), ("zb-len", "ZB Lending")):
            c.execute("insert into equity.peer_group (key, label, damodaran_industry, source_note) values (%s, %s, %s, 't')",
                      (key, key, industry))
        for slug, family, group, prices in (("zb-a", "operating", "zb-op", stock), ("zb-l", "lender", "zb-len", stock),
                                            ("zb-n", "operating", "zb-op", stock), ("zb-s", "operating", "zb-op", short)):
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', false) returning id""", (slug, slug)).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, fy_end_month, valuation_family, peer_group_key)
                         values (%s, 3, %s, %s)""", (eid, family, group))
            c.executemany("""insert into equity.price_daily (entity_id, trade_date, exchange, series, close, traded_value,
                                                             raw_file_id, task_run_id)
                             values (%s, %s, 'NSE', 'EQ', %s, 1e9, %s, %s)""",
                          [(eid, d, v, raw, run_id) for d, v in prices.items()])
            if slug == "zb-a":
                for key, kind, end, value in (("market_cap", "market", LAST_FRIDAY, 1000), ("debt", "instant",
                                                                                          D(2026, 9, 30), 500)):
                    c.execute("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end,
                                                                  value, inputs)
                                 values (%s, %s, %s, %s, %s, %s, %s)""",
                              (eid, key, "none" if kind == "market" else "consolidated", kind, end, value,
                               json.dumps({"label": "excluding lease liabilities (not in results filings)"})))


def go():
    refuse = httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError(f"fetched {r.url}")))
    http = HttpClient(transport=refuse, sleep=lambda s: None, clock=lambda: NOW)
    t = type("T", (EquityBeta,), {"key": KEY})()
    t.workflows = ("weekly", "refresh")
    code = run(JOB, "refresh", [t], only=KEY, force=True, http=http, storage=storage, clock=lambda: NOW)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1",
           KEY)[0]
    return code, tr


def stored(slug: str, key: str):
    return q("""select v.period_end, v.value, v.held_reason, v.inputs from equity.metric_value v
                  join core.entity e on e.id = v.entity_id where e.slug = %s and v.metric_key = %s
                 order by v.period_end""", slug, key)


@pytest.mark.db
def test_a_refresh_stores_both_betas_with_their_inputs_and_a_rerun_writes_nothing(s3):
    seed()
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr
    checks = {c["name"]: c for c in tr[2]}
    assert checks["industries"]["passed"] and checks["bounds"]["passed"]
    (end, slope, held, inputs), = stored("zb-a", "beta_regression")
    assert end == LAST_WEEK_END and held is None and abs(slope - Decimal("1.3")) < Decimal("0.05")
    assert inputs["n"] == 104 and inputs["index"] == "yf.^NSEI"
    (end, value, held, inputs), = stored("zb-a", "beta_bottom_up")
    assert end == LAST_FRIDAY and held is None
    assert value == Decimal("1.374160")                         # 1.0 × (1 + (1 − 0.25168) × 500 / 1,000)
    assert inputs["debt_to_equity"] == "0.500000" and inputs["tax_rate"] == "0.25168"
    assert inputs["industry"] == "ZB Operating" and inputs["edition"] == "2026-01-05"
    assert "lease" in inputs["debt_label"]
    before = q("select count(*) from equity.metric_value where metric_key like 'beta%%'")[0][0]
    code, tr = go()
    assert code == 0 and tr[0] == "success" and tr[1] == 0
    assert q("select count(*) from equity.metric_value where metric_key like 'beta%%'")[0][0] == before


@pytest.mark.db
def test_a_lender_takes_the_industry_s_levered_beta_unchanged(s3):
    seed()
    go()
    (_, value, held, inputs), = stored("zb-l", "beta_bottom_up")
    assert held is None and value == Decimal("0.9") and "not relevered" in inputs["method"]


@pytest.mark.db
def test_a_short_history_and_a_missing_balance_sheet_are_held_with_their_reasons(s3):
    seed()
    code, tr = go()
    assert code == 0
    (_, value, held, _), = stored("zb-s", "beta_regression")
    assert value is None and held == "Fewer than 80 weeks of prices."
    (_, value, held, _), = stored("zb-n", "beta_bottom_up")
    assert value is None and held.startswith("Market capitalisation isn't available")
    names = {c["name"]: c for c in tr[2]}
    assert not names["held"]["passed"] and "zb-s beta_regression" in names["held"]["detail"]


@pytest.mark.db
def test_a_peer_group_whose_industry_is_missing_fails_the_blocking_check_and_writes_nothing(s3):
    seed()
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("update equity.peer_group set damodaran_industry = 'ZB Not in the edition' where key = 'zb-op'")
    code, tr = go()
    assert code != 0 and tr[0] == "checks_failed"
    assert q("select count(*) from equity.metric_value where metric_key like 'beta%%'")[0][0] == 0
