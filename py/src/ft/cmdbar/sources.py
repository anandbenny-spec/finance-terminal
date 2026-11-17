"""The source lines of a data answer (K-13, #630; `CLAUDE.md` §1: every figure shows its source line, every NSE-derived figure says
"Source: NSE", an RBI figure names RBI and never links to RBI's pages; Design 01 §4.2's `SourceLine`).

`lines(sql, columns, rows)` is a data answer's `sources`: the lines of every `semantic` view its query read (`cache.views_read`),
in the site's own words. Indian company figures say "Source: NSE" (`web/lib/equity/company.ts`'s `SOURCE_LINE`), US company
figures "Source: SEC EDGAR" (statements) and "Source: Yahoo Finance" (prices, market caps, valuations), Fed rows "Source: Federal
Reserve Board", RBI rows "Source: Reserve Bank of India" (text only), the model-implied valuation columns Damodaran's line, and
`market_daily` each series' own attribution (`core.series`, as the ticker tape shows it: split at " · ", so the four US index
series carry their copyright line).

**Which market or bank.** A view that holds more than one narrows its lines by what the returned rows show: a `market` or
`currency` column, or an identifier only one market fills (`nse_symbol` for India, `ticker` for the US), for the company views; a
`bank` column for the policy views; `series_key`, `series_name` or `source` (else `market`) for `market_daily`; an exchange column
(`exchange`, `source_exchange`) for the views whose Indian rows name the exchange they came from. It **fails closed**: a row that
shows none of these, or a value this module doesn't know, counts as everything the view holds; so does a query that reads a view
or a `WITH` name more than once, or holds a subquery (`SubLink`), since a row's columns then needn't describe every figure in it.
Over-inclusive is allowed; an answer with an NSE-derived figure and no "Source: NSE" is not.

**Every view in `sqlguard.VIEWS` has an entry** (`VIEW_SOURCES`): `tests/test_cmdbar_sources.py` fails if one hasn't, so a later
view must add its lines here. Cached answers and the stored example answers predate the field: the route fills it from the stored
query and rows (`fill`), with no model call and no query.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from pglast import ast, parse_sql
from pglast.visitors import Visitor

from ft.cmdbar import cache

NSE = "Source: NSE"
BSE = "Source: BSE"
YAHOO = "Source: Yahoo Finance"
SEC = "Source: SEC EDGAR"
FED = "Source: Federal Reserve Board"
RBI = "Source: Reserve Bank of India"
DAMODARAN = "Source: Aswath Damodaran, NYU Stern"
NIFTY = "Source: NSE Indices, via Yahoo Finance"
TREASURY = "Source: U.S. Treasury, via Federal Reserve Board (H.15) and FRED"

# market_daily's series (`core.series`, kind 'observed'), each with its market and the attribution its rows' `source` column
# holds (0008, 0012, 0015, 0019's seeds). fred.SP500 is left out: it stays unpublished for good (0012, D3), so no row of it
# reaches the view; its key, like any key not here, counts as every series.
SERIES: dict[str, tuple[str, str]] = {
    "yf.^NSEI": ("IN", "Source: NSE Indices, via Yahoo Finance"),
    "yf.^NSEBANK": ("IN", "Source: NSE Indices, via Yahoo Finance"),
    "yf.^BSESN": ("IN", "Source: BSE Index Services, via Yahoo Finance"),
    "fred.INDIRLTLT01STM": ("IN", "Source: OECD, via FRED"),
    "damodaran.IND_CRP": ("IN", DAMODARAN),
    "damodaran.IND_DEFAULT_SPREAD": ("IN", DAMODARAN),
    "yf.^GSPC": ("US", "Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC"),
    "yf.^DJI": ("US", "Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC"),
    "yf.^IXIC": ("US", "Source: Nasdaq, Inc., via Yahoo Finance · © Nasdaq, Inc."),
    "yf.^NDX": ("US", "Source: Nasdaq, Inc., via Yahoo Finance · © Nasdaq, Inc."),
    "fred.DGS2": ("US", TREASURY),
    "fred.DGS10": ("US", TREASURY),
    "fred.DFEDTARL": ("US", "Source: FRED, Federal Reserve Bank of St. Louis"),
    "fred.DFEDTARU": ("US", "Source: FRED, Federal Reserve Bank of St. Louis"),
    "fred.EFFR": ("US", "Source: Federal Reserve Bank of New York, via FRED"),
    "fred.DTWEXBGS": ("US", "Source: Federal Reserve Board (H.10), via FRED"),
    "fred.DEXINUS": ("GLOBAL", "Source: Federal Reserve Board (H.10), via FRED"),
    "yf.INR=X": ("GLOBAL", "Source: Yahoo Finance"),
    "damodaran.ERP_IMPLIED": ("GLOBAL", DAMODARAN),
}
# The same series by the name the view's `series_name` column shows ("S&P 500" is yf.^GSPC's: fred.SP500 never shows).
SERIES_NAMES: dict[str, str] = {
    "Nifty 50": "yf.^NSEI", "Nifty Bank": "yf.^NSEBANK", "Sensex": "yf.^BSESN",
    "India 10-year government bond yield (monthly)": "fred.INDIRLTLT01STM",
    "India country risk premium (per ctryprem edition)": "damodaran.IND_CRP",
    "India sovereign default spread (per ctryprem edition)": "damodaran.IND_DEFAULT_SPREAD",
    "S&P 500": "yf.^GSPC", "Dow Jones Industrial Average": "yf.^DJI", "Nasdaq Composite": "yf.^IXIC", "Nasdaq-100": "yf.^NDX",
    "US 2-year Treasury yield": "fred.DGS2", "US 10-year Treasury yield": "fred.DGS10",
    "Fed target range, lower bound": "fred.DFEDTARL", "Fed target range, upper bound": "fred.DFEDTARU",
    "Effective federal funds rate": "fred.EFFR", "Broad US dollar index": "fred.DTWEXBGS", "USD/INR": "fred.DEXINUS",
    "USD/INR (Yahoo Finance)": "yf.INR=X", "Implied equity risk premium, mature market (monthly)": "damodaran.ERP_IMPLIED",
}
_ATTRIBUTIONS: dict[str, str] = {attr: key for key, (_, attr) in SERIES.items()}


def split(attribution: str) -> tuple[str, ...]:
    """An attribution as its lines: "Source: …, via Yahoo Finance · © …" → (source, copyright), as the ticker tape splits it."""
    return tuple(p.strip() for p in attribution.split(" · ") if p.strip())


# Each view's lines by what its rows can be: a market (company views), a bank (policy views) or a series (market_daily).
# `None` is every row of the view, whatever the rows show.
VIEW_SOURCES: dict[str, dict[str | None, tuple[str, ...]]] = {
    "policy_meetings": {"Fed": (FED,), "RBI": (RBI,)},
    "policy_votes": {"Fed": (FED,), "RBI": (RBI,)},
    "policy_projections": {None: (RBI,)},                                   # RBI's projections only
    "market_daily": {k: split(attr) for k, (_, attr) in SERIES.items()},
    "companies": {"IN": (NSE,), "US": (SEC,)},
    "company_financials": {"IN": (NSE,), "US": (SEC,)},
    "company_valuations": {"IN": (NSE,), "US": (YAHOO, SEC)},
    "rate_decision_moves": {None: (NSE, NIFTY, FED, RBI)},                  # Nifty 50 stocks around Fed and RBI decisions
    "value_chain": {"IN": (NSE,)},
    "earnings_calls": {"IN": (NSE,)},
    "call_guidance": {"IN": (NSE,)},
}
POLICY_VIEWS = ("policy_meetings", "policy_votes")
COMPANY_VIEWS = ("companies", "company_financials", "company_valuations", "value_chain", "earnings_calls", "call_guidance")
# Views whose Indian rows name the exchange they came from: with the column, BSE only where a row says BSE; without it, both.
EXCHANGE_COLUMNS: dict[str, str] = {"earnings_calls": "exchange", "value_chain": "source_exchange"}
# Columns whose figures carry another source than the view's rows: Damodaran's inputs behind the model-implied valuation
# columns (`company.ts`'s DAMODARAN), and the US 2-year yield behind policy_meetings' surprise measure.
EXTRA_COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "company_valuations": {c: (DAMODARAN,) for c in ("beta", "implied_growth", "growth_gap", "implied_roe", "roe_gap")},
    "policy_meetings": {"us_2y_change_bps": (TREASURY,)},
}
MARKETS = {"IN": "IN", "US": "US", "INR": "IN", "USD": "US"}
BANKS = {"fed": "Fed", "rbi": "RBI"}


# ---------------------------------------------------------------------------------------------- the query's shape
class _Shape(Visitor):
    def __init__(self):
        self.relations: Counter = Counter()
        self.sublinks = 0

    def visit_RangeVar(self, ancestors, node):
        self.relations[(node.relname or "").lower()] += 1

    def visit_SubLink(self, ancestors, node):
        self.sublinks += 1


def narrowable(sql: str | None) -> bool:
    """Whether a row's columns may narrow its sources: each view and `WITH` name read once and no subquery. Anything else, or a
    query that doesn't parse, is read as every market (fails closed)."""
    if not sql:
        return False
    try:
        tree = parse_sql(sql)
    except Exception:                                    # noqa: BLE001 - pglast's ParseError
        return False
    v = _Shape()
    for raw in tree:
        if isinstance(raw, ast.RawStmt):
            v(raw.stmt)
    return v.sublinks == 0 and all(n == 1 for n in v.relations.values())


# ---------------------------------------------------------------------------------------------- a row's keys
def _named(names: Sequence[str], row: Sequence, column: str) -> list:
    return [row[i] for i, n in enumerate(names) if n == column and i < len(row)]


def _texts(values: Iterable) -> list[str]:
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def _market(names: Sequence[str], row: Sequence) -> set[str] | None:
    """The markets a company row shows (`market`, `currency`, `nse_symbol`, `ticker`); None if none or one isn't known."""
    got: set[str] = set()
    for v in _texts(_named(names, row, "market") + _named(names, row, "currency")):
        if v.upper() not in MARKETS:
            return None
        got.add(MARKETS[v.upper()])
    if _texts(_named(names, row, "nse_symbol")):
        got.add("IN")
    if _texts(_named(names, row, "ticker")):
        got.add("US")
    return got or None


def _bank(names: Sequence[str], row: Sequence) -> set[str] | None:
    got: set[str] = set()
    for v in _texts(_named(names, row, "bank")):
        if v.lower() not in BANKS:
            return None
        got.add(BANKS[v.lower()])
    return got or None


def _series(names: Sequence[str], row: Sequence) -> set[str] | None:
    """The series a market_daily row is: by key, name or attribution, else every series of the market it shows."""
    keys = _texts(_named(names, row, "series_key"))
    if keys:
        return set(keys) if all(k in SERIES for k in keys) else None
    named = _texts(_named(names, row, "series_name"))
    if named:
        return {SERIES_NAMES[n] for n in named} if all(n in SERIES_NAMES for n in named) else None
    attrs = _texts(_named(names, row, "source"))
    if attrs:
        return {_ATTRIBUTIONS[a] for a in attrs} if all(a in _ATTRIBUTIONS for a in attrs) else None
    markets = {v.upper() for v in _texts(_named(names, row, "market"))}
    if markets and markets <= {"IN", "US", "GLOBAL"}:
        return {k for k, (m, _) in SERIES.items() if m in markets}
    return None


# ---------------------------------------------------------------------------------------------- the lines
def _view_lines(view: str, names: Sequence[str], rows: Sequence[Sequence], narrow: bool) -> list[str]:
    table = VIEW_SOURCES[view]
    every = [line for lines in table.values() for line in lines]
    if None in table or not narrow or not rows:
        keys: set | None = None
    else:
        keys = set()
        pick = _series if view == "market_daily" else _bank if view in POLICY_VIEWS else _market
        for row in rows:
            got = pick(names, row)
            if got is None:
                keys = None
                break
            keys |= got
        if keys is not None and not keys <= set(table):    # a market the view doesn't hold: read as every row (fails closed)
            keys = None
    out = every if keys is None else [line for k in sorted(keys) for line in table[k]]
    if view in EXCHANGE_COLUMNS and NSE in out:
        col = EXCHANGE_COLUMNS[view]
        shown = _texts(v for row in rows for v in _named(names, row, col)) if narrow and col in names else []
        if not shown or any(v.upper() != "NSE" for v in shown):    # BSE shown, unknown or not shown: BSE too
            out = out + [BSE]
    return out


def _mentioned(sql: str, names: Sequence[str], column: str) -> bool:
    return column in names or re.search(rf"\b{re.escape(column)}\b", sql or "", re.IGNORECASE) is not None


def lines(sql: str | None, columns: Sequence, rows: Sequence[Sequence]) -> list[str]:
    """The source lines of a data answer: each view the query read (`cache.views_read`), narrowed by its rows where that is safe,
    in a fixed order (the views' order, then each view's), each line once. `columns` are names or the route's `{name, kind}`."""
    names = [c.get("name") if isinstance(c, Mapping) else c for c in columns]
    names = [n if isinstance(n, str) else "" for n in names]
    rows = [r for r in rows if isinstance(r, (list, tuple))]
    views = cache.views_read(sql)
    narrow = narrowable(sql)
    out: list[str] = []
    for view in views:
        got = _view_lines(view, names, rows, narrow)
        for col, extra in EXTRA_COLUMNS.get(view, {}).items():
            if _mentioned(sql or "", names, col):
                got = got + list(extra)
        for line in got:
            if line not in out:
                out.append(line)
    return out


def fill(answer: dict) -> dict:
    """A stored data answer with its `sources` (cached and example answers stored before K-13 have none): computed from its own
    query and rows, with no model call and no query. Any other answer gets `[]` where it has none. Returns the same dict."""
    if not isinstance(answer, dict):
        return answer
    have = answer.get("sources")
    if isinstance(have, list) and all(isinstance(s, str) for s in have) and (have or answer.get("route") != "data"):
        return answer
    if answer.get("route") == "data" and answer.get("state") == "answered":
        cols = answer.get("columns") if isinstance(answer.get("columns"), list) else []
        rows = answer.get("rows") if isinstance(answer.get("rows"), list) else []
        sql = answer.get("sql") if isinstance(answer.get("sql"), str) else None
        answer["sources"] = lines(sql, cols, rows)
    else:
        answer["sources"] = []
    return answer
