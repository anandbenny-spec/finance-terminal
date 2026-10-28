"""equity.filings: every universe company's quarterly results XBRL filings, registered as documents (Spec 02A §5.3;
job 2A-07). The first task built on the document-job interface (`ft/engines/document/job.py`).

Per company the task reads NSE's two listings (`ft/equity/filings.py`: quarters to Dec 2024, then the integrated
format), each reply stored as a raw file. Every filing's XBRL instance is fetched once (stored by content hash) and
registered: a `core.document` (doc type `results_xbrl`, unpublished: it has no text), the announcement's `core.event`
(type `results_filing`, slug `<company-slug>-results-<period-end>`, `occurs_at` the earliest dissemination time of its
documents; a revised filing gets `-r2` and the earlier announcement becomes `superseded`) and an `equity.filing` row
(`parse_status = 'pending'`: 2A-09 reads the statements).

Window: a weekly run reads the last 400 days of period ends; a backfill reads the period ends from `--since` to
`--until` (the first window reaches back to the Dec 2018 quarter). A re-run writes nothing: a stored file name with the
same bytes is skipped, and the same name with other bytes becomes a new document version and an alert.

Checks, B blocking, W warning:
- (B) `lists_read`: every company's listings were read (a reply that is not JSON, or refused after the retries, fails);
- (B) `basis_known`: every listing row names its basis (consolidated or standalone), period and dissemination time;
- (B) `instances_parse`: every fetched file parses as XML with an XBRL root (an error page served with 200 does not);
- (B) `formats_known`: every filing is one of the five formats (a format other than the five stops the job: Spec 02A §9.4);
- (W) `files_served`: a listed file the source no longer serves, named;
- (W) `quarters_filed`: a quarter with no filing on either basis more than 60 days after it ended (75 for the fiscal
  year's last quarter), named per company;
- (W) `skipped_links`: the listing rows with the empty link, the inline-only rows and the inline duplicates, counted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ft.engines.document.job import (
    Candidate,
    DocumentFields,
    DocumentJob,
    Got,
    Harvest,
    Registered,
    Stored,
    insert_document,
)
from ft.equity import filings as fl
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context
from ft.jobs.tasks import register

SHOWN = 12
Q_LAG, Q4_LAG = timedelta(days=60), timedelta(days=75)
QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))
API_HEADERS = {"Referer": fl.HOME, "Accept": "application/json, text/plain, */*"}


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass(frozen=True)
class FilingGot(Got):
    format: str = ""


@register
class EquityFilings(DocumentJob):
    key = "equity.filings"
    source = "nse"
    doc_type = "results_xbrl"
    media_type = "application/xml"

    def __init__(self):
        super().__init__()
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ discovery
    def discover(self, ctx: Context):
        f = ctx.fetcher
        companies = f.db.execute(
            """select c.entity_id, e.slug, e.name, c.nse_symbol, c.fy_end_month, c.listed_on
                 from equity.company c join core.entity e on e.id = c.entity_id
                where c.nse_symbol is not null order by e.slug""").fetchall()
        today = ctx.now.astimezone(fl.INDIA).date()
        start = fl.window_start(ctx.now, ctx.since)
        end = min(ctx.until or today, today)
        notes: dict = {"window": (start, end), "today": today, "failed": [], "unreadable": [], "empty_links": 0, "ixbrl_only": 0,
                       "ixbrl_duplicates": 0, "cumulative": 0, "outside": 0, "companies": {}}
        self._warm(ctx)
        found: dict[str, Candidate] = {}
        for entity_id, slug, name, symbol, fy_month, listed_on in companies:
            try:
                lst = self._list(ctx, symbol, start, end)
            except (FetchError, ValueError) as e:
                notes["failed"].append(f"{symbol}: {str(e)[:120]}")
                continue
            notes["empty_links"] += lst.empty_links
            notes["ixbrl_only"] += lst.ixbrl_only
            notes["ixbrl_duplicates"] += lst.ixbrl_duplicates
            notes["cumulative"] += lst.cumulative
            notes["unreadable"] += lst.unreadable
            mine = 0
            for x in lst.filings:
                if not (start <= x.period_end <= end):
                    notes["outside"] += 1
                    continue
                fy_start, fy_end = (x.fy_start, x.fy_end) if x.fy_start and x.fy_end else fl.fy_of(x.period_end, fy_month)
                found.setdefault(x.file_name, Candidate(x.file_name, x.url, {
                    "entity_id": entity_id, "slug": slug, "name": name, "symbol": symbol, "basis": x.basis,
                    "period_end": x.period_end, "fy_start": fy_start, "fy_end": fy_end, "audited": x.audited,
                    "filed_at": x.filed_at, "route": x.route, "revision": x.revision}))
                mine += 1
            notes["companies"][entity_id] = {"slug": slug, "symbol": symbol, "fy_month": fy_month, "listed_on": listed_on,
                                             "filings": mine}
        ordered = sorted(found.values(), key=lambda c: (c.meta["filed_at"], c.external_key))   # revisions after originals
        return ordered, notes

    def _warm(self, ctx: Context) -> None:
        """NSE's API wants the cookies its homepage sets: one request to the homepage, kept in the client's jar (the
        page itself is not stored: it is not data we keep)."""
        f = ctx.fetcher
        try:
            f.http.get(fl.HOME, record_url=fl.HOME, stats=f.counts)
        except FetchError:
            pass                                        # the listing calls will say if the cookies were needed

    def _json(self, ctx: Context, path: str, params: dict):
        """A listing reply, stored as a raw file, as JSON. A reply that isn't JSON (Akamai's page, served with 200) or
        a 401/403 is retried once after the homepage is visited again."""
        f = ctx.fetcher
        for attempt in (1, 2):
            try:
                got = f.get(fl.API + path, source="nse", params=params, headers=API_HEADERS, conditional=False,
                            media_type="application/json", ext="json")
                body = got.body if got.body is not None else f.read(got.raw_file_id)
                return json.loads(body)
            except FetchError as e:
                if attempt == 2 or e.status not in (401, 403):
                    raise
            except ValueError:
                if attempt == 2:
                    raise ValueError("the reply isn't JSON") from None
            self._warm(ctx)

    def _list(self, ctx: Context, symbol: str, start: date, end: date) -> fl.Listing:
        parts = []
        if start <= date(2025, 3, 31):                  # the quarterly list ends with the Dec 2024 quarter
            to_day = min(end + timedelta(days=130), ctx.now.astimezone(fl.INDIA).date())
            lo = date(2018, 12, 1) if start <= fl.FIRST_PERIOD else start - timedelta(days=1)
            reply = self._json(ctx, fl.RESULTS_PATH, {"index": "equities", "symbol": symbol, "period": "Quarterly",
                                                      "from_date": lo.strftime("%d-%m-%Y"),
                                                      "to_date": to_day.strftime("%d-%m-%Y")})
            parts.append(fl.parse_results(fl.rows_of(reply), symbol))
        if end >= date(2025, 1, 1):
            for page in range(1, fl.MAX_PAGES + 1):
                reply = self._json(ctx, fl.INTEGRATED_PATH, {"type": fl.INTEGRATED_TYPE, "index": "equities",
                                                             "symbol": symbol, "page": str(page),
                                                             "size": str(fl.PAGE_SIZE)})
                rows = fl.rows_of(reply)
                page_list = fl.parse_integrated(rows, symbol)
                parts.append(page_list)
                oldest = min((x.period_end for x in page_list.filings), default=None)
                if len(rows) < fl.PAGE_SIZE or (oldest is not None and oldest < start):
                    break
        return fl.merge(*parts)

    # ------------------------------------------------------------------ the files
    def fetch_candidate(self, ctx: Context, c: Candidate):
        got = super().fetch_candidate(ctx, c)
        if got is None:
            return None
        try:
            x = fl.read_instance(got.body)
        except fl.FilingError as e:
            raise FetchError(c.url, f"invalid: {e}") from None
        try:
            fmt = fl.format_of(c.external_key, x)
        except fl.FilingError as e:
            raise FetchError(c.url, f"format: {e}") from None
        return FilingGot(c, got.raw_file_id, got.sha256, got.body, got.media_type, fmt)

    # ------------------------------------------------------------------ register
    def register(self, tx, c: Candidate, got: FilingGot, task_run_id: int, previous: Stored | None) -> Registered:
        m = c.meta
        source_id = tx.execute("select id from core.source where key = 'nse'").fetchone()[0]
        filed_at: datetime = m["filed_at"]
        if previous is not None:                        # the same file name with other bytes: the same announcement
            event_id = tx.execute("select event_id from equity.filing where document_id = %s",
                                  (previous.document_id,)).fetchone()
            event_id = event_id[0] if event_id else None
        else:
            event_id = None
        if event_id is None:
            event_id = self._announcement(tx, m, c, filed_at, source_id)
        reg = insert_document(tx, DocumentFields(
            self.doc_type, m["entity_id"], source_id, c.external_key, c.url,
            f"{m['name']} results XBRL, period ended {m['period_end'].isoformat()} ({m['basis']})",
            got.raw_file_id, filed_at, event_id), previous)
        tx.execute(
            """insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end,
                                          audited, filed_at, source_url, exchange, parse_status)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'NSE','pending')""",
            (reg.document_id, m["entity_id"], event_id, m["basis"], got.format, m["period_end"], m["fy_start"],
             m["fy_end"], m["audited"], filed_at, c.url))
        return reg

    def _announcement(self, tx, m, c: Candidate, filed_at: datetime, source_id: int) -> int:
        """The results announcement of this filing: rank 1 + the other files already filed on the same basis for the
        same period (a revision is the second, third … filing), its event created or found; the earlier events are
        superseded."""
        earlier = tx.execute(
            """select count(distinct d.external_key) from equity.filing f join core.document d on d.id = f.document_id
                where f.entity_id = %s and f.period_end = %s and f.basis = %s and d.external_key <> %s
                  and f.filed_at < %s""", (m["entity_id"], m["period_end"], m["basis"], c.external_key, filed_at)
        ).fetchone()[0]
        rank = earlier + 1
        slug = fl.event_slug(m["slug"], m["period_end"], rank)
        title = f"{m['name']} results for the period ended {m['period_end'].isoformat()}" + (
            f" (revision {rank - 1})" if rank > 1 else "")
        event_id = tx.execute(
            """insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id,
                                       source_url, is_published)
               values ('results_filing', %s, %s, %s, %s, %s, 'filed', %s, %s, true)
               on conflict (event_type, slug) do update
                 set occurs_at = least(core.event.occurs_at, excluded.occurs_at), updated_at = now()
               returning id""",
            (m["entity_id"], slug, title, filed_at.astimezone(fl.INDIA).date(), filed_at, source_id, c.url)).fetchone()[0]
        if rank > 1:
            tx.execute(
                """update core.event set status = 'superseded', updated_at = now()
                    where event_type = 'results_filing' and entity_id = %s and status = 'filed' and slug = any(%s)""",
                (m["entity_id"], [fl.event_slug(m["slug"], m["period_end"], r) for r in range(1, rank)]))
        return event_id

    # ------------------------------------------------------------------ checks
    def document_checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        n = h.notes
        start, end = n["window"]
        out: list[Check] = []
        failed = n["failed"]
        out.append(Check("lists_read", not failed,
                         ("the listings of " + _names(failed) + " weren't read") if failed
                         else f"{len(n['companies'])} companies' listings read"))
        bad = n["unreadable"]
        out.append(Check("basis_known", not bad, _names(bad) if bad
                         else f"{len(h.candidates)} filings, each with a basis, a period and a dissemination time"))
        invalid = [f"{c.external_key} ({why[9:]})" for c, why in h.unserved if why.startswith("invalid")]
        out.append(Check("instances_parse", not invalid,
                         ("not an XBRL instance: " + _names(invalid)) if invalid
                         else f"{len(h.got)} files parse as XBRL instances"))
        odd = [f"{c.external_key} ({why[8:]})" for c, why in h.unserved if why.startswith("format")]
        out.append(Check("formats_known", not odd, _names(odd) if odd
                         else "every filing is one of the five formats"))
        gone = [c.external_key for c, why in h.unserved if not why.startswith(("invalid", "format"))]
        out.append(Check("files_served", not gone,
                         ("listed but not served: " + _names(gone)) if gone else "every listed file was served",
                         blocking=False))

        # quarters with no filing, from what the database holds after this load
        missing = []
        today_limit = self._today(h)
        for entity_id, info in n["companies"].items():
            have = {r[0] for r in tx.execute(
                "select distinct period_end from equity.filing where entity_id = %s", (entity_id,)).fetchall()}
            lo = max(start, info["listed_on"]) if info["listed_on"] else start
            names = []
            for y in range(lo.year, end.year + 1):
                for mth, day in QUARTER_ENDS:
                    q = date(y, mth, day)
                    if not (lo <= q <= end):
                        continue
                    lag = Q4_LAG if mth == info["fy_month"] else Q_LAG
                    if q + lag < today_limit and q not in have:
                        names.append(q.isoformat())
            if names:
                missing.append(f"{info['slug']} ({len(names)}: {', '.join(names[:3])}{' …' if len(names) > 3 else ''})")
        out.append(Check("quarters_filed", not missing,
                         ("no filing on either basis for: " + _names(missing)) if missing
                         else "every quarter in the window has a filing", blocking=False))
        out.append(Check("skipped_links", True,
                         f"{n['empty_links']} rows listed with the empty link, {n['ixbrl_only']} with only an inline page, "
                         f"{n['ixbrl_duplicates']} inline duplicates not stored, {n['cumulative']} year-to-date rows, "
                         f"{n['outside']} filings outside the window", blocking=False))
        self.result = {"skipped": self.outcome.get("skipped", 0), "added": len(self.outcome.get("added", [])),
                       "versions": len(self.outcome.get("versions", [])), "candidates": len(h.candidates)}
        return out

    @staticmethod
    def _today(h: Harvest) -> date:
        return h.notes["today"]
