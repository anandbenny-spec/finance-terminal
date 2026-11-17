"""The command bar's example chips (K-09, #588; Spec 07 §4 "Examples", §9 step 4; Spec 01 §4.6): the chips file, the task
`cmdbar.examples` (disabled until go-live; recomputes a chip only when its data_version moved; an answer that changes is
unchecked), the route's steps 4 and 5 for an asker that isn't a visitor (`route.answer`), and the dev trial's guards."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from ai_fakes import claude
from test_cmdbar_route import ENV, NOW, ROWS, SQL, SUMMARY, FakeReader, FakeStore, model

from ft.ai import sqlguard
from ft.cmdbar import cache
from ft.cmdbar import route as r
from ft.cmdbar import sql as s
from ft.cmdbar.log import LogRow
from ft.jobs.due import Due
from ft.jobs.runner import Context
from ft.jobs.tasks import cmdbar_examples as ex

ROOT = Path(__file__).resolve().parents[2]
FRESH = {"policy.fed_documents": NOW - timedelta(hours=5), "policy.market_series": NOW - timedelta(hours=3),
         "equity.prices": NOW - timedelta(days=2)}
MOVED = {**FRESH, "policy.market_series": NOW - timedelta(minutes=10)}
CHIP = ex.Chip("When did the RBI last change the repo rate, and by how much?", "policy", 1)
DOC_CHIP = ex.Chip("What changed in the RBI's latest resolution?", "policy", 4)


def data_answer(rows=(("2025-12-05", "-25"),), summary=SUMMARY, sql=None):
    cols = [{"name": "meeting_date", "kind": "date"}, {"name": "policy_rate_change_bps", "kind": "number"}]
    return r.final("answered", route="data", summary=summary, columns=cols, rows=[list(x) for x in rows],
                   sql=sql or sqlguard.guard(SQL), view="policy_meetings", ms=1200)


def doc_answer(*doc_ids, text="The MPC kept the repo rate at 5.50%."):
    docs = [{"document_id": d, "bank": "RBI", "doc_type": "mpc_resolution", "title": "Resolution", "date": "2026-10-01"}
            for d in doc_ids]
    cites = [{"text": text, "refs": [{"document": 0, "char_start": 10, "char_end": 40, "quote": "kept the repo rate"}]}]
    return r.final("answered", route="document", summary=text, citations=cites, documents=docs, ms=900)


def asked(out, keep=True, cost="0.004"):
    row = LogRow("", "q", cost_usd=Decimal(cost))
    return lambda q: (out, keep, row)


def never(*_):
    raise AssertionError("must not be called")


# ------------------------------------------------------------------ the chips file
def test_the_shipped_chips_are_spec_01s_plus_two_nifty_50_ones():
    chips = ex.load_examples()
    assert ex.MIN_CHIPS <= len(chips) <= ex.MAX_CHIPS == 6
    qs = [c.question for c in chips]
    for spec01 in ("When did the RBI last change the repo rate, and by how much?",            # Spec 01 §4.6, chips 1, 2, 4, 6
                   "How many FOMC dissents were there in each year since 2019?",
                   "What did the RBI project for CPI inflation at its latest meeting?",
                   "What changed in the RBI's latest resolution?"):
        assert spec01 in qs
    assert sum(c.module == "equity" and "Nifty 50" in c.question for c in chips) == 2
    assert [c.display_order for c in chips] == list(range(1, len(chips) + 1))


@pytest.mark.parametrize("body,why", [
    ("examples: []", "4 to 6"),
    ("examples:\n" + "".join(f"  - {{question: 'q{i}?', module: policy, display_order: {i}}}\n" for i in range(1, 8)), "4 to 6"),
    ("examples:\n" + "".join(f"  - {{question: 'q{i}?', module: crypto, display_order: {i}}}\n" for i in range(1, 5)), "module"),
    ("examples:\n" + "".join(f"  - {{question: 'q{i}?', module: policy, display_order: 1}}\n" for i in range(1, 5)),
     "display_order"),
    ("examples:\n" + "".join(f"  - {{question: 'Same question?', module: policy, display_order: {i}}}\n" for i in range(1, 5)),
     "same question"),
    ("examples:\n" + "".join(f"  - {{question: 'q{i}?', module: policy, display_order: {i}, route: data}}\n"
                             for i in range(1, 5)), "exactly"),
    ("examples:\n" + "".join(f"  - {{question: '{'x' * 501}', module: policy, display_order: {i}}}\n" for i in range(1, 5)),
     "500"),
])
def test_a_chips_file_that_breaks_a_rule_is_refused(tmp_path, body, why):
    f = tmp_path / "examples.yaml"
    f.write_text(body)
    with pytest.raises(ex.ExamplesError, match=why):
        ex.load_examples(f)


# ------------------------------------------------------------------ the task's configuration
def test_the_task_is_registered_in_daily_and_ships_disabled():
    from ft.jobs import config
    from ft.jobs.tasks import registry
    t = config.load()["cmdbar.examples"]
    assert registry()["cmdbar.examples"] is ex.CmdbarExamples
    assert t.workflows == ("daily",) and not t.enabled and not t.uses_nse and not t.visitor_refreshable
    assert set(t.run_after) >= {"policy.reactions", "policy.ai_batch"}       # the day's data is in before a chip is asked


# ------------------------------------------------------------------ what each chip needs (plan_chip)
def test_an_answer_whose_data_version_hasnt_moved_runs_and_asks_nothing():
    a = data_answer()
    old = ex.Stored(CHIP.question, a, cache.answer_version(a, FRESH), True)
    p = ex.plan_chip(CHIP, old, FRESH, ask=never, reread=never)
    assert p.outcome == "unchanged" and not p.write


def test_a_moved_version_with_the_same_rows_keeps_the_checked_answer_with_no_model_call():
    a = data_answer()
    old = ex.Stored(CHIP.question, a, cache.answer_version(a, FRESH), True)
    p = ex.plan_chip(CHIP, old, MOVED, ask=never, reread=lambda ans: True)
    assert p.outcome == "rows_unchanged" and p.write and not p.reset
    assert p.answer is a and p.data_version == cache.answer_version(a, MOVED) != old.data_version


def test_moved_rows_are_asked_again_and_a_changed_answer_is_unchecked():
    a, new = data_answer(), data_answer(rows=(("2026-10-01", "25"),), summary="The RBI raised by 25 basis points.")
    old = ex.Stored(CHIP.question, a, cache.answer_version(a, FRESH), True)
    p = ex.plan_chip(CHIP, old, MOVED, ask=asked(new), reread=lambda ans: False)
    assert p.outcome == "changed" and p.write and p.reset and p.answer is new
    assert p.data_version == cache.answer_version(new, MOVED) and p.cost == Decimal("0.004")


def test_a_data_answer_asked_again_with_the_same_rows_and_summary_stays_checked():
    a = data_answer()
    new = data_answer(sql=sqlguard.guard(SQL + " limit 5"))                 # another query, the same rows and summary
    old = ex.Stored(CHIP.question, a, "an older version", True)
    p = ex.plan_chip(CHIP, old, MOVED, ask=asked(new), reread=lambda ans: False)
    assert p.outcome == "kept" and p.write and not p.reset and p.answer is new      # the query that ran is the one shown


def test_the_same_rows_with_another_summary_is_a_changed_answer():
    a = data_answer()
    old = ex.Stored(CHIP.question, a, "an older version", True)
    p = ex.plan_chip(CHIP, old, MOVED, ask=asked(data_answer(summary="It cut by 25 bps.")), reread=lambda ans: False)
    assert p.outcome == "changed" and p.reset


def test_a_document_answer_citing_the_same_documents_keeps_the_checked_answer():
    a = doc_answer(11, 12)
    old = ex.Stored(DOC_CHIP.question, a, cache.answer_version(a, FRESH), True)
    p = ex.plan_chip(DOC_CHIP, old, MOVED, ask=asked(doc_answer(12, 11, text="Reworded.")), reread=never)
    assert p.outcome == "kept" and p.answer is a and not p.reset
    assert p.data_version == cache.answer_version(a, MOVED)


def test_a_document_answer_citing_another_document_is_unchecked():
    a = doc_answer(11)
    old = ex.Stored(DOC_CHIP.question, a, cache.answer_version(a, FRESH), True)
    new = doc_answer(11, 30)
    p = ex.plan_chip(DOC_CHIP, old, MOVED, ask=asked(new), reread=never)
    assert p.outcome == "changed" and p.answer is new and p.reset


@pytest.mark.parametrize("out,keep", [
    (r.final("cant_answer", route="data", summary=r.CANT_TEXT), False),              # the database down, a refused call
    (r.final("paused", summary=r.PAUSED_TEXT), False),                                # AI unavailable mid-run
    (r.final("not_found", route="document", summary=r.NOT_FOUND_TEXT), False),
])
def test_a_failure_leaves_the_stored_answer_as_it_was(out, keep):
    a = data_answer()
    old = ex.Stored(CHIP.question, a, "an older version", True)
    p = ex.plan_chip(CHIP, old, MOVED, ask=asked(out, keep), reread=lambda ans: False)
    assert p.outcome == "failed" and not p.write and p.cost == Decimal("0.004")
    assert p.error_class == out["state"] and p.line() == f"  chip 1, failed, {out['state']}"     # a class, never an error's text


def test_a_new_chip_is_stored_unchecked_and_a_cant_answer_has_no_version():
    p = ex.plan_chip(CHIP, None, FRESH, ask=asked(data_answer()), reread=never)
    assert p.outcome == "new" and p.reset and p.data_version == cache.answer_version(data_answer(), FRESH)
    cant = r.final("cant_answer", route="data", summary=r.CANT_TEXT)
    p = ex.plan_chip(CHIP, None, FRESH, ask=asked(cant, True), reread=never)
    assert p.outcome == "new" and p.write and p.data_version is None             # asked again next run
    old = ex.Stored(CHIP.question, cant, None, False)
    p = ex.plan_chip(CHIP, old, FRESH, ask=asked(data_answer()), reread=never)
    assert p.outcome == "changed" and p.reset


@pytest.mark.parametrize("outcome", ["ai_off", "capped"])
def test_nothing_sent_writes_nothing(outcome):
    p = ex.plan_chip(CHIP, None, FRESH, ask=lambda q: outcome, reread=never)
    assert p.outcome == outcome and not p.write and p.cost == 0


# ------------------------------------------------------------------ re-running a stored query (no model call)
def test_a_stored_query_runs_again_through_the_guard_and_matches_its_rows():
    reader = FakeReader(ROWS)
    a = r.final("answered", route="data", summary=SUMMARY, sql=sqlguard.guard(SQL),
                **dict(zip(("columns", "rows"), r.table(ROWS.columns, ROWS.rows), strict=True)))
    assert ex.rerun(reader, a) is True and reader.ran == [sqlguard.guard(sqlguard.guard(SQL))]
    moved = FakeReader(s.Result(ROWS.columns, [(datetime(2026, 10, 1).date(), Decimal(25))]))
    assert ex.rerun(moved, a) is False
    assert ex.rerun(FakeReader(s.QueryError("QueryCanceled")), a) is False
    assert ex.rerun(FakeReader(ROWS), {**a, "sql": "delete from ops.cmdbar_example"}) is False       # the guard refuses it
    assert ex.rerun(FakeReader(ROWS), doc_answer(1)) is False


# ------------------------------------------------------------------ the route's steps 4 and 5 alone (route.answer)
def _deps(fake, reader, store):
    return r.Deps(env=dict(ENV), store=lambda e: store, client=lambda e: claude(fake), reader=lambda e: reader,
                  prompt=lambda rd, e: s.sql_prompt(s.render_views([s.ViewDoc("policy_meetings", "One row per meeting.",
                                                                               (("bank", "text", "Fed or RBI"),))])),
                  now=lambda: NOW)


def test_route_answer_runs_the_router_and_the_data_route_with_no_switch_limits_cache_or_log():
    store, fake, reader = FakeStore(enabled=False), model(), FakeReader()
    out, keep, row = r.answer("When did the RBI last cut, and by how much?", _deps(fake, reader, store), store)
    assert out["state"] == "answered" and out["route"] == "data" and out["summary"] == SUMMARY and keep
    assert len(fake.requests) == 3 and row.cost_usd > 0 and row.models and row.route == "data"
    assert store.calls == [] and store.logs == [] and store.entries == {}       # the switch, limits, cache and log are the visitor's


def test_route_answer_never_raises():
    store = FakeStore()
    deps = _deps(model(), FakeReader(), store)
    deps.client = lambda e: (_ for _ in ()).throw(RuntimeError("boom"))
    out, keep, row = r.answer("When did the RBI last cut?", deps, store)
    assert out["state"] == "cant_answer" and not keep and row.error_class == "internal"
    assert "boom" not in json.dumps(out)


def test_a_page_answer_is_not_a_chips_answer():
    store = ex._Pages(FRESH)
    out, keep, _ = r.answer("Show me the RBI's last meeting", _deps(model(route="page", slug="rbi-2026-10-01"),
                                                                     FakeReader(), store), store)
    assert out["state"] == "cant_answer" and not keep                 # not stored; asked again next run


# ------------------------------------------------------------------ the task's fetch (spend, keys, the reader)
class FakeDB:
    def __init__(self, rows=(), budget="15", spend="0"):
        self.rows, self.budget, self.spend = list(rows), budget, spend

    def execute(self, sql, params=None):
        class Cur:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return self._rows[0] if self._rows else None
        if "from ops.cmdbar_example" in sql:
            return Cur(self.rows)
        if "from core.task_freshness" in sql:
            return Cur(list(FRESH.items()))
        if "ai_monthly_budget_usd" in sql:
            return Cur([(json.loads(self.budget),)])
        if "from ops.task_run" in sql:
            return Cur([(Decimal(self.spend),)])
        raise AssertionError(sql)


class FakeFetcher:
    def __init__(self, db):
        self.db = db


def ctx(db):
    return Context(workflow="daily", job_run_id=1, task_run_id=1, now=NOW, due=Due(True, "forced"),
                   open_fetcher=lambda: FakeFetcher(db))


def task(env=None, out=None, reader=None):
    t = ex.CmdbarExamples(env=dict(ENV) if env is None else env)
    calls = []

    def ask(question, deps, store):
        calls.append(question)
        return (out or data_answer()), True, LogRow("", question, cost_usd=Decimal("0.004"))
    t.ask_fn, t.reader_fn, t.calls = ask, (lambda e: reader or FakeReader()), calls
    return t


def test_with_no_reader_secret_nothing_runs_and_the_blocking_check_fails():
    t = task(env={**ENV, "SQL_READER_DATABASE_URL": ""})
    run = t.fetch(ctx(FakeDB()))
    assert not run.reader_set and run.plans == [] and t.calls == []


def test_with_no_anthropic_key_nothing_is_asked():
    t = task(env={**ENV, "ANTHROPIC_API_KEY": ""})
    run = t.fetch(ctx(FakeDB()))
    assert {p.outcome for p in run.plans} == {"ai_off"} and t.calls == [] and run.cost == 0


def test_the_run_cap_and_the_monthly_budget_stop_the_asking():
    t = task()
    run = t.fetch(ctx(FakeDB()))
    assert [p.outcome for p in run.plans] == ["new"] * 6 and run.cost == Decimal("0.024")
    t = task()
    run = t.fetch(ctx(FakeDB(budget="15", spend="14.95")))                 # one more chip's worst case would reach the budget
    assert {p.outcome for p in run.plans} == {"capped"} and t.calls == []
    t = task()
    ex_cap, ex.RUN_CAP_USD = ex.RUN_CAP_USD, ex.WORST_CHIP_USD + Decimal("0.001")      # room for one chip
    try:
        run = t.fetch(ctx(FakeDB()))
    finally:
        ex.RUN_CAP_USD = ex_cap
    assert [p.outcome for p in run.plans] == ["new", "capped", "capped", "capped", "capped", "capped"]


def test_the_run_prints_counts_and_classes_never_a_row_or_a_summary(capsys):
    t = task()
    t.fetch(ctx(FakeDB()))
    out = capsys.readouterr().out
    assert "chip 1, new, data, answered, 1 rows" in out
    assert SUMMARY not in out and "2025-12-05" not in out and "-25" not in out


# ------------------------------------------------------------------ the dev trial (cmdbar-gates.yml's `examples`)
@pytest.mark.parametrize("argv", [[], ["--target", "production"]])
def test_the_trial_is_dev_only(argv):
    with pytest.raises(SystemExit):
        ex.main(argv, env={"JOB_DATABASE_URL": "postgresql://job_writer@localhost/none"})


def test_the_trial_needs_the_job_writers_connection():
    with pytest.raises(SystemExit):
        ex.main(["--target", "dev"], env={})


def test_the_workflows_wire_the_trial_and_the_reader_secret():
    gates = (ROOT / ".github" / "workflows" / "cmdbar-gates.yml").read_text()
    assert "options: [cmdbar_sql, cmdbar_router, retrieval, examples]" in gates
    assert "python -m ft.jobs.tasks.cmdbar_examples --target dev" in gates
    assert "- run: uv sync --locked --extra jobs" in gates               # the runner's fetcher needs curl_cffi (dev run 1, #588)
    daily = (ROOT / ".github" / "workflows" / "daily.yml").read_text()
    assert ("SQL_READER_DATABASE_URL: ${{ env.FT_TARGET == 'production' && secrets.SQL_READER_DATABASE_URL || "
            "env.FT_TARGET == 'dev' && secrets.DEV_SQL_READER_DATABASE_URL || '' }}") in daily


# ------------------------------------------------------------------ the load, as job_writer, and what visitors then see
@pytest.mark.db
def test_the_load_stores_answers_unchecks_changed_and_removed_chips_and_visitors_see_checked_ones_only():
    import psycopg
    from conftest import dsn
    tag = "k09 test"
    chips = [ex.Chip(f"{tag} chip {i}?", "policy", i) for i in range(1, 5)]
    gone = f"{tag} retired chip?"
    with psycopg.connect(dsn("postgres"), autocommit=True) as db:
        db.execute("""insert into ops.cmdbar_example (question, module, display_order, answer, data_version, verified) values
                        (%s, 'policy', 1, %s, 'v1', true), (%s, 'policy', 2, %s, 'v1', true), (%s, 'policy', 9, '{}', 'v1', true)""",
                   (chips[0].question, json.dumps(data_answer()), chips[1].question, json.dumps(data_answer()), gone))
    try:
        new = data_answer(rows=(("2026-10-01", "25"),))
        plans = [ex.Plan(chips[0], "rows_unchanged", write=True, answer=data_answer(), data_version="v2"),
                 ex.Plan(chips[1], "changed", write=True, answer=new, data_version="v2", reset=True),
                 ex.Plan(chips[2], "new", write=True, answer=new, data_version="v2", reset=True),
                 ex.Plan(chips[3], "failed")]
        run = ex.Run(chips, plans, cost=Decimal("0.012"))
        t = ex.CmdbarExamples(env=dict(ENV))
        with psycopg.connect(dsn("job_writer")) as jw, jw.transaction():
            assert t.load(jw, run, None) >= 4
            checks = {c.name: c for c in t.checks(jw, run, None)}
        assert checks["chips"].passed and checks["reader"].passed and not checks["answers"].passed
        assert json.loads(checks["answers"].detail) == {"rows_unchanged": 1, "changed": 1, "new": 1, "failed": 1,
                                                        "failed_classes": {"4": None}}
        assert not checks["answers"].blocking and not checks["ai_spend"].blocking
        with psycopg.connect(dsn("postgres"), autocommit=True) as db:
            got = {q: (v, ok, a) for q, v, ok, a in db.execute(
                "select question, data_version, verified, answer from ops.cmdbar_example where question like %s", (tag + "%",))}
            assert got[chips[0].question][:2] == ("v2", True)                # rows unchanged: still checked
            assert got[chips[1].question][:2] == ("v2", False) and got[chips[1].question][2] == new   # changed: unchecked
            assert got[chips[2].question][:2] == ("v2", False)               # new: unchecked
            assert got[chips[3].question] == (None, False, None)             # failed: a row, nothing asked yet
            assert got[gone][1] is False                                     # out of the file: unchecked
            db.execute("set role service_role")
            shown = [e["question"] for e in db.execute("select api.cmdbar_examples()").fetchone()[0]]
            assert [q for q in shown if q.startswith(tag)] == [chips[0].question]
    finally:
        with psycopg.connect(dsn("postgres"), autocommit=True) as db:
            db.execute("delete from ops.cmdbar_example where question like %s", (tag + "%",))


@pytest.mark.db
def test_job_writer_reads_what_the_fetch_needs():
    import psycopg
    from conftest import dsn
    with psycopg.connect(dsn("job_writer"), autocommit=True) as jw:
        for sql in ("select question, answer, data_version, verified from ops.cmdbar_example limit 1",
                    "select task_key, last_success_at from core.task_freshness limit 1",
                    "select value from ops.setting where key = 'ai_monthly_budget_usd'"):
            jw.execute(sql).fetchall()
