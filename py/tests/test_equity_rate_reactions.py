"""equity.rate_reactions through the runner, against the test database as job_writer (job 2A-24; Spec 02A §5.10).

Each test builds its own banks, Nifty-like series, companies and meetings under a fresh prefix, so the fixture's rows and
other tests are untouched. All dates fall on NSE sessions in January and February 2025 (every weekday is a session)."""
import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from conftest import dsn

from ft.jobs.runner import run
from ft.jobs.tasks.equity_rate_reactions import EquityRateReactions

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
KEY = "test.rate_reactions"
NOW = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
_n = itertools.count(1)
D = date


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        cur = c.execute(sql, a)
        return cur.fetchall() if cur.description else []


def wipe():
    """Remove everything this module made (companies are universe members to other tests: the universe and shareholding
    tests count every equity.company row), leaving the task runs, which other rows of the run history point at."""
    ev = "(select id from core.event where slug like 'rr%x%-%')"
    co = "(select id from core.entity where slug like 'rr%x%-%')"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f"delete from core.event_reaction where event_id in {ev} or entity_id in {co}")
        c.execute(f"delete from equity.adjustment_factor where entity_id in {co}")
        c.execute(f"delete from equity.corporate_action where event_id in {ev}")
        c.execute(f"delete from policy.rate_cycle where event_id in {ev}")
        c.execute(f"delete from policy.meeting where event_id in {ev}")
        c.execute("delete from core.event where slug like 'rr%x%-%'")
        c.execute(f"delete from equity.price_daily where entity_id in {co}")
        c.execute(f"delete from equity.company where entity_id in {co}")
        c.execute("delete from core.entity where slug like 'rr%x%-%'")
        c.execute("delete from core.observation where series_id in (select id from core.series where key like 'rr%x%.%')")
        c.execute("delete from core.series where key like 'rr%x%.%'")
        c.execute("delete from core.raw_file where object_key like 'test/rr%x%-prices'")


@pytest.fixture(scope="module", autouse=True)
def clean_up():
    wipe()
    yield
    wipe()


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


class World:
    """Two test banks, a Nifty-like series, a task run for the observations and prices, and the task pointed at them."""

    def __init__(self):
        p = f"rr{next(_n)}x{datetime.now().strftime('%H%M%S%f')}"
        self.p, self.fed, self.rbi, self.nifty = p, f"{p}-fed", f"{p}-rbi", f"{p}.NSEI"
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'rate reactions test', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (KEY,))
            for slug, mkt, cur in ((self.fed, "US", "USD"), (self.rbi, "IN", "INR")):
                c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                             values ('central_bank', %s, %s, %s, %s, true)""", (slug, slug, mkt, cur))
            c.execute("""insert into core.series (key, name, source_id, source_code, unit, frequency, fixing_time,
                                                  fixing_tz, is_published)
                         select %s, 'test nifty', id, '^NSEI', 'index_points', 'D', '15:30', 'Asia/Kolkata', true
                           from core.source where key = 'yfinance'""", (self.nifty,))
            job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('weekly', 'cli', 'local') "
                            "returning id").fetchone()[0]
            self.tr = c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, %s, 'success') "
                                "returning id", (job, KEY)).fetchone()[0]
            self.raw = c.execute(
                """insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at, task_run_id)
                   values (%s, (select id from core.source where key = 'nse'), sha256(%s::bytea), 0, 'text/csv', now(), %s)
                   returning id""", (f"test/{p}-prices", p, self.tr)).fetchone()[0]
        self.series_id = q("select id from core.series where key = %s", self.nifty)[0][0]

    def task(self, index_key: str | None = None) -> EquityRateReactions:
        w, series = self, index_key or self.nifty

        class T(EquityRateReactions):
            key = KEY
            banks = (w.fed, w.rbi)
            slug_prefix = w.p
        T.index_key = series

        t = T()
        t.workflows = ("weekly",)
        return t

    # ------------------------------------------------------------------ data
    def index(self, start: date, end: date, base="24000", step="20", skip=()):
        with psycopg.connect(ADMIN, autocommit=True) as c:
            for i, d in enumerate(weekdays(start, end)):
                if d in skip:
                    continue
                c.execute("""insert into core.observation (series_id, obs_date, value, task_run_id) values (%s,%s,%s,%s)
                             on conflict (series_id, obs_date) do update set value = excluded.value""",
                          (self.series_id, d, Decimal(base) + i * Decimal(step), self.tr))

    def company(self, name: str, start: date, end: date, base="100", step="1", skip=()) -> int:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', true) returning id""",
                            (f"{self.p}-{name}", f"{name} test")).fetchone()[0]
            c.execute("insert into equity.company (entity_id, nse_symbol, price_exchange) values (%s, %s, 'NSE')",
                      (eid, name.upper()))
            for i, d in enumerate(weekdays(start, end)):
                if d in skip:
                    continue
                v = Decimal(base) + i * Decimal(step)
                c.execute("""insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low,
                                                            close, prev_close, raw_file_id, task_run_id)
                             values (%s, %s, 'NSE', 'EQ', %s, %s, %s, %s, %s, %s, %s)""",
                          (eid, d, v, v, v, v, v, self.raw, self.tr))
        return eid

    def more_prices(self, eid: int, day: date, value: str):
        q("""insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close, prev_close,
                                            raw_file_id, task_run_id)
             values (%s, %s, 'NSE', 'EQ', %s, %s, %s, %s, %s, %s, %s)""",
          eid, day, value, value, value, value, value, self.raw, self.tr)

    def meeting(self, bank, slug, occurs_at: str, status="held", decision=True) -> int:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            e = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status,
                                                     is_published)
                             select 'policy_meeting', id, %s, %s, %s::timestamptz::date, %s, %s, true
                               from core.entity where slug = %s returning id""",
                          (f"{self.p}-{slug}", slug, occurs_at, occurs_at, status, bank)).fetchone()[0]
            c.execute("insert into policy.meeting (event_id, kind, is_rate_decision) values (%s, %s, %s)",
                      (e, "scheduled" if decision else "special", decision))
        return e

    def cycle(self, bank, slug, first: int, last: int, status="complete", published=True) -> int:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            e = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, status,
                                                     source_id, is_published)
                             select 'rate_cycle', b.id, %s, %s, f.starts_on, l.starts_on, %s, f.source_id, %s
                               from core.entity b, core.event f, core.event l
                              where b.slug = %s and f.id = %s and l.id = %s returning id""",
                          (f"{self.p}-{slug}", slug, status, published, bank, first, last)).fetchone()[0]
            c.execute("""insert into policy.rate_cycle (event_id, direction, first_meeting_event_id,
                                                        last_meeting_event_id, moves, total_change_bps, rule_version)
                         values (%s, 'easing', %s, %s, 2, -50, 1)""", (e, first, last))
        return e

    def dividend(self, eid: int, ex: date):
        with psycopg.connect(ADMIN, autocommit=True) as c:
            ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, is_published)
                              values ('corporate_action', %s, %s, 'dividend', %s, 'effective', true) returning id""",
                           (eid, f"{self.p}-div-{eid}-{ex}", ex)).fetchone()[0]
            c.execute("""insert into equity.corporate_action (event_id, kind, ex_date, subject, dividend_per_share,
                                                              exchange)
                         values (%s, 'dividend', %s, 'dividend', 5, 'NSE')""", (ev, ex))

    def factor(self, eid: int, ex: date, status: str, value: str | None):
        with psycopg.connect(ADMIN, autocommit=True) as c:
            ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, is_published)
                              values ('corporate_action', %s, %s, 'split', %s, 'effective', true) returning id""",
                           (eid, f"{self.p}-ca-{eid}-{ex}", ex)).fetchone()[0]
            c.execute("""insert into equity.corporate_action (event_id, kind, ex_date, subject, exchange)
                         values (%s, 'split', %s, 'split', 'NSE')""", (ev, ex))
            c.execute("""insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method, status)
                         values (%s, %s, %s, %s, 'ratio', %s)""", (ev, eid, ex, value, status))

    # ------------------------------------------------------------------ reads
    def rows(self, subject: str | None = None):
        """(subject slug or series key, event slug, window) -> (pre, post, pre value, post value, change, unit, basis)."""
        out = {}
        for who, ev, win, *rest in q(
                """select coalesce(n.slug, s.key), e.slug, r.window_key, r.pre_date, r.post_date, r.pre_value,
                          r.post_value, r.change, r.change_unit, r.value_basis, src.key
                     from core.event_reaction r
                     join core.event e on e.id = r.event_id
                     left join core.entity n on n.id = r.entity_id
                     left join core.series s on s.id = r.series_id
                     join core.source src on src.id = r.source_id
                    where e.slug like %s""", self.p + "-%"):
            out[(who.removeprefix(self.p + "-").removeprefix(self.p + "."), ev.removeprefix(self.p + "-"), win)] = rest
        return out if subject is None else {k: v for k, v in out.items() if k[0] == subject}


def last_run():
    return q("select status, rows_written, checks from ops.task_run where task_key = %s order by id desc limit 1",
             KEY)[0]


def go(task, now=NOW):
    return run(JOB, "weekly", [task], force=True, clock=lambda: now)


def check(checks, name):
    return next(c for c in checks if c["name"] == name)


def pct(pre, post) -> Decimal:
    return ((Decimal(post) / Decimal(pre) - 1) * 100).quantize(Decimal("0.0001"))


def standard() -> World:
    """Prices for 6 Jan to 21 Feb 2025; an RBI decision at 10:00 IST on Wed 15 Jan and a Fed decision at 14:00 New York
    on Wed 29 Jan (00:30 IST on the 30th)."""
    w = World()
    w.index(D(2025, 1, 6), D(2025, 2, 21))
    w.company("aaa", D(2025, 1, 6), D(2025, 2, 21))
    w.meeting(w.rbi, "mpc-jan", "2025-01-15 10:00 Asia/Kolkata")
    w.meeting(w.fed, "fomc-jan", "2025-01-29 14:00 America/New_York")
    return w


# ---------------------------------------------------------------------------------------------- windows and values
@pytest.mark.db
def test_the_windows_follow_the_decision_time_and_the_rows_carry_their_basis_and_source():
    w = standard()
    assert go(w.task()) == 0
    status, written, checks = last_run()
    assert status == "success" and written == 8                    # 2 decisions × d1 and d5 × (company + Nifty)
    r = w.rows()
    # RBI at 10:00 IST: pre is the 14th's close, d1 is that day's own close, d5 the fifth close after (21st)
    assert r[("aaa", "mpc-jan", "d1")][:2] == [D(2025, 1, 14), D(2025, 1, 15)]
    assert r[("aaa", "mpc-jan", "d5")][:2] == [D(2025, 1, 14), D(2025, 1, 21)]
    # Fed at 14:00 New York = 00:30 IST on the 30th: pre is the 29th's close, d1 the 30th's (the next Indian session)
    assert r[("aaa", "fomc-jan", "d1")][:2] == [D(2025, 1, 29), D(2025, 1, 30)]
    assert r[("aaa", "fomc-jan", "d5")][:2] == [D(2025, 1, 29), D(2025, 2, 5)]
    # 6 Jan is day 0: 14 Jan is day 6 (price 106), 15 Jan day 7 (107); the Nifty path is 24000 + 20 × day
    _pre, _post, pv, qv, change, unit, basis, source = r[("aaa", "mpc-jan", "d1")]
    assert (pv, qv, change, unit, basis, source) == (Decimal(106), Decimal(107), pct(106, 107), "pct",
                                                     "adjusted_close", "nse")
    n = r[(w.nifty.removeprefix(w.p + "."), "mpc-jan", "d1")]
    assert (n[2], n[3], n[4], n[5], n[6], n[7]) == (Decimal(24120), Decimal(24140), pct(24120, 24140), "pct",
                                                    "observed", "yfinance")
    assert [c["name"] for c in checks] == ["series", "observations", "exclusions", "no_nifty", "excess_pairs", "reactions"]
    assert check(checks, "series")["passed"] and check(checks, "observations")["passed"]
    assert check(checks, "no_nifty")["passed"]
    assert "8 stored (8 inserted" in check(checks, "reactions")["detail"]


@pytest.mark.db
def test_a_rerun_changes_nothing_and_a_decision_that_is_no_decision_or_not_held_or_baseline_has_no_rows():
    w = standard()
    w.meeting(w.rbi, "mpc-special", "2025-01-22 10:00 Asia/Kolkata", decision=False)
    w.meeting(w.fed, "fomc-future", "2025-02-12 14:00 America/New_York", status="scheduled")
    w.meeting(w.fed, "fomc-2018", "2018-12-19 14:00 America/New_York")
    go(w.task())
    assert last_run()[1] == 8
    before = w.rows()
    assert {k[1] for k in before} == {"mpc-jan", "fomc-jan"}
    assert go(w.task()) == 0 and last_run()[1] == 0
    assert w.rows() == before


@pytest.mark.db
def test_a_missing_price_day_shifts_the_company_dates_and_the_pairs_that_differ_from_the_nifty_are_counted():
    w = World()
    w.index(D(2025, 1, 6), D(2025, 2, 21))
    w.company("bbb", D(2025, 1, 6), D(2025, 2, 21), skip=(D(2025, 1, 14),))      # no 14 Jan close
    w.meeting(w.rbi, "mpc-jan", "2025-01-15 10:00 Asia/Kolkata")
    go(w.task())
    r = w.rows("bbb")
    assert r[("bbb", "mpc-jan", "d1")][:2] == [D(2025, 1, 13), D(2025, 1, 15)]          # pre falls back to the 13th
    detail = check(last_run()[2], "excess_pairs")["detail"]
    assert "2 company rows; 2 with Nifty 50 dates that differ" in detail


# ---------------------------------------------------------------------------------------------- exclusions
@pytest.mark.db
def test_an_ex_dividend_date_inside_a_decision_window_excludes_that_pair_only():
    w = standard()
    eid = w.company("div", D(2025, 1, 6), D(2025, 2, 21))
    w.dividend(eid, D(2025, 1, 20))                  # inside the RBI d5 (14 → 21) but outside its d1 (14 → 15)
    go(w.task())
    r = w.rows("div")
    assert ("div", "mpc-jan", "d1") in r and ("div", "mpc-jan", "d5") not in r
    assert ("div", "fomc-jan", "d1") in r and ("div", "fomc-jan", "d5") in r         # the Fed windows (29 → 5 Feb) clear it
    detail = check(last_run()[2], "exclusions")["detail"]
    assert "1 pairs left out (dividend 1)" in detail and not check(last_run()[2], "exclusions")["passed"]
    # the ex-date on the post date itself is inside the window; on the pre date it is not
    q("delete from equity.corporate_action where event_id in (select id from core.event where slug like %s)",
      f"{w.p}-div-%")
    w.dividend(eid, D(2025, 1, 15))
    go(w.task())
    r = w.rows("div")
    assert ("div", "mpc-jan", "d1") not in r and ("div", "mpc-jan", "d5") not in r
    q("delete from equity.corporate_action where event_id in (select id from core.event where slug like %s)",
      f"{w.p}-div-%")
    w.dividend(eid, D(2025, 1, 14))
    go(w.task())
    assert ("div", "mpc-jan", "d1") in w.rows("div")


@pytest.mark.db
def test_a_cycle_span_keeps_a_company_that_paid_a_dividend_in_it():
    w = standard()
    eid = w.company("pay", D(2025, 1, 6), D(2025, 2, 21))
    w.dividend(eid, D(2025, 1, 20))
    first = q("select id from core.event where slug = %s", f"{w.p}-mpc-jan")[0][0]
    last = w.meeting(w.rbi, "mpc-feb", "2025-02-05 10:00 Asia/Kolkata")
    w.cycle(w.rbi, "cycle", first, last)
    go(w.task())
    r = w.rows("pay")
    assert ("pay", "cycle", "span") in r
    assert r[("pay", "cycle", "span")][:2] == [D(2025, 1, 14), D(2025, 2, 5)]
    assert ("pay", "mpc-jan", "d5") not in r                                    # the decision-day pair is excluded


@pytest.mark.db
def test_a_held_factor_inside_a_window_gives_no_row_and_an_applied_one_is_already_in_the_adjusted_close():
    w = standard()
    held = w.company("hld", D(2025, 1, 6), D(2025, 2, 21))
    w.factor(held, D(2025, 1, 17), "held", None)              # earlier closes are null: the RBI windows read them
    split = w.company("spl", D(2025, 1, 6), D(2025, 2, 21))
    w.factor(split, D(2025, 1, 15), "applied", "0.5")         # a 2:1 split on the decision day itself
    go(w.task())
    r = w.rows()
    assert not [k for k in r if k[0] == "hld" and k[1] == "mpc-jan"]
    assert ("hld", "fomc-jan", "d1") in r                      # the Fed windows start after the factor's ex-date
    assert "held_factor" in check(last_run()[2], "exclusions")["detail"]
    # the split halves the earlier closes (106 → 53): the adjusted series is what the window reads
    x = r[("spl", "mpc-jan", "d1")]
    assert (x[2], x[3]) == (Decimal(53), Decimal(107)) and x[4] == pct(53, 107)


@pytest.mark.db
def test_a_company_not_yet_listed_has_no_row_and_a_window_still_waiting_for_its_close_is_not_an_exclusion():
    w = World()
    w.index(D(2025, 1, 6), D(2025, 2, 21))
    w.company("new", D(2025, 1, 16), D(2025, 2, 21))                # first close on the 16th: nothing before the 15th
    w.company("old", D(2025, 1, 6), D(2025, 2, 19))                 # closes end on the 19th
    w.meeting(w.rbi, "mpc-jan", "2025-01-15 10:00 Asia/Kolkata")
    w.meeting(w.rbi, "mpc-feb", "2025-02-14 10:00 Asia/Kolkata")    # d5 needs the fifth close after (20th)
    go(w.task())
    r = w.rows()
    assert not [k for k in r if k[0] == "new" and k[1] == "mpc-jan"]
    assert ("new", "mpc-feb", "d1") in r
    assert ("old", "mpc-feb", "d1") in r and ("old", "mpc-feb", "d5") not in r      # the fifth close after the 14th: 21st
    checks = last_run()[2]
    assert "no_pre 2" in check(checks, "exclusions")["detail"]
    assert "windows wait for a close" in check(checks, "excess_pairs")["detail"]


# ---------------------------------------------------------------------------------------------- the Nifty and cycles
@pytest.mark.db
def test_a_decision_the_nifty_has_no_close_for_is_named_and_the_company_row_still_stands():
    w = World()
    w.index(D(2025, 1, 6), D(2025, 1, 31))
    w.company("ccc", D(2025, 1, 6), D(2025, 2, 21))
    w.meeting(w.rbi, "mpc-feb", "2025-02-14 10:00 Asia/Kolkata")      # the Nifty series ends on 31 Jan: out of range
    go(w.task())
    checks = last_run()[2]
    assert ("ccc", "mpc-feb", "d1") in w.rows("ccc")
    nn = check(checks, "no_nifty")
    assert not nn["passed"] and "mpc-feb d1" in nn["detail"] and "mpc-feb d5" in nn["detail"]
    assert "2 with no Nifty 50 row" in check(checks, "excess_pairs")["detail"]


@pytest.mark.db
def test_an_ongoing_cycle_runs_to_the_latest_close_and_its_row_moves_when_a_newer_close_arrives():
    w = World()
    w.index(D(2025, 1, 6), D(2025, 2, 14))
    eid = w.company("ddd", D(2025, 1, 6), D(2025, 2, 14))
    first = w.meeting(w.rbi, "mpc-jan", "2025-01-15 10:00 Asia/Kolkata")
    last = w.meeting(w.rbi, "mpc-feb", "2025-02-05 10:00 Asia/Kolkata")
    w.cycle(w.rbi, "cycle", first, last, status="ongoing")
    go(w.task())
    r = w.rows("ddd")
    assert r[("ddd", "cycle", "span")][:2] == [D(2025, 1, 14), D(2025, 2, 14)]
    n = w.rows(w.nifty.removeprefix(w.p + "."))[(w.nifty.removeprefix(w.p + "."), "cycle", "span")]
    assert n[:2] == [D(2025, 1, 14), D(2025, 2, 14)]
    # a new close on the 17th moves the ongoing cycle's row (an update, not an insert); the Nifty gets one too
    w.more_prices(eid, D(2025, 2, 17), "150")
    w.index(D(2025, 2, 17), D(2025, 2, 17), base="25000", step="0")
    go(w.task())
    assert last_run()[1] == 2
    assert w.rows("ddd")[("ddd", "cycle", "span")][:2] == [D(2025, 1, 14), D(2025, 2, 17)]
    assert w.rows("ddd")[("ddd", "cycle", "span")][3] == Decimal(150)


@pytest.mark.db
def test_a_complete_cycle_stops_at_the_first_close_after_its_last_move_and_an_unpublished_cycle_loses_its_rows():
    w = World()
    w.index(D(2025, 1, 6), D(2025, 2, 21))
    w.company("eee", D(2025, 1, 6), D(2025, 2, 21))
    first = w.meeting(w.rbi, "mpc-jan", "2025-01-15 10:00 Asia/Kolkata")
    last = w.meeting(w.rbi, "mpc-feb", "2025-02-05 10:00 Asia/Kolkata")
    cycle = w.cycle(w.rbi, "cycle", first, last)
    go(w.task())
    r = w.rows("eee")
    assert r[("eee", "cycle", "span")][:2] == [D(2025, 1, 14), D(2025, 2, 5)]     # the 5th's own close (decision 10:00)
    assert last_run()[1] == 10                                                      # 2 decisions × 2 windows + 1 span, ×2
    q("update core.event set is_published = false where id = %s", cycle)
    go(w.task())
    status, written, checks = last_run()
    assert status == "success" and written == 2                                    # the company's and the Nifty's span rows
    assert ("eee", "cycle", "span") not in w.rows("eee")
    assert "2 deleted" in check(checks, "reactions")["detail"]


@pytest.mark.db
def test_a_missing_or_unfixed_index_series_blocks_the_load():
    w = standard()
    go(w.task("no-such-series"))
    status, written, checks = last_run()
    assert status == "checks_failed" and written == 0
    assert [c["name"] for c in checks if not c["passed"]] == ["series"]
    assert w.rows() == {}
