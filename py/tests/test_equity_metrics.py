"""equity.metrics (job 2A-11): derived metrics from the saved filings (parsed by 2A-09's parser), the bank ROA scale,
TTM against the year, structural breaks, the market figures, the lease rule, the checks, and the task through the
runner.

The filings are in data/fixtures/equity (the private fixtures repo; the tests skip without it, and CI checks it out).
No NSE data is kept in this repo (CLAUDE.md §4)."""
import functools
from datetime import UTC, date, datetime
from decimal import Decimal

import boto3
import httpx
import psycopg
import pytest
import yaml
from conftest import dsn
from moto.server import ThreadedMotoServer
from test_equity_statements import XBRL, filing_of, needs_fixtures, parse, raw

from ft.equity import metrics as mx
from ft.equity import statements as st
from ft.jobs import config
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_filings import EquityFilings
from ft.jobs.tasks.equity_metrics import EquityMetrics
from ft.jobs.tasks.equity_statements import EquityStatements

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
def test_segment_assets_filed_as_zero_hold_roa_and_the_nim_proxy_on_both_sides():
    """Regression (31 Oct 2026, #117's dev run): ICICI Bank's standalone filings give total segment assets of 0 at
    each quarter end from Sep 2020 to Dec 2022. Averaged with Mar 2023's ₹1,57,50,950 cr, that zero made Mar 2023's
    NIM proxy 8.97%, twice its neighbours' (about 4.5%). Zero is now held like a missing figure."""
    v = values("ICICIBANK")
    zero = "Total segment assets are filed as zero or less for the quarter end."
    assert one(v, "roa", "quarter", D(2022, 12, 31)).held == zero
    assert one(v, "nim_proxy", "quarter", D(2022, 12, 31)).held == zero
    assert one(v, "nim_proxy", "quarter", D(2023, 3, 31)).held == zero          # the average needs Dec 2022's
    roa = one(v, "roa", "quarter", D(2023, 3, 31))                             # its own quarter end is positive
    assert roa.held is None and roa.inputs["scale"]["class"] == "annualised"
    nim = one(v, "nim_proxy", "quarter", D(2023, 6, 30))
    assert nim.held is None and Decimal("0.035") < nim.value < Decimal("0.055")


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


@needs_fixtures
def test_shares_that_disagree_with_the_earlier_filings_are_held_with_what_was_compared():
    """Regression (31 Oct 2026, #117's dev run): Bajaj Finserv's Jun 2026 filings give paid-up capital ₹1 cr and a face
    value of 159.87, so 62,551 shares against Mar 2026's 159.87 crore: the market cap read ₹10 cr and P/E 0.00. Held,
    with both earlier counts compared in the inputs; up to Mar 2026 its shares agree and are used."""
    t = D(2026, 10, 23)
    v = values("BAJAJFINSV", prices=prices(t, "2000"), leases={})
    s = v[("shares_today", "none", "market", t)]
    assert s.value is None and s.held == mx.SHARES_HELD
    assert [x["period_end"] for x in s.inputs["shares_check"]["disagrees_with"]] == ["2026-03-31", "2025-12-31"]
    for key, basis in (("market_cap", "none"), ("pe_ttm", "consolidated"), ("pb", "consolidated")):
        assert v[(key, basis, "market", t)].held == mx.SHARES_HELD
    rows = [f for f in fins("BAJAJFINSV") if f.end <= D(2026, 3, 31)]
    ok = values("BAJAJFINSV", rows=rows, prices=prices(t, "2000"), leases={})[("shares_today", "none", "market", t)]
    assert ok.value == Decimal(1598700000) and ok.inputs["shares_check"]["agrees_with"]["period_end"] == "2025-12-31"


def _quarter(i: int, end: date, pat: str, owners: str | None, nci: str | None, fmt: str = "ind_as",
             basis: str = "consolidated") -> mx.Fin:
    vals = {"revenue": Decimal(10**11), "pat": Decimal(pat), "shares_outstanding": Decimal(10**9)}
    vals |= {k: Decimal(v) for k, v in (("pat_owners", owners), ("pat_nci", nci)) if v is not None}
    return mx.Fin(i, basis, "quarter", mx.month_end(end, -2).replace(day=1), end, vals, {}, fmt)


def test_profit_to_owners_that_doesnt_add_up_with_its_profit_is_held_and_rounding_passes():
    """Regression (31 Oct 2026, #117's dev run): Nestlé India files profit to owners and to NCI as 0 every period
    (FY2026 ROE read 0.00%), Adani Ports' FY2026 owners ₹914.83 cr against a profit of ₹12,782.03 cr (ROE 1.15%),
    Tech Mahindra's Dec 2025 quarter ₹199 cr against ₹1,119 cr (its P/E read 34). Owners + NCI ≠ profit beyond
    rounding holds profit to owners and what's built on it; a ₹1 lakh rounding gap passes; banks aren't checked."""
    t = D(2026, 4, 30)
    ends = [D(2025, 6, 30), D(2025, 9, 30), D(2025, 12, 31), D(2026, 3, 31)]
    good = [_quarter(i, e, "10000000000", "9900000000", "100100000") for i, e in enumerate(ends[:3], 1)]
    bad = good + [_quarter(4, ends[3], "11000000000", "0", "0")]
    v = values("X", rows=bad, prices=prices(t, "100"), leases={})
    held = v[("net_margin", "consolidated", "quarter", ends[3])]
    assert held.held == mx.OWNERS_HELD
    lines = held.inputs["financials"][0]["lines"]
    assert {k: lines[k]["value"] for k in ("pat", "pat_owners", "pat_nci")} == {
        "pat": "11000000000", "pat_owners": "0", "pat_nci": "0"}
    assert v[("pe_ttm", "consolidated", "market", t)].held == mx.OWNERS_HELD
    assert v[("pat_owners", "consolidated", "ttm", ends[3])].held == mx.OWNERS_HELD
    ok = values("X", rows=good + [_quarter(4, ends[3], "11000000000", "10890000000", "110100000")],
                prices=prices(t, "100"), leases={})                        # 1,089 cr + 11.01 cr: ₹1 lakh over
    assert ok[("pe_ttm", "consolidated", "market", t)].value == \
        (Decimal(100) * 10**9 / (Decimal(9900000000) * 3 + Decimal(10890000000))).quantize(mx.PLACES)
    assert not mx.owners_disagree(_quarter(5, ends[3], "11000000000", "0", "0", fmt="bank"))
    assert not mx.owners_disagree(_quarter(6, ends[3], "11000000000", None, None, basis="standalone"))


def _shares_row(i: int, end: date, shares: str) -> mx.Fin:
    return mx.Fin(i, "consolidated", "quarter", None, end, {"shares_outstanding": Decimal(shares)}, {}, "ind_as")


def test_shares_pass_against_the_older_filing_when_the_previous_one_is_wrong_and_a_bonus_scales_them():
    """Trent's Dec 2025 filing gives a face value of 10 (35.55 lakh shares against 35.55 crore): a newest filing that
    agrees with the one before it still passes. A bonus between filings scales the comparison; one under review makes
    that filing not comparable; a newest count off by more than twice from both earlier ones is held."""
    t = D(2026, 4, 30)
    rows = [_shares_row(1, D(2025, 9, 30), "355500000"), _shares_row(2, D(2025, 12, 31), "35550000"),
            _shares_row(3, D(2026, 3, 31), "355500000")]
    s = values("X", rows=rows, prices=prices(t, "100"))[("shares_today", "none", "market", t)]
    assert s.value == Decimal(355500000) and s.inputs["shares_check"]["agrees_with"]["period_end"] == "2025-09-30"
    bonus = mx.Action("x-bonus-2026-02-02", "bonus", D(2026, 2, 2), factor=Decimal("0.5"), status="applied")
    rows2 = [_shares_row(1, D(2025, 9, 30), "100"), _shares_row(2, D(2025, 12, 31), "100"),
             _shares_row(3, D(2026, 3, 31), "200")]
    got = values("X", rows=rows2, prices=prices(t, "100"), actions=[bonus])[("shares_today", "none", "market", t)]
    assert got.value == 200 and got.inputs["shares_check"]["agrees_with"]["ratio"] == "1.0000"
    rows3 = rows2[:2] + [_shares_row(3, D(2026, 3, 31), "2000")]
    held = values("X", rows=rows3, prices=prices(t, "100"), actions=[bonus])[("shares_today", "none", "market", t)]
    assert held.held == mx.SHARES_HELD and [x["ratio"] for x in held.inputs["shares_check"]["disagrees_with"]] == \
        ["10.0000", "10.0000"]
    review = mx.Action(bonus.slug, "bonus", bonus.ex_date, status="held")
    free = values("X", rows=rows3, prices=prices(t, "100"), actions=[review])[("shares_today", "none", "market", t)]
    assert free.value == 2000 and "shares_check" not in free.inputs


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
    loss = ("pat", "pat_owners", "pat_nci")          # all three, so owners + NCI still make the period's profit
    rows = [mx.Fin(f.document_id, f.basis, f.kind, f.start, f.end,
                   {**f.values, **{k: -f.values[k] for k in loss if k in f.values}} if "pat_owners" in f.values
                   else f.values, f.tags, f.format, f.filed_at) for f in fins("TCS")]
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


@needs_fixtures
def test_ev_is_held_when_the_lease_check_can_t_be_made():
    t = D(2026, 10, 23)
    f = mx.lease_row(mx.Book(list(fins("RELIANCE"))), t)
    v = values("RELIANCE", prices=prices(t, "1400"), leases={f.document_id: mx.Lease(None, unreadable=True)})
    assert v[("ev", "consolidated", "market", t)].held == \
        "Lease payments can't be checked against EBIT: the filing couldn't be read again."
    untagged = values("RELIANCE", prices=prices(t, "1400"), leases={f.document_id: mx.Lease(None)})
    assert untagged[("ev", "consolidated", "market", t)].value is not None     # no lease payments are filed


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


class _Tx:
    """A load transaction that records its statements: the stored rows come back from the first select."""

    def __init__(self, stored: list[tuple]):
        self.stored, self.executes, self.batches = stored, [], []

    def execute(self, sql, params=None):
        self.executes.append(sql)
        return type("R", (), {"fetchall": lambda _: self.stored})()

    def cursor(self):
        tx = self

        class Cur:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=None): tx.executes.append(sql)
            def executemany(self, sql, rows): tx.batches.append((sql.split()[0], len(list(rows))))
        return Cur()


def test_the_load_batches_its_writes_however_many_rows_change():
    """Regression (31 Oct 2026, #117): the first dev run wrote one statement per row, a round trip each from GitHub's
    US runner to the Mumbai project, and hit the workflow's 30 minutes. Now one select, then one executemany per
    kind: 3,000 inserts, an update and a stale row's delete are four statements."""
    E = D(2026, 3, 31)
    vals = [mx.Value(f"k{i}", "consolidated", "quarter", E, Decimal(i), None, {}) for i in range(3000)]
    vals.append(mx.Value("revenue", "consolidated", "ttm", E, Decimal(2), None, {}))
    stored = [(1, "revenue", "consolidated", "ttm", E, Decimal(1), None, {}),              # changed: updated
              (1, "revenue", "consolidated", "ttm", D(2001, 3, 31), Decimal(1), None, {}),  # stale: deleted
              (1, "market_cap", "none", "market", D(2026, 1, 2), Decimal(1), None, {})]     # market history: kept
    fetched = type("F", (), {"companies": {1: None}, "values": {1: vals}, "lease_reads": 0})()
    t, tx = EquityMetrics(), _Tx(stored)
    assert t.load(tx, fetched, 1) == 3002
    assert len(tx.executes) == 1 and tx.batches == [("insert", 3000), ("update", 1), ("delete", 1)]
    assert t.result == {"inserted": 3000, "updated": 1, "deleted": 1, "lease_reads": 0}
    tx = _Tx(stored[2:] + [(1, v.key, v.basis, v.period_kind, v.period_end, v.value, None, {}) for v in vals])
    assert t.load(tx, fetched, 1) == 0 and tx.batches == []                   # unchanged: nothing written


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5597, "equity-metrics-raw"
FKEY, SKEY, MKEY = "test.equity_metrics_filings", "test.equity_metrics_statements", "test.equity_metrics"
NOW = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
LAST = D(2026, 10, 23)
CO = {"RELIANCE": ("INE002A01018", "operating"), "SBIN": ("INE062A01020", "lender")}


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        for key in (FKEY, SKEY, MKEY):
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'equity metrics test', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (key,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=f"http://127.0.0.1:{PORT}", region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


def storage():
    from ft.storage import Storage
    return Storage(f"http://127.0.0.1:{PORT}", BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zm-%')"
        for sql in (f"delete from equity.metric_value where entity_id in {ids}",
                    f"delete from equity.financials where entity_id in {ids}",
                    f"delete from equity.filing where entity_id in {ids}",
                    f"delete from core.document where entity_id in {ids}",
                    f"delete from equity.adjustment_factor where entity_id in {ids}",
                    f"delete from equity.corporate_action where event_id in (select id from core.event where entity_id in {ids})",
                    f"delete from core.event where entity_id in {ids}",
                    f"delete from equity.price_daily where entity_id in {ids}",
                    f"delete from equity.company where entity_id in {ids}",
                    f"delete from core.entity_identifier where entity_id in {ids}",
                    "delete from core.entity where slug like 'zm-%'",
                    "delete from core.raw_file where object_key = 'zm-prices'"):
            c.execute(sql)
        c.execute("delete from ops.alert where task_key = any(%s)", ([FKEY, SKEY, MKEY],))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def _task(base, key, workflows=("weekly", "refresh", "backfill")):
    t = type("T", (base,), {"key": key})()
    t.workflows = workflows
    return t


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a database-only task fetched {request.url}")


def setup_companies():
    """RELIANCE and SBI: their filings registered (2A-07) and parsed (2A-09) from the saved replies and instances,
    then 300 sessions of prices to 23 Oct 2026, a dividend and Reliance's 2023 demerger with its factor."""
    from test_equity_filings import Nse
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for s, (isin, family) in CO.items():
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                            (f"zm-{s.lower()}", f"{s} test")).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, isin, nse_symbol, fy_end_month, valuation_family)
                         values (%s, %s, %s, 3, %s)""", (eid, isin, s, family))
    http = HttpClient(transport=httpx.MockTransport(Nse()), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "backfill", [_task(EquityFilings, FKEY)], only=FKEY, force=True, http=http, storage=storage,
               clock=lambda: NOW, since=D(2019, 1, 1), until=D(2026, 10, 27))
    assert code == 0, q("select status, error from ops.task_run where task_key = %s order by id desc limit 1", FKEY)
    nohttp = HttpClient(transport=httpx.MockTransport(refuse), sleep=lambda s: None, clock=lambda: NOW)
    assert run(JOB, "refresh", [_task(EquityStatements, SKEY)], only=SKEY, force=True, http=nohttp,
               storage=storage, clock=lambda: NOW) == 0
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at,
                                                task_run_id)
                     select 'zm-prices', s.id, null, sha256('zm'::bytea), 1, 'text/csv', now(),
                            (select max(id) from ops.task_run) from core.source s where s.key = 'nse'""")
        for s, close in (("RELIANCE", 1400), ("SBIN", 880)):
            c.execute("""insert into equity.price_daily (entity_id, trade_date, exchange, series, close, traded_value,
                                                         raw_file_id, task_run_id)
                         select e.id, d::date, 'NSE', 'EQ', %s, 1e9, (select id from core.raw_file
                                where object_key = 'zm-prices'), (select max(id) from ops.task_run)
                           from core.entity e, generate_series(%s::date - 420, %s::date, '1 day') d
                          where e.slug = %s and extract(isodow from d) < 6""",
                      (close, LAST, LAST, f"zm-{s.lower()}"))
        for slug, kind, ex, dps in (("zm-reliance-demerger-2023-07-20", "demerger", D(2023, 7, 20), None),
                                    ("zm-reliance-dividend-2026-08-14", "dividend", D(2026, 8, 14), 5.5)):
            ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id,
                                                      is_published)
                              select 'corporate_action', e.id, %s, %s, %s, 'effective',
                                     (select id from core.source where key = 'nse'), true
                                from core.entity e where e.slug = 'zm-reliance' returning id, entity_id""",
                           (slug, slug, ex)).fetchone()
            c.execute("""insert into equity.corporate_action (event_id, kind, ex_date, subject, dividend_per_share,
                                                              exchange) values (%s, %s, %s, %s, %s, 'NSE')""",
                      (ev[0], kind, ex, slug, dps))
            if kind == "demerger":
                c.execute("""insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method,
                                                                   status) values (%s, %s, %s, 0.90785932,
                                                                   'special_preopen', 'applied')""", (ev[0], ev[1], ex))


def go(workflow: str = "refresh"):
    http = HttpClient(transport=httpx.MockTransport(refuse), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, workflow, [_task(EquityMetrics, MKEY)], only=MKEY, force=True, http=http, storage=storage,
               clock=lambda: NOW)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s
              order by id desc limit 1""", MKEY)[0]
    return code, tr


def stored(symbol: str, key: str, kind: str, end: date):
    return q("""select v.basis, v.value, v.held_reason, v.inputs from equity.metric_value v
                  join core.entity e on e.id = v.entity_id
                 where e.slug = %s and v.metric_key = %s and v.period_kind = %s and v.period_end = %s""",
             f"zm-{symbol.lower()}", key, kind, end)


@pytest.mark.db
@needs_fixtures
def test_a_refresh_writes_every_company_s_metrics_and_a_rerun_writes_nothing(s3):
    setup_companies()
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr
    c = {x["name"]: x for x in tr[2]}
    assert {n: (x["passed"], x["blocking"]) for n, x in c.items()} == {
        "ttm_sums": (True, True), "ebit_inputs": (True, True), "keys": (True, True), "roa_scale": (False, False),
        "ebit_margin": (True, False), "companies": (True, False), "computed": (True, False)}
    n = q("select count(*) from equity.metric_value where entity_id in (select id from core.entity where slug like 'zm-%%')")
    assert tr[1] == n[0][0] > 500
    assert stored("SBIN", "roa", "quarter", D(2026, 6, 30))[0][:3] == ("standalone", Decimal("0.0111"), None)
    # SBI's Dec 2018 quarter files a ROA 2.41 times PAT ÷ segment assets: neither scale, held and named (a warning)
    assert c["roa_scale"]["detail"] == ("banks' quarters: 0 quarterly (× 4), 28 annualised, 1 held "
                                        "(zm-sbin 2018-12-31 (ratio 2.4134))")
    cagr = stored("RELIANCE", "revenue_cagr_5y", "year", D(2026, 3, 31))[0]
    assert cagr[2] == "The window spans a demerger, so its years aren't comparable."
    assert cagr[3]["structural_breaks"][0]["source"] == "zm-reliance-demerger-2023-07-20"
    ev = stored("RELIANCE", "ev", "market", LAST)[0]
    assert ev[1] is not None and ev[3]["lease_check"]["material"] is False and ev[3]["label"] == mx.LEASE_LABEL
    assert stored("SBIN", "ev", "market", LAST)[0][2] == "N/A: not meaningful for a lender"
    dy = stored("RELIANCE", "dividend_yield", "market", LAST)[0]
    assert dy[1] == (Decimal("5.5") / 1400).quantize(mx.PLACES)
    # a re-run on unchanged data writes nothing
    code, tr = go()
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr


@pytest.mark.db
@needs_fixtures
def test_a_stale_period_s_value_is_deleted_and_market_history_kept(s3):
    setup_companies()
    go()
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value)
                     select id, 'revenue', 'consolidated', 'ttm', '2001-03-31', 1 from core.entity
                      where slug = 'zm-reliance'""")
        c.execute("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value)
                     select id, 'market_cap', 'none', 'market', '2026-01-02', 1 from core.entity
                      where slug = 'zm-reliance'""")
    code, tr = go()
    assert code == 0 and tr[1] == 1, tr
    assert stored("RELIANCE", "revenue", "ttm", D(2001, 3, 31)) == []
    assert stored("RELIANCE", "market_cap", "market", D(2026, 1, 2))[0][1] == 1


@pytest.mark.db
def test_the_production_guard_holds_a_scheduled_run_until_the_first_recorded_one():
    with psycopg.connect(JOB, autocommit=True) as db:
        due = _task(EquityMetrics, "test.equity_metrics_never_ran").due(db, "weekly")
    assert not due.due and "first recorded run" in due.reason
