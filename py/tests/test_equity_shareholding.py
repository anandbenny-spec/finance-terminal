"""equity.shareholding (job 2A-10): the pattern reader's second use on the 20 saved shareholding patterns, the listing
parser, and the task run through the runner on mock replies built from those files.

The files are in data/fixtures/equity (the private fixtures repo; the tests skip without it, and CI checks it out):
`xbrl_shp/<SYMBOL>/` (the latest pattern of each of the audit's 20 companies, two for TCS and Hindustan Unilever) and
`audit/X1_latest.json` (check X1.4: each company's XBRL value and NSE's own). NSE's listing reply isn't saved, so the
mock builds its rows from the file names (their time stamps) and X1's values. No NSE data is kept in this repo
(CLAUDE.md §4)."""
import gzip
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import filings as fl
from ft.equity import shareholding as sh
from ft.equity import xbrl as x
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks.equity_shareholding import EquityShareholding

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
SHP, X1 = FIX / "xbrl_shp", FIX / "audit" / "X1_latest.json"
needs_fixtures = pytest.mark.skipif(not SHP.exists() or not X1.exists(),
                                    reason="the saved files aren't checked out (data/fixtures)")
D = date


def unzipped(path: Path) -> bytes:
    with gzip.open(path) as f:
        return f.read()


def x1_values() -> dict:
    """X1.4's detail: symbol -> {xbrl, api, found_by, date} for the latest pattern of each company."""
    def walk(o):
        if isinstance(o, dict):
            v = o.get("values")
            if isinstance(v, dict) and "RELIANCE" in v:
                return v
            for sub in o.values():
                r = walk(sub)
                if r:
                    return r
        elif isinstance(o, list):
            for sub in o:
                r = walk(sub)
                if r:
                    return r
    return walk(json.loads(X1.read_text()))


def files() -> dict[str, dict[str, Path]]:
    """symbol -> {file name -> path}."""
    return {d.name: {p.name[:-3]: p for p in sorted(d.glob("*.xml.gz"))} for d in sorted(SHP.iterdir()) if d.is_dir()}


def latest(symbol: str) -> tuple[str, Path]:
    """The file of the symbol's newest quarter."""
    best = max(files()[symbol].items(), key=lambda kv: sh.parse_pattern(unzipped(kv[1])).as_of)
    return best


# ---------------------------------------------------------------------------------------------- the reader
@needs_fixtures
def test_promoter_pct_of_each_of_the_20_companies_equals_x1s_value():
    expect = x1_values()
    assert len(expect) == 20 and set(expect) == set(files())
    for symbol, v in expect.items():
        _, path = latest(symbol)
        p = sh.parse_pattern(unzipped(path))
        assert p.promoter_pct == Decimal(str(v["xbrl"])), symbol
        assert p.promoter_pct == Decimal(str(v["api"])), symbol
        assert p.as_of == fl.parse_date(v["date"]), symbol


@needs_fixtures
@pytest.mark.parametrize("symbol", ["LT", "HDFCBANK", "FEDERALBNK"])
def test_a_company_with_no_promoter_group_gives_zero_percent_and_no_promoter_shares(symbol):
    p = sh.parse_pattern(unzipped(latest(symbol)[1]))
    assert p.promoter_pct == 0 and p.promoter_shares is None and p.found_by == sh.NO_PROMOTER
    assert p.total_shares and p.total_shares > 1_000_000_000          # the pattern's own total row


@needs_fixtures
def test_shares_are_the_filed_counts_of_the_promoter_and_total_rows():
    tcs = sh.parse_pattern(unzipped(latest("TCS")[1]))
    assert (tcs.as_of, tcs.promoter_shares, tcs.total_shares) == (D(2026, 9, 30), Decimal(2596558628), Decimal(3618087518))
    assert tcs.found_by == sh.PCT_TAGS[0]
    infy = sh.parse_pattern(unzipped(latest("INFY")[1]))               # the filed % is not promoter ÷ total shares
    assert infy.promoter_pct == Decimal("13.82") and infy.promoter_shares / infy.total_shares != infy.promoter_pct / 100


@needs_fixtures
def test_the_two_saved_quarters_of_a_company_are_read_to_their_own_quarter_ends():
    got = {sh.parse_pattern(unzipped(p)).as_of for p in files()["TCS"].values()}
    assert got == {D(2026, 6, 30), D(2026, 9, 30)}


@needs_fixtures
def test_a_file_without_shareholder_category_contexts_is_refused_not_read_as_zero():
    body = (b"<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance' xmlns:a='urn:a'>"
            b"<xbrli:context id='MainI'><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>"
            b"<a:DateOfReport contextRef='MainI'>2026-09-30</a:DateOfReport></xbrli:xbrl>")
    with pytest.raises(sh.ShareholdingError, match="not a shareholding pattern") as e:
        sh.parse_pattern(body)
    assert e.value.kind == "invalid"
    for bad in (b"<html><body>Access Denied", b"<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance'/>"):
        with pytest.raises(sh.ShareholdingError):
            sh.parse_pattern(bad)
    # a results filing is not a pattern either
    results = next((FIX / "xbrl" / "RELIANCE").glob("*.xml.gz"))
    with pytest.raises(sh.ShareholdingError, match="not a shareholding pattern"):
        sh.parse_pattern(unzipped(results))


@needs_fixtures
def test_a_promoter_figure_outside_0_to_100_is_refused_as_a_range_error():
    body = unzipped(latest("TCS")[1])
    assert b">0.7177<" in body
    with pytest.raises(sh.ShareholdingError, match="outside 0–100") as e:
        sh.parse_pattern(body.replace(b">0.7177<", b">1.7177<"))
    assert e.value.kind == "range"


@needs_fixtures
def test_a_figure_100_times_too_large_that_its_own_share_counts_confirm_is_unit_suspect():
    body = unzipped(latest("TCS")[1])
    with pytest.raises(sh.ShareholdingError, match="divided by 100") as e:
        sh.parse_pattern(body.replace(b">0.7177<", b">71.77<"))
    assert e.value.kind == "unit_suspect"
    d = e.value.detail
    assert d["filed"] == Decimal("7177.00") and d["divided"] == Decimal("71.77") and abs(d["ratio"] - d["divided"]) <= sh.UNIT_GAP


@needs_fixtures
def test_two_different_promoter_figures_at_the_best_rank_are_refused():
    body = unzipped(latest("TCS")[1])
    inst = x.parse(body)
    f = x.facts_on(inst, sh.PCT_TAGS[0], sh.AXIS, sh.PROMOTER_MEMBER)[0]
    assert f.value == Decimal("0.7177")
    # a second fact for the same member and tag with another value (in the file's own context)
    extra = (f"<in-bse-shp:{sh.PCT_TAGS[0]} contextRef=\"{f.context}\" decimals=\"INF\" unitRef=\"pure\">0.5</in-bse-shp:"
             f"{sh.PCT_TAGS[0]}></xbrli:xbrl>").encode()
    with pytest.raises(sh.ShareholdingError, match="ambiguous"):
        sh.parse_pattern(body.replace(b"</xbrli:xbrl>", extra))


@needs_fixtures
def test_facts_on_finds_a_fact_by_element_and_member_and_leaves_the_results_reader_alone():
    inst = x.parse(unzipped(latest("TCS")[1]))
    assert [f.value for f in x.facts_on(inst, sh.PCT_TAGS[0], sh.AXIS, sh.PROMOTER_MEMBER)] == [Decimal("0.7177")]
    assert x.facts_on(inst, sh.PCT_TAGS[0], sh.AXIS, "NoSuchMember") == []
    assert x.facts_on(inst, sh.PCT_TAGS[0], "NoSuchAxis", sh.PROMOTER_MEMBER) == []
    assert [f.value for f in x.facts_on(inst, "NumberOfShares", sh.AXIS, (sh.TOTAL_MEMBER.upper(),))] == [Decimal(3618087518)]


# ---------------------------------------------------------------------------------------------- the listing
ROW = {"symbol": "X", "date": "30-SEP-2026", "submissionDate": "19-Oct-2026 06:37:41", "pr_and_prgrp": "71.77",
       "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/SHP_1741905_19102026063741_WEB.xml"}


def test_a_listing_row_is_read_to_its_quarter_end_submission_time_and_nse_figure():
    lst = sh.parse_master([ROW], "X")
    p = lst.patterns[0]
    assert (p.file_name, p.as_of, p.nse_pct) == ("SHP_1741905_19102026063741_WEB.xml", D(2026, 9, 30), Decimal("71.77"))
    assert p.filed_at == datetime(2026, 10, 19, 6, 37, 41, tzinfo=fl.INDIA)
    assert p.filed_at.astimezone(UTC) == datetime(2026, 10, 19, 1, 7, 41, tzinfo=UTC)


def test_rows_without_a_link_are_counted_and_unreadable_rows_named_and_a_file_listed_twice_is_one_pattern():
    rows = [ROW, {**ROW}, {**ROW, "xbrl": None}, {**ROW, "xbrl": "-"}, {**ROW, "xbrl": " "},
            {**ROW, "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/-"},
            {**ROW, "xbrl": ROW["xbrl"].replace(".xml", ".html")},
            {**ROW, "xbrl": ROW["xbrl"].replace("SHP_1741905", "SHP_9"), "date": "soon"}]
    lst = sh.parse_master(rows, "X")
    assert len(lst.patterns) == 1 and lst.no_link == 4
    assert len(lst.unreadable) == 2 and "not an .xml link" in lst.unreadable[0] and "quarter end unreadable" in lst.unreadable[1]


def test_the_submission_time_falls_back_to_the_broadcast_time_and_then_the_file_names():
    base = {**ROW, "submissionDate": None, "pr_and_prgrp": "-"}
    assert sh.parse_master([{**base, "broadcastDate": "20-Oct-2026 08:00:00"}], "X").patterns[0].filed_at == \
        datetime(2026, 10, 20, 8, 0, tzinfo=fl.INDIA)
    p = sh.parse_master([base], "X").patterns[0]
    assert p.filed_at == datetime(2026, 10, 19, 6, 37, 41, tzinfo=fl.INDIA) and p.nse_pct is None
    nameless = {**base, "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/pattern.xml"}
    assert "no submission time" in sh.parse_master([nameless], "X").unreadable[0]


def test_quarter_ends_are_the_calendar_quarters_in_a_window():
    assert sh.quarter_ends(D(2025, 12, 31), D(2026, 6, 30)) == [D(2025, 12, 31), D(2026, 3, 31), D(2026, 6, 30)]
    assert sh.quarter_ends(D(2026, 7, 1), D(2026, 9, 29)) == []


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5598, "equity-shareholding-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_shareholding"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
ARCHIVE = "https://nsearchives.nseindia.com/corporate/xbrl/"


def stamp_time(name: str) -> str:
    t = datetime.strptime(name.split("_")[2], "%d%m%Y%H%M%S")
    return t.strftime("%d-%b-%Y %H:%M:%S")


def row_for(name: str, body: bytes, nse: str | None = None) -> dict:
    p = sh.parse_pattern(body)
    return {"symbol": "?", "date": p.as_of.strftime("%d-%b-%Y").upper(), "submissionDate": stamp_time(name),
            "pr_and_prgrp": nse if nse is not None else f"{p.promoter_pct:.2f}", "xbrl": ARCHIVE + name}


class Nse:
    """NSE's API and archive as a mock transport: listing rows built from the saved patterns and the patterns themselves.
    `files` overrides an instance (a file name -> bytes, None for 404); `rows` appends listing rows (symbol -> [row]);
    `nse` overrides NSE's own figure for a file name; `seen` records the requests."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.files: dict[str, bytes | None] = {}
        self.rows: dict[str, list[dict]] = {}
        self.nse: dict[str, str] = {}
        self.paths = {name: p for d in files().values() for name, p in d.items()}
        self.list = {s: [row_for(n, unzipped(p), self.nse.get(n)) for n, p in d.items()] for s, d in files().items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path, q = request.url.path, dict(request.url.params)
        if path == "/":
            return httpx.Response(200, content=b"<html>home</html>", headers={"set-cookie": "nsit=1; Path=/"})
        if path == sh.LIST_PATH:
            sym = q["symbol"]
            rows = []
            for n, p in files().get(sym, {}).items():
                rows.append(row_for(n, unzipped(p), self.nse.get(n)))
            return httpx.Response(200, json=rows + self.rows.get(sym, []))
        name = path.rsplit("/", 1)[-1]
        data = self.files[name] if name in self.files else (unzipped(self.paths[name]) if name in self.paths else None)
        return httpx.Response(404) if data is None else httpx.Response(200, content=data)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity shareholding test', '{weekly}', '6 days', '100 days', false)
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
        ids = "(select id from core.entity where slug like 'zs-%')"
        c.execute(f"delete from equity.shareholding where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zs-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))


def make_companies(names, listed_on=None):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for n in names:
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                            (f"zs-{n.lower()}", f"{n} test")).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, nse_symbol, fy_end_month, listed_on)
                         values (%s, %s, 3, %s)""", (eid, n, listed_on))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def task() -> EquityShareholding:
    class T(EquityShareholding):
        key = KEY
    t = T()
    t.workflows = ("weekly", "backfill")
    return t


WINDOW = {"since": D(2026, 1, 1), "until": D(2026, 10, 27)}


def go(ex, since=None, until=None, now=NOW, workflow="backfill"):
    http = HttpClient(transport=httpx.MockTransport(ex), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, workflow, [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: now,
               since=since, until=until)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s
              order by id desc limit 1""", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def rows():
    return q("""select e.slug, s.as_of, s.promoter_pct, s.promoter_shares, s.total_shares, s.filed_at, s.exchange,
                       d.external_key, d.version_no, d.is_published, d.doc_type, s.source_url
                  from equity.shareholding s join core.document d on d.id = s.document_id
                  join core.entity e on e.id = s.entity_id where e.slug like 'zs-%%' order by 1, 2, 8""")


@pytest.mark.db
@needs_fixtures
def test_a_backfill_registers_every_pattern_with_the_promoter_percent_x1_found(s3):
    make_companies(list(files()))
    ex = Nse()
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert {n: (v["passed"], v["blocking"]) for n, v in c.items()} == {
        "lists_read": (True, True), "instances_are_patterns": (True, True), "promoter_range": (True, True),
        "held_back": (True, False), "corrected": (True, False), "agrees_with_nse": (True, False), "quarter_move": (True, False), "quarters_filed": (False, False),
        "files_served": (True, False), "skipped_links": (True, False)}
    got = rows()
    assert len(got) == tr[1] == 22                                    # 20 companies, two quarters for TCS and HUL
    by = {(r[0], r[1]): r for r in got}
    for symbol, v in x1_values().items():
        r = by[(f"zs-{symbol.lower()}", fl.parse_date(v["date"]))]
        assert r[2] == Decimal(str(v["xbrl"])) and r[6] == "NSE", symbol
    tcs = by[("zs-tcs", D(2026, 9, 30))]
    assert (tcs[3], tcs[4]) == (Decimal(2596558628), Decimal(3618087518))
    assert by[("zs-lt", D(2026, 9, 30))][2] == 0 and by[("zs-lt", D(2026, 9, 30))][3] is None
    assert all(r[9] is False and r[10] == "shareholding_xbrl" and r[8] == 1 for r in got)
    assert tcs[5] == datetime(2026, 10, 19, 1, 7, 41, tzinfo=UTC) and tcs[7] == "SHP_1741905_19102026063741_WEB.xml"
    assert tcs[11] == ARCHIVE + tcs[7]
    # no event is created, every document points at the raw file whose bytes it is, and the homepage came first
    assert q("select count(*) from core.event where entity_id in (select id from core.entity where slug like 'zs-%')")[0][0] == 0
    assert q("""select count(*) from core.document d join core.raw_file r on r.id = d.raw_file_id
                 where d.entity_id in (select id from core.entity where slug like 'zs-%') and r.media_type like '%xml%'
                   and d.doc_type = 'shareholding_xbrl'""")[0][0] == 22
    assert ex.seen[0].url.path == "/" and sum(1 for r in ex.seen if r.url.path == "/") == 1
    assert q("select count(*) from core.raw_file where media_type = 'application/json'")[0][0] >= 20
    # the Dec 2025 and Mar 2026 quarters ended long ago and no company has a saved pattern for them
    assert "20 companies" in c["quarters_filed"]["detail"] or "zs-" in c["quarters_filed"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_run_writes_nothing(s3):
    make_companies(["TCS", "RELIANCE"])
    go(Nse(), **WINDOW)
    before = rows()
    code, tr = go(Nse(), **WINDOW)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert rows() == before


@pytest.mark.db
@needs_fixtures
def test_a_revised_pattern_for_the_same_quarter_adds_a_row_and_keeps_the_first(s3):
    make_companies(["TCS"])
    go(Nse(), **WINDOW)
    first, path = latest("TCS")
    revised = "SHP_1741999_21102026101010_WEB.xml"
    ex = Nse()
    ex.files[revised] = unzipped(path).replace(b">0.7177<", b">0.7100<")
    ex.rows["TCS"] = [{**row_for(revised, ex.files[revised]), "date": "30-SEP-2026"}]
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success" and tr[1] == 1, tr
    got = [r for r in rows() if r[1] == D(2026, 9, 30)]
    assert [(r[7], r[2]) for r in got] == [(first, Decimal("71.7700")), (revised, Decimal("71.0000"))]
    # a reader's rule: the latest filed per company and quarter
    assert q("""select promoter_pct from (select distinct on (entity_id, as_of) * from equity.shareholding
                  where entity_id in (select id from core.entity where slug = 'zs-tcs')
                  order by entity_id, as_of, filed_at desc, document_id desc) t where as_of = '2026-09-30'""") == \
        [(Decimal("71.0000"),)]
    # the move check sees a revision as the same quarter, not a move
    assert checks(tr)["quarter_move"]["passed"] is True


@pytest.mark.db
@needs_fixtures
def test_other_bytes_under_one_file_name_make_a_new_version_and_an_alert(s3):
    make_companies(["TCS"])
    go(Nse(), **WINDOW)
    name, path = latest("TCS")
    ex = Nse()
    ex.files[name] = unzipped(path).replace(b"<xbrli:xbrl", b"<!-- changed --><xbrli:xbrl", 1)
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success" and tr[1] == 1, tr
    assert [r[8] for r in rows() if r[7] == name] == [1, 2]
    assert q("select resolved_at is null from ops.alert where dedupe_key = %s", f"document_changed:{KEY}:{name}") == [(True,)]


@pytest.mark.db
@needs_fixtures
def test_a_served_file_that_is_not_a_pattern_fails_the_run_naming_it_and_nothing_is_stored(s3):
    make_companies(["TCS", "RELIANCE"])
    name, _ = latest("RELIANCE")
    ex = Nse()
    ex.files[name] = b"<html><body>Access Denied</body></html>"
    code, tr = go(ex, **WINDOW)
    assert code != 0 and tr[0] == "checks_failed", tr
    c = checks(tr)
    assert c["instances_are_patterns"]["passed"] is False and name in c["instances_are_patterns"]["detail"]
    assert rows() == []                                              # the load rolled back: not even TCS


@pytest.mark.db
@needs_fixtures
def test_a_file_filed_in_percent_is_stored_divided_by_100_with_its_evidence_and_the_run_passes(s3):
    make_companies(["TCS", "RELIANCE"])
    name, path = latest("TCS")
    ex = Nse()
    ex.files[name] = unzipped(path).replace(b">0.7177<", b">71.77<")
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert c["promoter_range"]["passed"] is True and c["held_back"]["passed"] is True
    assert c["corrected"]["passed"] is False and c["corrected"]["blocking"] is False
    d = c["corrected"]["detail"]
    assert "TCS" in d and "7177" in d and "shown 71.77%" in d and name in d and "NSE lists 71.77%" in d
    r = q("""select s.promoter_pct, s.correction, s.filed_pct, s.share_ratio_pct, s.nse_pct, s.promoter_shares
               from equity.shareholding s join core.document d on d.id = s.document_id where d.external_key = %s""", name)[0]
    assert r[0] == Decimal("71.77") and r[1] == "percent_for_fraction" and r[2] == Decimal("7177.00")
    assert abs(r[3] - Decimal("71.77")) <= sh.UNIT_GAP and r[4] == Decimal("71.77") and r[5] is not None
    plain = [x for x in q("select correction from equity.shareholding s join core.entity e on e.id = s.entity_id where e.slug = 'zs-reliance'")]
    assert plain and all(x[0] is None for x in plain)


@pytest.mark.db
@needs_fixtures
def test_a_percent_file_that_nse_does_not_confirm_fails_promoter_range_naming_it(s3):
    make_companies(["TCS"])
    name, path = latest("TCS")
    ex = Nse()
    ex.files[name] = unzipped(path).replace(b">0.7177<", b">71.77<")
    ex.nse[name] = "50.00"
    code, tr = go(ex, **WINDOW)
    assert code != 0 and tr[0] == "checks_failed" and rows() == []
    c = checks(tr)
    assert c["promoter_range"]["passed"] is False and "NSE lists 50" in c["promoter_range"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_an_out_of_range_file_on_the_approved_list_is_held_back_by_name_and_any_other_still_fails(s3, monkeypatch):
    make_companies(["TCS", "RELIANCE"])
    n1, p1 = latest("TCS")
    n2, p2 = latest("RELIANCE")
    ex = Nse()
    ex.files[n1] = unzipped(p1).replace(b">0.7177<", b">1.7177<")
    ex.files[n2] = unzipped(p2)
    monkeypatch.setattr(sh, "held_files", lambda path=None: {n1: "own share counts don't confirm"})
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert c["promoter_range"]["passed"] is True
    assert c["held_back"]["passed"] is False and c["held_back"]["blocking"] is False and n1 in c["held_back"]["detail"]
    assert c["files_served"]["passed"] is True and n1 not in [r[7] for r in rows()]
    monkeypatch.setattr(sh, "held_files", lambda path=None: {})
    code, tr = go(ex, **WINDOW)
    assert code != 0 and checks(tr)["promoter_range"]["passed"] is False


@needs_fixtures
def test_corrected_needs_nse_to_agree_where_it_lists_a_figure():
    body = unzipped(latest("TCS")[1]).replace(b">0.7177<", b">71.77<")
    with pytest.raises(sh.ShareholdingError) as e:
        sh.parse_pattern(body)
    p = sh.corrected(e.value, Decimal("71.77"))
    assert (p.promoter_pct, p.filed_pct, p.correction) == (Decimal("71.77"), Decimal("7177.00"), sh.CORRECTION)
    assert sh.corrected(e.value, None).nse_pct is None
    with pytest.raises(sh.ShareholdingError) as bad:
        sh.corrected(e.value, Decimal(50))
    assert bad.value.kind == "range" and "NSE lists 50" in str(bad.value)


def test_nse_listing_one_decimal_less_still_agrees_with_the_correction():
    """BAJAJFINSV, 21 Apr 2022: filed 6077.00, ratio 60.77, NSE lists 60.8 (rounded). Regression for the 0.05 NSE gap."""
    err = sh.ShareholdingError("x", "unit_suspect", {
        "as_of": date(2022, 3, 31), "filed": Decimal("6077.00"), "divided": Decimal("60.77"),
        "ratio": Decimal("60.77"), "p_shares": 1, "t_shares": 2, "found_by": "f"})
    p = sh.corrected(err, Decimal("60.8"))
    assert p.promoter_pct == Decimal("60.77") and p.nse_pct == Decimal("60.8") and p.correction == sh.CORRECTION
    with pytest.raises(sh.ShareholdingError):
        sh.corrected(err, Decimal("60.9"))


def test_the_approved_list_is_well_formed():
    held = sh.held_files()
    assert all(k.endswith(".xml") and v for k, v in held.items())
    assert len(held) == 76 and "SHP_168310_646925_21042022103052_WEB.xml" not in held


@pytest.mark.db
@needs_fixtures
def test_a_promoter_figure_outside_0_to_100_fails_the_run_naming_it(s3):
    make_companies(["TCS"])
    name, path = latest("TCS")
    ex = Nse()
    ex.files[name] = unzipped(path).replace(b">0.7177<", b">1.7177<")
    code, tr = go(ex, **WINDOW)
    assert code != 0 and tr[0] == "checks_failed" and rows() == []
    c = checks(tr)
    assert c["promoter_range"]["passed"] is False and "TCS" in c["promoter_range"]["detail"]
    assert c["instances_are_patterns"]["passed"] is True


@pytest.mark.db
@needs_fixtures
def test_a_listing_that_cannot_be_read_fails_the_run(s3):
    make_companies(["TCS"])
    ex = Nse()
    inner = ex.__call__

    def broken(request):
        if request.url.path == sh.LIST_PATH:
            return httpx.Response(200, content=b"<html>Access Denied</html>")
        return inner(request)
    code, tr = go(broken, **WINDOW)
    assert code != 0 and checks(tr)["lists_read"]["passed"] is False and rows() == []


@pytest.mark.db
@needs_fixtures
def test_a_move_of_more_than_10_points_or_a_disagreement_with_nse_is_a_warning_naming_it(s3):
    make_companies(["TCS", "RELIANCE"])
    june = next(n for n, p in files()["TCS"].items() if sh.parse_pattern(unzipped(p)).as_of == D(2026, 6, 30))
    ex = Nse()
    ex.files[june] = unzipped(files()["TCS"][june]).replace(b">0.7177<", b">0.2000<")     # 20% in June, 71.77% in Sep
    ex.nse[latest("RELIANCE")[0]] = "50.00"                                                   # NSE says 50.00, the file 50.48
    code, tr = go(ex, **WINDOW)
    assert code == 0 and tr[0] == "success", tr           # warnings don't stop the load
    c = checks(tr)
    assert c["quarter_move"]["passed"] is False and "zs-tcs 2026-06-30 to 2026-09-30" in c["quarter_move"]["detail"]
    assert c["agrees_with_nse"]["passed"] is False and "RELIANCE" in c["agrees_with_nse"]["detail"]
    assert "50.48%" in c["agrees_with_nse"]["detail"] and "NSE 50.00%" in c["agrees_with_nse"]["detail"]
    assert len(rows()) == 3


@pytest.mark.db
@needs_fixtures
def test_a_quarter_without_a_pattern_is_named_only_once_it_ended_more_than_45_days_ago(s3):
    make_companies(["TCS"])
    code, tr = go(Nse(), since=D(2026, 4, 1), until=D(2026, 10, 27))
    c = checks(tr)
    assert code == 0 and c["quarters_filed"]["passed"] is True        # Jun and Sep are stored; Sep 30 + 45 days is after "now"
    make_companies(["MEESHO"], listed_on=D(2026, 1, 15))              # MEESHO (the saved file is for Sep) has no June pattern here
    ex = Nse()
    ex.list = {}
    code, tr = go(ex, since=D(2026, 1, 1), until=D(2026, 10, 27))
    d = checks(tr)["quarters_filed"]
    assert d["passed"] is False and "zs-meesho (2: 2026-03-31, 2026-06-30)" in d["detail"]


@pytest.mark.db
@needs_fixtures
def test_the_weekly_window_is_the_last_400_days_of_quarter_ends(s3):
    make_companies(["TCS"])
    code, tr = go(Nse(), workflow="weekly")
    assert code == 0 and tr[1] == 2 and len(rows()) == 2              # 28 Oct 2026 − 400 days = 23 Sep 2025: both are in
    wipe()
    make_companies(["TCS"])
    code, tr = go(Nse(), since=D(2026, 7, 1), until=D(2026, 9, 30))
    assert tr[1] == 1 and [r[1] for r in rows()] == [D(2026, 9, 30)]


@needs_fixtures
def test_a_range_error_says_whether_the_file_has_share_counts():
    """Option A (#116) holds a file by name only when it has share counts; the error carries that fact."""
    body = unzipped(latest("TCS")[1]).replace(b">0.7177<", b">1.7177<")
    with pytest.raises(sh.ShareholdingError) as e:
        sh.parse_pattern(body)
    assert e.value.kind == "range" and e.value.detail == {"counts": True}
