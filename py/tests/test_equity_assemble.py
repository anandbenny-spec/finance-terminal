"""One listed company's DCF inputs (job 2A-13; Spec 02A §5.7): built from the saved filings through the metrics' own
rules, so the DCF reads the figures the metrics show; the lease rule (Anand, 30 Oct 2026, #117); one basis; India's
rates from the stored series; and the database read.

The filings are in data/fixtures/equity (the private fixtures repo; the tests skip without it, and CI checks it out).
No NSE data is kept in this repo (CLAUDE.md §4)."""
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from conftest import dsn
from test_equity_metrics import RULES, fins, prices
from test_equity_statements import needs_fixtures

from ft.equity import assemble
from ft.equity import metrics as mx
from ft.equity.valuation import Input, Market, default_run, scenario_grid
from ft.equity.valuation.model import FAMILY_NA

D = date
T = D(2026, 10, 30)                       # the price date (a Friday)
SETTINGS = assemble.load_settings()
JOB, ADMIN = dsn("job_writer"), dsn("postgres")


def india() -> Market:
    """India's parameters fixed for the offline tests (the DB test reads them from the stored series)."""
    def f(v):
        return Input.of(Decimal(v), "fraction", D(2026, 9, 1), "test")
    return Market("IN", "INR", f("0.0531"), f("0.0706"), f("0.0692"), assemble.marginal_tax())


def stored(symbol: str, *, family: str = "operating", rows=None, lease: dict | None = None,
           beta: str = "0.9") -> assemble.Stored:
    """The company's saved filings, with the market and year metrics as equity.metrics computes them at ₹1,000 a
    share (synthetic prices), and a bottom-up beta."""
    rows = list(rows if rows is not None else fins(symbol))
    co = mx.Company(1, symbol.lower(), family)
    vals = mx.compute(mx.Data(co, rows, prices(T, "1000")), RULES)
    metrics = {(v.key, v.basis, v.period_kind, v.period_end): (v.value, v.held, v.inputs) for v in vals
               if (v.period_kind == "market" and v.key in assemble.MARKET_KEYS)
               or (v.period_kind == "year" and v.key in assemble.YEAR_KEYS)}
    if lease is not None:
        k = next(k for k in metrics if k[0] == "ev")
        value, held, inputs = metrics[k]
        metrics[k] = (value, held, {**inputs, "lease_check": lease})
    metrics[("beta_bottom_up", "none", "market", T)] = (Decimal(beta), None, {"industry": "test"})
    return assemble.Stored(co, "IN", "INR", rows, metrics)


def computed(symbol: str) -> dict:
    return {(v.key, v.basis, v.period_kind, v.period_end): v
            for v in mx.compute(mx.Data(mx.Company(1, symbol.lower(), "operating"), list(fins(symbol)),
                                        prices(T, "1000")), RULES)}


@needs_fixtures
@pytest.mark.parametrize("symbol", ["INFY", "HINDUNILVR", "ITC"])
def test_a_company_s_inputs_are_the_figures_the_metrics_show(symbol):
    c = assemble.company_inputs(stored(symbol), T)
    v = computed(symbol)
    held = {k: x.held for k, x in c.inputs().items() if x.held}
    assert not held, held
    q_end = c.revenue_ttm.as_of
    assert c.revenue_ttm.value == v[("revenue", "consolidated", "ttm", q_end)].value
    assert c.ebit_ttm.value == v[("ebit_derived", "consolidated", "ttm", q_end)].value
    fy_end = c.revenue_3fy.as_of
    years = [fy_end, mx.month_end(fy_end, -12), mx.month_end(fy_end, -24)]
    assert c.ebit_3fy.value == sum(v[("ebit_derived", "consolidated", "year", e)].value for e in years)
    assert c.opening_capital_3fy.value == sum(
        v[("invested_capital", "consolidated", "instant", mx.month_end(e, -12))].value for e in years)
    bs = c.debt.as_of
    assert c.debt.value == v[("debt", "consolidated", "instant", bs)].value
    assert c.cash_and_investments.value == v[("cash_and_investments", "consolidated", "instant", bs)].value
    assert c.debt_closing_fy.value == v[("debt", "consolidated", "instant", fy_end)].value
    assert c.market_cap.value == v[("market_cap", "none", "market", T)].value and c.market_cap.as_of == T
    assert c.revenue_cagr_3y.value == v[("revenue_cagr_3y", "consolidated", "year", fy_end)].value
    assert (c.market, c.currency, c.extra_held) == ("IN", "INR", ())
    # every statement figure names the filing lines it read
    assert c.ebit_3fy.detail["financials"] and c.ebit_3fy.detail["basis"] == "consolidated"
    assert "lease" in c.debt.source
    run = default_run(c, india(), SETTINGS)
    assert run.status == "solved" and run.value_per_share.is_finite(), run.reason
    assert json.loads(json.dumps(run.inputs))["company"]["key"] == symbol.lower()
    assert scenario_grid(c, india(), SETTINGS).status == "solved"


@needs_fixtures
def test_material_lease_payments_hold_the_model_and_an_unchecked_lease_too():
    material = assemble.company_inputs(stored("INFY", lease={"material": True}), T)
    assert material.extra_held == (assemble.LEASE_MATERIAL,)
    assert default_run(material, india(), SETTINGS).reason == assemble.LEASE_MATERIAL
    why = "Lease payments can't be checked against EBIT: the filing couldn't be read again."
    unchecked = assemble.company_inputs(stored("INFY", lease={"material": False, "held": why}), T)
    assert default_run(unchecked, india(), SETTINGS).reason == why
    s = stored("INFY")
    s.metrics = {k: v for k, v in s.metrics.items() if k[0] != "ev"}
    assert default_run(assemble.company_inputs(s, T), india(), SETTINGS).reason == \
        "Lease payments couldn't be checked against EBIT."


@needs_fixtures
def test_a_lender_is_na_for_its_family_whatever_its_inputs():
    c = assemble.company_inputs(stored("SBIN", family="lender"), T)
    assert default_run(c, india(), SETTINGS).reason == FAMILY_NA["lender"]


@needs_fixtures
def test_a_balance_sheet_on_another_basis_than_the_quarters_is_held():
    rows = list(fins("INFY"))
    c0 = assemble.company_inputs(stored("INFY"), T)
    bs = c0.debt.as_of
    moved = [replace(f, basis="standalone") if (f.kind == "instant" and f.end == bs and f.basis == "consolidated")
             else f for f in rows if not (f.kind == "instant" and f.end == bs and f.basis == "standalone")]
    c = assemble.company_inputs(stored("INFY", rows=moved), T)
    assert c.debt.held == "The balance sheet and the last four quarters are on different bases."
    assert default_run(c, india(), SETTINGS).status == "not_applicable"


@needs_fixtures
def test_fewer_than_three_financial_years_on_one_basis_are_held():
    rows = [f for f in fins("INFY") if not (f.kind == "year" and f.basis == "consolidated" and f.end <= D(2024, 3, 31))]
    c = assemble.company_inputs(stored("INFY", rows=rows), T)
    assert c.ebit_3fy.held == "Fewer than three financial years are filed on one basis."
    assert default_run(c, india(), SETTINGS).reason == "Fewer than three financial years are filed on one basis."


@needs_fixtures
def test_no_market_figures_stored_holds_the_model_with_that_reason():
    s = stored("INFY")
    s.metrics = {}
    c = assemble.company_inputs(s, T)
    assert c.market_cap.held == "Market capitalisation isn't stored." and c.extra_held == ()
    assert default_run(c, india(), SETTINGS).reason in ("Shares outstanding today aren't stored.",
                                                         "Market capitalisation isn't stored.")


def test_the_marginal_rate_comes_from_valuation_yaml_with_its_source_and_date():
    tax = assemble.marginal_tax()
    assert tax.value == Decimal("0.25168") and tax.as_of == D(2026, 4, 1) and "section 200" in tax.source


# ---------------------------------------------------------------------------------------------- the database
SEED = "test.equity_assemble_seed"


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zv-%')"
        c.execute("delete from core.observation where task_run_id in (select id from ops.task_run where task_key = %s)",
                  (SEED,))
        for sql in (f"delete from equity.metric_value where entity_id in {ids}",
                    f"delete from equity.company where entity_id in {ids}",
                    "delete from core.entity where slug like 'zv-%'"):
            c.execute(sql)


@pytest.fixture
def clean():
    wipe()
    yield
    wipe()


@pytest.mark.db
def test_fetch_and_market_inputs_read_the_stored_rows(clean):
    """A company with its market metrics but no filings, and India's four series in 2031 (after any other test's
    rows, so they are the newest)."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'seed', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (SEED,))
        job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('refresh', 'cli', 'local') "
                        "returning id").fetchone()[0]
        run_id = c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, %s, 'success') "
                           "returning id", (job, SEED)).fetchone()[0]
        for key, when, value in (("fred.INDIRLTLT01STM", D(2031, 5, 1), "7.06"),
                                 ("damodaran.IND_DEFAULT_SPREAD", D(2031, 1, 1), "1.75"),
                                 ("damodaran.ERP_IMPLIED", D(2031, 6, 1), "4.20"),
                                 ("damodaran.IND_CRP", D(2031, 1, 1), "2.72")):
            c.execute("""insert into core.observation (series_id, obs_date, value, task_run_id)
                         select id, %s, %s, %s from core.series where key = %s""", (when, value, run_id, key))
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company', 'zv-a', 'zv-a', 'IN', 'INR', false) returning id""").fetchone()[0]
        c.execute("insert into equity.company (entity_id, fy_end_month, valuation_family) values (%s, 3, 'operating')",
                  (eid,))
        for key, kind, basis, end, value in (("market_cap", "market", "none", D(2031, 6, 27), 1000),
                                             ("shares_today", "market", "none", D(2031, 6, 27), 10),
                                             ("beta_bottom_up", "market", "none", D(2031, 6, 27), "0.9"),
                                             ("revenue_cagr_3y", "year", "consolidated", D(2031, 3, 31), "0.1"),
                                             ("debt", "instant", "consolidated", D(2031, 3, 31), 5)):
            c.execute("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value,
                                                          inputs) values (%s, %s, %s, %s, %s, %s, '{}')""",
                      (eid, key, basis, kind, end, value))
    with psycopg.connect(JOB, autocommit=True) as c:
        got = assemble.fetch(c, [eid])
        assert list(got) == [eid]
        s = got[eid]
        assert (s.company.slug, s.company.family, s.market, s.currency, s.fins) == ("zv-a", "operating", "IN", "INR",
                                                                                    [])
        assert set(s.metrics) == {("market_cap", "none", "market", D(2031, 6, 27)),
                                  ("shares_today", "none", "market", D(2031, 6, 27)),
                                  ("beta_bottom_up", "none", "market", D(2031, 6, 27)),
                                  ("revenue_cagr_3y", "consolidated", "year", D(2031, 3, 31))}   # not the instant
        assert eid in assemble.fetch(c)                                         # all companies when none are named
        m = assemble.market_inputs(c, D(2031, 6, 30))
        assert m.risk_free.value == Decimal("0.0531") and m.risk_free.as_of == D(2031, 5, 1)
        assert m.gross_yield.value == Decimal("0.0706")
        assert m.erp.value == Decimal("0.0692") and m.erp.as_of == D(2031, 6, 1)
        assert m.marginal_tax.value == Decimal("0.25168")
        c_in = assemble.company_inputs(s, D(2031, 6, 30))
        assert c_in.market_cap.value == 1000 and c_in.market_cap.as_of == D(2031, 6, 27)
        assert c_in.revenue_ttm.held == "No quarter is filed."
        run = default_run(c_in, m, SETTINGS)
        assert run.status == "not_applicable" and run.reason == "Lease payments couldn't be checked against EBIT."
        early = assemble.market_inputs(c, D(2000, 1, 1))                        # nothing stored that early
        assert early.risk_free.held and early.erp.held and early.gross_yield.held
