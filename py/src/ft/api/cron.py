"""`GET /api/cron/keepalive` (issue #196; Plan 04 §4, survival job S1).

Supabase pauses a Free-plan project that gets too little database activity over seven days. Once a day Vercel calls
this route (`crons` in `vercel.json`), and the route makes one real read through the project's Data API with the
**anon key**, the browser key that is public by design (`CLAUDE.md` §4): `GET /rest/v1/series_latest?select=series_key
&limit=1`, schema `api`. It never uses the service-role key. A twice-daily GitHub workflow (`keepalive-db.yml`) is
the backup that touches the database a different way, so neither a missed cron nor a lapsed Vercel setting can leave
the project idle on its own.

Vercel sends `Authorization: Bearer <CRON_SECRET>` when the project has a `CRON_SECRET` environment variable. The
route refuses anything else with 401 (compared in constant time) and answers 503 when `CRON_SECRET` is unset, so a
deployment without the secret can't be triggered by anyone. The answer is `{"ok": true}` (200) when the Data API
returned 200 and a JSON list, otherwise `{"ok": false}` (503). The upstream body, the URL and the keys are never
returned and never logged: a failure logs the status code or the exception's class name only.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

TIMEOUT_S = 10.0
NO_STORE = {"Cache-Control": "no-store"}          # a cached answer would mean no read happened that day

router = APIRouter(prefix="/api/cron")


@dataclass(frozen=True)
class DataApi:
    url: str            # the project's URL, without a trailing slash
    anon_key: str       # the browser key; the same value as the web service's NEXT_PUBLIC_SUPABASE_ANON_KEY


def data_api_headers(api: DataApi) -> dict[str, str]:
    """Headers for a Data API read with the browser key (the cron read and `/api/health` share them).

    A legacy anon key is a JWT and goes in both `apikey` and `Authorization: Bearer`. Supabase's newer publishable key
    (`sb_publishable_...`) isn't a JWT: it belongs in `apikey` alone, and sent as a Bearer token it is forwarded to the
    database and refused with a 401 (Supabase docs, "Understanding API keys"). Production's health check got a 401 on
    31 Oct 2026 (launch check #87); this removes that cause: the Bearer header goes only with a key that isn't an
    `sb_` key."""
    headers = {"apikey": api.anon_key, "Accept-Profile": "api", "Accept": "application/json"}
    if not api.anon_key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {api.anon_key}"
    return headers


def cron_secret() -> str | None:
    return os.environ.get("CRON_SECRET") or None


def data_api() -> DataApi | None:
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not key:
        return None
    return DataApi(url.rstrip("/"), key)


def data_api_client():
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False) as client:
        yield client


def _bearer_matches(authorization: str | None, secret: str) -> bool:
    """Constant-time comparison of the whole header with `Bearer <secret>` (bytes, so non-ASCII can't raise)."""
    got = (authorization or "").encode("utf-8", "replace")
    return hmac.compare_digest(got, f"Bearer {secret}".encode())


def read_one_row(client: httpx.Client, api: DataApi) -> bool:
    """One real read through the Data API; True only for a 200 carrying a JSON list. Logs no body, URL or key."""
    try:
        resp = client.get(
            f"{api.url}/rest/v1/series_latest", params={"select": "series_key", "limit": 1},
            headers=data_api_headers(api))
    except httpx.HTTPError as e:
        print(f"keepalive: Data API unreachable: {type(e).__name__}")                 # server log only
        return False
    if resp.status_code != 200:
        print(f"keepalive: Data API answered {resp.status_code}")
        return False
    try:
        body = resp.json()
    except ValueError:
        print("keepalive: Data API answer was not JSON")
        return False
    if not isinstance(body, list):
        print("keepalive: Data API answer was not a list")
        return False
    return True


@router.get("/keepalive")
def keepalive(authorization: Annotated[str | None, Header()] = None,
              secret: Annotated[str | None, Depends(cron_secret)] = None,
              api: Annotated[DataApi | None, Depends(data_api)] = None,
              client: Annotated[httpx.Client | None, Depends(data_api_client)] = None):
    if secret is None:
        print("keepalive: CRON_SECRET is not set")
        raise HTTPException(status_code=503)
    if not _bearer_matches(authorization, secret):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    if api is None:
        print("keepalive: SUPABASE_URL or SUPABASE_ANON_KEY is not set")
        return JSONResponse({"ok": False}, status_code=503, headers=NO_STORE)
    if client is None or not read_one_row(client, api):
        return JSONResponse({"ok": False}, status_code=503, headers=NO_STORE)
    return JSONResponse({"ok": True}, headers=NO_STORE)
