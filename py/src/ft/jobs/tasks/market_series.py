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
response is stored as a raw file before the load (B2's open question 4, decided here: issue #13). Yahoo refused a
plain HTTP client from GitHub's runners (HTTP 429 on every attempt, 24 Oct 2026), so, as Anand decided (option (a)
on #13, 24 Oct 2026), the client sends Yahoo's requests with Chrome's fingerprint (`ft.jobs.fetch.CurlTransport`)
and this task gets Yahoo's cookie and crumb first, the way yfinance does: fc.yahoo.com for the cookie, then
`/v1/test/getcrumb`. The crumb goes on each chart request as a secret parameter, so the recorded URLs stay the same
from run to run; neither response is data, so neither is stored. Without a crumb the charts are still asked for
(yfinance does the same), and the `yahoo_session` warning says why.

Loads every point as a core.observation; a stored value that differs is updated (the trigger logs the revision).
The load reads back the stored value of every date the response holds, not only the window asked for: FRED answers a
monthly series from the first of the month holding `observation_start`, so the 28 Oct 2026 production daily, asking
for India's 10-year from 20 Jul, got 1 Jul too, and inserted it a second time (UniqueViolation; issue #149). A date
outside the window is compared like any other: the same value writes nothing, a different one is a logged revision
(the source's latest is kept, the old value is in core.observation_revision, and the blocking `revisions` check
counts it), and a date not stored is inserted. A date a response sends twice with different values is held back,
neither stored nor updated, and listed in the `repeated_dates` warning (series_sources: one value per date).
The writes go in batches, one pipelined `executemany` per series for its inserts and one for its updates (Anand,
24 Oct 2026, issue #13): the first dev backfill spent 2 h 11 min on 33,518 single-row statements, each a round trip
from GitHub's US runner to the Mumbai project. The updates stay real UPDATEs, so the trigger still logs each one.

A Yahoo S&P 500 close that disagrees with FRED's is held back (Anand, 24 Oct 2026, option (a) on #13): where
`yf.^GSPC` and `fred.SP500` both have a date (FRED's value fetched in this run, else stored) and differ by more than
0.05%, `yf.^GSPC`'s observation for that date is neither stored nor updated. Its raw file is stored like every
response, so nothing fetched is lost, and FRED's value is never shown in its place (Spec 01 D3). The first was
2021-08-11: Yahoo 4,442.41, FRED 4,447.70, and FRED is right.

Checks (Architecture 02 §2; Spec 01 §2), B blocking, W warning:
- (B) `responses`: each Yahoo chart is the symbol asked for, in the series' currency;
- (B) `bounds`: yields and rates −1% to 25%; USD/INR 50–150; index levels above 0 with a daily move under 20%;
- (B) `revisions`: no more than 5% of the run's look-back values revised;
- (B) `gspc_vs_sp500`: `yf.^GSPC` within 0.05% of `fred.SP500` on every date both have stored, over the full stored
  overlap, and no more than 1% of the overlap held back (more is a systematic break, not a stray bad print). A stored
  value is never deleted (jobs can't delete source data), so a stored date that comes to disagree (FRED revises, or
  arrives a day late) still blocks: that case is Anand's;
- (W) `gspc_held_back`: each held-back date, with both values;
- (W) `repeated_dates`: each date a response sent twice with different values, with the values (held back);
- (W) `lag`: each series' latest value within its lag (daily 5 days, weekly-published 12, monthly 75);
- (W) `yahoo_session`: whether Yahoo's crumb was received.
Then a series is published once a load has passed its bounds and lag checks (Spec 01 §2); `fred.SP500` never is
(Spec 01 D3). Published series stay published: a later lag warning shows as stale, it doesn't hide the series.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise

from ft.jobs.fetch import FetchError
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
MAX_HELD_BACK = Decimal("0.01")                   # 1% of the overlap (Anand, 24 Oct 2026, #13)
HELD_BACK_LISTED = 50                             # the warning lists each held-back date up to this many
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
    repeated: src.Repeated = field(default_factory=dict)   # sent twice with different values: held back


@dataclass
class Fetched:
    now: datetime
    full: bool
    got: list[Got] = field(default_factory=list)
    yahoo_session: str | None = None       # None: no Yahoo series fetched; else "crumb received" or why not


def disagree(x: Decimal, y: Decimal) -> bool:
    """`x` more than 0.05% from `y` (or `y` zero)."""
    return y == 0 or abs(x / y - 1) > CROSS_TOLERANCE


def yahoo_crumb(fetcher) -> tuple[str | None, str]:
    """Yahoo's cookie (kept in the run's HttpClient, sent with every later Yahoo request) and crumb, as yfinance
    gets them. Returns (crumb or None, what happened). Counted in http_stats like every request; not stored."""
    try:
        fetcher.http.get(src.YAHOO_COOKIE_URL, stats=fetcher.counts, follow_redirects=False)
        r = fetcher.http.get(src.YAHOO_CRUMB_URL, stats=fetcher.counts)
    except FetchError as e:
        return None, f"no crumb: {e}"
    crumb = src.parse_crumb(r.status, r.body)
    return (crumb, "crumb received") if crumb else (None, f"no crumb: getcrumb answered HTTP {r.status}")


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
                # Every point FRED sends, the month before `start` included (issue #149): load compares them all.
                obs = src.parse_fred(body, s.code)
                out.got.append(Got(s, start, end, obs.points, repeated=obs.repeated))
            else:
                if out.yahoo_session is None:
                    crumb, out.yahoo_session = yahoo_crumb(f)
                got = f.get(src.yahoo_url(s.code), source="yfinance", params=src.yahoo_params(start, end),
                            secret_params={"crumb": crumb} if crumb else None, media_type="application/json")
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
                                   "; ".join(problems) or None,
                                   {d: vs for d, vs in chart.repeated.items() if start <= d <= end}))
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def _held_back(self, tx, f: Fetched) -> dict[date, tuple[Decimal, Decimal]]:
        """The fetched `yf.^GSPC` closes more than 0.05% from `fred.SP500` on the same date: {date: (Yahoo, FRED)}.
        FRED's value is this run's where it fetched one (it's what the load will store), else the stored one."""
        a, b = self.cross_check
        ga = next((g for g in f.got if g.series.key == a), None)
        if ga is None or not ga.points:
            return {}
        start, end = ga.points[0][0], ga.points[-1][0]
        fred = dict(tx.execute("""select o.obs_date, o.value from core.observation o
                                     join core.series s on s.id = o.series_id
                                    where s.key = %s and o.obs_date between %s and %s""", (b, start, end)).fetchall())
        for g in f.got:
            if g.series.key == b:
                fred.update((d, v) for d, v in g.points if start <= d <= end)
        return {d: (v, fred[d]) for d, v in ga.points if d in fred and disagree(v, fred[d])}

    def _write(self, tx, new: list[tuple], changed: list[tuple]) -> None:
        """One series' inserts and updates, each as one pipelined executemany. The updates are real UPDATEs, so the
        revision trigger logs every change."""
        with tx.cursor() as cur:
            if new:
                cur.executemany("insert into core.observation (series_id, obs_date, value, task_run_id) "
                                "values (%s, %s, %s, %s)", new)
            if changed:
                cur.executemany("update core.observation set value = %s, task_run_id = %s "
                                "where series_id = %s and obs_date = %s", changed)

    def load(self, tx, f: Fetched, task_run_id) -> int:
        written, revised, values = 0, {}, 0
        held = self._held_back(tx, f)
        for g in f.got:
            dates = [d for d, _ in g.points]
            twice = sorted(d for d, n in Counter(dates).items() if n > 1)
            if twice:      # the parsers keep one value per date, so this never happens; if it did, say where
                raise RuntimeError(f"{g.series.key}: {', '.join(map(str, twice[:5]))} more than once in the points")
            # The stored value of every date the response holds, inside the window asked for or not (issue #149).
            stored = dict(tx.execute("select obs_date, value from core.observation where series_id = %s "
                                     "and obs_date = any(%s)", (g.series.id, dates)).fetchall()) if dates else {}
            skip = held if g.series.key == self.cross_check[0] else {}
            new, changed = [], []
            for d, v in g.points:
                values += 1
                if d in skip:
                    continue                    # held back: neither stored nor updated; its raw file is kept
                old = stored.get(d)
                if old is None:
                    new.append((g.series.id, d, v, task_run_id))
                elif old != v:
                    changed.append((v, task_run_id, g.series.id, d))
            self._write(tx, new, changed)
            written += len(new) + len(changed)
            if changed:
                revised[g.series.key] = len(changed)
        self.result = {"revised": revised, "values": values, "published": [], "held_back": held,
                       "repeated": {g.series.key: g.repeated for g in f.got if g.repeated}}
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

        # (B) yf.^GSPC within 0.05% of fred.SP500 on every date both have stored, over the whole stored overlap, and
        # no more than 1% of the overlap (the stored overlap plus the dates held back) held back. Runs whenever
        # yf.^GSPC was fetched (the held-back dates were compared with FRED's fetched or stored values).
        keys = {g.series.key for g in f.got}
        a, b = self.cross_check
        if a in keys:
            rows = tx.execute(
                """select ya.obs_date, ya.value, fb.value from core.observation ya
                     join core.observation fb on fb.obs_date = ya.obs_date
                    where ya.series_id = (select id from core.series where key = %s)
                      and fb.series_id = (select id from core.series where key = %s)
                    order by ya.obs_date""", (a, b)).fetchall()
            off = [f"{d} {x} vs {y}" for d, x, y in rows if disagree(x, y)]
            held = r["held_back"]
            overlap = len({d for d, _, _ in rows} | held.keys())
            share = Decimal(len(held)) / overlap if overlap else Decimal(0)
            too_many = share > MAX_HELD_BACK
            parts = []
            if off:
                parts.append(f"{len(off)} of {len(rows)} stored dates off by more than 0.05%: " + ", ".join(off[:5]))
            else:
                parts.append(f"{len(rows)} dates within 0.05%")
            if held:
                parts.append(f"{len(held)} of {overlap} dates held back ({share * 100:.2f}%"
                             + (", more than 1%)" if too_many else ")"))
            out.append(Check("gspc_vs_sp500", not off and not too_many, "; ".join(parts)))
            # (W) Each held-back date, with both values.
            listed = [f"{d} Yahoo {yahoo} vs FRED {fred}"
                      for d, (yahoo, fred) in sorted(held.items())[:HELD_BACK_LISTED]]
            more = f", and {len(held) - HELD_BACK_LISTED} more" if len(held) > HELD_BACK_LISTED else ""
            out.append(Check("gspc_held_back", not held,
                             f"{len(held)} held back: " + ", ".join(listed) + more if held else "none held back",
                             blocking=False))

        # (W) Dates a response sent twice with different values: held back, each listed with its values.
        rep = [f"{k} {d}: {' vs '.join(map(str, vs))}" for k, by_date in sorted(r["repeated"].items())
               for d, vs in sorted(by_date.items())]
        out.append(Check("repeated_dates", not rep,
                         f"{len(rep)} held back: " + ", ".join(rep[:HELD_BACK_LISTED])
                         + (f", and {len(rep) - HELD_BACK_LISTED} more" if len(rep) > HELD_BACK_LISTED else "")
                         if rep else "none", blocking=False))

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
        if f.yahoo_session is not None:
            out.append(Check("yahoo_session", f.yahoo_session == "crumb received", f.yahoo_session, blocking=False))

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
