"""equity.segment_changes: each India company's reportable segments over its latest six quarters, read from the consolidated
results filings 2A already stores, and the flags where a segment of the previous four quarters is missing from the latest
(Plan 05 §1.2, D6, D8; job DD-01, #409). Database only: it reads the stored instances through the storage function
(`ctx.fetcher.read`: every read checks the object's hash) and fetches nothing. A file that isn't stored is named, never
fetched here (2A's `equity.filings` registers it). No AI.

**Which filings.** Per India company (`market = 'IN'`, Plan 05 §0.5 N2), the latest quarter with a consolidated filing on a
`filed` (not superseded) results announcement, and the five quarter ends before it; each quarter's consolidated filing at its
latest version. Standalone filings are never read, so a quarter is never filled from one.

**What is stored** (migration 0038; nothing else): `equity.segment_lineup`, the quarter's segments as filed (a run replaces a
quarter's rows when the reading changes, and removes them when the quarter's latest filing gives no line-up, so the stored
line-ups are always this run's reading), and `equity.segment_change`, the rule's flags for the latest quarter with a line-up
(replaced when the rule's result changes). The reader and the rule, with their evidence: `ft/equity/segments.py`.

Checks, B blocking, W warning (details name companies, quarters, kinds and counts; never a segment's name, since the field is
held back until its judge passes):
- (B) `states`: for every company, the stored line-ups give the state the rule found, by the same derivation
  `api.segment_changes` uses (the latest quarter with a line-up on a filed announcement, its four earlier quarters, single
  segment, flagged), so the page can't say "none flagged" over a quarter the rule didn't compare;
- (W) `no_segment_facts`: a filing whose flag says multi-segment, or of a company whose other quarters are multi-segment,
  with no segment facts for the quarter, named (never filled from the standalone filing);
- (W) `filings_held`: any other filing that gave no line-up (unreadable, basis or period mismatch, undated segment facts, a
  revenue not in rupees, a member stated twice, two segments of one name), named with its reason;
- (W) `latest_unread`: a company whose latest consolidated filing gave no line-up, so an earlier quarter is the latest
  compared;
- (W) `not_compared`: a company whose latest quarter wasn't compared (an earlier quarter missing or held, no segment
  disclosure, no consolidated filing), named;
- (W) `flags`: the flags by kind, with the companies (passing: flags aren't faults; the judge reads each one, DD-03);
- (W) `parsed`: what the run read and stored, always passing.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from ft.equity import segments as sg
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Item:
    document_id: int
    entity_id: int
    slug: str
    event_id: int
    period_end: date
    fy_start: date | None
    lineup: sg.Lineup | None = None
    error: sg.SegmentError | None = None


@dataclass
class Evaluation:
    entity_id: int
    slug: str
    last_filed: date                        # the latest quarter with a consolidated filing
    period_end: date | None = None          # the latest quarter with a line-up (the one compared)
    latest_event_id: int | None = None
    previous_event_id: int | None = None
    outcome: sg.Outcome | None = None
    missing: list[str] = field(default_factory=list)      # earlier quarters with no line-up, and why


@dataclass
class Fetched:
    items: list[Item] = field(default_factory=list)
    evaluations: list[Evaluation] = field(default_factory=list)
    no_consolidated: list[str] = field(default_factory=list)


# The latest consolidated filing per India company and quarter on a filed announcement, at its latest version; the quarters
# from the company's latest one back five (the oldest is 14 months and a day before the latest's month starts).
SELECT = """
with latest as (
  select distinct on (fl.entity_id, fl.period_end)
         fl.document_id, fl.entity_id, fl.event_id, fl.period_end, fl.fy_start, d.raw_file_id
    from equity.filing fl
    join core.document d on d.id = fl.document_id
    join core.event ev on ev.id = fl.event_id and ev.status = 'filed'
    join equity.company c on c.entity_id = fl.entity_id
    join core.entity e on e.id = fl.entity_id and e.market = 'IN'
   where fl.basis = 'consolidated'
   order by fl.entity_id, fl.period_end, d.version_no desc, fl.filed_at desc nulls last, d.id desc
), top as (
  select entity_id, max(period_end) as last from latest group by entity_id
)
select l.document_id, l.entity_id, e.slug, l.event_id, l.period_end, l.fy_start, l.raw_file_id, t.last
  from latest l
  join top t on t.entity_id = l.entity_id
  join core.entity e on e.id = l.entity_id
 where l.period_end >= (date_trunc('month', t.last) - interval '14 months' - interval '1 day')::date
 order by e.slug, l.period_end
"""
WITHOUT_CONSOLIDATED = """
select e.slug from equity.company c join core.entity e on e.id = c.entity_id
 where e.market = 'IN'
   and not exists (select 1 from equity.filing fl join core.event ev on ev.id = fl.event_id and ev.status = 'filed'
                    where fl.entity_id = c.entity_id and fl.basis = 'consolidated')
 order by 1"""
# The view's derivation (api.segment_changes, migration 0038), per company, from the stored rows.
STATES = """
with lu as (
  select l.* from equity.segment_lineup l
  join core.event ev on ev.id = l.results_event_id and ev.status = 'filed'
  where l.entity_id = any(%s)
), q as (
  select entity_id, period_end, bool_or(reporting = 'single') as single from lu group by entity_id, period_end
), latest as (
  select distinct on (entity_id) entity_id, period_end, single from q order by entity_id, period_end desc
)
select l.entity_id, l.period_end,
       case when exists (select 1 from equity.segment_change c where c.entity_id = l.entity_id and c.period_end = l.period_end)
              then 'changed'
            when l.single then 'single_segment'
            when (select count(distinct q2.period_end) from q q2 where q2.entity_id = l.entity_id and q2.period_end in (
                    (date_trunc('month', l.period_end) - interval '2 months' - interval '1 day')::date,
                    (date_trunc('month', l.period_end) - interval '5 months' - interval '1 day')::date,
                    (date_trunc('month', l.period_end) - interval '8 months' - interval '1 day')::date,
                    (date_trunc('month', l.period_end) - interval '11 months' - interval '1 day')::date)) = 4
              then 'none_flagged'
            else 'not_compared' end
  from latest l
"""


def evaluate(items: list[Item], slug: str, entity_id: int) -> Evaluation:
    """The rule for one company, from its items (the latest six quarters' consolidated filings, read)."""
    by_end = {it.period_end: it for it in items}
    ev = Evaluation(entity_id, slug, max(by_end))
    read = [it for it in items if it.lineup is not None]
    if not read:
        return ev
    latest = max(read, key=lambda it: it.period_end)
    ev.period_end, ev.latest_event_id = latest.period_end, latest.event_id
    previous: list[sg.Lineup | None] = []
    for i in range(1, sg.PREVIOUS + 1):
        pe = sg.quarter_end_before(latest.period_end, i)
        it = by_end.get(pe)
        if it is None:
            ev.missing.append(f"{pe.isoformat()} (no consolidated filing stored)")
        elif it.lineup is None:
            ev.missing.append(f"{pe.isoformat()} ({it.error.check if it.error else 'not read'})")
        if i == 1 and it is not None:
            ev.previous_event_id = it.event_id
        previous.append(it.lineup if it is not None else None)
    ev.outcome = sg.compare(latest.lineup, previous)
    return ev


@register
class EquitySegmentChanges(Task):
    key = "equity.segment_changes"
    version = sg.RULE_VERSION
    requires = "equity.segment_change"     # the production guard's table (ft/equity/guard.py)

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (no transaction; reads only)
    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        out = Fetched()
        out.no_consolidated = [r[0] for r in f.db.execute(WITHOUT_CONSOLIDATED).fetchall()]
        by_company: dict[tuple[int, str], list[Item]] = {}
        rows = f.db.execute(SELECT).fetchall()
        t0 = time.monotonic()
        for n, (doc_id, entity_id, slug, event_id, period_end, fy_start, raw_id, last) in enumerate(rows, 1):
            if n % 50 == 0 or n == len(rows):              # counts and seconds only: the run log is public
                print(f"  equity.segment_changes: at file {n} of {len(rows)}, {time.monotonic() - t0:.0f} s")
            if period_end not in {last, *(sg.quarter_end_before(last, i) for i in range(1, sg.HISTORY))}:
                continue                                   # not one of the six quarter ends
            item = Item(doc_id, entity_id, slug, event_id, period_end, fy_start)
            if raw_id is None:
                item.error = sg.SegmentError("unreadable", "the document has no stored file")
            else:
                body = f.read(raw_id)                      # a storage or hash error fails the task: nothing is written
                try:
                    item.lineup = sg.read_lineup(body, sg.Filing("consolidated", period_end, fy_start))
                except sg.SegmentError as e:
                    item.error = e
            out.items.append(item)
            by_company.setdefault((entity_id, slug), []).append(item)
        out.evaluations = [evaluate(its, slug, eid) for (eid, slug), its in sorted(by_company.items(), key=lambda kv: kv[0][1])]
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        """Production's runner talks to Mumbai from the US, so a round trip per row is too slow (VC-05's first production run,
        #190): the stored rows are read in two queries, compared here, and the writes go as pipelined `executemany`
        batches, the deletes before the inserts."""
        t0 = time.monotonic()
        ids = sorted({it.entity_id for it in f.items})
        stored: dict[int, list[tuple]] = {}               # event -> its rows, in line order
        quarter_events: dict[tuple[int, date], set[int]] = {}
        for ev, eid, pe, *row in tx.execute(
                """select results_event_id, entity_id, period_end, line_no, entity_id, document_id, period_end, reporting,
                          description, revenue, parser_version
                     from equity.segment_lineup where entity_id = any(%s) and basis = 'consolidated'
                    order by results_event_id, line_no""", (ids,)).fetchall():
            stored.setdefault(ev, []).append(tuple(row))
            quarter_events.setdefault((eid, pe), set()).add(ev)
        del_lu: set[int] = set()
        ins_lu: list[tuple] = []
        for it in f.items:
            for other in quarter_events.get((it.entity_id, it.period_end), set()) - {it.event_id}:
                del_lu.add(other)                         # a quarter's rows come from its chosen announcement only
            want = [(ln.line_no, it.entity_id, it.document_id, it.period_end, it.lineup.reporting, ln.description, ln.revenue,
                     sg.PARSER_VERSION) for ln in (it.lineup.lines if it.lineup else [])]
            if stored.get(it.event_id, []) != want:
                if it.event_id in stored:
                    del_lu.add(it.event_id)
                ins_lu.extend((it.event_id, *w) for w in want)
        evs = [ev for ev in f.evaluations if ev.period_end is not None]
        have_c: dict[tuple[int, date], list[tuple]] = {}
        for eid, pe, *row in tx.execute(
                """select entity_id, period_end, change_no, kind, segments_before, segments_after, revenue_ratio, latest_event_id,
                          previous_event_id, previous_period_end, rule_version
                     from equity.segment_change where entity_id = any(%s) order by entity_id, period_end, change_no""",
                ([ev.entity_id for ev in evs],)).fetchall():
            have_c.setdefault((eid, pe), []).append(tuple(row))
        del_c: list[tuple] = []
        ins_c: list[tuple] = []
        for ev in evs:
            changes = ev.outcome.changes if ev.outcome else []
            want_c = [(i, c.kind, c.before, c.after, c.revenue_ratio, ev.latest_event_id, ev.previous_event_id,
                       sg.quarter_end_before(ev.period_end), sg.RULE_VERSION) for i, c in enumerate(changes, 1)]
            if have_c.get((ev.entity_id, ev.period_end), []) != want_c:
                if (ev.entity_id, ev.period_end) in have_c:
                    del_c.append((ev.entity_id, ev.period_end))
                ins_c.extend((ev.entity_id, ev.period_end, *w) for w in want_c)
        with tx.cursor() as cur:
            if del_lu:
                cur.executemany("delete from equity.segment_lineup where results_event_id = %s and basis = 'consolidated'",
                                [(e,) for e in sorted(del_lu)])
            if del_c:
                cur.executemany("delete from equity.segment_change where entity_id = %s and period_end = %s", del_c)
            if ins_lu:
                cur.executemany("""insert into equity.segment_lineup (results_event_id, basis, line_no, entity_id, document_id,
                                   period_end, reporting, description, revenue, parser_version)
                                   values (%s, 'consolidated', %s, %s, %s, %s, %s, %s, %s, %s)""", ins_lu)
            if ins_c:
                cur.executemany("""insert into equity.segment_change (entity_id, period_end, change_no, kind, segments_before,
                                   segments_after, revenue_ratio, latest_event_id, previous_event_id, previous_period_end,
                                   rule_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", ins_c)
        print(f"  equity.segment_changes: replaced {len(del_lu)} quarters' line-ups ({len(ins_lu)} rows) and "     # counts only:
              f"{len(del_c)} companies' flags ({len(ins_c)} rows), {time.monotonic() - t0:.0f} s")      # the log is public
        return len(ins_lu) + len(ins_c) + len(del_lu) + len(del_c)      # rows inserted, and quarters or companies cleared

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out = [self._states(tx, f)]
        multi = {it.entity_id for it in f.items if it.lineup is not None and it.lineup.reporting == "multi"}
        facts = [f"{it.slug} {it.period_end.isoformat()}" for it in f.items if it.error is not None and (
            it.error.check == "no_segment_facts" or (it.error.check == "no_disclosure" and it.entity_id in multi))]
        out.append(Check("no_segment_facts", not facts, ("a multi-segment company's consolidated filing has no segment facts "
                         "for the quarter (not filled from standalone): " + _names(facts)) if facts
                         else "every multi-segment company's filings carry segment facts", blocking=False))
        held = [f"{it.slug} {it.period_end.isoformat()} ({it.error.check}): {it.error}" for it in f.items
                if it.error is not None and it.error.check not in ("no_segment_facts", "no_disclosure")]
        out.append(Check("filings_held", not held, ("held, so no line-up: " + _names(held)) if held
                         else "no filing was held", blocking=False))
        unread = [f"{ev.slug} (latest filing {ev.last_filed.isoformat()}, latest compared "
                  f"{ev.period_end.isoformat() if ev.period_end else 'none'})"
                  for ev in f.evaluations if ev.period_end != ev.last_filed]
        out.append(Check("latest_unread", not unread, ("the latest consolidated filing gave no line-up: " + _names(unread))
                         if unread else "every company's latest consolidated filing gave its line-up", blocking=False))
        nc = [f"{ev.slug}: " + ("; ".join(ev.missing) if ev.missing else "no line-up in any of its latest six quarters")
              for ev in f.evaluations if ev.outcome is None or ev.outcome.state == "not_compared"]
        nc += [f"{s}: no consolidated filing stored" for s in f.no_consolidated]
        out.append(Check("not_compared", not nc, ("latest quarter not compared: " + _names(nc)) if nc
                         else "every company's latest quarter was compared", blocking=False))
        kinds = Counter(c.kind for ev in f.evaluations if ev.outcome for c in ev.outcome.changes)
        flagged = [f"{ev.slug} {ev.period_end.isoformat()} ({', '.join(c.kind for c in ev.outcome.changes)})"
                   for ev in f.evaluations if ev.outcome and ev.outcome.changes]
        out.append(Check("flags", True, (f"{sum(kinds.values())} flags (" + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
                                         + "): " + _names(flagged)) if flagged else "no flags", blocking=False))
        states = Counter(ev.outcome.state if ev.outcome else "not_compared" for ev in f.evaluations)
        read = sum(1 for it in f.items if it.lineup is not None)
        lines = sum(len(it.lineup.lines) for it in f.items if it.lineup is not None)
        self.result = {"companies": len(f.evaluations) + len(f.no_consolidated), "filings": len(f.items), "read": read,
                       "lineup_rows": lines, "flags": dict(kinds), "states": dict(states)}
        out.append(Check("parsed", True, f"{read} of {len(f.items)} consolidated filings of {len(f.evaluations)} companies read: "
                                         f"{lines} line-up rows; states " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())),
                         blocking=False))
        return out

    @staticmethod
    def _states(tx, f: Fetched) -> Check:
        ids = [ev.entity_id for ev in f.evaluations]
        got = {eid: (pe, st) for eid, pe, st in tx.execute(STATES, (ids,)).fetchall()} if ids else {}
        wrong = []
        for ev in f.evaluations:
            want = (ev.period_end, ev.outcome.state) if ev.outcome else None
            have = got.get(ev.entity_id)
            if ev.period_end is None:
                if have is not None:
                    wrong.append(f"{ev.slug}: no line-up read, but rows are stored for {have[0].isoformat()}")
            elif have != want:
                wrong.append(f"{ev.slug}: the rule found {want[1]} for {want[0].isoformat()}, the stored rows give "
                             + (f"{have[1]} for {have[0].isoformat()}" if have else "nothing"))
        return Check("states", not wrong, ("the stored line-ups disagree with the rule: " + _names(wrong)) if wrong
                     else f"the stored line-ups give the rule's state for all {len(f.evaluations)} companies")
