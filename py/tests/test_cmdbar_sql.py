"""The command bar's data route (Spec 07 §4 step 5a; Architecture 03 §1.7; K-04, #582): the guard's VIEWS equal to the semantic
views (in the migrations and as sql_reader sees them), the prompt built from the views' comments, the read-only 3-second run as
sql_reader, one Sonnet retry told the error's class only, and the summary kept only when its numbers are in the rows."""
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import yaml
from ai_fakes import FakeClaude, claude
from conftest import dsn

from ft.ai import sqlguard
from ft.ai.client import HAIKU, SONNET, TASKS, AIUnavailable, load_prompt
from ft.cmdbar import sql as s

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
SETS = ROOT / "py" / "config" / "eval" / "sets"


def migration_views() -> set[str]:
    """Every view in the `semantic` schema after all migrations, read from the migration files in order."""
    views: set[str] = set()
    made = re.compile(r"create\s+(?:or\s+replace\s+)?view\s+semantic\.(\w+)|drop\s+view\s+(?:if\s+exists\s+)?semantic\.(\w+)",
                      re.IGNORECASE)
    for f in sorted(MIGRATIONS.glob("*.sql")):
        for m in made.finditer(f.read_text()):
            if m.group(1):
                views.add(m.group(1).lower())
            else:
                views.discard(m.group(2).lower())
    return views


# ------------------------------------------------------------------ VIEWS
def test_the_guards_views_are_the_semantic_views_of_the_migrations():
    assert sqlguard.VIEWS == migration_views()


@pytest.mark.db
def test_the_guards_views_are_the_views_sql_reader_can_select():
    with psycopg.connect(dsn("sql_reader"), autocommit=True) as db:
        got = {r[0] for r in db.execute(
            """select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
               where n.nspname = 'semantic' and c.relkind in ('v', 'm', 'r', 'f', 'p')
                 and has_table_privilege(c.oid, 'select')""").fetchall()}
    assert got == sqlguard.VIEWS


# ------------------------------------------------------------------ the reader (test database)
@pytest.fixture
def reader():
    r = s.Reader(psycopg.connect(dsn("sql_reader"), autocommit=True, prepare_threshold=None))
    yield r
    r.close()


@pytest.mark.db
def test_describe_reads_every_view_and_column_with_its_comment(reader):
    docs, groups = reader.describe()
    assert [d.name for d in docs] == sorted(sqlguard.VIEWS)
    for d in docs:
        assert d.comment and d.columns
        assert all(com for _, _, com in d.columns), d.name               # every column has its COMMENT (K-03's test too)
    text = s.render_views(docs, groups)
    assert all(f"## {v}\n" in text for v in sqlguard.VIEWS) and "(no description)" not in text
    prompt = s.sql_prompt(text)
    assert "{{" not in prompt.text and "## call_guidance" in prompt.text


@pytest.mark.db
def test_each_run_is_read_only_with_a_three_second_timeout_and_the_semantic_path(reader):
    assert reader.run("show transaction_read_only").rows == [("on",)]
    assert reader.run("show statement_timeout").rows == [("3s",)]
    assert reader.run("show search_path").rows == [("semantic",)]
    assert reader.run("select 1 as one") == s.Result(["one"], [(1,)])
    assert reader.conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE      # rolled back, nothing left open


@pytest.mark.db
def test_a_query_error_carries_its_class_only(reader):
    with pytest.raises(s.QueryError) as e:
        reader.run(sqlguard.guard("select no_such_column_xyz from policy_meetings"))
    assert e.value.kind == "UndefinedColumn" and "no_such_column_xyz" not in str(e.value)
    with pytest.raises(s.QueryError) as e:
        reader.run("select pg_sleep(4)")                                 # the 3-second timeout (the guard refuses pg_sleep)
    assert e.value.kind == "QueryCanceled"
    assert reader.run("select 2 as two").rows == [(2,)]                  # the connection is usable after a failure


@pytest.mark.db
def test_sql_reader_cant_write_even_if_a_write_reached_it(reader):
    with pytest.raises(s.QueryError) as e:
        reader.run("insert into ops.setting (key, value, description) values ('x', '1', 'x')")
    assert e.value.kind in ("ReadOnlySqlTransaction", "InsufficientPrivilege", "UndefinedTable")


# ------------------------------------------------------------------ the prompt
DOCS = [s.ViewDoc("companies", "One row per company.", (("company", "text", "Company name."), ("currency", "text", "INR or USD."))),
        s.ViewDoc("policy_meetings", "One row per meeting.", (("bank", "text", "Fed or RBI"),))]


def test_the_prompt_is_the_rules_the_views_and_the_guards_own_lists():
    p = s.sql_prompt(s.render_views(DOCS, ["Banks", "IT services"]))
    assert p.task == "cmdbar_sql" and p.version == s.PROMPT_VERSION == "v2" and "{{" not in p.text and "<!--" not in p.text
    assert "## companies\nOne row per company.\n- company (text): Company name." in p.text
    assert "The peer_group values: 'Banks'; 'IT services'." in p.text
    assert all(f in p.text for f in sqlguard.FUNCS) and all(c in p.text for c in sqlguard.CAST_TYPES)
    assert "Never compare, rank, order, add, average" in p.text
    assert p.sha256 != s.sql_prompt(s.render_views(DOCS[:1])).sha256      # the descriptions are part of the prompt's hash


def _examples() -> list[tuple[str, dict]]:
    text = load_prompt("cmdbar_sql", s.PROMPT_VERSION).text.split("# Worked examples", 1)[1].split("# Views", 1)[0]
    qs = re.findall(r'^Question: "(.+)"$', text, flags=re.MULTILINE)
    answers = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    assert len(qs) == len(answers) >= 5
    return list(zip(qs, answers, strict=True))


def test_every_worked_example_passes_the_guard_and_one_is_unanswerable():
    for _, a in _examples():
        assert set(a) == {"answerable", "sql", "explanation"}
        if a["answerable"]:
            assert sqlguard.guard(a["sql"]).endswith("LIMIT 200") and not s.constant_column(a["sql"])
    assert any(not a["answerable"] for _, a in _examples())


def test_no_worked_example_is_an_item_of_either_split():
    """Spec 07 N5: the prompt mustn't carry the sets' own items. Compared in code, by question and by SQL."""
    def flat(t: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]", " ", t.lower()).split())
    items = yaml.safe_load((SETS / "cmdbar_sql.yaml").read_text())["items"]
    items += yaml.safe_load((SETS / "cmdbar_router.yaml").read_text())["items"]
    questions = {flat(i["question"]) for i in items}
    golds = {flat(i["gold_sql"]) for i in items if i.get("gold_sql")}
    for q, a in _examples():
        assert flat(q) not in questions
        assert not a["sql"] or flat(a["sql"]) not in golds


def test_the_task_is_haiku_medium_effort_json_and_the_request_is_cached():
    assert (TASKS["cmdbar_sql"].route, TASKS["cmdbar_sql"].effort, TASKS["cmdbar_sql"].model) == ("json", "medium", HAIKU)
    req = s.request("What was the repo rate?", s.sql_prompt(s.render_views(DOCS)))
    p = req.params()
    assert p["output_config"]["effort"] == "medium" and p["output_config"]["format"]["type"] == "json_schema"
    assert p["cache_control"]["type"] == "ephemeral" and not {"temperature", "top_p", "top_k"} & set(p)
    assert p["messages"][0]["role"] == "user" and len(p["messages"]) == 1              # no assistant prefill


# ------------------------------------------------------------------ answer_data, with a fake model and a fake reader
class FakeReader:
    """Runs nothing: answers each run from `script` in order (a Result, or an exception to raise)."""

    def __init__(self, *script):
        self.script, self.ran = list(script), []

    def run(self, guarded):
        self.ran.append(guarded)
        got = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(got, Exception):
            raise got
        return got


ROWS = s.Result(["meeting_date", "policy_rate_change_bps"], [(date(2025, 12, 5), -25), (date(2025, 10, 1), 0)])
GOOD_SQL = "select meeting_date, policy_rate_change_bps from policy_meetings where bank = 'RBI' order by meeting_date desc"


def model(sql_by_model=None, summary="The RBI cut by 25 basis points on 5 December 2025.", answerable=True):
    """A fake Claude answering the SQL task (by model: Haiku first, Sonnet on the retry) and the summary task."""
    sql_by_model = sql_by_model or {HAIKU: GOOD_SQL}

    def answer(body):
        first = next(iter(body["output_config"]["format"]["schema"]["properties"]))
        if first == "summary":
            return {"summary": summary}
        return {"answerable": answerable, "sql": sql_by_model.get(body["model"]) if answerable else None,
                "explanation": "The RBI's decisions."}
    return FakeClaude(answer=answer)


PROMPT = s.sql_prompt(s.render_views(DOCS))
Q = "When did the RBI last cut, and by how much?"


def test_a_question_is_answered_from_the_rows_with_a_grounded_summary():
    fake, rd = model(), FakeReader(ROWS)
    a = s.answer_data(claude(fake), rd, Q, PROMPT)
    assert a.state == "answered" and a.error_class is None and a.guard_verdict == "accepted"
    assert a.sql == sqlguard.guard(GOOD_SQL) and rd.ran == [a.sql]                    # Show SQL is what ran
    assert (a.columns, a.rows) == (ROWS.columns, ROWS.rows)
    assert a.summary_grounded and a.summary.startswith("The RBI cut by 25")
    assert a.models == [HAIKU] and len(fake.requests) == 2                             # the SQL, then the summary
    assert a.cost_usd > 0 and a.cost_usd == a.attempts[0].cost_usd + a.summary_cost_usd
    assert json.dumps(Q) in fake.requests[0]["messages"][0]["content"][-1]["text"]     # the question as a JSON string


def test_a_summary_with_a_number_the_rows_dont_hold_falls_back_to_n_rows():
    a = s.answer_data(claude(model(summary="The RBI cut by 50 basis points.")), FakeReader(ROWS), Q, PROMPT)
    assert a.state == "answered" and a.summary == "2 rows" and not a.summary_grounded


def test_no_rows_means_no_summary_call():
    fake = model()
    a = s.answer_data(claude(fake), FakeReader(s.Result(ROWS.columns, [])), Q, PROMPT)
    assert a.state == "answered" and a.summary == "0 rows" and len(fake.requests) == 1


def test_a_guard_rejection_is_retried_once_on_sonnet_told_the_class_only():
    fake, rd = model({HAIKU: "select * from core.event", SONNET: GOOD_SQL}), FakeReader(ROWS)
    a = s.answer_data(claude(fake), rd, Q, PROMPT)
    assert a.state == "answered" and a.models == [HAIKU, SONNET]
    assert a.attempts[0].error_class == "guard: relation not allowed" and a.attempts[1].error_class is None
    retry = fake.requests[1]
    assert retry["model"] == SONNET
    note = retry["messages"][0]["content"][-1]["text"]
    assert "guard: relation not allowed" in note and "core.event" not in json.dumps(retry)
    assert len(rd.ran) == 1                                                           # the rejected query never ran


def test_a_database_error_is_retried_once_told_its_class_only():
    fake = model({HAIKU: GOOD_SQL, SONNET: GOOD_SQL})
    rd = FakeReader(s.QueryError("UndefinedColumn"), ROWS)
    a = s.answer_data(claude(fake), rd, Q, PROMPT)
    assert a.state == "answered" and a.models == [HAIKU, SONNET] and len(rd.ran) == 2
    assert "database: UndefinedColumn" in fake.requests[1]["messages"][0]["content"][-1]["text"]


def test_a_second_failure_is_cant_answer_with_no_rows_no_query_and_no_raw_error():
    fake = model({HAIKU: "delete from ops.setting", SONNET: "select pg_sleep(9)"})
    rd = FakeReader(ROWS)
    a = s.answer_data(claude(fake), rd, Q, PROMPT)
    assert a.state == "cant_answer" and a.sql is None and a.rows == [] and a.summary is None
    assert a.error_class == "guard: function not allowed" and a.guard_verdict == "rejected"
    assert [x.error_class for x in a.attempts] == ["guard: only SELECT allowed", "guard: function not allowed"]
    assert len(fake.requests) == 2 and rd.ran == []


def test_an_unanswerable_question_runs_nothing():
    fake, rd = model(answerable=False), FakeReader(ROWS)
    a = s.answer_data(claude(fake), rd, "Will the RBI cut in December?", PROMPT)
    assert a.state == "unanswerable" and a.error_class is None and a.sql is None
    assert rd.ran == [] and len(fake.requests) == 1 and a.explanation


def test_a_reply_that_stopped_early_is_cant_answer_without_a_retry():
    fake = FakeClaude(script=("max_tokens",), answer=lambda body: {"answerable": True, "sql": GOOD_SQL, "explanation": ""})
    a = s.answer_data(claude(fake), FakeReader(ROWS), Q, PROMPT)
    assert a.state == "cant_answer" and a.error_class == "model_output" and len(fake.requests) == 1
    assert a.cost_usd > 0                                                             # billed, and counted


def test_the_database_being_down_is_not_retried():
    fake = model({HAIKU: GOOD_SQL, SONNET: GOOD_SQL})
    a = s.answer_data(claude(fake), FakeReader(s.ReaderDown("OperationalError")), Q, PROMPT)
    assert a.state == "cant_answer" and a.error_class == "database_unavailable" and len(fake.requests) == 1


def test_no_key_or_no_credit_passes_through_for_the_route_to_pause():
    with pytest.raises(AIUnavailable):
        s.answer_data(claude(FakeClaude(script=("401",))), FakeReader(ROWS), Q, PROMPT)


def test_a_refused_call_is_cant_answer():
    a = s.answer_data(claude(FakeClaude(script=("400",))), FakeReader(ROWS), Q, PROMPT)
    assert a.state == "cant_answer" and a.error_class == "ai_call" and a.cost_usd == Decimal(0)


def test_guard_class_drops_the_detail():
    for sql, cls in [("select * from core.event", "relation not allowed"), ("select pg_sleep(1)", "function not allowed"),
                     ("select 1; select 2", "exactly one statement allowed"), ("drop table x", "only SELECT allowed"),
                     ("select 'x'::regclass", "cast not allowed"), ("selec", "does not parse")]:
        with pytest.raises(sqlguard.Rejected) as e:
            sqlguard.guard(sql)
        assert s.guard_class(e.value) == cls


# ------------------------------------------------------------------ the constant-column check
@pytest.mark.parametrize("sql", [
    "select 6.5 as repo_rate",
    "select 'HDFC Bank' as company, count(*) as calls from earnings_calls where nse_symbol = 'HDFCBANK'",
    "select '2025-12-05'::date as d, bank from policy_meetings",
    "select null as x, bank from policy_meetings",
    "select * from (select 25 as cut) t",
    "select bank from policy_meetings union all select 'ECB'",
    "select * from (values (6.5), (5.5)) as v(rate)",
])
def test_a_query_that_writes_its_own_value_as_a_column_is_caught(sql):
    sqlguard.guard(sql)                                                  # the guard lets each through
    assert s.constant_column(sql)


@pytest.mark.parametrize("sql", [
    GOOD_SQL,
    "select round(policy_rate_pct, 1) as r, policy_rate_change_bps * -1 as cut from policy_meetings",
    "select case when policy_rate_change_bps < 0 then 'cut' else 'other' end as kind from policy_meetings",
    "select count(*) as n from policy_meetings where meeting_date >= date '2024-01-01'",
    "select coalesce(stance, 'n/a') as stance from policy_meetings",
])
def test_constants_inside_expressions_and_filters_pass(sql):
    assert not s.constant_column(sql)


def test_a_constant_column_is_retried_on_sonnet_told_the_class_only():
    fake = model({HAIKU: "select 6.5 as repo_rate", SONNET: GOOD_SQL})
    rd = FakeReader(ROWS)
    a = s.answer_data(claude(fake), rd, Q, PROMPT)
    assert a.state == "answered" and a.models == [HAIKU, SONNET] and len(rd.ran) == 1
    assert a.attempts[0].error_class == "check: constant column"
    assert "check: constant column" in fake.requests[1]["messages"][0]["content"][-1]["text"]
    assert "6.5" not in json.dumps(fake.requests[1])
