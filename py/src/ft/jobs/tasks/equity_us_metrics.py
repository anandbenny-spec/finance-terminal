"""us.metrics: the US companies' derived metrics in `equity.metric_value` (Spec 02A.1 §5.4, Spec 02A §5.6; job US-04, #403).

The US tasks are module `us` (decided on #400). Weekly (`every 6 days`, after `us.filings` and `us.corporate_actions`);
its first run on a project is a refresh or backfill (`equity_due`). Database only: it fetches nothing, and it never reads
the raw bucket (Plan 05 §0.5 N10: India's lease check reads stored filings, the US has none: `ft/equity/us_metrics.py`).

It reads only `market = 'US'` companies, and India's task reads only India's (US-01's filter), so neither writes the
other's rows. The rules are `ft/equity/us_metrics.py`'s; the load is India's (`equity.metrics`: a new key inserted, a
changed value updated, a statement-period value no longer produced deleted, market values kept as history).

Checks, B blocking, W warning:
- (B) `ttm_sums`: every TTM sum equals the sum of its four quarters exactly;
- (B) `ebit_as_filed`: every EBIT is operating income as filed, one line per period;
- (B) `keys`: every key written is in the `equity.metric` catalogue;
- (W) `ebit_margin`: an operating margin beyond ±100%, named;
- (W) `companies`: a US company with no value at all, named;
- (W) `computed`: values and held reasons by key, always passing.
"""
from __future__ import annotations

from ft.equity import metrics as mx
from ft.equity import us_inputs as ui
from ft.equity import us_metrics as um
from ft.jobs.runner import Check, Context
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_metrics import PRICE_DAYS, EquityMetrics, Fetched, _names


@register
class UsMetrics(EquityMetrics):
    key = "us.metrics"

    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        out = Fetched(catalogue={k for (k,) in db.execute("select key from equity.metric")})
        for eid, slug, family, fy in db.execute(
                """select c.entity_id, e.slug, c.valuation_family, c.fy_end_month from equity.company c
                     join core.entity e on e.id = c.entity_id where e.market = 'US' order by e.slug""").fetchall():
            out.companies[eid] = mx.Company(eid, slug, family, fy)
        ids = list(out.companies)
        fins: dict[int, list[mx.Fin]] = {i: [] for i in ids}
        cols = ui.US.columns
        for row in db.execute(
                f"""select x.entity_id, x.document_id, x.basis, x.period_kind, x.period_start, x.period_end, x.tags,
                           fl.format, fl.filed_at, {", ".join(cols)}
                      from equity.financials x join equity.filing fl on fl.document_id = x.document_id
                     where x.entity_id = any(%s) and fl.parse_status = 'parsed'""", (ids,)).fetchall():
            eid, doc, basis, kind, start, end, tags, fmt, filed_at, *vals = row
            values = {c: v for c, v in zip(cols, vals, strict=True) if v is not None}
            fins[eid].append(ui.by_month(mx.Fin(doc, basis, kind, start, end, values, tags or {}, fmt, filed_at)))
        prices: dict[int, list[mx.Price]] = {i: [] for i in ids}
        for eid, d, close, adj, tv in db.execute(
                """select p.entity_id, p.trade_date, p.close, p.adj_close, p.traded_value
                     from equity.price_adjusted p
                     join (select d.entity_id, max(d.trade_date) as last from equity.price_daily d
                             join equity.company c on c.entity_id = d.entity_id and c.price_exchange = d.exchange
                            where d.entity_id = any(%s) group by 1) m on m.entity_id = p.entity_id
                    where p.trade_date > m.last - %s order by p.entity_id, p.trade_date""",
                (ids, PRICE_DAYS)).fetchall():
            prices[eid].append(mx.Price(d, close, adj, tv))
        actions: dict[int, list[mx.Action]] = {i: [] for i in ids}
        for eid, slug, kind, ex, dps, factor, status in db.execute(
                """select ev.entity_id, ev.slug, ca.kind, ca.ex_date, ca.dividend_per_share, af.price_factor, af.status
                     from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                     left join equity.adjustment_factor af on af.event_id = ca.event_id
                    where ev.entity_id = any(%s) and ev.status <> 'withdrawn'""", (ids,)).fetchall():
            actions[eid].append(mx.Action(slug, kind, ex, dps, factor, status))
        for eid, comp in out.companies.items():
            data = mx.Data(comp, fins[eid], prices[eid], actions[eid], um.breaks(actions[eid]))
            out.values[eid] = um.compute(data)
        return out

    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out = [c for c in super().checks(tx, f, task_run_id) if c.name not in ("ebit_inputs", "roa_scale")]
        slug = {eid: c.slug for eid, c in f.companies.items()}
        bad = [f"{slug[eid]} {e}" for eid, vs in f.values.items() for e in um.ebit_input_errors(vs)]
        out.insert(1, Check("ebit_as_filed", not bad, ("EBIT not read as operating income as filed: " + _names(bad))
                            if bad else "every EBIT is operating income as filed"))
        return out
