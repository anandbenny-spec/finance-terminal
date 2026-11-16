"""us.valuation_inputs: the US slice's market inputs that India's inputs task doesn't already store (Spec 02A.1 §5.4;
job US-04, #403).

Weekly (a monthly and twice-yearly source, so most runs find nothing new). Each response is stored as a raw file before
the load and fetched whole (`conditional=False`), so the task never reads the raw bucket back (Plan 05 §0.5 N10):
- `equity.industry_benchmark`, `market = 'US'`: every row of Damodaran's current `betas.xls` (US firms; edition = its
  "Date updated"), the industries the US peer groups name (`py/config/equity/us_peer_groups.yaml`);
- `imf.NGDP_RPCH.USA` and `imf.PCPIPCH.USA` as `core.forecast` rows, one per calendar year, `vintage_date` = the WEO
  edition (India's edition rule, `ft.equity.inputs.choose_vintage`), `method = 'api'`. The two series rows are created
  by the first run, unpublished, as `equity.guidance_scores` creates its series (no migration: the card's "no
  migration", Spec 02A.1 §9.4). A US task never publishes anything (Spec 02A.1 §3).

The other US rates are stored already and only read: the 10-year Treasury yield (`fred.DGS10`, module 1's
`policy.market_series`, daily) and Damodaran's implied mature-market ERP (`damodaran.ERP_IMPLIED`, India's
`equity.valuation_inputs`, monthly). How they are used: `ft/equity/us_inputs.py`.

Terms: as India's task (Damodaran's acknowledgement is the source line; the IMF's non-commercial use with a source line,
two single-country calls a week).

Checks, B blocking, W warning:
- (B) `beta_industries`: more than 50 industries in `betas.xls`; (B once `us.universe` has loaded the groups, else W)
  `beta_peer_groups`: every US peer group's Damodaran industry is among them;
- (B) `imf_shape`: both indicators have US values for consecutive years, through the stored vintage's year + 3, within
  bounds; (B) `imf_vintage`: the edition rule found an edition for changed values;
- (W) `rates_stored`: the latest `fred.DGS10` and `damodaran.ERP_IMPLIED` observations, and how old they are;
- (W) `imf_fresh`: no WEO vintage in the last 7 months; (W) `revisions`: a `betas.xls` edition re-published with other
  numbers (stored rows kept).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ft.equity import inputs as inp
from ft.equity import us_inputs as ui
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_valuation_inputs import (
    ERP_STALE,
    IMF_STALE,
    IMF_YEARS_AHEAD,
    LISTED,
    MIN_INDUSTRIES,
    _same,
)

MARKET = "US"


@dataclass
class Fetched:
    now: datetime
    beta_edition: date
    beta_rows: list[inp.IndustryRow]
    beta_raw_id: int
    imf: dict[str, dict[int, Decimal]]                              # indicator -> {year: value}
    editions: list[date]


@register
class UsValuationInputs(Task):
    key = "us.valuation_inputs"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now: datetime | None = None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.industry_benchmark")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher

        def get(url: str, source: str, media_type: str, ext: str | None = None) -> tuple[int | None, bytes]:
            got = f.get(url, source=source, media_type=media_type, ext=ext, content_date=ctx.now.date(),
                        conditional=False)
            if got.body is None:                                      # never read the bucket back (Plan 05 N10)
                raise RuntimeError(f"{got.url}: no body came back")
            return got.raw_file_id, got.body

        beta_raw, body = get(ui.BETAS_URL, "damodaran", inp.XLS_TYPE, "xls")
        beta_edition, beta_rows = inp.parse_beta_india(body)          # the same sheet as betaIndia's (US firms)
        imf = {}
        for code in ui.INDICATORS:
            _, body = get(inp.IMF_URL.format(indicator=code, country=ui.IMF_COUNTRY), "imf", "application/json")
            imf[code] = inp.parse_datamapper(body, code, ui.IMF_COUNTRY)
        return Fetched(ctx.now, beta_edition, beta_rows, beta_raw, imf, inp.load_weo_editions())

    # ------------------------------------------------------------------ load (one transaction)
    @staticmethod
    def series_ids(tx) -> tuple[dict[str, int], list[str]]:
        """The two IMF series' ids, created unpublished where missing (returns the ids and the keys created)."""
        keys = [k for k, _, _ in ui.INDICATORS.values()]
        ids = dict(tx.execute("select key, id from core.series where key = any(%s)", (keys,)).fetchall())
        created = []
        for code, (key, name, _) in ui.INDICATORS.items():
            if key in ids:
                continue
            ids[key] = tx.execute(
                """insert into core.series (key, name, kind, entity_id, source_id, source_code, attribution, unit,
                                            frequency, date_rule, market, currency, is_published)
                   select %s, %s, 'forecast', null, s.id, %s, s.attribution, 'percent', 'A', 'as_published', 'US',
                          null, false
                     from core.source s where s.key = 'imf' returning id""", (key, name, code)).fetchone()[0]
            created.append(key)
        return ids, created

    def load(self, tx, f: Fetched, task_run_id) -> int:
        ids, created = self.series_ids(tx)
        written = 0
        have = {r[0]: r[1:] for r in tx.execute(
            """select industry, n_firms, beta, de_ratio, effective_tax, unlevered_beta, cash_firm_value,
                      unlevered_beta_cash_corrected from equity.industry_benchmark where edition = %s and market = %s""",
            (f.beta_edition, MARKET)).fetchall()}
        changed_rows, new_rows = [], []
        for r in f.beta_rows:
            vals = (r.n_firms, r.beta, r.de_ratio, r.effective_tax, r.unlevered_beta, r.cash_firm_value,
                    r.unlevered_beta_cash_corrected)
            if r.industry not in have:
                new_rows.append((f.beta_edition, MARKET, r.industry, *vals, f.beta_raw_id))
            elif not _same(have[r.industry], vals):
                changed_rows.append(r.industry)
        if new_rows:
            with tx.cursor() as cur:
                cur.executemany("""insert into equity.industry_benchmark (edition, market, industry, n_firms, beta,
                                       de_ratio, effective_tax, unlevered_beta, cash_firm_value,
                                       unlevered_beta_cash_corrected, raw_file_id)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", new_rows)
        written += len(new_rows)

        today = f.now.date()
        notes, errors, vintages = {}, [], {}
        for code, (key, _, _) in ui.INDICATORS.items():
            fetched = f.imf[code]
            stored_latest = tx.execute("select max(vintage_date) from core.forecast where series_id = %s",
                                       (ids[key],)).fetchone()[0]
            stored = {}
            if stored_latest is not None:
                stored = {int(y): v for y, v in tx.execute(
                    """select target_label, value from core.forecast where series_id = %s and vintage_date = %s""",
                    (ids[key], stored_latest)).fetchall()}
            v = inp.choose_vintage(f.editions, today, stored_latest, changed=bool(stored_latest) and stored != fetched)
            notes[key] = v.note
            if v.action == "error":
                errors.append(f"{key}: {v.note}")
            elif v.action == "write":
                rows = [(ids[key], v.vintage, date(y, 1, 1), date(y, 12, 31), str(y), val, "api", task_run_id)
                        for y, val in fetched.items()]
                with tx.cursor() as cur:
                    cur.executemany("""insert into core.forecast (series_id, vintage_date, target_start, target_end,
                                           target_label, value, method, task_run_id)
                                       values (%s, %s, %s, %s, %s, %s, %s, %s)""", rows)
                written += len(rows)
            vintages[key] = v.vintage or stored_latest
        self.result = {"series_created": created, "beta_edition": f.beta_edition.isoformat(),
                       "beta_new": len(new_rows), "beta_changed": changed_rows, "imf_notes": notes,
                       "imf_errors": errors, "vintages": {k: v.isoformat() if v else None for k, v in vintages.items()}}
        self._vintages = vintages
        return written

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        out: list[Check] = []
        today = f.now.date()
        industries = [x for x in f.beta_rows if not x.industry.lower().startswith("total market")]
        out.append(Check("beta_industries", len(industries) > MIN_INDUSTRIES,
                         f"{len(industries)} industries in the {f.beta_edition:%d %b %Y} US edition (more than "
                         f"{MIN_INDUSTRIES} expected)"))
        names = {x.industry for x in f.beta_rows}
        groups = tx.execute("select key, damodaran_industry from equity.peer_group where market = %s",
                            (MARKET,)).fetchall()
        missing = [f"{k}: {ind!r}" for k, ind in groups if ind is not None and ind not in names]
        out.append(Check("beta_peer_groups", not missing, (
            "US peer groups whose Damodaran industry isn't in betas.xls: " + "; ".join(missing)) if missing else (
            f"{len(groups)} US peer groups, every industry found" if groups else
            "no US peer groups loaded yet (us.universe hasn't run): not checked"), blocking=bool(groups)))

        shape = []
        for code, (key, _, (blo, bhi)) in ui.INDICATORS.items():
            vals = f.imf[code]
            years = sorted(vals)
            vintage = self._vintages.get(key)
            if len(years) < 2 or years != list(range(years[0], years[-1] + 1)):
                shape.append(f"{key}: years aren't consecutive")
                continue
            if vintage is not None and years[-1] < vintage.year + IMF_YEARS_AHEAD:
                shape.append(f"{key}: runs to {years[-1]}, not {vintage.year + IMF_YEARS_AHEAD}")
            off = [f"{y} {v}" for y, v in vals.items() if not blo <= v <= bhi]
            if off:
                shape.append(f"{key}: outside {blo}…{bhi}: " + ", ".join(off[:LISTED]))
        out.append(Check("imf_shape", not shape, "; ".join(shape) or "; ".join(
            f"{k}: {min(f.imf[c])}–{max(f.imf[c])}" for c, (k, _, _) in ui.INDICATORS.items())))
        out.append(Check("imf_vintage", not r["imf_errors"], "; ".join(r["imf_errors"]) or "; ".join(
            f"{k}: {n}" for k, n in r["imf_notes"].items())))

        rates, stale = [], []
        for key, limit in ((ui.RF_SERIES, ui.RF_STALE), (ui.ERP_SERIES, ERP_STALE)):
            latest = tx.execute("""select max(o.obs_date) from core.observation o join core.series s on s.id = o.series_id
                                    where s.key = %s""", (key,)).fetchone()[0]
            rates.append(f"{key} {latest.isoformat() if latest else 'none stored'}")
            if latest is None or today - latest > limit:
                stale.append(key)
        out.append(Check("rates_stored", not stale, "latest: " + "; ".join(rates)
                         + (f" (older than its limit or missing: {', '.join(stale)}; the valuation holds what needs it)"
                            if stale else ""), blocking=False))
        old = [k for k, v in self._vintages.items() if v is None or today - v > IMF_STALE]
        out.append(Check("imf_fresh", not old, ("no WEO vintage in the last 7 months: " + ", ".join(old)) if old else
                         "newest WEO vintage " + ", ".join(f"{k} {v:%d %b %Y}" for k, v in self._vintages.items()),
                         blocking=False))
        out.append(Check("revisions", not r["beta_changed"],
                         (f"betas.xls {f.beta_edition:%d %b %Y} re-published with other numbers for "
                          f"{len(r['beta_changed'])} industries (stored rows kept): "
                          + ", ".join(r["beta_changed"][:LISTED])) if r["beta_changed"] else "nothing revised",
                         blocking=False))
        return out
