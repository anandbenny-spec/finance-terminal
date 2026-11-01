"""Betas (Spec 02A §5.8; job 2A-12): the regression beta of weekly price returns against the Nifty 50 and Damodaran's
bottom-up beta relevered at the company's market debt to equity. Pure functions; `ft/jobs/tasks/equity_beta.py` reads
the database and writes `equity.metric_value`.

Regression: for each ISO week the company's adjusted close and the Nifty 50's close on the same date, the latest date in
the week on which both exist (Yahoo's `^NSEI` misses some special sessions, Data Audit 02a finding 23; the pair never
mixes days). 104 weekly returns ending with the last complete ISO week; a return needs the week before it, so a week
dropped for want of a common date also drops the return across it (it would span two weeks). Fewer than 80 returns:
held.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "config" / "equity"
WINDOW = 104
MIN_RETURNS = 80
BOUND = Decimal(5)                  # sanity bound on a stored beta (decided by Claude: it only adds a check)
DIFFERENCE = Decimal("0.5")         # |regression - bottom-up| beyond this is named (W)
PLACES = Decimal("0.000001")
LENDERS = ("lender", "insurer")


def tax_rate(path: Path | None = None) -> tuple[Decimal, str]:
    raw = yaml.safe_load((path or CONFIG / "valuation.yaml").read_text("utf-8"))["india_marginal_tax_rate"]
    rate = Decimal(str(raw["rate"]))
    if not Decimal(0) < rate < Decimal(1) or not raw.get("source"):
        raise ValueError("valuation.yaml: india_marginal_tax_rate needs a rate between 0 and 1 and its source")
    return rate, raw["source"]


def iso_week(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return y, w


def last_complete_week(latest: date) -> tuple[int, int]:
    """The newest ISO week whose Sunday is on or before `latest` (the newest Nifty date stored)."""
    sunday = latest + timedelta(days=(6 - latest.weekday()))
    return iso_week(sunday if sunday <= latest else sunday - timedelta(days=7))


def week_index(wk: tuple[int, int]) -> int:
    """Weeks since a fixed Monday: consecutive ISO weeks differ by one, across year ends."""
    return (date.fromisocalendar(wk[0], wk[1], 1) - date(2000, 1, 3)).days // 7


@dataclass
class Regression:
    n: int
    slope: Decimal | None = None
    stderr: Decimal | None = None
    r2: Decimal | None = None
    held: str | None = None
    last_date: date | None = None
    inputs: dict = field(default_factory=dict)


def _q(x: float) -> Decimal:
    return Decimal(repr(x)).quantize(PLACES)


def regress(stock: dict[date, Decimal], index: dict[date, Decimal], latest_index: date) -> Regression:
    """`stock`: the company's adjusted closes by date (nulls left out); `index`: the Nifty's closes by date."""
    last = week_index(last_complete_week(latest_index))
    first = last - WINDOW                                        # 105 weekly closes make 104 returns
    by_week: dict[int, date] = {}
    company_last: dict[int, date] = {}
    for d in sorted(stock):
        company_last[week_index(iso_week(d))] = d
    for d in sorted(stock.keys() & index.keys()):
        wi = week_index(iso_week(d))
        if first <= wi <= last:
            by_week[wi] = d                                      # sorted: the latest common date of the week wins
    other_day = sum(1 for wi, d in by_week.items() if company_last[wi] != d)
    dropped = [wi for wi in range(first, last + 1) if wi not in by_week]
    xs, ys = [], []
    for wi in range(first + 1, last + 1):
        if wi in by_week and wi - 1 in by_week:
            d0, d1 = by_week[wi - 1], by_week[wi]
            ys.append(float(stock[d1] / stock[d0] - 1))
            xs.append(float(index[d1] / index[d0] - 1))
    ends = [by_week[wi] for wi in sorted(by_week)]
    inputs = {"index": "yf.^NSEI", "window_weeks": WINDOW, "returns": len(xs), "weeks_without_common_date": len(dropped),
              "weeks_on_an_earlier_common_date": other_day,
              "first_week_close": ends[0].isoformat() if ends else None,
              "last_week_close": ends[-1].isoformat() if ends else None}
    out = Regression(len(xs), last_date=ends[-1] if ends else None, inputs=inputs)
    if len(xs) < MIN_RETURNS:
        out.held = "Fewer than 80 weeks of prices."
        return out
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0:
        out.held = "The Nifty 50 did not move over the window."
        return out
    slope = sxy / sxx
    sse = max(syy - slope * sxy, 0.0)
    out.slope = _q(slope)
    out.stderr = _q(math.sqrt(sse / (n - 2) / sxx))
    out.r2 = _q(0.0 if syy == 0 else 1 - sse / syy)
    out.inputs.update(slope=str(out.slope), stderr=str(out.stderr), r2=str(out.r2), n=n)
    return out


def relever(unlevered: Decimal, debt: Decimal, market_cap: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """(beta, D/E): unlevered x (1 + (1 - rate) x D/E)."""
    de = debt / market_cap
    return (unlevered * (1 + (1 - rate) * de)).quantize(PLACES), de.quantize(PLACES)
