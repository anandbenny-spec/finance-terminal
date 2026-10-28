"""equity.valuation_inputs: the valuation engine's market inputs (Spec 02A §5.7, §6; job 2A-05).

Weekly (a monthly source, so most runs find nothing new). From Damodaran (pages.stern.nyu.edu) and the IMF DataMapper,
each response stored as a raw file before the load:
- `damodaran.ERP_IMPLIED`: the S&P 500's implied equity risk premium, one observation per month (obs_date = the month's
  first day, percent), every month `ERPbymonth.xlsx` holds from Jan 2019;
- `damodaran.IND_DEFAULT_SPREAD` and `damodaran.IND_CRP`: India's rating-based default spread and country risk premium,
  one observation per `ctryprem` edition (obs_date = the "Date of update" inside the file). The editions come from the
  links on `datacurrent.html` plus the standing `datasets/ctryprem.xlsx`; each is dated by its content, never its name;
- `equity.industry_benchmark`: every row of the current `betaIndia.xls` (edition = its "Date updated"), market IN;
- `imf.NGDP_RPCH.IND` and `imf.PCPIPCH.IND` as `core.forecast` rows, one per calendar year, `vintage_date` = the WEO
  edition (the API's reply doesn't name it, so `config/equity/weo_editions.yaml` supplies the release dates:
  `ft.equity.inputs.choose_vintage`), `method = 'api'`. Years before the vintage's are actuals and are stored the same
  way. A new edition adds rows; a stored vintage is never changed.
Changed observations are real UPDATEs (the revision trigger logs them): Damodaran does correct an edition in place
(July 2026's default spreads were corrected on 9 July). A re-published `betaIndia` edition keeps its stored rows and
raises a warning (the table has no revision log).

Production guard (decided by Claude, standing delegation, 28 Oct 2026): `due()` goes through `ft.equity.guard.equity_due`.

Terms (read 28 Oct 2026): the IMF's copyright and usage page lets users "download, extract, copy, create derivative
works, publish, distribute, and use" statistical data for non-commercial purposes with a source line ("Source:
International Monetary Fund, ..."), says when data are transformed, and asks permission for bulk downloads by automated
means: this task makes two single-country calls a week. Damodaran's page states no terms; the Data Audit 02 E3 record
(free to use, acknowledgement asked) stands, and the source line is the acknowledgement.

Checks, B blocking, W warning:
- (B) `erp_range`: every implied ERP between 2% and 10%; (B) `erp_months`: no month missing between the first and the last;
- (B) `ctryprem_values`: India's default spread and CRP between 0% and 10% in every edition, a rating string present;
- (B) `ctryprem_edition`: every `ctryprem` workbook the page lists parsed, and no dated file name is newer than the newest
  edition parsed (a newer one listed but not read fails);
- (B) `beta_industries`: more than 50 industries in `betaIndia`; (B once 2A-04 has loaded the groups, else W)
  `beta_peer_groups`: every peer group's Damodaran industry is among them;
- (B) `imf_shape`: both indicators have India values for consecutive years, through the stored vintage's year + 3, within
  bounds; (B) `imf_vintage`: the edition rule found an edition for changed values;
- (W) `erp_fresh`: the latest monthly ERP older than 45 days; (W) `imf_fresh`: no WEO vintage in the last 7 months;
  (W) `revisions`: observations or editions revised in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from ft.equity import inputs as inp
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

INDICATORS = {"NGDP_RPCH": ("imf.NGDP_RPCH.IND", (-20, 30)), "PCPIPCH": ("imf.PCPIPCH.IND", (-5, 60))}
SERIES = ("damodaran.ERP_IMPLIED", "damodaran.IND_DEFAULT_SPREAD", "damodaran.IND_CRP", *(k for k, _ in INDICATORS.values()))
ERP_BOUNDS = (Decimal(2), Decimal(10))
PREMIUM_BOUNDS = (Decimal(0), Decimal(10))
MIN_INDUSTRIES = 50
ERP_STALE = timedelta(days=45)
IMF_STALE = timedelta(days=213)               # 7 months
IMF_YEARS_AHEAD = 3                            # the shape check wants values through the vintage's year + 3
LISTED = 10


@dataclass
class Fetched:
    now: datetime
    erp: list[tuple[date, Decimal]]
    ctry: list[tuple[str, inp.CountryPremium]]                      # (url, parsed) for the workbooks that parsed
    ctry_failed: list[str]                                          # "url: reason"
    beta_edition: date
    beta_rows: list[inp.IndustryRow]
    beta_raw_id: int
    imf: dict[str, dict[int, Decimal]]                              # indicator -> {year: value}
    editions: list[date]
    discovery: str = ""


@register
class EquityValuationInputs(Task):
    key = "equity.valuation_inputs"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now: datetime | None = None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.industry_benchmark")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher

        def get(url: str, source: str, media_type: str, ext: str | None = None) -> tuple[int, bytes]:
            got = f.get(url, source=source, media_type=media_type, ext=ext, content_date=ctx.now.date())
            return got.raw_file_id, (got.body if got.body is not None else f.read(got.raw_file_id))

        discovery = ""
        links: list[str] = []
        beta_url = inp.BETA_INDIA_URL
        try:
            _, page = get(inp.DATACURRENT_URL, "damodaran", "text/html")
            html = page.decode("utf-8", "replace")
            links = inp.find_ctryprem_links(html)
            beta_url = inp.find_beta_india_link(html) or beta_url
            discovery = f"datacurrent.html lists {len(links)} country-risk workbooks"
        except FetchError as e:
            discovery = f"datacurrent.html couldn't be read ({e}); the standing file URLs were used"

        _, body = get(inp.ERP_MONTHLY_URL, "damodaran", inp.XLSX_TYPE, "xlsx")
        erp = inp.parse_erp_by_month(body)

        urls = list(dict.fromkeys([*links, inp.CTRYPREM_DEFAULT_URL]))
        ctry, failed = [], []
        for url in urls:
            try:
                _, body = get(url, "damodaran", inp.XLSX_TYPE if url.lower().endswith("x") else inp.XLS_TYPE,
                              "xlsx" if url.lower().endswith("x") else "xls")
                ctry.append((url, inp.parse_ctryprem(body)))
            except (FetchError, inp.InputsError) as e:
                failed.append(f"{url.rsplit('/', 1)[-1]}: {e}")
        if not ctry:
            raise RuntimeError("no country-risk workbook could be read: " + "; ".join(failed))

        beta_raw, body = get(beta_url, "damodaran", inp.XLS_TYPE, "xls")
        beta_edition, beta_rows = inp.parse_beta_india(body)

        imf = {}
        for code in INDICATORS:
            _, body = get(inp.IMF_URL.format(indicator=code, country="IND"), "imf", "application/json")
            imf[code] = inp.parse_datamapper(body, code, "IND")
        return Fetched(ctx.now, erp, ctry, failed, beta_edition, beta_rows, beta_raw, imf, inp.load_weo_editions(),
                       discovery)

    # ------------------------------------------------------------------ load (one transaction)
    def _observations(self, tx, series_id: int, points: list[tuple[date, Decimal]], task_run_id) -> tuple[int, int]:
        """Insert new points and update changed ones (the trigger logs the revision); returns (new, revised)."""
        stored = dict(tx.execute("select obs_date, value from core.observation where series_id = %s", (series_id,)).fetchall())
        new = [(series_id, d, v, task_run_id) for d, v in points if d not in stored]
        changed = [(v, task_run_id, series_id, d) for d, v in points if d in stored and stored[d] != v]
        with tx.cursor() as cur:
            if new:
                cur.executemany("insert into core.observation (series_id, obs_date, value, task_run_id) "
                                "values (%s, %s, %s, %s)", new)
            if changed:
                cur.executemany("update core.observation set value = %s, task_run_id = %s "
                                "where series_id = %s and obs_date = %s", changed)
        return len(new), len(changed)

    def load(self, tx, f: Fetched, task_run_id) -> int:
        ids = dict(tx.execute("select key, id from core.series where key = any(%s)", (list(SERIES),)).fetchall())
        absent = [k for k in SERIES if k not in ids]
        if absent:
            raise RuntimeError("series missing from core.series (migration 0019 not pushed?): " + ", ".join(absent))
        written, revised = 0, {}
        for key, points in (
                ("damodaran.ERP_IMPLIED", f.erp),
                ("damodaran.IND_DEFAULT_SPREAD", sorted({c.edition: c.default_spread for _, c in f.ctry}.items())),
                ("damodaran.IND_CRP", sorted({c.edition: c.crp for _, c in f.ctry}.items()))):
            n, r = self._observations(tx, ids[key], points, task_run_id)
            written += n + r
            if r:
                revised[key] = r

        # industry benchmark: an edition is stored once; a re-published edition with other numbers is a warning
        market = "IN"
        have = {r[0]: r[1:] for r in tx.execute(
            """select industry, n_firms, beta, de_ratio, effective_tax, unlevered_beta, cash_firm_value,
                      unlevered_beta_cash_corrected from equity.industry_benchmark where edition = %s and market = %s""",
            (f.beta_edition, market)).fetchall()}
        changed_rows, new_rows = [], []
        for r in f.beta_rows:
            vals = (r.n_firms, r.beta, r.de_ratio, r.effective_tax, r.unlevered_beta, r.cash_firm_value,
                    r.unlevered_beta_cash_corrected)
            if r.industry not in have:
                new_rows.append((f.beta_edition, market, r.industry, *vals, f.beta_raw_id))
            elif not _same(have[r.industry], vals):
                changed_rows.append(r.industry)
        if new_rows:
            with tx.cursor() as cur:
                cur.executemany("""insert into equity.industry_benchmark (edition, market, industry, n_firms, beta,
                                       de_ratio, effective_tax, unlevered_beta, cash_firm_value,
                                       unlevered_beta_cash_corrected, raw_file_id)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", new_rows)
        written += len(new_rows)

        # IMF forecasts: a vintage is stored once; the edition rule says which vintage a changed reply belongs to
        today = f.now.date()
        notes, errors, vintages = {}, [], {}
        for code, (key, _) in INDICATORS.items():
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
        self.result = {"ids": ids, "revised": revised, "beta_changed": changed_rows, "beta_new": len(new_rows),
                       "imf_notes": notes, "imf_errors": errors, "vintages": vintages, "published": []}
        return written

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        out: list[Check] = []
        today = f.now.date()
        lo, hi = ERP_BOUNDS

        bad = [f"{m:%b %Y} {v}" for m, v in f.erp if not lo <= v <= hi]
        out.append(Check("erp_range", not bad, f"{len(bad)} of {len(f.erp)} months outside {lo}%–{hi}%: "
                         + ", ".join(bad[:LISTED]) if bad else f"{len(f.erp)} months, {f.erp[0][0]:%b %Y} to "
                         f"{f.erp[-1][0]:%b %Y}, all within {lo}%–{hi}%; latest {f.erp[-1][1]}%"))
        gaps = inp.month_gaps([m for m, _ in f.erp])
        out.append(Check("erp_months", not gaps, ("months missing: " + ", ".join(f"{g:%b %Y}" for g in gaps[:LISTED]))
                         if gaps else "no month missing"))

        plo, phi = PREMIUM_BOUNDS
        problems = []
        for url, c in f.ctry:
            where = f"{c.edition:%d %b %Y}"
            if not (plo <= c.default_spread <= phi and plo <= c.crp <= phi):
                problems.append(f"{where}: spread {c.default_spread}%, premium {c.crp}%")
            if not c.rating:
                problems.append(f"{where}: no rating")
        out.append(Check("ctryprem_values", not problems, "; ".join(problems) or "; ".join(
            f"{c.edition:%d %b %Y}: {c.rating}, spread {c.default_spread}%, premium {c.crp}%"
            for _, c in sorted(f.ctry, key=lambda x: x[1].edition))))

        newest = max(c.edition for _, c in f.ctry)
        named = [(edition_name, url) for url, _ in f.ctry if (edition_name := inp.edition_from_name(url))]
        ahead = [url.rsplit("/", 1)[-1] for en, url in named if en > newest.replace(day=1)]
        why = []
        # A workbook that can't be read blocks the run unless its name dates it before the newest edition read: Damodaran's
        # page also links archived editions (ctrypremJune13.xls, a 2013 file the reader can't open), and the engine only
        # ever uses his newest dated edition. An undated or later name still blocks (a newer edition not read must fail).
        old_failed = [x for x in f.ctry_failed
                      if (en := inp.edition_from_name(x.split(":", 1)[0])) and en < newest.replace(day=1)]
        blocking_failed = [x for x in f.ctry_failed if x not in old_failed]
        if blocking_failed:
            why.append("not read: " + "; ".join(blocking_failed))
        if ahead:
            why.append(f"named for a month after the newest edition read ({newest:%d %b %Y}): " + ", ".join(ahead))
        out.append(Check("ctryprem_edition", not why, " | ".join(why) or
                         f"{len(f.ctry)} workbooks read, newest edition {newest:%d %b %Y}; {f.discovery}"
                         + (f"; archived editions not read (older than the newest): {'; '.join(old_failed)}"
                            if old_failed else "")))

        industries = [x for x in f.beta_rows if not x.industry.lower().startswith("total market")]
        out.append(Check("beta_industries", len(industries) > MIN_INDUSTRIES,
                         f"{len(industries)} industries in the {f.beta_edition:%d %b %Y} edition (more than {MIN_INDUSTRIES} "
                         "expected)"))
        names = {x.industry for x in f.beta_rows}
        groups = tx.execute("select key, damodaran_industry from equity.peer_group").fetchall()
        missing = [f"{k}: {ind!r}" for k, ind in groups if ind is not None and ind not in names]
        out.append(Check("beta_peer_groups", not missing, (
            "peer groups whose Damodaran industry isn't in betaIndia: " + "; ".join(missing)) if missing else (
            f"{len(groups)} peer groups, every industry found" if groups else
            "no peer groups loaded yet (equity.universe hasn't run): not checked"), blocking=bool(groups)))

        shape = []
        for code, (key, (blo, bhi)) in INDICATORS.items():
            vals = f.imf[code]
            years = sorted(vals)
            vintage = r["vintages"].get(key)
            if len(years) < 2 or years != list(range(years[0], years[-1] + 1)):
                shape.append(f"{key}: years aren't consecutive")
                continue
            if vintage is not None and years[-1] < vintage.year + IMF_YEARS_AHEAD:
                shape.append(f"{key}: runs to {years[-1]}, not {vintage.year + IMF_YEARS_AHEAD}")
            off = [f"{y} {v}" for y, v in vals.items() if not blo <= v <= bhi]
            if off:
                shape.append(f"{key}: outside {blo}…{bhi}: " + ", ".join(off[:LISTED]))
        out.append(Check("imf_shape", not shape, "; ".join(shape) or "; ".join(
            f"{k}: {min(f.imf[c])}–{max(f.imf[c])}" for c, (k, _) in INDICATORS.items())))
        out.append(Check("imf_vintage", not r["imf_errors"], "; ".join(r["imf_errors"]) or "; ".join(
            f"{k}: {n}" for k, n in r["imf_notes"].items())))

        latest_erp = f.erp[-1][0]
        out.append(Check("erp_fresh", today - latest_erp <= ERP_STALE,
                         f"the latest monthly ERP is {latest_erp:%b %Y} ({(today - latest_erp).days} days old; "
                         f"warns after {ERP_STALE.days})", blocking=False))
        stale = [k for k, v in r["vintages"].items() if v is None or today - v > IMF_STALE]
        out.append(Check("imf_fresh", not stale, ("no WEO vintage in the last 7 months: " + ", ".join(stale)) if stale else
                         "newest WEO vintage " + ", ".join(f"{k} {v:%d %b %Y}" for k, v in r["vintages"].items()),
                         blocking=False))
        notes = []
        if r["revised"]:
            notes.append("observations revised in place: " + ", ".join(f"{k} {n}" for k, n in sorted(r["revised"].items())))
        if r["beta_changed"]:
            notes.append(f"betaIndia {f.beta_edition:%d %b %Y} re-published with other numbers for "
                         f"{len(r['beta_changed'])} industries (stored rows kept): " + ", ".join(r["beta_changed"][:LISTED]))
        out.append(Check("revisions", not notes, "; ".join(notes) or "nothing revised", blocking=False))

        # Publish each series once its first load passed (only if the whole load stands).
        if all(c.passed for c in out if c.blocking):
            tx.execute("update core.series set is_published = true where key = any(%s) and not is_published",
                       (list(SERIES),))
            r["published"] = list(SERIES)
        return out


def _same(stored: tuple, new: tuple) -> bool:
    """Stored numerics (Decimal) against freshly parsed ones, exactly (both came from the same float's repr)."""
    return all((a is None and b is None) or (a is not None and b is not None and Decimal(a) == Decimal(b))
               for a, b in zip(stored, new, strict=True))
