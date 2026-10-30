"""The freshness drill (Prompt 01 D4; Review 01 F17): as `job_writer`, it moves one task's success time past its
`stale_after` with no run, so the public view reads 'stale'; restore puts the times back without undoing a real run
since; it refuses what would prove nothing."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import dsn

from ft.jobs import drill_freshness as drill

ADMIN, JOBS = dsn("postgres"), dsn("job_writer")
TASK = "test.d4_drill"


def test_arguments():
    with pytest.raises(SystemExit):
        drill.main(["--task", TASK, "--action", "restore"], env={"JOB_DATABASE_URL": "x"})
    with pytest.raises(SystemExit):
        drill.main(["--task", TASK, "--action", "push"], env={})
    with pytest.raises(SystemExit):
        drill.main(["--task", TASK, "--action", "restore", "--last-success-at", "2026-10-24T05:00:00"], env={"JOB_DATABASE_URL": "x"})


@pytest.fixture
def row():
    def seed(status="ok", success=timedelta(hours=1), stale_after="2 days"):
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after)
                         values (%s, 'test', 'D4 test task', '{daily}', '20 hours', '2 days') on conflict (key) do nothing""",
                      (TASK,))
            c.execute("delete from core.task_freshness where task_key = %s", (TASK,))
            c.execute("""insert into core.task_freshness (task_key, label, last_checked_at, last_success_at, status, stale_after)
                         values (%s, 'D4 test', now() - %s, now() - %s, %s, %s)""",
                      (TASK, success, success, status, stale_after))
    yield seed
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("delete from core.task_freshness where task_key = %s", (TASK,))
        c.execute("delete from ops.task where key = %s", (TASK,))


def _public_status():
    with psycopg.connect(ADMIN) as c:
        c.execute("set role anon")
        return c.execute("select status from api.task_freshness where task_key = %s", (TASK,)).fetchone()[0]


@pytest.mark.db
def test_push_makes_the_public_view_stale_with_no_run(row):
    row()
    assert _public_status() == "ok"
    with drill.connect(JOBS, autocommit=True, role="drill") as conn:
        before, after = drill.push(conn, TASK)
    assert before["reads_stale"] is False and after["reads_stale"] is True
    assert after["status"] == "ok"                                     # the stored status is untouched
    assert after["last_success_at"] < datetime.now(UTC) - timedelta(days=2)
    assert _public_status() == "stale"                                 # what the status bar reads


@pytest.mark.db
def test_restore_puts_the_times_back_and_keeps_a_newer_run(row):
    row()
    with drill.connect(JOBS, autocommit=True, role="drill") as conn:
        before, _ = drill.push(conn, TASK)
        back = drill.restore(conn, TASK, before["last_success_at"], before["last_checked_at"])
        assert back["last_success_at"] == before["last_success_at"] and back["reads_stale"] is False
        assert _public_status() == "ok"
        # a real run after the push: restore must not move its newer time back
        drill.push(conn, TASK)
        conn.execute("update core.task_freshness set last_success_at = now(), last_checked_at = now() where task_key = %s", (TASK,))
        newer = drill._row(conn, TASK)["last_success_at"]
        assert drill.restore(conn, TASK, before["last_success_at"], None)["last_success_at"] == newer


@pytest.mark.db
@pytest.mark.parametrize("status", ["failed", "stale"])
def test_push_refuses_a_task_that_is_not_ok(row, status):
    row(status=status)
    with drill.connect(JOBS, autocommit=True, role="drill") as conn, pytest.raises(drill.DrillError, match="prove nothing"):
        drill.push(conn, TASK)


@pytest.mark.db
def test_push_refuses_a_missing_row_and_changes_nothing(row):
    row()
    with drill.connect(JOBS, autocommit=True, role="drill") as conn:
        with pytest.raises(drill.DrillError, match="no core.task_freshness row"):
            drill.push(conn, "test.d4_missing")
        assert drill._row(conn, TASK)["reads_stale"] is False


@pytest.mark.db
def test_main_prints_the_restore_line(row, capsys):
    row()
    assert drill.main(["--task", TASK, "--action", "push"], env={"JOB_DATABASE_URL": JOBS}) == 0
    out = capsys.readouterr().out
    assert "restore with: --last-success-at " in out and '"reads_stale": true' in out
    assert drill.main(["--task", "test.d4_missing", "--action", "status"], env={"JOB_DATABASE_URL": JOBS}) == 2


def _records():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute("""select drill, target, finished_at is not null, passed, counts, github_run_id from ops.drill_run
                             where counts->>'task' = %s order by id""", (TASK,)).fetchall()


@pytest.fixture
def clean_records():
    def wipe():
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute("delete from ops.drill_run where counts->>'task' = %s", (TASK,))
    wipe()
    yield
    wipe()


@pytest.mark.db
def test_push_opens_a_drill_record_and_restore_closes_it_as_passed(row, clean_records):
    row()
    with drill.connect(JOBS, autocommit=True, role="drill") as conn:
        before, _ = drill.push(conn, TASK, target="dev", github_run_id=4242)
        (open_rec,) = _records()
        assert open_rec[:4] == ("d4_freshness", "dev", False, None) and open_rec[5] == 4242
        assert open_rec[4]["before"]["reads_stale"] is False and open_rec[4]["after"]["reads_stale"] is True
        drill.restore(conn, TASK, before["last_success_at"], before["last_checked_at"], target="dev")
    (rec,) = _records()                                                 # the same row, now closed
    assert rec[:4] == ("d4_freshness", "dev", True, True) and rec[5] == 4242
    assert rec[4]["before_restore"]["reads_stale"] is True and rec[4]["after_restore"]["reads_stale"] is False


@pytest.mark.db
def test_restore_after_a_real_run_already_fixed_it_records_not_passed(row, clean_records):
    row()
    with drill.connect(JOBS, autocommit=True, role="drill") as conn:
        before, _ = drill.push(conn, TASK)
        conn.execute("update core.task_freshness set last_success_at = now(), last_checked_at = now() where task_key = %s", (TASK,))
        drill.restore(conn, TASK, before["last_success_at"], None)      # it no longer read stale: nothing was proven
    (rec,) = _records()
    assert rec[2] is True and rec[3] is False and rec[4]["before_restore"]["reads_stale"] is False


@pytest.mark.db
def test_restore_without_a_recorded_push_still_records_the_run(row, clean_records):
    row()
    with drill.connect(JOBS, autocommit=True, role="drill") as conn:
        before, _ = drill.push(conn, TASK)
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("delete from ops.drill_run where counts->>'task' = %s", (TASK,))
    with drill.connect(JOBS, autocommit=True, role="drill") as conn:
        drill.restore(conn, TASK, before["last_success_at"], before["last_checked_at"], target="production")
    (rec,) = _records()
    assert rec[:4] == ("d4_freshness", "production", True, True) and rec[4]["push_not_recorded"] is True


@pytest.mark.db
def test_a_refused_push_writes_no_record(row, clean_records):
    row(status="failed")
    with drill.connect(JOBS, autocommit=True, role="drill") as conn, pytest.raises(drill.DrillError):
        drill.push(conn, TASK)
    assert _records() == []


def test_main_passes_the_github_run_id_and_target(monkeypatch):
    seen = {}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(drill, "connect", lambda *a, **k: Conn())
    row = {"last_success_at": datetime(2026, 10, 1, tzinfo=UTC), "last_checked_at": None}
    monkeypatch.setattr(drill, "push", lambda conn, task, **k: (seen.update(k), (row, row))[1])
    assert drill.main(["--task", TASK, "--action", "push", "--target", "production"],
                      env={"JOB_DATABASE_URL": "x", "GITHUB_RUN_ID": "991"}) == 0
    assert seen == {"target": "production", "github_run_id": 991}
