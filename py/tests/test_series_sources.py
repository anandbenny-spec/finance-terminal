"""The two parsers behind policy.market_series (Prompt 01 B4): FRED's observations JSON and Yahoo's chart JSON."""
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from ft.policy import series_sources as src

NY = ZoneInfo("America/New_York")


def fred_body(*obs):
    return json.dumps({"observations": [{"date": d, "value": v} for d, v in obs]}).encode()


def chart(symbol, tz, bars, currency="USD", error=None):
    """bars: (datetime of the session start, close or None)."""
    if error:
        return json.dumps({"chart": {"result": None, "error": error}}).encode()
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "currency": currency, "exchangeTimezoneName": tz},
        "timestamp": [int(t.timestamp()) for t, _ in bars],
        "indicators": {"quote": [{"close": [c for _, c in bars]}]}}]}}).encode()


def test_fred_values_are_exact_decimals_and_holidays_are_skipped():
    pts = src.parse_fred(fred_body(("2026-10-20", "4.01"), ("2026-10-13", "."), ("2026-10-21", "3.98")), "DGS10")
    assert pts == [(date(2026, 10, 20), Decimal("4.01")), (date(2026, 10, 21), Decimal("3.98"))]


def test_fred_error_and_unreadable_values_raise():
    with pytest.raises(src.SourceError, match="Bad Request. The series does not exist"):
        src.parse_fred(json.dumps({"error_code": 400, "error_message": "Bad Request. The series does not exist."})
                       .encode(), "NOPE")
    with pytest.raises(src.SourceError, match="unreadable"):
        src.parse_fred(fred_body(("2026-10-20", "n/a")), "DGS10")
    with pytest.raises(src.SourceError, match="isn't JSON"):
        src.parse_fred(b"<html>", "DGS10")
    with pytest.raises(src.SourceError, match="non-finite"):
        src.parse_fred(fred_body(("2026-10-20", "NaN")), "DGS10")


def test_fred_params_carry_no_key():
    p = src.fred_params("DGS2", date(2026, 10, 9), date(2026, 10, 23))
    assert p == {"series_id": "DGS2", "observation_start": "2026-10-09", "observation_end": "2026-10-23",
                 "file_type": "json"}


def test_yahoo_dates_are_exchange_local_closes_rounded_to_the_quote():
    bars = [(datetime(2026, 10, 21, 9, 30, tzinfo=NY), 6687.31982421875),
            (datetime(2026, 10, 22, 9, 30, tzinfo=NY), None),            # listed without a close
            (datetime(2026, 10, 23, 9, 30, tzinfo=NY), 6700.0)]          # today's session, still trading
    c = src.parse_yahoo(chart("^GSPC", "America/New_York", bars), "^GSPC", "index_points",
                        fetched_at=datetime(2026, 10, 23, 18, 0, tzinfo=UTC), fixing_time=time(16),
                        fixing_tz="America/New_York")
    assert c.points == [(date(2026, 10, 21), Decimal("6687.32"))]
    assert c.dropped_live == [date(2026, 10, 23)]
    assert (c.symbol, c.currency, c.timezone) == ("^GSPC", "USD", "America/New_York")


def test_yahoo_session_counts_once_its_fixing_time_has_passed():
    bars = [(datetime(2026, 10, 23, 9, 30, tzinfo=NY), 6700.0)]
    after = src.parse_yahoo(chart("^GSPC", "America/New_York", bars), "^GSPC", "index_points",
                            fetched_at=datetime(2026, 10, 23, 20, 0, 1, tzinfo=UTC), fixing_time=time(16),
                            fixing_tz="America/New_York")
    assert after.points == [(date(2026, 10, 23), Decimal("6700.00"))]


def test_yahoo_rate_without_fixing_time_drops_only_todays_bar():
    lon = ZoneInfo("Europe/London")
    bars = [(datetime(2026, 10, 21, tzinfo=lon), 88.7699966430664), (datetime(2026, 10, 22, tzinfo=lon), 88.81),
            (datetime(2026, 10, 23, tzinfo=lon), 88.9)]
    c = src.parse_yahoo(chart("INR=X", "Europe/London", bars, currency="INR"), "INR=X", "inr_per_usd",
                        fetched_at=datetime(2026, 10, 23, 1, 7, tzinfo=UTC), fixing_time=None, fixing_tz=None)
    assert c.points == [(date(2026, 10, 21), Decimal("88.7700")), (date(2026, 10, 22), Decimal("88.8100"))]
    assert c.dropped_live == [date(2026, 10, 23)]


def test_yahoo_india_bar_is_dated_in_india():
    ist = ZoneInfo("Asia/Kolkata")
    bars = [(datetime(2026, 10, 22, 9, 15, tzinfo=ist), 25100.5)]      # 03:45 UTC on the 22nd
    c = src.parse_yahoo(chart("^NSEI", "Asia/Kolkata", bars, currency="INR"), "^NSEI", "index_points",
                        fetched_at=datetime(2026, 10, 23, 1, 7, tzinfo=UTC), fixing_time=time(15, 30),
                        fixing_tz="Asia/Kolkata")
    assert c.points == [(date(2026, 10, 22), Decimal("25100.50"))]


def test_yahoo_errors_raise():
    at = datetime(2026, 10, 23, tzinfo=UTC)
    with pytest.raises(src.SourceError, match="Not Found"):
        src.parse_yahoo(chart("X", "UTC", [], error={"code": "Not Found", "description": "No data found"}), "X",
                        "index_points", fetched_at=at, fixing_time=None, fixing_tz=None)
    with pytest.raises(src.SourceError, match="isn't a chart"):
        src.parse_yahoo(b"Too Many Requests", "X", "index_points", fetched_at=at, fixing_time=None, fixing_tz=None)
    with pytest.raises(src.SourceError, match="no quoting precision"):
        src.parse_yahoo(chart("X", "UTC", []), "X", "percent", fetched_at=at, fixing_time=None, fixing_tz=None)


def test_yahoo_url_and_params():
    assert src.yahoo_url("^GSPC") == "https://query2.finance.yahoo.com/v8/finance/chart/%5EGSPC"
    assert src.yahoo_url("INR=X") == "https://query2.finance.yahoo.com/v8/finance/chart/INR%3DX"
    p = src.yahoo_params(date(2026, 9, 22), date(2026, 10, 23))
    assert p["period1"] == str(int(datetime(2026, 9, 22, tzinfo=UTC).timestamp()))
    assert p["period2"] == str(int(datetime(2026, 10, 24, tzinfo=UTC).timestamp()))
    assert p["interval"] == "1d"


def test_a_crumb_is_a_short_token_on_a_200_only():
    assert src.parse_crumb(200, b"b4Crumb.x7Q\n") == "b4Crumb.x7Q"
    assert src.parse_crumb(429, b"b4Crumb.x7Q") is None
    assert src.parse_crumb(200, b"Too Many Requests") is None          # yfinance's rate-limit reply
    assert src.parse_crumb(200, b"<html><body>consent</body></html>") is None
    assert src.parse_crumb(200, b"") is None
    assert src.parse_crumb(200, b"x" * 65) is None
    assert src.parse_crumb(200, "crümb".encode()) is None


def test_yahoos_session_hosts_are_in_the_yahoo_site():
    from ft.jobs.fetch import site_for
    for url in (src.YAHOO_COOKIE_URL, src.YAHOO_CRUMB_URL, src.yahoo_url("^GSPC")):
        assert site_for(url).key == "yahoo"
