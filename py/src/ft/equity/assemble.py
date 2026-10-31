"""One listed company's DCF inputs from the database (Spec 02A §5.7; job 2A-13). Every market-specific part of the
valuation lives here and in `py/config/equity/valuation.yaml`, never in the engine (`ft/equity/valuation/`).

`fetch` reads the rows (no transaction is needed; reads only); `company_inputs` and `market_inputs` are the pure parts,
so the tests build them from the saved filings without a database.

**Where each input comes from**
- Statement figures from `equity.financials`, through the metrics' own rules (`ft/equity/metrics.py`: the latest filed
  row per period, derived EBIT, debt, cash and investments, invested capital), so the DCF reads what the metrics show:
  TTM revenue and EBIT (the latest four consecutive quarters on one basis); the last three financial years' revenue,
  EBIT, tax, profit before tax and opening invested capital, on the TTM's basis; the latest year's finance costs and
  its opening and closing debt; the latest balance sheet's debt, cash and investments and NCI.
- Market figures from `equity.metric_value` at the price date (the newest stored on or before `as_of`): shares today,
  market capitalisation, the bottom-up beta (2A-12), and the lease check stored with EV (Anand, 30 Oct 2026, #117:
  debt is borrowings only, so the DCF is held where lease payments are material against EBIT); the 3-year revenue
  CAGR at the latest financial year (it carries the structural-break rule).
- Rates: the INR risk-free rate (India's 10-year from FRED less Damodaran's default spread) and India's ERP (the
  mature-market ERP plus India's country risk premium), both from `ft/equity/inputs.py`; the gross 10-year as the cost
  of debt's floor; the marginal rate from `valuation.yaml`.
- For the reverse DCF and implied ROE (2A-14): the latest close (the one market capitalisation was computed at); the
  delivered 5-year revenue CAGR on the TTM's basis; a lender's P/B, the industry's levered beta and its delivered
  return on equity (`delivered_roe`: the window in `valuation.yaml`, the structural breaks read with the rows).

**One basis** (decided by Claude, standing delegation, 1 Nov 2026, #119): the three years and the balance sheet must be
on the TTM's basis (consolidated where filed), else the model is held; the alternative, mixing bases, would set one
basis's margins against another's capital.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from ft.equity import inputs as inp
from ft.equity import metrics as mx
from ft.equity import statements as st
from ft.equity.valuation.model import Company, Input, Lender, Market, Settings

CONFIG = Path(__file__).resolve().parents[3] / "config" / "equity"
MARKET, CURRENCY = "IN", "INR"
MARKET_KEYS = ("shares_today", "market_cap", "beta_bottom_up", "ev", "pb")
YEAR_KEYS = ("revenue_cagr_3y", "revenue_cagr_5y", "roe")
FILINGS = "results filings (NSE)"
LEASE_MATERIAL = ("This company's lease payments are material against its EBIT, and results filings don't report "
                  "lease liabilities, so the model would understate its debt.")


# ------------------------------------------------------------------------------------------------ settings and rates
def _valuation_yaml(path: Path | None = None) -> dict:
    return yaml.safe_load((path or CONFIG / "valuation.yaml").read_text("utf-8"))


def load_settings(path: Path | None = None) -> Settings:
    return Settings.from_mapping(_valuation_yaml(path)["model"])


def marginal_tax(path: Path | None = None) -> Input:
    raw = _valuation_yaml(path)["india_marginal_tax_rate"]
    rate = Decimal(str(raw["rate"]))
    in_force = raw.get("in_force")
    if not Decimal(0) < rate < Decimal(1) or not raw.get("source") or not isinstance(in_force, date):
        raise ValueError("valuation.yaml: india_marginal_tax_rate needs a rate between 0 and 1, its source and the "
                         "date it's in force from")
    return Input.of(rate, "fraction", in_force, raw["source"])


def _pct(v: Decimal) -> Decimal:
    return v / 100


def market_inputs(db, as_of: date, path: Path | None = None) -> Market:
    """India's parameters on `as_of` (each a held input, with the reason, when its rows aren't stored)."""
    tax = marginal_tax(path)
    try:
        rf = inp.inr_risk_free(db, as_of)
        p = rf.parts
        month = date.fromisoformat(p["india_10y_month"])
        risk_free = Input.of(_pct(rf.value), "fraction", month,
                             "India 10-year government bond yield (FRED, OECD, monthly) less India's sovereign default "
                             "spread (Aswath Damodaran, NYU Stern, ctryprem)",
                             india_10y_pct=str(p["india_10y_pct"]), default_spread_pct=str(p["default_spread_pct"]),
                             spread_edition=p["spread_edition"])
        gross = Input.of(_pct(p["india_10y_pct"]), "fraction", month,
                         "India 10-year government bond yield (FRED, OECD, monthly)")
    except inp.InputsError:
        why = "India's 10-year yield or its default spread isn't stored."
        risk_free = Input.withheld(why, "fraction", "FRED India 10-year less Damodaran's default spread")
        gross = Input.withheld(why, "fraction", "FRED India 10-year")
    try:
        e = inp.india_erp(db, as_of)
        erp = Input.of(_pct(e.value), "fraction", date.fromisoformat(e.parts["mature_erp_month"]),
                       "Implied equity risk premium of a mature market plus India's country risk premium (Aswath "
                       "Damodaran, NYU Stern: ERPbymonth, ctryprem)",
                       mature_erp_pct=str(e.parts["mature_erp_pct"]), india_crp_pct=str(e.parts["india_crp_pct"]),
                       crp_edition=e.parts["crp_edition"])
    except inp.InputsError:
        erp = Input.withheld("India's equity risk premium isn't stored.", "fraction",
                             "Damodaran's implied ERP plus India's country risk premium")
    return Market(MARKET, CURRENCY, risk_free, gross, erp, tax)


# ------------------------------------------------------------------------------------------------ one company's rows
@dataclass
class Stored:
    company: mx.Company
    market: str
    currency: str
    fins: list[mx.Fin]
    metrics: dict[tuple, tuple] = field(default_factory=dict)   # (key, basis, kind, end) → (value, held, inputs)
    breaks: list[mx.Break] = field(default_factory=list)          # demergers and mergers, recorded and curated


def fetch(db, entity_ids: list[int] | None = None) -> dict[int, Stored]:
    """The rows `company_inputs` reads, for the given companies (all of `equity.company` when None)."""
    out: dict[int, Stored] = {}
    for eid, slug, family, fy, market, currency in db.execute(
            """select c.entity_id, e.slug, c.valuation_family, c.fy_end_month, e.market, e.currency
                 from equity.company c join core.entity e on e.id = c.entity_id
                where %s::int[] is null or c.entity_id = any(%s::int[]) order by e.slug""",
            (entity_ids, entity_ids)).fetchall():
        out[eid] = Stored(mx.Company(eid, slug, family, fy), market, currency, [])
    ids = list(out)
    cols = st.COLUMNS
    for row in db.execute(
            f"""select x.entity_id, x.document_id, x.basis, x.period_kind, x.period_start, x.period_end, x.tags,
                       fl.format, fl.filed_at, {", ".join(cols)}
                  from equity.financials x join equity.filing fl on fl.document_id = x.document_id
                 where x.entity_id = any(%s) and fl.parse_status = 'parsed'""", (ids,)).fetchall():
        eid, doc, basis, kind, start, end, tags, fmt, filed_at, *vals = row
        values = {c: v for c, v in zip(cols, vals, strict=True) if v is not None}
        out[eid].fins.append(mx.Fin(doc, basis, kind, start, end, values, tags or {}, fmt, filed_at))
    for eid, key, basis, kind, end, value, held, inputs in db.execute(
            """select entity_id, metric_key, basis, period_kind, period_end, value, held_reason, inputs
                 from equity.metric_value
                where entity_id = any(%s) and ((period_kind = 'market' and metric_key = any(%s))
                                               or (period_kind = 'year' and metric_key = any(%s)))""",
            (ids, list(MARKET_KEYS), list(YEAR_KEYS))).fetchall():
        out[eid].metrics[(key, basis, kind, end)] = (value, held, inputs or {})
    # structural breaks, as the metrics task reads them (2A-11): demergers and mergers in the corporate-action record,
    # and the curated rows of structural_breaks.yaml
    for eid, slug, kind, ex in db.execute(
            """select ev.entity_id, ev.slug, ca.kind, ca.ex_date
                 from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                where ev.entity_id = any(%s) and ev.status <> 'withdrawn' and ca.kind = any(%s)
                order by ca.ex_date, ev.slug""", (ids, list(mx.BREAK_KINDS))).fetchall():
        out[eid].breaks.append(mx.Break(kind, ex, slug))
    curated = mx.load_breaks()
    for s in out.values():
        s.breaks += curated.get(s.company.slug, [])
    return out


# ------------------------------------------------------------------------------------------------ the pure part
def _reason(h: mx.Held | str) -> str:
    r = h.reason if isinstance(h, mx.Held) else h
    return r.removeprefix("N/A: ")


def _figure(unit: str, source: str, fn, as_of: date | None = None) -> Input:
    """fn(calc) → value, with the lines it read as the detail; a Held becomes a held input."""
    c = mx.Calc()
    try:
        v = fn(c)
    except mx.Held as h:
        return Input.withheld(_reason(h), unit, source, as_of, **c.inputs())
    return Input.of(v, unit, as_of, source, **c.inputs())


def _stored(s: Stored, key: str, basis: str, kind: str, end: date | None, unit: str, source: str,
            missing: str) -> Input:
    row = s.metrics.get((key, basis, kind, end)) if end else None
    if row is None:
        return Input.withheld(missing, unit, source, end)
    value, held, inputs = row
    if held is not None:
        return Input.withheld(_reason(held), unit, source, end)
    return Input.of(value, unit, end, source, metric=key, **({"inputs": inputs} if inputs else {}))


class _Years:
    """The last three financial years on one basis, up to a date."""

    def __init__(self, book: mx.Book, basis: str, upto: date):
        ends = [e for e in book.ends("year") if e <= upto and book.get(basis, "year", e) is not None]
        self.rows = [book.get(basis, "year", e) for e in ends[-3:]]
        self.held = None
        if len(self.rows) < 3:
            self.held = "Fewer than three financial years are filed on one basis."
        elif any(mx.month_end(b.end, -12) != a.end for a, b in zip(self.rows, self.rows[1:], strict=False)):
            self.held = "The last three financial years aren't consecutive on one basis."

    def need(self) -> list[mx.Fin]:
        if self.held:
            raise mx.Held(self.held)
        return self.rows


@dataclass
class _Frame:
    """Where one company's figures are read: the price date, the TTM and its basis, the last three years."""
    book: mx.Book
    t: date | None                 # the price date: the newest market capitalisation stored on or before `as_of`
    upto: date
    q_end: date | None
    basis: str
    ttm_rows: list[mx.Fin] | None
    ttm_held: str | None
    years: _Years
    fy_end: date | None


def _frame(s: Stored, as_of: date) -> _Frame:
    book = mx.Book(s.fins)
    caps = [k[3] for k in s.metrics if k[0] == "market_cap" and k[2] == "market" and k[3] <= as_of]
    t = max(caps) if caps else None
    upto = t or as_of
    # TTM, and the basis every statement figure must share
    q_end = mx._latest(book, "quarter", upto)
    basis, ttm_rows, ttm_held = "consolidated", None, "No quarter is filed."
    if q_end is not None:
        try:
            basis, ttm_rows = mx.ttm(book, q_end)
            ttm_held = None
        except mx.Held as h:
            ttm_held = h.reason
    years = _Years(book, basis, upto)
    fy_end = years.rows[-1].end if years.rows else None
    return _Frame(book, t, upto, q_end, basis, ttm_rows, ttm_held, years, fy_end)


def company_inputs(s: Stored, as_of: date) -> Company:
    """The engine's inputs for one company at the newest price date on or before `as_of`."""
    fr = _frame(s, as_of)
    book, t, upto, q_end, basis = fr.book, fr.t, fr.upto, fr.q_end, fr.basis
    ttm_rows, ttm_held, years, fy_end = fr.ttm_rows, fr.ttm_held, fr.years, fr.fy_end

    def market(key, unit, source, missing):
        return _stored(s, key, "none", "market", t, unit, source, missing)

    shares = market("shares_today", "shares", "shares outstanding today: the latest filing's, after share actions "
                    "since (equity.metrics)", "Shares outstanding today aren't stored.")
    cap = market("market_cap", "money", "market capitalisation at the latest close (Source: NSE; equity.metrics)",
                 "Market capitalisation isn't stored.")
    beta = market("beta_bottom_up", "beta", "bottom-up beta: Damodaran's India industry beta relevered at market D/E "
                  "(equity.beta)", "The bottom-up beta isn't stored.")

    def ttm_sum(fn, name):
        def f(c):
            if ttm_rows is None:
                raise mx.Held(ttm_held)
            c.extra["basis"] = basis
            return mx._sum(c, ttm_rows, fn, name)
        return f

    revenue_ttm = _figure("money", f"revenue over the last four quarters ({FILINGS})",
                          ttm_sum(mx._col("revenue"), "revenue"), q_end)
    ebit_ttm = _figure("money", f"derived EBIT over the last four quarters ({FILINGS})",
                       ttm_sum(mx._ebit, "ebit_derived"), q_end)

    def three(fn, name):
        def f(c):
            c.extra["basis"] = basis
            return mx._sum(c, years.need(), fn, name)
        return f

    def opening_ic(c, f):
        return mx._opening_ic(c, book, basis, mx.month_end(f.end, -12))

    src = f"over the last three financial years ({FILINGS})"
    revenue_3fy = _figure("money", f"revenue {src}", three(mx._col("revenue"), "revenue"), fy_end)
    ebit_3fy = _figure("money", f"derived EBIT {src}", three(mx._ebit, "ebit_derived"), fy_end)
    tax_3fy = _figure("money", f"tax expense {src}", three(mx._col("tax"), "tax"), fy_end)
    pbt_3fy = _figure("money", f"profit before tax {src}", three(mx._col("pbt"), "pbt"), fy_end)
    capital_3fy = _figure("money", f"invested capital at the start of each of the last three financial years "
                          f"({FILINGS})", three(opening_ic, "opening_invested_capital"), fy_end)

    def latest_fy(c):
        return years.need()[-1]

    finance_cost = _figure("money", f"finance costs in the latest financial year ({FILINGS})",
                           lambda c: c.take(latest_fy(c), "finance_cost"), fy_end)
    debt_open = _figure("money", f"borrowings at the start of the latest financial year ({FILINGS}; "
                        f"{mx.LEASE_LABEL})",
                        lambda c: mx._debt(c, mx._instant(book, basis, mx.month_end(latest_fy(c).end, -12), "start")),
                        mx.month_end(fy_end, -12) if fy_end else None)
    debt_close = _figure("money", f"borrowings at the end of the latest financial year ({FILINGS}; {mx.LEASE_LABEL})",
                         lambda c: mx._debt(c, mx._instant(book, basis, latest_fy(c).end, "end")), fy_end)

    # the latest balance sheet, on the same basis
    bs_end = mx._latest(book, "instant", upto, where=mx._has_balance_sheet)
    bs_basis = book.basis_at("instant", bs_end, where=mx._has_balance_sheet) if bs_end else None

    def sheet(fn):
        def f(c):
            if bs_end is None:
                raise mx.Held("No balance sheet is filed.")
            if bs_basis != basis:
                raise mx.Held("The balance sheet and the last four quarters are on different bases.")
            return fn(c, book.get(basis, "instant", bs_end))
        return f

    debt = _figure("money", f"borrowings at the latest balance sheet ({FILINGS}; {mx.LEASE_LABEL})",
                   sheet(mx._debt), bs_end)
    cash = _figure("money", f"cash and investments at the latest balance sheet ({FILINGS})", sheet(mx._cash_inv),
                   bs_end)
    nci = _figure("money", f"non-controlling interests at the latest balance sheet ({FILINGS})",
                  sheet(lambda c, f: c.take(f, "nci")), bs_end)

    cagr = _stored(s, "revenue_cagr_3y", basis, "year", fy_end, "fraction",
                   "revenue growth a year over the last three financial years (equity.metrics)",
                   "The three-year revenue growth isn't stored.")

    held: list[str] = []
    if s.company.family == "operating" and t is not None and cap.value is not None:
        held += _lease(s, t)
    return Company(s.company.slug, s.market, s.currency, s.company.family, revenue_ttm, ebit_ttm, revenue_3fy,
                   ebit_3fy, tax_3fy, pbt_3fy, capital_3fy, cagr, finance_cost, debt_open, debt_close, debt, cash, nci,
                   shares, cap, beta, tuple(held))


def _lease(s: Stored, t: date) -> list[str]:
    """Anand's 30 Oct rule through the lease check stored with EV at the price date (equity.metrics)."""
    rows = [v for k, v in s.metrics.items() if k[0] == "ev" and k[2] == "market" and k[3] == t]
    check = (rows[0][2] or {}).get("lease_check") if rows else None
    if not check:
        return ["Lease payments couldn't be checked against EBIT."]
    if check.get("material"):
        return [LEASE_MATERIAL]
    if check.get("held"):
        return [check["held"]]
    return []


# ------------------------------------------------------------------------------------------------ 2A-14's inputs
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
CLOSE_SOURCE = "the latest close (Source: NSE; equity.metrics)"
ROE_SOURCE = ("return on equity (profit to owners ÷ average equity to owners), averaged over consecutive financial "
              "years ending with the latest (equity.metrics)")


@dataclass(frozen=True)
class DeliveredRoe:
    """The window of a lender's delivered return on equity (valuation.yaml's `delivered_roe`)."""
    years: int                     # at most this many financial years
    min_years: int                 # fewer: held


def load_delivered_roe(path: Path | None = None) -> DeliveredRoe:
    raw = _valuation_yaml(path).get("delivered_roe")
    if not isinstance(raw, dict) or not str(raw.get("source") or "").strip():
        raise ValueError("valuation.yaml: delivered_roe needs its years, min_years and a source")
    out = DeliveredRoe(int(raw["years"]), int(raw["min_years"]))
    if not 1 <= out.min_years <= out.years <= max(NUMBER_WORDS):
        raise ValueError("valuation.yaml: delivered_roe needs 1 ≤ min_years ≤ years ≤ 5")
    return out


def price(s: Stored, as_of: date) -> Input:
    """The latest close: the one the market capitalisation at the price date was computed at (equity.metrics)."""
    t = _frame(s, as_of).t
    rows = [v for k, v in s.metrics.items() if k[0] == "market_cap" and k[2] == "market" and k[3] == t]
    p = (rows[0][2] or {}).get("price") if rows else None
    if not p:
        return Input.withheld("The latest close isn't stored.", "money", CLOSE_SOURCE, t)
    return Input.of(Decimal(str(p["close"])), "money", date.fromisoformat(p["trade_date"]), CLOSE_SOURCE)


def delivered_growth(s: Stored, as_of: date) -> Input:
    """What an operating company has delivered (Spec 02A §5.9): the 5-year revenue CAGR at the latest financial year,
    on the basis of its last four quarters (the reverse DCF's), with the metric's own held reason (2A-11: the
    structural-break rule, a window without filed years)."""
    fr = _frame(s, as_of)
    got = _stored(s, "revenue_cagr_5y", fr.basis, "year", fr.fy_end, "fraction",
                  "revenue growth a year over the last five financial years (equity.metrics)",
                  "The five-year revenue growth isn't stored.")
    return replace(got, detail={**got.detail, "years": 5, "basis": fr.basis})


def _pb_basis(s: Stored, fr: _Frame) -> str:
    keys = [k for k in s.metrics if k[0] == "pb" and k[2] == "market" and k[3] == fr.t]
    return keys[0][1] if keys else fr.basis


def delivered_roe(s: Stored, as_of: date, rule: DeliveredRoe | None = None) -> Input:
    """What a lender has delivered (Spec 02A §5.9): the average of its financial-year return on equity (2A-11's `roe`)
    on the basis of its P/B, over the consecutive years, up to `rule.years`, that end with the latest year stored on
    or before the price date and have a value; held below `rule.min_years`, or when a demerger or merger falls inside
    the years averaged (2A-11's structural-break rule, applied from the first averaged year's first day). The window
    is decided by Claude (standing delegation, 1 Nov 2026, #120), as Anand's note of 30 Oct on #120 asked: results
    filings carry balance sheets only from March 2023 (banks' from March 2024; #114), so five years aren't filed yet."""
    rule = rule or load_delivered_roe()
    fr = _frame(s, as_of)
    basis = _pb_basis(s, fr)
    ends = sorted(k[3] for k in s.metrics if k[0] == "roe" and k[1] == basis and k[2] == "year" and k[3] <= fr.upto)
    if not ends:
        return Input.withheld("No financial year's return on equity is stored.", "fraction", ROE_SOURCE,
                              basis=basis)
    last = ends[-1]
    used: list[tuple[date, Decimal]] = []
    for k in range(rule.years):
        row = s.metrics.get(("roe", basis, "year", mx.month_end(last, -12 * k)))
        if row is None or row[0] is None or row[1] is not None:
            break
        used.append((mx.month_end(last, -12 * k), row[0]))
    used.reverse()
    detail = {"basis": basis, "n": len(used), "years": [{"period_end": e.isoformat(), "roe": str(v)} for e, v in used]}
    if used:
        start = mx.month_end(used[0][0], -12) + timedelta(days=1)            # the first averaged year's first day
        inside = [b for b in s.breaks if start <= b.effective <= last]
        if inside:
            detail["structural_breaks"] = [{"kind": b.kind, "effective": b.effective.isoformat(), "source": b.source,
                                            "curated": b.curated} for b in inside]
            kinds = sorted({b.kind for b in inside})
            return Input.withheld(f"The window spans a {' and a '.join(kinds)}, so its years aren't comparable.",
                                  "fraction", ROE_SOURCE, last, **detail)
    if len(used) < rule.min_years:
        return Input.withheld(f"Fewer than {NUMBER_WORDS[rule.min_years]} consecutive financial years of return on "
                              f"equity are filed, so its average isn't shown.", "fraction", ROE_SOURCE, last, **detail)
    average = (sum(v for _, v in used) / len(used)).quantize(mx.PLACES)
    return Input.of(average, "fraction", last, ROE_SOURCE, **detail)


def lender_inputs(s: Stored, as_of: date, rule: DeliveredRoe | None = None) -> Lender:
    """A lender's implied-ROE inputs at the newest price date on or before `as_of`: P/B (2A-11), the industry's levered
    beta (2A-12 stores it as `beta_bottom_up` for lenders), the delivered average and the latest close."""
    fr = _frame(s, as_of)
    pb = _stored(s, "pb", _pb_basis(s, fr), "market", fr.t, "multiple",
                 "price-to-book: market capitalisation ÷ equity to owners at the latest balance sheet (Source: NSE; "
                 "equity.metrics)", "Price-to-book isn't stored.")
    beta = _stored(s, "beta_bottom_up", "none", "market", fr.t, "beta",
                   "the industry's levered beta: Damodaran's India industry beta, not relevered (equity.beta)",
                   "The industry beta isn't stored.")
    return Lender(s.company.slug, s.market, s.currency, s.company.family, pb, beta, delivered_roe(s, as_of, rule),
                  price(s, as_of))
