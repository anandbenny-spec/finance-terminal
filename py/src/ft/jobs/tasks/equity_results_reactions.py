"""equity.results_reactions: how each company and the Nifty 50 moved around its results filings (Spec 02B §5.12; job 2B-16;
Spec 02A §5.10). Rows go to `core.event_reaction` through the reactions engine (`ft/engines/reactions.py`, unchanged).

Database only: it fetches nothing. **Hidden:** no field-policy row, `api` view or page exists for it (`equity.results_move`
fails closed); the rows live only in `core.event_reaction`, which the Data API doesn't serve.

**Events.** Every `results_filing` event (filed or superseded: a revision is a filing too) whose `occurs_at` (the
dissemination time) is on or after `WINDOW_START` (1 Jan 2025, the start of the stored earnings-call history). Windows `d1`
(the last close before → the first close after) and `d5` (→ the fifth close after). A release in market hours takes that
day's close as its first close after (the engine reads closes fixed strictly after `occurs_at`), so the move includes the
part of the day before the release; an evening or weekend release reads the next session.

**Subjects.** The event's company (`equity.price_adjusted`'s adjusted close, 15:30 IST; `value_basis = adjusted_close`,
source NSE) and `yf.^NSEI`. The excess over the Nifty 50 is computed later, in a view, not stored.

**Exclusions** (no row; counted by reason): `dividend` (the company's ex-dividend date falls in the window: pre date <
ex-date <= post date), `held_factor` (a held factor's ex-date is in the window: the adjusted close is null, never filled),
`no_pre` (no price before the release). A window whose post close hasn't arrived is `waiting`, not an exclusion.

**Rows are written once.** A row that exists is never touched and nothing is deleted: each run inserts only the
event × subject × window rows that are missing (a re-run writes nothing).

Checks, B blocking, W warning:
- (B) `series`: the Nifty 50 series exists, observed, with a fixing time and zone;
- (B) `observations`: every series row's two observations exist, and every row's post date is after its pre date;
- (W) `exclusions`: windows left out, by reason (and the no-Nifty-row windows);
- `counts` (informational): events in the window, events with both subjects' rows, exclusions by reason, rows stored and
  inserted.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ft.engines.reactions import (
    D1,
    D5,
    Anchor,
    Reaction,
    load_company_subject,
    load_series_subject,
    react,
)
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

WINDOW_START = date(2025, 1, 1)         # results filings count from here (Q3 FY25, the earliest stored call)
INDEX = "yf.^NSEI"
SEARCH_DAYS = 31
WINDOWS = (D1, D5)
WINDOW_KEYS = tuple(w.key for w in WINDOWS)
IST = ZoneInfo("Asia/Kolkata")
SHOWN = 8


@dataclass
class Result:
    events: int = 0
    both: int = 0                                                             # events with both subjects' d1 and d5 rows
    exclusions: Counter = field(default_factory=Counter)                      # reason -> windows
    examples: dict[str, list[str]] = field(default_factory=dict)
    waiting: int = 0
    no_nifty: int = 0
    problems: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    unverified: int = 0
    bad_order: int = 0


@register
class EquityResultsReactions(Task):
    key = "equity.results_reactions"
    requires = "core.event_reaction"
    index_key: str = INDEX
    slug_prefix: str = ""                         # tests restrict the events to their own companies

    def __init__(self):
        self.result = Result()

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    def fetch(self, ctx: Context) -> Result:      # nothing to fetch: the database holds every input
        self.result = Result()
        return self.result

    def load(self, tx, r: Result, task_run_id) -> int:
        row = tx.execute("""select id, kind, fixing_time is not null and fixing_tz is not null, source_id
                              from core.series where key = %s""", (self.index_key,)).fetchone()
        if row is None:
            r.problems.append(f"{self.index_key} missing")
        elif row[1] != "observed":
            r.problems.append(f"{self.index_key} is not an observed series")
        elif not row[2]:
            r.problems.append(f"{self.index_key} has no fixing time and zone")
        if r.problems:
            return 0
        index_id, index_source = row[0], row[3]
        nse = tx.execute("select id from core.source where key = 'nse'").fetchone()[0]
        start = datetime.combine(WINDOW_START, time(0), tzinfo=IST)
        events = tx.execute(
            """select e.id, e.entity_id, e.slug, e.occurs_at from core.event e
                 join equity.company c on c.entity_id = e.entity_id
                 join core.entity n on n.id = e.entity_id
                where e.event_type = 'results_filing' and e.status in ('filed', 'superseded') and e.is_published
                  and e.occurs_at >= %s and n.slug like %s and n.market = 'IN'
                order by e.occurs_at, e.id""", (start, self.slug_prefix + "%")).fetchall()
        r.events = len(events)
        wanted: dict[tuple, tuple[Reaction, int]] = {}
        if events:
            lo = min(e[3] for e in events).astimezone(IST).date() - timedelta(days=SEARCH_DAYS + 3)
            hi = tx.execute("select current_date").fetchone()[0] + timedelta(days=1)
            nifty = load_series_subject(tx, index_id, lo, hi)
            dividends = self._dividends(tx)
            subjects: dict[int, object] = {}
            for event_id, entity_id, slug, occurs_at in events:
                anchor = Anchor.decision(event_id, occurs_at)
                if entity_id not in subjects:
                    subjects[entity_id] = load_company_subject(tx, entity_id, lo, hi)
                subject = subjects[entity_id]
                for w in WINDOWS:
                    out = react(nifty, anchor, w, search_days=SEARCH_DAYS)
                    if out.reaction is None:
                        if out.missing == "no_post":
                            r.waiting += 1
                        else:
                            r.no_nifty += 1
                    else:
                        wanted[(event_id, index_id, None, w.key)] = (out.reaction, index_source)
                    out = react(subject, anchor, w, search_days=SEARCH_DAYS) if len(subject) else None
                    if out is None:
                        self._exclude(r, "no_pre", slug, w.key)
                        continue
                    x = out.reaction
                    if x is None:
                        if out.missing == "no_post":
                            r.waiting += 1
                        else:
                            self._exclude(r, "held_factor" if out.missing == "held" else out.missing, slug, w.key)
                        continue
                    if any(x.pre_date < d <= x.post_date for d in dividends.get(entity_id, ())):
                        self._exclude(r, "dividend", slug, w.key)
                        continue
                    wanted[(event_id, None, entity_id, w.key)] = (x, nse)
                if all((event_id, index_id, None, k) in wanted and (event_id, None, entity_id, k) in wanted
                       for k in WINDOW_KEYS):
                    r.both += 1
        stored = {(e, s, c, w) for e, s, c, w in tx.execute(
            """select event_id, series_id, entity_id, window_key from core.event_reaction
                where window_key = any(%s) and event_id = any(%s)""",
            (list(WINDOW_KEYS), [e[0] for e in events])).fetchall()}
        new = [(k, v) for k, v in wanted.items() if k not in stored]
        with tx.cursor() as cur:
            if new:
                cur.executemany(
                    """insert into core.event_reaction (event_id, series_id, entity_id, window_key, source_id, pre_date,
                                                        post_date, pre_value, post_value, change, change_unit, value_basis,
                                                        task_run_id)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       on conflict (event_id, coalesce(series_id, 0), coalesce(entity_id, 0), window_key) do nothing""",
                    [(e, s, c, w, src, x.pre_date, x.post_date, x.pre_value, x.post_value, x.change, x.change_unit,
                      x.value_basis, task_run_id) for (e, s, c, w), (x, src) in new])
        r.bad_order = sum(1 for x, _ in wanted.values() if x.post_date <= x.pre_date)
        r.unverified = tx.execute(
            """select count(*) from core.event_reaction r
                where r.series_id = %s and r.window_key = any(%s)
                  and (not exists (select 1 from core.observation o where o.series_id = r.series_id
                                      and o.obs_date = r.pre_date)
                    or not exists (select 1 from core.observation o where o.series_id = r.series_id
                                      and o.obs_date = r.post_date))""",
            (index_id, list(WINDOW_KEYS))).fetchone()[0]
        r.counts = {"computed": len(wanted), "inserted": len(new)}
        return len(new)

    @staticmethod
    def _exclude(r: Result, reason: str, slug: str, window: str) -> None:
        r.exclusions[reason] += 1
        ex = r.examples.setdefault(reason, [])
        if len(ex) < 3:
            ex.append(f"{slug} {window}")

    @staticmethod
    def _dividends(tx) -> dict[int, tuple[date, ...]]:
        out: dict[int, list[date]] = {}
        for eid, ex in tx.execute(
                """select e.entity_id, a.ex_date from equity.corporate_action a join core.event e on e.id = a.event_id
                    where a.kind = 'dividend' and e.entity_id is not null"""):
            out.setdefault(eid, []).append(ex)
        return {k: tuple(v) for k, v in out.items()}

    def checks(self, tx, r: Result, task_run_id) -> list[Check]:
        out = [Check("series", not r.problems, "; ".join(r.problems) or f"{self.index_key} has a fixing time and zone")]
        if r.problems:
            return out
        bad = r.unverified + r.bad_order
        out.append(Check("observations", not bad,
                         f"{r.unverified} series row(s) with a missing observation, {r.bad_order} with a post date "
                         "not after the pre date" if bad
                         else "every series row's two observations exist and every post date follows its pre date"))
        why = ", ".join(f"{k} {n}" for k, n in sorted(r.exclusions.items())) or "none"
        eg = "; ".join(f"{k}: {', '.join(v[:SHOWN])}" for k, v in sorted(r.examples.items()))
        out.append(Check("exclusions", not (r.exclusions or r.no_nifty),
                         f"{sum(r.exclusions.values())} windows left out ({why}); {r.no_nifty} with no Nifty 50 row"
                         + (f"; e.g. {eg}" if eg else ""), blocking=False))
        c = r.counts
        out.append(Check("counts", True,
                         f"{r.events} results filings in the window; {r.both} with both subjects' d1 and d5 rows; "
                         f"{r.waiting} windows wait for a close; {c['computed']} rows computed, {c['inserted']} inserted",
                         blocking=False))
        return out
