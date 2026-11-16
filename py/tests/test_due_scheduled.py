"""The skip trap (issue #386): an `every <interval>` rule in a scheduled workflow counts only the task's successes in
runs GitHub started on its schedule (ops.job_run.trigger = 'schedule'). A manual backfill, refresh, or a `daily` or
`weekly` started by hand no longer makes the next scheduled run skip. A refresh's NSE minimum interval (run.py's
`refresh_force`) still counts a success in any run, and the decision-day rules and their catch-ups are unchanged.

The rules run against a fake world first (no database, so they run anywhere), then against the test database, where
the query reads ops.job_run's trigger."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from conftest import dsn

from ft.jobs import config, due
from ft.jobs.run import refresh_force
from ft.jobs.runner import Task

NY, IST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")
FOMC = due.BANKS["fomc_decision_day"]
WEEKLY_TASK, FED, AI = "equity.calls_ai", "policy.fed_documents", "policy.ai_batch"
SUN = datetime(2026, 11, 15, 9, 23, tzinfo=IST)            # a Sunday `weekly` (cron 09:23 IST)


class World:
    """Successes as (task key, workflow, trigger, finished_at): the rows `_last_success` reads."""

    def __init__(self):
        self.meetings: dict[tuple[str, date], due.Meeting] = {}
        self.successes: list[tuple[str, str, str, datetime]] = []

    def succeeded(self, key: str, workflow: str, trigger: str, at: datetime) -> None:
        self.successes.append((key, workflow, trigger, at))

    def add(self, bank: due.Bank, day: date, stored: datetime | None = None) -> due.Meeting:
        release = datetime.combine(day, bank.release, tzinfo=bank.tz)
        m = due.Meeting(len(self.meetings) + 1, f"test-{bank.slug}-{day}", bank, day, release, stored)
        self.meetings[(bank.slug, day)] = m
        return m

    def decision_meeting(self, db, bank, day):
        return self.meetings.get((bank.slug, day))

    def last_success(self, db, key, workflow=None, *, scheduled=False):
        got = [at for k, w, t, at in self.successes
               if k == key and workflow in (None, w) and (not scheduled or t == "schedule")]
        return max(got, default=None)


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(due, "decision_meeting", w.decision_meeting)
    monkeypatch.setattr(due, "_last_success", w.last_success)
    return w


def weekly(now=SUN, key=WEEKLY_TASK):
    return due.evaluate(None, key, "every 6 days", now, "weekly")


# ---------------------------------------------------------------- manual runs don't stand in for the schedule

@pytest.mark.parametrize("workflow", ["backfill", "refresh"])
def test_a_manual_backfill_or_refresh_two_days_ago_leaves_the_weekly_due(world, workflow):
    world.succeeded(WEEKLY_TASK, "weekly", "schedule", SUN - timedelta(days=7))
    world.succeeded(WEEKLY_TASK, workflow, "cli", SUN - timedelta(days=2))
    d = weekly()
    assert d.due and d.reason == "last scheduled success 7 days, 0:00:00 ago (rule: every 6 days)"
    # the old rule, still what a caller naming no workflow gets: the manual run counts and the task skips
    old = due.evaluate(None, WEEKLY_TASK, "every 6 days", SUN)
    assert not old.due and old.reason == "last success 2 days, 0:00:00 ago (rule: every 6 days)"


def test_an_admin_or_visitor_refresh_doesnt_count_either(world):
    world.succeeded(WEEKLY_TASK, "weekly", "schedule", SUN - timedelta(days=7))
    world.succeeded(WEEKLY_TASK, "refresh", "admin", SUN - timedelta(days=1))
    world.succeeded(WEEKLY_TASK, "refresh", "visitor", SUN - timedelta(hours=1))
    assert weekly().due


def test_a_weekly_started_by_hand_doesnt_count_either(world):
    """Rule (a), decided on #386: a manual dispatch of `weekly` displaces nothing; the Sunday run still runs."""
    world.succeeded(WEEKLY_TASK, "weekly", "schedule", SUN - timedelta(days=7))
    world.succeeded(WEEKLY_TASK, "weekly", "cli", SUN - timedelta(days=3))
    assert weekly().due


def test_a_scheduled_success_two_days_ago_stops_it(world):
    world.succeeded(WEEKLY_TASK, "weekly", "schedule", SUN - timedelta(days=2))
    d = weekly()
    assert not d.due and d.reason == "last scheduled success 2 days, 0:00:00 ago (rule: every 6 days)"
    assert weekly(SUN + timedelta(days=4)).due                                  # six days on, due again


def test_no_scheduled_success_means_due_whatever_ran_by_hand(world):
    d = weekly()
    assert d.due and d.reason == "never succeeded in a scheduled run"
    world.succeeded(WEEKLY_TASK, "backfill", "cli", SUN - timedelta(hours=3))
    world.succeeded(WEEKLY_TASK, "weekly", "cli", SUN - timedelta(hours=2))
    d = weekly()
    assert d.due and d.reason == "never succeeded in a scheduled run"


def test_a_scheduled_success_in_another_scheduled_workflow_counts_as_before(world):
    """A decision-day `watch` started by its cron counts for the daily's interval, as any success did before."""
    now = datetime(2030, 10, 31, 6, 37, tzinfo=IST)
    world.succeeded(AI, "watch", "schedule", now - timedelta(hours=7))
    assert not due.evaluate(None, AI, "every 16 hours", now, "daily").due
    world.successes.clear()
    world.succeeded(AI, "watch", "cli", now - timedelta(hours=7))                # a watch started by hand doesn't
    assert due.evaluate(None, AI, "every 16 hours", now, "daily").due


# ---------------------------------------------------------------- the daily's two scheduled runs

def test_the_dailys_morning_and_evening_runs_keep_their_intervals(world):
    rules = config.load()
    reactions = rules["policy.reactions"].rules["daily"]
    series = rules["policy.market_series"].rules["daily"]
    morning = datetime(2026, 11, 11, 6, 37, tzinfo=IST)
    evening = morning.replace(hour=16)
    next_morning = morning + timedelta(days=1)
    for key in ("policy.reactions", "policy.market_series"):
        world.succeeded(key, "daily", "schedule", morning + timedelta(minutes=5))
    world.succeeded("policy.reactions", "refresh", "cli", morning + timedelta(hours=5))   # a refresh at noon
    # the evening run: 16-hour tasks wait, the 8-hour market series runs
    assert not due.evaluate(None, "policy.reactions", reactions, evening, "daily").due
    assert due.evaluate(None, "policy.market_series", series, evening, "daily").due
    world.succeeded("policy.market_series", "daily", "schedule", evening + timedelta(minutes=5))
    # the next morning: both are due again; the noon refresh changed nothing
    assert due.evaluate(None, "policy.reactions", reactions, next_morning, "daily").due
    assert due.evaluate(None, "policy.market_series", series, next_morning, "daily").due


def test_a_scheduled_failure_then_a_manual_success_leaves_the_task_due(world):
    """The scheduled run on the 8th failed (no success recorded); a manual refresh on the 10th succeeded."""
    world.succeeded(WEEKLY_TASK, "weekly", "schedule", SUN - timedelta(days=14))
    world.succeeded(WEEKLY_TASK, "refresh", "cli", SUN - timedelta(days=5))
    world.succeeded(WEEKLY_TASK, "weekly", "cli", SUN - timedelta(days=4))
    assert weekly().due


# ---------------------------------------------------------------- what stays as it was

def test_a_refreshs_nse_minimum_interval_still_counts_any_run(world):
    class NseTask(Task):
        key, uses_nse, min_interval = "equity.prices", True, timedelta(hours=20)
    t = NseTask()
    t.rules = {"weekly": "every 6 days"}
    assert refresh_force(t, False) is False and t.rules["refresh"] == "every 1200 minutes"
    now = SUN
    world.succeeded(t.key, "backfill", "cli", now - timedelta(hours=2))
    held = t.due(None, "refresh", now)
    assert not held.due and held.reason == "last success 2:00:00 ago (rule: every 1200 minutes)"
    assert t.due(None, "weekly", now).due                    # the same backfill doesn't hold the scheduled weekly
    world.succeeded(t.key, "weekly", "schedule", now - timedelta(hours=1))
    assert not t.due(None, "refresh", now).due               # nor does the interval forget a scheduled run


def test_after_decision_document_counts_watch_runs_in_the_window_and_any_run_in_its_catch_up(world):
    d = date(2030, 10, 30)
    stored = datetime(2030, 10, 30, 14, 3, tzinfo=NY)
    m = world.add(FOMC, d, stored=stored)
    inside = datetime(2030, 10, 30, 14, 20, tzinfo=NY)
    world.succeeded(AI, "daily", "schedule", stored + timedelta(minutes=5))      # a scheduled daily: not a watch run
    assert due.evaluate(None, AI, "after_decision_document", inside, "watch").meeting is m
    world.succeeded(AI, "watch", "cli", stored + timedelta(minutes=8))            # a watch started by hand counts
    assert not due.evaluate(None, AI, "after_decision_document", inside, "watch").due
    # the catch-up: any run since the document was stored, a backfill included
    world.successes.clear()
    later = datetime(2030, 10, 30, 17, 0, tzinfo=NY)
    assert due.evaluate(None, AI, "after_decision_document", later, "watch").catch_up
    world.succeeded(AI, "backfill", "cli", stored + timedelta(hours=1))
    assert not due.evaluate(None, AI, "after_decision_document", later, "watch").due


def test_the_daily_catch_up_still_makes_the_pass_due(world):
    d = date(2030, 10, 30)
    world.add(FOMC, d)                                                            # the statement isn't stored
    world.succeeded(FED, "daily", "schedule", datetime(2030, 10, 30, 10, 0, tzinfo=NY))
    now = datetime(2030, 10, 31, 6, 37, tzinfo=IST)                              # 11 hours 7 minutes later
    c = due.evaluate(None, FED, "every 16 hours or fomc_catch_up", now, "daily")
    assert c.due and c.catch_up and c.meeting is None
    assert "catch-up" in c.reason and "last scheduled success 11:07:00 ago" in c.reason
    assert not due.evaluate(None, FED, "every 16 hours", now, "daily").due        # the interval alone: not due


# ---------------------------------------------------------------- against the test database

ADMIN = dsn("postgres")
KEY = "test.due_scheduled"


@pytest.mark.db
def test_the_query_reads_the_trigger_of_each_tasks_job_run():
    now = datetime(2031, 3, 16, 3, 53, tzinfo=UTC)                     # a Sunday, 09:23 India time
    with psycopg.connect(ADMIN) as c:                                  # one transaction, rolled back at the end
        try:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', '#386 test task', '{weekly}', '6 days', '8 days', true)""", (KEY,))

            def ran(workflow, trigger, status, at):
                job = c.execute("insert into ops.job_run (workflow, trigger, runner) values (%s, %s, 'github') "
                                "returning id", (workflow, trigger)).fetchone()[0]
                c.execute("insert into ops.task_run (job_run_id, task_key, status, finished_at) values (%s, %s, %s, %s)",
                          (job, KEY, status, at))

            class T(Task):
                key = KEY
            t = T()
            t.rules = {"weekly": "every 6 days"}

            never = t.due(c, "weekly", now)
            assert never.due and never.reason == "never succeeded in a scheduled run"
            ran("weekly", "schedule", "success", now - timedelta(days=14))
            ran("weekly", "schedule", "failed", now - timedelta(days=7))           # last Sunday's run failed
            ran("refresh", "cli", "success", now - timedelta(days=5))              # then it was run by hand
            ran("backfill", "cli", "success", now - timedelta(days=2))
            ran("weekly", "cli", "success", now - timedelta(days=1))
            ran("refresh", "admin", "success", now - timedelta(hours=3))
            d = t.due(c, "weekly", now)
            assert d.due and d.reason == "last scheduled success 14 days, 0:00:00 ago (rule: every 6 days)"
            # the refresh's interval and a caller naming no workflow still count the last success of any run
            assert due.evaluate(c, KEY, "every 6 days", now, "refresh").reason.startswith("last success 3:00:00 ago")
            assert not due.evaluate(c, KEY, "every 6 days", now).due
            ran("weekly", "schedule", "success", now - timedelta(days=2))
            held = t.due(c, "weekly", now)
            assert not held.due and held.reason == "last scheduled success 2 days, 0:00:00 ago (rule: every 6 days)"
            assert due._last_success(c, KEY, "weekly", scheduled=True) == now - timedelta(days=2)
            assert due._last_success(c, KEY, "refresh", scheduled=True) is None
            assert due._last_success(c, KEY, "watch") is None                  # the watch case is as it was
        finally:
            c.rollback()
