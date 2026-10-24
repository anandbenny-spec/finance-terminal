"""policy.reactions: market reactions around each policy decision (Prompt 01 §5 item 4; Spec 01 §2, the "Reactions
(1b)" row, and §3.1; Architecture 02 §2, the `policy.reactions` row and "Reaction series").

Database only: it fetches nothing. For every held rate-decision meeting × its bank's reaction series it stores one
`policy.market_reaction` row:
- Fed meetings: `fred.DGS2` (the surprise measure), `fred.DGS10`, `yf.^GSPC`, `fred.DEXINUS`, `yf.^NSEI`,
  `yf.^NSEBANK`; RBI meetings: `yf.^NSEI`, `yf.^NSEBANK`, `fred.DEXINUS`. The India 10-year is monthly, so it isn't
  a reaction series (Architecture 02 §2).
- pre = the last observation fixed before the release (`core.event.occurs_at`), post = the first fixed after it, each
  observation's instant being its date at the series' fixing time in the series' zone (core.series). So a Fed
  decision at 14:00 New York reads DEXINUS (fixed at noon New York) from the same day before and the next day after,
  and the Nifty (15:30 India) from the next Indian session after.
- Yields (unit `percent`) change in basis points, (post − pre) × 100; everything else in percent,
  (post ÷ pre − 1) × 100, rounded to 4 decimal places (a derived figure; the page formats it).

Every run recomputes every row from the stored observations, so a revised input (the revision log) or a late
observation updates its row; a meeting whose post value hasn't arrived has no row yet ("Waiting for the first close
after the decision", Spec 01 §4.5) and the `waiting` warning names it once the series' expected lag has passed (daily
5 days, weekly-published 12: Spec 01 §2). A row whose inputs no longer qualify is deleted (reactions are derived:
`job_writer` may delete them, never the observations they come from). Suppressing a source hides its reactions
through RLS (D3's drill); the rows stay.

Meetings: `core.event.status = 'held'` and `policy.meeting.is_rate_decision`, so RBI's special meeting no. 39 (no
decision) has none. Decided by Claude (standing delegation, 24 Oct 2026; issue #70): the panel asks "How did markets
react to each decision?", and a meeting with no decision has nothing to react to; the alternative, rows for it too,
would show moves around a release that held no policy news. The two 2018 baseline meetings are held decisions with
observations from 1 Dec 2018, so they get rows like the rest.

Checks, B blocking, W warning:
- (B) `series`: every reaction series exists, observed, with a fixing time and zone (without one, "fixed before the
  release" has no meaning);
- (B) `us_2y_move`: the 1-day US 2-year change (the surprise measure) within ±100 bps (Architecture 02 §2);
- (W) `waiting`: a meeting × series with no post value after the series' lag, or no pre value within 31 days;
- `reactions` (informational): rows stored, inserted, updated and deleted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

# Spec 01 §2 / Architecture 02 §2: the reaction series by bank (core.entity slug), the surprise measure first.
FED_SERIES = ("fred.DGS2", "fred.DGS10", "yf.^GSPC", "fred.DEXINUS", "yf.^NSEI", "yf.^NSEBANK")
RBI_SERIES = ("yf.^NSEI", "yf.^NSEBANK", "fred.DEXINUS")
SURPRISE_MAX_BPS = Decimal(100)
SEARCH_DAYS = 31                            # how far either side of the release an observation is looked for
PCT_PLACES = Decimal("0.0001")
# Spec 01 §2: a missing post value waits for the series' expected lag before warning. DEXINUS is a daily series
# published weekly (H.10; Data Audit 01 #24), as in policy.market_series.
WEEKLY_PUBLISHED = frozenset({"DEXINUS"})
LAG = {"daily": timedelta(days=5), "weekly": timedelta(days=12)}


@dataclass
class Clock:
    now: datetime
    result: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Row:
    meeting_event_id: int
    series_id: int
    pre_obs_date: object
    post_obs_date: object
    pre_value: Decimal
    post_value: Decimal
    change: Decimal
    change_unit: str
    is_surprise_measure: bool

    def key(self) -> tuple[int, int]:
        return self.meeting_event_id, self.series_id


def change_of(unit: str, pre: Decimal, post: Decimal) -> tuple[Decimal, str]:
    """Yields in basis points ((post − pre) × 100); everything else in percent ((post ÷ pre − 1) × 100, 4 places)."""
    if unit == "percent":
        return (post - pre) * 100, "bps"
    return ((post / pre - 1) * 100).quantize(PCT_PLACES), "pct"


# One statement: every meeting × series pair with the observation fixed last before the release and first after it.
# The local date bounds keep each lookup on the observation primary key (series_id, obs_date).
PAIRS_SQL = """
with pairs as (
  select * from unnest(%(events)s::bigint[], %(series)s::integer[], %(surprise)s::boolean[])
           as p(event_id, series_id, is_surprise)
)
select p.event_id, p.series_id, p.is_surprise, e.occurs_at, s.key, src.key, s.source_code, s.unit,
       pre.obs_date, pre.value, post.obs_date, post.value
  from pairs p
  join core.event e on e.id = p.event_id
  join core.series s on s.id = p.series_id
  join core.source src on src.id = s.source_id
  left join lateral (
    select o.obs_date, o.value from core.observation o
     where o.series_id = s.id
       and o.obs_date between (e.occurs_at at time zone s.fixing_tz)::date - %(days)s
                          and (e.occurs_at at time zone s.fixing_tz)::date
       and ((o.obs_date + s.fixing_time) at time zone s.fixing_tz) < e.occurs_at
     order by o.obs_date desc limit 1) pre on true
  left join lateral (
    select o.obs_date, o.value from core.observation o
     where o.series_id = s.id
       and o.obs_date between (e.occurs_at at time zone s.fixing_tz)::date
                          and (e.occurs_at at time zone s.fixing_tz)::date + %(days)s
       and ((o.obs_date + s.fixing_time) at time zone s.fixing_tz) > e.occurs_at
     order by o.obs_date asc limit 1) post on true
 order by e.occurs_at, p.event_id, s.key
"""


@register
class Reactions(Task):
    key = "policy.reactions"
    # Bank (core.entity slug) -> its reaction series; the surprise measure. Tests point these at their own rows.
    series_by_bank: MappingProxyType[str, tuple[str, ...]] = MappingProxyType({"fed": FED_SERIES, "rbi": RBI_SERIES})
    surprise: tuple[str, str] = ("fed", "fred.DGS2")

    def fetch(self, ctx: Context) -> Clock:          # nothing to fetch: the database holds every input
        return Clock(ctx.now)

    # ------------------------------------------------------------------ load (one transaction)
    def _series(self, tx) -> tuple[dict[str, int], list[str]]:
        keys = sorted({k for ks in self.series_by_bank.values() for k in ks})
        rows = tx.execute("""select key, id, kind, fixing_time is not null and fixing_tz is not null
                               from core.series where key = any(%s)""", (keys,)).fetchall()
        found = {k: (i, kind, fixed) for k, i, kind, fixed in rows}
        problems = []
        for k in keys:
            if k not in found:
                problems.append(f"{k} missing")
            elif found[k][1] != "observed":
                problems.append(f"{k} is not an observed series")
            elif not found[k][2]:
                problems.append(f"{k} has no fixing time and zone")
        return {k: v[0] for k, v in found.items()}, problems

    def _meetings(self, tx) -> list[tuple[int, str]]:
        return tx.execute(
            """select e.id, b.slug from core.event e
                 join policy.meeting m on m.event_id = e.id
                 join core.entity b on b.id = e.entity_id
                where e.status = 'held' and m.is_rate_decision and e.occurs_at is not null and b.slug = any(%s)
                order by e.occurs_at""", (list(self.series_by_bank),)).fetchall()

    def load(self, tx, clock: Clock, task_run_id) -> int:
        r = clock.result
        ids, problems = self._series(tx)
        r.update(problems=problems, rows=[], waiting=[], no_pre=[], counts={})
        if problems:
            return 0                                 # the `series` check blocks the load
        events, series, surprise = [], [], []
        for event_id, bank in self._meetings(tx):
            for k in self.series_by_bank[bank]:
                events.append(event_id)
                series.append(ids[k])
                surprise.append((bank, k) == self.surprise)
        found = tx.execute(PAIRS_SQL, {"events": events, "series": series, "surprise": surprise,
                                       "days": SEARCH_DAYS}).fetchall()
        wanted: dict[tuple[int, int], Row] = {}
        for (event_id, series_id, is_surprise, occurs_at, key, source, code, unit,
             pre_date, pre_value, post_date, post_value) in found:
            if pre_date is None:
                r["no_pre"].append((occurs_at, key))
                continue
            if post_date is None:
                lag = LAG["weekly" if source == "fred" and code in WEEKLY_PUBLISHED else "daily"]
                if clock.now - occurs_at > lag:
                    r["waiting"].append((occurs_at, key, lag.days))
                continue
            change, change_unit = change_of(unit, pre_value, post_value)
            row = Row(event_id, series_id, pre_date, post_date, pre_value, post_value, change, change_unit,
                      is_surprise)
            wanted[row.key()] = row
        r["rows"] = list(wanted.values())

        # Stored rows within this task's scope (its banks' meetings and its series): delete what no longer qualifies,
        # insert what's new, update what changed.
        stored = {(m, s): rest for m, s, *rest in tx.execute(
            """select r.meeting_event_id, r.series_id, r.pre_obs_date, r.post_obs_date, r.pre_value, r.post_value,
                      r.change, r.change_unit, r.is_surprise_measure
                 from policy.market_reaction r
                 join core.event e on e.id = r.meeting_event_id
                 join core.entity b on b.id = e.entity_id
                where b.slug = any(%s) and r.series_id = any(%s)""",
            (list(self.series_by_bank), list(ids.values()))).fetchall()}
        gone = [k for k in stored if k not in wanted]
        new = [w for k, w in wanted.items() if k not in stored]
        changed = [w for k, w in wanted.items()
                   if k in stored and tuple(stored[k]) != (w.pre_obs_date, w.post_obs_date, w.pre_value, w.post_value,
                                                           w.change, w.change_unit, w.is_surprise_measure)]
        with tx.cursor() as cur:
            if gone:
                cur.executemany("delete from policy.market_reaction where meeting_event_id = %s and series_id = %s",
                                gone)
            if new:
                cur.executemany(
                    """insert into policy.market_reaction (meeting_event_id, series_id, pre_obs_date, post_obs_date,
                                                          pre_value, post_value, change, change_unit,
                                                          is_surprise_measure)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(w.meeting_event_id, w.series_id, w.pre_obs_date, w.post_obs_date, w.pre_value, w.post_value,
                      w.change, w.change_unit, w.is_surprise_measure) for w in new])
            if changed:
                cur.executemany(
                    """update policy.market_reaction
                          set pre_obs_date = %s, post_obs_date = %s, pre_value = %s, post_value = %s, change = %s,
                              change_unit = %s, is_surprise_measure = %s
                        where meeting_event_id = %s and series_id = %s""",
                    [(w.pre_obs_date, w.post_obs_date, w.pre_value, w.post_value, w.change, w.change_unit,
                      w.is_surprise_measure, w.meeting_event_id, w.series_id) for w in changed])
        r["counts"] = {"stored": len(wanted), "inserted": len(new), "updated": len(changed), "deleted": len(gone)}
        return len(new) + len(changed) + len(gone)

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, clock: Clock, task_run_id) -> list[Check]:
        r = clock.result
        out = [Check("series", not r["problems"], "; ".join(r["problems"]) or "every reaction series has a fixing time")]
        if r["problems"]:
            return out
        big = [f"{w.post_obs_date} {w.change:+} bps" for w in r["rows"]
               if w.is_surprise_measure and abs(w.change) > SURPRISE_MAX_BPS]
        n = sum(1 for w in r["rows"] if w.is_surprise_measure)
        out.append(Check("us_2y_move", not big,
                         f"{len(big)} of {n} beyond ±{SURPRISE_MAX_BPS} bps: " + ", ".join(big[:5]) if big
                         else f"{n} within ±{SURPRISE_MAX_BPS} bps"))
        late = [f"{at:%Y-%m-%d} {key}: no value after the release ({days}-day lag passed)"
                for at, key, days in r["waiting"]]
        late += [f"{at:%Y-%m-%d} {key}: no value in the {SEARCH_DAYS} days before the release" for at, key in r["no_pre"]]
        out.append(Check("waiting", not late, f"{len(late)}: " + "; ".join(late[:10]) if late else "none overdue",
                         blocking=False))
        c = r["counts"]
        out.append(Check("reactions", True, f"{c['stored']} stored ({c['inserted']} inserted, {c['updated']} updated, "
                                            f"{c['deleted']} deleted)", blocking=False))
        return out
