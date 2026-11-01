"""equity.call_transcripts, part 1: discovery of earnings-call transcript filings and call intimations over the last two
seasons (Spec 02B §5.1, §13.3; job 2B-06). Part 2 (2B-07) adds the fetch half: it takes `self.announcements` (the
classified transcripts and intimations below), picks each company's latest call and registers the documents. This
part registers nothing: every reply is stored as a raw file (`ft/storage.py`, through the fetcher), and the
announcements live in those stored replies, so there is no new table.

Per company the task asks NSE for `/api/corporate-announcements` over the window (backfill: 1 Jul 2026 to the run's
date; weekly: the latest stored dissemination time minus 14 days to today), under the current symbol and any symbol the
company had in the window. BSE is the fallback (D10) when NSE fails for a company, or answers with no rows for a window
in which the company's results are filed. `ft/equity/calls/discovery.py` holds the logic (window splitting, the stop
after 10 companies in a row, the classification, seasons); this class supplies the network and the database.

Checks, B blocking, W warning:
- (B) `nse_answered`: NSE (or the BSE fallback) answered for all but two of the 50 companies, and the run wasn't stopped
  by ten failures in a row;
- (W) `refusals`: every 401, 403 and 429, counted;
- (W) `transcripts_found`: each company-season whose results are filed and at least 45 days old but has no transcript
  filing, named (2B-07 opens first pages for these);
- (W) `unmatched_isin`: transcript rows whose ISIN matches no universe company, counted;
- (W) `discovery_summary`: transcripts and intimations per season (always passes).
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta

from ft.engines.document.job import DocumentJob, Harvest
from ft.equity import filings as fl
from ft.equity.calls import discovery as dc
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context
from ft.jobs.tasks import register

SHOWN = 12
NO_TRANSCRIPT_AFTER = timedelta(days=45)         # §5.1: results filed and at least 45 days old
NSE_HEADERS = {"Referer": dc.NSE_REFERER, "Accept": "application/json, text/plain, */*"}
BSE_HEADERS = {"Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/",
               "Accept": "application/json, text/plain, */*"}
ALLOWED_FAILURES = 2                             # of the 50 (§5.1: at least 48 answered)


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@register
class EquityCallTranscripts(DocumentJob):
    key = "equity.call_transcripts"
    source = "nse"
    doc_type = "call_transcript"

    def __init__(self):
        super().__init__()
        self.announcements: list[dc.Announcement] = []       # for 2B-07's fetch step
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ the database
    @staticmethod
    def companies(db, start: date) -> list[dc.Company]:
        rows = db.execute(
            """select c.entity_id, e.slug, c.nse_symbol, c.isin, c.bse_code, c.listed_on
                 from equity.company c join core.entity e on e.id = c.entity_id
                where c.nse_symbol is not null order by e.slug""").fetchall()
        ids = [r[0] for r in rows]
        sym: dict[int, set[str]] = {i: set() for i in ids}
        isn: dict[int, set[str]] = {i: set() for i in ids}
        for entity_id, scheme, value, upper in db.execute(
                """select entity_id, scheme, value, upper(valid) from core.entity_identifier
                    where entity_id = any(%s) and scheme in ('nse_symbol','isin')""", (ids,)).fetchall():
            if scheme == "isin":
                isn[entity_id].add(value)
            elif upper is None or upper >= start:          # a symbol held at some time in the window
                sym[entity_id].add(value)
        return [dc.Company(i, slug, s, isin, bse, listed, frozenset(sym[i]), frozenset(isn[i]))
                for i, slug, s, isin, bse, listed in rows]

    @staticmethod
    def results_filed(db, entity_id: int) -> list[datetime]:
        return [r[0] for r in db.execute(
            "select occurs_at from core.event where event_type = 'results_filing' and entity_id = %s "
            "and status <> 'superseded' and occurs_at is not null", (entity_id,)).fetchall()]

    # ------------------------------------------------------------------ the network
    def _warm(self, ctx: Context) -> None:
        try:
            ctx.fetcher.http.get(fl.HOME, record_url=fl.HOME, stats=ctx.fetcher.counts)
        except FetchError:
            pass

    def _get_json(self, ctx: Context, url: str, source: str, params: dict, headers: dict, warm: bool) -> dc.Answer:
        """One request, its reply stored as a raw file before it is read. A 401/403 from NSE is retried once after the
        homepage is visited again for its cookies; any other failure is the answer."""
        f = ctx.fetcher
        for attempt in (1, 2):
            try:
                got = f.get(url, source=source, params=params, headers=headers, conditional=False,
                            media_type="application/json", ext="json")
                body = got.body if got.body is not None else f.read(got.raw_file_id)
                return dc.Answer(200, json.loads(body))
            except FetchError as e:
                if warm and attempt == 1 and e.status in (401, 403):
                    self._warm(ctx)
                    continue
                return dc.Answer(e.status if e.status is not None else "network error")
            except ValueError:
                return dc.Answer("not JSON")
        return dc.Answer("failed")                                       # pragma: no cover

    def _nse(self, ctx: Context):
        def fetch(symbol: str, start: date, end: date) -> dc.Answer:
            return self._get_json(ctx, fl.API + dc.NSE_ANN_PATH, "nse",
                                  {"index": "equities", "symbol": symbol, "from_date": start.strftime("%d-%m-%Y"),
                                   "to_date": end.strftime("%d-%m-%Y")}, NSE_HEADERS, warm=True)
        return fetch

    def _bse(self, ctx: Context):
        def fetch(scrip: str, start: date, end: date, subcategory: str, page: int) -> dc.Answer:
            return self._get_json(ctx, dc.BSE_ANN_URL, "bse",
                                  {"pageno": str(page), "strCat": dc.BSE_CATEGORY, "strPrevDate": start.strftime("%Y%m%d"),
                                   "strScrip": scrip, "strSearch": "P", "strToDate": end.strftime("%Y%m%d"),
                                   "strType": "C", "subcategory": subcategory}, BSE_HEADERS, warm=False)
        return fetch

    # ------------------------------------------------------------------ discovery
    def discover(self, ctx: Context):
        db = ctx.fetcher.db
        today = ctx.now.astimezone(dc.INDIA).date()
        latest = db.execute("select max(published_at) from core.document where doc_type = %s", (self.doc_type,)).fetchone()[0]
        start = dc.window_start(ctx.since, latest)
        end = min(ctx.until or today, today)
        companies = self.companies(db, start)
        index = dc.CompanyIndex(companies)
        notes: dict = {"window": (start, end), "today": today, "results": [], "stopped": None, "unreadable": 0,
                       "companies": companies}
        self._warm(ctx)
        nse, bse, guard = self._nse(ctx), self._bse(ctx), dc.RunGuard()
        found: list[dc.Announcement] = []
        for co in companies:
            res = dc.CompanyResult(co)
            rows: list[dict] = []
            symbols = [co.symbol] + sorted(co.symbols - {co.symbol})
            ok = True
            for sym in symbols:
                got, logs, sym_ok, refused = dc.nse_symbol(nse, sym, start, end)
                rows += got
                res.windows += logs
                res.refused += refused
                ok &= sym_ok
                if not sym_ok:
                    res.errors.append(f"NSE {sym}: " + ", ".join(sorted({str(x.status) for x in logs if not x.answered})))
            res.nse_answered, res.nse_rows = ok, len(rows)
            for r in rows:
                a = dc.norm_nse(r)
                if a is None:
                    notes["unreadable"] += 1
                    continue
                res.announcements.append(self._join(a, index))
            filed = self.results_filed(db, co.entity_id)
            if co.bse_code and (not ok or (not rows and dc.results_filed_in((start, end), filed))):
                res.bse_used = True
                brows, bok, problems = dc.bse_company(bse, co.bse_code, start, end)
                res.bse_answered = bok
                res.errors += [f"BSE {p}" for p in problems]
                for r in brows:
                    a = dc.norm_bse(r)
                    if a is None:
                        notes["unreadable"] += 1
                    else:
                        res.announcements.append(self._join(a, index, co))
            res.announcements = dc.dedupe(res.announcements)
            found += res.announcements
            notes["results"].append(res)
            try:
                guard.record(res.nse_answered)
            except dc.NseStopped as e:
                notes["stopped"] = str(e)
                break
        self.announcements = sorted(dc.dedupe(found), key=lambda a: (a.entity_id or 0, a.disseminated, a.filing_key))
        return [], notes                                  # nothing to fetch here: 2B-07 fetches the transcripts

    @staticmethod
    def _join(a: dc.Announcement, index: dc.CompanyIndex, asked: dc.Company | None = None) -> dc.Announcement:
        eid = asked.entity_id if (asked and a.exchange == "BSE") else index.resolve(a)
        return replace(a, entity_id=eid)

    # ------------------------------------------------------------------ checks
    def document_checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        n = h.notes
        start, end = n["window"]
        results: list[dc.CompanyResult] = n["results"]
        total = len(n["companies"])
        answered = sum(1 for r in results if r.answered)
        need = total - ALLOWED_FAILURES if total >= 50 else total
        failed = [r.company.slug for r in results if not r.answered]
        by_bse = [r.company.slug for r in results if r.bse_used and not r.nse_answered and r.bse_answered]
        detail = f"answered for {answered} of {total} companies ({start} to {end})"
        if by_bse:
            detail += f"; the BSE fallback served {_names(by_bse)}"
        if failed:
            detail += f"; no answer for {_names(failed)}"
        if n["stopped"]:
            detail += f"; the run stopped: {n['stopped']}"
        out = [Check("nse_answered", answered >= need and not n["stopped"] and total > 0, detail)]
        refused = sum(r.refused for r in results)
        out.append(Check("refusals", refused == 0, f"{refused} windows refused with 401, 403 or 429", blocking=False))

        ann = self.announcements
        by_cs = {(a.entity_id, a.season) for a in ann if a.is_transcript and a.entity_id is not None}
        missing = []
        for r in results:
            seen: set[str] = set()
            for filed in self.results_filed(tx, r.company.entity_id):
                d = filed.astimezone(dc.INDIA).date()
                season = dc.season_of_call(d)
                if season in seen or not (start <= d <= end) or d + NO_TRANSCRIPT_AFTER > n["today"]:
                    continue
                seen.add(season)
                if (r.company.entity_id, season) not in by_cs:
                    missing.append(f"{r.company.slug} {season}")
        out.append(Check("transcripts_found", not missing,
                         ("results filed 45 or more days ago, no transcript filing found for: " + _names(sorted(missing)))
                         if missing else "every company-season with results filed 45 or more days ago has a transcript filing",
                         blocking=False))
        unmatched = sum(1 for a in ann if a.is_transcript and a.entity_id is None)
        out.append(Check("unmatched_isin", unmatched == 0,
                         f"{unmatched} transcript rows whose ISIN matches no universe company", blocking=False))
        seasons: dict[str, list[int]] = {}
        for a in ann:
            if a.kind in ("transcript", "intimation"):
                seasons.setdefault(a.season, [0, 0])[0 if a.is_transcript else 1] += 1
        out.append(Check("discovery_summary", True, "; ".join(
            f"{s}: {t} transcripts, {i} intimations" for s, (t, i) in sorted(seasons.items())) or "no transcripts found",
            blocking=False))
        self.result = {"answered": answered, "transcripts": sum(v[0] for v in seasons.values()),
                       "intimations": sum(v[1] for v in seasons.values()), "unreadable_rows": n["unreadable"]}
        return out
