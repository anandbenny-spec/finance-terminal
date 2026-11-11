"""equity.related_parties: each universe company's latest fiscal year's related-party rows and segment revenue, read from the
results XBRL filings 2A already stores (Spec 02B §12.4; job VC-05). Database only: it reads the stored instances through the
storage function (`ctx.fetcher.read`: every read checks the object's hash) and fetches nothing. A file that isn't stored is
named, never fetched here (2A's `equity.filings` registers it).

**Which filings.** Per company, the latest fiscal year with a stored year-end (Q4) filing; of that year, the Q4 filing and the
Q2 filing (the half-years Regulation 23(9) covers), on every basis stored, each at its latest version on a `filed` (not
superseded) results announcement. A company's rows replace the earlier rows of the same filing and basis; a second run with
the same files writes nothing.

**What is stored** (VC-02's tables; nothing else): `equity.rpt_line` (a row per related-party transaction: the counterparty,
its relationship, the type and the entering party as filed, `entered_by`, the value in rupees, the filing's half-year as the
period) and `equity.segment_revenue` (the consolidated Q4 filing's fiscal-year revenue by reportable segment, or one `single`
row). PANs are never read. Rules and their evidence: `ft/equity/related_parties.py`.

Checks, B blocking, W warning:
- (B) `values`: every related-party amount in every file read is numeric and in rupees (a negative amount is a reversal, not a
  bad value: it is stored signed, since 0037, and `negative_amounts` names it);
- (B) `one_fiscal_year`: the filings read for a company belong to one fiscal year (each file's own year end equals the year the
  company was selected for, and its period is that year's end or six months before it);
- (W) `filings_held`: a filing that gave no rows, with its reason (unreadable, basis or period mismatch, an incomplete row);
- (W) `unmatched_company`: a filing with rows where none names the company as its entering party (held, never guessed);
- (W) `negative_amounts`: rows filed with a negative amount (stored signed; the view nets them), by company and type;
- (W) `any_other`: rows of type "Any other transaction", counted;
- (W) `subsidiary_rows`: rows a subsidiary entered into, counted per company;
- (W) `no_rows`: a company with no related-party rows in a half-year 2A stores (and whether the filing says it entered into
  none), or with only one of the year's two half-years stored;
- (W) `both_bases`: a company whose two bases both carry rows in a half-year, with whether the rows are the same;
- (W) `spelling`: a counterparty whose name differs between filings only by case, punctuation or "Ltd"/"Limited" (the view groups
  names as filed);
- (W) `segments_held`: a company whose segments aren't stored, with the reason (no consolidated Q4 filing, no disclosure, no
  segment facts, or the segments do not tie to revenue within 0.1%);
- (W) `parsed`: what the run read and stored, always passing.
"""
from __future__ import annotations

import calendar
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from ft.equity import related_parties as rp
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
    filing: rp.Filing
    fy_end: date                       # the fiscal year the company was selected for
    parsed: rp.Parsed | None = None
    error: rp.RptError | None = None


@dataclass
class Fetched:
    items: list[Item] = field(default_factory=list)
    no_half: list[str] = field(default_factory=list)       # "slug basis: Q2 not stored"
    no_year_end: list[str] = field(default_factory=list)    # universe companies with no stored year-end filing


# The latest filing per company, period and basis on a filed (not superseded) announcement; the latest fiscal year with a
# stored year-end filing; that year's year-end filing and the filing six months before it (the last day of that month).
SELECT = """
with latest as (
  select distinct on (fl.entity_id, fl.period_end, fl.basis)
         fl.document_id, fl.entity_id, fl.event_id, fl.basis, fl.period_end, fl.fy_start, fl.fy_end, d.raw_file_id
    from equity.filing fl
    join core.document d on d.id = fl.document_id
    join core.event ev on ev.id = fl.event_id and ev.status = 'filed'
    join equity.company c on c.entity_id = fl.entity_id
    join core.entity ce on ce.id = fl.entity_id and ce.market = 'IN'
   order by fl.entity_id, fl.period_end, fl.basis, d.version_no desc, fl.filed_at desc nulls last, d.id desc
), year_of as (
  select entity_id, max(fy_end) as fy_end from latest where fy_end is not null and period_end = fy_end group by entity_id
)
select l.document_id, l.entity_id, e.slug, l.event_id, l.basis, l.period_end, l.fy_start, l.fy_end, l.raw_file_id,
       y.fy_end as selected_fy_end
  from latest l
  join year_of y on y.entity_id = l.entity_id
  join core.entity e on e.id = l.entity_id
 where l.period_end = y.fy_end
    or l.period_end = (date_trunc('month', y.fy_end) - interval '5 months' - interval '1 day')::date
 order by e.slug, l.period_end, l.basis
"""
UNIVERSE_WITHOUT_YEAR = """
select e.slug from equity.company c join core.entity e on e.id = c.entity_id
 where e.market = 'IN'
   and not exists (select 1 from equity.filing fl join core.event ev on ev.id = fl.event_id and ev.status = 'filed'
                    where fl.entity_id = c.entity_id and fl.period_end = fl.fy_end)
 order by 1"""


@register
class EquityRelatedParties(Task):
    key = "equity.related_parties"
    version = rp.PARSER_VERSION
    requires = "equity.rpt_line"           # the production guard's table (ft/equity/guard.py)

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (no transaction; reads only)
    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        rows = f.db.execute(SELECT).fetchall()
        out = Fetched()
        out.no_year_end = [r[0] for r in f.db.execute(UNIVERSE_WITHOUT_YEAR).fetchall()]
        seen: dict[tuple[int, str], set[date]] = {}
        t0 = time.monotonic()
        for n, (doc_id, entity_id, slug, event_id, basis, period_end, fy_start, fy_end, raw_id, selected) in enumerate(rows, 1):
            filing = rp.Filing(basis, period_end, fy_start, fy_end, period_end == selected)
            item = Item(doc_id, entity_id, slug, event_id, filing, selected)
            seen.setdefault((entity_id, slug), set()).add(period_end)
            if raw_id is None:
                item.error = rp.RptError("unreadable", "the document has no stored file")
            else:
                body = f.read(raw_id)               # a storage or hash error fails the task: nothing is written
                try:
                    item.parsed = rp.parse_filing(body, filing)
                except rp.RptError as e:
                    item.error = e
            out.items.append(item)
            if n % 25 == 0 or n == len(rows):                 # counts and seconds only: the run log is public
                print(f"  equity.related_parties: read {n} of {len(rows)} files, {time.monotonic() - t0:.0f} s")
        for (_, slug), ends in sorted(seen.items(), key=lambda kv: kv[0][1]):
            if len(ends) < 2:
                out.no_half.append(f"{slug}: only the {max(ends).isoformat()} filing is stored (the year's other half-year isn't)")
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        """A US runner talks to Mumbai: one round trip per row took production's first run past the workflow's 30 minutes
        (11 Nov 2026, #190). The reads stay one per filing; the writes are collected and sent as pipelined `executemany`
        batches (as `equity.metrics` does, #117), the deletes before the inserts."""
        n = 0
        t0 = time.monotonic()
        del_rpt: list[tuple] = []
        ins_rpt: list[tuple] = []
        del_seg: list[tuple] = []
        ins_seg: list[tuple] = []
        for i, it in enumerate(f.items, 1):
            want_rpt = [(r.line_no, r.counterparty, r.relationship, r.txn_type, r.entering_party, r.entered_by, r.value,
                         it.parsed.period_start, it.parsed.period_end) for r in (it.parsed.rows if it.parsed else [])]
            have = [tuple(r) for r in tx.execute(
                """select line_no, counterparty, relationship, txn_type, entering_party, entered_by, value_in_period,
                          period_start, period_end from equity.rpt_line
                    where results_event_id = %s and basis = %s order by line_no""",
                (it.event_id, it.filing.basis)).fetchall()]
            if have != want_rpt:
                del_rpt.append((it.event_id, it.filing.basis))
                ins_rpt.extend((it.event_id, it.filing.basis, *w) for w in want_rpt)
                n += len(want_rpt)
            seg = it.parsed.segments if it.parsed else None
            if it.filing.year_end and it.filing.basis == "consolidated":
                start = it.filing.fy_start
                want_seg = [(s.line_no, s.reporting, s.description, s.revenue, start, it.filing.period_end)
                            for s in (seg.rows if seg else [])]
                have_seg = [tuple(r) for r in tx.execute(
                    """select line_no, reporting, description, revenue, period_start, period_end from equity.segment_revenue
                        where results_event_id = %s and basis = 'consolidated' order by line_no""",
                    (it.event_id,)).fetchall()]
                if have_seg != want_seg:
                    del_seg.append((it.event_id,))
                    ins_seg.extend((it.event_id, *w) for w in want_seg)
                    n += len(want_seg)
            if i % 25 == 0 or i == len(f.items):                  # counts and seconds only: the run log is public
                print(f"  equity.related_parties: compared {i} of {len(f.items)} filings, {time.monotonic() - t0:.0f} s")
        with tx.cursor() as cur:
            if del_rpt:
                cur.executemany("delete from equity.rpt_line where results_event_id = %s and basis = %s", del_rpt)
            if del_seg:
                cur.executemany("delete from equity.segment_revenue where results_event_id = %s and basis = 'consolidated'",
                                del_seg)
            if ins_rpt:
                cur.executemany("""insert into equity.rpt_line (results_event_id, basis, line_no, counterparty, relationship,
                                   txn_type, entering_party, entered_by, value_in_period, period_start, period_end)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", ins_rpt)
            if ins_seg:
                cur.executemany("""insert into equity.segment_revenue (results_event_id, basis, line_no, reporting,
                                   description, revenue, period_start, period_end)
                                   values (%s, 'consolidated', %s, %s, %s, %s, %s, %s)""", ins_seg)
        print(f"  equity.related_parties: wrote {len(ins_rpt)} related-party rows and {len(ins_seg)} segment rows, "
              f"{time.monotonic() - t0:.0f} s")
        return n

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        items = f.items
        read = [it for it in items if it.parsed is not None]
        bad = [f"{it.slug} {it.filing.period_end.isoformat()} {it.filing.basis}: {it.error}"
               for it in items if it.error is not None and it.error.check == "value"]
        out = [Check("values", not bad, ("an amount isn't numeric or isn't in rupees: " + _names(bad)) if bad
                     else f"every related-party amount in {len(items)} files is numeric and in rupees")]
        out.append(self._one_year(items))
        held = [it for it in items if it.error is not None and it.error.check not in ("value", "unmatched")]
        out.append(Check("filings_held", not held, ("held, so no rows: " + _names(
            [f"{it.slug} {it.filing.period_end.isoformat()} {it.filing.basis} ({it.error.check}): {it.error}" for it in held]))
                         if held else "no filing was held", blocking=False))
        unmatched = [f"{it.slug} {it.filing.period_end.isoformat()} {it.filing.basis}: {it.error}"
                     for it in items if it.error is not None and it.error.check == "unmatched"]
        out.append(Check("unmatched_company", not unmatched, ("held, no row matched the company: " + _names(unmatched))
                         if unmatched else "every filing with rows names the company as an entering party", blocking=False))
        neg: dict[str, Counter] = {}
        for it in read:
            neg.setdefault(it.slug, Counter()).update(it.parsed.negatives)
        negs = [f"{s} {sum(c.values())} ({', '.join(f'{k} {v}' for k, v in sorted(c.items()))})"
                for s, c in sorted(neg.items()) if c]
        out.append(Check("negative_amounts", not negs,
                         ("rows filed with a negative amount, stored signed: " + _names(negs))
                         if negs else "no row was filed with a negative amount", blocking=False))
        other = sum(1 for it in read for r in it.parsed.rows if r.txn_type.casefold() == "any other transaction")
        out.append(Check("any_other", True, f"{other} rows of type 'Any other transaction' stored", blocking=False))
        subs = Counter()
        for it in read:
            subs[it.slug] += sum(1 for r in it.parsed.rows if r.entered_by == "subsidiary")
        out.append(Check("subsidiary_rows", True, "rows entered into by a subsidiary, per company: " + (
            _names([f"{s} {n}" for s, n in sorted(subs.items()) if n]) or "none"), blocking=False))
        out.append(self._no_rows(f, read))
        out.append(self._both_bases(read))
        out.append(self._spelling(read))
        out.append(self._segments(f, items))
        stored = sum(len(it.parsed.rows) for it in read)
        segs = sum(len(it.parsed.segments.rows) for it in read if it.parsed.segments)
        companies = len({it.slug for it in items})
        self.result = {"companies": companies, "filings": len(items), "read": len(read), "rpt_rows": stored,
                       "segment_rows": segs}
        out.append(Check("parsed", True, f"{len(read)} of {len(items)} filings of {companies} companies read: {stored} related-party "
                                         f"rows and {segs} segment rows to hold", blocking=False))
        return out

    @staticmethod
    def _one_year(items: list[Item]) -> Check:
        wrong = []
        for it in items:
            if it.parsed is None:
                continue
            ends = {it.fy_end, it.parsed.period_end}
            if it.parsed.fy_end is not None and it.parsed.fy_end != it.fy_end:
                wrong.append(f"{it.slug} {it.filing.period_end.isoformat()}: the file's fiscal year ends "
                             f"{it.parsed.fy_end.isoformat()}, the company's selected year {it.fy_end.isoformat()}")
            elif it.filing.period_end not in (it.fy_end, rp_half_start(it.fy_end)) or it.parsed.period_end not in ends:
                wrong.append(f"{it.slug} {it.filing.period_end.isoformat()}: not one of the year's two half-years")
        return Check("one_fiscal_year", not wrong, ("the halves aren't one fiscal year: " + _names(wrong)) if wrong
                     else "every filing read is the year-end or the half-year of its company's selected fiscal year")

    @staticmethod
    def _no_rows(f: Fetched, read: list[Item]) -> Check:
        halves: dict[tuple[str, date], list[Item]] = {}
        for it in read:
            halves.setdefault((it.slug, it.filing.period_end), []).append(it)
        none = []
        for (slug, pe), its in sorted(halves.items()):
            if not any(i.parsed.rows for i in its):
                said = any(i.parsed.declared_none for i in its)
                none.append(f"{slug} {pe.isoformat()}" + (" (filed as none)" if said else ""))
        extra = list(f.no_half)
        missing_year = f.no_year_end
        detail = []
        if none:
            detail.append("no related-party rows in a stored half-year: " + _names(none))
        if extra:
            detail.append("one half-year only: " + _names(extra))
        if missing_year:
            detail.append("no year-end filing stored: " + _names(missing_year))
        return Check("no_rows", not detail, "; ".join(detail) if detail else "every stored half-year has rows",
                     blocking=False)

    @staticmethod
    def _both_bases(read: list[Item]) -> Check:
        by: dict[tuple[str, date], dict[str, list[rp.RptRow]]] = {}
        for it in read:
            if it.parsed.rows:
                by.setdefault((it.slug, it.filing.period_end), {})[it.filing.basis] = it.parsed.rows
        both = []
        for (slug, pe), d in sorted(by.items()):
            if len(d) == 2:
                both.append(f"{slug} {pe.isoformat()} ({'the same rows' if _key(d['standalone']) == _key(d['consolidated']) else 'different rows'})")
        return Check("both_bases", not both, ("both bases carry rows: " + _names(both)) if both
                     else "no company carries related-party rows on both bases", blocking=False)

    @staticmethod
    def _spelling(read: list[Item]) -> Check:
        spell: dict[tuple[str, str, str], set[str]] = {}
        for it in read:
            for r in it.parsed.rows:
                spell.setdefault((it.slug, it.filing.basis, rp.norm_name(r.counterparty)), set()).add(r.counterparty)
        diff = [f"{slug} {basis}: {len(names)} spellings of one counterparty (e.g. {min(names)!r})"
                for (slug, basis, _), names in sorted(spell.items()) if len(names) > 1]
        return Check("spelling", not diff, ("a counterparty is spelt differently between filings: " + _names(diff)) if diff
                     else "no counterparty is spelt differently between filings", blocking=False)

    @staticmethod
    def _segments(f: Fetched, items: list[Item]) -> Check:
        held = []
        have_cons: set[str] = set()
        for it in items:
            if not (it.filing.year_end and it.filing.basis == "consolidated"):
                continue
            have_cons.add(it.slug)
            if it.error is not None:
                held.append(f"{it.slug}: the filing was held ({it.error.check})")
            elif it.parsed.segments is None or it.parsed.segments.held:
                held.append(f"{it.slug}: {it.parsed.segments.held if it.parsed.segments else 'no segment reading'}")
        for slug in sorted({it.slug for it in items} - have_cons):
            held.append(f"{slug}: no consolidated year-end filing is stored")
        return Check("segments_held", not held, ("segments not stored: " + _names(held)) if held
                     else "every company's segments are stored", blocking=False)


def _key(rows: list[rp.RptRow]) -> list:
    return sorted((r.counterparty, r.txn_type, r.entering_party, r.value) for r in rows)


def rp_half_start(fy_end: date) -> date:
    """The month-end six months before a fiscal year end (the Q2 filing's period end)."""
    m = fy_end.month - 6
    y = fy_end.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, calendar.monthrange(y, m)[1])
