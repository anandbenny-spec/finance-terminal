"""`GET /api/cron/keepalive` (issue #196; Plan 04 §4, survival job S1): the daily Vercel cron's route, the one real read
it makes through the Data API with the anon key, and the `vercel.json` and `.env.example` lines that go with it."""
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ft.api import cron
from ft.api.app import app

ROOT = Path(__file__).resolve().parents[2]
SECRET = "c" * 32                                   # a stand-in for CRON_SECRET (low entropy: no scanner finding)
ANON = "anon-key-for-the-test-only"
SERVICE_ROLE = "service-role-key-must-never-be-used"
URL = "https://abcdefgh.supabase.co"
GOOD = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", ANON)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SERVICE_ROLE)
    return monkeypatch


class Upstream:
    """The Data API, as httpx's mock transport: records every request and answers as told."""

    def __init__(self, status=200, body=b'[{"series_key": "fed_target_upper"}]', error=None):
        self.status, self.body, self.error, self.requests = status, body, error, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        return httpx.Response(self.status, content=self.body, headers={"Content-Type": "application/json"})


@pytest.fixture
def call(env):
    """call(upstream, headers) -> the route's response, with the Data API replaced by `upstream`."""
    def run(upstream, headers=GOOD):
        def client():
            with httpx.Client(transport=httpx.MockTransport(upstream.handler), follow_redirects=False) as c:
                yield c
        app.dependency_overrides[cron.data_api_client] = client
        return TestClient(app).get("/api/cron/keepalive", headers=headers)
    yield run
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- the gate

@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong-secret"},
    {"Authorization": SECRET},                                  # the secret without the scheme
    {"Authorization": f"bearer {SECRET}"},                      # Vercel sends exactly "Bearer"
    {"Authorization": f"Bearer {SECRET} "},
    {"Authorization": f"Bearer {SECRET}x"},
    {"Authorization": "Bearer "},
    {"Authorization": "Bearer ünïcode".encode("latin-1")},      # Starlette reads header bytes as latin-1
])
def test_a_request_without_the_secret_is_refused_and_reads_nothing(call, headers):
    up = Upstream()
    r = call(up, headers)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert up.requests == []                                    # no database read for anyone without the secret
    assert "ok" not in r.text


def test_with_no_cron_secret_the_route_answers_503_even_for_a_matching_looking_header(call, env, capsys):
    env.delenv("CRON_SECRET")
    up = Upstream()
    for headers in ({}, GOOD, {"Authorization": "Bearer "}):
        r = call(up, headers)
        assert r.status_code == 503 and r.json() == {"detail": "Service Unavailable"}
    assert up.requests == []
    assert "CRON_SECRET is not set" in capsys.readouterr().out


def test_an_empty_cron_secret_counts_as_unset(call, env):
    env.setenv("CRON_SECRET", "")
    up = Upstream()
    assert call(up, {"Authorization": "Bearer "}).status_code == 503 and up.requests == []


def test_the_secret_is_compared_in_constant_time(call, monkeypatch):
    seen = []
    real = cron.hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(cron.hmac, "compare_digest", spy)
    assert call(Upstream()).status_code == 200
    assert seen == [(f"Bearer {SECRET}".encode(), f"Bearer {SECRET}".encode())]


# ---------------------------------------------------------------- the one real read

def test_a_good_call_makes_one_anon_key_read_of_series_latest_and_answers_ok(call):
    up = Upstream()
    r = call(up)
    assert r.status_code == 200 and r.json() == {"ok": True} and r.headers["cache-control"] == "no-store"
    assert len(up.requests) == 1
    req = up.requests[0]
    assert req.method == "GET" and req.url.scheme == "https" and req.url.host == "abcdefgh.supabase.co"
    assert req.url.path == "/rest/v1/series_latest"
    assert dict(req.url.params) == {"select": "series_key", "limit": "1"}
    assert req.headers["apikey"] == ANON and req.headers["authorization"] == f"Bearer {ANON}"
    assert req.headers["accept-profile"] == "api"


def test_the_service_role_key_is_never_sent(call):
    up = Upstream()
    call(up)
    sent = " ".join([str(up.requests[0].url), *[f"{k}={v}" for k, v in up.requests[0].headers.items()]])
    assert SERVICE_ROLE not in sent and SECRET not in sent       # nor the cron secret


def test_a_trailing_slash_on_the_project_url_is_tolerated(call, env):
    env.setenv("SUPABASE_URL", URL + "/")
    up = Upstream()
    assert call(up).status_code == 200 and up.requests[0].url.path == "/rest/v1/series_latest"


def test_an_empty_list_still_counts_the_read_happened(call):
    r = call(Upstream(body=b"[]"))
    assert r.status_code == 200 and r.json() == {"ok": True}


@pytest.mark.parametrize("upstream", [
    Upstream(status=500, body=b'{"message": "boom"}'),
    Upstream(status=401, body=b'{"message": "Invalid API key"}'),
    Upstream(status=404, body=b"not found"),
    Upstream(status=302, body=b""),                              # redirects are not followed
    Upstream(status=200, body=b"<html>not json</html>"),
    Upstream(status=200, body=b'{"message": "an object, not a list"}'),
    Upstream(status=200, body=b"null"),
    Upstream(error=httpx.ConnectError("could not connect to https://abcdefgh.supabase.co")),
    Upstream(error=httpx.ReadTimeout("timed out")),
])
def test_a_failed_read_answers_503_not_ok(call, upstream):
    r = call(upstream)
    assert r.status_code == 503 and r.json() == {"ok": False} and r.headers["cache-control"] == "no-store"
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("missing", ["SUPABASE_URL", "SUPABASE_ANON_KEY"])
def test_a_deployment_without_the_data_api_settings_answers_503_not_ok(call, env, missing, capsys):
    env.delenv(missing)
    up = Upstream()
    r = call(up)
    assert r.status_code == 503 and r.json() == {"ok": False} and up.requests == []
    assert "SUPABASE_URL or SUPABASE_ANON_KEY is not set" in capsys.readouterr().out


def test_nothing_from_the_upstream_reaches_the_response_or_the_log(call, capsys):
    leak = f"LEAK-BODY {ANON} {URL} {SERVICE_ROLE} postgres://user:pw@host/db"
    for upstream in (Upstream(status=500, body=leak.encode()), Upstream(status=200, body=leak.encode()),
                     Upstream(error=httpx.ConnectError(leak))):
        r = call(upstream)
        assert r.status_code == 503 and r.json() == {"ok": False}
        assert "LEAK" not in r.text and ANON not in r.text and URL not in r.text
        assert not r.headers.get("x-leak")
    log = capsys.readouterr().out
    for secret in ("LEAK", ANON, URL, SERVICE_ROLE, SECRET, "postgres://"):
        assert secret not in log
    assert "Data API answered 500" in log and "ConnectError" in log and "was not JSON" in log


def test_a_good_answer_does_not_echo_the_rows(call):
    r = call(Upstream(body=b'[{"series_key": "fed_target_upper", "extra": "sentinel-row-value"}]'))
    assert r.json() == {"ok": True} and "sentinel" not in r.text


# ---------------------------------------------------------------- how the route is wired

def test_the_route_is_one_get_under_the_api_prefix_and_is_mounted_on_the_app(env):
    assert [(r.path, sorted(r.methods)) for r in cron.router.routes] == [("/api/cron/keepalive", ["GET"])]
    client = TestClient(app)
    assert client.get("/api/cron/keepalive").status_code == 401           # mounted: 401 from the gate, not 404
    assert client.post("/api/cron/keepalive", headers=GOOD).status_code == 405


def test_the_existing_health_and_admin_routes_are_unchanged(env):
    client = TestClient(app)
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/admin/health").status_code == 401             # still behind the admin gate


def test_vercel_json_schedules_the_route_once_a_day_and_routes_api_paths_to_the_python_service():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["crons"] == [{"path": "/api/cron/keepalive", "schedule": "17 3 * * *"}]
    minute, hour, dom, month, dow = cfg["crons"][0]["schedule"].split()
    assert minute.isdigit() and hour.isdigit() and (dom, month, dow) == ("*", "*", "*")   # Hobby: at most once a day
    assert {"source": "/api/(.*)", "destination": {"service": "api"}} in cfg["rewrites"]
    assert cfg["services"]["api"]["root"] == "py/"
    # the cron's path is a route of the service the rewrite reaches
    assert re.fullmatch(cfg["rewrites"][0]["source"], cfg["crons"][0]["path"])
    assert cfg["crons"][0]["path"] in {r.path for r in cron.router.routes}


def test_env_example_names_the_two_new_variables_under_the_vercel_api_service_with_no_values():
    text = (ROOT / ".env.example").read_text()
    section = re.search(r"# --- Vercel, api service.*?\n(.*?)\n\n", text, re.DOTALL)[1]
    values = dict(re.findall(r"^([A-Z_]+)=(\S*)", section, re.MULTILINE))
    assert values["CRON_SECRET"] == "" and values["SUPABASE_ANON_KEY"] == ""
    web = re.search(r"# --- Vercel, web service.*?\n(.*?)\n\n", text, re.DOTALL)[1]
    assert "NEXT_PUBLIC_SUPABASE_ANON_KEY=" in web          # the web service keeps its own copy of the same value


def test_the_api_service_still_needs_no_dependency_outside_its_base_list():
    """The route uses only fastapi and httpx, which the API service's base dependencies already hold."""
    import tomllib
    base = {re.split(r"[\[<>=]", d)[0] for d in tomllib.loads((ROOT / "py/pyproject.toml").read_text())["project"]["dependencies"]}
    assert {"fastapi", "httpx"} <= base
    imports = set(re.findall(r"^(?:from|import) (\w+)", (ROOT / "py/src/ft/api/cron.py").read_text(), re.MULTILINE))
    assert imports <= {"__future__", "hmac", "os", "dataclasses", "typing", "httpx", "fastapi"}
