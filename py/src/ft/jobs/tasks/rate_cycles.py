"""policy.rate_cycles: each bank's rate-cycles, derived from its stored held rate decisions (Spec 02A §5.10; job 2A-23).

Database only: it fetches nothing. Every run re-derives every cycle from `policy.rate_setting` (the repo rate for the RBI,
the upper bound of the federal funds target range for the Fed), so an ongoing cycle extends or completes as decisions
arrive, and a new `RULE_VERSION` re-derives them all.

**The rule** (`derive_cycles`, a pure function on a bank's ordered decisions): a cycle is a run of policy-rate moves in
one direction that ends at a move the other way or at two consecutive decisions without a move. Only runs of two or more
moves are cycles (a lone move appears only in the decision-day statistics). A cycle whose last move has fewer than two
decisions without a move after it, and no move the other way, is `ongoing`; every other cycle is `complete`. Decisions
count from 1 Jan 2019 (the 2018 baselines give only the starting level); a meeting that is not a rate decision (the
RBI's special meeting no. 39) is no decision.

**What it writes**, one per cycle: a `core.event` (type `rate_cycle`, entity = the bank, slug
`<bank>-<easing|tightening>-<yyyy-mm>` of its first move, `starts_on` and `ends_on` the dates of its first and last
moves, status `ongoing` or `complete`, published like the meetings; the views' gate holds the figures back) and its
`policy.rate_cycle` row (direction, the first and last move's meetings, the number of moves, the net change in basis
points, the rule version). A job may not delete an event: a cycle the rule no longer derives (a revised decision, a new
rule version) is unpublished and named in the `stale` check, and published again if it returns.

Checks, B blocking, W warning:
- (B) `decisions`: every held rate decision since 1 Jan 2019 has its policy-rate row (without one it can't be classed
  as a move or a hold, and a missing move would split or hide a cycle);
- (B) `totals`: each cycle's net change equals the policy rate after its last move minus the rate before its first,
  read from the stored rate levels (an independent cross-check of the summed changes);
- (W) `stale`: cycles unpublished because the rule no longer derives them;
- `cycles` (informational): the cycles stored, how many are ongoing, and how many were inserted, updated and unpublished.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

RULE_VERSION = 1
FLOOR = date(2019, 1, 1)               # decisions count from here; the 2018 baselines only give the starting level
PAUSE = 2                              # consecutive decisions without a move that end a cycle
MIN_MOVES = 2                          # a lone move is not a cycle
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Decision:
    event_id: int
    on: date                           # the meeting's decision date
    change_bps: int                    # the policy-rate change at this decision
    rate_pct: Decimal                  # the policy rate after it


@dataclass(frozen=True)
class Cycle:
    direction: str                     # 'easing' | 'tightening'
    first: Decision
    last: Decision                     # the last move
    moves: int
    total_change_bps: int
    ongoing: bool


def derive_cycles(decisions: list[Decision]) -> list[Cycle]:
    """The cycles in one bank's decisions, ordered by date (the rule above)."""
    out: list[Cycle] = []
    run: list[Decision] = []           # the open run's moves
    direction = ""
    holds = 0

    def close(ongoing: bool) -> None:
        if len(run) >= MIN_MOVES:
            out.append(Cycle(direction, run[0], run[-1], len(run), sum(d.change_bps for d in run), ongoing))

    for d in decisions:
        if d.change_bps == 0:
            if run:
                holds += 1
                if holds >= PAUSE:
                    close(False)
                    run = []
            continue
        way = "easing" if d.change_bps < 0 else "tightening"
        if run and way != direction:
            close(False)
            run = []
        if not run:
            direction = way
        run.append(d)
        holds = 0
    if run:
        close(True)
    return out


def slug_of(bank: str, c: Cycle) -> str:
    return f"{bank}-{c.direction}-{c.first.on:%Y-%m}"


def title_of(bank_name: str, c: Cycle) -> str:
    return f"{bank_name} {c.direction} cycle from {MONTHS[c.first.on.month - 1]} {c.first.on.year}"


@dataclass
class Derived:
    now: object
    banks: dict = field(default_factory=dict)         # bank slug -> (entity id, name, [Cycle])
    problems: list = field(default_factory=list)      # held decisions without a policy-rate row
    counts: dict = field(default_factory=dict)
    stale: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)


@register
class RateCycles(Task):
    key = "policy.rate_cycles"
    run_after = ("policy.fed_documents", "policy.rbi_documents")
    # Bank (core.entity slug) -> the instrument that is its policy rate. Tests point these at their own banks.
    instrument_by_bank: MappingProxyType[str, str] = MappingProxyType({"fed": "fed_target_upper", "rbi": "repo"})

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="policy.rate_cycle")

    def fetch(self, ctx: Context) -> Derived:         # nothing to fetch: the database holds every input
        return Derived(ctx.now)

    # ------------------------------------------------------------------ load (one transaction)
    def _decisions(self, tx, bank: str, instrument: str) -> tuple[list[Decision], list[str]]:
        rows = tx.execute(
            """select e.id, coalesce(e.ends_on, e.starts_on), r.change_bps, r.value_pct
                 from core.event e
                 join policy.meeting m on m.event_id = e.id
                 join core.entity b on b.id = e.entity_id
                 left join policy.rate_setting r on r.meeting_event_id = e.id and r.instrument = %s
                where b.slug = %s and e.status = 'held' and m.is_rate_decision
                  and coalesce(e.ends_on, e.starts_on) >= %s
                order by coalesce(e.ends_on, e.starts_on), e.id""", (instrument, bank, FLOOR)).fetchall()
        missing = [f"{bank} {on}: no {instrument} row" for _, on, change, _ in rows if change is None]
        return [Decision(i, on, int(c), v) for i, on, c, v in rows if c is not None], missing

    def load(self, tx, d: Derived, task_run_id) -> int:
        wanted: dict[str, tuple[str, Cycle, int, str]] = {}      # slug -> (bank, cycle, entity id, bank name)
        for bank, instrument in self.instrument_by_bank.items():
            ent = tx.execute("select id, name from core.entity where slug = %s and kind = 'central_bank'",
                             (bank,)).fetchone()
            if ent is None:
                d.problems.append(f"{bank}: no such central bank")
                continue
            decisions, missing = self._decisions(tx, bank, instrument)
            d.problems += missing
            cycles = derive_cycles(decisions)
            d.banks[bank] = (ent[0], ent[1], cycles)
            for c in cycles:
                wanted[slug_of(bank, c)] = (bank, c, ent[0], ent[1])
        if d.problems:
            return 0                                   # the `decisions` check blocks: nothing is written
        # `totals`: the net change against the stored rate levels, independent of the summed changes
        for slug, (bank, c, _, _) in wanted.items():
            before = c.first.rate_pct - Decimal(c.first.change_bps) / 100
            net = int(((c.last.rate_pct - before) * 100).to_integral_value())
            if net != c.total_change_bps:
                d.mismatches.append(f"{slug}: {c.total_change_bps} bps summed, {net} bps from the rate levels")
        if d.mismatches:
            return 0

        stored = {slug: (eid, status, ends_on, published) for eid, slug, status, ends_on, published in tx.execute(
            """select e.id, e.slug, e.status, e.ends_on, e.is_published from core.event e
                where e.event_type = 'rate_cycle' and e.entity_id = any(%s)""",
            ([v[0] for v in d.banks.values()],)).fetchall()}
        inserted = updated = unpublished = 0
        for slug, (bank, c, entity_id, bank_name) in wanted.items():
            status = "ongoing" if c.ongoing else "complete"
            source_id = tx.execute("select source_id from core.event where id = %s", (c.first.event_id,)).fetchone()[0]
            if slug not in stored:
                event_id = tx.execute(
                    """insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, status, source_id,
                                               is_published)
                       values ('rate_cycle', %s, %s, %s, %s, %s, %s, %s, true) returning id""",
                    (entity_id, slug, title_of(bank_name, c), c.first.on, c.last.on, status, source_id)).fetchone()[0]
                tx.execute(
                    """insert into policy.rate_cycle (event_id, direction, first_meeting_event_id, last_meeting_event_id,
                                                      moves, total_change_bps, rule_version)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    (event_id, c.direction, c.first.event_id, c.last.event_id, c.moves, c.total_change_bps,
                     RULE_VERSION))
                inserted += 1
                continue
            event_id = stored[slug][0]
            now_row = (c.direction, c.first.event_id, c.last.event_id, c.moves, c.total_change_bps, RULE_VERSION)
            had = tx.execute("""select direction, first_meeting_event_id, last_meeting_event_id, moves, total_change_bps,
                                       rule_version from policy.rate_cycle where event_id = %s""", (event_id,)).fetchone()
            ev = tx.execute("select status, starts_on, ends_on, is_published, title from core.event where id = %s",
                            (event_id,)).fetchone()
            same_event = ev == (status, c.first.on, c.last.on, True, title_of(bank_name, c))
            if tuple(had) == now_row and same_event:
                continue
            tx.execute("""update core.event set status = %s, starts_on = %s, ends_on = %s, is_published = true,
                                 title = %s, source_id = %s, updated_at = now() where id = %s""",
                       (status, c.first.on, c.last.on, title_of(bank_name, c), source_id, event_id))
            tx.execute("""update policy.rate_cycle set direction = %s, first_meeting_event_id = %s,
                                 last_meeting_event_id = %s, moves = %s, total_change_bps = %s, rule_version = %s
                           where event_id = %s""", (*now_row, event_id))
            updated += 1
        for slug, (event_id, _, _, published) in stored.items():
            if slug not in wanted and published:
                tx.execute("update core.event set is_published = false, updated_at = now() where id = %s", (event_id,))
                d.stale.append(slug)
                unpublished += 1
        d.counts = {"stored": len(wanted), "ongoing": sum(1 for v in wanted.values() if v[1].ongoing),
                    "inserted": inserted, "updated": updated, "unpublished": unpublished}
        return inserted + updated + unpublished

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, d: Derived, task_run_id) -> list[Check]:
        out = [Check("decisions", not d.problems,
                     f"{len(d.problems)}: " + "; ".join(d.problems[:10]) if d.problems
                     else "every held rate decision since 2019 has its policy-rate row")]
        if d.problems:
            return out
        out.append(Check("totals", not d.mismatches,
                         "; ".join(d.mismatches[:5]) if d.mismatches
                         else "each cycle's net change equals the rate level after its last move minus the one before its first"))
        if d.mismatches:
            return out
        out.append(Check("stale", not d.stale,
                         f"{len(d.stale)} cycle(s) no longer derived, unpublished: " + ", ".join(d.stale)
                         if d.stale else "none", blocking=False))
        c = d.counts
        out.append(Check("cycles", True, f"{c['stored']} cycles ({c['ongoing']} ongoing); {c['inserted']} inserted, "
                                         f"{c['updated']} updated, {c['unpublished']} unpublished", blocking=False))
        return out
