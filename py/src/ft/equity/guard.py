"""The production guard of module 2's tasks (decided by Claude under the standing delegation, 28 Oct 2026; recorded on
tracker #30 and on the 2A-04 to 2A-10 cards).

Production's scheduled `weekly` has been on since 27 Oct. Once an equity task is in tasks.yaml, the first Sunday
after it merges would run it on production before Anand has pushed the `equity` schema there (the run would fail) or
before 2A-16 has run the approved backfills in their dependency order (a weekly run with no stored history has no
defined window). `equity_due` makes a task skip, with its reason, in those two cases:

1. the table the task needs isn't on the project (`requires`, e.g. "equity.company": its migration isn't pushed);
2. in a scheduled workflow (daily, weekly, watch) the task has never succeeded on the project: its first run is a
   `refresh` or `backfill` (dev runs, and 2A-16 on production), which this guard never holds.

Otherwise the task's own due rule decides. Every equity task's `due()` calls it; the first to land built it, and the
others reuse it, each naming the table its own migration creates (2A-23's and 2A-24's tasks pass their own).
"""
from __future__ import annotations

from ft.jobs.due import Due

SCHEDULED = ("daily", "weekly", "watch")


def equity_due(db, task_key: str, workflow: str, rule_due: Due, *, requires: str = "equity.company") -> Due:
    """`rule_due` is the task's own verdict (its tasks.yaml rule); `db` is the runner's control connection."""
    present = db.execute("select to_regclass(%s) is not null", (requires,)).fetchone()[0]
    if not present:
        return Due(False, "not set up: its migration isn't pushed to this project")
    if workflow in SCHEDULED:
        ran = db.execute("select exists (select 1 from ops.task_run where task_key = %s and status = 'success')",
                         (task_key,)).fetchone()[0]
        if not ran:
            return Due(False, "waiting for its first recorded run; on production that's 2A-16's backfill")
    return rule_due
