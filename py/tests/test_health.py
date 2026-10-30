"""`GET /api/health` (issue #198; Plan 04 §4, survival job S3): the read through the Data API with the anon key and the
36-hour rule on the daily load, with a body of exactly one key; and the dead-man ping at the end of `daily.yml`."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from ft.api import cron, health
from ft.api.app import app

ANON = "anon-key-for-the-test-only"
URL = "https://abcdefgh.supabase.co"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/daily.yml"


@pytest.fixture
def get(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", ANON)
    monkeypatch.setattr(health, "now", lambda: NOW)
    seen = []

    def run(status=200, body=None, error=None, age=timedelta(hours=2)):
        payload = body if body is not None else [{"last_success_at": (NOW - age).isoformat()}]

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if error:
                raise error
            return httpx.Response(status, json=payload)

        def client():
            with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as c:
                yield c
        app.dependency_overrides[cron.data_api_client] = client
        return TestClient(app).get("/api/health")
    run.seen = seen
    yield run
    app.dependency_overrides.clear()


def test_a_fresh_daily_load_is_ok_and_the_body_has_one_key(get):
    r = get()
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"
    (req,) = get.seen
    assert req.headers["apikey"] == ANON and req.headers["accept-profile"] == "api"
    assert req.url.path == "/rest/v1/task_freshness" and "policy.market_series" in str(req.url)


@pytest.mark.parametrize("age, code", [(timedelta(hours=35), 200), (timedelta(hours=37), 503)])
def test_the_newest_load_must_be_under_36_hours_old(get, age, code):
    r = get(age=age)
    assert r.status_code == code and list(r.json()) == ["status"]


@pytest.mark.parametrize("kwargs", [
    {"status": 500}, {"status": 401}, {"error": httpx.ConnectError("secret-host-detail")},
    {"body": []}, {"body": [{"last_success_at": None}]}, {"body": {"message": "x"}}, {"body": [{"last_success_at": "no"}]},
])
def test_a_failed_read_or_no_usable_time_is_degraded_with_nothing_else_in_the_body(get, kwargs, capsys):
    r = get(**kwargs)
    assert r.status_code == 503 and r.json() == {"status": "degraded"} and r.headers["cache-control"] == "no-store"
    assert "secret-host-detail" not in r.text + capsys.readouterr().out


def test_without_the_data_api_settings_it_is_degraded(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    r = TestClient(app).get("/api/health")
    assert r.status_code == 503 and r.json() == {"status": "degraded"}


def test_the_daily_workflow_pings_the_dead_man_check_last_only_on_a_successful_production_run():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["run"]["steps"]
    ping = steps[-1]
    assert "dead-man" in ping["name"]
    assert ping["if"] == "success() && env.FT_TARGET == 'production'"
    assert ping["env"] == {"HEALTHCHECKS_PING_URL": "${{ secrets.HEALTHCHECKS_PING_URL }}"}
    assert "inputs." not in ping["run"] and "secrets." not in ping["run"]       # only through env
    assert '[ -z "$HEALTHCHECKS_PING_URL" ]' in ping["run"] and "exit 0" in ping["run"]   # no secret: skipped
    assert 'curl -fsS -m 10 --retry 3 "$HEALTHCHECKS_PING_URL"' in ping["run"]
