"""equity.beta: every universe company's two betas in `equity.metric_value` (Spec 02A §5.8; job 2A-12).

- `beta_regression`: the slope of 104 weekly returns of the adjusted close against the Nifty 50 (`yf.^NSEI`), paired on
  the same date; held under 80 weeks ("Fewer than 80 weeks of prices."). Slope, standard error, R² and n in `inputs`.
- `beta_bottom_up`: Damodaran's India industry (the company's peer group's `damodaran_industry`), cash-corrected
  unlevered beta of the newest `betaIndia` edition, relevered at market D/E = `debt` ÷ `market_cap` (both from
  `equity.metrics`, 2A-11; `debt` is borrowings only, labelled so). Lenders and insurers take the industry's levered
  beta unchanged (their debt is their raw material). The tax rate is `py/config/equity/valuation.yaml`'s.

Both are market values (period_kind `market`, basis `none`), labelled derived (field policy `equity.beta`). A beta is
stored once per price date and kept as history: a run writes a new key as an insert, a changed value at the same key as
an update, and never deletes. A re-run on unchanged data writes nothing. Database only: it fetches nothing.

Checks, B blocking, W warning:
- (B) `industries`: every peer group's Damodaran industry is in the newest `betaIndia` edition;
- (B) `bounds`: every stored beta lies within ±5;
- (W) `differences`: |regression − bottom-up| beyond 0.5, named, with the share of companies;
- (W) `held`: held betas by key, named (recent listings, a held corporate action, no debt figure).

`run_after` names `policy.market_series`, which runs in `daily`, not `weekly`: the runner orders only tasks in the same
workflow run, so there it simply reads the observations the daily run stored. The Nifty's newest stored date is the
window's anchor, so a stale index shortens nothing silently: the window ends the week before it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from psycopg.types.json import Jsonb

from ft.equity import beta as bt
from ft.equity import metrics as mx
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12
KEYS = ("beta_regression", "beta_bottom_up")
PRICE_DAYS = 800                    # 105 weeks plus slack back from the newest price
INDEX = "yf.^NSEI"


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Co:
    eid: int
    slug: str
    family: str
    industry: str | None


@dataclass
class Fetched:
    companies: dict[int, Co] = field(default_factory=dict)
    values: dict[int, list[mx.Value]] = field(default_factory=dict)
    industries: dict[str, tuple[Decimal | None, Decimal | None]] = field(default_factory=dict)   # name → (βu, β)
    edition: date | None = None
    missing_industries: list[str] = field(default_factory=list)
    index_latest: date | None = None
    tax: Decimal = Decimal(0)


@register
class EquityBeta(Task):
    key = "equity.beta"
    requires = "equity.metric_value"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (reads only)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        out = Fetched()
        out.tax, tax_source = bt.tax_rate()
        for eid, slug, family, industry in db.execute(
                """select c.entity_id, e.slug, c.valuation_family, g.damodaran_industry
                     from equity.company c join core.entity e on e.id = c.entity_id
                     left join equity.peer_group g on g.key = c.peer_group_key order by e.slug""").fetchall():
            out.companies[eid] = Co(eid, slug, family, industry)
        edition = db.execute("select max(edition) from equity.industry_benchmark where market = 'IN'").fetchone()[0]
        out.edition = edition
        if edition is not None:
            out.industries = {n: (u, b) for n, u, b in db.execute(
                """select industry, unlevered_beta_cash_corrected, beta from equity.industry_benchmark
                    where edition = %s and market = 'IN'""", (edition,)).fetchall()}
        out.missing_industries = sorted({f"{g}: {i}" for g, i in db.execute(
            "select key, damodaran_industry from equity.peer_group where damodaran_industry is not null").fetchall()
            if i not in out.industries})
        index = {d: v for d, v in db.execute(
            """select o.obs_date, o.value from core.observation o join core.series s on s.id = o.series_id
                where s.key = %s""", (INDEX,)).fetchall()}
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
        cap = {}                                                  # newest market cap by company
        for eid, d, v, held in db.execute(
                """select distinct on (entity_id) entity_id, period_end, value, held_reason from equity.metric_value
                    where metric_key = 'market_cap' and period_kind = 'market' and entity_id = any(%s)
                    order by entity_id, period_end desc""", (ids,)).fetchall():
            cap[eid] = (d, v, held)
        debt = {}                                                 # newest debt by company (any basis)
        for eid, d, basis, v, held, label in db.execute(
                """select distinct on (entity_id) entity_id, period_end, basis, value, held_reason, inputs ->> 'label'
                     from equity.metric_value
                    where metric_key = 'debt' and period_kind = 'instant' and entity_id = any(%s)
                    order by entity_id, period_end desc, (basis = 'consolidated') desc""", (ids,)).fetchall():
            debt[eid] = (d, basis, v, held, label)
        for eid, co in out.companies.items():
            when = last_price.get(eid) or out.index_latest
            if when is None:
                continue                                          # nothing stored yet: no date to hold a row on
            vals = []
            if out.index_latest is None:
                reg = bt.Regression(0, held="No Nifty 50 series is stored.", inputs={"index": INDEX})
            else:
                reg = bt.regress(prices[eid], index, out.index_latest)
            vals.append(mx.Value("beta_regression", "none", "market", reg.last_date or when, reg.slope, reg.held,
                                 reg.inputs) if reg.held is None else
                        mx.Value("beta_regression", "none", "market", when, None, reg.held, reg.inputs))
            vals.append(self._bottom_up(co, out, tax_source, cap.get(eid), debt.get(eid), when))
            out.values[eid] = vals
        return out

    @staticmethod
    def _bottom_up(co: Co, f: Fetched, tax_source: str, cap, debt, when: date) -> mx.Value:
        def held(reason: str, **inputs) -> mx.Value:
            return mx.Value("beta_bottom_up", "none", "market", cap[0] if cap else when, None, reason, inputs)

        if co.industry is None:
            return held("The company's peer group has no Damodaran industry.")
        if f.edition is None or co.industry not in f.industries:
            return held("The company's Damodaran industry isn't in the newest betaIndia edition.", industry=co.industry)
        unlevered, levered = f.industries[co.industry]
        base = {"industry": co.industry, "edition": f.edition.isoformat()}
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

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        ids = list(f.values)
        have = {(r[0], r[1], r[2], r[3], r[4]): (r[5], r[6], r[7]) for r in tx.execute(
            """select entity_id, metric_key, basis, period_kind, period_end, value, held_reason, inputs
                 from equity.metric_value where metric_key = any(%s) and entity_id = any(%s)""",
            (list(KEYS), ids)).fetchall()}
        new, changed = [], []
        for eid, values in f.values.items():
            for v in values:
                k = (eid, v.key, v.basis, v.period_kind, v.period_end)
                inputs = json.loads(json.dumps(v.inputs, ensure_ascii=False))
                old = have.get(k)
                if old is not None and old[0] == v.value and old[1] == v.held and (old[2] or {}) == inputs:
                    continue
                if old is None:
                    new.append((*k, v.value, v.held, Jsonb(inputs)))
                else:
                    changed.append((v.value, v.held, Jsonb(inputs), *k))
        with tx.cursor() as cur:                                  # pipelined, as equity.metrics does (#117)
            if new:
                cur.executemany("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind,
                                       period_end, value, held_reason, inputs) values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                new)
            if changed:
                cur.executemany("""update equity.metric_value set value = %s, held_reason = %s, inputs = %s
                                    where entity_id = %s and metric_key = %s and basis = %s and period_kind = %s
                                      and period_end = %s""", changed)
        self.result = {"inserted": len(new), "updated": len(changed), "companies": len(f.values),
                       "edition": f.edition.isoformat() if f.edition else None,
                       "index_latest": f.index_latest.isoformat() if f.index_latest else None}
        return len(new) + len(changed)

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        slug = {eid: c.slug for eid, c in f.companies.items()}
        out = [Check("industries", not f.missing_industries,
                     ("peer groups whose Damodaran industry isn't in the newest betaIndia edition: "
                      + _names(f.missing_industries)) if f.missing_industries
                     else f"every peer group's industry is in the {f.edition} edition")]
        wide = [f"{slug[eid]} {v.key} ({v.value})" for eid, vs in f.values.items() for v in vs
                if v.value is not None and abs(v.value) > bt.BOUND]
        out.append(Check("bounds", not wide, ("a beta beyond ±5: " + _names(wide)) if wide
                         else "every stored beta lies within ±5"))
        reg, bu, far = {}, {}, []
        for eid, vs in f.values.items():
            for v in vs:
                if v.value is not None:
                    (reg if v.key == "beta_regression" else bu)[eid] = v.value
        both = sorted(set(reg) & set(bu), key=lambda e: slug[e])
        for e in both:
            if abs(reg[e] - bu[e]) > bt.DIFFERENCE:
                far.append(f"{slug[e]} (regression {reg[e]:.2f}, bottom-up {bu[e]:.2f})")
        out.append(Check("differences", not far,
                         (f"{len(far)} of {len(both)} companies differ by more than 0.5: " + _names(far)) if far
                         else f"none of {len(both)} companies differ by more than 0.5", blocking=False))
        held = [f"{slug[eid]} {v.key} ({v.held})" for eid, vs in f.values.items() for v in vs if v.value is None]
        out.append(Check("held", not held, ("held betas: " + _names(held)) if held else "no beta is held",
                         blocking=False))
        self.result.update(regression=len(reg), bottom_up=len(bu), differ_by_more_than_half=len(far),
                           compared=len(both), held=len(held))
        return out
