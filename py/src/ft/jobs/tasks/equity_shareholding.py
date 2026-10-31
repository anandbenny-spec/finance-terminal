"""equity.shareholding: every universe company's quarterly shareholding patterns (SEBI LODR Reg 31), registered as
documents, with the promoter and promoter-group holding in `equity.shareholding` (Spec 02A §5.5; job 2A-10). The
document-job interface's second implementation (the first is `equity.filings`) and the XBRL reader's second use.

Per company the task reads NSE's `/api/corporate-share-holdings-master` (the homepage first, for its cookies), the reply
stored as a raw file before anything is read from it. Each listed pattern's XBRL instance is fetched once (stored by
content hash) and registered: a `core.document` (doc type `shareholding_xbrl`, `external_key` the file name,
`published_at` the submission time, unpublished: it has no text) and an `equity.shareholding` row (`as_of` the quarter
end, `filed_at`, `source_url`, `exchange = 'NSE'`). No `core.event`: Spec 02A seeds no event type for patterns. The
rows' reading rules (latest filed per company and quarter; no promoter group is 0%) are in `ft/equity/shareholding.py`.

Window: a weekly run reads the patterns whose quarter ended in the last 400 days; a backfill reads the quarter ends from
`--since` to `--until` (the first window reaches the 31 Dec 2018 quarter), as far as the API lists: there is no other
route. A re-run writes nothing: a stored file name with the same bytes is skipped, and the same name with other bytes
becomes a new document version (with an alert) and a new row.

Checks, B blocking, W warning:
- (B) `lists_read`: every company's listing was read (a reply that is not JSON, or refused after the retries, fails);
- (B) `instances_are_patterns`: every fetched file is an XBRL instance with shareholder-category contexts and a quarter
  end; a file that is not one fails the run, named (never a guessed 0%);
- (B) `promoter_range`: every promoter % is between 0 and 100;
- (W) `unit_suspect`: a file whose promoter % is outside 0–100 by a factor of 100 while its own share counts agree with the
  figure divided by 100 (BHARTIARTL, quarter ended 30 Sep 2020, filed as 5,623%; Anand, 31 Oct 2026, option 1): held
  back, no row stored, named with the filed value, the value divided by 100, the file's share-count ratio and NSE's own
  figure; any other out-of-range figure still fails `promoter_range`;
- (W) `quarter_move`: a move of more than 10 points between consecutive quarters, named;
- (W) `agrees_with_nse`: the XBRL figure differs from NSE's own `pr_and_prgrp` by more than 0.01 points, named, or the
  listing's quarter end differs from the file's (both added by Claude under the standing delegation: they only add checks;
  X1 found 20 of 20 equal);
- (W) `quarters_filed`: a universe company with no pattern for a quarter that ended more than 45 days ago, named;
- (W) `files_served`: a listed file the source no longer serves, named;
- (W) `skipped_links`: the listing rows without an XBRL link, counted.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

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
from ft.equity import shareholding as sh
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_filings import EquityFilings

SHOWN = 12
MOVE = Decimal(10)                  # points between consecutive quarters
NSE_GAP = Decimal("0.01")           # points between the XBRL figure and NSE's own


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass(frozen=True)
class PatternGot(Got):
    pattern: sh.Pattern | None = None


@register
class EquityShareholding(DocumentJob):
    key = "equity.shareholding"
    source = "nse"
    doc_type = "shareholding_xbrl"
    media_type = "application/xml"

    # NSE's homepage cookies and the stored JSON reply are the same two steps `equity.filings` takes (a third user,
    # `equity.corporate_actions`, has its own copy): borrowed rather than copied, to be generalised in a job of their own.
    _warm = EquityFilings._warm
    _json = EquityFilings._json

    def __init__(self):
        super().__init__()
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ discovery
    def discover(self, ctx: Context):
        f = ctx.fetcher
        companies = f.db.execute(
            """select c.entity_id, e.slug, c.nse_symbol, c.listed_on
                 from equity.company c join core.entity e on e.id = c.entity_id
                where c.nse_symbol is not null order by e.slug""").fetchall()
        today = ctx.now.astimezone(fl.INDIA).date()
        start = fl.window_start(ctx.now, ctx.since)
        end = min(ctx.until or today, today)
        notes: dict = {"window": (start, end), "today": today, "failed": [], "unreadable": [], "no_link": 0, "outside": 0,
                       "companies": {}}
        self._warm(ctx)
        found: dict[str, Candidate] = {}
        for entity_id, slug, symbol, listed_on in companies:
            try:
                lst = sh.parse_master(fl.rows_of(self._json(ctx, sh.LIST_PATH, {"index": "equities", "symbol": symbol})),
                                      symbol)
            except (FetchError, ValueError) as e:
                notes["failed"].append(f"{symbol}: {str(e)[:120]}")
                continue
            notes["no_link"] += lst.no_link
            notes["unreadable"] += lst.unreadable
            mine = 0
            for p in lst.patterns:
                if not (start <= p.as_of <= end):
                    notes["outside"] += 1
                    continue
                found.setdefault(p.file_name, Candidate(p.file_name, p.url, {
                    "entity_id": entity_id, "slug": slug, "symbol": symbol, "as_of": p.as_of, "filed_at": p.filed_at,
                    "nse_pct": p.nse_pct}))
                mine += 1
            notes["companies"][entity_id] = {"slug": slug, "symbol": symbol, "listed_on": listed_on, "patterns": mine}
        return sorted(found.values(), key=lambda c: (c.meta["filed_at"], c.external_key)), notes   # revisions after originals

    # ------------------------------------------------------------------ the files
    def fetch_candidate(self, ctx: Context, c: Candidate):
        got = super().fetch_candidate(ctx, c)
        if got is None:
            return None
        try:
            pattern = sh.parse_pattern(got.body)
        except sh.ShareholdingError as e:
            raise FetchError(c.url, f"{e.kind}: {e}") from None
        return PatternGot(c, got.raw_file_id, got.sha256, got.body, got.media_type, pattern)

    # ------------------------------------------------------------------ register
    def register(self, tx, c: Candidate, got: PatternGot, task_run_id: int, previous: Stored | None) -> Registered:
        m, p = c.meta, got.pattern
        source_id = tx.execute("select id from core.source where key = 'nse'").fetchone()[0]
        reg = insert_document(tx, DocumentFields(
            self.doc_type, m["entity_id"], source_id, c.external_key, c.url,
            f"{m['symbol']} shareholding pattern, quarter ended {p.as_of.isoformat()}", got.raw_file_id, m["filed_at"]),
            previous)
        tx.execute(
            """insert into equity.shareholding (document_id, entity_id, as_of, promoter_pct, promoter_shares, total_shares,
                                                filed_at, source_url, exchange)
               values (%s,%s,%s,%s,%s,%s,%s,%s,'NSE')""",
            (reg.document_id, m["entity_id"], p.as_of, p.promoter_pct, p.promoter_shares, p.total_shares,
             m["filed_at"], c.url))
        return reg

    # ------------------------------------------------------------------ checks
    def document_checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        n = h.notes
        start, end = n["window"]
        out: list[Check] = []
        failed = n["failed"] + n["unreadable"]
        out.append(Check("lists_read", not failed,
                         ("the listings of " + _names(failed) + " weren't read") if failed
                         else f"{len(n['companies'])} companies' listings read"))
        why = {k: [f"{c.meta['symbol']} {c.external_key} ({w.split(': ', 1)[1]})" for c, w in h.unserved
                   if w.startswith(k + ":")] for k in ("invalid", "range", "unit_suspect")}
        out.append(Check("instances_are_patterns", not why["invalid"],
                         ("not a shareholding pattern: " + _names(why["invalid"])) if why["invalid"]
                         else f"{len(h.got)} files are shareholding patterns with a quarter end"))
        out.append(Check("promoter_range", not why["range"],
                         ("promoter % outside 0–100: " + _names(why["range"])) if why["range"]
                         else "every promoter % is between 0 and 100"))

        held = []
        for c, w in h.unserved:
            if w.startswith("unit_suspect:"):
                nse = c.meta["nse_pct"]
                held.append(f"{c.meta['symbol']} {c.external_key} ({w.split(': ', 1)[1]}; NSE "
                            f"{'lists ' + str(nse) + '%' if nse is not None else 'lists no figure'})")
        out.append(Check("unit_suspect", not held,
                         ("held back, no row stored: " + _names(held)) if held
                         else "no file's promoter % looks 100 times too large", blocking=False))

        # the XBRL figure against NSE's own, and the listing's quarter end against the file's
        gaps = []
        for g in h.got:
            m, p = g.candidate.meta, g.pattern
            if p.as_of != m["as_of"]:
                gaps.append(f"{m['symbol']} {g.candidate.external_key} (listed for {m['as_of'].isoformat()}, "
                            f"file says {p.as_of.isoformat()})")
            elif m["nse_pct"] is not None and abs(p.promoter_pct - m["nse_pct"]) > NSE_GAP:
                gaps.append(f"{m['symbol']} {p.as_of.isoformat()} (file {p.promoter_pct:.2f}%, NSE {m['nse_pct']}%)")
        out.append(Check("agrees_with_nse", not gaps,
                         ("the file and NSE's listing differ: " + _names(gaps)) if gaps
                         else "every file's promoter % and quarter end match NSE's listing", blocking=False))

        # moves between consecutive quarters, and quarters with no pattern, from what the database holds after this load
        moves, missing = [], []
        touched = {g.candidate.meta["entity_id"] for g in h.got}
        for entity_id, info in n["companies"].items():
            rows = tx.execute(
                """select distinct on (as_of) as_of, promoter_pct from equity.shareholding
                    where entity_id = %s order by as_of, filed_at desc, document_id desc""", (entity_id,)).fetchall()
            if entity_id in touched:
                for (a, pa), (b, pb) in itertools.pairwise(rows):
                    if (b - a) <= timedelta(days=100) and abs(pb - pa) > MOVE:
                        moves.append(f"{info['slug']} {a.isoformat()} to {b.isoformat()} ({pa:.2f}% to {pb:.2f}%)")
            have = {a for a, _ in rows}
            lo = max(start, info["listed_on"]) if info["listed_on"] else start
            gone = [q.isoformat() for q in sh.quarter_ends(lo, end) if q + sh.LATE < n["today"] and q not in have]
            if gone:
                missing.append(f"{info['slug']} ({len(gone)}: {', '.join(gone[:3])}{' …' if len(gone) > 3 else ''})")
        out.append(Check("quarter_move", not moves,
                         ("more than 10 points in a quarter: " + _names(moves)) if moves
                         else "no promoter % moved more than 10 points between consecutive quarters", blocking=False))
        out.append(Check("quarters_filed", not missing,
                         ("no pattern for: " + _names(missing)) if missing
                         else "every quarter in the window has a pattern", blocking=False))
        gone_files = [c.external_key for c, w in h.unserved if not w.startswith(("invalid", "range", "unit_suspect"))]
        out.append(Check("files_served", not gone_files,
                         ("listed but not served: " + _names(gone_files)) if gone_files else "every listed file was served",
                         blocking=False))
        out.append(Check("skipped_links", True,
                         f"{n['no_link']} listing rows without an XBRL link, {n['outside']} patterns outside the window",
                         blocking=False))
        self.result = {"skipped": self.outcome.get("skipped", 0), "added": len(self.outcome.get("added", [])),
                       "versions": len(self.outcome.get("versions", [])), "candidates": len(h.candidates)}
        return out
