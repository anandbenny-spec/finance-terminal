"""policy.market_series: FRED and Yahoo Finance series for the policy tracker and the ticker tape (Prompt 01 B4;
Architecture 02 §2; Spec 01 §1.1, §2).

Fetches, for every observed series of the `fred` and `yfinance` sources in core.series (8 published FRED series plus
`fred.SP500`, and 8 Yahoo series):
- daily (every 20 hours): FRED with a 14-day look-back, Yahoo with a one-month look-back (Architecture 02 §1.5: late
  publications and revisions are caught, and logged by the revision trigger);
- backfill (`--workflow backfill --task policy.market_series`): from `--since`, else 1 Dec 2018, to `--until`, else
  today, one request per series.
FRED through its API (`FRED_API_KEY`, sent as a secret parameter, never recorded); Yahoo through its chart API, the
endpoint the yfinance library reads, so both go through the shared HTTP client (pacing, retries, counts) and every
response is stored as a raw file before the load (B2's open question 4, decided here: issue #13). Yahoo is asked
as an ordinary HTTP client with the project's own User-Agent; no browser fingerprint is imitated. If Yahoo refuses
that from GitHub's runners (429s, counted in `http_stats`), the route is Anand's call (issue #13).

Loads every point as a core.observation; a stored value that differs is updated (the trigger logs the revision).
Checks (Architecture 02 §2; Spec 01 §2), B blocking, W warning:
- (B) `responses`: each Yahoo chart is the symbol asked for, in the series' currency;
- (B) `bounds`: yields and rates −1% to 25%; USD/INR 50–150; index levels above 0 with a daily move under 20%;
- (B) `revisions`: no more than 5% of the run's look-back values revised;
- (B) `gspc_vs_sp500`: `yf.^GSPC` within 0.05% of `fred.SP500` on every date both have, over the full stored overlap;
- (W) `lag`: each series' latest value within its lag (daily 5 days, weekly-published 12, monthly 75).
Then a series is published once a load has passed its bounds and lag checks (Spec 01 §2); `fred.SP500` never is
(Spec 01 D3). Published series stay published: a later lag warning shows as stale, it doesn't hide the series.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise

from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register
from ft.policy import series_sources as src

HISTORY_START = date(2018, 12, 1)                 # Prompt 01 B4: history from 1 Dec 2018
LOOKBACK = {"fred": timedelta(days=14), "yfinance": timedelta(days=31)}
# A monthly series is dated at the start of its month and published weeks later (INDIRLTLT01STM: September's value,
# dated 1 Sep, came out in October), so a 14-day look-back would never reach a new value: 100 days covers its
# 75-day lag (decided by Claude under the standing delegation, 24 Oct 2026; issue #13).
MONTHLY_LOOKBACK = timedelta(days=100)
# Bounds (B), by unit: Architecture 02 §2. Yields (and the policy rates, also in percent) −1% to 25%; USD/INR 50–150;
# index levels above 0 with a daily move under 20%.
RATE_BOUNDS = {"percent": (Decimal(-1), Decimal(25)), "inr_per_usd": (Decimal(50), Decimal(150))}
MAX_DAILY_MOVE = Decimal("0.20")
MAX_REVISED = Decimal("0.05")
CROSS_TOLERANCE = Decimal("0.0005")               # 0.05%
# Lags (W): daily 5 days, weekly-published 12, monthly 75. DTWEXBGS and DEXINUS (H.10) are daily values published
# weekly (Data Audit 01 #21, #24).
WEEKLY_PUBLISHED = frozenset({"DTWEXBGS", "DEXINUS"})
LAGS = {"daily": timedelta(days=5), "weekly": timedelta(days=12), "monthly": timedelta(days=75)}


@dataclass(frozen=True)
class Series:
    id: int
    key: str
    source: str            # core.source key: fred or yfinance
    code: str
    unit: str
    frequency: str
    fixing_time: time | None
    fixing_tz: str | None
    currency: str | None
    is_published: bool

    @property
    def lag_kind(self) -> str:
        if self.frequency == "M":
            return "monthly"
        return "weekly" if self.source == "fred" and self.code in WEEKLY_PUBLISHED else "daily"


@dataclass
class Got:
    series: Series
    start: date
    end: date
    points: list[src.Point]
    meta_problem: str | None = None


@dataclass
class Fetched:
    now: datetime
    full: bool
    got: list[Got] = field(default_factory=list)


@register
class MarketSeries(Task):
    key = "policy.market_series"
    series_like: tuple[str, ...] = ("fred.%", "yf.%")     # tests point this at their own series
    cross_check: tuple[str, str] = ("yf.^GSPC", "fred.SP500")
    never_publish: frozenset[str] = frozenset({"fred.SP500"})   # Spec 01 D3: a cross-check and fallback, never shown

    def __init__(self):
        self.result: dict = {}

    def _series(self, db) -> list[Series]:
        rows = db.execute(
            """select s.id, s.key, src.key, s.source_code, s.unit, s.frequency, s.fixing_time, s.fixing_tz, s.currency,
                      s.is_published
                 from core.series s join core.source src on src.id = s.source_id
                where s.kind = 'observed' and src.key in ('fred', 'yfinance') and s.key like any(%s)
                order by s.key""", (list(self.series_like),)).fetchall()
        return [Series(*r) for r in rows]

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        full = ctx.workflow == "backfill"
        today = ctx.now.date()
        end = min(ctx.until or today, today)
        series = self._series(f.db)
        if not series:
            raise RuntimeError("no fred or yfinance series in core.series")
        out = Fetched(ctx.now, full)
        key = None
        for s in series:
            back = MONTHLY_LOOKBACK if s.frequency == "M" else LOOKBACK[s.source]
            start = (ctx.since or HISTORY_START) if full else end - back
            if s.source == "fred":
                if key is None:
                    key = os.environ.get("FRED_API_KEY")
                    if not key:
                        raise RuntimeError("FRED_API_KEY is not set")
                got = f.get(src.FRED_URL, source="fred", params=src.fred_params(s.code, start, end),
                            secret_params={"api_key": key}, media_type="application/json")
                body = got.body if got.body is not None else f.read(got.raw_file_id)
                out.got.append(Got(s, start, end, src.parse_fred(body, s.code)))
            else:
                got = f.get(src.yahoo_url(s.code), source="yfinance", params=src.yahoo_params(start, end),
                            media_type="application/json")
                body = got.body if got.body is not None else f.read(got.raw_file_id)
                chart = src.parse_yahoo(body, s.code, s.unit, fetched_at=got.fetched_at, fixing_time=s.fixing_time,
                                        fixing_tz=s.fixing_tz)
                problems = []
                if chart.symbol != s.code:
                    problems.append(f"symbol {chart.symbol!r}")
                if s.currency and chart.currency != s.currency:
                    problems.append(f"currency {chart.currency!r}, not {s.currency}")
                if s.unit == "inr_per_usd" and chart.currency != "INR":
                    problems.append(f"quoted in {chart.currency!r}, not INR")
                out.got.append(Got(s, start, end, [p for p in chart.points if start <= p[0] <= end],
                                   "; ".join(problems) or None))
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        written, revised, values = 0, {}, 0
        for g in f.got:
            stored = dict(tx.execute("select obs_date, value from core.observation where series_id = %s "
                                     "and obs_date between %s and %s", (g.series.id, g.start, g.end)).fetchall())
            n_rev = 0
            for d, v in g.points:
                values += 1
                old = stored.get(d)
                if old is None:
                    tx.execute("insert into core.observation (series_id, obs_date, value, task_run_id) "
                               "values (%s, %s, %s, %s)", (g.series.id, d, v, task_run_id))
                    written += 1
                elif old != v:
                    tx.execute("update core.observation set value = %s, task_run_id = %s "
                               "where series_id = %s and obs_date = %s", (v, task_run_id, g.series.id, d))
                    written += 1
                    n_rev += 1
            if n_rev:
                revised[g.series.key] = n_rev
        self.result = {"revised": revised, "values": values, "published": []}
        return written

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out: list[Check] = []
        today = f.now.date()

        bad_meta = [f"{g.series.key}: {g.meta_problem}" for g in f.got if g.meta_problem]
        out.append(Check("responses", not bad_meta, "; ".join(bad_meta)))

        # (B) Bounds, on the values this run fetched; daily moves against the stored value before each.
        out_of_bounds: dict[str, list[str]] = {}
        for g in f.got:
            if not g.points:
                continue
            s, problems = g.series, []
            if s.unit in RATE_BOUNDS:
                lo, hi = RATE_BOUNDS[s.unit]
                problems += [f"{d} {v}" for d, v in g.points if not lo <= v <= hi]
            elif s.unit == "index_points":
                rows = tx.execute(
                    """select obs_date, value from core.observation
                        where series_id = %(id)s and obs_date <= %(end)s
                          and obs_date >= coalesce((select max(obs_date) from core.observation
                                                     where series_id = %(id)s and obs_date < %(start)s), %(start)s)
                        order by obs_date""", {"id": s.id, "start": g.points[0][0], "end": g.points[-1][0]}).fetchall()
                fetched = {d for d, _ in g.points}
                problems += [f"{d} {v}" for d, v in g.points if v <= 0]
                for (_, prev), (d, v) in pairwise(rows):
                    if d in fetched and prev > 0 and abs(v / prev - 1) >= MAX_DAILY_MOVE:
                        problems.append(f"{d} {v} after {prev} ({(v / prev - 1) * 100:+.1f}%)")
            else:
                problems.append(f"no bounds rule for unit {s.unit}")
            if problems:
                out_of_bounds[s.key] = problems
        out.append(Check("bounds", not out_of_bounds,
                         "; ".join(f"{k}: {', '.join(v[:5])}" for k, v in out_of_bounds.items())))

        # (B) No more than 5% of the run's look-back values revised (across the run's series).
        r = self.result
        n_rev = sum(r["revised"].values())
        share = Decimal(n_rev) / r["values"] if r["values"] else Decimal(0)
        out.append(Check("revisions", share <= MAX_REVISED,
                         f"{n_rev} of {r['values']} values revised ({share * 100:.1f}%)"
                         + (": " + ", ".join(f"{k} {n}" for k, n in sorted(r["revised"].items())) if n_rev else "")))

        # (B) yf.^GSPC within 0.05% of fred.SP500 on every date both have, over the whole stored overlap.
        keys = {g.series.key for g in f.got}
        a, b = self.cross_check
        if a in keys and b in keys:
            rows = tx.execute(
                """select ya.obs_date, ya.value, fb.value from core.observation ya
                     join core.observation fb on fb.obs_date = ya.obs_date
                    where ya.series_id = (select id from core.series where key = %s)
                      and fb.series_id = (select id from core.series where key = %s)
                    order by ya.obs_date""", (a, b)).fetchall()
            off = [f"{d} {x} vs {y}" for d, x, y in rows if y == 0 or abs(x / y - 1) > CROSS_TOLERANCE]
            out.append(Check("gspc_vs_sp500", not off,
                             f"{len(off)} of {len(rows)} dates off by more than 0.05%: " + ", ".join(off[:5]) if off
                             else f"{len(rows)} dates within 0.05%"))

        # (W) Latest value within its lag.
        latest = dict(tx.execute("select series_id, max(obs_date) from core.observation where series_id = any(%s) "
                                 "group by series_id", ([g.series.id for g in f.got],)).fetchall())
        late = {}
        for g in f.got:
            last = latest.get(g.series.id)
            if last is None or today - last > LAGS[g.series.lag_kind]:
                late[g.series.key] = f"{g.series.key}: latest {last or 'none'} ({g.series.lag_kind}, " \
                                     f"allowed {LAGS[g.series.lag_kind].days} days)"
        out.append(Check("lag", not late, "; ".join(late.values()), blocking=False))

        # Publish each series whose load passed its bounds and lag checks (only if the whole load stands).
        if all(c.passed for c in out if c.blocking):
            fresh = [g.series for g in f.got
                     if not g.series.is_published and g.series.key not in self.never_publish
                     and g.series.key not in late and g.series.key not in out_of_bounds
                     and latest.get(g.series.id) is not None]
            for s in fresh:
                tx.execute("update core.series set is_published = true where id = %s and not is_published", (s.id,))
            r["published"] = [s.key for s in fresh]
            out.append(Check("published", True, ", ".join(r["published"]) or "none newly published", blocking=False))
        return out
