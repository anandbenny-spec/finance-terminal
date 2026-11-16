"""The US slice's valuation (job US-04, #403; Spec 02A.1 §5.4): the US rates and how they were read from Damodaran's
files, the US lines (operating income as filed, borrowings and cash as filed, held lines), the period labels of
52-53-week filers, the US metrics, a DCF assembled from US filings and worked out by hand to the cent, the round trip
for the operating companies in the EDGAR fixtures, and India's assembly unchanged by the US argument.

The database half (the five tasks through the runner, and that no US task reads or writes an India row) is in
test_equity_us_valuation_task.py."""
import copy
import functools
import gzip
import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
import yaml
from test_equity_valuation import TABLE

from ft.equity import assemble
from ft.equity import edgar as E
from ft.equity import inputs as inp
from ft.equity import metrics as mx
from ft.equity import us_inputs as ui
from ft.equity import us_metrics as um
from ft.equity.valuation import Input, Market, default_run, prepare, reverse_dcf
from ft.equity.valuation.capital import cost_of_capital
from ft.equity.valuation.dcf import project
from ft.jobs.tasks.equity_us_valuation import compute
from ft.jobs.tasks.equity_valuation import round_trip

D = date
U = Decimal(10_000_000)                     # $10 million: the worked example's unit (India's ₹ crore, in dollars)
FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
DAMODARAN, EDGAR = FIX / "damodaran", FIX / "edgar"
needs_fixtures = pytest.mark.skipif(not EDGAR.exists(), reason="the saved files aren't checked out (data/fixtures)")
SETTINGS = assemble.load_settings()
RULE = assemble.load_delivered_roe()
CONFIG = Path(__file__).resolve().parents[1] / "config"


# ---------------------------------------------------------------------------------------------- a fake database
class Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDb:
    """`inputs._latest`'s one query: the latest observation of a series on or before a date."""

    def __init__(self, series: dict[str, dict[date, Decimal]]):
        self.series = series

    def execute(self, sql, params):
        key, as_of = params
        days = [d for d in self.series.get(key, {}) if d <= as_of]
        return Rows((max(days), self.series[key][max(days)]) if days else None)


PRICE_DAY = D(2026, 5, 29)
ERP_MONTH = D(2026, 5, 1)


def rates(rf="6.0", erp="7.0", rf_day=PRICE_DAY, erp_month=ERP_MONTH) -> FakeDb:
    return FakeDb({ui.RF_SERIES: {rf_day: Decimal(rf), D(2026, 1, 2): Decimal("4.1")},
                   ui.ERP_SERIES: {erp_month: Decimal(erp), D(2026, 4, 1): Decimal("4.77")}})


# ---------------------------------------------------------------------------------------------- the rates
def test_the_usd_rate_is_the_10_year_treasury_at_the_price_date_gross_and_the_erp_the_mature_one():
    m = ui.market_inputs(rates("4.12", "4.20"), PRICE_DAY)
    assert (m.market, m.currency) == ("US", "USD")
    assert m.risk_free.value == m.gross_yield.value == Decimal("0.0412") and m.risk_free.as_of == PRICE_DAY
    assert "not netted" in m.risk_free.source and "H.15" in m.risk_free.source
    assert m.erp.value == Decimal("0.042") and m.erp.as_of == D(2026, 5, 1)
    assert "no country risk premium" in m.erp.source
    assert m.marginal_tax.value == Decimal("0.25") and m.marginal_tax.as_of == D(2018, 1, 1)


def test_the_rate_is_the_latest_day_on_or_before_the_price_date_and_held_when_stale():
    m = ui.market_inputs(rates(rf_day=PRICE_DAY - timedelta(days=3)), PRICE_DAY)
    assert m.risk_free.as_of == PRICE_DAY - timedelta(days=3) and m.risk_free.held is None
    stale = ui.market_inputs(rates(rf_day=PRICE_DAY + timedelta(days=1)), PRICE_DAY)    # only January's is before it
    assert stale.risk_free.value is None and stale.risk_free.held == \
        "The 10-year Treasury yield isn't stored for the price date."
    none = ui.market_inputs(FakeDb({}), PRICE_DAY)
    assert none.erp.held == "Damodaran's implied equity risk premium isn't stored."


def test_the_us_settings_need_their_sources_and_refuse_a_netted_rate(tmp_path):
    raw = yaml.safe_load((CONFIG / "equity" / "valuation.yaml").read_text("utf-8"))
    assert ui.settings().tax == Decimal("0.25")
    netted = copy.deepcopy(raw)
    netted["us"]["risk_free"]["netted"] = True
    (tmp_path / "v.yaml").write_text(yaml.safe_dump(netted))
    with pytest.raises(ValueError, match="netted"):
        ui.settings(tmp_path / "v.yaml")
    del netted["us"]
    (tmp_path / "w.yaml").write_text(yaml.safe_dump(netted))
    with pytest.raises(TypeError, match="no `us` block"):
        ui.settings(tmp_path / "w.yaml")


@pytest.mark.skipif(not DAMODARAN.exists(), reason="the saved files aren't checked out (data/fixtures)")
def test_damodarans_files_say_he_nets_the_us_spread_from_the_rate_and_adds_it_back_to_the_us_premium():
    """The card's step 2, as `valuation.yaml` records it: his rate net of the US default spread, paired with an ERP
    higher by the same spread, gives the same expected return as the T.Bond rate with the mature premium."""
    wb = openpyxl.load_workbook(DAMODARAN / "erp_monthly_ERPbymonth.xlsx", read_only=True, data_only=True)
    rows = list(wb["Historical ERP"].iter_rows(values_only=True))
    head = rows[0]
    col = {h: i for i, h in enumerate(head) if h}
    note = {r[0].date() if isinstance(r[0], datetime) else r[0]: r[col["Notes"]] for r in rows[1:] if r[0]}
    assert note[D(2025, 6, 1)] == "updated growth and added default-adjusted riskfree rate"
    oct26 = next(r for r in rows[1:] if isinstance(r[0], datetime) and r[0].date() == D(2026, 10, 1))
    tbond, adj = Decimal(str(oct26[col["T.Bond Rate"]])), Decimal(str(oct26[col["$ Riskfree Rate"]]))
    erp, erp_adj = Decimal(str(oct26[col["ERP (T12m)"]])), Decimal(str(oct26[col["ERP (T12m) with adj riskfree rate"]]))
    assert (tbond, adj, erp, erp_adj) == (Decimal("0.0529"), Decimal("0.0507"), Decimal("0.037"), Decimal("0.0392"))
    assert tbond + erp == adj + erp_adj == Decimal(str(oct26[col["Expected Return"]])).quantize(Decimal("0.0001"))
    ws = openpyxl.load_workbook(DAMODARAN / "ctrypremJuly26.xlsx", read_only=True, data_only=True)["ERPs by country"]
    top = {str(r[0]).strip(): r for r in ws.iter_rows(min_row=1, max_row=6, values_only=True) if r[0]}
    mature = [c for c in top["Enter the current risk premium for a mature equity market"] if c is not None]
    assert mature[1] == 0.042 and "adjusted dollar riskfree rate" in mature[2]
    us = inp.parse_ctryprem((DAMODARAN / "ctrypremJuly26.xlsx").read_bytes(), "United States")
    assert us.rating == "Aa1" and us.default_spread == us.crp == Decimal("0.218635")
    assert us.total_erp == Decimal("4.42") == us.mature_erp + Decimal("0.22")
    text = yaml.safe_load((CONFIG / "equity" / "valuation.yaml").read_text("utf-8"))["us"]["risk_free"]["source"]
    assert "ERPbymonth" in text and "ctrypremJuly26" in text and "Decided by Claude" in text


@pytest.mark.skipif(not DAMODARAN.exists(), reason="the saved files aren't checked out (data/fixtures)")
def test_every_us_peer_groups_industry_is_in_damodarans_us_betas():
    edition, rows = inp.parse_beta_india((DAMODARAN / "betas_us_betas.xls").read_bytes())
    names = {r.industry for r in rows}
    groups = yaml.safe_load((CONFIG / "equity" / "us_peer_groups.yaml").read_text("utf-8"))["groups"]
    assert edition == D(2026, 1, 5) and len(rows) > 50
    assert not [k for k, g in groups.items() if g["damodaran_industry"] not in names]


# ---------------------------------------------------------------------------------------------- periods and lines
@pytest.mark.parametrize(("filed", "label"), [
    (D(2026, 6, 27), D(2026, 6, 30)),           # Apple: the last Saturday of the quarter's month
    (D(2026, 1, 25), D(2026, 1, 31)),           # Nvidia: the last Sunday of January
    (D(2026, 2, 1), D(2026, 1, 31)),            # Home Depot: the Sunday nearest 31 January, in February
    (D(2020, 10, 3), D(2020, 9, 30)),           # Disney: the Saturday nearest 30 September, in October
    (D(2026, 9, 30), D(2026, 9, 30)),           # a calendar quarter is its own label
    (D(2026, 9, 15), D(2026, 9, 15)),           # mid-month: no label
])
def test_a_52_53_week_period_is_labelled_by_the_month_it_ends_in(filed, label):
    assert ui.nearest_month_end(filed) == label
    f = ui.by_month(mx.Fin(1, "consolidated", "quarter", filed - timedelta(days=91), filed, {}, {}, "us_gaap"))
    assert f.end == label


def fin(kind, end, start=None, doc=1, filed=None, **values) -> mx.Fin:
    tags = {k: v for k, v in values.items() if isinstance(v, str)}
    vals = {k: Decimal(str(v)) for k, v in values.items() if not isinstance(v, str)}
    return mx.Fin(doc, "consolidated", kind, start, end, vals, tags, "us_gaap",
                  filed or datetime.combine(end + timedelta(days=30), datetime.min.time(), tzinfo=UTC))


def test_a_held_line_is_its_sentence_and_a_missing_one_names_the_us_line():
    parts = fin("instant", D(2026, 6, 30), borrowings_current="held: filed in parts (LongTermDebtCurrent 1, "
                "CommercialPaper 2)", borrowings_noncurrent=100, cash=5, total_equity=50)
    c = ui.UsCalc()
    with pytest.raises(mx.Held, match="splits its current debt into parts"):
        ui.debt(c, parts)
    assert "LongTermDebtCurrent" in c.inputs()["held_lines"]["borrowings_current"]
    with pytest.raises(mx.Held, match="^The filing doesn't report operating income.$"):
        ui.ebit(ui.UsCalc(), fin("quarter", D(2026, 6, 30), revenue=10))
    assert ui.UsCalc().take(parts, "nci") == 0                          # not filed: none, as India's
    shares = fin("instant", D(2026, 6, 30), shares_outstanding="held: no curated conversion ratio for CommonClassB1Member")
    with pytest.raises(mx.Held, match="conversion rates aren't curated"):
        ui.UsCalc().take(shares, "shares_outstanding")


def test_debt_and_cash_are_as_filed_with_their_labels():
    f = fin("instant", D(2026, 6, 30), borrowings_current=30, borrowings_noncurrent=70, cash=40, total_equity=200)
    c = ui.UsCalc()
    assert ui.debt(c, f) == 100 and c.inputs()["label"] == ui.DEBT_LABEL
    c = ui.UsCalc()
    assert ui.cash(c, f) == 40 and c.inputs()["label"] == ui.CASH_LABEL
    c = ui.UsCalc()
    assert ui.invested(c, f) == 260 and c.inputs()["label"] == f"{ui.DEBT_LABEL}; {ui.CASH_LABEL}"
    only_long = fin("instant", D(2026, 6, 30), borrowings_noncurrent=70)
    assert ui.debt(ui.UsCalc(), only_long) == 70                        # no current line: India's rule, the sum filed
    with pytest.raises(mx.Held, match="doesn't report borrowings"):
        ui.debt(ui.UsCalc(), fin("instant", D(2026, 6, 30), cash=1))


# ---------------------------------------------------------------------------------------------- the worked example
FY = [D(2022, 12, 31), D(2023, 12, 31), D(2024, 12, 31), D(2025, 12, 31)]       # FY0 … FY3
QUARTERS = [D(2025, 6, 30), D(2025, 9, 30), D(2025, 12, 31), D(2026, 3, 31)]
SHARES = 10 * U                                                                 # 100 million


def example_rows(**latest) -> list[mx.Fin]:
    """India's worked example (test_equity_valuation) as a US filer's rows, in units of $10 million: three years of
    revenue 800, 900, 1,000 and operating income 20% of it, tax 20% of income before tax (equal to operating income),
    interest expense 40 in FY3; invested capital 400, 450, 500 at the ends of FY0 to FY2 (total equity + debt − cash);
    the last four quarters 250 of revenue and 50 of operating income each; the latest balance sheet (31 Mar 2026) debt
    500, cash 200, NCI 50, and 100 million shares on the cover page."""
    rows = []
    for n, (end, rev) in enumerate(zip(FY[1:], (800, 900, 1000), strict=True)):
        rows.append(fin("year", end, end.replace(month=1, day=1), revenue=rev * U, operating_income=rev * U / 5,
                        pbt=rev * U / 5, tax=rev * U / 25, pat_owners=rev * U * 4 / 25,
                        **({"finance_cost": 40 * U} if n == 2 else {})))
    for q in QUARTERS:
        rows.append(fin("quarter", q, mx.month_end(q, -3) + timedelta(days=1), revenue=250 * U,
                        operating_income=50 * U, pbt=50 * U, tax=10 * U, pat_owners=40 * U))
    for end, debt, equity, cash in ((FY[0], 250, 250, 100), (FY[1], 300, 250, 100), (FY[2], 400, 200, 100),
                                    (FY[3], 600, 300, 150)):
        rows.append(fin("instant", end, borrowings_noncurrent=debt * U, total_equity=equity * U, equity_owners=equity * U,
                        cash=cash * U, total_assets=(debt + equity) * U))
    last = {"borrowings_noncurrent": 500 * U, "total_equity": 900 * U, "equity_owners": 850 * U, "nci": 50 * U,
            "cash": 200 * U, "total_assets": 1500 * U, "shares_outstanding": SHARES, **latest}
    row = fin("instant", QUARTERS[-1], **last)
    if not isinstance(last["shares_outstanding"], str):
        row = replace(row, tags={**row.tags, "shares_outstanding":
                                 "dei:EntityCommonStockSharesOutstanding (cover page, as of 2026-04-24)"})
    rows.append(row)
    return rows


def example(**latest) -> assemble.Stored:
    """The example with the market figures the DCF reads stored as us.metrics and us.beta store them: a $450 close,
    market capitalisation 4,500, a bottom-up beta of 1, the 3-year revenue CAGR 12% (set: its own computation is
    tested below and in test_equity_metrics)."""
    s = assemble.Stored(mx.Company(1, "us-zdcf", "operating", 12), "US", "USD", example_rows(**latest))
    s.metrics = {("shares_today", "none", "market", PRICE_DAY): (SHARES, None, {}),
                 ("market_cap", "none", "market", PRICE_DAY): (4500 * U, None, {"price": {
                     "close": "450", "trade_date": PRICE_DAY.isoformat()}}),
                 ("beta_bottom_up", "none", "market", PRICE_DAY): (Decimal(1), None, {}),
                 ("revenue_cagr_3y", "consolidated", "year", FY[3]): (Decimal("0.12"), None, {})}
    return s


def test_a_dcf_assembled_from_us_filings_is_worked_out_by_hand_to_the_cent():
    """The worked example of test_equity_valuation (its table and arithmetic), with every figure read from US rows
    through the US lines and the US market: the 10-year Treasury at 6% (so the cost of debt's floor is 6% too: 8% is
    inside [6%, 12%]), the mature ERP at 7%, a 25% marginal rate. Per share: $1,769.05 × 10⁷ ÷ 10⁸ shares = $176.90."""
    c = ui.company_inputs(example(), PRICE_DAY)
    m = ui.market_inputs(rates(), PRICE_DAY)
    want = {"revenue_ttm": 1000, "ebit_ttm": 200, "revenue_3fy": 2700, "ebit_3fy": 540, "tax_3fy": 108, "pbt_3fy": 540,
            "opening_capital_3fy": 1350, "finance_cost_fy": 40, "debt_opening_fy": 400, "debt_closing_fy": 600,
            "debt": 500, "cash_and_investments": 200, "nci": 50}
    assert {k: getattr(c, k).value / U for k in want} == want
    assert "operating income over the last four quarters (10-K and 10-Q filings, SEC EDGAR)" == c.ebit_ttm.source
    assert ui.DEBT_LABEL in c.debt.source and c.cash_and_investments.source.startswith("cash and cash equivalents")
    assert c.extra_held == ()                                       # no lease rule for the US
    cap = cost_of_capital(c, m, SETTINGS)
    assert (cap.cost_of_equity, cap.cost_of_debt, cap.bounded, cap.wacc) == \
        (Decimal("0.13"), Decimal("0.08"), None, Decimal("0.123"))
    d, _, _ = prepare(c, m, SETTINGS)
    p = project(d, Decimal("0.12"))
    tol = Decimal("0.0051")
    for y, row in zip(p.years, TABLE, strict=True):
        assert abs(y.fcff / U - Decimal(row[7])) < tol and abs(y.present_value / U - Decimal(row[9])) < tol
    assert abs(p.terminal_value / U - Decimal("3407.71")) < tol and abs(p.equity / U - Decimal("1769.05")) < tol
    run = default_run(c, m, SETTINGS)
    assert run.status == "solved" and run.value_per_share == Decimal("176.90")       # to the cent


def test_the_us_example_round_trips_and_its_runs_carry_us_sources():
    s = example()
    m = ui.market_inputs(rates(), PRICE_DAY)
    co = compute(s, PRICE_DAY, m, SETTINGS, RULE)
    rev = next(r for r in co.runs if r.kind == "reverse_dcf")
    assert rev.status == "solved" and round_trip(co, m, SETTINGS, rev) is None
    assert rev.price == Decimal(450)
    text = json.dumps(rev.inputs)
    assert "Source: Yahoo Finance" in text and "SEC EDGAR" in text and "NSE" not in text and "India" not in text


def test_held_current_debt_holds_the_model_with_its_sentence():
    s = example(borrowings_current="held: filed in parts (LongTermDebtCurrent 1, ShortTermBorrowings 2)")
    co = compute(s, PRICE_DAY, ui.market_inputs(rates(), PRICE_DAY), SETTINGS, RULE)
    rev = next(r for r in co.runs if r.kind == "reverse_dcf")
    assert rev.status == "not_applicable"
    assert rev.reason == "The filing splits its current debt into parts and files no total, so its debt isn't shown."


def test_a_company_that_files_no_operating_income_is_na():
    s = example()
    s.fins = [replace(f, values={k: v for k, v in f.values.items() if k != "operating_income"}) for f in s.fins]
    co = compute(s, PRICE_DAY, ui.market_inputs(rates(), PRICE_DAY), SETTINGS, RULE)
    rev = next(r for r in co.runs if r.kind == "reverse_dcf")
    assert rev.status == "not_applicable" and rev.reason in (
        "The company doesn't file operating income.", "The filing doesn't report operating income.")


def test_a_lender_is_valued_on_its_implied_roe_with_us_sources():
    s = example()
    s.company = mx.Company(1, "us-zbank", "lender", 12)
    s.metrics[("pb", "consolidated", "market", PRICE_DAY)] = (Decimal("1.5"), None, {})
    for n, end in enumerate(FY[1:]):
        s.metrics[("roe", "consolidated", "year", end)] = (Decimal("0.12") + Decimal(n) / 100, None, {})
    co = compute(s, PRICE_DAY, ui.market_inputs(rates(rf="4.0", erp="4.2"), PRICE_DAY), SETTINGS, RULE)
    (run,) = co.runs
    assert run.kind == "implied_roe" and run.status == "solved"
    # implied ROE = g + P/B × (Ke − g), g = rf = 4%, Ke = 4% + 1 × 4.2% = 8.2%: 4% + 1.5 × 4.2% = 10.3%
    assert run.result.quantize(Decimal("0.0001")) == Decimal("0.1030") and run.delivered == Decimal("0.13")
    assert "Damodaran's US industry beta" in json.dumps(run.inputs, ensure_ascii=False)


# ---------------------------------------------------------------------------------------------- the US metrics
def data(rows=None, prices=None, actions=(), family="operating") -> mx.Data:
    rows = example_rows() if rows is None else rows
    prices = prices or [mx.Price(PRICE_DAY - timedelta(days=366), Decimal(400), Decimal(400), None),
                        mx.Price(PRICE_DAY, Decimal(450), Decimal(450), None)]
    acts = list(actions)
    return mx.Data(mx.Company(1, "us-zdcf", family, 12), rows, prices, acts, um.breaks(acts))


def values(d: mx.Data) -> dict:
    return {(v.key, v.period_kind, v.period_end): v for v in um.compute(d)}


def test_us_metrics_read_operating_income_as_filed_and_the_us_lines():
    v = values(data())
    ttm = v[("ebit_derived", "ttm", QUARTERS[-1])]
    assert ttm.value == 200 * U and um.ebit_input_errors([ttm]) == []
    assert {line for r in ttm.inputs["financials"] for line in r["lines"]} == {"operating_income"}
    assert v[("ebit_margin", "ttm", QUARTERS[-1])].value == Decimal("0.2")
    assert v[("debt", "instant", QUARTERS[-1])].value == 500 * U
    assert v[("debt", "instant", QUARTERS[-1])].inputs["label"] == ui.DEBT_LABEL
    assert v[("cash_and_investments", "instant", QUARTERS[-1])].inputs["label"] == ui.CASH_LABEL
    assert v[("shares_today", "market", PRICE_DAY)].value == SHARES
    assert v[("market_cap", "market", PRICE_DAY)].value == 4500 * U
    ev = v[("ev", "market", PRICE_DAY)]
    assert ev.value == (4500 + 500 + 50 - 200) * U and ev.inputs["label"] == f"{ui.DEBT_LABEL}; {ui.CASH_LABEL}"
    assert v[("ev_ebit_ttm", "market", PRICE_DAY)].value == mx.ratio(ev.value, 200 * U)
    assert v[("return_1y", "market", PRICE_DAY)].value == Decimal("0.125")
    assert v[("traded_value_median_3m", "market", PRICE_DAY)].held == um.NO_TRADED_VALUE
    assert v[("roic", "year", FY[3])].value == mx.ratio(200 * U * Decimal("0.8"), 500 * U)
    assert not [k for k in v if k[0] in ("nii", "roa", "nim_proxy", "gnpa")]              # no bank ratios


def test_a_derived_ebit_would_fail_the_as_filed_check():
    bad = mx.Value("ebit_derived", "consolidated", "quarter", QUARTERS[-1], Decimal(1), None,
                   {"financials": [{"lines": {"pbt_before_exceptional": {}, "finance_cost": {}}}]})
    assert um.ebit_input_errors([bad]) == [f"consolidated quarter {QUARTERS[-1].isoformat()}"]


def test_no_operating_income_anywhere_is_na_for_ebit_and_what_needs_it():
    rows = [replace(f, values={k: x for k, x in f.values.items() if k != "operating_income"}) for f in example_rows()]
    v = values(data(rows))
    for key, kind, end in (("ebit_derived", "ttm", QUARTERS[-1]), ("ebit_margin", "year", FY[3]),
                           ("roic", "year", FY[3]), ("ev_ebit_ttm", "market", PRICE_DAY)):
        assert v[(key, kind, end)].held == ui.NO_OPERATING_INCOME
    assert v[("net_margin", "ttm", QUARTERS[-1])].value == Decimal("0.16")


def test_held_shares_hold_the_market_figures_with_their_sentence():
    rows = example_rows()
    rows[-1] = replace(rows[-1], values={k: x for k, x in rows[-1].values.items() if k != "shares_outstanding"},
                       tags={**rows[-1].tags, "shares_outstanding": "held: no curated conversion ratio for CommonClassB1Member"})
    v = values(data(rows))
    for key in ("shares_today", "market_cap", "pe_ttm", "pb", "ev"):
        assert "conversion rates aren't curated" in v[(key, "market", PRICE_DAY)].held


def test_a_split_after_the_cover_page_divides_the_shares_and_one_before_it_doesnt():
    after = mx.Action("us-zdcf-split-2026-05-05", "split", D(2026, 5, 5), factor=Decimal("0.25"), status="applied")
    v = values(data(actions=[after]))
    assert v[("shares_today", "market", PRICE_DAY)].value == SHARES * 4
    assert v[("shares_today", "market", PRICE_DAY)].inputs["cover_date"] == "2026-04-24"
    # between the period end (31 Mar) and the cover page (24 Apr): the cover page's count is already after it
    between = replace(after, slug="us-zdcf-split-2026-04-10", ex_date=D(2026, 4, 10))
    v = values(data(actions=[between]))
    assert v[("shares_today", "market", PRICE_DAY)].value == SHARES


def test_a_spin_off_inside_the_window_holds_the_revenue_cagr():
    spin = mx.Action("us-zdcf-other-2025-04-01", "other", D(2025, 4, 1), factor=None, status="held")
    rows = example_rows() + [fin("year", FY[0], D(2022, 1, 1), revenue=700 * U, operating_income=140 * U)]
    v = values(data(rows))
    assert v[("revenue_cagr_3y", "year", FY[3])].value == \
        ((Decimal(1000) / Decimal(700)) ** (Decimal(1) / 3) - 1).quantize(mx.PLACES)
    held = values(data(rows, actions=[spin]))[("revenue_cagr_3y", "year", FY[3])]
    assert held.value is None and held.held == "The window spans a spin-off, so its years aren't comparable."


def test_a_52_53_week_filers_quarters_make_a_ttm():
    """Apple-style period ends (the last Saturday of the month) make four consecutive quarters once labelled."""
    ends = [D(2025, 6, 28), D(2025, 9, 27), D(2025, 12, 27), D(2026, 3, 28)]
    rows = [ui.by_month(fin("quarter", e, e - timedelta(days=90), revenue=250 * U, operating_income=50 * U))
            for e in ends]
    v = values(data(rows, prices=[mx.Price(PRICE_DAY, Decimal(1), Decimal(1), None)]))
    assert v[("revenue", "ttm", D(2026, 3, 31))].value == 1000 * U


# ---------------------------------------------------------------------------------------------- the EDGAR fixtures
CIK = {"AAPL": 320193, "MSFT": 789019, "NVDA": 1045810, "WMT": 104169, "GOOGL": 1652044}
GOOGL_COVER = Decimal(12_230_000_000)       # E1's cover-page total of the three classes (test_equity_us_filings)
PARTS = re.compile(r"(\w+) (-?\d+(?:\.\d+)?)")


@functools.cache
def edgar_rows(ticker: str) -> tuple[mx.Fin, ...]:
    cf = json.loads(gzip.decompress((EDGAR / f"companyfacts_CIK{CIK[ticker]:010d}.json.gz").read_bytes()))
    s = E.Statements([(CIK[ticker], cf)], E.load_tags())
    out, n = [], 0
    for f in sorted(s.filings.values(), key=lambda f: (f.filed, f.accn)):
        if not f.period_end or f.period_end < D(2019, 1, 1):
            continue
        b = s.build(f)
        if not b:
            continue
        n += 1
        for r in b.rows:
            out.append(ui.by_month(mx.Fin(n, "consolidated", r.period_kind, r.period_start, r.period_end,
                                          dict(r.values), dict(r.tags), "us_gaap",
                                          datetime(f.filed.year, f.filed.month, f.filed.day, tzinfo=UTC))))
    return tuple(out)


def summed_parts(f: mx.Fin) -> mx.Fin:
    """A test assumption only: current debt the adapter still holds in parts (outside option A's sum, #455) taken as
    the sum of its parts."""
    tag = str(f.tags.get("borrowings_current") or "")
    if not tag.startswith("held: filed in parts"):
        return f
    total = sum((Decimal(v) for _, v in PARTS.findall(tag.split("(", 1)[1])), Decimal(0))
    return replace(f, values={**f.values, "borrowings_current": total})


def edgar_company(ticker: str, close: Decimal, assume_parts: bool) -> tuple[assemble.Stored, date]:
    rows = [summed_parts(f) if assume_parts else f for f in edgar_rows(ticker)]
    if ticker == "GOOGL":                       # a multi-class issuer's shares come from the latest cover page
        last = max((f for f in rows if f.kind == "instant"), key=lambda f: (f.end, f.order))
        rows[rows.index(last)] = replace(last, values={**last.values, "shares_outstanding": GOOGL_COVER},
                                         tags={**last.tags, "shares_outstanding": "test: E1's cover-page total"})
    day = max(f.end for f in rows) + timedelta(days=60)
    prices = [mx.Price(day - timedelta(days=370), close, close, None), mx.Price(day, close, close, None)]
    comp = mx.Company(1, "us-" + ticker.lower(), "operating", 12)
    vals = um.compute(mx.Data(comp, rows, prices, [], []))
    s = assemble.Stored(comp, "US", "USD", rows, {(v.key, v.basis, v.period_kind, v.period_end): (v.value, v.held, v.inputs)
                                                   for v in vals})
    s.metrics[("beta_bottom_up", "none", "market", day)] = (Decimal("1.1"), None, {})
    return s, day


@needs_fixtures
@pytest.mark.parametrize("ticker", list(CIK))
def test_each_fixture_company_is_valued_or_na_with_its_reason(ticker):
    """With current debt filed in parts summed by the adapter (#401's question 1, option A; #455), Microsoft's,
    Nvidia's, Walmart's and Alphabet's models run and round-trip; Apple's stays N/A, as it tags no interest expense
    after FY2023 (its cost of debt is held)."""
    s, day = edgar_company(ticker, Decimal(150), assume_parts=False)
    m = ui.market_inputs(rates(rf="5.29", erp="4.2", rf_day=day, erp_month=day.replace(day=1)), day)
    co = compute(s, day, m, SETTINGS, RULE)
    rev = next(r for r in co.runs if r.kind == "reverse_dcf")
    if ticker == "AAPL":
        assert rev.status == "not_applicable" and rev.reason == "The filing doesn't report interest expense."
    else:
        assert rev.status == "solved" and round_trip(co, m, SETTINGS, rev) is None


@needs_fixtures
@pytest.mark.parametrize("ticker", list(CIK))
def test_every_fixture_operating_company_round_trips_with_current_debt_summed(ticker):
    """With every part of current debt summed (beyond option A's sum, only here), every solved run solves back to its
    growth within 0.01 points, and one that isn't solved says why (Spec 02A §9.5 item 9)."""
    s, day = edgar_company(ticker, Decimal(150), assume_parts=True)
    m = ui.market_inputs(rates(rf="5.29", erp="4.2", rf_day=day, erp_month=day.replace(day=1)), day)
    co = compute(s, day, m, SETTINGS, RULE)
    for r in co.runs:
        assert r.status == "solved" or r.reason, (ticker, r.kind)
    rev = next(r for r in co.runs if r.kind == "reverse_dcf")
    if rev.status == "solved":
        assert round_trip(co, m, SETTINGS, rev) is None
    again = reverse_dcf(co.company, m, SETTINGS, Input.of(Decimal(150), "money", day, "test"),
                        Input.of(Decimal(0), "fraction", day, "test"))
    assert again.status == rev.status


# ---------------------------------------------------------------------------------------------- India unchanged
def test_indias_lines_are_the_labels_and_rules_of_before_the_us_slice():
    """The default `Lines` is India's: the strings India's stored runs carry, and the lease rule (Anand, 30 Oct 2026)."""
    i = assemble.INDIA
    assert (i.calc, i.ebit, i.debt, i.cash_inv, i.opening_ic) == (mx.Calc, mx._ebit, mx._debt, mx._cash_inv,
                                                                 mx._opening_ic)
    assert (i.ebit_name, i.ebit_words, i.cash_words, i.filings, i.lease_rule) == \
        ("ebit_derived", "derived EBIT", "cash and investments", "results filings (NSE)", True)
    assert i.cap_source == "market capitalisation at the latest close (Source: NSE; equity.metrics)"
    assert i.close_source == assemble.CLOSE_SOURCE == "the latest close (Source: NSE; equity.metrics)"
    assert i.beta_source == "bottom-up beta: Damodaran's India industry beta relevered at market D/E (equity.beta)"
    assert i.lender_beta_source == ("the industry's levered beta: Damodaran's India industry beta, not relevered "
                                    "(equity.beta)")
    assert i.columns == assemble.st.COLUMNS and "operating_income" not in i.columns
    assert ui.US.columns[-1] == "operating_income" and ui.US.lease_rule is False


def test_us_market_inputs_are_never_indias():
    m = ui.market_inputs(rates(), PRICE_DAY)
    assert isinstance(m, Market) and m.market == "US"
    text = json.dumps(m.json())
    assert "India" not in text and "ctryprem" not in text and "25.168" not in text
