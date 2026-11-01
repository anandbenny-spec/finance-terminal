"""One company's weekly screener row (Spec 02A §3.2; job 2A-15): every column of `equity.screener_snapshot` from the stored
metrics, the shareholding pattern and the valuation runs, with the label and the held-back reason of each column in
`notes` (`{"<column>": {"label": ..., "reason": ...}}`, the shape 2A-17's views read; an entry is present only when the
column has a label or a reason). Pure: the task (`ft/jobs/tasks/equity_snapshot.py`) reads the rows and stores the result.

**Where a column comes from** (stored metric rows only, nothing is recomputed here; gating is the views' job, so a hidden
column is stored as computed):
- market columns (price, market cap, shares, P/E, P/B, EV and its multiples, dividend yield, one-year return, median
  traded value, beta): the `market` rows at the snapshot date;
- the latest period at or before the snapshot date for the rest: `ebit_margin` (TTM), `roe` and the revenue CAGRs
  (financial years), `debt_to_equity` (balance sheet). When several bases are stored for the same period the consolidated
  one wins, then standalone, then none;
- promoter % from the newest shareholding pattern filed on or before the date;
- implied and delivered growth from the company's reverse-DCF run, implied and delivered ROE from its implied-ROE run.

**Labels** come from the metric catalogue's `label_kind` (derived, annualised, proxy), plus the stored inputs' own label
(debt excludes lease liabilities); the model columns say "model-implied". **Reasons** are the stored held reason with
2A-11's "N/A: " prefix removed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

MODEL_LABEL = "model-implied"
BASIS_ORDER = {"consolidated": 0, "standalone": 1, "none": 2}
# snapshot column → (metric key, period kind)
METRIC_COLUMNS: dict[str, tuple[str, str]] = {
    "shares": ("shares_today", "market"), "market_cap": ("market_cap", "market"), "pe_ttm": ("pe_ttm", "market"),
    "pb": ("pb", "market"), "ev": ("ev", "market"), "ev_ebit": ("ev_ebit_ttm", "market"),
    "ev_sales": ("ev_sales_ttm", "market"), "dividend_yield": ("dividend_yield", "market"),
    "return_1y": ("return_1y", "market"), "traded_value_median_3m": ("traded_value_median_3m", "market"),
    "beta": ("beta_bottom_up", "market"), "roe": ("roe", "year"), "ebit_margin": ("ebit_margin", "ttm"),
    "revenue_cagr_3y": ("revenue_cagr_3y", "year"), "revenue_cagr_5y": ("revenue_cagr_5y", "year"),
    "debt_to_equity": ("debt_to_equity", "instant"),
}
METRIC_KEYS = tuple(sorted({k for k, _ in METRIC_COLUMNS.values()}))
COLUMNS = ("price", "price_date", "market_cap", "shares", "pe_ttm", "pb", "ev", "ev_ebit", "ev_sales", "dividend_yield",
           "roe", "ebit_margin", "revenue_cagr_3y", "revenue_cagr_5y", "debt_to_equity", "promoter_pct", "beta",
           "return_1y", "traded_value_median_3m", "implied_growth", "delivered_growth", "implied_roe", "delivered_roe")
NO_PRICE = "The latest close isn't stored."
NO_SHAREHOLDING = "No shareholding pattern is filed."
NOT_LENDER = "Applies to lenders only."
NOT_OPERATING = "Applies to operating companies only; a lender's price is read through the return on equity it implies."
NO_RUN = "The valuation hasn't been run."

MetricRow = tuple[Decimal | None, str | None, dict]          # value, held reason, inputs


@dataclass(frozen=True)
class Run:
    """The fields of a stored `equity.valuation_run` row the snapshot reads."""
    status: str
    reason: str | None
    result: Decimal | None
    delivered: Decimal | None
    delivered_reason: str | None


@dataclass
class Row:
    snapshot_date: date
    values: dict = field(default_factory=dict)         # column → value (None when held)
    notes: dict = field(default_factory=dict)          # column → {"label": ..., "reason": ...}
    family: str = "operating"

    def put(self, column: str, value, label: str | None = None, reason: str | None = None) -> None:
        self.values[column] = value
        note = {k: v for k, v in (("label", label), ("reason", reason)) if v}
        if note:
            self.notes[column] = note


def strip_na(reason: str) -> str:
    return reason.removeprefix("N/A: ")


def _join(*labels: str | None) -> str | None:
    seen = [x for i, x in enumerate(labels) if x and x not in labels[:i]]
    return "; ".join(seen) or None


def pick(rows: dict[tuple, MetricRow], key: str, kind: str, upto: date, exact: bool) -> MetricRow | None:
    """The stored row of (key, kind) for the latest period end at or before `upto` (exactly `upto` for market rows),
    preferring the consolidated basis, then standalone, then none."""
    best = None
    for (k, basis, kd, end), row in rows.items():
        if k != key or kd != kind or end > upto or (exact and end != upto):
            continue
        rank = (-end.toordinal(), BASIS_ORDER.get(basis, 9))
        if best is None or rank < best[0]:
            best = (rank, row)
    return best[1] if best else None


def build(family: str, snapshot_date: date, rows: dict[tuple, MetricRow], kinds: dict[str, str | None],
          promoter: Decimal | None, reverse: Run | None, roe: Run | None) -> Row:
    """`rows` are the company's stored metric rows keyed (key, basis, kind, period end); `kinds` maps a metric key to its
    catalogue `label_kind`; `reverse` and `roe` are the company's latest reverse-DCF and implied-ROE runs."""
    out = Row(snapshot_date, family=family)
    for column, (key, kind) in METRIC_COLUMNS.items():
        got = pick(rows, key, kind, snapshot_date, exact=kind == "market")
        if got is None:
            out.put(column, None, reason=f"{key.replace('_', ' ').capitalize()} isn't stored.")
            continue
        value, held, inputs = got
        label = _join(kinds.get(key), (inputs or {}).get("label"))
        out.put(column, value, label, strip_na(held) if held else None)
    cap = pick(rows, "market_cap", "market", snapshot_date, exact=True)
    p = ((cap[2] or {}).get("price") if cap else None) or {}
    if p.get("close") is not None and p.get("trade_date"):
        out.put("price", Decimal(str(p["close"])))
        out.values["price_date"] = date.fromisoformat(p["trade_date"])
    else:
        out.put("price", None, reason=NO_PRICE)
        out.values["price_date"] = None
    if promoter is None:
        out.put("promoter_pct", None, reason=NO_SHAREHOLDING)
    else:
        out.put("promoter_pct", promoter)
    _model(out, family, "implied_growth", "delivered_growth", reverse, lender_only=False)
    _model(out, family, "implied_roe", "delivered_roe", roe, lender_only=True)
    return out


def _model(out: Row, family: str, implied: str, delivered: str, run: Run | None, *, lender_only: bool) -> None:
    applies = (family == "lender") if lender_only else (family != "lender")
    if not applies:
        out.put(implied, None, reason=NOT_LENDER if lender_only else NOT_OPERATING)
        out.put(delivered, None, reason=NOT_LENDER if lender_only else NOT_OPERATING)
        return
    if run is None:
        out.put(implied, None, MODEL_LABEL, NO_RUN)
        out.put(delivered, None, reason=NO_RUN)
        return
    out.put(implied, run.result, MODEL_LABEL, None if run.result is not None else strip_na(run.reason or NO_RUN))
    if run.delivered is not None:
        out.put(delivered, run.delivered)
    else:
        out.put(delivered, None, reason=strip_na(run.delivered_reason or "The delivered figure isn't stored."))
