"""The valuation engine (job 2A-13; Spec 02A §5.7, §9.5 item 9): a DCF worked out by hand and reproduced to the rupee,
the cost of capital's rules, the N/A rules, the tax and growth fades, the default run, the scenario grid, the
market-agnostic rule, the banned-words scan over every reason sentence, and EDGAR's companies valued in USD.

The EDGAR files are in data/fixtures/equity/edgar (the private fixtures repo; those tests skip without it, and CI checks
it out)."""
import ast
import json
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import equity_edgar as E
import pytest
from banned_words import banned_words_in

from ft.equity import assemble
from ft.equity.valuation import (
    MODEL_VERSION,
    Company,
    Input,
    Market,
    NotApplicable,
    cost_of_capital,
    default_run,
    per_share,
    prepare,
    project,
    scenario_grid,
)
from ft.equity.valuation import dcf as dcf_module
from ft.equity.valuation.model import FAMILY_NA, Settings

D = date
CR = Decimal(10_000_000)                    # one crore
AS_OF = D(2026, 10, 30)
SETTINGS = assemble.load_settings()
SRC = Path(__file__).resolve().parents[1] / "src" / "ft" / "equity"
needs_edgar = pytest.mark.skipif(not E.EDGAR.is_dir(), reason="data/fixtures/equity/edgar not present (fixtures repo)")


def i(v, unit: str = "money") -> Input:
    return Input.of(Decimal(str(v)), unit, AS_OF, "test")


def held(reason: str, unit: str = "money") -> Input:
    return Input.withheld(reason, unit, "test")


def company(**over) -> Company:
    """The worked example's company: round inputs, in crore."""
    base = {"key": "zdcf", "market": "IN", "currency": "INR", "family": "operating",
                "revenue_ttm": i(1000 * CR), "ebit_ttm": i(200 * CR), "revenue_3fy": i(2700 * CR), "ebit_3fy": i(540 * CR),
                "tax_3fy": i(108 * CR), "pbt_3fy": i(540 * CR), "opening_capital_3fy": i(1350 * CR),
                "revenue_cagr_3y": i("0.12", "fraction"), "finance_cost_fy": i(40 * CR), "debt_opening_fy": i(400 * CR),
                "debt_closing_fy": i(600 * CR), "debt": i(500 * CR), "cash_and_investments": i(200 * CR), "nci": i(50 * CR),
                "shares": i(10 * CR, "shares"), "market_cap": i(4500 * CR), "beta": i(1, "beta")}
    base.update(over)
    return Company(**base)


def market(**over) -> Market:
    base = {"market": "IN", "currency": "INR", "risk_free": i("0.06", "fraction"), "gross_yield": i("0.075", "fraction"),
                "erp": i("0.07", "fraction"), "marginal_tax": i("0.25", "fraction")}
    base.update(over)
    return Market(**base)


# ---------------------------------------------------------------------------------------------- the worked example
# year, growth %, revenue, EBIT, tax %, NOPAT, reinvestment, FCFF (₹ crore), discount factor, present value (₹ crore)
TABLE = [
    (1, "12.00", "1120.00", "224.00", "20.00", "179.20", "60.00", "119.20", "0.890472", "106.14"),
    (2, "12.00", "1254.40", "250.88", "20.00", "200.70", "67.20", "133.50", "0.792940", "105.86"),
    (3, "12.00", "1404.93", "280.99", "20.00", "224.79", "75.26", "149.52", "0.706091", "105.58"),
    (4, "12.00", "1573.52", "314.70", "20.00", "251.76", "84.30", "167.47", "0.628754", "105.30"),
    (5, "12.00", "1762.34", "352.47", "20.00", "281.97", "94.41", "187.56", "0.559888", "105.01"),
    (6, "10.80", "1952.67", "390.53", "21.00", "308.52", "95.17", "213.36", "0.498565", "106.37"),
    (7, "9.60", "2140.13", "428.03", "22.00", "333.86", "93.73", "240.13", "0.443958", "106.61"),
    (8, "8.40", "2319.90", "463.98", "23.00", "357.26", "89.89", "267.38", "0.395332", "105.70"),
    (9, "7.20", "2486.94", "497.39", "24.00", "378.01", "83.52", "294.50", "0.352032", "103.67"),
    (10, "6.00", "2636.15", "527.23", "25.00", "395.42", "74.61", "320.81", "0.313475", "100.57"),
]


def test_a_dcf_worked_out_by_hand_is_reproduced_to_the_rupee():
    """A synthetic Indian company with round inputs (₹ crore), worked by hand (Spec 02A §9.5 item 9):

    Cost of capital
      Ke   = rf + β × ERP = 6% + 1.0 × 7% = 13%
      Kd   = FY finance costs ÷ average FY debt = 40 ÷ ((400 + 600) ÷ 2) = 8%, inside [7.5%, 13.5%]: not bounded
      E, D = market cap 4,500 and debt 500 → weights 0.9 and 0.1
      WACC = 0.9 × 13% + 0.1 × 8% × (1 − 25%) = 11.7% + 0.6% = 12.3%
    Drivers (three-FY sums: revenue 2,700, EBIT 540, tax 108, PBT 540, opening invested capital 1,350)
      EBIT margin 540 ÷ 2,700 = 20%; effective tax 108 ÷ 540 = 20%; sales-to-capital 2,700 ÷ 1,350 = 2
      g = the 3-FY revenue CAGR, 12% (inside −5%…30%); gₜ = rf = 6%; marginal tax 25%
    Each year: revenue = last year's × (1 + g_t), g_t = 12% for years 1–5, then 12% − 1.2 points a year to 6% in
      year 10; EBIT = 20% of revenue; tax 20% for years 1–5, then + 1 point a year to 25% in year 10;
      NOPAT = EBIT × (1 − tax); reinvestment = Δrevenue ÷ 2; FCFF = NOPAT − reinvestment;
      discount factor = 1 ÷ 1.123^t; present value = FCFF × discount factor.

      year  growth  revenue    EBIT    tax    NOPAT   reinv.   FCFF   discount   PV
       1    12.00%  1,120.00  224.00  20.00%  179.20   60.00  119.20  0.890472  106.14
       2    12.00%  1,254.40  250.88  20.00%  200.70   67.20  133.50  0.792940  105.86
       3    12.00%  1,404.93  280.99  20.00%  224.79   75.26  149.52  0.706091  105.58
       4    12.00%  1,573.52  314.70  20.00%  251.76   84.30  167.47  0.628754  105.30
       5    12.00%  1,762.34  352.47  20.00%  281.97   94.41  187.56  0.559888  105.01
       6    10.80%  1,952.67  390.53  21.00%  308.52   95.17  213.36  0.498565  106.37
       7     9.60%  2,140.13  428.03  22.00%  333.86   93.73  240.13  0.443958  106.61
       8     8.40%  2,319.90  463.98  23.00%  357.26   89.89  267.38  0.395332  105.70
       9     7.20%  2,486.94  497.39  24.00%  378.01   83.52  294.50  0.352032  103.67
      10     6.00%  2,636.15  527.23  25.00%  395.42   74.61  320.81  0.313475  100.57
                                                           Σ PV, years 1–10 = 1,050.82

    Terminal value
      NOPAT₁₁ = 2,636.15 × 1.06 × 20% × (1 − 25%) = 419.15
      TV₁₀    = 419.15 × (1 − 6% ÷ 12.3%) ÷ (12.3% − 6%) = 419.15 × 0.512195 ÷ 0.063 = 3,407.71
      its PV  = 3,407.71 × 0.313475 = 1,068.23
    Equity
      operations = 1,050.82 + 1,068.23 = 2,119.05
      equity     = 2,119.05 + cash and investments 200 − debt 500 − NCI 50 = 1,769.05
      per share  = ₹1,769.05 crore ÷ 10 crore shares = ₹176.90, so ₹177 to the rupee.
    """
    c, m = company(), market()
    cap = cost_of_capital(c, m, SETTINGS)
    assert (cap.cost_of_equity, cap.cost_of_debt, cap.bounded) == (Decimal("0.13"), Decimal("0.08"), None)
    assert (cap.weight_equity, cap.weight_debt, cap.wacc) == (Decimal("0.9"), Decimal("0.1"), Decimal("0.123"))
    d, _, _ = prepare(c, m, SETTINGS)
    assert (d.margin, d.tax_effective, d.sales_to_capital, d.terminal_growth) == \
        (Decimal("0.2"), Decimal("0.2"), Decimal(2), Decimal("0.06"))
    p = project(d, Decimal("0.12"))
    tol = Decimal("0.0051")
    for y, row in zip(p.years, TABLE, strict=True):
        t, g, rev, ebit, tax, nopat, reinv, fcff, df, pv = row
        assert y.year == t
        assert abs(y.growth * 100 - Decimal(g)) < tol and abs(y.tax_rate * 100 - Decimal(tax)) < tol
        for got, want in ((y.revenue, rev), (y.ebit, ebit), (y.nopat, nopat), (y.reinvestment, reinv),
                          (y.fcff, fcff), (y.present_value, pv)):
            assert abs(got / CR - Decimal(want)) < tol, (t, got / CR, want)
        assert abs(y.discount_factor - Decimal(df)) < Decimal("0.0000006")
    assert abs(sum(y.present_value for y in p.years) / CR - Decimal("1050.82")) < tol
    assert abs(p.nopat_11 / CR - Decimal("419.15")) < tol
    assert abs(p.terminal_value / CR - Decimal("3407.71")) < tol
    assert abs(p.terminal_present_value / CR - Decimal("1068.23")) < tol
    assert abs(p.equity / CR - Decimal("1769.05")) < tol
    run = default_run(c, m, SETTINGS)
    assert run.status == "solved" and run.reason is None and run.model_version == MODEL_VERSION == "dcf-1"
    assert run.value_per_share == Decimal("176.90")
    assert run.value_per_share.quantize(Decimal(1)) == 177                       # to the rupee
    assert per_share(d, Decimal("0.12")) == p.per_share                         # the cheap function agrees


def test_the_run_records_every_input_with_its_value_unit_date_and_source():
    run = default_run(company(), market(), SETTINGS)
    got = run.inputs
    for name, v in got["company"].items():
        if isinstance(v, dict):
            assert set(v) >= {"value", "unit", "as_of", "source"}, name
    assert got["market"]["risk_free"]["value"] == "0.06" and got["market"]["currency"] == "INR"
    assert got["growth"] == {"revenue_cagr_3y": "0.12", "used": "0.12", "clamped": False}
    assert len(got["projection"]["years"]) == 10 and Decimal(got["capital"]["wacc"]) == Decimal("0.123")
    assert got["capital"]["cost_of_debt_label"] == "derived"
    json.dumps(got)                                                               # storable as jsonb


# ---------------------------------------------------------------------------------------------- paths and fades
def test_year_10_grows_at_terminal_growth_and_tax_reaches_the_marginal_rate():
    d, _, _ = prepare(company(), market(), SETTINGS)
    for g in (Decimal("-0.05"), Decimal("0.3")):
        ys = project(d, g).years
        assert [y.growth for y in ys[:5]] == [g] * 5
        assert ys[9].growth == d.terminal_growth
        assert ys[5].growth == g + (d.terminal_growth - g) / 5
    ys = project(d, Decimal("0.1")).years
    assert [y.tax_rate for y in ys[:5]] == [Decimal("0.2")] * 5
    assert ys[5].tax_rate == Decimal("0.21") and ys[9].tax_rate == Decimal("0.25")


@pytest.mark.parametrize("tax,pbt,why", [(-10, 540, "outside"), (300, 540, "outside"), (10, -540, "isn't positive"),
                                         (0, 0, "isn't positive")])
def test_an_effective_rate_outside_0_to_50_or_a_loss_uses_the_marginal_rate(tax, pbt, why):
    d, _, notes = prepare(company(tax_3fy=i(tax * CR), pbt_3fy=i(pbt * CR)), market(), SETTINGS)
    assert d.tax_effective == Decimal("0.25") and why in notes["tax_rate_years_1_5"]


def test_a_50_percent_effective_rate_is_kept():
    d, _, _ = prepare(company(tax_3fy=i(270 * CR)), market(), SETTINGS)
    assert d.tax_effective == Decimal("0.5")


# ---------------------------------------------------------------------------------------------- cost of capital
def test_the_cost_of_debt_is_bounded_below_by_the_gross_yield_and_above_by_it_plus_6_points():
    low = cost_of_capital(company(finance_cost_fy=i(10 * CR)), market(), SETTINGS)      # 2%
    assert (low.cost_of_debt, low.cost_of_debt_raw, low.bounded) == (Decimal("0.075"), Decimal("0.02"), "floor")
    high = cost_of_capital(company(finance_cost_fy=i(100 * CR)), market(), SETTINGS)    # 20%
    assert (high.cost_of_debt, high.cost_of_debt_raw, high.bounded) == (Decimal("0.135"), Decimal("0.2"), "cap")
    assert high.json()["cost_of_debt_bounded"] == "cap"


def test_nil_debt_has_no_debt_weight_and_needs_no_cost_of_debt():
    c = company(debt=i(0), finance_cost_fy=held("The filing doesn't report finance costs."))
    cap = cost_of_capital(c, market(), SETTINGS)
    assert (cap.weight_debt, cap.cost_of_debt, cap.wacc) == (Decimal(0), None, Decimal("0.13"))
    assert default_run(c, market(), SETTINGS).status == "solved"


def test_debt_now_but_none_at_either_end_of_the_year_holds_the_cost_of_debt():
    c = company(debt_opening_fy=i(0), debt_closing_fy=i(0))
    run = default_run(c, market(), SETTINGS)
    assert run.status == "not_applicable" and "cost of debt can't be derived" in run.reason


# ---------------------------------------------------------------------------------------------- N/A rules
@pytest.mark.parametrize("family", ["lender", "insurer", "mixed"])
def test_lenders_insurers_and_mixed_companies_are_na_with_their_reason(family):
    run = default_run(company(family=family), market(), SETTINGS)
    assert (run.status, run.reason, run.value_per_share) == ("not_applicable", FAMILY_NA[family], None)
    grid = scenario_grid(company(family=family), market(), SETTINGS)
    assert grid.status == "not_applicable" and grid.cells == ()


@pytest.mark.parametrize("over,reason", [
    ({"ebit_ttm": i(0)}, "EBIT over the last four quarters is zero or negative."),
    ({"ebit_ttm": i(-5 * CR)}, "EBIT over the last four quarters is zero or negative."),
    ({"ebit_3fy": i(0)}, "EBIT over the last three financial years is zero or negative."),
    ({"opening_capital_3fy": i(0)}, "Invested capital is zero or negative."),
    ({"opening_capital_3fy": i(-10 * CR)}, "Invested capital is zero or negative."),
    ({"revenue_3fy": i(-1)}, "Sales-to-capital is zero or negative."),
    ({"revenue_ttm": i(0)}, "Revenue over the last four quarters is zero or negative."),
    ({"shares": i(0, "shares")}, "Shares outstanding aren't positive."),
])
def test_each_na_rule_gives_its_reason_and_no_number(over, reason):
    run = default_run(company(**over), market(), SETTINGS)
    assert (run.status, run.reason, run.value_per_share) == ("not_applicable", reason, None)


def test_wacc_at_or_below_terminal_growth_is_na():
    # rf 12.3% ≥ the WACC that follows from it: Ke = 12.3% + 7% = 19.3% … so lift rf past the WACC instead
    m = market(risk_free=i("0.20", "fraction"), erp=i("0.0", "fraction"))           # Ke = 20%, WACC < 20% = gₜ
    run = default_run(company(), m, SETTINGS)
    assert run.status == "not_applicable" and run.reason == dcf_module.WACC_NOT_ABOVE_GROWTH
    with pytest.raises(NotApplicable):
        per_share(replace(prepare(company(), market(), SETTINGS)[0], wacc=Decimal("0.06")), Decimal("0.1"))


@pytest.mark.parametrize("name", ["revenue_ttm", "ebit_3fy", "opening_capital_3fy", "debt", "cash_and_investments",
                                  "nci", "shares", "market_cap", "beta", "finance_cost_fy", "tax_3fy"])
def test_any_held_input_makes_the_model_na_with_that_input_s_own_reason(name):
    why = f"Held for the test: {name}."
    unit = {"shares": "shares", "beta": "beta"}.get(name, "money")
    run = default_run(company(**{name: held(why, unit)}), market(), SETTINGS)
    assert (run.status, run.reason) == ("not_applicable", why)


def test_a_held_market_rate_makes_the_model_na_with_its_reason():
    run = default_run(company(), market(erp=held("India's equity risk premium isn't stored.", "fraction")), SETTINGS)
    assert run.reason == "India's equity risk premium isn't stored."


def test_a_reason_the_caller_holds_the_model_for_comes_first_for_operating_companies():
    run = default_run(company(extra_held=(assemble.LEASE_MATERIAL,)), market(), SETTINGS)
    assert (run.status, run.reason) == ("not_applicable", assemble.LEASE_MATERIAL)
    assert scenario_grid(company(extra_held=(assemble.LEASE_MATERIAL,)), market(), SETTINGS).status == \
        "not_applicable"


# ---------------------------------------------------------------------------------------------- the default run
@pytest.mark.parametrize("cagr,used", [("0.45", "0.30"), ("-0.20", "-0.05"), ("0.30", "0.30"), ("-0.05", "-0.05")])
def test_the_default_run_clamps_the_3fy_cagr_to_minus_5_to_30_percent(cagr, used):
    run = default_run(company(revenue_cagr_3y=i(cagr, "fraction")), market(), SETTINGS)
    assert run.status == "solved" and Decimal(run.inputs["growth"]["used"]) == Decimal(used)
    assert run.inputs["growth"]["clamped"] == (Decimal(cagr) != Decimal(used))


def test_a_held_cagr_makes_the_default_run_na_but_the_grid_is_still_built():
    why = "The window spans a merger, so its years aren't comparable."
    c = company(revenue_cagr_3y=held(why, "fraction"))
    run = default_run(c, market(), SETTINGS)
    assert (run.status, run.reason) == ("not_applicable", why)
    grid = scenario_grid(c, market(), SETTINGS)
    assert grid.status == "solved" and len(grid.cells) == 375


# ---------------------------------------------------------------------------------------------- the scenario grid
def test_the_grid_has_375_cells_on_its_steps_and_each_matches_the_model():
    c, m = company(), market()
    grid = scenario_grid(c, m, SETTINGS)
    assert grid.status == "solved" and len(grid.cells) == 375 and grid.model_version == MODEL_VERSION
    assert sorted({x.growth for x in grid.cells}) == [Decimal("-0.05") + Decimal("0.025") * k for k in range(15)]
    assert sorted({x.ebit_margin for x in grid.cells}) == [Decimal(v) for v in ("0.15", "0.175", "0.2", "0.225",
                                                                                 "0.25")]
    assert sorted({x.wacc for x in grid.cells}) == [Decimal(v) for v in ("0.103", "0.113", "0.123", "0.133", "0.143")]
    centre = next(x for x in grid.cells if x.growth == Decimal("0.12") - Decimal("0.02") and
                  x.ebit_margin == Decimal("0.2") and x.wacc == Decimal("0.123"))
    d, _, _ = prepare(c, m, SETTINGS)
    assert centre.value_per_share == dcf_module.cents(per_share(d, Decimal("0.1")))
    at_default = next(x for x in grid.cells if x.growth == Decimal("0.125") and x.ebit_margin == Decimal("0.2")
                      and x.wacc == Decimal("0.123"))
    assert at_default.value_per_share > default_run(c, m, SETTINGS).value_per_share       # 12.5% > 12% growth


def test_grid_margins_at_or_below_zero_are_skipped_and_waccs_at_or_below_terminal_growth_are_holes():
    # margin 4%: −5 points skipped (−1%); WACC 7% with gₜ 6%: −1 (6%) and −2 (5%) points give no value
    c = company(ebit_3fy=i(108 * CR), debt=i(0), beta=i("0.1", "beta"))            # Ke = 6% + 0.1 × 10% = 7%
    m = market(erp=i("0.10", "fraction"))
    grid = scenario_grid(c, m, SETTINGS)
    assert grid.status == "solved"
    assert {x.ebit_margin for x in grid.cells} == {Decimal(v) for v in ("0.015", "0.04", "0.065", "0.09")}
    holes = [x for x in grid.cells if x.value_per_share is None]
    assert {x.wacc for x in holes} == {Decimal("0.05"), Decimal("0.06")}
    assert all(x.reason == dcf_module.WACC_NOT_ABOVE_GROWTH for x in holes)
    assert len(grid.cells) == 15 * 4 * 5 and len(holes) == 15 * 4 * 2


def test_the_grid_is_built_even_when_the_base_wacc_is_not_above_terminal_growth():
    c = company(debt=i(0), beta=i("0.0", "beta"))                                  # Ke = WACC = rf = gₜ
    assert default_run(c, market(), SETTINGS).reason == dcf_module.WACC_NOT_ABOVE_GROWTH
    grid = scenario_grid(c, market(), SETTINGS)
    assert grid.status == "solved"
    assert {x.wacc for x in grid.cells if x.value_per_share is not None} == {Decimal("0.07"), Decimal("0.08")}


# ---------------------------------------------------------------------------------------------- guards
def test_a_company_runs_only_on_its_own_market_s_parameters():
    with pytest.raises(ValueError, match="can't run"):
        default_run(company(), E.market(), SETTINGS)
    with pytest.raises(ValueError, match="can't run"):
        scenario_grid(company(currency="USD"), market(), SETTINGS)


def test_an_input_needs_a_value_or_a_reason_not_both():
    with pytest.raises(ValueError):
        Input(Decimal(1), "money", None, "x", "and a reason")
    with pytest.raises(ValueError):
        Input(None, "money", None, "x", None)


def test_the_settings_need_their_sources(tmp_path):
    raw = assemble._valuation_yaml()["model"]
    assert Settings.from_mapping(raw) == SETTINGS
    with pytest.raises(ValueError, match="source"):
        Settings.from_mapping({**raw, "scenario_grid": {k: v for k, v in raw["scenario_grid"].items()
                                                         if k != "source"}})


WORDS = re.compile(r"\b(India|Indian|INR|NSE|BSE)\b")
LITERALS = ("25.168", "ctryprem", "betaIndia")


def test_the_engine_names_no_market():
    """Market-agnostic (Spec 02A §5.7): nothing in ft/equity/valuation/ names India, its currency, its exchanges or its
    parameters; whole words, so "expense" and "license" don't trip it."""
    files = sorted((SRC / "valuation").glob("*.py"))
    assert len(files) >= 4
    for f in files:
        text = f.read_text("utf-8")
        assert not WORDS.search(text), (f.name, WORDS.search(text).group(0))
        assert not [x for x in LITERALS if x in text], f.name


def _sentences(path: Path) -> list[str]:
    """Every string literal in a module that reads as a sentence (it has a space and ends with a full stop), docstrings
    aside (they aren't the site's text)."""
    tree = ast.parse(path.read_text("utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            s = node.value.strip()
            if " " in s and s.endswith("."):
                out.append(s)
    return out


def test_every_reason_sentence_passes_the_banned_words_scan():
    files = sorted((SRC / "valuation").glob("*.py")) + [SRC / "assemble.py"]
    sentences = [s for f in files for s in _sentences(f)]
    assert len(sentences) > 15
    found = {s: banned_words_in(s) for s in sentences if banned_words_in(s)}
    assert not found, found
    produced = [default_run(company(**over), market(), SETTINGS).reason for over in (
        {"family": "lender"}, {"family": "insurer"}, {"family": "mixed"}, {"ebit_ttm": i(0)},
        {"opening_capital_3fy": i(0)}, {"debt_opening_fy": i(0), "debt_closing_fy": i(0)})]
    assert all(r and not banned_words_in(r) for r in produced)


def test_the_banned_words_scan_finds_whole_words_only():
    assert banned_words_in("Fair value: a BUY, upside and a target") == ["fair value", "buy", "upside", "target"]
    assert banned_words_in("expenses, buyback-free, targeting, sellers") == []


# ---------------------------------------------------------------------------------------------- EDGAR, in USD
VALUED = ("AAPL", "MSFT", "NVDA", "WMT", "GOOGL", "GEV")


@needs_edgar
@pytest.mark.parametrize("ticker", VALUED)
def test_edgar_companies_are_valued_in_usd_finite_and_reproducible(ticker):
    c, m = E.company(ticker), E.market()
    assert (c.market, c.currency, m.currency) == ("US", "USD", "USD")
    run = default_run(c, m, SETTINGS)
    assert run.status == "solved", run.reason
    assert run.value_per_share.is_finite() and run.value_per_share > 0
    again = default_run(E.company(ticker), E.market(), SETTINGS)
    assert again.value_per_share == run.value_per_share and again.inputs == run.inputs
    grid = scenario_grid(c, m, SETTINGS)
    assert grid.status == "solved" and grid.cells
    assert all(x.value_per_share.is_finite() for x in grid.cells if x.value_per_share is not None)
    text = json.dumps(run.inputs)
    assert not re.search(r"\b(INR|India|Indian|NSE|BSE|IN)\b", text) and "0.25168" not in text, ticker
    assert run.inputs["market"]["risk_free"] == {"value": "0.0529", "unit": "fraction", "as_of": "2026-10-01",
                                                 "source": run.inputs["market"]["risk_free"]["source"]}
    assert run.inputs["market"]["marginal_tax"]["value"] == "0.25"


@needs_edgar
@pytest.mark.parametrize("ticker,family", [("JPM", "lender"), ("BRK-B", "insurer")])
def test_edgar_financials_are_na(ticker, family):
    run = default_run(E.company(ticker), E.market(), SETTINGS)
    assert (run.status, run.reason) == ("not_applicable", FAMILY_NA[family])


@needs_edgar
@pytest.mark.parametrize("ticker", ["O", "XOM"])
def test_edgar_companies_without_an_operating_income_line_are_na(ticker):
    run = default_run(E.company(ticker), E.market(), SETTINGS)
    assert (run.status, run.reason) == ("not_applicable", "No operating-income line is filed.")


@needs_edgar
def test_edgar_rules_succession_multi_class_shares_and_tag_fallbacks():
    xom = E.Filer("XOM")
    assert xom.fy_ends[-1] == D(2025, 12, 31) and len(xom.fy_ends) >= 4          # Exxon Mobil Corp's history merged
    googl = E.company("GOOGL")
    assert googl.shares.value == Decimal(12230000000) and "cover page" in googl.shares.source   # three classes, E1
    assert E.company("MSFT").shares.value == Decimal(7425545491)             # one class, companyfacts dei
    aapl = E.Filer("AAPL")
    assert E.pick_tags(aapl.facts, E.FIELD_TAGS["revenue"], True)[0] == \
        "RevenueFromContractWithCustomerExcludingAssessedTax"                   # red flag 10: the current tag
    assert all(f.form.split("/")[0] in E.PERIODIC_FORMS for fs in aapl.facts.values() for f in fs)
    assert E.company("AAPL").finance_cost_fy.as_of == D(2023, 9, 30)          # Apple's last year tagging interest
