"""The admin gate (Prompt 01 A3; Review 01 F19; Spec 01 §5.1): a tampered token, an expired token and a token whose
`user_metadata.role` is 'admin' are refused; a valid admin token passes; a valid non-admin token is refused.

Supabase Auth is played by a fake that signs and checks its own tokens (HMAC here; the real project signs with ECC)
and answers `GET /auth/v1/user` from its own user records, as the real one does: the route never trusts the token's
claims, only Auth's answer."""
import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from ft.api import admin_auth, refresh
from ft.api.admin_auth import AuthSettings, require_admin
from ft.api.app import app

URL, KEY = "https://example-ref.supabase.co", "sb_secret_test_only"
ADMIN_ID, PLAIN_ID, EDITOR_ID = "11111111-0000-0000-0000-000000000001", "22222222-0000-0000-0000-000000000002", \
    "33333333-0000-0000-0000-000000000003"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeAuth:
    """GoTrue's /auth/v1/user: checks the apikey, the signature and the expiry, then returns the stored user."""

    def __init__(self):
        self.secret = b"fake-signing-key"
        self.users = {
            ADMIN_ID: {"id": ADMIN_ID, "email": "admin@example.com", "app_metadata": {"provider": "email",
                       "role": "admin"}, "user_metadata": {}},
            PLAIN_ID: {"id": PLAIN_ID, "email": "plain@example.com", "app_metadata": {"provider": "email"},
                       "user_metadata": {}},
            # the user edited their own metadata: user_metadata.role = 'admin' must not count
            EDITOR_ID: {"id": EDITOR_ID, "email": "editor@example.com", "app_metadata": {"provider": "email"},
                        "user_metadata": {"role": "admin"}},
        }
        self.calls = 0
        self.down = False

    def token(self, sub: str, exp_in: float = 3600, **extra) -> str:
        user = self.users.get(sub, {})
        claims = {"sub": sub, "exp": int(time.time() + exp_in), "role": "authenticated",
                  "app_metadata": user.get("app_metadata", {}), "user_metadata": user.get("user_metadata", {})}
        claims.update(extra)
        head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body = _b64(json.dumps(claims).encode())
        sig = _b64(hmac.new(self.secret, f"{head}.{body}".encode(), hashlib.sha256).digest())
        return f"{head}.{body}.{sig}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.down:
            raise httpx.ConnectError("down", request=request)
        if request.url != httpx.URL(f"{URL}/auth/v1/user") or request.headers.get("apikey") != KEY:
            return httpx.Response(401, json={"msg": "Invalid API key"})
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            head, body, sig = token.split(".")
        except ValueError:
            return httpx.Response(403, json={"msg": "bad jwt"})
        good = _b64(hmac.new(self.secret, f"{head}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return httpx.Response(403, json={"msg": "invalid JWT: signature is invalid"})
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if claims["exp"] <= time.time():
            return httpx.Response(403, json={"msg": "invalid JWT: token is expired"})
        user = self.users.get(claims["sub"])
        return httpx.Response(200, json=user) if user else httpx.Response(403, json={"msg": "user not found"})


class FakeStore:
    def __init__(self):
        self.touched = 0

    def open_request(self, task_key, cooldown):
        self.touched += 1
        return refresh.Opened(None, "not_found")

    def finish_request(self, request_id, status, github_run_id):
        self.touched += 1

    def request_status(self, request_id):
        self.touched += 1
        return {"request_id": request_id, "status": "dispatched"}


@pytest.fixture
def env():
    auth, store = FakeAuth(), FakeStore()

    def http():
        with httpx.Client(transport=httpx.MockTransport(auth.handler)) as c:
            yield c

    app.dependency_overrides[admin_auth.auth_settings] = lambda: AuthSettings(URL, KEY)
    app.dependency_overrides[admin_auth.auth_http] = http
    app.dependency_overrides[refresh.refresh_store] = lambda: store
    yield auth, store, TestClient(app)
    app.dependency_overrides.clear()


def _get(client, token=None, header=None):
    headers = {"Authorization": header if header is not None else f"Bearer {token}"} if (token or header) else {}
    return client.get("/api/admin/refresh/7", headers=headers)


def test_valid_admin_token_passes(env):
    auth, store, client = env
    r = _get(client, auth.token(ADMIN_ID))
    assert r.status_code == 200 and r.json()["request_id"] == 7
    assert auth.calls == 1 and store.touched == 1


def test_valid_non_admin_token_is_refused(env):
    auth, store, client = env
    r = _get(client, auth.token(PLAIN_ID))
    assert r.status_code == 403 and store.touched == 0


def test_user_metadata_admin_is_refused(env):
    auth, store, client = env
    r = _get(client, auth.token(EDITOR_ID))
    assert r.status_code == 403 and store.touched == 0


def test_tampered_token_is_refused(env):
    auth, store, client = env
    head, _, sig = auth.token(PLAIN_ID).split(".")
    forged = _b64(json.dumps({"sub": PLAIN_ID, "exp": int(time.time() + 3600),
                              "app_metadata": {"role": "admin"}}).encode())
    r = _get(client, f"{head}.{forged}.{sig}")                      # payload swapped, signature kept
    assert r.status_code == 401 and auth.calls == 1 and store.touched == 0
    head, body, sig = auth.token(ADMIN_ID).split(".")
    altered = ("B" if sig[0] == "A" else "A") + sig[1:]  # always differs (sig[:-2] + "AA" didn't, 1 in 1,024)
    r = _get(client, f"{head}.{body}.{altered}")                     # signature altered
    assert r.status_code == 401 and store.touched == 0


def test_expired_token_is_refused_locally_and_by_auth(env):
    auth, store, client = env
    r = _get(client, auth.token(ADMIN_ID, exp_in=-60))
    assert r.status_code == 401 and auth.calls == 0 and store.touched == 0   # refused before asking Auth
    # with the local pre-check passed (a clock behind Auth's), Auth's own expiry check still refuses it
    token = auth.token(ADMIN_ID, exp_in=-60)
    with httpx.Client(transport=httpx.MockTransport(auth.handler)) as c, pytest.raises(Exception) as e:
        admin_auth.verify_admin(token, AuthSettings(URL, KEY), c, now=time.time() - 3600)
    assert e.value.status_code == 401 and auth.calls == 1


def test_token_claims_are_not_trusted(env):
    """The token says admin, but Auth's record no longer does (the role was removed): refused."""
    auth, store, client = env
    token = auth.token(ADMIN_ID)
    auth.users[ADMIN_ID]["app_metadata"] = {"provider": "email"}
    assert _get(client, token).status_code == 403 and store.touched == 0


def test_auth_answering_for_another_user_is_refused(env):
    auth, store, client = env
    token = auth.token(PLAIN_ID)
    auth.users[PLAIN_ID] = dict(auth.users[ADMIN_ID])                 # Auth's record id differs from the token's sub
    assert _get(client, token).status_code == 401 and store.touched == 0


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer ", "Bearer not-a-jwt", "Bearer a.b", "Bearer a.%%%.c"])
def test_missing_or_malformed_header_is_refused(env, header):
    auth, store, client = env
    r = client.get("/api/admin/refresh/7", headers={"Authorization": header} if header else {})
    assert r.status_code == 401 and auth.calls == 0 and store.touched == 0


def test_no_exp_claim_is_refused(env):
    auth, _store, client = env
    token = auth.token(ADMIN_ID)
    head, body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    del claims["exp"]
    r = _get(client, f"{head}.{_b64(json.dumps(claims).encode())}.{sig}")
    assert r.status_code == 401 and auth.calls == 0


def test_auth_down_or_unconfigured_fails_closed(env):
    auth, store, client = env
    auth.down = True
    assert _get(client, auth.token(ADMIN_ID)).status_code == 401
    auth.down = False
    app.dependency_overrides[admin_auth.auth_settings] = lambda: None
    assert _get(client, auth.token(ADMIN_ID)).status_code == 401 and store.touched == 0


def test_refusals_carry_no_detail(env):
    auth, _store, client = env
    bodies = {_get(client, t).text for t in (auth.token(ADMIN_ID, exp_in=-5), "x.y.z")}
    bodies |= {_get(client, header="Basic abc").text}
    assert bodies == {'{"detail":"Unauthorized"}'}
    assert {_get(client, auth.token(t)).text for t in (PLAIN_ID, EDITOR_ID)} == {'{"detail":"Forbidden"}'}


def test_every_admin_route_is_behind_the_gate(env):
    """The gate sits on the admin router itself, and every route on it refuses a request without a token."""
    auth, store, client = env
    assert [d.dependency for d in refresh.router.dependencies] == [require_admin]
    assert refresh.router.routes, "no admin routes registered"
    for r in refresh.router.routes:
        assert r.path.startswith("/api/admin/")
        path = r.path.replace("{request_id}", "1")
        for method in r.methods:
            assert client.request(method, path, json={"task": "policy.calendar"}).status_code == 401, (method, path)
    assert store.touched == 0 and auth.calls == 0


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert admin_auth.auth_settings() is None
    monkeypatch.setenv("SUPABASE_URL", URL + "/")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    assert admin_auth.auth_settings() == AuthSettings(URL, KEY)


def test_deployed_store_is_not_wired_yet():
    """Until issue #8's open question is decided, the deployed refresh routes answer 503 (after the admin check)."""
    probe = TestClient(app)
    app.dependency_overrides[require_admin] = lambda: admin_auth.AdminUser("x", None)
    try:
        assert probe.get("/api/admin/refresh/1").status_code == 503
    finally:
        app.dependency_overrides.clear()

