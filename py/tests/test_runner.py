"""Job runner scenario against the test database, as job_writer (Architecture 02 §5)."""
import psycopg
import pytest
from conftest import dsn

from ft.jobs.runner import Alert, Check, Task, run

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


class MarketSeries(Task):
    key = "policy.market_series"; workflows = ("daily",)
    def load(self, tx, fetched, tr):
        sid = tx.execute("select id from core.series where key='fred.DGS10'").fetchone()[0]
        for d, v in [("2026-10-14", 4.11), ("2026-10-15", 4.08), ("2026-10-16", 4.02)]:
            tx.execute("""insert into core.observation values (%s,%s,%s,%s)
                          on conflict (series_id, obs_date) do update set value=excluded.value, task_run_id=excluded.task_run_id""",
                       (sid, d, v, tr))
        return 3
    def checks(self, tx, fetched, tr):
        n = tx.execute("select count(*) from core.observation o join core.series s on s.id=o.series_id where s.key='fred.DGS10'").fetchone()[0]
        return [Check("rows_present", n >= 3, f"{n} rows"),
                Check("latest_within_5_days", True, "", blocking=False)]

class Reactions(Task):
    key = "policy.reactions"; workflows = ("daily",); run_after = ("policy.market_series",)
    fail = True
    def load(self, tx, fetched, tr):
        sid = tx.execute("select id from core.series where key='fred.DGS10'").fetchone()[0]
        tx.execute("insert into core.observation values (%s,'2026-10-17',99.9,%s) on conflict do nothing", (sid, tr))
        return 1
    def checks(self, tx, fetched, tr):
        return [Check("yield_in_bounds", not self.fail, "99.9% is not a plausible 10-year yield"),
                Check("lag_ok", not self.fail, "warn-only check", blocking=False)]

class Calendar(Task):
    key = "policy.calendar"; workflows = ("weekly",)



@pytest.mark.db
def test_runner_scenario():
    print("Run 1: daily, reactions has a failing blocking check")
    code = run(JOB, "daily", [Reactions(), MarketSeries(), Calendar()])
    check("R1 exit code is non-zero (GitHub would email the failure)", code == 1, code)
    check("R2 order follows run_after (market_series ran first)",
          [r[0] for r in q("select task_key from ops.task_run where job_run_id=(select max(id) from ops.job_run) order by id")] == ["policy.market_series", "policy.reactions"])
    check("R3 failed check rolled back the whole load (no 99.9 row)",
          q("select count(*) from core.observation where value=99.9")[0][0] == 0)
    check("R4 good task committed alongside the failed one",
          q("select count(*) from core.observation o join core.series s on s.id=o.series_id where s.key='fred.DGS10'")[0][0] == 3)
    check("R5 statuses recorded", dict(q("select task_key, status from ops.task_run where job_run_id=(select max(id) from ops.job_run)")) ==
          {"policy.market_series": "success", "policy.reactions": "checks_failed"})
    check("R6 job run marked partial", q("select status from ops.job_run order by id desc limit 1")[0][0] == "partial")
    check("R7 one error alert and one warn alert open for reactions",
          sorted(r[0] for r in q("select severity from ops.alert where task_key='policy.reactions' and resolved_at is null")) == ["error", "warn"])
    check("R8 weekly-only task not run in the daily workflow",
          q("select count(*) from ops.task_run where task_key='policy.calendar'")[0][0] == 0)
    fr = dict(q("select task_key, status from core.task_freshness where task_key in ('policy.market_series','policy.reactions')"))
    check("R9 public freshness: ok for market series, failed for reactions", fr == {"policy.market_series": "ok", "policy.reactions": "failed"}, fr)

    print("Run 2: daily again immediately (reactions still failing)")
    code = run(JOB, "daily", [Reactions(), MarketSeries()])
    check("R10 market_series skipped as not due (20-hour rule)",
          q("select status from ops.task_run where task_key='policy.market_series' order by id desc limit 1")[0][0] == "skipped_not_due")
    check("R11 a repeat failure doesn't open a duplicate alert",
          q("select count(*) from ops.alert where task_key='policy.reactions' and resolved_at is null and severity='error'")[0][0] == 1)

    print("Run 3: reactions fixed")
    r = Reactions(); r.fail = False
    code = run(JOB, "daily", [r, MarketSeries()])
    check("R12 exit code zero once fixed", code == 0, code)
    check("R13 both alerts auto-resolved", q("select count(*) from ops.alert where task_key='policy.reactions' and resolved_at is null")[0][0] == 0)
    check("R14 freshness back to ok", q("select status from core.task_freshness where task_key='policy.reactions'")[0][0] == "ok")

    print("Run 4: another run holds the task's lock")
    holder = psycopg.connect(JOB, autocommit=True)
    holder.execute("select pg_advisory_lock(hashtext('task:policy.market_series'))")
    code = run(JOB, "daily", [MarketSeries()], force=True)
    holder.close()
    check("R15 concurrent run skips the locked task instead of double-loading",
          q("select status from ops.task_run where task_key='policy.market_series' order by id desc limit 1")[0][0] == "skipped_locked")

    h = {r[0]: (r[1], r[2]) for r in q("select key, is_overdue, last_status from ops.task_health")}
    check("R16 health page: never-run calendar is overdue, market series is not",
          h["policy.calendar"][0] is True and h["policy.market_series"][0] is False, h)
    check("R17 job_writer can use the HTTP cache table",
          psycopg.connect(JOB, autocommit=True).execute(
              "insert into ops.http_cache (url, etag, fetched_at) values ('https://example.org/x','\"abc\"',now()) returning url").fetchone()[0] == "https://example.org/x")

    failed = [(n, d) for n, ok, d in results if not ok]
    assert not failed, failed
    assert len(results) == 17


class LongFetch(Task):
    """A fetch that outlives the control connection (Review 01, F3): the network path drops it, simulated by
    terminating its backend. With `steal`, another run takes the task lock while this one is disconnected."""
    key = "policy.calendar"; workflows = ("weekly",)
    steal = False
    loaded = False
    def fetch(self, ctx):
        with psycopg.connect(ADMIN, autocommit=True) as a:
            a.execute("select pg_terminate_backend(pid) from pg_stat_activity where application_name = 'ft-runner-control'")
        if self.steal:
            self.holder = psycopg.connect(JOB, autocommit=True)
            self.holder.execute("select pg_advisory_lock(hashtext('task:policy.calendar'))")  # waits for the old session to end
        return "calendar"
    def load(self, tx, fetched, tr):
        self.loaded = True
        return 1


def last_run(key):
    return q("""select r.status, r.rows_written, coalesce(r.error, ''), j.status from ops.task_run r
                join ops.job_run j on j.id = r.job_run_id where r.task_key = %s order by r.id desc limit 1""", key)[0]


@pytest.mark.db
def test_control_connection_dropped_during_fetch():
    from ft.jobs.runner import connect
    with connect(JOB) as c:
        p = c.info.get_parameters()
    assert (p.get("keepalives_idle"), p.get("keepalives_interval"), p.get("keepalives_count")) == ("60", "15", "4")

    # Reconnects, takes the lock again, loads, and records the run normally.
    t = LongFetch()
    assert run(JOB, "weekly", [t], force=True) == 0
    assert t.loaded and last_run("policy.calendar") == ("success", 1, "", "success")
    assert q("select stale_after::text from core.task_freshness where task_key = 'policy.calendar'")[0][0] == "40 days"
    assert q("select count(*) from pg_locks where locktype = 'advisory'")[0][0] == 0      # lock released

    # Another run took the lock meanwhile: nothing is loaded and the task is recorded as failed.
    t = LongFetch(); t.steal = True
    assert run(JOB, "weekly", [t], force=True) == 1
    t.holder.close()
    status, rows, err, job = last_run("policy.calendar")
    assert not t.loaded and (status, rows, job) == ("failed", 0, "failed") and "another run now holds the task lock" in err
    assert q("select count(*) from ops.alert where dedupe_key = 'task_failed:policy.calendar' and resolved_at is null")[0][0] == 1


class Unreachable(Task):
    """A task whose fetch fails, with alerts for its failure (failure_alerts: B6 part 2's site-unreachable alert)."""
    key = "policy.calendar"; workflows = ("weekly",)
    fail = True
    def fetch(self, ctx):
        if self.fail:
            raise ConnectionError("the source is down")
    def failure_alerts(self, db, status, error):
        return [Alert("calendar_source_down", "ConnectionError" in (error or ""), "error", f"{status}: down")]
    def alerts(self, db):
        return [Alert("calendar_source_down", False)]


@pytest.mark.db
def test_a_failed_run_raises_its_failure_alerts_and_a_good_run_resolves_them():
    def open_():
        return q("select severity, message from ops.alert where dedupe_key = 'calendar_source_down' and resolved_at is null")
    t = Unreachable()
    assert run(JOB, "weekly", [t], force=True) == 1
    assert open_() == [("error", "failed: down")]
    t.fail = False
    assert run(JOB, "weekly", [t], force=True) == 0
    assert open_() == []
    assert Task().failure_alerts(None, "failed", "x") == []              # the default raises nothing


@pytest.mark.db
def test_a_database_error_is_recorded_with_postgres_detail_line():
    """Issue #149: ops.task_run.error held only "duplicate key value violates unique constraint", so the series and
    date had to be worked out from the stored responses. The DETAIL line names the key."""
    key = "test.db_detail"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'runner DETAIL line test', '{daily}', '20 hours', '4 days', false)
                     on conflict (key) do nothing""", (key,))

    class Twice(Task):
        workflows = ("daily",)
        def load(self, tx, fetched, tr):
            sid = tx.execute("select id from core.series where key='fred.DGS10'").fetchone()[0]
            for _ in range(2):
                tx.execute("insert into core.observation values (%s, '1999-01-04', 1, %s)", (sid, tr))
            return 2
    Twice.key = key

    code = run(JOB, "daily", [Twice()], only=key, force=True)
    status, err = q("select status, error from ops.task_run where task_key=%s order by id desc limit 1", key)[0]
    assert code == 1 and status == "failed"
    assert "UniqueViolation" in err and "DETAIL: Key (series_id, obs_date)=(" in err
    assert err.rstrip().endswith(", 1999-01-04) already exists.")
    assert q("select count(*) from core.observation where obs_date = '1999-01-04'") == [(0,)]   # rolled back
