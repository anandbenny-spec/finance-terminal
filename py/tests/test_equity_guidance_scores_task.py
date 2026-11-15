"""equity.guidance_scores through the runner (job 2B-14, #177): outcomes for management statements only (Spec 02B.1 C5's two
fixtures and its two SQL counts), the guidance series and their forecast rows, the publish rule, an outcome recomputed from
its stored inputs alone, a re-run that writes nothing, and a superseded output's rows removed. The rules themselves are
tested in test_equity_guidance_scoring.py."""
import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
import yaml
from conftest import dsn
from psycopg.types.json import Jsonb

from ft.equity.calls import scoring as sc
from ft.jobs import config
from ft.jobs.runner import run
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_guidance_scores import EquityGuidanceScores

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
KEY = "test.equity_guidance_scores"
D = date
CR = Decimal(10_000_000)
NOW = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def x(sql, *a) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql, a or None)


@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '2B-14 test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


def wipe() -> None:
    ids = "(select id from core.entity where slug like 'gs-%')"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("delete from core.forecast where series_id in (select id from core.series where key like 'guidance.gs-%')")
        c.execute(f"update equity.guidance set forecast_series_id = null where entity_id in {ids}")
        c.execute("delete from core.series where key like 'guidance.gs-%'")
        c.execute(f"delete from equity.guidance_outcome where guidance_id in (select id from equity.guidance "
                  f"where entity_id in {ids})")
        c.execute(f"delete from equity.stated_figure where entity_id in {ids}")
        c.execute(f"delete from equity.guidance where entity_id in {ids}")
        c.execute(f"delete from core.ai_output where document_id in (select id from core.document where entity_id in {ids})")
        c.execute(f"delete from equity.earnings_call where entity_id in {ids}")
        c.execute(f"delete from equity.financials where entity_id in {ids}")
        c.execute(f"delete from equity.filing where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'gs-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))


@pytest.fixture(autouse=True)
def clean():
    wipe()
    yield
    wipe()
    x("""update core.field_policy set status = 'hidden'
          where field_key in ('ai.guidance_extraction', 'equity.guidance_outcome', 'equity.guidance_hit_rate')""")


def company() -> tuple[int, str]:
    slug = f"gs-{uuid.uuid4().hex[:8]}"
    eid = q("""insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
               values ('company', %s, 'Acme Limited', 'Acme', 'IN', 'INR', true) returning id""", slug)[0][0]
    x("insert into equity.company (entity_id, fy_end_month, valuation_family) values (%s, 3, 'operating')", eid)
    return eid, slug


def year(eid: int, end: date, revenue: int) -> None:
    """A parsed annual results filing with its year row (₹ figures given in crore)."""
    tag = uuid.uuid4().hex[:8]
    doc = q("""insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
               select 'results_xbrl', %s, 'results', s.id, %s, 'https://example.invalid/r.xml' from core.source s
                where s.key = 'nse' returning id""", eid, f"/test/gs/{tag}.xml")[0][0]
    x("""insert into equity.filing (document_id, entity_id, basis, format, period_end, fy_start, filed_at, source_url,
                                    exchange, parse_status)
         values (%s, %s, 'consolidated', 'ind_as', %s, %s, %s, 'https://example.invalid/r.xml', 'NSE', 'parsed')""",
      doc, eid, end, D(end.year - 1, 4, 1), datetime(end.year, 5, 10, tzinfo=UTC))
    x("""insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, revenue,
                                        pbt_before_exceptional, finance_cost, other_income, dna, tags)
         values (%s, %s, 'consolidated', %s, %s, 'year', %s, %s, 0, 0, 0, %s)""",
      doc, eid, D(end.year - 1, 4, 1), end, revenue * CR, revenue * CR / 10,
      Jsonb({"revenue": "RevenueFromOperations"}))


def call(eid: int, slug: str, day: date, label: str) -> tuple[int, int]:
    """A stored, published earnings call with its transcript document (no text: only what the scorer reads)."""
    season = "q" + label[1] + "-fy" + label[-2:]
    ev = q("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
              select 'earnings_call', %s, %s, 'call', %s, 'stored', s.id, true from core.source s where s.key = 'nse'
              returning id""", eid, f"{slug}-call-{season}", day)[0][0]
    doc = q("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                          char_count, text_sha256, is_published)
               select 'call_transcript', %s, %s, 'transcript', s.id, %s, 'https://example.invalid/t.pdf', 10000, %s, true
                 from core.source s where s.key = 'nse' returning id""",
            eid, ev, f"/test/gs/{slug}-{season}.pdf", hashlib.sha256(f"{slug}{season}".encode()).digest())[0][0]
    x("""insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source,
                                           call_date, call_date_source, filed_at, exchange, route, transcript_document_id)
         values (%s, %s, %s, %s, %s, 'text', %s, 'text', %s, 'NSE', 'exchange_pdf', %s)""",
      ev, eid, label, day, season, day, datetime(day.year, day.month, day.day, tzinfo=UTC), doc)
    return ev, doc


def extraction(eid: int, ev: int, doc: int, day: date, targets: list[dict]) -> list[int]:
    """A published guidance_extraction output with one verified citation per target, and its equity.guidance rows."""
    content = {"targets": [{"citation_no": i + 1, "quote": t.get("quote", "we expect this")} for i, t in enumerate(targets)],
               "stated_figures": []}
    out = q("""insert into core.ai_output (task, document_id, content, model, prompt_key, prompt_version, input_sha256)
               values ('guidance_extraction', %s, %s, 'test', 'guidance_extraction', 'v1', %s) returning id""",
            doc, Jsonb(content), hashlib.sha256(uuid.uuid4().bytes).digest())[0][0]
    ids = []
    with psycopg.connect(ADMIN) as c:
        for i, t in enumerate(targets):
            c.execute("""insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start,
                                                       char_end, verified)
                         values (%s, %s, %s, 'document_span', %s, %s, %s, true)""",
                      (out, i + 1, f"/targets/{i}", doc, *t["span"]))
        c.execute("update core.ai_output set status = 'published' where id = %s", (out,))
        for i, t in enumerate(targets):
            ids.append(c.execute(
                """insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start,
                                                char_end, metric, metric_label, metric_text, kind, value, low, high, unit,
                                                period_text, period_kind, target_start, target_end, growth_basis,
                                                vintage_date, not_scored_reason, speaker_role)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'FY26', %s, %s, %s, %s, %s, %s, %s)
                   returning id""",
                (ev, eid, out, i + 1, doc, *t["span"], t["metric"], t["metric"].replace("_", " ").capitalize(),
                 t["metric"], t["kind"], t.get("value"), t.get("low"), t.get("high"), t.get("unit"),
                 t.get("period_kind", "year"), t.get("start", D(2025, 4, 1)), t.get("end", D(2026, 3, 31)),
                 t.get("growth_basis"), day, t.get("reason"), t.get("role", "management"))).fetchone()[0])
        c.commit()
    return ids


def task(*slugs) -> EquityGuidanceScores:
    class T(EquityGuidanceScores):
        key = KEY
    t = T()
    t.scope = frozenset(slugs)
    t.workflows, t.rules = ("weekly", "refresh", "backfill"), {"weekly": "every 6 days"}
    return t


def go(*slugs) -> tuple:
    code = run(JOB, "refresh", [task(*slugs)], only=KEY, force=True, clock=lambda: NOW)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1",
           KEY)[0]
    assert code == 0, tr
    return tr


def outcomes(eid: int) -> dict[int, tuple]:
    return {r[0]: r[1:] for r in q(
        """select o.guidance_id, o.outcome, o.relation, o.actual, o.base, o.is_first, o.revision, o.counted, o.reason,
                  o.inputs
             from equity.guidance_outcome o join equity.guidance g on g.id = o.guidance_id where g.entity_id = %s""", eid)}


C5_COUNTS = ("""select count(*) from core.forecast f join equity.guidance g
                  on g.ai_output_id = f.ai_output_id and g.document_id = f.document_id
                 and g.char_start = f.char_start and g.char_end = f.char_end
               where g.speaker_role <> 'management'""",
             """select count(*) from equity.guidance_outcome o join equity.guidance g on g.id = o.guidance_id
               where g.speaker_role <> 'management'""")


@pytest.mark.db
def test_only_management_statements_are_scored_and_become_forecasts():
    """Spec 02B.1 C5, test 1: an unknown speaker's point target on an earlier call, and management's statement of the same
    target on a later call: management's is the first statement, the unknown row has no outcome and no forecast row carries
    its span; the series and the scores are what they'd be without it."""
    eid, slug = company()
    year(eid, D(2025, 3, 31), 4000)
    year(eid, D(2026, 3, 31), 4600)
    ev_a, doc_a = call(eid, slug, D(2025, 5, 10), "Q4 FY25")
    ev_b, doc_b = call(eid, slug, D(2025, 7, 20), "Q1 FY26")
    [unknown] = extraction(eid, ev_a, doc_a, D(2025, 5, 10), [
        {"metric": "revenue_growth", "kind": "point", "value": 15, "unit": "pct", "growth_basis": "yoy", "span": (100, 200),
         "role": "unknown"}])
    growth, revenue, other = extraction(eid, ev_b, doc_b, D(2025, 7, 20), [
        {"metric": "revenue_growth", "kind": "point", "value": 15, "unit": "pct", "growth_basis": "yoy", "span": (300, 400)},
        {"metric": "revenue", "kind": "point", "value": 4600, "unit": "inr", "span": (500, 600)},
        {"metric": "other", "kind": "range", "low": 8, "high": 10, "unit": "pct", "span": (700, 800),
         "reason": "no_filed_figure"}])
    tr = go(slug)
    assert all(c["passed"] for c in tr[2]), tr[2]
    got = outcomes(eid)
    assert set(got) == {growth, revenue, other}                                          # none for the unknown row
    assert got[growth][:8] == ("delivered", "within", Decimal("15.000000"), None, True, "first", True, None)
    assert got[revenue][:3] == ("delivered", "within", Decimal("4600.000000"))
    assert got[other][0] == "not_scored" and got[other][7] == "no_filed_figure" and got[other][6] is False
    series = {k: (kind, pub, unit, src) for k, kind, pub, unit, src in q(
        """select s.key, s.kind, s.is_published, s.unit, so.key from core.series s join core.source so on so.id = s.source_id
            where s.entity_id = %s""", eid)}
    assert series == {f"guidance.{slug}.revenue_growth": ("forecast", False, "percent", "nse"),
                      f"guidance.{slug}.revenue": ("forecast", False, "inr", "nse")}
    rows = q("""select s.key, f.vintage_date, f.target_label, f.value, f.char_start, f.method from core.forecast f
                 join core.series s on s.id = f.series_id where s.entity_id = %s order by s.key""", eid)
    assert rows == [(f"guidance.{slug}.revenue", D(2025, 7, 20), "FY 2025-26", 4600 * CR, 500, "ai_extracted"),
                    (f"guidance.{slug}.revenue_growth", D(2025, 7, 20), "FY 2025-26", Decimal(15), 300, "ai_extracted")]
    links = dict(q("select id, forecast_series_id is not null from equity.guidance where entity_id = %s", eid))
    assert links == {unknown: False, growth: True, revenue: True, other: False}
    assert [q(sql)[0][0] for sql in C5_COUNTS] == [0, 0]
    # the company's hit rate counts management's two first statements only (n < 5: no rate yet); since 0048 (#532) the hit rates
    # also wait for equity.guidance_hit_rate, so it is flipped with the other two (setup only; the assertion is unchanged)
    x("""update core.field_policy set status = 'shown'
          where field_key in ('ai.guidance_extraction', 'equity.guidance_outcome', 'equity.guidance_hit_rate')""")
    assert q("""select delivered, missed, n, rate from api.guidance_hit_rates
                 where scope = 'company' and scope_key = %s""", slug) == [(2, 0, 2, None)]
    x("""update core.field_policy set status = 'hidden'
          where field_key in ('ai.guidance_extraction', 'equity.guidance_outcome', 'equity.guidance_hit_rate')""")
    # every stored outcome comes back from its stored inputs alone
    for gid, (outcome, relation, actual, base, *_, inputs) in got.items():
        assert sc.recompute(inputs) == (outcome, relation, actual, base), gid
    assert go(slug)[1] == 0                                                              # a re-run writes nothing


@pytest.mark.db
def test_no_series_exists_when_a_metric_s_only_point_target_is_unattributed():
    """Spec 02B.1 C5, test 2."""
    eid, slug = company()
    year(eid, D(2026, 3, 31), 4600)
    ev, doc = call(eid, slug, D(2025, 7, 20), "Q1 FY26")
    extraction(eid, ev, doc, D(2025, 7, 20), [
        {"metric": "pat", "kind": "point", "value": 500, "unit": "inr", "span": (100, 200), "role": "unknown"},
        {"metric": "revenue", "kind": "floor", "value": 4000, "unit": "inr", "span": (300, 400)}])
    go(slug)
    assert q("select count(*) from core.series where entity_id = %s", eid)[0][0] == 0       # a floor is never a forecast
    assert [o[0] for o in outcomes(eid).values()] == ["delivered"]


@pytest.mark.db
def test_the_series_follow_the_extraction_s_field_policy():
    eid, slug = company()
    year(eid, D(2026, 3, 31), 4600)
    ev, doc = call(eid, slug, D(2025, 7, 20), "Q1 FY26")
    extraction(eid, ev, doc, D(2025, 7, 20), [{"metric": "revenue", "kind": "range", "low": 4500, "high": 4700,
                                               "unit": "inr", "span": (100, 200)}])
    go(slug)
    published = "select is_published from core.series where entity_id = %s"
    assert q(published, eid) == [(False,)]
    x("update core.field_policy set status = 'labelled' where field_key = 'ai.guidance_extraction'")
    go(slug)
    assert q(published, eid) == [(True,)]
    assert q("""select value, value_low, value_high from core.forecast f join core.series s on s.id = f.series_id
                 where s.entity_id = %s""", eid) == [(None, 4500 * CR, 4700 * CR)]
    x("update core.field_policy set status = 'hidden' where field_key = 'ai.guidance_extraction'")
    go(slug)
    assert q(published, eid) == [(False,)]


@pytest.mark.db
def test_a_superseded_output_s_outcomes_and_forecasts_are_removed():
    eid, slug = company()
    year(eid, D(2026, 3, 31), 4600)
    ev, doc = call(eid, slug, D(2025, 7, 20), "Q1 FY26")
    [gid] = extraction(eid, ev, doc, D(2025, 7, 20), [{"metric": "revenue", "kind": "point", "value": 4600, "unit": "inr",
                                                       "span": (100, 200)}])
    go(slug)
    assert set(outcomes(eid)) == {gid}
    x("""update core.ai_output set status = 'superseded', status_reason = 'test'
          where id = (select ai_output_id from equity.guidance where id = %s)""", gid)
    tr = go(slug)
    assert tr[1] > 0 and outcomes(eid) == {}
    assert q("""select count(*) from core.forecast f join core.series s on s.id = f.series_id
                 where s.entity_id = %s""", eid) == [(0,)]
    assert q("select forecast_series_id from equity.guidance where id = %s", gid) == [(None,)]


def test_the_task_is_wired_in_tasks_yaml():
    raw = yaml.safe_load(config.CONFIG.read_text())["equity.guidance_scores"]
    assert raw["workflows"] == {"weekly": "every 6 days"} and raw["uses_nse"] is False
    assert raw["run_after"] == ["equity.calls_ai", "equity.metrics"] and raw["overdue_after"] == "10 days"
    assert registry()["equity.guidance_scores"] is EquityGuidanceScores


@pytest.mark.db
def test_an_unscoped_run_scores_every_company_as_production_does():
    others = q("""select count(*) from equity.guidance g join core.entity e on e.id = g.entity_id
                   where e.slug not like 'gs-%'""")[0][0]
    assert others == 0, "another test left guidance rows behind"
    eid, slug = company()
    year(eid, D(2026, 3, 31), 4600)
    ev, doc = call(eid, slug, D(2025, 7, 20), "Q1 FY26")
    [gid] = extraction(eid, ev, doc, D(2025, 7, 20), [{"metric": "revenue", "kind": "point", "value": 4600, "unit": "inr",
                                                       "span": (100, 200)}])
    t = task()
    t.scope = None
    assert run(JOB, "refresh", [t], only=KEY, force=True, clock=lambda: NOW) == 0
    assert outcomes(eid)[gid][:2] == ("delivered", "within")
    assert q("select count(*) from core.series where key = %s", f"guidance.{slug}.revenue") == [(1,)]
