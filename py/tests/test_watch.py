"""The watch loop and the run CLI against the test database, as job_writer, with a fake clock and fake fetches
(Prompt 01 A2; Architecture 02 §1.1, §1.7; Review 01, F3). Meetings here are synthetic (2030, slugs `test-…`)."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from conftest import dsn

from ft.jobs.run import main
from ft.jobs.runner import JobRun, Task

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NY, INDIA = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")
FED, RBI, AI = "policy.fed_documents", "policy.rbi_documents", "policy.ai_batch"


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def meeting(bank, slug, starts_on, ends_on, occurs_at):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at, status)
                     select 'policy_meeting', id, %s, %s, %s, %s, %s, 'scheduled'
                     from core.entity where kind = 'central_bank' and slug = %s
                     on conflict (event_type, slug) do nothing""", (slug, slug, starts_on, ends_on, occurs_at, bank))
        eid = c.execute("select id from core.event where event_type = 'policy_meeting' and slug = %s", (slug,)).fetchone()[0]
        c.execute("""insert into policy.meeting (event_id, kind, is_rate_decision) values (%s, 'scheduled', true)
                     on conflict do nothing""", (eid,))
    return eid


class FakeClock:
    def __init__(self, start):
        self.t, self.sleeps, self.on_sleep = start, [], None

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)
        if self.on_sleep:
            self.on_sleep()

    def monotonic(self):
        return self.t.timestamp()


def fakes(find_on=None, fail_on=()):
    """Fake task classes for the watch tasks. A document task's fetch is one look: it 'finds' the decision document
    from look number `find_on` (never if None) and raises on the looks in `fail_on`."""
    seen = {FED: [], RBI: [], AI: []}

    class Docs(Task):
        bank, doc_type = "fed", "fomc_statement"
        def fetch(self, ctx):
            seen[self.key].append(ctx)
            n = len(seen[self.key])
            if n in fail_on:
                raise ConnectionError("simulated network failure")
            return ctx.meeting if find_on is not None and n >= find_on else None
        def load(self, tx, m, tr):
            if m is None:
                return 0
            tx.execute("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url)
                          select %s, e.entity_id, e.id, 'Test decision document', s.id, %s, 'https://example.org/test'
                          from core.event e, core.source s where e.id = %s and s.key = %s""",
                       (self.doc_type, f"test/{m.slug}", m.event_id, self.bank))
            return 1

    class Fed(Docs):
        key = FED

    class Rbi(Docs):
        key, bank, doc_type = RBI, "rbi", "mpc_resolution"

    class Ai(Task):
        key = AI
        def fetch(self, ctx):
            seen[AI].append(ctx)
            return ctx.meeting
        def load(self, tx, m, tr):
            return 1

    return {FED: Fed, RBI: Rbi, AI: Ai}, seen


def run_watch(extra, clock, registry):
    env = {"JOB_DATABASE_URL": JOB, "GITHUB_EVENT_NAME": "workflow_dispatch"}
    return main(["--workflow", "watch", "--runner", "github", "--github-run-id", "1234", "--git-sha", "abc123",
                 "--target", "dev", *extra], env=env, clock=clock, sleep=clock.sleep, monotonic=clock.monotonic,
                registry=registry)


def last_job(workflow="watch"):
    return q("""select id, workflow, trigger, runner, github_run_id, git_sha, status from ops.job_run
                where workflow = %s order by id desc limit 1""", workflow)[0]


def task_runs(job_id, key=None):
    rows = q("select task_key, status, rows_written, coalesce(error, '') from ops.task_run where job_run_id = %s order by id",
             job_id)
    return [r[1:] for r in rows if r[0] == key] if key else rows


@pytest.mark.db
def test_watch_finds_the_statement_on_the_fifth_poll():
    eid = meeting("fed", "test-fomc-2030-10-30", "2030-10-29", "2030-10-30", datetime(2030, 10, 30, 18, 0, tzinfo=UTC))
    clock = FakeClock(datetime(2030, 10, 30, 14, 7, tzinfo=NY))         # the scheduled start, 14:07 New York
    reg, seen = fakes(find_on=5)
    assert run_watch([], clock, reg) == 0

    job = last_job()
    assert job[1:] == ("watch", "cli", "github", 1234, "abc123", "success")   # a manual dispatch is trigger 'cli'
    # one short task run per poll, every 2 minutes; the polls that find nothing record success with 0 rows
    assert [(s, n) for s, n, _ in task_runs(job[0], FED)] == [("success", 0)] * 4 + [("success", 1)]
    assert [c.now.astimezone(NY).strftime("%H:%M") for c in seen[FED]] == ["14:07", "14:09", "14:11", "14:13", "14:15"]
    assert clock.sleeps == [120.0] * 4
    assert all(c.meeting.event_id == eid and c.target == "dev" and c.workflow == "watch" for c in seen[FED])
    # the other bank's task is recorded once, on the first poll, and never polled
    assert [(s, e) for s, _, e in task_runs(job[0], RBI)] == [("skipped_not_due", "not an MPC decision day")]
    # the AI step: not due until the statement is stored, then in the same poll (it runs after the documents)
    assert [s for s, _, _ in task_runs(job[0], AI)] == ["skipped_not_due", "success"]
    assert len(seen[AI]) == 1 and seen[AI][0].meeting.event_id == eid
    assert q("select count(*) from core.document where event_id = %s and doc_type = 'fomc_statement'", eid)[0][0] == 1
    # done-when (F3): the replayed or live decision day shows the document within 30 minutes of the release
    assert seen[FED][-1].now - datetime(2030, 10, 30, 14, 0, tzinfo=NY) <= timedelta(minutes=30)
    assert q("select status from core.task_freshness where task_key = %s", FED)[0][0] == "ok"
    assert q("select count(*) from pg_locks where locktype = 'advisory'")[0][0] == 0

    # a second watch the same day finds nothing left to do and exits at once
    clock = FakeClock(datetime(2030, 10, 30, 14, 20, tzinfo=NY))
    assert run_watch([], clock, fakes(find_on=1)[0]) == 0 and clock.sleeps == []
    reasons = {k: e for k, _, _, e in task_runs(last_job()[0])}
    assert reasons[FED] == "test-fomc-2030-10-30: the fomc_statement is stored"
    assert reasons[AI].startswith("test-fomc-2030-10-30: already ran after the fomc_statement was stored")


@pytest.mark.db
def test_watch_window_is_anchored_to_the_release_and_survives_a_failed_poll():
    eid = meeting("rbi", "test-mpc-2030-12-04", "2030-12-02", "2030-12-04", datetime(2030, 12, 4, 4, 30, tzinfo=UTC))
    clock = FakeClock(datetime(2030, 12, 4, 9, 52, tzinfo=INDIA))       # a manual start 8 minutes before 10:00
    reg, seen = fakes(find_on=None, fail_on={3})
    assert run_watch([], clock, reg) == 0                                # the failed third poll was followed by successes

    polled = [c.now.astimezone(INDIA).strftime("%H:%M") for c in seen[RBI]]
    # every 2 minutes from the start until the window closes at 11:00, 60 minutes after the release (not 60 minutes
    # after the start): the next poll would be at 11:00, so the loop ends after the 10:58 poll
    assert polled[0] == "09:52" and polled[-1] == "10:58" and len(polled) == 34
    job = last_job()
    runs = task_runs(job[0], RBI)
    assert [s for s, _, _ in runs] == ["success", "success", "failed"] + ["success"] * 31
    assert all(n == 0 for _, n, _ in runs) and job[-1] == "success"
    assert q("select count(*) from core.document where event_id = %s", eid)[0][0] == 0
    # the failed poll's alert opened and the next poll resolved it
    assert q("""select count(*), count(resolved_at) from ops.alert
                where dedupe_key = 'task_failed:policy.rbi_documents'""")[0] == (1, 1)
    assert [s for s, _, _ in task_runs(job[0], AI)] == ["skipped_not_due"]


@pytest.mark.db
def test_watch_exits_at_once_on_a_non_decision_day_and_hours_before_a_release():
    meeting("rbi", "test-mpc-2030-12-04", "2030-12-02", "2030-12-04", datetime(2030, 12, 4, 4, 30, tzinfo=UTC))
    reg, seen = fakes(find_on=1)
    clock = FakeClock(datetime(2030, 11, 6, 14, 7, tzinfo=NY))          # no meeting that day
    assert run_watch([], clock, reg) == 0
    assert clock.sleeps == [] and not seen[FED] and not seen[RBI]
    runs = {k: (s, e) for k, s, _, e in task_runs(last_job()[0])}
    assert runs[FED] == ("skipped_not_due", "not an FOMC decision day")
    assert runs[RBI] == ("skipped_not_due", "not an MPC decision day")
    assert runs[AI][0] == "skipped_not_due" and last_job()[-1] == "success"

    # The Fed's scheduled watch, 14:07 New York on 3 Dec, is 00:37 India on 4 Dec: the MPC's decision day in
    # India, but nine hours before its release, so nothing is polled.
    clock = FakeClock(datetime(2030, 12, 3, 14, 7, tzinfo=NY))
    assert run_watch([], clock, reg) == 0
    assert clock.sleeps == [] and not seen[RBI]
    runs = {k: (s, e) for k, s, _, e in task_runs(last_job()[0])}
    assert runs[RBI] == ("skipped_not_due", "test-mpc-2030-12-04: too early, the watch window opens at 2030-12-04 09:50 IST")


@pytest.mark.db
def test_replay_with_as_of_runs_a_past_decision_day_on_its_own_clock(capsys):
    eid = meeting("fed", "test-fomc-2030-09-18", "2030-09-17", "2030-09-18", datetime(2030, 9, 18, 18, 0, tzinfo=UTC))
    clock = FakeClock(datetime(2030, 10, 1, 9, 0, tzinfo=UTC))          # weeks later; the replay's clock is its own
    reg, seen = fakes(find_on=2)
    assert run_watch(["--as-of", "2030-09-18"], clock, reg) == 0
    assert "replaying test-fomc-2030-09-18: the clock starts at its release, 2030-09-18 14:00 EDT" in capsys.readouterr().out
    assert [c.now.astimezone(NY).strftime("%Y-%m-%d %H:%M") for c in seen[FED]] == ["2030-09-18 14:00", "2030-09-18 14:02"]
    assert all(c.as_of == date(2030, 9, 18) for c in seen[FED]) and len(seen[AI]) == 1
    assert q("select count(*) from core.document where event_id = %s", eid)[0][0] == 1

    # a date with no rate decision replays nothing
    clock = FakeClock(datetime(2030, 10, 1, 9, 0, tzinfo=UTC))
    assert run_watch(["--as-of", "2030-09-19"], clock, reg) == 0
    assert "no rate decision on 2030-09-19" in capsys.readouterr().out and clock.sleeps == []


@pytest.mark.db
def test_watch_reconnects_when_the_pooler_drops_the_idle_control_connection(capsys):
    meeting("fed", "test-fomc-2030-07-31", "2030-07-30", "2030-07-31", datetime(2030, 7, 31, 18, 0, tzinfo=UTC))
    clock = FakeClock(datetime(2030, 7, 31, 14, 7, tzinfo=NY))
    drops = []
    def drop():                                    # the network path drops the control connection while it idles
        if not drops:
            drops.append(q("select count(pg_terminate_backend(pid)) from pg_stat_activity "
                           "where application_name = 'ft-runner-control'")[0][0])
    clock.on_sleep = drop
    reg, seen = fakes(find_on=3)
    assert run_watch([], clock, reg) == 0
    assert drops == [1] and len(seen[FED]) == 3 and last_job()[-1] == "success"
    assert "control connection had dropped after" in capsys.readouterr().out


@pytest.mark.db
def test_cli_records_the_github_run_and_links_a_refresh_request():
    seen = []

    class Series(Task):
        key = "policy.market_series"
        def fetch(self, ctx):
            seen.append(ctx)

    reg = {"policy.market_series": Series}
    # a cron start: trigger 'schedule' from GITHUB_EVENT_NAME, with the run id and sha recorded
    env = {"JOB_DATABASE_URL": JOB, "GITHUB_EVENT_NAME": "schedule"}
    assert main(["--workflow", "daily", "--target", "production", "--runner", "github", "--github-run-id", "987",
                 "--git-sha", "f00d"], env=env, registry=reg) == 0
    assert last_job("daily")[1:] == ("daily", "schedule", "github", 987, "f00d", "success")

    # an admin refresh skips the due check and links its job run to the request
    rid = q("""insert into ops.refresh_request (task_key, requested_by, status)
               values ('policy.market_series', 'admin', 'dispatched') returning id""")[0][0]
    env = {"JOB_DATABASE_URL": JOB, "GITHUB_EVENT_NAME": "workflow_dispatch"}
    assert main(["--workflow", "refresh", "--task", "policy.market_series", "--trigger", "admin",
                 "--request-id", str(rid), "--runner", "github"], env=env, registry=reg) == 0
    job = last_job("refresh")
    assert job[1:4] == ("refresh", "admin", "github") and seen[-1].due.reason == "forced"
    assert q("select job_run_id from ops.refresh_request where id = %s", rid)[0][0] == job[0]

    # a backfill passes its date range to the task
    assert main(["--workflow", "backfill", "--task", "policy.market_series", "--since", "2019-01-01",
                 "--until", ""], env=env, registry=reg) == 0
    assert (seen[-1].since, seen[-1].until, seen[-1].workflow) == (date(2019, 1, 1), None, "backfill")


@pytest.mark.db
def test_an_unexpected_error_marks_the_job_run_failed():
    with pytest.raises(RuntimeError), JobRun(JOB, "daily") as j:
        raise RuntimeError("boom")
    assert q("select status, finished_at is not null from ops.job_run where id = %s", j.id)[0] == ("failed", True)
