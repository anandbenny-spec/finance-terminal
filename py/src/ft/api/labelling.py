"""`GET /api/admin/labelling/items` (Spec 02B §5.8, §3.2; job 2B-12, #175): the passages of the tone gate's set `earnings_tone`, for
`/admin/labelling`. Transcript text is private (a visitor, and the admin's own browser session, can't read it), so the text comes
from here, behind the admin gate (`require_admin` on the whole router: signature, expiry, `app_metadata.role = 'admin'`).

- The set's items are chunk ids only (`config/eval/sets/earnings_tone_items.json`); the text is read from the database by the
  store below, never kept in the repo. The reply holds each passage's id, section and text and **nothing that could colour a
  label**: no company, quarter, results figure or model output (the labelling is blind).
- The token is never logged, and neither is a passage. An unknown set is a 404; a set with no items yet is an empty list; a store
  that fails is a bare 503 (the message goes to the server log, which names no text).
- `Cache-Control: no-store`.
- The store calls the Data API function `api.admin_labelling_passages(p_items jsonb)` with the secret key, as `RpcRefreshStore` calls
  0016's. **That function is not in any migration yet**: a migration the card doesn't name needs Anand's OK (`CLAUDE.md` §7),
  so until it exists the Data API answers 404 and this route answers 503. The proposed function is on issue #175.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ft.api.admin_auth import AuthSettings, auth_http, auth_settings, require_admin

SET = "earnings_tone"
ITEMS_PATH = Path(__file__).resolve().parents[3] / "config" / "eval" / "sets" / "earnings_tone_items.json"
MAX_ITEMS = 60


class StoreError(Exception):
    """The database didn't answer as expected. The message is for the server log only (no text in it)."""


class PassageStore(Protocol):
    def passages(self, refs: list[tuple[int, int]]) -> dict[tuple[int, int], str]: ...


class RpcPassageStore:
    def __init__(self, settings: AuthSettings, client: httpx.Client):
        self.settings, self.client = settings, client

    def passages(self, refs: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
        body = {"p_items": [{"document_id": d, "chunk_no": n} for d, n in refs]}
        try:
            resp = self.client.post(
                f"{self.settings.supabase_url}/rest/v1/rpc/admin_labelling_passages", json=body,
                headers={"apikey": self.settings.api_key, "Authorization": f"Bearer {self.settings.api_key}",
                         "Content-Profile": "api", "Accept-Profile": "api", "Accept": "application/json"})
        except httpx.HTTPError as e:
            raise StoreError(f"Data API unreachable: {type(e).__name__}") from None
        if resp.status_code != 200:
            raise StoreError(f"admin_labelling_passages: Data API answered {resp.status_code}")
        try:
            rows = resp.json()
            return {(int(r["document_id"]), int(r["chunk_no"])): str(r["text"]) for r in rows}
        except (ValueError, KeyError, TypeError):
            raise StoreError("admin_labelling_passages: not the expected rows") from None


def passage_store(settings: Annotated[AuthSettings | None, Depends(auth_settings)],
                  client: Annotated[httpx.Client, Depends(auth_http)]) -> PassageStore:
    if settings is None:
        raise HTTPException(status_code=503)
    return RpcPassageStore(settings, client)


def set_items(path: Path | None = None) -> list[dict]:
    path = path or ITEMS_PATH
    doc = json.loads(path.read_text(encoding="utf-8"))
    items = doc.get("items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError(f"{path.name}: items must be a list of at most {MAX_ITEMS}")
    return items


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.get("/labelling/items")
def labelling_items(store: Annotated[PassageStore, Depends(passage_store)],
                    set: Annotated[str, Query(max_length=40)] = SET):
    if set != SET:
        raise HTTPException(status_code=404)
    try:
        items = set_items()
    except (OSError, ValueError):
        raise HTTPException(status_code=503) from None
    refs = [(int(i["document_id"]), int(i["chunk_no"])) for i in items]
    try:
        text = store.passages(refs) if refs else {}
    except StoreError as e:
        print(f"labelling items: {e}")                                      # server log only
        raise HTTPException(status_code=503) from None
    out = [{"id": i["id"], "section": i["section"], "text": text[(int(i["document_id"]), int(i["chunk_no"]))]}
           for i in items if (int(i["document_id"]), int(i["chunk_no"])) in text]
    return JSONResponse({"set": SET, "count": len(items), "items": out}, headers={"Cache-Control": "no-store"})
