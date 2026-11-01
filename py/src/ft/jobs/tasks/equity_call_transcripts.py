"""equity.call_transcripts: earnings-call transcripts over the last two seasons (Spec 02B §5.1, §5.2, §13.3, §13.4).

Part 1 (2B-06) is discovery: the transcript filings and call intimations, found and classified. Part 2 (2B-07), at the
end of this file's `fetch` and `load`, takes those announcements and, per company, picks the newest season with a
transcript filing (and the season before it only if that call doesn't end `stored`), fetches each filing once, classifies
the PDF, follows a cover letter's links to the company's own PDF (or the curated table's), reads the text with each
physical page's code-point range, and writes the call: a `core.event` of type `earnings_call` and its
`equity.earnings_call` row (every processed call, whatever its status), the `call_transcript` and `call_cover_letter`
documents and the `equity.transcript_page` rows. Rows are unpublished: 2B-09 publishes them. The decisions are in
`ft/equity/calls/{pdfs,rows,pipeline}.py`; this class supplies the network and the database.

Discovery registers nothing: every reply is stored as a raw file (`ft/storage.py`, through the fetcher), and the
announcements live in those stored replies, so there is no table for them.

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
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta

from ft.engines.document.job import (
    DocumentFields,
    DocumentJob,
    Harvest,
    Stored,
    insert_document,
    latest_stored,
)
from ft.equity import filings as fl
from ft.equity.calls import discovery as dc
from ft.equity.calls import pdfs, rows
from ft.equity.calls import pipeline as pl
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import COMPANY_WEB, FetchError
from ft.jobs.runner import Alert, Check, Context
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
        self.announcements: list[dc.Announcement] = []       # classified transcripts and intimations (and the rest)
        self.result: dict = {}
        self.plans: list[pl.CallPlan] = []                    # the calls to write (part 2)
        self.report = pl.Report()
        self.storage_host = ""
        self.conflicts: list[str] = []

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


    # ================================================================== part 2 (2B-07): fetch, text, the call rows
    def fetch(self, ctx: Context) -> Harvest:
        h = super().fetch(ctx)                                # part 1: discovery
        self.report, self.plans, self.conflicts = pl.Report(), [], []
        db = ctx.fetcher.db
        try:
            self.storage_host = ctx.fetcher.storage()._s3.meta.endpoint_url or ""
        except Exception:                                      # noqa: BLE001 - only the "no link to our copy" check uses it
            self.storage_host = ""
        names = dict(db.execute("select id, name from core.entity where kind = 'company'").fetchall())
        by_entity: dict[int, str] = {}                         # the season of each company's newest call (weekly runs)
        if ctx.workflow == "weekly":
            for entity_id, key in db.execute("select entity_id, season_key from equity.earnings_call").fetchall():
                season = key.upper().replace("-", " ")
                if entity_id not in by_entity or rows.season_order(season) > rows.season_order(by_entity[entity_id]):
                    by_entity[entity_id] = season
        curated = pl.load_curated()
        run = pl.Run(self._getter(ctx), curated, self.report)
        for co in h.notes["companies"]:
            ref = pl.CompanyRef(co.entity_id, co.slug, names.get(co.entity_id, co.slug), co.symbol)
            self.plans += pl.process_company(run, ref, self.announcements, self.results_events(db, co.entity_id),
                                             weekly=ctx.workflow == "weekly", newest_call=by_entity.get(co.entity_id))
        return h

    @staticmethod
    def results_events(db, entity_id: int) -> list[rows.ResultsEvent]:
        """The company's results announcements with their period ends (equity.filing holds the period)."""
        return [rows.ResultsEvent(r[0], r[1], r[2]) for r in db.execute(
            """select e.id, e.occurs_at, max(f.period_end) from core.event e join equity.filing f on f.event_id = e.id
                where e.event_type = 'results_filing' and e.entity_id = %s and e.status <> 'superseded'
                  and e.occurs_at is not null group by e.id, e.occurs_at""", (entity_id,)).fetchall()]

    def _getter(self, ctx: Context):
        """The pipeline's fetch function. Every body is stored as a raw file before anything reads it. A company host that
        refuses (401, 403, 406 or a network error) gets one retry with Chrome's TLS fingerprint; every failure on a company
        host is logged with its error class (D14)."""
        report = self.report

        def get(url: str, kind: str) -> pl.File | None:
            company = kind == "company"
            source = "company_web" if company else ("bse" if "bseindia.com" in url else "nse")
            for chrome in ((False, True) if company else (False,)):
                try:
                    got = ctx.fetcher.get(url, source=source, allow=(404,), chrome=chrome,
                                          extra_sites=(COMPANY_WEB,) if company else ())
                except FetchError as e:
                    if company and not chrome and (e.status in (None, 401, 403, 406)):
                        continue
                    if company:
                        report.host_failures.append((pdfs.host_of(url), f"HTTP {e.status}" if e.status else _error_class(str(e))))
                    return None
                if got.raw_file_id is None:                    # 404
                    if company:
                        report.host_failures.append((pdfs.host_of(url), f"HTTP {got.status}"))
                    return None
                body = got.body if got.body is not None else ctx.fetcher.read(got.raw_file_id)
                return pl.File(url, body, got.sha256 or b"", got.raw_file_id)
            return None
        return get

    # ------------------------------------------------------------------ load
    def load(self, tx, h: Harvest, task_run_id: int) -> int:
        self.outcome = {"skipped": 0, "added": [], "versions": [], "source_id": None}
        sources = {k: i for k, i in tx.execute("select key, id from core.source where key in ('nse','bse','company_web')").fetchall()}
        n = 0
        for plan in self.plans:
            n += self._write(tx, plan, sources)
        return n

    def _doc(self, tx, d: pl.Doc, plan: pl.CallPlan, sources: dict, event_id: int | None, *, previous: Stored | None = None,
             key: str | None = None) -> tuple[int, bool]:
        """Register a document (or find it, stored with the same bytes). Returns (document id, newly written)."""
        source_id = sources[d.source]
        key = key or d.external_key
        prev = previous if previous is not None else latest_stored(tx, source_id, key)
        if previous is None and prev is not None and prev.sha256 == d.file.sha256:
            return prev.document_id, False
        reg = insert_document(tx, DocumentFields(d.doc_type, plan.company.entity_id, source_id, key, d.source_url, d.title,
                                                 d.file.raw_file_id, d.published_at, event_id), prev)
        if d.text is not None:                                 # the text is in the raw file; the document holds its hash
            ex = d.text.extracted
            tx.execute("""update core.document set extractor_version = %s, char_count = %s, text_sha256 = %s, extracted_at = now()
                           where id = %s""", (ex.extractor_version, ex.char_count, ex.sha256, reg.document_id))
            for sp in d.text.page_rows:
                tx.execute("""insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
                              values (%s,%s,%s,%s)""", (reg.document_id, sp.pdf_page, sp.char_start, sp.char_end))
        (self.outcome["versions"] if reg.new_version else self.outcome["added"]).append((key, reg))
        return reg.document_id, True

    def _write(self, tx, plan: pl.CallPlan, sources: dict) -> int:
        co, q, a = plan.company, plan.quarter, plan.filing
        label = q.label
        slug = f"{co.slug}-call-{rows.season_key(label or plan.season)}"
        existing = tx.execute("select id, status from core.event where event_type = 'earnings_call' and slug = %s", (slug,)).fetchone()
        if existing and not (existing[1] != "stored" and plan.status == "stored"):
            self.outcome["skipped"] += 1                       # the call is stored: a re-run writes nothing
            return 0
        if label and (clash := tx.execute("select event_id from equity.earnings_call where entity_id = %s and fiscal_label = %s",
                                          (co.entity_id, label)).fetchone()) and (not existing or clash[0] != existing[0]):
            self.conflicts.append(f"{co.slug} {label}: already a call for this quarter")
            return 0
        filed_day = rows.india_date(a.disseminated)
        title = f"{co.name} earnings call, {label or plan.season}"
        wrote = 0
        if existing:
            event_id = existing[0]
            tx.execute("""update core.event set status = %s, title = %s, starts_on = %s, source_url = %s, updated_at = now()
                           where id = %s""", (plan.status, title, plan.call_date or filed_day, a.url, event_id))
        else:
            event_id = tx.execute(
                """insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, source_url,
                                           is_published)
                   values ('earnings_call', %s, %s, %s, %s, null, %s, %s, %s, false) returning id""",
                (co.entity_id, slug, title, plan.call_date or filed_day, plan.status, sources[a.exchange.lower()], a.url)
            ).fetchone()[0]
            wrote += 1
        cover_id = None
        if plan.cover is not None:
            cover_id, new = self._doc(tx, plan.cover, plan, sources, event_id)
            wrote += new
        transcript_id = None
        if plan.transcript is not None:
            transcript_id, new = self._doc(tx, plan.transcript, plan, sources, event_id)
            wrote += new
        fields = (label, q.period_end, q.results_event_id, q.source, plan.call_date, plan.call_date_source, plan.route,
                  transcript_id, cover_id, plan.company_url or None, plan.transcript.words if plan.transcript else None,
                  plan.status_reason)
        if existing:
            tx.execute("""update equity.earnings_call set fiscal_label = %s, period_end = %s, results_event_id = %s,
                             quarter_source = %s, call_date = %s, call_date_source = %s, route = %s,
                             transcript_document_id = %s, cover_letter_document_id = %s, company_url = %s, words = %s,
                             status_reason = %s where event_id = %s""", (*fields, event_id))
        else:
            tx.execute(
                """insert into equity.earnings_call (event_id, entity_id, season_key, filed_at, exchange, filing_url, filing_key,
                                                     parser_version, fiscal_label, period_end, results_event_id, quarter_source,
                                                     call_date, call_date_source, route, transcript_document_id,
                                                     cover_letter_document_id, company_url, words, status_reason)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (event_id, co.entity_id, rows.season_key(plan.season), a.disseminated, a.exchange, a.url, a.filing_key,
                 pdfs.PARSER_VERSION, *fields))
            wrote += 1
        for x in plan.extras:                                  # later filings of the season: registered, not analysed
            if x.kind == "revised" and transcript_id is not None:
                first = Stored(transcript_id, 1, plan.transcript.file.sha256)
                _, new = self._doc(tx, x.doc, plan, sources, event_id, previous=_latest(tx, transcript_id, first),
                                   key=plan.transcript.external_key)
            else:
                _, new = self._doc(tx, x.doc, plan, sources, None)
            wrote += new
        return wrote

    # ------------------------------------------------------------------ checks and alerts
    def document_checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        out = self._discovery_checks(tx, h, task_run_id)
        rep = self.report
        docs = [(p, p.transcript) for p in self.plans if p.transcript] + [(p, x.doc) for p in self.plans for x in p.extras]
        bad = [f"{p.company.slug} {p.season}" for p, d in docs
               if pdfs.classify_pdf(d.info) != "full" or d.text is None or len(d.text.extracted.sha256) != 32]
        out.append(Check("transcripts_full", not bad, ("not full transcripts: " + _names(bad)) if bad
                         else f"{len(docs)} registered transcripts passed the full test and have their text hash"))
        host = pdfs.host_of(self.storage_host)
        own = [f"{p.company.slug} {p.season}" for p, d in docs
               if host and pdfs.host_of(d.source_url) == host or not d.source_url.startswith(("http://", "https://"))]
        out.append(Check("no_link_to_our_copy", not own, ("source_url points at the storage host: " + _names(own)) if own
                         else "no registered source_url points at the storage host"))
        for name, items, what in (
                ("unresolved_letters", rep.unresolved, "cover letters with no company PDF found (company, quarter, hosts and statuses)"),
                ("curated_rows", rep.curated_failed, "curated rows that failed the full, company and quarter tests"),
                ("quarter_conflicts", rep.quarter_conflicts, "calls held on a quarter conflict"),
                ("held_calls", rep.held, "calls held with no quarter"),
                ("page_maps", rep.page_map_failed, "documents whose page map failed (no page rows written)"),
                ("files_served", rep.not_served, "transcript filings whose file couldn't be fetched"),
                ("not_transcripts", rep.not_a_transcript, "filings classified a transcript that read as something else"),
                ("other_meetings", rep.other_meetings, "later filings that are another meeting (registered, not analysed)")):
            out.append(Check(name, not items, f"{what}: {_names(items)}" if items else f"none of the {what}", blocking=False))
        fails = Counter(rep.host_failures)
        lines = [f"{host} {err} x{n}" for (host, err), n in sorted(fails.items())]
        out.append(Check("company_hosts", not lines, ("failures on company hosts: " + _names(lines)) if lines
                         else "no failure on a company host", blocking=False))
        if self.conflicts:
            out.append(Check("call_clashes", False, "; ".join(self.conflicts[:SHOWN]), blocking=False))
        stored = sum(p.status == "stored" for p in self.plans)
        by_status = Counter(p.status for p in self.plans)
        revised = sum(1 for p in self.plans for x in p.extras if x.kind == "revised")
        out.append(Check("calls_written", True,
                         f"{len(self.plans)} calls ({stored} stored; " + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items()))
                         + f"); {revised} revised versions; first pages opened for {rep.first_pages} filings", blocking=False))
        return out

    def alerts(self, db) -> list[Alert]:
        out = super().alerts(db)
        for p in self.plans:
            if p.quarter.held_reason == "quarter_conflict":
                out.append(Alert(f"call_quarter_conflict:{p.company.slug}:{p.season}", True, "warn",
                                 f"{self.key}: the {p.season} call of {p.company.slug} is held: the quarter parsed from its "
                                 "transcript disagrees with the results filing it follows"))
        return out


    # ------------------------------------------------------------------ discovery checks
    def _discovery_checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
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


def _latest(tx, document_id: int, fallback: Stored) -> Stored:
    """The latest stored version of the document's key (a second revision supersedes the first)."""
    row = tx.execute(
        """select d.id, d.version_no, f.sha256 from core.document d join core.document k on k.source_id = d.source_id
                  and k.external_key = d.external_key left join core.raw_file f on f.id = d.raw_file_id
            where k.id = %s order by d.version_no desc limit 1""", (document_id,)).fetchone()
    return Stored(row[0], row[1], bytes(row[2]) if row[2] else None) if row else fallback


def _error_class(message: str) -> str:
    """The class of a fetch failure from its message: certificate, connection, timeout, redirect or oversize."""
    m = message.lower()
    if "ssl" in m or "certificate" in m or "tls" in m:
        return "certificate"
    if "timeout" in m:
        return "timeout"
    if "redirect" in m:
        return "redirect"
    return "connection"
