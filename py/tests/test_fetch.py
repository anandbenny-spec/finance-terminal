"""The shared HTTP client and raw-file registration (Prompt 01 B2; Architecture 02 §1.5): pacing, retries, the
not-modified path and raw files that read back hash-verified. The network is a mock transport and the clock a fake
one; storage is a local S3-protocol server (moto), as in test_storage."""
import hashlib
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.jobs import fetch
from ft.jobs.fetch import Fetcher, FetchError, HttpClient, retry_after
from ft.jobs.runner import Task, connect, run
from ft.storage import Storage

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
SECRET = "k" * 32                                  # a stand-in for FRED_API_KEY
T0 = datetime(2026, 10, 28, 18, 0, tzinfo=UTC)


class FakeTime:
    """monotonic() and sleep() for the client: sleeping moves the clock; each request takes `latency` seconds."""

    def __init__(self, latency: float = 0.0):
        self.t, self.sleeps, self.latency = 1000.0, [], latency

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 6))
        self.t += s

    def clock(self):
        return T0 + timedelta(seconds=self.t - 1000.0)


def client(handler, ft: FakeTime | None = None, rand=lambda: 0.0) -> tuple[HttpClient, FakeTime]:
    ft = ft or FakeTime()

    def timed(request):
        ft.t += ft.latency
        return handler(request)
    return HttpClient(transport=httpx.MockTransport(timed), monotonic=ft.monotonic, sleep=ft.sleep, rand=rand,
                      clock=ft.clock), ft


def replies(*responses):
    """A handler that answers with these, in order (an exception instance is raised instead)."""
    it, seen = iter(responses), []

    def handler(request):
        seen.append(request)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    handler.seen = seen
    return handler


def ok(n: int, status: int = 200):
    return replies(*[httpx.Response(status, text="ok") for _ in range(n)])


FED ="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm"
RBI = "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63742"
FRED = "https://api.stlouisfed.org/fred/series/observations"


# ------------------------------------------------------------------ pacing
def test_pacing_waits_the_sites_gap_with_jitter():
    c, ft = client(ok(2), rand=lambda: 0.5)
    c.get(FED); c.get(FED)
    assert ft.sleeps == [1.5]                       # Fed: 1 s + 0.5 of the 1 s jitter
    c2, ft2 = client(ok(2), rand=lambda: 0.999)
    c2.get(RBI); c2.get(RBI)
    assert 1.99 < ft2.sleeps[0] < 2.0               # RBI: 1-2 s
    c3, ft3 = client(ok(2), rand=lambda: 0.999)
    c3.get(FRED); c3.get(FRED)
    assert ft3.sleeps == [0.5]                      # FRED: 0.5 s, no jitter


def test_pacing_is_per_site_and_counts_time_already_passed():
    c, ft = client(ok(4))
    c.get(FED); c.get(FRED)
    assert ft.sleeps == []                          # another site doesn't wait
    ft.t += 0.4
    c.get(FED)
    assert ft.sleeps == [0.6]                       # 1 s gap, 0.4 s already gone
    ft.t += 5
    c.get(FED)
    assert ft.sleeps == [0.6]                       # long enough ago: no wait


def test_pacing_measures_from_the_end_of_the_last_request():
    c, ft = client(ok(2), FakeTime(latency=3.0))
    c.get(FED); c.get(FED)
    assert ft.sleeps == [1.0]                       # a slow response doesn't count as the gap


# ------------------------------------------------------------------ retries
def test_retries_on_5xx_with_backoff_and_jitter_then_succeeds():
    h = replies(httpx.Response(503), httpx.Response(502), httpx.Response(200, text="ok"))
    c, ft = client(h, rand=lambda: 0.25)
    stats = Counter()
    r = c.get(FED, stats=stats)
    assert (r.status, r.attempts, r.body) == (200, 3, b"ok")
    assert ft.sleeps == [2.5, 4.5]                  # 2·2^attempt + 2·rand
    assert stats == {"503": 1, "502": 1, "200": 1, "retries": 2}


def test_gives_up_after_three_retries():
    c, ft = client(ok(4, 500))
    stats = Counter()
    r = c.get(FED, stats=stats)
    assert (r.status, r.attempts) == (500, 4) and stats["500"] == 4 and stats["retries"] == 3
    assert ft.sleeps == [2.0, 4.0, 8.0]


def test_retry_after_seconds_is_honoured():
    c, ft = client(replies(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)))
    stats = Counter()
    assert c.get(RBI, stats=stats).status == 200
    assert ft.sleeps == [7.0] and stats["429"] == 1


def test_retry_after_date_is_read_as_utc():
    when = format_datetime(T0 + timedelta(seconds=30), usegmt=True)
    c, ft = client(replies(httpx.Response(503, headers={"Retry-After": when}), httpx.Response(200)))
    assert c.get(FED).status == 200
    assert ft.sleeps == [30.0]
    assert retry_after("garbage", T0) is None and retry_after("", T0) is None
    assert retry_after(format_datetime(T0 - timedelta(hours=1), usegmt=True), T0) == 0.0


def test_retry_after_beyond_the_cap_fails_without_waiting():
    c, ft = client(replies(httpx.Response(429, headers={"Retry-After": "3600"})))
    stats = Counter()
    with pytest.raises(FetchError) as e:
        c.get(FED, stats=stats)
    assert e.value.status == 429 and "3600" in str(e.value)
    assert ft.sleeps == [] and stats["429"] == 1


def test_403_is_counted_not_retried():
    h = replies(httpx.Response(403))
    c, ft = client(h)
    stats = Counter()
    assert c.get(RBI, stats=stats).status == 403
    assert len(h.seen) == 1 and stats == {"403": 1} and ft.sleeps == []


def test_network_errors_are_retried_and_never_leak_the_url():
    c, _ = client(replies(httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), httpx.Response(200)))
    stats = Counter()
    assert c.get(FED, stats=stats).status == 200 and stats["errors"] == 2
    c2, _ = client(replies(*[httpx.ConnectError(f"refused {FRED}?api_key={SECRET}")] * 4))
    with pytest.raises(FetchError) as e:
        c2.get(FRED, record_url=FRED + "?series_id=DGS10", params={"series_id": "DGS10", "api_key": SECRET})
    assert SECRET not in str(e.value) and "4 attempts" in str(e.value)


def test_unlisted_hosts_and_redirects_to_them_are_refused():
    h = replies()
    c, _ = client(h)
    for url in ("https://example.com/x", "ftp://www.federalreserve.gov/x", "https://federalreserve.gov.evil.com/"):
        with pytest.raises(FetchError, match="isn't"):
            c.get(url)
    assert h.seen == []
    h2 = replies(httpx.Response(302, headers={"Location": "https://example.com/elsewhere"}), httpx.Response(200))
    c2, _ = client(h2)
    with pytest.raises(FetchError, match="redirected to example.com"):
        c2.get(FED)
    assert len(h2.seen) == 1                        # the unlisted host was never contacted


def test_redirects_within_listed_sites_are_followed():
    h = replies(httpx.Response(301, headers={"Location": "/monetarypolicy/new.htm"}),
                httpx.Response(302, headers={"Location": "https://rbidocs.rbi.org.in/x.pdf"}), httpx.Response(200, text="pdf"))
    c, _ = client(h)
    r = c.get(FED)
    assert (r.status, r.body, r.url) == (200, b"pdf", FED)
    assert [str(x.url) for x in h.seen] == [FED, "https://www.federalreserve.gov/monetarypolicy/new.htm",
                                           "https://rbidocs.rbi.org.in/x.pdf"]


def test_subdomains_of_a_listed_site_are_allowed():
    c, _ = client(ok(2))
    assert c.get("https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/X.PDF").status == 200
    assert c.get("https://website.rbi.org.in/x").status == 200


def test_oversized_responses_fail(monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 10)
    c, _ = client(replies(httpx.Response(200, content=b"x" * 11)))
    with pytest.raises(FetchError, match="larger than 10 bytes"):
        c.get(FED)


# ------------------------------------------------------------------ raw files and the not-modified path (database)
PORT = 5598
ENDPOINT = f"http://127.0.0.1:{PORT}"
BUCKET = "fetch-raw"


@pytest.fixture(scope="module")
def s3():
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                      aws_access_key_id="test", aws_secret_access_key="test")
    s3.create_bucket(Bucket=BUCKET)
    yield s3
    srv.stop()


def storage():
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


@pytest.fixture
def task_run():
    with psycopg.connect(JOB, autocommit=True) as c:
        jr = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('daily','cli','local') returning id").fetchone()[0]
        return c.execute("insert into ops.task_run (job_run_id, task_key) values (%s, 'policy.fed_documents') returning id",
                         (jr,)).fetchone()[0]


class Site:
    """A page that answers conditional requests the way a real server does (ETag and Last-Modified)."""

    def __init__(self, body: bytes, *, etag: str | None = '"v1"', last_modified: str | None = None,
                 content_type: str = "text/html; charset=utf-8"):
        self.body, self.etag, self.last_modified, self.type, self.seen = body, etag, last_modified, content_type, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.etag and request.headers.get("if-none-match") == self.etag:
            return httpx.Response(304, headers={"ETag": self.etag})
        if self.last_modified and request.headers.get("if-modified-since") == self.last_modified:
            return httpx.Response(304)
        headers = {"Content-Type": self.type}
        if self.etag:
            headers["ETag"] = self.etag
        if self.last_modified:
            headers["Last-Modified"] = self.last_modified
        return httpx.Response(200, content=self.body, headers=headers)


def fetcher(handler, task_run_id) -> Fetcher:
    c, _ = client(handler)
    return Fetcher(c, connect(JOB, autocommit=True, role="fetch"), storage, task_run_id)


def unique(url: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}t={uuid.uuid4().hex}"


@pytest.mark.db
def test_a_fetched_page_is_stored_registered_and_reads_back_hash_verified(s3, task_run):
    body = ("<html><body><p>The Committee decided to maintain the target range.</p></body></html>"
            + uuid.uuid4().hex).encode()
    url, site = unique(FED), Site(body)
    f = fetcher(site, task_run)
    got = f.get(url, source="fed")
    assert (got.status, got.changed, got.new_file, got.body) == (200, True, True, body)
    assert got.sha256 == hashlib.sha256(body).digest() and got.media_type == "text/html"
    key, src, sha, n, mt, tr, at = q("""select object_key, source_url, sha256, bytes, media_type, task_run_id, fetched_at
                                        from core.raw_file where id=%s""", got.raw_file_id)[0]
    assert (src, bytes(sha), n, mt, tr, at) == (url, got.sha256, len(body), "text/html", task_run, got.fetched_at)
    assert got.fetched_at.utcoffset() == timedelta(0)
    assert key == f"raw/fed/{got.sha256.hex()[:2]}/{got.sha256.hex()}.html.gz"
    assert storage().get(key) == body                       # the storage function checks the hash on every read
    assert f.read(got.raw_file_id) == body
    assert q("select etag, status, sha256 = %s from ops.http_cache where url=%s", got.sha256, url) == [('"v1"', 200, True)]
    assert f.stats() == {"http": {"200": 1, "403": 0, "429": 0, "new_raw_files": 1}, "files": 1}
    f.close()


@pytest.mark.db
def test_not_modified_returns_the_stored_copy_without_downloading(s3, task_run):
    body = f"<p>Statement {uuid.uuid4().hex}</p>".encode()
    url, site = unique(FED), Site(body, etag='"abc"', last_modified="Wed, 28 Oct 2026 18:00:00 GMT")
    f = fetcher(site, task_run)
    first = f.get(url, source="fed")
    before = q("select count(*) from core.raw_file")[0][0]
    again = f.get(url, source="fed")
    sent = site.seen[-1].headers
    assert sent["if-none-match"] == '"abc"' and sent["if-modified-since"] == "Wed, 28 Oct 2026 18:00:00 GMT"
    assert (again.status, again.changed, again.body, again.new_file) == (304, False, None, False)
    assert (again.raw_file_id, again.sha256) == (first.raw_file_id, first.sha256)
    assert q("select count(*) from core.raw_file")[0][0] == before
    assert q("select status, etag from ops.http_cache where url=%s", url) == [(304, '"abc"')]
    assert f.read(again.raw_file_id) == body
    assert f.stats()["http"] == {"200": 1, "304": 1, "403": 0, "429": 0, "new_raw_files": 1}
    assert f.get(url, source="fed", conditional=False).status == 200     # on purpose: download again
    assert "if-none-match" not in site.seen[-1].headers
    f.close()


@pytest.mark.db
def test_same_bytes_from_a_server_without_validators_store_nothing_new(s3, task_run):
    body = f"<p>No ETag here {uuid.uuid4().hex}</p>".encode()
    url, site = unique(RBI), Site(body, etag=None)
    f = fetcher(site, task_run)
    first = f.get(url, source="rbi")
    rows, objects = q("select count(*) from core.raw_file")[0][0], s3.list_objects_v2(Bucket=BUCKET)["KeyCount"]
    second = f.get(url, source="rbi")
    assert "if-none-match" not in site.seen[-1].headers
    assert (second.status, second.changed, second.new_file, second.raw_file_id) == (200, False, False, first.raw_file_id)
    assert q("select count(*) from core.raw_file")[0][0] == rows
    assert s3.list_objects_v2(Bucket=BUCKET)["KeyCount"] == objects
    site.body = body + b" revised"
    third = f.get(url, source="rbi")
    assert (third.changed, third.new_file) == (True, True) and third.raw_file_id != first.raw_file_id
    assert bytes(q("select sha256 from ops.http_cache where url=%s", url)[0][0]) == third.sha256
    f.close()


@pytest.mark.db
def test_secret_params_are_sent_but_never_recorded(s3, task_run):
    seen = []

    def api(request):
        seen.append(request)
        return httpx.Response(200, json={"observations": [{"date": "2026-10-27", "value": "4.02"}], "n": uuid.uuid4().hex})
    f = fetcher(api, task_run)
    got = f.get(FRED, source="fred", params={"series_id": "DGS10", "file_type": "json"}, secret_params={"api_key": SECRET})
    assert seen[0].url.params["api_key"] == SECRET
    assert got.url == f"{FRED}?series_id=DGS10&file_type=json" and got.media_type == "application/json"
    assert q("select count(*) from core.raw_file where source_url like %s", f"%{SECRET}%")[0][0] == 0
    assert q("select count(*) from ops.http_cache where url like %s", f"%{SECRET}%")[0][0] == 0
    assert q("select source_url from core.raw_file where id=%s", got.raw_file_id)[0][0] == got.url
    with pytest.raises(ValueError):
        f.get(FRED + "?series_id=DGS2", source="fred", params={"file_type": "json"})
    f.close()


@pytest.mark.db
def test_allowed_statuses_return_without_a_file_and_others_raise(s3, task_run):
    f = fetcher(replies(httpx.Response(404), httpx.Response(404), httpx.Response(403)), task_run)
    url = unique(FED)
    missing = f.get(url, source="fed", allow=(404,))
    assert (missing.status, missing.stored, missing.body) == (404, False, None)
    with pytest.raises(FetchError) as e:
        f.get(url, source="fed")
    assert e.value.status == 404
    with pytest.raises(FetchError) as e:
        f.get(url, source="fed")
    assert e.value.status == 403
    assert q("select count(*) from ops.http_cache where url=%s", url)[0][0] == 0
    assert f.stats()["http"] == {"403": 1, "404": 2, "429": 0, "new_raw_files": 0}
    f.close()


@pytest.mark.db
def test_a_304_to_a_request_without_validators_is_an_error(s3, task_run):
    f = fetcher(replies(httpx.Response(304)), task_run)
    with pytest.raises(FetchError, match="no validators"):
        f.get(unique(FED), source="fed")
    f.close()


# The key must stay unseeded in core.source: it was "nse" until migration 0019 seeded that row (module 2).
@pytest.mark.db
def test_an_unknown_source_key_is_refused(s3, task_run):
    f = fetcher(replies(), task_run)
    with pytest.raises(ValueError, match="no core.source row"):
        f.get(FED, source="no_such_source")
    f.close()


# ------------------------------------------------------------------ the runner: ctx.fetcher and the run log
@pytest.mark.db
def test_the_runner_gives_each_task_a_fetcher_and_logs_its_counts(s3):
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values ('test.b2_fetch', 'test', 'B2 fetch test', '{daily}', '1 hour', '1 day', false)
                     on conflict (key) do nothing""")
    body = f"<p>{uuid.uuid4().hex}</p>".encode()
    url, site = unique(FED), Site(body, etag='"r1"')
    outcomes = {}

    class Fetching(Task):
        key = "test.b2_fetch"
        workflows = ("daily",)

        def fetch(self, ctx):
            a = ctx.fetcher.get(url, source="fed")
            b = ctx.fetcher.get(url, source="fed")
            outcomes.update(a=a, b=b)
            return [a, b]

        def load(self, tx, fetched, tr):
            return len(fetched)

    http, _ = client(site)
    code = run(JOB, "refresh", [Fetching()], only="test.b2_fetch", force=True, http=http, storage=storage)
    assert code == 0
    assert (outcomes["a"].status, outcomes["b"].status) == (200, 304)
    stats, files, status = q("""select http_stats, files_fetched, status from ops.task_run
                                where task_key='test.b2_fetch' order by id desc limit 1""")[0]
    assert status == "success" and files == 1
    assert stats == {"200": 1, "304": 1, "403": 0, "429": 0, "new_raw_files": 1}
    tr = q("select max(id) from ops.task_run where task_key='test.b2_fetch'")[0][0]
    assert q("select count(*) from core.raw_file where task_run_id=%s", tr)[0][0] == 1


@pytest.mark.db
def test_a_task_that_fetches_nothing_logs_no_http_stats():
    class Quiet(Task):
        key = "test.b2_fetch"
        workflows = ("daily",)
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values ('test.b2_fetch', 'test', 'B2 fetch test', '{daily}', '1 hour', '1 day', false)
                     on conflict (key) do nothing""")
    assert run(JOB, "refresh", [Quiet()], only="test.b2_fetch", force=True) == 0
    assert q("""select http_stats, files_fetched from ops.task_run where task_key='test.b2_fetch'
                order by id desc limit 1""") == [(None, 0)]


@pytest.mark.db
def test_runner_sessions_are_utc_whatever_the_server_default():
    odd = f"{JOB} options='-c TimeZone=Asia/Kolkata'"
    with psycopg.connect(odd) as raw:
        assert raw.execute("show timezone").fetchone()[0] == "Asia/Kolkata"
    for autocommit in (True, False):
        with connect(odd, autocommit=autocommit) as c:
            assert c.execute("show timezone").fetchone()[0] == "UTC"
            assert c.autocommit is autocommit
            assert c.execute("select now()").fetchone()[0].utcoffset() == timedelta(0)


# ------------------------------------------------------------------ browser impersonation (Yahoo: issue #13, option (a))
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/%5EGSPC"


def test_yahoo_nse_and_bse_impersonate_chrome_by_default_and_no_other_site_does():
    c = HttpClient()
    try:
        for url in (YAHOO, "https://query1.finance.yahoo.com/v1/test/getcrumb", "https://fc.yahoo.com/"):
            t = c._http._transport_for_url(httpx.URL(url))
            assert isinstance(t, fetch.CurlTransport) and t.target == "chrome", url
        for url in (FED, RBI, FRED):
            assert not isinstance(c._http._transport_for_url(httpx.URL(url)), fetch.CurlTransport), url
    finally:
        c.close()
    # NSE and BSE impersonate too (2A-04: www.nseindia.com is behind Akamai; BSE's API as the audit kit used it)
    assert [s.key for s in fetch.SITES if s.impersonate] == ["yahoo", "nse", "bse", "imf"]


def test_an_impersonating_site_keeps_pacing_retries_and_counts():
    ft, plain, yahoo = FakeTime(), replies(httpx.Response(200, text="fed")), replies(
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text="chart"))
    c = HttpClient(transport=httpx.MockTransport(plain), site_transport=lambda s: httpx.MockTransport(yahoo),
                   monotonic=ft.monotonic, sleep=ft.sleep, rand=lambda: 0.0, clock=ft.clock)
    stats = Counter()
    assert c.get(FED, stats=stats).body == b"fed"
    r = c.get(YAHOO, stats=stats)
    assert r.body == b"chart" and r.attempts == 2
    assert len(plain.seen) == 1 and len(yahoo.seen) == 2            # each site through its own transport
    assert ft.sleeps == [7.0]                                         # Retry-After honoured (more than the backoff)
    assert stats == Counter({"200": 2, "429": 1, "retries": 1})


def test_a_cookie_request_can_leave_a_redirect_unfollowed():
    h = replies(httpx.Response(302, headers={"Location": "https://www.example.com/", "Set-Cookie": "A3=v; Domain=.yahoo.com"}))
    c, _ = client(h)
    r = c.get("https://fc.yahoo.com/", follow_redirects=False)
    assert r.status == 302 and len(h.seen) == 1                      # the unlisted host is never contacted


class FakeCurl:
    """Stands in for a curl_cffi Session: records each call, answers `reply` or raises it."""

    def __init__(self, reply):
        self.reply, self.calls, self.closed = reply, [], False

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def close(self):
        self.closed = True


class CurlReply:
    def __init__(self, status, headers, content):
        self.status_code, self.headers, self.content = status, httpx.Headers(headers), content


def test_the_curl_transport_sends_the_browsers_headers_and_hands_cookies_to_the_client():
    body = b'{"chart": {}}'
    fake = FakeCurl(CurlReply(200, [("content-encoding", "gzip"), ("content-length", "9"), ("etag", '"e1"'),
                                    ("set-cookie", "A3=one; Domain=.yahoo.com; Path=/"),
                                    ("set-cookie", "B=two; Domain=.yahoo.com; Path=/")], body))
    t = fetch.CurlTransport("chrome", session=fake)
    c = HttpClient(transport=httpx.MockTransport(ok(1)), site_transport=lambda s: t, sleep=lambda s: None)
    r = c.get(YAHOO, params={"interval": "1d"}, headers={"If-None-Match": '"e0"'})
    assert r.status == 200 and r.body == body                        # already decoded: not gunzipped again
    assert "content-encoding" not in r.headers and r.headers["etag"] == '"e1"'
    method, url, kw = fake.calls[0]
    assert method == "GET" and url == YAHOO + "?interval=1d"
    sent = {k.lower(): v for k, v in kw["headers"]}
    assert sent == {"if-none-match": '"e0"'}                          # no httpx User-Agent, Accept or Accept-Language
    assert kw["allow_redirects"] is False and kw["discard_cookies"] is True and kw["impersonate"] == "chrome"
    c.get(YAHOO)                                                      # the client's jar now sends both cookies
    assert {k.lower(): v for k, v in fake.calls[1][2]["headers"]}["cookie"] in ("A3=one; B=two", "B=two; A3=one")
    c.close()
    assert fake.closed


def test_curl_errors_become_retried_network_errors_without_the_url():
    from curl_cffi.requests import exceptions as cx
    secret_url = FRED + "?api_key=" + SECRET
    for err, kind in ((cx.Timeout(secret_url), "ReadTimeout"), (cx.ConnectionError(secret_url), "ConnectError")):
        fake = FakeCurl(err)
        c = HttpClient(transport=httpx.MockTransport(ok(1)),
                       site_transport=lambda s, fake=fake: fetch.CurlTransport("chrome", session=fake),
                       sleep=lambda s: None)
        stats = Counter()
        with pytest.raises(FetchError) as e:
            c.get(YAHOO, stats=stats)
        assert kind in str(e.value) and SECRET not in str(e.value) and e.value.__cause__ is None
        assert len(fake.calls) == 4 and stats["errors"] == 4 and stats["retries"] == 3


def test_the_curl_transport_impersonates_chrome_on_the_wire():
    """A real curl_cffi request to a local server: Chrome's User-Agent (not ours), a gzip body decoded once, and the
    server's cookies kept by the client."""
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from curl_cffi import requests as curl
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers.items()))
            z = gzip.compress(b'{"ok": true}')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(z)))
            self.send_header("Set-Cookie", "A3=wire; Path=/")
            self.end_headers()
            self.wfile.write(z)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        site = fetch.Site("local", ("127.0.0.1",), 0.0, impersonate="chrome")
        session = curl.Session(impersonate="chrome", trust_env=False)    # no proxy for a local server
        c = HttpClient(sites=(site,), site_transport=lambda s: fetch.CurlTransport("chrome", session=session))
        url = f"http://127.0.0.1:{srv.server_address[1]}/chart"
        r = c.get(url)
        assert r.status == 200 and r.body == b'{"ok": true}'
        c.get(url)
        c.close()
    finally:
        srv.shutdown()
    ua = seen[0].get("User-Agent", "")
    assert "Chrome/" in ua and "FinanceTerminal" not in ua
    first, second = ({k.lower(): v for k, v in h.items()} for h in seen)
    assert "cookie" not in first and second.get("cookie") == "A3=wire"
