"""The reactions engine (ft/engines/reactions.py; 2A-22): windows, anchors and subjects on constructed data. The
computation is pure, so most tests need no database; the company subject reads equity.price_adjusted."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import psycopg
import pytest
from conftest import dsn

from ft.engines.reactions import (
    D1,
    D5,
    SPAN,
    Anchor,
    Subject,
    Window,
    change_of,
    company_fixing,
    load_company_subject,
    load_series_subject,
    react,
    series_fixing,
)

IST, NY = ZoneInfo("Asia/Kolkata"), ZoneInfo("America/New_York")
ADMIN, JOB = dsn("postgres"), dsn("job_writer")


def at(y, m, d, hh, mm, zone=IST) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=zone)


def nifty(values: dict[date, str]) -> Subject:
    return Subject("nifty", "index_points", [(d, D(v)) for d, v in values.items()],
                   series_fixing(time(15, 30), "Asia/Kolkata"), series_id=1)


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# Mon 7 Oct 2024 to Fri 25 Oct 2024: values 100, 101, 102 ...
DAYS = weekdays(date(2024, 10, 7), 15)
PATH = {d: str(100 + i) for i, d in enumerate(DAYS)}


def test_change_units():
    assert change_of("percent", D("4.21"), D("4.05")) == (D("-16.00"), "bps")
    assert change_of("index_points", D(24000), D(24240)) == (D("1.0000"), "pct")


def test_d1_reads_the_close_before_and_the_first_close_after():
    s = nifty(PATH)
    # 10:00 India on Wed 9 Oct: the day's own close is after the release
    o = react(s, Anchor.decision(7, at(2024, 10, 9, 10, 0)), D1)
    x = o.reaction
    assert (x.pre_date, x.post_date, x.pre_value, x.post_value) == (date(2024, 10, 8), date(2024, 10, 9), D(101), D(102))
    assert (x.change, x.change_unit, x.window_key, x.value_basis, x.event_id) == (D("0.9901"), "pct", "d1", "observed", 7)
    # 23:30 India the same day (a Fed decision): the next session's close
    x = react(s, Anchor.decision(7, at(2024, 10, 9, 23, 30)), D1).reaction
    assert (x.pre_date, x.post_date) == (date(2024, 10, 9), date(2024, 10, 10))
    # exactly at the fixing instant: that value is neither before nor after
    x = react(s, Anchor.decision(7, at(2024, 10, 9, 15, 30)), D1).reaction
    assert (x.pre_date, x.post_date) == (date(2024, 10, 8), date(2024, 10, 10))


def test_d5_is_the_fifth_value_after_the_end():
    s = nifty(PATH)
    x = react(s, Anchor.decision(1, at(2024, 10, 9, 10, 0)), D5).reaction
    assert (x.pre_date, x.post_date, x.window_key) == (date(2024, 10, 8), date(2024, 10, 15), "d5")   # 9, 10, 11, 14, 15
    assert x.change == ((D(106) / D(101) - 1) * 100).quantize(D("0.0001"))
    # the fifth value after must exist
    assert react(s, Anchor.decision(1, at(2024, 10, 22, 10, 0)), D5).missing == "no_post"


def test_span_is_the_first_value_after_the_end_or_the_latest_while_ongoing():
    s = nifty(PATH)
    start, end = at(2024, 10, 8, 10, 0), at(2024, 10, 10, 10, 0)
    x = react(s, Anchor(3, start, end), SPAN).reaction
    assert (x.pre_date, x.post_date, x.window_key) == (date(2024, 10, 7), date(2024, 10, 10), "span")
    x = react(s, Anchor(3, start, end, ongoing=True), SPAN).reaction
    assert (x.pre_date, x.post_date, x.post_value) == (date(2024, 10, 7), DAYS[-1], D(114))
    # an ongoing cycle with nothing after its last decision has no span yet
    assert react(s, Anchor(3, start, at(2024, 11, 30, 10, 0), ongoing=True), SPAN).missing == "no_post"


def test_no_value_in_the_search_window_is_no_row():
    s = nifty(PATH)
    assert react(s, Anchor.decision(1, at(2024, 10, 7, 10, 0)), D1).missing == "no_pre"          # nothing before 7 Oct
    assert react(s, Anchor.decision(1, at(2024, 11, 10, 10, 0)), D1).missing == "no_post"        # nothing after 25 Oct
    assert react(s, Anchor.decision(1, at(2024, 12, 1, 10, 0)), D1).missing == "no_pre"          # the last value is 37 days back
    assert react(Subject("empty", "index_points", [], series_fixing(time(15, 30), "Asia/Kolkata"), series_id=1),
                 Anchor.decision(1, at(2024, 10, 9, 10, 0)), D1).missing == "no_pre"


def test_a_pre_value_must_be_within_the_search_days():
    s = nifty({date(2024, 10, 1): "100", date(2024, 11, 5): "110"})
    # the release on 2 Nov is 32 days after 1 Oct
    assert react(s, Anchor.decision(1, at(2024, 11, 2, 10, 0)), D1, search_days=31).missing == "no_pre"
    assert react(s, Anchor.decision(1, at(2024, 11, 2, 10, 0)), D1, search_days=32).reaction is not None


def test_a_null_value_gives_no_row_and_is_reported_as_held():
    vals = [(d, None if d <= date(2024, 10, 8) else D(v)) for d, v in PATH.items()]
    s = Subject("held", "price", vals, series_fixing(time(15, 30), "Asia/Kolkata"), entity_id=1)
    assert react(s, Anchor.decision(1, at(2024, 10, 9, 10, 0)), D1).missing == "held"
    assert react(s, Anchor.decision(1, at(2024, 10, 10, 10, 0)), D1).reaction.pre_value == D(102)


def test_a_subject_is_a_series_or_a_company_not_both():
    with pytest.raises(ValueError):
        Subject("x", "price", [], series_fixing(time(15, 30), "Asia/Kolkata"))
    with pytest.raises(ValueError):
        Subject("x", "price", [], series_fixing(time(15, 30), "Asia/Kolkata"), series_id=1, entity_id=2)
    with pytest.raises(ValueError):
        Window("bad", 0)


def test_new_york_series_and_a_dst_change_use_each_dates_own_offset():
    # S&P fixes 16:00 New York; on 1 Nov 2024 New York is still on daylight time, on 4 Nov it's standard time (DST ended 3 Nov)
    s = Subject("spx", "index_points", [(date(2024, 11, 1), D(100)), (date(2024, 11, 4), D(101))],
                series_fixing(time(16, 0), "America/New_York"), series_id=1)
    assert s.instants[0].utcoffset() == timedelta(hours=-4) and s.instants[1].utcoffset() == timedelta(hours=-5)
    x = react(s, Anchor.decision(1, at(2024, 11, 3, 20, 0, NY)), D1).reaction          # a Sunday-evening release
    assert (x.pre_date, x.post_date) == (date(2024, 11, 1), date(2024, 11, 4))


def test_a_date_with_no_session_is_dropped_and_counted():
    def fixing(d):
        if d.weekday() >= 5:
            raise ValueError("closed")
        return datetime.combine(d, time(15, 30), tzinfo=IST)
    s = Subject("x", "price", [(date(2024, 10, 11), D(1)), (date(2024, 10, 12), D(2)), (date(2024, 10, 14), D(3))],
                fixing, entity_id=1)
    assert len(s) == 2 and s.dropped == 1


# ---------------------------------------------------------------------------------------- the database loaders
def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zre-%')"
        c.execute(f"delete from equity.adjustment_factor where entity_id in {ids}")
        c.execute(f"delete from equity.corporate_action where event_id in (select id from core.event where entity_id in {ids})")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.price_daily where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zre-%'")


@pytest.fixture
def world():
    wipe()
    with psycopg.connect(ADMIN, autocommit=True) as c:
        job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('daily', 'cli', 'local') "
                        "returning id").fetchone()[0]
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values ('test.reactions_engine', 'test', 'engine test', '{daily}', '20 hours', '4 days', false)
                     on conflict (key) do nothing""")
        tr = c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, 'test.reactions_engine', "
                       "'success') returning id", (job,)).fetchone()[0]
        raw = c.execute("""insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at, task_run_id)
                           values ('test/zre-prices', (select id from core.source where key = 'nse'),
                                   sha256('zre-prices'::bytea), 0, 'text/csv', now(), %s)
                           on conflict (object_key) do update set bytes = 0 returning id""", (tr,)).fetchone()[0]
    yield tr, raw
    wipe()


def company(name: str, prices: dict[date, str], tr, raw) -> int:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company', %s, %s, 'IN', 'INR', true) returning id""",
                        (f"zre-{name}", f"{name} test")).fetchone()[0]
        c.execute("insert into equity.company (entity_id, nse_symbol, price_exchange) values (%s, %s, 'NSE')",
                  (eid, name.upper()))
        for d, v in prices.items():
            c.execute("""insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close,
                                                        prev_close, raw_file_id, task_run_id)
                         values (%s, %s, 'NSE', 'EQ', %s, %s, %s, %s, %s, %s, %s)""",
                      (eid, d, D(v), D(v), D(v), D(v), D(v), raw, tr))
    return eid


def factor(eid: int, ex: date, status: str, value: str | None) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, is_published)
                          values ('corporate_action', %s, %s, 'split', %s, 'effective', true) returning id""",
                       (eid, f"zre-ca-{eid}-{ex}", ex)).fetchone()[0]
        c.execute("""insert into equity.corporate_action (event_id, kind, ex_date, subject, exchange)
                     values (%s, 'split', %s, 'split', 'NSE')""", (ev, ex))
        c.execute("""insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method, status)
                     values (%s, %s, %s, %s, 'ratio', %s)""", (ev, eid, ex, value, status))


def test_the_company_calendar_close_is_1530_or_a_special_sessions_own():
    f = company_fixing()
    assert f(date(2019, 10, 25)) == datetime(2019, 10, 25, 15, 30, tzinfo=IST)
    assert f(date(2019, 10, 27)) == datetime(2019, 10, 27, 19, 15, tzinfo=IST)        # Muhurat (a Sunday)
    with pytest.raises(ValueError):
        f(date(2019, 10, 26))                                                           # a Saturday: no session


@pytest.mark.db
def test_a_company_reads_the_adjusted_close_at_the_sessions_close(world):
    tr, raw = world
    eid = company("aaa", {date(2019, 10, 24): "200", date(2019, 10, 25): "210", date(2019, 10, 27): "220",
                          date(2019, 10, 28): "230", date(2019, 10, 29): "120"}, tr, raw)
    factor(eid, date(2019, 10, 29), "applied", "0.5")            # a 2:1 split on 29 Oct: earlier closes halve
    with psycopg.connect(JOB) as tx:
        s = load_company_subject(tx, eid, date(2019, 10, 1), date(2019, 11, 30))
    assert s.entity_id == eid and s.series_id is None and s.value_basis == "adjusted_close" and len(s) == 4
    assert s.dropped == 1                                         # 28 Oct 2019 (Diwali Balipratipada) was an NSE holiday: no session
    assert s.vals == [D("100.0"), D("105.0"), D("110.0"), D(120)]
    # 19:00 on Muhurat Sunday: before that session's 19:15 close, so it is the first close after (a 15:30 close would be before)
    x = react(s, Anchor.decision(1, at(2019, 10, 27, 19, 0)), D1).reaction
    assert (x.pre_date, x.post_date, x.value_basis, x.entity_id) == (date(2019, 10, 25), date(2019, 10, 27), "adjusted_close", eid)
    assert x.change == ((D("110.0") / D("105.0") - 1) * 100).quantize(D("0.0001")) and x.change_unit == "pct"
    # 19:30 the same evening: the next session (the 28th was a holiday)
    x = react(s, Anchor.decision(1, at(2019, 10, 27, 19, 30)), D1).reaction
    assert (x.pre_date, x.post_date) == (date(2019, 10, 27), date(2019, 10, 29))


@pytest.mark.db
def test_a_held_factor_gives_no_row_for_the_prices_before_it(world):
    tr, raw = world
    eid = company("bbb", {date(2024, 10, 8): "100", date(2024, 10, 9): "101", date(2024, 10, 10): "102",
                          date(2024, 10, 11): "103"}, tr, raw)
    factor(eid, date(2024, 10, 11), "held", None)               # an unreviewed action on 11 Oct: earlier adj_close is null
    with psycopg.connect(JOB) as tx:
        s = load_company_subject(tx, eid, date(2024, 9, 1), date(2024, 11, 30))
    assert s.vals == [None, None, None, D(103)]
    assert react(s, Anchor.decision(1, at(2024, 10, 9, 10, 0)), D1).missing == "held"


@pytest.mark.db
def test_a_series_is_read_from_core_observation_at_its_fixing_time(world):
    tr, _ = world
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into core.series (key, name, source_id, source_code, unit, frequency, fixing_time, fixing_tz,
                                              is_published)
                     select 'zre.series', 'zre', id, 'ZRE', 'index_points', 'D', '16:00', 'America/New_York', true
                       from core.source where key = 'yfinance'""")
        sid = c.execute("select id from core.series where key = 'zre.series'").fetchone()[0]
        for d, v in ((date(2024, 10, 8), 10), (date(2024, 10, 9), 11)):
            c.execute("insert into core.observation (series_id, obs_date, value, task_run_id) values (%s, %s, %s, %s)",
                      (sid, d, v, tr))
    try:
        with psycopg.connect(JOB) as tx:
            s = load_series_subject(tx, sid, date(2024, 10, 1), date(2024, 10, 31))
        assert s.key == "zre.series" and s.series_id == sid and s.instants[0] == datetime(2024, 10, 8, 16, 0, tzinfo=NY)
        x = react(s, Anchor.decision(5, at(2024, 10, 9, 14, 0, NY)), D1).reaction
        assert (x.pre_date, x.post_date, x.series_id, x.change) == (date(2024, 10, 8), date(2024, 10, 9), sid, D("10.0000"))
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute("delete from core.observation where series_id = %s", (sid,))
            c.execute("delete from core.series where id = %s", (sid,))
