"""Derived metrics of the US companies (Spec 02A.1 §5.4, Spec 02A §5.6; job US-04, #403): India's metrics
(`ft/equity/metrics.py`) with the US lines of `ft/equity/us_inputs.py`, as `equity.metric_value` rows under the same
catalogue keys (Spec 02A.1 §4 item 6: the catalogue is unchanged). Pure: the task (`equity_us_metrics.py`) reads the rows
and writes the values. India's module is reused, not changed: its periods, TTM, growth, CAGR, ROE, free cash flow,
dividend yield and one-year return apply as they are; what differs is here.

**What differs from India's**
- EBIT is operating income as filed, stored under `ebit_derived` (the catalogue's key; its inputs name
  `OperatingIncomeLoss`, and the US views label it as filed: Spec 02A.1 §6). A company that files no operating income
  at all has EBIT, its margin, return on capital and EV/EBIT as N/A (Data Audit 02 §11.1 reading 2).
- Debt, cash and invested capital use the US lines (borrowings as filed, cash and cash equivalents as filed), each
  with its label; there is no lease check (`us_inputs`'s docstring says why).
- Shares today: the newest cover page's figure, after the split, bonus and consolidation factors with ex-dates after
  the cover page's own date (its tag gives it: a cover page is dated weeks after the period end), not the period end.
  A held share count (Visa's classes) holds every market figure that needs it.
- Structural breaks: a price adjustment Yahoo lists as a split that isn't a simple ratio (`us.corporate_actions`'
  kind `other`: Merck's, IBM's, 3M's and Honeywell's spin-offs) is a break for the revenue CAGRs, as India's demergers
  are (decided by Claude, standing delegation, 12 Nov 2026, #403: it only holds values back).
- Banks' ratios aren't computed (they are India's standalone bank format).
- The median traded value is N/A: the US price source gives volume, not traded value.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from ft.equity import metrics as mx
from ft.equity import us_inputs as ui
from ft.equity.us_inputs import UsCalc

EBIT_KEYS = ("ebit_derived", "ebit_margin", "roic", "ev_ebit_ttm")
NO_TRADED_VALUE = "N/A: The US price source gives volume, not traded value."
SPIN_OFF = "spin-off"
COVER_DATE = re.compile(r"as of (\d{4}-\d{2}-\d{2})")


class UsOut(mx.Out):
    """India's `Out` with the US calc (a line the adapter held is a held value with its sentence)."""

    def put(self, key: str, basis: str, kind: str, end: date, fn) -> Decimal | None:
        c = UsCalc()
        try:
            v = fn(c)
        except mx.Held as h:
            self.values[(key, basis, kind, end)] = mx.Value(key, basis, kind, end, None, h.reason, c.inputs())
            return None
        self.values[(key, basis, kind, end)] = mx.Value(key, basis, kind, end, v, None, c.inputs())
        return v


def breaks(actions: list[mx.Action]) -> list[mx.Break]:
    """The US structural breaks in the corporate-action record: Yahoo's non-ratio 'splits' (spin-offs)."""
    return [mx.Break(SPIN_OFF, a.ex_date, a.slug) for a in actions if a.kind == "other"]


def compute(d: mx.Data) -> list[mx.Value]:
    """Every metric of one US company, sorted as India's."""
    out = UsOut()
    book = mx.Book(d.fins)
    na = mx.NON_OPERATING.get(d.company.family)
    ebit_na = None if any(f.values.get("operating_income") is not None for f in d.fins) else ui.NO_OPERATING_INCOME
    _flows(out, book, na, ebit_na)
    _years(out, book, d, na, ebit_na)
    _instants(out, book, na)
    _market(out, book, d, na, ebit_na)
    return sorted(out.values.values(), key=lambda v: (v.key, v.period_kind, v.period_end, v.basis))


def _ebit_sum(c, rows):
    return mx._sum(c, rows, ui.ebit, "ebit_derived")


def _flow_metrics(out: UsOut, kind: str, end: date, basis: str, rows: list[mx.Fin] | None, held: str | None,
                  na: str | None, ebit_na: str | None) -> None:
    def run(key, fn):
        if na and key in mx.OPERATING_ONLY:
            out.na(key, basis, kind, end, na)
            return
        if ebit_na and key in EBIT_KEYS:
            out.na(key, basis, kind, end, ebit_na)
            return
        if held:
            out.na(key, basis, kind, end, held)
            return
        out.put(key, basis, kind, end, lambda c: fn(c, rows))

    def margin(num):
        def f(c, rows):
            rev = mx._sum(c, rows, mx._col("revenue"), "revenue")
            top = num(c, rows)
            if rev <= 0:
                raise mx.Held("Revenue is zero or negative.")
            return mx.ratio(top, rev)
        return f

    def tax(c, rows):
        t, p = mx._sum(c, rows, mx._col("tax"), "tax"), mx._sum(c, rows, mx._col("pbt"), "pbt")
        if p <= 0:
            raise mx.Held("Profit before tax is zero or negative.")
        return mx.ratio(t, p)

    if kind == "ttm":
        run("revenue", lambda c, rows: mx._sum(c, rows, mx._col("revenue"), "revenue"))
        run("pat_owners", lambda c, rows: mx._sum(c, rows, mx._col("pat_owners"), "pat_owners"))
    run("ebit_derived", _ebit_sum)
    run("ebit_margin", margin(_ebit_sum))
    run("net_margin", margin(lambda c, rows: mx._sum(c, rows, mx._col("pat_owners"), "pat_owners")))
    run("effective_tax", tax)


def _flows(out: UsOut, book: mx.Book, na: str | None, ebit_na: str | None) -> None:
    for kind in ("quarter", "year"):
        for end in book.ends(kind):
            basis = book.basis_at(kind, end)
            _flow_metrics(out, kind, end, basis, [book.get(basis, kind, end)], None, na, ebit_na)
    for end in book.ends("quarter"):
        try:
            basis, rows = mx.ttm(book, end)
            _flow_metrics(out, "ttm", end, basis, rows, None, na, ebit_na)
        except mx.Held as h:
            _flow_metrics(out, "ttm", end, book.basis_at("quarter", end), None, h.reason, na, ebit_na)


def _sales_to_capital(c, book, basis, fy, opening):
    rev = c.take(fy, "revenue")
    return mx.ratio(rev, ui.opening_ic(c, book, basis, opening))


def _roic(c, book, basis, fy, opening):
    e, tax, pbt = ui.ebit(c, fy), c.take(fy, "tax"), c.take(fy, "pbt")
    if pbt <= 0:
        raise mx.Held("Profit before tax is zero or negative, so the tax rate isn't defined.")
    return mx.ratio(e * (1 - tax / pbt), ui.opening_ic(c, book, basis, opening))


def _years(out: UsOut, book: mx.Book, d: mx.Data, na: str | None, ebit_na: str | None) -> None:
    for kind in ("quarter", "year"):
        for end in book.ends(kind):
            mx._growth(out, book, "revenue_yoy", "revenue", kind, end)
            mx._growth(out, book, "pat_owners_yoy", "pat_owners", kind, end)
    brk = sorted(d.breaks, key=lambda b: b.effective)
    for end in book.ends("year"):
        basis = book.basis_at("year", end)
        fy = book.get(basis, "year", end)
        opening = mx.month_end(end, -12)
        args = (book, basis, fy)
        for n, key in ((3, "revenue_cagr_3y"), (5, "revenue_cagr_5y")):
            out.put(key, basis, "year", end, lambda c, n=n, a=args: mx._cagr(c, *a, n, brk))
        out.put("roe", basis, "year", end, lambda c, a=args, o=opening: mx._roe(c, *a, o))
        for key, fn in (("sales_to_capital", _sales_to_capital), ("roic", _roic)):
            if na:
                out.na(key, basis, "year", end, na)
            elif ebit_na and key in EBIT_KEYS:
                out.na(key, basis, "year", end, ebit_na)
            else:
                out.put(key, basis, "year", end, lambda c, fn=fn, a=args, o=opening: fn(c, *a, o))
    mx._fcf(out, book, na)


def _instants(out: UsOut, book: mx.Book, na: str | None) -> None:
    for end in book.ends("instant", where=mx._has_balance_sheet):
        basis = book.basis_at("instant", end, where=mx._has_balance_sheet)
        f = book.get(basis, "instant", end)
        if na:
            for key in ("debt", "cash_and_investments", "invested_capital", "debt_to_equity"):
                out.na(key, basis, "instant", end, na)
            continue
        out.put("debt", basis, "instant", end, lambda c, f=f: ui.debt(c, f))
        out.put("cash_and_investments", basis, "instant", end, lambda c, f=f: ui.cash(c, f))
        out.put("invested_capital", basis, "instant", end, lambda c, f=f: ui.invested(c, f))

        def de(c, f=f):
            eq = c.take(f, "total_equity")
            if eq <= 0:
                raise mx.Held("Total equity is zero or negative.")
            return mx.ratio(ui.debt(c, f), eq)
        out.put("debt_to_equity", basis, "instant", end, de)


def cover_date(f: mx.Fin) -> date:
    """The cover page's date in the shares' tag (`... cover page, as of 2026-10-17 ...`), else the period end."""
    m = COVER_DATE.search(str(f.tags.get("shares_outstanding") or ""))
    d = date.fromisoformat(m.group(1)) if m else f.end
    return max(d, f.end)


def _has_shares(f: mx.Fin) -> bool:
    return ui._filed_or_held(f, "shares_outstanding")


def _market(out: UsOut, book: mx.Book, d: mx.Data, na: str | None, ebit_na: str | None) -> None:
    if not d.prices:
        return
    p = d.prices[-1]
    t = p.trade_date
    price = {"trade_date": t.isoformat(), "close": str(p.close)}
    M = "market"

    def shares(c):
        """The newest cover page's shares ÷ the share actions since its date."""
        rows = [f for f in book.rows.values() if f.end <= t and _has_shares(f)]
        if not rows:
            raise mx.Held("No filing reports shares outstanding.")
        f = max(rows, key=lambda f: (f.end, f.basis == "consolidated", f.kind == "instant", f.order))
        filed = c.take(f, "shares_outstanding")                   # a held count raises its sentence
        mx._shares_agree(c, d, [g for g in rows if g.values.get("shares_outstanding")], f)
        after = cover_date(f)
        prod, used = mx._factor_span(d, after, t, "A corporate action since the last cover page is under review.")
        c.extra["factors"] = used
        c.extra["cover_date"] = after.isoformat()
        return (filed / prod).quantize(Decimal(1))

    n_shares = out.put("shares_today", "none", M, t, shares)
    sh_held = out.held("shares_today", "none", M, t)

    def mcap(c):
        if n_shares is None:
            raise mx.Held(sh_held)
        c.extra["price"] = price
        c.extra["shares_today"] = str(n_shares)
        return p.close * n_shares

    cap = out.put("market_cap", "none", M, t, mcap)
    cap_held = out.held("market_cap", "none", M, t)

    q_end = mx._latest(book, "quarter", t)
    tt_basis, tt_rows, tt_held = "consolidated", None, "No quarter is filed."
    if q_end is not None:
        try:
            tt_basis, tt_rows = mx.ttm(book, q_end)
            tt_held = None
        except mx.Held as h:
            tt_basis, tt_held = book.basis_at("quarter", q_end) or "consolidated", h.reason
    bs_end = mx._latest(book, "instant", t, where=mx._has_balance_sheet)
    bs_basis = book.basis_at("instant", bs_end, where=mx._has_balance_sheet) if bs_end else "consolidated"
    bs = book.get(bs_basis, "instant", bs_end) if bs_end else None

    def need_cap(c):
        if cap is None:
            raise mx.Held(cap_held)
        c.extra["market_cap"] = {"value": str(cap), **price, "shares_today": str(n_shares)}

    def need_ttm():
        if tt_rows is None:
            raise mx.Held(tt_held)
        return tt_rows

    def need_bs():
        if bs is None:
            raise mx.Held("No balance sheet is filed.")
        return bs

    def pe(c):
        need_cap(c)
        pat = mx._sum(c, need_ttm(), mx._col("pat_owners"), "pat_owners")
        if pat <= 0:
            raise mx.Held("Loss-making: profit to owners over the last four quarters isn't positive.")
        return mx.ratio(cap, pat)
    out.put("pe_ttm", tt_basis, M, t, pe)

    def pb(c):
        need_cap(c)
        eq = c.take(need_bs(), "equity_owners")
        if eq <= 0:
            raise mx.Held("Equity to owners is zero or negative.")
        return mx.ratio(cap, eq)
    out.put("pb", bs_basis, M, t, pb)

    if na:
        for key in ("ev", "ev_ebit_ttm", "ev_sales_ttm"):
            out.na(key, bs_basis, M, t, na)
    else:
        def ev(c):
            need_cap(c)
            f = need_bs()
            value = cap + ui.debt(c, f) + c.take(f, "nci") - ui.cash(c, f)
            c.extra["label"] = f"{ui.DEBT_LABEL}; {ui.CASH_LABEL}"
            return value
        e = out.put("ev", bs_basis, M, t, ev)
        ev_held = out.held("ev", bs_basis, M, t)

        def over(name, fn, nonpos):
            def f(c):
                if e is None:
                    raise mx.Held(ev_held)
                rows = need_ttm()
                if tt_basis != bs_basis:
                    raise mx.Held("The balance sheet and the last four quarters are on different bases.")
                c.extra["ev"] = str(e)
                den = mx._sum(c, rows, fn, name)
                if den <= 0:
                    raise mx.Held(nonpos)
                return mx.ratio(e, den)
            return f
        if ebit_na:
            out.na("ev_ebit_ttm", bs_basis, M, t, ebit_na)
        else:
            out.put("ev_ebit_ttm", bs_basis, M, t, over("ebit_derived", ui.ebit,
                                                        "Operating income over the last four quarters is zero or "
                                                        "negative."))
        out.put("ev_sales_ttm", bs_basis, M, t, over("revenue", mx._col("revenue"),
                                                      "Revenue over the last four quarters is zero or negative."))

    out.put("dividend_yield", "none", M, t, lambda c: mx._dividend_yield(c, d, p))
    out.put("return_1y", "none", M, t, lambda c: mx._return_1y(c, d))
    out.na("traded_value_median_3m", "none", M, t, NO_TRADED_VALUE)


def ebit_input_errors(values: list[mx.Value]) -> list[str]:
    """(B) every US EBIT is operating income as filed: each of its periods read `operating_income` and nothing else."""
    bad = []
    for v in values:
        if v.key != "ebit_derived" or v.value is None:
            continue
        rows = v.inputs.get("financials") or []
        want = 4 if v.period_kind == "ttm" else 1
        if len(rows) != want or any(set(r["lines"]) != {"operating_income"} for r in rows):
            bad.append(f"{v.basis} {v.period_kind} {v.period_end.isoformat()}")
    return bad
