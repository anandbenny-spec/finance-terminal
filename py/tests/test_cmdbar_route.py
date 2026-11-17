"""The command bar's live route (Spec 07 §3, §4, §8, N1, N7, N8; K-05, #583): the switch first (paused with no call of any kind
and no log row), the limits (a daily cap of 2 enforced; the visitor's hourly count), the cache (a hit makes no model call; a
refresh of the views' tasks invalidates it), the log (no IP address or user agent; error classes only), the stream, and the
examples endpoint's `verified` filter. K-11's go-live check reads items 1 and 5 here (Spec 07 §10)."""
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from ai_fakes import FakeClaude, claude
from fastapi.testclient import TestClient

from ft.ai import sqlguard
from ft.api import cmdbar as api
from ft.api.app import app
from ft.cmdbar import cache, limits
from ft.cmdbar import route as r
from ft.cmdbar import sql as s
from ft.cmdbar.log import COLUMNS, LogRow

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 17, 6, 30, tzinfo=UTC)                      # 12:00 IST
IP, UA = "203.0.113.7", "Mozilla/5.0 (test browser)"
ENV = {"ANTHROPIC_API_KEY": "test-not-a-key", "VOYAGE_API_KEY": "test-not-a-key",
       "SQL_READER_DATABASE_URL": "postgresql://sql_reader@localhost/none", "CMDBAR_SALT": "00" * 32,
       "SUPABASE_URL": "https://abcdefgh.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "test-not-a-key"}
SQL = "select meeting_date, policy_rate_change_bps from policy_meetings where bank = 'RBI' order by meeting_date desc"
ROWS = s.Result(["meeting_date", "policy_rate_change_bps"], [(datetime(2025, 12, 5).date(), Decimal(-25))])
SUMMARY = "The RBI cut by 25 basis points on 5 December 2025."
PROMPT = s.sql_prompt(s.render_views([s.ViewDoc("policy_meetings", "One row per meeting.", (("bank", "text", "Fed or RBI"),))]))
FRESH = {"policy.fed_documents": NOW - timedelta(hours=5), "policy.market_series": NOW - timedelta(hours=3),
         "equity.prices": NOW - timedelta(days=2)}


# ------------------------------------------------------------------ fakes
class FakeStore:
    """The `api.cmdbar_*` functions in memory, recording every call by name. `fail` names calls that raise StoreError."""

    def __init__(self, *, enabled=True, cap=100, hourly=10, budget="15", spend="0", fail=(), examples=(), pages=None):
        self.st = r.State(enabled, cap, hourly, Decimal(budget), Decimal(spend))
        self.fail, self.calls = set(fail), []
        self.counts: dict[tuple[str, datetime], int] = {}
        self.entries: dict[bytes, dict] = {}
        self.logs: list[LogRow] = []
        self.fresh = dict(FRESH)
        self.pages = pages or {}
        self.rows = list(examples)
        self.closed = 0

    def _call(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise r.StoreError(f"{name}: Data API answered 404")

    def state(self):
        self._call("state")
        return self.st

    def count(self, bucket, window_start):
        self._call("count")
        k = (bucket, window_start)
        self.counts[k] = self.counts.get(k, 0) + 1
        return self.counts[k]

    def cache_get(self, sha):
        self._call("cache_get")
        e = self.entries.get(sha)
        if e is None or e["expires_at"] <= NOW:
            return None
        return r.CacheEntry(e["route"], json.loads(json.dumps(e["answer"])), e["data_version"])

    def cache_put(self, sha, question, route, answer, data_version, expires_at):
        self._call("cache_put")
        self.entries[sha] = {"question": question, "route": route, "answer": json.loads(json.dumps(answer)),
                             "data_version": data_version, "expires_at": expires_at, "hits": 0}

    def cache_hit(self, sha):
        self._call("cache_hit")
        self.entries[sha]["hits"] += 1

    def log(self, row):
        self._call("log")
        self.logs.append(row)

    def freshness(self):
        self._call("freshness")
        return dict(self.fresh)

    def page(self, slug):
        self._call("page")
        return self.pages.get(slug)

    def examples(self):
        self._call("examples")
        return list(self.rows)

    def close(self):
        self.closed += 1


class FakeReader:
    def __init__(self, *script, passages=()):
        self.script, self.ran, self.closed = list(script) or [ROWS], [], False
        self.passages, self.searched = list(passages), []         # the document route's search_chunks rows (K-06)

    def select(self, sql, params):
        self.searched.append(params)
        return s.Result([], [tuple(p) for p in self.passages])

    def run(self, guarded):
        self.ran.append(guarded)
        got = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(got, Exception):
            raise got
        return got

    def close(self):
        self.closed = True


def model(route="data", sql=SQL, summary=SUMMARY, slug=None, script=("ok",)):
    """A fake Claude for the router, the SQL task and the summary task."""
    def answer(body):
        first = next(iter(body["output_config"]["format"]["schema"]["properties"]))
        if first == "route":
            return {"route": route, "page_slug": slug, "filters": {"banks": [], "doc_types": [], "date_from": None,
                                                                   "date_to": None}}
        if first == "summary":
            return {"summary": summary}
        return {"answerable": sql is not None, "sql": sql, "explanation": "The RBI's decisions."}
    return FakeClaude(script=script, answer=answer)


class FakeEmbedder:
    """Voyage for the document route (K-06): one fixed vector per question, every call recorded."""

    def __init__(self):
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return [0.01] * 512


class Harness:
    def __init__(self, store=None, fake=None, reader=None, env=None, now=NOW):
        self.store = store or FakeStore()
        self.fake = fake or model()
        self.reader = reader or FakeReader()
        self.voyage = FakeEmbedder()
        self.env = dict(ENV if env is None else env)
        self.built = {"store": 0, "client": 0, "reader": 0, "prompt": 0, "embedder": 0}
        self.now = now

        def build(kind, value):
            def f(*_):
                self.built[kind] += 1
                return value() if callable(value) else value
            return f
        self.deps = r.Deps(env=self.env, store=build("store", lambda: self.store),
                           client=build("client", lambda: claude(self.fake)), reader=build("reader", lambda: self.reader),
                           prompt=build("prompt", PROMPT), now=lambda: self.now,
                           embedder=build("embedder", lambda: self.voyage))

    def ask(self, question="When did the RBI last cut, and by how much?", ip=IP, ua=UA):
        lines: list[str] = []
        out = r.ask(question, ip, ua, self.deps, emit=lines.append)
        self.lines = lines
        return out


# ------------------------------------------------------------------ step 0: the switch (K-11 items 1 and 5)
@pytest.mark.parametrize("missing", r.SECRETS)
def test_a_missing_secret_is_paused_with_no_call_of_any_kind(missing):
    h = Harness(env={k: v for k, v in ENV.items() if k != missing})
    out = h.ask()
    assert out["state"] == "paused" and out["summary"] == "AI questions are paused for now; search still works."
    assert h.built == {"store": 0, "client": 0, "reader": 0, "prompt": 0, "embedder": 0}
    assert h.store.calls == [] and h.fake.requests == [] and h.lines == []


def test_the_switch_off_is_paused_with_no_model_query_or_voyage_call_and_no_log_row():
    h = Harness(store=FakeStore(enabled=False))
    out = h.ask()
    assert out["state"] == "paused" and set(out) == set(r.FINAL_KEYS)
    assert h.store.calls == ["state"]                    # the switch's one read; nothing counted, cached or logged
    assert h.fake.requests == [] and h.built["client"] == h.built["reader"] == 0 and h.store.logs == []


@pytest.mark.parametrize("spend", ["15", "15.000001", "40"])
def test_month_to_date_spend_at_or_over_the_budget_is_paused(spend):
    h = Harness(store=FakeStore(budget="15", spend=spend))
    assert h.ask()["state"] == "paused"
    assert h.store.calls == ["state"] and h.fake.requests == [] and h.store.logs == []


def test_an_unreadable_switch_is_paused():
    h = Harness(store=FakeStore(fail={"state"}))
    assert h.ask()["state"] == "paused" and h.fake.requests == [] and h.store.logs == []


def test_under_the_budget_with_the_switch_on_it_answers():
    h = Harness(store=FakeStore(budget="15", spend="14.99"))
    out = h.ask()
    assert out["state"] == "answered" and out["summary"] == SUMMARY


# ------------------------------------------------------------------ step 2: limits
def test_the_daily_cap_is_enforced_with_a_cap_of_2():
    h = Harness(store=FakeStore(cap=2))
    got = [h.ask(q)["state"] for q in ("When did the RBI last cut?", "When did the Fed last cut?", "Which banks trade below book?")]
    assert got == ["answered", "answered", "limit"]
    assert h.store.counts[(limits.GLOBAL, limits.ist_day_start(NOW))] == 3
    assert len(h.fake.requests) == 6                     # two asks: router, SQL, summary each (the third: none)
    last = h.store.logs[-1]
    assert not last.answered and last.error_class == "daily_limit" and last.route is None and last.models == []


def test_the_daily_count_is_per_ist_day():
    h = Harness(store=FakeStore(cap=1))
    assert h.ask("When did the RBI last cut?")["state"] == "answered"
    h.now = NOW + timedelta(hours=12)                    # 00:00 IST the next day
    assert h.ask("When did the Fed last cut?")["state"] == "answered"
    assert limits.ist_day_start(NOW) == datetime(2026, 11, 16, 18, 30, tzinfo=UTC)


def test_the_visitor_limit_counts_every_ask_cached_or_not():
    h = Harness(store=FakeStore(hourly=2))
    assert h.ask()["state"] == "answered"
    assert h.ask()["state"] == "answered" and h.store.logs[-1].cache_hit      # the cache answered; still counted
    out = h.ask()
    assert out["state"] == "limit" and out["summary"] == r.PAUSED_TEXT
    assert h.store.logs[-1].error_class == "visitor_limit" and not h.store.logs[-1].answered
    assert h.ask(ip="198.51.100.9")["state"] == "answered"                     # another visitor isn't limited


def test_a_count_that_fails_refuses_rather_than_answering_uncounted():
    h = Harness(store=FakeStore(fail={"count"}))
    assert h.ask()["state"] == "limit" and h.fake.requests == []


def test_the_visitor_hash_rotates_with_the_ist_day_and_is_not_the_ip():
    a = limits.visitor_hash("salt", IP, UA, NOW)
    assert re.fullmatch(r"[0-9a-f]{64}", a) and a == limits.visitor_hash("salt", IP, UA, NOW + timedelta(hours=5))
    assert a != limits.visitor_hash("salt", IP, UA, NOW + timedelta(hours=12))  # past IST midnight
    assert a != limits.visitor_hash("other", IP, UA, NOW) and a != limits.visitor_hash("salt", IP, "x", NOW)
    with pytest.raises(ValueError):
        limits.visitor_hash("", IP, UA, NOW)


# ------------------------------------------------------------------ step 3: the cache
def test_a_cache_hit_makes_no_model_call():
    h = Harness()
    first = h.ask("When did the RBI last cut?")
    n = len(h.fake.requests)
    again = h.ask("  when did the RBI   last cut ")                             # the same question, normalised
    assert len(h.fake.requests) == n and h.built["reader"] == 1
    assert {k: v for k, v in again.items() if k != "ms"} == {k: v for k, v in first.items() if k != "ms"}
    hit = h.store.logs[-1]
    assert hit.cache_hit and hit.answered and hit.models == [] and hit.cost_usd == 0 and hit.generated_sql == first["sql"]
    assert h.store.entries[cache.key("When did the RBI last cut?")]["hits"] == 1


def test_a_refresh_of_the_views_tasks_invalidates_the_entry():
    h = Harness()
    h.ask()
    n = len(h.fake.requests)
    h.store.fresh["equity.prices"] = NOW                 # not a task behind policy_meetings: still a hit
    h.ask()
    assert len(h.fake.requests) == n
    h.store.fresh["policy.fed_documents"] = NOW          # a policy task refreshed: the entry is stale
    assert h.ask()["state"] == "answered" and len(h.fake.requests) > n and not h.store.logs[-1].cache_hit


def test_an_expired_entry_is_a_miss():
    h = Harness()
    h.ask()
    sha = cache.key("When did the RBI last cut, and by how much?")
    h.store.entries[sha]["expires_at"] = NOW
    n = len(h.fake.requests)
    h.ask()
    assert len(h.fake.requests) > n


def test_failures_and_paused_states_are_never_cached():
    h = Harness(reader=FakeReader(s.ReaderDown("OperationalError")))
    assert h.ask()["state"] == "cant_answer" and h.store.entries == {}
    h = Harness(fake=model(route="document"))
    assert h.ask()["state"] == "not_found" and h.store.entries == {}


def test_the_key_normalises_and_the_version_follows_the_views_tasks():
    assert cache.normalise("  When did the RBI cut?!. ") == "when did the rbi cut"
    assert cache.key("When did the RBI cut?") == cache.key("when did  the rbi cut") and len(cache.key("x")) == 32
    assert set(cache.VIEW_TASKS) == sqlguard.VIEWS
    assert cache.views_read("select * from semantic.companies c join company_valuations v using (x) where exists "
                            "(select 1 from policy_meetings)") == ["companies", "company_valuations", "policy_meetings"]
    assert cache.data_version([], FRESH) == cache.STATIC
    assert cache.data_version(["policy_meetings"], FRESH) == FRESH["policy.market_series"].isoformat()
    assert cache.data_version(["companies"], FRESH) == FRESH["equity.prices"].isoformat()
    assert cache.data_version(["companies"], {"policy.calendar": NOW}) == cache.NONE
    assert cache.data_version(["not_a_view"], FRESH) == FRESH["policy.market_series"].isoformat()   # unknown: every task


# ------------------------------------------------------------------ steps 4 and 5: dispatch
def test_a_data_answer_streams_design_01s_status_lines_and_its_final_object():
    h = Harness()
    out = h.ask()
    assert h.lines == ["Understanding the question…", "Writing SQL…", "Running the query…", "Writing the answer…"]
    assert set(out) == set(r.FINAL_KEYS) and out["state"] == "answered" and out["route"] == "data"
    assert out["sql"] == sqlguard.guard(SQL) and h.reader.ran == [out["sql"]] and h.reader.closed
    assert out["columns"] == [{"name": "meeting_date", "kind": "date"}, {"name": "policy_rate_change_bps", "kind": "number"}]
    assert out["rows"] == [["2025-12-05", "-25"]] and out["view"] == "policy_meetings" and out["summary"] == SUMMARY
    log = h.store.logs[-1]
    assert log.answered and log.route == "data" and log.rows_returned == 1 and log.sql_valid is True
    assert log.generated_sql == out["sql"] and log.models == ["claude-haiku-5-5"] * 3 and log.cost_usd > 0
    assert log.input_tokens > 0 and log.output_tokens > 0 and log.error_class is None


def test_a_page_question_links_the_page_search_index_names():
    page = {"item_type": "event", "subtype": "policy_meeting", "slug": "rbi-2025-12", "title": "RBI MPC, December 2025",
            "parent_slug": "rbi"}
    h = Harness(store=FakeStore(pages={"rbi-2025-12": page}), fake=model(route="page", slug="rbi-2025-12"))
    out = h.ask("Show me the RBI's December meeting")
    assert out["state"] == "page" and out["pages"] == [page] and out["summary"] is None and h.built["reader"] == 0
    assert len(h.fake.requests) == 1 and h.store.logs[-1].answered
    h = Harness(fake=model(route="page", slug="no-such-page"))
    assert h.ask("Show me something")["state"] == "cant_answer"


def test_an_unsupported_question_is_cant_answer_with_live_suggested_pages():
    h = Harness(fake=model(route="unsupported"))
    out = h.ask("What will the Nifty do tomorrow?")
    assert out["state"] == "cant_answer" and out["summary"] == "That isn't in this site's data."
    assert len(h.fake.requests) == 1 and h.built["reader"] == 0
    nav = (ROOT / "web" / "lib" / "shell" / "nav.ts").read_text()
    assert 2 <= len(out["pages"]) <= 3 and all(f'href: "{p["href"]}"' in nav for p in out["pages"])


def test_a_document_question_with_no_passage_found_is_not_found_with_no_second_model_call():
    # K-06 replaced K-05's stub (which searched nothing): the question is embedded and searched over Fed and RBI policy documents
    # only; with no passage returned there is no answer call, and nothing is cited.
    h = Harness(fake=model(route="document"))
    out = h.ask("What did the RBI say about food inflation?")
    assert out["state"] == "not_found" and out["summary"] == "Not found in the stored Fed and RBI documents."
    assert len(h.fake.requests) == 1 and out["citations"] == [] and out["documents"] == []
    assert h.voyage.calls == ["What did the RBI say about food inflation?"] and len(h.reader.searched) == 1
    assert h.reader.searched[0][2:4] == (["fed", "rbi"], list(r.documents_mod.DOC_TYPES)) and h.reader.closed


def test_an_unanswerable_data_question_is_cant_answer_and_runs_nothing():
    h = Harness(fake=model(sql=None))
    out = h.ask("Which company has the highest revenue, Reliance or Apple?")
    assert out["state"] == "cant_answer" and h.reader.ran == [] and out["rows"] == [] and out["sql"] is None


def test_no_ai_key_or_credit_mid_ask_is_paused_and_logged():
    h = Harness(fake=model(script=("402",)))
    out = h.ask()
    assert out["state"] == "paused" and h.store.logs[-1].error_class == "ai_unavailable"


# ------------------------------------------------------------------ no raw error reaches a visitor; the log holds classes only
SECRET_TEXT = "password=hunter2 relation ops.cmdbar_log"


@pytest.mark.parametrize("fail", [
    "query", "reader", "router", "describe", "internal"])
def test_no_raw_error_is_returned_and_the_log_holds_its_class_only(fail):
    reader = FakeReader(s.QueryError("UndefinedColumn")) if fail == "query" else FakeReader()
    h = Harness(reader=reader, fake=model(script=("400",)) if fail == "router" else model())
    if fail == "reader":
        h.deps.reader = lambda env: (_ for _ in ()).throw(s.ReaderDown(SECRET_TEXT))
    if fail == "describe":
        h.deps.prompt = lambda rd, env: (_ for _ in ()).throw(RuntimeError(SECRET_TEXT))
    if fail == "internal":
        h.deps.client = lambda env: (_ for _ in ()).throw(KeyError(SECRET_TEXT))
    out = h.ask()
    assert out["state"] == "cant_answer" and out["summary"] == "That isn't in this site's data."
    text = json.dumps(out)
    assert "hunter2" not in text and "UndefinedColumn" not in text and "Error" not in text and "error" not in out
    cls = h.store.logs[-1].error_class
    assert cls in {"database: UndefinedColumn", "database_unavailable", "ai_call", "internal"} and "hunter2" not in cls


def test_the_log_row_holds_no_ip_address_or_user_agent():
    h = Harness()
    h.ask()
    row = h.store.logs[-1].as_json()
    assert tuple(row) == COLUMNS
    flat = json.dumps(row)
    assert IP not in flat and UA not in flat and "Mozilla" not in flat
    assert row["visitor_hash"] == limits.visitor_hash(ENV["CMDBAR_SALT"], IP, UA, NOW)
    assert row["question"] == "When did the RBI last cut, and by how much?" and row["cost_usd"] == str(h.store.logs[-1].cost_usd)


def test_a_log_write_that_fails_still_answers():
    h = Harness(store=FakeStore(fail={"log"}))
    assert h.ask()["state"] == "answered"


# ------------------------------------------------------------------ the HTTP routes
@pytest.fixture
def http():
    h = Harness()
    app.dependency_overrides[api.live_deps] = lambda: h.deps
    yield h, TestClient(app)
    app.dependency_overrides.clear()


def _lines(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_the_ask_route_streams_status_lines_then_the_final_object(http):
    h, client = http
    resp = client.post("/api/cmdbar/ask", json={"question": "When did the RBI last cut?"},
                       headers={"x-forwarded-for": f"{IP}, 10.0.0.1", "user-agent": UA})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["cache-control"] == "no-store"
    lines = _lines(resp)
    assert [ln["status"] for ln in lines[:-1]] == ["Understanding the question…", "Writing SQL…", "Running the query…",
                                                    "Writing the answer…"]
    assert set(lines[-1]) == set(r.FINAL_KEYS) and lines[-1]["state"] == "answered"
    assert h.store.logs[-1].visitor_hash == limits.visitor_hash(ENV["CMDBAR_SALT"], IP, UA, NOW)


@pytest.mark.parametrize("body", [b"", b"not json", b'{"question": ""}', b'{"question": 5}', b"[1]",
                                  json.dumps({"question": "x" * 501}).encode()])
def test_a_bad_body_is_a_bare_400_with_no_call(http, body):
    h, client = http
    resp = client.post("/api/cmdbar/ask", content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 400 and resp.json() == {"state": "invalid"}
    assert h.store.calls == [] and h.fake.requests == []


def test_production_today_answers_paused_without_any_request():
    """Production holds SUPABASE_URL and the service key (the admin routes), but none of the bar's own secrets: paused, with
    no request to the Data API, a model or a database."""
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(500)
    env = {"SUPABASE_URL": ENV["SUPABASE_URL"], "SUPABASE_SERVICE_ROLE_KEY": "test-not-a-key"}
    deps = r.Deps(env=env, store=lambda e: api.RpcCmdbarStore(e["SUPABASE_URL"], e["SUPABASE_SERVICE_ROLE_KEY"],
                                                              httpx.Client(transport=httpx.MockTransport(handler))),
                  client=lambda e: pytest.fail("no model client"), reader=lambda e: pytest.fail("no reader"),
                  prompt=lambda rd, e: pytest.fail("no prompt"))
    app.dependency_overrides[api.live_deps] = lambda: deps
    try:
        lines = _lines(TestClient(app).post("/api/cmdbar/ask", json={"question": "When did the RBI last cut?"}))
    finally:
        app.dependency_overrides.clear()
    assert lines == [r.final("paused", summary=r.PAUSED_TEXT, ms=lines[-1]["ms"])] and seen == []


def test_with_every_secret_but_no_functions_yet_the_rpc_store_reads_as_paused():
    """Until K-05's part 2 adds the `api.cmdbar_*` functions, the Data API answers 404 for each: the switch reads as paused."""
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(404, json={"code": "PGRST202", "message": "Could not find the function"})
    store = api.RpcCmdbarStore(ENV["SUPABASE_URL"], "test-not-a-key", httpx.Client(transport=httpx.MockTransport(handler)))
    h = Harness(store=store)
    assert h.ask()["state"] == "paused" and h.fake.requests == [] and h.built["reader"] == 0
    assert [q.url.path for q in seen] == ["/rest/v1/rpc/cmdbar_state"]


def test_the_rpc_store_calls_the_functions_with_the_secret_key():
    seen = []
    answers = {"cmdbar_state": {"ai_enabled": False, "daily_cap": 100, "per_visitor_hourly": 10,
                                "monthly_budget_usd": "15", "month_spend_usd": "0.25"},
               "cmdbar_count": 3, "cmdbar_cache_get": None}

    def handler(req):
        seen.append(req)
        if req.url.path == "/rest/v1/task_freshness":
            return httpx.Response(200, json=[{"task_key": "policy.calendar", "last_success_at": "2026-11-16T01:00:00+00:00"}])
        fn = req.url.path.rsplit("/", 1)[1]
        if fn in answers:                                # PostgREST writes a null result as `null`
            return httpx.Response(200, content=json.dumps(answers[fn]).encode())
        return httpx.Response(204)
    st = api.RpcCmdbarStore(ENV["SUPABASE_URL"] + "/", "sb_secret_test", httpx.Client(transport=httpx.MockTransport(handler)))
    assert st.state() == r.State(False, 100, 10, Decimal(15), Decimal("0.25"))
    assert st.count("global", limits.ist_day_start(NOW)) == 3
    assert st.cache_get(b"\x01" * 32) is None
    st.log(LogRow("ab" * 32, "q"))
    assert st.freshness() == {"policy.calendar": datetime(2026, 11, 16, 1, 0, tzinfo=UTC)}
    count = json.loads(seen[1].content)
    assert count == {"p_bucket": "global", "p_window_start": "2026-11-16T18:30:00+00:00"}
    assert json.loads(seen[2].content) == {"p_question_sha": "01" * 32}
    assert set(json.loads(seen[3].content)["p_row"]) == set(COLUMNS)
    for q in seen:
        assert q.headers["apikey"] == "sb_secret_test" and "authorization" not in q.headers     # an sb_ key isn't a JWT
        assert q.headers["accept-profile"] == "api"


@pytest.mark.parametrize("value", [None, [], {"ai_enabled": "true", "daily_cap": 100, "per_visitor_hourly": 10,
                                              "monthly_budget_usd": "15", "month_spend_usd": "0"},
                                   {"ai_enabled": True, "daily_cap": "x", "per_visitor_hourly": 10,
                                    "monthly_budget_usd": "15", "month_spend_usd": "0"},
                                   {"ai_enabled": True, "daily_cap": 100, "per_visitor_hourly": 10,
                                    "monthly_budget_usd": "NaN", "month_spend_usd": "0"}])
def test_a_switch_that_isnt_exactly_on_is_paused(value):
    try:
        st = api.parse_state(value)
    except r.StoreError:
        return
    assert st.ai_enabled is False                        # "true" as text is not true


def test_the_examples_route_returns_verified_rows_only_in_display_order(http):
    h, client = http
    h.store.rows = [
        {"question": "Unchecked", "module": "policy", "display_order": 1, "answer": {"state": "answered"}, "verified": False},
        {"question": "Second", "module": "equity", "display_order": 2, "answer": {"state": "answered"}, "verified": True},
        {"question": "First", "module": "policy", "display_order": 1, "answer": {"state": "answered"}, "verified": True},
        {"question": "No flag", "module": "policy", "display_order": 0, "answer": None}]
    resp = client.get("/api/cmdbar/examples")
    assert resp.status_code == 200
    assert [e["question"] for e in resp.json()["examples"]] == ["First", "Second"]
    assert h.fake.requests == [] and "count" not in h.store.calls and "log" not in h.store.calls


def test_the_examples_route_is_empty_when_the_store_fails_or_isnt_set():
    for env, store in ((ENV, FakeStore(fail={"examples"})), ({}, FakeStore())):
        h = Harness(store=store, env=env)
        app.dependency_overrides[api.live_deps] = lambda h=h: h.deps
        try:
            resp = TestClient(app).get("/api/cmdbar/examples")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 200 and resp.json() == {"examples": []}


# ------------------------------------------------------------------ 0054 on the test database (K-05 part 2)
@pytest.fixture
def service():
    import psycopg
    from conftest import dsn
    conn = psycopg.connect(dsn("postgres"), autocommit=True)
    conn.execute("set role service_role")                # the role the Vercel routes hold (Arch 01 §1.9)
    yield conn
    conn.close()


def _admin():
    import psycopg
    from conftest import dsn
    return psycopg.connect(dsn("postgres"), autocommit=True)


@pytest.mark.db
def test_the_stores_functions_are_0054s_with_their_argument_names_and_types():
    with _admin() as db:
        got = {r[0]: r[1] for r in db.execute(
            """select p.proname, coalesce(array_to_string(array(
                        select a.n || ' ' || format_type(a.t, null)
                          from unnest(p.proargnames, p.proargtypes::oid[]) with ordinality a(n, t, i) order by a.i), ','), '')
                 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'api' and p.proname like 'cmdbar\\_%'""").fetchall()}
    want = {fn: ",".join(f"{name} {typ.replace('timestamptz', 'timestamp with time zone')}" for name, typ in args)
            for fn, args in api.FUNCTIONS.items()}
    assert got == want


@pytest.mark.db
def test_the_route_runs_end_to_end_through_0054_and_stays_paused_while_the_switch_is_off(service):
    import uuid
    question = f"When did the RBI last cut, test {uuid.uuid4().hex[:12]}?"
    store = api.SqlCmdbarStore(service)
    now = datetime.now(UTC)
    vhash = limits.visitor_hash(ENV["CMDBAR_SALT"], IP, UA, now)

    def harness():
        h = Harness(store=store, now=now)
        h.store = store
        return h
    try:
        h = harness()
        out = h.ask(question)                             # 0052 left the switch off
        assert out["state"] == "paused" and h.fake.requests == [] and h.built["reader"] == 0
        with _admin() as db:
            assert db.execute("select count(*) from ops.cmdbar_log where visitor_hash = %s", (vhash,)).fetchone()[0] == 0
            db.execute("update ops.setting set value = 'true'::jsonb where key = 'cmdbar_ai_enabled'")
        h = harness()
        out = h.ask(question)
        assert out["state"] == "answered" and out["summary"] == SUMMARY and len(h.fake.requests) == 3
        again = harness()
        assert again.ask(question)["state"] == "answered" and again.fake.requests == []      # the cache, through 0054
        with _admin() as db:
            rows = db.execute("""select cache_hit, answered, route, rows_returned, generated_sql is not null, cost_usd > 0
                                   from ops.cmdbar_log where visitor_hash = %s order by id""", (vhash,)).fetchall()
            assert rows == [(False, True, "data", 1, True, True), (True, True, "data", 1, True, False)]
            assert db.execute("select count from ops.cmdbar_usage where bucket = %s and window_start = %s",
                              (limits.visitor_bucket(vhash), limits.hour_start(now))).fetchone()[0] == 2
            assert db.execute("select hits from ops.cmdbar_cache where question_sha = %s",
                              (cache.key(question),)).fetchone()[0] == 1
    finally:
        with _admin() as db:
            db.execute("update ops.setting set value = 'false'::jsonb where key = 'cmdbar_ai_enabled'")
            db.execute("delete from ops.cmdbar_log where visitor_hash = %s", (vhash,))
            db.execute("delete from ops.cmdbar_usage where bucket in (%s, 'global') and window_start >= now() - interval '2 days'",
                       (limits.visitor_bucket(vhash),))
            db.execute("delete from ops.cmdbar_cache where question_sha = %s", (cache.key(question),))
    with _admin() as db:
        assert db.execute("select value from ops.setting where key = 'cmdbar_ai_enabled'").fetchone()[0] is False


@pytest.mark.db
def test_the_retention_task_deletes_log_rows_after_90_days_and_expired_cache_entries_as_job_writer():
    import psycopg
    from conftest import dsn

    from ft.jobs.tasks.cmdbar_log_retention import CmdbarLogRetention, Run
    h = "d" * 64
    with _admin() as db:
        db.execute("""insert into ops.cmdbar_log (at, visitor_hash, question, answered) values
                        (now() - interval '91 days', %(h)s, 'r54 old', true), (now() - interval '89 days', %(h)s, 'r54 kept', true)""",
                   {"h": h})
        db.execute("""insert into ops.cmdbar_cache (question_sha, question, route, answer, data_version, expires_at) values
                        (%s, 'r54 expired', 'data', '{}', 'v', now() - interval '1 minute'),
                        (%s, 'r54 live', 'data', '{}', 'v', now() + interval '1 hour')""", (b"\x0e" * 32, b"\x0f" * 32))
    try:
        task = CmdbarLogRetention()
        with psycopg.connect(dsn("job_writer")) as jw, jw.transaction():
            run = Run(datetime.now(UTC))
            assert task.load(jw, run, None) >= 2
            checks = {c.name: c for c in task.checks(jw, run, None)}
        assert checks["log_kept"].passed and checks["cache_kept"].passed and not checks["deleted"].blocking
        with _admin() as db:
            assert [r[0] for r in db.execute("select question from ops.cmdbar_log where visitor_hash = %s", (h,))] == ["r54 kept"]
            assert [r[0] for r in db.execute("select question from ops.cmdbar_cache where question like 'r54 %'")] == ["r54 live"]
    finally:
        with _admin() as db:
            db.execute("delete from ops.cmdbar_log where visitor_hash = %s", (h,))
            db.execute("delete from ops.cmdbar_cache where question like 'r54 %'")


def test_the_retention_task_is_registered_and_in_daily():
    from ft.jobs import config
    from ft.jobs.tasks import registry
    from ft.jobs.tasks.cmdbar_log_retention import KEEP_DAYS, CmdbarLogRetention
    t = config.load()["cmdbar.log_retention"]
    assert registry()["cmdbar.log_retention"] is CmdbarLogRetention and KEEP_DAYS == 90
    assert "daily" in t.workflows and t.enabled and not t.uses_nse and not t.visitor_refreshable
