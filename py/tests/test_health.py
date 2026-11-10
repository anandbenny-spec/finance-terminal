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

    def run(status=200, body=None, error=None, age=timedelta(hours=2), last_age=None):
        """`age` is policy.market_series' last success, `last_age` (default: the same) site.quality_summary's."""
        last_age = age if last_age is None else last_age
        payload = body if body is not None else [
            {"task_key": "policy.market_series", "last_success_at": (NOW - age).isoformat()},
            {"task_key": "site.quality_summary", "last_success_at": (NOW - last_age).isoformat()}]

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
    assert "site.quality_summary" in str(req.url) and len(get.seen) == 1       # one read for both tasks


def test_the_legacy_jwt_key_goes_in_apikey_and_as_a_bearer_token(get):
    get()
    (req,) = get.seen
    assert req.headers["authorization"] == f"Bearer {ANON}"


def test_a_publishable_key_goes_in_apikey_alone(get, monkeypatch):
    """Supabase refuses an `sb_publishable_` key sent as a Bearer token (it isn't a JWT): launch check #87."""
    publishable = "sb_publishable_for_the_test_only"
    monkeypatch.setenv("SUPABASE_ANON_KEY", publishable)
    r = get()
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    (req,) = get.seen
    assert req.headers["apikey"] == publishable and "authorization" not in req.headers
    assert req.headers["accept-profile"] == "api"


@pytest.mark.parametrize("age, code", [(timedelta(hours=35), 200), (timedelta(hours=37), 503)])
def test_the_newest_load_must_be_under_36_hours_old(get, age, code):
    r = get(age=age)
    assert r.status_code == code and list(r.json()) == ["status"]


@pytest.mark.parametrize("age, code", [(timedelta(hours=35), 200), (timedelta(hours=37), 503)])
def test_the_last_task_must_be_under_36_hours_old_too(get, age, code):
    r = get(age=timedelta(hours=2), last_age=age)
    assert r.status_code == code and list(r.json()) == ["status"]


def test_a_run_that_stopped_before_the_last_task_is_degraded_while_the_first_is_fresh(get):
    r = get(age=timedelta(hours=2), last_age=timedelta(hours=40))
    assert r.status_code == 503 and r.json() == {"status": "degraded"}


def test_the_first_task_stale_while_the_last_is_fresh_is_degraded(get):
    r = get(age=timedelta(hours=40), last_age=timedelta(hours=2))
    assert r.status_code == 503 and r.json() == {"status": "degraded"}


def test_a_missing_row_for_either_task_is_degraded(get):
    fresh = (NOW - timedelta(hours=2)).isoformat()
    for only in ("policy.market_series", "site.quality_summary"):
        r = get(body=[{"task_key": only, "last_success_at": fresh}])
        assert r.status_code == 503 and r.json() == {"status": "degraded"}


@pytest.mark.parametrize("bad", [None, "no", 5])
def test_an_unusable_time_on_either_row_is_degraded(get, bad):
    fresh = (NOW - timedelta(hours=2)).isoformat()
    for first in (True, False):
        rows = [{"task_key": "policy.market_series", "last_success_at": bad if first else fresh},
                {"task_key": "site.quality_summary", "last_success_at": fresh if first else bad}]
        r = get(body=rows)
        assert r.status_code == 503 and r.json() == {"status": "degraded"}


@pytest.mark.parametrize("kwargs", [
    {"status": 500}, {"status": 401}, {"error": httpx.ConnectError("secret-host-detail")},
    {"body": []}, {"body": [{"last_success_at": None}]}, {"body": {"message": "x"}}, {"body": [{"last_success_at": "no"}]},
    {"body": [{"last_success_at": "2026-10-31T10:00:00+00:00"}]},               # no task_key
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


WEEKLY = WORKFLOW.parent / "weekly.yml"


def test_the_daily_workflow_allows_45_minutes():
    """#364 (Review 02c F3-08): a day whose AI batch outlasts its poll reaches 25 to 28 minutes before the summary task."""
    assert yaml.safe_load(WORKFLOW.read_text())["jobs"]["run"]["timeout-minutes"] == 45


def test_the_weekly_workflow_pings_its_own_check_last_and_only_on_production():
    steps = yaml.safe_load(WEEKLY.read_text())["jobs"]["run"]["steps"]
    names = [s.get("name", "") for s in steps]
    ok, fail = steps[-2], steps[-1]
    assert "dead-man" in ok["name"] and "dead-man" in fail["name"] and "Warm the site" in names[-3]   # after the warm-up
    assert ok["if"] == "success() && env.FT_TARGET == 'production'"                  # a dev run never pings
    assert fail["if"] == "failure() && env.FT_TARGET == 'production'"
    for step in (ok, fail):
        assert step["env"] == {"HEALTHCHECKS_WEEKLY_PING_URL": "${{ secrets.HEALTHCHECKS_WEEKLY_PING_URL }}"}
        assert "inputs." not in step["run"] and "secrets." not in step["run"]        # only through env
        assert '[ -z "$HEALTHCHECKS_WEEKLY_PING_URL" ]' in step["run"] and "exit 0" in step["run"]   # no secret: skipped
    assert 'curl -fsS -m 10 --retry 3 "$HEALTHCHECKS_WEEKLY_PING_URL" ' in ok["run"]
    assert 'curl -fsS -m 10 --retry 3 "$HEALTHCHECKS_WEEKLY_PING_URL/fail" ' in fail["run"]
