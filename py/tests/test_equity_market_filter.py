"""India's equity tasks never read a US company (job US-01, #400; Plan 05 §0.5 N2; Spec 02A.1 §3).

Every India equity task picks its companies from `equity.company`, which from US-01 on also holds the Dow 30 (market
US, unpublished). Each task's company selection says `market = 'IN'`, so a US row is never fetched from NSE, valued
with India's inputs, counted by India's checks or published by India's price load.

The test puts one US company on the test database, with the rows a US load writes and a few it never will (an NSE
symbol and an ISIN, so the filter is tested even against a row that looks like an Indian one): its entity (id
987650001, unpublished), identifiers, peer group (market US, with an industry India's betaIndia doesn't list), master
row, NYSE prices, a pending SEC filing, a results event, a market capitalisation. Then, for each India task, through the
runner with every request answered 404:

- the run with the US company is what the run without it is (status, rows written, check names and verdicts, the
  requests made): "the task's company list and its rows are what they are without it";
- no row any of the task's queries returned holds the US company (its id, slug, name or ticker): a trap on psycopg's
  cursor sees every row the task reads;
- no request names it, no row the task wrote refers to it, and it stays unpublished.
"""
import itertools
from datetime import UTC, datetime

import httpx
import psycopg
import pytest
from conftest import dsn

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import registry

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NOW = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)          # a Monday morning UTC: after Friday's US and Indian sessions
US_ID = 987650001
US_SLUG, US_TICKER, US_NAME = "us-zzmf", "ZZMF", "Market Filter Holdings Inc"
US_GROUP, US_GROUP_LABEL = "us-zz-test", "Market filter test group"
MARKERS = (US_SLUG, US_TICKER, US_NAME, str(US_ID), US_GROUP, US_GROUP_LABEL)

# Every India equity task (Plan 05 §0.5 N2's list, with 0038's segment changes), and valuation_inputs, which checks India's
# peer groups' Damodaran industries against betaIndia (a US group's industry is a US one).
INDIA_TASKS = ["equity.universe", "equity.valuation_inputs", "equity.prices", "equity.filings", "equity.corporate_actions", "equity.statements",
               "equity.shareholding", "equity.brsr", "equity.related_parties", "equity.segment_changes",
               "equity.metrics", "equity.beta", "equity.valuation", "equity.snapshot", "equity.rate_reactions",
               "equity.results_reactions", "equity.call_transcripts", "equity.call_text", "equity.calls_ai",
               "equity.guidance_scores", "equity.call_scores"]

# The rows the US company may hold, by table and the column that names the entity.
US_ROWS = [("core.entity_identifier", "entity_id"), ("equity.company", "entity_id"), ("equity.price_daily", "entity_id"),
           ("equity.filing", "entity_id"), ("equity.financials", "entity_id"), ("equity.metric_value", "entity_id"),
           ("equity.valuation_run", "entity_id"), ("equity.screener_snapshot", "entity_id"),
           ("equity.index_membership", "entity_id"), ("equity.adjustment_factor", "entity_id"),
           ("equity.shareholding", "entity_id"), ("core.event", "entity_id"), ("core.document", "entity_id")]


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def wipe_us() -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for sql in ("delete from equity.financials where entity_id = %(e)s",
                    "delete from equity.filing where entity_id = %(e)s",
                    "delete from equity.adjustment_factor where entity_id = %(e)s",
                    "delete from equity.price_daily where entity_id = %(e)s",
                    "delete from equity.metric_value where entity_id = %(e)s",
                    "delete from equity.valuation_scenario where run_id in (select id from equity.valuation_run where entity_id = %(e)s)",
                    "delete from equity.valuation_run where entity_id = %(e)s",
                    "delete from equity.screener_snapshot where entity_id = %(e)s",
                    "delete from equity.index_membership where entity_id = %(e)s",
                    "delete from core.document where entity_id = %(e)s",
                    "delete from core.event where entity_id = %(e)s",
                    "delete from equity.company where entity_id = %(e)s",
                    "delete from core.entity_identifier where entity_id = %(e)s",
                    "delete from core.entity where id = %(e)s",
                    "delete from equity.peer_group where key = 'us-zz-test'",
                    "delete from core.raw_file where object_key like 'mf-test-%%'"):
            c.execute(sql, {"e": US_ID})


def add_us() -> None:
    """The US company and its rows, written as the postgres role (job_writer can't choose an entity's id)."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        tr = c.execute("select id from ops.task_run where task_key = 'test.market_filter' order by id desc limit 1").fetchone()[0]
        c.execute("""insert into core.entity (id, kind, slug, name, market, currency, is_published) overriding system value
                     values (%s, 'company', %s, %s, 'US', 'USD', false)""", (US_ID, US_SLUG, US_NAME))
        c.execute("""insert into core.entity_identifier (entity_id, scheme, value, source_id)
                     select %s, v.scheme, v.value, (select id from core.source where key = 'sec')
                     from (values ('ticker', %s), ('cik', '0009876501'), ('isin', 'USZZMF000001'),
                                  ('nse_symbol', %s)) v(scheme, value)""", (US_ID, US_TICKER, US_TICKER))
        c.execute("""insert into equity.peer_group (key, market, label, damodaran_industry, source_note)
                     values ('us-zz-test', 'US', 'Market filter test group', 'Market Filter Test Industry', 'test')""")
        c.execute("""insert into equity.company (entity_id, isin, nse_symbol, peer_group_key, valuation_family, fy_end_month,
                                                 price_exchange, listed_on)
                     values (%s, 'USZZMF000001', %s, 'us-zz-test', 'operating', 12, 'NYSE', '2019-01-02')""",
                  (US_ID, US_TICKER))
        raw = c.execute("""insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at, task_run_id)
                           select 'mf-test-prices', id, sha256('mf-test'::bytea), 1, 'application/json', now(), %s
                             from core.source where key = 'yfinance' returning id""", (tr,)).fetchone()[0]
        c.execute("""insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close, volume,
                                                     raw_file_id, task_run_id)
                     select %s, d::date, 'NYSE', 'EQ', 100, 101, 99, 100, 1000, %s, %s
                       from generate_series('2026-10-26'::date, '2026-11-06', '1 day') d
                      where extract(isodow from d) < 6""", (US_ID, raw, tr))
        ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id,
                                                  is_published)
                          values ('results_filing', %s, 'us-zzmf-2026-09-30', 'ZZMF 10-Q', '2026-09-30',
                                  '2026-11-05 21:05+00', 'filed', (select id from core.source where key = 'sec'), true)
                          returning id""", (US_ID,)).fetchone()[0]
        doc = c.execute("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                                      raw_file_id)
                           values ('results_xbrl', %s, %s, 'ZZMF 10-Q', (select id from core.source where key = 'sec'),
                                   'mf-test-zzmf-10q', 'https://www.sec.gov/mf-test/zzmf-10q', %s) returning id""",
                        (US_ID, ev, raw)).fetchone()[0]
        c.execute("""insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end,
                                                filed_at, source_url, exchange)
                     values (%s, %s, %s, 'consolidated', 'us_gaap', '2026-09-30', '2026-01-01', '2026-12-31',
                             '2026-11-05 21:05+00', 'https://www.sec.gov/mf-test/zzmf-10q', 'SEC')""", (doc, US_ID, ev))
        c.execute("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value)
                     values (%s, 'market_cap', 'none', 'market', '2026-11-06', 1000000)""", (US_ID,))


def us_rows() -> dict[str, int]:
    return {t: q(f"select count(*) from {t} where {col} = %s", US_ID)[0][0] for t, col in US_ROWS}


def _has_marker(v) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return v == US_ID
    if isinstance(v, str):
        return any(m in v for m in MARKERS)
    if isinstance(v, (list, tuple, set)):
        return any(_has_marker(x) for x in v)
    if isinstance(v, dict):
        return any(_has_marker(k) or _has_marker(x) for k, x in v.items())
    return False


class Trap:
    """Every row any psycopg cursor returns while installed, checked for the US company."""

    def __init__(self, monkeypatch):
        self.hits: list[str] = []
        self.sql: dict[int, str] = {}                  # the last query each cursor ran (cursors have no __dict__)
        cls, trap = psycopg.Cursor, self
        execute, fetchone, fetchmany, fetchall, step = cls.execute, cls.fetchone, cls.fetchmany, cls.fetchall, cls.__next__

        def seen(cur, rows):
            for row in rows:
                if row is not None and _has_marker(row):
                    trap.hits.append(f"{trap.sql.get(id(cur), '?')[:300]} -> {row!r}"[:500])
            return rows

        def _execute(cur, query, *a, **k):
            trap.sql[id(cur)] = query if isinstance(query, str) else str(query)
            return execute(cur, query, *a, **k)

        def _fetchone(cur):
            row = fetchone(cur)
            seen(cur, [row])
            return row

        def _next(cur):                                # psycopg 3.3: a cursor is its own iterator
            row = step(cur)
            seen(cur, [row])
            return row

        monkeypatch.setattr(cls, "execute", _execute)
        monkeypatch.setattr(cls, "fetchone", _fetchone)
        monkeypatch.setattr(cls, "fetchmany", lambda cur, size=0: seen(cur, fetchmany(cur, size)))
        monkeypatch.setattr(cls, "fetchall", lambda cur: seen(cur, fetchall(cur)))
        monkeypatch.setattr(cls, "__next__", _next)


class NoStorage:
    def __call__(self):
        raise AssertionError("nothing is stored when every request answers 404")


_runs = itertools.count(1)


def go(cls, key: str, monkeypatch=None):
    """One forced run of the task under a test key; every request answers 404 and is recorded."""
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    task = type(f"MarketFilter{next(_runs)}", (cls,), {"key": key})()
    task.workflows = ("weekly",)
    http = HttpClient(transport=httpx.MockTransport(answer), sleep=lambda s: None, clock=lambda: NOW)
    trap = Trap(monkeypatch) if monkeypatch is not None else None
    try:
        run(JOB, "weekly", [task], only=key, force=True, http=http, storage=NoStorage(), clock=lambda: NOW)
    finally:
        if monkeypatch is not None:
            monkeypatch.undo()
    status, rows, checks = q("""select status, rows_written, checks from ops.task_run where task_key = %s
                                 order by id desc limit 1""", key)[0]
    outcome = (status, rows, sorted((c["name"], c["passed"], c["blocking"]) for c in (checks or [])), sorted(set(seen)))
    return outcome, (trap.hits if trap else []), seen


@pytest.fixture(scope="module")
def keys():
    """A test key per India task (ops.task_run needs its ops.task row), and one task run the US rows can name."""
    reg = registry()
    out = {k: (reg[k], "test.mf_" + k.split(".", 1)[1]) for k in INDIA_TASKS}
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for key in [k for _, k in out.values()] + ["test.market_filter"]:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'market filter test (US-01)', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (key,))
        job = c.execute("""insert into ops.job_run (workflow, trigger, runner) values ('weekly', 'cli', 'local')
                           returning id""").fetchone()[0]
        c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, 'test.market_filter', 'success')",
                  (job,))
    wipe_us()
    yield out
    wipe_us()


@pytest.mark.db
def test_the_list_is_every_india_equity_task_and_none_is_a_us_task(keys):
    reg = registry()
    equity = sorted(k for k in reg if k.startswith("equity."))
    assert sorted(INDIA_TASKS) == equity
    assert not [k for k in reg if k.startswith("us.") and k in INDIA_TASKS]


@pytest.mark.db
def test_the_trap_sees_a_us_row_when_a_query_has_no_filter(keys, monkeypatch):
    """The check below is only as good as the trap: an unfiltered read of the master trips it."""
    add_us()
    try:
        trap = Trap(monkeypatch)
        with psycopg.connect(JOB) as c:
            c.execute("select c.entity_id, e.slug from equity.company c join core.entity e on e.id = c.entity_id").fetchall()
            list(c.execute("select e.name from core.entity e where e.kind = 'company'"))
            c.execute("select e.slug from core.entity e where e.kind = 'company' and e.market = 'IN'").fetchall()
        monkeypatch.undo()
        assert len(trap.hits) == 2 and US_SLUG in trap.hits[0] and US_NAME in trap.hits[1]
    finally:
        wipe_us()


@pytest.mark.db
@pytest.mark.parametrize("task_key", INDIA_TASKS)
def test_an_india_task_never_reads_or_writes_a_us_company(keys, task_key, monkeypatch):
    cls, key = keys[task_key]
    wipe_us()
    go(cls, key)                                       # settles anything a first run does
    without, _, _ = go(cls, key)
    add_us()
    try:
        before = us_rows()
        with_us, hits, requests = go(cls, key, monkeypatch)
        assert hits == [], f"{task_key} read the US company:\n" + "\n".join(hits[:5])
        assert with_us == without, f"{task_key}: its run changed with a US company in the master"
        assert not [u for u in requests if any(m.lower() in u.lower() for m in (US_SLUG, US_TICKER, "0009876501"))]
        assert us_rows() == before                     # nothing written for it
        assert q("select is_published from core.entity where id = %s", US_ID) == [(False,)]
    finally:
        wipe_us()


@pytest.mark.db
def test_assemble_reads_india_by_default_and_a_us_company_only_when_named(keys):
    """`equity.valuation` reaches the engine's inputs through `assemble.fetch(db, None)`, which reads India's companies
    only (the run above returns before it on a database with no Indian price). A list of ids still reads exactly those
    companies, so US-04 can name the US ones."""
    from ft.equity import assemble
    add_us()
    try:
        with psycopg.connect(JOB) as c:
            assert US_ID not in assemble.fetch(c, None)
            got = assemble.fetch(c, [US_ID])
        assert list(got) == [US_ID] and (got[US_ID].market, got[US_ID].currency) == ("US", "USD")
    finally:
        wipe_us()
