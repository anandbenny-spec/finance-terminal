"""Cost of equity, cost of debt and WACC (Spec 02A §5.7; job 2A-13). Market-agnostic: the rates come in `Market`.

- Cost of equity = rf + β × ERP (β the bottom-up beta, §10 decision 6).
- Pre-tax cost of debt = the latest financial year's finance costs ÷ the average of its opening and closing debt,
  bounded to [the gross 10-year yield, that + the spread] and labelled derived; `bounded` names the bound it hit.
- Weights at market capitalisation and book debt; no debt weight when debt is nil (the cost of debt isn't needed).
- WACC = E/(D+E) × Ke + D/(D+E) × Kd × (1 − the marginal rate).

**No borrowings at either end of the year but some now** (decided by Claude, standing delegation, 1 Nov 2026, #119):
the ratio has no denominator, so the cost of debt is held and the model with it (the alternative was the floor, a
number the filings don't give). Debt nil now means no debt weight whatever the year held.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ft.equity.valuation.model import Company, Market, Settings

ZERO = Decimal(0)


class NotApplicable(Exception):
    """The model can't run for this company; `reason` is the visitor's sentence."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Capital:
    cost_of_equity: Decimal
    cost_of_debt: Decimal | None   # pre-tax, after the bounds; None when there's no debt weight
    cost_of_debt_raw: Decimal | None
    bounded: str | None            # floor | cap | None
    weight_equity: Decimal
    weight_debt: Decimal
    wacc: Decimal

    def json(self) -> dict:
        def s(v):
            return None if v is None else str(v)
        return {"cost_of_equity": s(self.cost_of_equity), "cost_of_debt": s(self.cost_of_debt),
                "cost_of_debt_raw": s(self.cost_of_debt_raw), "cost_of_debt_label": "derived",
                "cost_of_debt_bounded": self.bounded, "weight_equity": s(self.weight_equity),
                "weight_debt": s(self.weight_debt), "wacc": s(self.wacc)}


def need(*inputs) -> list[Decimal]:
    """The values of the inputs, or NotApplicable with the first held one's reason."""
    for i in inputs:
        if i.value is None:
            raise NotApplicable(i.held)
    return [i.value for i in inputs]


def cost_of_debt(c: Company, m: Market, s: Settings) -> tuple[Decimal, Decimal, str | None]:
    """(bounded, raw, which bound) for a company with debt."""
    cost, opening, closing, floor = need(c.finance_cost_fy, c.debt_opening_fy, c.debt_closing_fy, m.gross_yield)
    average = (opening + closing) / 2
    if average <= 0:
        raise NotApplicable("The cost of debt can't be derived: the last financial year had no borrowings at its "
                            "start or end.")
    raw = cost / average
    cap = floor + s.kd_spread
    if raw < floor:
        return floor, raw, "floor"
    if raw > cap:
        return cap, raw, "cap"
    return raw, raw, None


def cost_of_capital(c: Company, m: Market, s: Settings) -> Capital:
    rf, beta, erp, tax = need(m.risk_free, c.beta, m.erp, m.marginal_tax)
    equity, debt = need(c.market_cap, c.debt)
    if equity <= 0:
        raise NotApplicable("Market capitalisation isn't positive.")
    if debt < 0:
        raise NotApplicable("Borrowings are negative.")
    ke = rf + beta * erp
    if debt == 0:
        return Capital(ke, None, None, None, Decimal(1), ZERO, ke)
    kd, raw, bound = cost_of_debt(c, m, s)
    we, wd = equity / (equity + debt), debt / (equity + debt)
    return Capital(ke, kd, raw, bound, we, wd, we * ke + wd * kd * (1 - tax))
