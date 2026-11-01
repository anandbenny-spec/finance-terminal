"""The return on equity a lender's price implies (Spec 02A §5.9; job 2A-14). Market-agnostic.

For a stable lender, justified P/B = (ROE − g) ÷ (Ke − g), so **implied ROE = g + P/B × (Ke − g)**: P/B on equity to
owners at the latest balance sheet, Ke = rf + β × ERP with the industry's levered beta (Spec 02A §5.8: not relevered,
debt is a lender's raw material), and g = the risk-free rate (the DCF's terminal rule). Single-stage and closed form
(Spec 02A §10 decision 4). It's compared with the return on equity the lender has delivered (the caller's average and
its rules); gap = implied − delivered, only when both exist.

N/A, with the reason: an insurer or a `mixed` company (§5.9's sentences), an operating company (its price is read
through the reverse DCF), a held input, P/B at or below zero, or Ke at or below g.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ft.equity.valuation.capital import NotApplicable, need
from ft.equity.valuation.model import FAMILY_NA, IMPLIED_ROE_VERSION, Lender, Market, Run

KIND = "implied_roe"
POINTS = Decimal("0.0001")                         # 0.01 points, as a fraction
OPERATING_NA = ("An operating company's price is read through the revenue growth it implies, not the return on "
                "equity.")
KE_NOT_ABOVE_GROWTH = "The cost of equity is at or below terminal growth."


def implied_roe(lender: Lender, m: Market) -> Run:
    if (lender.market, lender.currency) != (m.market, m.currency):          # the currency guard (CLAUDE.md §1)
        raise ValueError(f"{lender.key}: a {lender.market} company in {lender.currency} can't run on {m.market} "
                         f"parameters in {m.currency}")
    inputs = {"lender": lender.json(), "market": m.json()}
    delivered = lender.delivered_roe
    price = lender.price.value

    def run(status: str, reason: str | None, result: Decimal | None = None) -> Run:
        outcome = "solved" if status == "solved" else "not_applicable"
        inputs["outcome"] = outcome
        if result is not None and delivered.value is not None:
            inputs["gap"] = str(result - delivered.value)
        return Run(KIND, status, outcome, reason, price, result, delivered.value, delivered.held, inputs,
                   IMPLIED_ROE_VERSION)

    if lender.family != "lender":
        return run("not_applicable", OPERATING_NA if lender.family == "operating" else FAMILY_NA[lender.family])
    try:
        pb, beta = need(lender.pb, lender.beta)
        rf, erp = need(m.risk_free, m.erp)
        if pb <= 0:
            raise NotApplicable("Price-to-book isn't positive.")
        ke, g = rf + beta * erp, rf
        if ke <= g:
            raise NotApplicable(KE_NOT_ABOVE_GROWTH)
    except NotApplicable as na:
        return run("not_applicable", na.reason)
    roe = g + pb * (ke - g)
    inputs["model"] = {"cost_of_equity": str(ke), "terminal_growth": str(g), "pb": str(pb),
                       "implied_roe": str(roe), "formula": "g + P/B × (Ke − g)"}
    return run("solved", None, roe.quantize(POINTS, rounding=ROUND_HALF_UP))
