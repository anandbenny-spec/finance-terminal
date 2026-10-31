"""The freshness drill (Prompt 01 D4; Review 01 F17; Architecture 02 §1.4, last bullet).

Only the runner writes `core.task_freshness.status`, so if the scheduled runs stop it stays 'ok'. Migration 0011
makes `api.task_freshness` show 'stale' by itself once now() passes `last_success_at + stale_after`. The drill
proves it on a real project with no run: it moves one task's `last_success_at` (and `last_checked_at`) back past
its `stale_after`, and Anand looks at the status bar, which should then read that a source is delayed.

    python -m ft.jobs.drill_freshness --task policy.calendar --action push --target dev
    python -m ft.jobs.drill_freshness --task policy.calendar --action restore --target dev \
        --last-success-at <printed by push> --last-checked-at <printed by push>
    python -m ft.jobs.drill_freshness --task policy.calendar --action status --target dev

- `push` prints the row's times before it changes them (they're what `restore` takes back), then the row after.
  It refuses a task with no freshness row, no `stale_after` or no recorded success (nothing to move back), and a
  task whose status isn't 'ok' (a failed task already shows "Update delayed": the drill would prove nothing).
- `restore` puts the printed times back, but never earlier than what's there now: if a real run succeeded since the
  push, its newer times stay. The task's next scheduled run would also put things right by itself.
- `status` prints the row and whether the public view now reads it as stale (the view's own rule, read from the
  row: `JOB_DATABASE_URL`'s role, `job_writer`, can't read `api` views; the site's status bar is the real check).

It connects as `job_writer` (JOB_DATABASE_URL; the workflow picks dev's or production's from `--target`) and
changes only the one `core.task_freshness` row, in one transaction. Prompt 01 §3, phase D: ask Anand before each
run; the production run needs his OK too.

Each run is also recorded in `ops.drill_run` (migration 0021; issue #133), so "drills run and passed" on the accuracy
page comes from the database: `push` opens a row (the times it moved), `restore` first reads the row the way
`status` does and records that reading, puts the times back, and closes the row with `passed` true when the row read
as stale just before the restore. The record is written in the same transaction as the change it describes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from psycopg.types.json import Jsonb

from ft.jobs.runner import connect

MARGIN = "1 hour"      # how far past stale_after the push goes, so the status flips at once and stays flipped

ROW_SQL = """select task_key, status, last_checked_at, last_success_at, stale_after,
                    (status = 'ok' and now() > last_success_at + stale_after) as reads_stale
               from core.task_freshness where task_key = %s"""


class DrillError(Exception):
    """The drill refused to run. The message says why."""


def _row(conn, task: str) -> dict | None:
    cur = conn.execute(ROW_SQL, (task,))
    r = cur.fetchone()
    if r is None:
        return None
    names = [d.name for d in cur.description]
    return dict(zip(names, r, strict=True))


def _show(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else str(v) if k == "stale_after" and v is not None else v
    return out


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def push(conn, task: str, *, target: str = "dev", github_run_id: int | None = None) -> tuple[dict, dict]:
    """Moves the task's times back past stale_after and opens the drill record. Returns the row before and after."""
    with conn.transaction():
        before = _row(conn, task)
        if before is None:
            raise DrillError(f"{task}: no core.task_freshness row")
        if before["stale_after"] is None:
            raise DrillError(f"{task}: stale_after is not set (the runner copies it from overdue_after)")
        if before["last_success_at"] is None:
            raise DrillError(f"{task}: no recorded success to move back")
        if before["status"] != "ok":
            raise DrillError(f"{task}: status is {before['status']!r}, not 'ok': the drill would prove nothing")
        conn.execute(f"""update core.task_freshness
                            set last_success_at = now() - stale_after - interval '{MARGIN}',
                                last_checked_at = now() - stale_after - interval '{MARGIN}'
                          where task_key = %s""", (task,))
        after = _row(conn, task)
        conn.execute("""insert into ops.drill_run (drill, target, github_run_id, counts) values ('d4_freshness', %s, %s, %s)""",
                     (target, github_run_id, Jsonb({
                         "task": task,
                         "before": {"last_success_at": _iso(before["last_success_at"]),
                                    "last_checked_at": _iso(before["last_checked_at"]), "reads_stale": before["reads_stale"]},
                         "after": {"last_success_at": _iso(after["last_success_at"]),
                                   "last_checked_at": _iso(after["last_checked_at"]), "reads_stale": after["reads_stale"]},
                     })))
    return before, after


def restore(conn, task: str, last_success_at: datetime, last_checked_at: datetime | None, *,
            target: str = "dev", github_run_id: int | None = None) -> dict:
    """Records the reading, puts the printed times back (keeping anything newer: a real run since the push) and
    closes the drill record: passed when the row read as stale just before the restore."""
    with conn.transaction():
        reading = _row(conn, task)
        if reading is None:
            raise DrillError(f"{task}: no core.task_freshness row")
        passed = bool(reading["reads_stale"])
        conn.execute("""update core.task_freshness
                           set last_success_at = greatest(last_success_at, %s),
                               last_checked_at = greatest(last_checked_at, coalesce(%s, last_checked_at))
                         where task_key = %s""", (last_success_at, last_checked_at, task))
        after = _row(conn, task)
        counts = {"task": task,
                  "before_restore": {"last_success_at": _iso(reading["last_success_at"]), "reads_stale": reading["reads_stale"]},
                  "after_restore": {"last_success_at": _iso(after["last_success_at"]), "reads_stale": after["reads_stale"]}}
        open_id = conn.execute("""select id from ops.drill_run where drill = 'd4_freshness' and finished_at is null
                                     and counts->>'task' = %s order by id desc limit 1""", (task,)).fetchone()
        if open_id is not None:
            conn.execute("""update ops.drill_run set finished_at = now(), passed = %s, counts = counts || %s::jsonb,
                                   github_run_id = coalesce(github_run_id, %s) where id = %s""",
                         (passed, Jsonb(counts), github_run_id, open_id[0]))
        else:                  # a push made before the record existed (or on another database): record the restore alone
            conn.execute("""insert into ops.drill_run (drill, target, finished_at, passed, counts, github_run_id)
                            values ('d4_freshness', %s, now(), %s, %s, %s)""",
                         (target, passed, Jsonb({**counts, "push_not_recorded": True}), github_run_id))
        return after


def _instant(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} has no UTC offset")
    return t


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ft.jobs.drill_freshness", description=__doc__.split("\n\n")[0])
    p.add_argument("--task", required=True)
    p.add_argument("--action", required=True, choices=("push", "restore", "status"))
    p.add_argument("--target", choices=("dev", "production"), default="dev", help="logged; the workflow picks the credentials")
    p.add_argument("--last-success-at", type=_instant, help="restore: the value push printed")
    p.add_argument("--last-checked-at", type=_instant, help="restore: the value push printed")
    return p


def main(argv: list[str] | None = None, *, env=None) -> int:
    env = os.environ if env is None else env
    p = parser()
    args = p.parse_args(argv)
    if args.action == "restore" and args.last_success_at is None:
        p.error("restore needs --last-success-at (printed by push)")
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (for target dev, the workflow reads the DEV_ secrets)")
    run_id = int(env["GITHUB_RUN_ID"]) if (env.get("GITHUB_RUN_ID") or "").isdigit() else None
    print(f"freshness drill: {args.action} {args.task}, target {args.target}")
    try:
        with connect(dsn, autocommit=True, role="drill") as conn:
            if args.action == "push":
                before, after = push(conn, args.task, target=args.target, github_run_id=run_id)
                print("before:", json.dumps(_show(before)))
                print("after: ", json.dumps(_show(after)))
                print(f"restore with: --last-success-at {before['last_success_at'].isoformat()}"
                      + (f" --last-checked-at {before['last_checked_at'].isoformat()}" if before["last_checked_at"] else ""))
            elif args.action == "restore":
                print("after:", json.dumps(_show(restore(conn, args.task, args.last_success_at, args.last_checked_at,
                                                    target=args.target, github_run_id=run_id))))
            else:
                row = _row(conn, args.task)
                if row is None:
                    raise DrillError(f"{args.task}: no core.task_freshness row")
                print("now:", json.dumps(_show(row)))
    except DrillError as e:
        print(f"freshness drill refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
