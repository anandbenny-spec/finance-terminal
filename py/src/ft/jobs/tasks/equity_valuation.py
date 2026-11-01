"""equity.valuation: every universe company's valuation runs and scenario grid (Spec 02A §5.7, §5.9; job 2A-15).

Database only: it fetches nothing. For each company in `equity.company`, at the newest price date stored (the company's
own market-capitalisation date at or before it):
- `operating`: a `reverse_dcf` run (solved, `no_solution` with its sentence, or `not_applicable`), a `dcf_default` run, and the
  scenario grid of the `dcf_default` run;
- `lender`: an `implied_roe` run (P/B-implied return on equity against the delivered average);
- `insurer` and `mixed`: a `reverse_dcf` run with status `not_applicable` and §5.9's sentence, so every company has a lead
  run that says what it is (decided by Claude, standing delegation, 24 Oct 2026, on the 2A-15 card: a missing row would read
  as "not computed"; the alternative is to write only the kinds that apply).

Rows are unique on (company, kind, price date, model version): a re-run on the same date updates a row in place only when
a value changed, and the same inputs change nothing. The grid of the latest `dcf_default` run replaces the company's
previous grid (`job_writer` may delete `valuation_scenario`). Model outputs are stored as computed; the `api` views hide
them until their field policies are flipped (2A-17, 2A-26).

Checks, B blocking, W warning:
- (B) `round_trip`: for every solved operating company the value at the implied growth, solved again, gives the growth back
  within 0.01 points, and the price lies between the values at the growth ± 0.01 points (Spec 02A §9.5 item 9);
- (B) `lead_runs`: every universe company has its lead run, with a reason whenever it isn't solved;
- (B) `quarter_rule`: more than a quarter of the operating companies `no_solution` fails the run (Spec 02A §9.4: the model
  or its inputs are wrong; loosening it is Anand's call);
- (W) `outcomes`: the outcomes by kind, with every no-solution and not-applicable reason counted;
- (W) `banned_words`: no stored sentence holds one of Brief §11's banned words (whole words, the site's own text).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from psycopg.types.json import Jsonb

from ft.equity import assemble
from ft.equity.guard import equity_due
from ft.equity.valuation import (
    Cell,
    Company,
    Input,
    Lender,
    Market,
    Result,
    Run,
    Settings,
    implied_roe,
    per_share,
    prepare,
    reverse_dcf,
    scenario_grid,
)
from ft.equity.valuation import default_run as dcf_default_run
from ft.equity.valuation.model import MODEL_VERSION
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 10
POINTS = Decimal("0.0001")                      # 0.01 points, as a fraction
QUARTER = 4                                     # more than one in QUARTER operating companies without a solution fails
BANNED = re.compile(r"\b(overvalued|undervalued|cheap|expensive|upside|fair value|target|buy|sell)\b", re.IGNORECASE)


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Stored:
    """One run row to store: `kind`, status, reason, price, result, delivered(+reason), inputs and the model version."""
    kind: str
    status: str
    reason: str | None
    price: Decimal | None
    result: Decimal | None
    delivered: Decimal | None
    delivered_reason: str | None
    inputs: dict
    model_version: str

    def key(self) -> tuple:
        return (self.kind, self.model_version)

    def comparable(self) -> tuple:
        return (self.status, self.reason, self.price, self.result, self.delivered, self.delivered_reason,
                _plain(self.inputs))


@dataclass
class CompanyRuns:
    eid: int
    slug: str
    family: str
    as_of: date
    runs: list[Stored] = field(default_factory=list)
    grid: tuple[Cell, ...] = ()
    company: Company | None = None             # for the round-trip check
    lender: Lender | None = None


@dataclass
class Fetched:
    as_of: date | None = None
    market: Market | None = None
    companies: list[CompanyRuns] = field(default_factory=list)


def _plain(inputs: dict) -> dict:
    return json.loads(json.dumps(inputs, ensure_ascii=False, default=str))


def from_run(r: Run) -> Stored:
    return Stored(r.kind, r.status, r.reason, r.price, r.result, r.delivered, r.delivered_reason, r.inputs,
                  r.model_version)


def from_result(r: Result, price: Decimal | None) -> Stored:
    return Stored("dcf_default", r.status, r.reason, price, r.value_per_share, None, None, r.inputs, r.model_version)


def compute(s: assemble.Stored, as_of: date, market: Market, settings: Settings,
            rule: assemble.DeliveredRoe) -> CompanyRuns:
    """One company's runs and grid at the newest price date on or before `as_of` (pure, no database)."""
    t = assemble.price_date(s, as_of) or as_of
    out = CompanyRuns(s.company.entity_id, s.company.slug, s.company.family, t)
    if s.company.family == "lender":
        lender = assemble.lender_inputs(s, as_of, rule)
        out.lender = lender
        run = implied_roe(lender, market)
        run.inputs["delivered_years"] = lender.delivered_roe.detail.get("n") if lender.delivered_roe.value is not None \
            else None
        out.runs.append(from_run(run))
        return out
    c = assemble.company_inputs(s, as_of)
    out.company = c
    price, delivered = assemble.price(s, as_of), assemble.delivered_growth(s, as_of)
    rev = reverse_dcf(c, market, settings, price, delivered)
    rev.inputs["delivered_years"] = delivered.detail.get("years") if delivered.value is not None else None
    out.runs.append(from_run(rev))
    if c.family == "operating":
        out.runs.append(from_result(dcf_default_run(c, market, settings), price.value))
        out.grid = scenario_grid(c, market, settings).cells
    return out


@register
class EquityValuation(Task):
    key = "equity.valuation"
    requires = "equity.valuation_run"
    slug_prefix: str | None = None              # tests: only the companies whose slug starts with it

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (reads only)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        out = Fetched()
        ids = None
        if self.slug_prefix:
            ids = [i for (i,) in db.execute(
                """select c.entity_id from equity.company c join core.entity e on e.id = c.entity_id
                    where e.slug like %s""", (self.slug_prefix + "%",)).fetchall()]
        newest = db.execute("""select max(period_end) from equity.metric_value
                                where metric_key = 'market_cap' and period_kind = 'market'
                                  and (%s::int[] is null or entity_id = any(%s))""", (ids, ids)).fetchone()[0]
        if newest is None:
            return out                                           # no price stored yet: nothing to value
        out.as_of = newest
        out.market = assemble.market_inputs(db, newest)
        settings, rule = assemble.load_settings(), assemble.load_delivered_roe()
        for s in assemble.fetch(db, ids).values():
            out.companies.append(compute(s, newest, out.market, settings, rule))
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        inserted = updated = scen_new = scen_gone = 0
        if f.as_of is None:
            self.result = {"companies": 0}
            return 0
        ids = [c.eid for c in f.companies]
        have = {(r[0], r[1], r[2], r[3]): (r[4], r[5:]) for r in tx.execute(
            """select entity_id, kind, as_of, model_version, id, status, reason, price, result, delivered,
                      delivered_reason, inputs from equity.valuation_run where entity_id = any(%s)""",
            (ids,)).fetchall()}
        run_ids: dict[tuple, int] = {}
        for co in f.companies:
            for r in co.runs:
                k = (co.eid, r.kind, co.as_of, r.model_version)
                old = have.get(k)
                inputs = _plain(r.inputs)
                if old is None:
                    run_ids[k] = tx.execute(
                        """insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result,
                               delivered, delivered_reason, inputs, model_version)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                        (co.eid, r.kind, co.as_of, r.status, r.reason, r.price, r.result, r.delivered,
                         r.delivered_reason, Jsonb(inputs), r.model_version)).fetchone()[0]
                    inserted += 1
                    continue
                run_ids[k] = old[0]
                stored = (*old[1][:6], _plain(old[1][6] or {}))
                if stored != r.comparable():
                    tx.execute("""update equity.valuation_run set status = %s, reason = %s, price = %s, result = %s,
                                      delivered = %s, delivered_reason = %s, inputs = %s where id = %s""",
                               (r.status, r.reason, r.price, r.result, r.delivered, r.delivered_reason,
                                Jsonb(inputs), old[0]))
                    updated += 1
        # the latest dcf_default run's grid replaces the company's previous grid
        for co in f.companies:
            if co.family != "operating":
                continue
            run_id = run_ids[(co.eid, "dcf_default", co.as_of, MODEL_VERSION)]
            new = {(run_id, c.growth, c.ebit_margin, c.wacc): c.value_per_share for c in co.grid}
            existing = {(r[0], r[1], r[2], r[3]): r[4] for r in tx.execute(
                """select s.run_id, s.growth, s.ebit_margin, s.wacc, s.value_per_share from equity.valuation_scenario s
                     join equity.valuation_run r on r.id = s.run_id
                    where r.entity_id = %s and r.kind = 'dcf_default'""", (co.eid,)).fetchall()}
            if existing == new:
                continue
            scen_gone += tx.execute("""delete from equity.valuation_scenario s using equity.valuation_run r
                                        where r.id = s.run_id and r.entity_id = %s and r.kind = 'dcf_default'""",
                                    (co.eid,)).rowcount
            if new:
                with tx.cursor() as cur:
                    cur.executemany("""insert into equity.valuation_scenario (run_id, growth, ebit_margin, wacc,
                                           value_per_share) values (%s, %s, %s, %s, %s)""",
                                    [(*k, v) for k, v in new.items()])
            scen_new += len(new)
        self.result = {"as_of": f.as_of.isoformat(), "companies": len(f.companies), "runs_inserted": inserted,
                       "runs_updated": updated, "scenarios_written": scen_new, "scenarios_removed": scen_gone}
        return inserted + updated + scen_new + scen_gone

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        if f.as_of is None:
            return [Check("lead_runs", True, "no price is stored yet, so nothing was valued", blocking=False)]
        runs = [(co, r) for co in f.companies for r in co.runs]
        lead = {co.eid: [r for r in co.runs if r.kind in ("reverse_dcf", "implied_roe")] for co in f.companies}
        bad = [co.slug for co in f.companies if not lead[co.eid]]
        bad += [f"{co.slug} ({r.kind} has no reason)" for co, r in runs if r.status != "solved" and not r.reason]
        out = [Check("lead_runs", not bad, ("companies without their lead run: " + _names(bad)) if bad
                     else f"all {len(f.companies)} companies have their lead run, with a reason where it isn't solved")]

        reverse = {co.eid: r for co in f.companies if co.family == "operating"
                   for r in co.runs if r.kind == "reverse_dcf"}
        operating = [co for co in f.companies if co.eid in reverse]       # one lacking its run is lead_runs' failure
        failed = [co.slug for co in operating if reverse[co.eid].status == "no_solution"]
        out.append(Check("quarter_rule", len(failed) * QUARTER <= len(operating),
                         f"{len(failed)} of {len(operating)} operating companies have no solution"
                         + (": " + _names(failed) if failed else "")
                         + ("" if len(failed) * QUARTER <= len(operating) else
                            " (more than a quarter: the model or its inputs are wrong; Spec 02A §9.4)")))

        trips, solved, settings = [], 0, assemble.load_settings()
        for co in operating:
            r = reverse[co.eid]
            if r.status != "solved":
                continue
            solved += 1
            why = round_trip(co, f.market, settings, r)
            if why:
                trips.append(f"{co.slug} ({why})")
        out.append(Check("round_trip", not trips, ("round trip fails: " + _names(trips)) if trips
                         else f"{solved} solved companies: the value at the implied growth solves back to it within "
                              "0.01 points"))

        counts = Counter((r.kind, r.status, (r.inputs or {}).get("outcome")) for _, r in runs)
        by_reason = Counter((r.kind, r.reason) for _, r in runs if r.status != "solved" and r.reason)
        out.append(Check("outcomes", True, "; ".join(f"{k}/{s}/{o}: {n}" for (k, s, o), n in sorted(
            counts.items(), key=lambda x: tuple(map(str, x[0])))), blocking=False))
        found = sorted({f"{co.slug} {r.kind}: {', '.join(w)}" for co, r in runs for text in
                        [r.reason or ""] if (w := [m.group(1).lower() for m in BANNED.finditer(text)])})
        out.append(Check("banned_words", not found, ("banned words in stored reasons: " + _names(found)) if found
                         else "no stored reason holds a banned word", blocking=False))
        self.result.update(solved=solved, operating=len(operating), no_solution=len(failed),
                           reasons={f"{k}: {why}": n for (k, why), n in by_reason.items()})
        return out


def round_trip(co: CompanyRuns, market: Market, settings: Settings, run: Stored) -> str | None:
    """None when the run's implied growth survives the Spec 02A §9.5 item 9 check, else why not."""
    c, g = co.company, run.result
    d, _, _ = prepare(c, market, settings)
    value = per_share(d, g)
    again = reverse_dcf(c, market, settings, Input.of(value, "money", None, "round trip"),
                        Input.of(Decimal(0), "fraction", None, "round trip"))
    if again.status != "solved" or abs(again.result - g) > POINTS:
        return f"solved back to {again.result}, not {g}" if again.result is not None else "no solution on the way back"
    low, high = per_share(d, g - POINTS), per_share(d, g + POINTS)
    price = run.price
    if not min(low, high) <= price <= max(low, high):
        return f"price {price} lies outside the values at g ± 0.01 points ({min(low, high)} to {max(low, high)})"
    return None
