"""The decision-day catch-up against the test database (issue #158): the watch loop's one pass, the later polls that
leave a catch-up alone, and the daily rule's reading of yesterday's meeting and of ops.task_run. The rules themselves are
tested without a database in test_catch_up_rules.py. Meetings here are synthetic (2031, slugs `test-…`)."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from test_watch import ADMIN, AI, FED, INDIA, NY, RBI, FakeClock, fakes, last_job, meeting, q, run_watch, task_runs

from ft.jobs import due


def reasons(job_id):
    return {k: e for k, _, _, e in task_runs(job_id)}


@pytest.mark.db
def test_a_watch_started_after_the_window_does_one_pass_loads_the_statement_and_runs_the_ai_step(capsys):
    eid = meeting("fed", "test-fomc-2031-06-11", "2031-06-10", "2031-06-11", datetime(2031, 6, 11, 18, 0, tzinfo=UTC))
    clock = FakeClock(datetime(2031, 6, 11, 14, 50, tzinfo=NY))       # 50 minutes after the release: the window closed 14:40
    reg, seen = fakes(find_on=1)
    assert run_watch([], clock, reg) == 0

    out = capsys.readouterr().out
    assert clock.sleeps == [] and "watch: nothing left to do after 1 poll(s)" in out        # one pass, no polling
    assert len(seen[FED]) == 1 and len(seen[AI]) == 1
    fed, ai = seen[FED][0], seen[AI][0]
    assert fed.due.catch_up and fed.due.until is None and fed.meeting.event_id == eid and fed.workflow == "watch"
    assert f"  {FED}: test-fomc-2031-06-11: catch-up" in out                                # the run log says why
    assert ai.due.catch_up and ai.meeting.event_id == eid          # the AI step follows in the same pass, in real time
    job = last_job()
    assert job[-1] == "success"
    assert [(s, n) for s, n, _ in task_runs(job[0], FED)] == [("success", 1)]
    assert [(s, n) for s, n, _ in task_runs(job[0], AI)] == [("success", 1)]
    assert reasons(job[0])[RBI] == "not an MPC decision day"
    assert q("select count(*) from core.document where event_id = %s and doc_type = 'fomc_statement'", eid)[0][0] == 1

    # a later run finds the statement stored and the AI step done: nothing is due, so nothing is fetched or sent twice
    clock = FakeClock(datetime(2031, 6, 11, 15, 12, tzinfo=NY))
    reg, seen = fakes(find_on=1)
    assert run_watch([], clock, reg) == 0 and clock.sleeps == [] and not seen[FED] and not seen[AI]
    r = reasons(last_job()[0])
    assert r[FED] == "test-fomc-2031-06-11: the fomc_statement is stored"
    assert "already ran after the fomc_statement was stored" in r[AI]
    assert q("select count(*) from core.document where event_id = %s", eid)[0][0] == 1

    # the next morning (India) the same; two days later the decision day is over
    clock = FakeClock(datetime(2031, 6, 12, 6, 37, tzinfo=INDIA))
    assert run_watch([], clock, fakes(find_on=1)[0]) == 0
    assert reasons(last_job()[0])[FED] == "test-fomc-2031-06-11: the fomc_statement is stored"
    clock = FakeClock(datetime(2031, 6, 13, 14, 52, tzinfo=NY))
    assert run_watch([], clock, fakes(find_on=1)[0]) == 0
    assert reasons(last_job()[0])[FED] == "not an FOMC decision day"


@pytest.mark.db
def test_the_mpc_is_caught_up_by_a_watch_started_after_its_window(capsys):
    eid = meeting("rbi", "test-mpc-2031-08-06", "2031-08-04", "2031-08-06", datetime(2031, 8, 6, 4, 30, tzinfo=UTC))
    clock = FakeClock(datetime(2031, 8, 6, 11, 12, tzinfo=INDIA))     # the window closed at 11:00
    reg, seen = fakes(find_on=1)
    assert run_watch([], clock, reg) == 0
    assert clock.sleeps == [] and "after 1 poll(s)" in capsys.readouterr().out
    assert len(seen[RBI]) == 1 and seen[RBI][0].due.catch_up and seen[RBI][0].meeting.event_id == eid
    assert len(seen[AI]) == 1 and seen[AI][0].due.catch_up
    job = last_job()
    assert [(s, n) for s, n, _ in task_runs(job[0], RBI)] == [("success", 1)] and job[-1] == "success"
    assert q("select count(*) from core.document where event_id = %s and doc_type = 'mpc_resolution'", eid)[0][0] == 1
    assert reasons(job[0])[FED] == "not an FOMC decision day"

    # the card's second catch-up run
    clock = FakeClock(datetime(2031, 8, 6, 13, 42, tzinfo=INDIA))
    reg, seen = fakes(find_on=1)
    assert run_watch([], clock, reg) == 0 and not seen[RBI] and not seen[AI]


@pytest.mark.db
def test_a_catch_up_gets_the_first_pass_only_while_another_banks_window_polls():
    # The MPC decided on the 12th (India). At 13:52 on the 12th in New York it is 00:22 on the 13th in India: the MPC's
    # resolution is still missing (a catch-up), and the Fed's window is open (13:50 to 14:40). The Fed is polled all
    # window long; the MPC's catch-up runs once, in the first pass, and not again every 2 minutes.
    meeting("rbi", "test-mpc-2031-11-12", "2031-11-10", "2031-11-12", datetime(2031, 11, 12, 4, 30, tzinfo=UTC))
    meeting("fed", "test-fomc-2031-11-12", "2031-11-11", "2031-11-12", datetime(2031, 11, 12, 19, 0, tzinfo=UTC))
    clock = FakeClock(datetime(2031, 11, 12, 13, 52, tzinfo=NY))
    reg, seen = fakes(find_on=None)
    assert run_watch([], clock, reg) == 0
    assert len(seen[FED]) == 24 and not any(c.due.catch_up for c in seen[FED])          # 13:52 to 14:38, every 2 minutes
    assert seen[FED][-1].now.astimezone(NY).strftime("%H:%M") == "14:38"
    assert len(seen[RBI]) == 1 and seen[RBI][0].due.catch_up and seen[RBI][0].now == seen[FED][0].now
    job = last_job()
    assert [s for s, _, _ in task_runs(job[0], RBI)] == ["success"] and job[-1] == "success"


@pytest.mark.db
def test_the_daily_rules_read_yesterdays_meeting_and_the_task_runs_from_the_database():
    eid = meeting("fed", "test-fomc-2031-09-17", "2031-09-16", "2031-09-17", datetime(2031, 9, 17, 18, 0, tzinfo=UTC))
    now = datetime(2031, 9, 18, 6, 37, tzinfo=INDIA)                   # the daily: 21:07 on the 17th in New York
    daily_fed, daily_ai = "every 20 hours or fomc_catch_up", "every 20 hours or ai_catch_up"
    with psycopg.connect(ADMIN) as c:                                  # one transaction, rolled back at the end
        try:
            job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('daily', 'cli', 'local') "
                            "returning id").fetchone()[0]

            def succeeded(key, at):
                c.execute("insert into ops.task_run (job_run_id, task_key, status, finished_at) "
                          "values (%s, %s, 'success', %s)", (job, key, at))

            succeeded(FED, now - timedelta(hours=5))
            succeeded(AI, now - timedelta(hours=5))
            # the statement is missing: the document task is due in the daily whatever its 20 hours, the AI step isn't
            d = due.evaluate(c, FED, daily_fed, now)
            assert d.due and d.catch_up and d.meeting is None and "catch-up" in d.reason
            missing = due.evaluate(c, AI, daily_ai, now)
            assert not missing.due and "the fomc_statement isn't stored yet" in missing.reason
            # the next day in New York it is yesterday's meeting; two days later nothing
            assert due.evaluate(c, FED, "fomc_decision_day", now + timedelta(hours=12)).catch_up
            gone = due.evaluate(c, FED, "fomc_decision_day", now + timedelta(days=2))
            assert not gone.due and gone.reason == "not an FOMC decision day"

            # stored two hours ago, after the AI step's last success: the AI step follows, the document task is done
            c.execute("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key,
                                                    source_url, created_at)
                         select 'fomc_statement', e.entity_id, e.id, 'Test decision document', s.id, %s,
                                'https://example.org/test', %s
                         from core.event e, core.source s where e.id = %s and s.key = 'fed'""",
                      ("test/test-fomc-2031-09-17", now - timedelta(hours=2), eid))
            ai = due.evaluate(c, AI, daily_ai, now)
            assert ai.due and ai.catch_up and ai.meeting is None and "the AI step hasn't run since" in ai.reason
            stored = due.evaluate(c, FED, daily_fed, now)
            assert not stored.due and "the fomc_statement is stored" in stored.reason
            # once the AI step has succeeded since, it isn't due again: it runs once per stored document
            succeeded(AI, now - timedelta(hours=1))
            done = due.evaluate(c, AI, daily_ai, now)
            assert not done.due and "already ran after the fomc_statement was stored" in done.reason
        finally:
            c.rollback()
