"""The `watch` workflow (Architecture 02 §1.1; Review 01, F3; Prompt 01 A2).

Each poll is one short pass of the runner in the same job run: a task's fetch is one look at the page or feed,
so no connection sits idle for an hour holding a task lock. Polls start every 2 minutes. A poll that finds
nothing records success with 0 rows. The loop ends when no watch task is due any more: the decision document
is stored and the AI step has run (its rule, after_decision_document, follows the same pattern: Architecture 03
§1.3), or the meeting's window has closed (due.py: 40 minutes after the Fed's release, 60 after the RBI's). On
a non-decision day the first pass finds nothing due and the run exits at once.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta

from ft.jobs.due import local
from ft.jobs.runner import JobRun, Task

POLL_EVERY = timedelta(minutes=2)


def watch(job: JobRun, tasks: list[Task], *, sleep: Callable[[float], None] = time.sleep,
          poll_every: timedelta = POLL_EVERY) -> int:
    """Poll until nothing is due. Returns the number of polls."""
    tasks = [t for t in tasks if "watch" in t.workflows]
    poll = 0
    while True:
        poll += 1
        started = job.clock()
        print(f"poll {poll} at {started:%Y-%m-%d %H:%M:%S %Z}")
        job.run_pass(tasks, record_skips=poll == 1)               # later polls leave no rows for tasks not due
        # Only a decision-day window keeps the loop polling (a task due under any other rule ran in this pass).
        still = [(t, d) for t in tasks if (d := t.due(job.db, "watch", job.clock())).due and d.until is not None]
        if not still:
            print(f"watch: nothing left to do after {poll} poll(s)")
            return poll
        next_at = started + poll_every
        last = max((d for _, d in still), key=lambda d: d.until)
        if next_at >= last.until:
            print(f"watch: the window closes at {local(last.until, last.meeting.bank)} before the next poll; "
                  f"still due: {', '.join(t.key for t, _ in still)} (the daily run catches up)")
            return poll
        sleep(max(0.0, (next_at - job.clock()).total_seconds()))
