"""The US slice's five tasks on the test database (job US-04, #403; Spec 02A.1 §5.4; Plan 05 §0.5 N2, D4).

- End to end, as job_writer through the runner: a US company built from the worked example's rows
  (test_equity_us_valuation), with prices, the 10-year Treasury and the implied ERP stored, runs through `us.metrics`,
  `us.beta`, `us.valuation` and `us.snapshot`: operating income as filed, the bottom-up beta from Damodaran's US
  industry at 25%, the reverse DCF solved and round-tripped, the default run's value per share, the snapshot row with
  promoter holding N/A, a re-run writing nothing, and the company still unpublished.
- `us.valuation_inputs`' load: the two IMF series created unpublished, the US industry betas and the forecasts stored
  once, a re-load writing nothing.
- No US task reads or writes an India company: an Indian company with the rows India's tasks write sits beside them,
  and a trap on psycopg's cursor sees every row each US task reads.

Dates are in 2032, after any other test's rows, so the rates read are these."""
import itertools
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import dsn
from psycopg.types.json import Jsonb
from test_equity_us_valuation import U, example_rows, fin

from ft.equity import inputs as inp
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_us_snapshot import NO_PROMOTER
from ft.jobs.tasks.equity_us_valuation_inputs import Fetched as InputsFetched
from ft.jobs.tasks.equity_us_valuation_inputs import UsValuationInputs

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
D = date
NOW = datetime(2032, 5, 31, 6, 0, tzinfo=UTC)
DAY = D(2032, 5, 28)                        # a Friday: the price date
SEED = "test.usv_seed"
SLUG, IN_SLUG = "us-zzv", "zzin-usv"
GROUP, IN_GROUP = "us-zzv-test", "zzin-usv-test"
INDUSTRY = "ZZV Test Industry"
EDITION = D(2032, 1, 5)
US_TASKS = ["us.valuation_inputs", "us.metrics", "us.beta", "us.valuation", "us.snapshot"]
_runs = itertools.count(1)


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        cur = c.execute(sql, a or None)
        return cur.fetchall() if cur.description else []


def shift(d):
    return d.replace(year=d.year + 6) if d else d


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = f"(select id from core.entity where slug in ('{SLUG}', '{IN_SLUG}'))"
        for sql in ((f"delete from equity.valuation_scenario where run_id in (select id from equity.valuation_run "
                    f"where entity_id in {ids})"),
                    f"delete from equity.valuation_run where entity_id in {ids}",
                    f"delete from equity.screener_snapshot where entity_id in {ids}",
                    f"delete from equity.metric_value where entity_id in {ids}",
                    f"delete from equity.financials where entity_id in {ids}",
                    f"delete from equity.filing where entity_id in {ids}",
                    f"delete from core.document where entity_id in {ids}",
                    f"delete from core.event where entity_id in {ids}",
                    f"delete from equity.price_daily where entity_id in {ids}",
                    f"delete from equity.company where entity_id in {ids}",
                    f"delete from core.entity_identifier where entity_id in {ids}",
                    f"delete from core.entity where slug in ('{SLUG}', '{IN_SLUG}')",
                    f"delete from equity.peer_group where key in ('{GROUP}', '{IN_GROUP}')",
                    "delete from core.raw_file where object_key like 'usv-test-%%'",
                    f"delete from equity.industry_benchmark where edition = '{EDITION.isoformat()}'"):
            c.execute(sql)
        c.execute("delete from core.observation where task_run_id in (select id from ops.task_run where task_key = %s)",
                  (SEED,))


@pytest.fixture(scope="module")
def keys():
    reg = registry()
    out = {k: (reg[k], "test.usv_" + k.split(".", 1)[1]) for k in US_TASKS}
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for key in [k for _, k in out.values()] + [SEED]:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'US-04 test', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (key,))
    wipe()
    yield out
    wipe()


def seed_run(c) -> int:
    job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('refresh', 'cli', 'local') "
                    "returning id").fetchone()[0]
    return c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, %s, 'success') returning id",
                     (job, SEED)).fetchone()[0]


def add_company(c, tr: int, *, slug: str, market: str, group: str, exchange: str, filing_exchange: str, fmt: str,
                source: str, rows) -> int:
    currency = "USD" if market == "US" else "INR"
    eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                       values ('company', %s, %s, %s, %s, %s) returning id""",
                    (slug, slug.upper(), market, currency, market == "IN")).fetchone()[0]
    c.execute("""insert into equity.company (entity_id, peer_group_key, valuation_family, fy_end_month, price_exchange)
                 values (%s, %s, 'operating', 12, %s)""", (eid, group, exchange))
    raw = c.execute("""insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at, task_run_id)
                       select %s, id, sha256(%s::bytea), 1, 'application/json', now(), %s
                         from core.source where key = %s returning id""",
                    (f"usv-test-{slug}", f"usv-test-{slug}", tr, source)).fetchone()[0]
    c.execute("""insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close, volume,
                                                 raw_file_id, task_run_id)
                 select %s, d::date, %s, 'EQ', 450, 450, 450, 450, 1000, %s, %s
                   from generate_series('2031-04-01'::date, %s::date, '1 day') d
                  where extract(isodow from d) < 6""", (eid, exchange, raw, tr, DAY))
    src = "sec" if market == "US" else "nse"
    ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id,
                                              is_published)
                      values ('results_filing', %s, %s, 'results', '2032-03-31', '2032-04-30 21:05+00', 'filed',
                              (select id from core.source where key = %s), false) returning id""",
                   (eid, f"{slug}-2032-03-31", src)).fetchone()[0]
    doc = c.execute("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                                  raw_file_id)
                       values ('results_xbrl', %s, %s, 'results', (select id from core.source where key = %s), %s,
                               'https://example.invalid/usv-test', %s) returning id""",
                    (eid, ev, src, f"usv-test-{slug}", raw)).fetchone()[0]
    c.execute("""insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end,
                                            filed_at, source_url, exchange, parse_status)
                 values (%s, %s, %s, 'consolidated', %s, '2032-03-31', '2032-01-01', '2032-12-31',
                         '2032-04-30 21:05+00', 'https://example.invalid/usv-test', %s, 'parsed')""",
              (doc, eid, ev, fmt, filing_exchange))
    for f in rows:
        cols = sorted(f.values)
        c.execute(f"""insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind,
                                                      currency, tags, {", ".join(cols)})
                      values (%s, %s, 'consolidated', %s, %s, %s, %s, %s, {", ".join(["%s"] * len(cols))})""",
                  (doc, eid, f.start, f.end, f.kind, currency, Jsonb(f.tags), *[f.values[k] for k in cols]))
    return eid


def us_rows():
    from dataclasses import replace
    rows = [replace(f, start=shift(f.start), end=shift(f.end)) for f in example_rows()]
    rows[-1] = replace(rows[-1], tags={**rows[-1].tags, "shares_outstanding":
                                       "dei:EntityCommonStockSharesOutstanding (cover page, as of 2032-04-24)"})
    rows.append(fin("year", D(2028, 12, 31), D(2028, 1, 1), revenue=700 * U, operating_income=140 * U, pbt=140 * U,
                    tax=28 * U, pat_owners=112 * U))
    return rows


def betas_file(c, tr: int) -> int:
    return c.execute("""insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at, task_run_id)
                        select 'usv-test-betas', id, sha256('usv-test-betas'::bytea), 1, 'application/vnd.ms-excel',
                               now(), %s from core.source where key = 'damodaran'
                        on conflict (object_key) do update set bytes = 1 returning id""", (tr,)).fetchone()[0]


def seed() -> tuple[int, int]:
    """The US company (and an Indian one beside it), the rates and the US industry betas."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        tr = seed_run(c)
        raw = betas_file(c, tr)
        for key, when, value in (("fred.DGS10", DAY, "6.0"), ("damodaran.ERP_IMPLIED", D(2032, 5, 1), "7.0")):
            c.execute("""insert into core.observation (series_id, obs_date, value, task_run_id)
                         select id, %s, %s, %s from core.series where key = %s""", (when, value, tr, key))
        c.execute("""insert into equity.peer_group (key, market, label, damodaran_industry, source_note)
                     values (%s, 'US', 'US-04 test group', %s, 'test'), (%s, 'IN', 'US-04 India test group', %s, 'test')""",
                  (GROUP, INDUSTRY, IN_GROUP, "ZZIN Test Industry"))
        # every US group's industry in the test edition, so the newest edition covers them all (the beta task's check)
        c.execute("""insert into equity.industry_benchmark (edition, market, industry, n_firms, beta, de_ratio,
                                                            effective_tax, unlevered_beta, cash_firm_value,
                                                            unlevered_beta_cash_corrected, raw_file_id)
                     select distinct %s::date, 'US', damodaran_industry, 10, 1.0, 0.1, 0.2, 0.9, 0.05, 0.9, %s::bigint
                       from equity.peer_group where market = 'US' and damodaran_industry is not null""",
                  (EDITION, raw))
        us = add_company(c, tr, slug=SLUG, market="US", group=GROUP, exchange="NYSE", filing_exchange="SEC",
                         fmt="us_gaap", source="yfinance", rows=us_rows())
        india = add_company(c, tr, slug=IN_SLUG, market="IN", group=IN_GROUP, exchange="NSE", filing_exchange="NSE",
                            fmt="ind_as", source="nse", rows=[fin("quarter", D(2032, 3, 31), D(2032, 1, 1), revenue=1)])
        c.execute("""insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value)
                     values (%s, 'market_cap', 'none', 'market', %s, 1000000)""", (india, DAY))
    return us, india


class NoStorage:
    def __call__(self):
        raise AssertionError("these tasks store no file")


def go(cls, key: str, trap=None) -> tuple[str, int, dict]:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    task = type(f"UsValuationTest{next(_runs)}", (cls,), {"key": key})()
    task.workflows = ("weekly",)
    http = HttpClient(transport=httpx.MockTransport(answer), sleep=lambda s: None, clock=lambda: NOW)
    run(JOB, "weekly", [task], only=key, force=True, http=http, storage=NoStorage(), clock=lambda: NOW)
    status, rows, checks = q("""select status, rows_written, checks from ops.task_run where task_key = %s
                                 order by id desc limit 1""", key)[0]
    return status, rows, {c["name"]: c["passed"] for c in (checks or [])}


@pytest.mark.db
def test_the_us_tasks_value_a_us_company_end_to_end(keys):
    wipe()
    us, india = seed()
    try:
        india_before = q("select count(*) from equity.metric_value where entity_id = %s", india)
        status, rows, checks = go(*keys["us.metrics"])
        assert status == "success" and rows > 0 and checks["ttm_sums"] and checks["ebit_as_filed"], checks
        got = {(k, p, e): (v, h) for k, p, e, v, h in q(
            """select metric_key, period_kind, period_end, value, held_reason from equity.metric_value
                where entity_id = %s""", us)}
        assert got[("ebit_derived", "ttm", D(2032, 3, 31))] == (200 * U, None)
        assert got[("market_cap", "market", DAY)] == (4500 * U, None)
        assert got[("traded_value_median_3m", "market", DAY)][1].startswith("N/A: The US price source")
        inputs = q("""select inputs from equity.metric_value where entity_id = %s and metric_key = 'ebit_derived'
                       and period_kind = 'ttm' and period_end = '2032-03-31'""", us)[0][0]
        assert {line for r in inputs["financials"] for line in r["lines"]} == {"operating_income"}

        status, _, checks = go(*keys["us.beta"])
        assert status == "success" and checks["industries"], checks
        (beta, inp_), = q("""select value, inputs from equity.metric_value where entity_id = %s
                              and metric_key = 'beta_bottom_up' and period_end = %s""", us, DAY)
        # 0.9 relevered at D/E 500 ÷ 4,500 and 25%: 0.9 × (1 + 0.75 × 0.1111…) = 0.975
        assert beta.quantize(Decimal("0.000001")) == Decimal("0.975000") and inp_["file"] == "betas.xls (US)"
        assert inp_["tax_rate"] == "0.25"

        cls, key = keys["us.valuation"]
        status, _, checks = go(type("Only", (cls,), {"slug_prefix": SLUG}), key)
        assert status == "success" and checks["round_trip"] and checks["quarter_rule"], checks
        runs = {k: (st, r, p, i) for k, st, r, p, i in q(
            "select kind, status, result, price, inputs from equity.valuation_run where entity_id = %s", us)}
        assert runs["reverse_dcf"][:3] == ("solved", Decimal("0.3115"), Decimal(450))
        assert runs["dcf_default"][:2] == ("solved", Decimal("186.13"))
        text = json.dumps(runs["reverse_dcf"][3])
        assert "SEC EDGAR" in text and "Yahoo Finance" in text and "India" not in text and "NSE" not in text
        assert q("""select count(*) from equity.valuation_scenario s join equity.valuation_run r on r.id = s.run_id
                     where r.entity_id = %s""", us)[0][0] > 0

        cls, key = keys["us.snapshot"]
        status, _, checks = go(type("Only", (cls,), {"slug_prefix": SLUG}), key)
        assert status == "success" and checks["coverage"], checks
        (row,) = q("""select market_cap, implied_growth, promoter_pct, notes from equity.screener_snapshot
                       where entity_id = %s and snapshot_date = %s""", us, DAY)
        assert row[:3] == (4500 * U, Decimal("0.3115"), None)
        assert row[3]["promoter_pct"] == {"reason": NO_PROMOTER.removeprefix("N/A: ")}

        again, rows, _ = go(type("Only", (cls,), {"slug_prefix": SLUG}), key)
        assert again == "success" and rows == 0                       # unchanged data writes nothing
        assert q("select is_published from core.entity where id = %s", us) == [(False,)]
        assert q("select count(*) from equity.metric_value where entity_id = %s", india) == india_before
        assert q("select count(*) from equity.valuation_run where entity_id = %s", india) == [(0,)]
    finally:
        wipe()


@pytest.mark.db
def test_the_inputs_load_creates_the_imf_series_unpublished_and_stores_each_edition_once(keys):
    wipe()
    rows = [inp.IndustryRow(INDUSTRY, 10, Decimal("1.0"), Decimal("0.1"), Decimal("0.2"), Decimal("0.9"),
                            Decimal("0.05"), Decimal("0.9")),
            *(inp.IndustryRow(f"ZZV filler {i}", 1, None, None, None, None, None, None) for i in range(55))]
    imf = {"NGDP_RPCH": {y: Decimal("2.0") for y in range(2024, 2037)},
           "PCPIPCH": {y: Decimal("2.5") for y in range(2024, 2037)}}
    try:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            tr = seed_run(c)
            raw = betas_file(c, tr)
        f = InputsFetched(NOW, EDITION, rows, raw, imf, [D(2031, 10, 13)])
        for n in (1, 2):
            with psycopg.connect(JOB) as c:
                task = UsValuationInputs()
                written = task.load(c, f, tr)
                checks = {k.name: k.passed for k in task.checks(c, f, tr)}
                c.commit()
            assert checks["beta_industries"] and checks["imf_shape"] and checks["imf_vintage"], checks
            assert written == (56 + 26 if n == 1 else 0)
        series = q("""select key, is_published, market from core.series where key in
                      ('imf.NGDP_RPCH.USA', 'imf.PCPIPCH.USA') order by key""")
        assert series == [("imf.NGDP_RPCH.USA", False, "US"), ("imf.PCPIPCH.USA", False, "US")]
        assert q("select count(*) from equity.industry_benchmark where edition = %s and market = 'US'", EDITION) == [(56,)]
    finally:
        q("""delete from core.forecast where series_id in (select id from core.series
              where key in ('imf.NGDP_RPCH.USA', 'imf.PCPIPCH.USA'))""")
        q("delete from core.series where key in ('imf.NGDP_RPCH.USA', 'imf.PCPIPCH.USA')")
        wipe()


# ---------------------------------------------------------------------------------------------- India never read
MARKERS = (IN_SLUG, IN_SLUG.upper(), IN_GROUP)


def _has_marker(v, eid) -> bool:
    if v is None or isinstance(v, bool):
        return False
    if isinstance(v, int):
        return v == eid
    if isinstance(v, str):
        return any(m in v for m in MARKERS)
    if isinstance(v, (list, tuple, set)):
        return any(_has_marker(x, eid) for x in v)
    if isinstance(v, dict):
        return any(_has_marker(k, eid) or _has_marker(x, eid) for k, x in v.items())
    return False


def trap(monkeypatch, eid: int) -> list[str]:
    hits: list[str] = []
    cls = psycopg.Cursor
    fetchone, fetchall, step = cls.fetchone, cls.fetchall, cls.__next__

    def seen(rows):
        for row in rows:
            if row is not None and _has_marker(row, eid):
                hits.append(repr(row)[:300])
        return rows

    def _next(cur):
        row = step(cur)
        seen([row])
        return row

    monkeypatch.setattr(cls, "fetchone", lambda cur: seen([fetchone(cur)])[0])
    monkeypatch.setattr(cls, "fetchall", lambda cur: seen(fetchall(cur)))
    monkeypatch.setattr(cls, "__next__", _next)
    return hits


@pytest.mark.db
@pytest.mark.parametrize("task_key", US_TASKS)
def test_no_us_task_reads_or_writes_an_india_company(keys, task_key, monkeypatch):
    """The ids of every row a US task reads are checked against the Indian company's (a US company sits beside it, so
    the task has something to do)."""
    wipe()
    _, india = seed()
    try:
        before = {t: q(f"select count(*) from {t} where entity_id = %s", india) for t in (
            "equity.metric_value", "equity.valuation_run", "equity.screener_snapshot", "equity.financials")}
        hits = trap(monkeypatch, india)
        go(*keys[task_key])
        monkeypatch.undo()
        assert hits == [], f"{task_key} read the Indian company: {hits[:3]}"
        assert {t: q(f"select count(*) from {t} where entity_id = %s", india) for t in before} == before
    finally:
        wipe()


@pytest.mark.db
def test_the_trap_sees_an_india_row_when_a_query_has_no_filter(keys, monkeypatch):
    wipe()
    _, india = seed()
    try:
        hits = trap(monkeypatch, india)
        with psycopg.connect(JOB) as c:
            c.execute("select c.entity_id, e.slug from equity.company c join core.entity e on e.id = c.entity_id").fetchall()
        monkeypatch.undo()
        assert hits
    finally:
        wipe()


def test_the_five_tasks_are_module_us_weekly_and_chained_like_indias():
    from ft.jobs import config
    tasks = config.load()
    assert {k for k in tasks if k.startswith("us.")} >= set(US_TASKS)
    assert tasks["us.metrics"].run_after == ("us.filings", "us.corporate_actions")
    assert tasks["us.beta"].run_after == ("us.metrics", "us.valuation_inputs", "policy.market_series")
    assert tasks["us.valuation"].run_after == ("us.beta",) and tasks["us.snapshot"].run_after == ("us.valuation",)
    assert all(tasks[k].rules == {"weekly": "every 6 days"} and not tasks[k].uses_nse for k in US_TASKS)
    reg = registry()
    assert all(reg[k].key == k for k in US_TASKS)
    assert timedelta(days=40) == tasks["us.valuation_inputs"].overdue_after
