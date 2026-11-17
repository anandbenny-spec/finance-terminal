"""`/api/admin/cmdbar/examples` (Spec 07 §4 Examples, §9 step 4; K-09 part 2, #588): the admin page's list of every example answer,
checked or not, and the mark that lets a checked one reach the box. The gate, the checks before a mark (the answer the admin saw,
an answered example only), bare statuses, and 0055's functions on the test database."""
import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ft.api import cmdbar as api
from ft.api import cmdbar_admin
from ft.api.admin_auth import AdminUser, require_admin
from ft.api.app import app
from ft.cmdbar import route as route_mod

T0 = "2026-11-17T04:30:00.123456+00:00"
ANSWER = {"state": "answered", "route": "data", "summary": "1 row", "columns": [], "rows": []}


def rows():
    return [{"id": 2, "question": "Which Nifty 50 banks trade below one times book value?", "module": "equity",
             "display_order": 2, "answer": ANSWER, "data_version": "v2", "verified": False, "updated_at": T0},
            {"id": 1, "question": "When did the RBI last change the repo rate, and by how much?", "module": "policy",
             "display_order": 1, "answer": ANSWER, "data_version": "v1", "verified": True, "updated_at": T0},
            {"id": 3, "question": "What changed in the RBI's latest resolution?", "module": "policy", "display_order": 3,
             "answer": {"state": "cant_answer", "route": "document"}, "data_version": None, "verified": False, "updated_at": T0},
            {"id": 4, "question": "Not computed yet", "module": "equity", "display_order": 4, "answer": None,
             "data_version": None, "verified": False, "updated_at": T0},
            {"id": "x", "question": "a malformed row"}, "not a row"]


class Store(api.FunctionStore):
    def __init__(self, fail=None):
        self.rows, self.fail, self.calls = rows(), fail, []

    def _rpc(self, fn, args, *, empty_ok=False):
        self.calls.append((fn, args))
        if self.fail == fn:
            raise route_mod.StoreError(f"{fn}: Data API answered 404")
        if fn == "cmdbar_examples_admin":
            return self.rows
        if fn == "cmdbar_example_check":
            for r in self.rows:
                if isinstance(r, dict) and r.get("id") == args["p_id"]:
                    r["verified"] = args["p_verified"]
                    return {"id": r["id"], "verified": r["verified"], "data_version": r["data_version"], "updated_at": r["updated_at"]}
            return None
        raise AssertionError(fn)


@pytest.fixture
def client():
    store = Store()
    app.dependency_overrides.update({require_admin: lambda: AdminUser("a", "a@example.com"),
                                     cmdbar_admin.examples_store: lambda: store})
    yield TestClient(app), store
    app.dependency_overrides.clear()


def check(c, i, verified=True, updated_at=T0, raw=None):
    body = raw if raw is not None else json.dumps({"verified": verified, "updated_at": updated_at})
    return c.post(f"/api/admin/cmdbar/examples/{i}/check", content=body, headers={"Content-Type": "application/json"})


def test_the_list_holds_every_example_checked_or_not_in_display_order(client):
    c, store = client
    r = c.get("/api/admin/cmdbar/examples")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    got = r.json()["examples"]
    assert [e["id"] for e in got] == [1, 2, 3, 4]                                   # malformed rows left out
    assert [e["verified"] for e in got] == [True, False, False, False]
    assert got[0] == {**rows()[1]}
    assert store.calls == [("cmdbar_examples_admin", {})]


def test_marking_an_answered_example_checked_sets_it_through_0055(client):
    c, store = client
    r = check(c, 2)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"id": 2, "verified": True, "updated_at": T0}
    assert store.calls[-1] == ("cmdbar_example_check", {"p_id": 2, "p_verified": True})


def test_the_same_instant_written_another_way_still_matches(client):
    c, _ = client
    assert check(c, 2, updated_at="2026-11-17T10:00:00.123456+05:30").status_code == 200


def test_an_answer_rewritten_since_the_page_read_it_is_a_409_and_nothing_is_set(client):
    c, store = client
    later = (datetime.fromisoformat(T0) + timedelta(seconds=1)).isoformat()
    r = check(c, 2, updated_at=later)
    assert r.status_code == 409 and r.json() == {"state": "changed"}
    assert all(fn != "cmdbar_example_check" for fn, _ in store.calls)


@pytest.mark.parametrize("i", [3, 4])
def test_an_example_with_no_answer_or_a_cant_answer_can_t_be_checked(client, i):
    c, store = client
    r = check(c, i)
    assert r.status_code == 409 and r.json() == {"state": "not_answered"}
    assert all(fn != "cmdbar_example_check" for fn, _ in store.calls)


@pytest.mark.parametrize("i", [1, 3, 4])
def test_the_mark_can_always_be_taken_off_without_a_time(client, i):
    c, store = client
    r = check(c, i, verified=False, raw=json.dumps({"verified": False}))
    assert r.status_code == 200 and r.json()["verified"] is False
    assert store.calls[-1] == ("cmdbar_example_check", {"p_id": i, "p_verified": False})


def test_an_unknown_id_is_a_404(client):
    c, store = client
    assert check(c, 99).status_code == 404
    assert all(fn != "cmdbar_example_check" for fn, _ in store.calls)


@pytest.mark.parametrize("raw", ['{"verified": "yes", "updated_at": "' + T0 + '"}', '{"updated_at": "' + T0 + '"}',
                                 '{"verified": true}', '{"verified": true, "updated_at": "not a time"}',
                                 '{"verified": true, "updated_at": 5}', "not json", "[]",
                                 '{"verified": true, "updated_at": "' + "x" * 600 + '"}'])
def test_a_bad_body_is_a_bare_400(client, raw):
    c, store = client
    r = check(c, 2, raw=raw)
    assert r.status_code == 400 and "verified" not in r.text
    assert all(fn != "cmdbar_example_check" for fn, _ in store.calls)


@pytest.mark.parametrize("path", ["/api/admin/cmdbar/examples/0/check", "/api/admin/cmdbar/examples/x/check",
                                  "/api/admin/cmdbar/examples/2147483648/check"])
def test_an_id_out_of_range_is_refused(client, path):
    assert client[0].post(path, json={"verified": False}).status_code == 422


@pytest.mark.parametrize("fn", ["cmdbar_examples_admin", "cmdbar_example_check"])
def test_a_failing_store_is_a_bare_503_with_no_row_in_it(client, fn):
    c, store = client
    store.fail = fn
    r = c.get("/api/admin/cmdbar/examples") if fn == "cmdbar_examples_admin" else check(c, 2)
    assert r.status_code == 503 and "Nifty" not in r.text and "Data API" not in r.text


def test_an_unconfigured_deployment_is_a_503_after_the_gate(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    app.dependency_overrides[require_admin] = lambda: AdminUser("a", None)
    try:
        assert TestClient(app).get("/api/admin/cmdbar/examples").status_code == 503
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer a.b.c"}])
def test_without_the_admin_token_nothing_is_returned_or_set(headers):
    app.dependency_overrides.clear()
    store = Store()
    app.dependency_overrides[cmdbar_admin.examples_store] = lambda: store
    try:
        c = TestClient(app)
        assert c.get("/api/admin/cmdbar/examples", headers=headers).status_code == 401
        assert c.post("/api/admin/cmdbar/examples/2/check", headers=headers, json={"verified": True, "updated_at": T0}).status_code == 401
    finally:
        app.dependency_overrides.clear()
    assert store.calls == []


def test_the_gate_sits_on_the_router_itself():
    assert [d.dependency for d in cmdbar_admin.router.dependencies] == [require_admin]
    assert {r.path for r in cmdbar_admin.router.routes} == {"/api/admin/cmdbar/examples", "/api/admin/cmdbar/examples/{example_id}/check"}


def test_the_deployed_store_calls_0055_as_rpc_with_the_secret_key():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.headers.get("apikey"), req.headers.get("content-profile"), json.loads(req.content)))
        if req.url.path.endswith("/cmdbar_examples_admin"):
            return httpx.Response(200, json=rows()[:2])
        return httpx.Response(200, content=b"null")
    store = api.RpcCmdbarStore("https://x.supabase.co", "sb_secret_test", httpx.Client(transport=httpx.MockTransport(handler)))
    assert [r["id"] for r in store.examples_admin()] == [2, 1]
    assert store.example_check(9, True) is None                                    # an unknown id
    assert seen == [("/rest/v1/rpc/cmdbar_examples_admin", "sb_secret_test", "api", {}),
                    ("/rest/v1/rpc/cmdbar_example_check", "sb_secret_test", "api", {"p_id": 9, "p_verified": True})]


# ------------------------------------------------------------------ 0055 on the test database
@pytest.fixture
def service():
    import psycopg
    from conftest import dsn
    conn = psycopg.connect(dsn("postgres"), autocommit=True)
    conn.execute("set role service_role")                # the role the Vercel routes hold (Arch 01 §1.9)
    yield conn
    conn.close()


@pytest.mark.db
def test_the_routes_run_through_0055_and_a_checked_answer_reaches_the_box(service):
    import uuid

    import psycopg
    from conftest import dsn
    q = f"r55 py {uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn("postgres"), autocommit=True) as db:
        i = db.execute("""insert into ops.cmdbar_example (question, module, display_order, answer, data_version, verified)
                          values (%s, 'policy', 90, %s::jsonb, 'v9', false) returning id""",
                       (q, json.dumps(ANSWER))).fetchone()[0]
    store = api.SqlCmdbarStore(service)
    app.dependency_overrides.update({require_admin: lambda: AdminUser("a", None), cmdbar_admin.examples_store: lambda: store})
    try:
        c = TestClient(app)
        listed = {e["question"]: e for e in c.get("/api/admin/cmdbar/examples").json()["examples"]}
        assert listed[q]["verified"] is False and listed[q]["answer"]["state"] == "answered"
        assert q not in {e["question"] for e in store.examples()}                     # unchecked: not in the box's list
        r = check(c, i, updated_at=listed[q]["updated_at"])
        assert r.status_code == 200 and r.json()["verified"] is True
        assert q in {e["question"] for e in store.examples()}                         # checked: the box may show it
        assert check(c, i, verified=False, raw='{"verified": false}').json()["verified"] is False
        assert q not in {e["question"] for e in store.examples()}
    finally:
        app.dependency_overrides.clear()
        with psycopg.connect(dsn("postgres"), autocommit=True) as db:
            db.execute("delete from ops.cmdbar_example where question = %s", (q,))
