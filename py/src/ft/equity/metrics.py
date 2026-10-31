"""Derived metrics (Spec 02A §5.6; job 2A-11): each company's TTM figures, margins, returns, growth, banks' ratios and
market multiples from its stored statements, prices and corporate actions, as `equity.metric_value` rows. Pure: the
task (`ft/jobs/tasks/equity_metrics.py`) reads the rows and writes the values; nothing here touches the database.

Every value carries `inputs`: the `equity.financials` lines it used (document id, period, element, value), and the
prices and factors for the market figures. A value that can't be computed carries `held_reason` instead, a plain
sentence a visitor can read (no task names or internals, CLAUDE.md §4; no dates or figures, which the pages format);
a metric that doesn't apply starts its reason with `N/A: ` (the views turn that prefix into a state, 2A-17).

**Periods** (`metric_value.period_kind`): `quarter` and `year` from a filing's own quarter and full-year columns;
`ttm`, the latest four consecutive quarters on one basis (each as filed in its own filing; a revised filing replaces
the earlier one for its period); `instant`, a balance sheet; `market`, as of a price date. Cash flow is filed only for
half-years and years, so TTM cash flow is FY − the previous H1 + the current H1 at a half-year (and the FY at a year
end).

**Basis:** consolidated, except banks' ratios (standalone, as filed there). A period a company files no consolidated
statement for uses its standalone one, with `basis` stored (decided by Claude, standing delegation, on #117).

**Owners' figures where a filing has no line for them** (decided by Claude, standing delegation, 31 Oct 2026, #117:
the same quantity, not another line): on the standalone basis there are no non-controlling interests, so profit to
owners is the profit for the period and equity to owners is total equity; in the bank format, capital plus reserves
(the parser's total equity) is the owners' equity, with minority interest shown outside it. Each is noted in the
inputs. Anywhere else a missing line holds the value back.

**Debt** is borrowings only, labelled "excluding lease liabilities (not in results filings)" (Anand, 30 Oct 2026, on
#117: no results filing tags lease liabilities). EV-based figures are held back where the filed lease payments are
material against EBIT over the same period (`py/config/equity/metrics.yaml`).
"""
from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from ft.equity import statements as st

CONFIG = Path(__file__).resolve().parents[3] / "config" / "equity"
PLACES = Decimal("1e-12")          # ratios and multiples are stored to 12 places; money and shares as computed
ZERO = Decimal(0)
LEASE_LABEL = "excluding lease liabilities (not in results filings)"
SHARE_KINDS = ("split", "bonus", "consolidation")
BREAK_KINDS = ("demerger", "merger")
TTM_SUMS = ("revenue", "pat_owners", "ebit_derived", "nii")      # TTM values that are sums of their quarters
NON_OPERATING = {"lender": "N/A: not meaningful for a lender", "insurer": "N/A: not meaningful for an insurer"}
# the metrics Brief §6.3 and the card mark not meaningful for lenders and insurers
OPERATING_ONLY = ("ebit_derived", "ebit_margin", "invested_capital", "sales_to_capital", "roic", "debt",
                  "cash_and_investments", "debt_to_equity", "fcf", "ev", "ev_ebit_ttm", "ev_sales_ttm")
FORMAT_NAMES = {"ind_as": "Ind AS", "bank": "Bank", "nbfc": "NBFC", "insurer_life": "Life insurer",
                "insurer_general": "General insurer"}
LINES = {
    "revenue": "revenue from operations", "pat_owners": "profit attributable to owners", "pat": "profit for the period",
    "pbt_before_exceptional": "profit before exceptional items and tax", "finance_cost": "finance costs",
    "other_income": "other income", "share_of_associates": "the share of profit of associates", "tax": "tax expense",
    "pbt": "profit before tax", "total_equity": "total equity", "equity_owners": "equity attributable to owners",
    "borrowings_current": "current borrowings", "borrowings_noncurrent": "non-current borrowings",
    "cash": "cash and cash equivalents", "bank_balances_other": "other bank balances",
    "current_investments": "current investments", "noncurrent_investments": "non-current investments",
    "nci": "non-controlling interests", "cfo": "cash flow from operations", "capex": "capital expenditure",
    "interest_earned": "interest earned", "interest_expended": "interest expended", "roa_filed": "return on assets",
    "segment_assets_total": "total segment assets", "gnpa_ratio": "the gross NPA ratio",
    "nnpa_ratio": "the net NPA ratio", "cet1_ratio": "the CET1 ratio", "at1_ratio": "the additional tier 1 ratio",
    "shares_outstanding": "shares outstanding",
}
# lines a balance sheet that has them all omits when there is nothing to report: taken as none, noted
ABSENT_IS_NONE = frozenset({"share_of_associates", "bank_balances_other", "current_investments",
                            "noncurrent_investments", "nci"})


# ---------------------------------------------------------------------------------------- inputs
@dataclass(frozen=True)
class Fin:
    """One `equity.financials` row with its filing's format and dissemination time."""
    document_id: int
    basis: str
    kind: str                      # quarter | half_year | nine_months | year | instant
    start: date | None
    end: date
    values: dict
    tags: dict
    format: str
    filed_at: datetime | None = None

    @property
    def order(self) -> tuple:
        """Latest filed last (a revised filing replaces the earlier one for its period)."""
        return (self.filed_at is not None, self.filed_at.timestamp() if self.filed_at else 0.0, self.document_id)


@dataclass(frozen=True)
class Company:
    entity_id: int
    slug: str
    family: str                    # operating | lender | insurer | mixed
    fy_end_month: int = 3


@dataclass(frozen=True)
class Price:
    trade_date: date
    close: Decimal
    adj_close: Decimal | None      # null while an earlier action is held (equity.price_adjusted)
    traded_value: Decimal | None


@dataclass(frozen=True)
class Action:
    """A corporate action (not withdrawn) with its factor, if it has one."""
    slug: str
    kind: str
    ex_date: date
    dps: Decimal | None = None
    factor: Decimal | None = None
    status: str | None = None      # applied | held | None (no factor row)


@dataclass(frozen=True)
class Break:
    """A structural break: a demerger or merger in the record, or a curated row (structural_breaks.yaml)."""
    kind: str
    effective: date
    source: str                    # the event slug, or the curated row's announcement
    curated: bool = False


@dataclass(frozen=True)
class Lease:
    """A filing's lease payments over its year to date (None: the filing doesn't tag them; `unreadable`: the stored
    file couldn't be read again, so the check can't be made)."""
    value: Decimal | None
    element: str | None = None
    unreadable: bool = False


@dataclass
class Data:
    company: Company
    fins: list[Fin]
    prices: list[Price] = field(default_factory=list)          # ascending by date
    actions: list[Action] = field(default_factory=list)
    breaks: list[Break] = field(default_factory=list)
    leases: dict[int, Lease] = field(default_factory=dict)       # document id -> its lease payments


@dataclass(frozen=True)
class Rules:
    lease_threshold: Decimal       # lease payments ÷ EBIT at or above this hold EV-based figures back

    @classmethod
    def load(cls, path: Path | None = None) -> Rules:
        raw = yaml.safe_load((path or CONFIG / "metrics.yaml").read_text("utf-8"))
        t = raw.get("lease_materiality", {}) if isinstance(raw, dict) else {}
        if not isinstance(t, dict) or "threshold" not in t or not str(t.get("reason") or "").strip():
            raise ValueError("metrics.yaml: lease_materiality needs a threshold and its reason")
        v = Decimal(str(t["threshold"]))
        if not 0 < v < 1:
            raise ValueError(f"metrics.yaml: lease_materiality.threshold {v} isn't a fraction between 0 and 1")
        return cls(v)


@dataclass(frozen=True)
class Value:
    key: str
    basis: str                     # consolidated | standalone | none
    period_kind: str               # quarter | year | ttm | instant | market
    period_end: date
    value: Decimal | None
    held: str | None
    inputs: dict


def load_breaks(path: Path | None = None) -> dict[str, list[Break]]:
    """The curated structural breaks by company slug. Every row names its evidence; a malformed row fails the run."""
    p = path or CONFIG / "structural_breaks.yaml"
    raw = yaml.safe_load(p.read_text("utf-8")) or {}
    rows = raw.get("breaks") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise TypeError(f"{p.name}: a top-level `breaks` list is required")
    out: dict[str, list[Break]] = {}
    for i, r in enumerate(rows):
        need = ("company", "kind", "effective", "announcement", "announced", "link")
        if not isinstance(r, dict) or any(not str(r.get(k) or "").strip() for k in need):
            raise ValueError(f"{p.name} breaks[{i}]: needs {', '.join(need)}")
        if r["kind"] not in BREAK_KINDS:
            raise ValueError(f"{p.name} breaks[{i}]: kind is {', '.join(BREAK_KINDS)}")
        eff = r["effective"] if isinstance(r["effective"], date) else date.fromisoformat(str(r["effective"]))
        out.setdefault(str(r["company"]), []).append(
            Break(r["kind"], eff, f"{r['announcement']} ({r['announced']}; {r['link']})", curated=True))
    return out


# ---------------------------------------------------------------------------------------- periods and lines
def month_end(d: date, months: int = 0) -> date:
    """The month end `months` after d's month."""
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, calendar.monthrange(y, m + 1)[1])


def year_before(d: date) -> date:
    return d.replace(year=d.year - 1) if not (d.month == 2 and d.day == 29) else date(d.year - 1, 2, 28)


class Held(Exception):
    """A value that can't be computed; `reason` is the visitor's sentence."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Book:
    """A company's financials rows, the latest filed for each basis, period kind and period end."""

    def __init__(self, fins: list[Fin]):
        self.rows: dict[tuple[str, str, date], Fin] = {}
        for f in fins:
            k = (f.basis, f.kind, f.end)
            if k not in self.rows or f.order > self.rows[k].order:
                self.rows[k] = f

    def get(self, basis: str, kind: str, end: date) -> Fin | None:
        return self.rows.get((basis, kind, end))

    def ends(self, kind: str, *, where=None) -> list[date]:
        return sorted({e for (_, k, e), f in self.rows.items() if k == kind and (where is None or where(f))})

    def basis_at(self, kind: str, end: date, *, where=None) -> str | None:
        """consolidated where the company filed it for the period, else standalone (stored as the value's basis)."""
        for b in ("consolidated", "standalone"):
            f = self.get(b, kind, end)
            if f is not None and (where is None or where(f)):
                return b
        return None


def carries(fmt: str, col: str) -> bool:
    """Whether the format's element map has a line for the column (the parser can fill it)."""
    sec = st.section(fmt)
    return any(col in sec.get(part, {}) for part in ("duration", "cash_flow", "instant", "ratios")) \
        or col in ("paid_up_capital", "face_value", "shares_outstanding")


class Calc:
    """One value's computation: every line it reads is recorded as an input; a line not filed raises Held."""

    def __init__(self):
        self.lines: dict[tuple, dict] = {}
        self.extra: dict = {}

    def take(self, f: Fin, col: str) -> Decimal:
        got = owners_line(f, col)
        if got is None and col in ABSENT_IS_NONE:
            got = ZERO, "not filed: taken as none"
        if got is None:
            if not carries(f.format, col) and col not in ("pat_owners", "equity_owners"):
                raise Held(f"N/A: {FORMAT_NAMES.get(f.format, f.format)} filings don't report {LINES.get(col, col)}.")
            raise Held(f"The filing doesn't report {LINES.get(col, col)}.")
        v, element = got
        row = self.lines.setdefault((f.document_id, f.kind, f.end), {
            "document_id": f.document_id, "basis": f.basis, "period_kind": f.kind, "period_end": f.end.isoformat(),
            "lines": {}})
        row["lines"][col] = {"value": str(v), "element": element}
        return v

    def inputs(self, **more) -> dict:
        out = {"financials": list(self.lines.values()), **self.extra, **more}
        return {k: v for k, v in out.items() if v not in (None, [], {})}


def owners_line(f: Fin, col: str) -> tuple[Decimal, str] | None:
    v = f.values.get(col)
    if v is not None:
        return v, f.tags.get(col) or col
    if col == "pat_owners" and f.basis == "standalone" and f.values.get("pat") is not None:
        return f.values["pat"], f"{f.tags.get('pat') or 'pat'} (standalone: no non-controlling interests)"
    if col == "equity_owners" and f.values.get("total_equity") is not None:
        why = ("standalone: no non-controlling interests" if f.basis == "standalone" else
               "bank format: capital and reserves; minority interest is shown outside them" if f.format == "bank"
               else None)
        if why:
            return f.values["total_equity"], f"{f.tags.get('total_equity') or 'total_equity'} ({why})"
    return None


def ratio(a: Decimal, b: Decimal) -> Decimal:
    return (a / b).quantize(PLACES)


# ---------------------------------------------------------------------------------------- one company
class Out:
    def __init__(self):
        self.values: dict[tuple, Value] = {}

    def put(self, key: str, basis: str, kind: str, end: date, fn) -> Decimal | None:
        """Run fn(calc) -> value; a Held becomes the held reason. Returns the value (None if held)."""
        c = Calc()
        try:
            v = fn(c)
        except Held as h:
            self.values[(key, basis, kind, end)] = Value(key, basis, kind, end, None, h.reason, c.inputs())
            return None
        self.values[(key, basis, kind, end)] = Value(key, basis, kind, end, v, None, c.inputs())
        return v

    def na(self, key: str, basis: str, kind: str, end: date, reason: str) -> None:
        """A held value with no inputs: N/A for the company's family, or a reason found before any line was read."""
        self.values[(key, basis, kind, end)] = Value(key, basis, kind, end, None, reason, {})

    def held(self, key: str, basis: str, kind: str, end: date) -> str | None:
        return self.values[(key, basis, kind, end)].held


def compute(d: Data, rules: Rules) -> list[Value]:
    """Every metric of one company, sorted by key, period kind, period end and basis."""
    out = Out()
    book = Book(d.fins)
    na = NON_OPERATING.get(d.company.family)
    _flows(out, book, na)
    _years(out, book, d, na)
    _instants(out, book, na)
    _banks(out, book)
    _market(out, book, d, rules, na)
    return sorted(out.values.values(), key=lambda v: (v.key, v.period_kind, v.period_end, v.basis))


def _ebit(c: Calc, f: Fin) -> Decimal:
    return (c.take(f, "pbt_before_exceptional") + c.take(f, "finance_cost") - c.take(f, "other_income")
            - c.take(f, "share_of_associates"))


def _nii(c: Calc, f: Fin) -> Decimal:
    return c.take(f, "interest_earned") - c.take(f, "interest_expended")


def _sum(c: Calc, rows: list[Fin], fn, name: str) -> Decimal:
    """fn over the rows (one row, or a TTM's four quarters), summed; a TTM records each quarter's part."""
    parts = [fn(c, f) for f in rows]
    if len(rows) > 1:
        c.extra.setdefault("quarters", {})[name] = [
            {"document_id": f.document_id, "period_end": f.end.isoformat(), "value": str(p)}
            for f, p in zip(rows, parts, strict=True)]
    return sum(parts, ZERO)


def _col(col: str):
    return lambda c, f: c.take(f, col)


def ttm(book: Book, end: date, *, where=None) -> tuple[str, list[Fin]]:
    """The four consecutive quarters ending at `end` on one basis (the end's own basis), or Held."""
    basis = book.basis_at("quarter", end, where=where)
    if basis is None:
        raise Held("No quarter is filed for this period.")
    qs = [book.get(basis, "quarter", month_end(end, -3 * i)) for i in range(3, -1, -1)]
    if any(q is None or (where is not None and not where(q)) for q in qs):
        raise Held("Fewer than four consecutive quarters are filed on one basis.")
    return basis, qs


def _flow_metrics(out: Out, kind: str, end: date, basis: str, rows: list[Fin] | None, held: str | None,
                  na: str | None) -> None:
    """ebit_derived, ebit_margin, net_margin and effective_tax for one period (and revenue, pat_owners for TTM)."""
    def run(key, fn):
        if na and key in OPERATING_ONLY:
            out.na(key, basis, kind, end, na)
            return
        if held:
            out.na(key, basis, kind, end, held)
            return
        out.put(key, basis, kind, end, lambda c: fn(c, rows))

    def margin(num):
        def f(c, rows):
            rev = _sum(c, rows, _col("revenue"), "revenue")
            top = num(c, rows)
            if rev <= 0:
                raise Held("Revenue is zero or negative.")
            return ratio(top, rev)
        return f

    def tax(c, rows):
        t, p = _sum(c, rows, _col("tax"), "tax"), _sum(c, rows, _col("pbt"), "pbt")
        if p <= 0:
            raise Held("Profit before tax is zero or negative.")
        return ratio(t, p)

    if kind == "ttm":
        run("revenue", lambda c, rows: _sum(c, rows, _col("revenue"), "revenue"))
        run("pat_owners", lambda c, rows: _sum(c, rows, _col("pat_owners"), "pat_owners"))
    run("ebit_derived", lambda c, rows: _sum(c, rows, _ebit, "ebit_derived"))
    run("ebit_margin", margin(lambda c, rows: _sum(c, rows, _ebit, "ebit_derived")))
    run("net_margin", margin(lambda c, rows: _sum(c, rows, _col("pat_owners"), "pat_owners")))
    run("effective_tax", tax)


def _flows(out: Out, book: Book, na: str | None) -> None:
    for kind in ("quarter", "year"):
        for end in book.ends(kind):
            basis = book.basis_at(kind, end)
            _flow_metrics(out, kind, end, basis, [book.get(basis, kind, end)], None, na)
    for end in book.ends("quarter"):
        try:
            basis, rows = ttm(book, end)
            _flow_metrics(out, "ttm", end, basis, rows, None, na)
        except Held as h:
            _flow_metrics(out, "ttm", end, book.basis_at("quarter", end), None, h.reason, na)


def _growth(out: Out, book: Book, key: str, col: str, kind: str, end: date) -> None:
    basis = book.basis_at(kind, end)
    cur = book.get(basis, kind, end)

    def fn(c):
        earlier = book.get(basis, kind, month_end(end, -12))
        if earlier is None:
            raise Held("The same period a year earlier isn't filed on the same basis.")
        now, then = c.take(cur, col), c.take(earlier, col)
        if then <= 0:
            raise Held("The figure a year earlier isn't positive.")
        return ratio(now, then) - 1
    out.put(key, basis, kind, end, fn)


def _years(out: Out, book: Book, d: Data, na: str | None) -> None:
    for kind in ("quarter", "year"):
        for end in book.ends(kind):
            _growth(out, book, "revenue_yoy", "revenue", kind, end)
            _growth(out, book, "pat_owners_yoy", "pat_owners", kind, end)
    breaks = sorted(d.breaks, key=lambda b: b.effective)
    for end in book.ends("year"):
        basis = book.basis_at("year", end)
        fy = book.get(basis, "year", end)
        opening = month_end(end, -12)
        args = (book, basis, fy)
        for n, key in ((3, "revenue_cagr_3y"), (5, "revenue_cagr_5y")):
            out.put(key, basis, "year", end, lambda c, n=n, a=args: _cagr(c, *a, n, breaks))
        out.put("roe", basis, "year", end, lambda c, a=args, o=opening: _roe(c, *a, o))
        for key, fn in (("sales_to_capital", _sales_to_capital), ("roic", _roic)):
            if na:
                out.na(key, basis, "year", end, na)
            else:
                out.put(key, basis, "year", end, lambda c, fn=fn, a=args, o=opening: fn(c, *a, o))
    _fcf(out, book, na)


def _cagr(c: Calc, book: Book, basis: str, fy: Fin, n: int, breaks: list[Break]) -> Decimal:
    base_end = month_end(fy.end, -12 * n)
    start = month_end(base_end, -12) + timedelta(days=1)          # the first day of the base year
    inside = [b for b in breaks if start < b.effective <= fy.end]
    if inside:
        c.extra["structural_breaks"] = [{"kind": b.kind, "effective": b.effective.isoformat(), "source": b.source,
                                         "curated": b.curated} for b in inside]
        kinds = sorted({b.kind for b in inside})
        raise Held(f"The window spans a {' and a '.join(kinds)}, so its years aren't comparable.")
    base = book.get(basis, "year", base_end)
    if base is None:
        raise Held("The window lacks filed financial-year figures.")
    a, b = c.take(fy, "revenue"), c.take(base, "revenue")
    if a <= 0 or b <= 0:
        raise Held("Revenue isn't positive at both ends of the window.")
    return ((a / b) ** (Decimal(1) / n) - 1).quantize(PLACES)


def _instant(book: Book, basis: str, end: date, what: str) -> Fin:
    f = book.get(basis, "instant", end)
    if f is None or not _has_balance_sheet(f):
        raise Held(f"The balance sheet at the {what} of the year isn't filed on the same basis.")
    return f


def _has_balance_sheet(f: Fin) -> bool:
    return any(f.values.get(k) is not None for k in ("total_assets", "total_equity", "equity_owners"))


def _roe(c: Calc, book: Book, basis: str, fy: Fin, opening: date) -> Decimal:
    pat = c.take(fy, "pat_owners")
    eq0 = c.take(_instant(book, basis, opening, "start"), "equity_owners")
    eq1 = c.take(_instant(book, basis, fy.end, "end"), "equity_owners")
    avg = (eq0 + eq1) / 2
    if avg <= 0:
        raise Held("Average equity to owners is zero or negative.")
    return ratio(pat, avg)


def _debt(c: Calc, f: Fin) -> Decimal:
    has = [k for k in ("borrowings_current", "borrowings_noncurrent") if f.values.get(k) is not None]
    if not has:
        raise Held("The balance sheet doesn't report borrowings.")
    c.extra["label"] = LEASE_LABEL
    return sum((c.take(f, k) for k in has), ZERO)


def _cash_inv(c: Calc, f: Fin) -> Decimal:
    return (c.take(f, "cash") + c.take(f, "bank_balances_other") + c.take(f, "current_investments")
            + c.take(f, "noncurrent_investments"))


def _invested(c: Calc, f: Fin) -> Decimal:
    return c.take(f, "total_equity") + _debt(c, f) - _cash_inv(c, f)


def _opening_ic(c: Calc, book: Book, basis: str, opening: date) -> Decimal:
    ic = _invested(c, _instant(book, basis, opening, "start"))
    if ic <= 0:
        raise Held("Opening invested capital is zero or negative.")
    return ic


def _sales_to_capital(c: Calc, book: Book, basis: str, fy: Fin, opening: date) -> Decimal:
    rev = c.take(fy, "revenue")
    return ratio(rev, _opening_ic(c, book, basis, opening))


def _roic(c: Calc, book: Book, basis: str, fy: Fin, opening: date) -> Decimal:
    ebit, tax, pbt = _ebit(c, fy), c.take(fy, "tax"), c.take(fy, "pbt")
    if pbt <= 0:
        raise Held("Profit before tax is zero or negative, so the tax rate isn't defined.")
    return ratio(ebit * (1 - tax / pbt), _opening_ic(c, book, basis, opening))


def _fcf_of(c: Calc, f: Fin) -> Decimal:
    return c.take(f, "cfo") - c.take(f, "capex")


def _fcf(out: Out, book: Book, na: str | None) -> None:
    """fcf for each FY, and TTM at each half-year (FY − previous H1 + current H1) and year end (the FY)."""
    for end in book.ends("year"):
        basis = book.basis_at("year", end)
        fy = book.get(basis, "year", end)
        for kind in ("year", "ttm"):
            if na:
                out.na("fcf", basis, kind, end, na)
            else:
                out.put("fcf", basis, kind, end, lambda c, fy=fy: _fcf_of(c, fy))
    for end in book.ends("half_year"):
        basis = book.basis_at("half_year", end)
        if na:
            out.na("fcf", basis, "ttm", end, na)
            continue

        def fn(c, basis=basis, end=end):
            h1 = book.get(basis, "half_year", end)
            fy = book.get(basis, "year", month_end(end, -6))
            h0 = book.get(basis, "half_year", month_end(end, -12))
            if fy is None or h0 is None:
                raise Held("The previous year's and half-year's cash flows aren't both filed on the same basis.")
            return _fcf_of(c, fy) - _fcf_of(c, h0) + _fcf_of(c, h1)
        out.put("fcf", basis, "ttm", end, fn)


def _instants(out: Out, book: Book, na: str | None) -> None:
    for end in book.ends("instant", where=_has_balance_sheet):
        basis = book.basis_at("instant", end, where=_has_balance_sheet)
        f = book.get(basis, "instant", end)
        if na:
            for key in ("debt", "cash_and_investments", "invested_capital", "debt_to_equity"):
                out.na(key, basis, "instant", end, na)
            continue
        out.put("debt", basis, "instant", end, lambda c, f=f: _debt(c, f))
        out.put("cash_and_investments", basis, "instant", end, lambda c, f=f: _cash_inv(c, f))
        out.put("invested_capital", basis, "instant", end, lambda c, f=f: _invested(c, f))

        def de(c, f=f):
            eq = c.take(f, "total_equity")
            if eq <= 0:
                raise Held("Total equity is zero or negative.")
            return ratio(_debt(c, f), eq)
        out.put("debt_to_equity", basis, "instant", end, de)


# ---------------------------------------------------------------------------------------- banks (standalone)
ROA_QUARTERLY = (Decimal("0.8"), Decimal("1.3"))
ROA_ANNUALISED = (Decimal("3.2"), Decimal("5.2"))


def _is_bank(f: Fin) -> bool:
    return f.basis == "standalone" and f.format == "bank"


def _banks(out: Out, book: Book) -> None:
    sb = "standalone"
    for end in book.ends("quarter", where=_is_bank):
        q = book.get(sb, "quarter", end)
        out.put("nii", sb, "quarter", end, lambda c, q=q: _nii(c, q))
        out.put("roa", sb, "quarter", end, lambda c, q=q: _roa(c, book, q))
        out.put("nim_proxy", sb, "quarter", end, lambda c, q=q: _nim(c, book, q))
        for key, col in (("gnpa", "gnpa_ratio"), ("nnpa", "nnpa_ratio"), ("cet1", "cet1_ratio")):
            out.put(key, sb, "quarter", end, lambda c, q=q, col=col: c.take(q, col))
        out.put("tier1", sb, "quarter", end, lambda c, q=q: c.take(q, "cet1_ratio") + c.take(q, "at1_ratio"))
        try:
            _, rows = ttm(book, end, where=_is_bank)
            out.put("nii", sb, "ttm", end, lambda c, rows=rows: _sum(c, rows, _nii, "nii"))
        except Held as h:
            out.na("nii", sb, "ttm", end, h.reason)       # a reason found before any line was read
    for end in book.ends("year", where=_is_bank):
        y = book.get(sb, "year", end)
        out.put("nii", sb, "year", end, lambda c, y=y: _nii(c, y))


def _segment_assets(c: Calc, book: Book, end: date) -> Decimal:
    f = book.get("standalone", "instant", end)
    if f is None or f.values.get("segment_assets_total") is None:
        raise Held("Total segment assets aren't filed for the quarter end.")
    return c.take(f, "segment_assets_total")


def roa_scale(r: Decimal) -> str | None:
    if ROA_QUARTERLY[0] <= r <= ROA_QUARTERLY[1]:
        return "quarterly"
    if ROA_ANNUALISED[0] <= r <= ROA_ANNUALISED[1]:
        return "annualised"
    return None


def _roa(c: Calc, book: Book, q: Fin) -> Decimal:
    """Brief §6.3: r = filed ROA ÷ (quarter PAT ÷ quarter-end total segment assets) decides the filing's scale."""
    filed, pat = c.take(q, "roa_filed"), c.take(q, "pat")
    sa = _segment_assets(c, book, q.end)
    if pat == 0 or sa <= 0:
        raise Held("The filed return on assets' scale can't be decided.")
    r = (filed / (pat / sa)).quantize(Decimal("0.0001"))
    scale = roa_scale(r)
    c.extra["scale"] = {"ratio": str(r), "class": scale or "unclear"}
    if scale is None:
        raise Held("The filed return on assets' scale can't be decided.")
    return filed * 4 if scale == "quarterly" else filed


def _nim(c: Calc, book: Book, q: Fin) -> Decimal:
    nii = _nii(c, q)
    avg = (_segment_assets(c, book, month_end(q.end, -3)) + _segment_assets(c, book, q.end)) / 2
    if avg <= 0:
        raise Held("Total segment assets aren't positive.")
    return ratio(nii * 4, avg)


# ---------------------------------------------------------------------------------------- market
def _factor_span(d: Data, after: date, upto: date, held: str) -> tuple[Decimal, list[dict]]:
    """The product of the split, bonus and consolidation factors with ex-dates in (after, upto]; Held(held) if one of
    them isn't applied (held for review, or no factor stored)."""
    prod, used = Decimal(1), []
    for a in sorted(d.actions, key=lambda a: (a.ex_date, a.slug)):
        if a.kind not in SHARE_KINDS or not (after < a.ex_date <= upto):
            continue
        if a.status != "applied" or a.factor is None:
            raise Held(held)
        prod *= a.factor
        used.append({"event": a.slug, "kind": a.kind, "ex_date": a.ex_date.isoformat(), "factor": str(a.factor)})
    return prod, used


def _latest(book: Book, kind: str, upto: date, *, where=None) -> date | None:
    ends = [e for e in book.ends(kind, where=where) if e <= upto]
    return ends[-1] if ends else None


def _market(out: Out, book: Book, d: Data, rules: Rules, na: str | None) -> None:
    if not d.prices:
        return
    p = d.prices[-1]
    t = p.trade_date
    price = {"trade_date": t.isoformat(), "close": str(p.close)}
    M = "market"

    def shares(c):
        """The newest filing's shares (its quarter row first) ÷ the share actions since its period end."""
        rows = [f for f in book.rows.values() if f.end <= t and f.values.get("shares_outstanding")]
        if not rows:
            raise Held("No filing reports shares outstanding.")
        f = max(rows, key=lambda f: (f.end, f.basis == "consolidated", f.kind == "quarter", f.order))
        filed = c.take(f, "shares_outstanding")
        prod, used = _factor_span(d, f.end, t, "A corporate action since the last filing is under review.")
        c.extra["factors"] = used
        return (filed / prod).quantize(Decimal(1))

    n_shares = out.put("shares_today", "none", M, t, shares)
    sh_held = out.held("shares_today", "none", M, t)

    def mcap(c):
        if n_shares is None:
            raise Held(sh_held)
        c.extra["price"] = price
        c.extra["shares_today"] = str(n_shares)
        return p.close * n_shares

    cap = out.put("market_cap", "none", M, t, mcap)
    cap_held = out.held("market_cap", "none", M, t)

    # the last four quarters: those ending at the newest quarter filed (on any basis) up to the price date
    q_end = _latest(book, "quarter", t)
    tt_basis, tt_rows, tt_held = "consolidated", None, "No quarter is filed."
    if q_end is not None:
        try:
            tt_basis, tt_rows = ttm(book, q_end)
            tt_held = None
        except Held as h:
            tt_basis, tt_held = book.basis_at("quarter", q_end) or "consolidated", h.reason
    bs_end = _latest(book, "instant", t, where=_has_balance_sheet)
    bs_basis = book.basis_at("instant", bs_end, where=_has_balance_sheet) if bs_end else "consolidated"
    bs = book.get(bs_basis, "instant", bs_end) if bs_end else None

    def need_cap(c):
        if cap is None:
            raise Held(cap_held)
        c.extra["market_cap"] = {"value": str(cap), **price, "shares_today": str(n_shares)}

    def need_ttm():
        if tt_rows is None:
            raise Held(tt_held)
        return tt_rows

    def need_bs():
        if bs is None:
            raise Held("No balance sheet is filed.")
        return bs

    def pe(c):
        need_cap(c)
        pat = _sum(c, need_ttm(), _col("pat_owners"), "pat_owners")
        if pat <= 0:
            raise Held("Loss-making: profit to owners over the last four quarters isn't positive.")
        return ratio(cap, pat)
    out.put("pe_ttm", tt_basis, M, t, pe)

    def pb(c):
        need_cap(c)
        eq = c.take(need_bs(), "equity_owners")
        if eq <= 0:
            raise Held("Equity to owners is zero or negative.")
        return ratio(cap, eq)
    out.put("pb", bs_basis, M, t, pb)

    if na:
        for key in ("ev", "ev_ebit_ttm", "ev_sales_ttm"):
            out.na(key, bs_basis, M, t, na)
    else:
        lease = _lease_check(book, d, rules, t)

        def ev(c):
            need_cap(c)
            c.extra["lease_check"] = lease
            if lease.get("material"):
                raise Held("This company's lease payments are material against its EBIT, and results filings "
                           "don't report lease liabilities, so enterprise value would understate its debt.")
            if lease.get("held"):
                raise Held(lease["held"])
            f = need_bs()
            return cap + _debt(c, f) + c.take(f, "nci") - _cash_inv(c, f)
        e = out.put("ev", bs_basis, M, t, ev)
        ev_held = out.held("ev", bs_basis, M, t)

        def over(name, fn, nonpos):
            def f(c):
                if e is None:
                    raise Held(ev_held)
                rows = need_ttm()
                if tt_basis != bs_basis:
                    raise Held("The balance sheet and the last four quarters are on different bases.")
                c.extra["ev"] = str(e)
                den = _sum(c, rows, fn, name)
                if den <= 0:
                    raise Held(nonpos)
                return ratio(e, den)
            return f
        out.put("ev_ebit_ttm", bs_basis, M, t, over("ebit_derived", _ebit,
                                                     "EBIT over the last four quarters is zero or negative."))
        out.put("ev_sales_ttm", bs_basis, M, t, over("revenue", _col("revenue"),
                                                      "Revenue over the last four quarters is zero or negative."))

    out.put("dividend_yield", "none", M, t, lambda c: _dividend_yield(c, d, p))
    out.put("return_1y", "none", M, t, lambda c: _return_1y(c, d))
    out.put("traded_value_median_3m", "none", M, t, lambda c: _median_traded(c, d))


def _lease_check(book: Book, d: Data, rules: Rules, t: date) -> dict:
    """Anand's 30 Oct rule: lease payments over the latest half-year or year with a cash flow, against EBIT over the
    same period (the same filing's year-to-date column). `material` holds EV back; `held` says it can't be checked."""
    f = lease_row(book, t)
    if f is None:
        return {"held": "No cash flow statement is filed, so lease payments can't be checked against EBIT."}
    lease = d.leases.get(f.document_id)
    out = {"document_id": f.document_id, "basis": f.basis, "period_kind": f.kind, "period_end": f.end.isoformat(),
           "threshold": str(rules.lease_threshold)}
    if lease is not None and lease.unreadable:
        return {**out, "material": False,
                "held": "Lease payments can't be checked against EBIT: the filing couldn't be read again."}
    if lease is None or lease.value is None:
        return {**out, "lease_payments": None, "material": False}
    out.update(lease_payments=str(lease.value), element=lease.element)
    if lease.value == 0:
        return {**out, "material": False}
    try:
        ebit = _ebit(Calc(), f)
    except Held:
        return {**out, "material": False,
                "held": "Lease payments can't be checked against EBIT, which this filing's lines don't give."}
    out["ebit"] = str(ebit)
    material = ebit <= 0 or lease.value / ebit >= rules.lease_threshold
    return {**out, "material": bool(material)}


def lease_row(book: Book, upto: date) -> Fin | None:
    """The newest half-year or year row with a cash flow up to `upto` (consolidated where filed): the lease check's."""
    best = None
    for kind in ("half_year", "year"):
        for end in book.ends(kind, where=lambda f: f.values.get("cfo") is not None):
            if end <= upto and (best is None or end > best[0]):
                best = (end, kind)
    if best is None:
        return None
    end, kind = best
    basis = book.basis_at(kind, end, where=lambda f: f.values.get("cfo") is not None)
    return book.get(basis, kind, end)


def _dividend_yield(c: Calc, d: Data, p: Price) -> Decimal:
    t = p.trade_date
    total, used = ZERO, []
    for a in sorted(d.actions, key=lambda a: (a.ex_date, a.slug)):
        if a.kind != "dividend" or not (t - timedelta(days=365) < a.ex_date <= t):
            continue
        if a.dps is None:
            raise Held("A dividend's amount couldn't be read.")
        prod, factors = _factor_span(d, a.ex_date, t, "A corporate action since a dividend is under review, so its "
                                                      "amount per share today isn't known.")
        per_share = a.dps * prod
        total += per_share
        used.append({"event": a.slug, "ex_date": a.ex_date.isoformat(), "dps": str(a.dps),
                     "per_share_today": str(per_share), "factors": factors})
    c.extra.update(dividends=used, price={"trade_date": t.isoformat(), "close": str(p.close)})
    return ratio(total, p.close)


def _return_1y(c: Calc, d: Data) -> Decimal:
    p = d.prices[-1]
    target = year_before(p.trade_date)
    base = [x for x in d.prices if x.trade_date <= target]
    if not base:
        raise Held("Less than a year of prices is stored.")
    b = base[-1]
    c.extra["prices"] = [{"trade_date": x.trade_date.isoformat(), "close": str(x.close),
                          "adj_close": None if x.adj_close is None else str(x.adj_close)} for x in (b, p)]
    if b.adj_close is None or p.adj_close is None:
        raise Held("An earlier corporate action is under review, so adjusted prices are held back.")
    return ratio(p.adj_close, b.adj_close) - 1


MEDIAN_SESSIONS = 63


def _median_traded(c: Calc, d: Data) -> Decimal:
    last = d.prices[-MEDIAN_SESSIONS:]
    if len(last) < MEDIAN_SESSIONS:
        raise Held("Fewer than 63 sessions of prices are stored.")
    vals = [x.traded_value for x in last]
    if any(v is None for v in vals):
        raise Held("A session's traded value isn't stored.")
    c.extra["sessions"] = {"from": last[0].trade_date.isoformat(), "to": last[-1].trade_date.isoformat(),
                           "n": len(last)}
    return Decimal(statistics.median(vals))


# ---------------------------------------------------------------------------------------- the run's checks
def ttm_sum_errors(values: list[Value]) -> list[str]:
    """(B) every TTM sum equals the sum of its four quarters exactly (recomputed from its inputs)."""
    bad = []
    for v in values:
        if v.period_kind != "ttm" or v.key not in TTM_SUMS or v.value is None:
            continue
        parts = (v.inputs.get("quarters") or {}).get(v.key) or []
        if len(parts) != 4 or sum((Decimal(x["value"]) for x in parts), ZERO) != v.value:
            bad.append(f"{v.key} {v.basis} at {v.period_end.isoformat()}")
    return bad


EBIT_LINES = ("pbt_before_exceptional", "finance_cost", "other_income")


def ebit_input_errors(values: list[Value]) -> list[str]:
    """(B) an ebit_derived value exists only with all its inputs present (for each of its periods)."""
    bad = []
    for v in values:
        if v.key != "ebit_derived" or v.value is None:
            continue
        rows = v.inputs.get("financials") or []
        want = 4 if v.period_kind == "ttm" else 1
        ok = len(rows) == want and all(all(k in r["lines"] for k in EBIT_LINES + ("share_of_associates",))
                                       for r in rows)
        if not ok:
            bad.append(f"{v.basis} {v.period_kind} {v.period_end.isoformat()}")
    return bad
