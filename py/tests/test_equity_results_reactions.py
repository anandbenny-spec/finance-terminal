"""equity.results_reactions through the runner, against the test database as job_writer (job 2B-16; Spec 02B §5.12).

Reuses the rate-reactions tests' World (a Nifty-like series, companies, prices, dividends, factors) and adds results_filing
events. January and February 2025 weekdays are all NSE sessions (Wed 15 Jan; Fri 17 Jan then Mon 20 Jan)."""
from datetime import date

import psycopg
import pytest
from test_equity_rate_reactions import ADMIN, D, World, check, go, pct, q, wipe

from ft.jobs.tasks.equity_results_reactions import EquityResultsReactions

KEY = "test.rate_reactions"           # the World's own task key (its ops.task row exists, disabled)


@pytest.fixture(scope="module", autouse=True)
def clean_up():
    wipe()
    yield
    wipe()


def task_for(w: World) -> EquityResultsReactions:
    class T(EquityResultsReactions):
        key = KEY
        index_key = w.nifty
        slug_prefix = w.p

    t = T()
    t.workflows = ("weekly",)
    return t


def release(w: World, company: str, slug: str, at: str, status="filed") -> int:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status,
                                                   is_published)
                            select 'results_filing', id, %s, %s, %s::timestamptz::date, %s, %s, true
                              from core.entity where slug = %s returning id""",
                         (f"{w.p}-{slug}", slug, at, at, status, f"{w.p}-{company}")).fetchone()[0]


def last_run():
    return q("select status, rows_written, checks from ops.task_run where task_key = %s order by id desc limit 1", KEY)[0]


def setup() -> World:
    w = World()
    w.index(D(2025, 1, 6), D(2025, 2, 21))
    w.company("aaa", D(2025, 1, 6), D(2025, 2, 21))
    return w


@pytest.mark.db
def test_an_evening_an_in_hours_and_a_weekend_release_read_the_right_closes():
    w = setup()
    release(w, "aaa", "res-evening", "2025-01-15 19:00 Asia/Kolkata")     # after the close: next session is the 16th
    release(w, "aaa", "res-hours", "2025-01-22 11:00 Asia/Kolkata")       # in hours: that day's close is the first after
    release(w, "aaa", "res-friday", "2025-01-17 20:00 Asia/Kolkata")      # Friday evening: Monday the 20th
    assert go(task_for(w)) == 12                                           # 3 events × d1, d5 × (company + Nifty)
    r = w.rows("aaa")
    assert r[("aaa", "res-evening", "d1")][:2] == [D(2025, 1, 15), D(2025, 1, 16)]
    assert r[("aaa", "res-evening", "d5")][:2] == [D(2025, 1, 15), D(2025, 1, 22)]
    assert r[("aaa", "res-hours", "d1")][:2] == [D(2025, 1, 21), D(2025, 1, 22)]
    assert r[("aaa", "res-friday", "d1")][:2] == [D(2025, 1, 17), D(2025, 1, 20)]
    assert r[("aaa", "res-friday", "d5")][:2] == [D(2025, 1, 17), D(2025, 1, 24)]
    # 6 Jan is day 0, price 100 + day: the 15th is day 7 (107), the 16th day 8 (108)
    _pre, _post, pv, qv, change, unit, basis, source = r[("aaa", "res-evening", "d1")]
    assert (pv, qv, change, unit, basis, source) == (107, 108, pct(107, 108), "pct", "adjusted_close", "nse")
    n = w.rows(w.nifty.removeprefix(w.p + "."))[(w.nifty.removeprefix(w.p + "."), "res-friday", "d1")]
    assert n[:2] == [D(2025, 1, 17), D(2025, 1, 20)] and n[6] == "observed" and n[7] == "yfinance"
    detail = check(last_run()[2], "counts")["detail"]
    assert "3 results filings in the window; 3 with both subjects' d1 and d5 rows" in detail


@pytest.mark.db
def test_a_rerun_writes_nothing_and_filings_before_the_window_start_or_of_other_types_have_no_rows():
    w = setup()
    release(w, "aaa", "res-one", "2025-01-15 19:00 Asia/Kolkata")
    release(w, "aaa", "res-old", "2024-12-10 19:00 Asia/Kolkata")
    release(w, "aaa", "res-revised", "2025-01-20 19:00 Asia/Kolkata", status="superseded")
    go(task_for(w))
    assert last_run()[1] == 8
    before = w.rows()
    assert {k[1] for k in before} == {"res-one", "res-revised"}
    assert go(task_for(w)) == 0 and last_run()[1] == 0
    assert w.rows() == before


@pytest.mark.db
def test_a_dividend_in_the_window_leaves_that_window_out_and_counts_it():
    w = setup()
    eid = q("select id from core.entity where slug = %s", f"{w.p}-aaa")[0][0]
    release(w, "aaa", "res-div", "2025-01-15 19:00 Asia/Kolkata")
    w.dividend(eid, D(2025, 1, 20))                  # inside d5 (15 → 22) but outside d1 (15 → 16)
    go(task_for(w))
    r = w.rows("aaa")
    assert ("aaa", "res-div", "d1") in r and ("aaa", "res-div", "d5") not in r
    detail = check(last_run()[2], "exclusions")["detail"]
    assert "1 windows left out (dividend 1)" in detail
    assert "1 results filings in the window; 0 with both" in check(last_run()[2], "counts")["detail"]


@pytest.mark.db
def test_a_held_factor_gives_no_row_and_a_window_waiting_for_its_close_is_not_an_exclusion():
    w = setup()
    eid = q("select id from core.entity where slug = %s", f"{w.p}-aaa")[0][0]
    w.factor(eid, D(2025, 1, 17), "held", None)       # earlier adjusted closes are null
    release(w, "aaa", "res-held", "2025-01-15 19:00 Asia/Kolkata")
    release(w, "aaa", "res-late", "2025-02-19 19:00 Asia/Kolkata")     # prices end on the 21st: d5 needs the 26th
    go(task_for(w))
    r = w.rows("aaa")
    assert not [k for k in r if k[1] == "res-held"]
    assert ("aaa", "res-late", "d1") in r and ("aaa", "res-late", "d5") not in r
    checks = last_run()[2]
    assert "held_factor 2" in check(checks, "exclusions")["detail"]
    assert "windows wait for a close" in check(checks, "counts")["detail"]
    assert check(checks, "series")["passed"] and check(checks, "observations")["passed"]


@pytest.mark.db
def test_a_missing_nifty_series_fails_the_blocking_check_and_writes_nothing():
    w = setup()
    release(w, "aaa", "res-x", "2025-01-15 19:00 Asia/Kolkata")
    t = task_for(w)
    t.index_key = "no.such.series"
    go(t)
    status, written, checks = last_run()
    assert status == "checks_failed" and written == 0
    assert not check(checks, "series")["passed"]
    assert w.rows() == {}


def test_the_window_start_is_one_named_constant():
    from ft.jobs.tasks import equity_results_reactions as m
    assert m.WINDOW_START == date(2025, 1, 1)
