"""`GET /api/health` (issue #198; Plan 04 §4, survival job S3): the one URL an outside uptime monitor checks.

It makes one real read through the Data API with the **anon key** (the same helper as the keep-alive route, `ft.api.cron`):
`api.task_freshness` for the daily data load's first and last tasks, `policy.market_series` and `site.quality_summary`
(a run that hangs midway never reaches the last one: #364). It answers 200 `{"status": "ok"}` when the read works and
both tasks' newest success is under 36 hours old, otherwise 503 `{"status": "degraded"}`. Nothing else is
in the body (no task names, run ids, timestamps or error text: `CLAUDE.md` §4); a failure logs the status code or the
exception's class name only. `Cache-Control: no-store`, so a monitor always gets a fresh answer.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ft.api.cron import DataApi, data_api, data_api_client, data_api_headers

# The daily's first task and its last: a run that dies or hangs midway keeps the first one fresh, never the last.
DAILY_TASKS = ("policy.market_series", "site.quality_summary")
MAX_AGE = timedelta(hours=36)
NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter(prefix="/api")


def newest_success(client: httpx.Client, api: DataApi) -> datetime | None:
    """The OLDEST of the daily tasks' last successes (so one stale task is enough to be degraded), from one Data API
    read; None when the read fails, a task has no row, or any time is unusable."""
    try:
        resp = client.get(
            f"{api.url}/rest/v1/task_freshness",
            params={"select": "task_key,last_success_at", "task_key": f"in.({','.join(DAILY_TASKS)})",
                    "limit": len(DAILY_TASKS)},
            headers=data_api_headers(api))
    except httpx.HTTPError as e:
        print(f"health: Data API unreachable: {type(e).__name__}")
        return None
    if resp.status_code != 200:
        print(f"health: Data API answered {resp.status_code}")
        return None
    try:
        times = {row["task_key"]: datetime.fromisoformat(row["last_success_at"]) for row in resp.json()}
        oldest = min(times[key] if times[key].tzinfo else times[key].replace(tzinfo=UTC) for key in DAILY_TASKS)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        print("health: no usable last success in the Data API answer")
        return None
    return oldest


def now() -> datetime:
    return datetime.now(UTC)


@router.get("/health")
def health(api: Annotated[DataApi | None, Depends(data_api)] = None,
           client: Annotated[httpx.Client | None, Depends(data_api_client)] = None):
    if api is None or client is None:
        print("health: SUPABASE_URL or SUPABASE_ANON_KEY is not set")
        return JSONResponse({"status": "degraded"}, status_code=503, headers=NO_STORE)
    at = newest_success(client, api)
    if at is None or now() - at > MAX_AGE:
        return JSONResponse({"status": "degraded"}, status_code=503, headers=NO_STORE)
    return JSONResponse({"status": "ok"}, headers=NO_STORE)
