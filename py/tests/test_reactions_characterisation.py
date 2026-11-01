"""Characterisation of policy.reactions (2A-22, build step 1): exact stored rows for constructed data, written before the
task was moved onto the reactions engine and asserting what it wrote before. They must pass unchanged after the refactor
(Spec 02A §5.10: module 1's rows are identical before and after). Values are irregular on purpose so that picking the wrong
observation changes the number."""
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from test_reactions_task import World, go, last_run

NOW = datetime(2024, 10, 20, 12, 0, tzinfo=UTC)


def build() -> World:
    w = World()
    # DGS2 (15:30 NY, percent -> bps), S&P (16:00 NY, index -> %), DEXINUS (noon NY), Nifty (15:30 India)
    w.observe("DGS2", {date(2024, 9, 16): "3.70", date(2024, 9, 17): "3.61", date(2024, 9, 18): "3.55",
                       date(2024, 9, 19): "3.52", date(2024, 9, 20): "3.57",
                       date(2024, 3, 19): "4.65", date(2024, 3, 20): "4.60", date(2024, 3, 21): "4.52"})
    w.observe("^GSPC", {date(2024, 9, 17): "5634.58", date(2024, 9, 18): "5618.26", date(2024, 9, 19): "5713.64",
                        date(2024, 3, 19): "5178.51", date(2024, 3, 20): "5224.62", date(2024, 3, 21): "5241.53"})
    w.observe("DEXINUS", {date(2024, 9, 17): "83.80", date(2024, 9, 18): "83.75", date(2024, 9, 19): "83.60",
                          date(2024, 10, 8): "84.07", date(2024, 10, 9): "84.04", date(2024, 10, 10): "84.04"})
    w.observe("^NSEI", {date(2024, 9, 17): "25418.55", date(2024, 9, 18): "25377.55", date(2024, 9, 19): "25415.80",
                        date(2024, 10, 8): "24981.95", date(2024, 10, 9): "24998.45", date(2024, 10, 10): "25014.60",
                        date(2020, 3, 13): "9955.20", date(2020, 3, 16): "8745.45", date(2020, 3, 17): "9029.20"})
    w.observe("DGS2", {date(2020, 3, 13): "0.51", date(2020, 3, 16): "0.35"})
    w.observe("^GSPC", {date(2020, 3, 13): "2711.02", date(2020, 3, 16): "2386.13"})
    w.observe("DEXINUS", {date(2020, 3, 13): "74.30", date(2020, 3, 16): "74.20"})
    # A Fed release at exactly the DGS2 fixing time (15:30 NY) on 20 Mar 2024: that day's DGS2 is neither before nor after
    # (pre = 19 Mar, post = 21 Mar); the S&P (16:00) is after (post = 20 Mar).
    w.meeting(w.fed, "fomc-2024-03-20", "2024-03-20 15:30 America/New_York")
    w.meeting(w.fed, "fomc-2024-09-18", "2024-09-18 14:00 America/New_York")
    w.meeting(w.rbi, "mpc-2024-10-09", "2024-10-09 10:00 Asia/Kolkata")
    w.meeting(w.fed, "fomc-2020-03-15", "2020-03-15 17:00 America/New_York")           # a Sunday
    w.meeting(w.rbi, "mpc-2024-06-07", "2024-06-07 10:00 Asia/Kolkata")                # no observations near it
    return w


EXPECTED = {
    ("fomc-2024-09-18", "DGS2"): [date(2024, 9, 17), date(2024, 9, 18), D("3.61"), D("3.55"), D("-6.00"), "bps", True],
    ("fomc-2024-09-18", "^GSPC"): [date(2024, 9, 17), date(2024, 9, 18), D("5634.58"), D("5618.26"), D("-0.2896"), "pct", False],
    ("fomc-2024-09-18", "DEXINUS"): [date(2024, 9, 18), date(2024, 9, 19), D("83.75"), D("83.60"), D("-0.1791"), "pct", False],
    ("fomc-2024-09-18", "^NSEI"): [date(2024, 9, 18), date(2024, 9, 19), D("25377.55"), D("25415.80"), D("0.1507"), "pct", False],
    ("mpc-2024-10-09", "^NSEI"): [date(2024, 10, 8), date(2024, 10, 9), D("24981.95"), D("24998.45"), D("0.0660"), "pct", False],
    ("mpc-2024-10-09", "DEXINUS"): [date(2024, 10, 8), date(2024, 10, 9), D("84.07"), D("84.04"), D("-0.0357"), "pct", False],
    ("fomc-2020-03-15", "DGS2"): [date(2020, 3, 13), date(2020, 3, 16), D("0.51"), D("0.35"), D("-16.00"), "bps", True],
    ("fomc-2020-03-15", "^GSPC"): [date(2020, 3, 13), date(2020, 3, 16), D("2711.02"), D("2386.13"), D("-11.9841"), "pct", False],
    ("fomc-2020-03-15", "DEXINUS"): [date(2020, 3, 13), date(2020, 3, 16), D("74.30"), D("74.20"), D("-0.1346"), "pct", False],
    ("fomc-2020-03-15", "^NSEI"): [date(2020, 3, 13), date(2020, 3, 16), D("9955.20"), D("8745.45"), D("-12.1519"), "pct", False],
    ("fomc-2024-03-20", "DGS2"): [date(2024, 3, 19), date(2024, 3, 21), D("4.65"), D("4.52"), D("-13.00"), "bps", True],
    ("fomc-2024-03-20", "^GSPC"): [date(2024, 3, 19), date(2024, 3, 20), D("5178.51"), D("5224.62"), D("0.8904"), "pct", False],
}


@pytest.mark.db
def test_exact_rows_for_constructed_data():
    w = build()
    assert go(w.task()) == 0
    status, written, _ = last_run()
    assert status == "success"
    assert w.reactions() == EXPECTED
    assert written == len(EXPECTED)


@pytest.mark.db
def test_checks_and_warnings_for_the_same_data():
    w = build()
    go(w.task())
    checks = {c["name"]: c for c in last_run()[2]}
    assert checks["us_2y_move"]["passed"] and checks["us_2y_move"]["detail"] == "3 within ±100 bps"
    assert checks["series"]["passed"]
    waiting = checks["waiting"]
    assert waiting["passed"] is False and waiting["blocking"] is False
    # 2024-06-07 has nothing within 31 days before it (no pre) for both RBI series; the others with a pre but no post wait.
    assert "2024-06-07 " in waiting["detail"] and "no value in the 31 days before the release" in waiting["detail"]
    assert checks["reactions"]["detail"] == f"{len(EXPECTED)} stored ({len(EXPECTED)} inserted, 0 updated, 0 deleted)"


@pytest.mark.db
def test_a_revised_value_updates_only_its_row():
    w = build()
    go(w.task())
    w.observe("^NSEI", {date(2024, 9, 18): "25400.00"})                                    # revise the pre value
    go(w.task())
    r = w.reactions()
    assert r[("fomc-2024-09-18", "^NSEI")][2:5] == [D("25400.00"), D("25415.80"), D("0.0622")]
    assert {k: v for k, v in r.items() if k != ("fomc-2024-09-18", "^NSEI")} == \
        {k: v for k, v in EXPECTED.items() if k != ("fomc-2024-09-18", "^NSEI")}
    detail = {c["name"]: c for c in last_run()[2]}["reactions"]["detail"]
    # 2024-09-18 is also the pre value of nothing else: the fed meeting's ^NSEI row only
    assert detail == f"{len(EXPECTED)} stored (0 inserted, 1 updated, 0 deleted)"
