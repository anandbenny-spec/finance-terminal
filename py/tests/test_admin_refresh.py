"""`/api/admin/refresh` and its status route (Prompt 01 A3; Architecture 02 §1.6): the rejections, the dispatch of
`refresh.yml` with the right inputs and target, the run id stored, the Data API RPC calls the deployed route makes, and
migration 0016's functions against the test database as `service_role` (the role Architecture 01 §1.9 gives the
Vercel routes)."""
import json
from datetime import timedelta

import httpx
import psycopg
import pytest
from conftest import dsn
from fastapi.testclient import TestClient

from ft.api import dispatch as gh
from ft.api import refresh
from ft.api.admin_auth import AdminUser, require_admin
from ft.api.app import app


class MemStore:
    def __init__(self, status="dispatched"):
        self.status, self.rows = status, {}

    def open_request(self, task_key):
        if task_key == "policy.nonexistent":
            return refresh.Opened(None, "not_found")
        rid = len(self.rows) + 1
        self.rows[rid] = {"task": task_key, "status": self.status, "github_run_id": None}
        return refresh.Opened(rid, self.status)

    def finish_request(self, request_id, status, github_run_id):
        self.rows[request_id].update(status=status, github_run_id=github_run_id)

    def request_status(self, request_id):
        return self.rows.get(request_id)


class FakeGitHub:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.requests = status, body, []

    def handler(self, request):
        self.requests.append(request)
        if self.status == 200:
            return httpx.Response(200, json=self.body if self.body is not None else
                                  {"workflow_run_id": 987654321, "run_url": "x", "html_url": "y"})
        return httpx.Response(self.status)


@pytest.fixture
def make(monkeypatch):
    def build(store=None, github=None, token="github_pat_test_only", target="dev"):
        store, github = store or MemStore(), github or FakeGitHub()

        def http():
            with httpx.Client(transport=httpx.MockTransport(github.handler)) as c:
                yield c
        app.dependency_overrides.update({
            require_admin: lambda: AdminUser("admin-id", "admin@example.com"),
            refresh.refresh_store: lambda: store, gh.github_client: http,
            gh.dispatch_token: lambda: token, gh.deployment_target: lambda: target})
        return store, github, TestClient(app)
    yield build
    app.dependency_overrides.clear()


def test_dispatches_refresh_yml_with_the_request(make):
    store, github, client = make()
    r = client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert r.status_code == 202
    assert r.json() == {"request_id": 1, "status": "dispatched", "github_run_id": 987654321, "target": "dev",
                        "run_url": "https://github.com/anandbenny-spec/finance-terminal/actions/runs/987654321"}
    assert store.rows[1] == {"task": "policy.calendar", "status": "dispatched", "github_run_id": 987654321}
    (req,) = github.requests
    assert req.method == "POST" and str(req.url) == \
        "https://api.github.com/repos/anandbenny-spec/finance-terminal/actions/workflows/refresh.yml/dispatches"
    assert req.headers["authorization"] == "Bearer github_pat_test_only"
    assert json.loads(req.content) == {"ref": "main", "return_run_details": True, "inputs": {
        "task": "policy.calendar", "request_id": "1", "trigger": "admin", "target": "dev"}}


def test_dispatch_inputs_match_refresh_yml():
    """The inputs sent are exactly the workflow's inputs, and `trigger` / `target` use its choices."""
    from pathlib import Path

    import yaml
    wf = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/refresh.yml").read_text())
    inputs = wf[True]["workflow_dispatch"]["inputs"]             # PyYAML reads the key `on` as True
    assert set(inputs) == {"task", "request_id", "trigger", "target"}
    assert "admin" in inputs["trigger"]["options"] and set(inputs["target"]["options"]) == {"dev", "production"}


def test_production_target_only_on_vercels_production_deployment(make, monkeypatch):
    for env, want in (("production", "production"), ("preview", "dev"), ("development", "dev"), (None, "dev")):
        if env is None:
            monkeypatch.delenv("VERCEL_ENV", raising=False)
        else:
            monkeypatch.setenv("VERCEL_ENV", env)
        assert gh.deployment_target() == want
    _store, github, client = make(target="production")
    client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert json.loads(github.requests[0].content)["inputs"]["target"] == "production"


def test_github_without_run_details_still_dispatched(make):
    store, _github, client = make(github=FakeGitHub(status=204))
    r = client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert r.status_code == 202 and r.json()["github_run_id"] is None and r.json()["run_url"] is None
    assert store.rows[1]["status"] == "dispatched"


@pytest.mark.parametrize("gh_status", [401, 403, 404, 422, 500])
def test_failed_dispatch_is_recorded_as_failed(make, gh_status):
    store, _github, client = make(github=FakeGitHub(status=gh_status))
    r = client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert r.status_code == 502 and r.json() == {"request_id": 1, "status": "failed"}
    assert store.rows[1]["status"] == "failed"


def test_missing_dispatch_token_fails_without_calling_github(make):
    store, github, client = make(token=None)
    r = client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert r.status_code == 502 and github.requests == [] and store.rows[1]["status"] == "failed"


@pytest.mark.parametrize("status,code", [("rejected_disabled", 409), ("rejected_cooldown", 429),
                                         ("rejected_nse", 429)])
def test_rejections_are_recorded_and_not_dispatched(make, status, code):
    _store, github, client = make(store=MemStore(status=status))
    r = client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert r.status_code == code and r.json() == {"request_id": 1, "status": status}
    assert github.requests == []


def test_unknown_task_is_404(make):
    _store, github, client = make()
    assert client.post("/api/admin/refresh", json={"task": "policy.nonexistent"}).status_code == 404
    assert github.requests == []


@pytest.mark.parametrize("task", ["", "policy", "Policy.Calendar", "policy.calendar; drop", "a" * 70 + ".b"])
def test_malformed_task_key_is_422(make, task):
    store, github, client = make()
    assert client.post("/api/admin/refresh", json={"task": task}).status_code == 422
    assert store.rows == {} and github.requests == []


def test_status_route(make):
    _store, _github, client = make()
    client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert client.get("/api/admin/refresh/1").json()["github_run_id"] == 987654321
    assert client.get("/api/admin/refresh/99").status_code == 404


# ---------------------------------------------------------------- 0016's functions against the test database

ADMIN = dsn("postgres")
KEYS = ("test.a3_on", "test.a3_off", "test.a3_nse")


@pytest.fixture
def pg():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("delete from ops.refresh_request where task_key = any(%s)", (list(KEYS),))
        for key, enabled, nse in (("test.a3_on", True, False), ("test.a3_off", False, False),
                                  ("test.a3_nse", True, True)):
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after,
                                               uses_nse, enabled)
                         values (%s, 'test', 'A3 test task', '{daily}', '7 days', '8 days', %s, %s)
                         on conflict (key) do update set enabled = excluded.enabled, uses_nse = excluded.uses_nse""",
                      (key, nse, enabled))
    conn = psycopg.connect(ADMIN)
    conn.execute("set role service_role")          # the role the Vercel routes hold (Arch 01 §1.9)
    conn.commit()
    yield conn
    conn.close()
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("delete from ops.refresh_request where task_key = any(%s)", (list(KEYS),))
        c.execute("delete from ops.task where key = any(%s)", (list(KEYS),))


def _age(rid, minutes):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("update ops.refresh_request set requested_at = now() - %s where id = %s",
                  (timedelta(minutes=minutes), rid))


@pytest.mark.db
def test_pg_store_cooldown_and_statuses(pg):
    store = refresh.SqlRefreshStore(pg)
    assert store.open_request("test.a3_missing") == refresh.Opened(None, "not_found")
    assert store.open_request("test.a3_off").status == "rejected_disabled"
    first = store.open_request("test.a3_on")
    assert first.status == "dispatched"
    store.finish_request(first.request_id, "dispatched", 123)
    assert store.open_request("test.a3_on").status == "rejected_cooldown"
    _age(first.request_id, 46)
    again = store.open_request("test.a3_on")
    assert again.status == "dispatched"
    store.finish_request(again.request_id, "failed", None)            # a failed dispatch doesn't start the cooldown
    assert store.open_request("test.a3_on").status == "dispatched"
    s = store.request_status(first.request_id)
    assert s["status"] == "dispatched" and s["github_run_id"] == 123 and s["job_run"] is None
    assert s["run_url"].endswith("/actions/runs/123") and store.request_status(10**12) is None


@pytest.mark.db
def test_pg_store_nse_minimum_interval(pg):
    store = refresh.SqlRefreshStore(pg)
    first = store.open_request("test.a3_nse")
    assert first.status == "dispatched"
    _age(first.request_id, 60)                                        # past the cooldown, inside the 7-day interval
    assert store.open_request("test.a3_nse").status == "rejected_nse"


@pytest.mark.db
def test_pg_store_links_the_job_run(pg):
    store = refresh.SqlRefreshStore(pg)
    rid = store.open_request("test.a3_on").request_id
    with psycopg.connect(dsn("job_writer"), autocommit=True) as c:    # what the run does with --request-id
        jr = c.execute("insert into ops.job_run (workflow, trigger, runner, status) values"
                       " ('refresh', 'admin', 'github', 'success') returning id").fetchone()[0]
        c.execute("update ops.refresh_request set job_run_id = %s where id = %s", (jr, rid))
    assert store.request_status(rid)["job_run"]["status"] == "success"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("update ops.refresh_request set job_run_id = null where id = %s", (rid,))


# ---------------------------------------------------------------- the deployed store: RPC through the Data API

SETTINGS = refresh.AuthSettings("https://proj.supabase.co", "sb_secret_test_only")


class FakeDataApi:
    """Answers `/rest/v1/rpc/<fn>` like PostgREST does for the 0016 functions."""

    def __init__(self, answers, status=200):
        self.answers, self.status, self.requests = answers, status, []

    def handler(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "permission denied for function"})
        body = json.dumps(self.answers[request.url.path.rsplit("/", 1)[-1]])      # a null result is the body `null`
        return httpx.Response(200, content=body.encode(), headers={"content-type": "application/json"})


def _rpc_store(api):
    return refresh.RpcRefreshStore(SETTINGS, httpx.Client(transport=httpx.MockTransport(api.handler)))


def test_rpc_store_calls_the_0016_functions_with_the_secret_key():
    api = FakeDataApi({"admin_open_refresh": {"status": "dispatched", "request_id": 17},
                       "admin_finish_refresh": True,
                       "admin_refresh_status": {"request_id": 17, "task": "policy.calendar", "status": "dispatched",
                                                "requested_at": "2026-10-24T04:30:00+05:30", "github_run_id": 55,
                                                "job_run": None}})
    store = _rpc_store(api)
    assert store.open_request("policy.calendar") == refresh.Opened(17, "dispatched")
    store.finish_request(17, "dispatched", 55)
    assert store.request_status(17)["run_url"].endswith("/actions/runs/55")
    paths = [r.url.path for r in api.requests]
    assert paths == ["/rest/v1/rpc/admin_open_refresh", "/rest/v1/rpc/admin_finish_refresh",
                     "/rest/v1/rpc/admin_refresh_status"]
    assert [json.loads(r.content) for r in api.requests] == [
        {"p_task_key": "policy.calendar"}, {"p_request_id": 17, "p_status": "dispatched", "p_github_run_id": 55},
        {"p_request_id": 17}]
    for r in api.requests:
        assert r.method == "POST" and str(r.url).startswith("https://proj.supabase.co/rest/v1/rpc/")
        assert r.headers["apikey"] == "sb_secret_test_only"
        assert r.headers["authorization"] == "Bearer sb_secret_test_only"
        assert r.headers["content-profile"] == "api" and r.headers["accept-profile"] == "api"


def test_rpc_store_not_found_and_missing_status():
    store = _rpc_store(FakeDataApi({"admin_open_refresh": {"status": "not_found", "request_id": None},
                                    "admin_refresh_status": None}))
    assert store.open_request("policy.nonexistent") == refresh.Opened(None, "not_found")
    assert store.request_status(5) is None


@pytest.mark.parametrize("answer", [None, [], {"status": "dispatched"}, {"status": "weird", "request_id": 1},
                                    {"status": "dispatched", "request_id": True}])
def test_rpc_store_refuses_an_unexpected_answer(answer):
    with pytest.raises(refresh.StoreError):
        _rpc_store(FakeDataApi({"admin_open_refresh": answer})).open_request("policy.calendar")


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_rpc_store_errors_are_store_errors(status):
    with pytest.raises(refresh.StoreError):
        _rpc_store(FakeDataApi({}, status=status)).open_request("policy.calendar")


def test_rpc_store_finish_that_changed_nothing_is_an_error():
    with pytest.raises(refresh.StoreError):
        _rpc_store(FakeDataApi({"admin_finish_refresh": False})).finish_request(1, "failed", None)


def test_refresh_store_unconfigured_is_503():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        refresh.refresh_store(None, httpx.Client())
    assert e.value.status_code == 503
    assert isinstance(refresh.refresh_store(SETTINGS, httpx.Client()), refresh.RpcRefreshStore)


class BrokenStore(MemStore):
    def __init__(self, fail_on):
        super().__init__()
        self.fail_on = fail_on

    def open_request(self, task_key):
        if self.fail_on == "open":
            raise refresh.StoreError("down")
        return super().open_request(task_key)

    def finish_request(self, request_id, status, github_run_id):
        if self.fail_on == "finish":
            raise refresh.StoreError("down")
        super().finish_request(request_id, status, github_run_id)

    def request_status(self, request_id):
        raise refresh.StoreError("down")


def test_store_down_is_a_bare_503_and_nothing_is_dispatched(make):
    _store, github, client = make(store=BrokenStore("open"))
    r = client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert r.status_code == 503 and "down" not in r.text and github.requests == []
    assert client.get("/api/admin/refresh/1").status_code == 503


def test_run_started_but_not_recorded_still_reports_dispatched(make):
    _store, github, client = make(store=BrokenStore("finish"))
    r = client.post("/api/admin/refresh", json={"task": "policy.calendar"})
    assert r.status_code == 202 and r.json()["github_run_id"] == 987654321 and len(github.requests) == 1


@pytest.mark.parametrize("rid", ["0", "-1", str(2**63), "abc"])
def test_status_route_refuses_out_of_range_ids(make, rid):
    _store, _github, client = make()
    assert client.get(f"/api/admin/refresh/{rid}").status_code == 422


@pytest.mark.db
def test_sql_store_finish_refuses_a_request_already_finished(pg):
    store = refresh.SqlRefreshStore(pg)
    rid = store.open_request("test.a3_on").request_id
    store.finish_request(rid, "dispatched", 321)
    with pytest.raises(refresh.StoreError):
        store.finish_request(rid, "failed", None)
    assert store.request_status(rid)["github_run_id"] == 321


@pytest.mark.db
def test_anon_and_authenticated_cannot_call_the_functions():
    for role in ("anon", "authenticated"):
        with psycopg.connect(ADMIN) as c:
            c.execute(f"set role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("select api.admin_open_refresh('policy.calendar')")
