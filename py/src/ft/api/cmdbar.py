"""`POST /api/cmdbar/ask` and `GET /api/cmdbar/examples` (Spec 07 §3, §4; Architecture 03 §1.8; K-05, #583).

**Hidden until judged** (Spec 07 §3, N1): the route answers the paused state, with no model, data-query or Voyage call and no log
row, while `ops.setting.cmdbar_ai_enabled` is false, while any secret it needs is missing (`ft.cmdbar.route.SECRETS`: none of
`CMDBAR_SALT`, `VOYAGE_API_KEY` or the bar's others is set in Vercel before go-live), and while the store can't be read. The box
itself renders only behind `site.command_bar` (K-07). The tests pin all of it (`tests/test_cmdbar_route.py`).

**`POST /api/cmdbar/ask`** takes `{"question": "..."}` (at most 500 characters once its whitespace is collapsed; anything else
is a bare 400 `{"state": "invalid"}`) and streams newline-delimited JSON: `{"status": "<Design 01 §4.6's line>"}` for each stage,
then the final object (`ft.cmdbar.route`). The visitor's IP address and user agent are used only to make the daily-rotating hash
(N7) and are never stored or logged. `Cache-Control: no-store`.

**`GET /api/cmdbar/examples`** returns `{"examples": [...]}`: the precomputed example answers marked checked by the admin
(`verified = true`) only, ordered by `display_order` (K-09 fills them). It needs only the store, and costs nothing. A failure is
an empty list.

**The store** (`RpcCmdbarStore`): `ops` isn't in the Data API and `service_role` can't log in to Postgres, so the route calls
`api.cmdbar_*` functions as RPC with the secret key (0016's pattern, Anand's option B on #8: `security definer`, executable by
`service_role` only), and reads `api.task_freshness` and `api.search_index` through the Data API with the same key. The
functions are migration 0054 `cmdbar_route` (K-05's part 2, #583; Anand, 17 Nov 2026). Until they exist every RPC answers 404,
so the switch reads as "paused" and the examples list is empty.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ft.ai.client import Client, Embedder, VoyageHTTP
from ft.cmdbar import BadQuestion, clean_question
from ft.cmdbar import route as route_mod
from ft.cmdbar import sources as sources_mod
from ft.cmdbar import sql as sql_mod
from ft.cmdbar.log import LogRow

NO_STORE = {"Cache-Control": "no-store"}
STORE_TIMEOUT_S = 5.0
CLIENT_TIMEOUT_S, CLIENT_RETRIES, CLIENT_MAX_WAIT_S = 30.0, 1, 5.0      # a visitor waits: short timeouts, one retry
VOYAGE_TIMEOUT_S = 10.0                                                  # one question's embedding (K-06)
READER_CONNECT_TIMEOUT_S = 5
MAX_BODY_CHARS = 4_000
PAGE_COLUMNS = ("item_type", "subtype", "slug", "title", "parent_slug")
# 0054's functions (and 0055's two for the admin page, K-09 part 2, #588): each one's arguments, by name, with their types (the
# SQL store casts to them).
FUNCTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "cmdbar_state": (),
    "cmdbar_count": (("p_bucket", "text"), ("p_window_start", "timestamptz")),
    "cmdbar_cache_get": (("p_question_sha", "text"),),
    "cmdbar_cache_put": (("p_question_sha", "text"), ("p_question", "text"), ("p_route", "text"), ("p_answer", "jsonb"),
                         ("p_data_version", "text"), ("p_expires_at", "timestamptz")),
    "cmdbar_cache_hit": (("p_question_sha", "text"),),
    "cmdbar_log": (("p_row", "jsonb"),),
    "cmdbar_examples": (),
    "cmdbar_examples_admin": (),
    "cmdbar_example_check": (("p_id", "integer"), ("p_verified", "boolean")),
}

router = APIRouter(prefix="/api/cmdbar")


# ---------------------------------------------------------------------------------------------- the store
def _int(v) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise route_mod.StoreError("not an integer")
    return int(v)


def _dec(v) -> Decimal:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise route_mod.StoreError("not a number")
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise route_mod.StoreError("not a number") from None
    if not d.is_finite():
        raise route_mod.StoreError("not a finite number")
    return d


def parse_state(value) -> route_mod.State:
    """`api.cmdbar_state()`'s answer. Anything off its shape raises StoreError, which the route reads as "paused"."""
    if not isinstance(value, dict):
        raise route_mod.StoreError("cmdbar_state returned an unexpected value")
    try:
        return route_mod.State(ai_enabled=value.get("ai_enabled") is True, daily_cap=_int(value.get("daily_cap")),
                               per_visitor_hourly=_int(value.get("per_visitor_hourly")),
                               monthly_budget_usd=_dec(value.get("monthly_budget_usd")),
                               month_spend_usd=_dec(value.get("month_spend_usd")))
    except ValueError:
        raise route_mod.StoreError("cmdbar_state returned an unusable value") from None


def _when(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    t = datetime.fromisoformat(v)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


class FunctionStore:
    """The route's store over 0054's `api.cmdbar_*` functions and the `api.task_freshness` and `api.search_index` views: each
    answer's shape is checked here, so the deployed store (`RpcCmdbarStore`) and the tests' (`SqlCmdbarStore`) read the same
    way. Anything off its shape raises StoreError (the route reads a failed switch as "paused" and a failed cache as a miss)."""

    def _rpc(self, fn: str, args: dict, *, empty_ok: bool = False):
        raise NotImplementedError

    def _freshness_rows(self) -> list:
        raise NotImplementedError

    def _page_rows(self, slug: str) -> list:
        raise NotImplementedError

    def state(self) -> route_mod.State:
        return parse_state(self._rpc("cmdbar_state", {}))

    def count(self, bucket: str, window_start: datetime) -> int:
        n = self._rpc("cmdbar_count", {"p_bucket": bucket, "p_window_start": window_start.astimezone(UTC).isoformat()})
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise route_mod.StoreError("cmdbar_count returned an unexpected value")
        return n

    def cache_get(self, sha: bytes) -> route_mod.CacheEntry | None:
        got = self._rpc("cmdbar_cache_get", {"p_question_sha": sha.hex()})
        if got is None:
            return None
        if not isinstance(got, dict) or not isinstance(got.get("answer"), dict) \
                or not isinstance(got.get("route"), str) or not isinstance(got.get("data_version"), str):
            raise route_mod.StoreError("cmdbar_cache_get returned an unexpected value")
        return route_mod.CacheEntry(got["route"], got["answer"], got["data_version"])

    def cache_put(self, sha: bytes, question: str, route: str, answer: dict, data_version: str,
                  expires_at: datetime) -> None:
        self._rpc("cmdbar_cache_put", {"p_question_sha": sha.hex(), "p_question": question, "p_route": route,
                                       "p_answer": answer, "p_data_version": data_version,
                                       "p_expires_at": expires_at.astimezone(UTC).isoformat()}, empty_ok=True)

    def cache_hit(self, sha: bytes) -> None:
        self._rpc("cmdbar_cache_hit", {"p_question_sha": sha.hex()}, empty_ok=True)

    def log(self, row: LogRow) -> None:
        self._rpc("cmdbar_log", {"p_row": row.as_json()}, empty_ok=True)

    def freshness(self) -> dict[str, datetime]:
        rows = self._freshness_rows()
        if not isinstance(rows, list) or len(rows) >= 1000:            # 1,000 is the Data API's cap: never read short
            raise route_mod.StoreError("task_freshness: unexpected or truncated answer")
        try:
            return {r["task_key"]: _when(r.get("last_success_at")) for r in rows}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise route_mod.StoreError("task_freshness: unusable row") from None

    def page(self, slug: str) -> dict | None:
        rows = self._page_rows(slug)
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise route_mod.StoreError("search_index: unexpected answer")
        if not rows:
            return None
        return {k: rows[0].get(k) for k in PAGE_COLUMNS}

    def examples(self) -> list[dict]:
        rows = self._rpc("cmdbar_examples", {})
        if not isinstance(rows, list):
            raise route_mod.StoreError("cmdbar_examples returned an unexpected value")
        return rows

    def examples_admin(self) -> list[dict]:
        """Every example, checked or not (0055; the admin page only, `ft.api.cmdbar_admin`)."""
        rows = self._rpc("cmdbar_examples_admin", {})
        if not isinstance(rows, list):
            raise route_mod.StoreError("cmdbar_examples_admin returned an unexpected value")
        return rows

    def example_check(self, example_id: int, verified: bool) -> dict | None:
        """Sets one example's `verified` (0055); None for an unknown id."""
        got = self._rpc("cmdbar_example_check", {"p_id": example_id, "p_verified": verified}, empty_ok=True)
        if got is not None and not isinstance(got, dict):
            raise route_mod.StoreError("cmdbar_example_check returned an unexpected value")
        return got

    def close(self) -> None:
        pass


class RpcCmdbarStore(FunctionStore):
    """What the deployed route uses: the functions as RPC (`/rest/v1/rpc/<fn>`, schema `api`) and the two views through the Data
    API, with the secret key."""

    def __init__(self, supabase_url: str, key: str, client: httpx.Client | None = None):
        self.url, self.key = supabase_url.rstrip("/"), key
        self.client = client or httpx.Client(timeout=STORE_TIMEOUT_S, follow_redirects=False)

    def _headers(self) -> dict[str, str]:
        h = {"apikey": self.key, "Content-Profile": "api", "Accept-Profile": "api", "Accept": "application/json"}
        if not self.key.startswith("sb_"):               # a legacy service-role JWT also goes as the bearer token
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def _answer(self, what: str, resp: httpx.Response, *, empty_ok: bool = False):
        if empty_ok and resp.status_code == 204:
            return None
        if resp.status_code != 200:
            raise route_mod.StoreError(f"{what}: Data API answered {resp.status_code}")
        if empty_ok and not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            raise route_mod.StoreError(f"{what}: not JSON") from None

    def _rpc(self, fn: str, args: dict, *, empty_ok: bool = False):
        try:
            resp = self.client.post(f"{self.url}/rest/v1/rpc/{fn}", json=args, headers=self._headers())
        except httpx.HTTPError as e:
            raise route_mod.StoreError(f"{fn}: Data API unreachable: {type(e).__name__}") from None
        return self._answer(fn, resp, empty_ok=empty_ok)

    def _get(self, view: str, params: dict):
        try:
            resp = self.client.get(f"{self.url}/rest/v1/{view}", params=params, headers=self._headers())
        except httpx.HTTPError as e:
            raise route_mod.StoreError(f"{view}: Data API unreachable: {type(e).__name__}") from None
        return self._answer(view, resp)

    def _freshness_rows(self) -> list:
        return self._get("task_freshness", {"select": "task_key,last_success_at", "limit": "1000"})

    def _page_rows(self, slug: str) -> list:
        return self._get("search_index", {"select": ",".join(PAGE_COLUMNS), "slug": f"eq.{slug}", "limit": "1"})

    def close(self) -> None:
        self.client.close()


class SqlCmdbarStore(FunctionStore):
    """The same functions and views over one psycopg connection that holds `service_role` (the tests, against the test database;
    as `ft.api.refresh.SqlRefreshStore`). Each call is its own transaction."""

    def __init__(self, conn):
        self.conn = conn

    def _rpc(self, fn: str, args: dict, *, empty_ok: bool = False):
        if fn not in FUNCTIONS or set(args) != {name for name, _ in FUNCTIONS[fn]}:
            raise route_mod.StoreError(f"{fn}: unknown function or arguments")
        params = ", ".join(f"{name} => %s::{typ}" for name, typ in FUNCTIONS[fn])
        values = [json.dumps(args[name]) if typ == "jsonb" else args[name] for name, typ in FUNCTIONS[fn]]
        import psycopg
        try:
            with self.conn.transaction():
                got = self.conn.execute(f"select api.{fn}({params})::text", values).fetchone()[0]
        except psycopg.Error as e:
            raise route_mod.StoreError(f"{fn}: {type(e).__name__}") from None
        return None if got in (None, "") else json.loads(got)

    def _freshness_rows(self) -> list:
        with self.conn.transaction():
            rows = self.conn.execute("select task_key, last_success_at from api.task_freshness limit 1000").fetchall()
        return [{"task_key": k, "last_success_at": t.isoformat() if t else None} for k, t in rows]

    def _page_rows(self, slug: str) -> list:
        with self.conn.transaction():
            rows = self.conn.execute(f"select {', '.join(PAGE_COLUMNS)} from api.search_index where slug = %s limit 1",
                                     (slug,)).fetchall()
        return [dict(zip(PAGE_COLUMNS, r, strict=True)) for r in rows]


# ---------------------------------------------------------------------------------------------- the live dependencies
def live_store(env: Mapping[str, str]) -> RpcCmdbarStore:
    return RpcCmdbarStore(env["SUPABASE_URL"].strip(), env["SUPABASE_SERVICE_ROLE_KEY"].strip())


def live_client(env: Mapping[str, str]) -> Client:
    return Client(env["ANTHROPIC_API_KEY"].strip(), timeout=CLIENT_TIMEOUT_S, retries=CLIENT_RETRIES,
                  max_wait=CLIENT_MAX_WAIT_S)


def live_embedder(env: Mapping[str, str]) -> Embedder:
    """Voyage for the document route's question (`QUERY_MODEL`, voyage-4), with the model client's short waits (K-06)."""
    return Embedder(client=VoyageHTTP(env["VOYAGE_API_KEY"].strip(), timeout=VOYAGE_TIMEOUT_S), retries=CLIENT_RETRIES,
                    max_wait=CLIENT_MAX_WAIT_S)


def live_reader(env: Mapping[str, str]) -> sql_mod.Reader:
    return sql_mod.Reader.connect(env["SQL_READER_DATABASE_URL"].strip(), connect_timeout=READER_CONNECT_TIMEOUT_S)


def live_deps() -> route_mod.Deps:
    return route_mod.Deps(env=os.environ, store=live_store, client=live_client, reader=live_reader,
                          prompt=route_mod.cached_prompt, embedder=live_embedder)


# ---------------------------------------------------------------------------------------------- the routes
def client_ip(request: Request) -> str:
    """The visitor's address as Vercel's edge reports it (`x-real-ip`, else the first `x-forwarded-for`), only for the hash."""
    ip = (request.headers.get("x-real-ip") or "").strip()
    if not ip:
        ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip and request.client is not None:
        ip = request.client.host or ""
    return ip


def stream(question: str, ip: str, user_agent: str, deps: route_mod.Deps):
    """The ask in a worker thread; each status line as it comes, then the final object, one JSON object per line."""
    box: queue.Queue = queue.Queue()

    def work():
        try:
            out = route_mod.ask(question, ip, user_agent, deps, emit=lambda line: box.put(("status", line)))
        except Exception as e:                           # noqa: BLE001 - ask never raises; if it ever did, the visitor sees no error
            print(f"cmdbar: ask raised {type(e).__name__}", file=sys.stderr)
            out = route_mod.final("cant_answer", summary=route_mod.CANT_TEXT, pages=route_mod.SUGGESTED_PAGES)
        box.put(("final", out))

    threading.Thread(target=work, daemon=True).start()
    while True:
        kind, value = box.get()
        if kind == "status":
            yield json.dumps({"status": value}, ensure_ascii=False) + "\n"
        else:
            yield json.dumps(value, ensure_ascii=False) + "\n"
            return


def _invalid() -> JSONResponse:
    return JSONResponse({"state": "invalid"}, status_code=400, headers=NO_STORE)


@router.post("/ask")
async def ask(request: Request, deps: Annotated[route_mod.Deps, Depends(live_deps)]):
    try:
        raw = await request.body()
        if len(raw) > MAX_BODY_CHARS * 4:
            return _invalid()
        body = json.loads(raw)
        q = body.get("question") if isinstance(body, dict) else None
        if not isinstance(q, str) or len(q) > MAX_BODY_CHARS:
            return _invalid()
        q = clean_question(q)
    except (ValueError, BadQuestion):
        return _invalid()
    return StreamingResponse(stream(q, client_ip(request), request.headers.get("user-agent") or "", deps),
                             media_type="application/x-ndjson", headers=NO_STORE)


def verified_examples(rows) -> list[dict]:
    """The rows marked checked (`verified` true), ordered by `display_order`, with the fields the box shows."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("verified") is not True or not isinstance(r.get("question"), str):
            continue
        order = r.get("display_order")
        out.append({"question": r["question"], "module": r.get("module"),
                    "display_order": order if isinstance(order, int) and not isinstance(order, bool) else None,
                    "answer": sources_mod.fill(dict(r["answer"])) if isinstance(r.get("answer"), dict) else None})
    return sorted(out, key=lambda e: (e["display_order"] is None, e["display_order"] or 0))


@router.get("/examples")
def examples(deps: Annotated[route_mod.Deps, Depends(live_deps)]):
    env = deps.env
    if not (env.get("SUPABASE_URL") or "").strip() or not (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip():
        return JSONResponse({"examples": []}, headers=NO_STORE)
    store = None
    try:
        store = deps.store(env)
        rows = store.examples()
    except Exception as e:                               # noqa: BLE001 - the store's failure is the server log's, as a class name
        print(f"cmdbar: examples: {type(e).__name__}", file=sys.stderr)
        return JSONResponse({"examples": []}, headers=NO_STORE)
    finally:
        if store is not None:
            try:
                store.close()
            except Exception as e:  # noqa: BLE001
                print(f"cmdbar: examples close: {type(e).__name__}", file=sys.stderr)
    return JSONResponse({"examples": verified_examples(rows)}, headers={"Cache-Control": "public, max-age=300"})
