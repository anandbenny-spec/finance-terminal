"""cmdbar.log_retention: the command bar's 90-day log retention (Spec 07 §4 step 6, §8; K-05 part 2, #583).

Database only: it fetches nothing. Each run deletes, as `job_writer` (the one role 0010 lets delete from them):
- `ops.cmdbar_log` rows older than 90 days: the question log keeps the question, the daily-rotating visitor hash, the route, the
  query that ran, counts, timings, models and cost for 90 days and no longer (Spec 07 §7's "What is logged");
- `ops.cmdbar_cache` entries that have expired (a cached answer lives 24 hours: Spec 07 §4 step 3).
Neither table holds source data. The usage windows (`ops.cmdbar_usage`) are pruned by migration 0054's count function itself.

Checks (B blocking): `log_kept` that no log row older than 90 days is left; `cache_kept` that no expired cache entry is left.
`deleted` (informational) gives the two counts. While the bar is paused (Spec 07 §3) both tables are empty and the run deletes
nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

KEEP_DAYS = 90


@dataclass
class Run:
    now: datetime
    log_deleted: int = 0
    cache_deleted: int = 0


@register
class CmdbarLogRetention(Task):
    key = "cmdbar.log_retention"

    def fetch(self, ctx: Context) -> Run:              # nothing to fetch
        return Run(ctx.now)

    def load(self, tx, r: Run, task_run_id) -> int:
        r.log_deleted = tx.execute("delete from ops.cmdbar_log where at < now() - make_interval(days => %s)",
                                   (KEEP_DAYS,)).rowcount
        r.cache_deleted = tx.execute("delete from ops.cmdbar_cache where expires_at <= now()").rowcount
        return r.log_deleted + r.cache_deleted

    def checks(self, tx, r: Run, task_run_id) -> list[Check]:
        old = tx.execute("select count(*) from ops.cmdbar_log where at < now() - make_interval(days => %s)",
                         (KEEP_DAYS,)).fetchone()[0]
        expired = tx.execute("select count(*) from ops.cmdbar_cache where expires_at <= now()").fetchone()[0]
        return [Check("log_kept", old == 0, f"{old} log rows older than {KEEP_DAYS} days left"),
                Check("cache_kept", expired == 0, f"{expired} expired cache entries left"),
                Check("deleted", True, f"{r.log_deleted} log rows, {r.cache_deleted} cache entries", blocking=False)]
