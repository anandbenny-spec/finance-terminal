"""`GET /api/admin/cmdbar/examples` and `POST /api/admin/cmdbar/examples/{id}/check` (Spec 07 §4 Examples, §9 step 4; Architecture
03 §1.8; K-09 part 2, #588): the admin page `/admin/cmdbar` lists every example chip with its stored answer, checked or not, and
marks each one checked (or takes the mark off). Only a checked chip reaches the box (`api.cmdbar_examples`, 0054).

- **The gate:** `require_admin` on the whole router (signature, expiry, `app_metadata.role = 'admin'`), as every `/api/admin/*`
  route. Every refusal is a bare status; nothing here asks a model, runs a data query or starts a run: the answers are the
  `cmdbar.examples` task's (K-09 part 1).
- **The store:** the command bar's own (`ft.api.cmdbar.live_store`): migration 0055's `api.cmdbar_examples_admin()` and
  `api.cmdbar_example_check(p_id, p_verified)` as RPC with the secret key (0016's and 0054's pattern; Anand's option A on #588,
  17 Nov 2026). Until 0055 is on the project the Data API answers 404 and these routes answer 503.
- **Marking an answer checked** needs the `updated_at` the page showed: if the task has rewritten the answer since (which also
  unchecks it), the route answers 409 `{"state": "changed"}` and the page reads the list again, so the admin never checks an
  answer they didn't see (decided by Claude under the standing delegation, 17 Nov 2026, #588: it only tightens; the alternative
  was to trust the click). An example whose stored answer isn't an answer (`state` other than `answered`, or none) can't be
  checked: 409 `{"state": "not_answered"}` (0055 refuses it too). Taking the mark off is always allowed.
- `Cache-Control: no-store`. A failing store is a bare 503; the server log gets the failure's class only.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from ft.api import cmdbar as cmdbar_api
from ft.api.admin_auth import require_admin
from ft.cmdbar import route as route_mod
from ft.cmdbar import sources as sources_mod

NO_STORE = {"Cache-Control": "no-store"}
MAX_BODY_CHARS = 500
MAX_ID = 2_147_483_647                                   # ops.cmdbar_example.id is an integer


def examples_store() -> Iterator[cmdbar_api.FunctionStore]:
    """The deployed store, or a bare 503 when the project isn't configured."""
    env = os.environ
    if not (env.get("SUPABASE_URL") or "").strip() or not (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip():
        raise HTTPException(status_code=503)
    store = cmdbar_api.live_store(env)
    try:
        yield store
    finally:
        try:
            store.close()
        except Exception as e:  # noqa: BLE001
            print(f"cmdbar admin: close: {type(e).__name__}", file=sys.stderr)


def _when(v) -> datetime | None:
    try:
        return cmdbar_api._when(v)
    except (TypeError, ValueError):
        return None


def admin_rows(rows) -> list[dict]:
    """The rows the page shows, each with its fields checked: an id and a question, or the row is left out."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or isinstance(r.get("id"), bool) or not isinstance(r.get("id"), int) \
                or not isinstance(r.get("question"), str):
            continue
        order = r.get("display_order")
        out.append({"id": r["id"], "question": r["question"],
                    "module": r.get("module") if isinstance(r.get("module"), str) else None,
                    "display_order": order if isinstance(order, int) and not isinstance(order, bool) else None,
                    "answer": sources_mod.fill(dict(r["answer"])) if isinstance(r.get("answer"), dict) else None,
                    "data_version": r.get("data_version") if isinstance(r.get("data_version"), str) else None,
                    "verified": r.get("verified") is True,
                    "updated_at": r.get("updated_at") if isinstance(r.get("updated_at"), str) else None})
    return sorted(out, key=lambda e: (e["display_order"] is None, e["display_order"] or 0, e["id"]))


def answered(answer: dict | None) -> bool:
    return isinstance(answer, dict) and answer.get("state") == "answered"


def _read(store: cmdbar_api.FunctionStore) -> list[dict]:
    try:
        return admin_rows(store.examples_admin())
    except route_mod.StoreError as e:
        print(f"cmdbar admin: examples: {e}", file=sys.stderr)              # the function and status only, no row
        raise HTTPException(status_code=503) from None


def _conflict(state: str) -> JSONResponse:
    return JSONResponse({"state": state}, status_code=409, headers=NO_STORE)


router = APIRouter(prefix="/api/admin/cmdbar", dependencies=[Depends(require_admin)])


@router.get("/examples")
def list_examples(store: Annotated[cmdbar_api.FunctionStore, Depends(examples_store)]):
    return JSONResponse({"examples": _read(store)}, headers=NO_STORE)


@router.post("/examples/{example_id}/check")
async def check_example(example_id: Annotated[int, Path(ge=1, le=MAX_ID)], request: Request,
                        store: Annotated[cmdbar_api.FunctionStore, Depends(examples_store)]):
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        body = json.loads(raw) if len(raw) <= MAX_BODY_CHARS else None
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("verified"), bool) \
            or not (body.get("updated_at") is None or isinstance(body.get("updated_at"), str)):
        raise HTTPException(status_code=400)
    verified: bool = body["verified"]
    row = next((r for r in _read(store) if r["id"] == example_id), None)
    if row is None:
        raise HTTPException(status_code=404)
    if verified:
        seen = _when(body.get("updated_at"))
        if seen is None:
            raise HTTPException(status_code=400)
        if _when(row["updated_at"]) != seen:
            return _conflict("changed")
        if not answered(row["answer"]):
            return _conflict("not_answered")
    try:
        got = store.example_check(example_id, verified)
    except route_mod.StoreError as e:
        print(f"cmdbar admin: check: {e}", file=sys.stderr)
        raise HTTPException(status_code=503) from None
    if got is None:
        raise HTTPException(status_code=404)
    return JSONResponse({"id": example_id, "verified": got.get("verified") is True,
                         "updated_at": got.get("updated_at") if isinstance(got.get("updated_at"), str) else None},
                        headers=NO_STORE)
