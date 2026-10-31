"""Damodaran's FCFF model (Spec 02A §5.7; job 2A-13): the default run and the scenario grid. Market-agnostic.

- Base revenue = TTM revenue; years 1–5 grow at g; years 6–10 fade linearly to gₜ (year 10's growth is gₜ), with
  gₜ = the market's risk-free rate (Brief §6.3: no higher than it; Damodaran sets it equal).
- EBIT margin at its 3-FY aggregate (Σ EBIT ÷ Σ revenue).
- Tax at the 3-FY effective rate (Σ tax ÷ Σ PBT; the marginal rate if that's outside 0–50% or Σ PBT ≤ 0) for years
  1–5, moving linearly to the marginal rate by year 10.
- Reinvestment = Δrevenue ÷ the 3-FY sales-to-capital (Σ revenue ÷ Σ opening invested capital).
- TV₁₀ = NOPAT₁₁ × (1 − gₜ ÷ WACC) ÷ (WACC − gₜ), NOPAT₁₁ = revenue₁₀ × (1 + gₜ) × margin × (1 − marginal rate):
  return on capital in perpetuity equals WACC, so growth after year 10 adds no value.
- End-of-year discounting at WACC; equity = operations + cash and investments − debt − NCI; per share ÷ shares today.

N/A (a status and a reason, never a number): a lender, insurer or mixed company; any input held (its reason carried
through); TTM or 3-FY EBIT ≤ 0; invested capital or sales-to-capital ≤ 0; WACC ≤ gₜ.

`prepare` runs the rules once and returns the `Drivers`; `per_share(drivers, g)` is the pure, quick value function the
reverse DCF (2A-14) calls many times; `project` is the same arithmetic with every year kept, for the stored run.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from ft.equity.valuation.capital import Capital, NotApplicable, cost_of_capital, need
from ft.equity.valuation.model import FAMILY_NA, Cell, Company, Grid, Market, Result, Settings, Year

ONE, ZERO = Decimal(1), Decimal(0)
YEARS, HIGH_GROWTH = 10, 5
CENT = Decimal("0.01")
WACC_NOT_ABOVE_GROWTH = ("The cost of capital isn't above the terminal growth rate, so the model has no terminal "
                         "value.")


@dataclass(frozen=True)
class Drivers:
    """What the model runs on, derived once per company."""
    revenue0: Decimal
    margin: Decimal
    tax_effective: Decimal         # years 1–5
    tax_marginal: Decimal          # year 10 and the terminal year
    sales_to_capital: Decimal
    wacc: Decimal
    terminal_growth: Decimal
    cash: Decimal
    debt: Decimal
    nci: Decimal
    shares: Decimal

    def json(self) -> dict:
        return {k: str(v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class Projection:
    years: tuple[Year, ...]
    nopat_11: Decimal
    terminal_value: Decimal
    terminal_present_value: Decimal
    operations: Decimal
    equity: Decimal
    per_share: Decimal

    def json(self) -> dict:
        return {"years": [y.json() for y in self.years], "nopat_11": str(self.nopat_11),
                "terminal_value": str(self.terminal_value),
                "terminal_present_value": str(self.terminal_present_value), "operations": str(self.operations),
                "equity": str(self.equity), "per_share": str(self.per_share)}


def growth_path(g: Decimal, gt: Decimal) -> list[Decimal]:
    """Years 1–5 at g; years 6–10 move linearly to gₜ, reaching it in year 10."""
    return [g if t <= HIGH_GROWTH else g + (gt - g) * (t - HIGH_GROWTH) / (YEARS - HIGH_GROWTH)
            for t in range(1, YEARS + 1)]


def tax_path(te: Decimal, tm: Decimal) -> list[Decimal]:
    """Years 1–5 at the effective rate; years 6–10 move linearly to the marginal rate, reaching it in year 10."""
    return [te if t <= HIGH_GROWTH else te + (tm - te) * (t - HIGH_GROWTH) / (YEARS - HIGH_GROWTH)
            for t in range(1, YEARS + 1)]


def _terminal(d: Drivers, revenue10: Decimal) -> tuple[Decimal, Decimal]:
    gt, w = d.terminal_growth, d.wacc
    nopat11 = revenue10 * (1 + gt) * d.margin * (1 - d.tax_marginal)
    return nopat11, nopat11 * (1 - gt / w) / (w - gt)


def per_share(d: Drivers, g: Decimal) -> Decimal:
    """The model's value per share at growth g (unrounded). Pure and quick: the reverse DCF's function."""
    if d.wacc <= d.terminal_growth:
        raise NotApplicable(WACC_NOT_ABOVE_GROWTH)
    revenue, total, df = d.revenue0, ZERO, ONE
    step = 1 + d.wacc
    for gr, tax in zip(growth_path(g, d.terminal_growth), tax_path(d.tax_effective, d.tax_marginal), strict=True):
        new = revenue * (1 + gr)
        fcff = new * d.margin * (1 - tax) - (new - revenue) / d.sales_to_capital
        df = df / step
        total += fcff * df
        revenue = new
    total += _terminal(d, revenue)[1] * df
    return (total + d.cash - d.debt - d.nci) / d.shares


def project(d: Drivers, g: Decimal) -> Projection:
    """The same arithmetic as `per_share`, with every year kept."""
    if d.wacc <= d.terminal_growth:
        raise NotApplicable(WACC_NOT_ABOVE_GROWTH)
    years, revenue, df, total = [], d.revenue0, ONE, ZERO
    for t, (gr, tax) in enumerate(zip(growth_path(g, d.terminal_growth), tax_path(d.tax_effective, d.tax_marginal),
                                      strict=True), 1):
        new = revenue * (1 + gr)
        ebit = new * d.margin
        nopat = ebit * (1 - tax)
        reinvest = (new - revenue) / d.sales_to_capital
        fcff = nopat - reinvest
        df = df / (1 + d.wacc)
        years.append(Year(t, gr, new, ebit, tax, nopat, reinvest, fcff, df, fcff * df))
        total += fcff * df
        revenue = new
    nopat11, tv = _terminal(d, revenue)
    ops = total + tv * df
    equity = ops + d.cash - d.debt - d.nci
    return Projection(tuple(years), nopat11, tv, tv * df, ops, equity, equity / d.shares)


def _check_markets(c: Company, m: Market) -> None:
    """The currency guard (CLAUDE.md §1): a company runs only with its own market's parameters."""
    if (c.market, c.currency) != (m.market, m.currency):
        raise ValueError(f"{c.key}: a {c.market} company in {c.currency} can't run on {m.market} parameters in "
                         f"{m.currency}")


def effective_tax(c: Company, m: Market, s: Settings) -> tuple[Decimal, str]:
    tax, pbt, marginal = need(c.tax_3fy, c.pbt_3fy, m.marginal_tax)
    if pbt <= 0:
        return marginal, "marginal: profit before tax over the three years isn't positive"
    rate = tax / pbt
    if rate < 0 or rate > s.max_effective_tax:
        return marginal, f"marginal: the three-year effective rate is outside 0 to {s.max_effective_tax}"
    return rate, "effective: tax ÷ profit before tax over the three years"


def prepare(c: Company, m: Market, s: Settings, *, check_wacc: bool = True) -> tuple[Drivers, Capital, dict]:
    """The drivers, the cost of capital and notes; NotApplicable with the reason when the model doesn't run."""
    _check_markets(c, m)
    if c.family != "operating":
        raise NotApplicable(FAMILY_NA[c.family])
    if c.extra_held:
        raise NotApplicable(c.extra_held[0])
    rev_ttm, ebit_ttm, rev3, ebit3, cap3 = need(c.revenue_ttm, c.ebit_ttm, c.revenue_3fy, c.ebit_3fy,
                                                c.opening_capital_3fy)
    cash, debt, nci, shares = need(c.cash_and_investments, c.debt, c.nci, c.shares)
    rf, marginal = need(m.risk_free, m.marginal_tax)
    if ebit_ttm <= 0:
        raise NotApplicable("EBIT over the last four quarters is zero or negative.")
    if ebit3 <= 0:
        raise NotApplicable("EBIT over the last three financial years is zero or negative.")
    if rev_ttm <= 0:
        raise NotApplicable("Revenue over the last four quarters is zero or negative.")
    if cap3 <= 0:
        raise NotApplicable("Invested capital is zero or negative.")
    if rev3 <= 0:
        raise NotApplicable("Sales-to-capital is zero or negative.")
    if shares <= 0:
        raise NotApplicable("Shares outstanding aren't positive.")
    tax, tax_note = effective_tax(c, m, s)
    capital = cost_of_capital(c, m, s)
    d = Drivers(rev_ttm, ebit3 / rev3, tax, marginal, rev3 / cap3, capital.wacc, rf, cash, debt, nci, shares)
    if check_wacc and d.wacc <= d.terminal_growth:
        raise NotApplicable(WACC_NOT_ABOVE_GROWTH)
    return d, capital, {"tax_rate_years_1_5": tax_note}


def _base_inputs(c: Company, m: Market, s: Settings) -> dict:
    return {"company": c.json(), "market": m.json(), "settings": s.json()}


def cents(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def default_run(c: Company, m: Market, s: Settings) -> Result:
    """`dcf_default`: the model at g = the 3-FY revenue CAGR, clamped to the settings' range."""
    inputs = _base_inputs(c, m, s)
    try:
        d, capital, notes = prepare(c, m, s)
        (cagr,) = need(c.revenue_cagr_3y)
    except NotApplicable as na:
        return Result("not_applicable", na.reason, None, inputs)
    g = min(max(cagr, s.growth_low), s.growth_high)
    p = project(d, g)
    inputs.update(drivers=d.json(), capital=capital.json(), notes=notes,
                  growth={"revenue_cagr_3y": str(cagr), "used": str(g), "clamped": g != cagr},
                  projection=p.json())
    return Result("solved", None, cents(p.per_share), inputs)


def scenario_grid(c: Company, m: Market, s: Settings) -> Grid:
    """Growth × EBIT margin (± steps, a margin ≤ 0 skipped) × WACC (± steps; a WACC ≤ gₜ gives no value, with the
    reason). Built without the default run's growth, so a held CAGR doesn't stop it."""
    inputs = _base_inputs(c, m, s)
    try:
        d, capital, notes = prepare(c, m, s, check_wacc=False)
    except NotApplicable as na:
        return Grid("not_applicable", na.reason, (), inputs)
    cells = []
    for g in s.grid_growth:
        for dm in s.grid_margin_steps:
            margin = d.margin + dm
            if margin <= 0:
                continue
            for dw in s.grid_wacc_steps:
                w = d.wacc + dw
                if w <= d.terminal_growth:
                    cells.append(Cell(g, margin, w, None, WACC_NOT_ABOVE_GROWTH))
                    continue
                cells.append(Cell(g, margin, w, cents(per_share(replace(d, margin=margin, wacc=w), g))))
    inputs.update(drivers=d.json(), capital=capital.json(), notes=notes)
    return Grid("solved", None, tuple(cells), inputs)
