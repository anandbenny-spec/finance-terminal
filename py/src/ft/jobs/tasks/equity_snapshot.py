"""equity.snapshot: one `equity.screener_snapshot` row per universe company for the snapshot date (Spec 02A §3.2; job 2A-15).

Database only: it fetches nothing. The snapshot date is the date of the newest market capitalisation stored (the latest
close's date). Each row is `ft/equity/snapshot.py`'s: the figures come from `equity.metric_value`, the promoter holding from
`equity.shareholding` (the newest pattern filed on or before the date) and the implied and delivered figures from the
company's latest `reverse_dcf` and `implied_roe` runs of `equity.valuation` (`run_after`); `notes` holds the label and the
held-back reason of each column. `beta` is the **bottom-up** beta, the one the DCF uses (decided by Claude, standing
delegation, 28 Oct 2026, Spec 02A §10 decision 6; the company page shows both).

Rows are written weekly and kept (multiples' history builds up): a re-run on the same date updates a row in place only when
a value changed, and a re-run on unchanged data writes nothing. A company with no close on the snapshot date gets no row
(named in the `coverage` check). Gating is the views' job: hidden columns are stored as computed.

Checks, B blocking, W warning:
- (B) `coverage`: every universe company with a price on the snapshot date has its row;
- (B) `one_row`: no company has two rows for the date, and every written row's date is the snapshot date;
- (W) `unpriced`: companies with no close on the snapshot date, named;
- (W) `banned_words`: no stored note holds one of Brief §11's banned words (whole words, the site's own text).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from psycopg.types.json import Jsonb

from ft.equity import snapshot as sn
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 10
BANNED = re.compile(r"\b(overvalued|undervalued|cheap|expensive|upside|fair value|target|buy|sell)\b", re.IGNORECASE)
COLUMNS = ("price", "price_date", "market_cap", "shares", "pe_ttm", "pb", "ev", "ev_ebit", "ev_sales", "dividend_yield",
           "roe", "ebit_margin", "revenue_cagr_3y", "revenue_cagr_5y", "debt_to_equity", "promoter_pct", "beta",
           "return_1y", "traded_value_median_3m", "implied_growth", "delivered_growth", "implied_roe", "delivered_roe")


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Fetched:
    date: date | None = None
    rows: dict[int, sn.Row] = field(default_factory=dict)       # entity id → row
    slugs: dict[int, str] = field(default_factory=dict)
    unpriced: list[str] = field(default_factory=list)
    priced: set[int] = field(default_factory=set)


def _notes(row: sn.Row) -> dict:
    return json.loads(json.dumps(row.notes, ensure_ascii=False))


@register
class EquitySnapshot(Task):
    key = "equity.snapshot"
    requires = "equity.screener_snapshot"
    slug_prefix: str | None = None              # tests: only the companies whose slug starts with it

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (reads only)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        out = Fetched()
        like = (self.slug_prefix or "") + "%"
        companies = db.execute("""select c.entity_id, e.slug, c.valuation_family from equity.company c
                                  join core.entity e on e.id = c.entity_id where e.slug like %s order by e.slug""",
                               (like,)).fetchall()
        ids = [c[0] for c in companies]
        out.date = db.execute("""select max(period_end) from equity.metric_value
                                  where metric_key = 'market_cap' and period_kind = 'market' and entity_id = any(%s)""",
                              (ids,)).fetchone()[0]
        if out.date is None:
            return out
        day = out.date
        kinds = dict(db.execute("select key, label_kind from equity.metric").fetchall())
        rows: dict[int, dict] = {i: {} for i in ids}
        # the latest period of each (metric, kind, basis) at or before the date: enough for every column
        for eid, key, basis, kind, end, value, held, inputs in db.execute(
                """select distinct on (entity_id, metric_key, period_kind, basis)
                          entity_id, metric_key, basis, period_kind, period_end, value, held_reason, inputs
                     from equity.metric_value
                    where entity_id = any(%s) and metric_key = any(%s) and period_end <= %s
                    order by entity_id, metric_key, period_kind, basis, period_end desc""",
                (ids, list(sn.METRIC_KEYS), day)).fetchall():
            rows[eid][(key, basis, kind, end)] = (value, held, inputs or {})
        promoter = {eid: pct for eid, pct in db.execute(
            """select distinct on (entity_id) entity_id, promoter_pct from equity.shareholding
                where entity_id = any(%s) and as_of <= %s order by entity_id, as_of desc, filed_at desc nulls last""",
            (ids, day)).fetchall()}
        runs: dict[tuple[int, str], sn.Run] = {}
        for eid, kind, status, reason, result, delivered, why in db.execute(
                """select distinct on (entity_id, kind) entity_id, kind, status, reason, result, delivered,
                          delivered_reason from equity.valuation_run
                    where entity_id = any(%s) and kind in ('reverse_dcf', 'implied_roe') and as_of <= %s
                    order by entity_id, kind, as_of desc, model_version desc""", (ids, day)).fetchall():
            runs[(eid, kind)] = sn.Run(status, reason, result, delivered, why)
        priced = {eid for (eid,) in db.execute(
            """select p.entity_id from equity.price_daily p join equity.company c
                 on c.entity_id = p.entity_id and c.price_exchange = p.exchange
                where p.trade_date = %s and p.entity_id = any(%s)""", (day, ids)).fetchall()}
        for eid, slug, family in companies:
            out.slugs[eid] = slug
            if (("market_cap", "none", "market", day)) not in rows[eid]:
                if eid in priced:
                    out.rows[eid] = sn.build(family, day, {}, kinds, None, None, None)     # priced, no metrics yet
                else:
                    out.unpriced.append(slug)
                continue
            out.rows[eid] = sn.build(family, day, rows[eid], kinds, promoter.get(eid), runs.get((eid, "reverse_dcf")),
                                     runs.get((eid, "implied_roe")))
        out.priced = priced
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        if f.date is None:
            self.result = {"rows": 0}
            return 0
        have = {r[0]: r[1:] for r in tx.execute(
            f"""select entity_id, {", ".join(COLUMNS)}, valuation_family, notes from equity.screener_snapshot
                 where snapshot_date = %s""", (f.date,)).fetchall()}
        new, changed = [], []
        for eid, row in f.rows.items():
            values = [row.values.get(c) for c in COLUMNS]
            notes = _notes(row)
            old = have.get(eid)
            if old is not None:
                if list(old[:len(COLUMNS)]) == values and old[len(COLUMNS)] == row.family \
                        and (old[len(COLUMNS) + 1] or {}) == notes:
                    continue
                changed.append((*values, row.family, Jsonb(notes), f.date, eid))
            else:
                new.append((f.date, eid, *values, row.family, Jsonb(notes)))
        cols = ", ".join(COLUMNS)
        with tx.cursor() as cur:
            if new:
                cur.executemany(f"""insert into equity.screener_snapshot (snapshot_date, entity_id, {cols},
                                        valuation_family, notes) values (%s, %s, {", ".join(["%s"] * len(COLUMNS))},
                                        %s, %s)""", new)
            if changed:
                sets = ", ".join(f"{c} = %s" for c in COLUMNS)
                cur.executemany(f"""update equity.screener_snapshot set {sets}, valuation_family = %s, notes = %s
                                     where snapshot_date = %s and entity_id = %s""", changed)
        self.result = {"snapshot_date": f.date.isoformat(), "rows": len(f.rows), "inserted": len(new),
                       "updated": len(changed)}
        return len(new) + len(changed)

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        if f.date is None:
            return [Check("coverage", True, "no price is stored yet, so no snapshot was written", blocking=False)]
        stored = {r[0]: r[1] for r in tx.execute(
            "select entity_id, snapshot_date from equity.screener_snapshot where snapshot_date = %s",
            (f.date,)).fetchall()}
        missing = sorted(f.slugs[e] for e in f.priced if e not in stored)
        out = [Check("coverage", not missing, ("priced companies without a snapshot row: " + _names(missing)) if missing
                     else f"{len(stored)} rows for {f.date}: every company with a close that day has one")]
        dup = tx.execute("""select count(*) from (select 1 from equity.screener_snapshot where snapshot_date = %s
                             group by entity_id having count(*) > 1) x""", (f.date,)).fetchone()[0]
        out.append(Check("one_row", dup == 0, "one row per company for the date" if dup == 0
                         else f"{dup} companies have more than one row"))
        out.append(Check("unpriced", not f.unpriced,
                         ("companies with no close on the snapshot date: " + _names(sorted(f.unpriced)))
                         if f.unpriced else "every company has a close on the snapshot date", blocking=False))
        found = sorted({f"{f.slugs[e]} {c}: {', '.join(w)}" for e, r in f.rows.items() for c, n in r.notes.items()
                        for text in [str(n.get("reason", "")) + " " + str(n.get("label", ""))]
                        if (w := [m.group(1).lower() for m in BANNED.finditer(text)])})
        out.append(Check("banned_words", not found, ("banned words in stored notes: " + _names(found)) if found
                         else "no stored note holds a banned word", blocking=False))
        self.result["priced"] = len(f.priced)
        self.result["unpriced"] = len(f.unpriced)
        return out

