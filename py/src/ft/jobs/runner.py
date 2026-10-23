"""The refresh-job runner (Architecture 02 §1.2). One job run per workflow run; a pass runs each due task through
fetch (no transaction), load and checks (one transaction), then alerts and freshness. `watch` calls a pass once
per poll in the same job run (watch.py). The run CLI (run.py) builds the tasks from config/tasks.yaml."""
from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

import psycopg

from ft.jobs import due as due_rules
from ft.jobs.due import Due, Meeting


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = True          # a failed blocking check rolls the whole load back


@dataclass
class Alert:
    dedupe_key: str
    active: bool                   # condition true now -> open (once); false -> resolve any open one
    severity: str = "warn"
    message: str = ""


class ChecksFailed(Exception):
    def __init__(self, checks): self.checks = checks


class LockLost(Exception):
    pass


# Every connection sends TCP keepalives, so a path that drops idle connections doesn't silently kill the
# control connection while a task fetches for many minutes (libpq otherwise uses the OS default, ~2 hours).
KEEPALIVES = {"keepalives": 1, "keepalives_idle": 60, "keepalives_interval": 15, "keepalives_count": 4}


def connect(dsn: str, *, autocommit: bool = False, role: str = "load") -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=autocommit, application_name=f"ft-runner-{role}", **KEEPALIVES)


class Control:
    """The control connection: run log, task locks, alerts. It sits idle while a task fetches (and between watch
    polls), so before anything is written after a fetch, ensure() checks it and reconnects if it dropped. The task
    lock is session-level and goes with a dropped connection, so a reconnect takes it again; if another run took it
    meanwhile, the task is recorded as failed and nothing is loaded (Review 01, F3). Each reconnect is logged with
    how long the connection had been idle: the evidence for the pooler's idle behaviour (Architecture 02 §7, 6)."""

    def __init__(self, dsn: str):
        self.dsn, self.held, self.reconnects = dsn, None, 0
        self.conn = connect(dsn, autocommit=True, role="control")
        self.used = time.monotonic()

    def execute(self, *a):
        self.used = time.monotonic()
        return self.conn.execute(*a)

    def lock(self, key: str) -> bool:
        got = self.execute("select pg_try_advisory_lock(hashtext(%s))", ("task:" + key,)).fetchone()[0]
        self.held = key if got else None
        return got

    def unlock(self) -> None:
        key, self.held = self.held, None
        if key is None:
            return
        try:
            self.execute("select pg_advisory_unlock(hashtext(%s))", ("task:" + key,))
        except psycopg.OperationalError:
            pass                      # the connection is gone, and the server released the lock with it

    def ensure(self) -> bool:
        """Reconnect if the connection dropped. False if the task lock it held couldn't be taken again."""
        idle = time.monotonic() - self.used
        try:
            if not self.conn.closed:
                self.execute("select 1")
                return True
        except psycopg.OperationalError:
            pass
        self.conn.close()
        self.conn = connect(self.dsn, autocommit=True, role="control")
        self.used, self.reconnects = time.monotonic(), self.reconnects + 1
        print(f"  control connection had dropped after {idle:.0f} s idle; reconnected")
        return self.held is None or self.lock(self.held)

    def close(self) -> None:
        self.conn.close()


@dataclass
class Context:
    """What a task's fetch knows about its run. For a decision-day rule, `meeting` is the meeting being watched."""
    workflow: str
    job_run_id: int
    task_run_id: int
    now: datetime                    # the run's clock when the task started (a replay's clock on --as-of)
    due: Due
    target: str = "dev"
    trigger: str = "cli"
    as_of: date | None = None
    since: date | None = None
    until: date | None = None

    @property
    def meeting(self) -> Meeting | None:
        return self.due.meeting


class Task:
    key: str
    workflows: tuple[str, ...] = ()   # workflows this task runs in
    rules: dict[str, str] | None = None   # workflow -> due rule, from config/tasks.yaml (run.py sets it)
    min_interval = timedelta(hours=20)
    run_after: tuple[str, ...] = ()
    uses_nse = False
    label = ""

    def due(self, db, workflow: str, now: datetime | None = None) -> Due:
        if self.rules and workflow in self.rules:
            return due_rules.evaluate(db, self.key, self.rules[workflow], now or due_rules.utcnow())
        # A task built without config (the runner's own tests): every min_interval, on the database's clock.
        last = db.execute("select max(finished_at) from ops.task_run where task_key=%s and status='success'",
                          (self.key,)).fetchone()[0]
        if last is None:
            return Due(True, "never succeeded")
        age = db.execute("select now() - %s::timestamptz", (last,)).fetchone()[0]
        return Due(age >= self.min_interval, f"last success {age} ago")

    def fetch(self, ctx: Context): return None              # network: outside any transaction
    def load(self, tx, fetched, task_run_id) -> int: return 0
    def checks(self, tx, fetched, task_run_id) -> list[Check]: return []
    def alerts(self, db) -> list[Alert]: return []          # evaluated after the load, in its own transaction


def _order(tasks):
    by_key, done, out = {t.key: t for t in tasks}, set(), []
    def visit(t):
        if t.key in done: return
        for k in t.run_after:
            if k in by_key: visit(by_key[k])
        done.add(t.key); out.append(t)
    for t in tasks: visit(t)
    return out


BAD = ("checks_failed", "failed")


class JobRun:
    """One ops.job_run row per workflow run. Use as a context manager: an unexpected exception marks it failed."""

    def __init__(self, dsn: str, workflow: str, *, trigger: str = "cli", runner: str = "local",
                 github_run_id: int | None = None, git_sha: str | None = None, request_id: int | None = None,
                 clock: Callable[[], datetime] = due_rules.utcnow, target: str = "dev", as_of: date | None = None,
                 since: date | None = None, until: date | None = None):
        self.dsn, self.workflow, self.clock = dsn, workflow, clock
        self.ctx = {"target": target, "trigger": trigger, "as_of": as_of, "since": since, "until": until}
        self.outcomes: list[tuple[str, str]] = []
        self.db = Control(dsn)                                     # control connection: run log, locks, alerts
        self.id = self.db.execute(
            """insert into ops.job_run (workflow, trigger, runner, github_run_id, git_sha) values (%s,%s,%s,%s,%s)
               returning id""", (workflow, trigger, runner, github_run_id, git_sha)).fetchone()[0]
        if request_id is not None:                                 # the admin panel shows the request's run
            linked = self.db.execute("update ops.refresh_request set job_run_id=%s where id=%s and job_run_id is null",
                                     (self.id, request_id)).rowcount
            if not linked:
                print(f"  refresh request {request_id} not found or already linked; run {self.id} not linked")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self.db.ensure()
                self.db.execute("update ops.job_run set status='failed', finished_at=now() where id=%s and "
                                "finished_at is null", (self.id,))
            except psycopg.Error:
                pass
        self.db.close()
        return False

    def _skip(self, key: str, status: str, reason: str | None, out: list) -> None:
        self.db.execute("""insert into ops.task_run (job_run_id, task_key, status, finished_at, error)
                           values (%s,%s,%s,now(),%s)""", (self.id, key, status, reason))
        out.append((key, status))

    def run_pass(self, tasks: list[Task], *, only: str | None = None, force: bool = False,
                 record_skips: bool = True) -> list[tuple[str, str]]:
        """Run each due task once. With record_skips=False (watch's later polls), tasks that aren't due leave no row."""
        self.db.ensure()                                           # idle between watch polls: reconnect if dropped
        out: list[tuple[str, str]] = []
        for t in _order(tasks):
            if only:
                if t.key != only: continue
            elif self.workflow not in t.workflows: continue
            now = self.clock()
            try:
                d = Due(True, "forced") if force else t.due(self.db, self.workflow, now)
            except Exception:  # noqa: BLE001 - a broken due rule fails this task, not the run
                self._skip(t.key, "failed", "due rule: " + traceback.format_exc(limit=3), out)
                continue
            if not d.due:
                if record_skips:
                    self._skip(t.key, "skipped_not_due", d.reason, out)
                continue
            tr = self.db.execute("insert into ops.task_run (job_run_id, task_key) values (%s,%s) returning id",
                                 (self.id, t.key)).fetchone()[0]
            if not self.db.lock(t.key):
                self.db.execute("update ops.task_run set status='skipped_locked', finished_at=now() where id=%s", (tr,))
                out.append((t.key, "skipped_locked")); continue
            status, err, checks, rows = "success", None, [], 0
            ctx = Context(workflow=self.workflow, job_run_id=self.id, task_run_id=tr, now=now, due=d, **self.ctx)
            try:
                fetched = t.fetch(ctx)
                if not self.db.ensure():                           # idle through the fetch: check before loading
                    raise LockLost("control connection dropped during the fetch and another run now holds the task lock")
                with connect(self.dsn) as txc, txc.transaction():  # one transaction per task load
                    rows = t.load(txc, fetched, tr)
                    checks = t.checks(txc, fetched, tr)
                    if any(c.blocking and not c.passed for c in checks):
                        raise ChecksFailed(checks)
            except LockLost as e:
                status, err = "failed", str(e)
            except ChecksFailed:
                status, err = "checks_failed", "; ".join(c.name for c in checks if c.blocking and not c.passed)
            except Exception:  # noqa: BLE001 - one task's failure must never stop the run
                status, err = "failed", traceback.format_exc(limit=3)
            finally:
                self.db.unlock()
            self.db.ensure()                                       # a long load can idle the control connection too
            self._record(t, tr, status, err, checks, rows)
            out.append((t.key, status))
        for k, s in out: print(f"  {k}: {s}")
        self.outcomes += out
        return out

    def _record(self, t: Task, tr: int, status: str, err, checks, rows) -> None:
        db = self.db
        db.execute("update ops.task_run set status=%s, finished_at=now(), rows_written=%s, error=%s, checks=%s where id=%s",
                   (status, rows if status == "success" else 0, err, json.dumps([asdict(c) for c in checks]), tr))
        # Alerts: one open alert per condition; a later run resolves it when the condition clears.
        failed_alert = Alert(f"task_failed:{t.key}", status in BAD, "error", f"{t.key} {status}: {err}")
        warn_alerts = [Alert(f"check:{t.key}:{c.name}", not c.passed, "warn", f"{c.name}: {c.detail}")
                       for c in checks if not c.blocking]
        for a in [failed_alert, *warn_alerts, *(t.alerts(db.conn) if status == "success" else [])]:
            if a.active:
                db.execute("""insert into ops.alert (task_key, severity, message, dedupe_key) values (%s,%s,%s,%s)
                              on conflict (dedupe_key) where resolved_at is null and dedupe_key is not null do nothing""",
                           (t.key, a.severity, a.message, a.dedupe_key))
            else:
                db.execute("update ops.alert set resolved_at=now() where dedupe_key=%s and resolved_at is null", (a.dedupe_key,))
        # Public freshness: what visitors may know, no internals.
        db.execute("""insert into core.task_freshness (task_key, label, last_checked_at, last_success_at, status, next_due_at,
                                                 stale_after)
                      select t.key, t.description,
                             case when %(ok)s then now() end,
                             (select max(finished_at) from ops.task_run where task_key=t.key and status='success'),
                             case when %(ok)s then 'ok' else 'failed' end,
                             (select max(finished_at) from ops.task_run where task_key=t.key and status='success') + t.min_interval,
                             t.overdue_after
                      from ops.task t where t.key=%(k)s
                      on conflict (task_key) do update set
                        last_checked_at = coalesce(excluded.last_checked_at, core.task_freshness.last_checked_at),
                        last_success_at = excluded.last_success_at, status = excluded.status, next_due_at = excluded.next_due_at,
                        stale_after = excluded.stale_after""",
                   {"ok": status == "success", "k": t.key})

    def finish(self) -> int:
        """Close the job run. Each task counts by its last outcome that wasn't a skip (watch polls a task many
        times: a poll that failed and a later one that succeeded is a success)."""
        last: dict[str, str] = {}
        for k, s in self.outcomes:
            if not s.startswith("skipped") or k not in last:
                last[k] = s
        ran = [s for s in last.values() if not s.startswith("skipped")]
        bad = [s for s in ran if s in BAD]
        final = "success" if not bad else ("failed" if len(bad) == len(ran) else "partial")
        self.db.ensure()
        self.db.execute("update ops.job_run set status=%s, finished_at=now() where id=%s", (final, self.id))
        print(f"  job run {self.id}: {final}")
        return 1 if bad else 0        # non-zero exit -> GitHub marks the run failed -> email to the workflow's creator


def run(dsn: str, workflow: str, tasks: list[Task], *, only=None, force=False, **job) -> int:
    """One pass in its own job run: daily, weekly, refresh and backfill. `job` takes JobRun's keyword arguments."""
    with JobRun(dsn, workflow, **job) as j:
        j.run_pass(tasks, only=only, force=force)
        return j.finish()
