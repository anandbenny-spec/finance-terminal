"""Call-transcript discovery (job 2B-06): the classifier on 2B-04's saved NSE and BSE replies, seasons, windows, the
window splitting and the ten-company stop with a fake fetcher, the ISIN join through a rename, and the BSE fallback.

The saved replies are in data/fixtures/equity/calls (the private fixtures repo; these tests skip without it and CI
checks it out). No NSE or BSE data is kept in this repo (CLAUDE.md §4)."""
import gzip
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ft.equity.calls import discovery as dc
from ft.equity.calls.classify import classify, file_name

CALLS = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "calls"
needs_fixtures = pytest.mark.skipif(not (CALLS / "audit").exists(), reason="the saved files aren't checked out (data/fixtures)")
D = date


def load(path: Path):
    return json.loads(gzip.open(path).read() if path.suffix == ".gz" else path.read_bytes())


def nse_rows(symbol: str) -> list[dict]:
    return load(next((CALLS / "nse").glob(f"nse_ann_{symbol}_*.json.gz")))


# ---------------------------------------------------------------------------------------------- classification
@needs_fixtures
def test_every_saved_nse_row_is_classified_as_the_audit_kit_did():
    expected = {(f["symbol"], f["seq_id"]) for f in load(CALLS / "audit" / "nse_transcript_filings.json")["filings"]}
    got, seen = set(), 0
    for p in sorted((CALLS / "nse").glob("*.json.gz")):
        for r in load(p):
            seen += 1
            a = dc.norm_nse(r)
            assert a is not None
            if a.is_transcript:
                got.add((r["symbol"], r["seq_id"]))
    assert seen > 1000 and len(expected) == 39
    assert got == expected


def test_the_audio_notice_and_the_intimation_are_not_transcripts():
    assert classify("Audio recording of the Earnings Call").kind == "audio_video"
    assert classify("Intimation of schedule of analyst/institutional investor meet").kind == "intimation"
    assert classify("Transcript of the Earnings Call held on 23rd July 2026").kind == "transcript"
    assert classify("Earnings call audio recording and transcript will be uploaded shortly", "x.pdf").kind == "audio_video"


def test_a_future_tense_mention_does_not_make_a_notice_a_transcript():
    # the notice tests ignore "the transcript will be uploaded"; the same subject is then an audio notice / intimation
    assert classify("Audio recording of the call; the transcript will be uploaded in due course").kind == "audio_video"
    assert classify("The call will be held on 21 Oct; the transcript will be made available").kind == "intimation"
    # a plain mention is a transcript
    assert classify("Submission of transcript of the call", "").kind == "transcript"


def test_the_word_intimation_in_a_file_name_alone_is_not_used():
    assert classify("Disclosure under Regulation 30", "ABC_Intimation.pdf").kind == "other"
    assert classify("Disclosure under Regulation 30", "ABC_Intimation_Transcript.pdf").kind == "transcript"
    assert classify("Disclosure under Regulation 30", "ABC_Schedule_of_call.pdf").kind == "intimation"


def test_file_names_come_from_the_attachment_url():
    assert file_name("https://nsearchives.nseindia.com/corporate/A%20B_1.pdf") == "A B_1.pdf"
    assert file_name(None) == ""


@needs_fixtures
def test_bse_rows_are_classified_on_the_headline_so_srf_audio_notices_are_not_transcripts():
    rows = load(CALLS / "bse" / "bse_ann_503806_Earnings_Call_Transcript_20250923_20260917_p1.json.gz")["Table"]
    kinds = sorted(dc.norm_bse(r).kind for r in rows)
    assert kinds == ["audio_video"] * 4 + ["transcript"] * 4
    outcome = load(CALLS / "bse" / "bse_ann_503806_Analyst_Investor_Meet_20250923_20260917_p1.json.gz")["Table"]
    assert sum(1 for r in outcome if dc.norm_bse(r).kind == "intimation") == 3


@needs_fixtures
def test_bajaj_finance_has_no_rows_under_the_transcript_subcategory_and_four_transcripts_under_the_outcome_one():
    empty = load(CALLS / "bse" / "bse_ann_500034_Earnings_Call_Transcript_20250923_20260917_p1.json")
    assert not empty.get("Table")
    outcome = load(CALLS / "bse" / "bse_ann_500034_Analyst_Investor_Meet_20250923_20260917_p1.json.gz")["Table"]
    kinds = [dc.norm_bse(r).kind for r in outcome]
    assert kinds.count("transcript") == 4 and kinds.count("audio_video") == 4


# ---------------------------------------------------------------------------------------------- seasons and windows
def test_a_filings_season_is_that_of_its_dissemination_date_minus_seven_days():
    assert dc.season_of_filing(D(2026, 7, 6)) == "Q4 FY26"          # 29 Jun: an Apr-Jun call
    assert dc.season_of_filing(D(2026, 7, 8)) == "Q1 FY27"          # 1 Jul
    assert dc.season_of_filing(D(2026, 10, 7)) == "Q1 FY27"         # 30 Sep
    assert dc.season_of_filing(D(2026, 10, 8)) == "Q2 FY27"
    

def test_calls_in_each_quarter_of_the_calendar_belong_to_the_season_they_discuss():
    assert [dc.season_of_call(D(2026, m, 15)) for m in (1, 4, 7, 10)] == ["Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"]


def test_the_backfill_window_starts_on_1_jul_2026_and_the_weekly_one_14_days_before_the_latest_stored():
    assert dc.window_start(None, None) == D(2026, 7, 1)
    assert dc.window_start(D(2026, 8, 1), None) == D(2026, 8, 1)
    latest = datetime(2026, 8, 20, 18, 0, tzinfo=dc.INDIA)
    assert dc.window_start(None, latest) == D(2026, 8, 6)
    assert dc.window_start(None, datetime(2026, 7, 5, tzinfo=dc.INDIA)) == D(2026, 7, 1)     # never before the backfill start


def test_no_window_is_longer_than_twelve_months():
    ws = dc.split_range(D(2025, 1, 1), D(2026, 11, 1))
    assert all((b - a).days + 1 <= 365 for a, b in ws) and ws[0][0] == D(2025, 1, 1) and ws[-1][1] == D(2026, 11, 1)
    assert dc.split_range(D(2026, 7, 1), D(2026, 11, 1)) == [(D(2026, 7, 1), D(2026, 11, 1))]


def test_a_backfill_without_since_starts_at_history_from_and_an_explicit_since_wins():
    cfg = dc.load_calls_config()
    assert (cfg.history_from, cfg.first_pages_from, cfg.raw_storage_stop_mb) == (D(2025, 1, 1), "Q1 FY27", 850)
    assert dc.window_start(None, None, backfill=True, history_from=cfg.history_from) == D(2025, 1, 1)
    latest = datetime(2026, 8, 20, tzinfo=dc.INDIA)
    assert dc.window_start(None, latest, backfill=True, history_from=cfg.history_from) == D(2025, 1, 1)    # a backfill is not a weekly run
    assert dc.window_start(D(2026, 7, 1), None, backfill=True, history_from=cfg.history_from) == D(2026, 7, 1)
    assert dc.window_start(None, latest, backfill=False, history_from=cfg.history_from) == D(2026, 8, 6)    # weekly: unchanged
    assert dc.window_start(None, None, backfill=False, history_from=cfg.history_from) == D(2026, 7, 1)      # refresh: unchanged
    ws = dc.split_range(cfg.history_from, D(2026, 11, 3))
    assert len(ws) == 2 and all((b - a).days + 1 <= 365 for a, b in ws)                                    # two windows of 12 months or less


# ---------------------------------------------------------------------------------------------- the ISIN join
def test_the_isin_join_follows_a_rename():
    old = dc.Company(1, "tata-motors", "TATAMOTORS", "INE155A01022", "500570", None,
                     frozenset({"TATAMOTORS", "TMPV"}), frozenset({"INE155A01022", "INE155A01030"}))
    other = dc.Company(2, "cipla", "CIPLA", "INE059A01026", "500087")
    idx = dc.CompanyIndex([old, other])
    row = {"seq_id": "1", "exchdisstime": "05-Aug-2026 10:00:00", "attchmntText": "Transcript of call",
           "sm_isin": "INE155A01030", "symbol": "TMPV", "attchmntFile": "https://nsearchives.nseindia.com/corporate/x.pdf"}
    assert idx.resolve(dc.norm_nse(row)) == 1                        # the former ISIN, found through the history
    assert idx.resolve(dc.norm_nse({**row, "sm_isin": "", "symbol": "TMPV"})) == 1      # no ISIN: the symbol decides
    assert idx.resolve(dc.norm_nse({**row, "sm_isin": "INE000X01010", "symbol": "TATAMOTORS"})) is None   # a stranger ISIN
    assert idx.resolve(dc.norm_nse({**row, "sm_isin": "INE059A01026", "symbol": "TMPV"})) == 2   # the ISIN beats the symbol


def _co(i, slug, symbol, isin, symbols=(), isins=()):
    return dc.Company(i, slug, symbol, isin, None, None, frozenset({symbol, *symbols}), frozenset({isin, *isins}))


def _row(isin, symbol, seq="7"):
    return dc.norm_nse({"seq_id": seq, "exchdisstime": "23-Jul-2026 18:04:00", "attchmntText": "Transcript of call",
                        "sm_isin": isin, "symbol": symbol,
                        "attchmntFile": "https://nsearchives.nseindia.com/corporate/x.pdf"})


def test_the_issuer_stem_is_the_first_nine_characters_of_an_isin_shaped_value():
    assert dc.isin_stem("INE090A01021") == "INE090A01" == dc.isin_stem("INE090A01013")
    for bad in (None, "", "-", "NA", "INE090A0102", "INE090A010213", "ine090a01021", "US0378331005"):
        assert dc.isin_stem(bad) is None, bad


def test_an_earlier_isin_of_the_same_shares_joins_on_the_symbol_and_the_issuer_stem():
    """#264: NSE's rows carry the ISIN from before a split or bonus; the universe holds the current one."""
    icici, cipla = _co(1, "icicibank", "ICICIBANK", "INE090A01021"), _co(2, "cipla", "CIPLA", "INE059A01026")
    idx = dc.CompanyIndex([icici, cipla])
    assert idx.match(_row("INE090A01013", "ICICIBANK")) == (icici, "earlier_isin")
    assert idx.resolve(_row("INE090A01013", "ICICIBANK")) == 1
    assert idx.match(_row("INE090A01021", "ICICIBANK")) == (icici, "isin")          # the current ISIN joins as before
    assert idx.resolve(_row("INE090A01013", "OTHERSYM")) is None                   # the stem fits, the symbol doesn't
    assert idx.resolve(_row("INE000X01010", "ICICIBANK")) is None                  # the symbol fits, the ISIN is a stranger's
    assert idx.resolve(_row("INE059A01026", "ICICIBANK")) == 2                     # an ISIN that names another company wins
    assert idx.resolve(_row("-", "ICICIBANK")) is None                             # a placeholder is not guessed at
    assert idx.match(_row("", "ICICIBANK")) == (icici, "symbol")                   # no ISIN: the symbol decides, as before


def test_the_earlier_isin_rule_follows_the_symbol_history_but_never_a_reused_symbol():
    tata = _co(1, "tata-motors", "TMPV", "INE155A01022", symbols={"TATAMOTORS"})
    idx = dc.CompanyIndex([tata])
    assert idx.match(_row("INE155A01014", "TATAMOTORS")) == (tata, "earlier_isin")     # old symbol, earlier ISIN, same stem
    assert idx.resolve(_row("INE000X01010", "TATAMOTORS")) is None                    # the demerged company's own ISIN


def test_a_symbol_held_by_two_companies_with_the_same_stem_is_not_guessed():
    a, b = _co(1, "a", "AAA", "INE111A01011", symbols={"SHARED"}), _co(2, "b", "BBB", "INE111A01029", symbols={"SHARED"})
    assert dc.CompanyIndex([a, b]).resolve(_row("INE111A01003", "SHARED")) is None


# ---------------------------------------------------------------------------------------------- window splitting
class Fake:
    """A scripted NSE: `script` maps (start, end) to an Answer or a callable; any other window answers 200 with []."""

    def __init__(self, script=None, default=None):
        self.script, self.calls = script or {}, []
        self.default = default or (lambda sym, a, b: dc.Answer(200, []))

    def __call__(self, symbol, a, b):
        self.calls.append((symbol, a, b))
        s = self.script.get((symbol, a, b))
        return s(symbol, a, b) if callable(s) else (s or self.default(symbol, a, b))


def test_a_failed_window_is_split_once_and_the_halves_are_merged():
    start, end = D(2026, 7, 1), D(2026, 11, 1)
    row = {"seq_id": "7", "exchdisstime": "05-Aug-2026 10:00:00"}
    f = Fake({("X", start, end): dc.Answer(500)}, lambda s, a, b: dc.Answer(200, [row]) if a == start else dc.Answer(200, []))
    rows, log = dc.nse_window(f, "X", start, end)
    assert len(f.calls) == 3 and [r["seq_id"] for r in rows] == ["7"]
    assert log[0].split == "failed" and log[0].superseded and all(x.answered for x in log[1:])


def test_a_failed_window_is_split_only_once():
    f = Fake(default=lambda s, a, b: dc.Answer(500))
    rows, log = dc.nse_window(f, "X", D(2026, 1, 1), D(2026, 11, 1))
    assert len(f.calls) == 3 and rows == [] and not any(x.answered for x in log if not x.superseded)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refusal_is_never_retried_by_splitting(status):
    f = Fake(default=lambda s, a, b: dc.Answer(status))
    rows, log = dc.nse_window(f, "X", D(2026, 1, 1), D(2026, 11, 1))
    assert len(f.calls) == 1 and rows == [] and log[0].status == status and not log[0].answered


def test_a_suspiciously_round_row_count_is_split_in_halves():
    start, end = D(2026, 1, 1), D(2026, 11, 1)
    mk = lambda lo, n: [{"seq_id": f"{lo}-{i}"} for i in range(n)]
    f = Fake({("X", start, end): dc.Answer(200, mk("w", 100))},
             lambda s, a, b: dc.Answer(200, mk(str(a), 7)))
    rows, log = dc.nse_window(f, "X", start, end)
    assert len(f.calls) == 3 and len(rows) == 14 and log[0].split == "round count 100"


def test_a_round_count_in_a_short_window_is_kept():
    f = Fake(default=lambda s, a, b: dc.Answer(200, [{"seq_id": str(i)} for i in range(50)]))
    rows, _ = dc.nse_window(f, "X", D(2026, 10, 1), D(2026, 10, 30))
    assert len(f.calls) == 1 and len(rows) == 50


def test_a_reply_that_is_not_a_row_list_is_not_an_answer():
    f = Fake(default=lambda s, a, b: dc.Answer(200, {"error": "Access Denied"}))
    rows, log = dc.nse_window(f, "X", D(2026, 7, 1), D(2026, 7, 20))
    assert rows == [] and not log[0].answered


def test_the_run_stops_after_ten_companies_in_a_row_that_nse_fails_for():
    g = dc.RunGuard()
    for _ in range(9):
        g.record(False)
    g.record(True)                                                 # an answer resets the streak
    for _ in range(9):
        g.record(False)
    with pytest.raises(dc.NseStopped):
        g.record(False)


# ---------------------------------------------------------------------------------------------- the BSE fallback
@needs_fixtures
def test_the_bse_fallback_reads_both_subcategories_through_the_classifier():
    files = {("503806", "Earnings Call Transcript"): "bse_ann_503806_Earnings_Call_Transcript_20250923_20260917_p1.json.gz",
             ("503806", "Analyst / Investor Meet - Outcome"): "bse_ann_503806_Analyst_Investor_Meet_20250923_20260917_p1.json.gz",
             ("500034", "Earnings Call Transcript"): "bse_ann_500034_Earnings_Call_Transcript_20250923_20260917_p1.json",
             ("500034", "Analyst / Investor Meet - Outcome"): "bse_ann_500034_Analyst_Investor_Meet_20250923_20260917_p1.json.gz"}
    asked = []

    def fetch(scrip, a, b, sub, page):
        asked.append((scrip, sub, page))
        return dc.Answer(200, load(CALLS / "bse" / files[(scrip, sub)]))
    for scrip, transcripts, audio in (("503806", 4, 4), ("500034", 4, 4)):
        rows, ok, problems = dc.bse_company(fetch, scrip, D(2025, 9, 23), D(2026, 9, 17))
        anns = dc.dedupe(a for a in map(dc.norm_bse, rows) if a)
        assert ok and not problems
        assert sum(a.is_transcript for a in anns) == transcripts and sum(a.kind == "audio_video" for a in anns) == audio
        assert {s for sc, s, p in asked if sc == scrip} == set(dc.BSE_SUBCATEGORIES)
    # a transcript carries its BSE id, its attachment and the season of its dissemination date minus 7 days
    srf = [a for a in map(dc.norm_bse, load(CALLS / "bse" / files[("503806", "Earnings Call Transcript")])["Table"]) if a.is_transcript]
    first = max(srf, key=lambda a: a.disseminated)
    assert first.exchange == "BSE" and first.season == "Q1 FY27" and first.filing_key and first.url.startswith(dc.BSE_ATTACH[0])


def test_a_bse_failure_is_reported_not_raised():
    rows, ok, problems = dc.bse_company(lambda *a: dc.Answer(403), "503806", D(2026, 7, 1), D(2026, 11, 1))
    assert rows == [] and not ok and len(problems) == 2


def test_results_filed_in_a_window():
    t = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
    assert dc.results_filed_in((D(2026, 7, 1), D(2026, 11, 1)), [t])
    assert not dc.results_filed_in((D(2026, 7, 1), D(2026, 8, 11)), [t])
    assert not dc.results_filed_in((D(2026, 7, 1), D(2026, 11, 1)), [t - timedelta(days=200)])


# ---------------------------------------------------------------------------------------------- fixtures in the join
@needs_fixtures
def test_the_saved_replies_join_to_their_companies_on_isin():
    cos = []
    for i, sym in enumerate(("CIPLA", "HINDUNILVR", "INFY", "HINDALCO", "APOLLOHOSP", "MARUTI", "ICICIBANK", "ASIANPAINT"), 1):
        isin = next(r["sm_isin"] for r in nse_rows(sym) if r.get("sm_isin"))
        cos.append(dc.Company(i, sym.lower(), sym, isin, None))
    idx = dc.CompanyIndex(cos)
    for i, c in enumerate(cos, 1):
        rows = [dc.norm_nse(r) for r in nse_rows(c.symbol)]
        assert all(idx.resolve(a) == i for a in rows if a.isin), c.symbol


@needs_fixtures
def test_the_saved_replies_of_companies_whose_isin_changed_join_through_the_issuer_stem():
    """#264, on real rows: dev's universe (2 Nov 2026) holds the current ISINs of ICICI Bank, Apollo Hospitals and Asian
    Paints; NSE's rows (saved 21 Oct 2026) carry earlier ones. Before the fix none of their rows joined."""
    universe = {"ICICIBANK": "INE090A01021", "APOLLOHOSP": "INE437A01024", "ASIANPAINT": "INE021A01026"}
    cos = [_co(i, sym.lower(), sym, isin) for i, (sym, isin) in enumerate(universe.items(), 1)]
    idx = dc.CompanyIndex(cos)
    for i, sym in enumerate(universe, 1):
        rows = [dc.norm_nse(r) for r in nse_rows(sym)]
        assert rows and all(a.isin and a.isin != universe[sym] for a in rows), sym
        assert {idx.match(a)[1] for a in rows} == {"earlier_isin"}, sym
        assert {idx.resolve(a) for a in rows} == {i}, sym


# ---------------------------------------------------------------------------------------------- through the runner
import boto3
import httpx
import psycopg
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks.equity_call_transcripts import EquityCallTranscripts

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5598, "equity-call-discovery-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_call_transcripts"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
TASKS: list = []


class Exchanges:
    """NSE's announcements API and BSE's, as a mock transport serving the saved replies; `nse_status` overrides NSE's
    answer for a symbol."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.nse_status: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path, q = request.url.path, dict(request.url.params)
        if path == "/":
            return httpx.Response(200, content=b"<html>home</html>", headers={"set-cookie": "nsit=1; Path=/"})
        if path == dc.NSE_ANN_PATH:
            if q["symbol"] in self.nse_status:
                return httpx.Response(self.nse_status[q["symbol"]], content=b"Access Denied")
            f = next((CALLS / "nse").glob(f"nse_ann_{q['symbol']}_*.json.gz"), None)
            return httpx.Response(200, json=load(f) if f else [])
        if path.endswith("AnnSubCategoryGetData/w"):
            sub = q["subcategory"].replace(" / ", "_").replace(" - Outcome", "").replace(" ", "_")
            f = next((CALLS / "bse").glob(f"bse_ann_{q['strScrip']}_{sub}_*"), None)
            return httpx.Response(200, json=load(f) if f else {"Table": []})
        return httpx.Response(404)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'call discovery test', '{weekly}', '6 days', '10 days', false)
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
        ids = "(select id from core.entity where slug like 'zc-%')"
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zc-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))


def make_company(symbol: str, isin: str, bse_code: str | None = None, results_on: date | None = None) -> int:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                        (f"zc-{symbol.lower()}", f"{symbol} test")).fetchone()[0]
        c.execute("insert into equity.company (entity_id, isin, nse_symbol, bse_code) values (%s,%s,%s,%s)",
                  (eid, isin, symbol, bse_code))
        if results_on:
            src = c.execute("select id from core.source where key = 'nse'").fetchone()[0]
            c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id,
                                                 source_url, is_published)
                         values ('results_filing', %s, %s, 'results', %s, %s, 'filed', %s, 'https://x.example/', false)""",
                      (eid, f"zc-{symbol.lower()}-results", results_on,
                       datetime(results_on.year, results_on.month, results_on.day, 9, 0, tzinfo=UTC), src))
        return eid


def isin_of(symbol: str) -> str:
    return next(r["sm_isin"] for r in nse_rows(symbol) if r.get("sm_isin"))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    TASKS.clear()
    yield
    wipe()


def task() -> EquityCallTranscripts:
    class T(EquityCallTranscripts):
        key = KEY
    t = T()
    t.workflows = ("weekly", "backfill")
    TASKS.append(t)
    return t


def go(ex, since=None, until=None, now=NOW, workflow="backfill"):
    http = HttpClient(transport=httpx.MockTransport(ex), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, workflow, [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now,
               since=since, until=until)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1",
           KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


@pytest.mark.db
@needs_fixtures
def test_a_backfill_stores_every_reply_classifies_the_filings_and_registers_nothing(s3):
    for s in ("CIPLA", "HINDUNILVR", "INFY"):
        make_company(s, isin_of(s))
    make_company("NOFILE", "INE000N01010", results_on=D(2026, 8, 12))
    ex = Exchanges()
    code, tr = go(ex, since=D(2026, 7, 1))        # #265: a backfill without --since now starts at history_from (2025-01-01)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    c = checks(tr)
    assert c["nse_answered"]["passed"] and "answered for 4 of 4" in c["nse_answered"]["detail"]
    assert c["refusals"]["passed"] and c["unmatched_isin"]["passed"]
    assert not c["transcripts_found"]["passed"] and "zc-nofile Q1 FY27" in c["transcripts_found"]["detail"]
    assert "Q1 FY27" in c["discovery_summary"]["detail"]
    ann = TASKS[0].announcements
    expected = {f["seq_id"] for f in load(CALLS / "audit" / "nse_transcript_filings.json")["filings"]
                if f["symbol"] in ("CIPLA", "HINDUNILVR", "INFY")}
    assert {a.filing_key for a in ann if a.is_transcript} == expected
    assert all(a.entity_id is not None for a in ann)
    assert q("select count(*) from core.raw_file where media_type = 'application/json'")[0][0] >= 4
    first = next(r for r in ex.seen if r.url.path == dc.NSE_ANN_PATH)
    assert first.url.params["from_date"] == "01-07-2026" and first.url.params["to_date"] == "28-10-2026"
    assert ex.seen[0].url.path == "/"                          # the homepage first, for its cookies


@pytest.mark.db
@needs_fixtures
def test_a_second_run_registers_nothing_new(s3):
    make_company("CIPLA", isin_of("CIPLA"))
    go(Exchanges())
    raw = q("select count(*) from core.raw_file")
    docs = q("select count(*) from core.document where entity_id in (select id from core.entity where slug like 'zc-%')")
    code, tr = go(Exchanges())
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert q("select count(*) from core.raw_file") == raw
    assert q("select count(*) from core.document where entity_id in (select id from core.entity where slug like 'zc-%')") == docs


@pytest.mark.db
@needs_fixtures
def test_bse_serves_a_company_nse_refuses_and_the_refusals_are_counted(s3):
    make_company("SRF", "INE647A01010", "503806")
    ex = Exchanges()
    ex.nse_status["SRF"] = 403
    code, tr = go(ex)
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert c["nse_answered"]["passed"] and "BSE fallback served zc-srf" in c["nse_answered"]["detail"]
    assert not c["refusals"]["passed"] and not c["refusals"]["blocking"] and c["refusals"]["detail"].startswith("1 ")
    t = [a for a in TASKS[0].announcements if a.is_transcript]
    assert len(t) == 4 and {a.exchange for a in t} == {"BSE"} and all(a.entity_id for a in t)
    assert {r.url.params["subcategory"] for r in ex.seen if r.url.path.endswith("AnnSubCategoryGetData/w")} == set(dc.BSE_SUBCATEGORIES)


@pytest.mark.db
@needs_fixtures
def test_bse_also_runs_when_nse_has_no_rows_for_a_window_in_which_the_results_are_filed(s3):
    make_company("SRF", "INE647A01010", "503806", results_on=D(2026, 7, 20))
    code, tr = go(Exchanges())                                  # NSE answers with no rows for SRF (no saved reply)
    assert code == 0, tr
    assert len([a for a in TASKS[0].announcements if a.is_transcript]) == 4


@pytest.mark.db
@needs_fixtures
def test_a_company_nse_and_bse_both_fail_for_fails_the_run(s3):
    make_company("CIPLA", isin_of("CIPLA"))
    ex = Exchanges()
    ex.nse_status["CIPLA"] = 403
    code, tr = go(ex)
    assert code != 0 and tr[0] == "checks_failed", tr
    c = checks(tr)["nse_answered"]
    assert not c["passed"] and c["blocking"] and "no answer for zc-cipla" in c["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_company_whose_universe_isin_differs_from_its_rows_is_joined_and_the_rows_are_named(s3):
    make_company("ICICIBANK", "INE090A01021")                     # the universe holds the current ISIN; NSE's rows carry INE090A01013
    code, tr = go(Exchanges())
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    transcripts = [a for a in TASKS[0].announcements if a.is_transcript]
    assert transcripts and all(a.entity_id is not None and a.joined_by == "earlier_isin" for a in transcripts)
    assert c["unmatched_isin"]["passed"] and c["unmatched_isin"]["detail"].startswith("0 transcript rows")
    e = c["joined_by_earlier_isin"]
    assert e["passed"] and not e["blocking"] and e["detail"].startswith(f"{len(transcripts)} transcript rows joined")
    assert "zc-icicibank ICICIBANK/ICICIBANK ISIN INE090A01013 (universe: INE090A01021)" in e["detail"]
    assert all(f"seq {a.filing_key}" in e["detail"] for a in transcripts)


@pytest.mark.db
@needs_fixtures
def test_a_row_that_fits_no_company_stays_unmatched_and_is_named(s3):
    make_company("CIPLA", "INE000Z01010")                         # an ISIN that shares nothing with the rows' INE059A01026
    code, tr = go(Exchanges())
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    transcripts = [a for a in TASKS[0].announcements if a.is_transcript]
    assert transcripts and all(a.entity_id is None and a.joined_by is None for a in transcripts)
    u = c["unmatched_isin"]
    assert not u["passed"] and not u["blocking"] and u["detail"].startswith(f"{len(transcripts)} transcript rows whose ISIN")
    assert "CIPLA/CIPLA ISIN INE059A01026" in u["detail"] and all(f"seq {a.filing_key}" in u["detail"] for a in transcripts)
    assert c["joined_by_earlier_isin"]["detail"].startswith("0 transcript rows")


@pytest.mark.db
@needs_fixtures
def test_a_backfill_without_since_reads_nse_from_history_from_in_windows_of_12_months_or_less(s3):
    make_company("CIPLA", isin_of("CIPLA"))
    ex = Exchanges()
    code, tr = go(ex)                                          # no --since
    assert code == 0 and tr[0] == "success", tr
    asked = sorted({(r.url.params["from_date"], r.url.params["to_date"]) for r in ex.seen if r.url.path == dc.NSE_ANN_PATH})
    assert asked == [("01-01-2025", "31-12-2025"), ("01-01-2026", "28-10-2026")]


@pytest.mark.db
def test_the_production_guard_holds_a_scheduled_first_run_but_not_a_refresh():
    class Never(EquityCallTranscripts):
        key = "test.equity_call_transcripts_never_run"
    t = Never()
    with psycopg.connect(ADMIN, autocommit=True) as c:
        weekly = t.due(c, "weekly", NOW)
        refresh = t.due(c, "refresh", NOW)
    assert weekly.due is False and "waiting for its first recorded run" in weekly.reason
    assert refresh.due is True
