"""A data answer's source lines (K-13, #630; CLAUDE.md §1: every figure shows its source line, every NSE-derived figure says
"Source: NSE", RBI named and never linked; the four US index series carry their copyright line): every view in the guard's list
has its lines (fails closed), the rows narrow them only where that is safe, and cached and stored answers that predate the field
get theirs with no model call."""
import json
import re

import psycopg
import pytest
from conftest import dsn
from test_cmdbar_route import ROWS, SQL, FakeReader, Harness, model

from ft.ai import sqlguard
from ft.api import cmdbar as api
from ft.api import cmdbar_admin
from ft.cmdbar import cache
from ft.cmdbar import route as r
from ft.cmdbar import sources as src
from ft.cmdbar import sql as s

NSE, BSE, YAHOO, SEC = "Source: NSE", "Source: BSE", "Source: Yahoo Finance", "Source: SEC EDGAR"
FED, RBI = "Source: Federal Reserve Board", "Source: Reserve Bank of India"


def g(sql):
    """A query as the route stores it: through the guard (re-printed, wrapped in the outer LIMIT)."""
    return sqlguard.guard(sql)


# ------------------------------------------------------------------ every view (fails closed)
def test_every_view_the_guard_allows_has_source_lines():
    assert set(src.VIEW_SOURCES) == sqlguard.VIEWS
    for view, table in src.VIEW_SOURCES.items():
        assert table and all(lines and all(line.startswith(("Source: ", "© ")) for line in lines) for lines in table.values()), view


def test_no_line_holds_a_link_and_rbi_is_named_in_text_only():
    every = [line for t in src.VIEW_SOURCES.values() for lines in t.values() for line in lines]
    assert not [line for line in every if re.search(r"https?://|www\.|rbi\.org\.in|\.(?:org|gov|com|in)\b", line)]
    assert RBI in every


def test_every_series_name_and_attribution_maps_to_a_known_series():
    assert set(src.SERIES_NAMES.values()) <= set(src.SERIES)
    assert set(src.SERIES_NAMES.values()) == set(src.SERIES)                 # every published series can be named


# ------------------------------------------------------------------ the company views
def test_an_answer_about_indian_companies_says_source_nse():
    sql = g("select company, market_cap, currency from semantic.company_valuations where market = 'IN' "
            "order by market_cap desc limit 5")
    rows = [["Reliance Industries Ltd", "19000000000000", "INR"], ["HDFC Bank Ltd", "15000000000000", "INR"]]
    assert src.lines(sql, ["company", "market_cap", "currency"], rows) == [NSE]


def test_an_answer_that_doesnt_show_the_market_names_every_source_the_view_holds_nse_included():
    sql = g("select company, market_cap from semantic.company_valuations order by market_cap desc limit 5")
    got = src.lines(sql, ["company", "market_cap"], [["Reliance Industries Ltd", "19000000000000"]])
    assert NSE in got and YAHOO in got and SEC in got                             # over-inclusive, never short


def test_a_us_only_answer_whose_rows_show_it_names_yahoo_and_sec_and_not_nse():
    sql = g("select company, ticker, pe_ttm, market from semantic.company_valuations where market = 'US' order by pe_ttm")
    got = src.lines(sql, [{"name": "company", "kind": "text"}, {"name": "ticker", "kind": "text"},
                          {"name": "pe_ttm", "kind": "number"}, {"name": "market", "kind": "text"}],
                    [["Apple Inc.", "AAPL", "31.2", "US"], ["Microsoft Corp", "MSFT", "34.0", "US"]])
    assert got == [YAHOO, SEC] and NSE not in got
    fin = g("select company, revenue, currency from company_financials where currency = 'USD'")
    assert src.lines(fin, ["company", "revenue", "currency"], [["Apple Inc.", "391035000000", "USD"]]) == [SEC]


def test_both_markets_in_the_rows_name_both():
    sql = g("select market, count(*) from semantic.companies group by market")
    assert src.lines(sql, ["market", "count"], [["IN", "50"], ["US", "30"]]) == [NSE, SEC]


def test_identifiers_only_one_market_fills_narrow_too():
    sql = g("select company, nse_symbol from semantic.companies where indices like '%Nifty Bank%'")
    assert src.lines(sql, ["company", "nse_symbol"], [["HDFC Bank Ltd", "HDFCBANK"]]) == [NSE]


@pytest.mark.parametrize("sql", [
    # a subquery's figure needn't be described by the row's market column
    ("select company, market, market_cap, (select max(market_cap) from company_valuations) as top "
     "from company_valuations where market = 'US'"),
    # the view read twice: the row's market column describes one side only
    ("select a.company, a.market, b.company as other, b.market_cap from company_valuations a "
     "join company_valuations b on a.market_cap < b.market_cap where a.market = 'US'"),
])
def test_a_row_whose_columns_needn_t_describe_every_figure_is_read_as_every_market(sql):
    got = src.lines(g(sql), ["company", "market", "x", "y"], [["Apple Inc.", "US", "1", "2"]])
    assert NSE in got and SEC in got


def test_an_unknown_market_value_is_read_as_every_market():
    sql = g("select company, market from semantic.companies")
    assert NSE in src.lines(sql, ["company", "market"], [["X", "GLOBAL"]])


def test_the_model_implied_columns_add_damodarans_line():
    sql = g("select company, beta as b, currency from company_valuations where market = 'IN'")
    assert src.lines(sql, ["company", "b", "currency"], [["Reliance Industries Ltd", "1.02", "INR"]]) == [
        NSE, "Source: Aswath Damodaran, NYU Stern"]


def test_the_exchange_column_decides_bse_and_without_it_both_exchanges_are_named():
    calls = "select company, call_date{} from semantic.earnings_calls where nse_symbol = 'INFY'"
    assert src.lines(g(calls.format(", exchange")), ["company", "call_date", "exchange"], [["Infosys Ltd", "2026-10-16", "NSE"]]) == [NSE]
    assert src.lines(g(calls.format(", exchange")), ["company", "call_date", "exchange"],
                     [["Infosys Ltd", "2026-10-16", "NSE"], ["X Ltd", "2026-10-17", "BSE"]]) == [NSE, BSE]
    assert src.lines(g(calls.format("")), ["company", "call_date"], [["Infosys Ltd", "2026-10-16"]]) == [NSE, BSE]
    vc = g("select company, msme_share, source_exchange from value_chain")
    assert src.lines(vc, ["company", "msme_share", "source_exchange"], [["Infosys Ltd", "12.5", "NSE"]]) == [NSE]


def test_the_views_that_hold_one_market_always_say_nse():
    for view in ("call_guidance", "value_chain", "earnings_calls", "rate_decision_moves"):
        assert NSE in src.lines(g(f"select * from {view}"), [], []), view
    moves = src.lines(g("select subject_name, excess_pct from rate_decision_moves where bank = 'rbi'"), ["subject_name", "excess_pct"],
                      [["HDFC Bank Ltd", "0.4"]])
    assert moves == [NSE, "Source: NSE Indices, via Yahoo Finance", FED, RBI]


# ------------------------------------------------------------------ the policy views and market_daily
def test_an_rbi_answer_says_reserve_bank_of_india_with_no_link():
    got = src.lines(g(SQL), ["meeting_date", "policy_rate_change_bps"], [["2025-12-05", "-25"]])
    assert got == [FED, RBI]                                       # no bank column: both banks the view holds
    got = src.lines(g("select bank, meeting_date, policy_rate_pct from policy_meetings where bank = 'RBI'"),
                    ["bank", "meeting_date", "policy_rate_pct"], [["RBI", "2026-10-07", "5.25"]])
    assert got == [RBI] and not any("http" in line or "rbi.org.in" in line for line in got)
    assert src.lines(g("select variable, value_pct from policy_projections"), ["variable", "value_pct"], [["CPI inflation", "3.1"]]) == [RBI]


def test_the_surprise_measure_adds_the_treasury_line():
    got = src.lines(g("select bank, meeting_date, us_2y_change_bps from policy_meetings where bank = 'Fed'"),
                    ["bank", "meeting_date", "us_2y_change_bps"], [["Fed", "2026-10-28", "-3"]])
    assert got == [FED, src.TREASURY]


@pytest.mark.parametrize("column,value", [("series_name", "S&P 500"), ("series_key", "yf.^GSPC"),
                                          ("source", "Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC")])
def test_a_market_daily_answer_about_a_us_index_carries_its_copyright_line(column, value):
    sql = g(f"select {column}, date, value from market_daily where series_key = 'yf.^GSPC' order by date desc limit 1")
    assert src.lines(sql, [column, "date", "value"], [[value, "2026-11-16", "6800.1"]]) == [
        "Source: S&P Dow Jones Indices LLC, via Yahoo Finance", "© S&P Dow Jones Indices LLC"]
    nasdaq = src.lines(sql, ["series_name", "value"], [["Nasdaq-100", "21000"]])
    assert nasdaq == ["Source: Nasdaq, Inc., via Yahoo Finance", "© Nasdaq, Inc."]


def test_a_market_daily_answer_that_names_no_series_lists_every_series_source_and_each_copyright():
    got = src.lines(g("select date, value from market_daily where series_key = 'yf.^NSEI'"), ["date", "value"], [["2026-11-16", "1"]])
    assert "Source: NSE Indices, via Yahoo Finance" in got and "© S&P Dow Jones Indices LLC" in got and "© Nasdaq, Inc." in got
    india = src.lines(g("select series_name, market, value from market_daily"), ["market", "value"], [["IN", "1"]])
    assert "Source: NSE Indices, via Yahoo Finance" in india and not any(line.startswith("©") for line in india)
    assert src.lines(g("select series_name, value from market_daily"), ["series_name", "value"], [["Unknown index", "1"]]) == got


# ------------------------------------------------------------------ the route, the cache and the stored answers
def test_a_fresh_data_answer_carries_its_sources():
    out = Harness().ask()
    assert out["state"] == "answered" and out["sources"] == [FED, RBI] and set(out) == set(r.FINAL_KEYS)
    assert r.final("cant_answer")["sources"] == []


def test_a_cached_answer_without_sources_gets_them_with_no_model_call():
    h = Harness()
    first = h.ask()
    sha = cache.key("When did the RBI last cut, and by how much?")
    del h.store.entries[sha]["answer"]["sources"]                     # an entry stored before K-13
    n = len(h.fake.requests)
    again = h.ask()
    assert len(h.fake.requests) == n and h.store.logs[-1].cache_hit
    assert again["sources"] == first["sources"] == [FED, RBI] and list(again) == list(r.FINAL_KEYS)


def test_the_stored_example_answers_get_their_sources_in_the_endpoint_and_the_admin_list():
    stored = {"state": "answered", "route": "data", "summary": "2 rows", "sql": g("select company, currency from companies"),
              "columns": [{"name": "company", "kind": "text"}, {"name": "currency", "kind": "text"}],
              "rows": [["Reliance Industries Ltd", "INR"], ["HDFC Bank Ltd", "INR"]], "view": "companies"}
    shown = api.verified_examples([{"question": "q", "verified": True, "display_order": 1, "answer": stored}])
    assert shown[0]["answer"]["sources"] == [NSE] and "sources" not in stored                 # the stored row isn't changed
    listed = cmdbar_admin.admin_rows([{"id": 1, "question": "q", "answer": stored}])
    assert listed[0]["answer"]["sources"] == [NSE]
    doc = api.verified_examples([{"question": "q", "verified": True, "answer": {"state": "answered", "route": "document"}}])
    assert doc[0]["answer"]["sources"] == []


def test_fill_keeps_sources_already_there_and_never_raises_on_a_malformed_answer():
    assert src.fill({"route": "data", "state": "answered", "sources": [NSE]})["sources"] == [NSE]
    assert src.fill({"route": "data", "state": "answered", "sql": "not sql", "columns": "x", "rows": None})["sources"] == []
    assert src.fill({"route": "data", "state": "answered", "sources": [1]})["sources"] == []
    assert src.fill("x") == "x"


def test_a_query_that_reads_no_view_has_no_sources():
    assert src.lines(None, [], []) == [] and src.lines("select 1", ["?column?"], [["1"]]) == []


def test_the_rows_cells_are_read_by_column_name_whatever_their_kind():
    h = Harness(reader=FakeReader(s.Result(["bank", "meeting_date"], [("RBI", ROWS.rows[0][0])])),
                fake=model(sql="select bank, meeting_date from policy_meetings where bank = 'RBI'"))
    assert h.ask()["sources"] == [RBI]
    assert json.dumps(h.store.entries[cache.key("When did the RBI last cut, and by how much?")]["answer"]["sources"]) == json.dumps([RBI])


# ------------------------------------------------------------------ the test database: market_daily's series as seeded
@pytest.mark.db
def test_market_dailys_series_are_the_seeded_ones_with_their_attributions():
    with psycopg.connect(dsn("postgres"), autocommit=True) as db:
        got = {k: (m, a, n) for k, m, a, n in db.execute(
            """select s.key, s.market, coalesce(s.attribution, src.attribution), s.name from core.series s
               join core.source src on src.id = s.source_id where s.kind = 'observed' and s.key <> 'fred.SP500'""").fetchall()}
    assert {k: (m, a) for k, (m, a, _) in got.items()} == src.SERIES
    assert {n: k for k, (_, _, n) in got.items()} == src.SERIES_NAMES
