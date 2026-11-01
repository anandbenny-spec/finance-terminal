"""The valuation engine's inputs and results (Spec 02A §5.7; job 2A-13). Market-agnostic: a company's figures arrive in
its own reporting currency with the parameters of its own market (`CLAUDE.md` §1, the currency guard), and nothing in
this package names a market, a currency or a source. The caller (`ft/equity/assemble.py` for the listed companies,
the EDGAR test helper for the engine's US check) builds them.

Every input is a value with its unit, as-of date and source, or the reason it's held. A held input makes the model
N/A with that reason (Brief §1: might be wrong, held back); the reason is a sentence a visitor can read.

Rates are fractions (0.0531 for 5.31%). Money is in the company's currency, in full units (rupees, dollars).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal

MODEL_VERSION = "dcf-1"
IMPLIED_ROE_VERSION = "implied-roe-1"
FAMILIES = ("operating", "lender", "insurer", "mixed")
RUN_KINDS = ("reverse_dcf", "implied_roe")       # 2A-14's kinds of equity.valuation_run (dcf_default is `Result`)
RUN_STATUSES = ("solved", "no_solution", "not_applicable")
UNITS = ("fraction", "money", "shares", "beta", "multiple")

# The N/A sentences of the non-operating families (Spec 02A §5.9's wording for insurers and mixed companies).
FAMILY_NA = {
    "lender": "A lender's debt is its raw material, so an operating-cash-flow model doesn't apply; "
              "it's read through the return on equity its price implies instead.",
    "insurer": "Book value doesn't capture an insurer's value; embedded value is v2.",
    "mixed": "Consolidated figures include a lending or insurance business; a sum-of-the-parts valuation isn't "
             "built yet.",
}


@dataclass(frozen=True)
class Input:
    """One input: a value with its unit, as-of date and source, or the reason it's held (exactly one of the two)."""
    value: Decimal | None
    unit: str
    as_of: date | None
    source: str
    held: str | None = None
    detail: dict = field(default_factory=dict, compare=False)      # where it came from (lines, documents, parts)

    def __post_init__(self):
        if (self.value is None) == (self.held is None):
            raise ValueError(f"an input has a value or a held reason, not both or neither ({self.source})")
        if self.unit not in UNITS:
            raise ValueError(f"unknown unit {self.unit!r}")

    @classmethod
    def of(cls, value, unit: str, as_of: date | None, source: str, **detail) -> Input:
        return cls(value if isinstance(value, Decimal) else Decimal(str(value)), unit, as_of, source, None, detail)

    @classmethod
    def withheld(cls, reason: str, unit: str, source: str, as_of: date | None = None, **detail) -> Input:
        return cls(None, unit, as_of, source, reason, detail)

    def json(self) -> dict:
        out = {"value": None if self.value is None else str(self.value), "unit": self.unit,
               "as_of": self.as_of.isoformat() if self.as_of else None, "source": self.source}
        if self.held:
            out["held"] = self.held
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class Market:
    """The parameters of the company's market, each with its source and date."""
    market: str                    # the market code the company records carry
    currency: str
    risk_free: Input               # the rate the cost of equity starts from and terminal growth equals
    gross_yield: Input             # the government 10-year yield as quoted: the cost of debt's floor
    erp: Input                     # the equity risk premium for the market
    marginal_tax: Input

    def json(self) -> dict:
        return {"market": self.market, "currency": self.currency,
                **{f.name: getattr(self, f.name).json() for f in fields(self) if isinstance(getattr(self, f.name), Input)}}


@dataclass(frozen=True)
class Company:
    """One company's figures, in its own currency. The three-FY sums run over the last three financial years; the
    balance sheet is the latest filed; shares and market capitalisation are at the price date."""
    key: str                       # the caller's name for it (a slug, a ticker): used in messages only
    market: str
    currency: str
    family: str                    # operating | lender | insurer | mixed
    revenue_ttm: Input
    ebit_ttm: Input
    revenue_3fy: Input
    ebit_3fy: Input
    tax_3fy: Input
    pbt_3fy: Input
    opening_capital_3fy: Input     # Σ invested capital at the start of each of the three years
    revenue_cagr_3y: Input         # the default run's growth (held: the default run is N/A, the grid is still built)
    finance_cost_fy: Input         # the latest financial year's
    debt_opening_fy: Input
    debt_closing_fy: Input
    debt: Input                    # the latest balance sheet's (borrowings)
    cash_and_investments: Input
    nci: Input
    shares: Input
    market_cap: Input
    beta: Input
    extra_held: tuple[str, ...] = ()   # reasons the caller found that hold the whole model (e.g. a lease rule)

    def __post_init__(self):
        if self.family not in FAMILIES:
            raise ValueError(f"{self.key}: unknown valuation family {self.family!r}")

    def inputs(self) -> dict[str, Input]:
        return {f.name: getattr(self, f.name) for f in fields(self) if isinstance(getattr(self, f.name), Input)}

    def json(self) -> dict:
        return {"key": self.key, "market": self.market, "currency": self.currency, "family": self.family,
                **{k: v.json() for k, v in self.inputs().items()},
                **({"held": list(self.extra_held)} if self.extra_held else {})}


@dataclass(frozen=True)
class Lender:
    """A lender's inputs for the return on equity its price implies (Spec 02A §5.9; job 2A-14), in its own currency."""
    key: str
    market: str
    currency: str
    family: str
    pb: Input                      # market capitalisation ÷ equity to owners at the latest balance sheet
    beta: Input                    # the industry's levered beta (not relevered: debt is a lender's raw material)
    delivered_roe: Input           # the average return on equity it has delivered (the caller's window and rules)
    price: Input                   # the latest close (stored with the run; the model reads P/B)

    def __post_init__(self):
        if self.family not in FAMILIES:
            raise ValueError(f"{self.key}: unknown valuation family {self.family!r}")

    def json(self) -> dict:
        return {"key": self.key, "market": self.market, "currency": self.currency, "family": self.family,
                **{f.name: getattr(self, f.name).json() for f in fields(self)
                   if isinstance(getattr(self, f.name), Input)}}


@dataclass(frozen=True)
class Run:
    """One `equity.valuation_run` row of 2A-14's kinds, as data (storing it is 2A-15's): `solved` with its result,
    or `no_solution` / `not_applicable` with the visitor's sentence. `outcome` names the case (it goes into `inputs`
    too); `delivered` is the figure the result is compared with, or `delivered_reason` says why it's held."""
    kind: str                      # reverse_dcf | implied_roe
    status: str                    # solved | no_solution | not_applicable
    outcome: str                   # solved, not_applicable, or the no-solution case
    reason: str | None
    price: Decimal | None
    result: Decimal | None         # implied revenue growth, or implied return on equity (fractions)
    delivered: Decimal | None
    delivered_reason: str | None
    inputs: dict
    model_version: str

    def __post_init__(self):
        if self.kind not in RUN_KINDS or self.status not in RUN_STATUSES:
            raise ValueError(f"unknown run kind or status: {self.kind!r}, {self.status!r}")
        if (self.status == "solved") != (self.result is not None):
            raise ValueError("a solved run has a result; any other has none")
        if self.status != "solved" and not self.reason:
            raise ValueError("a run that isn't solved has its reason")
        if (self.delivered is None) == (self.delivered_reason is None):
            raise ValueError("a run has the delivered figure or the reason it's held, not both or neither")

    @property
    def gap(self) -> Decimal | None:
        """Implied − delivered (a fraction a year: points ÷ 100), only when both exist."""
        if self.result is None or self.delivered is None:
            return None
        return self.result - self.delivered


@dataclass(frozen=True)
class Settings:
    """The model's settings (Spec 02A §5.7), read from `py/config/equity/valuation.yaml` by the caller."""
    kd_spread: Decimal             # the cost of debt's cap is the floor plus this (6 points)
    growth_low: Decimal            # the default run's growth clamp
    growth_high: Decimal
    grid_growth: tuple[Decimal, ...]
    grid_margin_steps: tuple[Decimal, ...]
    grid_wacc_steps: tuple[Decimal, ...]
    max_effective_tax: Decimal     # a 3-FY effective rate outside [0, this] gives way to the marginal rate

    def json(self) -> dict:
        return {"kd_spread": str(self.kd_spread), "growth_clamp": [str(self.growth_low), str(self.growth_high)],
                "grid_growth": [str(g) for g in self.grid_growth],
                "grid_margin_steps": [str(m) for m in self.grid_margin_steps],
                "grid_wacc_steps": [str(w) for w in self.grid_wacc_steps],
                "max_effective_tax": str(self.max_effective_tax)}

    @classmethod
    def from_mapping(cls, raw: dict) -> Settings:
        """From valuation.yaml's `model` mapping; every entry needs its source."""
        def num(v) -> Decimal:
            return Decimal(str(v))

        def need(key: str) -> dict:
            v = raw.get(key)
            if not isinstance(v, dict) or not str(v.get("source") or "").strip():
                raise ValueError(f"valuation.yaml model.{key}: needs its values and a source")
            return v

        kd, clamp, grid, tax = need("cost_of_debt_spread"), need("default_growth"), need("scenario_grid"), \
            need("effective_tax")
        g = grid["growth"]
        start, stop, step = num(g["from"]), num(g["to"]), num(g["step"])
        if step <= 0 or stop < start:
            raise ValueError("valuation.yaml model.scenario_grid.growth: from ≤ to and a positive step")
        growth, x = [], start
        while x <= stop:
            growth.append(x)
            x += step
        out = cls(num(kd["spread"]), num(clamp["low"]), num(clamp["high"]), tuple(growth),
                  tuple(num(m) for m in grid["margin_steps"]), tuple(num(w) for w in grid["wacc_steps"]),
                  num(tax["max"]))
        if not out.growth_low < out.growth_high or out.kd_spread <= 0 or not 0 < out.max_effective_tax < 1:
            raise ValueError("valuation.yaml model: a growth clamp low < high, a positive spread, a tax cap in (0, 1)")
        return out


@dataclass(frozen=True)
class Year:
    """One forecast year of the FCFF model."""
    year: int
    growth: Decimal
    revenue: Decimal
    ebit: Decimal
    tax_rate: Decimal
    nopat: Decimal
    reinvestment: Decimal
    fcff: Decimal
    discount_factor: Decimal
    present_value: Decimal

    def json(self) -> dict:
        return {f.name: (getattr(self, f.name) if f.name == "year" else str(getattr(self, f.name)))
                for f in fields(self)}


@dataclass(frozen=True)
class Result:
    """A model run: `solved` with the value per share, or `not_applicable` with the reason (never a number)."""
    status: str                    # solved | not_applicable (equity.valuation_run.status)
    reason: str | None
    value_per_share: Decimal | None
    inputs: dict                   # every input with value, unit, date and source, and what the model derived
    model_version: str = MODEL_VERSION

    def __post_init__(self):
        if (self.status == "solved") != (self.value_per_share is not None) or \
                (self.status != "solved" and not self.reason):
            raise ValueError("a solved result has a value; any other has a reason and no value")


@dataclass(frozen=True)
class Cell:
    """One scenario: growth, EBIT margin and WACC, with the value per share or the reason there's none."""
    growth: Decimal
    ebit_margin: Decimal
    wacc: Decimal
    value_per_share: Decimal | None
    reason: str | None = None


@dataclass(frozen=True)
class Grid:
    status: str                    # solved (cells built) | not_applicable
    reason: str | None
    cells: tuple[Cell, ...]
    inputs: dict
    model_version: str = MODEL_VERSION
