"""equity.brsr: every universe company's BRSR filings (SEBI's Business Responsibility and Sustainability Report) as XBRL,
read for the value chain card (Spec 02B §12.4; Data Audit 08; job VC-04, #189). The document-job interface's third
implementation (the first two are `equity.filings` and `equity.shareholding`); the XBRL reader's BRSR reading is
`ft/equity/xbrl.py` (VC-01) and the rules are `ft/equity/brsr.py`.

Discovery, per company and fiscal year (FY 2023-24 on): NSE's BRSR list (`/api/corporate-bussiness-sustainabilitiy`, the
BRSR page as referer, once for each symbol the company has had; each reply stored as a raw file before it is read) and
BSE's `XbrlBrsrDetails/w` (by scrip code; every version, the latest per year taken). NSE's file is tried first. BSE's
latest version for the year is the fallback, each use a (W) line, when NSE's file fails (an HTTP status, not XBRL, or the
identity check) or NSE lists no filing for a year BSE does. A BSE `.html` entry is fetched as the `.xml` of the same name; if
BSE serves no `.xml` that company-year is held with a (W) line naming it (inline XBRL has no reader: Data Audit 08, VC-01).

Each file is stored once by content hash and registered as a `brsr_xbrl` document (`external_key` the file name,
unpublished, no text) with a `brsr_filing` event, its `equity.brsr_filing` row and the values the card shows
(`equity.brsr_value`, `brsr_product`, `brsr_group_company`). A new NSE revision (a new file name) or a later BSE row for the
year is a new version: the event's slug ends `-r2`, and the earlier event becomes `superseded`. A published value that changes
raises an alert. A file whose identity fails on every route is stored as a `held` filing and shows nothing.

Window: a weekly run reads the filings listed since the latest stored filing date minus 30 days; a backfill reads from
1 Apr 2024 (FY 2023-24's filings) or `--since`. A re-run writes nothing: a stored file name with the same bytes is skipped,
and a file the source answers 304 for is not read back from storage (dev's egress hold, #30).

Checks, B blocking, W warning:
- (B) `nse_answered`: NSE (or BSE, as the fallback) answered for all but two of the companies;
- (W) `lists_read`: a company whose NSE or BSE list was not read, named;
- (W) `refusals`: every 401, 403 and 429, counted;
- (W) `fallbacks`: every company-year served by BSE instead of NSE, with the reason;
- (W) `files_served`: a listed filing that no route could serve or read, named;
- (W) `held_filings`: a filing held because it is not the company's or the listed year's, or BSE served no `.xml`, named;
- (W) `held_values`: held values counted by reason; filings in the 2021 format (stored, not shown) counted; an entry point
  the config doesn't know named;
- (W) `filings_filed`: a company with no stored filing for a year whose annual-report season ended (30 Sep), named;
- (W) `storage_stop` (F3-02 follow-up, #496): the hard stop for new files (`raw_storage_hold_mb`, the same key as the transcript
  task's, 900 MB). The bucket is listed once before the first filing file is fetched (`ft.storage.StorageStop`); at or over the
  stop, or when the bucket can't be listed (the stop fails closed), no new file is fetched or stored for a company-year: it is
  held through the `files_served` line ("storage stop") and a later run the stop allows reads it. Discovery (both exchanges'
  lists) is recorded as usual, and a filing already registered keeps its rows. The check fails (counts and sizes only) and an
  **error** alert `storage_stop:equity.brsr` opens; the run itself still succeeds, as its other held states do.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from ft.engines.document.job import (
    Candidate,
    DocumentFields,
    DocumentJob,
    Got,
    Harvest,
    Registered,
    Stored,
    insert_document,
    latest_stored,
)
from ft.equity import brsr as br
from ft.equity.calls import discovery as dc
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Alert, Check, Context
from ft.jobs.tasks import register
from ft.storage import StorageStop

SHOWN = 12
ALLOWED_FAILURES = 2                                  # of the universe: at least all but two answered (§12.4)
NSE_HEADERS = {"Referer": br.NSE_REFERER, "Accept": "application/json, text/plain, */*"}
BSE_HEADERS = {"Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/",
               "Accept": "application/json, text/plain, */*"}
FILE_HEADERS = {"nse": {"Referer": "https://www.nseindia.com/"}, "bse": {"Referer": "https://www.bseindia.com/"}}
RETRY_WINDOW = timedelta(days=30)                     # the weekly run reads from the latest filing date minus 30 days
ANNUAL_REPORT_SEASON_END = (9, 30)                    # a year with no filing after 30 Sep is named (§12.4)


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass(frozen=True)
class BrsrServed(Got):
    filing: br.Filing | None = None
    held: str | None = None
    unchanged: bool = False


@register
class EquityBrsr(DocumentJob):
    key = "equity.brsr"
    source = "nse"
    doc_type = "brsr_xbrl"
    media_type = "application/xml"

    def __init__(self):
        super().__init__()
        self.cfg = br.load_config()
        self._reset()
        self.result: dict = {}
        self.stop: StorageStop | None = None                  # F3-02 (#496): may this run store a new file now?
        self.checked_filings = 0

    def _reset(self) -> None:
        self.refusals: Counter = Counter()
        self.fallbacks: list[str] = []
        self.value_changes: list[tuple[str, str, int]] = []
        self.restated = 0
        self.stop_held = 0                                    # company-years held by the storage stop this run

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.brsr_filing")

    # ------------------------------------------------------------------ the lists
    def _warm(self, ctx: Context) -> None:
        f = ctx.fetcher
        try:
            f.http.get(br.NSE_HOME + "/", record_url=br.NSE_HOME + "/", stats=f.counts)
        except FetchError:
            pass

    def _json(self, ctx: Context, url: str, source: str, params: dict, headers: dict):
        """A list reply, stored as a raw file, as JSON. NSE's reply that isn't JSON (Akamai's page, served with 200) or a
        401/403 is retried once after the homepage is visited again."""
        f = ctx.fetcher
        for attempt in (1, 2):
            try:
                got = f.get(url, source=source, params=params, headers=headers, conditional=False,
                            media_type="application/json", ext="json")
                body = got.body if got.body is not None else f.read(got.raw_file_id)
                return json.loads(body)
            except FetchError as e:
                if e.status in (401, 403, 429):
                    self.refusals[str(e.status)] += 1
                if attempt == 2 or e.status not in (401, 403) or source != "nse":
                    raise
            except ValueError:
                if attempt == 2:
                    raise ValueError("the reply isn't JSON") from None
            self._warm(ctx)

    def _window(self, ctx: Context, db) -> tuple[date, date]:
        today = ctx.now.astimezone(br.INDIA).date()
        end = min(ctx.until or today, today)
        if ctx.since is not None:
            return ctx.since, end
        if ctx.workflow == "backfill":
            return br.BACKFILL_START, end
        last = db.execute("select max(filed_at) from equity.brsr_filing").fetchone()[0]
        start = br.BACKFILL_START if last is None else max(br.BACKFILL_START, last.astimezone(br.INDIA).date() - RETRY_WINDOW)
        return start, end

    # ------------------------------------------------------------------ the storage stop
    def fetch(self, ctx: Context) -> Harvest:
        # F3-02 (#496): the bucket is listed once, before the first file is fetched (discovery's lists are small and are
        # recorded whatever the stop says); each new file stored is added as it comes. A bucket that can't be listed holds.
        fetcher = ctx.fetcher
        self.stop = StorageStop(lambda: fetcher.storage(), dc.load_calls_config().raw_storage_hold_mb)
        h = super().fetch(ctx)
        self.checked_filings = len(h.candidates)
        if self.stop_held:                                    # the run's annotation: counts and sizes only
            print(f"::error title=storage stop::{self.key}: storage stop reached: {self.stop.describe()}; {self._held_line()}")
        return h

    def _held_line(self) -> str:
        """Counts only (an alert's and the run log's line): never a company's name."""
        return f"held: {self.stop_held} of {self.checked_filings} company-years"

    def _registered(self, ctx: Context, m: dict) -> bool:
        """True when a file of this company-year is stored already (by the name any route would serve): its fetch is a 304."""
        return any(self._stored_hash(ctx, r.exchange.lower(), br.file_name(url)) is not None
                   for r in m["routes"] for url in br.xml_variants(r.url))

    def _storage_stop_checks(self) -> list[Check]:
        """`storage_stop` fails when the stop held a company-year (counts and sizes only, as its alert is); the held
        company-years are named by `files_served`."""
        if self.stop is None:
            return []
        if not self.stop_held:
            return [Check("storage_stop", True, f"not reached: {self.stop.describe()}", blocking=False)]
        return [Check("storage_stop", False,
                      f"storage stop reached: {self.stop.describe()}; {self._held_line()}; no BRSR file was fetched or stored "
                      "for them (files_served names them), both exchanges' lists were recorded as usual, and a later run "
                      "the stop allows reads them", blocking=False)]

    # ------------------------------------------------------------------ discovery
    def discover(self, ctx: Context):
        self._reset()
        f = ctx.fetcher
        db = f.db
        companies = db.execute(
            """select c.entity_id, e.slug, e.name, c.nse_symbol, c.bse_code, c.listed_on
                 from equity.company c join core.entity e on e.id = c.entity_id
                where c.nse_symbol is not null and e.market = 'IN' order by e.slug""").fetchall()
        symbols: dict[int, list[str]] = {}
        codes: dict[int, str] = {}
        for entity_id, scheme, value in db.execute(
                """select entity_id, scheme, value from core.entity_identifier
                    where scheme in ('nse_symbol', 'bse_code') and entity_id in (select id from core.entity where market = 'IN')
                    order by lower(valid) desc nulls last""").fetchall():
            if scheme == "nse_symbol":
                symbols.setdefault(entity_id, []).append(value)
            else:
                codes.setdefault(entity_id, value)
        start, end = self._window(ctx, db)
        notes: dict = {"window": (start, end), "today": ctx.now.astimezone(br.INDIA).date(), "answered": 0, "failed": [],
                       "unreadable": [], "no_link": 0, "companies": {}, "outside": 0, "older": 0}
        self._warm(ctx)
        found: dict[tuple[int, int], Candidate] = {}
        for entity_id, slug, name, symbol, bse_code, listed_on in companies:
            syms = [symbol] + [s for s in symbols.get(entity_id, []) if s != symbol]
            scrip = bse_code or codes.get(entity_id)
            nse_rows: list[br.Listed] = []
            nse_ok = bse_ok = False
            for i, sym in enumerate(syms):
                try:
                    lst = br.parse_nse(br.rows_of(self._json(
                        ctx, br.NSE_HOME + br.NSE_LIST_PATH, "nse",
                        {"index": "equities", "symbol": sym, "from_date": start.strftime("%d-%m-%Y"),
                         "to_date": end.strftime("%d-%m-%Y")}, NSE_HEADERS), "data"))
                except (FetchError, ValueError) as e:
                    if i == 0:
                        notes["failed"].append(f"{sym} (NSE): {str(e)[:100]}")
                    continue
                if i == 0:
                    nse_ok = True
                notes["no_link"] += lst.no_link
                notes["unreadable"] += lst.unreadable
                nse_rows += lst.filings
            bse_rows: list[br.Listed] = []
            if scrip:
                try:
                    lst = br.parse_bse(br.rows_of(self._json(ctx, br.BSE_LIST_URL, "bse", {"scripcode": scrip}, BSE_HEADERS),
                                                  "Table"))
                    bse_ok = True
                    notes["no_link"] += lst.no_link
                    notes["unreadable"] += lst.unreadable
                    bse_rows = lst.filings
                except (FetchError, ValueError) as e:
                    notes["failed"].append(f"{symbol} (BSE {scrip}): {str(e)[:100]}")
            if nse_ok or (bse_ok and bse_rows):
                notes["answered"] += 1
            nse_year, bse_year = br.latest_by_year(nse_rows), br.latest_by_year(bse_rows)
            company = br.CompanyId(name, frozenset(syms), scrip, next((r.company for r in nse_rows if r.company), None))
            mine = 0
            for fy_from in sorted(set(nse_year) | set(bse_year)):
                if fy_from < br.FIRST_FY:
                    notes["older"] += 1
                    continue
                routes = [r for r in (nse_year.get(fy_from), bse_year.get(fy_from)) if r is not None]
                stamp = routes[0].stamp.date()
                if not (start <= stamp <= end):
                    notes["outside"] += 1
                    continue
                first = routes[0]
                found[(entity_id, fy_from)] = Candidate(first.file_name, first.url, {
                    "entity_id": entity_id, "slug": slug, "name": name, "symbol": symbol, "scrip": scrip, "fy_from": fy_from,
                    "fy_to": first.fy_to, "routes": routes, "company": company, "listed_on": listed_on})
                mine += 1
            notes["companies"][entity_id] = {"slug": slug, "symbol": symbol, "listed_on": listed_on, "filings": mine,
                                             "scrip": scrip}
        # years in order (a later year's comparatives need the earlier year's row), then the revisions after the originals
        cands = sorted(found.values(), key=lambda c: (c.meta["fy_from"], c.meta["routes"][0].stamp, c.external_key))
        return cands, notes

    # ------------------------------------------------------------------ the files
    def _stored_hash(self, ctx: Context, source: str, key: str) -> bytes | None:
        row = ctx.fetcher.db.execute(
            """select f.sha256 from core.document d join core.source s on s.id = d.source_id
                 left join core.raw_file f on f.id = d.raw_file_id
                where s.key = %s and d.external_key = %s order by d.version_no desc limit 1""", (source, key)).fetchone()
        return None if row is None or row[0] is None else bytes(row[0])

    def _serve(self, ctx: Context, r: br.Listed, url: str):
        """One file from one route: (Got-like fetched, error text). A 404 is an answer (the file isn't there)."""
        source = r.exchange.lower()
        try:
            got = ctx.fetcher.get(url, source=source, media_type=self.media_type, headers=FILE_HEADERS[source], allow=(404,))
        except FetchError as e:
            if e.status in (401, 403, 429):
                self.refusals[str(e.status)] += 1
            return None, (f"HTTP {e.status}" if e.status else str(e).removeprefix(f"{e.url}: ")[:100])
        if got.raw_file_id is None:
            return None, f"HTTP {got.status}"
        if got.body is not None and self.stop is not None:            # a 200 was stored: count it (a 304 stores nothing)
            self.stop.add(len(got.body))
        return got, None

    def fetch_candidate(self, ctx: Context, c: Candidate):
        m = c.meta
        company: br.CompanyId = m["company"]
        if self.stop is not None and not self.stop.allows() and not self._registered(ctx, m):
            self.stop_held += 1                                        # F3-02 (#496): nothing fetched or stored; (W) files_served
            raise FetchError(m["routes"][0].url, "held by the storage stop (the raw bucket is at or over its stop for new files)")
        why: list[str] = []
        first_held: BrsrServed | None = None
        html_only: str | None = None
        for i, r in enumerate(m["routes"]):
            variants = br.xml_variants(r.url)
            for url in variants:
                got, err = self._serve(ctx, r, url)
                if got is None:
                    why.append(f"{r.exchange} {err}")
                    if r.url.lower().endswith(".html") and html_only is None:
                        html_only = f"BSE serves no .xml for {br.file_name(r.url)}"
                    continue
                name = br.file_name(url)
                src = r.exchange.lower()
                cand = Candidate(name, url, {**m, "exchange": r.exchange, "route": r, "route_no": i})
                if got.body is None:                                     # 304: the stored copy
                    sha = got.sha256 or b""
                    if self._stored_hash(ctx, src, name) == sha:         # registered already: never read back (egress)
                        return BrsrServed(cand, got.raw_file_id, sha, b"", got.media_type, None, None, True)
                    body = ctx.fetcher.read(got.raw_file_id)
                else:
                    body = got.body
                try:
                    filing = br.read_filing(body, company, m["fy_from"], m["fy_to"], self.cfg)
                except br.BrsrError as e:
                    why.append(f"{r.exchange} {e.kind}: {str(e)[:80]}")
                    continue
                served = BrsrServed(cand, got.raw_file_id, got.sha256 or b"", body, got.media_type, filing)
                if filing.identity_ok:
                    if i > 0 or why:
                        self.fallbacks.append(f"{m['slug']} FY {m['fy_from']}-{str(m['fy_to'])[-2:]}: {r.exchange} ({'; '.join(why)})")
                    return served
                why.append(f"{r.exchange} identity: {filing.identity_note[:100]}")
                if first_held is None:
                    first_held = BrsrServed(cand, got.raw_file_id, got.sha256 or b"", body, got.media_type, filing,
                                            "identity: " + filing.identity_note[:200])
        if first_held is not None:
            return first_held
        if html_only is not None and not any("not_xbrl" in w or "identity" in w for w in why):
            raise FetchError(m["routes"][-1].url, f"inline: {html_only}")      # held with a (W) line naming it
        raise FetchError(m["routes"][0].url, "; ".join(why) or "no route served the file")

    # ------------------------------------------------------------------ load
    def load(self, tx, h: Harvest, task_run_id: int) -> int:
        ids = {k: i for k, i in tx.execute("select key, id from core.source where key in ('nse', 'bse')").fetchall()}
        skipped, added, versions = 0, [], []
        for g in h.got:
            c = g.candidate
            previous = latest_stored(tx, ids[c.meta["exchange"].lower()], c.external_key)
            if previous is not None and previous.sha256 == g.sha256:
                skipped += 1
                continue
            if g.filing is None:                                          # unchanged file whose registration is gone: refetch
                raise FetchError(c.url, "a file marked unchanged has no stored registration")
            r = self.register(tx, c, g, task_run_id, previous)
            (versions if r.new_version else added).append((c.external_key, r))
        self.outcome = {"skipped": skipped, "added": added, "versions": versions, "source_id": ids["nse"]}
        return len(added) + len(versions)

    def register(self, tx, c: Candidate, got: BrsrServed, task_run_id: int, previous: Stored | None) -> Registered:
        m, f, r = c.meta, got.filing, c.meta["route"]
        exchange = c.meta["exchange"]
        source_id = tx.execute("select id from core.source where key = %s", (exchange.lower(),)).fetchone()[0]
        held = got.held
        status = "held" if held else "stored"
        version = (tx.execute("select coalesce(max(version_no), 0) from equity.brsr_filing where entity_id = %s and fy_end = %s",
                              (m["entity_id"], f.fy_end)).fetchone()[0]) + 1
        label = br.fy_label(f.fy_start, f.fy_end)
        slug = br.event_slug(m["slug"], f.fy_start, f.fy_end, version)
        filed, revised = br.utc(r.filed), (br.utc(r.revised) if r.revised and r.revised >= r.filed else None)
        event_id = tx.execute(
            """insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, source_url,
                                       is_published)
               values ('brsr_filing', %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (event_type, slug) do update set updated_at = now() returning id""",
            (m["entity_id"], slug, f"{m['name']} BRSR for {label}" + (f" (version {version})" if version > 1 else ""),
             r.filed.date(), filed, status, source_id, br.list_url(exchange, m["scrip"], m["slug"]), status == "stored")).fetchone()[0]
        reg = insert_document(tx, DocumentFields(
            self.doc_type, m["entity_id"], source_id, c.external_key, c.url,
            f"{m['symbol']} BRSR {label}, filed on {exchange}", got.raw_file_id, filed, event_id), previous)
        tx.execute(
            """insert into equity.brsr_filing (event_id, entity_id, document_id, fy_label, fy_start, fy_end, format_version, boundary,
                                               exchange, list_url, version_no, filed_at, revised_at, months, openness_assured,
                                               openness_assurance, status_reason, parser_version)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (event_id, m["entity_id"], reg.document_id, label, f.fy_start, f.fy_end, f.entry_point, f.boundary, exchange,
             br.list_url(exchange, m["scrip"], m["slug"]), version, filed, revised, f.months, f.openness_assured,
             f.openness_assurance, held, br.VERSION))
        if held:
            return reg
        for v in f.values:
            tx.execute(
                """insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status, held_reason)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (event_id, v.key, v.value, v.kind, v.element, v.item_ref, v.status, v.held_reason))
        for n, p in enumerate(f.products, 1):
            tx.execute("insert into equity.brsr_product (event_id, line_no, description, nic_code, share_of_turnover) "
                       "values (%s,%s,%s,%s,%s)", (event_id, n, p.description, p.nic_code, p.share))
        for n, g in enumerate(f.group, 1):
            tx.execute("insert into equity.brsr_group_company (event_id, line_no, name, relation, share_held) "
                       "values (%s,%s,%s,%s,%s)", (event_id, n, g.name, g.relation, g.share_held))
        if version > 1:
            self._supersede(tx, m, f, event_id, version, label)
        self._compare_with_prior_year(tx, m, f)
        return reg

    def _supersede(self, tx, m: dict, f: br.Filing, event_id: int, version: int, label: str) -> None:
        """The earlier stored versions of this company-year become `superseded`; a published value that differs raises an alert."""
        old = tx.execute(
            """select bf.event_id from equity.brsr_filing bf join core.event ev on ev.id = bf.event_id
                where bf.entity_id = %s and bf.fy_end = %s and bf.event_id <> %s and ev.status = 'stored'
                order by bf.version_no desc""", (m["entity_id"], f.fy_end, event_id)).fetchall()
        if not old:
            return
        prev = {k: (v, s) for k, v, s in tx.execute(
            "select field_key, value, status from equity.brsr_value where event_id = %s", (old[0][0],)).fetchall()}
        now = {v.key: (v.value, v.status) for v in f.values}
        changed = sum(1 for k in set(prev) | set(now) if prev.get(k) != now.get(k))
        if changed:
            self.value_changes.append((m["slug"], label, changed))
        tx.execute("update core.event set status = 'superseded', updated_at = now() where id = any(%s)", ([o[0] for o in old],))

    def _compare_with_prior_year(self, tx, m: dict, f: br.Filing) -> None:
        """This filing's prior-year comparatives go on the previous year's row as `next_comparative`; a difference above 0.5 pp
        (a share) or any difference (a count) sets `restated`. The previous year's own value is never replaced (D3)."""
        if not f.prior:
            return
        day_before = f.fy_start - timedelta(days=1)
        row = tx.execute(
            """select bf.event_id from equity.brsr_filing bf join core.event ev on ev.id = bf.event_id
                where bf.entity_id = %s and bf.fy_end = %s and ev.status = 'stored' order by bf.version_no desc limit 1""",
            (m["entity_id"], day_before)).fetchone()
        if row is None:
            return
        for key, comparative in f.prior.items():
            cur = tx.execute("select value, kind, status from equity.brsr_value where event_id = %s and field_key = %s",
                             (row[0], key)).fetchone()
            if cur is None or cur[2] != "ok" or cur[0] is None:
                continue
            restated = br.is_restated(cur[1], cur[0], comparative)
            tx.execute("update equity.brsr_value set next_comparative = %s, restated = %s where event_id = %s and field_key = %s",
                       (comparative, restated, row[0], key))
            self.restated += restated

    # ------------------------------------------------------------------ checks and alerts
    def document_checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        n = h.notes
        out: list[Check] = []
        total = len(n["companies"])
        need = max(0, total - ALLOWED_FAILURES)
        out.append(Check("nse_answered", n["answered"] >= need,
                         f"NSE (or BSE, as the fallback) answered for {n['answered']} of {total} companies (needs {need})"))
        out.append(Check("lists_read", not n["failed"] and not n["unreadable"],
                         ("lists not read: " + _names(n["failed"] + n["unreadable"])) if n["failed"] or n["unreadable"]
                         else f"{total} companies' lists read", blocking=False))
        ref = sum(self.refusals.values())
        out.append(Check("refusals", not ref,
                         "refused: " + ", ".join(f"{k} x{v}" for k, v in sorted(self.refusals.items())) if ref
                         else "no 401, 403 or 429", blocking=False))
        out.append(Check("fallbacks", not self.fallbacks,
                         ("served by the other exchange: " + _names(self.fallbacks)) if self.fallbacks
                         else "every filing came from NSE", blocking=False))
        gone = [f"{c.meta['slug']} FY {c.meta['fy_from']}-{str(c.meta['fy_to'])[-2:]} ({w[:120]})" for c, w in h.unserved]
        out.append(Check("files_served", not gone,
                         ("no route served or read: " + _names(gone)) if gone else "every listed filing was served and read",
                         blocking=False))
        held = [f"{g.candidate.meta['slug']} {g.filing.fy_end.isoformat()} ({g.held})" for g in h.got if g.held]
        out.append(Check("held_filings", not held,
                         ("stored held, nothing shown: " + _names(held)) if held else "no filing is held", blocking=False))
        reasons: Counter = Counter()
        old = 0
        unknown: set[str] = set()
        for g in h.got:
            if g.filing is None or g.held:
                continue
            if g.filing.format_status == "not_shown":
                old += 1
            elif g.filing.format_status == "unknown":
                unknown.add(g.filing.entry_point.isoformat())
            reasons.update(v.held_reason for v in g.filing.values if v.status == "held")
        bad = bool(reasons or old or unknown)
        out.append(Check("held_values", not bad,
                         "; ".join([f"{v} held: {k}" for k, v in sorted(reasons.items())] +
                                    ([f"{old} filings in the 2021 format, stored and not shown"] if old else []) +
                                    ([f"entry point {', '.join(sorted(unknown))} not in brsr_fields.yaml"] if unknown else []))
                         if bad else "no value is held", blocking=False))

        # a year whose annual-report season ended with no stored filing, from what the database holds after this load
        missing = []
        today = n["today"]
        for entity_id, info in n["companies"].items():
            have = {fe.year for (fe,) in tx.execute(
                """select bf.fy_end from equity.brsr_filing bf join core.event ev on ev.id = bf.event_id
                    where bf.entity_id = %s and ev.status in ('stored', 'superseded')""", (entity_id,)).fetchall()}
            gap = []
            for fy_from in range(br.FIRST_FY, today.year):
                season_end = date(fy_from + 1, *ANNUAL_REPORT_SEASON_END)
                year_end = date(fy_from + 1, 3, 31)
                listed = info["listed_on"]
                if season_end < today and fy_from + 1 not in have and not (listed and listed > year_end):
                    gap.append(f"FY {fy_from}-{str(fy_from + 1)[-2:]}")
            if gap:
                missing.append(f"{info['slug']} ({', '.join(gap)})")
        out.append(Check("filings_filed", not missing,
                         ("no filing stored for: " + _names(missing)) if missing
                         else "every company has a filing for each year whose annual-report season ended", blocking=False))
        out += self._storage_stop_checks()
        self.result = {"skipped": self.outcome.get("skipped", 0), "added": len(self.outcome.get("added", [])),
                       "versions": len(self.outcome.get("versions", [])), "candidates": len(h.candidates),
                       "fallbacks": len(self.fallbacks), "held": len(held), "restated": self.restated}
        return out

    def alerts(self, db) -> list[Alert]:
        out = super().alerts(db)
        if self.stop is not None:                              # error level; resolved by the next run the stop allows
            out.append(Alert(f"storage_stop:{self.key}", bool(self.stop_held), "error",
                             f"{self.key}: storage stop reached: {self.stop.describe()}; {self._held_line()}; no new BRSR "
                             "files fetched or stored (docs/OPERATIONS.md section 5.9)"))
        out += [Alert(f"brsr_values_changed:{slug}:{label}", True, "warn",
                      f"{self.key}: {n} published values of {slug} {label} changed in a new version of its filing")
                for slug, label, n in self.value_changes]
        return out
