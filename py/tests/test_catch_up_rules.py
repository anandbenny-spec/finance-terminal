"""The decision-day catch-up rules (issue #158), against a fake world: no database, so these run anywhere.

A rate decision must reach production even when GitHub starts the decision-day `watch` late or not at all. Inside the
watch window the rules are what they were (the D1 path, #24). After it closes they stay due as a catch-up while the
decision document isn't stored (and, for the AI step, while it hasn't run since), until the end of the next calendar
day in the bank's zone. The database side (yesterday's meeting, `ops.task_run`) is covered in test_catch_up_db.py."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ft.jobs import config, due

NY, IST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")
FOMC, MPC = due.BANKS["fomc_decision_day"], due.BANKS["mpc_decision_day"]
FED, RBI, AI = "policy.fed_documents", "policy.rbi_documents", "policy.ai_batch"
DAILY_FED, DAILY_RBI, DAILY_AI = ("every 20 hours or fomc_catch_up", "every 20 hours or mpc_catch_up",
                                  "every 20 hours or ai_catch_up")


class World:
    """The meetings and task successes the due rules ask the database for."""

    def __init__(self):
        self.meetings: dict[tuple[str, date], due.Meeting] = {}
        self.successes: list[tuple[str, str, datetime]] = []        # task key, workflow, finished_at

    def add(self, bank: due.Bank, day: date, stored: datetime | None = None) -> due.Meeting:
        release = datetime.combine(day, bank.release, tzinfo=bank.tz)
        m = due.Meeting(len(self.meetings) + 1, f"test-{bank.slug}-{day}", bank, day, release, stored)
        self.meetings[(bank.slug, day)] = m
        return m

    def succeeded(self, key: str, workflow: str, at: datetime) -> None:
        self.successes.append((key, workflow, at))

    def decision_meeting(self, db, bank, day):
        return self.meetings.get((bank.slug, day))

    def last_success(self, db, key, workflow=None):
        got = [at for k, w, at in self.successes if k == key and workflow in (None, w)]
        return max(got, default=None)


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(due, "decision_meeting", w.decision_meeting)
    monkeypatch.setattr(due, "_last_success", w.last_success)
    return w


def at(day: date, hh: int, mm: int, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)


D = date(2030, 10, 30)                       # an FOMC decision day: 14:00 New York, window 13:50 to 14:40
M = date(2030, 12, 4)                        # an MPC decision day: 10:00 India, window 09:50 to 11:00
NEXT, AFTER = D + timedelta(days=1), D + timedelta(days=2)


# ---------------------------------------------------------------- fomc_decision_day / mpc_decision_day

def test_inside_the_window_the_rule_is_what_it_was(world):
    m = world.add(FOMC, D)
    d = due.evaluate(None, FED, "fomc_decision_day", at(D, 14, 7, NY))
    assert d.due and d.meeting is m and d.until == m.closes_at and not d.catch_up
    assert "watching until" in d.reason and "catch-up" not in d.reason
    # the lead: a start 10 minutes early polls; earlier finds it too early, as before
    assert due.evaluate(None, FED, "fomc_decision_day", at(D, 13, 50, NY)).due
    early = due.evaluate(None, FED, "fomc_decision_day", at(D, 13, 30, NY))
    assert not early.due and "too early, the watch window opens at" in early.reason and not early.catch_up


@pytest.mark.parametrize("hh, mm", [(14, 40), (14, 50), (15, 12), (17, 42), (23, 59)])
def test_after_the_window_the_statement_is_caught_up_in_one_pass(world, hh, mm):
    m = world.add(FOMC, D)
    d = due.evaluate(None, FED, "fomc_decision_day", at(D, hh, mm, NY))
    assert d.due and d.catch_up and d.meeting is m
    assert d.until is None                                # nothing to poll towards: the loop ends after one pass
    assert "catch-up" in d.reason and "isn't stored" in d.reason and "closed at 2030-10-30 14:40 EDT" in d.reason


def test_the_catch_up_runs_until_the_end_of_the_next_calendar_day_in_the_banks_zone(world):
    m = world.add(FOMC, D)
    assert m.catch_up_ends == at(date(2030, 11, 1), 0, 0, NY)
    # the next morning in India (06:37 on the 31st) is still the 30th in New York: the same meeting, today's
    morning = at(NEXT, 6, 37, IST)
    assert morning.astimezone(NY).date() == D
    assert due.evaluate(None, FED, "fomc_decision_day", morning).catch_up
    # the next day in New York: yesterday's meeting, found from the day before
    assert due.evaluate(None, FED, "fomc_decision_day", at(NEXT, 9, 0, NY)).catch_up
    assert due.evaluate(None, FED, "fomc_decision_day", at(NEXT, 23, 59, NY)).catch_up
    # two days later nothing is due
    gone = due.evaluate(None, FED, "fomc_decision_day", at(AFTER, 0, 0, NY))
    assert not gone.due and gone.reason == "not an FOMC decision day"
    assert not due.evaluate(None, FED, "fomc_decision_day", at(AFTER, 14, 7, NY)).due


def test_once_the_document_is_stored_there_is_nothing_to_catch_up(world):
    world.add(FOMC, D, stored=at(D, 14, 3, NY))
    for now in (at(D, 14, 7, NY), at(D, 15, 12, NY), at(NEXT, 9, 0, NY)):
        d = due.evaluate(None, FED, "fomc_decision_day", now)
        assert not d.due and not d.catch_up and d.reason == "test-fed-2030-10-30: the fomc_statement is stored"


def test_the_mpc_is_caught_up_the_same_way_in_india(world):
    m = world.add(MPC, M)
    assert m.closes_at == at(M, 11, 0, IST) and m.catch_up_ends == at(date(2030, 12, 6), 0, 0, IST)
    live = due.evaluate(None, RBI, "mpc_decision_day", at(M, 10, 7, IST))
    assert live.due and not live.catch_up and live.until == m.closes_at
    for now in (at(M, 11, 12, IST), at(M, 13, 42, IST), at(M + timedelta(days=1), 6, 37, IST)):
        d = due.evaluate(None, RBI, "mpc_decision_day", now)
        assert d.due and d.catch_up and d.meeting is m and d.until is None and "catch-up" in d.reason
    assert not due.evaluate(None, RBI, "mpc_decision_day", at(M + timedelta(days=2), 6, 37, IST)).due


def test_each_bank_is_caught_up_in_its_own_zone(world):
    # Both banks decide on the 30th. The next morning in India (the 31st, 06:37) is the evening of the 30th in New York:
    # the FOMC's meeting is today's there, the MPC's is yesterday's in India.
    fed, rbi = world.add(FOMC, D), world.add(MPC, D)
    now = at(NEXT, 6, 37, IST)
    assert (now.astimezone(NY).date(), now.astimezone(IST).date()) == (D, NEXT)
    assert due.evaluate(None, FED, "fomc_decision_day", now).meeting is fed
    assert due.evaluate(None, RBI, "mpc_decision_day", now).meeting is rbi
    assert due.evaluate(None, RBI, "mpc_decision_day", now).catch_up


# ---------------------------------------------------------------- after_decision_document

def test_the_ai_step_is_caught_up_when_it_hasnt_run_since_the_document_was_stored(world):
    m = world.add(FOMC, D, stored=at(D, 14, 5, NY))
    d = due.evaluate(None, AI, "after_decision_document", at(D, 15, 12, NY))
    assert d.due and d.catch_up and d.meeting is m and d.until is None
    assert "catch-up" in d.reason and "the AI step hasn't run since" in d.reason
    # a run of any kind that succeeded since the document was stored has done it (once per stored document)
    for workflow in ("watch", "daily", "refresh"):
        world.successes.clear()
        world.succeeded(AI, workflow, at(D, 14, 6, NY))
        done = due.evaluate(None, AI, "after_decision_document", at(D, 15, 12, NY))
        assert not done.due and "already ran after the fomc_statement was stored" in done.reason
    # a success from before the document was stored doesn't
    world.successes.clear()
    world.succeeded(AI, "daily", at(D, 6, 0, NY))
    assert due.evaluate(None, AI, "after_decision_document", at(NEXT, 9, 0, NY)).catch_up
    # a success of another task doesn't either
    world.successes.clear()
    world.succeeded(FED, "watch", at(D, 14, 6, NY))
    assert due.evaluate(None, AI, "after_decision_document", at(D, 15, 12, NY)).catch_up


def test_the_ai_catch_up_ends_with_the_catch_up_and_waits_for_the_document(world):
    world.add(FOMC, D)
    missing = due.evaluate(None, AI, "after_decision_document", at(D, 15, 12, NY))
    assert not missing.due and "the fomc_statement isn't stored yet" in missing.reason
    world.add(FOMC, D, stored=at(D, 14, 5, NY))
    assert due.evaluate(None, AI, "after_decision_document", at(NEXT, 23, 59, NY)).catch_up
    assert not due.evaluate(None, AI, "after_decision_document", at(AFTER, 0, 0, NY)).due


def test_inside_the_window_only_a_watch_run_stands_in_for_the_ai_step(world):
    m = world.add(FOMC, D, stored=at(D, 14, 3, NY))
    world.succeeded(AI, "daily", at(D, 14, 10, NY))              # a batch run sends nothing in real time (B7)
    d = due.evaluate(None, AI, "after_decision_document", at(D, 14, 20, NY))
    assert d.due and not d.catch_up and d.meeting is m and d.until == m.closes_at
    world.succeeded(AI, "watch", at(D, 14, 12, NY))
    done = due.evaluate(None, AI, "after_decision_document", at(D, 14, 20, NY))
    assert not done.due and "already ran after the fomc_statement was stored" in done.reason


# ---------------------------------------------------------------- the daily's catch-up

def test_the_daily_catches_up_whatever_the_interval_says(world):
    world.add(FOMC, D)
    world.succeeded(FED, "daily", at(D, 10, 0, NY))
    now = at(NEXT, 6, 37, IST)                                  # 21:07 on the 30th in New York: 11 hours since
    d = due.evaluate(None, FED, DAILY_FED, now)
    assert d.due and d.catch_up and d.until is None
    assert d.meeting is None                                    # the daily's own pass, not the watch's single meeting
    assert "catch-up" in d.reason and "last success 11:07:00 ago" in d.reason
    # the same rule without the catch-up is not due: that is the gap #158 closes
    plain = due.evaluate(None, FED, "every 20 hours", now)
    assert not plain.due and not plain.catch_up


def test_the_daily_is_due_by_its_interval_as_before(world):
    world.add(FOMC, D, stored=at(D, 14, 3, NY))
    world.succeeded(FED, "daily", at(D, 10, 0, NY))
    d = due.evaluate(None, FED, DAILY_FED, at(NEXT, 7, 0, NY))      # 21 hours later
    assert d.due and not d.catch_up and d.reason == "last success 21:00:00 ago (rule: every 20 hours or fomc_catch_up)"
    fresh = due.evaluate(None, "policy.market_series", "every 20 hours", at(D, 10, 0, NY))
    assert fresh.due and fresh.reason == "never succeeded"


def test_the_daily_has_nothing_to_catch_up_once_the_document_is_stored_or_inside_the_window(world):
    world.add(FOMC, D)
    world.succeeded(FED, "daily", at(D, 10, 0, NY))
    inside = due.evaluate(None, FED, DAILY_FED, at(D, 14, 7, NY))      # an open window belongs to the watch
    assert not inside.due and not inside.catch_up
    world.add(FOMC, D, stored=at(D, 14, 40, NY))
    stored = due.evaluate(None, FED, DAILY_FED, at(NEXT, 6, 37, IST))
    assert not stored.due and "last success" in stored.reason and "the fomc_statement is stored" in stored.reason


def test_the_daily_catch_up_of_the_mpc_and_of_the_ai_step(world):
    world.add(MPC, M)
    world.succeeded(RBI, "daily", at(M, 9, 0, IST))
    world.succeeded(AI, "daily", at(M, 9, 0, IST))
    now = at(M, 22, 0, IST)                                     # a manual daily that evening: 13 hours since
    rbi = due.evaluate(None, RBI, DAILY_RBI, now)
    assert rbi.due and rbi.catch_up and rbi.meeting is None
    # the AI step follows once the resolution is stored, as the 28 Oct production refresh took it
    assert not due.evaluate(None, AI, DAILY_AI, now).due
    world.add(MPC, M, stored=at(M, 10, 6, IST))
    ai = due.evaluate(None, AI, DAILY_AI, now)
    assert ai.due and ai.catch_up and ai.meeting is None and "the AI step hasn't run since" in ai.reason
    world.succeeded(AI, "daily", at(M, 12, 0, IST))
    assert not due.evaluate(None, AI, DAILY_AI, now).due
    assert not due.evaluate(None, RBI, DAILY_RBI, now).due        # stored: nothing for the document task either


# ---------------------------------------------------------------- the rule text

def test_a_rule_with_a_catch_up_reads_and_validates(tmp_path):
    assert config.split_rule("every 20 hours or fomc_catch_up") == ("every 20 hours", "fomc_catch_up")
    assert config.split_rule("every 20 hours") == ("every 20 hours", None)
    assert config.every_interval("every 20 hours or mpc_catch_up") == timedelta(hours=20)
    assert config.every_interval("fomc_decision_day") is None
    assert set(config.CATCH_UPS.values()) <= set(config.NAMED_RULES)

    tasks = config.load()
    assert tasks[FED].rules == {"daily": "every 20 hours or fomc_catch_up", "watch": "fomc_decision_day"}
    assert tasks[RBI].rules == {"daily": "every 20 hours or mpc_catch_up", "watch": "mpc_decision_day"}
    assert tasks[AI].rules == {"daily": "every 20 hours or ai_catch_up", "watch": "after_decision_document"}
    assert all(tasks[k].min_interval == timedelta(hours=20) for k in (FED, RBI, AI))

    def load(rule):
        f = tmp_path / "tasks.yaml"
        f.write_text(f"a.b: {{description: x, workflows: {{daily: '{rule}'}}, overdue_after: 1 days}}")
        return config.load(f)

    assert load("every 2 days or ai_catch_up")["a.b"].min_interval == timedelta(days=2)
    for bad, message in (("every 20 hours or tomorrow", "unknown catch-up"), ("every 20 hours or", "not an interval"),
                         ("fomc_decision_day or fomc_catch_up", "unknown due rule")):
        with pytest.raises(config.ConfigError, match=message):
            load(bad)


def test_the_rules_refuse_a_naive_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        due.evaluate(None, FED, "fomc_decision_day", datetime(2030, 10, 30, 14, 7))
