"""us.prices and us.corporate_actions (job US-03, #402; Spec 02A.1 §5.3): the Dow 30's prices, splits and dividends from
Yahoo's chart API, each day's price as traded, each later split factored once.

The parser is tested on chart answers shaped like Yahoo's (single-precision floats, history divided by later splits);
the tasks run through the runner on the test database against a mock Yahoo that scales history to its own 'today' the
way Yahoo does, with a mock object store. Apple's 4:1 of 31 Aug 2020 and Nvidia's 4:1 of 20 Jul 2021 and 10:1 of
10 Jun 2024 carry their traded closes on record (499.23 on 28 Aug 2020; 1,208.88 on 7 Jun 2024).
"""
import itertools
import json
import struct
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
import httpx
import psycopg
import pytest
import yaml
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft import calendars
from ft.equity import us_prices as yp
from ft.jobs import config
from ft.jobs.fetch import HttpClient, site_for
from ft.jobs.runner import run
from ft.jobs.tasks import equity_us_corporate_actions as uca
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_us_corporate_actions import UsCorporateActions, ratio_factor, split_kind
from ft.jobs.tasks.equity_us_prices import UsPrices, close_at, last_closed_session

D = Decimal
NY = ZoneInfo("America/New_York")
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5643, "us-prices-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY_P, KEY_A = "test.us_prices", "test.us_corporate_actions"
SLUG = "t3u-%"


def f32(x) -> float:
    """What Yahoo sends: the value as a single-precision float."""
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def stamp(d: date) -> int:
    return int(datetime.combine(d, time(9, 30), NY).timestamp())


def after_close(d: date) -> datetime:
    return datetime.combine(d, time(23, 0), NY).astimezone(UTC)


# ---------------------------------------------------------------------------------------------- a Yahoo of our own
@dataclass
class Stock:
    ticker: str
    traded: dict[date, Decimal]                                   # closes as traded, by session
    splits: dict[date, tuple[int, int]] = field(default_factory=dict)     # ex-date -> (numerator, denominator)
    dividends: dict[date, Decimal] = field(default_factory=dict)          # ex-date -> amount as declared
    exchange: str = "NMS"


def walk(start: date, end: date, first: Decimal, splits: dict[date, tuple[int, int]] | None = None,
         fixed: dict[date, Decimal] | None = None) -> dict[date, Decimal]:
    """Traded closes on every NYSE session: a gentle zig-zag, divided by each split's ratio from its ex-date."""
    out, level = {}, first
    for k, d in enumerate(calendars.sessions("xnys", start, end)):
        for ex, (n, m) in (splits or {}).items():
            if d == ex:
                level = level * m / n
        out[d] = (level * (1 + D("0.002") * (k % 5 - 2))).quantize(D("0.01"))
    out.update(fixed or {})
    return out


class Yahoo:
    """The chart API as Yahoo answers it on `today`: every bar and dividend before a split (one that has happened by
    `today`) divided by its ratio, volume multiplied, values as single-precision floats; events inside the window."""

    def __init__(self, stocks: list[Stock], today: date, **broken):
        self.stocks = {s.ticker: s for s in stocks}
        self.today = today
        self.broken = broken                                       # skip=(ticker, day), extra=(ticker, day), status=n
        self.seen: list[httpx.Request] = []

    def scale(self, s: Stock, d: date) -> Decimal:
        k = D(1)
        for ex, (n, m) in s.splits.items():
            if d < ex <= self.today:
                k *= D(n) / D(m)
        return k

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.host == "fc.yahoo.com":
            return httpx.Response(404)
        if request.url.path == "/v1/test/getcrumb":
            return httpx.Response(200, content=b"t3ucrumb")
        t = request.url.path.rsplit("/", 1)[1]
        if self.broken.get("status"):
            return httpx.Response(self.broken["status"])
        s = self.stocks.get(t)
        if s is None:
            return httpx.Response(404, content=b'{"chart":{"result":null,"error":{"code":"Not Found"}}}')
        p1, p2 = int(request.url.params["period1"]), int(request.url.params["period2"])
        days = [d for d in sorted(s.traded) if p1 <= stamp(d) < p2 and d <= self.today]
        if self.broken.get("skip", (None,))[0] == t:
            days = [d for d in days if d != self.broken["skip"][1]]
        extra = self.broken.get("extra")
        if extra and extra[0] == t and p1 <= stamp(extra[1]) < p2:
            days = sorted([*days, extra[1]])
        bars = {"open": [], "high": [], "low": [], "close": [], "volume": []}
        for d in days:
            c = s.traded.get(d) or s.traded[max(x for x in s.traded if x < d)]
            k = self.scale(s, d)
            hi, lo = (c * D("1.01")).quantize(D("0.01")), (c * D("0.99")).quantize(D("0.01"))
            for name, v in (("open", c), ("high", hi), ("low", lo), ("close", c)):
                bars[name].append(f32(v / k))
            bars["volume"].append(int(1_000_000 * k))
        splits = {str(stamp(ex)): {"date": stamp(ex), "numerator": float(n), "denominator": float(m),
                                   "splitRatio": f"{n}:{m}"}
                  for ex, (n, m) in s.splits.items() if p1 <= stamp(ex) < p2 and ex <= self.today}
        divs = {str(stamp(ex)): {"amount": float(a / self.scale(s, ex)), "date": stamp(ex)}
                for ex, a in s.dividends.items() if p1 <= stamp(ex) < p2 and ex <= self.today}
        doc = {"chart": {"result": [{"meta": {"currency": "USD", "symbol": t, "exchangeName": s.exchange,
                                              "exchangeTimezoneName": "America/New_York"},
                                     "timestamp": [stamp(d) for d in days],
                                     "events": {"splits": splits, "dividends": divs},
                                     "indicators": {"quote": [bars]}}], "error": None}}
        return httpx.Response(200, content=json.dumps(doc).encode(), headers={"content-type": "application/json"})


AAPL_FIXED = {date(2020, 8, 28): D("499.23"), date(2020, 8, 31): D("129.04")}


def aapl() -> Stock:
    """Apple around its 4:1 split, with the dividends either side (0.82 a share before it, 0.205 after)."""
    return Stock("AAPL", walk(date(2020, 7, 1), date(2020, 11, 30), D("500"), {date(2020, 8, 31): (4, 1)}, AAPL_FIXED),
                 {date(2020, 8, 31): (4, 1)}, {date(2020, 8, 7): D("0.82"), date(2020, 11, 6): D("0.205")})


def nvda() -> Stock:
    splits = {date(2021, 7, 20): (4, 1), date(2024, 6, 10): (10, 1)}
    fixed = {date(2024, 6, 7): D("1208.88"), date(2024, 6, 10): D("121.79")}
    return Stock("NVDA", walk(date(2021, 7, 1), date(2024, 6, 28), D("4835.52"), splits, fixed), splits,
                 {date(2024, 6, 11): D("0.01")})


# ---------------------------------------------------------------------------------------------- the parser
def chart_body(**over) -> bytes:
    doc = {"chart": {"result": [{"meta": {"currency": "USD", "symbol": "AAPL", "exchangeName": "NMS",
                                          "exchangeTimezoneName": "America/New_York"},
                                 "timestamp": [stamp(date(2020, 8, 27)), stamp(date(2020, 8, 28)), stamp(date(2020, 8, 31))],
                                 "events": {"splits": {"1": {"date": stamp(date(2020, 8, 31)), "numerator": 4.0,
                                                             "denominator": 1.0, "splitRatio": "4:1"}},
                                            "dividends": {"2": {"amount": 0.205, "date": stamp(date(2020, 8, 7))}}},
                                 "indicators": {"quote": [{"open": [f32(D("125.01")), f32(D("126.01")), f32(D("127.58"))],
                                                           "high": [f32(D("125.18")), f32(D("126.44")), f32(D("131.00"))],
                                                           "low": [f32(D("123.84")), f32(D("124.58")), f32(D("126.00"))],
                                                           "close": [f32(D("125.01")), f32(D("124.8075")), f32(D("129.04"))],
                                                           "volume": [155552400, 187630000, 225702700]}]}}],
                     "error": None}}
    doc["chart"]["result"][0].update(over)
    return json.dumps(doc).encode()


def parse(body=None, fetched=None):
    return yp.parse_chart(body or chart_body(), "AAPL", fetched_at=fetched or after_close(date(2020, 9, 1)),
                          close_at=close_at)


def test_the_chart_request_asks_for_daily_bars_with_splits_and_dividends():
    p = yp.chart_params(date(2020, 8, 3), date(2020, 9, 4))
    assert p["interval"] == "1d" and p["events"] == "div,splits" and p["includePrePost"] == "false"
    assert int(p["period1"]) <= stamp(date(2020, 8, 3)) and stamp(date(2020, 9, 4)) < int(p["period2"]) < stamp(date(2020, 9, 5))
    assert yp.yahoo_url("BRK.B").endswith("/v8/finance/chart/BRK.B")
    assert site_for(yp.yahoo_url("AAPL")).key == "yahoo"


def test_bars_before_a_split_are_multiplied_back_to_the_traded_price():
    ch = parse()
    by = {b.day: b for b in ch.bars}
    # Yahoo's 124.8075 (499.23 / 4, as a single-precision float) comes back as Apple's traded close of 28 Aug 2020
    assert by[date(2020, 8, 28)].close == D("499.23") and by[date(2020, 8, 28)].scale == 4
    assert (by[date(2020, 8, 28)].open, by[date(2020, 8, 28)].high, by[date(2020, 8, 28)].low) == (
        D("504.04"), D("505.76"), D("498.32"))
    assert by[date(2020, 8, 28)].volume == 46907500                       # 187,630,000 shares / 4
    assert by[date(2020, 8, 31)].close == D("129.04") and by[date(2020, 8, 31)].scale == 1   # the ex-date: unscaled
    assert all(not b.off_cents for b in ch.bars)
    assert ch.splits == [yp.Split(date(2020, 8, 31), D("4.0"), D("1.0"), "4:1")] and ch.splits[0].ratio == 4
    assert ch.dividends == [yp.Dividend(date(2020, 8, 7), D("0.82"), D(4))]  # 0.205 a share then: 0.82 declared
    assert ch.price_exchange == "NASDAQ" and ch.symbol == "AAPL" and ch.currency == "USD"


def test_two_later_splits_multiply_and_nvidias_traded_closes_come_back():
    days = [date(2021, 7, 19), date(2024, 6, 7), date(2024, 6, 10)]
    traded = [D("187.80") * 4, D("1208.88"), D("121.79")]       # as traded; Yahoo divides by 40, 10 and 1
    body = json.dumps({"chart": {"result": [{
        "meta": {"currency": "USD", "symbol": "NVDA", "exchangeName": "NMS", "exchangeTimezoneName": "America/New_York"},
        "timestamp": [stamp(d) for d in days],
        "events": {"splits": {"a": {"date": stamp(date(2021, 7, 20)), "numerator": 4.0, "denominator": 1.0, "splitRatio": "4:1"},
                              "b": {"date": stamp(date(2024, 6, 10)), "numerator": 10.0, "denominator": 1.0, "splitRatio": "10:1"}}},
        "indicators": {"quote": [{"close": [f32(v / k) for v, k in zip(traded, (40, 10, 1), strict=True)]}]}}],
        "error": None}}).encode()
    ch = yp.parse_chart(body, "NVDA", fetched_at=after_close(date(2024, 6, 11)), close_at=close_at)
    assert [(b.day, b.close, b.scale) for b in ch.bars] == [
        (date(2021, 7, 19), D("751.20"), 40), (date(2024, 6, 7), D("1208.88"), 10), (date(2024, 6, 10), D("121.79"), 1)]
    assert all(b.open is None and b.volume is None for b in ch.bars)


def test_a_live_bar_a_bar_without_a_close_and_a_date_sent_twice_are_held_back():
    body = json.loads(chart_body())
    r = body["chart"]["result"][0]
    r["timestamp"] += [stamp(date(2020, 8, 31)), stamp(date(2020, 9, 1)), stamp(date(2020, 9, 2))]
    q = r["indicators"]["quote"][0]
    for k in ("open", "high", "low"):
        q[k] += [None, None, None]
    q["close"] += [f32(D("130.00")), None, f32(D("131.40"))]
    q["volume"] += [1, 2, 3]
    ch = parse(json.dumps(body).encode(), fetched=datetime.combine(date(2020, 9, 2), time(15, 0), NY))
    assert [b.day for b in ch.bars] == [date(2020, 8, 27), date(2020, 8, 28)]
    assert ch.repeated == [date(2020, 8, 31)] and ch.skipped == [date(2020, 9, 1)]
    assert ch.dropped_live == [date(2020, 9, 2)]


def test_a_price_off_the_cent_grid_is_flagged_and_bad_answers_are_refused():
    body = json.loads(chart_body(events={}))
    body["chart"]["result"][0]["indicators"]["quote"][0]["close"][2] = 129.0437
    ch = parse(json.dumps(body).encode())
    assert ch.bars[2].off_cents == ("close",) and ch.bars[2].close == D("129.04")
    with pytest.raises(yp.SourceError, match="isn't a chart"):
        parse(b"<html>Too Many Requests</html>")
    with pytest.raises(yp.SourceError, match="Not Found"):
        parse(b'{"chart":{"result":null,"error":{"code":"Not Found"}}}')
    short = json.loads(chart_body())
    short["chart"]["result"][0]["indicators"]["quote"][0]["close"].pop()
    with pytest.raises(yp.SourceError, match="differ in length"):
        parse(json.dumps(short).encode())
    bad = json.loads(chart_body())
    bad["chart"]["result"][0]["events"]["splits"]["1"]["numerator"] = 0
    with pytest.raises(yp.SourceError, match="positive ratio"):
        parse(json.dumps(bad).encode())


def test_split_kinds_and_their_factors():
    def s(n, m):
        return yp.Split(date(2026, 1, 5), D(n), D(m), f"{n}:{m}")
    assert [split_kind(s(4, 1)), split_kind(s(1, 10)), split_kind(s("1.2", 1)), split_kind(s(1, 1))] == [
        "split", "consolidation", "other", "other"]
    assert ratio_factor(D(4), D(1)) == D("0.25") and ratio_factor(D(10), D(1)) == D("0.1")
    assert ratio_factor(D(1), D(10)) == D(10) and ratio_factor(D(3), D(2)) == D("0.666666666667")


def test_the_tasks_are_wired_as_module_us_after_the_universe():
    tasks = config.load()
    assert tasks["us.prices"].module == "us" and tasks["us.prices"].run_after == ("us.universe",)
    assert tasks["us.corporate_actions"].run_after == ("us.prices",)
    assert all(tasks[k].rules == {"weekly": "every 6 days"} and not tasks[k].uses_nse
               for k in ("us.prices", "us.corporate_actions"))
    assert registry()["us.prices"] is UsPrices and registry()["us.corporate_actions"] is UsCorporateActions
    assert UsPrices.slug_like == UsCorporateActions.slug_like == "us-%"
    quality = yaml.safe_load((config.CONFIG.parent / "quality.yaml").read_text())
    assert "us" not in quality["modules"]


def test_the_last_closed_session_waits_three_hours_after_the_close():
    assert last_closed_session(datetime.combine(date(2026, 11, 11), time(18, 59), NY)) == date(2026, 11, 10)
    assert last_closed_session(datetime.combine(date(2026, 11, 11), time(19, 0), NY)) == date(2026, 11, 11)
    # 27 Nov 2026 closes at 13:00 (the day after Thanksgiving), so its bar is final by 16:00
    assert last_closed_session(datetime.combine(date(2026, 11, 27), time(16, 30), NY)) == date(2026, 11, 27)


# ---------------------------------------------------------------------------------------------- through the runner
@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        for k in (KEY_P, KEY_A):
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'us prices test', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (k,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


def storage():
    from ft.storage import Storage
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def wipe():
    ids = "(select id from core.entity where slug like 't3u-%')"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for sql in (f"delete from equity.adjustment_factor where entity_id in {ids}",
                    f"delete from equity.corporate_action where event_id in (select id from core.event where entity_id in {ids})",
                    f"delete from core.event where entity_id in {ids}",
                    f"delete from equity.price_daily where entity_id in {ids}",
                    f"delete from equity.company where entity_id in {ids}",
                    f"delete from core.entity_identifier where entity_id in {ids}",
                    "delete from core.entity where slug like 't3u-%'"):
            c.execute(sql)


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def make(*tickers, exchange="NASDAQ", market="US") -> dict[str, int]:
    out = {}
    with psycopg.connect(ADMIN, autocommit=True) as c:
        sec = c.execute("select id from core.source where key = 'sec'").fetchone()[0]
        for t in tickers:
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, %s, %s, false) returning id""",
                            (f"t3u-{t.lower()}", f"{t} test", market, "USD" if market == "US" else "INR")).fetchone()[0]
            c.execute("insert into equity.company (entity_id, price_exchange) values (%s, %s)",
                      (eid, exchange if market == "US" else "NSE"))
            c.execute("""insert into core.entity_identifier (entity_id, scheme, value, valid, source_id)
                         values (%s, 'ticker', %s, daterange(null, null, '[)'), %s)""", (eid, t, sec))
            out[t] = eid
    return out


_n = itertools.count(1)


def go(cls, key, yahoo, *, since=None, until=None, now=None, workflow="backfill", review=None, monkeypatch=None):
    class T(cls):
        slug_like = SLUG
    T.key = key
    if review is not None:
        monkeypatch.setattr(uca, "REVIEW_FILE", review)
    t = T()
    t.workflows = ("weekly", "backfill")
    now = now or after_close(yahoo.today)
    http = HttpClient(transport=httpx.MockTransport(yahoo), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, workflow, [t], only=key, force=True, http=http, storage=storage, clock=lambda: now,
               since=since, until=until)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1",
           key)[0]
    return code, tr


def prices_run(yahoo, **kw):
    return go(UsPrices, KEY_P, yahoo, **kw)


def actions_run(yahoo, **kw):
    return go(UsCorporateActions, KEY_A, yahoo, **kw)


def checks(tr):
    return {c["name"]: (c["passed"], c["blocking"]) for c in tr[2] or []}


def detail(tr, name):
    return next(c["detail"] for c in tr[2] if c["name"] == name)


def stored(eid, *days):
    rows = dict(q("select trade_date, close from equity.price_daily where entity_id = %s", eid))
    return [rows.get(d) for d in days] if days else rows


def adjusted(eid, *days):
    rows = dict(q("select trade_date, adj_close from equity.price_adjusted where entity_id = %s", eid))
    return [rows.get(d) for d in days]


def factors():
    return {r[0]: r[1:] for r in q(
        """select ev.slug, a.price_factor, a.method, a.status, a.reason from equity.adjustment_factor a
             join core.event ev on ev.id = a.event_id where ev.slug like 't3u-%' order by 1""")}


@pytest.mark.db
def test_apples_backfill_stores_traded_prices_and_its_split_is_factored_once(s3):
    ids = make("AAPL")
    y = Yahoo([aapl()], today=date(2020, 11, 30))
    code, tr = prices_run(y, since=date(2020, 8, 3), until=date(2020, 9, 4))
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr) == {"charts_read": (True, True), "responses": (True, True), "sessions_present": (True, True),
                          "calendar_proven": (True, True), "sane_prices": (True, True), "cents": (True, True),
                          "unchanged": (True, True), "overlap": (True, True), "exchange": (True, False),
                          "rescaled": (True, False), "held_back": (True, False), "yahoo_session": (True, False)}
    sessions = calendars.sessions("xnys", date(2020, 8, 3), date(2020, 9, 4))
    assert tr[1] == len(sessions) == 25
    e = ids["AAPL"]
    # the traded closes either side of the split, though Yahoo sent 28 Aug divided by 4
    assert stored(e, date(2020, 8, 28), date(2020, 8, 31)) == [D("499.23"), D("129.04")]
    assert q("""select exchange, series, volume, prev_close from equity.price_daily where entity_id = %s
                 and trade_date = '2020-08-28'""", e) == [("NASDAQ", "EQ", 1_000_000, None)]
    assert "AAPL: 4:1 on 2020-08-31" in detail(tr, "rescaled")
    # the raw file is Yahoo's answer, stored before the load
    assert q("""select count(*), min(s.key) from core.raw_file f join core.source s on s.id = f.source_id
                 where f.task_run_id = (select max(id) from ops.task_run where task_key = %s)""", KEY_P) == [(1, "yfinance")]

    code, tr = actions_run(y, since=date(2019, 1, 1))
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr)["ratios_exact"] == (True, True) and checks(tr)["applied_checked"] == (True, True)
    assert checks(tr)["not_applied"] == (True, False)
    assert factors() == {"t3u-aapl-split-2020-08-31": (D("0.250000000000"), "ratio", "applied", None)}
    assert q("""select ca.kind, ca.ratio_new, ca.ratio_old, ca.dividend_per_share, ca.exchange, ev.status, s.key,
                       ev.is_published, ev.title
                  from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                  join core.source s on s.id = ev.source_id where ev.entity_id = %s order by ca.ex_date""", e) == [
        ("dividend", None, None, D("0.82"), "NASDAQ", "effective", "yfinance", True, "Cash dividend"),
        ("split", D("4"), D("1"), None, "NASDAQ", "effective", "yfinance", True, "Stock split 4:1"),
        ("dividend", None, None, D("0.205"), "NASDAQ", "effective", "yfinance", True, "Cash dividend")]
    # no jump in the adjusted series: 28 Aug's 499.23 x 0.25 sits beside 31 Aug's 129.04
    assert adjusted(e, date(2020, 8, 28), date(2020, 8, 31)) == [D("124.8075"), D("129.04")]
    # never published, so no visitor sees any of it (0039's gate)
    assert q("select is_published from core.entity where id = %s", e) == [(False,)]
    with psycopg.connect(ADMIN) as c:
        c.execute("set role anon")
        assert c.execute("select count(*) from equity.price_daily where entity_id = %s", (e,)).fetchone()[0] == 0
        assert c.execute("""select count(*) from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                             where ev.entity_id = %s""", (e,)).fetchone()[0] == 0


@pytest.mark.db
def test_nvidias_two_splits_leave_no_jump_in_the_adjusted_series(s3):
    ids = make("NVDA")
    y = Yahoo([nvda()], today=date(2024, 6, 28))
    code, tr = prices_run(y, since=date(2021, 7, 1), until=date(2024, 6, 28))
    assert code == 0 and tr[0] == "success", tr
    e = ids["NVDA"]
    s = nvda().traded
    assert stored(e, date(2021, 7, 19), date(2021, 7, 20), date(2024, 6, 7), date(2024, 6, 10)) == [
        s[date(2021, 7, 19)], s[date(2021, 7, 20)], D("1208.88"), D("121.79")]
    code, tr = actions_run(y, since=date(2021, 1, 1))
    assert code == 0 and tr[0] == "success", tr
    assert factors() == {"t3u-nvda-split-2021-07-20": (D("0.250000000000"), "ratio", "applied", None),
                         "t3u-nvda-split-2024-06-10": (D("0.100000000000"), "ratio", "applied", None)}
    rows = q("""select trade_date, adj_close from equity.price_adjusted where entity_id = %s order by trade_date""", e)
    moves = [abs(b / a - 1) for (_, a), (_, b) in itertools.pairwise(rows)]
    assert max(moves) < D("0.02")                                  # the walk's own steps; no split shows
    assert dict(rows)[date(2024, 6, 7)] == D("120.888")            # 1,208.88 x 0.1


@pytest.mark.db
def test_a_weekly_run_across_a_split_keeps_one_basis_and_the_split_gets_its_ratio(s3):
    ids = make("ZZZ")
    split = date(2026, 11, 5)                                       # a Thursday
    stock = Stock("ZZZ", walk(date(2026, 10, 1), date(2026, 11, 13), D("300"), {split: (2, 1)}), {split: (2, 1)})
    code, tr = prices_run(Yahoo([stock], today=date(2026, 11, 3)), since=date(2026, 10, 1))   # stored to Tue 3 Nov
    assert code == 0 and tr[0] == "success", tr
    code, tr = actions_run(Yahoo([stock], today=date(2026, 11, 3)), since=date(2026, 10, 1))
    assert code == 0 and factors() == {}
    # the next weekly run, on Fri 6 Nov: Yahoo now sends Tue and Wed divided by 2
    later = Yahoo([stock], today=date(2026, 11, 6))
    code, tr = prices_run(later, workflow="weekly")
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr)["overlap"] == (True, True) and "1 companies' last stored close came back" in detail(tr, "overlap")
    assert tr[1] == 3                                               # Wed 4, Thu 5 and Fri 6 Nov
    e = ids["ZZZ"]
    assert stored(e, date(2026, 11, 4), date(2026, 11, 5)) == [stock.traded[date(2026, 11, 4)],
                                                              stock.traded[date(2026, 11, 5)]]
    code, tr = actions_run(later, workflow="weekly")
    assert code == 0 and factors() == {"t3u-zzz-split-2026-11-05": (D("0.500000000000"), "ratio", "applied", None)}
    a = adjusted(e, date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5))
    assert abs(a[1] / a[0] - 1) < D("0.02") and abs(a[2] / a[1] - 1) < D("0.02")
    # a weekly run with nothing new writes nothing; nor does the actions' re-run
    code, tr = prices_run(later, workflow="weekly")
    assert code == 0 and tr[1] == 0 and "nothing new for 1" in detail(tr, "charts_read")
    code, tr = actions_run(later, workflow="weekly")
    assert code == 0 and tr[1] == 0


@pytest.mark.db
def test_a_stored_close_that_comes_back_different_blocks_the_run_and_writes_nothing(s3):
    ids = make("ZZZ")
    stock = Stock("ZZZ", walk(date(2026, 10, 1), date(2026, 11, 13), D("300")))
    code, tr = prices_run(Yahoo([stock], today=date(2026, 11, 3)), since=date(2026, 10, 1))
    assert code == 0
    before = len(stored(ids["ZZZ"]))
    stock.traded[date(2026, 11, 3)] += D("1.00")                    # Yahoo's history moved with no split to explain it
    code, tr = prices_run(Yahoo([stock], today=date(2026, 11, 6)), workflow="weekly")
    assert tr[0] == "failed" and checks(tr)["overlap"] == (False, True)
    assert "ZZZ 2026-11-03: stored" in detail(tr, "overlap")
    assert len(stored(ids["ZZZ"])) == before
    # a backfill that re-reads a stored day with another value is named too, and nothing is written over
    code, tr = prices_run(Yahoo([stock], today=date(2026, 11, 3)), since=date(2026, 10, 1))
    assert tr[0] == "failed" and checks(tr)["unchanged"] == (False, True) and "ZZZ 2026-11-03" in detail(tr, "unchanged")


@pytest.mark.db
def test_a_missing_session_or_a_bar_on_a_holiday_blocks_the_backfill(s3):
    make("ZZZ")
    stock = Stock("ZZZ", walk(date(2026, 10, 1), date(2026, 11, 13), D("300")))
    _, tr = prices_run(Yahoo([stock], today=date(2026, 11, 6), skip=("ZZZ", date(2026, 10, 14))),
                       since=date(2026, 10, 1))
    assert tr[0] == "failed" and checks(tr)["sessions_present"] == (False, True)
    assert "ZZZ (1: 2026-10-14)" in detail(tr, "sessions_present")
    _, tr = prices_run(Yahoo([stock], today=date(2026, 11, 6), extra=("ZZZ", date(2026, 10, 10))),
                       since=date(2026, 10, 1))             # a Saturday
    assert tr[0] == "failed" and checks(tr)["calendar_proven"] == (False, True)
    assert q("select count(*) from equity.price_daily where entity_id in (select id from core.entity where slug like 't3u-%')") == [(0,)]


@pytest.mark.db
def test_a_ticker_yahoo_refuses_fails_the_run(s3):
    make("ZZZ", "QQQQ")
    stock = Stock("ZZZ", walk(date(2026, 10, 1), date(2026, 11, 13), D("300")))
    _, tr = prices_run(Yahoo([stock], today=date(2026, 11, 6)), since=date(2026, 10, 1))
    assert tr[0] == "failed" and checks(tr)["charts_read"] == (False, True) and "QQQQ" in detail(tr, "charts_read")
    _, tr = prices_run(Yahoo([stock], today=date(2026, 11, 6), status=429), since=date(2026, 10, 1))
    assert tr[0] == "failed"


@pytest.mark.db
def test_a_split_before_the_first_stored_price_is_source_adjusted_and_an_odd_ratio_is_held_until_reviewed(
        s3, tmp_path, monkeypatch):
    ids = make("ZZZ", "YYY")
    early, odd = date(2026, 10, 1), date(2026, 10, 15)
    z = Stock("ZZZ", walk(date(2026, 9, 1), date(2026, 11, 13), D("300"), {early: (3, 1)}), {early: (3, 1)})
    y5 = Stock("YYY", walk(date(2026, 9, 1), date(2026, 11, 13), D("90"), {odd: (D("1.2"), 1)}), {odd: (D("1.2"), 1)})
    yahoo = Yahoo([z, y5], today=date(2026, 11, 6))
    code, tr = prices_run(yahoo, since=date(2026, 10, 1))           # ZZZ's prices start on its ex-date
    assert code == 0, tr
    code, tr = actions_run(yahoo, since=date(2026, 9, 1))
    assert tr[0] == "success", tr
    f = factors()
    assert f["t3u-zzz-split-2026-10-01"] == (D("1"), "source_adjusted", "applied", None)
    assert f["t3u-yyy-other-2026-10-15"][:3] == (None, "ratio", "held")
    assert "not a whole-number ratio" in f["t3u-yyy-other-2026-10-15"][3]
    assert checks(tr)["not_applied"] == (False, False)
    assert adjusted(ids["YYY"], date(2026, 10, 14)) == [None]       # held: earlier adjusted prices held back
    review = tmp_path / "us_review.yaml"
    review.write_text(yaml.safe_dump({"releases": [
        {"event": "t3u-yyy-other-2026-10-15", "factor": "0.833333333333", "evidence": "test: the company's 8-K",
         "reviewer": "test", "reviewed_on": "2026-11-11"}], "jumps": []}))
    code, tr = actions_run(yahoo, since=date(2026, 9, 1), review=review, monkeypatch=monkeypatch)
    assert tr[0] == "success" and checks(tr)["applied_checked"] == (True, True), tr
    assert factors()["t3u-yyy-other-2026-10-15"][:3] == (D("0.833333333333"), "reviewed", "applied")


@pytest.mark.db
def test_india_is_never_read_and_the_due_rule_waits_for_0039_and_a_first_run(s3):
    make("ZZZ")
    make("INDI", market="IN")                                      # an Indian company, even under the test's slugs
    stock = Stock("ZZZ", walk(date(2026, 10, 1), date(2026, 11, 13), D("300")))
    y = Yahoo([stock], today=date(2026, 11, 6))
    code, _ = prices_run(y, since=date(2026, 10, 1))
    assert code == 0 and not any("INDI" in str(r.url) for r in y.seen)
    for cls in (UsPrices, UsCorporateActions):
        t = cls()
        t.rules = {"weekly": "every 6 days"}
        with psycopg.connect(JOB, autocommit=True) as c:
            d = t.due(c, "weekly", after_close(date(2026, 11, 6)))
            assert not d.due and "first recorded run" in d.reason
            assert t.due(c, "refresh", after_close(date(2026, 11, 6))).due
        with psycopg.connect(ADMIN) as c:
            c.execute("update core.source set key = 'secgone' where key = 'sec'")
            d = t.due(c, "refresh", after_close(date(2026, 11, 6)))
            c.rollback()
        assert not d.due and "0039" in d.reason


# ---------------------------------------------------------------------------------------------- the runner probe
def probe_yahoo(**broken) -> Yahoo:
    recent = walk(date(2026, 10, 20), date(2026, 11, 11), D("250"))
    a = aapl()
    a.traded.update(recent)
    n = nvda()
    n.traded.update(recent)
    return Yahoo([a, n, Stock("JPM", recent, exchange="NYQ"), Stock("GOOGL", recent)], today=date(2026, 11, 11),
                 **broken)


def run_probe(monkeypatch, capsys, yahoo, argv=()):
    from ft.equity import us_probe
    now = datetime(2026, 11, 12, 6, 0, tzinfo=UTC)
    monkeypatch.setattr(us_probe, "utcnow", lambda: now)
    monkeypatch.setattr(us_probe, "HttpClient", lambda: HttpClient(transport=httpx.MockTransport(yahoo),
                                                                  sleep=lambda s: None, clock=lambda: now))
    code = us_probe.main(list(argv))
    return code, capsys.readouterr().out


def test_the_probe_reads_three_tickers_and_checks_yahoos_scaling_without_printing_a_price(monkeypatch, capsys):
    y = probe_yahoo()
    code, out = run_probe(monkeypatch, capsys, y)
    assert code == 0, out
    assert "Yahoo session: crumb received" in out and "probe passed" in out
    assert "  JPM: HTTP 200, 5 bars for 5 sessions (every session has its bar: yes), symbol yes, USD yes, exchange NYSE" in out
    assert ("  AAPL: split yes listed on 2020-08-31 as 4:1; the close of 2020-08-28 comes divided by it: yes; "
            "multiplied back to the traded close: yes; the 2020-08-07 dividend comes divided by it: yes; multiplied back "
            "to the declared amount: yes") in out
    assert "  NVDA: split yes listed on 2024-06-10 as 10:1" in out and "NO" not in out
    assert "499.23" not in out and "1208.88" not in out and "0.82" not in out      # no price in a public log
    assert {r.url.host for r in y.seen} <= {"fc.yahoo.com", "query1.finance.yahoo.com", "query2.finance.yahoo.com"}


def test_the_probe_fails_when_yahoo_refuses_or_the_input_isnt_tickers(monkeypatch, capsys):
    code, out = run_probe(monkeypatch, capsys, probe_yahoo(status=429))
    assert code == 1 and "probe FAILED" in out
    code, out = run_probe(monkeypatch, capsys, probe_yahoo(), ["--tickers", "AAPL;rm -rf"])
    assert code == 2
