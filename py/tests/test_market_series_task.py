"""policy.market_series through the runner, against the test database as job_writer (Prompt 01 B4; Architecture 02
§2; Spec 01 §2): FRED and Yahoo served by a mock transport, raw files into a local S3-protocol server (moto), the
run's clock set by the test. Each test loads its own series (keys under a fresh prefix, with the real source codes),
so the fixture's series and other tests are untouched."""
import itertools
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks.market_series import MarketSeries

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NY, LON = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata"),
PORT, BUCKET = 5594, "series-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.b4_market_series"
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)       # 06:00 New York: the 22 Oct close is in, 23 Oct's isn't
API_KEY = "fred-secret-key-b4"
CRUMB, A3 = "b4Crumb.x7Q", "d=AQABBb4cookie&S=AQAAAtest"
_n = itertools.count(1)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'B4 market series test', '{daily}', '20 hours', '4 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


@pytest.fixture(autouse=True)
def fred_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", API_KEY)


def storage():
    from ft.storage import Storage
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


# (suffix, source, code, unit, frequency, fixing time, zone, currency)
SERIES = [
    ("DGS10", "fred", "DGS10", "percent", "D", "15:30", "America/New_York", None),
    ("DEXINUS", "fred", "DEXINUS", "inr_per_usd", "D", "12:00", "America/New_York", None),
    ("IN10Y", "fred", "INDIRLTLT01STM", "percent", "M", None, None, None),
    ("SP500", "fred", "SP500", "index_points", "D", "16:00", "America/New_York", "USD"),
    ("^GSPC", "yfinance", "^GSPC", "index_points", "D", "16:00", "America/New_York", "USD"),
    ("INR=X", "yfinance", "INR=X", "inr_per_usd", "D", None, None, None),
]


def make_series() -> str:
    prefix = f"b4t{next(_n)}x{datetime.now().strftime('%H%M%S%f')}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for suffix, source, code, unit, freq, fix, tz, cur in SERIES:
            c.execute("""insert into core.series (key, name, source_id, source_code, unit, frequency, fixing_time,
                                                   fixing_tz, market, currency)
                         select %s, %s, id, %s, %s, %s, %s::time, %s, 'US', %s from core.source where key = %s""",
                      (f"{prefix}.{suffix}", f"test {suffix}", code, unit, freq, fix, tz, cur, source))
    return prefix


def task_for(prefix: str) -> MarketSeries:
    class T(MarketSeries):
        key = KEY
        series_like = (prefix + ".%",)
        cross_check = (f"{prefix}.^GSPC", f"{prefix}.SP500")
        never_publish = frozenset({f"{prefix}.SP500"})
    t = T()
    t.workflows = ("daily", "backfill")
    return t


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


class Data:
    """What the two sources hold. FRED by code, Yahoo by symbol: {date: value}."""

    def __init__(self, end=date(2026, 10, 22)):
        days = list(weekdays(date(2018, 11, 15), end))
        self.fred = {
            "DGS10": {d: "4.01" for d in days},
            "DEXINUS": {d: "88.20" for d in days if d <= end - timedelta(days=7)},     # published a week late
            "INDIRLTLT01STM": {date(2026, m, 1): "6.80" for m in range(1, 10)},
            "SP500": {d: f"{6000 + i}.25" for i, d in enumerate(days)},
        }
        self.yahoo = {
            "^GSPC": {d: 6000 + i + 0.25 for i, d in enumerate(days)},
            "INR=X": {d: 88.3 for d in days},
        }
        self.yahoo_meta = {"^GSPC": ("^GSPC", "USD"), "INR=X": ("INR=X", "INR")}
        self.yahoo_live = {"^GSPC": 7000.0}       # a bar for the fetch day's session, still trading
        # Yahoo's session, as it answers yfinance: fc.yahoo.com a 404 that sets the A3 cookie, getcrumb the crumb
        # (only with that cookie). The chart requires neither here; the tests check what's sent.
        self.crumb_reply: httpx.Response | None = None
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url, p = request.url, request.url.params
        if url.host == "fc.yahoo.com":
            return httpx.Response(404, text="<html>not found</html>",
                                  headers=[("set-cookie", f"A3={A3}; Domain=.yahoo.com; Path=/; Secure"),
                                           ("set-cookie", "B=x; Domain=.yahoo.com; Path=/")])
        if url.host == "query1.finance.yahoo.com" and url.path == "/v1/test/getcrumb":
            if self.crumb_reply is not None:
                return self.crumb_reply
            ok = f"A3={A3}" in request.headers.get("cookie", "")
            return httpx.Response(200, text=CRUMB) if ok else httpx.Response(401, text="<html>no</html>")
        if url.host == "api.stlouisfed.org":
            assert p["api_key"] == API_KEY
            s, e = date.fromisoformat(p["observation_start"]), date.fromisoformat(p["observation_end"])
            obs = [{"date": d.isoformat(), "value": v} for d, v in sorted(self.fred[p["series_id"]].items())
                   if s <= d <= e]
            return httpx.Response(200, json={"observations": obs})
        if url.host == "query2.finance.yahoo.com":
            sym = url.path.rsplit("/", 1)[-1]          # httpx decodes the path: ^GSPC, INR=X
            p1, p2 = int(p["period1"]), int(p["period2"])
            tz = "America/New_York" if sym.startswith("^") else "Europe/London"
            open_at = (9, 30) if sym.startswith("^") else (0, 0)
            bars = dict(self.yahoo[sym])
            if sym in self.yahoo_live:
                bars[NOW.date()] = self.yahoo_live[sym]
            stamps, closes = [], []
            for d, v in sorted(bars.items()):
                t = int(datetime(d.year, d.month, d.day, *open_at, tzinfo=ZoneInfo(tz)).timestamp())
                if p1 <= t < p2:
                    stamps.append(t)
                    closes.append(v)
            sym_out, cur = self.yahoo_meta[sym]
            return httpx.Response(200, json={"chart": {"error": None, "result": [{
                "meta": {"symbol": sym_out, "currency": cur, "exchangeTimezoneName": tz},
                "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}]}})
        return httpx.Response(404)


def series_run(prefix: str, data: Data, *, now=NOW, workflow="daily", since=None):
    http = HttpClient(transport=httpx.MockTransport(data), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, workflow, [task_for(prefix)], only=KEY, force=True, http=http, storage=storage,
               clock=lambda: now, since=since)
    tr = q("""select id, status, rows_written, checks, error, http_stats from ops.task_run where task_key=%s
              order by id desc limit 1""", KEY)[0]
    return code, tr


def check(tr, name):
    return next(c for c in tr[3] if c["name"] == name)


def published(prefix):
    return {k.split(".", 1)[1]: p for k, p in q("select key, is_published from core.series where key like %s",
                                                prefix + ".%")}


def obs(prefix, suffix):
    return dict(q("""select o.obs_date, o.value from core.observation o join core.series s on s.id = o.series_id
                      where s.key = %s order by 1""", f"{prefix}.{suffix}"))


# ---------------------------------------------------------------------------------------------- daily runs
@pytest.mark.db
def test_first_load_publishes_every_series_but_sp500_and_a_rerun_writes_nothing(s3):
    prefix, data = make_series(), Data()
    code, tr = series_run(prefix, data)
    assert code == 0 and tr[1] == "success", tr
    assert published(prefix) == {"DGS10": True, "DEXINUS": True, "IN10Y": True, "SP500": False, "^GSPC": True,
                                 "INR=X": True}
    assert check(tr, "gspc_vs_sp500")["passed"]
    # FRED's 14-day look-back, Yahoo's month, the monthly series' 100 days; the live bar is left out.
    assert min(obs(prefix, "DGS10")) == date(2026, 10, 9) and max(obs(prefix, "DGS10")) == date(2026, 10, 22)
    assert min(obs(prefix, "^GSPC")) == date(2026, 9, 22) and NOW.date() not in obs(prefix, "^GSPC")
    assert max(obs(prefix, "INR=X")) == date(2026, 10, 22)
    assert sorted(obs(prefix, "IN10Y")) == [date(2026, 8, 1), date(2026, 9, 1)]
    assert str(obs(prefix, "^GSPC")[date(2026, 10, 22)]) == str(obs(prefix, "SP500")[date(2026, 10, 22)])
    # One response per series, each stored as a raw file, plus getcrumb's 200 and fc.yahoo.com's 404 (not stored).
    assert tr[5]["200"] == 7 and tr[5]["404"] == 1 and tr[5]["new_raw_files"] == 6
    assert check(tr, "yahoo_session")["passed"] and check(tr, "yahoo_session")["detail"] == "crumb received"

    code, tr2 = series_run(prefix, data)
    assert tr2[1] == "success" and tr2[2] == 0, tr2
    assert tr2[5]["new_raw_files"] == 0                  # identical responses: no new raw files
    assert check(tr2, "published")["detail"] == "none newly published"


@pytest.mark.db
def test_the_fred_key_is_sent_but_never_recorded(s3):
    prefix, data = make_series(), Data()
    series_run(prefix, data)
    assert any(r.url.params.get("api_key") == API_KEY for r in data.seen)
    leaks = q("""select (select count(*) from ops.http_cache where url like %s)
                      + (select count(*) from core.raw_file where source_url like %s)
                      + (select count(*) from ops.task_run where coalesce(error, '') || checks::text like %s)""",
              *[f"%{API_KEY}%"] * 3)
    assert leaks == [(0,)]
    assert q("select count(*) from ops.http_cache where url like %s", "%series_id=DGS10%")[0][0] >= 1


@pytest.mark.db
def test_yahoo_gets_its_cookie_and_crumb_first_and_the_crumb_is_never_recorded(s3):
    prefix, data = make_series(), Data()
    code, tr = series_run(prefix, data)
    assert code == 0 and tr[1] == "success", tr
    hosts = [r.url.host for r in data.seen if "yahoo" in r.url.host]
    assert hosts[:2] == ["fc.yahoo.com", "query1.finance.yahoo.com"]          # once per run, before any chart
    assert hosts.count("fc.yahoo.com") == 1 and hosts.count("query1.finance.yahoo.com") == 1
    charts = [r for r in data.seen if r.url.host == "query2.finance.yahoo.com"]
    assert len(charts) == 2
    assert all(r.url.params["crumb"] == CRUMB and f"A3={A3}" in r.headers["cookie"] for r in charts)
    leaks = q("""select (select count(*) from ops.http_cache where url like %s)
                      + (select count(*) from core.raw_file where source_url like %s)""", *[f"%{CRUMB}%"] * 2)
    assert leaks == [(0,)]
    assert q("select count(*) from core.raw_file where source_url like %s", "%yahoo.com/v1/%")[0][0] == 0
    assert q("select count(*) from ops.http_cache where url like %s", "%fc.yahoo.com%")[0][0] == 0


@pytest.mark.db
def test_without_a_crumb_the_charts_are_still_asked_for_with_a_warning(s3):
    prefix, data = make_series(), Data()
    data.crumb_reply = httpx.Response(429, text="Too Many Requests")
    code, tr = series_run(prefix, data)
    assert code == 0 and tr[1] == "success", tr                       # the warning doesn't block the load
    c = check(tr, "yahoo_session")
    assert not c["passed"] and not c["blocking"] and "HTTP 429" in c["detail"]
    charts = [r for r in data.seen if r.url.host == "query2.finance.yahoo.com"]
    assert len(charts) == 2 and all("crumb" not in r.url.params for r in charts)
    assert tr[5]["429"] == 4                                          # getcrumb, retried 3 times


@pytest.mark.db
def test_a_revised_value_is_updated_and_logged(s3):
    prefix, data = make_series(), Data()
    series_run(prefix, data)
    data.fred["DGS10"][date(2026, 10, 20)] = "4.03"
    _code, tr = series_run(prefix, data)
    assert tr[1] == "success" and tr[2] == 1, tr
    assert str(obs(prefix, "DGS10")[date(2026, 10, 20)]) == "4.03"
    rev = q("""select r.old_value::text, r.new_value::text from core.observation_revision r
                 join core.series s on s.id = r.series_id where s.key = %s""", f"{prefix}.DGS10")
    assert rev == [("4.01", "4.03")]
    assert check(tr, "revisions")["passed"] and "1 of" in check(tr, "revisions")["detail"]


@pytest.mark.db
def test_more_than_five_percent_revised_rolls_the_load_back(s3):
    prefix, data = make_series(), Data()
    series_run(prefix, data)
    for d in list(data.yahoo["^GSPC"])[-22:]:
        data.yahoo["^GSPC"][d] += 0.5
        data.fred["SP500"][d] = f"{data.yahoo['^GSPC'][d]:.2f}"
    code, tr = series_run(prefix, data)
    assert code == 1 and tr[1] == "checks_failed" and "revisions" in tr[4], tr
    assert str(obs(prefix, "^GSPC")[date(2026, 10, 22)]).endswith(".25")      # last good data kept


@pytest.mark.db
def test_a_yield_out_of_bounds_blocks_the_load_and_nothing_is_published(s3):
    prefix, data = make_series(), Data()
    data.fred["DGS10"][date(2026, 10, 21)] = "99.9"
    _code, tr = series_run(prefix, data)
    assert tr[1] == "checks_failed" and "bounds" in tr[4], tr
    assert "99.9" in check(tr, "bounds")["detail"]
    assert obs(prefix, "DGS10") == {} and not any(published(prefix).values())


@pytest.mark.db
def test_usd_inr_outside_50_150_blocks(s3):
    prefix, data = make_series(), Data()
    data.yahoo["INR=X"][date(2026, 10, 21)] = 8.83
    _code, tr = series_run(prefix, data)
    assert tr[1] == "checks_failed" and "INR=X" in check(tr, "bounds")["detail"], tr


@pytest.mark.db
def test_an_index_move_of_20_percent_in_a_day_blocks(s3):
    prefix, data = make_series(), Data()
    series_run(prefix, data)
    later = NOW + timedelta(days=1)
    data.yahoo["^GSPC"][date(2026, 10, 23)] = data.yahoo["^GSPC"][date(2026, 10, 22)] * 1.25
    data.fred["SP500"][date(2026, 10, 23)] = f"{data.yahoo['^GSPC'][date(2026, 10, 23)]:.2f}"
    data.yahoo_live = {}
    _code, tr = series_run(prefix, data, now=later)
    assert tr[1] == "checks_failed", tr
    assert "+25.0%" in check(tr, "bounds")["detail"]


@pytest.mark.db
def test_gspc_and_sp500_must_agree_within_five_hundredths_of_a_percent(s3):
    prefix, data = make_series(), Data()
    d = date(2026, 10, 15)
    data.fred["SP500"][d] = f"{data.yahoo['^GSPC'][d] * 1.001:.2f}"      # 0.1% apart
    _code, tr = series_run(prefix, data)
    assert tr[1] == "checks_failed" and "gspc_vs_sp500" in tr[4], tr
    assert "2026-10-15" in check(tr, "gspc_vs_sp500")["detail"]


@pytest.mark.db
def test_a_series_past_its_lag_loads_with_a_warning_and_stays_unpublished(s3):
    prefix, data = make_series(), Data()
    data.fred["DEXINUS"] = {d: v for d, v in data.fred["DEXINUS"].items() if d <= date(2026, 10, 9)}
    _, tr = series_run(prefix, data)
    assert tr[1] == "success", tr
    lag = check(tr, "lag")
    assert not lag["passed"] and not lag["blocking"] and "DEXINUS: latest 2026-10-09" in lag["detail"]
    p = published(prefix)
    assert p["DEXINUS"] is False and p["DGS10"] is True
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"check:{KEY}:lag")[0][0] == 1
    # The source catches up: the next load publishes it and resolves the warning.
    data2 = Data()
    _code, tr = series_run(prefix, data2)
    assert tr[1] == "success" and published(prefix)["DEXINUS"] is True
    assert f"{prefix}.DEXINUS" in check(tr, "published")["detail"]


@pytest.mark.db
def test_a_chart_for_another_symbol_or_currency_blocks(s3):
    prefix, data = make_series(), Data()
    data.yahoo_meta["^GSPC"] = ("^GSPC", "EUR")
    _code, tr = series_run(prefix, data)
    assert tr[1] == "checks_failed" and "responses" in tr[4], tr
    assert "currency 'EUR'" in check(tr, "responses")["detail"]


@pytest.mark.db
def test_backfill_reads_from_1_december_2018(s3):
    prefix, data = make_series(), Data()
    _code, tr = series_run(prefix, data, workflow="backfill")
    assert tr[1] == "success", tr
    starts = {r.url.params["observation_start"] for r in data.seen if r.url.host == "api.stlouisfed.org"}
    assert starts == {"2018-12-01"}
    assert min(obs(prefix, "DGS10")) == date(2018, 12, 3) and min(obs(prefix, "^GSPC")) == date(2018, 12, 3)
    overlap = len(list(weekdays(date(2018, 12, 1), date(2026, 10, 22))))
    assert check(tr, "gspc_vs_sp500")["passed"] and check(tr, "gspc_vs_sp500")["detail"] == f"{overlap} dates within 0.05%"


@pytest.mark.db
def test_a_missing_fred_key_fails_the_task(s3, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY")
    prefix, data = make_series(), Data()
    code, tr = series_run(prefix, data)
    assert code == 1 and tr[1] == "failed" and "FRED_API_KEY is not set" in tr[4]


@pytest.mark.db
def test_the_real_task_reads_the_17_seeded_series_and_never_publishes_sp500(s3):
    with psycopg.connect(JOB) as c:
        keys = [s.key for s in MarketSeries()._series(c)]
    assert len(keys) == 17
    assert "fred.SP500" in keys and "yf.INR=X" in keys and "yf.^GSPC" in keys
    publishable = [k for k in keys if k not in MarketSeries.never_publish]
    assert len(publishable) == 16
    assert json.dumps(MarketSeries.cross_check) == '["yf.^GSPC", "fred.SP500"]'
