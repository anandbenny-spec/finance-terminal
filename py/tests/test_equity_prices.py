"""equity.prices (job 2A-06): the bhavcopy parsers, and the task run through the runner on the saved files.

The files are in data/fixtures/equity/bhavcopy (the private fixtures repo; the tests skip without it, and CI checks it
out): NSE's old format (Jan, Feb and Oct 2019, Jan-Feb 2020, Jul 2023) and UDiFF (Jul 2024 on), BSE's two formats on
sparse dates. Four sessions are not in the fixtures (1 Jan 2019, 13 Feb 2019, 27 Oct 2019, 1 Feb 2020: #108); the tests
name them where a window would reach them. No NSE or BSE data is kept in this repo (CLAUDE.md §4)."""
import csv
import io
import re
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft import calendars
from ft.equity import bhavcopy as bh
from ft.equity import guard
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import equity_prices as ep
from ft.jobs.tasks.equity_prices import EquityPrices

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
BHAV = FIX / "bhavcopy"
needs_fixtures = pytest.mark.skipif(not BHAV.exists(), reason="the saved files aren't checked out (data/fixtures)")

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5598, "equity-prices-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_prices"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)          # 11:30 in India: the last closed session is 27 Oct 2026
D = date

# The four sessions the fixtures lack, by month (#108), and the files' own coverage of the calendar.
FIXTURE_GAPS = {D(2019, 1, 1), D(2019, 2, 13), D(2019, 10, 27), D(2020, 2, 1)}
FIXTURE_MONTHS = [(2019, 1), (2019, 2), (2019, 10), (2020, 1), (2020, 2), (2023, 7), (2024, 7), (2025, 1), (2025, 6),
                  (2025, 10), (2026, 10)]

# company -> (ISIN, BSE scrip code); the ISINs are the Nifty 50 list's
CO = {"RELIANCE": ("INE002A01018", "500325"), "INFY": ("INE009A01021", "500209"),
      "HDFCBANK": ("INE040A01034", "500180"), "ITC": ("INE154A01025", "500875")}


def saved(exchange: str, d: date) -> Path | None:
    hits = sorted((BHAV / exchange).glob(f"*/{exchange}_{d:%Y%m%d}.*"))
    return hits[0] if hits else None


def body(exchange: str, d: date) -> bytes:
    p = saved(exchange, d)
    assert p is not None, f"no saved {exchange} file for {d}"
    return p.read_bytes()


def rewrite(raw: bytes, fn) -> bytes:
    """Apply fn to a saved file's text (zip in, zip out; a bare csv stays one)."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = z.namelist()[0]
            text = z.read(name).decode("utf-8-sig")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name, fn(text))
        return out.getvalue()
    return fn(raw.decode("utf-8-sig")).encode("utf-8")


def set_cell(text: str, column: str, where: dict[str, str], value: str) -> str:
    """Change `column` in the rows whose cells equal `where`."""
    rows = list(csv.reader(io.StringIO(text)))
    head = [c.strip().upper() for c in rows[0]]
    for r in rows[1:]:
        if all(r[head.index(k)].strip() == v for k, v in where.items()):
            r[head.index(column)] = value
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


# ---------------------------------------------------------------------------------------------- the parsers
@needs_fixtures
def test_every_saved_nse_file_parses_to_its_own_date_in_the_right_format():
    seen = set()
    for p in sorted((BHAV / "nse").glob("*/*")):
        d = D(*map(int, re.search(r"(\d{4})(\d{2})(\d{2})", p.name).groups()))
        b = bh.parse_bhavcopy(p.read_bytes())
        assert (b.exchange, b.trade_date) == ("NSE", d), p.name
        assert b.fmt == ("nse_udiff" if d >= bh.UDIFF_START else "nse_old"), p.name
        assert b.fmt == bh.url_for("NSE", d)[0]
        kept = bh.pick_rows(b)
        assert {r.series for r in kept.values()} <= set(bh.NSE_SERIES) and len(kept) > 1000, p.name
        for isin in ("INE002A01018", "INE009A01021", "INE154A01025"):                 # RELIANCE, INFY, ITC
            assert kept[isin].close > 0 and kept[isin].prev_close > 0, (p.name, isin)
        seen.add(b.fmt)
    assert seen == {"nse_old", "nse_udiff"}


@needs_fixtures
def test_the_two_nse_formats_give_the_same_row_shape():
    old = bh.pick_rows(bh.parse_bhavcopy(body("nse", D(2023, 7, 20))))["INE002A01018"]
    new = bh.pick_rows(bh.parse_bhavcopy(body("nse", D(2024, 7, 8))))["INE002A01018"]
    for r in (old, new):
        assert isinstance(r, bh.PriceRow) and r.symbol == "RELIANCE" and r.series == "EQ"
        assert all(isinstance(x, Decimal) for x in (r.open, r.high, r.low, r.close, r.prev_close, r.traded_value))
        assert isinstance(r.volume, int) and isinstance(r.trades, int) and r.low <= r.close <= r.high
    assert old.traded_value > 10**9                  # rupees, not lakhs or crores


@needs_fixtures
def test_bse_old_and_udiff_files_parse_to_the_same_shape_and_are_keyed_by_scrip_code():
    old = bh.parse_bhavcopy(body("bse", D(2019, 1, 2)))
    new = bh.parse_bhavcopy(body("bse", D(2026, 3, 25)))
    assert (old.exchange, old.fmt, old.trade_date) == ("BSE", "bse_old", D(2019, 1, 2))
    assert (new.exchange, new.fmt, new.trade_date) == ("BSE", "bse_udiff", D(2026, 3, 25))
    for b, abb in ((old, "500002"), (new, "500002")):
        r = bh.pick_rows(b)[abb]
        assert r.isin == "INE117A01022" and r.code == abb and r.close > 0 and r.series.startswith("A")
    # HINDALCO's ISIN has two codes on 26 Mar 2025 (100440 "HINDALCO#" and 500440): the old key (the ISIN) would clash
    kept = bh.pick_rows(bh.parse_bhavcopy(body("bse", D(2025, 3, 26))))
    assert kept["500440"].close == Decimal("691.25") and kept["500440"].prev_close == Decimal("693.50")
    assert kept["100440"].isin == kept["500440"].isin


def test_an_html_page_saved_as_a_zip_a_damaged_zip_and_an_unknown_layout_are_refused():
    page = b"<!DOCTYPE html><html><body>Page not found</body></html>"
    with pytest.raises(bh.BhavError, match="HTML"):
        bh.parse_bhavcopy(page)
    with pytest.raises(bh.BhavError, match="HTML"):
        bh.parse_bhavcopy(rewrite_to_zip(page))                # the same page inside a zip
    with pytest.raises(bh.BhavError, match="damaged zip"):
        bh.parse_bhavcopy(b"PK\x03\x04 not really a zip")
    with pytest.raises(bh.BhavError, match="unknown bhavcopy layout"):
        bh.parse_bhavcopy(b"A,B,C\n1,2,3\n")
    with pytest.raises(bh.BhavError, match="empty"):
        bh.parse_bhavcopy(b"")


def rewrite_to_zip(raw: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("page.csv", raw)
    return out.getvalue()


@needs_fixtures
def test_a_file_with_two_trade_dates_is_refused():
    mixed = rewrite(body("nse", D(2019, 1, 3)), lambda t: t.replace("03-JAN-2019", "02-JAN-2019", 5))
    with pytest.raises(bh.BhavError, match="2 different trade dates"):
        bh.parse_bhavcopy(mixed)


@needs_fixtures
def test_two_rows_of_one_series_for_one_company_are_refused():
    def twice(t: str) -> str:
        lines = t.splitlines()
        row = next(x for x in lines if "INE002A01018" in x and ",EQ," in x)
        return "\n".join([*lines, row]) + "\n"
    with pytest.raises(bh.BhavError, match="two EQ rows"):
        bh.pick_rows(bh.parse_bhavcopy(rewrite(body("nse", D(2019, 1, 3)), twice)))


def test_urls_follow_each_exchanges_cutover_on_8_july_2024():
    assert bh.url_for("NSE", D(2024, 7, 8)) == (
        "nse_udiff", "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip")
    assert bh.url_for("NSE", D(2024, 7, 5))[1] == (
        "https://nsearchives.nseindia.com/content/historical/EQUITIES/2024/JUL/cm05JUL2024bhav.csv.zip")
    assert bh.url_for("BSE", D(2024, 7, 8))[1].endswith("/BhavCopy_BSE_CM_0_0_0_20240708_F_0000.CSV")
    assert bh.url_for("BSE", D(2024, 7, 5))[1].endswith("/EQ_ISINCODE_050724.zip")
    with pytest.raises(ValueError):
        bh.url_for("LSE", D(2024, 7, 5))


# ---------------------------------------------------------------------------------------------- the saved months
@needs_fixtures
def test_the_saved_months_hold_a_file_for_every_calendar_session_but_the_four_known_gaps():
    """The calendar proven by the exchange's own files, as far as the fixtures reach: every session has a file and no
    weekday holiday has one."""
    for y, m in FIXTURE_MONTHS:
        lo = D(y, m, 1)
        hi = D(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        have = {D(*map(int, re.search(r"(\d{4})(\d{2})(\d{2})", p.name).groups()))
                for p in (BHAV / "nse").glob(f"{y}/nse_{y}{m:02d}*")}
        want = set(calendars.sessions("xnse", lo, hi))
        if (y, m) == (2026, 10):                                  # the fixtures stop at 20 Oct 2026
            want = {d for d in want if d <= D(2026, 10, 20)}
        assert want - have == want & FIXTURE_GAPS, (y, m, sorted(want - have))
        assert not have - want, (y, m, sorted(have - want))


@needs_fixtures
def test_continuity_holds_across_every_saved_pair_of_sessions_including_the_ex_dates():
    """PREV_CLOSE on a day is the previous session's close, unadjusted also on ex-dates (Data Audit 00 §6): RELIANCE
    (20 Jul 2023), ITC (6 Jan 2025), BAJFINANCE (16 Jun 2025) and Tata Motors (14 Oct 2025) are in the months. Pairs
    across the fixture gaps have no previous file and are skipped."""
    isins = {r["ISIN Code"] for r in csv.DictReader((FIX / "universe" / "nifty50_20261019.csv").open())}
    assert len(isins) == 50
    days = {}
    for p in sorted((BHAV / "nse").glob("*/*")):
        b = bh.parse_bhavcopy(p.read_bytes())
        days[b.trade_date] = bh.pick_rows(b)
    # BAJFINANCE's ISIN changed with its 16 Jun 2025 split and bonus: the old one is the same company for this check
    for rows in days.values():
        if "INE296A01024" in rows:
            rows["INE296A01032"] = rows.pop("INE296A01024")
    compared, ex_dates = 0, set()
    for d, rows in days.items():
        prev = calendars.previous_session("xnse", d)
        if prev not in days:
            assert prev in FIXTURE_GAPS or prev.day in (30, 31) or prev.month != d.month or prev.year != d.year, (d, prev)
            continue
        for isin in isins & rows.keys() & days[prev].keys():
            compared += 1
            assert abs(rows[isin].prev_close - days[prev][isin].close) <= ep.TOLERANCE, (d, isin)
            ex_dates.add((d, isin))
    assert compared > 8000
    assert {(D(2023, 7, 20), "INE002A01018"), (D(2025, 1, 6), "INE154A01025"), (D(2025, 6, 16), "INE296A01032"),
            (D(2025, 10, 14), "INE155A01022")} <= ex_dates


def test_the_last_closed_session_waits_for_the_close_and_the_publishing_lag():
    # Tue 27 Oct 2026: closes 15:30 IST (10:00 UTC); the file is expected from 18:30 IST (13:00 UTC)
    assert ep.last_closed_session(datetime(2026, 10, 27, 12, 59, tzinfo=UTC)) == D(2026, 10, 26)
    assert ep.last_closed_session(datetime(2026, 10, 27, 13, 0, tzinfo=UTC)) == D(2026, 10, 27)
    assert ep.last_closed_session(datetime(2026, 10, 25, 9, 0, tzinfo=UTC)) == D(2026, 10, 23)      # a Sunday
    assert ep.last_closed_session(NOW) == D(2026, 10, 27)
    # a special session counts: Muhurat trading, Sunday 8 Nov 2026 (evening session), is after its close
    assert ep.last_closed_session(datetime(2026, 11, 9, 4, 0, tzinfo=UTC)) in (
        D(2026, 11, 9) - timedelta(days=1), D(2026, 11, 6))


# ---------------------------------------------------------------------------------------------- through the runner
class Exchange:
    """NSE's archive and BSE's download as a mock transport serving the saved files; `files` overrides a path with other
    bytes (None: the exchange answers 404), and `seen` records the requests."""

    def __init__(self):
        self.files: dict[str, bytes | None] = {}
        self.seen: list[httpx.Request] = []
        self.paths = {}
        for ex in ("nse", "bse"):
            for p in (BHAV / ex).glob("*/*"):
                d = D(*map(int, re.search(r"(\d{4})(\d{2})(\d{2})", p.name).groups()))
                self.paths[httpx.URL(bh.url_for(ex.upper(), d)[1]).path] = p

    def put(self, exchange: str, d: date, data: bytes | None) -> None:
        self.files[httpx.URL(bh.url_for(exchange, d)[1]).path] = data

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if path in self.files:
            data = self.files[path]
        else:
            data = self.paths[path].read_bytes() if path in self.paths else None
        return httpx.Response(404) if data is None else httpx.Response(200, content=data)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity prices test', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))
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
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zp-%')"
        c.execute(f"delete from equity.price_daily where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zp-%'")


def make_companies(names=tuple(CO), exchange="NSE", listed_on=None):
    """Test companies zp-<name> with their ISIN, symbol and BSE code, unpublished."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        nse, bse = (c.execute("select id from core.source where key = %s", (k,)).fetchone()[0] for k in ("nse", "bse"))
        for n in names:
            isin, code = CO[n]
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                            (f"zp-{n.lower()}", f"{n} test")).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, isin, nse_symbol, bse_code, price_exchange, listed_on)
                         values (%s, %s, %s, %s, %s, %s)""", (eid, isin, n, code, exchange, listed_on))
            for scheme, value, src in (("isin", isin, nse), ("nse_symbol", n, nse), ("bse_code", code, bse)):
                c.execute("""insert into core.entity_identifier (entity_id, scheme, value, valid, source_id)
                             values (%s, %s, %s, daterange(null, null, '[)'), %s)""", (eid, scheme, value, src))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def task() -> EquityPrices:
    class T(EquityPrices):
        key = KEY
    t = T()
    t.workflows = ("weekly", "backfill")
    return t


def go(ex, since=None, until=None, now=NOW, workflow="backfill"):
    http = HttpClient(transport=httpx.MockTransport(ex), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, workflow, [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now,
               since=since, until=until)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s
              order by id desc limit 1""", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def prices(slug=None):
    return q("""select e.slug, p.trade_date, p.exchange, p.series, p.close, p.prev_close from equity.price_daily p
                  join core.entity e on e.id = p.entity_id where e.slug like 'zp-%%' and (%s::text is null or e.slug = %s)
                 order by 2, 1""", slug, slug)


def published():
    return sorted(r[0] for r in q("select slug from core.entity where slug like 'zp-%' and is_published"))


JAN = {"since": D(2019, 1, 2), "until": D(2019, 1, 31)}     # 1 Jan 2019 (a session the fixtures lack) is left out


@pytest.mark.db
@needs_fixtures
def test_a_january_2019_window_loads_four_companies_and_publishes_them(s3):
    make_companies()
    code, tr = go(Exchange(), **JAN)
    assert code == 0 and tr[0] == "success", tr
    assert {n: (c["passed"], c["blocking"]) for n, c in checks(tr).items()} == {
        "sessions_present": (True, True), "calendar_proven": (True, True), "rows_matched": (True, True),
        "sane_prices": (True, True), "unchanged": (True, True), "continuity": (True, True),
        "continuity_rows": (True, False), "no_gaps": (True, False)}
    sessions = calendars.sessions("xnse", JAN["since"], JAN["until"])
    assert len(sessions) == 22 and tr[1] == 22 * 4 + 4                 # rows, plus the four companies published
    assert published() == ["zp-hdfcbank", "zp-infy", "zp-itc", "zp-reliance"]
    got = {(r[0], r[1]): r for r in prices()}
    assert {d for _, d in got} == set(sessions)
    r = got[("zp-infy", D(2019, 1, 3))]
    assert (r[2], r[3]) == ("NSE", "EQ") and r[4] > 0 and r[5] > 0
    assert "22 sessions" in checks(tr)["sessions_present"]["detail"]
    assert "84 rows agree" in checks(tr)["continuity"]["detail"]       # 22 days, the first has no stored previous close
    # every price points at the raw file it came from, whose content date is the session
    assert q("""select count(*) from equity.price_daily p join core.raw_file f on f.id = p.raw_file_id
                 where p.entity_id in (select id from core.entity where slug like 'zp-%')
                   and f.content_date = p.trade_date""")[0][0] == 22 * 4


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_run_writes_nothing(s3):
    make_companies()
    go(Exchange(), **JAN)
    before = prices()
    code, tr = go(Exchange(), **JAN)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert prices() == before


@pytest.mark.db
@needs_fixtures
def test_the_old_format_files_are_asked_for_with_the_nse_referer_and_nothing_goes_to_bse(s3):
    make_companies()
    ex = Exchange()
    go(ex, **JAN)
    assert {r.url.host for r in ex.seen} == {"nsearchives.nseindia.com"}
    assert all(r.headers["referer"] == "https://www.nseindia.com/" for r in ex.seen)
    assert ex.seen[0].url.path.endswith("/EQUITIES/2019/JAN/cm02JAN2019bhav.csv.zip")


@pytest.mark.db
@needs_fixtures
def test_a_missing_session_fails_the_run_naming_it_and_rolls_the_load_back(s3):
    """13 Feb 2019 is a session the fixtures lack, so the exchange 'answers 404' for it."""
    make_companies()
    code, tr = go(Exchange(), since=D(2019, 2, 1), until=D(2019, 2, 28))
    assert code == 1 and tr[0] == "checks_failed" and "sessions_present" in tr[3], tr
    assert "2019-02-13 (no such file)" in checks(tr)["sessions_present"]["detail"]
    assert prices() == [] and published() == []                        # nothing written, nobody published


@pytest.mark.db
@needs_fixtures
def test_html_served_with_status_200_and_the_wrong_days_file_both_count_as_missing(s3):
    make_companies()
    ex = Exchange()
    ex.put("NSE", D(2019, 1, 9), b"<!DOCTYPE html><html><body>Not found</body></html>")
    ex.put("NSE", D(2019, 1, 10), body("nse", D(2019, 1, 9)))         # the archive serves the previous day's file
    code, tr = go(ex, **JAN)
    detail = checks(tr)["sessions_present"]["detail"]
    assert code == 1 and "2019-01-09 (an HTML page, not a bhavcopy)" in detail, tr
    assert "2019-01-10 (the file is for 2019-01-09)" in detail


@pytest.mark.db
@needs_fixtures
def test_holidays_in_an_earlier_year_are_asked_for_and_none_may_have_a_file(s3):
    """October 2019: 2 Oct (Gandhi Jayanti), 8 Oct (Dussehra) and 21 Oct (the Maharashtra election) are holidays; 27 Oct
    (Muhurat, a Sunday) isn't in the fixtures, so the window ends on the 26th."""
    make_companies()
    ex = Exchange()
    code, tr = go(ex, since=D(2019, 10, 1), until=D(2019, 10, 26))
    assert code == 0, tr
    assert "3 weekday holidays asked for, none has a file" in checks(tr)["calendar_proven"]["detail"]
    assert {r.url.path.split("/")[-1] for r in ex.seen} >= {"cm02OCT2019bhav.csv.zip", "cm08OCT2019bhav.csv.zip",
                                                           "cm21OCT2019bhav.csv.zip"}
    # an archive that serves the previous day's file on a holiday isn't a file for the holiday
    ex = Exchange()
    ex.put("NSE", D(2019, 10, 2), body("nse", D(2019, 10, 1)))
    assert go(ex, since=D(2019, 10, 1), until=D(2019, 10, 26))[0] == 0


@pytest.mark.db
@needs_fixtures
def test_a_holiday_with_a_file_of_its_own_date_fails_the_calendar_check(s3):
    make_companies()
    ex = Exchange()
    ex.put("NSE", D(2019, 10, 2), rewrite(body("nse", D(2019, 10, 1)), lambda t: t.replace("01-OCT-2019", "02-OCT-2019")))
    code, tr = go(ex, since=D(2019, 10, 1), until=D(2019, 10, 26))
    assert code == 1 and tr[0] == "checks_failed", tr
    assert "NSE 2019-10-02" in checks(tr)["calendar_proven"]["detail"]
    assert prices() == []


@pytest.mark.db
@needs_fixtures
def test_the_run_s_own_year_is_not_probed_and_a_weekly_run_continues_after_the_latest_stored_day(s3):
    make_companies()
    go(Exchange(), since=D(2019, 1, 2), until=D(2019, 1, 11))
    assert max(r[1] for r in prices()) == D(2019, 1, 11)
    ex = Exchange()
    code, tr = go(ex, now=datetime(2019, 1, 20, 12, 0, tzinfo=UTC), workflow="weekly")     # Sunday 20 Jan 2019
    assert code == 0 and tr[0] == "success", tr
    assert sorted({r[1] for r in prices()})[-5:] == calendars.sessions("xnse", D(2019, 1, 14), D(2019, 1, 18))
    assert "no weekday holiday of an earlier year" in checks(tr)["calendar_proven"]["detail"]   # 2019 is the run's year
    assert len(ex.seen) == 5                                             # 14-18 Jan, and nothing else
    assert checks(tr)["continuity"]["detail"].startswith("20 rows agree")         # 5 days x 4, 14 Jan against the 11th


@pytest.mark.db
@needs_fixtures
def test_nothing_new_after_the_last_closed_session_is_a_clean_run_that_asks_for_no_file(s3):
    make_companies()
    ex = Exchange()
    code, tr = go(ex, since=D(2026, 10, 28), until=D(2026, 12, 31))     # 28 Oct 2026 hasn't closed yet at 11:30 IST
    assert code == 0 and tr[0] == "success" and tr[1] == 0 and ex.seen == [], tr
    assert "nothing new" in checks(tr)["sessions_present"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_window_is_clipped_to_the_last_closed_session(s3):
    make_companies(("RELIANCE",))
    ex = Exchange()
    ex.put("NSE", D(2026, 10, 21), None)
    code, tr = go(ex, since=D(2026, 10, 19), until=D(2026, 12, 31))
    # 19 and 20 Oct are saved; 21 to 27 Oct are not served by the mock, so they're named; 28 Oct onward isn't asked for
    assert code == 1 and "2026-10-27 (no such file)" in checks(tr)["sessions_present"]["detail"], tr
    assert not any("20261028" in r.url.path for r in ex.seen)


@pytest.mark.db
@needs_fixtures
def test_a_run_without_stored_prices_or_a_since_date_fails_and_says_to_backfill(s3):
    make_companies()
    code, tr = go(Exchange(), workflow="weekly")
    assert code == 1 and tr[0] == "failed" and "start with a backfill" in tr[3], tr


@pytest.mark.db
@needs_fixtures
def test_a_stale_file_breaks_continuity_for_most_rows_and_blocks(s3):
    """4 Jan 2019's file is replaced by 3 Jan's with the date changed, as an archive that served the previous day's
    content under the new date would: PREV_CLOSE is the 2 Jan close, not the stored 3 Jan close."""
    make_companies()
    ex = Exchange()
    ex.put("NSE", D(2019, 1, 4), rewrite(body("nse", D(2019, 1, 3)), lambda t: t.replace("03-JAN-2019", "04-JAN-2019")))
    code, tr = go(ex, since=D(2019, 1, 2), until=D(2019, 1, 4))
    assert code == 1 and tr[0] == "checks_failed" and "continuity" in tr[3], tr
    detail = checks(tr)["continuity"]["detail"]
    assert "NSE 2019-01-04:" in detail and "of 4 rows break" in detail
    assert prices() == []


@pytest.mark.db
@needs_fixtures
def test_a_single_row_that_breaks_continuity_only_warns_and_names_the_row(s3):
    make_companies()
    ex = Exchange()
    ex.put("NSE", D(2019, 1, 3), rewrite(body("nse", D(2019, 1, 3)),
                                          lambda t: set_cell(t, "PREVCLOSE", {"ISIN": "INE009A01021"}, "1.00")))
    code, tr = go(ex, since=D(2019, 1, 2), until=D(2019, 1, 4))
    assert code == 0 and tr[0] == "success", tr
    row = checks(tr)["continuity_rows"]
    assert not row["passed"] and not row["blocking"]
    assert "zp-infy 2019-01-03 (PREV_CLOSE 1.00" in row["detail"] and "zp-itc" not in row["detail"]
    assert checks(tr)["continuity"]["passed"]


@pytest.mark.db
@needs_fixtures
def test_a_price_that_breaks_high_low_close_order_fails_and_names_the_row(s3):
    make_companies()
    ex = Exchange()
    ex.put("NSE", D(2019, 1, 3), rewrite(body("nse", D(2019, 1, 3)),
                                          lambda t: set_cell(t, "HIGH", {"ISIN": "INE154A01025"}, "1.00")))
    code, tr = go(ex, since=D(2019, 1, 2), until=D(2019, 1, 4))
    assert code == 1 and not checks(tr)["sane_prices"]["passed"], tr
    assert "zp-itc 2019-01-03" in checks(tr)["sane_prices"]["detail"] and prices() == []


@pytest.mark.db
@needs_fixtures
def test_a_stored_row_re_loaded_with_a_different_value_is_a_check_with_both_values_not_a_crash(s3):
    make_companies()
    go(Exchange(), since=D(2019, 1, 2), until=D(2019, 1, 4))
    stored = prices("zp-infy")
    ex = Exchange()
    ex.put("NSE", D(2019, 1, 3), rewrite(body("nse", D(2019, 1, 3)),
                                          lambda t: set_cell(t, "VOLUME", {"ISIN": "INE009A01021"}, "7")
                                          if "VOLUME" in t.splitlines()[0].upper() else
                                          set_cell(t, "TOTTRDQTY", {"ISIN": "INE009A01021"}, "7")))
    code, tr = go(ex, since=D(2019, 1, 2), until=D(2019, 1, 4))
    assert code == 1 and tr[0] == "checks_failed" and "unchanged" in tr[3], tr
    detail = checks(tr)["unchanged"]["detail"]
    assert "zp-infy 2019-01-03 NSE: stored (EQ" in detail and ", 7, " in detail
    assert prices("zp-infy") == stored                                 # the stored rows stand


@pytest.mark.db
@needs_fixtures
def test_a_listed_company_without_a_row_warns_and_one_not_yet_listed_is_not_expected(s3):
    make_companies(("RELIANCE", "ITC", "INFY"))
    with psycopg.connect(ADMIN, autocommit=True) as c:
        # RELIANCE and ITC can no longer be found in any file (a changed ISIN and symbol the master doesn't know)
        for old, new in (("INE002A01018", "INE000000000"), ("INE154A01025", "INE000000002")):
            c.execute("update core.entity_identifier set value = %s where scheme = 'isin' and value = %s", (new, old))
        for old, new in (("RELIANCE", "GONE1"), ("ITC", "GONE2")):
            c.execute("update core.entity_identifier set value = %s where scheme = 'nse_symbol' and value = %s", (new, old))
        c.execute("update equity.company set listed_on = %s where nse_symbol = 'ITC'", (D(2019, 1, 4),))
    code, tr = go(Exchange(), since=D(2019, 1, 2), until=D(2019, 1, 7))     # sessions: 2, 3, 4 and 7 Jan
    gaps = checks(tr)["no_gaps"]
    assert code == 0 and tr[0] == "success", tr
    assert not gaps["passed"] and not gaps["blocking"]
    assert "zp-reliance (4 sessions: 2019-01-02, 2019-01-03, 2019-01-04)" in gaps["detail"]
    assert "zp-itc (2 sessions: 2019-01-04, 2019-01-07)" in gaps["detail"] and "zp-infy" not in gaps["detail"]
    assert {r[0] for r in prices()} == {"zp-infy"} and published() == ["zp-infy"]    # only a company with prices is published


@pytest.mark.db
@needs_fixtures
def test_an_isin_the_master_does_not_hold_is_matched_by_the_symbol_the_company_had_that_day(s3):
    """HDFC Bank's ISIN was INE040A01026 until its 2019 split; the master holds the new one, so Jan 2019 matches on the
    symbol. A symbol held only in a dated range doesn't match outside it."""
    make_companies(("HDFCBANK",))
    code, tr = go(Exchange(), since=D(2019, 1, 2), until=D(2019, 1, 4))
    assert code == 0 and len(prices()) == 3 and checks(tr)["no_gaps"]["passed"], tr
    wipe()
    make_companies(("HDFCBANK", "INFY"))
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""update core.entity_identifier set valid = daterange(null, %s, '[)')
                      where scheme = 'nse_symbol' and value = 'HDFCBANK'""", (D(2019, 1, 3),))
    code, tr = go(Exchange(), since=D(2019, 1, 2), until=D(2019, 1, 4))
    assert code == 0 and [r[1] for r in prices("zp-hdfcbank")] == [D(2019, 1, 2)], tr
    assert "zp-hdfcbank (2 sessions: 2019-01-03, 2019-01-04)" in checks(tr)["no_gaps"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_two_rows_for_one_company_in_a_file_fail_the_run(s3):
    """A second INFY row under an ISIN the master doesn't hold would be matched by its symbol too: not guessed between."""
    make_companies(("INFY",))

    def doubled(text: str) -> str:
        rows = list(csv.reader(io.StringIO(text)))
        head = [c.strip().upper() for c in rows[0]]
        extra = next(r for r in rows[1:] if r[head.index("ISIN")] == "INE009A01021").copy()
        extra[head.index("ISIN")] = "INE000000001"
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows([*rows, extra])
        return out.getvalue()
    ex = Exchange()
    ex.put("NSE", D(2019, 1, 3), rewrite(body("nse", D(2019, 1, 3)), doubled))
    code, tr = go(ex, since=D(2019, 1, 2), until=D(2019, 1, 4))
    assert code == 1 and not checks(tr)["rows_matched"]["passed"], tr
    assert "2019-01-03: two rows" in checks(tr)["rows_matched"]["detail"] and prices() == []


@pytest.mark.db
@needs_fixtures
def test_bse_prices_load_for_a_company_whose_price_exchange_is_bse_across_both_formats(s3):
    """The whole-series fallback: matched on BSE's scrip code, exchange 'BSE', old format (5 Jul 2024) then UDiFF
    (8 Jul 2024), consecutive sessions; no NSE file is asked for."""
    make_companies(("RELIANCE", "HDFCBANK"), exchange="BSE")
    ex = Exchange()
    code, tr = go(ex, since=D(2024, 7, 5), until=D(2024, 7, 8))
    assert code == 0 and tr[0] == "success", tr
    assert {r.url.host for r in ex.seen} == {"www.bseindia.com"}
    assert all(r.headers["origin"] == "https://www.bseindia.com" for r in ex.seen)
    rows = prices()
    assert [(r[0], r[1], r[2]) for r in rows] == [("zp-hdfcbank", D(2024, 7, 5), "BSE"), ("zp-reliance", D(2024, 7, 5), "BSE"),
                                                 ("zp-hdfcbank", D(2024, 7, 8), "BSE"), ("zp-reliance", D(2024, 7, 8), "BSE")]
    assert "4 rows agree" not in checks(tr)["continuity"]["detail"] and "2 rows agree" in checks(tr)["continuity"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_each_company_gets_rows_from_its_own_exchange_only(s3):
    make_companies(("RELIANCE",))
    make_companies(("INFY",), exchange="BSE")
    ex = Exchange()
    code, tr = go(ex, since=D(2024, 7, 5), until=D(2024, 7, 8))
    assert code == 0, tr
    assert {r.url.host for r in ex.seen} == {"nsearchives.nseindia.com", "www.bseindia.com"}
    assert [(r[0], r[1], r[2]) for r in prices()] == [
        ("zp-infy", D(2024, 7, 5), "BSE"), ("zp-reliance", D(2024, 7, 5), "NSE"),
        ("zp-infy", D(2024, 7, 8), "BSE"), ("zp-reliance", D(2024, 7, 8), "NSE")]


@pytest.mark.db
@needs_fixtures
def test_the_guard_holds_a_scheduled_run_until_the_task_has_a_recorded_success(s3):
    with psycopg.connect(JOB, autocommit=True) as c:
        t = EquityPrices()
        t.rules = {"weekly": "every 6 days"}
        assert not t.due(c, "weekly", NOW).due                                           # never ran
        assert t.due(c, "refresh", NOW) is not None
        assert "migration isn't pushed" in guard.equity_due(c, "x", "weekly", t.due(c, "refresh", NOW),
                                                            requires="equity.nope").reason


def test_the_session_before_the_calendars_first_year_is_none_not_an_error():
    assert ep._previous_session(D(2019, 1, 1)) is None
    assert ep._previous_session(D(2019, 1, 2)) == D(2019, 1, 1)


@pytest.mark.db
@needs_fixtures
def test_a_window_starting_on_the_first_session_of_2019_runs_its_checks_instead_of_crashing(s3):
    make_companies()
    code, tr = go(Exchange(), since=D(2019, 1, 1), until=D(2019, 1, 31))
    # the fixtures lack the 1 Jan 2019 file, so the run fails by its check, naming the session (not a CalendarYearError)
    assert code != 0 and checks(tr)["sessions_present"]["passed"] is False
    assert "2019-01-01" in checks(tr)["sessions_present"]["detail"]
    assert checks(tr)["continuity"]["passed"] is True


OLD = ("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
       "INFY,EQ,10,12,9,11,11,10,100,1100,{d},5,INE009A01021,\n")


@pytest.mark.parametrize("text", ["13-Jul-2020", "13-JUL-20", "13-07-2020", "2020-07-13"])
def test_old_format_trade_dates_in_other_spellings_still_read_as_the_same_day(text):
    assert bh.parse_bhavcopy(OLD.format(d=text).encode()).trade_date == D(2020, 7, 13)


def test_an_unreadable_trade_date_is_refused_and_the_error_shows_the_cell():
    with pytest.raises(bh.BhavError, match=r"readable trade date.*'INFY'"):
        bh.parse_bhavcopy(OLD.format(d="n/a").encode())
