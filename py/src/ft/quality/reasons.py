"""The reason categoriser (issue #134, Accuracy 2; Plan 02 §4; the 27 Oct late-night handoff, §2 item 1).

From an AI output's `status` and `status_reason`, one category for the accuracy page:

    citation      no verified citation (a bullet's, or a quote that isn't found exactly once)
    numbers       a number the cited passage doesn't hold
    format        the schema, the JSON or the bullet shape
    stopped       the reply stopped early
    held_review   kept as a draft for review ("held: ...": never retried)
    other         anything else

The categories come from the stable phrases the checks write themselves: each check module keeps its table beside the
check (`REASON_PHRASES` in ft/ai/summaries.py, ft/ai/extraction.py and ft/jobs/tasks/ai_batch.py), and the tests run
the real checks on crafted bad replies and assert each lands in its category, so a reworded message fails a test
instead of drifting into `other`. No loose text parsing: an unmatched reason is `other`, counted as unmatched so the
task's warning names the gap and the new phrase gets mapped.

An output with several reasons takes the first category in PRIORITY (a reply that stopped early has no bullets worth
judging; then the evidence; then the numbers; then the shape).
"""
from __future__ import annotations

import re

from ft.ai import extraction, summaries
from ft.jobs.tasks import ai_batch

CATEGORIES = ("citation", "numbers", "format", "stopped", "other")
PRIORITY = ("stopped", "citation", "numbers", "format")
HELD_PREFIX = "held:"

_TABLES = (summaries.REASON_PHRASES, extraction.REASON_PHRASES, ai_batch.REASON_PHRASES)
_PATTERNS = {cat: [re.compile(p, re.IGNORECASE) for t in _TABLES for p in t.get(cat, ())] for cat in PRIORITY}
_KNOWN_OTHER = [re.compile(p, re.IGNORECASE) for p in ai_batch.KNOWN_OTHER_PHRASES]


def categorise(status: str, status_reason: str | None) -> tuple[str | None, bool]:
    """(category, matched) of one output. The category is None for an output that isn't a rejection or a hold
    (published, superseded, or a draft in flight); `matched` is False only for a reason no phrase table knows (it
    counts as `other` and the task warns)."""
    reason = (status_reason or "").strip()
    if status == "draft" and reason.lower().startswith(HELD_PREFIX):
        return "held_review", True
    if status != "rejected":
        return None, True
    for cat in PRIORITY:
        if any(p.search(reason) for p in _PATTERNS[cat]):
            return cat, True
    return "other", any(p.search(reason) for p in _KNOWN_OTHER)
