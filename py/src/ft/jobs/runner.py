"""Prototype of the refresh-job runner (Architecture 02). Proves the run semantics against the real schema;
the production version adds the HTTP client, storage function and real tasks."""
from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass
from datetime import timedelta

import psycopg


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
    """The control connection: run log, task locks, alerts. It sits idle while a task fetches, so before
    anything is written after a fetch, ensure() checks it and reconnects if it dropped. The task lock is
    session-level and goes with a dropped connection, so a reconnect takes it again; if another run took it
    meanwhile, the task is recorded as failed and nothing is loaded (Review 01, F3)."""

    def __init__(self, dsn: str):
        self.dsn, self.held = dsn, None
        self.conn = connect(dsn, autocommit=True, role="control")

    def execute(self, *a):
        return self.conn.execute(*a)

    def lock(self, key: str) -> bool:
        got = self.conn.execute("select pg_try_advisory_lock(hashtext(%s))", ("task:" + key,)).fetchone()[0]
        self.held = key if got else None
        return got

    def unlock(self) -> None:
        key, self.held = self.held, None
        if key is None:
            return
        try:
            self.conn.execute("select pg_advisory_unlock(hashtext(%s))", ("task:" + key,))
        except psycopg.OperationalError:
            pass                      # the connection is gone, and the server released the lock with it

    def ensure(self) -> bool:
        """Reconnect if the connection dropped. False if the task lock it held couldn't be taken again."""
        try:
            if not self.conn.closed:
                self.conn.execute("select 1")
                return True
        except psycopg.OperationalError:
            pass
        self.conn.close()
        self.conn = connect(self.dsn, autocommit=True, role="control")
        return self.held is None or self.lock(self.held)

    def close(self) -> None:
        self.conn.close()


class Task:
    key: str
    workflows: tuple[str, ...] = ()   # workflows this task runs in; due rules per workflow come from config
    min_interval = timedelta(hours=20)
    run_after: tuple[str, ...] = ()
    label = ""

    def due(self, db, workflow: str) -> tuple[bool, str]:
        last = db.execute("select max(finished_at) from ops.task_run where task_key=%s and status='success'",
                          (self.key,)).fetchone()[0]
        if last is None:
            return True, "never succeeded"
        age = db.execute("select now() - %s::timestamptz", (last,)).fetchone()[0]
        return (age >= self.min_interval, f"last success {age} ago")

    def fetch(self, ctx): return None                      # network: outside any transaction
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


def run(dsn: str, workflow: str, tasks: list[Task], *, trigger="cli", runner="local", only=None, force=False) -> int:
    db = Control(dsn)                                         # control connection: run log, locks, alerts
    job_run_id = db.execute("insert into ops.job_run (workflow, trigger, runner) values (%s,%s,%s) returning id",
                            (workflow, trigger, runner)).fetchone()[0]
    outcomes = []
    for t in _order(tasks):
        if workflow not in t.workflows and not only: continue
        if only and t.key != only: continue
        tr = db.execute("insert into ops.task_run (job_run_id, task_key) values (%s,%s) returning id",
                        (job_run_id, t.key)).fetchone()[0]
        status, err, checks, rows = "success", None, [], 0
        is_due, why = (True, "forced") if force else t.due(db, workflow)
        if not is_due:
            db.execute("update ops.task_run set status='skipped_not_due', finished_at=now(), error=%s where id=%s", (why, tr))
            outcomes.append((t.key, "skipped_not_due")); continue
        if not db.lock(t.key):
            db.execute("update ops.task_run set status='skipped_locked', finished_at=now() where id=%s", (tr,))
            outcomes.append((t.key, "skipped_locked")); continue
        try:
            fetched = t.fetch(None)
            if not db.ensure():                                  # idle through the fetch: check before loading
                raise LockLost("control connection dropped during the fetch and another run now holds the task lock")
            with connect(dsn) as txc, txc.transaction():          # one transaction per task load
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
            db.unlock()
        db.ensure()                                              # a long load can idle the control connection too
        db.execute("update ops.task_run set status=%s, finished_at=now(), rows_written=%s, error=%s, checks=%s where id=%s",
                   (status, rows if status == "success" else 0, err, json.dumps([asdict(c) for c in checks]), tr))
        # Alerts: one open alert per condition; a later run resolves it when the condition clears.
        failed_alert = Alert(f"task_failed:{t.key}", status in ("checks_failed", "failed"), "error",
                             f"{t.key} {status}: {err}")
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
        outcomes.append((t.key, status))
    bad = [o for o in outcomes if o[1] in ("checks_failed", "failed")]
    final = "success" if not bad else ("failed" if len(bad) == len([o for o in outcomes if not o[1].startswith("skipped")]) else "partial")
    db.execute("update ops.job_run set status=%s, finished_at=now() where id=%s", (final, job_run_id))
    db.close()
    for k, s in outcomes: print(f"  {k}: {s}")
    return 1 if bad else 0            # non-zero exit -> GitHub marks the run failed -> email to the workflow's creator
