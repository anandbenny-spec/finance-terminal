"""The command bar's answer cache (Spec 07 §4 step 3; Architecture 03 §1.8): `ops.cmdbar_cache`, keyed by the normalised question.

- **The key:** SHA-256 of the question lower-cased, its whitespace collapsed and its trailing `?`, `.` and `!` dropped.
- **Valid while** the entry hasn't expired (24 hours after it was stored) and its `data_version` is unchanged.
- **`data_version`:** the newest `last_success_at`, in `core.task_freshness`, of the tasks feeding the views the answer read
  (the views come from the query that ran, so a cached entry is checked against its own views). `VIEW_TASKS` names the task-key
  prefixes behind each `semantic` view; a view it doesn't know counts every task, so a gap only invalidates more often. An
  answer that read no view (a page, "can't answer") has the version `static`, and only its expiry ends it.
- **What is stored** (the route decides, `ft.cmdbar.route`): the final object of an answered data question, a page, "that isn't in
  this site's data" when the router or the data route said so, and an answered document question (K-06), whose `data_version`
  is the policy tasks' newest last success (`answer_version`: the documents it searched).
  Never a paused or limit state, a failure (the database down, a refused call), or a document question "not found".
A hit is served with no model call; it still counts against the visitor's hourly limit (§4 step 2) and is logged.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from pglast import ast, parse_sql
from pglast.visitors import Visitor

from ft.ai import sqlguard

TTL = timedelta(hours=24)
STATIC = "static"
NONE = "none"

# The tasks feeding each semantic view, by task-key prefix (py/config/tasks.yaml). Module 1's views read the policy tasks; the
# company views read the India (equity.*) and, since 0053, the US (us.*) tasks; rate_decision_moves joins decisions and prices.
VIEW_TASKS: dict[str, tuple[str, ...]] = {
    "policy_meetings": ("policy.",),
    "policy_votes": ("policy.",),
    "policy_projections": ("policy.",),
    "market_daily": ("policy.",),
    "companies": ("equity.", "us."),
    "company_financials": ("equity.", "us."),
    "company_valuations": ("equity.", "us."),
    "rate_decision_moves": ("policy.", "equity."),
    "value_chain": ("equity.",),
    "earnings_calls": ("equity.",),
    "call_guidance": ("equity.",),
}

_TRAILING = re.compile(r"[\s?.!]+$")


def normalise(question: str) -> str:
    """Lower case, whitespace collapsed, trailing `?.!` dropped (Spec 07 §4 step 3)."""
    return _TRAILING.sub("", " ".join((question or "").lower().split()))


def key(question: str) -> bytes:
    """The cache key: SHA-256 of the normalised question (32 bytes, as `ops.cmdbar_cache.question_sha` requires)."""
    return hashlib.sha256(normalise(question).encode()).digest()


class _Relations(Visitor):
    def __init__(self):
        self.names: set[str] = set()

    def visit_RangeVar(self, ancestors, node):
        self.names.add((node.relname or "").lower())


def views_read(sql: str | None) -> list[str]:
    """The semantic views a query reads, sorted. No query (or one that doesn't parse) reads none."""
    if not sql:
        return []
    try:
        tree = parse_sql(sql)
    except Exception:                                    # noqa: BLE001 - pglast's ParseError; a stored answer's SQL always parsed once
        return []
    v = _Relations()
    for raw in tree:
        if isinstance(raw, ast.RawStmt):
            v(raw.stmt)
    return sorted(n for n in v.names if n in sqlguard.VIEWS)


def data_version(views: list[str], freshness: dict[str, datetime]) -> str:
    """The newest last success of the tasks feeding `views` (ISO 8601), `static` for no view, `none` if no task has one."""
    if not views:
        return STATIC
    prefixes: list[str] = []
    for view in views:
        if view not in VIEW_TASKS:
            prefixes = [""]                              # unknown: every task counts
            break
        prefixes.extend(VIEW_TASKS[view])
    return _newest(prefixes, freshness)


def _newest(prefixes: list[str] | tuple[str, ...], freshness: dict[str, datetime]) -> str:
    times = [t for task, t in freshness.items() if t is not None and task.startswith(tuple(prefixes))]
    return max(times).isoformat() if times else NONE


# A document answer (K-06) reads Fed and RBI policy documents, which the policy tasks load and summarise.
DOCUMENT_TASKS = ("policy.",)


def answer_version(answer: dict, freshness: dict[str, datetime]) -> str:
    """An answer's `data_version`: a document answer's is the policy tasks' newest last success (the documents it searched); any
    other answer's follows the views its query read (`data_version`)."""
    if answer.get("route") == "document":
        return _newest(DOCUMENT_TASKS, freshness)
    return data_version(views_read(answer.get("sql")), freshness)

