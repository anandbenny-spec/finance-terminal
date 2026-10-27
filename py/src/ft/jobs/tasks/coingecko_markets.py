"""crypto.coingecko_markets: CoinGecko's daily market snapshot of the top 500 coins, stored and unpublished
(issue #106; Data Audit 04 decisions C4 and C9; Plan 02 §6).

No free source keeps a history of fully diluted valuation (FDV) and supply, so a day that isn't stored can't be
recovered later. Every day this task fetches the top 500 coins by market cap (two `/coins/markets` pages of 250, USD,
no sparkline, full precision) and stores each response as a raw file through `ft/storage.py`, registered in
`core.raw_file` with `content_date` = the fetch's UTC date. That meets "every observation is stored once fetched"
(CLAUDE.md §3). There is no load: `rows_written` stays 0 and `files_fetched` shows 2. Module 4 (the crypto ranking)
parses the raw files later; nothing reads them before then, so no table is fixed here (reuse rule 2: generalise at the
second use) and nothing is published.

The Demo key (`COINGECKO_DEMO_KEY`) goes in the request header `x-cg-demo-api-key`, never in the URL, so it can't reach
`ops.http_cache`, `core.raw_file.source_url`, an error or a log. `conditional=False`: each day gets its own two files.
`due()` skips with its reason while the `coingecko` source row (migration 0018) or the key is missing, so a production
`daily` between the merge and Anand's `supabase db push` skips the task instead of failing; the health page shows it
overdue until its first success.

Checks, B blocking, W warning (a blocking failure marks the run `checks_failed`; the raw files stay stored):
- (B) `shape`: each page is a JSON array of 250 objects with the keys the module reads; null values are fine
  (Ethereum has no max supply), a missing key means the API changed;
- (B) `universe`: at least 495 distinct ids across the pages, `bitcoin` among them (the two calls are about 2 seconds
  apart, so a coin near rank 250 can land on both pages or on neither);
- (W) `page_overlap`: any id on both pages;
- (B) `fresh`: at least 95% of rows updated within 24 hours of the fetch (CoinGecko's terms §6.1 ask for stored data
  to be refreshed every 24 hours, and a stale snapshot isn't today's);
- (W) `supply`: rows whose circulating supply exceeds total supply, whose total supply exceeds max supply (each by more
  than 0.01%), or whose FDV is below market cap: input for module 4's own checks, nothing is changed here;
- (W) `nulls`: the share of rows with no FDV, no total supply and no max supply.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

URL = "https://api.coingecko.com/api/v3/coins/markets"
KEY_HEADER = "x-cg-demo-api-key"
KEY_ENV = "COINGECKO_DEMO_KEY"
PAGES = (1, 2)
PER_PAGE = 250
PARAMS = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": str(PER_PAGE), "sparkline": "false",
          "precision": "full"}
REQUIRED_KEYS = ("id", "symbol", "name", "current_price", "market_cap", "market_cap_rank", "fully_diluted_valuation",
                 "total_volume", "circulating_supply", "total_supply", "max_supply", "last_updated")
MIN_IDS = 495
FRESH_WITHIN = timedelta(hours=24)
MIN_FRESH_SHARE = 0.95
SUPPLY_TOLERANCE = 1e-4                     # 0.01%
LISTED = 20                                 # the warnings list at most this many ids


@dataclass
class Page:
    number: int
    fetched_at: datetime
    rows: object = None                      # the parsed JSON (checked by `shape`)
    parse_error: str | None = None


@dataclass
class Fetched:
    now: datetime
    pages: list[Page] = field(default_factory=list)


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _updated(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d if d.tzinfo else None


def _ids(ids: list[str]) -> str:
    more = f", and {len(ids) - LISTED} more" if len(ids) > LISTED else ""
    return ", ".join(ids[:LISTED]) + more


def evaluate(pages: list[Page], now: datetime) -> list[Check]:
    """The task's checks over the fetched pages (pure: no database)."""
    out: list[Check] = []

    problems: list[str] = []
    for p in pages:
        if p.parse_error:
            problems.append(f"page {p.number}: not JSON ({p.parse_error})")
        elif not isinstance(p.rows, list):
            problems.append(f"page {p.number}: not a JSON array")
        elif len(p.rows) != PER_PAGE:
            problems.append(f"page {p.number}: {len(p.rows)} rows, not {PER_PAGE}")
        else:
            bad = [r for r in p.rows if not isinstance(r, dict) or any(k not in r for k in REQUIRED_KEYS)]
            if bad:
                missing = sorted({k for r in bad if isinstance(r, dict) for k in REQUIRED_KEYS if k not in r})
                problems.append(f"page {p.number}: {len(bad)} rows lack keys" + (f" ({', '.join(missing)})" if missing else ""))
    if len(pages) != len(PAGES):
        problems.append(f"{len(pages)} pages fetched, not {len(PAGES)}")
    out.append(Check("shape", not problems, "; ".join(problems) or f"{len(pages)} pages of {PER_PAGE} rows, every key present"))
    if problems:
        return out                              # nothing below is meaningful on a changed or broken response

    rows = [(p.number, r) for p in pages for r in p.rows]
    ids = [str(r["id"]) for _, r in rows]
    distinct = set(ids)
    out.append(Check("universe", len(distinct) >= MIN_IDS and "bitcoin" in distinct,
                     f"{len(distinct)} distinct ids (at least {MIN_IDS}), bitcoin {'present' if 'bitcoin' in distinct else 'MISSING'}"))
    first = {str(r["id"]) for r in pages[0].rows}
    both = sorted(first & {str(r["id"]) for r in pages[1].rows}) if len(pages) > 1 else []
    out.append(Check("page_overlap", not both, f"{len(both)} on both pages: {_ids(both)}" if both else "no id on both pages",
                     blocking=False))

    fresh = 0
    for _, r in rows:
        u = _updated(r["last_updated"])
        if u is not None and now - u <= FRESH_WITHIN:
            fresh += 1
    share = fresh / len(rows)
    out.append(Check("fresh", share >= MIN_FRESH_SHARE,
                     f"{fresh} of {len(rows)} rows updated within 24 hours of the fetch ({share * 100:.1f}%, at least "
                     f"{MIN_FRESH_SHARE * 100:.0f}%)"))

    over_total, over_max, fdv_low = [], [], []
    for _, r in rows:
        circ, total, mx = _num(r["circulating_supply"]), _num(r["total_supply"]), _num(r["max_supply"])
        fdv, cap = _num(r["fully_diluted_valuation"]), _num(r["market_cap"])
        if circ is not None and total is not None and circ > total * (1 + SUPPLY_TOLERANCE):
            over_total.append(str(r["id"]))
        if total is not None and mx is not None and total > mx * (1 + SUPPLY_TOLERANCE):
            over_max.append(str(r["id"]))
        if fdv is not None and cap is not None and fdv < cap * (1 - SUPPLY_TOLERANCE):
            fdv_low.append(str(r["id"]))
    odd = over_total or over_max or fdv_low
    out.append(Check("supply", not odd,
                     f"circulating above total supply: {len(over_total)} ({_ids(over_total)}); total above max supply: "
                     f"{len(over_max)} ({_ids(over_max)}); FDV below market cap: {len(fdv_low)} ({_ids(fdv_low)})",
                     blocking=False))

    n = len(rows)
    null_share = {k: sum(r[k] is None for _, r in rows) / n
                  for k in ("fully_diluted_valuation", "total_supply", "max_supply")}
    out.append(Check("nulls", True, "share of rows with no value: " + ", ".join(
        f"{k} {v * 100:.1f}%" for k, v in null_share.items()), blocking=False))
    return out


@register
class CoingeckoMarkets(Task):
    key = "crypto.coingecko_markets"
    source = "coingecko"

    def due(self, db, workflow: str, now: datetime | None = None) -> Due:
        missing = []
        if db.execute("select 1 from core.source where key = %s", (self.source,)).fetchone() is None:
            missing.append("the coingecko source row (migration 0018: supabase db push)")
        if not os.environ.get(KEY_ENV):
            missing.append(f"{KEY_ENV}")
        if missing:
            return Due(False, "not set up: " + " and ".join(missing) + " missing")
        return super().due(db, workflow, now)

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        key = os.environ.get(KEY_ENV)
        if not key:
            raise RuntimeError(f"{KEY_ENV} is not set")
        f = ctx.fetcher
        out = Fetched(ctx.now)
        for n in PAGES:
            got = f.get(URL, source=self.source, params={**PARAMS, "page": str(n)}, headers={KEY_HEADER: key},
                        conditional=False, media_type="application/json", content_date=ctx.now.date())
            body = got.body if got.body is not None else f.read(got.raw_file_id)
            page = Page(n, got.fetched_at)
            try:
                page.rows = json.loads(body)
            except ValueError as e:
                page.parse_error = str(e)[:100]
            out.pages.append(page)
        return out

    # No load: the raw files are the record (rows_written stays 0).
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        return evaluate(f.pages, max(p.fetched_at for p in f.pages))
