"""us.snapshot: one `equity.screener_snapshot` row per US company for the US snapshot date (Spec 02A.1 §5, Spec 02A
§3.2; job US-04, #403).

India's task (`equity.snapshot`) over the `market = 'US'` companies: the snapshot date is the newest US market
capitalisation's date (the latest NYSE close), each row is `ft/equity/snapshot.py`'s from the stored US metrics and the
`us.valuation` runs, and the load and checks are India's. The one US difference: promoter holding is an Indian
disclosure, so the column is N/A with that reason (there is no US shareholding pattern to read). Rows stay hidden with
their companies until the slice's flip (Spec 02A.1 §3). Database only; India's rows are never read or written.
"""
from __future__ import annotations

from ft.equity import snapshot as sn
from ft.jobs.runner import Context
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_snapshot import EquitySnapshot, Fetched

NO_PROMOTER = "N/A: Promoter holding is an Indian disclosure; US companies don't report one."


@register
class UsSnapshot(EquitySnapshot):
    key = "us.snapshot"

    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        out = Fetched()
        like = (self.slug_prefix or "") + "%"
        companies = db.execute("""select c.entity_id, e.slug, c.valuation_family from equity.company c
                                  join core.entity e on e.id = c.entity_id where e.slug like %s and e.market = 'US'
                                  order by e.slug""", (like,)).fetchall()
        ids = [c[0] for c in companies]
        out.date = db.execute("""select max(period_end) from equity.metric_value
                                  where metric_key = 'market_cap' and period_kind = 'market' and entity_id = any(%s)""",
                              (ids,)).fetchone()[0]
        if out.date is None:
            return out
        day = out.date
        kinds = dict(db.execute("select key, label_kind from equity.metric").fetchall())
        rows: dict[int, dict] = {i: {} for i in ids}
        for eid, key, basis, kind, end, value, held, inputs in db.execute(
                """select distinct on (entity_id, metric_key, period_kind, basis)
                          entity_id, metric_key, basis, period_kind, period_end, value, held_reason, inputs
                     from equity.metric_value
                    where entity_id = any(%s) and metric_key = any(%s) and period_end <= %s
                    order by entity_id, metric_key, period_kind, basis, period_end desc""",
                (ids, list(sn.METRIC_KEYS), day)).fetchall():
            rows[eid][(key, basis, kind, end)] = (value, held, inputs or {})
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
            if ("market_cap", "none", "market", day) not in rows[eid]:
                if eid in priced:
                    row = sn.build(family, day, {}, kinds, None, None, None)      # priced, no metrics yet
                else:
                    out.unpriced.append(slug)
                    continue
            else:
                row = sn.build(family, day, rows[eid], kinds, None, runs.get((eid, "reverse_dcf")),
                               runs.get((eid, "implied_roe")))
            row.put("promoter_pct", None, reason=sn.strip_na(NO_PROMOTER))
            out.rows[eid] = row
        out.priced = priced
        return out
