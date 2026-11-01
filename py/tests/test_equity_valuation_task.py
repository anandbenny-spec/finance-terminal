"""equity.valuation and equity.snapshot (job 2A-15; Spec 02A §3.2, §5.7, §5.9): the screener row's columns, picks, notes and
rules, the round-trip check, the quarter-of-companies rule, and both tasks through the runner against the test database as
job_writer (runs and grid written, a re-run changing nothing, the grid replaced and never appended, a failing blocking check
rolling the load back).

The valuation task's inputs are built from the engine on the worked example's synthetic company (test_equity_valuation):
the assemble step has its own tests (test_equity_assemble, test_equity_reverse), so the task test replaces only `fetch`."""
import itertools
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from banned_words import banned_words_in
from conftest import dsn
from test_equity_valuation import SETTINGS, company, market

from ft.equity import snapshot as sn
from ft.equity.valuation import Input, default_run, per_share, prepare, reverse_dcf, scenario_grid
from ft.jobs.runner import run
from ft.jobs.tasks import equity_valuation as ev
from ft.jobs.tasks.equity_snapshot import EquitySnapshot
from ft.jobs.tasks.equity_valuation import (
    CompanyRuns,
    EquityValuation,
    Fetched,
    from_result,
    from_run,
    round_trip,
)

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
D = date
DAY = D(2026, 10, 30)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_n = itertools.count(1)
CR = Decimal(10_000_000)


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        cur = c.execute(sql, a)
        return cur.fetchall() if cur.description else []


# ---------------------------------------------------------------------------------------------- the snapshot row
def row(key, basis, kind, end, value=None, held=None, **inputs):
    return (key, basis, kind, end), (value, held, inputs)


def rows(*items):
    return dict(items)


KINDS = {"ebit_margin": None, "beta_bottom_up": "derived", "roa": "annualised", "debt_to_equity": None}


def solved(result="0.08", delivered="0.10"):
    return sn.Run("solved", None, Decimal(result), Decimal(delivered), None)


def test_a_row_takes_the_market_figures_at_the_date_and_the_latest_period_for_the_rest():
    metrics = rows(
        row("market_cap", "none", "market", DAY, Decimal(500), price={"close": "125.50", "trade_date": "2026-10-30"}),
        row("market_cap", "none", "market", D(2026, 10, 23), Decimal(400)),                  # an older price date: not used
        row("shares_today", "none", "market", DAY, Decimal(4)),
        row("pb", "consolidated", "market", DAY, Decimal("3.2")),
        row("beta_bottom_up", "none", "market", DAY, Decimal("1.1")),
        row("roe", "consolidated", "year", D(2025, 3, 31), Decimal("0.14")),
        row("roe", "consolidated", "year", D(2026, 3, 31), Decimal("0.16")),                # the latest year wins
        row("ebit_margin", "consolidated", "ttm", D(2026, 6, 30), Decimal("0.21")),
        row("debt_to_equity", "standalone", "instant", D(2026, 6, 30), Decimal("0.5")),
        row("debt_to_equity", "consolidated", "instant", D(2026, 6, 30), Decimal("0.7")),   # same period: consolidated wins
    )
    r = sn.build("operating", DAY, metrics, KINDS, Decimal("52.5"), solved(), None)
    v = r.values
    assert v["price"] == Decimal("125.50") and v["price_date"] == DAY
    assert (v["market_cap"], v["shares"], v["pb"], v["beta"]) == (Decimal(500), Decimal(4), Decimal("3.2"), Decimal("1.1"))
    assert (v["roe"], v["ebit_margin"], v["debt_to_equity"]) == (Decimal("0.16"), Decimal("0.21"), Decimal("0.7"))
    assert v["promoter_pct"] == Decimal("52.5")
    assert (v["implied_growth"], v["delivered_growth"]) == (Decimal("0.08"), Decimal("0.10"))
    assert r.notes["beta"] == {"label": "derived"}                                # the catalogue's label
    assert r.notes["implied_growth"] == {"label": sn.MODEL_LABEL}


def test_a_held_figure_is_null_with_its_reason_and_the_na_prefix_removed():
    metrics = rows(row("market_cap", "none", "market", DAY, Decimal(500), price={"close": "10", "trade_date": "2026-10-30"}),
                   row("pe_ttm", "consolidated", "market", DAY, None, "Profit over the last four quarters isn't positive."),
                   row("ev_ebit_ttm", "consolidated", "market", DAY, None, "N/A: a lender has no EBIT."))
    r = sn.build("operating", DAY, metrics, KINDS, None, solved(), None)
    assert r.values["pe_ttm"] is None and r.notes["pe_ttm"]["reason"] == "Profit over the last four quarters isn't positive."
    assert r.values["ev_ebit"] is None and r.notes["ev_ebit"]["reason"] == "a lender has no EBIT."
    assert r.values["promoter_pct"] is None and r.notes["promoter_pct"]["reason"] == sn.NO_SHAREHOLDING
    assert r.values["roe"] is None and r.notes["roe"]["reason"] == "Roe isn't stored."      # a missing row has a reason too


def test_the_stored_label_of_a_figure_joins_the_catalogue_label():
    metrics = rows(row("market_cap", "none", "market", DAY, Decimal(5), price={"close": "1", "trade_date": "2026-10-30"}),
                   row("debt_to_equity", "consolidated", "instant", D(2026, 6, 30), Decimal("0.7"),
                       label="excluding lease liabilities (not in results filings)"))
    r = sn.build("operating", DAY, metrics, {"debt_to_equity": "derived"}, None, solved(), None)
    assert r.notes["debt_to_equity"]["label"] == "derived; excluding lease liabilities (not in results filings)"


def test_each_family_carries_only_its_own_model_columns_with_a_reason_for_the_other():
    base = rows(row("market_cap", "none", "market", DAY, Decimal(5), price={"close": "1", "trade_date": "2026-10-30"}))
    op = sn.build("operating", DAY, base, KINDS, None, solved(), None)
    assert op.values["implied_roe"] is None and op.notes["implied_roe"]["reason"] == sn.NOT_LENDER
    lender = sn.build("lender", DAY, base, KINDS, None, None, solved("0.15", "0.13"))
    assert lender.values["implied_roe"] == Decimal("0.15") and lender.values["delivered_roe"] == Decimal("0.13")
    assert lender.values["implied_growth"] is None and lender.notes["implied_growth"]["reason"] == sn.NOT_OPERATING
    na = sn.Run("not_applicable", "Book value doesn't capture an insurer's value; embedded value is v2.", None, None,
                "The delivered figure is held.")
    insurer = sn.build("insurer", DAY, base, KINDS, None, na, None)
    assert insurer.values["implied_growth"] is None
    assert insurer.notes["implied_growth"]["reason"] == na.reason and insurer.notes["delivered_growth"]["reason"]
    unrun = sn.build("operating", DAY, base, KINDS, None, None, None)
    assert unrun.notes["implied_growth"]["reason"] == sn.NO_RUN


def test_a_no_solution_run_stores_its_sentence_and_the_delivered_figure_stays():
    base = rows(row("market_cap", "none", "market", DAY, Decimal(5), price={"close": "1", "trade_date": "2026-10-30"}))
    run_ = sn.Run("no_solution", "The price implies growth above 50% a year.", None, Decimal("0.1"), None)
    r = sn.build("operating", DAY, base, KINDS, None, run_, None)
    assert r.values["implied_growth"] is None and r.values["delivered_growth"] == Decimal("0.1")
    assert r.notes["implied_growth"] == {"label": sn.MODEL_LABEL, "reason": "The price implies growth above 50% a year."}


def test_every_note_the_snapshot_writes_passes_the_banned_words_scan():
    text = [sn.NO_PRICE, sn.NO_SHAREHOLDING, sn.NOT_LENDER, sn.NOT_OPERATING, sn.NO_RUN, sn.MODEL_LABEL]
    assert not [w for t in text for w in banned_words_in(t)]


def test_the_snapshot_columns_are_the_tables_columns():
    cols = {r[0] for r in q("""select column_name from information_schema.columns
                                where table_schema = 'equity' and table_name = 'screener_snapshot'""")}
    assert set(sn.COLUMNS) | {"snapshot_date", "entity_id", "valuation_family", "notes"} == cols
    assert set(sn.METRIC_COLUMNS) | {"price", "price_date", "promoter_pct", "implied_growth", "delivered_growth",
                                     "implied_roe", "delivered_roe"} == set(sn.COLUMNS)


# ---------------------------------------------------------------------------------------------- the valuation checks
def synthetic(slug: str, eid: int, target_growth: str | None = "0.08", *, price=None, family="operating") -> CompanyRuns:
    """Runs from the engine on the worked example's company, priced so that its reverse DCF solves at the target growth
    (or at `price`, e.g. a price above the range)."""
    c, m = company(key=slug, family=family), market()
    if price is None:
        d, _, _ = prepare(c, m, SETTINGS)
        price = per_share(d, Decimal(target_growth)).quantize(Decimal("0.01"))
    p = Input.of(Decimal(price), "money", DAY, "test")
    delivered = Input.of(Decimal("0.10"), "fraction", DAY, "test", years=5)
    rev = reverse_dcf(c, m, SETTINGS, p, delivered)
    rev.inputs["delivered_years"] = 5
    co = CompanyRuns(eid, slug, family, DAY, [from_run(rev)], company=c)
    if family == "operating":
        co.runs.append(from_result(default_run(c, m, SETTINGS), p.value))
        co.grid = scenario_grid(c, m, SETTINGS).cells
    return co


def test_the_engines_reverse_dcf_on_the_worked_example_solves_and_round_trips():
    co = synthetic("a", 1)
    rev = co.runs[0]
    assert rev.status == "solved" and abs(rev.result - Decimal("0.08")) <= Decimal("0.0001")
    assert round_trip(co, market(), SETTINGS, rev) is None
    assert len(co.grid) == 15 * 5 * 5 and co.runs[1].kind == "dcf_default" and co.runs[1].status == "solved"


def test_a_run_whose_implied_growth_does_not_match_its_price_fails_the_round_trip():
    co = synthetic("a", 1)
    rev = co.runs[0]
    rev.result = Decimal("0.09")                                              # a result the price doesn't give
    why = round_trip(co, market(), SETTINGS, rev)
    assert why is not None


def test_more_than_a_quarter_of_the_operating_companies_without_a_solution_fails_the_check():
    t = EquityValuation()
    fine = [synthetic(f"s{k}", k) for k in range(3)]
    high = synthetic("x", 9, price=Decimal(10) ** 9)                           # far above the range: no solution
    assert high.runs[0].status == "no_solution" and high.runs[0].reason
    f = Fetched(DAY, market(), fine + [high])
    got = {c.name: c for c in t.checks(None, f, 1)}
    assert got["quarter_rule"].passed and got["lead_runs"].passed and got["round_trip"].passed    # 1 of 4: allowed
    f = Fetched(DAY, market(), fine[:2] + [high, synthetic("y", 10, price=Decimal(10) ** 9)])
    got = {c.name: c for c in t.checks(None, f, 1)}
    assert not got["quarter_rule"].passed and "2 of 4" in got["quarter_rule"].detail


def test_a_company_without_its_lead_run_fails_the_lead_run_check():
    t = EquityValuation()
    bare = CompanyRuns(5, "bare", "operating", DAY)
    got = {c.name: c for c in t.checks(None, Fetched(DAY, market(), [synthetic("a", 1), bare]), 1)}
    assert not got["lead_runs"].passed and "bare" in got["lead_runs"].detail


def test_every_no_solution_sentence_the_engine_writes_passes_the_banned_words_scan():
    runs = [synthetic("a", 1, price=Decimal(10) ** 9).runs[0], synthetic("b", 2, price="0.01").runs[0]]
    assert {r.status for r in runs} == {"no_solution"}
    assert not [w for r in runs for w in banned_words_in(r.reason)]


# ---------------------------------------------------------------------------------------------- through the runner
def wipe():
    co = "(select id from core.entity where slug like 'vt%x%-%')"
    run_ids = f"(select id from equity.valuation_run where entity_id in {co})"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f"delete from equity.valuation_scenario where run_id in {run_ids}")
        c.execute(f"delete from equity.valuation_run where entity_id in {co}")
        c.execute(f"delete from equity.screener_snapshot where entity_id in {co}")
        c.execute(f"delete from equity.metric_value where entity_id in {co}")
        c.execute(f"delete from equity.price_daily where entity_id in {co}")
        c.execute(f"delete from equity.company where entity_id in {co}")
        c.execute("delete from core.entity where slug like 'vt%x%-%'")
        c.execute("delete from core.raw_file where object_key like 'test/vt%x%-prices'")



@pytest.fixture(scope="module", autouse=True)
def clean_up():
    wipe()
    yield
    wipe()


class World:
    def __init__(self, key: str):
        self.key = key
        self.p = f"vt{next(_n)}x{datetime.now().strftime('%H%M%S%f')}"
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'valuation test', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (key,))
            job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('weekly', 'cli', 'local') "
                            "returning id").fetchone()[0]
            self.tr = c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, %s, 'success') "
                                "returning id", (job, key)).fetchone()[0]
            self.raw = c.execute(
                """insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at, task_run_id)
                   values (%s, (select id from core.source where key = 'nse'), sha256(%s::bytea), 0, 'text/csv', now(), %s)
                   returning id""", (f"test/{self.p}-prices", self.p, self.tr)).fetchone()[0]

    def company(self, name: str, family="operating") -> tuple[int, str]:
        slug = f"{self.p}-{name}"
        with psycopg.connect(ADMIN, autocommit=True) as c:
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', true) returning id""", (slug, name)).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, nse_symbol, price_exchange, valuation_family)
                         values (%s, %s, 'NSE', %s)""", (eid, name.upper(), family))
        return eid, slug

    def price(self, eid: int, day: date, close="100"):
        q("""insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close, prev_close,
                                            raw_file_id, task_run_id)
             values (%s, %s, 'NSE', 'EQ', %s, %s, %s, %s, %s, %s, %s)""", eid, day, close, close, close, close, close,
          self.raw, self.tr)

    def metric(self, eid, key, basis, kind, end, value=None, held=None, inputs=None):
        q("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value, held_reason,
                                              inputs) values (%s, %s, %s, %s, %s, %s, %s, %s)
             on conflict (entity_id, metric_key, basis, period_kind, period_end) do update
                set value = excluded.value, held_reason = excluded.held_reason, inputs = excluded.inputs""",
          eid, key, basis, kind, end, value, held, json.dumps(inputs) if inputs else None)


def go(task, key, now=NOW):
    return run(JOB, "weekly", [task], force=True, clock=lambda: now)


def last_run(key):
    return q("select status, rows_written, checks from ops.task_run where task_key = %s order by id desc limit 1", key)[0]


def check(checks, name):
    return next(c for c in checks if c["name"] == name)


def valuation_task(key: str, build):
    """EquityValuation with `fetch` replaced: `build(task)` returns the Fetched (the assemble step has its own tests)."""
    class T(EquityValuation):
        pass
    T.key = key

    def fetch(self, ctx):
        return build(self)
    T.fetch = fetch
    t = T()
    t.workflows = ("weekly",)
    return t


def test_a_full_pass_writes_the_runs_and_the_grid_and_a_rerun_changes_nothing():
    key = "test.valuation_a"
    w = World(key)
    ids = [w.company(n)[0] for n in ("a", "b")]
    lender_id = w.company("l", "lender")[0]
    cos = [synthetic(f"s{k}", e) for k, e in enumerate(ids)]
    t = valuation_task(key, lambda self: Fetched(DAY, market(), cos))
    go(t, key)
    status, rows_written, checks = last_run(key)
    assert status == "success" and rows_written == 2 * 2 + 2 * 375
    assert q("select kind, status, as_of from equity.valuation_run where entity_id = any(%s) order by 1", ids)[0][2] == DAY
    assert {r[0] for r in q("select distinct kind from equity.valuation_run where entity_id = any(%s)", ids)} == \
        {"reverse_dcf", "dcf_default"}
    assert q("""select count(*) from equity.valuation_scenario s join equity.valuation_run r on r.id = s.run_id
                 where r.entity_id = any(%s)""", ids)[0][0] == 2 * 375
    assert q("select count(*) from equity.valuation_run where entity_id = %s", lender_id)[0][0] == 0
    assert check(checks, "quarter_rule")["passed"] and check(checks, "round_trip")["passed"]
    before = q("select id, inputs::text, result from equity.valuation_run where entity_id = any(%s) order by id", ids)
    go(t, key)
    status, rows_written, checks = last_run(key)
    assert status == "success" and rows_written == 0                             # unchanged inputs: nothing written
    assert q("select id, inputs::text, result from equity.valuation_run where entity_id = any(%s) order by id", ids) \
        == before


def test_a_changed_input_updates_the_run_in_place_and_a_new_date_replaces_the_grid():
    key = "test.valuation_b"
    w = World(key)
    eid, _ = w.company("a")
    cos = [synthetic("a", eid, "0.08")]
    t = valuation_task(key, lambda self: Fetched(DAY, market(), cos))
    go(t, key)
    first = q("select id from equity.valuation_run where entity_id = %s and kind = 'reverse_dcf'", eid)
    cos[0] = synthetic("a", eid, "0.05")                                          # the price moved, same date
    go(t, key)
    status, rows_written, _ = last_run(key)
    assert status == "success" and rows_written > 0
    assert q("select id, result from equity.valuation_run where entity_id = %s and kind = 'reverse_dcf'", eid)[0][0] \
        == first[0][0]                                                           # updated in place, same row
    assert abs(q("select result from equity.valuation_run where entity_id = %s and kind = 'reverse_dcf'",
                 eid)[0][0] - Decimal("0.05")) <= Decimal("0.0001")
    later = synthetic("a", eid, "0.05")
    later.as_of = D(2026, 11, 6)
    cos[0] = later
    go(t, key)
    assert q("select count(*) from equity.valuation_run where entity_id = %s and kind = 'dcf_default'", eid)[0][0] == 2
    scenarios = q("""select r.as_of, count(*) from equity.valuation_scenario s join equity.valuation_run r on r.id = s.run_id
                      where r.entity_id = %s group by 1""", eid)
    assert scenarios == [(D(2026, 11, 6), 375)]                                   # the previous grid is gone, not appended


def test_the_quarter_rule_fails_the_run_and_rolls_the_load_back():
    key = "test.valuation_c"
    w = World(key)
    ids = [w.company(n)[0] for n in ("a", "b", "c", "d")]
    cos = [synthetic("a", ids[0]), synthetic("b", ids[1]),
           synthetic("c", ids[2], price=Decimal(10) ** 9), synthetic("d", ids[3], price=Decimal(10) ** 9)]
    t = valuation_task(key, lambda self: Fetched(DAY, market(), cos))
    go(t, key)
    status, _, checks = last_run(key)
    assert status == "checks_failed" and not check(checks, "quarter_rule")["passed"]
    assert q("select count(*) from equity.valuation_run where entity_id = any(%s)", ids)[0][0] == 0


# ---------------------------------------------------------------------------------------------- the snapshot task
def snapshot_task(key: str, prefix: str):
    class T(EquitySnapshot):
        slug_prefix = prefix
    T.key = key
    t = T()
    t.workflows = ("weekly",)
    return t


def test_the_snapshot_writes_one_row_per_priced_company_keeps_it_and_changes_only_what_moved():
    key = "test.snapshot_a"
    w = World(key)
    a, _ = w.company("a")
    b, _ = w.company("b")                                                         # priced, no metrics stored yet
    c, c_slug = w.company("c")                                                    # no close on the date
    for e in (a, b):
        w.price(e, DAY)
    w.price(c, D(2026, 10, 23))
    w.metric(a, "market_cap", "none", "market", DAY, Decimal(500 * CR),
             inputs={"price": {"close": "125.5", "trade_date": "2026-10-30"}})
    w.metric(a, "pb", "consolidated", "market", DAY, Decimal("3.2"))
    w.metric(a, "pe_ttm", "consolidated", "market", DAY, None, "Profit over the last four quarters isn't positive.")
    w.metric(a, "beta_bottom_up", "none", "market", DAY, Decimal("1.1"))
    w.metric(a, "roe", "consolidated", "year", D(2026, 3, 31), Decimal("0.16"))
    w.metric(c, "market_cap", "none", "market", D(2026, 10, 23), Decimal(100 * CR))
    q("""insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result, delivered, inputs,
                                          model_version)
         values (%s, 'reverse_dcf', %s, 'solved', null, 125.5, 0.08, 0.10, '{}', 'dcf-1')""", a, DAY)
    t = snapshot_task(key, w.p)
    go(t, key)
    status, rows_written, checks = last_run(key)
    assert status == "success" and rows_written == 2
    assert check(checks, "coverage")["passed"] and check(checks, "one_row")["passed"]
    assert not check(checks, "unpriced")["passed"] and c_slug in check(checks, "unpriced")["detail"]
    got = q("""select price, price_date, market_cap, pb, pe_ttm, beta, roe, implied_growth, delivered_growth, valuation_family,
                      notes from equity.screener_snapshot where snapshot_date = %s and entity_id = any(%s)
                  order by entity_id""", DAY, [a, b, c])
    assert len(got) == 2
    (price, price_date, cap, pb, pe, beta, roe, ig, dg, family, notes) = got[0]
    assert (price, price_date, cap, pb, pe) == (Decimal("125.5"), DAY, Decimal(500 * CR), Decimal("3.2"), None)
    assert (beta, roe, ig, dg, family) == (Decimal("1.1"), Decimal("0.16"), Decimal("0.08"), Decimal("0.10"), "operating")
    assert notes["pe_ttm"]["reason"] == "Profit over the last four quarters isn't positive."
    assert notes["beta"]["label"] == "derived" and notes["implied_growth"]["label"] == sn.MODEL_LABEL
    assert notes["promoter_pct"]["reason"] == sn.NO_SHAREHOLDING
    assert got[1][0] is None and got[1][10]["price"]["reason"] == sn.NO_PRICE      # the unpriced-metrics company
    go(t, key)
    assert last_run(key)[1] == 0                                                   # unchanged data writes nothing
    w.metric(a, "pb", "consolidated", "market", DAY, Decimal("3.4"))
    go(t, key)
    assert last_run(key)[1] == 1                                                   # one row moved, updated in place
    assert q("select pb from equity.screener_snapshot where snapshot_date = %s and entity_id = %s", DAY, a)[0][0] \
        == Decimal("3.4")
    assert q("select count(*) from equity.screener_snapshot where entity_id = any(%s)", [a, b, c])[0][0] == 2


def test_a_lenders_row_carries_its_implied_roe():
    key = "test.snapshot_b"
    w = World(key)
    e, _ = w.company("l", "lender")
    w.price(e, DAY)
    w.metric(e, "market_cap", "none", "market", DAY, Decimal(900 * CR),
             inputs={"price": {"close": "900", "trade_date": "2026-10-30"}})
    q("""insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result, delivered,
                                          delivered_reason, inputs, model_version)
         values (%s, 'implied_roe', %s, 'solved', null, 900, 0.15, null, 'Fewer than three years are filed.', '{}',
                 'implied-roe-1')""", e, DAY)
    go(snapshot_task(key, w.p), key)
    ir, dr, ig, notes = q("""select implied_roe, delivered_roe, implied_growth, notes from equity.screener_snapshot
                              where entity_id = %s""", e)[0]
    assert (ir, dr, ig) == (Decimal("0.15"), None, None)
    assert notes["delivered_roe"]["reason"] == "Fewer than three years are filed."
    assert notes["implied_growth"]["reason"] == sn.NOT_OPERATING


def test_the_modules_stay_importable_with_their_check_names():
    assert ev.QUARTER == 4 and "round_trip" in ev.__doc__ and "quarter_rule" in ev.__doc__
