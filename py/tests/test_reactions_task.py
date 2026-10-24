"""policy.reactions through the runner, against the test database as job_writer (Prompt 01 §5 item 4; Spec 01 §2,
the "Reactions (1b)" row; Architecture 02 §2). Each test builds its own banks (entities under a fresh prefix), series
(with the real fixing times and zones) and meetings, so the fixture's rows and other tests are untouched."""
import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

import psycopg
import pytest
from conftest import dsn

from ft.jobs.runner import run
from ft.jobs.tasks.reactions import Reactions, change_of

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
KEY = "test.reactions_1b"
NOW = datetime(2024, 10, 20, 12, 0, tzinfo=UTC)
_n = itertools.count(1)

# (suffix, source, code, unit, fixing time, zone)
SERIES = [
    ("DGS2", "fred", "DGS2", "percent", "15:30", "America/New_York"),
    ("^GSPC", "yfinance", "^GSPC", "index_points", "16:00", "America/New_York"),
    ("DEXINUS", "fred", "DEXINUS", "inr_per_usd", "12:00", "America/New_York"),
    ("^NSEI", "yfinance", "^NSEI", "index_points", "15:30", "Asia/Kolkata"),
]


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


class World:
    """Two test banks, four series and a task pointed at them."""

    def __init__(self, fixing: bool = True):
        p = f"rx{next(_n)}x{datetime.now().strftime('%H%M%S%f')}"
        self.p, self.fed, self.rbi = p, f"{p}-fed", f"{p}-rbi"
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'reactions test', '{daily}', '20 hours', '4 days', false)
                         on conflict (key) do nothing""", (KEY,))
            for slug, mkt, cur in ((self.fed, "US", "USD"), (self.rbi, "IN", "INR")):
                c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                             values ('central_bank', %s, %s, %s, %s, true)""", (slug, slug, mkt, cur))
            for suffix, source, code, unit, fix, tz in SERIES:
                if not fixing and suffix == "^NSEI":
                    fix = tz = None
                c.execute("""insert into core.series (key, name, source_id, source_code, unit, frequency, fixing_time,
                                                      fixing_tz, is_published)
                             select %s, %s, id, %s, %s, 'D', %s::time, %s, true from core.source where key = %s""",
                          (f"{p}.{suffix}", f"test {suffix}", code, unit, fix, tz, source))
            job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('daily', 'cli', 'local') "
                            "returning id").fetchone()[0]
            self.tr = c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, %s, 'success') "
                                "returning id", (job, KEY)).fetchone()[0]
        self.ids = dict(q("select key, id from core.series where key like %s", p + ".%"))

    def k(self, suffix):
        return f"{self.p}.{suffix}"

    def task(self) -> Reactions:
        class T(Reactions):
            key = KEY
            series_by_bank = MappingProxyType({self.fed: tuple(self.k(s) for s in ("DGS2", "^GSPC", "DEXINUS", "^NSEI")),
                              self.rbi: (self.k("^NSEI"), self.k("DEXINUS"))})
            surprise = (self.fed, self.k("DGS2"))
        t = T()
        t.workflows = ("daily",)
        return t

    def observe(self, suffix, values: dict):
        with psycopg.connect(ADMIN, autocommit=True) as c:
            for d, v in values.items():
                c.execute("""insert into core.observation (series_id, obs_date, value, task_run_id) values (%s,%s,%s,%s)
                             on conflict (series_id, obs_date) do update set value = excluded.value""",
                          (self.ids[self.k(suffix)], d, Decimal(str(v)), self.tr))

    def fill(self, start: date, end: date):
        """A weekday path for each series: value = base + i × step."""
        for suffix, base, step in (("DGS2", "3.50", "0.01"), ("^GSPC", "5000", "10"), ("DEXINUS", "83.00", "0.05"),
                                   ("^NSEI", "24000", "20")):
            self.observe(suffix, {d: Decimal(base) + i * Decimal(step)
                                  for i, d in enumerate(weekdays(start, end))})

    def meeting(self, bank, slug, occurs_at: str, status="held", decision=True):
        with psycopg.connect(ADMIN, autocommit=True) as c:
            e = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status,
                                                     is_published)
                             select 'policy_meeting', id, %s, %s, %s::timestamptz::date, %s, %s, true
                               from core.entity where slug = %s returning id""",
                          (f"{self.p}-{slug}", slug, occurs_at, occurs_at, status, bank)).fetchone()[0]
            c.execute("insert into policy.meeting (event_id, kind, is_rate_decision) values (%s, %s, %s)",
                      (e, "scheduled" if decision else "special", decision))
        return e

    def reactions(self):
        return {(slug.removeprefix(self.p + "-"), key.removeprefix(self.p + ".")): rest for slug, key, *rest in q(
            """select e.slug, s.key, r.pre_obs_date, r.post_obs_date, r.pre_value, r.post_value, r.change,
                      r.change_unit, r.is_surprise_measure
                 from policy.market_reaction r join core.event e on e.id = r.meeting_event_id
                 join core.series s on s.id = r.series_id where s.key like %s""", self.p + ".%")}


def last_run():
    return q("select status, rows_written, checks from ops.task_run where task_key = %s order by id desc limit 1",
             KEY)[0]


def go(task, now=NOW):
    return run(JOB, "daily", [task], force=True, clock=lambda: now)


def test_change_units():
    assert change_of("percent", Decimal("4.21"), Decimal("4.05")) == (Decimal("-16.00"), "bps")
    assert change_of("index_points", Decimal(24000), Decimal(24240)) == (Decimal("1.0000"), "pct")
    assert change_of("inr_per_usd", Decimal("83.00"), Decimal("83.05")) == (Decimal("0.0602"), "pct")


@pytest.mark.db
def test_reactions_pick_the_observations_either_side_of_each_release():
    w = World()
    w.fill(date(2020, 2, 24), date(2020, 3, 27))
    w.fill(date(2024, 9, 2), date(2024, 10, 18))
    w.meeting(w.fed, "fomc-2024-09-18", "2024-09-18 14:00 America/New_York")
    w.meeting(w.rbi, "mpc-2024-10-09", "2024-10-09 10:00 Asia/Kolkata")
    w.meeting(w.fed, "fomc-2020-03-15", "2020-03-15 17:00 America/New_York")      # unscheduled, a Sunday
    w.meeting(w.rbi, "mpc-special", "2024-09-25 10:00 Asia/Kolkata", decision=False)  # no decision: no rows
    w.meeting(w.fed, "fomc-2024-11-07", "2024-11-07 14:00 America/New_York", status="scheduled")
    assert go(w.task()) == 0
    status, written, _ = last_run()
    r = w.reactions()
    assert status == "success" and written == 4 + 4 + 2

    assert {m for m, _ in r} == {"fomc-2024-09-18", "mpc-2024-10-09", "fomc-2020-03-15"}
    fed = {s: tuple(v[:2]) for (m, s), v in r.items() if m == "fomc-2024-09-18"}
    # Fed at 14:00 New York: DGS2 (15:30) and the S&P 500 (16:00) change from the day before to the decision day;
    # DEXINUS (noon New York) is the next day's; the Nifty (15:30 India) the next Indian session's (Spec 01 §5.2).
    assert fed == {"DGS2": (date(2024, 9, 17), date(2024, 9, 18)), "^GSPC": (date(2024, 9, 17), date(2024, 9, 18)),
                   "DEXINUS": (date(2024, 9, 18), date(2024, 9, 19)), "^NSEI": (date(2024, 9, 18), date(2024, 9, 19))}
    # RBI at 10:00 India: the Nifty and DEXINUS from the day before to the decision day.
    rbi = {s: tuple(v[:2]) for (m, s), v in r.items() if m == "mpc-2024-10-09"}
    assert rbi == {"^NSEI": (date(2024, 10, 8), date(2024, 10, 9)), "DEXINUS": (date(2024, 10, 8), date(2024, 10, 9))}
    # A Sunday-evening release: Friday's values before, Monday's after.
    sunday = {s: tuple(v[:2]) for (m, s), v in r.items() if m == "fomc-2020-03-15"}
    assert set(sunday.values()) == {(date(2020, 3, 13), date(2020, 3, 16))}

    dgs2 = r[("fomc-2024-09-18", "DGS2")]
    assert dgs2[2:] == [Decimal("3.61"), Decimal("3.62"), Decimal("1.00"), "bps", True]   # 0.01 percentage point
    nsei = r[("fomc-2024-09-18", "^NSEI")]
    assert nsei[4] == ((nsei[3] / nsei[2] - 1) * 100).quantize(Decimal("0.0001")) and nsei[5] == "pct"
    assert sorted((m, s) for (m, s), v in r.items() if v[6]) == [("fomc-2020-03-15", "DGS2"), ("fomc-2024-09-18", "DGS2")]
    # visitors read them through the api view
    with psycopg.connect(ADMIN) as c:
        c.execute("set role anon")
        n = c.execute("select count(*) from api.market_reactions where series_key like %s", (w.p + ".%",)).fetchone()[0]
    assert n == 10


@pytest.mark.db
def test_rerun_writes_nothing_and_a_revision_updates_its_row():
    w = World()
    w.fill(date(2024, 9, 2), date(2024, 9, 30))
    w.meeting(w.fed, "fomc-2024-09-18", "2024-09-18 14:00 America/New_York")
    assert go(w.task()) == 0 and last_run()[1] == 4
    assert go(w.task()) == 0 and last_run()[:2] == ("success", 0)          # nothing changed, nothing written
    w.observe("DGS2", {date(2024, 9, 18): "3.53"})                          # a revised input
    assert go(w.task()) == 0 and last_run()[1] == 1
    assert w.reactions()[("fomc-2024-09-18", "DGS2")][3:5] == [Decimal("3.53"), Decimal("-8.00")]


@pytest.mark.db
def test_a_missing_post_value_waits_then_warns():
    w = World()
    w.fill(date(2024, 9, 2), date(2024, 10, 8))                             # nothing after 8 Oct
    w.meeting(w.rbi, "mpc-2024-10-09", "2024-10-09 10:00 Asia/Kolkata")
    assert go(w.task(), now=datetime(2024, 10, 11, 12, tzinfo=UTC)) == 0   # within the lag: waits quietly
    _, written, checks = last_run()
    assert written == 0 and next(c for c in checks if c["name"] == "waiting")["passed"] is True
    assert go(w.task()) == 0                                               # 20 Oct: the 5-day lag has passed
    checks = last_run()[2]
    waiting = next(c for c in checks if c["name"] == "waiting")
    assert waiting["passed"] is False and waiting["blocking"] is False
    assert "^NSEI" in waiting["detail"] and "DEXINUS" not in waiting["detail"]   # DEXINUS waits 12 days
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"check:{KEY}:waiting")[0][0] == 1
    w.fill(date(2024, 9, 2), date(2024, 10, 18))                            # the closes arrive
    assert go(w.task()) == 0 and last_run()[1] == 2
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"check:{KEY}:waiting")[0][0] == 0


@pytest.mark.db
def test_a_us_2y_move_beyond_100_bps_blocks_the_load():
    w = World()
    w.fill(date(2024, 9, 2), date(2024, 9, 30))
    w.observe("DGS2", {date(2024, 9, 18): "4.70"})                          # +109 bps in a day
    w.meeting(w.fed, "fomc-2024-09-18", "2024-09-18 14:00 America/New_York")
    assert go(w.task()) == 1
    status, written, checks = last_run()
    assert status == "checks_failed" and written == 0
    assert "+109.00 bps" in next(c for c in checks if c["name"] == "us_2y_move")["detail"]
    assert w.reactions() == {}                                             # rolled back


@pytest.mark.db
def test_a_series_without_a_fixing_time_blocks_the_load():
    w = World(fixing=False)
    w.fill(date(2024, 9, 2), date(2024, 9, 30))
    w.meeting(w.fed, "fomc-2024-09-18", "2024-09-18 14:00 America/New_York")
    assert go(w.task()) == 1
    status, _, checks = last_run()
    assert status == "checks_failed"
    assert "^NSEI has no fixing time and zone" in checks[0]["detail"]
    assert w.reactions() == {}
