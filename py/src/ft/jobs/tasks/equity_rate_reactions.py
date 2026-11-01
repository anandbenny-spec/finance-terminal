"""equity.rate_reactions: how every universe company and the Nifty 50 moved on each rate decision and over each rate cycle
(Spec 02A §5.10; job 2A-24). Rows go to `core.event_reaction` through the reactions engine (`ft/engines/reactions.py`).

Database only: it fetches nothing.

**Events and windows.** Every held rate-decision meeting of both banks from 1 Jan 2019 (the 2018 baselines excluded) with
`d1` (the last close before the decision → the first close after) and `d5` (→ the fifth close after); every published
`rate_cycle` event with `span` (the last close before its first move → the first close after its last move; an ongoing
cycle runs to the latest close, so its row moves each week). Decision times come from `core.event.occurs_at`, so a 10:00
IST RBI decision reads that day's close and a 23:30 IST Fed decision the next Indian session's.

**Subjects.** Each universe company (`equity.price_adjusted`'s adjusted close, 15:30 IST) and `yf.^NSEI` (observed).
Company rows carry `value_basis = adjusted_close` and `source_id` = NSE's; the index rows carry the series' source.

**Exclusions** (no row; counted by reason in the `exclusions` check):
- `dividend`: for `d1` and `d5`, the company's ex-dividend date falls inside the window (pre date < ex-date <= post date).
  Spec 02A §10 decision 10: a decision-day move is a price return, so a pair that spans a dividend is left out. A cycle's
  `span` keeps dividend payers: it is a price return labelled "excluding dividends" (excluding dividends from cycles
  lasting months would empty the cycle tables; corrected 28 Oct after review);
- `held_factor`: for every window, a *held* corporate-action factor's ex-date falls inside it. The adjusted close before a
  held factor's ex-date is null (`equity.price_adjusted`), so the engine reports the window as `held` and nothing is
  guessed. Applied factors are already in the adjusted close and exclude nothing;
- `no_pre`: no price at the pre date (not yet listed, or none within 31 days before the decision).
A window whose post close has not arrived yet (`no_post`) is not an exclusion: it is waiting, and counted apart.

**Every run recomputes every row**; a row whose inputs no longer qualify is deleted (derived rows; `job_writer` may delete
them, never the observations or prices they come from). The excess over the Nifty 50 is computed in the views, and only
where the company's and the Nifty's pre and post dates match: the `excess_pairs` check counts the rows where they don't.

Checks, B blocking, W warning:
- (B) `series`: the Nifty 50 series exists, observed, with a fixing time and zone;
- (B) `observations`: every series row's two observations exist, and every row's post date is after its pre date;
- (W) `exclusions`: pairs left out, by reason;
- (W) `no_nifty`: decisions and cycles with no Nifty 50 row, named;
- `excess_pairs` (informational): company rows whose Nifty 50 pre or post date differs, or that have no Nifty row;
- `reactions` (informational): rows by window, bank and subject, and how many were inserted, updated and deleted.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from ft.engines.reactions import (
    D1,
    D5,
    SPAN,
    Anchor,
    Reaction,
    Window,
    load_company_subject,
    load_series_subject,
    react,
)
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

FLOOR = date(2019, 1, 1)               # decisions count from here; the 2018 baselines are excluded
INDEX = "yf.^NSEI"
BANKS = ("fed", "rbi")
SEARCH_DAYS = 31
SHOWN = 12
WINDOWS = ("d1", "d5", "span")


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass(frozen=True)
class Event:
    """An event with its anchor and the windows read around it."""
    anchor: Anchor
    label: str                                    # the event's slug, for naming
    bank: str
    kind: str                                     # 'decision' | 'cycle'
    windows: tuple[Window, ...]


@dataclass
class Result:
    reactions: list[tuple[Reaction, int]] = field(default_factory=list)       # (reaction, source id)
    exclusions: Counter = field(default_factory=Counter)                      # reason -> pairs
    excluded_examples: dict[str, list[str]] = field(default_factory=dict)
    waiting: Counter = field(default_factory=Counter)                         # window -> pairs with no post close yet
    no_nifty: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    pair_mismatch: int = 0
    pair_no_nifty: int = 0
    pair_total: int = 0
    counts: dict = field(default_factory=dict)
    by_window_bank: Counter = field(default_factory=Counter)
    unverified: int = 0
    bad_order: int = 0


@register
class EquityRateReactions(Task):
    key = "equity.rate_reactions"
    requires = "core.event_reaction"
    banks: tuple[str, ...] = BANKS                # core.entity slugs of the central banks; tests point these at their own
    index_key: str = INDEX
    slug_prefix: str = ""                         # tests restrict the universe to their own companies

    def __init__(self):
        self.result = Result()

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    def fetch(self, ctx: Context) -> Result:      # nothing to fetch: the database holds every input
        self.result = Result()
        return self.result

    # ------------------------------------------------------------------ the events
    def _events(self, tx) -> list[Event]:
        out: list[Event] = []
        for event_id, bank, slug, occurs_at in tx.execute(
                """select e.id, b.slug, e.slug, e.occurs_at from core.event e
                     join policy.meeting m on m.event_id = e.id
                     join core.entity b on b.id = e.entity_id
                    where e.status = 'held' and m.is_rate_decision and e.occurs_at is not null
                      and coalesce(e.ends_on, e.starts_on) >= %s and b.slug = any(%s)
                    order by e.occurs_at, e.id""", (FLOOR, list(self.banks))).fetchall():
            out.append(Event(Anchor.decision(event_id, occurs_at), slug, bank, "decision", (D1, D5)))
        for event_id, bank, slug, status, first_at, last_at in tx.execute(
                """select e.id, b.slug, e.slug, e.status, f.occurs_at, l.occurs_at
                     from core.event e
                     join policy.rate_cycle c on c.event_id = e.id
                     join core.entity b on b.id = e.entity_id
                     join core.event f on f.id = c.first_meeting_event_id
                     join core.event l on l.id = c.last_meeting_event_id
                    where e.event_type = 'rate_cycle' and e.is_published and b.slug = any(%s)
                      and f.occurs_at is not null and l.occurs_at is not null
                    order by f.occurs_at, e.id""", (list(self.banks),)).fetchall():
            out.append(Event(Anchor(event_id, first_at, last_at, ongoing=(status == "ongoing")), slug, bank, "cycle",
                             (SPAN,)))
        return out

    # ------------------------------------------------------------------ load (one transaction)
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
        events = self._events(tx)
        nse = tx.execute("select id from core.source where key = 'nse'").fetchone()[0]
        companies = tx.execute(
            """select c.entity_id, e.slug from equity.company c join core.entity e on e.id = c.entity_id
                where e.slug like %s order by e.slug""", (self.slug_prefix + "%",)).fetchall()
        wanted: dict[tuple, tuple[Reaction, int]] = {}
        if events:
            lo = min(e.anchor.starts_at for e in events).date() - timedelta(days=SEARCH_DAYS + 3)
            hi = tx.execute("select current_date").fetchone()[0] + timedelta(days=1)
            nifty = load_series_subject(tx, index_id, lo, hi)
            nifty_rows: dict[tuple[int, str], Reaction] = {}
            for ev in events:
                for w in ev.windows:
                    out = react(nifty, ev.anchor, w, search_days=SEARCH_DAYS)
                    if out.reaction is None:
                        r.no_nifty.append(f"{ev.label} {w.key} ({out.missing})")
                        continue
                    nifty_rows[(ev.anchor.event_id, w.key)] = out.reaction
                    wanted[(ev.anchor.event_id, index_id, None, w.key)] = (out.reaction, index_source)
                    r.by_window_bank[(w.key, ev.bank, "nifty")] += 1
            dividends = self._dividends(tx)
            for eid, slug in companies:
                subject = load_company_subject(tx, eid, lo, hi)
                if not len(subject):
                    continue
                for ev in events:
                    for w in ev.windows:
                        out = react(subject, ev.anchor, w, search_days=SEARCH_DAYS)
                        if out.reaction is None:
                            if out.missing == "no_post":
                                r.waiting[w.key] += 1
                            else:
                                self._exclude(r, "held_factor" if out.missing == "held" else out.missing, slug, ev, w)
                            continue
                        x = out.reaction
                        reason = self._reason(x, w, dividends.get(eid, ()))
                        if reason:
                            self._exclude(r, reason, slug, ev, w)
                            continue
                        wanted[(ev.anchor.event_id, None, eid, w.key)] = (x, nse)
                        r.by_window_bank[(w.key, ev.bank, "company")] += 1
                        r.pair_total += 1
                        n = nifty_rows.get((ev.anchor.event_id, w.key))
                        if n is None:
                            r.pair_no_nifty += 1
                        elif (n.pre_date, n.post_date) != (x.pre_date, x.post_date):
                            r.pair_mismatch += 1
        # the stored rows in this task's scope: its windows on its subjects
        scope_companies = [eid for eid, _ in companies]
        stored = {(e, s, c, w): rest for e, s, c, w, *rest in tx.execute(
            """select event_id, series_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value,
                      change, change_unit, value_basis from core.event_reaction
                where window_key = any(%s) and (series_id = %s or entity_id = any(%s))""",
            (list(WINDOWS), index_id, scope_companies)).fetchall()}

        def flat(x: Reaction, source: int) -> list:
            return [source, x.pre_date, x.post_date, x.pre_value, x.post_value, x.change, x.change_unit, x.value_basis]

        gone = [k for k in stored if k not in wanted]
        new = [(k, v) for k, v in wanted.items() if k not in stored]
        changed = [(k, v) for k, v in wanted.items() if k in stored and list(stored[k]) != flat(v[0], v[1])]
        with tx.cursor() as cur:
            if gone:
                cur.executemany("""delete from core.event_reaction where event_id = %s
                                      and coalesce(series_id, 0) = coalesce(%s, 0)
                                      and coalesce(entity_id, 0) = coalesce(%s, 0) and window_key = %s""", gone)
            if new:
                cur.executemany(
                    """insert into core.event_reaction (event_id, series_id, entity_id, window_key, source_id, pre_date,
                                                        post_date, pre_value, post_value, change, change_unit, value_basis,
                                                        task_run_id)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(e, s, c, w, src, x.pre_date, x.post_date, x.pre_value, x.post_value, x.change, x.change_unit,
                      x.value_basis, task_run_id) for (e, s, c, w), (x, src) in new])
            if changed:
                cur.executemany(
                    """update core.event_reaction set source_id = %s, pre_date = %s, post_date = %s, pre_value = %s,
                              post_value = %s, change = %s, change_unit = %s, value_basis = %s, task_run_id = %s
                        where event_id = %s and coalesce(series_id, 0) = coalesce(%s, 0)
                          and coalesce(entity_id, 0) = coalesce(%s, 0) and window_key = %s""",
                    [(src, x.pre_date, x.post_date, x.pre_value, x.post_value, x.change, x.change_unit, x.value_basis,
                      task_run_id, e, s, c, w) for (e, s, c, w), (x, src) in changed])
        r.reactions = list(wanted.values())
        r.bad_order = sum(1 for x, _ in r.reactions if x.post_date <= x.pre_date)
        # every series row's two observations exist (the foreign key enforces it; this is the independent read)
        r.unverified = tx.execute(
            """select count(*) from core.event_reaction r
                where r.series_id = %s and r.window_key = any(%s)
                  and (not exists (select 1 from core.observation o where o.series_id = r.series_id
                                      and o.obs_date = r.pre_date)
                    or not exists (select 1 from core.observation o where o.series_id = r.series_id
                                      and o.obs_date = r.post_date))""",
            (index_id, list(WINDOWS))).fetchone()[0]
        r.counts = {"stored": len(wanted), "inserted": len(new), "updated": len(changed), "deleted": len(gone)}
        return len(new) + len(changed) + len(gone)

    @staticmethod
    def _exclude(r: Result, reason: str, slug: str, ev: Event, w: Window) -> None:
        r.exclusions[reason] += 1
        ex = r.excluded_examples.setdefault(reason, [])
        if len(ex) < 3:
            ex.append(f"{slug} {ev.label} {w.key}")

    @staticmethod
    def _reason(x: Reaction, w: Window, dividends: tuple) -> str | None:
        if w.key != "span" and any(x.pre_date < d <= x.post_date for d in dividends):
            return "dividend"
        return None

    @staticmethod
    def _dividends(tx) -> dict[int, tuple[date, ...]]:
        out: dict[int, list[date]] = {}
        for eid, ex in tx.execute(
                """select e.entity_id, a.ex_date from equity.corporate_action a join core.event e on e.id = a.event_id
                    where a.kind = 'dividend' and e.entity_id is not null"""):
            out.setdefault(eid, []).append(ex)
        return {k: tuple(v) for k, v in out.items()}

    # ------------------------------------------------------------------ checks (same transaction)
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
        eg = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(r.excluded_examples.items()))
        out.append(Check("exclusions", not r.exclusions,
                         f"{sum(r.exclusions.values())} pairs left out ({why})" + (f"; e.g. {eg}" if eg else ""),
                         blocking=False))
        out.append(Check("no_nifty", not r.no_nifty,
                         f"{len(r.no_nifty)}: " + _names(r.no_nifty) if r.no_nifty
                         else "every decision and cycle has its Nifty 50 row", blocking=False))
        out.append(Check("excess_pairs", True,
                         f"{r.pair_total} company rows; {r.pair_mismatch} with Nifty 50 dates that differ and "
                         f"{r.pair_no_nifty} with no Nifty 50 row get no excess in the views; "
                         f"{sum(r.waiting.values())} windows wait for a close", blocking=False))
        c = r.counts
        by = ", ".join(f"{w}/{b}/{k} {n}" for (w, b, k), n in sorted(r.by_window_bank.items()))
        out.append(Check("reactions", True, f"{c['stored']} stored ({c['inserted']} inserted, {c['updated']} updated, "
                                            f"{c['deleted']} deleted); by window, bank and subject: {by or 'none'}",
                         blocking=False))
        return out
