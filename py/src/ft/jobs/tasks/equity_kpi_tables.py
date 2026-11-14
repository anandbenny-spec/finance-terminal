"""equity.kpi_tables: the KPI-table pilot's data (DD-04, #512; Plan 05 §1.3, D7, D8; Data Audit 10). For 7 companies
(TCS, Infosys, HCLTech; HDFC Bank, ICICI Bank, SBI, Axis Bank) over Q3 FY25 to Q2 FY27 (TCS from Q2 FY25), it finds the
results press release, fact sheet or data sheet in the announcements feed (the discovery `equity.call_transcripts` uses),
reads it, extracts the KPI metrics each page states through `ft/ai/client.py` (Haiku 5.5), and writes `equity.kpi_document`
(metadata only) and `equity.kpi_value` (the kept and filtered rows). It writes no flags (DD-05) and shows nothing (the
field policy `equity.kpi_changes` stays hidden until DD-07's judge).

**Nothing is stored** (Plan 05 D7, #287): the file is fetched with `keep=lambda _: False`, so the fetcher returns the
body without a raw file or an `ops.http_cache` row; the task keeps the URL, the pages, the SHA-256 and the size, never the
file or its text. A document already read with the same SHA-256 and parser version is not extracted again (the dedup;
"never resend an unchanged input"); its stored rows are re-filtered with the current rules, with no AI call.

**Which filing** (#512's fix round, 14 Nov 2026; `ft/equity/kpi/documents.py`): the quarter's results day first, then
that day's candidates in the curated rule's order; the first with enough pages whose cut finds its heading is read.
NSE is asked the shared discovery's 365-day windows; a quarter still without a results day then has its own 76-day
window asked once more (`kd.quarter_window`; #512 part 4: dev run 9's long windows came back without six companies'
April 2025 results filings, and dev run 10's per-quarter windows got HTTP 200 without JSON from NSE's new host).

Run logs print counts and ids only, never a page's text (CLAUDE.md §4).

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
- (B) `feed`: every company's announcements came from NSE and at least one of its quarters has a results day, checked
  before any file is read or AI is asked; a failure reads and writes nothing, so the last good rows stand, and names
  why NSE didn't answer (the client's reason without the URL, e.g. a redirect to an unlisted host, with its count);
  its detail also gives each company's NSE windows (requests, leaves answered, rows, the largest reply, any leaf at a
  round count) and, for each quarter with no results day, the feed's filings in its window by stage (#512 part 4);
- (B) `documents`: every company-quarter resolved to a read or a held document (none left silently unresolved);
  `kept` counts read documents that stood as stored because their file couldn't be fetched or read this run (each file
  is asked twice first; #512 part 4);
- (W) `held`: the documents held, with the reason (not found, could not be cut, image bundle, unreadable), named;
- (W) `extracted`: documents read, pages read (text and image), rows kept and filtered by reason, the AI cost;
- (W) `empty`: read documents with no kept row, named (a quarter DD-05 must treat as missing, never as a drop);
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
RUN_LIMIT_USD = Decimal("3.17")     # what is left of the pilot's $5 (pre-approval 2) after DA-10's $0.3864, dev run 2's
                                     # $0.6196 (13 Nov 2026) and dev run 9's $0.8178 (14 Nov 2026; #512), rounded down: no
                                     # single run can take the pilot past $5 (card's stop; was $3.99 before run 9)
READ_MAX_PAGES = 500                 # Infosys's results bundles run 186 to 380 pages (DA-10 K3); pdfs.read_pdf stops at 200
IMAGE_MAX_PAGES = 40                  # an image document longer than this is held (would over-send images)
NSE_HEADERS = {"Referer": dc.NSE_REFERER, "Accept": "application/json, text/plain, */*"}
WHY_MAX = 120                         # characters of one NSE failure reason in the `feed` check
UNFETCHED = ("fetch failed", "the filing is not a PDF", "the PDF could not be read")   # a file not had this run


def _why(e: FetchError) -> str:
    """A fetch failure's reason without its URL (`FetchError` reads "<url>: <reason>"), for the `feed` check: dev runs
    3 to 7 (14 Nov 2026) counted no NSE status at all, so the reason is the only trace of what NSE answered."""
    msg, prefix = str(e), f"{e.url}: "
    return msg[len(prefix):] if msg.startswith(prefix) else type(e).__name__
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
    reused: bool = False               # unchanged since a previous run: values not re-extracted, only re-filtered
    refiltered: list[Value] = field(default_factory=list)   # a reused document's rows whose value or verdict changed
    kept: bool = False                 # its file couldn't be fetched or read this run: the stored document stands as is


@dataclass
class Harvest:
    plans: list[DocPlan] = field(default_factory=list)
    cost: Decimal = Decimal(0)
    stopped: str | None = None         # 'run_cap' | 'budget'
    unavailable: str | None = None     # 'no_key' | 'auth' | 'billing'
    companies: list = field(default_factory=list)
    feed: dict[int, str] = field(default_factory=dict)        # entity_id → 'nse' (every NSE window answered) | 'bse' | 'none'
    results_days: dict[int, int] = field(default_factory=dict)  # entity_id → quarters with a results day found
    preflight: str | None = None       # why nothing was read or written (the feed check failed before any AI call)
    windows: dict[str, dict] = field(default_factory=dict)     # slug → the NSE windows' counts (`feed` check, #512 part 4)
    no_day: dict[str, dict] = field(default_factory=dict)      # 'tcs Q4 FY25' → why no results day, as counts


@register
class EquityKpiTables(Task):
    key = KEY
    uses_nse = True

    def __init__(self):
        self.claude: Client | None = None
        self.feed: dict[int, str] = {}
        self.nse_errors: Counter = Counter()   # why NSE didn't answer: the client's reason (no URL) → requests
        self.windows: dict[int, list[dc.WindowLog]] = {}   # entity_id → every NSE window asked (the `feed` check's counts)
        self.retried: dict[int, list[dc.WindowLog]] = {}   # entity_id → the supplementary per-quarter windows asked

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
                if source == "nse":
                    self.nse_errors[_why(e)] += 1
                if warm and attempt == 1 and e.status in (401, 403):
                    self._warm(ctx, self.nse_errors)
                    continue
                return dc.Answer(e.status if e.status is not None else "network error")
            except ValueError:
                if source == "nse":                       # dev run 10 (14 Nov 2026): every short historical window
                    self.nse_errors["HTTP 200, not JSON"] += 1   # answered so, and the check couldn't say why
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
    def _warm(ctx: Context, errors: Counter | None = None) -> None:
        """Visit NSE's homepage for its cookies, as equity.call_transcripts does: through the HTTP client itself
        (`ctx.fetcher.http`), which stores and records nothing. (`Fetcher.get` takes no `record_url`: the 13 Nov dev
        run, ops.task_run 238, failed on that before any request.) A failure is counted in `errors`, if given."""
        from ft.equity import filings as flg
        try:
            ctx.fetcher.http.get(flg.HOME, record_url=flg.HOME, stats=ctx.fetcher.counts)
        except FetchError as e:
            if errors is not None:
                errors["homepage: " + _why(e)] += 1

    def discover(self, ctx: Context, companies: list[dc.Company], start: date, end: date) -> dict[int, list[kd.Filing]]:
        """Every announcement of each company over [start, end], as `kd.Filing`s with NSE's category (`desc`, which
        the shared discovery doesn't keep; the results-day rule reads it). NSE first, BSE as the fallback when NSE fails
        (the call-transcripts rule); nothing is stored.

        NSE is asked the shared discovery's windows (365 days or fewer). Then, once those answered, each pilot quarter
        still without a results day has its own window (`kd.quarter_window`, 76 days) asked once more, and any rows
        it returns are added; that supplement never fails the feed (#512 part 4, 14 Nov 2026: dev run 9's 365-day
        windows answered without six companies' April 2025 results filings; dev run 10 asked only per-quarter windows,
        and NSE's new host answered every historical one with HTTP 200 and no JSON). Every window's log is kept for the
        `feed` check's counts: `self.windows` (the feed) and `self.retried` (the supplement)."""
        index = dc.CompanyIndex(companies)
        nse, bse = self._nse(ctx), self._bse(ctx)
        rules = kd.load_rules()
        self._warm(ctx, self.nse_errors)
        out: dict[int, list[kd.Filing]] = {c.entity_id: [] for c in companies}
        for co in companies:
            symbols = [co.symbol, *sorted(co.symbols - {co.symbol})]
            ok = True
            rows: list[dict] = []
            for sym in symbols:
                got, logs, sym_ok, _refused = dc.nse_symbol(nse, sym, start, end)
                ok &= sym_ok
                rows += got
                self._log(self.windows, co.entity_id, logs)
            found, category = self._nse_filings(rows, index, co)
            if ok and found and co.slug in rules:
                have = self._as_filings(found, category)
                for label in kd.quarters(co.slug):
                    w = kd.quarter_window(label, start, end)
                    q = rules[co.slug].for_quarter(label)
                    if w is None or kd.results_day(have, q, label) is not None:
                        continue
                    for sym in symbols:                   # depth 1: a failed window isn't split again (dev run 10's
                        got, logs = dc.nse_window(nse, sym, *w, depth=1)   # no-JSON answers would only multiply)
                        rows += got
                        self._log(self.retried, co.entity_id, logs)
                found, category = self._nse_filings(rows, index, co)
            self.feed[co.entity_id] = "nse" if ok and found else "none"
            if (not ok or not found) and co.bse_code:
                self.feed[co.entity_id] = "bse"
                brows, _bok, _problems = dc.bse_company(bse, co.bse_code, start, end)
                for r in brows:
                    a = dc.norm_bse(r)
                    if a is not None:
                        found.append(self._join(a, index, co))
                        category[(a.exchange, a.filing_key)] = (str(r.get("SUBCATNAME") or r.get("CATEGORYNAME") or "")
                                                                .strip() or None)
            out[co.entity_id] = self._as_filings(dc.dedupe(found), category)
        return out

    def _log(self, into: dict[int, list[dc.WindowLog]], entity_id: int, logs: list[dc.WindowLog]) -> None:
        into.setdefault(entity_id, []).extend(logs)
        for x in logs:                                    # a 200 that isn't a row list raises nothing: count it here
            if not x.superseded and not x.answered and x.status == 200:
                self.nse_errors["HTTP 200 without a row list"] += 1

    def _nse_filings(self, rows: list[dict], index: dc.CompanyIndex, co: dc.Company
                     ) -> tuple[list[dc.Announcement], dict[tuple[str, str], str | None]]:
        found: list[dc.Announcement] = []
        category: dict[tuple[str, str], str | None] = {}
        for r in dc.dedupe_rows(rows):
            a = dc.norm_nse(r)
            if a is not None:
                found.append(self._join(a, index, co))
                category[(a.exchange, a.filing_key)] = str(r.get("desc") or "").strip() or None
        return found, category

    @staticmethod
    def _as_filings(found: list[dc.Announcement], category: dict[tuple[str, str], str | None]) -> list[kd.Filing]:
        return [kd.Filing(a.exchange, a.filing_key, a.disseminated, a.subject, a.file_name, a.url,
                          category.get((a.exchange, a.filing_key))) for a in found]

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
        self.feed, self.nse_errors, self.windows, self.retried = {}, Counter(), {}, {}
        anns = self.discover(ctx, h.companies, start, end)
        h.preflight = self._preflight(h, anns, rules)
        for co in h.companies:
            if co.slug not in rules:
                continue
            for label in kd.quarters(co.slug):
                if h.preflight:                           # nothing is read, and the load writes nothing (#512, run 3)
                    q = rules[co.slug].for_quarter(label)
                    h.plans.append(DocPlan(co.entity_id, co.slug, label, kd.period_end(label), q.document_class, "held",
                                           held_reason="not read: the feed check failed"))
                    continue
                if h.stopped:                             # every later quarter is still resolved, as held, so the
                    q = rules[co.slug].for_quarter(label)  # `documents` check passes and what was read (and spent) loads
                    h.plans.append(DocPlan(co.entity_id, co.slug, label, kd.period_end(label), q.document_class, "held",
                                           held_reason=f"not read: the run stopped on the {h.stopped}"))
                    continue
                plan = self._one(ctx, db, co, rules[co.slug], label, anns.get(co.entity_id, []), prompt, schema,
                                 budget, month_spend + h.cost, h)
                h.plans.append(plan)
        return h

    def _preflight(self, h: Harvest, anns: dict[int, list[kd.Filing]], rules: dict[str, kd.Rule]) -> str | None:
        """Before any file is read or any AI call is made: every company's announcements came from NSE (every window
        answered: BSE's fallback asks only the call subcategories, so it can't carry a results filing), and at least one
        of its quarters has a results day. Otherwise the run reads nothing and its blocking `feed` check fails, so the
        last good rows stand (dev run 3, 14 Nov 2026: NSE answered nothing, every quarter came out held, and the load
        replaced run 2's rows). Returns the reason, or None."""
        h.feed = {co.entity_id: self.feed.get(co.entity_id, "none") for co in h.companies if co.slug in rules}
        for co in h.companies:
            if co.slug in rules:
                h.results_days[co.entity_id] = sum(
                    1 for label in kd.quarters(co.slug)
                    if kd.results_day(anns.get(co.entity_id, []), rules[co.slug].for_quarter(label), label) is not None)
        slug = {co.entity_id: co.slug for co in h.companies}
        for co in h.companies:
            if co.slug not in rules:
                continue
            h.windows[co.slug] = self._window_counts(self.windows.get(co.entity_id, []))
            if self.retried.get(co.entity_id):
                h.windows[co.slug]["retried"] = self._window_counts(self.retried[co.entity_id])
            for label in kd.quarters(co.slug):
                q = rules[co.slug].for_quarter(label)
                if kd.results_day(anns.get(co.entity_id, []), q, label) is None:
                    h.no_day[f"{co.slug} {label}"] = kd.results_day_counts(anns.get(co.entity_id, []), q, label)
        not_nse = sorted(slug[e] for e, src in h.feed.items() if src != "nse")
        no_days = sorted(slug[e] for e in h.feed if not h.results_days.get(e))
        problems = []
        if not_nse:
            why = "; ".join(f"{r[:WHY_MAX]} × {n}" for r, n in self.nse_errors.most_common(3))
            problems.append("NSE didn't answer every window for " + ", ".join(not_nse)
                            + (f" (NSE: {why})" if why else ""))
        if no_days:
            problems.append("no results day found for " + ", ".join(no_days))
        return "; ".join(problems) or None

    @staticmethod
    def _window_counts(logs: list[dc.WindowLog]) -> dict:
        """One company's NSE windows as counts and dates (the `feed` check; no row content, CLAUDE.md §4): the requests
        made, the leaf windows (the replies kept: a split window's halves replace it) and how many answered, the rows
        they held, the largest reply, and each answered leaf whose row count is a round number a capped reply would
        give (`discovery.SUSPECT_COUNTS`: kept, since a split stops at 31 days, but named so a cap shows)."""
        leaves = [x for x in logs if not x.superseded]
        return {"requests": len(logs), "leaves": len(leaves), "answered": sum(1 for x in leaves if x.answered),
                "rows": sum(x.rows for x in leaves), "largest": max((x.rows for x in leaves), default=0),
                "round": [f"{x.start}..{x.end} depth {x.depth}: {x.rows}" for x in leaves
                          if x.answered and x.rows in dc.SUSPECT_COUNTS]}

    def _one(self, ctx, db, co, rule: kd.Rule, label: str, anns, prompt, schema, budget, spent_before, h: Harvest) -> DocPlan:
        pe = kd.period_end(label)
        q = rule.for_quarter(label)
        plan = DocPlan(co.entity_id, co.slug, label, pe, q.document_class, "held")
        rd, cands = kd.candidates(anns, rule, label)
        if rd is None:
            plan.held_reason = "no results filing found in the announcements feed"
            return plan
        cands = [a for a in cands if a.url]
        if not cands:
            plan.held_reason = f"no {q.document_class.replace('_', ' ')} filing on the results day ({rd:%d %b %Y})"
            return plan
        why: list[str] = []
        chosen = None
        for ann in cands:                                 # the rule's order: the first usable candidate is the document
            body, info, reason = self._fetch_pdf(ctx, ann)
            if reason is None:
                nums, reason = kd.usable(rule, label, info.page_texts)
            if reason is not None:
                why.append(reason)
                continue
            chosen = (ann, body, info, nums)
            break
        first = cands[0]
        if chosen is None and why and all(r.startswith(UNFETCHED) for r in why):
            kept = self._keep_stored(db, plan, cands)
            if kept is not None:
                return kept
        if chosen is None:
            plan.source_url, plan.filing_key, plan.exchange, plan.filed_at = (first.url, first.filing_key, first.exchange,
                                                                              first.disseminated)
            plan.held_reason = (f"results day {rd:%d %b %Y}: no usable filing ({len(cands)} tried: {'; '.join(why)})")
            return plan
        ann, body, info, nums = chosen
        plan.source_url, plan.filing_key = ann.url, ann.filing_key
        plan.exchange, plan.filed_at = ann.exchange, ann.disseminated
        plan.sha256, plan.bytes, plan.page_count = hashlib.sha256(body).digest(), len(body), info.pages
        # dedup: a read document already stored with this SHA-256 and parser version is not extracted again; its stored
        # rows are re-filtered with the current rules (no AI call), so a filter fix reaches them
        if self._already_read(db, co.entity_id, pe, q.document_class, plan.sha256):
            plan.status, plan.reused = "read", True
            plan.pages_used, plan.image_page_count = self._stored_shape(db, co.entity_id, pe, q.document_class)
            plan.values = self._refilter(db, plan, info)
            return plan
        pages, held = self._pages(rule, label, info, body, nums)
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
        extracted = ex.extract_document(pages, rows_by_page, tile_pages=ex.tile_pages(pages))
        plan.status = "read"
        plan.values = [Value(i + 1, e.row, e.value, e.filter_status, e.filter_reason) for i, e in enumerate(extracted)]
        return plan

    def _fetch_pdf(self, ctx, ann) -> tuple[bytes | None, pdfs.PdfInfo | None, str | None]:
        """One candidate's file, read and not stored: (body, info, None), or a reason it can't be the document. A file
        not had (`UNFETCHED`: a failed fetch, a body that isn't a PDF, a PDF that can't be read) is asked once more: NSE's
        archive answered one file it holds that way in each of dev runs 9 and 11 (14 Nov 2026; TCS's and HDFC Bank's
        Q2 FY27, each read in the other run)."""
        out = self._fetch_pdf_once(ctx, ann)
        if out[2] is not None and out[2].startswith(UNFETCHED):
            out = self._fetch_pdf_once(ctx, ann)
        return out

    def _fetch_pdf_once(self, ctx, ann) -> tuple[bytes | None, pdfs.PdfInfo | None, str | None]:
        """One try at a candidate's file, read and not stored (`keep=lambda _: False`)."""
        source = "bse" if ann.exchange == "BSE" else "nse"
        try:
            got = ctx.fetcher.get(ann.url, source=source, keep=lambda _: False, allow=(404,))
        except FetchError as e:
            return None, None, f"fetch failed: HTTP {e.status}" if e.status else "fetch failed"
        body = got.body
        if not body or not pdfs.is_pdf_bytes(body):
            return None, None, "the filing is not a PDF"
        info = self._read(body)
        if info.unreadable or info.pages == 0:
            return None, None, "the PDF could not be read"
        return body, info, None

    @staticmethod
    def _read(body: bytes) -> pdfs.PdfInfo:
        return pdfs.read_pdf(body, max_pages=READ_MAX_PAGES)

    def _pages(self, rule: kd.Rule, label: str, info: pdfs.PdfInfo, body: bytes, nums: list[int]
               ) -> tuple[list[ex.Page], str | None]:
        """The pages to read: image pages for an image quarter (rendered), else the text pages `kd.usable` cut."""
        if rule.reads_as_image(label):
            if info.pages > IMAGE_MAX_PAGES:
                return [], f"image document too long to render ({info.pages} pages)"
            pages = [ex.Page(n, image_base64=rn.render_page_base64(body, n - 1), image_media_type=rn.PNG_MEDIA_TYPE)
                     for n in range(1, info.pages + 1)]
            return pages, None
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

    def _keep_stored(self, db, plan: DocPlan, cands) -> DocPlan | None:
        """A quarter whose candidates' files couldn't be fetched or read this run keeps its stored read document, when
        that document is one of today's candidates (the same filing): the plan carries the stored rows as they are, and
        the load leaves the document and its rows untouched. Dev run 11 (14 Nov 2026) replaced HDFC Bank's Q2 FY27,
        read in run 9, with a hold over one failed download. None when there is no such document."""
        row = db.execute(
            """select filing_key, source_url, exchange, filed_at, pages_used, page_count, image_page_count, sha256, bytes
                 from equity.kpi_document where entity_id = %s and period_end = %s and document_class = %s
                  and status = 'read' and parser_version = %s""",
            (plan.entity_id, plan.period_end, plan.document_class, kd.PARSER_VERSION)).fetchone()
        if row is None or row[0] not in {a.filing_key for a in cands}:
            return None
        (plan.filing_key, plan.source_url, plan.exchange, plan.filed_at, pages, plan.page_count, plan.image_page_count,
         plan.sha256, plan.bytes) = row
        plan.pages_used = list(pages or [])
        plan.status, plan.reused, plan.kept, plan.held_reason = "read", True, True, None
        plan.values = [Value(n, fl.Row(label, member, measure, value, unit, page), value, status, reason)
                       for n, label, member, measure, value, unit, page, status, reason
                       in self._stored_rows(db, plan.entity_id, plan.period_end, plan.document_class)]
        return plan

    @staticmethod
    def _stored_shape(db, entity_id: int, pe: date, document_class: str) -> tuple[list[int], int]:
        row = db.execute(
            """select pages_used, image_page_count from equity.kpi_document
                where entity_id = %s and period_end = %s and document_class = %s""",
            (entity_id, pe, document_class)).fetchone()
        return (list(row[0] or []), row[1]) if row else ([], 0)

    @staticmethod
    def _stored_rows(db, entity_id: int, pe: date, document_class: str) -> list[tuple]:
        return db.execute(
            """select v.row_no, v.label, v.member, v.measure, v.value, v.unit, v.page, v.filter_status, v.filter_reason
                 from equity.kpi_value v join equity.kpi_document d on d.id = v.document_id
                where d.entity_id = %s and d.period_end = %s and d.document_class = %s order by v.row_no""",
            (entity_id, pe, document_class)).fetchall()

    def _refilter(self, db, plan: DocPlan, info: pdfs.PdfInfo) -> list[Value]:
        """A reused document's stored rows with the current filters applied (no AI call): its page texts come from the
        file just read; an image page has none, as when it was extracted. The rows keep their numbers; the ones whose
        value or verdict changed go on `plan.refiltered` for the load to update."""
        stored = self._stored_rows(db, plan.entity_id, plan.period_end, plan.document_class)
        image = plan.image_page_count > 0 and plan.image_page_count == len(plan.pages_used)
        pages = [ex.Page(n, image_base64="", image_media_type=rn.PNG_MEDIA_TYPE) if image
                 else ex.Page(n, text=info.page_texts[n - 1] if 0 < n <= len(info.page_texts) else "")
                 for n in plan.pages_used]
        rows = [fl.Row(label, member, measure, value, unit, page)
                for _n, label, member, measure, value, unit, page, _st, _rs in stored]
        out: list[Value] = []
        for (n, _lb, _mb, _ms, value, _un, _pg, status, reason), e in zip(stored, ex.filter_rows(rows, pages)):
            v = Value(n, e.row, e.value, e.filter_status, e.filter_reason)
            out.append(v)
            if (e.value, e.filter_status, e.filter_reason) != (value, status, reason):
                plan.refiltered.append(v)
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, h: Harvest, task_run_id: int) -> int:
        n = 0
        if h.preflight:
            return 0                                      # nothing was read: the last good rows stand
        for plan in h.plans:
            n += self._rewrite_filters(tx, plan) if plan.reused else self._write(tx, plan)
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

    @staticmethod
    def _rewrite_filters(tx, plan: DocPlan) -> int:
        """A reused document: only the rows whose value or verdict the current filters changed are updated (its
        document row and every other row stand)."""
        if not plan.refiltered:
            return 0
        with tx.cursor() as cur:
            cur.executemany(
                """update equity.kpi_value v set value = %s, filter_status = %s, filter_reason = %s
                     from equity.kpi_document d
                    where d.id = v.document_id and d.entity_id = %s and d.period_end = %s and d.document_class = %s
                      and v.row_no = %s""",
                [(v.value, v.filter_status, v.filter_reason, plan.entity_id, plan.period_end, plan.document_class,
                  v.row_no) for v in plan.refiltered])
        return len(plan.refiltered)

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
        empty = [p for p in read if not any(v.filter_status == fl.KEPT for v in p.values)]
        slug = {c.entity_id: c.slug for c in h.companies}
        diag = {"windows": dict(sorted(h.windows.items())), "no_results_day": h.no_day}   # counts and dates only
        checks = [
            Check("feed", h.preflight is None,
                  (h.preflight + "; nothing read or written; " + json.dumps(diag)) if h.preflight else json.dumps(
                      {"nse": sum(1 for v in h.feed.values() if v == "nse"), "companies": len(h.feed),
                       "results_days": {slug[e]: n for e, n in sorted(h.results_days.items())}, **diag})),
            Check("documents", resolved == expected,
                  json.dumps({"expected": expected, "resolved": resolved, "read": len(read), "held": len(held),
                              "reused": sum(1 for p in read if p.reused), "kept": sum(1 for p in read if p.kept)})),
            Check("held", True, _names([f"{p.slug} {p.fiscal_label}: {p.held_reason}" for p in held]), blocking=False),
            Check("extracted", True, json.dumps({
                "documents_read": len(read),
                "text_pages": sum(len(p.pages_used) - p.image_page_count for p in read),
                "image_pages": sum(p.image_page_count for p in read),
                "rows_kept": kept, "rows_filtered": dict(sorted(filtered.items())),
                "rows_refiltered": sum(len(p.refiltered) for p in read),
                "ai_cost_usd": str(h.cost.quantize(Decimal("0.0001")))}), blocking=False),
            Check("empty", True, f"{len(read) - len(empty)} of {len(read)} read documents have kept rows"
                  + (f"; none kept: {_names([f'{p.slug} {p.fiscal_label}' for p in empty])}" if empty else ""),
                  blocking=False),
            Check("budget", h.stopped is None,
                  "stopped on the " + h.stopped if h.stopped else f"within the ${RUN_LIMIT_USD} run cap", blocking=False),
        ]
        return checks
