"""The command bar's gate runner (`ft/eval/cmdbar.py`; Spec 07 §6; K-04, #582) and `cmdbar-gates.yml`: the result-set match and
its cross-currency check, the scores and the pass rules fixed in Spec 07 §6, the price check that sends nothing, the budget stop,
the records, annotations that never hold a row or a summary, and a workflow that can reach dev only."""
import json
import re
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import yaml
from ai_fakes import FakeClaude, claude
from conftest import dsn

from ft.cmdbar import router as router_mod
from ft.cmdbar import sql as sql_mod
from ft.eval import cmdbar as ev

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "cmdbar-gates.yml"
SENTINEL = "SENTINEL-ROW-VALUE"
TODAY = date(2026, 11, 16)


# ------------------------------------------------------------------ the match (Spec 07 §6)
def m(gc, gr, mc, mr, ordered=False):
    return ev.rows_match(gc, gr, mc, mr, ordered)


def test_rows_match_as_multisets_on_the_golds_columns_with_extra_model_columns_allowed():
    gold = (["company", "pb"], [("A", Decimal("0.80")), ("B", Decimal("0.95"))])
    assert m(*gold, ["name", "pb", "price"], [("B", Decimal("0.95"), 10), ("A", Decimal("0.8"), 20)]).ok
    assert m(*gold, ["pb", "company"], [(Decimal("0.95"), "B"), (Decimal("0.80"), "A")]).mapping == (1, 0)
    assert m(*gold, ["company"], [("A",), ("B",)]).reason == "columns"
    assert m(*gold, ["company", "pb"], [("A", Decimal("0.80"))]).reason == "row_count"
    assert m(*gold, ["company", "pb"], [("A", Decimal("0.95")), ("B", Decimal("0.80"))]).reason == "rows"


def test_numbers_are_equal_at_the_golds_precision():
    gold = (["v"], [(Decimal("6.25"),)])
    assert m(*gold, ["v"], [(Decimal("6.2537"),)]).ok
    assert m(*gold, ["v"], [(6.249999,)]).ok
    assert not m(*gold, ["v"], [(Decimal("6.26"),)]).ok
    assert m(["n"], [(3,)], ["n"], [(Decimal("3.4"),)]).ok                     # a whole-number gold sets 0 places
    assert m(["n"], [(Decimal("2.5"),)], ["n"], [(Decimal("2.50"),)]).ok
    assert m(["n"], [(Decimal(0),)], ["n"], [(Decimal("-0.001"),)]).ok        # rounds to 0, not -0


def test_types_stay_apart_and_dates_and_instants_compare_as_iso():
    assert not m(["v"], [(5,)], ["v"], [("5",)]).ok
    assert not m(["v"], [(True,)], ["v"], [(1,)]).ok
    assert m(["d"], [(date(2025, 12, 5),)], ["d"], [(date(2025, 12, 5),)]).ok
    t = datetime(2025, 12, 5, 4, 30, tzinfo=UTC)
    assert m(["t"], [(t,)], ["t"], [(t.astimezone(timezone(timedelta(hours=5, minutes=30))),)]).ok
    assert not m(["d"], [(date(2025, 12, 5),)], ["d"], [("2025-12-05",)]).ok
    assert m(["v"], [(None,)], ["v"], [(None,)]).ok


def test_order_counts_only_where_the_item_says_so():
    gold = (["d"], [(1,), (2,)])
    assert m(*gold, ["d"], [(2,), (1,)]).ok
    assert not m(*gold, ["d"], [(2,), (1,)], ordered=True).ok
    assert m(*gold, ["d"], [(1,), (2,)], ordered=True).ok


def test_the_column_search_finds_the_right_pairing_when_two_columns_hold_the_same_values():
    gold = (["a", "b"], [(1, 2), (2, 1)])
    assert m(*gold, ["x", "y"], [(2, 1), (1, 2)]).ok                            # any pairing of the multiset works
    assert m(["a", "b"], [(1, 2), (2, 1), (1, 1)], ["x", "y"], [(1, 1), (1, 2), (2, 1)]).ok
    assert not m(["a", "b"], [(1, 2), (2, 1), (1, 1)], ["x", "y"], [(2, 2), (1, 2), (1, 1)]).ok


def test_empty_results_match_each_other():
    assert m(["a"], [], ["a", "b"], []).ok and not m(["a", "b"], [], ["a"], []).ok


# ------------------------------------------------------------------ the cross-currency check
CC_GOLD = (["company", "currency", "revenue"], [("Infosys", "INR", Decimal(100)), ("IBM", "USD", Decimal(60))])


def test_a_cross_currency_answer_that_orders_by_a_figure_or_adds_a_number_fails():
    cols, rows = ["company", "currency", "revenue"], [("IBM", "USD", Decimal(60)), ("Infosys", "INR", Decimal(100))]
    mapping = m(*CC_GOLD, cols, rows).mapping
    assert ev.money_comparison("select company, currency, revenue from t order by revenue desc", cols, rows, mapping) \
        == "orders_by_figure"
    assert ev.money_comparison("select company, currency, revenue from t order by 3", cols, rows, mapping) == "orders_by_figure"
    assert ev.money_comparison("select company, currency, revenue from t order by revenue / 2", cols, rows, mapping) \
        == "orders_by_figure"
    assert ev.money_comparison("select company, currency, revenue from t order by company", cols, rows, mapping) is None
    assert ev.money_comparison("select company, currency, revenue from t", cols, rows, mapping) is None
    cols2, rows2 = cols + ["ratio"], [r + (Decimal("1.6"),) for r in rows]
    assert ev.money_comparison("select 1", cols2, rows2, m(*CC_GOLD, cols2, rows2).mapping) == "extra_numeric_column"
    cols3, rows3 = cols + ["period_end"], [r + (date(2026, 3, 31),) for r in rows]
    assert ev.money_comparison("select 1", cols3, rows3, m(*CC_GOLD, cols3, rows3).mapping) is None


def test_the_cross_currency_check_fails_only_money_compared_across_currencies():
    """Anand's 1B (17 Nov 2026, #587): currency-neutral figures may be ranked across markets; money may not."""
    cc = ev.money_comparison
    one = (["c"], [], ())
    # currency-neutral rankings and within-market ranks pass (run 45's x02, x05, x06 and x04 shapes)
    assert cc("select company, currency, pe_ttm from company_valuations where nse_symbol = 'HDFCBANK' or ticker = 'JPM' "
              "order by pe_ttm desc", *one) is None
    assert cc("select company, ebit_margin from company_valuations order by ebit_margin desc limit 5", *one) is None
    assert cc("select company, currency, market_cap, rk from (select company, market, currency, market_cap, row_number() "
              "over (partition by market order by market_cap desc) as rk from company_valuations) t where rk <= 5 "
              "order by market, rk", ["company", "currency", "market_cap", "rk"], [("a", "INR", Decimal(5), 1)], (0, 1, 2)) is None
    assert cc("select company, currency, market_cap from company_valuations order by market, market_cap desc", *one) is None
    assert cc("select currency, sum(market_cap) from company_valuations group by currency", *one) is None
    assert cc("select company, revenue from company_financials where nse_symbol = 'TCS' order by revenue desc limit 1",
              *one) is None
    # money ranked, summed or compared across currencies fails
    assert cc("select company, mc from (select company, market_cap as mc from company_valuations) t order by mc", *one) \
        == "orders_by_figure"
    assert cc("select series_name, value from market_daily where series_key in ('yf.^NSEI', 'yf.^DJI') order by value desc",
              *one) == "orders_by_figure"
    assert cc("select company, rank() over (order by market_cap desc) as r from company_valuations", *one) == "orders_by_figure"
    assert cc("select sum(market_cap) from company_valuations", *one) == "sums_money"
    assert cc("select max(market_cap) from company_valuations", *one) == "sums_money"
    assert cc("select company, sum(revenue) over () as s from company_financials", *one) == "sums_money"
    assert cc("select a.company, a.revenue - b.revenue as diff from company_financials a, company_financials b",
              ["company", "diff"], [("x", Decimal(1))], (0,)) == "extra_numeric_column"
    assert cc("select company, market_cap / pe_ttm as e from company_valuations", ["company", "e"], [("x", Decimal(1))],
              (0,)) is None                                                    # one row's own ratio


# Round 2's sql-r2-x07 answer as the model wrote it (dev run 50, 17 Nov 2026): its rows matched the gold, and it failed only
# because its 12-month percent change, computed from each index's own two levels, read as money.
R2_X07 = ("WITH latest AS (SELECT series_name, max(date) AS latest_date FROM market_daily WHERE series_name IN ('Sensex', "
          "'Nasdaq Composite') AND value IS NOT NULL GROUP BY series_name), ago AS (SELECT l.series_name, l.latest_date, "
          "max(m.date) AS ago_date FROM latest AS l INNER JOIN market_daily AS m ON m.series_name = l.series_name AND m.date <= "
          "l.latest_date - CAST('12 months' AS interval) AND m.value IS NOT NULL GROUP BY l.series_name, l.latest_date) "
          "SELECT l.series_name, m1.market, m1.currency, a.ago_date, m0.value AS ago_value, l.latest_date, m1.value AS "
          "latest_value, round((m1.value / m0.value - 1) * 100, 2) AS change_pct_12m FROM latest AS l INNER JOIN ago AS a ON "
          "a.series_name = l.series_name INNER JOIN market_daily AS m1 ON m1.series_name = l.series_name AND m1.date = "
          "l.latest_date INNER JOIN market_daily AS m0 ON m0.series_name = l.series_name AND m0.date = a.ago_date ORDER BY "
          "change_pct_12m DESC")


def test_a_ratio_of_one_companys_or_one_series_own_money_is_currency_neutral():
    """Anand, 17 Nov 2026, 14:13 IST (#587, option A point 1): the same company or series on both sides of a ratio, by the
    query's join keys (or one row's own columns), makes it currency-neutral; a ratio across companies or series is still money."""
    cc = ev.money_comparison
    one = (["c"], [], ())
    # round 2's x07: ranked by a percent change of each index's own levels; its two levels shown beside, unmatched
    cols = ["series_name", "market", "currency", "ago_date", "ago_value", "latest_date", "latest_value", "change_pct_12m"]
    row = ("Sensex", "IN", "INR", date(2025, 11, 7), Decimal(1), date(2026, 11, 9), Decimal(2), Decimal("3.5"))
    assert cc(R2_X07, cols, [row], (0, 7)) is None
    # one row's own ratio, ranked: a margin
    assert cc("select company, pat / revenue as margin from company_financials order by margin desc", *one) is None
    assert cc("select company, round(100 * pat / revenue, 1) from company_financials order by 2 desc", *one) is None
    assert cc("select avg(pat / revenue) from company_financials", *one) is None
    # a window over one series' own rows (partitioned by its key), then ranked
    assert cc("with d as (select series_name, date, value / lag(value) over (partition by series_key order by date) - 1 as "
              "chg from market_daily where series_key in ('yf.^NSEI', 'yf.^GSPC')) select series_name, min(chg) as worst "
              "from d group by series_name order by worst", *one) is None
    # two views tied by an entity key, qualified or through USING with unqualified columns
    assert cc("select v.company, f.revenue / v.market_cap as r from company_valuations v join company_financials f "
              "on f.nse_symbol = v.nse_symbol order by r desc", *one) is None
    assert cc("select company, revenue / market_cap as r from company_valuations join company_financials using (nse_symbol) "
              "order by r desc", *one) is None
    # still money: a ratio across companies or series, or one whose sides can't be placed
    assert cc("select a.company, a.revenue / b.revenue as r from company_financials a join company_financials b "
              "on a.market = b.market order by r", *one) == "orders_by_figure"
    assert cc("with d as (select series_name, value / lag(value) over (order by date) - 1 as chg from market_daily) "
              "select series_name, min(chg) as worst from d group by series_name order by worst", *one) == "orders_by_figure"
    assert cc("select revenue / market_cap as r from company_valuations, company_financials order by r", *one) \
        == "orders_by_figure"
    assert cc("select company, market_cap / (select max(market_cap) from company_valuations) as share "
              "from company_valuations order by share", *one) is not None
    assert cc("select a.date, a.value / b.value as r from market_daily a join market_daily b on a.date = b.date "
              "where a.series_key = 'yf.^NSEI' and b.series_key = 'yf.^GSPC'", ["date", "r"], [(date(2026, 1, 2), Decimal(4))],
              (0,)) == "extra_numeric_column"
    # and a sum of money across currencies still fails even inside a ratio
    assert cc("select sum(pat) / sum(revenue) as margin from company_financials", *one) == "sums_money"


def test_every_cross_currency_gold_passes_the_cross_currency_check():
    for it in ev.load_items("cmdbar_sql", "dev") + ev.load_items("cmdbar_sql", "test"):
        if it["cross_currency"] and it.get("gold_sql"):
            assert ev.money_comparison(it["gold_sql"], ["c"], [], ()) is None, it["id"]


def _ans(state="answered", columns=(), rows=(), sql=None, models=("claude-haiku-5-5",), error=None):
    return sql_mod.DataAnswer(state, sql=sql, columns=list(columns), rows=list(rows), error_class=error,
                              summary="x" if state == "answered" else None, summary_grounded=state == "answered",
                              attempts=[sql_mod.Attempt(x, None, Decimal("0.001")) for x in models])


def test_score_sql_item():
    cc = {"id": "sql-x", "family": "cross_currency", "answerable": True, "cross_currency": True, "ordered": False}
    gold = sql_mod.Result(*CC_GOLD)
    guarded = "SELECT * FROM (SELECT company, currency, revenue FROM company_financials ORDER BY revenue DESC) AS q LIMIT 200"
    r = ev.score_sql_item(cc, gold, None, _ans(columns=CC_GOLD[0], rows=CC_GOLD[1], sql=guarded))
    assert (r.correct, r.reason) == (False, "orders_by_figure")
    plain = guarded.replace(" ORDER BY revenue DESC", "")
    assert ev.score_sql_item(cc, gold, None, _ans(columns=CC_GOLD[0], rows=CC_GOLD[1], sql=plain)).correct
    unans = {"id": "sql-u", "family": "equity", "answerable": False}
    assert ev.score_sql_item(unans, None, None, _ans("unanswerable")).correct
    assert ev.score_sql_item(unans, None, None, _ans(columns=["a"], rows=[(1,)], sql=plain)).reason == "answered_unanswerable"
    r = ev.score_sql_item({**cc, "id": "sql-g"}, None, "database: UndefinedColumn", _ans(columns=["a"], rows=[(1,)], sql=plain))
    assert r.correct is None and r.reason == "gold_error: database: UndefinedColumn"
    r = ev.score_sql_item(cc, gold, None, _ans("cant_answer", error="guard: relation not allowed", models=("h", "s")))
    assert (r.correct, r.reason, r.models) == (False, "cant_answer: guard: relation not allowed", ["h", "s"])


def test_score_router_item_and_its_filters():
    item = {"id": "route-x", "route": "document", "filters": {"banks": ["rbi"], "date_from": "2020-03-01"}}
    got = router_mod.Route("document", None, router_mod.Filters(("rbi",), (), date(2020, 3, 1), date(2020, 3, 31)))
    r = ev.score_router_item(item, got, None, Decimal("0.0001"), 5)
    assert r.correct and r.filters_agree                                       # only the stated filters are compared
    got2 = router_mod.Route("document", None, router_mod.Filters(("fed",), (), date(2020, 3, 1), None))
    assert ev.score_router_item(item, got2, None, Decimal(0), 5).filters_agree is False
    r = ev.score_router_item({"id": "route-u", "route": "unsupported"}, None, "CallError", Decimal(0), 5)
    assert (r.correct, r.reason) == (False, "error: CallError")


# ------------------------------------------------------------------ the pass rules (Spec 07 §6)
def _sql_results(n_ok, n_bad, cc_ok, cc_bad):
    out = []
    for i in range(n_ok + n_bad):
        out.append(ev.ItemResult(f"s{i}", i < n_ok, "match" if i < n_ok else "rows", "equity"))
    for i in range(cc_ok + cc_bad):
        out.append(ev.ItemResult(f"c{i}", i < cc_ok, "match", "cross_currency", cross_currency=True))
    return out


def test_the_sql_rule_needs_85_percent_and_every_cross_currency_item():
    assert ev.sql_rule_met(ev.sql_metrics(_sql_results(43, 7, 10, 0)))         # 53 of 60 = 88%
    assert not ev.sql_rule_met(ev.sql_metrics(_sql_results(50, 0, 9, 1)))     # 59 of 60, one cross-currency miss
    assert not ev.sql_rule_met(ev.sql_metrics(_sql_results(40, 10, 10, 0)))   # 50 of 60 = 83%
    unscored = _sql_results(50, 0, 10, 0) + [ev.ItemResult("g", None, "gold_error: x")]
    assert not ev.sql_rule_met(ev.sql_metrics(unscored))


def test_the_router_rule_needs_90_percent_and_no_unanswerable_item_answered():
    def results(ok, bad, answered_unsupported):
        out = [ev.ItemResult(f"r{i}", True, "route_match", gold_route="data", route="data") for i in range(ok)]
        out += [ev.ItemResult(f"b{i}", False, "route_differs", gold_route="page", route="data") for i in range(bad)]
        out += [ev.ItemResult(f"u{i}", False, "route_differs", gold_route="unsupported", route="data")
                for i in range(answered_unsupported)]
        return out
    assert ev.router_rule_met(ev.router_metrics(results(36, 4, 0)))
    assert not ev.router_rule_met(ev.router_metrics(results(36, 3, 1)))
    assert not ev.router_rule_met(ev.router_metrics(results(35, 5, 0)))


# ------------------------------------------------------------------ the CLI, with fakes
class FakeReader:
    """Every query (gold or model) returns the same row, holding a sentinel that must never reach the log."""

    def __init__(self):
        self.ran, self.closed = [], False

    def run(self, guarded):
        self.ran.append(guarded)
        return sql_mod.Result(["company"], [(SENTINEL,)])

    def describe(self, views=None):
        from ft.ai import sqlguard
        return [sql_mod.ViewDoc(v, f"{v} rows.", (("company", "text", "Name."),)) for v in sorted(sqlguard.VIEWS)], ["Banks"]

    def close(self):
        self.closed = True


class FakeWriter:
    def __init__(self):
        self.runs, self.results = [], []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params):
        if "ops.eval_run" in sql:
            self.runs.append(params)
            return type("C", (), {"fetchone": lambda self: (77,)})()
        self.results.append(params)


def gate_model():
    def answer(body):
        first = next(iter(body["output_config"]["format"]["schema"]["properties"]))
        if first == "summary":
            return {"summary": f"{SENTINEL} summary"}
        if first == "route":
            return {"route": "data", "page_slug": None,
                    "filters": {"banks": [], "doc_types": [], "date_from": None, "date_to": None}}
        return {"answerable": True, "sql": "select company from companies", "explanation": "Companies."}
    return FakeClaude(answer=answer)


def run_main(argv, **kw):
    lines = []
    env = {"GITHUB_SHA": "f" * 40, **kw.pop("env", {})}
    code = ev.main(argv, env=env, out=lines.append, today=lambda: TODAY, **kw)
    return code, lines


def test_the_dry_run_prices_and_runs_the_golds_but_sends_nothing():
    fake, rd = FakeClaude(), FakeReader()
    code, lines = run_main(["--set", "cmdbar_sql", "--split", "dev"], client=claude(fake), reader=rd)
    assert code == 0 and fake.requests == []
    n_golds = sum(1 for i in ev.load_items("cmdbar_sql", "dev") if i["answerable"])
    assert len(rd.ran) == n_golds and all(q.endswith("LIMIT 200") for q in rd.ran)
    text = "\n".join(lines)
    assert '"views_described":11' in text and '"worst_case"' in text and "nothing sent" in text
    assert SENTINEL not in text


def test_a_sent_dev_run_records_every_item_never_passes_and_logs_no_rows():
    fake, rd, wr = gate_model(), FakeReader(), FakeWriter()
    code, lines = run_main(["--set", "cmdbar_sql", "--split", "dev", "--send"], client=claude(fake), reader=rd, writer=wr)
    items = ev.load_items("cmdbar_sql", "dev")
    assert code == 0 and len(wr.results) == len(items) and len(wr.runs) == 1
    eval_set, set_version, model, prompt_version, n, metrics, passed, notes = wr.runs[0]
    assert (eval_set, set_version, model, prompt_version, n, passed) == ("cmdbar_sql", "f" * 40, "claude-haiku-5-5", "v5",
                                                                         len(items), False)
    assert metrics.obj["split"] == "dev" and metrics.obj["complete"] and "not the gate" in notes
    assert metrics.obj["correct"] == len(items) - sum(1 for i in items if not i["answerable"])
    text = "\n".join(lines)
    assert SENTINEL not in text and "select company" not in text                # no row, summary or SQL in the log
    listed = [x for x in lines if x.startswith("::notice title=cmdbar_sql dev items::")]
    assert len(listed) == 1 and [i["id"] for i in json.loads(listed[0].split("::", 2)[2])] == [i["id"] for i in items]
    assert len([x for x in lines if x.startswith("::notice")]) <= 4                # GitHub keeps 10 notices a step
    stored = [p[3].obj for p in wr.results]
    assert all("summary" in s for s in stored if s["state"] == "answered")       # the private record keeps the line


def test_the_budget_stop_ends_the_run_before_an_item_could_pass_it():
    fake, wr = gate_model(), FakeWriter()
    code, lines = run_main(["--set", "cmdbar_sql", "--split", "dev", "--send", "--max-cost", "0.01"],
                           client=claude(fake), reader=FakeReader(), writer=wr)
    assert code == 0 and fake.requests == [] and len(wr.results) == 0
    assert wr.runs[0][5].obj["stopped_by_budget"] and not wr.runs[0][6]
    assert any("budget stop" in x for x in lines)


def test_a_router_run_needs_no_database_reader_and_scores_routes():
    fake, wr = gate_model(), FakeWriter()
    code, _ = run_main(["--set", "cmdbar_router", "--split", "dev", "--send", "--items", "route-dev-d01,route-dev-u01"],
                           client=claude(fake), writer=wr)
    assert code == 0 and len(fake.requests) == 2
    metrics = wr.runs[0][5].obj
    assert (metrics["n"], metrics["correct"], metrics["unanswerable"]["answered"]) == (2, 1, 1)


def test_the_cli_refuses_missing_secrets_and_unknown_items():
    with pytest.raises(SystemExit):
        run_main(["--set", "cmdbar_sql", "--split", "dev"])                     # no SQL_READER_DATABASE_URL
    with pytest.raises(SystemExit):
        run_main(["--set", "cmdbar_router", "--split", "dev", "--send"], writer=FakeWriter())   # no ANTHROPIC_API_KEY
    with pytest.raises(SystemExit):
        run_main(["--set", "cmdbar_router", "--split", "dev", "--items", "route-d01"])           # a test item, not dev's


def test_the_price_check_is_dearer_in_its_worst_case_than_its_estimate():
    prompt = sql_mod.sql_prompt(sql_mod.render_views(FakeReader().describe()[0]))
    items = ev.load_items("cmdbar_sql", "dev")
    p = ev.price_run("cmdbar_sql", items, prompt, TODAY)
    assert Decimal(0) < p["estimate"] < p["worst_case"] < Decimal("0.20") * len(items)      # under 20 cents an item at worst
    assert len(p["per_item_worst"]) == len(items) == 122                                   # 12, plus rounds 1's and 2's 55 each (#587)


@pytest.mark.db
def test_the_records_land_in_ops_eval_run_and_eval_result_as_job_writer():
    with psycopg.connect(dsn("job_writer")) as conn:
        conn.execute("select 1")                                               # an open transaction: record()'s is a savepoint
        r = ev.ItemResult("sql-dev-m01", True, "match", "module1", state="answered", sql="select 1", model_rows=1,
                          gold_rows=1, models=["claude-haiku-5-5"], cost_usd=Decimal("0.0012"), ms=900)
        run_id = ev.record(conn, "cmdbar_sql", "dev", "abc", "claude-haiku-5-5", "v1", {"n": 1}, False, "test", [r],
                           lambda x: {"answerable": True, "rows": 1})
        got = conn.execute("select item_key, correct, latency_ms, cost_usd, predicted->>'reason' from ops.eval_result "
                           "where eval_run_id = %s", (run_id,)).fetchall()
        assert got == [("sql-dev-m01", True, 900, Decimal("0.001200"), "match")]
        conn.rollback()


# ------------------------------------------------------------------ the workflow
def _wf():
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_workflow_is_manual_with_no_target_input():
    wf = _wf()
    trigger = wf.get("on", wf.get(True))                                       # YAML 1.1 reads a bare `on` as True
    assert list(trigger) == ["workflow_dispatch"]
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"set", "split", "items", "send", "max_cost", "query_model"}
    qm = trigger["workflow_dispatch"]["inputs"]["query_model"]                 # K-10 (#587): the route's model by default
    assert qm["options"] == list(ev.QUERY_MODELS) and qm["default"] == "voyage-4"
    assert trigger["workflow_dispatch"]["inputs"]["send"]["default"] is False
    assert wf["permissions"] == {"contents": "read"} and wf["concurrency"]["cancel-in-progress"] is False


def test_the_workflow_reaches_dev_only_and_the_model_only_when_it_sends():
    text = WORKFLOW.read_text()
    assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) == {"DEV_SQL_READER_DATABASE_URL", "DEV_JOB_DATABASE_URL",
                                                                   "ANTHROPIC_API_KEY", "VOYAGE_API_KEY"}   # Voyage: K-06
    env = _wf()["jobs"]["run"]["steps"][-1]["env"]
    assert env["ANTHROPIC_API_KEY"] == "${{ inputs.send && secrets.ANTHROPIC_API_KEY || '' }}"
    assert env["VOYAGE_API_KEY"] == "${{ inputs.send && secrets.VOYAGE_API_KEY || '' }}"
    assert "${{" not in _wf()["jobs"]["run"]["steps"][-1]["run"]               # inputs reach the script through env only
