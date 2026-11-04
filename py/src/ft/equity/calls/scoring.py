"""Guidance scoring (Spec 02B §5.10 and Spec 02B.1 C5; job 2B-14, #177): each management target an earnings call stated,
scored against the company's filed figures. Pure: the task (`ft/jobs/tasks/equity_guidance_scores.py`) reads the rows and
writes `equity.guidance_outcome`, the guidance series and their `core.forecast` rows; nothing here touches the database.

**What is scored.** Only `speaker_role = 'management'` rows of a published `guidance_extraction` output, duplicates left out
(C5: a statement whose speaker's role couldn't be read is never a target, a first statement, a revision or a forecast). The
map is `py/config/equity/guidance_metrics.yaml`'s `lines` (map_version 1, fixed before any run).

**Actuals** (2A's filed figures, the latest filed for each period: `metrics.Book`; every line read through `metrics.Calc`, so
2A's rules for owners' profit, absent lines and lines a filing gets wrong apply unchanged):
- a quarter is its own row; a fiscal year is the year's row (as 2A's year metrics are); a longer P&L period is the sum of its
  filed quarters, each on one basis;
- cash flow comes from the half-year and year rows (the second half is the year less the first half; a multi-year span is the
  sum of its years);
- a balance-sheet figure is read at the period's end; a bank's ratio from the quarter row ending there;
- growth compares the same period a year earlier on the same basis; a CAGR runs from the year before the first year;
- ₹ figures are compared in crore (the extraction's unit), fractions as filed are compared in per cent.

**Not scored** (with the reason this job decides, §5.10): `structural_break` (a merger or demerger after the first day the
figures cover, up to the period's end), `definition` (the definition check, below), `filed_figures_unavailable` (not filed and
parsed 90 days after the period's end, 120 after a fiscal year's end; or a filed fraction outside 0 to 1, held and named),
and `no_filed_figure` where the map has no line for the statement (a period, basis, format or growth basis it doesn't hold).
The extraction's own reasons (`no_filed_figure`, `currency`, `undated`, `vague_figure`, `scope`) are kept as it set them.

**The definition check** (EBITDA margin, EBIT margin, net debt): our figure for each quarter management stated on a call
(`equity.stated_figure`) against what it stated; the company's targets are scored only if the median absolute difference
is at most 0.5 pp (margins) or 5% of the stated figure (net debt), over at least one comparison.

**Tolerances** (§5.10's table, rounding half away from zero): a range by rounding the actual to the target's decimals; a point
within ±0.5 pp (±10% of the target if smaller, never below ±0.1 pp) or ±5% of a ₹ amount, the actual rounded to one decimal
(per cent) or the nearest ₹ crore; floors and ceilings at that rounding; directions against the period of the same length just
before (or a year earlier when the statement says so), both rounded to one decimal. Past a target on its favourable side is
delivered. A missed target is a fact about a figure, never a verdict on management (Brief §11).

**Counting:** the target key is company, metric, basis and period (plus what tells apart statements the map can't score:
the words of an `other` metric, an undated period's words, a scope, a quarter-on-quarter or CAGR growth basis). A target is
scored on its earliest stored statement; later ones are raised, cut (the centre moved by more than the point tolerance),
reiterated or restated (the type changed), and each is still judged on its own figures. `counted` (in the hit rate) needs the
first statement, a horizon share below 0.5 and an outcome that isn't `not_scored`.

**Inputs.** Every outcome's `inputs` holds every filed line and value used (by reference into its own list), the formula, the
statement's figures and the rule's parameters, so `recompute(inputs)` gives the same outcome, relation, actual and base from
the inputs alone (the task's blocking check).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path

import yaml

from ft.equity import metrics as mx

RULE_VERSION = "1"
MAP_PATH = Path(__file__).resolve().parents[4] / "config" / "equity" / "guidance_metrics.yaml"
CRORE = Decimal(10_000_000)
HUNDRED = Decimal(100)
PLACES = Decimal("0.000001")          # actuals and bases are stored to 6 places (pp or ₹ crore)
HORIZON_PLACES = Decimal("0.0001")
ZERO = Decimal(0)

MEASURES = ("flow", "cash_flow", "instant", "bank_ratio")
FORMULAS = ("amount", "ratio", "fraction", "growth")
FAVOURABLE = ("higher", "lower", "none")
PERIOD_KINDS = ("quarter", "half_year", "nine_months", "year", "multi_year")
UNITS = ("inr", "pct")
COLUMN_UNITS = ("inr", "fraction")
# §5.10's not-scored reasons; the first four the statement alone decides (2B-13 sets them), the last three this job.
STATEMENT_REASONS = ("no_filed_figure", "currency", "undated", "vague_figure", "scope", "duplicate")
DEFINITION_METRICS = ("ebitda_margin", "ebit_margin", "net_debt")
DEFINITION_LIMITS = {"ebitda_margin": Decimal("0.5"), "ebit_margin": Decimal("0.5"), "net_debt": Decimal("0.05")}
OPERATING_ONLY = frozenset({"ebitda_margin", "ebit_margin", "net_debt"})     # 2A's N/A for lenders and insurers
FILE_DAYS, YEAR_END_DAYS = 90, 120
IMPROVE = re.compile(r"\bimprov", re.IGNORECASE)
CASH = ("cash", "bank_balances_other", "current_investments", "noncurrent_investments")
BORROWINGS = ("borrowings_current", "borrowings_noncurrent")

# The figures a line reads, each a signed sum of filed columns. `pat_owners` and `net_debt` have their own rules below.
FIGURES: dict[str, tuple[tuple[str, int], ...]] = {
    "revenue": (("revenue", 1),),
    "pat_owners": (("pat_owners", 1),),
    "ebit": (("pbt_before_exceptional", 1), ("finance_cost", 1), ("other_income", -1), ("share_of_associates", -1)),
    "ebitda": (("pbt_before_exceptional", 1), ("finance_cost", 1), ("other_income", -1), ("share_of_associates", -1),
               ("dna", 1)),
    "tax": (("tax", 1),),
    "pbt": (("pbt", 1),),
    "capex": (("capex", 1),),
    "net_debt": tuple((c, 1) for c in BORROWINGS) + tuple((c, -1) for c in CASH),
    "advances": (("advances", 1),),
    "deposits": (("deposits", 1),),
    "gnpa_ratio": (("gnpa_ratio", 1),),
    "nnpa_ratio": (("nnpa_ratio", 1),),
    "cet1_ratio": (("cet1_ratio", 1),),
}


# ---------------------------------------------------------------------------------------------- the map
@dataclass(frozen=True)
class Line:
    metric: str
    measure: str
    formula: str
    of: str
    unit: str
    basis: str
    periods: tuple[str, ...]
    favourable: str
    over: str | None = None
    ends: str | None = None
    format: str | None = None
    label: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in {"metric": self.metric, "measure": self.measure, "formula": self.formula, "of": self.of,
                                  "over": self.over, "unit": self.unit, "basis": self.basis, "favourable": self.favourable,
                                  "label": self.label}.items() if v is not None}


@dataclass(frozen=True)
class Map:
    version: int
    lines: dict[str, Line]
    columns: dict[str, str]          # filed column -> its unit as stored (inr: full rupees; fraction)


def load_map(path: Path = MAP_PATH) -> Map:
    """The map of §5.10. A malformed line fails the run (the map is fixed before any run)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    columns = {str(k): str(v) for k, v in (raw.get("columns") or {}).items()}
    if any(v not in COLUMN_UNITS for v in columns.values()):
        raise ValueError(f"{path.name}: a column's unit is one of {', '.join(COLUMN_UNITS)}")
    lines: dict[str, Line] = {}
    for key, r in (raw.get("lines") or {}).items():
        if key not in raw["metrics"] or key == "other":
            raise ValueError(f"{path.name} lines.{key}: not a metric key")
        try:
            ln = Line(str(key), r["measure"], r["formula"], r["of"], r["unit"], r["basis"], tuple(r["periods"]),
                      r["favourable"], r.get("over"), r.get("ends"), r.get("format"), r.get("label"))
        except (KeyError, TypeError) as e:
            raise ValueError(f"{path.name} lines.{key}: {e}") from e
        bad = [f for f, ok in (("measure", ln.measure in MEASURES), ("formula", ln.formula in FORMULAS),
                               ("unit", ln.unit in UNITS), ("basis", ln.basis in ("consolidated", "standalone")),
                               ("favourable", ln.favourable in FAVOURABLE), ("of", ln.of in FIGURES),
                               ("over", ln.over is None or ln.over in FIGURES),
                               ("periods", bool(ln.periods) and all(p in PERIOD_KINDS for p in ln.periods)),
                               ("ends", ln.ends in (None, "half_year")), ("format", ln.format in (None, "bank")))
               if not ok]
        if bad or (ln.formula == "ratio") != (ln.over is not None):
            raise ValueError(f"{path.name} lines.{key}: bad {', '.join(bad) or 'over'}")
        for fig in (ln.of, ln.over):
            for col, _ in FIGURES.get(fig or "", ()):
                if col not in columns:
                    raise ValueError(f"{path.name} lines.{key}: column {col} has no unit in `columns`")
        if ln.formula == "fraction" and any(columns[c] != "fraction" for c, _ in FIGURES[ln.of]):
            raise ValueError(f"{path.name} lines.{key}: a fraction line reads fraction columns only")
        lines[key] = ln
    return Map(int(raw["map_version"]), lines, columns)


# ---------------------------------------------------------------------------------------------- inputs
@dataclass(frozen=True)
class Statement:
    """One management `equity.guidance` row of a published output (not a duplicate)."""
    id: int
    entity_id: int
    call_event_id: int
    metric: str
    kind: str
    value: Decimal | None
    low: Decimal | None
    high: Decimal | None
    unit: str | None
    direction: str | None
    comparison: str | None
    period_kind: str
    start: date | None
    end: date | None
    basis: str | None
    growth_basis: str | None
    scope_text: str | None
    vintage: date
    reason: str | None = None        # the extraction's not_scored_reason
    metric_text: str = ""
    period_text: str | None = None
    quote: str | None = None


@dataclass(frozen=True)
class Stated:
    """One management `equity.stated_figure` row of a published output (the definition check's figure)."""
    id: int
    call_event_id: int
    metric: str
    value: Decimal
    unit: str | None
    period_kind: str
    start: date
    end: date
    basis: str | None
    scope_text: str | None


@dataclass
class Company:
    entity_id: int
    slug: str
    family: str
    fy_end_month: int
    formats: frozenset[str] = frozenset()
    fins: list[mx.Fin] = field(default_factory=list)
    breaks: list[mx.Break] = field(default_factory=list)
    stated: list[Stated] = field(default_factory=list)


@dataclass(frozen=True)
class Outcome:
    guidance_id: int
    target_key: str
    is_first: bool
    revision: str
    horizon_share: Decimal | None
    counted: bool
    outcome: str
    relation: str | None
    actual: Decimal | None
    base: Decimal | None
    reason: str | None
    inputs: dict
    forecast_ok: bool                # the statement fits the map's line and basis: its point or range may be a forecast row
    unit_held: str | None = None     # a filed fraction outside 0 to 1 (named by the task's check)
    definition: dict | None = None   # the definition check it failed, if it did


class UnitHeld(mx.Held):
    """A filed figure whose unit disagrees with the map's premise (a fraction outside 0 to 1): held, never rescaled."""


# ---------------------------------------------------------------------------------------------- reading the lines
class Reader(mx.Calc):
    """metrics.Calc that also keeps each line it reads in a flat list, so a figure is a list of signed references into it."""

    def __init__(self, columns: dict[str, str]):
        super().__init__()
        self.columns = columns
        self.flat: list[dict] = []
        self._at: dict[tuple, int] = {}
        self.last = -1

    def _record(self, f: mx.Fin, col: str, v: Decimal, element: str) -> None:
        super()._record(f, col, v, element)
        k = (f.document_id, f.kind, f.end, col)
        if k not in self._at:
            self._at[k] = len(self.flat)
            self.flat.append({"document_id": f.document_id, "basis": f.basis, "period_kind": f.kind,
                              "period_end": f.end.isoformat(), "line": col, "element": element, "value": str(v)})
        self.last = self._at[k]

    def term(self, f: mx.Fin, col: str, sign: int) -> list[dict]:
        v = self.take(f, col)
        if self.columns.get(col) == "fraction" and not ZERO <= v < 1:
            raise UnitHeld(f"{col} is filed as {v}, outside 0 to 1")
        return [{"ref": self.last, "sign": sign}]


def _figure(r: Reader, f: mx.Fin, name: str) -> list[dict]:
    if (name == "pat_owners" and f.basis == "consolidated" and f.values.get("pat_owners") is None
            and f.values.get("pat_nci") is None and f.values.get("pat") is not None):
        return r.term(f, "pat", 1)          # §5.10: `pat` when no non-controlling interest is filed
    if name == "net_debt":
        has = [c for c in BORROWINGS if f.values.get(c) is not None]
        if not has:
            raise mx.Held("The balance sheet doesn't report borrowings.")
        return [t for c in has for t in r.term(f, c, 1)] + [t for c in CASH for t in r.term(f, c, -1)]
    return [t for col, s in FIGURES[name] for t in r.term(f, col, s)]


def total(flat: list[dict], terms: list[dict]) -> Decimal:
    return sum((Decimal(t["sign"]) * Decimal(flat[t["ref"]]["value"]) for t in terms), ZERO)


# ---------------------------------------------------------------------------------------------- periods
def months(start: date, end: date) -> int:
    return (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1


def shift(d: date, n: int) -> date:
    """A period boundary moved by n months: a first of a month stays a first, an end of a month stays an end."""
    if d.day == 1:
        return mx.month_end(d, n - 1) + timedelta(days=1)
    return mx.month_end(d, n)


def fy_end_of(d: date, fy_end_month: int) -> date:
    """The end of the fiscal year holding d."""
    e = mx.month_end(date(d.year, fy_end_month, 1))
    return e if e >= d else mx.month_end(date(d.year + 1, fy_end_month, 1))


def is_fy_end(d: date, fy_end_month: int) -> bool:
    return d.month == fy_end_month and d == mx.month_end(d)


def is_half_end(d: date, fy_end_month: int) -> bool:
    return d == mx.month_end(d) and (d.month - fy_end_month) % 6 == 0


def deadline(end: date, fy_end_month: int) -> date:
    return end + timedelta(days=YEAR_END_DAYS if is_fy_end(end, fy_end_month) else FILE_DAYS)


def _year_label(fy_end: date) -> str:
    start = shift(fy_end, -12) + timedelta(days=1)
    return str(fy_end.year) if start.year == fy_end.year else f"{fy_end.year - 1}-{fy_end.year % 100:02d}"


def target_label(kind: str, start: date, end: date, fy_end_month: int) -> str:
    """core.forecast's label, in the shape module 1's RBI projections use ('Q2 2026-27', 'H1 2026-27', 'FY 2026-27')."""
    fy = fy_end_of(end, fy_end_month)
    n = months(shift(fy, -12) + timedelta(days=1), end)
    if kind == "quarter":
        return f"Q{n // 3} {_year_label(fy)}"
    if kind == "half_year":
        return f"H{n // 6} {_year_label(fy)}"
    if kind == "nine_months":
        return f"9M {_year_label(fy)}"
    if kind == "year":
        return f"FY {_year_label(fy)}"
    return f"FY {_year_label(fy_end_of(start, fy_end_month))} to FY {_year_label(fy)}"


# ---------------------------------------------------------------------------------------------- measuring a figure
@dataclass(frozen=True)
class Measured:
    formula: str
    n: int | None
    basis: str
    lines: list[dict]
    operands: dict[str, list[dict]]
    value: Decimal
    period: tuple[str, str]

    def as_dict(self) -> dict:
        out = {"formula": self.formula, "basis": self.basis, "period_start": self.period[0], "period_end": self.period[1],
               "lines": self.lines, "operands": self.operands, "value": str(self.value)}
        if self.n is not None:
            out["n"] = self.n
        return out


def apply(formula: str, v: dict[str, Decimal], n: int | None = None) -> Decimal:
    """The figure from its operands' totals (X, or N over D, or X against P a year earlier)."""
    if formula == "amount":
        out = v["X"] / CRORE
    elif formula == "fraction":
        out = v["X"] * HUNDRED
    elif formula == "ratio":
        if v["D"] <= 0:
            raise mx.Held("The base of the ratio is zero or negative.")
        out = HUNDRED * v["N"] / v["D"]
    elif formula == "growth":
        if v["P"] <= 0:
            raise mx.Held("The figure a year earlier isn't positive.")
        if n:
            if v["X"] <= 0:
                raise mx.Held("The figure at the end of the span isn't positive.")
            out = HUNDRED * ((v["X"] / v["P"]) ** (Decimal(1) / Decimal(n)) - 1)
        else:
            out = HUNDRED * (v["X"] / v["P"] - 1)
    else:
        raise ValueError(f"unknown formula {formula}")
    return out.quantize(PLACES, ROUND_HALF_UP)


def from_inputs(m: dict) -> Decimal:
    """A measured figure's value from its stored inputs alone."""
    flat = m["lines"]
    return apply(m["formula"], {k: total(flat, terms) for k, terms in m["operands"].items()}, m.get("n"))


class _Book:
    def __init__(self, c: Company):
        self.book = mx.Book(c.fins)
        self.fy = c.fy_end_month

    def _basis(self, want: str | None, kind: str, end: date, where=None) -> str:
        """`want` None: consolidated where filed for the period, else standalone (2A's rule); else that basis only."""
        b = want or self.book.basis_at(kind, end, where=where)
        if b is None:
            raise mx.Held("The period's figures aren't filed yet.")
        return b

    def flow(self, r: Reader, fig: str, start: date, end: date, want: str | None) -> tuple[str, list[dict]]:
        n = months(start, end)
        if n == 12 and is_fy_end(end, self.fy):
            kind, ends = "year", [end]
        elif n % 3 == 0 and n > 0:
            kind, ends = "quarter", [mx.month_end(start, 3 * i + 2) for i in range(n // 3)]
        else:
            raise mx.Held("N/A: the period isn't made of whole quarters.")
        b = self._basis(want, kind, ends[-1])
        rows = [self.book.get(b, kind, e) for e in ends]
        if any(f is None for f in rows):
            raise mx.Held("The period's figures aren't all filed on one basis.")
        return b, [t for f in rows for t in _figure(r, f, fig)]

    def cash(self, r: Reader, fig: str, start: date, end: date, want: str | None) -> tuple[str, list[dict]]:
        n = months(start, end)
        fy_start = start.day == 1 and is_fy_end(start - timedelta(days=1), self.fy)
        if n % 12 == 0 and is_fy_end(end, self.fy) and fy_start:
            ends = [mx.month_end(start, 12 * i + 11) for i in range(n // 12)]
            b = self._basis(want, "year", end)
            rows = [self.book.get(b, "year", e) for e in ends]
            if any(f is None for f in rows):
                raise mx.Held("The years' cash flows aren't all filed on one basis.")
            return b, [t for f in rows for t in _figure(r, f, fig)]
        if n == 6 and fy_start:
            b = self._basis(want, "half_year", end)
            f = self.book.get(b, "half_year", end)
            if f is None:
                raise mx.Held("The half-year's cash flow isn't filed.")
            return b, _figure(r, f, fig)
        if n == 6 and is_fy_end(end, self.fy):
            b = self._basis(want, "year", end)
            y, h = self.book.get(b, "year", end), self.book.get(b, "half_year", mx.month_end(end, -6))
            if y is None or h is None:
                raise mx.Held("The year's and the first half's cash flows aren't both filed on one basis.")
            return b, _figure(r, y, fig) + [{**t, "sign": -t["sign"]} for t in _figure(r, h, fig)]
        raise mx.Held("N/A: cash flow is filed only for half-years and years.")

    def instant(self, r: Reader, fig: str, end: date, want: str | None) -> tuple[str, list[dict]]:
        b = self._basis(want, "instant", end, where=mx._has_balance_sheet)
        f = self.book.get(b, "instant", end)
        if f is None or not mx._has_balance_sheet(f):
            raise mx.Held("The balance sheet at the period's end isn't filed.")
        return b, _figure(r, f, fig)

    def bank_ratio(self, r: Reader, fig: str, end: date) -> tuple[str, list[dict]]:
        f = self.book.get("standalone", "quarter", end)
        if f is None or f.format != "bank":
            raise mx.Held("The bank's quarter at the period's end isn't filed.")
        return "standalone", _figure(r, f, fig)

    def operand(self, r: Reader, line: Line, fig: str, start: date, end: date, want: str | None) -> tuple[str, list[dict]]:
        if line.measure == "flow":
            return self.flow(r, fig, start, end, want)
        if line.measure == "cash_flow":
            return self.cash(r, fig, start, end, want)
        if line.measure == "instant":
            return self.instant(r, fig, end, want)
        return self.bank_ratio(r, fig, end)

    def measure(self, line: Line, columns: dict[str, str], start: date, end: date, kind: str,
                want: str | None) -> Measured:
        r = Reader(columns)
        n = None
        if line.formula in ("amount", "fraction"):
            b, x = self.operand(r, line, line.of, start, end, want)
            ops = {"X": x}
        elif line.formula == "ratio":
            b, num = self.operand(r, line, line.of, start, end, want)
            _, den = self.operand(r, line, line.over, start, end, b)
            ops = {"N": num, "D": den}
        elif kind == "multi_year":
            n = months(start, end) // 12
            last = shift(end, -12) + timedelta(days=1)
            b, x = self.operand(r, line, line.of, last, end, want)
            _, p = self.operand(r, line, line.of, shift(start, -12), start - timedelta(days=1), b)
            ops = {"X": x, "P": p}
        else:
            b, x = self.operand(r, line, line.of, start, end, want)
            _, p = self.operand(r, line, line.of, shift(start, -12), shift(end, -12), b)
            ops = {"X": x, "P": p}
        value = apply(line.formula, {k: total(r.flat, t) for k, t in ops.items()}, n)
        return Measured(line.formula, n, b, r.flat, ops, value, (start.isoformat(), end.isoformat()))


# ---------------------------------------------------------------------------------------------- tolerances
def rnd(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)


def decimals(x: Decimal) -> int:
    e = x.as_tuple().exponent
    return max(0, -e) if isinstance(e, int) else 0


def tolerance(unit: str | None, target: Decimal) -> Decimal:
    """The point tolerance: ±0.5 pp, or ±10% of the target if that's smaller, never below ±0.1 pp; ±5% of a ₹ amount."""
    if unit == "pct":
        return max(Decimal("0.1"), min(Decimal("0.5"), abs(target) * Decimal("0.1")))
    if unit == "inr":
        return abs(target) * Decimal("0.05")
    return ZERO


def point_places(unit: str) -> int:
    """Points, floors and ceilings: one decimal for a percentage, the nearest ₹ crore for an amount."""
    return 1 if unit == "pct" else 0


@dataclass(frozen=True)
class Verdict:
    outcome: str
    relation: str
    detail: dict


def judge(kind: str, unit: str, favourable: str, *, value: Decimal | None = None, low: Decimal | None = None,
          high: Decimal | None = None, direction: str | None = None, actual: Decimal,
          base: Decimal | None = None) -> Verdict:
    """§5.10's tolerance table for one statement against its actual (and, for a direction, its base)."""
    if kind == "range":
        places = max(decimals(low), decimals(high))
        a = rnd(actual, places)
        d = {"rounded_actual": str(a), "places": places}
        if low <= a <= high:
            return Verdict("delivered", "within", d)
        if a > high:
            return Verdict("delivered" if favourable == "higher" else "missed", "above", d)
        return Verdict("delivered" if favourable == "lower" else "missed", "below", d)
    if kind == "point":
        places, tol = point_places(unit), tolerance(unit, value)
        a = rnd(actual, places)
        diff = a - value
        d = {"rounded_actual": str(a), "places": places, "tolerance": str(tol)}
        if abs(diff) <= tol:
            return Verdict("delivered", "within", d)
        rel = "above" if diff > 0 else "below"
        good = (favourable == "higher" and diff > 0) or (favourable == "lower" and diff < 0)
        return Verdict("delivered" if good else "missed", rel, d)
    if kind in ("floor", "ceiling"):
        places = point_places(unit)
        a = rnd(actual, places)
        d = {"rounded_actual": str(a), "places": places}
        ok = a >= value if kind == "floor" else a <= value
        return Verdict("delivered" if ok else "missed", "at_or_past" if ok else "short_of", d)
    if kind == "direction":
        a, b = rnd(actual, 1), rnd(base, 1)
        tol = tolerance(unit, b)
        d = {"rounded_actual": str(a), "rounded_base": str(b), "places": 1, "tolerance": str(tol)}
        ok = {"increase": a > b, "decrease": a < b, "maintain": abs(a - b) <= tol}[direction]
        return Verdict("delivered" if ok else "missed", "moved_as_said" if ok else "moved_against", d)
    raise ValueError(f"unknown kind {kind}")


def recompute(inputs: dict) -> tuple[str, str | None, Decimal | None, Decimal | None]:
    """(outcome, relation, actual, base) from an outcome's stored inputs alone."""
    if "actual" not in inputs:
        return inputs["outcome"], None, None, None
    s, m = inputs["statement"], inputs["map"]
    actual = from_inputs(inputs["actual"])
    base = from_inputs(inputs["base"]) if "base" in inputs else None
    d = (lambda k: Decimal(s[k]) if s.get(k) is not None else None)
    v = judge(s["kind"], m["unit"], m["favourable"], value=d("value"), low=d("low"), high=d("high"),
              direction=s.get("effective_direction"), actual=actual, base=base)
    return v.outcome, v.relation, actual, base


# ---------------------------------------------------------------------------------------------- counting
def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def scoring_basis(s: Statement, line: Line | None) -> str:
    """The statement's basis when it says standalone, otherwise the map's (§5.10)."""
    if s.basis == "standalone":
        return "standalone"
    return line.basis if line is not None else (s.basis or "consolidated")


def target_key(s: Statement, basis: str) -> str:
    metric = s.metric if s.metric != "other" else f"other:{_norm(s.metric_text)}"
    period = f"{s.start.isoformat()}|{s.end.isoformat()}" if s.start else f"undated:{_norm(s.period_text)}"
    parts = [str(s.entity_id), metric, basis, period]
    if s.scope_text:
        parts.append(f"scope:{_norm(s.scope_text)}")
    if s.growth_basis in ("qoq", "cagr"):
        parts.append(s.growth_basis)
    return "|".join(parts)


def centre(s: Statement) -> Decimal | None:
    if s.kind == "range" and s.low is not None and s.high is not None:
        return (s.low + s.high) / 2
    if s.kind in ("point", "floor", "ceiling"):
        return s.value
    return None


def revision(prev: Statement, s: Statement) -> str:
    if s.kind != prev.kind:
        return "restated"
    if s.kind == "direction":
        return "reiterated" if s.direction == prev.direction else "restated"
    a, b = centre(prev), centre(s)
    if a is None or b is None:
        return "reiterated" if a is None and b is None else "restated"
    tol = tolerance(prev.unit, a)
    if abs(b - a) <= tol:
        return "reiterated"
    return "raised" if b > a else "cut"


def horizon(s: Statement) -> Decimal | None:
    if s.start is None:
        return None
    days = (s.end - s.start).days + 1
    return (Decimal((s.vintage - s.start).days) / Decimal(days)).quantize(HORIZON_PLACES, ROUND_HALF_UP)


# ---------------------------------------------------------------------------------------------- the definition check
def definition_check(c: Company, line: Line, m: Map) -> dict:
    """Our figure for each quarter management stated on a call, against its stated figure (§5.10)."""
    book = _Book(c)
    comps = []
    for s in sorted(c.stated, key=lambda s: (s.end, s.id)):
        if s.metric != line.metric or s.period_kind != "quarter" or s.scope_text or s.unit != line.unit:
            continue
        try:
            ours = book.measure(line, m.columns, s.start, s.end, "quarter",
                                "standalone" if s.basis == "standalone" else (None if line.basis == "consolidated"
                                                                             else line.basis)).value
        except mx.Held:
            continue
        if line.metric == "net_debt":
            if s.value == 0:
                continue                                  # a relative difference against 0 isn't defined
            diff = (abs(ours - s.value) / abs(s.value)).quantize(PLACES, ROUND_HALF_UP)
        else:
            diff = abs(ours - s.value)
        comps.append({"stated_figure_id": s.id, "call_event_id": s.call_event_id, "period_end": s.end.isoformat(),
                      "stated": str(s.value), "ours": str(ours), "difference": str(diff)})
    limit = DEFINITION_LIMITS[line.metric]
    med = statistics.median(Decimal(x["difference"]) for x in comps) if comps else None
    return {"metric": line.metric, "comparisons": comps, "n": len(comps), "limit": str(limit),
            "difference_unit": "fraction of the stated figure" if line.metric == "net_debt" else "pp",
            "median": None if med is None else str(med), "passed": med is not None and med <= limit}


# ---------------------------------------------------------------------------------------------- one company
def _statement_dict(s: Statement, basis: str, effective: str | None) -> dict:
    out = {"guidance_id": s.id, "call_event_id": s.call_event_id, "metric": s.metric, "kind": s.kind,
           "value": None if s.value is None else str(s.value), "low": None if s.low is None else str(s.low),
           "high": None if s.high is None else str(s.high), "unit": s.unit, "direction": s.direction,
           "effective_direction": effective, "comparison": s.comparison, "period_kind": s.period_kind,
           "target_start": s.start.isoformat() if s.start else None, "target_end": s.end.isoformat() if s.end else None,
           "basis": basis, "stated_basis": s.basis, "growth_basis": s.growth_basis, "vintage_date": s.vintage.isoformat()}
    return {k: v for k, v in out.items() if v is not None}


def statement_reason(s: Statement, line: Line | None, c: Company) -> tuple[str | None, str | None, str | None]:
    """(reason, note, effective direction) from the statement and the map alone."""
    eff = s.direction
    if s.reason:
        return s.reason, "set by the extraction", eff
    if line is None:
        return "no_filed_figure", "the map has no line for this metric", eff
    if s.period_kind not in line.periods:
        return "no_filed_figure", f"the map's line isn't scored for a {s.period_kind.replace('_', ' ')} period", eff
    if line.format == "bank" and "bank" not in c.formats:
        return "no_filed_figure", "a bank line, and the company's filings aren't in the bank format", eff
    if s.metric in OPERATING_ONLY and c.family in ("lender", "insurer"):
        return "no_filed_figure", f"not meaningful for a {c.family} (2A's rule)", eff
    if line.ends == "half_year" and not is_half_end(s.end, c.fy_end_month):
        return "no_filed_figure", "balance sheets are filed only at half-year and year ends", eff
    if s.kind != "direction" and s.unit != line.unit:
        return "no_filed_figure", "the target's unit isn't the line's", eff
    if (s.kind == "range" and (s.low is None or s.high is None)) or (
            s.kind in ("point", "floor", "ceiling") and s.value is None) or (
            s.kind == "direction" and s.direction not in ("increase", "decrease", "maintain")):
        return "vague_figure", "no exact figure or direction to score", eff
    if line.formula == "growth" and s.growth_basis == "qoq":
        return "no_filed_figure", "the map's growth lines are year on year; quarter-on-quarter growth has no line", eff
    if s.period_kind == "multi_year" and line.formula == "growth" and s.growth_basis != "cagr":
        return "no_filed_figure", "a multi-year growth target is scored only as a CAGR", eff
    if s.kind == "direction" and IMPROVE.search(f"{s.quote or ''} {s.metric_text}"):
        if line.favourable == "none":
            return "vague_figure", "\"improve\" for a metric with no favourable side", eff
        eff = "increase" if line.favourable == "higher" else "decrease"
    return None, None, eff


def _span_start(s: Statement, line: Line) -> date:
    """The first day the figures cover: the base period's for a direction, a year earlier again for growth."""
    first = _base_period(s)[0] if s.kind == "direction" else s.start
    return shift(first, -12) if line.formula == "growth" else first


def _base_period(s: Statement) -> tuple[date, date]:
    n = 12 if s.comparison == "year_ago" else months(s.start, s.end)
    return shift(s.start, -n), shift(s.end, -n)


def score_company(c: Company, statements: list[Statement], m: Map, as_of: date) -> list[Outcome]:
    """Every outcome of one company's management statements, in guidance id order."""
    book = _Book(c)
    defs: dict[str, dict] = {}
    resolved = {}
    groups: dict[str, list[Statement]] = {}
    for s in statements:
        line = m.lines.get(s.metric)
        basis = scoring_basis(s, line)
        key = target_key(s, basis)
        resolved[s.id] = (line, basis, key, *statement_reason(s, line, c))
        groups.setdefault(key, []).append(s)
    firsts: dict[int, tuple[bool, str, int | None]] = {}
    for key, ss in groups.items():
        ss.sort(key=lambda s: (s.vintage, s.call_event_id, s.id))
        firsts[ss[0].id] = (True, "first", None)
        for prev, s in pairwise(ss):
            firsts[s.id] = (False, revision(prev, s), prev.id)
    out = []
    for s in sorted(statements, key=lambda s: s.id):
        line, basis, key, reason, note, eff = resolved[s.id]
        is_first, rev, prev_id = firsts[s.id]
        hz = horizon(s)
        inputs = {"rule_version": RULE_VERSION, "map_version": m.version,
                  "statement": _statement_dict(s, basis, eff)}
        if line is not None:
            inputs["map"] = line.as_dict()
        if prev_id is not None:
            inputs["previous_statement"] = prev_id
        o = _evaluate(c, book, s, line, basis, reason, note, eff, m, as_of, inputs, defs)
        forecast_ok = (reason is None and line is not None and basis == line.basis
                       and o.get("reason") != "no_filed_figure")
        counted = is_first and hz is not None and hz < Decimal("0.5") and o["outcome"] != "not_scored"
        out.append(Outcome(s.id, key, is_first, rev, hz, counted, o["outcome"], o.get("relation"), o.get("actual"),
                           o.get("base"), o.get("reason"), inputs, forecast_ok and s.kind in ("point", "range"),
                           o.get("unit_held"), o.get("definition")))
    return out


def _evaluate(c: Company, book: _Book, s: Statement, line: Line | None, basis: str, reason: str | None, note: str | None,
              eff: str | None, m: Map, as_of: date, inputs: dict, defs: dict) -> dict:
    def not_scored(why: str, text: str | None = None, **more) -> dict:
        inputs.update(outcome="not_scored", reason=why, **({"note": text} if text else {}))
        return {"outcome": "not_scored", "reason": why, **more}

    if reason is not None:
        return not_scored(reason, note)
    start = _span_start(s, line)
    inside = [b for b in c.breaks if start < b.effective <= s.end]
    if inside:
        inputs["structural_breaks"] = [{"kind": b.kind, "effective": b.effective.isoformat(), "source": b.source,
                                        "curated": b.curated} for b in sorted(inside, key=lambda b: b.effective)]
        inputs["span_start"] = start.isoformat()
        return not_scored("structural_break")
    if s.metric in DEFINITION_METRICS:
        if s.metric not in defs:
            defs[s.metric] = definition_check(c, line, m)
        inputs["definition"] = defs[s.metric]
        if not defs[s.metric]["passed"]:
            return not_scored("definition", definition=defs[s.metric])
    want = "standalone" if basis == "standalone" else None
    due = deadline(s.end, c.fy_end_month)
    try:
        actual = book.measure(line, m.columns, s.start, s.end, s.period_kind, want)
        base = None
        if s.kind == "direction":
            bs, be = _base_period(s)
            base = book.measure(line, m.columns, bs, be, s.period_kind, actual.basis)
    except UnitHeld as h:
        inputs["held"] = h.reason
        return not_scored("filed_figures_unavailable", unit_held=h.reason)
    except mx.Held as h:
        inputs["held"] = h.reason
        if h.reason.startswith("N/A:"):
            return not_scored("no_filed_figure", h.reason)
        inputs["deadline"] = due.isoformat()
        if as_of <= due:
            inputs["outcome"] = "pending"
            return {"outcome": "pending"}
        return not_scored("filed_figures_unavailable")
    inputs["actual"] = actual.as_dict()
    if base is not None:
        inputs["base"] = base.as_dict()
    v = judge(s.kind, line.unit, line.favourable, value=s.value, low=s.low, high=s.high, direction=eff,
              actual=actual.value, base=None if base is None else base.value)
    inputs["judge"] = v.detail
    return {"outcome": v.outcome, "relation": v.relation, "actual": actual.value,
            "base": None if base is None else base.value}
