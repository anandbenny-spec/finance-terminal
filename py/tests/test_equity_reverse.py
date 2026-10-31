"""The reverse DCF and a lender's implied return on equity (job 2A-14; Spec 02A §5.9, §9.5 item 9): the round trip
(value at g → solve → g within 0.01 points) for the synthetic company and EDGAR's valued companies, every outcome
from constructed inputs with its sentence, the delivered figures and the gap, implied ROE by hand and its N/A rules,
the delivered ROE's window and its structural-break rule, the inputs built from the saved filings, and the banned-words
scan over every sentence.

The worked example's company and market come from test_equity_valuation; EDGAR's files and the saved filings are in
data/fixtures/equity (the private fixtures repo; those tests skip without it, and CI checks it out)."""
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import equity_edgar as E
import pytest
from banned_words import banned_words_in
from test_equity_assemble import T, computed, india, stored
from test_equity_statements import needs_fixtures
from test_equity_valuation import CR, SETTINGS, VALUED, company, held, i, market, needs_edgar

from ft.equity import assemble
from ft.equity import metrics as mx
from ft.equity.valuation import (
    IMPLIED_ROE_VERSION,
    MODEL_VERSION,
    Input,
    Lender,
    Run,
    implied_roe,
    per_share,
    prepare,
    reverse_dcf,
)
from ft.equity.valuation import dcf as dcf_module
from ft.equity.valuation import reverse as R
from ft.equity.valuation.implied_roe import KE_NOT_ABOVE_GROWTH, OPERATING_NA
from ft.equity.valuation.model import FAMILY_NA

D = date
ZERO = Decimal(0)
TOL = Decimal("0.0001")                                  # 0.01 points
DELIVERED = i("0.10", "fraction")
ROUND_TRIP = [Decimal(x) / 1000 for x in range(-150, 451, 25)] + [Decimal("0.0333"), Decimal("0.1234"),
                                                                     Decimal("0.2718"), Decimal("0.4499")]


def price(v) -> Input:
    return Input.of(Decimal(str(v)), "money", D(2026, 10, 30), "test")


def shaped(roc_over_wacc: str, tax: str, sales_to_capital: str = "2", **over):
    """The worked example's company with its 3-FY EBIT (and tax) set so that its 3-FY return on capital is the given
    multiple of its WACC (12.3%) at the given effective rate."""
    w, te, s = Decimal("0.123"), Decimal(tax), Decimal(sales_to_capital)
    margin = w * Decimal(roc_over_wacc) / ((1 - te) * s)
    return company(**{"ebit_3fy": i(margin * 2700 * CR), "tax_3fy": i(te * 540 * CR),
                      "opening_capital_3fy": i(2700 / s * CR), **over})


def round_trip(c, m) -> list[tuple]:
    d, _, _ = prepare(c, m, SETTINGS)
    misses = []
    for g in ROUND_TRIP:
        p = per_share(d, g)
        assert p > 0, (c.key, g, p)
        run = reverse_dcf(c, m, SETTINGS, price(p), DELIVERED)
        if run.status != "solved" or abs(run.result - g) > TOL:
            misses.append((g, run.outcome, run.result))
    return misses


# ---------------------------------------------------------------------------------------------- the round trip
def test_the_synthetic_company_round_trips_across_minus_15_to_45_percent():
    assert round_trip(company(), market()) == []


@needs_edgar
@pytest.mark.parametrize("ticker", [t for t in VALUED if t != "GEV"])
def test_edgar_companies_round_trip_in_usd(ticker):
    assert round_trip(E.company(ticker), E.market()) == []


@needs_edgar
def test_ge_vernova_s_return_on_capital_is_below_its_wacc_so_growth_lowers_its_value():
    """GE Vernova (valued by 2A-13) earns 3.7% on capital against a WACC of 9.5%: no round trip, by §5.9's rule."""
    run = reverse_dcf(E.company("GEV"), E.market(), SETTINGS, price(30), DELIVERED)
    assert (run.status, run.outcome, run.inputs["reverse"]["shape"]) == ("no_solution", "growth_lowers_value",
                                                                          "falling")
    assert run.reason == ("At its 3-year return on capital of 3.7%, below its cost of capital of 9.5%, faster growth "
                          "lowers the model's value, so no growth rate matches the price.")


def test_the_solver_brackets_on_the_1_point_grid_and_bisects_to_0_01_points():
    assert len(R.GRID) == 71 and R.GRID[0] == Decimal("-0.20") and R.GRID[-1] == Decimal("0.50")
    d, _, _ = prepare(company(), market(), SETTINGS)
    on_grid = reverse_dcf(company(), market(), SETTINGS, price(per_share(d, Decimal("0.07"))), DELIVERED)
    assert on_grid.result == Decimal("0.0700")
    for g in (Decimal("-0.20"), Decimal("0.50")):                  # the range's own ends are inside it
        run = reverse_dcf(company(), market(), SETTINGS, price(per_share(d, g)), DELIVERED)
        assert run.result == g.quantize(TOL)
    off = reverse_dcf(company(), market(), SETTINGS, price(per_share(d, Decimal("0.123456"))), DELIVERED)
    assert off.result == Decimal("0.1235") and off.result.as_tuple().exponent == -4


# ---------------------------------------------------------------------------------------------- the outcomes
def test_a_solved_run_carries_the_price_the_delivered_growth_the_gap_and_every_input():
    d, _, _ = prepare(company(), market(), SETTINGS)
    run = reverse_dcf(company(), market(), SETTINGS, price(per_share(d, Decimal("0.15"))), DELIVERED)
    assert (run.kind, run.status, run.outcome, run.reason, run.model_version) == \
        ("reverse_dcf", "solved", "solved", None, MODEL_VERSION)
    assert run.result == Decimal("0.1500") and run.delivered == Decimal("0.10") and run.delivered_reason is None
    assert run.gap == Decimal("0.0500") and run.inputs["gap"] == "0.0500"
    got = json.loads(json.dumps(run.inputs))                       # storable as jsonb
    assert got["outcome"] == "solved" and got["price"]["unit"] == "money" and got["delivered"]["value"] == "0.10"
    assert Decimal(got["reverse"]["return_on_capital"]) == Decimal("0.32") and got["reverse"]["shape"] == "rising"
    assert {"company", "market", "settings", "drivers", "capital", "notes"} <= set(got)


def test_a_held_delivered_growth_leaves_the_result_with_no_gap():
    d, _, _ = prepare(company(), market(), SETTINGS)
    why = "The window spans a demerger, so its years aren't comparable."
    run = reverse_dcf(company(), market(), SETTINGS, price(per_share(d, Decimal("0.15"))), held(why, "fraction"))
    assert run.status == "solved" and (run.delivered, run.delivered_reason, run.gap) == (None, why, None)
    assert "gap" not in run.inputs


def test_growth_lowers_value_when_the_return_on_capital_is_below_wacc_with_x_and_y_filled():
    c = shaped("0.5", "0.2")                                       # 6.15% on capital against 12.3%
    run = reverse_dcf(c, market(), SETTINGS, price(100), DELIVERED)
    assert (run.status, run.outcome, run.result) == ("no_solution", "growth_lowers_value", None)
    assert run.inputs["reverse"]["shape"] == "falling"
    assert run.reason == ("At its 3-year return on capital of 6.2%, below its cost of capital of 12.3%, faster growth "
                          "lowers the model's value, so no growth rate matches the price.")


def test_a_return_just_below_wacc_still_gets_no_number_but_not_the_falling_value_sentence():
    """Between WACC ÷ (1 + WACC) and WACC the model's value rises a little with growth (each year's reinvestment buys
    that year's revenue), so "faster growth lowers the model's value" would be false there; no number either way
    (Spec 02A §10 decision 3)."""
    c = shaped("0.95", "0.25")                                     # at the marginal rate: no tax fade
    run = reverse_dcf(c, market(), SETTINGS, price(100), DELIVERED)
    assert (run.status, run.outcome, run.inputs["reverse"]["shape"]) == ("no_solution", "return_below_cost", "rising")
    assert run.reason == ("At its 3-year return on capital of 11.7%, at or below its cost of capital of 12.3%, growth "
                          "at that return earns no more than it costs, so no growth rate is matched to the price.")


@pytest.mark.parametrize("ratio,tax,s", [("1.01", "0.05", "1"), ("1.02", "0", "1")])
def test_a_low_tax_rate_fading_to_the_marginal_rate_is_held_not_solved(ratio, tax, s):
    """Taxed well below the marginal rate with a return just above WACC, the later years earn less than WACC after
    the fade, so the value rises and then falls across the grid: two growth rates could match one price."""
    c = shaped(ratio, tax, s)
    run = reverse_dcf(c, market(), SETTINGS, price(100), DELIVERED)
    assert (run.status, run.outcome, run.inputs["reverse"]["shape"]) == ("no_solution", "tax_fade", "neither")
    te = R.pct(Decimal(tax))
    assert run.reason == (f"Its tax rate rises from {te} to the marginal 25.0% in the model's later years, so its "
                          f"value doesn't rise steadily with growth and no single growth rate matches the price.")


def test_a_value_that_does_not_rise_with_no_tax_fade_is_a_bug(monkeypatch):
    monkeypatch.setattr(R, "per_share", lambda d, g: Decimal(100) - (g - Decimal("0.1")) ** 2 * 100)
    with pytest.raises(AssertionError, match="a bug"):
        reverse_dcf(shaped("2", "0.25"), market(), SETTINGS, price(100), DELIVERED)


def test_prices_outside_the_range_give_its_two_sentences():
    d, _, _ = prepare(company(), market(), SETTINGS)
    low = reverse_dcf(company(), market(), SETTINGS, price(per_share(d, Decimal("-0.25"))), DELIVERED)
    high = reverse_dcf(company(), market(), SETTINGS, price(per_share(d, Decimal("0.55"))), DELIVERED)
    assert (low.outcome, low.reason) == ("below_range", "The price implies growth below −20% a year.")
    assert (high.outcome, high.reason) == ("above_range", "The price implies growth above 50% a year.")
    assert low.status == high.status == "no_solution" and low.result is high.result is None
    assert low.delivered == Decimal("0.10")                        # the delivered figure is still shown


@pytest.mark.parametrize("over,reason", [
    ({"family": "lender"}, FAMILY_NA["lender"]),
    ({"family": "insurer"}, FAMILY_NA["insurer"]),
    ({"family": "mixed"}, FAMILY_NA["mixed"]),
    ({"ebit_ttm": i(-1)}, "EBIT over the last four quarters is zero or negative."),
    ({"opening_capital_3fy": i(0)}, "Invested capital is zero or negative."),
    ({"beta": held("The bottom-up beta isn't stored.", "beta")}, "The bottom-up beta isn't stored."),
])
def test_the_dcf_s_na_comes_first_even_where_growth_would_lower_value(over, reason):
    c = shaped("0.5", "0.2", **over)                               # would be growth_lowers_value if it ran
    run = reverse_dcf(c, market(), SETTINGS, price(100), DELIVERED)
    assert (run.status, run.outcome, run.reason, run.result) == ("not_applicable", "not_applicable", reason, None)


def test_wacc_at_or_below_terminal_growth_and_a_held_or_non_positive_price_are_na():
    m = market(risk_free=i("0.20", "fraction"), erp=i("0.0", "fraction"))          # Ke = 20%, WACC < 20% = gₜ
    run = reverse_dcf(company(), m, SETTINGS, price(100), DELIVERED)
    assert run.status == "not_applicable" and run.reason == dcf_module.WACC_NOT_ABOVE_GROWTH
    gone = reverse_dcf(company(), market(), SETTINGS, Input.withheld("The latest close isn't stored.", "money", "t"),
                       DELIVERED)
    assert (gone.reason, gone.price) == ("The latest close isn't stored.", None)
    assert reverse_dcf(company(), market(), SETTINGS, price(0), DELIVERED).reason == "The latest close isn't positive."


def test_the_currency_guard_holds_for_the_reverse_dcf():
    with pytest.raises(ValueError):
        reverse_dcf(company(), market(market="US", currency="USD"), SETTINGS, price(100), DELIVERED)


def test_x_and_y_get_more_decimals_only_when_one_would_show_them_equal():
    assert R.pct_pair(Decimal("0.1196"), Decimal("0.1204")) == ("11.96%", "12.04%")
    assert R.pct_pair(Decimal("0.062"), Decimal("0.123")) == ("6.2%", "12.3%")
    assert R.pct(Decimal("-0.2"), 0) == "−20%"


def test_a_run_holds_its_shape():
    base = {"kind": "reverse_dcf", "status": "solved", "outcome": "solved", "reason": None, "price": ZERO,
            "result": ZERO, "delivered": ZERO, "delivered_reason": None, "inputs": {}, "model_version": "x"}
    Run(**base)
    for bad in ({"result": None}, {"status": "no_solution"}, {"delivered_reason": "x"},
                {"delivered": None}, {"kind": "dcf_default"}, {"status": "held", "result": None, "reason": "x"}):
        with pytest.raises(ValueError):
            Run(**{**base, **bad})


# ---------------------------------------------------------------------------------------------- implied ROE
def lender(**over) -> Lender:
    base = {"key": "zl", "market": "IN", "currency": "INR", "family": "lender", "pb": i(2, "multiple"),
            "beta": i(1, "beta"), "delivered_roe": i("0.16", "fraction"), "price": price(500)}
    base.update(over)
    return Lender(**base)


def roe_market(**over):
    return market(**{"risk_free": i("0.05", "fraction"), "erp": i("0.07", "fraction"), **over})


def test_implied_roe_by_hand():
    """P/B 2.0, Ke = 5% + 1.0 × 7% = 12%, g = 5%: implied ROE = 5% + 2.0 × (12% − 5%) = 19%."""
    run = implied_roe(lender(), roe_market())
    assert (run.kind, run.status, run.outcome, run.model_version) == ("implied_roe", "solved", "solved",
                                                                      IMPLIED_ROE_VERSION)
    assert run.result == Decimal("0.1900") and run.price == Decimal(500)
    assert run.delivered == Decimal("0.16") and run.gap == Decimal("0.0300") and run.inputs["gap"] == "0.0300"
    assert run.inputs["model"]["cost_of_equity"] == "0.12" and run.inputs["model"]["terminal_growth"] == "0.05"
    json.dumps(run.inputs)


def test_implied_roe_uses_the_beta_it_is_given_without_relevering():
    run = implied_roe(lender(beta=i("0.8", "beta"), pb=i("1.5", "multiple")), roe_market())
    ke = Decimal("0.05") + Decimal("0.8") * Decimal("0.07")                 # 10.6%
    assert run.result == (Decimal("0.05") + Decimal("1.5") * (ke - Decimal("0.05"))).quantize(TOL)


@pytest.mark.parametrize("over,mkt,reason", [
    ({"beta": i(0, "beta")}, {}, KE_NOT_ABOVE_GROWTH),                     # Ke = rf = g
    ({}, {"erp": i("-0.01", "fraction")}, KE_NOT_ABOVE_GROWTH),
    ({"pb": i(0, "multiple")}, {}, "Price-to-book isn't positive."),
    ({"pb": held("Equity to owners is zero or negative.", "multiple")}, {}, "Equity to owners is zero or negative."),
    ({"family": "insurer"}, {}, FAMILY_NA["insurer"]),
    ({"family": "mixed"}, {}, FAMILY_NA["mixed"]),
    ({"family": "operating"}, {}, OPERATING_NA),
])
def test_implied_roe_is_na_with_its_reason(over, mkt, reason):
    run = implied_roe(lender(**over), roe_market(**mkt))
    assert (run.status, run.reason, run.result) == ("not_applicable", reason, None)


def test_insurers_and_mixed_companies_get_the_spec_s_sentences_word_for_word():
    assert FAMILY_NA["insurer"].lower() == "book value doesn't capture an insurer's value; embedded value is v2."
    assert FAMILY_NA["mixed"].lower() == ("consolidated figures include a lending or insurance business; a "
                                          "sum-of-the-parts valuation isn't built yet.")
    for family in ("insurer", "mixed"):
        c = company(family=family)
        assert reverse_dcf(c, market(), SETTINGS, price(100), DELIVERED).reason == FAMILY_NA[family]


def test_a_held_delivered_roe_leaves_implied_roe_with_no_gap_and_the_currency_guard_holds():
    why = "Fewer than three consecutive financial years of return on equity are filed, so its average isn't shown."
    run = implied_roe(lender(delivered_roe=held(why, "fraction")), roe_market())
    assert run.status == "solved" and (run.delivered, run.delivered_reason, run.gap) == (None, why, None)
    with pytest.raises(ValueError):
        implied_roe(lender(), roe_market(market="US", currency="USD"))


# ---------------------------------------------------------------------------------------------- delivered ROE
FY = [D(2022, 3, 31), D(2023, 3, 31), D(2024, 3, 31), D(2025, 3, 31), D(2026, 3, 31)]


def with_roe(values: dict, *, basis: str = "consolidated", breaks=(), pb_basis: str = "consolidated"):
    """A lender with only market and year metrics: its ROE by year (a str value or a held reason prefixed '!')."""
    s = assemble.Stored(mx.Company(1, "zl", "lender"), "IN", "INR", [])
    s.metrics[("market_cap", "none", "market", T)] = (Decimal(10_000), None, {"price": {"trade_date": T.isoformat(),
                                                                                         "close": "1000"}})
    s.metrics[("pb", pb_basis, "market", T)] = (Decimal(2), None, {})
    s.metrics[("beta_bottom_up", "none", "market", T)] = (Decimal("0.9"), None, {})
    for end, v in values.items():
        s.metrics[("roe", basis, "year", end)] = (None, v[1:], {}) if v.startswith("!") else (Decimal(v), None, {})
    s.breaks = list(breaks)
    return s


def test_delivered_roe_averages_up_to_five_consecutive_years_ending_with_the_latest():
    s = with_roe({e: v for e, v in zip(FY, ("0.10", "0.12", "0.14", "0.16", "0.18"), strict=True)})
    got = assemble.delivered_roe(s, T)
    assert got.value == Decimal("0.14") and got.as_of == D(2026, 3, 31)
    assert got.detail["n"] == 5 and [y["period_end"] for y in got.detail["years"]] == [e.isoformat() for e in FY]
    older = with_roe({D(2021, 3, 31): "0.50", **{e: "0.10" for e in FY}})
    assert assemble.delivered_roe(older, T).value == Decimal("0.10")          # at most five


def test_delivered_roe_with_three_years_is_shown_with_n_and_below_three_is_held():
    three = assemble.delivered_roe(with_roe({FY[2]: "0.12", FY[3]: "0.15", FY[4]: "0.18"}), T)
    assert three.value == Decimal("0.15") and three.detail["n"] == 3
    banks = with_roe({FY[0]: "!The balance sheet at the start of the year isn't filed on the same basis.",
                      FY[3]: "0.16", FY[4]: "0.14"})                          # banks today: FY2025 and FY2026
    got = assemble.delivered_roe(banks, T)
    assert got.held == ("Fewer than three consecutive financial years of return on equity are filed, so its average "
                        "isn't shown.")
    assert got.detail["n"] == 2 and got.as_of == D(2026, 3, 31)
    two = assemble.delivered_roe(banks, T, assemble.DeliveredRoe(5, 2))       # the alternative, one line in the yaml
    assert two.value == Decimal("0.15")


def test_a_held_year_ends_the_run_of_consecutive_years():
    s = with_roe({FY[0]: "0.20", FY[1]: "0.20", FY[2]: "!The filing doesn't report equity attributable to owners.",
                  FY[3]: "0.10", FY[4]: "0.12"})
    got = assemble.delivered_roe(s, T)
    assert got.held and got.detail["n"] == 2                                  # FY2022 and FY2023 aren't reached
    latest_held = with_roe({**{e: "0.10" for e in FY[:4]}, FY[4]: "!The filing doesn't report profit attributable "
                                                                   "to owners."})
    assert assemble.delivered_roe(latest_held, T).detail["n"] == 0


def test_delivered_roe_is_held_across_a_merger_inside_the_years_averaged():
    """HDFC Bank's merger with HDFC Ltd (effective 1 Jul 2023) inside five filed years holds the average back."""
    merger = mx.Break("merger", D(2023, 7, 1), "curated", curated=True)
    s = with_roe({e: "0.15" for e in FY}, breaks=[merger])
    got = assemble.delivered_roe(s, T)
    assert got.held == "The window spans a merger, so its years aren't comparable."
    assert got.detail["structural_breaks"][0]["effective"] == "2023-07-01"
    after = with_roe({e: "0.15" for e in FY[3:]} | {FY[2]: "!x"}, breaks=[merger])
    assert assemble.delivered_roe(after, T, assemble.DeliveredRoe(5, 2)).value == Decimal("0.15")   # all after it
    first_day = with_roe({e: "0.15" for e in FY[2:]}, breaks=[mx.Break("demerger", D(2023, 4, 1), "x")])
    assert assemble.delivered_roe(first_day, T).held                          # opening equity is before it


def test_delivered_roe_reads_the_basis_of_the_p_b_and_says_when_nothing_is_stored():
    s = with_roe({e: "0.15" for e in FY}, basis="standalone", pb_basis="consolidated")
    assert assemble.delivered_roe(s, T).held == "No financial year's return on equity is stored."
    s2 = with_roe({e: "0.15" for e in FY}, basis="standalone", pb_basis="standalone")
    assert assemble.delivered_roe(s2, T).value == Decimal("0.15")


def test_the_delivered_roe_window_needs_its_source(tmp_path):
    rule = assemble.load_delivered_roe()
    assert (rule.years, rule.min_years) == (5, 3)
    p = tmp_path / "valuation.yaml"
    p.write_text("delivered_roe: {years: 5, min_years: 3}\n", "utf-8")
    with pytest.raises(ValueError):
        assemble.load_delivered_roe(p)
    p.write_text("delivered_roe: {years: 2, min_years: 3, source: x}\n", "utf-8")
    with pytest.raises(ValueError):
        assemble.load_delivered_roe(p)


def test_lender_inputs_and_their_implied_roe():
    s = with_roe({e: "0.15" for e in FY})
    lend = assemble.lender_inputs(s, T)
    assert (lend.pb.value, lend.beta.value, lend.delivered_roe.value, lend.price.value) == \
        (Decimal(2), Decimal("0.9"), Decimal("0.15"), Decimal(1000))
    assert lend.pb.unit == "multiple" and lend.price.as_of == T
    run = implied_roe(lend, india())
    ke = Decimal("0.0531") + Decimal("0.9") * Decimal("0.0692")
    assert run.result == (Decimal("0.0531") + 2 * (ke - Decimal("0.0531"))).quantize(TOL)
    assert run.gap == run.result - Decimal("0.15")


# ---------------------------------------------------------------------------------------------- from the filings
@needs_fixtures
@pytest.mark.parametrize("symbol", ["INFY", "HINDUNILVR", "ITC"])
def test_a_company_s_reverse_dcf_reads_its_5_year_cagr_and_its_close(symbol):
    s = stored(symbol)
    c = assemble.company_inputs(s, T)
    fy_end = c.revenue_3fy.as_of
    want = computed(symbol)
    grown = assemble.delivered_growth(s, T)
    five = want[("revenue_cagr_5y", "consolidated", "year", fy_end)]
    assert (grown.value, grown.held) == (five.value, five.held) and grown.detail["years"] == 5
    close = assemble.price(s, T)
    assert (close.value, close.as_of) == (Decimal(1000), T)
    run = reverse_dcf(c, india(), assemble.load_settings(), close, grown)
    assert run.outcome in ("solved", "below_range", "above_range", "growth_lowers_value"), run.reason
    assert json.loads(json.dumps(run.inputs))["outcome"] == run.outcome
    if run.status == "solved":
        d, _, _ = prepare(c, india(), SETTINGS)
        assert abs(per_share(d, run.result) - Decimal(1000)) / 1000 < Decimal("0.001")


@needs_fixtures
def test_a_bank_from_its_filings_gets_implied_roe_and_its_delivered_roe_is_held_for_want_of_years():
    s = stored("SBIN", family="lender")
    lend = assemble.lender_inputs(s, T)
    assert lend.pb.value is not None and lend.pb.value > 0, lend.pb.held
    run = implied_roe(lend, india())
    assert run.status == "solved" and Decimal("0.05") < run.result < Decimal("0.40")
    assert lend.delivered_roe.detail["n"] < 3 and run.delivered_reason.startswith("Fewer than three")
    assert reverse_dcf(assemble.company_inputs(s, T), india(), SETTINGS, lend.price,
                       DELIVERED).reason == FAMILY_NA["lender"]


@needs_fixtures
def test_hdfc_bank_s_merger_from_the_curated_file_is_outside_its_filed_years_today():
    s = stored("HDFCBANK", family="lender")
    s.breaks = mx.load_breaks().get("hdfcbank", [])
    assert s.breaks and s.breaks[0].effective == D(2023, 7, 1)
    got = assemble.delivered_roe(s, T, assemble.DeliveredRoe(5, 1))
    years = [y["period_end"] for y in got.detail["years"]]
    assert years and min(years) >= "2024-03-31"                    # balance sheets from March 2024 (#114)
    assert got.value is not None                                   # every averaged year is after the merger


# ---------------------------------------------------------------------------------------------- the sentences
def test_every_sentence_2a_14_writes_passes_the_banned_words_scan():
    sentences = [R.growth_lowers_value(Decimal("0.06"), Decimal("0.12")),
                 R.return_below_cost(Decimal("0.11"), Decimal("0.12")),
                 R.tax_fade(Decimal("0.1"), Decimal("0.25")), R.BELOW_RANGE, R.ABOVE_RANGE, OPERATING_NA,
                 KE_NOT_ABOVE_GROWTH, "Price-to-book isn't positive.", "The latest close isn't positive.",
                 "The latest close isn't stored.", "No financial year's return on equity is stored.",
                 assemble.delivered_roe(with_roe({FY[4]: "0.1"}), T).held,
                 assemble.delivered_roe(with_roe({e: "0.1" for e in FY},
                                                 breaks=[mx.Break("merger", D(2024, 1, 1), "x")]), T).held,
                 *FAMILY_NA.values()]
    assert all(s and s.endswith(".") for s in sentences)
    found = {s: banned_words_in(s) for s in sentences if banned_words_in(s)}
    assert not found, found
    assert set(R.NO_SOLUTION) == {"growth_lowers_value", "return_below_cost", "tax_fade", "below_range",
                                  "above_range"}


def test_the_lender_inputs_need_their_family_and_a_known_unit():
    with pytest.raises(ValueError):
        lender(family="bank")
    with pytest.raises(ValueError):
        Input.of(1, "times", None, "t")
    assert replace(lender(), family="insurer").family == "insurer"
