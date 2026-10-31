"""The weekday evening `daily` run at 16:37 IST (issue #105): the ticker tape shows the same day's Indian closes.

Only `policy.market_series` is due at it; no other daily task is, so `policy.ai_batch` never runs and spends nothing.
The Yahoo parsing is checked at the evening fetch time. No database: the due rules run against a fake last success."""
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from ft.jobs import config, due
from ft.policy import series_sources as src

IST, NY, LON = ZoneInfo("Asia/Kolkata"), ZoneInfo("America/New_York"), ZoneInfo("Europe/London")
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "daily.yml"
SERIES, REACTIONS, AI = "policy.market_series", "policy.reactions", "policy.ai_batch"
WED = date(2026, 11, 4)                     # a weekday


def ist(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def rule(key: str) -> str:
    return config.load()[key].rules["daily"]


@pytest.fixture
def last(monkeypatch):
    box = {}
    monkeypatch.setattr(due, "_last_success", lambda db, key, workflow=None: box.get(key))
    monkeypatch.setattr(due, "_recent", lambda db, bank, now: [])          # no decision meetings: no catch-up applies
    monkeypatch.setattr(due, "decision_meeting", lambda db, bank, day: None)
    return box


def test_daily_has_the_morning_and_the_weekday_evening_schedule_in_india():
    sched = yaml.safe_load(WORKFLOW.read_text())[True]["schedule"]       # YAML reads the key `on` as True
    assert sched == [{"cron": "37 6 * * *", "timezone": "Asia/Kolkata"},
                     {"cron": "37 16 * * 1-5", "timezone": "Asia/Kolkata"}]


def test_the_guard_still_covers_the_whole_daily_workflow():
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    assert {str(j["if"]).strip() for j in jobs.values()} == {"github.event_name != 'schedule' || vars.PRODUCTION_SCHEDULES == 'on'"}


def test_market_series_is_due_at_both_runs(last):
    last[SERIES] = ist(WED, 6, 37)                                       # the morning run succeeded
    assert due.evaluate(None, SERIES, rule(SERIES), ist(WED, 16, 37)).due
    last[SERIES] = ist(WED, 16, 37)                                      # the evening run succeeded
    assert due.evaluate(None, SERIES, rule(SERIES), ist(date(2026, 11, 5), 6, 37)).due


def test_the_runs_delayed_by_github_are_still_due(last):
    last[SERIES] = ist(WED, 7, 13)                                       # the morning run started 36 minutes late
    assert due.evaluate(None, SERIES, rule(SERIES), ist(WED, 16, 37)).due


@pytest.mark.parametrize("key", [REACTIONS, AI])
def test_no_other_daily_task_is_due_at_the_evening_run(last, key):
    last[key] = ist(WED, 6, 37)
    d = due.evaluate(None, key, rule(key), ist(WED, 16, 37))
    assert not d.due, key


def test_the_ai_batch_is_not_due_even_when_the_morning_run_was_late(last):
    last[AI] = ist(WED, 7, 13)
    assert not due.evaluate(None, AI, rule(AI), ist(WED, 16, 37)).due


def chart(symbol: str, tz: ZoneInfo, bars, currency="USD") -> bytes:
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "currency": currency, "exchangeTimezoneName": str(tz)},
        "timestamp": [int(t.timestamp()) for t, _ in bars],
        "indicators": {"quote": [{"close": [c for _, c in bars]}]}}]}}).encode()


def test_yahoo_at_the_evening_fetch_keeps_todays_indian_close_and_drops_todays_us_and_rupee_bars():
    fetched = ist(WED, 16, 37).astimezone(UTC)
    nsei = src.parse_yahoo(chart("^NSEI", IST, [(ist(WED, 9, 15), 25100.5)], "INR"), "^NSEI", "index_points",
                           fetched_at=fetched, fixing_time=time(15, 30), fixing_tz="Asia/Kolkata")
    assert nsei.points == [(WED, Decimal("25100.50"))]
    spx = src.parse_yahoo(chart("^GSPC", NY, [(datetime(2026, 11, 4, 9, 30, tzinfo=NY), 6700.0)]), "^GSPC",
                          "index_points", fetched_at=fetched, fixing_time=time(16), fixing_tz="America/New_York")
    assert spx.points == [] and spx.dropped_live == [WED]
    inr = src.parse_yahoo(chart("INR=X", LON, [(datetime(2026, 11, 4, tzinfo=LON), 88.9)], "INR"), "INR=X",
                          "inr_per_usd", fetched_at=fetched, fixing_time=None, fixing_tz=None)
    assert inr.points == [] and inr.dropped_live == [WED]
