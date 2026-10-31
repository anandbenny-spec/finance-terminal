"""equity.metrics: every universe company's derived metrics in `equity.metric_value` (Spec 02A §5.6; job 2A-11), from
the stored statements (`equity.financials`, 2A-09), prices (`equity.price_adjusted`, 2A-06), corporate actions and
factors (2A-08). The rules are in `ft/equity/metrics.py`; the lease rule's threshold in `py/config/equity/metrics.yaml`
and the curated structural breaks in `py/config/equity/structural_breaks.yaml`.

Database only: it fetches nothing. Lease payments aren't in `equity.financials`, so for each company the newest
half-year or year filing with a cash flow is read from storage (`ctx.fetcher.read`, hash-checked) for them.

A run recomputes every company's values and writes only what changed: a new key is inserted, a changed value, reason
or inputs updated, and a stored statement-period value the run no longer produces deleted (market values are kept as
history, one set per price date). So a re-run on unchanged data writes nothing.

Checks, B blocking, W warning:
- (B) `ttm_sums`: every TTM sum (revenue, profit to owners, EBIT, NII) equals the sum of its four quarters exactly;
- (B) `ebit_inputs`: every derived EBIT has all its inputs (profit before exceptional items, finance costs, other
  income, the share of associates) for each of its periods;
- (B) `keys`: every key written is in the `equity.metric` catalogue (job_writer can't add one);
- (W) `roa_scale`: the banks' quarters by ROA scale (quarterly, annualised, held), the held ones named;
- (W) `ebit_margin`: an EBIT margin beyond ±100%, named;
- (W) `companies`: a universe company with no value at all, named;
- (W) `computed`: values and held reasons by key, always passing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from ft.equity import metrics as mx
from ft.equity import statements as st
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12
PRICE_DAYS = 400               # a year back from the latest close, and the 63 sessions before it


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Fetched:
    companies: dict[int, mx.Company] = field(default_factory=dict)
    values: dict[int, list[mx.Value]] = field(default_factory=dict)
    catalogue: set[str] = field(default_factory=set)
    lease_reads: int = 0


def _json(inputs: dict) -> dict:
    return json.loads(json.dumps(inputs, ensure_ascii=False))


@register
class EquityMetrics(Task):
    key = "equity.metrics"
    requires = "equity.metric_value"       # the production guard's table (ft/equity/guard.py)

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (no transaction; reads only)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        rules = mx.Rules.load()
        curated = mx.load_breaks()
        out = Fetched(catalogue={k for (k,) in db.execute("select key from equity.metric")})
        for eid, slug, family, fy in db.execute(
                """select c.entity_id, e.slug, c.valuation_family, c.fy_end_month from equity.company c
                     join core.entity e on e.id = c.entity_id order by e.slug""").fetchall():
            out.companies[eid] = mx.Company(eid, slug, family, fy)
        ids = list(out.companies)
        fins: dict[int, list[mx.Fin]] = {i: [] for i in ids}
        cols = st.COLUMNS
        filings: dict[int, tuple] = {}
        for row in db.execute(
                f"""select x.entity_id, x.document_id, x.basis, x.period_kind, x.period_start, x.period_end, x.tags,
                           fl.format, fl.filed_at, d.raw_file_id, d.external_key, fl.fy_start, {", ".join(cols)}
                      from equity.financials x join equity.filing fl on fl.document_id = x.document_id
                      join core.document d on d.id = x.document_id
                     where x.entity_id = any(%s) and fl.parse_status = 'parsed'""", (ids,)).fetchall():
            eid, doc, basis, kind, start, end, tags, fmt, filed_at, raw_id, name, fy_start, *vals = row
            values = {c: v for c, v in zip(cols, vals, strict=True) if v is not None}
            fins[eid].append(mx.Fin(doc, basis, kind, start, end, values, tags or {}, fmt, filed_at))
            filings[doc] = (raw_id, st.Filing(name, basis, fmt, end, fy_start))
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
            breaks = [mx.Break(a.kind, a.ex_date, a.slug) for a in actions[eid] if a.kind in mx.BREAK_KINDS]
            breaks += curated.get(comp.slug, [])
            data = mx.Data(comp, fins[eid], prices[eid], actions[eid], breaks)
            last = prices[eid][-1].trade_date if prices[eid] else None
            row = mx.lease_row(mx.Book(fins[eid]), last) if last and comp.family not in mx.NON_OPERATING else None
            if row is not None:
                raw_id, filing = filings[row.document_id]
                got = None
                if raw_id is not None:
                    got = st.lease_payments(ctx.fetcher.read(raw_id), filing)   # a storage error fails the task
                    out.lease_reads += 1
                data.leases[row.document_id] = mx.Lease(*got) if got else mx.Lease(None)
            out.values[eid] = mx.compute(data, rules)
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        ids = list(f.companies)
        have = {(r[0], r[1], r[2], r[3], r[4]): (r[5], r[6], r[7]) for r in tx.execute(
            """select entity_id, metric_key, basis, period_kind, period_end, value, held_reason, inputs
                 from equity.metric_value where entity_id = any(%s)""", (ids,)).fetchall()}
        inserted = updated = deleted = 0
        want = set()
        for eid, values in f.values.items():
            for v in values:
                k = (eid, v.key, v.basis, v.period_kind, v.period_end)
                want.add(k)
                inputs = _json(v.inputs)
                old = have.get(k)
                if old is not None and old[0] == v.value and old[1] == v.held and (old[2] or {}) == inputs:
                    continue
                if old is None:
                    tx.execute("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind,
                                      period_end, value, held_reason, inputs) values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                               (*k, v.value, v.held, Jsonb(inputs)))
                    inserted += 1
                else:
                    tx.execute("""update equity.metric_value set value = %s, held_reason = %s, inputs = %s
                                   where entity_id = %s and metric_key = %s and basis = %s and period_kind = %s
                                     and period_end = %s""", (v.value, v.held, Jsonb(inputs), *k))
                    updated += 1
        stale = [k for k in have if k not in want and k[3] != "market" and k[1] not in ("beta_regression",
                                                                                         "beta_bottom_up")]
        for k in stale:
            tx.execute("""delete from equity.metric_value where entity_id = %s and metric_key = %s and basis = %s
                           and period_kind = %s and period_end = %s""", k)
            deleted += 1
        self.result = {"inserted": inserted, "updated": updated, "deleted": deleted, "lease_reads": f.lease_reads}
        return inserted + updated + deleted

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        slug = {eid: c.slug for eid, c in f.companies.items()}
        every = [(slug[eid], v) for eid, vs in f.values.items() for v in vs]
        out = []
        ttm = [f"{slug[eid]} {e}" for eid, vs in f.values.items() for e in mx.ttm_sum_errors(vs)]
        out.append(Check("ttm_sums", not ttm, ("a TTM value isn't the sum of its quarters: " + _names(ttm)) if ttm
                         else "every TTM sum equals its four quarters"))
        ebit = [f"{slug[eid]} {e}" for eid, vs in f.values.items() for e in mx.ebit_input_errors(vs)]
        out.append(Check("ebit_inputs", not ebit, ("derived EBIT without all its inputs: " + _names(ebit)) if ebit
                         else "every derived EBIT has all its inputs"))
        unknown = sorted({v.key for _, v in every} - f.catalogue)
        out.append(Check("keys", not unknown, ("keys not in equity.metric: " + ", ".join(unknown)) if unknown
                         else "every key is in the catalogue"))
        out.append(self._roa(every, f))
        wide = [f"{s} {v.basis} {v.period_kind} {v.period_end.isoformat()} ({v.value:.1%})" for s, v in every
                if v.key == "ebit_margin" and v.value is not None and abs(v.value) > 1]
        out.append(Check("ebit_margin", not wide, ("EBIT margin beyond ±100%: " + _names(wide)) if wide
                         else "no EBIT margin beyond ±100%", blocking=False))
        empty = sorted(slug[eid] for eid, vs in f.values.items() if not vs)
        out.append(Check("companies", not empty, ("no metric at all: " + _names(empty)) if empty
                         else f"{len(f.values)} companies have metrics or their reasons", blocking=False))
        counts: dict[str, list[int]] = {}
        reasons: dict[str, int] = {}
        for _, v in every:
            c = counts.setdefault(v.key, [0, 0])
            c[0 if v.value is not None else 1] += 1
            if v.held:
                reasons[v.held] = reasons.get(v.held, 0) + 1
        self.result.update(counts={k: {"values": a, "held": b} for k, (a, b) in sorted(counts.items())},
                           held_reasons=dict(sorted(reasons.items(), key=lambda kv: -kv[1])))
        detail = "; ".join(f"{k} {a}/{a + b}" for k, (a, b) in sorted(counts.items()))
        out.append(Check("computed", True, f"values/all by key: {detail or 'none'}; held reasons: "
                         + (_names([f"{n} × {r}" for r, n in self.result["held_reasons"].items()]) or "none"),
                         blocking=False))
        return out

    @staticmethod
    def _roa(every: list[tuple[str, mx.Value]], f: Fetched) -> Check:
        by: dict[str, int] = {"quarterly": 0, "annualised": 0, "held": 0}
        held, latest = [], {}
        for s, v in every:
            if v.key != "roa":
                continue
            scale = (v.inputs.get("scale") or {}).get("class")
            k = scale if v.value is not None and scale in ("quarterly", "annualised") else "held"
            by[k] += 1
            if k == "held":
                held.append(f"{s} {v.period_end.isoformat()} (ratio {(v.inputs.get('scale') or {}).get('ratio', '—')})")
            if s not in latest or v.period_end > latest[s][0]:
                latest[s] = (v.period_end, k)
        latest_held = sorted(s for s, (_, k) in latest.items() if k == "held")
        detail = (f"banks' quarters: {by['quarterly']} quarterly (× 4), {by['annualised']} annualised, {by['held']} "
                  f"held" + (f" ({_names(held)})" if held else "")
                  + (f"; latest quarter held for {', '.join(latest_held)}" if latest_held else ""))
        return Check("roa_scale", not held, detail, blocking=False)

