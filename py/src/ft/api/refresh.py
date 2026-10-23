"""`/api/admin/refresh` and its status route (Prompt 01 A3; Architecture 02 §1.6).

Flow: the admin check (`require_admin`, on the whole router, so nothing below runs for anyone else); the task exists
(404) and is enabled (409, recorded as `rejected_disabled`); no dispatched request for it in the last 45 minutes
(429, `rejected_cooldown`); for an NSE task, its minimum interval since the last dispatched request or task run (429,
`rejected_nse`; module 1 has none); the request recorded; `refresh.yml` dispatched; the run id stored. The run links
its job run back to the request itself (`ft.jobs.runner`, `--request-id`). A failed dispatch is recorded as `failed`
and doesn't start the cooldown. Visitor refresh isn't built (Spec 01 D10).

The checks and the insert happen in one transaction under a per-task advisory lock (`PgRefreshStore`), so two
clicks can't both pass the cooldown.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ft.api import dispatch as gh
from ft.api.admin_auth import require_admin

COOLDOWN = timedelta(minutes=45)
RUN_URL = f"https://github.com/{gh.REPO}/actions/runs/{{}}"


@dataclass(frozen=True)
class Opened:
    request_id: int | None          # None only when the task doesn't exist (nothing can be recorded)
    status: str                     # 'dispatched' (provisionally, until the dispatch answers) or a rejected_* status


class RefreshStore(Protocol):
    def open_request(self, task_key: str, cooldown: timedelta) -> Opened: ...
    def finish_request(self, request_id: int, status: str, github_run_id: int | None) -> None: ...
    def request_status(self, request_id: int) -> dict | None: ...


class PgRefreshStore:
    """`ops.refresh_request` through one psycopg connection (autocommit off: one transaction per call)."""

    def __init__(self, conn):
        self.conn = conn

    def open_request(self, task_key: str, cooldown: timedelta) -> Opened:
        with self.conn.transaction():
            # serialises requests for one task, so the cooldown check and the insert can't interleave
            self.conn.execute("select pg_advisory_xact_lock(hashtextextended('ops.refresh_request:' || %s, 0))",
                              (task_key,))
            task = self.conn.execute("select enabled, uses_nse, min_interval from ops.task where key = %s",
                                     (task_key,)).fetchone()
            if task is None:
                return Opened(None, "not_found")
            enabled, uses_nse, min_interval = task
            if not enabled:
                status = "rejected_disabled"
            elif self.conn.execute(
                    "select 1 from ops.refresh_request where task_key = %s and status = 'dispatched'"
                    " and requested_at > now() - %s limit 1", (task_key, cooldown)).fetchone():
                status = "rejected_cooldown"
            elif uses_nse and self.conn.execute(
                    "select 1 where exists (select 1 from ops.refresh_request where task_key = %(k)s"
                    "   and status = 'dispatched' and requested_at > now() - %(i)s)"
                    " or exists (select 1 from ops.task_run where task_key = %(k)s"
                    "   and status not in ('skipped_not_due', 'skipped_locked') and started_at > now() - %(i)s)",
                    {"k": task_key, "i": min_interval}).fetchone():
                status = "rejected_nse"
            else:
                status = "dispatched"
            rid = self.conn.execute(
                "insert into ops.refresh_request (task_key, requested_by, status) values (%s, 'admin', %s)"
                " returning id", (task_key, status)).fetchone()[0]
        return Opened(rid, status)

    def finish_request(self, request_id: int, status: str, github_run_id: int | None) -> None:
        with self.conn.transaction():
            self.conn.execute("update ops.refresh_request set status = %s, github_run_id = %s where id = %s",
                              (status, github_run_id, request_id))

    def request_status(self, request_id: int) -> dict | None:
        with self.conn.transaction():
            row = self.conn.execute(
                "select r.id, r.task_key, r.requested_at, r.status, r.github_run_id, r.job_run_id,"
                "       j.status, j.started_at, j.finished_at"
                "  from ops.refresh_request r left join ops.job_run j on j.id = r.job_run_id"
                " where r.id = %s and r.requested_by = 'admin'", (request_id,)).fetchone()
        if row is None:
            return None
        rid, task, requested_at, status, run_id, job_run_id, job_status, started, finished = row
        return {"request_id": rid, "task": task, "requested_at": requested_at.isoformat(), "status": status,
                "github_run_id": run_id, "run_url": RUN_URL.format(run_id) if run_id else None,
                "job_run": None if job_run_id is None else {
                    "id": job_run_id, "status": job_status,
                    "started_at": started.isoformat() if started else None,
                    "finished_at": finished.isoformat() if finished else None}}


def refresh_store() -> RefreshStore:
    """How the deployed route reaches `ops` is the open question on issue #8 (the Data API exposes `api` only, and
    `service_role` can't log in to Postgres). Until Anand decides, the deployed route answers 503."""
    raise HTTPException(status_code=503)


class RefreshBody(BaseModel):
    task: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")


REJECTED = {"rejected_disabled": 409, "rejected_cooldown": 429, "rejected_nse": 429}

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.post("/refresh")
def start_refresh(body: RefreshBody, store: Annotated[RefreshStore, Depends(refresh_store)],
                  client: Annotated[httpx.Client, Depends(gh.github_client)],
                  token: Annotated[str | None, Depends(gh.dispatch_token)],
                  target: Annotated[str, Depends(gh.deployment_target)]):
    opened = store.open_request(body.task, COOLDOWN)
    if opened.request_id is None:
        raise HTTPException(status_code=404)
    if opened.status in REJECTED:
        return JSONResponse({"request_id": opened.request_id, "status": opened.status},
                            status_code=REJECTED[opened.status])
    try:
        run_id = gh.dispatch_refresh(client, token, body.task, opened.request_id, target)
    except gh.DispatchError as e:
        print(f"refresh request {opened.request_id}: dispatch failed: {e}")      # server log only
        store.finish_request(opened.request_id, "failed", None)
        return JSONResponse({"request_id": opened.request_id, "status": "failed"}, status_code=502)
    store.finish_request(opened.request_id, "dispatched", run_id)
    return JSONResponse({"request_id": opened.request_id, "status": "dispatched", "github_run_id": run_id,
                         "run_url": RUN_URL.format(run_id) if run_id else None, "target": target},
                        status_code=202)


@router.get("/refresh/{request_id}")
def refresh_status(request_id: int, store: Annotated[RefreshStore, Depends(refresh_store)]):
    status = store.request_status(request_id)
    if status is None:
        raise HTTPException(status_code=404)
    return status
