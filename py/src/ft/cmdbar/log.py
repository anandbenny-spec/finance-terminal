"""The command bar's query log (Spec 07 §4 step 6, §8; Architecture 03 §1.8): one `ops.cmdbar_log` row per ask that passed the
switch (§4 step 0), limit refusals and cache hits included.

A row holds the visitor hash (N7: never the IP address, the user agent or a cookie), the question, the route, whether the cache
answered, the re-printed query that ran, the guard's verdict and its reason class, the rows returned, whether it was answered,
the latency, the models, the tokens and the cost, and the error's class (a short fixed word such as `ai_call` or
`database: UndefinedColumn`, never the raw error). The row's columns are `ops.cmdbar_log`'s (0010); `at` is the database's
`now()`. Rows are kept 90 days: the `daily` workflow deletes older ones as `job_writer`, the one role that may.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

COLUMNS = ("visitor_hash", "question", "route", "cache_hit", "generated_sql", "sql_valid", "reject_reason", "rows_returned",
           "answered", "latency_ms", "models", "input_tokens", "output_tokens", "cost_usd", "error_class")
MAX_CLASS_CHARS = 80


@dataclass
class LogRow:
    visitor_hash: str
    question: str
    route: str | None = None
    cache_hit: bool = False
    generated_sql: str | None = None        # the re-printed query that ran (what "Show SQL" shows)
    sql_valid: bool | None = None           # the guard's verdict: true accepted, false rejected, null if no query was written
    reject_reason: str | None = None        # the rejection's class only ("guard: relation not allowed")
    rows_returned: int | None = None
    answered: bool = False
    latency_ms: int | None = None
    models: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    error_class: str | None = None

    def as_json(self) -> dict:
        """The row as the log function takes it: exactly `COLUMNS`, the cost as an exact decimal string."""
        row = asdict(self)
        row["cost_usd"] = str(self.cost_usd)
        for k in ("reject_reason", "error_class"):
            if row[k] is not None:
                row[k] = str(row[k])[:MAX_CLASS_CHARS]
        return {k: row[k] for k in COLUMNS}
