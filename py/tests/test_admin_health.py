"""`/api/admin/health` (Prompt 01 C6; Design 01 §4.7): the health page's read through the admin gate, the Data API
RPC the deployed route makes, and migration 0017's `api.admin_health()` against the test database as `service_role`."""
import json

import httpx
import psycopg
import pytest
from conftest import dsn
from fastapi.testclient import TestClient

from ft.api import refresh
from ft.api.admin_auth import AdminUser, require_admin
from ft.api.app import app

BODY = {"as_of": "2026-10-24T05:00:00+00:00", "open_alert_count": 1,
        "tasks": [{"key": "policy.calendar", "last_status": "success", "refresh_available_at": None}],
        "alerts": [{"id": 3, "task_key": "policy.rbi_documents", "severity": "warn", "message": "m"}],
        "runs": [{"id": 9, "workflow": "daily", "status": "success", "tasks": []}]}


class Store:
    def __init__(self, body=BODY, error=None):
        self.body, self.error, self.calls = body, error, 0

    def health(self):
        self.calls += 1
        if self.error:
            raise refresh.StoreError(self.error)
        return refresh._health(self.body)


@pytest.fixture
def client():
    def build(store):
        app.dependency_overrides.update({require_admin: lambda: AdminUser("admin-id", None),
                                         refresh.refresh_store: lambda: store})
        return TestClient(app)
    yield build
    app.dependency_overrides.clear()


def test_health_returns_the_functions_answer_uncached(client):
    r = client(Store()).get("/api/admin/health")
    assert r.status_code == 200
    assert r.json() == BODY
    assert r.headers["cache-control"] == "no-store"


def test_health_store_down_is_a_bare_503(client, capsys):
    r = client(Store(error="admin_health: Data API answered 500")).get("/api/admin/health")
    assert r.status_code == 503 and r.json() == {"detail": "Service Unavailable"}
    assert "Data API answered 500" in capsys.readouterr().out          # server log only


@pytest.mark.parametrize("answer", [None, [], {"tasks": [], "alerts": []}, {"tasks": {}, "alerts": [], "runs": []}])
def test_health_refuses_an_unexpected_answer(client, answer):
    assert client(Store(body=answer)).get("/api/admin/health").status_code == 503


def test_health_is_behind_the_gate():
    """Without the override, the real gate refuses a request with no token before the store is touched."""
    store = Store()
    app.dependency_overrides[refresh.refresh_store] = lambda: store
    try:
        assert TestClient(app).get("/api/admin/health").status_code == 401
        assert store.calls == 0
    finally:
        app.dependency_overrides.clear()
    assert any(r.path == "/api/admin/health" for r in refresh.router.routes)


def test_rpc_store_calls_admin_health_with_the_secret_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps(BODY).encode(), headers={"content-type": "application/json"})
    store = refresh.RpcRefreshStore(refresh.AuthSettings("https://proj.supabase.co", "sb_secret_test_only"),
                                    httpx.Client(transport=httpx.MockTransport(handler)))
    assert store.health() == BODY
    (req,) = seen
    assert req.method == "POST" and str(req.url) == "https://proj.supabase.co/rest/v1/rpc/admin_health"
    assert json.loads(req.content) == {}
    assert req.headers["apikey"] == "sb_secret_test_only" and req.headers["authorization"] == "Bearer sb_secret_test_only"
    assert req.headers["content-profile"] == "api" and req.headers["accept-profile"] == "api"


# ---------------------------------------------------------------- 0017's function against the test database

ADMIN = dsn("postgres")


@pytest.fixture
def seeded():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values ('test.c6_health', 'test', 'C6 test task', '{daily}', '20 hours', '2 days', true)
                     on conflict (key) do nothing""")
        c.execute("""insert into ops.alert (task_key, severity, message, dedupe_key)
                     values ('test.c6_health', 'warn', 'C6 test alert', 'c6:health')""")
        c.execute("""insert into ops.refresh_request (task_key, requested_by, status)
                     values ('test.c6_health', 'admin', 'dispatched')""")
    yield
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("delete from ops.alert where dedupe_key = 'c6:health'")
        c.execute("delete from ops.refresh_request where task_key = 'test.c6_health'")
        c.execute("delete from ops.task where key = 'test.c6_health'")


@pytest.mark.db
def test_sql_store_reads_admin_health_as_service_role(seeded):
    with psycopg.connect(ADMIN) as conn:
        conn.execute("set role service_role")          # the role the Vercel routes hold (Arch 01 §1.9)
        body = refresh.SqlRefreshStore(conn).health()
    (task,) = [t for t in body["tasks"] if t["key"] == "test.c6_health"]
    assert task["open_alerts"] == 1 and task["enabled"] is True
    assert task["refresh_available_at"] is not None              # dispatched just now: in its 45-minute cooldown
    assert [a["message"] for a in body["alerts"] if a["task_key"] == "test.c6_health"] == ["C6 test alert"]
    assert isinstance(body["runs"], list) and len(body["runs"]) <= 20


@pytest.mark.db
def test_anon_and_authenticated_cannot_read_health():
    for role in ("anon", "authenticated"):
        with psycopg.connect(ADMIN) as c:
            c.execute(f"set role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("select api.admin_health()")
