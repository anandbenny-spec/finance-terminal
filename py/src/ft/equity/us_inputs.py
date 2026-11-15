"""The US slice's valuation inputs (Spec 02A.1 §5.4; job US-04, #403): the US market's rates and how a US filing's lines
give the DCF's figures. The engine (`ft/equity/valuation/`) and India's assembly (`ft/equity/assemble.py`) are reused;
everything US-specific is here and in `py/config/equity/valuation.yaml`'s `us` block, each parameter with its source.

**Rates** (Spec 02A.1 §5.4; Data Audit 02 E2, E3, E8, E9):
- the risk-free rate is the 10-year Treasury yield (`fred.DGS10`, daily) at the price date: the latest observation on or
  before it, held when that is more than `RF_STALE` old. It is the gross yield, not netted of a US default spread
  (decided by Claude, standing delegation, 12 Nov 2026, #403; `valuation.yaml` `us.risk_free` holds the reading of
  Damodaran's files and the alternative): Damodaran nets the US default spread (0.22% at Aa1) from the rate since June
  2025, and adds the same spread back to the US's equity risk premium as its country risk premium (ctrypremJuly26: US
  ERP 4.42% = mature 4.20% + 0.22%). The spec's other two rules (the mature-market ERP, no country risk premium) would,
  with a netted rate, drop the spread from the cost of equity altogether; the gross yield with the mature ERP gives
  his market cost of equity exactly at a beta of 1;
- the equity risk premium is Damodaran's implied mature-market ERP of the latest month on or before the price date
  (`damodaran.ERP_IMPLIED`, the series India's ERP starts from), with no country risk premium;
- the marginal tax rate is 25% (federal 21% plus state taxes; Damodaran's US rate);
- terminal growth is the risk-free rate (the engine's rule: no higher than it).

**Lines** (Spec 02A.1 §5.2 and §5.4; the rows are `us.filings`'s, US-02):
- EBIT is operating income as filed (`OperatingIncomeLoss`), never derived; a company that files none has no EBIT;
- debt is the borrowings as filed (current and long-term). Lease liabilities aren't counted, and the India lease rule
  (Anand, 30 Oct 2026, #117: EV held where lease payments are material against derived EBIT, because results filings
  carry no lease liabilities) doesn't apply: under US GAAP an operating lease's cost sits inside operating income, so
  operating income as filed and debt without lease liabilities are consistent (decided by Claude, standing delegation,
  12 Nov 2026, #403; the alternative: read the lease liabilities and add them to debt with the lease cost added back to
  operating income, Damodaran's adjustment, which needs new columns from the adapter). Current debt filed in parts
  with no total is held by the adapter (#401's open question), and so is every figure that needs it;
- cash is cash and cash equivalents as filed; marketable securities aren't counted, as in the engine's EDGAR test
  (decided by Claude, standing delegation, 1 Nov 2026, #119: counting them takes Apple's invested capital below zero).
  Both labels travel with the figures.

The US companies are never published here or anywhere in lane U's tasks (Spec 02A.1 §3).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from ft.equity import assemble
from ft.equity import inputs as inp
from ft.equity import metrics as mx
from ft.equity import statements as st
from ft.equity.valuation.model import Company, Input, Lender, Market

CONFIG = Path(__file__).resolve().parents[3] / "config" / "equity"
MARKET, CURRENCY = "US", "USD"
FILINGS = "10-K and 10-Q filings, SEC EDGAR"
RF_SERIES = "fred.DGS10"
ERP_SERIES = "damodaran.ERP_IMPLIED"
INDEX = "yf.^GSPC"
RF_STALE = timedelta(days=10)                   # a week's quotes plus a holiday weekend
BETAS_URL = inp.DAMODARAN_BASE + "pc/datasets/betas.xls"
IMF_COUNTRY = "USA"
# indicator -> (series key, its name, bounds for the shape check)
INDICATORS = {
    "NGDP_RPCH": ("imf.NGDP_RPCH.USA", "United States real GDP growth, IMF projection (per WEO edition)", (-20, 30)),
    "PCPIPCH": ("imf.PCPIPCH.USA", "United States CPI inflation, IMF projection (per WEO edition)", (-5, 60)),
}
ZERO = Decimal(0)

DEBT_LABEL = "borrowings as filed; lease liabilities aren't counted"
CASH_LABEL = "cash and cash equivalents as filed; marketable securities aren't counted"
SOURCE_PRICE = "Source: Yahoo Finance"

# The visitor's sentence for a line the adapter held (its `tags` entry starts with "held"), by column
HELD = {
    "borrowings_current": "The filing splits its current debt into parts and files no total, so its debt isn't shown.",
    "shares_outstanding": "The cover page lists share classes whose conversion rates aren't curated yet, so the share "
                          "count isn't shown.",
}
LINES = {**mx.LINES, "revenue": "revenue", "operating_income": "operating income", "pat_owners": "net income",
         "pat": "net income including non-controlling interests", "finance_cost": "interest expense",
         "pbt": "income before income taxes", "borrowings_current": "current debt",
         "borrowings_noncurrent": "long-term debt", "total_equity": "total equity",
         "equity_owners": "stockholders' equity", "cash": "cash and cash equivalents"}
NO_OPERATING_INCOME = "N/A: The company doesn't file operating income."


class UsCalc(mx.Calc):
    """A US row's line: as filed, or Held with the visitor's sentence (a line the adapter held, or one not filed).
    Non-controlling interests not filed are none, as India's (noted in the inputs)."""

    def take(self, f: mx.Fin, col: str) -> Decimal:
        v = f.values.get(col)
        if v is not None:
            self._record(f, col, v, f.tags.get(col) or col)
            return v
        tag = str(f.tags.get(col) or "")
        if tag.startswith("held"):
            self.extra.setdefault("held_lines", {})[col] = tag
            raise mx.Held(HELD.get(col) or f"The filing's {LINES.get(col, col)} is held back.")
        if col == "nci":
            self._record(f, col, ZERO, "not filed: taken as none")
            return ZERO
        raise mx.Held(f"The filing doesn't report {LINES.get(col, col)}.")


def ebit(c: mx.Calc, f: mx.Fin) -> Decimal:
    """Operating income as filed (Spec 02A.1 §5.4)."""
    return c.take(f, "operating_income")


def _filed_or_held(f: mx.Fin, col: str) -> bool:
    return f.values.get(col) is not None or str(f.tags.get(col) or "").startswith("held")


def debt(c: mx.Calc, f: mx.Fin) -> Decimal:
    """Current and long-term borrowings as filed (either may be absent; a held one holds the sum)."""
    has = [k for k in ("borrowings_current", "borrowings_noncurrent") if _filed_or_held(f, k)]
    if not has:
        raise mx.Held("The balance sheet doesn't report borrowings.")
    total = sum((c.take(f, k) for k in has), ZERO)
    c.extra["label"] = DEBT_LABEL
    return total


def cash(c: mx.Calc, f: mx.Fin) -> Decimal:
    v = c.take(f, "cash")
    c.extra["label"] = CASH_LABEL
    return v


def invested(c: mx.Calc, f: mx.Fin) -> Decimal:
    out = c.take(f, "total_equity") + debt(c, f) - cash(c, f)
    c.extra["label"] = f"{DEBT_LABEL}; {CASH_LABEL}"
    return out


def opening_ic(c: mx.Calc, book: mx.Book, basis: str, opening: date) -> Decimal:
    ic = invested(c, mx._instant(book, basis, opening, "start"))
    if ic <= 0:
        raise mx.Held("Opening invested capital is zero or negative.")
    return ic


US = assemble.Lines(
    calc=UsCalc, ebit=ebit, ebit_name="operating_income", ebit_words="operating income", debt=debt, cash_inv=cash,
    cash_words="cash and cash equivalents", opening_ic=opening_ic, debt_label=DEBT_LABEL, filings=FILINGS,
    columns=(*st.COLUMNS, "operating_income"),
    cap_source=f"market capitalisation at the latest close ({SOURCE_PRICE}; equity.metrics)",
    close_source=f"the latest close ({SOURCE_PRICE}; equity.metrics)",
    beta_source="bottom-up beta: Damodaran's US industry beta relevered at market D/E (equity.beta)",
    pb_source=f"price-to-book: market capitalisation ÷ stockholders' equity at the latest balance sheet "
              f"({SOURCE_PRICE}; equity.metrics)",
    lender_beta_source="the industry's levered beta: Damodaran's US industry beta, not relevered (equity.beta)",
    lease_rule=False)


def files_operating_income(s: assemble.Stored) -> bool:
    return any(f.values.get("operating_income") is not None for f in s.fins)


def company_inputs(s: assemble.Stored, as_of: date) -> Company:
    """India's assembly with the US lines; a company that files no operating income at all has no EBIT (N/A)."""
    c = assemble.company_inputs(s, as_of, US)
    if s.company.family == "operating" and not files_operating_income(s):
        c = replace(c, extra_held=(*c.extra_held, NO_OPERATING_INCOME.removeprefix("N/A: ")))
    return c


def lender_inputs(s: assemble.Stored, as_of: date, rule: assemble.DeliveredRoe | None = None) -> Lender:
    return assemble.lender_inputs(s, as_of, rule, US)


def price(s: assemble.Stored, as_of: date) -> Input:
    return assemble.price(s, as_of, US)


def fetch(db, entity_ids: list[int]) -> dict[int, assemble.Stored]:
    """The US companies' rows (their ids, always given: `assemble.fetch` with no ids reads India's), each period
    labelled by its month (`by_month`)."""
    out = assemble.fetch(db, entity_ids, US.columns)
    for s in out.values():
        s.fins = [by_month(f) for f in s.fins]
    return out


# ------------------------------------------------------------------------------------------------ fiscal periods
MONTH_SLACK = 7                 # a 52-53-week year ends within a week of a month end


def nearest_month_end(d: date) -> date:
    """The month end within MONTH_SLACK days of `d` (the nearer one), else `d` itself."""
    best = min((mx.month_end(d, k) for k in (-1, 0)), key=lambda m: abs((m - d).days))
    return best if abs((best - d).days) <= MONTH_SLACK else d


def by_month(f: mx.Fin) -> mx.Fin:
    """A US row with its period labelled by the month it ends in. India's periods end on month ends, and the metrics
    and the DCF find a period's quarters, its year-earlier period and its opening balance sheet by month arithmetic
    (`metrics.month_end`); a 52-53-week filer's periods end on a weekday near the month end instead (Apple's quarter to
    27 Jun 2026, Nvidia's year to 25 Jan 2026, Home Depot's to 1 Feb 2026), so each row is keyed by the nearest month
    end, as the filers label their own quarters (decided by Claude, standing delegation, 12 Nov 2026, #403: it changes
    no figure; the alternative, India's arithmetic with a tolerance, would change India's module). The filed date stays
    on the `equity.financials` row the inputs point to (its document and period kind)."""
    end = nearest_month_end(f.end)
    start = nearest_month_end(f.start - timedelta(days=1)) + timedelta(days=1) if f.start else None
    if end == f.end and start == f.start:
        return f
    return replace(f, start=start, end=end)


# ------------------------------------------------------------------------------------------------ settings and rates
@dataclass(frozen=True)
class Settings:
    tax: Decimal
    tax_source: str
    tax_in_force: date


def load(path: Path | None = None) -> dict:
    raw = yaml.safe_load((path or CONFIG / "valuation.yaml").read_text("utf-8"))
    us = raw.get("us") if isinstance(raw, dict) else None
    if not isinstance(us, dict):
        raise TypeError("valuation.yaml: no `us` block (Spec 02A.1 §5.4)")
    return us


def settings(path: Path | None = None) -> Settings:
    t = load(path).get("marginal_tax_rate") or {}
    rate = Decimal(str(t.get("rate")))
    if not Decimal(0) < rate < Decimal(1) or not str(t.get("source") or "").strip() \
            or not isinstance(t.get("in_force"), date):
        raise ValueError("valuation.yaml: us.marginal_tax_rate needs a rate between 0 and 1, its source and the date "
                         "it's in force from")
    rf = load(path).get("risk_free") or {}
    if rf.get("series") != RF_SERIES or rf.get("netted") is not False or not str(rf.get("source") or "").strip():
        raise ValueError(f"valuation.yaml: us.risk_free must name {RF_SERIES}, say it isn't netted and give its source "
                         "(a netted rate needs the engine's inputs changed with it: #403)")
    return Settings(rate, t["source"], t["in_force"])


def marginal_tax(path: Path | None = None) -> Input:
    s = settings(path)
    return Input.of(s.tax, "fraction", s.tax_in_force, s.tax_source)


def usd_risk_free(db, as_of: date) -> inp.Rate:
    """The 10-year Treasury yield on the latest day on or before `as_of` (percent), held when older than RF_STALE."""
    day, value = inp._latest(db, RF_SERIES, as_of)
    if as_of - day > RF_STALE:
        raise inp.InputsError(f"the latest {RF_SERIES} observation ({day}) is more than {RF_STALE.days} days before "
                              f"{as_of}")
    return inp.Rate(value, {"us_10y_pct": value, "us_10y_date": day.isoformat()})


def mature_erp(db, as_of: date) -> inp.Rate:
    month, value = inp._latest(db, ERP_SERIES, as_of)
    return inp.Rate(value, {"mature_erp_pct": value, "mature_erp_month": month.isoformat()})


RF_SOURCE = ("10-year Treasury yield (Federal Reserve Board, H.15, via FRED), not netted of a US default spread (Aswath "
             "Damodaran nets 0.22% from it and adds the same back to the US's equity risk premium)")
ERP_SOURCE = "Implied equity risk premium of a mature market, with no country risk premium (Aswath Damodaran, NYU Stern: " \
             "ERPbymonth)"


def market_inputs(db, as_of: date, path: Path | None = None) -> Market:
    """The US's parameters on `as_of` (each a held input, with the reason, when its rows aren't stored)."""
    tax = marginal_tax(path)
    try:
        rf = usd_risk_free(db, as_of)
        day = date.fromisoformat(rf.parts["us_10y_date"])
        risk_free = Input.of(rf.value / 100, "fraction", day, RF_SOURCE, us_10y_pct=str(rf.value))
        gross = Input.of(rf.value / 100, "fraction", day,
                         "10-year Treasury yield (Federal Reserve Board, H.15, via FRED)")
    except inp.InputsError:
        why = "The 10-year Treasury yield isn't stored for the price date."
        risk_free = Input.withheld(why, "fraction", RF_SOURCE)
        gross = Input.withheld(why, "fraction", "10-year Treasury yield (FRED)")
    try:
        e = mature_erp(db, as_of)
        erp = Input.of(e.value / 100, "fraction", date.fromisoformat(e.parts["mature_erp_month"]), ERP_SOURCE,
                       mature_erp_pct=str(e.value))
    except inp.InputsError:
        erp = Input.withheld("Damodaran's implied equity risk premium isn't stored.", "fraction", ERP_SOURCE)
    return Market(MARKET, CURRENCY, risk_free, gross, erp, tax)
