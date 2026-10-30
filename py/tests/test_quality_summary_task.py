"""site.quality_summary (issue #134, Accuracy 2): every measure on constructed ops and core rows, the periods' edges, a
re-run giving the same rows, the checks, the not-set-up guard and a run through the runner as job_writer.

The task is built with a test module name ('qtest'), so the rows it counts are only the ones made here; the fixture's own
rows (policy) are never in play. The data is written as the owner, the task runs as job_writer (what production uses)."""
import itertools
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from conftest import dsn

from ft.jobs import config
from ft.jobs.runner import run
from ft.jobs.tasks import quality_summary as qs
from ft.jobs.tasks.quality_summary import Fetched, Gate, QualitySummary, Settings, evaluate

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
M = "qtest"
KEY = f"{M}.counts"
_n = itertools.count(1)
GATES = [Gate("g_pass", M, "qtest_set_a", "accuracy", Decimal("0.8"), None),
         Gate("g_low", M, "qtest_set_b", "accuracy", Decimal("0.8"), None),
         Gate("g_none", M, "qtest_set_c", "accuracy", Decimal("0.8"), None),
         Gate("g_max", M, "qtest_set_d", "rejected_share", None, Decimal("0.1"))]
SETTINGS = Settings((M,), {"d3_suppression": M, "d4_freshness": M}, GATES)


def admin(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        cur = c.execute(sql, a or None)
        return cur.fetchall() if cur.description else None


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        admin("delete from core.quality_summary")
        admin("delete from core.ai_output where scope like 'qtest:%'")
        admin("delete from core.event where slug like 'qtest-%'")
        admin("delete from ops.alert where task_key like 'qtest.%'")
        admin("delete from core.task_freshness where task_key like 'qtest.%'")
        admin("delete from ops.task_run where task_key like 'qtest.%'")
        admin("delete from ops.task where key like 'qtest.%'")
        admin("delete from core.ai_task where key like 'qtest_%'")
        admin("delete from ops.drill_run where github_run_id = 999")
        admin("delete from core.eval_summary where eval_set like 'qtest_%'")
        admin("delete from core.field_policy where field_key like 'qtest.%' or field_key like 'ai.qtest_%'")
    wipe()
    yield
    wipe()


def at(days_ago: float) -> datetime:
    return NOW - timedelta(days=days_ago)


def ai_task(key="qtest_task", module=M):
    admin("insert into core.ai_task (key, label, module, output_kind, requires_citations) values (%s, %s, %s, 'text', false) on conflict do nothing",
          key, key, module)


def ai_output(status, reason=None, days_ago=1.0, task="qtest_task", **target):
    ai_task(task) if task == "qtest_task" else None
    admin("""insert into core.ai_output (task, scope, event_id, content, status, status_reason, model, prompt_key, prompt_version,
                                         input_sha256, created_at)
             values (%s, %s, %s, '{}', %s, %s, 'm', 'p', '1', decode(repeat('00', 32), 'hex'), %s)""",
          task, f"qtest:{next(_n)}", target.get("event_id"), status, reason, at(days_ago))
    # a target-less output needs a scope (made above); an event target needs none, but the scope is harmless


def task(name="counts", enabled=True, overdue="2 days", module=M):
    admin("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
             values (%s, %s, 'test', '{daily}', '20 hours', %s, %s) on conflict do nothing""",
          f"{module}.{name}", module, overdue, enabled)
    return f"{module}.{name}"


def job_run() -> int:
    return admin("insert into ops.job_run (workflow, trigger, runner) values ('daily', 'cli', 'local') returning id")[0][0]


def task_run(key, status, days_ago, jr=None):
    admin("""insert into ops.task_run (job_run_id, task_key, status, started_at, finished_at)
             values (%s, %s, %s, %s, %s)""", jr or JR, key, status, at(days_ago), at(days_ago) + timedelta(minutes=5))


@pytest.fixture
def jr():
    global JR
    JR = job_run()
    yield JR


def compute(settings=SETTINGS, now=NOW):
    f = Fetched(now, settings)
    with psycopg.connect(JOB) as tx:
        QualitySummary().load(tx, f, 0)
        tx.commit()
    rows = {(r[0], r[1], r[2]): r[3:] for r in admin(
        "select module, period, measure, value, n, label, as_of from core.quality_summary")}
    return f, rows


def value(rows, period, measure):
    return rows[(M, period, measure)][0]


# ---------------------------------------------------------------------------------------------- AI outputs
def test_ai_measures_by_status_category_and_period(jr):
    ai_output("published", days_ago=1)
    ai_output("superseded", "superseded by output 9", days_ago=40)
    ai_output("rejected", "rejected: bullet 1: no verified citation", days_ago=2)
    ai_output("rejected", "retried: bullet 1: numbers not in its cited passages: 25", days_ago=3)
    ai_output("rejected", "rejected: 2 bullets, not 3 to 6", days_ago=4)
    ai_output("rejected", "rejected: the reply stopped with max_tokens", days_ago=5)
    ai_output("rejected", "stale: the target's input changed before its batch was collected", days_ago=100)
    ai_output("draft", "held: mismatch: the AI read 'a'", days_ago=6)
    ai_output("draft", None, days_ago=0.5)                                    # in flight: counted as an output only
    f, rows = compute()
    for period, expected in (("30d", {"ai_outputs": 7, "ai_published": 1, "ai_rejected_citation": 1,
                                      "ai_rejected_numbers": 1, "ai_rejected_format": 1, "ai_rejected_stopped": 1,
                                      "ai_rejected_other": 0, "ai_held_review": 1}),
                             ("90d", {"ai_outputs": 8, "ai_published": 2, "ai_rejected_other": 0}),
                             ("all", {"ai_outputs": 9, "ai_published": 2, "ai_rejected_other": 1, "ai_held_review": 1})):
        for measure, v in expected.items():
            assert value(rows, period, measure) == v, (period, measure)
    assert not f.unmatched                                                    # the stale reason is a known "other"


def test_a_row_exactly_on_the_edge_is_outside_the_period(jr):
    ai_output("published", days_ago=30)                                       # exactly 30 days before the run's time
    ai_output("published", days_ago=29.99)
    _, rows = compute()
    assert value(rows, "30d", "ai_outputs") == 1 and value(rows, "90d", "ai_outputs") == 2


def test_an_unmapped_reason_counts_as_other_and_warns(jr):
    ai_output("rejected", "rejected: a brand new kind of problem", days_ago=1)
    f, rows = compute()
    assert value(rows, "all", "ai_rejected_other") == 1
    warn = {c.name: c for c in evaluate(f)}["other_reasons"]
    assert not warn.passed and not warn.blocking and "a brand new kind of problem" in warn.detail


def test_a_shared_task_takes_its_documents_or_events_module(jr):
    ai_task("qtest_shared", "core")
    ev = admin("""insert into core.event (event_type, entity_id, slug, title, starts_on, status)
                  values ('policy_meeting', 1, 'qtest-ev', 'x', '2026-10-01', 'held') returning id""")[0][0]
    ai_output("published", task="qtest_shared", event_id=ev)
    s = Settings(("policy", M), {}, [])
    f = Fetched(NOW, s)
    with psycopg.connect(JOB) as tx:
        QualitySummary().load(tx, f, 0)
        tx.commit()
    got = {(r[0], r[1]): r[2] for r in admin("select module, measure, value from core.quality_summary where period = 'all'")}
    assert got[("policy", "ai_published")] >= 1 and got[(M, "ai_outputs")] == 0


# ---------------------------------------------------------------------------------------------- loads and drills
def test_loads_skip_skips_and_running_and_split_by_status_and_period(jr):
    key = task()
    for status, days in (("success", 1), ("success", 2), ("checks_failed", 3), ("failed", 4), ("skipped_not_due", 1),
                         ("running", 1), ("checks_failed", 60), ("success", 200)):
        task_run(key, status, days)
    other = task("other", module="qtest2")
    task_run(other, "failed", 1)                                              # another module's
    _, rows = compute()
    assert [value(rows, "30d", m) for m in ("loads", "loads_rolled_back", "loads_failed")] == [4, 1, 1]
    assert [value(rows, "90d", m) for m in ("loads", "loads_rolled_back", "loads_failed")] == [5, 2, 1]
    assert [value(rows, "all", m) for m in ("loads", "loads_rolled_back", "loads_failed")] == [6, 2, 1]
    admin("delete from ops.task_run where task_key = 'qtest2.other'")
    admin("delete from ops.task where key = 'qtest2.other'")


def test_drills_count_closed_records_by_their_module(jr):
    for drill, passed, days, fin in (("d3_suppression", True, 2, True), ("d4_freshness", False, 3, True),
                                     ("d3_suppression", True, 50, True), ("d4_freshness", None, 1, False)):
        admin("""insert into ops.drill_run (drill, target, started_at, finished_at, passed, github_run_id)
                 values (%s, 'dev', %s, %s, %s, 999)""", drill, at(days), at(days) + timedelta(minutes=3) if fin else None, passed)
    _, rows = compute()
    assert [value(rows, "30d", m) for m in ("drills_run", "drills_passed")] == [2, 1]
    assert [value(rows, "all", m) for m in ("drills_run", "drills_passed")] == [3, 2]
    other = Settings((M, "qtest2"), {"d3_suppression": M, "d4_freshness": "qtest2"}, [])
    f = Fetched(NOW, other)
    with psycopg.connect(JOB) as tx:
        QualitySummary().load(tx, f, 0)
        tx.commit()
    got = {(r[0], r[1]): r[2] for r in admin("select module, measure, value from core.quality_summary where period = 'all'")}
    assert (got[(M, "drills_run")], got[("qtest2", "drills_run")]) == (2, 1)


# ---------------------------------------------------------------------------------------------- freshness
def oracle(successes: list[datetime], overdue: timedelta, start: datetime | None) -> tuple[int, int]:
    """Brute force: each completed UTC day from the first success (and the period's first day), on time when the last
    success before the day's end is no older than `overdue` at that end."""
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    day = successes[0].replace(hour=0, minute=0, second=0, microsecond=0)
    if start is not None:
        day = max(day, start.replace(hour=0, minute=0, second=0, microsecond=0))
    ok = total = 0
    while day < midnight:
        end = day + timedelta(days=1)
        last = max((s for s in successes if s < end), default=None)
        total += 1
        ok += last is not None and end - last <= overdue
        day = end
    return ok, total


def test_freshness_share_is_derived_out_of_the_task_days_counted(jr):
    key = task(overdue="2 days")
    task(name="off", enabled=False)
    days = [60 - i for i in range(20)] + [15 - i for i in range(15)]       # daily, a gap, daily again
    succ = sorted(at(d - 0.4) for d in days)
    for d in days:
        task_run(key, "success", d - 0.4)
    task_run("qtest.off", "success", 5)                                         # a disabled task isn't counted
    _, rows = compute()
    for period, start in (("30d", NOW - timedelta(days=30)), ("90d", NOW - timedelta(days=90)), ("all", None)):
        ok, total = oracle(succ, timedelta(days=2), start)
        share, n, label = rows[(M, period, "freshness_on_time_share")][:3]
        assert (n, label) == (total, "derived") and share == Decimal(ok) / Decimal(total), period
    assert rows[(M, "30d", "freshness_on_time_share")][1] == 30


def test_no_task_days_means_no_freshness_row(jr):
    task()                                                                      # no success yet
    _, rows = compute()
    assert (M, "all", "freshness_on_time_share") not in rows and (M, "all", "loads") in rows


# ---------------------------------------------------------------------------------------------- gates and fields
def test_gates_pass_only_on_a_row_that_meets_the_rule(jr):
    for s, m, v in (("qtest_set_a", "accuracy", 0.85), ("qtest_set_b", "accuracy", 0.79),
                    ("qtest_set_d", "rejected_share", 0.05)):
        admin("insert into core.eval_summary (eval_set, model, metric, value, n_items, run_at) values (%s,'m',%s,%s,30,%s)",
              s, m, v, NOW)
    admin("insert into core.eval_summary (eval_set, model, metric, value, n_items, run_at) values ('qtest_set_c','m','other',0.99,30,%s)", NOW)
    _, rows = compute()
    assert [value(rows, p, m) for p in ("30d", "all") for m in ("eval_gates_passed", "eval_gates_pending")] == [2, 2, 2, 2]


def test_a_max_rule_is_strict(jr):
    admin("insert into core.eval_summary (eval_set, model, metric, value, n_items, run_at) values ('qtest_set_d','m','rejected_share',0.10,30,%s)", NOW)
    _, rows = compute()
    assert value(rows, "all", "eval_gates_passed") == 0


def test_fields_count_by_the_prefix_module_and_the_ai_tasks_module(jr):
    ai_task("qtest_gated", M)
    for key, status in (("qtest.a", "hidden"), ("qtest.b", "hidden"), ("qtest.c", "labelled"), ("ai.qtest_gated", "hidden"),
                        ("qtest.d", "shown")):                           # ai.doc_summary (core, labelled) is in the seed
        admin("insert into core.field_policy (field_key, status, label, reason) values (%s,%s,%s,'t')", key, status,
              "derived" if status == "labelled" else None)
    _, rows = compute()
    assert (value(rows, "all", "fields_hidden"), value(rows, "all", "fields_labelled")) == (3, 1)
    assert value(rows, "30d", "fields_hidden") == 3                              # the same in every period


# ---------------------------------------------------------------------------------------------- re-runs and checks
def test_a_rerun_gives_the_same_rows(jr):
    key = task()
    ai_output("published")
    task_run(key, "success", 1.4)
    task_run(key, "success", 0.4)
    first = compute()[1]
    second = compute()[1]
    assert first == second and len(first) == len(set(first))
    assert len(admin("select 1 from core.quality_summary")) == len(first)


def test_every_measure_written_is_in_the_migrations_list(jr):
    task_run(task(), "success", 2)
    ai_output("published")
    sql = Path(__file__).resolve().parents[2].joinpath("supabase/migrations/20261030002100_quality_summary.sql").read_text()
    listed = set(re.findall(r"'(\w+)'", re.search(r"measure\s+text\s+not null check \(measure in \((.*?)\)\)", sql, re.DOTALL).group(1)))
    assert set(qs.MEASURES) == listed
    _, rows = compute()
    assert {m for _, _, m in rows} <= listed and len(listed) == 18


def test_the_checks_refuse_an_unknown_measure_a_negative_value_a_share_over_one_and_a_double():
    def checks(rows):
        f = Fetched(NOW, SETTINGS)
        f.rows = rows
        return {c.name: c for c in evaluate(f)}
    R = qs.Row
    assert all(c.passed for c in checks([R(M, "all", "loads", Decimal(3)), R(M, "all", "freshness_on_time_share", Decimal("0.5"), 4, "derived")]).values())
    assert not checks([R(M, "all", "task_name", Decimal(1))])["measures"].passed
    assert not checks([R("elsewhere", "all", "loads", Decimal(1))])["measures"].passed
    assert not checks([R(M, "all", "loads", Decimal(-1))])["values"].passed
    assert not checks([R(M, "all", "freshness_on_time_share", Decimal("1.2"), 3, "derived")])["values"].passed
    assert not checks([R(M, "all", "loads", Decimal(1)), R(M, "all", "loads", Decimal(1))])["unique"].passed
    assert all(c.blocking for n, c in checks([]).items() if n != "other_reasons")


def test_nothing_is_replaced_when_a_blocking_check_fails(jr):
    admin("insert into core.quality_summary (module, period, measure, value, as_of) values ('keep', 'all', 'loads', 7, %s)", NOW)
    bad = Settings((M,), {}, [Gate("x", M, "s", "m", Decimal(-5), None)])
    f = Fetched(NOW, bad)
    f.rows.append(qs.Row("zzz", "all", "loads", Decimal(1)))                     # pre-seeded: an unlisted module
    with psycopg.connect(JOB) as tx:
        QualitySummary().load(tx, f, 0)
        tx.commit()
    assert admin("select module from core.quality_summary") == [("keep",)]


# ---------------------------------------------------------------------------------------------- wiring
def test_tasks_yaml_has_the_entry_and_the_task_waits_for_every_policy_task():
    t = config.load()["site.quality_summary"]
    assert t.rules == {"daily": "every 20 hours"} and t.overdue_after == timedelta(days=3) and not t.uses_nse
    assert t.module == "site" and not t.visitor_refreshable
    assert set(t.run_after) == {k for k in config.load() if k.startswith("policy.")}
    assert QualitySummary.key == "site.quality_summary" and qs.load_settings().modules == ("policy",)


def test_the_gates_file_loads_and_names_only_listed_modules():
    s = qs.load_settings()
    assert len(s.gates) == 7 and {g.module for g in s.gates} == {"policy"}
    assert next(g for g in s.gates if g.key == "citation_integrity").maximum == Decimal("0.10")


def test_due_skips_with_not_set_up_while_the_table_is_missing():
    class Db:
        def __init__(self, present): self.present, self.sql = present, ""
        def execute(self, sql, *a):
            self.sql = sql
            return self
        def fetchone(self): return (self.present,) if "to_regclass" in self.sql else (None,)
    d = QualitySummary().due(Db(False), "daily")
    assert not d.due and d.reason.startswith("not set up")
    assert QualitySummary().due(Db(True), "daily").due


def test_a_run_through_the_runner_as_job_writer_writes_the_rows(jr):
    admin("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
             values (%s, 'site', 'quality test', '{daily}', '20 hours', '3 days', false) on conflict do nothing""", "qtest.quality")
    class T(QualitySummary):
        key = "qtest.quality"
    t = T()
    t.workflows = ("daily",)
    ai_output("published")
    before = qs.load_settings
    qs.load_settings = lambda *a, **k: SETTINGS
    try:
        code = run(JOB, "daily", [t], only="qtest.quality", force=True, clock=lambda: NOW)
    finally:
        qs.load_settings = before
    tr = admin("select status, rows_written, checks from ops.task_run where task_key = 'qtest.quality' order by id desc limit 1")[0]
    assert code == 0 and tr[0] == "success" and tr[1] == len(admin("select 1 from core.quality_summary"))
    assert {c["name"] for c in tr[2]} == {"measures", "values", "unique", "other_reasons"}
    assert admin("select value from core.quality_summary where module = 'qtest' and period = 'all' and measure = 'ai_published'") == [(Decimal(1),)]
