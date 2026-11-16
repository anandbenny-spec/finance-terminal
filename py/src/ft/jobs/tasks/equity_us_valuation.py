"""us.valuation: the US companies' valuation runs and scenario grids (Spec 02A.1 §5.4, Spec 02A §5.7 and §5.9; job US-04,
#403).

India's task (`equity.valuation`) with the US inputs (`ft/equity/us_inputs.py`): the 10-year Treasury yield and the
mature-market ERP with no country risk premium, a 25% marginal rate, operating income as filed, and the US betas. For
each `market = 'US'` company at the newest US price date stored (each company's own market-capitalisation date at or
before it): an operating company's `reverse_dcf` and `dcf_default` runs and grid; a lender's `implied_roe` run; an
insurer's or `mixed` company's not-applicable lead run. Every value in USD with its inputs. The load and the checks are
India's (`round_trip`, `lead_runs`, `quarter_rule`, `outcomes`, `banned_words`), over the US companies only. Database
only; India's companies are never read, and India's task never reads these (US-01's filter).
"""
from __future__ import annotations

from datetime import date

from ft.equity import assemble
from ft.equity import us_inputs as ui
from ft.equity.valuation import Market, Settings, implied_roe, reverse_dcf, scenario_grid
from ft.equity.valuation import default_run as dcf_default_run
from ft.jobs.runner import Context
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_valuation import CompanyRuns, EquityValuation, Fetched, from_result, from_run


def compute(s: assemble.Stored, as_of: date, market: Market, settings: Settings,
            rule: assemble.DeliveredRoe) -> CompanyRuns:
    """One US company's runs and grid at the newest price date on or before `as_of` (pure, no database): India's
    `equity_valuation.compute` with the US lines."""
    t = assemble.price_date(s, as_of) or as_of
    out = CompanyRuns(s.company.entity_id, s.company.slug, s.company.family, t)
    if s.company.family == "lender":
        lender = ui.lender_inputs(s, as_of, rule)
        out.lender = lender
        run = implied_roe(lender, market)
        run.inputs["delivered_years"] = lender.delivered_roe.detail.get("n") if lender.delivered_roe.value is not None \
            else None
        out.runs.append(from_run(run))
        return out
    c = ui.company_inputs(s, as_of)
    out.company = c
    price, delivered = ui.price(s, as_of), assemble.delivered_growth(s, as_of)
    rev = reverse_dcf(c, market, settings, price, delivered)
    rev.inputs["delivered_years"] = delivered.detail.get("years") if delivered.value is not None else None
    out.runs.append(from_run(rev))
    if c.family == "operating":
        out.runs.append(from_result(dcf_default_run(c, market, settings), price.value))
        out.grid = scenario_grid(c, market, settings).cells
    return out


@register
class UsValuation(EquityValuation):
    key = "us.valuation"

    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        out = Fetched()
        like = (self.slug_prefix or "") + "%"
        ids = [i for (i,) in db.execute(
            """select c.entity_id from equity.company c join core.entity e on e.id = c.entity_id
                where e.market = 'US' and e.slug like %s order by e.slug""", (like,)).fetchall()]
        if not ids:
            return out
        newest = db.execute("""select max(period_end) from equity.metric_value
                                where metric_key = 'market_cap' and period_kind = 'market' and entity_id = any(%s)""",
                            (ids,)).fetchone()[0]
        if newest is None:
            return out                                           # no price stored yet: nothing to value
        out.as_of = newest
        out.market = ui.market_inputs(db, newest)
        settings, rule = assemble.load_settings(), assemble.load_delivered_roe()
        for s in ui.fetch(db, ids).values():
            out.companies.append(compute(s, newest, out.market, settings, rule))
        return out
