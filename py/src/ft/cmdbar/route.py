"""The command bar's live route (Spec 07 §3, §4, §8, N1, N7, N8; Architecture 03 §1.8): one question, from the switch to the log.

`ask` runs the steps in Spec 07 §4's order and returns the final object; `emit` is told each status line (Design 01 §4.6's
words) as it starts. `ft.api.cmdbar` streams both as newline-delimited JSON.

0. **The switch, first.** A secret missing (`SECRETS`), the store unreadable, `cmdbar_ai_enabled` not true, or month-to-date AI
   spend at or above `ai_monthly_budget_usd` (N8) returns the paused state. A missing secret is found before anything else, with
   no call of any kind; otherwise the switch's one read (`Store.state`) is the only call. No model, data query or Voyage call is
   made, nothing is counted and **no log row** is written. It fails closed: anything unreadable is "paused".
2. **Limits** (`ft.cmdbar.limits`): the visitor's hourly count on every ask; the overall daily count (IST day) only for a question
   that reaches the router. Over either: the limit state, logged with `answered = false`.
3. **Cache** (`ft.cmdbar.cache`): a valid entry is served with no model call.
4. **Router**, then 5a **the data route** (K-04's `ft.cmdbar.sql.answer_data`, the code the gates measure), 5c **a page** (checked
   against `api.search_index`), 5d **unsupported** ("That isn't in this site's data." and three suggested pages). 5b, document
   questions, is K-06's: until then the document route answers "Not found in the stored Fed and RBI documents." with no Voyage
   call and no model call beyond the router.
6. **The log** (`ft.cmdbar.log`): one row per ask that passed step 0.

**What a visitor sees** is only the final object: `route`, `summary` (the one-line answer or the state's words), `columns`
(each `{name, kind}`), `rows` (numbers as exact decimal strings, dates as ISO 8601), `sql` (the re-printed query that ran: "Show
SQL"), `citations` and `documents` (K-06), `view` (the views it read), `pages` (the page found, or the suggested pages), `ms` and
`state` (`answered`, `page`, `cant_answer`, `not_found`, `paused` or `limit`). Never a raw error, an error class, a run id or a
task name (`CLAUDE.md` §4): those go to the log row or, for the store's own failures, to the server log as a class name.

**The database:** `ops` isn't in the Data API (`CLAUDE.md` §4) and `service_role` can't log in to Postgres, so the deployed route
reaches `ops` through `api.cmdbar_*` functions called as RPC with the secret key (0016's pattern, Anand's option B on #8): the
`Store` protocol here, `RpcCmdbarStore` in `ft.api.cmdbar`. Those functions are migration 0054 `cmdbar_route` (K-05's part 2,
#583; approved by Anand, 17 Nov 2026). Until it reaches a database every store call fails there, and the route answers "paused".
"""
from __future__ import annotations

import hashlib
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from ft.ai.client import AIUnavailable, CallError, Failed
from ft.cmdbar import cache, limits
from ft.cmdbar import router as router_mod
from ft.cmdbar import sql as sql_mod
from ft.cmdbar.log import LogRow

# Step 0: every secret the route needs. Missing any one is "paused" (Spec 07 §3), found with no call of any kind.
SECRETS = ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "SQL_READER_DATABASE_URL", "CMDBAR_SALT", "SUPABASE_URL",
           "SUPABASE_SERVICE_ROLE_KEY")

# Design 01 §4.6, word for word.
PAUSED_TEXT = "AI questions are paused for now; search still works."
CANT_TEXT = "That isn't in this site's data."
NOT_FOUND_TEXT = "Not found in the stored Fed and RBI documents."
UNDERSTANDING, WRITING_SQL, SEARCHING = "Understanding the question…", "Writing SQL…", "Searching documents…"
SUGGESTED_PAGES = ({"href": "/policy", "title": "Fed vs RBI"}, {"href": "/screener", "title": "Screener"},
                   {"href": "/earnings", "title": "Earnings calls"})          # live pages (web/lib/shell/nav.ts)
STATES = ("answered", "page", "cant_answer", "not_found", "paused", "limit")
FINAL_KEYS = ("route", "summary", "columns", "rows", "sql", "citations", "documents", "view", "pages", "ms", "state")
PROMPT_TTL_S = 600.0                    # the views' descriptions are re-read from their COMMENTs every 10 minutes


# ---------------------------------------------------------------------------------------------- the store
class StoreError(Exception):
    """The database didn't answer as expected. The message is for the server log only."""


@dataclass(frozen=True)
class State:
    """What the switch reads: `ops.setting`'s three command-bar values and the AI budget, with month-to-date spend (N8)."""
    ai_enabled: bool
    daily_cap: int
    per_visitor_hourly: int
    monthly_budget_usd: Decimal
    month_spend_usd: Decimal


@dataclass(frozen=True)
class CacheEntry:
    route: str
    answer: dict
    data_version: str


class Store(Protocol):
    def state(self) -> State: ...
    def count(self, bucket: str, window_start: datetime) -> int: ...
    def cache_get(self, sha: bytes) -> CacheEntry | None: ...
    def cache_put(self, sha: bytes, question: str, route: str, answer: dict, data_version: str,
                  expires_at: datetime) -> None: ...
    def cache_hit(self, sha: bytes) -> None: ...
    def log(self, row: LogRow) -> None: ...
    def freshness(self) -> dict[str, datetime]: ...
    def page(self, slug: str) -> dict | None: ...
    def examples(self) -> list[dict]: ...
    def close(self) -> None: ...


@dataclass
class Deps:
    """What an ask may use. Each factory is called only once the step that needs it is reached, so a paused ask builds no
    model client and opens no connection."""
    env: Mapping[str, str]
    store: Callable[[Mapping[str, str]], Store]
    client: Callable[[Mapping[str, str]], object]
    reader: Callable[[Mapping[str, str]], sql_mod.Reader]
    prompt: Callable[[sql_mod.Reader, Mapping[str, str]], object]
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    clock: Callable[[], float] = time.monotonic


def missing_secrets(env: Mapping[str, str]) -> list[str]:
    return [n for n in SECRETS if not (env.get(n) or "").strip()]


# ---------------------------------------------------------------------------------------------- the final object
def final(state: str, *, route: str | None = None, summary: str | None = None, columns=(), rows=(), sql: str | None = None,
          view: str | None = None, pages=(), ms: int = 0) -> dict:
    """The route's final object (Spec 07 §4's answer shape, with `pages` for the page found or the suggestions)."""
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    return {"route": route, "summary": summary, "columns": list(columns), "rows": list(rows), "sql": sql, "citations": [],
            "documents": [], "view": view, "pages": [dict(p) for p in pages], "ms": ms, "state": state}


def _kind(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float, Decimal)):
        return "number"
    if isinstance(v, datetime):
        return "timestamp"
    if isinstance(v, date):
        return "date"
    return "text"


def cell(v):
    """A cell as JSON: numbers as exact decimal strings (the page formats them: web/lib/format.ts), dates as ISO 8601."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, (int, Decimal)):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def table(columns: list[str], rows: list[tuple]) -> tuple[list[dict], list[list]]:
    kinds = []
    for i, name in enumerate(columns):
        kind = next((k for r in rows if (k := _kind(r[i])) is not None), "text")
        kinds.append({"name": name, "kind": kind})
    return kinds, [[cell(v) for v in r] for r in rows]


# ---------------------------------------------------------------------------------------------- the prompt
_prompts: dict[str, tuple[float, object]] = {}


def cached_prompt(reader: sql_mod.Reader, env: Mapping[str, str], *, clock=time.monotonic):
    """The SQL prompt with the views' descriptions read from their COMMENTs through `sql_reader` (K-04's choice), kept for
    PROMPT_TTL_S per database so most asks reuse it (and the API's prompt cache with it)."""
    k = hashlib.sha256((env.get("SQL_READER_DATABASE_URL") or "").encode()).hexdigest()
    got = _prompts.get(k)
    if got and clock() - got[0] < PROMPT_TTL_S:
        return got[1]
    docs, groups = reader.describe()
    prompt = sql_mod.sql_prompt(sql_mod.render_views(docs, groups))
    _prompts[k] = (clock(), prompt)
    return prompt


# ---------------------------------------------------------------------------------------------- the ask
@dataclass
class _Ask:
    deps: Deps
    store: Store
    row: LogRow
    t0: float
    fresh: dict[str, datetime] | None = None

    def ms(self) -> int:
        return int((self.deps.clock() - self.t0) * 1000)

    def freshness(self) -> dict[str, datetime]:
        if self.fresh is None:
            self.fresh = self.store.freshness()
        return self.fresh

    def bill(self, model: str | None, cost: Decimal, tokens_in: int = 0, tokens_out: int = 0) -> None:
        if model:
            self.row.models.append(model)
        self.row.cost_usd += cost
        self.row.input_tokens += tokens_in
        self.row.output_tokens += tokens_out


def _note(what: str, e: BaseException) -> None:
    """The server log: the step and the exception's class only (never its message, which can hold a row or a key)."""
    print(f"cmdbar: {what}: {type(e).__name__}", file=sys.stderr)


def _usage(reply) -> tuple[int, int]:
    u = reply.usage
    return u.input + u.cache_write_5m + u.cache_write_1h, u.output


def ask(question: str, ip: str, user_agent: str, deps: Deps, emit: Callable[[str], None] = lambda line: None) -> dict:
    """One ask, already cleaned (`ft.cmdbar.clean_question`). Returns the final object; never raises."""
    t0 = deps.clock()
    # Step 0: the switch. Secrets first, with no call of any kind.
    if missing_secrets(deps.env):
        return final("paused", summary=PAUSED_TEXT, ms=int((deps.clock() - t0) * 1000))
    try:
        store = deps.store(deps.env)
    except Exception as e:                               # noqa: BLE001 - a store that can't be built is "paused"
        _note("store", e)
        return final("paused", summary=PAUSED_TEXT, ms=int((deps.clock() - t0) * 1000))
    try:
        return _ask(question, ip, user_agent, deps, store, emit, t0)
    finally:
        try:
            store.close()
        except Exception as e:                           # noqa: BLE001
            _note("store close", e)


def _ask(question, ip, user_agent, deps, store, emit, t0) -> dict:
    try:
        st = store.state()
    except Exception as e:                               # noqa: BLE001 - unreadable switch: paused
        _note("state", e)
        return final("paused", summary=PAUSED_TEXT, ms=int((deps.clock() - t0) * 1000))
    if st.ai_enabled is not True or st.month_spend_usd >= st.monthly_budget_usd:
        return final("paused", summary=PAUSED_TEXT, ms=int((deps.clock() - t0) * 1000))

    now = deps.now()
    a = _Ask(deps, store, LogRow(limits.visitor_hash(deps.env["CMDBAR_SALT"], ip, user_agent, now), question), t0)
    try:
        out = _limited(a, question, now, st, emit)
    except Exception as e:                               # noqa: BLE001 - anything unforeseen is "can't answer", logged
        _note("ask", e)
        a.row.error_class = "internal"
        out = final("cant_answer", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms())
    a.row.latency_ms = out["ms"] = a.ms()
    try:
        store.log(a.row)
    except Exception as e:                               # noqa: BLE001 - the answer stands; the server log notes it
        _note("log", e)
    return out


def _limit(a: _Ask, which: str) -> dict:
    a.row.answered, a.row.error_class = False, f"{which}_limit"
    return final("limit", summary=PAUSED_TEXT, ms=a.ms())


def _limited(a: _Ask, question: str, now: datetime, st: State, emit) -> dict:
    store = a.store
    # Step 2: the visitor's hourly count, on every ask.
    try:
        n = store.count(limits.visitor_bucket(a.row.visitor_hash), limits.hour_start(now))
    except Exception as e:                               # noqa: BLE001 - can't count: refuse, never answer uncounted
        _note("count visitor", e)
        a.row.error_class = "store"
        return final("limit", summary=PAUSED_TEXT, ms=a.ms())
    if limits.over(n, st.per_visitor_hourly):
        return _limit(a, "visitor")

    # Step 3: the cache.
    sha = cache.key(question)
    hit = _cached(a, sha)
    if hit is not None:
        return hit

    # Step 2, overall: only a question that reaches the router counts.
    try:
        n = store.count(limits.GLOBAL, limits.ist_day_start(now))
    except Exception as e:                               # noqa: BLE001
        _note("count global", e)
        a.row.error_class = "store"
        return final("limit", summary=PAUSED_TEXT, ms=a.ms())
    if limits.over(n, st.daily_cap):
        return _limit(a, "daily")

    out, keep = _dispatch(a, question, emit)
    if keep:
        _store(a, sha, question, out, now)
    return out


def _cached(a: _Ask, sha: bytes) -> dict | None:
    try:
        entry = a.store.cache_get(sha)
        if entry is None or not isinstance(entry.answer, dict) or entry.answer.get("state") not in STATES:
            return None
        if entry.data_version != cache.data_version(cache.views_read(entry.answer.get("sql")), a.freshness()):
            return None
    except Exception as e:                               # noqa: BLE001 - an unreadable cache is a miss
        _note("cache get", e)
        return None
    try:
        a.store.cache_hit(sha)
    except Exception as e:                               # noqa: BLE001
        _note("cache hit", e)
    out = {k: entry.answer.get(k) for k in FINAL_KEYS}
    out["ms"] = a.ms()
    a.row.route, a.row.cache_hit, a.row.answered = entry.route, True, out["state"] in ("answered", "page")
    a.row.generated_sql = out.get("sql")
    if isinstance(out.get("rows"), list) and out.get("sql"):
        a.row.rows_returned = len(out["rows"])
    return out


def _store(a: _Ask, sha: bytes, question: str, out: dict, now: datetime) -> None:
    try:
        version = cache.data_version(cache.views_read(out.get("sql")), a.freshness())
        a.store.cache_put(sha, question, out["route"], out, version, now + cache.TTL)
    except Exception as e:                               # noqa: BLE001 - not cached; the answer stands
        _note("cache put", e)


def _dispatch(a: _Ask, question: str, emit) -> tuple[dict, bool]:
    """Steps 4 and 5. Returns (the final object, whether it may be cached)."""
    deps, row = a.deps, a.row
    emit(UNDERSTANDING)
    client = deps.client(deps.env)
    try:
        r = router_mod.route(client, question)
    except AIUnavailable:                                # no key or no credit: the AI is off, whatever the switch says
        row.error_class = "ai_unavailable"
        return final("paused", summary=PAUSED_TEXT, ms=a.ms()), False
    except Failed as e:                                  # billed
        a.bill(e.reply.model, e.reply.cost_usd, *_usage(e.reply))
        row.error_class = "model_output"
        return final("cant_answer", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), False
    except CallError:
        row.error_class = "ai_call"
        return final("cant_answer", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), False
    if r.reply is not None:
        a.bill(r.reply.model, r.reply.cost_usd, *_usage(r.reply))
    row.route = r.route

    if r.route == "page":
        if not r.page_slug:
            return final("cant_answer", route="page", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), True
        try:
            page = a.store.page(r.page_slug)
        except Exception as e:                           # noqa: BLE001
            _note("page", e)
            row.error_class = "store"
            return final("cant_answer", route="page", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), False
        if page is None:
            return final("cant_answer", route="page", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), True
        row.answered = True
        return final("page", route="page", pages=[page], ms=a.ms()), True

    if r.route == "document":                            # K-06 builds 5b; until then, nothing is searched
        return final("not_found", route="document", summary=NOT_FOUND_TEXT, ms=a.ms()), False

    if r.route != "data":
        return final("cant_answer", route=r.route, summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), True

    emit(WRITING_SQL)
    try:
        reader = deps.reader(deps.env)
    except Exception as e:                               # noqa: BLE001 - ReaderDown, or anything else connecting
        _note("reader", e)
        row.error_class = "database_unavailable"
        return final("cant_answer", route="data", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), False
    try:
        try:
            prompt = deps.prompt(reader, deps.env)
        except Exception as e:                           # noqa: BLE001
            _note("describe", e)
            row.error_class = "database_unavailable"
            return final("cant_answer", route="data", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), False
        try:
            ans = sql_mod.answer_data(client, reader, question, prompt, stage=emit)
        except AIUnavailable:
            row.error_class = "ai_unavailable"
            return final("paused", summary=PAUSED_TEXT, ms=a.ms()), False
    finally:
        try:
            reader.close()
        except Exception as e:                           # noqa: BLE001
            _note("reader close", e)

    for att in ans.attempts:
        a.bill(att.model, att.cost_usd, att.input_tokens, att.output_tokens)
    if ans.summary_model:
        a.bill(ans.summary_model, ans.summary_cost_usd, ans.summary_input_tokens, ans.summary_output_tokens)
    else:
        row.cost_usd += ans.summary_cost_usd
    row.generated_sql = ans.sql
    row.sql_valid = {"accepted": True, "rejected": False}.get(ans.guard_verdict)
    row.reject_reason = next((t.error_class for t in ans.attempts
                              if t.error_class and t.error_class.startswith(("guard:", "check:"))), None)
    row.error_class = ans.error_class
    if ans.state == "answered":
        row.answered, row.rows_returned = True, len(ans.rows)
        columns, rows = table(ans.columns, ans.rows)
        view = ", ".join(cache.views_read(ans.sql)) or None
        return final("answered", route="data", summary=ans.summary, columns=columns, rows=rows, sql=ans.sql, view=view,
                     ms=a.ms()), True
    if ans.state == "unanswerable":                      # the model said the views can't answer it
        return final("cant_answer", route="data", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), True
    return final("cant_answer", route="data", summary=CANT_TEXT, pages=SUGGESTED_PAGES, ms=a.ms()), False
