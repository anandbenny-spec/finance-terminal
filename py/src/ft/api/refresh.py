"""`/api/admin/refresh` and its status route (Prompt 01 A3; Architecture 02 §1.6).

Flow: the admin check (`require_admin`, on the whole router, so nothing below runs for anyone else); the task exists
(404) and is enabled (409, recorded as `rejected_disabled`); no dispatched request for it in the last 45 minutes
(429, `rejected_cooldown`); for an NSE task, its minimum interval since the last dispatched request or task run (429,
`rejected_nse`; module 1 has none); the request recorded; `refresh.yml` dispatched; the run id stored. The run links
its job run back to the request itself (`ft.jobs.runner`, `--request-id`). A failed dispatch is recorded as `failed`
and doesn't start the cooldown. Visitor refresh isn't built (Spec 01 D10).

How the route reaches `ops` (Anand, 24 Oct 2026, option B on issue #8; migration 0016): the checks and the insert
are `api.admin_open_refresh`, one call under a per-task advisory lock (so two clicks can't both pass the cooldown),
with `api.admin_finish_refresh` and `api.admin_refresh_status`. They're `security definer` functions only
`service_role` can execute, called as RPC through the Data API with the secret key, which keeps the Data API
`api`-only. `SqlRefreshStore` calls the same functions over a Postgres connection (the tests, as `service_role`).

`GET /api/admin/health` (Prompt 01 C6; Design 01 §4.7) is on the same router, so the same gate guards it: it returns
`api.admin_health()` (migration 0017, the same pattern), the tasks' health, the open alerts oldest first and the recent
runs, for the admin's health page. It is never cached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ft.api import dispatch as gh
from ft.api.admin_auth import AuthSettings, auth_http, auth_settings, require_admin

RUN_URL = f"https://github.com/{gh.REPO}/actions/runs/{{}}"
STATUSES = {"dispatched", "rejected_disabled", "rejected_cooldown", "rejected_nse", "not_found"}


@dataclass(frozen=True)
class Opened:
    request_id: int | None          # None only when the task doesn't exist (nothing is recorded)
    status: str                     # 'dispatched' (provisionally, until the dispatch answers) or a rejected_* status


class StoreError(Exception):
    """The database didn't answer as expected. The message is for the server log only."""


class RefreshStore(Protocol):
    def open_request(self, task_key: str) -> Opened: ...
    def finish_request(self, request_id: int, status: str, github_run_id: int | None) -> None: ...
    def request_status(self, request_id: int) -> dict | None: ...
    def health(self) -> dict: ...


def _opened(value) -> Opened:
    if not isinstance(value, dict) or value.get("status") not in STATUSES:
        raise StoreError("admin_open_refresh returned an unexpected value")
    rid = value.get("request_id")
    if value["status"] == "not_found":
        return Opened(None, "not_found")
    if not isinstance(rid, int) or isinstance(rid, bool):
        raise StoreError("admin_open_refresh returned no request id")
    return Opened(rid, value["status"])


def _status(value) -> dict | None:
    """The status route's body: the function's row plus the run's link."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StoreError("admin_refresh_status returned an unexpected value")
    run_id = value.get("github_run_id")
    return {"request_id": value.get("request_id"), "task": value.get("task"),
            "requested_at": value.get("requested_at"), "status": value.get("status"), "github_run_id": run_id,
            "run_url": RUN_URL.format(run_id) if run_id else None, "job_run": value.get("job_run")}


def _health(value) -> dict:
    """The health page's body, exactly as `api.admin_health()` returns it, once its shape is checked."""
    if not isinstance(value, dict) or not all(isinstance(value.get(k), list) for k in ("tasks", "alerts", "runs")):
        raise StoreError("admin_health returned an unexpected value")
    return value


class SqlRefreshStore:
    """The 0016 functions over one psycopg connection that holds `service_role` (the tests)."""

    def __init__(self, conn):
        self.conn = conn

    def _call(self, sql: str, args: tuple):
        with self.conn.transaction():
            return self.conn.execute(sql, args).fetchone()[0]

    def open_request(self, task_key: str) -> Opened:
        return _opened(self._call("select api.admin_open_refresh(%s)", (task_key,)))

    def finish_request(self, request_id: int, status: str, github_run_id: int | None) -> None:
        if not self._call("select api.admin_finish_refresh(%s, %s, %s)", (request_id, status, github_run_id)):
            raise StoreError(f"request {request_id} could not be finished")

    def request_status(self, request_id: int) -> dict | None:
        return _status(self._call("select api.admin_refresh_status(%s)", (request_id,)))

    def health(self) -> dict:
        return _health(self._call("select api.admin_health()", ()))


class RpcRefreshStore:
    """The 0016 functions as RPC through the Data API (`/rest/v1/rpc/<fn>`, schema `api`) with the secret key:
    what the deployed route uses."""

    def __init__(self, settings: AuthSettings, client: httpx.Client):
        self.settings, self.client = settings, client

    def _rpc(self, fn: str, args: dict):
        try:
            resp = self.client.post(
                f"{self.settings.supabase_url}/rest/v1/rpc/{fn}", json=args,
                headers={"apikey": self.settings.api_key, "Authorization": f"Bearer {self.settings.api_key}",
                         "Content-Profile": "api", "Accept-Profile": "api", "Accept": "application/json"})
        except httpx.HTTPError as e:
            raise StoreError(f"{fn}: Data API unreachable: {type(e).__name__}") from None
        if resp.status_code != 200:
            raise StoreError(f"{fn}: Data API answered {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise StoreError(f"{fn}: not JSON") from None

    def open_request(self, task_key: str) -> Opened:
        return _opened(self._rpc("admin_open_refresh", {"p_task_key": task_key}))

    def finish_request(self, request_id: int, status: str, github_run_id: int | None) -> None:
        if self._rpc("admin_finish_refresh", {"p_request_id": request_id, "p_status": status,
                                              "p_github_run_id": github_run_id}) is not True:
            raise StoreError(f"request {request_id} could not be finished")

    def request_status(self, request_id: int) -> dict | None:
        return _status(self._rpc("admin_refresh_status", {"p_request_id": request_id}))

    def health(self) -> dict:
        return _health(self._rpc("admin_health", {}))


def refresh_store(settings: Annotated[AuthSettings | None, Depends(auth_settings)],
                  client: Annotated[httpx.Client, Depends(auth_http)]) -> RefreshStore:
    if settings is None:
        raise HTTPException(status_code=503)        # not configured (require_admin has refused already anyway)
    return RpcRefreshStore(settings, client)


class RefreshBody(BaseModel):
    task: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")


REJECTED = {"rejected_disabled": 409, "rejected_cooldown": 429, "rejected_nse": 429}

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _unavailable(e: StoreError) -> HTTPException:
    print(f"refresh store: {e}")                                              # server log only
    return HTTPException(status_code=503)


@router.post("/refresh")
def start_refresh(body: RefreshBody, store: Annotated[RefreshStore, Depends(refresh_store)],
                  client: Annotated[httpx.Client, Depends(gh.github_client)],
                  token: Annotated[str | None, Depends(gh.dispatch_token)],
                  target: Annotated[str, Depends(gh.deployment_target)]):
    try:
        opened = store.open_request(body.task)
    except StoreError as e:
        raise _unavailable(e) from None
    if opened.request_id is None:
        raise HTTPException(status_code=404)
    if opened.status in REJECTED:
        return JSONResponse({"request_id": opened.request_id, "status": opened.status},
                            status_code=REJECTED[opened.status])
    try:
        run_id = gh.dispatch_refresh(client, token, body.task, opened.request_id, target)
    except gh.DispatchError as e:
        print(f"refresh request {opened.request_id}: dispatch failed: {e}")      # server log only
        try:
            store.finish_request(opened.request_id, "failed", None)
        except StoreError as e2:
            print(f"refresh request {opened.request_id}: not recorded as failed: {e2}")
        return JSONResponse({"request_id": opened.request_id, "status": "failed"}, status_code=502)
    try:
        store.finish_request(opened.request_id, "dispatched", run_id)
    except StoreError as e:            # the run has started: say so, and leave the request as dispatched
        print(f"refresh request {opened.request_id}: run id {run_id} not recorded: {e}")
    return JSONResponse({"request_id": opened.request_id, "status": "dispatched", "github_run_id": run_id,
                         "run_url": RUN_URL.format(run_id) if run_id else None, "target": target},
                        status_code=202)


@router.get("/refresh/{request_id}")
def refresh_status(request_id: Annotated[int, Path(ge=1, le=2**63 - 1)], store: Annotated[RefreshStore, Depends(refresh_store)]):
    try:
        status = store.request_status(request_id)
    except StoreError as e:
        raise _unavailable(e) from None
    if status is None:
        raise HTTPException(status_code=404)
    return status


@router.get("/health")
def health(store: Annotated[RefreshStore, Depends(refresh_store)]):
    try:
        body = store.health()
    except StoreError as e:
        raise _unavailable(e) from None
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
