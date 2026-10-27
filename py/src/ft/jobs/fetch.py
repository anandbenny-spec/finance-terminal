"""The shared HTTP client and raw-file registration (Architecture 02 §1.5; Prompt 01 B2). Every fetch goes through
here, and every body it downloads reaches the storage function and `core.raw_file` before any load.

- **Per-site pacing** (`SITES`): a minimum gap between the end of one request to a site and the start of the next,
  plus random jitter where the docs give a range (Fed and RBI 1-2 s). A host that isn't listed is refused, and so is
  a redirect to one, before it's contacted: adding a data source is Anand's call (CLAUDE.md §7).
- **Retries:** up to 3, with exponential backoff and jitter, on 429, 5xx and network errors. `Retry-After` is honoured:
  the next attempt waits at least that long, and a wait longer than `MAX_WAIT` fails the fetch instead of stalling a
  watch poll. A 403 is a refusal, not a hiccup: counted, never retried.
- **Counted:** every attempt's status goes into the task run's `http_stats` ("429" and "403" always present).
- **Conditional requests** through `ops.http_cache`: the URL's ETag and Last-Modified go out as If-None-Match and
  If-Modified-Since, so an unchanged page answers 304 and isn't downloaded or parsed again. A 200 carrying the bytes
  this URL last returned is unchanged too (same hash: no new object, no new raw file).
- **UTC:** `fetched_at` is timezone-aware UTC and HTTP dates are read as UTC; database sessions are UTC
  (`runner.connect`).
- **Browser impersonation, per site** (`Site.impersonate`): Yahoo refuses a plain HTTP client from GitHub's runners
  (HTTP 429 on every attempt: issue #13, 24 Oct 2026), so its requests go out through curl_cffi with Chrome's TLS
  fingerprint and default headers, as the yfinance library's do (Anand, 24 Oct 2026: option (a) on #13). Only the
  transport changes: pacing, retries, the counts, conditional requests and raw files are the same for every site.
- **Secrets are sent, never recorded:** `secret_params` (FRED's `api_key`) go on the request only, so the URLs in
  `ops.http_cache`, `core.raw_file`, errors and logs never hold them.
"""
from __future__ import annotations

import hashlib
import random
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx

from ft.storage import Storage

# Browser-like, with the project's name: the audit kits' header, which Fed and RBI pages served (Data Audit 01).
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 FinanceTerminal")


@dataclass(frozen=True)
class Site:
    key: str
    domains: tuple[str, ...]       # a host matches a domain itself or any subdomain of it
    gap: float                     # seconds from the end of one request to the start of the next
    jitter: float = 0.0            # plus up to this many seconds, at random
    impersonate: str | None = None  # a curl_cffi browser target ("chrome"): this site's requests go through curl_cffi

    def matches(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return any(host == d or host.endswith("." + d) for d in self.domains)


# Architecture 02 §1.5. Module 2 adds NSE (about 1 s, browser impersonation through curl_cffi) and SEC (under 10
# requests a second with a declared User-Agent, SEC_USER_AGENT) when its tasks are built.
SITES: tuple[Site, ...] = (
    Site("fred", ("stlouisfed.org",), 0.5),              # api.stlouisfed.org, fred.stlouisfed.org
    Site("fed", ("federalreserve.gov",), 1.0, 1.0),
    Site("rbi", ("rbi.org.in",), 1.0, 1.0),              # rbi.org.in, website.rbi.org.in, rbidocs.rbi.org.in
    # query1/query2.finance.yahoo.com (the chart API and the crumb) and fc.yahoo.com (the cookie): one source.
    Site("yahoo", ("finance.yahoo.com", "fc.yahoo.com"), 1.5, impersonate="chrome"),
    Site("coingecko", ("api.coingecko.com",), 2.5),       # the Demo plan's 30 calls a minute; the daily snapshot makes 2
)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
RETRIES = 3
BACKOFF = 2.0                      # seconds: about 2, 4 and 8, each plus up to BACKOFF more at random
MAX_WAIT = 120.0                   # a Retry-After longer than this fails the fetch
MAX_BYTES = 45 * 1024 * 1024       # under Supabase Storage's 50 MB per file
TIMEOUT = httpx.Timeout(60.0, connect=15.0)

# Media type -> stored file extension (the storage function gzips the text types).
MEDIA_EXT = {
    "text/html": "html", "application/xhtml+xml": "html", "application/pdf": "pdf", "application/json": "json",
    "application/xml": "xml", "text/xml": "xml", "application/rss+xml": "xml", "application/atom+xml": "xml",
    "text/csv": "csv", "text/plain": "txt",
}
EXT_MEDIA = {"htm": "text/html", "html": "text/html", "aspx": "text/html", "pdf": "application/pdf",
             "json": "application/json", "xml": "application/xml", "csv": "text/csv", "txt": "text/plain"}


class FetchError(Exception):
    """A fetch that failed for good. The message names the recorded URL (never a secret) and the reason."""

    def __init__(self, url: str, message: str, status: int | None = None):
        super().__init__(f"{url}: {message}")
        self.url, self.status = url, status


def utcnow() -> datetime:
    return datetime.now(UTC)


def site_for(url: str, sites: Iterable[Site] = SITES) -> Site:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError(url, f"scheme {parts.scheme or '(none)'} isn't fetched")
    host = parts.hostname or ""
    for s in sites:
        if s.matches(host):
            return s
    raise FetchError(url, f"{host or 'no host'} isn't a listed source (ft.jobs.fetch.SITES); adding one is Anand's call")


def retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds a Retry-After header asks for: a number of seconds or an HTTP date (read as UTC)."""
    if not value or not (v := value.strip()):
        return None
    if v.isdigit():
        return float(v)
    try:
        when = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


@dataclass(frozen=True)
class Response:
    url: str                      # the recorded URL (no secrets)
    status: int
    headers: httpx.Headers
    body: bytes
    fetched_at: datetime          # UTC
    attempts: int


# Headers httpx adds on its own. An impersonated request drops them, so the browser's own defaults go out instead (a
# User-Agent that disagrees with the TLS fingerprint is itself a tell).
_HTTPX_DEFAULTS = frozenset({"host", "user-agent", "accept", "accept-encoding", "accept-language", "connection"})
# curl_cffi has already decoded the body, so these no longer describe it.
_DECODED = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class CurlTransport(httpx.BaseTransport):
    """An httpx transport that sends each request through curl_cffi impersonating a browser (`target`, e.g.
    "chrome"). Redirects aren't followed here (HttpClient checks each hop) and cookies aren't kept here: the
    HttpClient's jar holds them and sends them as the request's Cookie header. The body is read whole; a site using
    this transport answers with small JSON (Yahoo's chart API), and HttpClient still refuses one over MAX_BYTES."""

    def __init__(self, target: str, session=None):
        if session is None:
            # The `jobs` extra; the API service never imports this module.
            from curl_cffi import requests as curl
            session = curl.Session(impersonate=target)
        self.target, self._session = target, session

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        from curl_cffi.requests import exceptions as cx
        headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() not in _HTTPX_DEFAULTS]
        try:
            r = self._session.request(request.method, str(request.url), headers=headers,
                                      data=request.read() or None, allow_redirects=False, impersonate=self.target,
                                      timeout=(TIMEOUT.connect, TIMEOUT.read), discard_cookies=True)
        except cx.Timeout:                              # never str(e): it may hold the URL, with a secret parameter
            raise httpx.ReadTimeout("curl_cffi: timed out", request=request) from None
        except cx.RequestException as e:
            raise httpx.ConnectError(f"curl_cffi: {type(e).__name__}", request=request) from None
        out = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in _DECODED]
        return httpx.Response(r.status_code, headers=out, content=r.content, request=request)

    def close(self) -> None:
        self._session.close()


def curl_transport(site: Site) -> httpx.BaseTransport:
    return CurlTransport(site.impersonate)


class HttpClient:
    """One per job run: the pacing state lives here, so every task in the run, and every watch poll, shares it."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None, sites: tuple[Site, ...] = SITES,
                 monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[], float] = random.random, clock: Callable[[], datetime] = utcnow,
                 site_transport: Callable[[Site], httpx.BaseTransport] | None = None):
        """`site_transport` gives each impersonating site its transport; by default curl_cffi's, unless `transport`
        is given (a test's mock then answers every site, impersonating or not)."""
        self.sites, self._mono, self._sleep, self._rand, self._clock = sites, monotonic, sleep, rand, clock
        self._last: dict[str, float] = {}           # site key -> when its last request ended (monotonic)
        if site_transport is None and transport is None:
            site_transport = curl_transport
        mounts: dict[str, httpx.BaseTransport] = {}
        for s in sites:
            if s.impersonate and site_transport is not None:
                t = site_transport(s)
                for d in s.domains:
                    mounts[f"all://{d}"] = mounts[f"all://*.{d}"] = t
        self._http = httpx.Client(transport=transport, mounts=mounts or None, timeout=TIMEOUT,
                                  follow_redirects=False,
                                  headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})

    def close(self) -> None:
        self._http.close()

    def _pace(self, site: Site) -> None:
        last = self._last.get(site.key)
        if last is not None:
            wait = last + site.gap + site.jitter * self._rand() - self._mono()
            if wait > 0:
                self._sleep(wait)

    def _backoff(self, attempt: int) -> float:
        return BACKOFF * 2 ** attempt + BACKOFF * self._rand()

    def _once(self, url: str, record: str, params, headers, data=None,
              follow: bool = True) -> tuple[int, httpx.Headers, bytes]:
        """One attempt. Redirects are followed here (unless `follow` is False), each hop checked first, so an
        unlisted host is never contacted. A form post (`data`) that redirects continues as a GET, as browsers do."""
        method = "POST" if data is not None else "GET"
        for _ in range(MAX_REDIRECTS + 1):
            with self._http.stream(method, url, params=params, headers=headers, data=data) as r:
                if follow and r.status_code in REDIRECT_STATUS and (location := r.headers.get("location")):
                    nxt = r.url.join(location)
                    if nxt.scheme not in ("http", "https") or not any(s.matches(nxt.host) for s in self.sites):
                        raise FetchError(record, f"redirected to {nxt.host or nxt.scheme}, which isn't a listed source")
                    url, params = str(nxt), None
                    if r.status_code in (301, 302, 303):
                        method, data = "GET", None
                    continue
                parts, n = [], 0
                for b in r.iter_bytes():
                    n += len(b)
                    if n > MAX_BYTES:
                        raise FetchError(record, f"response larger than {MAX_BYTES} bytes", r.status_code)
                    parts.append(b)
                return r.status_code, r.headers, b"".join(parts)
        raise FetchError(record, f"more than {MAX_REDIRECTS} redirects")

    def get(self, url: str, *, record_url: str | None = None, params: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None, stats: Counter | None = None,
            data: Mapping[str, str] | None = None, follow_redirects: bool = True) -> Response:
        """GET, or a form post with `data`, with pacing and retries. Returns the last response whatever its status
        (the caller decides what a 404 means); raises FetchError for an unlisted host, an oversized body, a
        Retry-After beyond MAX_WAIT or a network error that outlasts the retries. follow_redirects=False returns a
        redirect as it is (a request made only for the cookie it sets)."""
        record = record_url or url
        site = site_for(url, self.sites)
        stats = Counter() if stats is None else stats
        for attempt in range(RETRIES + 1):
            self._pace(site)
            try:
                status, hdrs, body = self._once(url, record, params, headers, data, follow_redirects)
            except httpx.TransportError as e:        # DNS, TLS, timeouts, resets: never str(e), which may hold the URL
                self._last[site.key] = self._mono()
                stats["errors"] += 1
                if attempt == RETRIES:
                    raise FetchError(record, f"{type(e).__name__} on each of {attempt + 1} attempts") from None
                stats["retries"] += 1
                self._sleep(self._backoff(attempt))
                continue
            self._last[site.key] = self._mono()
            stats[str(status)] += 1
            if status in RETRY_STATUS and attempt < RETRIES:
                wait = self._backoff(attempt)
                asked = retry_after(hdrs.get("retry-after"), self._clock())
                if asked is not None:
                    if asked > MAX_WAIT:
                        raise FetchError(record, f"HTTP {status}, and Retry-After asks for {asked:.0f} s "
                                                 f"(more than {MAX_WAIT:.0f})", status)
                    wait = max(wait, asked)
                stats["retries"] += 1
                self._sleep(wait)
                continue
            return Response(record, status, hdrs, body, self._clock(), attempt + 1)
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class Fetched:
    url: str                      # as recorded (no secrets)
    status: int                   # 200, 304, or a status the caller allowed (a transcript not posted yet: 404)
    changed: bool                 # False on 304, and on a 200 whose bytes are the ones this URL last returned
    raw_file_id: int | None       # the core.raw_file row holding the bytes (on 304, the stored copy)
    sha256: bytes | None
    media_type: str | None
    body: bytes | None            # the downloaded bytes (200 only); Fetcher.read() gets a stored copy back
    fetched_at: datetime
    new_file: bool = False        # this fetch registered a new raw file

    @property
    def stored(self) -> bool:
        return self.raw_file_id is not None


@dataclass(frozen=True)
class _Cached:
    etag: str | None
    last_modified: str | None
    sha256: bytes | None
    raw_file_id: int | None
    media_type: str | None


def _media_type(content_type: str | None) -> str | None:
    mt = (content_type or "").split(";", 1)[0].strip().lower()
    return mt or None


def _url_ext(url: str) -> str | None:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lstrip(".").lower()
    return suffix if 1 <= len(suffix) <= 8 and suffix.isalnum() else None


class Fetcher:
    """One task run's fetching: the run's HttpClient, an autocommit connection for `ops.http_cache` and
    `core.raw_file` (written as each fetch happens, so the evidence survives a load that fails: Architecture 02 §1.2)
    and the storage function, opened on the first body to store."""

    def __init__(self, http: HttpClient, db, storage: Storage | Callable[[], Storage], task_run_id: int):
        self.http, self.db, self.task_run_id = http, db, task_run_id
        self._storage, self._sources = storage, {}
        self.counts: Counter = Counter({"429": 0, "403": 0})
        self.files = 0                 # bodies downloaded and stored
        self.new_files = 0             # of which new raw files

    # ------------------------------------------------------------------ helpers
    def storage(self) -> Storage:
        if not isinstance(self._storage, Storage):
            self._storage = self._storage()
        return self._storage

    def _source_id(self, key: str) -> int:
        if key not in self._sources:
            row = self.db.execute("select id from core.source where key=%s", (key,)).fetchone()
            if row is None:
                raise ValueError(f"no core.source row for {key!r}")
            self._sources[key] = row[0]
        return self._sources[key]

    def _cached(self, url: str, source_id: int) -> _Cached | None:
        row = self.db.execute(
            """select c.etag, c.last_modified, c.sha256, f.id, f.media_type
                 from ops.http_cache c
                 left join core.raw_file f on f.source_id = %s and f.sha256 = c.sha256
                where c.url = %s""", (source_id, url)).fetchone()
        return _Cached(*row) if row else None

    def stats(self) -> dict:
        """For ops.task_run: status counts (plus retries, network errors and new raw files) and files fetched."""
        http = dict(sorted(self.counts.items()))
        http["new_raw_files"] = self.new_files
        return {"http": http, "files": self.files}

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ fetch
    def get(self, url: str, *, source: str, params: Mapping[str, str] | None = None,
            secret_params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None,
            conditional: bool = True, media_type: str | None = None, ext: str | None = None,
            content_date: date | None = None, allow: Iterable[int] = ()) -> Fetched:
        """Fetch `url` for the source `source` (a core.source key). A 200 is stored and registered before this
        returns; a 304 returns the stored copy's raw file; a status in `allow` returns without a file; anything
        else raises FetchError. conditional=False skips the validators (to download a page again on purpose)."""
        if (params or secret_params) and urlsplit(url).query:
            raise ValueError("pass query parameters either in the URL or as params, not both")
        record = str(httpx.URL(url, params=dict(params))) if params else url
        send = {**(params or {}), **(secret_params or {})} or None
        source_id = self._source_id(source)
        cached = self._cached(record, source_id)
        h = dict(headers or {})
        if conditional and cached and cached.raw_file_id is not None:
            if cached.etag:
                h["If-None-Match"] = cached.etag
            if cached.last_modified:
                h["If-Modified-Since"] = cached.last_modified
        r = self.http.get(url, record_url=record, params=send, headers=h, stats=self.counts)
        if r.status == 304:
            if "If-None-Match" not in h and "If-Modified-Since" not in h:
                raise FetchError(record, "HTTP 304 to a request that sent no validators", 304)
            self.db.execute(
                """update ops.http_cache set status = 304, fetched_at = %s, task_run_id = %s,
                          etag = coalesce(%s, etag), last_modified = coalesce(%s, last_modified)
                    where url = %s""",
                (r.fetched_at, self.task_run_id, r.headers.get("etag"), r.headers.get("last-modified"), record))
            return Fetched(record, 304, False, cached.raw_file_id, cached.sha256, cached.media_type, None, r.fetched_at)
        if r.status == 200:
            return self._store(r, url, source, source_id, cached, media_type, ext, content_date)
        if r.status in set(allow):
            return Fetched(record, r.status, False, None, None, None, None, r.fetched_at)
        raise FetchError(record, f"HTTP {r.status}", r.status)

    def post(self, url: str, data: Mapping[str, str], *, source: str, record_url: str,
             media_type: str | None = None, ext: str | None = None, content_date: date | None = None) -> Fetched:
        """A form post (the RBI's Annual Policy page for an earlier fiscal year). `record_url` names what was asked
        for (e.g. the page's URL with `#hdnYear=2020`): it is what ops.http_cache and core.raw_file record, since the
        form's fields (ASP.NET view state) are neither stable nor worth keeping. No validators: a post isn't
        conditional, but a body identical to the last one is still no new raw file."""
        if record_url == url:
            raise ValueError("a form post needs its own record_url (the URL alone doesn't say what was asked for)")
        source_id = self._source_id(source)
        cached = self._cached(record_url, source_id)
        r = self.http.get(url, record_url=record_url, data=dict(data), stats=self.counts)
        if r.status == 200:
            return self._store(r, url, source, source_id, cached, media_type, ext, content_date)
        raise FetchError(record_url, f"HTTP {r.status}", r.status)

    def stored(self, record_url: str, *, source: str) -> Fetched | None:
        """The copy last stored for `record_url`, without contacting the site (a closed fiscal year's page that a
        re-run needn't fetch again), or None if there's none. Its bytes come from read(raw_file_id)."""
        source_id = self._source_id(source)
        cached = self._cached(record_url, source_id)
        if cached is None or cached.raw_file_id is None:
            return None
        at = self.db.execute("select fetched_at from ops.http_cache where url = %s", (record_url,)).fetchone()[0]
        return Fetched(record_url, 304, False, cached.raw_file_id, cached.sha256, cached.media_type, None, at)

    def _store(self, r: Response, url: str, source: str, source_id: int, cached: _Cached | None,
               media_type: str | None, ext: str | None, content_date: date | None) -> Fetched:
        sha = hashlib.sha256(r.body).digest()
        mt = media_type or _media_type(r.headers.get("content-type")) or EXT_MEDIA.get(_url_ext(url) or "") \
            or "application/octet-stream"
        obj = self.storage().put(r.body, source=source, media_type=mt, ext=ext or MEDIA_EXT.get(mt) or _url_ext(url) or "bin")
        row = self.db.execute(
            """insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, content_date,
                                          fetched_at, task_run_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (source_id, sha256) do nothing returning id""",
            (obj.object_key, source_id, r.url, sha, len(r.body), mt, content_date, r.fetched_at, self.task_run_id)).fetchone()
        new = row is not None
        raw_id = row[0] if new else self.db.execute(
            "select id from core.raw_file where source_id=%s and sha256=%s", (source_id, sha)).fetchone()[0]
        self.db.execute(
            """insert into ops.http_cache (url, etag, last_modified, sha256, status, fetched_at, task_run_id)
               values (%s,%s,%s,%s,200,%s,%s)
               on conflict (url) do update set etag = excluded.etag, last_modified = excluded.last_modified,
                 sha256 = excluded.sha256, status = excluded.status, fetched_at = excluded.fetched_at,
                 task_run_id = excluded.task_run_id""",
            (r.url, r.headers.get("etag"), r.headers.get("last-modified"), sha, r.fetched_at, self.task_run_id))
        self.files += 1
        self.new_files += new
        changed = cached is None or cached.sha256 != sha
        return Fetched(r.url, 200, changed, raw_id, sha, mt, r.body, r.fetched_at, new)

    def read(self, raw_file_id: int) -> bytes:
        """A stored raw file's bytes, hash-verified by the storage function (a 304's page, or a re-parse)."""
        row = self.db.execute("select object_key from core.raw_file where id=%s", (raw_file_id,)).fetchone()
        if row is None:
            raise ValueError(f"no raw file {raw_file_id}")
        return self.storage().get(row[0])
