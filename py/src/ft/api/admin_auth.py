"""The one gate on every /api/admin/* route (Prompt 01 A3; Review 01 F19; Architecture 02 §1.6).

The route holds the service key and can start workflows, so this check is its only gate:
- the access token's signature and expiry are verified by Supabase Auth itself (`GET /auth/v1/user`), never by
  decoding the token here. Decided by Claude (standing delegation, 24 Oct 2026; issue #8): Supabase Auth rather than
  the project's JWKS, because it needs no new dependency (httpx is already in the api service's base) and it also
  refuses a token whose session was signed out. The JWKS route would need a JWT library in the base dependencies;
- the token is decoded locally only to REFUSE early (no `exp`, or `exp` already past): it never grants anything;
- the role comes from the user record Auth returns, `app_metadata.role == "admin"`, never `user_metadata` (which
  the user can edit) and never the token's own claims;
- every refusal is a bare 401 or 403: no reason is given to the caller.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import time
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException

AUTH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class AuthSettings:
    supabase_url: str
    api_key: str            # the project's secret (service-role) key: Auth wants an apikey header on every call


@dataclass(frozen=True)
class AdminUser:
    id: str
    email: str | None


def auth_settings() -> AuthSettings | None:
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return None                                   # not configured: every admin request is refused
    return AuthSettings(url.rstrip("/"), key)


def auth_http():
    with httpx.Client(timeout=AUTH_TIMEOUT_S, follow_redirects=False) as client:
        yield client


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403)


def _unverified_claims(token: str) -> dict | None:
    """The token's payload, read only so that a malformed or expired token is refused without a network call."""
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return None
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (binascii.Error, ValueError):
        return None
    return claims if isinstance(claims, dict) else None


def verify_admin(token: str, settings: AuthSettings | None, client: httpx.Client, now: float | None = None) -> AdminUser:
    if settings is None:
        raise _unauthorized()
    claims = _unverified_claims(token)
    if claims is None:
        raise _unauthorized()
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= (time.time() if now is None else now):
        raise _unauthorized()
    try:
        resp = client.get(f"{settings.supabase_url}/auth/v1/user",
                          headers={"apikey": settings.api_key, "Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        raise _unauthorized() from None               # fail closed when Auth can't be asked
    if resp.status_code != 200:
        raise _unauthorized()
    try:
        user = resp.json()
    except ValueError:
        raise _unauthorized() from None
    if not isinstance(user, dict) or not user.get("id") or user.get("id") != claims.get("sub"):
        raise _unauthorized()
    app_metadata = user.get("app_metadata")
    if not isinstance(app_metadata, dict) or app_metadata.get("role") != "admin":
        raise _forbidden()
    return AdminUser(id=str(user["id"]), email=user.get("email"))


def require_admin(authorization: Annotated[str | None, Header()] = None,
                  settings: Annotated[AuthSettings | None, Depends(auth_settings)] = None,
                  client: Annotated[httpx.Client | None, Depends(auth_http)] = None) -> AdminUser:
    """FastAPI dependency for every admin route."""
    if not authorization:
        raise _unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    if client is None:
        raise _unauthorized()
    return verify_admin(token.strip(), settings, client)
