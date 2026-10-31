"""The reverse DCF (Spec 02A §5.9; job 2A-14): the revenue growth, years 1–5, that makes the FCFF model's value per
share (`dcf.per_share`, every other assumption as in §5.7) equal the latest close. Market-agnostic, like the rest of
the package. The result is compared with the delivered revenue growth the caller supplies (the 5-year CAGR on the same
basis); gap = implied − delivered, only when both exist.

**The solver:** the value at each point of a 1-point grid from −20% to +50%, then bisection to 0.01 points between the
two grid points that hold the price. Outcomes, in this order (each a `Run`, stored with every input):
1. `not_applicable`, with the DCF's own reason (the family; EBIT ≤ 0; invested capital or sales-to-capital ≤ 0; WACC ≤
   terminal growth; a held input, the price included): checked before anything else, so a company the model can't
   value never counts as a no-solution;
2. the 3-FY return on capital (EBIT margin × (1 − the 3-FY effective tax) × sales-to-capital) at or below WACC: no
   growth rate is reported (Spec 02A §10 decision 3). `growth_lowers_value` with §5.9's sentence where the model's value
   falls at every step of the grid; `return_below_cost` where it doesn't (see below);
3. the value not rising at every step of the grid although the return on capital exceeds WACC: `tax_fade` when the
   3-FY effective rate is below the marginal rate (see below); otherwise it's a bug, so the solver raises;
4. the price below the value at −20%: `below_range`; above the value at +50%: `above_range` (§5.9's sentences);
5. else `solved`, with the implied growth to 0.01 points.

**Where the model's value doesn't follow the return on capital** (found by this job, 1 Nov 2026, #120; the cases and
the choice are on the issue for Anand): §5.9 says the value rises with growth only when the 3-FY return on capital
exceeds WACC. In this model it doesn't hold exactly, for two reasons:
- each year's reinvestment buys that same year's revenue, so growth adds value down to a return of about WACC ÷
  (1 + WACC), not WACC: between the two, the value rises a little with growth, and §5.9's sentence ("faster growth
  lowers the model's value") would be false;
- the tax rate moves from the 3-FY effective rate to the marginal rate over years 6–10, so a company taxed well below
  the marginal rate whose return on capital is just above WACC earns less than WACC on its later growth: its value can
  fall, or rise and then fall, across the grid (two growth rates could match one price).
So the outcome follows the value the model computes on the grid, and each sentence says only what's true of it.
"""
from __future__ import annotations

from bisect import bisect_left
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from ft.equity.valuation.capital import NotApplicable, need
from ft.equity.valuation.dcf import per_share, prepare
from ft.equity.valuation.model import MODEL_VERSION, Company, Input, Market, Run, Settings

KIND = "reverse_dcf"
LOW, HIGH = Decimal("-0.20"), Decimal("0.50")      # Spec 02A §5.9: the range the price is solved in
STEP = Decimal("0.01")                             # the bracket's 1-point grid
TOLERANCE = Decimal("0.00001")                     # bisection stops here; the result is rounded to 0.01 points
POINTS = Decimal("0.0001")                         # 0.01 points, as a fraction
GRID = tuple(LOW + STEP * k for k in range(int((HIGH - LOW) / STEP) + 1))
NO_SOLUTION = ("growth_lowers_value", "return_below_cost", "tax_fade", "below_range", "above_range")
MINUS = "−"


def pct(v: Decimal, places: int = 1) -> str:
    """A fraction as a percentage for a sentence: `places` decimals, a true minus."""
    q = (v * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{abs(q):f}%"
    return f"{MINUS}{text}" if q < 0 else text


def pct_pair(a: Decimal, b: Decimal) -> tuple[str, str]:
    """Two different rates, each to one decimal, or as many more as it takes for the text to differ (up to four)."""
    for places in (1, 2, 3, 4):
        x, y = pct(a, places), pct(b, places)
        if x != y or places == 4:
            return x, y
    raise AssertionError("unreachable")


def growth_lowers_value(roc: Decimal, wacc: Decimal) -> str:
    """Spec 02A §5.9's sentence, word for word, with x and y filled in."""
    x, y = pct_pair(roc, wacc)
    return (f"At its 3-year return on capital of {x}, below its cost of capital of {y}, faster growth lowers the "
            f"model's value, so no growth rate matches the price.")


def return_below_cost(roc: Decimal, wacc: Decimal) -> str:
    x, y = pct_pair(roc, wacc)
    return (f"At its 3-year return on capital of {x}, at or below its cost of capital of {y}, growth at that return "
            f"earns no more than it costs, so no growth rate is matched to the price.")


def tax_fade(effective: Decimal, marginal: Decimal) -> str:
    x, y = pct_pair(effective, marginal)
    return (f"Its tax rate rises from {x} to the marginal {y} in the model's later years, so its value doesn't rise "
            f"steadily with growth and no single growth rate matches the price.")


BELOW_RANGE = f"The price implies growth below {pct(LOW, 0)} a year."
ABOVE_RANGE = f"The price implies growth above {pct(HIGH, 0)} a year."


def _shape(values: list[Decimal]) -> str:
    pairs = list(pairwise(values))
    if all(b > a for a, b in pairs):
        return "rising"
    if all(b < a for a, b in pairs):
        return "falling"
    return "neither"


def solve(c: Company, m: Market, s: Settings, price: Input, delivered: Input) -> Run:
    """The reverse DCF for one company at its latest close (`price`, in the company's currency), compared with
    `delivered` (a fraction a year, or held with its reason)."""
    inputs = {"company": c.json(), "market": m.json(), "settings": s.json(), "price": price.json(),
              "delivered": delivered.json()}
    p = price.value

    def run(status: str, outcome: str, reason: str | None, result: Decimal | None = None) -> Run:
        inputs["outcome"] = outcome
        if result is not None and delivered.value is not None:
            inputs["gap"] = str(result - delivered.value)
        return Run(KIND, status, outcome, reason, p, result, delivered.value, delivered.held, inputs, MODEL_VERSION)

    try:
        d, capital, notes = prepare(c, m, s)
        (p,) = need(price)
        if p <= 0:
            raise NotApplicable("The latest close isn't positive.")
    except NotApplicable as na:
        return run("not_applicable", "not_applicable", na.reason)

    roc = d.margin * (1 - d.tax_effective) * d.sales_to_capital
    values = [per_share(d, g) for g in GRID]
    shape = _shape(values)
    inputs.update(drivers=d.json(), capital=capital.json(), notes=notes, reverse={
        "return_on_capital": str(roc), "wacc": str(d.wacc), "range": [str(LOW), str(HIGH)], "step": str(STEP),
        "value_at_low": str(values[0]), "value_at_high": str(values[-1]), "shape": shape})

    if roc <= d.wacc:
        if shape == "falling":
            return run("no_solution", "growth_lowers_value", growth_lowers_value(roc, d.wacc))
        return run("no_solution", "return_below_cost", return_below_cost(roc, d.wacc))
    if shape != "rising":
        if d.tax_effective < d.tax_marginal:
            return run("no_solution", "tax_fade", tax_fade(d.tax_effective, d.tax_marginal))
        raise AssertionError(f"{c.key}: the model's value doesn't rise with growth although its return on capital "
                             f"({roc}) exceeds WACC ({d.wacc}) and its tax rate doesn't rise: a bug")
    if p < values[0]:
        return run("no_solution", "below_range", BELOW_RANGE)
    if p > values[-1]:
        return run("no_solution", "above_range", ABOVE_RANGE)
    return run("solved", "solved", None, bisect(d, p, values))


def bisect(d, p: Decimal, values: list[Decimal]) -> Decimal:
    """The growth whose value is `p`, between the two grid points that hold it, to 0.01 points."""
    k = bisect_left(values, p)                     # values[k - 1] < p <= values[k]
    if values[k] == p:
        return GRID[k].quantize(POINTS)
    lo, hi = GRID[k - 1], GRID[k]
    while hi - lo > TOLERANCE:
        mid = (lo + hi) / 2
        if per_share(d, mid) < p:
            lo = mid
        else:
            hi = mid
    return ((lo + hi) / 2).quantize(POINTS, rounding=ROUND_HALF_UP)
