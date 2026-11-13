"""equity.kpi_tables: the KPI-table pilot's data (DD-04, #512; Plan 05 §1.3, D7, D8; Data Audit 10). For 7 companies
(TCS, Infosys, HCLTech; HDFC Bank, ICICI Bank, SBI, Axis Bank) over Q3 FY25 to Q2 FY27 (TCS from Q2 FY25), it finds the
results press release, fact sheet or data sheet in the announcements feed (the discovery `equity.call_transcripts` uses),
reads it, extracts the KPI metrics each page states through `ft/ai/client.py` (Haiku 5.5), and writes `equity.kpi_document`
(metadata only) and `equity.kpi_value` (the kept and filtered rows). It writes no flags (DD-05) and shows nothing (the
field policy `equity.kpi_changes` stays hidden until DD-07's judge).

**Nothing is stored** (Plan 05 D7, #287): the file is fetched with `keep=lambda _: False`, so the fetcher returns the
body without a raw file or an `ops.http_cache` row; the task keeps the URL, the pages, the SHA-256 and the size, never the
file or its text. A document already read with the same SHA-256 and parser version is not extracted again (the dedup;
"never resend an unchanged input"). Run logs print counts and ids only, never a page's text (CLAUDE.md §4).

**Hand-run only, no schedule before 18 Nov** (card): `due()` refuses the daily, weekly and watch workflows, so the
Sunday weekly never runs it; a `backfill` with `--task equity.kpi_tables` on dev is how the dev run and, later, the
one production run (DD-06) are started. Not `refresh`: its job stops at 30 minutes, and the run's few hundred
page calls go one after another (DA-10's 384 took about 50 minutes); `backfill` allows 240. A run that stops on
its cap still resolves every remaining quarter as held, so what it read loads and a re-run picks up the rest
(read documents with an unchanged SHA-256 are not extracted again).

**AI** inside the pilot's $5 from the $25 pool (pre-approval 2, 11 Nov 2026; $4.61 left after DA-10): every call is priced
before it is sent and stopped at the run cap or the `ai_monthly_budget_usd` switch; the run's cost goes on `ops.task_run.ai_cost_usd`. Without a
key or credits the task skips and sends nothing (#197).

Checks, B blocking, W warning (counts and ids only):
- (B) `documents`: every company-quarter resolved to a read or a held document (none left silently unresolved);
- (W) `held`: the documents held, with the reason (not found, could not be cut, image bundle, unreadable), named;
- (W) `extracted`: documents read, pages read (text and image), rows kept and filtered by reason, the AI cost;
- (W) `budget`: whether the run stopped on the run cap or the monthly switch (passing when it didn't);
- (W) `ai_unavailable` (always passes): set when no key or credits, so the run skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from ft.ai import batch
from ft.ai.client import AIUnavailable, CallError, Client, Failed, TooLong, cost_usd, load_prompt, load_schema
from ft.equity.calls import discovery as dc
from ft.equity.calls import pdfs
from ft.equity.guard import equity_due
from ft.equity.kpi import documents as kd
from ft.equity.kpi import extraction as ex
from ft.equity.kpi import filters as fl
from ft.equity.kpi import render as rn
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

TASK = "kpi_tables"
KEY = "equity.kpi_tables"
SHOWN = 12
RUN_LIMIT_USD = Decimal("4.61")     # what is left of the pilot's $5 (pre-approval 2) after DA-10's $0.3864 (card, #514)
READ_MAX_PAGES = 500                 # Infosys's results bundles run 186 to 380 pages (DA-10 K3); pdfs.read_pdf stops at 200
IMAGE_MAX_PAGES = 40                  # an image document longer than this is held (would over-send images)
NSE_HEADERS = {"Referer": dc.NSE_REFERER, "Accept": "application/json, text/plain, */*"}
BSE_HEADERS = {"Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/",
               "Accept": "application/json, text/plain, */*"}


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Value:
    row_no: int
    row: fl.Row
    value: Decimal | None
    filter_status: str
    filter_reason: str | None


@dataclass
class DocPlan:
    entity_id: int
    slug: str
    fiscal_label: str
    period_end: date
    document_class: str
    status: str                        # 'read' | 'held'
    held_reason: str | None = None
    source_url: str | None = None
    filing_key: str | None = None
    exchange: str | None = None
    filed_at: datetime | None = None
    pages_used: list[int] = field(default_factory=list)
    page_count: int | None = None
    image_page_count: int = 0
    sha256: bytes | None = None
    bytes: int | None = None
    values: list[Value] = field(default_factory=list)
    reused: bool = False               # unchanged since a previous run: values not re-extracted


@dataclass
class Harvest:
    plans: list[DocPlan] = field(default_factory=list)
    cost: Decimal = Decimal(0)
    stopped: str | None = None         # 'run_cap' | 'budget'
    unavailable: str | None = None     # 'no_key' | 'auth' | 'billing'
    companies: list = field(default_factory=list)


@register
class EquityKpiTables(Task):
    key = KEY
    uses_nse = True

    def __init__(self):
        self.claude: Client | None = None

    # ------------------------------------------------------------------ scheduling (hand-run only before 18 Nov)
    def due(self, db, workflow: str, now=None) -> Due:
        if workflow in ("daily", "weekly", "watch"):
            return Due(False, "KPI pilot: hand-run only (refresh/backfill) until 18 Nov (card; tracker #30)")
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ the companies
    @staticmethod
    def companies(db, start: date) -> list[dc.Company]:
        rows = db.execute(
            """select c.entity_id, e.slug, c.nse_symbol, c.isin, c.bse_code, c.listed_on
                 from equity.company c join core.entity e on e.id = c.entity_id
                where e.slug = any(%s) and c.nse_symbol is not null and e.market = 'IN' order by e.slug""",
            (list(kd.COMPANIES),)).fetchall()
        ids = [r[0] for r in rows]
        sym: dict[int, set[str]] = {i: set() for i in ids}
        isn: dict[int, set[str]] = {i: set() for i in ids}
        for entity_id, scheme, value, upper in db.execute(
                """select entity_id, scheme, value, upper(valid) from core.entity_identifier
                    where entity_id = any(%s) and scheme in ('nse_symbol','isin')""", (ids,)).fetchall():
            if scheme == "isin":
                isn[entity_id].add(value)
            elif upper is None or upper >= start:
                sym[entity_id].add(value)
        return [dc.Company(i, slug, s, isin, bse, listed, frozenset(sym[i]), frozenset(isn[i]))
                for i, slug, s, isin, bse, listed in rows]

    # ------------------------------------------------------------------ the network (nothing stored)
    def _get_json(self, ctx: Context, url: str, source: str, params: dict, headers: dict, warm: bool) -> dc.Answer:
        """One feed request, not stored (`keep=lambda _: False`): the body is read and discarded. A 401/403 from NSE is
        retried once after the homepage is visited again for its cookies."""
        f = ctx.fetcher
        for attempt in (1, 2):
            try:
                got = f.get(url, source=source, params=params, headers=headers, conditional=False,
                            media_type="application/json", ext="json", keep=lambda _: False)
                body = got.body if got.body is not None else b""
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
        from ft.equity import filings as flg

        def fetch(symbol: str, start: date, end: date) -> dc.Answer:
            return self._get_json(ctx, flg.API + dc.NSE_ANN_PATH, "nse",
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

    @staticmethod
    def _warm(ctx: Context) -> None:
        """Visit NSE's homepage for its cookies, as equity.call_transcripts does: through the HTTP client itself
        (`ctx.fetcher.http`), which stores and records nothing. (`Fetcher.get` takes no `record_url`: the 13 Nov dev
        run, ops.task_run 238, failed on that before any request.)"""
        from ft.equity import filings as flg
        try:
            ctx.fetcher.http.get(flg.HOME, record_url=flg.HOME, stats=ctx.fetcher.counts)
        except FetchError:
            pass

    def discover(self, ctx: Context, companies: list[dc.Company], start: date, end: date) -> dict[int, list[dc.Announcement]]:
        """Every announcement of each company over [start, end], joined to the company. NSE first, BSE as the fallback
        when NSE fails (the call-transcripts rule); nothing is stored."""
        index = dc.CompanyIndex(companies)
        nse, bse = self._nse(ctx), self._bse(ctx)
        self._warm(ctx)
        out: dict[int, list[dc.Announcement]] = {c.entity_id: [] for c in companies}
        for co in companies:
            found: list[dc.Announcement] = []
            ok = True
            for sym in [co.symbol, *sorted(co.symbols - {co.symbol})]:
                rows, _logs, sym_ok, _refused = dc.nse_symbol(nse, sym, start, end)
                ok &= sym_ok
                for r in rows:
                    a = dc.norm_nse(r)
                    if a is not None:
                        found.append(self._join(a, index, co))
            if (not ok or not found) and co.bse_code:
                brows, _bok, _problems = dc.bse_company(bse, co.bse_code, start, end)
                for r in brows:
                    a = dc.norm_bse(r)
                    if a is not None:
                        found.append(self._join(a, index, co))
            out[co.entity_id] = dc.dedupe(found)
        return out

    @staticmethod
    def _join(a: dc.Announcement, index: dc.CompanyIndex, asked: dc.Company) -> dc.Announcement:
        from dataclasses import replace
        if a.exchange == "BSE":
            return replace(a, entity_id=asked.entity_id, joined_by="asked")
        c, how = index.match(a)
        return replace(a, entity_id=c.entity_id if c else None, joined_by=how)

    # ------------------------------------------------------------------ AI
    def _claude(self) -> Client:
        if self.claude is None:
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise AIUnavailable("no_key", "NoKey", None, "ANTHROPIC_API_KEY is not set")
            self.claude = Client(key)
        return self.claude

    # ------------------------------------------------------------------ fetch (discovery, read, extract)
    def fetch(self, ctx: Context) -> Harvest:
        db = ctx.fetcher.db
        h = Harvest()
        today = ctx.now.astimezone(dc.INDIA).date()
        start = kd.period_end(kd.TCS_FIRST) + timedelta(days=1)
        end = min(ctx.until or today, today)
        h.companies = self.companies(db, start)
        rules = kd.load_rules()
        prompt, schema = load_prompt(ex.TASK), load_schema(ex.TASK)
        budget = batch.monthly_budget(db)
        month_spend = batch.month_spend(db)
        try:
            self._claude()
        except AIUnavailable as e:
            h.unavailable = e.reason
            return h                                      # nothing to read without AI (#197)
        anns = self.discover(ctx, h.companies, start, end)
        for co in h.companies:
            if co.slug not in rules:
                continue
            for label in kd.quarters(co.slug):
                if h.stopped:                             # every later quarter is still resolved, as held, so the
                    q = rules[co.slug].for_quarter(label)  # `documents` check passes and what was read (and spent) loads
                    h.plans.append(DocPlan(co.entity_id, co.slug, label, kd.period_end(label), q.document_class, "held",
                                           held_reason=f"not read: the run stopped on the {h.stopped}"))
                    continue
                plan = self._one(ctx, db, co, rules[co.slug], label, anns.get(co.entity_id, []), prompt, schema,
                                 budget, month_spend + h.cost, h)
                h.plans.append(plan)
        return h

    def _one(self, ctx, db, co, rule: kd.Rule, label: str, anns, prompt, schema, budget, spent_before, h: Harvest) -> DocPlan:
        pe = kd.period_end(label)
        q = rule.for_quarter(label)
        plan = DocPlan(co.entity_id, co.slug, label, pe, q.document_class, "held")
        ann = kd.select(anns, rule, label)
        if ann is None or not ann.url:
            plan.held_reason = "no document found in the announcements feed"
            return plan
        plan.source_url, plan.filing_key = ann.url, ann.filing_key
        plan.exchange, plan.filed_at = ann.exchange, ann.disseminated
        source = "bse" if ann.exchange == "BSE" else "nse"
        try:
            got = ctx.fetcher.get(ann.url, source=source, keep=lambda _: False, allow=(404,))
        except FetchError as e:
            plan.held_reason = f"fetch failed: HTTP {e.status}" if e.status else "fetch failed"
            return plan
        body = got.body
        if not body or not pdfs.is_pdf_bytes(body):
            plan.held_reason = "the filing is not a PDF"
            return plan
        plan.sha256, plan.bytes = hashlib.sha256(body).digest(), len(body)
        info = self._read(body)
        plan.page_count = info.pages
        if info.unreadable or info.pages == 0:
            plan.held_reason = "the PDF could not be read"
            return plan
        # dedup: a read document already stored with this SHA-256 and parser version is not extracted again
        if self._already_read(db, co.entity_id, pe, q.document_class, plan.sha256):
            plan.status, plan.reused = "read", True
            plan.pages_used, plan.image_page_count = self._stored_shape(db, co.entity_id, pe, q.document_class)
            return plan
        pages, held = self._pages(rule, label, info, body)
        if held is not None:
            plan.held_reason = held
            return plan
        plan.pages_used = [p.page for p in pages]
        plan.image_page_count = sum(1 for p in pages if p.is_image)
        rows_by_page, cost, stopped = self._extract(pages, co, label, prompt, schema, budget, spent_before + h.cost, h)
        h.cost += cost
        if stopped:
            h.stopped = stopped
            plan.held_reason = f"stopped on the {stopped}"
            return plan
        tile_pages = frozenset(p.page for p in pages if p.is_image)   # image pages are treated as summary tiles for the cross-check
        extracted = ex.extract_document(pages, rows_by_page, tile_pages=tile_pages)
        plan.status = "read"
        plan.values = [Value(i + 1, e.row, e.value, e.filter_status, e.filter_reason) for i, e in enumerate(extracted)]
        return plan

    @staticmethod
    def _read(body: bytes) -> pdfs.PdfInfo:
        return pdfs.read_pdf(body, max_pages=READ_MAX_PAGES)

    def _pages(self, rule: kd.Rule, label: str, info: pdfs.PdfInfo, body: bytes) -> tuple[list[ex.Page], str | None]:
        """The pages to read: image pages for an image quarter (rendered), else the text pages cut from the file."""
        if rule.reads_as_image(label):
            if info.pages > IMAGE_MAX_PAGES:
                return [], f"image document too long to render ({info.pages} pages)"
            pages = [ex.Page(n, image_base64=rn.render_page_base64(body, n - 1), image_media_type=rn.PNG_MEDIA_TYPE)
                     for n in range(1, info.pages + 1)]
            return pages, None
        nums, reason = kd.cut_pages(rule, label, info.page_texts)
        if reason is not None:
            return [], reason
        return [ex.Page(n, text=info.page_texts[n - 1]) for n in nums], None

    def _extract(self, pages, co, label, prompt, schema, budget, spent_before, h: Harvest):
        """Send one request per page, within the run cap and the monthly switch. Returns (rows by page, this document's
        cost, a stop reason or None)."""
        claude = self._claude()
        rows_by_page: dict[int, list[fl.Row]] = {}
        cost = Decimal(0)
        prev: ex.Page | None = None
        for page in pages:
            req = ex.page_request(0, co.slug, label, page, prev, prompt, schema)
            prev = page
            try:
                claude.check(req)
            except TooLong:
                rows_by_page[page.page] = []
                continue
            worst = self._worst_case(req)
            if spent_before + cost + worst > budget or cost + worst > RUN_LIMIT_USD:
                return rows_by_page, cost, "budget" if spent_before + cost + worst > budget else "run_cap"
            try:
                reply = claude.send(req)
            except (Failed, CallError):
                rows_by_page[page.page] = []
                continue
            cost += reply.cost_usd
            try:
                rows_by_page[page.page] = ex.parse_rows(reply.json(), page.page)
            except (ValueError, json.JSONDecodeError):
                rows_by_page[page.page] = []
        return rows_by_page, cost, None

    def _worst_case(self, req) -> Decimal:
        from ft.ai.client import Usage
        u = Usage(input=req.estimated_tokens(), output=req.spec.max_tokens)
        return cost_usd(req.model_id, u)

    # ------------------------------------------------------------------ dedup helpers
    @staticmethod
    def _already_read(db, entity_id: int, pe: date, document_class: str, sha: bytes) -> bool:
        return bool(db.execute(
            """select 1 from equity.kpi_document where entity_id = %s and period_end = %s and document_class = %s
                and status = 'read' and sha256 = %s and parser_version = %s""",
            (entity_id, pe, document_class, sha, kd.PARSER_VERSION)).fetchone())

    @staticmethod
    def _stored_shape(db, entity_id: int, pe: date, document_class: str) -> tuple[list[int], int]:
        row = db.execute(
            """select pages_used, image_page_count from equity.kpi_document
                where entity_id = %s and period_end = %s and document_class = %s""",
            (entity_id, pe, document_class)).fetchone()
        return (list(row[0] or []), row[1]) if row else ([], 0)

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, h: Harvest, task_run_id: int) -> int:
        n = 0
        for plan in h.plans:
            if plan.reused:
                continue                                  # unchanged: its rows stand
            n += self._write(tx, plan)
        tx.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                   (h.cost.quantize(Decimal("0.0001")), task_run_id))
        return n

    def _write(self, tx, plan: DocPlan) -> int:
        # replace this company/quarter/class document (a run re-reads it): delete the old, insert the new
        tx.execute("""delete from equity.kpi_document where entity_id = %s and period_end = %s and document_class = %s""",
                   (plan.entity_id, plan.period_end, plan.document_class))
        doc_id = tx.execute(
            """insert into equity.kpi_document (entity_id, fiscal_label, period_end, document_class, source_url,
                   filing_key, exchange, filed_at, pages_used, page_count, image_page_count, sha256, bytes, status,
                   held_reason, parser_version)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (plan.entity_id, plan.fiscal_label, plan.period_end, plan.document_class, plan.source_url, plan.filing_key,
             plan.exchange, plan.filed_at, plan.pages_used or None, plan.page_count, plan.image_page_count,
             plan.sha256, plan.bytes, plan.status, plan.held_reason, kd.PARSER_VERSION)).fetchone()[0]
        with tx.cursor() as cur:
            cur.executemany(
                """insert into equity.kpi_value (document_id, row_no, label, member, measure, value, unit, page,
                       dict_key, filter_status, filter_reason, parser_version)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,null,%s,%s,%s)""",
                [(doc_id, v.row_no, v.row.label, v.row.member, v.row.measure, v.value, v.row.unit, v.row.page,
                  v.filter_status, v.filter_reason, kd.PARSER_VERSION) for v in plan.values])
        return 1 + len(plan.values)

    # ------------------------------------------------------------------ checks
    def checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        if h.unavailable:
            return [Check("ai_unavailable", True, f"skipped: AI unavailable ({h.unavailable})", blocking=False),
                    Check("documents", True, "no run (AI unavailable)")]
        expected = sum(len(kd.quarters(c.slug)) for c in h.companies if c.slug in kd.load_rules())
        resolved = len(h.plans)
        read = [p for p in h.plans if p.status == "read"]
        held = [p for p in h.plans if p.status == "held"]
        kept = sum(1 for p in read for v in p.values if v.filter_status == fl.KEPT)
        filtered = Counter(v.filter_reason for p in read for v in p.values if v.filter_status == fl.FILTERED)
        checks = [
            Check("documents", resolved == expected,
                  json.dumps({"expected": expected, "resolved": resolved, "read": len(read), "held": len(held),
                              "reused": sum(1 for p in read if p.reused)})),
            Check("held", True, _names([f"{p.slug} {p.fiscal_label}: {p.held_reason}" for p in held]), blocking=False),
            Check("extracted", True, json.dumps({
                "documents_read": len(read),
                "text_pages": sum(len(p.pages_used) - p.image_page_count for p in read),
                "image_pages": sum(p.image_page_count for p in read),
                "rows_kept": kept, "rows_filtered": dict(sorted(filtered.items())),
                "ai_cost_usd": str(h.cost.quantize(Decimal("0.0001")))}), blocking=False),
            Check("budget", h.stopped is None,
                  "stopped on the " + h.stopped if h.stopped else f"within the ${RUN_LIMIT_USD} run cap", blocking=False),
        ]
        return checks
