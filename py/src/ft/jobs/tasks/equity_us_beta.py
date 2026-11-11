"""us.beta: the US companies' two betas in `equity.metric_value` (Spec 02A.1 §5.4, Spec 02A §5.8; job US-04, #403).

- `beta_regression`: the slope of 104 weekly returns of the adjusted close against the S&P 500 (`yf.^GSPC`), paired on
  the same date; held under 80 weeks. India's regression (`ft/equity/beta.py`), with the S&P 500 for the Nifty 50.
- `beta_bottom_up`: the company's peer group's Damodaran US industry (`betas.xls`, the newest edition `us.valuation_inputs`
  stored, `market = 'US'`), its cash-corrected unlevered beta relevered at market D/E = `debt` ÷ `market_cap` (both
  from `us.metrics`) at the US marginal rate (25%, `valuation.yaml` `us`). Lenders and insurers take the industry's
  levered beta unchanged, as India's.

The load and the checks are India's (`equity.beta`): a beta is stored once per price date and kept as history, a re-run on
unchanged data writes nothing. Database only. Only `market = 'US'` companies are read; India's task reads only India's.

Checks, B blocking, W warning:
- (B) `industries`: every US peer group's Damodaran industry is in the newest US `betas.xls` edition;
- (B) `bounds`: every stored beta lies within ±5;
- (W) `differences`: |regression − bottom-up| beyond 0.5, named;
- (W) `held`: held betas by key, named.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ft.equity import beta as bt
from ft.equity import metrics as mx
from ft.equity import us_inputs as ui
from ft.jobs.runner import Check, Context
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_beta import PRICE_DAYS, Co, EquityBeta, Fetched, _names

MARKET = "US"


@register
class UsBeta(EquityBeta):
    key = "us.beta"

    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        out = Fetched()
        s = ui.settings()
        out.tax, tax_source = s.tax, s.tax_source
        for eid, slug, family, industry in db.execute(
                """select c.entity_id, e.slug, c.valuation_family, g.damodaran_industry
                     from equity.company c join core.entity e on e.id = c.entity_id
                     left join equity.peer_group g on g.key = c.peer_group_key
                    where e.market = 'US' order by e.slug""").fetchall():
            out.companies[eid] = Co(eid, slug, family, industry)
        out.edition = db.execute("select max(edition) from equity.industry_benchmark where market = %s",
                                 (MARKET,)).fetchone()[0]
        if out.edition is not None:
            out.industries = {n: (u, b) for n, u, b in db.execute(
                """select industry, unlevered_beta_cash_corrected, beta from equity.industry_benchmark
                    where edition = %s and market = %s""", (out.edition, MARKET)).fetchall()}
        out.missing_industries = sorted({f"{g}: {i}" for g, i in db.execute(
            "select key, damodaran_industry from equity.peer_group where damodaran_industry is not null and market = %s",
            (MARKET,)).fetchall() if i not in out.industries})
        index = {d: v for d, v in db.execute(
            """select o.obs_date, o.value from core.observation o join core.series s on s.id = o.series_id
                where s.key = %s""", (ui.INDEX,)).fetchall()}
        out.index_latest = max(index) if index else None
        ids = list(out.companies)
        prices: dict[int, dict[date, Decimal]] = {i: {} for i in ids}
        for eid, d, adj in db.execute(
                """select p.entity_id, p.trade_date, p.adj_close from equity.price_adjusted p
                    join (select entity_id, max(trade_date) as last from equity.price_daily
                           where entity_id = any(%s) group by 1) m on m.entity_id = p.entity_id
                   where p.adj_close is not null and p.trade_date > m.last - %s""", (ids, PRICE_DAYS)).fetchall():
            prices[eid][d] = adj
        last_price = {eid: d for eid, d in db.execute(
            "select entity_id, max(trade_date) from equity.price_daily where entity_id = any(%s) group by 1",
            (ids,)).fetchall()}
        cap = {}
        for eid, d, v, held in db.execute(
                """select distinct on (entity_id) entity_id, period_end, value, held_reason from equity.metric_value
                    where metric_key = 'market_cap' and period_kind = 'market' and entity_id = any(%s)
                    order by entity_id, period_end desc""", (ids,)).fetchall():
            cap[eid] = (d, v, held)
        debt = {}
        for eid, d, basis, v, held, label in db.execute(
                """select distinct on (entity_id) entity_id, period_end, basis, value, held_reason, inputs ->> 'label'
                     from equity.metric_value
                    where metric_key = 'debt' and period_kind = 'instant' and entity_id = any(%s)
                    order by entity_id, period_end desc, (basis = 'consolidated') desc""", (ids,)).fetchall():
            debt[eid] = (d, basis, v, held, label)
        for eid, co in out.companies.items():
            when = last_price.get(eid) or out.index_latest
            if when is None:
                continue
            if out.index_latest is None:
                reg = bt.Regression(0, held="No S&P 500 series is stored.", inputs={"index": ui.INDEX})
            else:
                reg = bt.regress(prices[eid], index, out.index_latest)
                reg.inputs["index"] = ui.INDEX
                if reg.held and "Nifty" in reg.held:
                    reg.held = "The S&P 500 did not move over the window."
            vals = [mx.Value("beta_regression", "none", "market", reg.last_date or when, reg.slope, reg.held,
                             reg.inputs) if reg.held is None else
                    mx.Value("beta_regression", "none", "market", when, None, reg.held, reg.inputs)]
            vals.append(self._us_bottom_up(co, out, tax_source, cap.get(eid), debt.get(eid), when))
            out.values[eid] = vals
        return out

    @staticmethod
    def _us_bottom_up(co: Co, f: Fetched, tax_source: str, cap, debt, when: date) -> mx.Value:
        def held(reason: str, **inputs) -> mx.Value:
            return mx.Value("beta_bottom_up", "none", "market", cap[0] if cap else when, None, reason, inputs)

        if co.industry is None:
            return held("The company's peer group has no Damodaran industry.")
        if f.edition is None or co.industry not in f.industries:
            return held("The company's Damodaran industry isn't in the newest US industry betas.", industry=co.industry)
        unlevered, levered = f.industries[co.industry]
        base = {"industry": co.industry, "edition": f.edition.isoformat(), "file": "betas.xls (US)"}
        if co.family in bt.LENDERS:
            if levered is None:
                return held("Damodaran gives no levered beta for the industry.", **base)
            return mx.Value("beta_bottom_up", "none", "market", cap[0] if cap else when, levered, None,
                            {**base, "method": "the industry's levered beta, not relevered (debt is a lender's raw "
                             "material)", "levered_beta": str(levered), "valuation_family": co.family})
        if unlevered is None:
            return held("Damodaran gives no cash-corrected unlevered beta for the industry.", **base)
        if cap is None or cap[1] is None:
            return held("Market capitalisation isn't available: " + ((cap[2] if cap else None) or "none stored."),
                        **base)
        if debt is None or debt[2] is None:
            return held("Borrowings aren't available: " + ((debt[3] if debt else None) or "no balance sheet."), **base)
        if cap[1] <= 0:
            return held("Market capitalisation isn't positive.", **base)
        beta, de = bt.relever(unlevered, debt[2], cap[1], f.tax)
        return mx.Value("beta_bottom_up", "none", "market", cap[0], beta, None, {
            **base, "method": "unlevered beta (cash-corrected) relevered at market D/E",
            "unlevered_beta": str(unlevered), "debt_to_equity": str(de), "debt": str(debt[2]),
            "debt_period_end": debt[0].isoformat(), "debt_basis": debt[1], "debt_label": debt[4],
            "market_cap": str(cap[1]), "market_cap_date": cap[0].isoformat(), "tax_rate": str(f.tax),
            "tax_rate_source": tax_source, "valuation_family": co.family})

    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out = super().checks(tx, f, task_run_id)
        out[0] = Check("industries", not f.missing_industries,
                       ("US peer groups whose Damodaran industry isn't in the newest US betas.xls edition: "
                        + _names(f.missing_industries)) if f.missing_industries
                       else f"every US peer group's industry is in the {f.edition} edition")
        return out
