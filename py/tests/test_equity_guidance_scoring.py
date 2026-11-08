"""Guidance scoring's rules (job 2B-14, #177; Spec 02B §5.10, Spec 02B.1 C5): the tolerances at their boundaries, the
favourable sides, every not-scored reason this job decides, the half-period rule, revisions, multi-quarter sums, a CAGR, a
structural break, the definition check both ways, SBI's filed unit, and an outcome recomputed from its inputs alone. Pure:
the task's database tests are in test_equity_guidance_scores_task.py."""
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_equity_statements import by_kind, needs_fixtures, parse

from ft.equity import metrics as mx
from ft.equity.calls import scoring as sc

D = date
CR = Decimal(10_000_000)
M = sc.load_map()
AS_OF = D(2026, 11, 5)
FY26, MAY_2025 = (D(2025, 4, 1), D(2026, 3, 31)), D(2025, 5, 10)


def dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def fin(doc: int, kind: str, end: date, *, basis="consolidated", fmt="ind_as", start=None, **crore) -> mx.Fin:
    """A financials row; ₹ figures given in crore (stored in rupees, as the parser stores them); ratios as given."""
    vals = {k: (Decimal(str(v)) if k.endswith("_ratio") else Decimal(str(v)) * CR) for k, v in crore.items()}
    return mx.Fin(doc, basis, kind, start, end, vals, {k: k for k in vals}, fmt)


def pl(doc: int, kind: str, end: date, rev, ebit, dna=0, *, tax=None, pbt=None, pat=None, owners=None, **kw) -> mx.Fin:
    """A P&L row whose derived EBIT is `ebit` (no finance cost, other income or associates)."""
    v = {"revenue": rev, "pbt_before_exceptional": ebit, "finance_cost": 0, "other_income": 0, "dna": dna}
    v |= {k: x for k, x in (("tax", tax), ("pbt", pbt), ("pat", pat), ("pat_owners", owners)) if x is not None}
    return fin(doc, kind, end, **v, **kw)


def company(fins=(), *, formats=("ind_as",), family="operating", breaks=(), stated=()) -> sc.Company:
    return sc.Company(1, "acme", family, 3, frozenset(formats), list(fins), list(breaks), list(stated))


def stmt(i=1, metric="ebitda_margin", kind="range", *, value=None, low=None, high=None, unit="pct", direction=None,
         comparison=None, period_kind="year", start=FY26[0], end=FY26[1], basis=None, growth_basis=None,
         scope=None, vintage=MAY_2025, reason=None, text="EBITDA margin", quote=None, call=10) -> sc.Statement:
    return sc.Statement(i, 1, call, metric, kind, dec(value), dec(low), dec(high), unit, direction, comparison,
                        period_kind, start, end, basis, growth_basis, scope, vintage, reason, text, None, quote)


def one(c: sc.Company, s: sc.Statement, as_of: date = AS_OF) -> sc.Outcome:
    [o] = sc.score_company(c, [s], M, as_of)
    return o


def j(kind, unit, fav, actual, **t):
    v = sc.judge(kind, unit, fav, actual=Decimal(actual), **{k: dec(x) for k, x in t.items() if k != "direction"},
                 direction=t.get("direction"))
    return v.outcome, v.relation


# ---------------------------------------------------------------------------------------------- tolerances
def test_rounding_is_half_away_from_zero_both_ways():
    assert sc.rnd(Decimal("0.05"), 1) == Decimal("0.1") and sc.rnd(Decimal("-0.05"), 1) == Decimal("-0.1")
    assert sc.rnd(Decimal("2.5"), 0) == 3 and sc.rnd(Decimal("-2.5"), 0) == -3


@pytest.mark.parametrize("actual, fav, want", [
    ("12.49", "higher", ("delivered", "within")), ("12.5", "higher", ("delivered", "above")),
    ("12.5", "none", ("missed", "above")), ("9.5", "higher", ("delivered", "within")),
    ("9.49", "higher", ("missed", "below")), ("9.49", "lower", ("delivered", "below")),
    ("12.5", "lower", ("missed", "above"))])
def test_a_range_rounds_the_actual_to_its_decimals_and_delivers_past_the_favourable_side(actual, fav, want):
    assert j("range", "pct", fav, actual, low=10, high=12) == want


def test_a_range_stated_to_one_decimal_rounds_to_one_decimal():
    assert j("range", "pct", "none", "11.04", low="10.5", high="11.0") == ("delivered", "within")
    assert j("range", "pct", "none", "11.05", low="10.5", high="11.0") == ("missed", "above")


@pytest.mark.parametrize("actual, fav, want", [
    ("25.54", "higher", ("delivered", "within")), ("25.55", "higher", ("delivered", "above")),
    ("25.55", "none", ("missed", "above")), ("24.45", "higher", ("delivered", "within")),
    ("24.44", "higher", ("missed", "below")), ("24.44", "lower", ("delivered", "below"))])
def test_a_percentage_point_is_within_half_a_point_after_rounding_to_one_decimal(actual, fav, want):
    assert j("point", "pct", fav, actual, value=25) == want


def test_a_small_target_s_tolerance_is_ten_per_cent_and_never_below_a_tenth_of_a_point():
    assert sc.tolerance("pct", Decimal(25)) == Decimal("0.5")
    assert sc.tolerance("pct", Decimal(3)) == Decimal("0.3")
    assert sc.tolerance("pct", Decimal("0.8")) == Decimal("0.1")          # 10% would be 0.08: the floor holds
    assert sc.tolerance("inr", Decimal(5000)) == Decimal(250)
    assert j("point", "pct", "none", "3.34", value=3) == ("delivered", "within")
    assert j("point", "pct", "none", "3.35", value=3) == ("missed", "above")
    assert j("point", "pct", "lower", "0.94", value="0.8") == ("delivered", "within")   # 0.9: 0.1 above, at the floor
    assert j("point", "pct", "lower", "0.95", value="0.8") == ("missed", "above")       # 1.0: 0.2 above


def test_the_tenth_of_a_point_floor_at_its_boundary():
    # target 1.0 (tolerance 0.1): 1.14 rounds to 1.1 (within), 1.15 rounds half away to 1.2 (beyond)
    assert j("point", "pct", "lower", "1.14", value="1.0") == ("delivered", "within")
    assert j("point", "pct", "lower", "1.15", value="1.0") == ("missed", "above")
    assert j("point", "pct", "lower", "0.85", value="1.0") == ("delivered", "within")
    assert j("point", "pct", "lower", "0.84", value="1.0") == ("delivered", "below")   # past it on the favourable side
    assert j("point", "pct", "higher", "0.84", value="1.0") == ("missed", "below")


@pytest.mark.parametrize("actual, want", [
    ("5250.4", ("delivered", "within")), ("5250.5", ("delivered", "above")), ("4749.5", ("delivered", "within")),
    ("4749.4", ("missed", "below"))])
def test_a_rupee_point_is_within_five_per_cent_at_the_nearest_crore(actual, want):
    assert j("point", "inr", "higher", actual, value=5000) == want


def test_floors_and_ceilings_at_one_decimal_or_the_nearest_crore():
    assert j("floor", "pct", "higher", "9.95", value=10) == ("delivered", "at_or_past")
    assert j("floor", "pct", "higher", "9.94", value=10) == ("missed", "short_of")
    assert j("ceiling", "inr", "lower", "0.49", value=0) == ("delivered", "at_or_past")       # "net debt free"
    assert j("ceiling", "inr", "lower", "0.5", value=0) == ("missed", "short_of")
    assert j("ceiling", "inr", "lower", "-0.5", value=0) == ("delivered", "at_or_past")


def test_directions_compare_both_figures_at_one_decimal():
    assert j("direction", "pct", "higher", "20.04", base="19.96", direction="increase") == ("missed", "moved_against")
    assert j("direction", "pct", "higher", "20.05", base="19.96", direction="increase") == ("delivered", "moved_as_said")
    assert j("direction", "pct", "lower", "19.94", base="20.0", direction="decrease") == ("delivered", "moved_as_said")
    assert j("direction", "pct", "none", "20.5", base="20.0", direction="maintain") == ("delivered", "moved_as_said")
    assert j("direction", "pct", "none", "20.55", base="20.0", direction="maintain") == ("missed", "moved_against")


# ---------------------------------------------------------------------------------------------- actuals
Q1, Q2, Q3, Q4 = D(2025, 6, 30), D(2025, 9, 30), D(2025, 12, 31), D(2026, 3, 31)


def test_a_half_year_is_the_sum_of_its_filed_quarters_not_the_year_to_date_column():
    c = company([pl(1, "quarter", Q1, 1000, 150, 50), pl(2, "quarter", Q2, 1000, 170, 50),
                 pl(2, "half_year", Q2, 2000, 100, 0, start=D(2025, 4, 1))], stated=[stated(1, "20.0")])
    o = one(c, stmt(kind="range", low=20, high=22, period_kind="half_year", end=Q2))
    assert (o.outcome, o.relation, o.actual) == ("delivered", "within", Decimal("21.000000"))   # 420 / 2000
    assert {x["document_id"] for x in o.inputs["actual"]["lines"]} == {1, 2}
    assert {x["period_kind"] for x in o.inputs["actual"]["lines"]} == {"quarter"}


def test_nine_months_sum_three_quarters_and_a_missing_quarter_leaves_it_unscored():
    c = company([pl(1, "quarter", Q1, 1000, 100), pl(2, "quarter", Q2, 1000, 100), pl(3, "quarter", Q3, 1100, 100)])
    o = one(c, stmt(metric="revenue", kind="point", value=3100, unit="inr", period_kind="nine_months", end=Q3))
    assert (o.outcome, o.relation, o.actual) == ("delivered", "within", Decimal("3100.000000"))
    gap = company([pl(1, "quarter", Q1, 1000, 100), pl(3, "quarter", Q3, 1100, 100)])
    o = one(gap, stmt(metric="revenue", kind="point", value=3100, unit="inr", period_kind="nine_months", end=Q3))
    assert (o.outcome, o.reason) == ("not_scored", "filed_figures_unavailable")      # 90 days after Dec 2025 passed


def test_a_year_is_the_year_s_row_and_growth_compares_the_year_before_on_the_same_basis():
    c = company([pl(1, "year", D(2025, 3, 31), 4000, 400, start=D(2024, 4, 1)),
                 pl(2, "year", Q4, 4600, 500, start=D(2025, 4, 1))])
    o = one(c, stmt(metric="revenue_growth", kind="range", low=14, high=16, growth_basis="yoy"))
    assert (o.outcome, o.relation, o.actual) == ("delivered", "within", Decimal("15.000000"))
    assert set(o.inputs["actual"]["operands"]) == {"X", "P"}


def test_a_cagr_runs_from_the_year_before_the_first_year():
    c = company([pl(1, "year", D(2025, 3, 31), 1000, 100), pl(2, "year", D(2028, 3, 31), 1331, 100)])
    o = one(c, stmt(metric="revenue_growth", kind="point", value=10, period_kind="multi_year", start=D(2025, 4, 1),
                    end=D(2028, 3, 31), growth_basis="cagr", vintage=D(2025, 5, 10)), as_of=D(2028, 9, 1))
    assert (o.outcome, o.actual, o.inputs["actual"]["n"]) == ("delivered", Decimal("10.000000"), 3)
    yoy = one(c, stmt(metric="revenue_growth", kind="point", value=10, period_kind="multi_year", start=D(2025, 4, 1),
                      end=D(2028, 3, 31), growth_basis="yoy"), as_of=D(2028, 9, 1))
    assert (yoy.outcome, yoy.reason) == ("not_scored", "no_filed_figure")


def test_the_second_half_s_capex_is_the_year_less_the_first_half():
    c = company([fin(1, "half_year", Q2, capex=400, start=D(2025, 4, 1)), fin(2, "year", Q4, capex=1000,
                                                                              start=D(2025, 4, 1))])
    o = one(c, stmt(metric="capex", kind="point", value=600, unit="inr", period_kind="half_year", start=D(2025, 10, 1),
                    end=Q4))
    assert (o.outcome, o.actual) == ("delivered", Decimal("600.000000"))
    assert [t["sign"] for t in o.inputs["actual"]["operands"]["X"]] == [1, -1]


def test_net_debt_is_read_at_a_half_year_end_and_a_quarter_end_target_has_no_filed_figure():
    bs = fin(1, "instant", Q2, total_assets=5000, borrowings_current=300, borrowings_noncurrent=200, cash=150,
             current_investments=50)
    c = company([bs])
    o = one(c, stmt(metric="net_debt", kind="ceiling", value=350, unit="inr", period_kind="half_year", end=Q2))
    assert "definition" in o.inputs and o.reason == "definition"      # no stated figure to check the definition against
    o = sc.score_company(c, [stmt(metric="net_debt", kind="ceiling", value=350, unit="inr", period_kind="quarter",
                                  start=D(2025, 4, 1), end=Q1)], M, AS_OF)[0]
    assert (o.outcome, o.reason) == ("not_scored", "no_filed_figure")


def test_profit_after_tax_uses_profit_for_the_period_when_no_non_controlling_interest_is_filed():
    c = company([pl(1, "quarter", Q1, 1000, 100, pat=80)])
    o = one(c, stmt(metric="pat", kind="floor", value=80, unit="inr", period_kind="quarter", end=Q1))
    assert (o.outcome, o.actual) == ("delivered", Decimal("80.000000"))
    assert o.inputs["actual"]["lines"][0]["line"] == "pat"


# ---------------------------------------------------------------------------------------------- banks and units
def bank(**ratios) -> sc.Company:
    return company([fin(1, "quarter", Q4, basis="standalone", fmt="bank", revenue=100, **ratios)], formats=("bank",),
                   family="lender")


def test_a_bank_s_filed_fraction_is_scored_in_per_cent():
    o = one(bank(gnpa_ratio="0.0147"), stmt(metric="gnpa", kind="ceiling", value="1.5", period_kind="year"))
    assert (o.outcome, o.relation, o.actual) == ("delivered", "at_or_past", Decimal("1.470000"))


def test_a_fraction_filed_outside_zero_to_one_is_held_never_rescaled():
    o = one(bank(gnpa_ratio="1.47"), stmt(metric="gnpa", kind="ceiling", value="1.5", period_kind="year"))
    assert (o.outcome, o.reason, o.actual) == ("not_scored", "filed_figures_unavailable", None)
    assert "outside 0 to 1" in o.unit_held


def test_a_bank_line_for_a_company_not_in_the_bank_format_has_no_filed_figure():
    c = company([fin(1, "quarter", Q4, revenue=100)], formats=("nbfc",), family="lender")
    o = one(c, stmt(metric="advances_growth", kind="floor", value=15, period_kind="year"))
    assert (o.outcome, o.reason) == ("not_scored", "no_filed_figure")


def test_advances_at_a_quarter_end_without_a_balance_sheet_wait_then_become_unavailable():
    c = company([fin(1, "instant", D(2026, 6, 30), basis="standalone", fmt="bank", segment_assets_total=9000)],
                formats=("bank",), family="lender")
    s = stmt(metric="advances_growth", kind="floor", value=15, period_kind="quarter", start=D(2026, 4, 1),
             end=D(2026, 6, 30), vintage=D(2026, 4, 20))
    assert one(c, s, as_of=D(2026, 9, 1)).outcome == "pending"
    o = one(c, s, as_of=D(2026, 10, 1))
    assert (o.outcome, o.reason, o.inputs["deadline"]) == ("not_scored", "filed_figures_unavailable", "2026-09-28")


@needs_fixtures
def test_sbi_s_gross_npa_ratio_is_filed_as_a_fraction_as_the_map_records():
    """§5.10: the map records each column's unit, and a fixture test pins SBI's."""
    assert M.columns["gnpa_ratio"] == "fraction" and M.lines["gnpa"].formula == "fraction"
    q = by_kind(parse("SBIN", "INTEGRATED_FILING_BANKING_1709644_07082026025925_WEB.xml"))["quarter"]
    f = mx.Fin(1, "standalone", "quarter", None, q.period_end, q.values, q.tags, "bank")
    c = company([f], formats=("bank",), family="lender")
    o = one(c, stmt(metric="gnpa", kind="ceiling", value="1.5", period_kind="quarter",
                    start=mx.month_end(q.period_end, -3) + timedelta(days=1), end=q.period_end,
                    vintage=mx.month_end(q.period_end, -3)), as_of=D(2026, 11, 5))
    assert o.actual == Decimal("1.470000") and o.outcome == "delivered"


# ---------------------------------------------------------------------------------------------- not scored
def test_the_extraction_s_reasons_are_kept_and_nothing_is_measured():
    o = one(company(), stmt(scope="the cement business", reason="scope"))
    assert (o.outcome, o.reason, o.forecast_ok) == ("not_scored", "scope", False) and "actual" not in o.inputs


@pytest.mark.parametrize("s", [
    stmt(metric="tax_rate", kind="range", low=24, high=26, period_kind="nine_months", end=Q3),
    stmt(metric="revenue_growth", kind="floor", value=10, growth_basis="qoq", period_kind="quarter", end=Q1),
    stmt(metric="ebit_margin", kind="floor", value=10)])
def test_a_statement_the_map_has_no_line_for_isn_t_scored(s):
    c = company(family="lender") if s.metric == "ebit_margin" else company()
    o = one(c, s)
    assert (o.outcome, o.reason, o.forecast_ok) == ("not_scored", "no_filed_figure", False)


def test_improve_goes_through_the_favourable_side_and_is_vague_without_one():
    c = company([fin(1, "quarter", Q4, basis="standalone", fmt="bank", gnpa_ratio="0.02"),
                 fin(2, "quarter", D(2026, 6, 30), basis="standalone", fmt="bank", gnpa_ratio="0.018")],
                formats=("bank",))
    s = stmt(metric="gnpa", kind="direction", direction="increase", unit=None, period_kind="quarter",
             start=D(2026, 4, 1), end=D(2026, 6, 30), vintage=D(2026, 4, 20), quote="we expect asset quality to improve")
    o = one(c, s)
    assert (o.outcome, o.relation, o.inputs["statement"]["effective_direction"]) == ("delivered", "moved_as_said",
                                                                                      "decrease")
    capex = one(company(), stmt(metric="capex", kind="direction", direction="increase", unit=None,
                                quote="capex efficiency will improve"))
    assert (capex.outcome, capex.reason) == ("not_scored", "vague_figure")


@pytest.mark.parametrize("field", ["quote", "metric_text", "scope_text", "period_text"])
@pytest.mark.parametrize("phrase", ["2% to 5% in constant currency", "growth of 2 to 5 percent in Constant-Currency terms",
                                    "2-5% CC growth"])
def test_constant_currency_growth_has_no_filed_figure_wherever_the_statement_says_it(field, phrase):
    """Found by #178's sitting-2 scoring gate: HCL's and Infosys's "constant currency" targets were scored as rupee growth."""
    rows = [pl(1, "year", D(2025, 3, 31), 4000, 400), pl(2, "year", Q4, 4600, 500)]
    plain = stmt(metric="revenue_growth", kind="range", low=2, high=5, quote="revenue growth of 2 to 5%")
    assert (one(company(rows), plain).outcome, one(company(rows), plain).reason) == ("delivered", None)   # without the phrase
    o = one(company(rows), replace(plain, **{field: phrase}))
    assert (o.outcome, o.reason, o.counted, o.forecast_ok) == ("not_scored", "no_filed_figure", False, False)


def test_words_that_only_contain_cc_or_currency_are_still_scored():
    rows = [pl(1, "year", D(2025, 3, 31), 4000, 400), pl(2, "year", Q4, 4600, 500)]
    for quote in ("we will succeed in growing 10% in a currency-stable year", "accounts receivable growth steady; revenue +10%"):
        o = one(company(rows), stmt(metric="revenue_growth", kind="floor", value=10, quote=quote))
        assert (o.outcome, o.reason) == ("delivered", None), quote


def test_a_merger_inside_the_span_leaves_the_target_unscored_but_one_on_the_first_day_doesn_t():
    rows = [pl(1, "year", D(2025, 3, 31), 4000, 400), pl(2, "year", Q4, 4600, 500)]
    merged = company(rows, breaks=[mx.Break("merger", D(2025, 10, 1), "a scheme", curated=True)])
    o = one(merged, stmt(metric="revenue_growth", kind="floor", value=10))
    assert (o.outcome, o.reason, o.inputs["structural_breaks"][0]["effective"]) == ("not_scored", "structural_break",
                                                                                     "2025-10-01")
    first_day = company(rows, breaks=[mx.Break("merger", D(2025, 4, 1), "a scheme")])
    assert one(first_day, stmt(metric="revenue", kind="floor", value=4000, unit="inr")).outcome == "delivered"
    assert one(first_day, stmt(metric="revenue_growth", kind="floor", value=10)).reason == "structural_break"


def stated(i, value, end=Q1, metric="ebitda_margin"):
    return sc.Stated(i, 100 + i, metric, Decimal(str(value)), "pct", "quarter", mx.month_end(end, -3) + timedelta(days=1),
                     end, None, None)


def test_the_definition_check_passes_within_half_a_point_and_fails_beyond_it():
    rows = [pl(1, "quarter", Q1, 1000, 150, 50), pl(2, "quarter", Q2, 1000, 150, 50), pl(3, "year", Q4, 4000, 600, 200)]
    s = stmt(kind="range", low=19, high=21)
    ok = one(company(rows, stated=[stated(1, "20.3"), stated(2, "19.8", Q2)]), s)     # ours 20.0 both quarters
    assert (ok.outcome, Decimal(ok.inputs["definition"]["median"]), ok.inputs["definition"]["n"]) == (
        "delivered", Decimal("0.25"), 2)
    bad = one(company(rows, stated=[stated(1, "21.6"), stated(2, "21.2", Q2), stated(3, "20.1", Q2)]), s)
    assert (bad.outcome, bad.reason, Decimal(bad.definition["median"])) == ("not_scored", "definition", Decimal("1.2"))
    none = one(company(rows), s)
    assert (none.reason, none.definition["n"], none.definition["passed"]) == ("definition", 0, False)


def test_a_period_not_yet_filed_is_pending_until_ninety_days_then_unavailable():
    s = stmt(metric="revenue", kind="point", value=1000, unit="inr", period_kind="quarter", start=D(2026, 7, 1),
             end=D(2026, 9, 30), vintage=D(2026, 7, 20))
    o = one(company(), s)
    assert (o.outcome, o.counted, o.inputs["deadline"]) == ("pending", True, "2026-12-29")
    assert one(company(), s, as_of=D(2026, 12, 30)).reason == "filed_figures_unavailable"
    year = stmt(metric="revenue", kind="point", value=1000, unit="inr", start=D(2025, 4, 1), end=Q4)
    assert one(company(), year, as_of=D(2026, 7, 29)).outcome == "pending"           # 120 days after a year end
    assert one(company(), year, as_of=D(2026, 7, 30)).outcome == "not_scored"


# ---------------------------------------------------------------------------------------------- counting
def test_the_half_period_rule_counts_fy26_targets_from_apr_may_2025_and_not_fy25_targets_from_jan_2025():
    c = company([pl(1, "year", D(2025, 3, 31), 4000, 400), pl(2, "year", Q4, 4600, 500)])
    fy26 = one(c, stmt(metric="revenue", kind="floor", value=4000, unit="inr", vintage=D(2025, 5, 15)))
    assert (fy26.horizon_share, fy26.counted) == (Decimal("0.1205"), True)
    fy25 = one(c, stmt(metric="revenue", kind="floor", value=3000, unit="inr", start=D(2024, 4, 1), end=D(2025, 3, 31),
                       vintage=D(2025, 1, 20)))
    assert (fy25.outcome, fy25.horizon_share, fy25.counted) == ("delivered", Decimal("0.8055"), False)
    early = one(c, stmt(metric="revenue", kind="floor", value=4000, unit="inr", vintage=D(2025, 2, 1)))
    assert early.horizon_share < 0 and early.counted                                  # given before the period began


def test_later_statements_are_raised_cut_reiterated_or_restated_and_judged_on_their_own_figures():
    c = company([pl(1, "year", Q4, 1000, 205, tax="20.5", pbt=100)])                 # a 20.5% tax rate
    ss = [stmt(1, "tax_rate", "point", value=20, vintage=D(2025, 5, 10), call=1),
          stmt(2, "tax_rate", "point", value=21, vintage=D(2025, 8, 10), call=2),
          stmt(3, "tax_rate", "point", value="21.4", vintage=D(2025, 11, 10), call=3),
          stmt(4, "tax_rate", "range", low=20, high=21, vintage=D(2026, 1, 20), call=4),
          stmt(5, "tax_rate", "point", value=20, basis="standalone", vintage=D(2025, 9, 1), call=2)]
    out = {o.guidance_id: o for o in sc.score_company(c, ss, M, AS_OF)}
    assert [(out[i].is_first, out[i].revision) for i in (1, 2, 3, 4)] == [
        (True, "first"), (False, "raised"), (False, "reiterated"), (False, "restated")]
    assert out[5].is_first and out[5].target_key != out[1].target_key                # another basis: another target
    assert [out[i].outcome for i in (1, 2, 3, 4)] == ["delivered", "delivered", "missed", "delivered"]
    assert [out[i].counted for i in (1, 2, 3, 4)] == [True, False, False, False]
    assert out[2].inputs["previous_statement"] == 1
    cut = sc.score_company(c, [stmt(1, "tax_rate", "point", value=21), stmt(2, "tax_rate", "point", value=20,
                                                                            vintage=D(2025, 8, 1))], M, AS_OF)
    assert [o.revision for o in cut] == ["first", "cut"]


# ---------------------------------------------------------------------------------------------- inputs
def test_an_outcome_is_recomputed_from_its_inputs_alone():
    c = company([pl(1, "quarter", Q1, 1000, 150, 50), pl(2, "quarter", Q2, 1000, 170, 50),
                 pl(3, "quarter", D(2025, 3, 31), 900, 150, 40)], stated=[stated(1, "20.1")])
    for s in (stmt(kind="range", low=20, high=22, period_kind="half_year", end=Q2),
              stmt(kind="direction", direction="increase", unit=None, period_kind="quarter", end=Q2,
                   start=D(2025, 7, 1), vintage=D(2025, 7, 20))):
        o = one(c, s)
        stored = json.loads(json.dumps(o.inputs))
        assert o.outcome == "delivered"
        assert sc.recompute(stored) == (o.outcome, o.relation, o.actual, o.base)
        broken = json.loads(json.dumps(o.inputs))
        broken["actual"]["lines"][0]["value"] = str(Decimal(broken["actual"]["lines"][0]["value"]) * 2)
        assert sc.recompute(broken) != (o.outcome, o.relation, o.actual, o.base)


def test_the_map_loads_and_a_malformed_line_fails(tmp_path):
    assert set(M.lines) == {k for k in sc.yaml.safe_load(sc.MAP_PATH.read_text())["metrics"] if k != "other"}
    raw = sc.MAP_PATH.read_text().replace("favourable: lower, label: derived}", "favourable: down, label: derived}", 1)
    (tmp_path / "m.yaml").write_text(raw)
    with pytest.raises(ValueError, match="net_debt: bad favourable"):
        sc.load_map(tmp_path / "m.yaml")


def test_forecast_labels_follow_the_rbi_projections_shape():
    assert sc.target_label("quarter", D(2026, 7, 1), D(2026, 9, 30), 3) == "Q2 2026-27"
    assert sc.target_label("half_year", D(2026, 10, 1), D(2027, 3, 31), 3) == "H2 2026-27"
    assert sc.target_label("year", D(2025, 4, 1), D(2026, 3, 31), 3) == "FY 2025-26"
    assert sc.target_label("year", D(2026, 1, 1), D(2026, 12, 31), 12) == "FY 2026"
    assert sc.target_label("multi_year", D(2025, 4, 1), D(2028, 3, 31), 3) == "FY 2025-26 to FY 2027-28"
