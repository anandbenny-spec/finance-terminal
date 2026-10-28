"""config/tasks.yaml loading and its sync into ops.task, and the run CLI's argument rules (Prompt 01 A2)."""
from datetime import timedelta

import psycopg
import pytest
from conftest import dsn

from ft.jobs import config
from ft.jobs.run import build_tasks, main, project_ref, refresh_force, resolve_trigger
from ft.jobs.runner import Task

JOB, ADMIN = dsn("job_writer"), dsn("postgres")


def test_repo_config_loads():
    tasks = config.load()
    assert set(tasks) == {"policy.calendar", "policy.fed_documents", "policy.rbi_documents", "policy.market_series",
                          "policy.reactions", "policy.ai_batch", "crypto.coingecko_markets", "equity.universe",
                          "equity.valuation_inputs", "equity.prices"}
    # the due-rule names are kept as tasks.yaml writes them (Prompt 01 A2)
    assert tasks["policy.fed_documents"].rules == {"daily": "every 20 hours", "watch": "fomc_decision_day"}
    assert tasks["policy.rbi_documents"].rules["watch"] == "mpc_decision_day"
    assert tasks["policy.ai_batch"].rules["watch"] == "after_decision_document"
    assert tasks["policy.calendar"].min_interval == timedelta(days=25)
    assert tasks["policy.fed_documents"].min_interval == timedelta(hours=20)
    assert tasks["policy.fed_documents"].overdue_after == timedelta(days=3)
    # every task is enabled since Pass 1b built policy.reactions (#70); `enabled: false` is still honoured (below)
    assert [k for k, t in tasks.items() if not t.enabled] == []


def with_reactions_disabled(tmp_path):
    """The repo config with policy.reactions switched off, as it was until Pass 1b (#70): the disabled path's tests
    keep running against a real config."""
    f = tmp_path / "tasks.yaml"
    text = config.CONFIG.read_text()
    marker = "  overdue_after: 4 days\npolicy.ai_batch:"
    assert text.count(marker) == 1      # policy.reactions' last line, just before policy.ai_batch
    f.write_text(text.replace(marker, "  overdue_after: 4 days\n  enabled: false\npolicy.ai_batch:"))
    return f


@pytest.mark.parametrize("body, message", [
    ("a.b: {description: x, workflows: {hourly: every 1 hours}, overdue_after: 1 days}", "unknown workflow"),
    ("a.b: {description: x, workflows: {daily: when_ready}, overdue_after: 1 days}", "unknown due rule"),
    ("a.b: {description: x, workflows: {daily: every 0 hours}, overdue_after: 1 days}", "not an interval"),
    ("a.b: {description: x, workflows: {watch: fomc_decision_day}, overdue_after: 1 days}", "at least one `every"),
    ("a.b: {description: x, workflows: {daily: every 1 days}, overdue_after: 1 days, run_after: [c.d]}", "unknown tasks"),
    (("a.b: {description: x, workflows: {daily: every 1 days}, overdue_after: 1 days, uses_nse: true,"
      " visitor_refreshable: true}"), "never visitor-refreshable"),
    ("a.b: {description: x, workflows: {daily: every 1 days}, overdue_after: 1 days, enabled: 'no'}", "true or false"),
    ("a.b: {description: x, workflows: {daily: every 1 days}, overdue_after: soon}", "not an interval"),
    ("a.b: {description: x, workflows: {daily: every 1 days}, overdue_after: 1 days, schedule: x}", "unknown keys"),
    ("A.B: {description: x, workflows: {daily: every 1 days}, overdue_after: 1 days}", "lower case"),
])
def test_bad_config_is_refused(tmp_path, body, message):
    f = tmp_path / "tasks.yaml"
    f.write_text(body)
    with pytest.raises(config.ConfigError, match=message):
        config.load(f)


@pytest.mark.db
def test_sync_mirrors_the_config_into_ops_task(tmp_path):
    disabled = config.load(with_reactions_disabled(tmp_path))
    tasks = config.load()
    try:
        with psycopg.connect(JOB, autocommit=True) as c:
            config.sync(c, disabled)
        with psycopg.connect(ADMIN) as c:
            off = c.execute("select enabled from ops.task where key = 'policy.reactions'").fetchone()[0]
            off_health = c.execute("select is_overdue from ops.task_health where key = 'policy.reactions'").fetchone()[0]
    finally:
        with psycopg.connect(JOB, autocommit=True) as c:
            config.sync(c, tasks)
            config.sync(c, tasks)                 # idempotent
    assert off is False
    assert off_health is False                    # disabled: the health page doesn't flag it
    with psycopg.connect(ADMIN) as c:
        rows = {r[0]: r[1:] for r in c.execute(
            "select key, workflows, due_rules, run_after, min_interval, overdue_after, enabled from ops.task")}
    for key, t in tasks.items():
        assert rows[key] == (list(t.workflows), t.rules, list(t.run_after), t.min_interval, t.overdue_after, t.enabled)
    assert rows["policy.fed_documents"][1] == {"daily": "every 20 hours", "watch": "fomc_decision_day"}
    assert rows["policy.reactions"][-1] is True


def test_build_tasks_configures_registered_classes_and_names_the_rest(tmp_path):
    class Fed(Task):
        key = "policy.fed_documents"
    tasks, missing = build_tasks(config.load(with_reactions_disabled(tmp_path)),
                                 {"policy.fed_documents": Fed, "policy.reactions": Fed})
    assert [t.key for t in tasks] == ["policy.fed_documents"]     # reactions is disabled, so it isn't built
    fed = tasks[0]
    assert fed.workflows == ("daily", "watch") and fed.rules["watch"] == "fomc_decision_day"
    assert fed.run_after == ("policy.calendar",) and fed.min_interval == timedelta(hours=20)
    assert sorted(missing) == ["crypto.coingecko_markets", "equity.prices", "equity.universe", "equity.valuation_inputs",
                               "policy.ai_batch", "policy.calendar", "policy.market_series",
                               "policy.rbi_documents"]


def test_trigger_and_project_ref():
    assert resolve_trigger(None, {"GITHUB_EVENT_NAME": "schedule"}) == "schedule"
    assert resolve_trigger("cli", {"GITHUB_EVENT_NAME": "schedule"}) == "schedule"
    assert resolve_trigger(None, {"GITHUB_EVENT_NAME": "workflow_dispatch"}) == "cli"   # a manual dispatch
    assert resolve_trigger("admin", {"GITHUB_EVENT_NAME": "workflow_dispatch"}) == "admin"
    assert resolve_trigger(None, {}) == "cli"
    pooler = "postgresql://job_writer.abcdefghijklmnopqrst@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    assert project_ref(pooler) == "abcdefghijklmnopqrst"
    assert project_ref("host=localhost dbname=ft user=job_writer") is None


def test_refresh_skips_the_due_check_except_an_nse_tasks_minimum_interval():
    t = Task(); t.key, t.rules, t.min_interval = "x.y", {"weekly": "every 7 days"}, timedelta(days=7)
    assert refresh_force(t, False) is True and "refresh" not in t.rules
    t.uses_nse = True
    assert refresh_force(t, True) is True and "refresh" not in t.rules       # --force from the CLI
    assert refresh_force(t, False) is False and t.rules["refresh"] == "every 10080 minutes"


@pytest.mark.parametrize("argv, env, message", [
    (["--workflow", "refresh"], {}, "needs --task"),
    (["--workflow", "backfill", "--since", "2019-01-01"], {}, "needs --task"),
    (["--workflow", "daily", "--request-id", "4"], {}, "for refresh runs"),
    (["--workflow", "daily", "--since", "2019-01-01"], {}, "for backfill runs"),
    (["--workflow", "backfill", "--task", "policy.calendar", "--since", "2020-01-02", "--until", "2020-01-01"], {},
     "after --until"),
    (["--workflow", "watch", "--as-of", "2026-09-17", "--target", "production"], {}, "dev project only"),
    (["--workflow", "daily", "--as-of", "2026-09-17"], {}, "dev project only"),
    (["--workflow", "daily"], {"GITHUB_EVENT_NAME": "schedule"}, "always targets production"),
    (["--workflow", "refresh", "--task", "policy.nope"], {}, "unknown task"),
    (["--workflow", "refresh", "--task", "policy.reactions", "--config", "DISABLED"], {}, "disabled"),
    (["--workflow", "refresh", "--task", "policy.calendar"], {}, "isn't built yet"),
    (["--workflow", "weekly", "--task", "policy.fed_documents"], {}, "doesn't run in the weekly"),
    (["--workflow", "daily"], {"JOB_DATABASE_URL": " "}, "JOB_DATABASE_URL is not set"),
    (["--workflow", "daily", "--as-of", "28-10-2026"], {}, "not a date"),
])
def test_cli_refuses_bad_arguments(capsys, tmp_path, argv, env, message):
    argv = [str(with_reactions_disabled(tmp_path)) if a == "DISABLED" else a for a in argv]

    class Fed(Task):
        key = "policy.fed_documents"
    with pytest.raises(SystemExit) as e:
        main(argv, env=env, registry={"policy.fed_documents": Fed})
    assert e.value.code == 2 and message in capsys.readouterr().err


def test_blank_workflow_inputs_mean_not_given(capsys):
    # The workflows pass blank inputs as "" (e.g. --as-of "" on a scheduled watch); blank means not given.
    with pytest.raises(SystemExit):
        main(["--workflow", "watch", "--as-of", "", "--request-id", "", "--git-sha", "", "--github-run-id", ""],
             env={}, registry={})
    assert "JOB_DATABASE_URL is not set" in capsys.readouterr().err
