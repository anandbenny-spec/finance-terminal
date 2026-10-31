"""equity.metrics (job 2A-11): derived metrics from the saved filings (parsed by 2A-09's parser), the bank ROA scale,
TTM against the year, structural breaks, the market figures, the lease rule, the checks, and the task through the
runner.

The filings are in data/fixtures/equity (the private fixtures repo; the tests skip without it, and CI checks it out).
No NSE data is kept in this repo (CLAUDE.md §4)."""
import functools
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import yaml
from test_equity_statements import XBRL, filing_of, needs_fixtures, parse, raw

from ft.equity import metrics as mx
from ft.equity import statements as st
from ft.jobs import config
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_metrics import EquityMetrics

D = date
RULES = mx.Rules.load()
FAMILY = {"SBIN": "lender", "HDFCBANK": "lender", "FEDERALBNK": "lender", "KOTAKBANK": "lender",
          "ICICIBANK": "lender", "BAJFINANCE": "lender", "SBILIFE": "insurer"}


@functools.cache
def fins(symbol: str) -> tuple[mx.Fin, ...]:
    """Every saved filing of the company as the task reads it: one Fin per row (document ids in file order)."""
    out = []
    for i, p in enumerate(sorted((XBRL / symbol).iterdir())):
        name = p.name[:-3]
        try:
            parsed, f = parse(symbol, name), filing_of(symbol, name)
        except (st.StatementError, KeyError):         # files the parser refuses, or without a header period
            continue
        for r in parsed.rows:
            out.append(mx.Fin(i + 1, f.basis, r.period_kind, r.period_start, r.period_end, r.values, r.tags,
                              f.format, datetime(2000, 1, 1, tzinfo=UTC)))
    return tuple(out)


def company(symbol: str, family: str | None = None) -> mx.Company:
    return mx.Company(1, symbol.lower(), family or FAMILY.get(symbol, "operating"))


def values(symbol: str, *, rows=None, family=None, **data) -> dict[tuple, mx.Value]:
    d = mx.Data(company(symbol, family), list(rows if rows is not None else fins(symbol)), **data)
    return {(v.key, v.basis, v.period_kind, v.period_end): v for v in mx.compute(d, RULES)}


def one(vals: dict, key: str, kind: str, end: date) -> mx.Value:
    got = [v for (k, _, pk, e), v in vals.items() if k == key and pk == kind and e == end]
    assert len(got) == 1, (key, kind, end, got)
    return got[0]


# ---------------------------------------------------------------------------------------------- banks
@needs_fixtures
def test_sbi_q1_fy27_roa_is_classed_annualised_and_its_denominator_checks_out():
    """Review 01 F6, Brief §6.3 (Spec 02A §9.5 item 6). SBI's standalone Q1 FY27 filing (quarter to 30 Jun 2026):
    PAT ₹21,121.22 cr, filed ROA 1.11%, total segment assets ₹75,96,696.68 cr at 30 Jun 2026 (₹75,49,158.65 cr at
    31 Mar 2026). Quarter PAT ÷ quarter-end segment assets = 0.278%, and 1.11% ÷ 0.278% = 3.99: annualised, so ROA is
    shown as filed. The denominator check: PAT × 4 ÷ the average of the two quarter-end segment assets
    = 84,484.88 ÷ 75,72,927.67 = 1.1156% ≈ 1.12%, against the filed 1.11%."""
    v = values("SBIN")
    roa = one(v, "roa", "quarter", D(2026, 6, 30))
    assert roa.basis == "standalone" and roa.value == Decimal("0.0111") and roa.held is None
    assert roa.inputs["scale"] == {"ratio": "3.9924", "class": "annualised"}
    q = next(f for f in fins("SBIN") if f.basis == "standalone" and f.kind == "quarter" and f.end == D(2026, 6, 30))
    sa = {f.end: f.values["segment_assets_total"] for f in fins("SBIN")
          if f.basis == "standalone" and f.kind == "instant" and f.end in (D(2026, 3, 31), D(2026, 6, 30))}
    check = q.values["pat"] * 4 / ((sa[D(2026, 3, 31)] + sa[D(2026, 6, 30)]) / 2)
    assert round(check, 4) == Decimal("0.0112") and abs(check - roa.value) < Decimal("0.0001")


@needs_fixtures
def test_sbi_q1_fy27_nim_proxy_by_hand_and_plausible():
    """NII = interest earned ₹1,27,896.46 cr − interest expended ₹80,904.02 cr = ₹46,992.44 cr; annualised × 4 =
    ₹1,87,969.76 cr; ÷ the average of total segment assets at 31 Mar and 30 Jun 2026 (₹75,72,927.665 cr) = 2.4821%.
    SBI reports a NIM of about 3% on interest-earning assets; a NIM on total assets runs below that, so 2.48% is
    plausible (a 4× error would read about 10% or 0.6%)."""
    v = values("SBIN")
    nim = one(v, "nim_proxy", "quarter", D(2026, 6, 30))
    assert nim.value == Decimal("0.024821280265")
    assert one(v, "nii", "quarter", D(2026, 6, 30)).value == Decimal(469924400000)
    assert Decimal("0.02") < nim.value < Decimal("0.03")


@needs_fixtures
@pytest.mark.parametrize("symbol", ["HDFCBANK", "FEDERALBNK"])
def test_hdfc_bank_and_federal_bank_file_roa_quarterly_shown_times_four(symbol):
    v = values(symbol)
    roas = [x for (k, *_), x in v.items() if k == "roa" and x.period_end >= D(2024, 3, 31)]
    assert roas and all(x.inputs["scale"]["class"] == "quarterly" for x in roas)
    q = next(f for f in fins(symbol) if f.basis == "standalone" and f.kind == "quarter" and f.end == D(2026, 6, 30))
    assert one(v, "roa", "quarter", D(2026, 6, 30)).value == q.values["roa_filed"] * 4


@needs_fixtures
def test_a_roa_whose_scale_cant_be_decided_is_held_with_its_ratio():
    """Kotak's filed ROA is about 1.35 times quarter PAT ÷ segment assets: neither scale, so held (the decided
    fallback for one bank; Brief §6.3)."""
    roa = one(values("KOTAKBANK"), "roa", "quarter", D(2026, 6, 30))
    assert roa.value is None and roa.held == "The filed return on assets' scale can't be decided."
    assert roa.inputs["scale"] == {"ratio": "1.3446", "class": "unclear"}


@needs_fixtures
def test_tier1_is_cet1_plus_at1_and_lenders_have_no_ebit_or_ev():
    v = values("SBIN")
    q = next(f for f in fins("SBIN") if f.basis == "standalone" and f.kind == "quarter" and f.end == D(2026, 6, 30))
    assert one(v, "tier1", "quarter", D(2026, 6, 30)).value == q.values["cet1_ratio"] + q.values["at1_ratio"]
    for key, kind in (("ebit_derived", "quarter"), ("ebit_margin", "ttm"), ("roic", "year"), ("debt", "instant")):
        held = [x for (k, _, pk, _), x in v.items() if k == key and pk == kind]
        assert held and all(x.held == "N/A: not meaningful for a lender" for x in held), key


# ---------------------------------------------------------------------------------------------- periods
X1_MARCH = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN", "BAJFINANCE", "HINDUNILVR", "LT", "NTPC", "TMPV",
            "FEDERALBNK", "SBILIFE"]     # X1's sample companies with saved quarters, all with a March year end
DEMERGED_FY2026 = {"TMPV": "Tata Motors' demerger, ex-date 14 Oct 2025", "HINDUNILVR": "the ice-cream demerger, "
                   "ex-date 5 Dec 2025"}


# The filer's own columns disagree: the gap is pinned (any change shows) and flagged on #117 for Anand.
FILER_GAPS = {
    # Bajaj Finance's Q4 filing gives a year (₹81,982.38 cr) below its nine months (₹60,916.67 cr, the Q3 filing) plus
    # its Q4 quarter (₹21,605.79 cr) by ₹540.08 cr; its quarters sum to its nine months exactly
    # (₹528.18 cr on its standalone filings, which the metrics don't use: consolidated is filed)
    ("BAJFINANCE", "consolidated"): 5_400_800_000,
    # L&T's quarters sum to ₹3 lakh less than its nine-months column, and its nine months plus Q4 to ₹2 lakh less than
    # its year: ₹5 lakh in all, one lakh past the four quarters' rounding
    ("LT", "consolidated"): -500_000,
}


@needs_fixtures
def test_ttm_revenue_at_march_2026_agrees_with_the_fy2026_year_row():
    """Four quarters, each as filed and rounded to ₹1 lakh, against the Q4 filing's year column: within ₹4 lakh.
    Left out: companies whose FY2026 year column was restated by a demerger (named in DEMERGED_FY2026), and the
    insurer, whose filings carry no revenue line (N/A). Where the filer's own year column isn't its nine months plus
    its Q4 quarter (FILER_GAPS), the TTM still equals its four quarters and the gap is the filer's, pinned."""
    end = D(2026, 3, 31)
    gaps = {}
    for symbol in X1_MARCH:
        v = values(symbol)
        for basis in ("consolidated", "standalone"):
            y = next((f for f in fins(symbol) if f.basis == basis and f.kind == "year" and f.end == end), None)
            t = v.get(("revenue", basis, "ttm", end))
            if y is None or t is None or symbol in DEMERGED_FY2026:
                continue
            if symbol == "SBILIFE":
                assert t.held.startswith("N/A: Life insurer filings don't report revenue"), t
                continue
            assert t.value is not None, (symbol, basis, t.held)
            gap = t.value - y.values["revenue"]
            if abs(gap) > 400_000:
                gaps[(symbol, basis)] = gap
    assert gaps == FILER_GAPS
    for symbol in DEMERGED_FY2026:        # the restated year differs, as it should
        assert abs(values(symbol)[("revenue", "consolidated", "ttm", end)].value
                   - next(f for f in fins(symbol) if f.basis == "consolidated" and f.kind == "year"
                          and f.end == end).values["revenue"]) > 400_000


@needs_fixtures
def test_ttm_is_held_with_only_three_consecutive_quarters():
    end = D(2026, 6, 30)
    rows = [f for f in fins("RELIANCE") if not (f.kind == "quarter" and f.end == D(2025, 12, 31))]
    v = values("RELIANCE", rows=rows)
    for key in ("revenue", "pat_owners", "ebit_derived", "ebit_margin"):
        t = v[(key, "consolidated", "ttm", end)]
        assert t.value is None and t.held == "Fewer than four consecutive quarters are filed on one basis."
    full = values("RELIANCE")[("revenue", "consolidated", "ttm", end)]
    parts = full.inputs["quarters"]["revenue"]
    assert [p["period_end"] for p in parts] == ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    assert full.value == sum(Decimal(p["value"]) for p in parts)


@needs_fixtures
def test_ebit_is_derived_from_its_four_lines_and_held_without_one():
    end = D(2026, 3, 31)
    y = next(f for f in fins("RELIANCE") if f.basis == "consolidated" and f.kind == "year" and f.end == end)
    e = values("RELIANCE")[("ebit_derived", "consolidated", "year", end)]
    v = y.values
    assert e.value == (v["pbt_before_exceptional"] + v["finance_cost"] - v["other_income"]
                       - v.get("share_of_associates", 0))
    assert set(e.inputs["financials"][0]["lines"]) == {"pbt_before_exceptional", "finance_cost", "other_income",
                                                      "share_of_associates"}
    assert not mx.ebit_input_errors(list(values("RELIANCE").values()))
    gone = [mx.Fin(f.document_id, f.basis, f.kind, f.start, f.end,
                   {k: x for k, x in f.values.items() if k != "finance_cost"}, f.tags, f.format, f.filed_at)
            if f is y else f for f in fins("RELIANCE")]
    held = values("RELIANCE", rows=gone)[("ebit_derived", "consolidated", "year", end)]
    assert held.value is None and held.held == "The filing doesn't report finance costs."


@needs_fixtures
def test_a_company_without_consolidated_statements_uses_its_standalone_ones():
    rows = [f for f in fins("RELIANCE") if f.basis == "standalone"]
    v = values("RELIANCE", rows=rows)
    t = v[("revenue", "standalone", "ttm", D(2026, 3, 31))]
    assert t.value == next(f for f in rows if f.kind == "year" and f.end == D(2026, 3, 31)).values["revenue"]
    pat = v[("pat_owners", "standalone", "ttm", D(2026, 3, 31))]
    assert pat.value is not None
    assert "standalone: no non-controlling interests" in pat.inputs["financials"][0]["lines"]["pat_owners"]["element"]
    assert not any(b == "consolidated" for (_, b, _, _) in v)


@needs_fixtures
def test_a_revised_filing_replaces_the_earlier_one_for_its_period():
    end = D(2026, 3, 31)
    y = next(f for f in fins("TCS") if f.basis == "consolidated" and f.kind == "year" and f.end == end)
    revised = mx.Fin(999, y.basis, y.kind, y.start, y.end, {**y.values, "revenue": y.values["revenue"] + 10_000_000},
                     y.tags, y.format, datetime(2026, 5, 1, tzinfo=UTC))
    v = values("TCS", rows=[*fins("TCS"), revised])
    g = v[("ebit_margin", "consolidated", "year", end)]
    assert g.inputs["financials"][0]["document_id"] == 999


# ---------------------------------------------------------------------------------------------- growth and breaks
def demerger(slug: str, ex: date) -> mx.Break:
    return mx.Break("demerger", ex, f"{slug}-demerger-{ex.isoformat()}")


@needs_fixtures
@pytest.mark.parametrize("symbol, ex", [("RELIANCE", D(2023, 7, 20)), ("ITC", D(2025, 1, 6))])
def test_a_demerger_in_the_record_holds_the_revenue_cagr_back(symbol, ex):
    v = values(symbol, breaks=[demerger(symbol.lower(), ex)])
    g = v[("revenue_cagr_5y", "consolidated", "year", D(2026, 3, 31))]
    assert g.value is None and g.held == "The window spans a demerger, so its years aren't comparable."
    assert g.inputs["structural_breaks"][0]["effective"] == ex.isoformat()


@needs_fixtures
def test_hdfc_bank_s_merger_from_the_curated_file_holds_its_cagrs_back():
    breaks = mx.load_breaks()["hdfcbank"]
    assert breaks[0].kind == "merger" and breaks[0].effective == D(2023, 7, 1) and breaks[0].curated
    v = values("HDFCBANK", breaks=breaks)
    for key in ("revenue_cagr_3y", "revenue_cagr_5y"):
        g = v[(key, "consolidated", "year", D(2026, 3, 31))]
        assert g.held == "The window spans a merger, so its years aren't comparable.", key


@needs_fixtures
def test_revenue_cagr_and_growth_by_hand():
    v = values("TCS")
    rev = {f.end: f.values["revenue"] for f in fins("TCS") if f.basis == "consolidated" and f.kind == "year"}
    g = v[("revenue_cagr_3y", "consolidated", "year", D(2026, 3, 31))].value
    assert abs((1 + g) ** 3 - rev[D(2026, 3, 31)] / rev[D(2023, 3, 31)]) < Decimal("1e-10")
    yoy = v[("revenue_yoy", "consolidated", "year", D(2026, 3, 31))].value
    assert yoy == (rev[D(2026, 3, 31)] / rev[D(2025, 3, 31)]).quantize(mx.PLACES) - 1


def test_a_break_on_the_base_year_s_first_day_is_outside_the_window():
    """HUL's GSK Consumer merger took effect on 1 Apr 2020: FY2021 and later all include it."""
    fy = [mx.Fin(i, "consolidated", "year", D(y - 1, 4, 1), D(y, 3, 31), {"revenue": Decimal(100 + i)}, {}, "ind_as")
          for i, y in enumerate(range(2020, 2027))]
    v = values("X", rows=fy, breaks=[mx.Break("merger", D(2020, 4, 1), "x")])
    assert v[("revenue_cagr_5y", "consolidated", "year", D(2026, 3, 31))].value is not None
    assert v[("revenue_cagr_5y", "consolidated", "year", D(2025, 3, 31))].held.startswith("The window spans a merger")


# ---------------------------------------------------------------------------------------------- market
def prices(last: date, close: str, n: int = 300, adj: str | None = None) -> list[mx.Price]:
    """n sessions on weekdays up to `last`, all at `close` (adjusted `adj`, default the close), ₹1 cr traded."""
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(mx.Price(d, Decimal(close), Decimal(adj or close), Decimal(10_000_000)))
        d = date.fromordinal(d.toordinal() - 1)
    return out[::-1]


BAJ_SPLIT = mx.Action("bajfinance-split-2025-06-16", "split", D(2025, 6, 16), factor=Decimal("0.5"), status="applied")
BAJ_BONUS = mx.Action("bajfinance-bonus-2025-06-16", "bonus", D(2025, 6, 16), factor=Decimal("0.2"), status="applied")


@needs_fixtures
def test_bajaj_finance_shares_today_are_ten_times_a_filing_before_its_split_and_bonus():
    """Data Audit 02a finding 12: the 1:2 split and 4:1 bonus of 16 Jun 2025 multiply (0.5 × 0.2 = 0.1)."""
    rows = [f for f in fins("BAJFINANCE") if f.end <= D(2025, 3, 31)]
    q = max((f for f in rows if f.kind == "quarter" and f.values.get("shares_outstanding")),
            key=lambda f: (f.end, f.basis == "consolidated"))
    assert q.end == D(2025, 3, 31)
    v = values("BAJFINANCE", rows=rows, prices=prices(D(2025, 6, 20), "950"), actions=[BAJ_SPLIT, BAJ_BONUS])
    s = v[("shares_today", "none", "market", D(2025, 6, 20))]
    assert s.value == q.values["shares_outstanding"] * 10
    assert [x["factor"] for x in s.inputs["factors"]] == ["0.2", "0.5"]
    assert v[("market_cap", "none", "market", D(2025, 6, 20))].value == Decimal(950) * s.value
    held = values("BAJFINANCE", rows=rows, prices=prices(D(2025, 6, 20), "950"),
                  actions=[BAJ_BONUS, mx.Action(BAJ_SPLIT.slug, "split", BAJ_SPLIT.ex_date, status="held")])
    for key in ("shares_today", "market_cap"):
        assert held[(key, "none", "market", D(2025, 6, 20))].held == \
            "A corporate action since the last filing is under review."
    assert held[("pb", "consolidated", "market", D(2025, 6, 20))].held == \
        "A corporate action since the last filing is under review."


def test_a_dividend_before_a_split_is_restated_per_today_s_share():
    t = D(2026, 6, 30)
    acts = [mx.Action("x-dividend-2025-12-01", "dividend", D(2025, 12, 1), dps=Decimal(10)),
            mx.Action("x-split-2026-01-15", "split", D(2026, 1, 15), factor=Decimal("0.5"), status="applied"),
            mx.Action("x-dividend-2026-05-01", "dividend", D(2026, 5, 1), dps=Decimal(3)),
            mx.Action("x-dividend-2025-06-01", "dividend", D(2025, 6, 1), dps=Decimal(99))]    # over a year ago
    v = values("X", rows=[], prices=prices(t, "200"), actions=acts)
    y = v[("dividend_yield", "none", "market", t)]
    assert y.value == Decimal("0.04")                         # (10 × 0.5 + 3) ÷ 200
    assert [d["per_share_today"] for d in y.inputs["dividends"]] == ["5.0", "3"]


def test_return_and_traded_value_from_adjusted_prices():
    t = D(2026, 6, 30)
    ps = prices(t, "200", adj="200")
    ps = [mx.Price(p.trade_date, p.close, Decimal(100) if p.trade_date <= D(2025, 6, 30) else p.adj_close,
                   p.traded_value) for p in ps]
    v = values("X", rows=[], prices=ps)
    assert v[("return_1y", "none", "market", t)].value == 1
    assert v[("traded_value_median_3m", "none", "market", t)].value == Decimal(10_000_000)
    held = values("X", rows=[], prices=[mx.Price(p.trade_date, p.close, None, p.traded_value) for p in ps])
    assert held[("return_1y", "none", "market", t)].held == \
        "An earlier corporate action is under review, so adjusted prices are held back."
    short = values("X", rows=[], prices=ps[-40:])
    assert short[("return_1y", "none", "market", t)].held == "Less than a year of prices is stored."
    assert short[("traded_value_median_3m", "none", "market", t)].held == "Fewer than 63 sessions of prices are stored."


@needs_fixtures
def test_market_multiples_by_hand_for_an_operating_company():
    t = D(2026, 10, 23)
    v = values("TCS", prices=prices(t, "3000"), leases={})
    sh = v[("shares_today", "none", "market", t)].value
    cap = Decimal(3000) * sh
    tt = [f for f in fins("TCS") if f.basis == "consolidated" and f.kind == "quarter"
          and f.end in (D(2025, 12, 31), D(2026, 3, 31), D(2026, 6, 30), D(2026, 9, 30))]
    assert len(tt) == 4
    pat = sum(f.values["pat_owners"] for f in tt)
    assert v[("pe_ttm", "consolidated", "market", t)].value == (cap / pat).quantize(mx.PLACES)
    bs = next(f for f in fins("TCS") if f.basis == "consolidated" and f.kind == "instant" and f.end == D(2026, 9, 30))
    assert v[("pb", "consolidated", "market", t)].value == (cap / bs.values["equity_owners"]).quantize(mx.PLACES)


@needs_fixtures
def test_pe_is_held_for_a_loss_and_a_lender_s_ev_multiples_are_na():
    t = D(2026, 10, 23)
    rows = [mx.Fin(f.document_id, f.basis, f.kind, f.start, f.end,
                   {**f.values, "pat_owners": -abs(f.values["pat_owners"])} if "pat_owners" in f.values else f.values,
                   f.tags, f.format, f.filed_at) for f in fins("TCS")]
    pe = values("TCS", rows=rows, prices=prices(t, "3000"))[("pe_ttm", "consolidated", "market", t)]
    assert pe.held == "Loss-making: profit to owners over the last four quarters isn't positive."
    bank = values("HDFCBANK", prices=prices(t, "900"))
    for key in ("ev", "ev_ebit_ttm", "ev_sales_ttm"):
        assert bank[(key, "consolidated", "market", t)].held == "N/A: not meaningful for a lender"
    assert bank[("pb", "consolidated", "market", t)].value is not None      # capital and reserves (bank format)


# ---------------------------------------------------------------------------------------------- leases
def lease_of(symbol: str, rows) -> dict[int, mx.Lease]:
    """The lease payments the task reads for the company's lease-check filing (the same reader, the saved file)."""
    f = mx.lease_row(mx.Book(list(rows)), D(2026, 10, 30))
    name = sorted((XBRL / symbol).iterdir())[f.document_id - 1].name[:-3]
    got = st.lease_payments(raw(symbol, name), filing_of(symbol, name))
    return {f.document_id: mx.Lease(*got) if got else mx.Lease(None)}


@needs_fixtures
def test_ev_is_held_where_lease_payments_are_material_against_ebit():
    """Eternal's half-year to 30 Sep 2026: lease payments ₹642 cr against derived EBIT of ₹175 cr (367%), above the
    10% threshold (metrics.yaml). Reliance's FY2026: ₹6,508 cr against ₹1,21,117 cr (5.4%): EV is computed."""
    t = D(2026, 10, 23)
    e = values("ETERNAL", prices=prices(t, "300"), leases=lease_of("ETERNAL", fins("ETERNAL")))
    ev = e[("ev", "consolidated", "market", t)]
    assert ev.held.startswith("This company's lease payments are material against its EBIT")
    assert ev.inputs["lease_check"]["material"] is True and ev.inputs["lease_check"]["lease_payments"] == "6420000000"
    assert e[("ev_ebit_ttm", "consolidated", "market", t)].held == ev.held
    r = values("RELIANCE", prices=prices(t, "1400"), leases=lease_of("RELIANCE", fins("RELIANCE")))
    ok = r[("ev", "consolidated", "market", t)]
    assert ok.value is not None and ok.inputs["lease_check"]["material"] is False
    assert ok.inputs["label"] == mx.LEASE_LABEL
    assert Decimal("0.05") < Decimal(ok.inputs["lease_check"]["lease_payments"]) / \
        Decimal(ok.inputs["lease_check"]["ebit"]) < Decimal("0.06")


def test_the_lease_rule_and_breaks_files_load_and_refuse_malformed_rows(tmp_path):
    assert RULES.lease_threshold == Decimal("0.10")
    bad = tmp_path / "m.yaml"
    bad.write_text(yaml.safe_dump({"lease_materiality": {"threshold": 0.1}}))
    with pytest.raises(ValueError, match="threshold and its reason"):
        mx.Rules.load(bad)
    br = tmp_path / "b.yaml"
    br.write_text(yaml.safe_dump({"breaks": [{"company": "x", "kind": "acquisition", "effective": "2024-01-01",
                                              "announcement": "a", "announced": "2024-01-01", "link": "l"}]}))
    with pytest.raises(ValueError, match="kind is"):
        mx.load_breaks(br)
    br.write_text(yaml.safe_dump({"breaks": [{"company": "x", "kind": "merger"}]}))
    with pytest.raises(ValueError, match="needs"):
        mx.load_breaks(br)


# ---------------------------------------------------------------------------------------------- checks and wiring
@needs_fixtures
def test_the_checks_catch_a_ttm_that_isnt_its_quarters_and_an_ebit_without_its_inputs():
    vs = list(values("RELIANCE").values())
    assert not mx.ttm_sum_errors(vs) and not mx.ebit_input_errors(vs)
    t = next(v for v in vs if v.key == "revenue" and v.period_kind == "ttm" and v.value is not None)
    e = next(v for v in vs if v.key == "ebit_derived" and v.period_kind == "quarter" and v.value is not None)
    bad = [mx.Value(t.key, t.basis, t.period_kind, t.period_end, t.value + 1, None, t.inputs),
           mx.Value(e.key, e.basis, e.period_kind, e.period_end, e.value, None, {"financials": []})]
    assert len(mx.ttm_sum_errors(bad)) == 1 and len(mx.ebit_input_errors(bad)) == 1


def test_every_key_computed_is_one_of_the_catalogue_s_35_non_beta_keys():
    keys = set(mx.OPERATING_ONLY) | {"revenue", "pat_owners", "net_margin", "effective_tax", "roe", "revenue_yoy",
                                     "pat_owners_yoy", "revenue_cagr_3y", "revenue_cagr_5y", "nii", "roa",
                                     "nim_proxy", "gnpa", "nnpa", "cet1", "tier1", "shares_today", "market_cap",
                                     "pe_ttm", "pb", "dividend_yield", "return_1y", "traded_value_median_3m"}
    assert len(keys) == 35
    import re
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261028001900_equity_schema.sql").read_text()
    block = sql.split("the metric catalogue (Spec")[1].split("field policy (Spec")[0]
    catalogue = set(re.findall(r"^\s+\('([a-z0-9_]+)',\s+'", block, re.MULTILINE))
    assert keys == catalogue - {"beta_regression", "beta_bottom_up"}


def test_the_task_is_wired_in_tasks_yaml():
    t = config.load()["equity.metrics"]
    assert t.rules == {"weekly": "every 6 days"}
    assert t.run_after == ("equity.statements", "equity.corporate_actions", "equity.shareholding")
    assert t.uses_nse is False and registry()["equity.metrics"] is EquityMetrics
