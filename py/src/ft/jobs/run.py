"""The jobs' entry point, called by every job workflow (Architecture 02 §1.1-§1.3; Prompt 01 A2):

    python -m ft.jobs.run --workflow daily --target production --runner github --github-run-id <id> --git-sha <sha>

It loads config/tasks.yaml, upserts it into ops.task, builds the task list and runs it: one runner pass for daily,
weekly, refresh and backfill, the poll loop for watch (watch.py). The database is JOB_DATABASE_URL. The workflow
picks dev's or production's credentials from its `target` input (GitHub issue #2, item 1); here --target is logged
(not stored: each project holds only its own runs) and guards replays, which run on dev only.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

from psycopg.conninfo import conninfo_to_dict

from ft.jobs import config, due
from ft.jobs.runner import JobRun, Task, connect
from ft.jobs.watch import watch

WORKFLOWS = ("daily", "watch", "weekly", "refresh", "backfill")
TRIGGERS = ("schedule", "admin", "visitor", "cli")          # ops.job_run.trigger; a manual dispatch is 'cli'
NEW_YORK, INDIA = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")


def _blank(text: str) -> str | None:
    return text.strip() or None


def _date(text: str) -> date | None:
    if not (text := text.strip()):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def _int(text: str) -> int | None:
    if not (text := text.strip()):
        return None
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ft.jobs.run", description="Run one workflow's due tasks.")
    p.add_argument("--workflow", required=True, choices=WORKFLOWS)
    p.add_argument("--task", type=_blank, help="one task key: required for refresh and backfill")
    p.add_argument("--trigger", type=_blank, choices=TRIGGERS,
                   help="default cli; a GitHub cron start is always 'schedule' (GITHUB_EVENT_NAME)")
    p.add_argument("--request-id", type=_int, help="refresh: the ops.refresh_request id to link the run to")
    p.add_argument("--since", type=_date, help="backfill: first date")
    p.add_argument("--until", type=_date, help="backfill: last date (blank = today)")
    p.add_argument("--as-of", type=_date, dest="as_of",
                   help="watch on dev only: replay a past decision day, the clock starting at its release time")
    p.add_argument("--target", choices=("dev", "production"), default="dev",
                   help="the project the workflow's credentials point to (logged; replays need dev)")
    p.add_argument("--runner", choices=("github", "self_hosted", "local"), default="local")
    p.add_argument("--github-run-id", type=_int)
    p.add_argument("--git-sha", type=_blank)
    p.add_argument("--force", action="store_true",
                   help="refresh: also skip an NSE task's minimum interval (Architecture 02 §1.3)")
    p.add_argument("--config", type=Path, default=config.CONFIG, help=argparse.SUPPRESS)
    return p


def resolve_trigger(arg: str | None, env) -> str:
    if env.get("GITHUB_EVENT_NAME") == "schedule":
        return "schedule"
    return arg or "cli"


def project_ref(dsn: str) -> str | None:
    """Supabase's pooler logs in as <role>.<project ref>: the ref is public (it's in the project's URL)."""
    try:
        user = conninfo_to_dict(dsn).get("user") or ""
    except Exception:  # noqa: BLE001 - a malformed URL fails at connect, with psycopg's own message
        return None
    return user.split(".", 1)[1] if "." in user else None


def build_tasks(configs: dict[str, config.TaskConfig],
                registry: dict[str, type[Task]]) -> tuple[list[Task], list[str]]:
    """Instances of the enabled tasks that have a class, configured from tasks.yaml; and the keys not built yet."""
    tasks, missing = [], []
    for key, cfg in configs.items():
        if not cfg.enabled:
            continue
        if key not in registry:
            missing.append(key)
            continue
        t = registry[key]()
        t.workflows, t.rules, t.run_after = cfg.workflows, dict(cfg.rules), cfg.run_after
        t.min_interval, t.uses_nse, t.label = cfg.min_interval, cfg.uses_nse, cfg.description
        tasks.append(t)
    return tasks, missing


def refresh_force(task: Task, force_flag: bool) -> bool:
    """A refresh skips the due check, except that an NSE task keeps its minimum interval unless --force
    (Architecture 02 §1.3); the admin route checks the cooldown before it dispatches."""
    if force_flag or not task.uses_nse:
        return True
    task.rules = {**(task.rules or {}), "refresh": f"every {int(task.min_interval.total_seconds() // 60)} minutes"}
    return False


def replay_clock(start: datetime, monotonic: Callable[[], float] = time.monotonic) -> Callable[[], datetime]:
    """A clock that reads `start` now and runs at real speed (an --as-of replay)."""
    t0 = monotonic()
    return lambda: start + timedelta(seconds=monotonic() - t0)


def main(argv: list[str] | None = None, *, env=None, clock: Callable[[], datetime] | None = None,
         sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
         registry: dict[str, type[Task]] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    trigger, wf = resolve_trigger(args.trigger, env), args.workflow
    if wf in ("refresh", "backfill") and not args.task:
        p.error(f"--workflow {wf} needs --task")
    if args.request_id is not None and wf != "refresh":
        p.error("--request-id is for refresh runs")
    if (args.since or args.until) and wf != "backfill":
        p.error("--since and --until are for backfill runs")
    if args.since and args.until and args.since > args.until:
        p.error("--since is after --until")
    if args.as_of and (wf != "watch" or args.target != "dev"):
        p.error("--as-of replays a decision day with --workflow watch on the dev project only (--target dev)")
    if trigger == "schedule" and args.target != "production":
        p.error("a scheduled run always targets production (GitHub issue #2, item 1)")

    try:
        configs = config.load(args.config)
    except config.ConfigError as e:
        p.error(f"config/tasks.yaml: {e}")
    if registry is None:
        from ft.jobs.tasks import registry as load_registry
        registry = load_registry()
    tasks, missing = build_tasks(configs, registry)
    if args.task:
        cfg = configs.get(args.task)
        if cfg is None:
            p.error(f"unknown task {args.task!r} (not in config/tasks.yaml)")
        if not cfg.enabled:
            p.error(f"{args.task} is disabled in config/tasks.yaml")
        if args.task in missing:
            p.error(f"{args.task} isn't built yet (no class registered in ft.jobs.tasks)")
        if wf in ("daily", "watch", "weekly") and wf not in cfg.workflows:
            p.error(f"{args.task} doesn't run in the {wf} workflow")
        tasks = [t for t in tasks if t.key == args.task]

    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (for target dev, the workflow reads the DEV_ secrets)")

    real = datetime.now(UTC)
    print(f"ft.jobs.run: {wf}, target {args.target} (project {project_ref(dsn) or 'ref not in the URL'}), "
          f"trigger {trigger}, runner {args.runner}; started {real.astimezone(NEW_YORK):%Y-%m-%d %H:%M %Z} "
          f"New York, {real.astimezone(INDIA):%H:%M %Z} India")
    with connect(dsn, autocommit=True, role="sync") as c:
        config.sync(c, configs)
        replaying = due.meetings_on(c, args.as_of) if args.as_of else None
    if args.as_of:
        if replaying:
            m = replaying[0]
            start = m.release_at
            print(f"replaying {m.slug}: the clock starts at its release, {due.local(start, m.bank)}")
        else:
            start = datetime.combine(args.as_of, clock_time(12), tzinfo=UTC)
            print(f"no rate decision on {args.as_of}: nothing to replay")
        clock = replay_clock(start, monotonic)
    clock = clock or due.utcnow
    if wf in ("daily", "watch", "weekly"):
        if unbuilt := [k for k in missing if wf in configs[k].workflows]:
            print(f"not built yet, skipped: {', '.join(unbuilt)}")
        if disabled := [k for k, c in configs.items() if not c.enabled and wf in c.workflows]:
            print(f"disabled in config/tasks.yaml: {', '.join(disabled)}")

    with JobRun(dsn, wf, trigger=trigger, runner=args.runner, github_run_id=args.github_run_id, git_sha=args.git_sha,
                request_id=args.request_id, clock=clock, target=args.target, as_of=args.as_of, since=args.since,
                until=args.until) as job:
        if wf == "watch":
            watch(job, tasks, sleep=sleep)
        elif wf == "refresh":
            job.run_pass(tasks, only=args.task, force=refresh_force(tasks[0], args.force))
        elif wf == "backfill":
            job.run_pass(tasks, only=args.task, force=True)      # a date range, not a due check (Architecture 02 §1.8)
        else:
            job.run_pass(tasks)
        return job.finish()


if __name__ == "__main__":
    sys.exit(main())
