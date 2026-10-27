"""Trading calendars (2A-03; Spec 02A §4): the curated NSE and NYSE files and ft/calendars.py."""
from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path

import pytest
import yaml

from ft import calendars as cal

NSE = "xnse"
NYSE = "xnys"
BHAV = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "bhavcopy" / "nse"

# The nine sessions Yahoo's ^NSEI missed (Data Audit 00 §6) plus the weekday Muhurat sessions: (date, close hh:mm IST).
SPECIAL = [
    (date(2019, 2, 13), "15:30"),   # a normal Wednesday session in every NSE circular read; Yahoo's gap
    (date(2019, 10, 27), "19:15"),  # Muhurat, Sunday
    (date(2020, 2, 1), "15:30"),    # Budget day, Saturday
    (date(2020, 11, 14), "19:15"),  # Muhurat, Saturday
    (date(2021, 11, 4), "19:15"),   # Muhurat, Thursday (also in the holiday list)
    (date(2022, 10, 24), "19:15"),  # Muhurat, Monday
    (date(2023, 11, 12), "19:15"),  # Muhurat, Sunday
    (date(2024, 1, 20), "12:30"),   # Saturday DR session
    (date(2024, 3, 2), "12:30"),
    (date(2024, 5, 18), "12:30"),
    (date(2024, 11, 1), "19:00"),   # Muhurat, Friday
    (date(2025, 2, 1), "15:30"),    # Budget day, Saturday
    (date(2025, 10, 21), "14:45"),  # Muhurat, afternoon
    (date(2026, 2, 1), "15:30"),    # Budget day, Sunday
]


@pytest.mark.parametrize("d,close", SPECIAL)
def test_special_sessions_are_sessions_with_their_close(d, close):
    assert cal.is_session(NSE, d)
    got = cal.close_time(NSE, d)
    assert got.strftime("%H:%M") == close
    assert str(got.tzinfo) == "Asia/Kolkata"


def test_special_session_kinds():
    assert cal.special_session(NSE, date(2019, 10, 27)) == "muhurat"
    assert cal.special_session(NSE, date(2020, 2, 1)) == "budget"
    assert cal.special_session(NSE, date(2024, 3, 2)) == "dr"
    assert cal.special_session(NSE, date(2024, 3, 4)) is None


def test_muhurat_overrides_the_holiday_entry():
    # 4 Nov 2021, 1 Nov 2024 and 21 Oct 2025 are holidays of the regular session and have an evening session
    for d in (date(2021, 11, 4), date(2024, 11, 1), date(2025, 10, 21)):
        assert cal.is_session(NSE, d)


def test_normal_day_closes_at_1530_ist():
    assert cal.close_time(NSE, date(2024, 7, 1)).strftime("%H:%M") == "15:30"


@pytest.mark.parametrize("year", cal.covered_years(NSE))
def test_republic_day_and_independence_day_are_closed(year):
    for d in (date(year, 1, 26), date(year, 8, 15)):
        assert not cal.is_session(NSE, d)  # weekday holiday or weekend, either way closed


def test_nse_holidays_answered_404_in_y1_are_holidays():
    for d in (date(2026, 5, 28), date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 20)):
        assert d.weekday() < 5
        assert not cal.is_session(NSE, d)


def test_amendments_are_holidays():
    assert not cal.is_session(NSE, date(2026, 1, 15))   # NSE/CMTR/72260
    assert not cal.is_session(NSE, date(2024, 1, 22))
    assert not cal.is_session(NSE, date(2024, 11, 20))
    assert cal.is_session(NSE, date(2023, 6, 28)) and not cal.is_session(NSE, date(2023, 6, 29))


def test_weekends_are_closed_unless_special():
    assert not cal.is_session(NSE, date(2024, 7, 6))
    assert not cal.is_session(NSE, date(2024, 7, 7))


def test_previous_next_and_range():
    assert cal.previous_session(NSE, date(2024, 7, 8)) == date(2024, 7, 5)
    assert cal.next_session(NSE, date(2024, 7, 5)) == date(2024, 7, 8)
    assert cal.next_session(NSE, date(2026, 10, 19)) == date(2026, 10, 21)  # 20 Oct 2026 is Dussehra
    assert cal.sessions(NSE, date(2024, 7, 15), date(2024, 7, 19)) == [
        date(2024, 7, 15), date(2024, 7, 16), date(2024, 7, 18), date(2024, 7, 19)]  # 17 Jul: Moharram


def test_year_outside_the_file_raises():
    assert 2027 not in cal.covered_years(NSE)  # NSE hasn't published 2027's circular
    for fn in (lambda: cal.is_session(NSE, date(2027, 3, 1)), lambda: cal.is_session(NSE, date(2018, 12, 31)),
               lambda: cal.sessions(NSE, date(2026, 12, 30), date(2027, 1, 4))):
        with pytest.raises(cal.CalendarYearError):
            fn()
    with pytest.raises(ValueError):
        cal.is_session("xlon", date(2024, 1, 2))


def test_close_time_of_a_non_session_raises():
    with pytest.raises(ValueError):
        cal.close_time(NSE, date(2024, 7, 6))


def test_nyse_holidays_and_early_closes():
    assert cal.covered_years(NYSE) == list(range(2019, 2028))
    assert not cal.is_session(NYSE, date(2025, 1, 9))    # National Day of Mourning (Carter)
    assert not cal.is_session(NYSE, date(2022, 6, 20))   # Juneteenth observed
    assert cal.is_session(NYSE, date(2021, 6, 18))       # Juneteenth wasn't a holiday yet
    assert not cal.is_session(NYSE, date(2026, 4, 3))    # Good Friday
    assert not cal.is_session(NYSE, date(2027, 7, 5))    # Independence Day observed
    assert cal.is_session(NYSE, date(2027, 12, 31))      # New Year's Day 2028 is not observed
    assert not cal.is_session(NYSE, date(2022, 12, 26))  # Christmas on a Sunday, observed Monday
    assert cal.is_session(NYSE, date(2021, 12, 31))      # New Year's Day 2022 (Saturday) not observed
    ny = cal.close_time(NYSE, date(2024, 11, 29))
    assert ny.strftime("%H:%M %Z") == "13:00 EST"
    assert cal.close_time(NYSE, date(2024, 11, 27)).strftime("%H:%M") == "16:00"
    assert cal.close_time(NYSE, date(2024, 7, 3)).strftime("%H:%M") == "13:00"


def test_files_are_well_formed():
    for name in ("xnse", "xnys"):
        raw = yaml.safe_load((cal.CONFIG_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
        for year, body in raw.items():
            assert body["source"], (name, year)
            for h in body["holidays"]:
                assert h["date"].year == year and h["date"].weekday() < 5, (name, h)  # weekday holidays only
                assert h["name"]
            days = [h["date"] for h in body["holidays"]]
            assert days == sorted(days) and len(set(days)) == len(days)
            for s in body.get("special_sessions", []):
                assert s["date"].year == year and s["source"] and s["kind"] in {"muhurat", "budget", "special", "dr"}
                assert time.fromisoformat(s["open"]) < time.fromisoformat(s["close"])


# --- against the saved bhavcopies: the dates with a file are exactly the calendar's sessions ----------------------------
# README of the fixtures calls these months complete, but four sessions have no file (found 28 Oct 2026). 2A-06's dev
# backfill on GitHub's runners proves them against the real archive (its blocking calendar check).
KNOWN_FIXTURE_GAPS = {
    date(2019, 1, 1): "a normal Tuesday session; NSE's archive has no bhavcopy for it in the saved set",
    date(2019, 2, 13): "a normal Wednesday session in every circular read; the saved set has no file (Yahoo lacks it too)",
    date(2019, 10, 27): "Muhurat session (Sunday); no file saved",
    date(2020, 2, 1): "Budget-day session (Saturday); no file saved",
}
# The Oct 2026 files were saved on 19 Oct 2026 (the kit's run); later October sessions have none yet.
PARTIAL_MONTHS = {(2026, 10): date(2026, 10, 19)}


@pytest.mark.skipif(not BHAV.exists(), reason="the saved pages aren't checked out (data/fixtures: the private fixtures repo)")
def test_sessions_match_the_saved_bhavcopies():
    files: dict[tuple[int, int], set[date]] = {}
    for p in BHAV.glob("*/nse_*.zip"):
        stamp = p.stem.split("_")[1]
        d = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        files.setdefault((d.year, d.month), set()).add(d)
    assert len(files) >= 11
    problems = []
    for (y, m), have in sorted(files.items()):
        first = date(y, m, 1)
        last = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
        last = min(last, PARTIAL_MONTHS.get((y, m), last))
        expect = set(cal.sessions(NSE, first, last)) - set(KNOWN_FIXTURE_GAPS)
        have = have - set(KNOWN_FIXTURE_GAPS)
        if expect != have:
            problems.append((y, m, "calendar only", sorted(expect - have), "files only", sorted(have - expect)))
    assert not problems
    # a known gap that gains a file must leave the list
    present = {date(int(p.stem.split("_")[1][:4]), int(p.stem.split("_")[1][4:6]), int(p.stem.split("_")[1][6:]))
               for p in BHAV.glob("*/nse_*.zip")}
    assert not (set(KNOWN_FIXTURE_GAPS) & present)
    for d in KNOWN_FIXTURE_GAPS:
        assert cal.is_session(NSE, d)
