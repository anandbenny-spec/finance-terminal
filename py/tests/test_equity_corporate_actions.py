"""equity.corporate_actions (job 2A-08): the subject parser and the factor maths on the saved replies, and the task run
through the runner on the saved bhavcopies' prices.

The saved files are in data/fixtures/equity (the private fixtures repo; the tests skip without it, and CI checks it
out): NSE's corporate-action replies for 22 companies (api/corporate_actions/, MEESHO's BSE reply is empty) and the
bhavcopies of the months around the card's four ex-dates: RELIANCE's demerger (20 Jul 2023), ITC's (6 Jan 2025), Tata
Motors' (14 Oct 2025) and BAJFINANCE's split and bonus (16 Jun 2025). ITC isn't among the 22 replies, so its reply is
written here (one row, NSE's wording). Prices that aren't NSE's are made-up numbers for the rules (a company `zc-...`
whose closes are round numbers). No NSE or BSE data is kept in this repo (CLAUDE.md §4)."""
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import boto3
import httpx
import psycopg
import pytest
import yaml
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import actions as ac
from ft.equity import bhavcopy as bh
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import equity_corporate_actions as eca
from ft.jobs.tasks.equity_corporate_actions import EquityCorporateActions

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
REPLIES = FIX / "api" / "corporate_actions"
BHAV = FIX / "bhavcopy" / "nse"
needs_fixtures = pytest.mark.skipif(not REPLIES.exists(), reason="the saved files aren't checked out (data/fixtures)")

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5597, "equity-actions-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_corporate_actions"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)          # 11:30 in India on 28 Oct 2026
D = date
DEC = Decimal


def reply(symbol: str) -> list[dict]:
    return json.loads((REPLIES / f"nse_corpactions_{symbol}.json").read_text())


def nse_row(symbol: str, ex: str, subject: str, face_value: str = "10", rec: str = "-") -> dict:
    """A row in NSE's shape (the saved replies' keys)."""
    return {"bcEndDate": "-", "bcStartDate": "-", "caBroadcastDate": None, "comp": f"{symbol} test", "exDate": ex,
            "faceVal": face_value, "ind": "-", "isin": "INE000000000", "ndEndDate": "-", "ndStartDate": "-",
            "recDate": rec, "series": "EQ", "subject": subject, "symbol": symbol}


# ---------------------------------------------------------------------------------------------- the parser
@needs_fixtures
def test_the_parser_reads_every_subject_in_the_saved_replies():
    """Every subject of the 22 saved NSE replies (284 rows; MEESHO's is empty) is read in full: a kind with every
    number it needs. The ones it can't read would be listed here; there are none."""
    seen, unread = {}, []
    for p in sorted(REPLIES.glob("nse_*.json")):
        for r in json.loads(p.read_text()):
            x = ac.parse_subject(r["subject"], eca._face_value(r["faceVal"]))
            seen[x.kind] = seen.get(x.kind, 0) + 1
            if not x.understood:
                unread.append(f"{r['symbol']} {r['exDate']}: {r['subject']!r} ({x.problem or 'unrecognised'})")
    assert unread == []
    assert seen == {"dividend": 251, "other": 10, "bonus": 8, "buyback": 6, "split": 4, "demerger": 4, "rights": 1}
    assert json.loads((REPLIES / "bse_corpactions_544632.json").read_text()) == []      # MEESHO on BSE: nothing


@needs_fixtures
def test_the_saved_replies_numbers_are_read_as_the_exchange_wrote_them():
    def one(symbol, ex):
        r = next(x for x in reply(symbol) if x["exDate"] == ex and "Dividend" not in x["subject"])
        return ac.parse_subject(r["subject"], eca._face_value(r["faceVal"]))
    rights = one("RELIANCE", "13-May-2020")                       # " Rights 1:15 @ Premium Rs 1247", face value 10
    assert (rights.kind, rights.ratio_new, rights.ratio_old, rights.rights_price) == ("rights", 1, 15, 1257)
    split = next(ac.parse_subject(x["subject"]) for x in reply("BAJFINANCE") if "Split" in x["subject"])
    assert (split.kind, split.face_value_old, split.face_value_new) == ("split", 2, 1)
    bonus = next(ac.parse_subject(x["subject"]) for x in reply("BAJFINANCE") if "Bonus" in x["subject"])
    assert (bonus.kind, bonus.ratio_new, bonus.ratio_old) == ("bonus", 4, 1)
    assert ac.parse_subject(" Bonus 1:5").ratio_old == 5                                # NTPC, 19 Mar 2019
    assert one("ABB", "20-Dec-2019").kind == "demerger"
    assert one("INFY", "14-Nov-2025").kind == "buyback"
    assert ac.parse_subject(" Annual General Meeting").notice


def test_dividend_amounts_are_summed_and_face_values_left_out():
    cases = {
        "Interim Dividend - Rs 19 Per Share/Special Dividend - Rs 10 Per Share": "29",
        "Interim Dividend - Rs 8 Per Share Special Dividend - Rs 67 Per Share": "75",
        " Int Div - Rs 0.71 Per Sh": "0.71",
        " Annual General Meeting/Dividend Rs 3/-": "3",
        " Interim Dividend - Rs 0.50 Per Hsare": "0.50",
        "Dividend - Re 0.75 Per Share": "0.75",
        "Final Dividend - Rs. - 6.0000": "6",                       # BSE's wording
        "Dividend - Rs 5 Per Share (Face Value Rs 10)": "5",
    }
    for subject, dps in cases.items():
        x = ac.parse_subject(subject)
        assert (x.kind, x.dividend_per_share) == ("dividend", DEC(dps)), subject
    pct = ac.parse_subject("Dividend - 50%")
    assert pct.kind == "dividend" and pct.dividend_per_share is None and not pct.understood


def test_the_parser_reads_bse_wording_and_refuses_what_it_cannot_read():
    s = ac.parse_subject("Stock  Split From Rs.10/- to Rs.2/-")
    assert (s.kind, s.face_value_old, s.face_value_new) == ("split", 10, 2)
    c = ac.parse_subject("Consolidation of Shares From Re 1/- To Rs 10/-")
    assert (c.kind, c.face_value_old, c.face_value_new, c.problem) == ("consolidation", 1, 10, None)
    up = ac.parse_subject("Face Value Split From Rs 1 To Rs 5")          # a "split" that raises the face value
    assert up.kind == "consolidation"
    assert ac.parse_subject("Bonus issue 1:1").ratio_new == 1
    assert ac.parse_subject("Rights 3:10 @ Rs 140").rights_price == 140
    assert ac.parse_subject("Rights 1:5 @ Par", DEC(2)).rights_price == 2
    no_fv = ac.parse_subject("Rights 1:15 @ Premium Rs 1247")              # BSE rows carry no face value
    assert no_fv.problem and "face value" in no_fv.problem and not no_fv.understood
    assert ac.parse_subject("Right Issue of Equity Shares").problem == "no rights ratio M:N in the subject"
    assert ac.parse_subject("Bonus").problem == "no bonus ratio A:B in the subject"
    assert ac.parse_subject("Face Value Split (Sub-Division)").problem == "no face values (from X to Y) in the subject"
    both = ac.parse_subject("Bonus 1:1 And Face Value Split From Rs 10 To Rs 5")
    assert both.several == ("split", "bonus") and not both.understood
    arrangement = ac.parse_subject("Scheme of Arrangement")
    assert arrangement.kind == "other" and arrangement.restructuring and not arrangement.understood
    assert ac.parse_subject("Capital Reduction").restructuring
    assert ac.parse_subject("Amalgamation").kind == "merger"
    assert ac.parse_subject("Demerger").kind == "demerger" and ac.parse_subject("De-merger").kind == "demerger"
    odd = ac.parse_subject("Something New")
    assert odd.kind == "other" and not odd.notice and not odd.understood


# ---------------------------------------------------------------------------------------------- the factors
def test_ratio_factors_are_exact_to_twelve_places():
    assert ac.ratio_factor("bonus", ratio_new=1, ratio_old=1) == DEC("0.5")
    assert ac.ratio_factor("bonus", ratio_new=4, ratio_old=1) == DEC("0.2")
    assert ac.ratio_factor("bonus", ratio_new=1, ratio_old=5) == DEC("0.833333333333")      # NTPC's 1:5: 5/6
    assert ac.ratio_factor("bonus", ratio_new=2, ratio_old=1) == DEC("0.333333333333")
    assert ac.ratio_factor("split", face_value_old=2, face_value_new=1) == DEC("0.5")
    assert ac.ratio_factor("split", face_value_old=10, face_value_new=2) == DEC("0.2")
    assert ac.ratio_factor("consolidation", face_value_old=1, face_value_new=10) == DEC(10)
    assert ac.product([DEC("0.5"), DEC("0.2")]) == DEC("0.1")


def test_the_cards_demerger_factors_and_the_plausibility_check():
    for c0, open1, c1, expected in (("2841.85", "2580", "2619.85", "0.90786"),      # RELIANCE, 20 Jul 2023
                                    ("481.60", "455.60", "442.65", "0.94601"),       # ITC, 6 Jan 2025
                                    ("660.75", "400.00", "395.45", "0.60537")):      # Tata Motors, 14 Oct 2025
        f = ac.preopen_factor(DEC(open1), DEC(c0))
        assert round(f, 5) == DEC(expected)
        assert ac.plausible(DEC(c0), DEC(c1), f).passed
    # BAJFINANCE, 16 Jun 2025: 0.5 x 0.2 = 0.1 and the adjusted move is +0.5% (938 / 933.10)
    chk = ac.plausible(DEC("9331"), DEC("938"), ac.product([DEC("0.5"), DEC("0.2")]))
    assert chk.passed and round(chk.adjusted_move, 4) == DEC("0.0052")
    # Tata Motors without its factor moves -51%: outside the band
    bare = ac.plausible(DEC("660.75"), DEC("395.45"), DEC(1))
    assert not bare.passed and "outside NSE's ±20% band" in bare.reason
    # a factor below 0.8 that leaves the move no closer to zero fails, inside the band or not
    flat = ac.plausible(DEC(100), DEC(90), DEC("0.75"))          # adjusted +18.2%, unadjusted -10.5%
    assert not flat.passed and "closer to zero" in flat.reason
    # the band is NSE's widest, 20% either way, as a log move: ln 1.2
    assert ac.plausible(DEC(100), DEC(120), DEC(1)).passed and not ac.plausible(DEC(100), DEC("120.01"), DEC(1)).passed


def test_rights_factor_is_the_terp_over_the_previous_close():
    # 1 new for every 15 held at 1,257 with the previous close at 1,500: TERP = (1500 x 15 + 1257) / 16 = 1484.8125
    assert ac.terp(DEC(1500), DEC(1), DEC(15), DEC(1257)) == DEC("1484.8125")
    assert ac.terp_factor(DEC(1500), DEC(1), DEC(15), DEC(1257)) == DEC("0.989875000000")


def test_dates_in_the_exchanges_formats():
    for s in ("20-Jul-2023", "20 Jul 2023", "20230720", "2023-07-20T00:00:00", "20/07/2023", "20-07-2023"):
        assert eca.parse_day(s) == D(2023, 7, 20), s
    assert eca.parse_day("-") is None and eca.parse_day(None) is None and eca.parse_day("soon") is None


# ---------------------------------------------------------------------------------------------- the review file
def write(tmp_path, text: str) -> Path:
    p = tmp_path / "corporate_action_review.yaml"
    p.write_text(text)
    return p


def test_the_shipped_review_file_loads():
    r = ac.load_reviews()
    assert isinstance(r.releases, dict) and isinstance(r.jumps, dict)


def test_every_shipped_review_names_a_curated_company():
    """A review keyed on a mistyped slug would never match its jump (the warning would go on naming it), so every
    shipped entry names a company in companies.yaml and an event of one (#113's first review, 31 Oct 2026)."""
    curated = set(yaml.safe_load((ac.CONFIG_DIR / "companies.yaml").read_text()))
    r = ac.load_reviews()
    assert {c for c, _ in r.jumps} <= curated
    assert all(any(slug.startswith(f"{c}-") for c in curated) for slug in r.releases)


def test_a_release_and_a_jump_review_load_and_malformed_entries_fail(tmp_path):
    ok = write(tmp_path, """
releases:
  - event: reliance-rights-2020-05-13
    factor: 0.98898
    evidence: TERP from the record's ratio and price
    reviewer: Claude (Opus)
    reviewed_on: 2026-10-29
jumps:
  - company: yesbank
    date: 2020-03-06
    verdict: real move
    note: RBI moratorium
    reviewer: Claude (Opus)
    reviewed_on: 2026-10-29
""")
    r = ac.load_reviews(ok)
    assert r.releases["reliance-rights-2020-05-13"].factor == DEC("0.98898")
    assert ("yesbank", D(2020, 3, 6)) in r.jumps
    bad = {
        "releases: [{event: Reliance, factor: 1, evidence: x, reviewer: y, reviewed_on: 2026-10-29}]": "isn't an event",
        "releases: [{event: a-split-2020-01-01, factor: 0, evidence: x, reviewer: y, reviewed_on: 2026-10-29}]":
            "above 0",
        "releases: [{event: a-split-2020-01-01, factor: 1, evidence: x, reviewed_on: 2026-10-29}]": "reviewer",
        "releases: [{event: a-split-2020-01-01, factor: 1, evidence: x, reviewer: y, reviewed_on: soon}]":
            "isn't a date",
        "jumps: [{company: a, date: 2020-01-01, verdict: missing action, note: x, reviewer: y, reviewed_on: 2026-10-29}]":
            "only verdict",
        "other: []": "only `releases` and `jumps`",
    }
    for text, msg in bad.items():
        with pytest.raises(ValueError, match=msg):
            ac.load_reviews(write(tmp_path, text))
    entry = "{event: a-split-2020-01-01, factor: 1, evidence: x, reviewer: y, reviewed_on: 2026-10-29}"
    twice = f"releases: [{entry}, {entry}]"
    with pytest.raises(ValueError, match="released twice"):
        ac.load_reviews(write(tmp_path, twice))


# ---------------------------------------------------------------------------------------------- the task
class Exchanges:
    """NSE's homepage and corporate-actions API, and BSE's, serving the replies set for each symbol or scrip code
    (rows outside the asked window left out, as the API does)."""

    def __init__(self):
        self.nse: dict[str, list[dict] | str] = {}
        self.bse: dict[str, list[dict]] = {}
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        u = urlsplit(str(request.url))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.asked.append(f"{u.netloc}{u.path}?{u.query}")
        if u.netloc == "www.nseindia.com" and u.path == "/":
            return httpx.Response(200, text="<html>home</html>")
        if u.path == "/api/corporates-corporateActions":
            got = self.nse.get(q["symbol"], [])
            if isinstance(got, str):
                return httpx.Response(200, text=got, headers={"content-type": "text/html"})
            lo, hi = (datetime.strptime(q[k], "%d-%m-%Y").date() for k in ("from_date", "to_date"))
            rows = [r for r in got if lo <= eca.parse_day(r["exDate"]) <= hi]
            return httpx.Response(200, json=rows)
        if u.netloc == "api.bseindia.com":
            lo, hi = (datetime.strptime(q[k], "%Y%m%d").date() for k in ("Fdate", "TDate"))
            rows = [r for r in self.bse.get(q["scripcode"], []) if lo <= eca.parse_day(r["Ex_date"]) <= hi]
            return httpx.Response(200, json={"Table": rows})
        return httpx.Response(404)


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'corporate actions test', '{weekly}', '6 days', '10 days', false)
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
        c.execute(f"delete from equity.adjustment_factor where entity_id in {ids}")
        c.execute(f"delete from equity.corporate_action where event_id in (select id from core.event where entity_id in {ids})")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.price_daily where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zc-%'")


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def company(name: str, symbol: str | None = None, bse_code: str | None = None) -> int:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company', %s, %s, 'IN', 'INR', true) returning id""",
                        (f"zc-{name.lower()}", f"{name} test")).fetchone()[0]
        c.execute("""insert into equity.company (entity_id, isin, nse_symbol, bse_code, price_exchange)
                     values (%s, %s, %s, %s, 'NSE')""", (eid, f"INE{abs(hash(name)) % 10**9:09d}", symbol, bse_code))
    return eid


def _anchors(c) -> tuple[int, int]:
    """A task run and a raw file the made-up price rows can point at (price_daily needs both)."""
    tr = c.execute("select id from ops.task_run where task_key = %s order by id limit 1", (KEY,)).fetchone()
    if tr is None:
        job = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('backfill', 'cli', 'local') "
                        "returning id").fetchone()[0]
        tr = c.execute("insert into ops.task_run (job_run_id, task_key, status) values (%s, %s, 'success') "
                       "returning id", (job, KEY)).fetchone()
    raw = c.execute("""insert into core.raw_file (object_key, source_id, sha256, bytes, media_type, fetched_at,
                                                  task_run_id)
                       values ('test/zc-prices', (select id from core.source where key = 'nse'),
                               sha256('zc-prices'::bytea), 0, 'text/csv', now(), %s)
                       on conflict (object_key) do update set bytes = 0 returning id""", (tr[0],)).fetchone()[0]
    return tr[0], raw


def prices(eid: int, rows: dict[date, tuple]) -> None:
    """Price rows (open, close, prev_close) for a test company."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        tr, raw = _anchors(c)
        for d, (o, cl, pc) in rows.items():
            c.execute("""insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low,
                             close, prev_close, raw_file_id, task_run_id)
                         values (%s, %s, 'NSE', 'EQ', %s, greatest(%s, %s), least(%s, %s), %s, %s, %s, %s)""",
                      (eid, d, DEC(o), DEC(o), DEC(cl), DEC(o), DEC(cl), DEC(cl), DEC(pc), raw, tr))


def saved_prices(eid: int, days: list[date], isins: tuple[str, ...] = (), symbol: str | None = None) -> None:
    """The saved bhavcopies' rows of one company (by ISIN, or by symbol across an ISIN change)."""
    out = {}
    for d in days:
        b = bh.parse_bhavcopy(next(BHAV.glob(f"*/nse_{d:%Y%m%d}.*")).read_bytes())
        hit = next(r for r in b.rows if r.series == "EQ" and (r.isin in isins or r.symbol == symbol))
        out[d] = (hit.open, hit.close, hit.prev_close)
    prices(eid, out)


def task() -> EquityCorporateActions:
    class T(EquityCorporateActions):
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


def factors(slug_like="zc-%"):
    return {r[0]: r[1:] for r in q(
        """select ev.slug, a.price_factor, a.method, a.status, a.reason, a.evidence
             from equity.adjustment_factor a join core.event ev on ev.id = a.event_id
            where ev.slug like %s order by 1""", slug_like)}


def events(slug_like="zc-%"):
    return {r[0]: r[1:] for r in q(
        """select ev.slug, ev.status, ev.title, ev.starts_on, ev.is_published, s.key, ca.kind, ca.subject,
                  ca.exchange, ca.dividend_per_share
             from core.event ev join equity.corporate_action ca on ca.event_id = ev.id
             join core.source s on s.id = ev.source_id where ev.slug like %s order by 1""", slug_like)}


def adjusted(eid: int):
    return {r[0]: r[1:] for r in q("select trade_date, close, adj_close from equity.price_adjusted "
                                    "where entity_id = %s order by 1", eid)}


ALL = {"since": D(2019, 1, 1), "until": D(2026, 12, 31)}


@pytest.mark.db
@needs_fixtures
def test_the_cards_four_ex_dates_are_applied_with_the_factors_as_stored(s3):
    ex = Exchanges()
    rel, itc, tmpv, baj = company("RELIANCE", "RELIANCE"), company("ITC", "ITC"), company("TMPV", "TMPV"), \
        company("BAJFINANCE", "BAJFINANCE")
    ex.nse = {"RELIANCE": reply("RELIANCE"), "TMPV": reply("TMPV"), "BAJFINANCE": reply("BAJFINANCE"),
              "ITC": [nse_row("ITC", "06-Jan-2025", "Demerger", "1", "06-Jan-2025"),
                      nse_row("ITC", "04-Jun-2025", "Dividend - Rs 7.85 Per Share", "1", "04-Jun-2025")]}
    saved_prices(rel, [D(2023, 7, 19), D(2023, 7, 20)], ("INE002A01018",))
    saved_prices(itc, [D(2025, 1, 3), D(2025, 1, 6)], ("INE154A01025",))
    saved_prices(tmpv, [D(2025, 10, 13), D(2025, 10, 14)], ("INE155A01022",))
    saved_prices(baj, [D(2025, 6, 13), D(2025, 6, 16)], symbol="BAJFINANCE")
    code, tr = go(ex, **ALL)
    assert code == 0 and tr[0] == "success", tr
    fs = factors()
    for slug, expected in (("zc-reliance-demerger-2023-07-20", "0.90786"), ("zc-itc-demerger-2025-01-06", "0.94601"),
                           ("zc-tmpv-demerger-2025-10-14", "0.60537")):
        factor, method, status, reason, evidence = fs[slug]
        assert (round(factor, 5), method, status, reason) == (DEC(expected), "special_preopen", "applied", None), slug
        assert evidence["passed"] is True and abs(evidence["adjusted_move"]) < 0.05
    assert fs["zc-reliance-demerger-2023-07-20"][4]["c0"] == "2841.85"
    assert fs["zc-reliance-demerger-2023-07-20"][4]["open1"] == "2580"
    bonus, split = fs["zc-bajfinance-bonus-2025-06-16"], fs["zc-bajfinance-split-2025-06-16"]
    assert (bonus[0], bonus[1], bonus[2]) == (DEC("0.2"), "ratio", "applied")
    assert (split[0], split[1], split[2]) == (DEC("0.5"), "ratio", "applied")
    assert bonus[4]["group_factor"] == "0.1" and sorted(bonus[4]["group"]) == ["zc-bajfinance-bonus-2025-06-16",
                                                                              "zc-bajfinance-split-2025-06-16"]
    assert round(bonus[4]["adjusted_move"], 4) == 0.0052                     # +0.5%: 938 / (9331 x 0.1)
    # the view restates the earlier closes: RELIANCE's 19 Jul 2023 close at the demerger price, BAJFINANCE's / 10
    assert round(adjusted(rel)[D(2023, 7, 19)][1], 2) == DEC("2580.00")
    assert adjusted(baj)[D(2025, 6, 13)][1] == DEC("933.1")
    # RELIANCE's other two: the 2020 rights issue has no prices either side here (held, nulling nothing stored), and
    # the 2024 bonus is after its last stored price (waiting: no row yet)
    assert fs["zc-reliance-rights-2020-05-13"][2:4] == ("held", "no close on the previous session (2020-05-12)")
    assert "zc-reliance-bonus-2024-10-28" not in fs
    c = checks(tr)
    assert c["lists_read"]["passed"] and c["ratios_exact"]["passed"] and c["applied_checked"]["passed"]
    assert not c["not_applied"]["passed"]
    assert "zc-reliance-rights-2020-05-13" in c["not_applied"]["detail"]
    assert "waiting for the ex-date's prices: zc-reliance-bonus-2024-10-28" in c["not_applied"]["detail"]
    evs = events()
    assert evs["zc-reliance-demerger-2023-07-20"][:5] == ("effective", "Demerger", D(2023, 7, 20), True, "nse")
    assert evs["zc-reliance-dividend-2019-08-02"][6] == " Annual General Meeting/Dividend - Rs 6.50 Per Share"  # verbatim
    assert evs["zc-reliance-dividend-2019-08-02"][1] == "Annual General Meeting/Dividend - Rs 6.50 Per Share"   # title
    assert evs["zc-reliance-dividend-2019-08-02"][8] == DEC("6.50")
    assert len([s for s in evs if s.startswith("zc-reliance-")]) == 11
    # a second identical run writes nothing
    before = (factors(), events())
    code, tr2 = go(ex, **ALL)
    assert code == 0 and tr2[0] == "success" and tr2[1] == 0
    assert (factors(), events()) == before


@pytest.mark.db
def test_a_bonus_of_1_1_is_a_half_and_two_actions_of_one_kind_on_a_day_get_their_own_slugs(s3):
    eid = company("ZBONUS", "ZBONUS")
    ex = Exchanges()
    ex.nse = {"ZBONUS": [nse_row("ZBONUS", "02-Jun-2025", "Bonus 1:1", "10"),
                         nse_row("ZBONUS", "02-Aug-2023", "Dividend - Rs 24 Per Share"),
                         nse_row("ZBONUS", "02-Aug-2023", "Special Dividend - Rs 6 Per Share"),
                         nse_row("ZBONUS", "02-Aug-2023", "Special  Dividend - Rs 6 Per Share")]}   # listed twice
    prices(eid, {D(2025, 5, 30): (1000, 1000, 990), D(2025, 6, 2): (505, 510, 1000)})
    code, tr = go(ex, **ALL)
    assert code == 0 and tr[0] == "success", tr
    f = factors()["zc-zbonus-bonus-2025-06-02"]
    assert (f[0], f[1], f[2], f[3]) == (DEC("0.5"), "ratio", "applied", None)
    evs = events()
    assert {"zc-zbonus-dividend-2023-08-02", "zc-zbonus-dividend-2023-08-02-2"} <= set(evs)
    assert sorted(v[8] for k, v in evs.items() if "dividend" in k) == [DEC(6), DEC(24)]
    assert adjusted(eid) == {D(2025, 5, 30): (DEC(1000), DEC("500.000000000000")), D(2025, 6, 2): (DEC(510), DEC(510))}
    _, tr2 = go(ex, **ALL)
    assert tr2[1] == 0


@pytest.mark.db
def test_a_held_factor_nulls_earlier_adjusted_closes_until_a_review_releases_it(s3, tmp_path, monkeypatch):
    eid = company("ZHELD", "ZHELD")
    ex = Exchanges()
    ex.nse = {"ZHELD": [nse_row("ZHELD", "02-Jun-2025", "Bonus 1:1")]}
    # the price doesn't halve on the ex-date: the record's 1:1 bonus doesn't fit, so it's held
    prices(eid, {D(2025, 5, 29): (990, 1000, 980), D(2025, 5, 30): (1000, 1000, 1000), D(2025, 6, 2): (995, 990, 1000)})
    code, tr = go(ex, **ALL)
    assert code == 0 and tr[0] == "success"
    factor, method, status, reason, evidence = factors()["zc-zheld-bonus-2025-06-02"]
    assert (factor, method, status) == (DEC("0.5"), "ratio", "held")
    assert reason.startswith("the plausibility check failed: the adjusted move +68.31% is outside")
    assert evidence["passed"] is False and evidence["c0"] == "1000" and evidence["c1"] == "990"
    assert adjusted(eid) == {D(2025, 5, 29): (DEC(1000), None), D(2025, 5, 30): (DEC(1000), None),
                             D(2025, 6, 2): (DEC(990), DEC(990))}
    c = checks(tr)
    assert c["applied_checked"]["passed"] and not c["not_applied"]["passed"]
    assert "held (1): zc-zheld-bonus-2025-06-02: the plausibility check failed" in c["not_applied"]["detail"]
    # a review releases it at 1 (say the exchange's record was wrong): applied, method reviewed
    monkeypatch.setattr(ac, "REVIEW_FILE", write(tmp_path, """
releases:
  - event: zc-zheld-bonus-2025-06-02
    factor: 1
    evidence: the price didn't move on the ex-date
    reviewer: test
    reviewed_on: 2026-10-29
"""))
    code, tr = go(ex, **ALL)
    assert code == 0 and tr[0] == "success" and tr[1] == 1
    factor, method, status, reason, evidence = factors()["zc-zheld-bonus-2025-06-02"]
    assert (factor, method, status, reason) == (DEC(1), "reviewed", "applied", "released by review (test, 2026-10-29)")
    assert evidence["review"]["evidence"] == "the price didn't move on the ex-date" and evidence["passed"] is True
    assert adjusted(eid)[D(2025, 5, 29)] == (DEC(1000), DEC(1000))
    c = checks(tr)
    assert c["applied_checked"]["passed"] and c["ratios_exact"]["passed"] and c["reviews_matched"]["passed"]


@pytest.mark.db
def test_an_applied_factor_without_a_passed_check_or_a_review_fails_the_run(s3, monkeypatch):
    eid = company("ZFORGED", "ZFORGED")
    ex = Exchanges()
    ex.nse = {"ZFORGED": [nse_row("ZFORGED", "02-Jun-2025", "Bonus 1:1")]}
    prices(eid, {D(2025, 5, 30): (1000, 1000, 1000), D(2025, 6, 2): (995, 990, 1000)})
    go(ex, **ALL)
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""update equity.adjustment_factor set status = 'applied', price_factor = 0.4, reason = null
                      where event_id = (select id from core.event where slug = 'zc-zforged-bonus-2025-06-02')""")
    # the run's own recomputation would put it right, so skip it: the checks read what is stored
    monkeypatch.setattr(EquityCorporateActions, "_factors", lambda self, tx, f, ids: (0, 0))
    _, tr = go(ex, **ALL)
    assert tr[0] == "checks_failed"
    c = checks(tr)
    assert not c["applied_checked"]["passed"] and "zc-zforged-bonus-2025-06-02 (ratio)" in c["applied_checked"]["detail"]
    assert not c["ratios_exact"]["passed"] and "stored 0.4, ratio 0.5" in c["ratios_exact"]["detail"]


@pytest.mark.db
def test_a_demerger_without_a_pre_open_drop_and_one_without_prices_are_held(s3):
    a, b = company("ZDEMA", "ZDEMA"), company("ZDEMB", "ZDEMB")
    ex = Exchanges()
    ex.nse = {"ZDEMA": [nse_row("ZDEMA", "02-Jun-2025", "Demerger")],
              "ZDEMB": [nse_row("ZDEMB", "02-Jun-2025", "Demerger")]}
    prices(a, {D(2025, 5, 30): (100, 100, 100), D(2025, 6, 2): (101, 99, 100)})       # opened above the close
    prices(b, {D(2025, 5, 29): (100, 100, 100), D(2025, 6, 3): (80, 81, 100)})        # no row on 30 May or 2 Jun
    go(ex, **ALL)
    fs = factors()
    assert fs["zc-zdema-demerger-2025-06-02"][2:4] == (
        "held", ("the ex-date's open (101) isn't below the previous close (100): no sign of the demerger in the "
                 "pre-open price"))
    assert fs["zc-zdemb-demerger-2025-06-02"][1:4] == ("special_preopen", "held",
                                                       "no pre-open price in the ex-date's bhavcopy")
    assert fs["zc-zdemb-demerger-2025-06-02"][0] is None


@pytest.mark.db
def test_a_rights_issue_is_adjusted_by_its_terp_and_one_priced_above_the_market_is_held(s3):
    a, b = company("ZRIGHTS", "ZRIGHTS"), company("ZRICH", "ZRICH")
    ex = Exchanges()
    ex.nse = {"ZRIGHTS": [nse_row("ZRIGHTS", "02-Jun-2025", " Rights 1:15 @ Premium Rs 1247")],
              "ZRICH": [nse_row("ZRICH", "02-Jun-2025", "Rights 1:4 @ Rs 300")]}
    prices(a, {D(2025, 5, 30): (1500, 1500, 1490), D(2025, 6, 2): (1485, 1480, 1500)})
    prices(b, {D(2025, 5, 30): (250, 250, 250), D(2025, 6, 2): (250, 251, 250)})
    go(ex, **ALL)
    fs = factors()
    f = fs["zc-zrights-rights-2025-06-02"]
    assert (f[0], f[1], f[2]) == (DEC("0.989875000000"), "terp", "applied")
    assert f[4]["terp"] == "1484.8125" and f[4]["rights"] == {"new": "1", "held": "15", "price": "1257"}
    assert fs["zc-zrich-rights-2025-06-02"][2:4] == (
        "held", "the rights price (300) is at or above the previous close (250): no price effect to adjust for")


@pytest.mark.db
def test_a_scheme_of_arrangement_is_held_for_a_reviewed_method_and_an_unknown_entry_is_named(s3):
    eid = company("ZSCHEME", "ZSCHEME")
    ex = Exchanges()
    ex.nse = {"ZSCHEME": [nse_row("ZSCHEME", "02-Jun-2025", "Scheme of Arrangement"),
                          nse_row("ZSCHEME", "03-Jun-2025", "Something New")]}
    prices(eid, {D(2025, 5, 30): (100, 100, 100), D(2025, 6, 2): (90, 91, 100), D(2025, 6, 3): (91, 91, 91)})
    code, tr = go(ex, **ALL)
    assert code == 0 and tr[0] == "success"
    f = factors()["zc-zscheme-other-2025-06-02"]
    assert f[2] == "held" and "a restructuring none of the methods covers" in f[3]
    assert "zc-zscheme-other-2025-06-03" not in factors()
    detail = checks(tr)["not_applied"]["detail"]
    assert "unrecognised entries: zc-zscheme 2025-06-03 (Something New)" in detail


@pytest.mark.db
def test_announced_actions_become_effective_on_their_ex_date_and_withdrawn_when_dropped(s3):
    company("ZSOON", "ZSOON")
    ex = Exchanges()
    ex.nse = {"ZSOON": [nse_row("ZSOON", "16-Nov-2026", "Bonus 1:1"),
                        nse_row("ZSOON", "20-Nov-2026", "Dividend - Rs 5 Per Share")]}
    code, tr = go(ex, workflow="weekly")
    assert code == 0 and tr[0] == "success"
    evs = events()
    assert evs["zc-zsoon-bonus-2026-11-16"][0] == "announced" and evs["zc-zsoon-dividend-2026-11-20"][0] == "announced"
    assert factors() == {}                           # no factor before the ex-date's prices exist
    # three weeks on: the bonus is listed and its ex-date has passed; the dividend was dropped by the exchange
    later = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)
    ex.nse["ZSOON"] = [nse_row("ZSOON", "16-Nov-2026", "Bonus 1:1")]
    code, tr = go(ex, now=later, workflow="weekly")
    assert code == 0 and tr[0] == "success"
    evs = events()
    assert evs["zc-zsoon-bonus-2026-11-16"][0] == "effective" and evs["zc-zsoon-dividend-2026-11-20"][0] == "withdrawn"
    assert "waiting for the ex-date's prices: zc-zsoon-bonus-2026-11-16" in checks(tr)["not_applied"]["detail"]
    # a weekly window: 400 days back to 90 ahead
    asked = [a for a in ex.asked if "corporateActions" in a][-1]
    assert "from_date=14-10-2025" in asked and "to_date=16-02-2027" in asked


@pytest.mark.db
def test_bse_is_asked_only_for_a_company_nse_returns_nothing_for_in_windows_under_a_year(s3):
    meesho = company("MEESHO", "MEESHO", "544632")
    ex = Exchanges()
    ex.nse = {"MEESHO": []}
    ex.bse = {"544632": [{"scrip_code": "544632", "short_name": "MEESHO", "Ex_date": "12 Mar 2026",
                          "Purpose": "Interim Dividend - Rs. - 1.0000", "RD_Date": "12 Mar 2026",
                          "long_name": "MEESHO test", "exdate": "20260312"}]}
    code, tr = go(ex, since=D(2025, 1, 1), until=D(2026, 10, 27))
    assert code == 0 and tr[0] == "success", tr
    ev = events()["zc-meesho-dividend-2026-03-12"]
    assert (ev[4], ev[7], ev[8]) == ("bse", "BSE", DEC(1))
    bse = [a for a in ex.asked if "bseindia" in a]
    assert len(bse) == 2 and "Fdate=20250101" in bse[0] and "TDate=20251227" in bse[0] and "Fdate=20251228" in bse[1]
    # a company NSE serves isn't sent to BSE in a window where NSE lists nothing
    other = company("ZNSE", "ZNSE", "500001")
    ex.nse["ZNSE"] = [nse_row("ZNSE", "02-Jun-2025", "Dividend - Rs 2 Per Share")]
    go(ex, since=D(2025, 1, 1), until=D(2025, 12, 31))
    ex.asked.clear()
    go(ex, since=D(2026, 1, 1), until=D(2026, 10, 27))
    assert not any("scripcode=500001" in a for a in ex.asked)
    assert meesho and other


@pytest.mark.db
def test_a_reply_that_is_not_json_fails_the_run_and_nothing_is_kept(s3):
    company("ZPAGE", "ZPAGE")
    ok = company("ZFINE", "ZFINE")
    ex = Exchanges()
    ex.nse = {"ZPAGE": "<html>Access Denied</html>", "ZFINE": [nse_row("ZFINE", "02-Jun-2025", "Dividend - Rs 2")]}
    _, tr = go(ex, **ALL)
    assert tr[0] == "checks_failed"
    c = checks(tr)
    assert not c["lists_read"]["passed"] and "zc-zpage (NSE): the reply isn't a JSON list of actions" in \
        c["lists_read"]["detail"]
    assert events() == {} and ok


@pytest.mark.db
def test_unexplained_jumps_are_named_and_a_reviewed_real_move_is_not(s3, tmp_path, monkeypatch):
    eid = company("ZJUMP", "ZJUMP")
    ex = Exchanges()
    ex.nse = {"ZJUMP": []}
    prices(eid, {D(2020, 3, 5): (100, 100, 100), D(2020, 3, 6): (70, 70, 100), D(2020, 3, 9): (72, 71, 70),
                 D(2020, 3, 10): (75, 75, 71)})
    code, tr = go(ex, **ALL)
    c = checks(tr)
    assert code == 0 and not c["unexplained_jumps"]["passed"]
    assert c["unexplained_jumps"]["detail"] == "no applied factor and no review for: zc-zjump 2020-03-06 (-35.67%: 100 to 70)"
    monkeypatch.setattr(ac, "REVIEW_FILE", write(tmp_path, """
jumps:
  - company: zc-zjump
    date: 2020-03-06
    verdict: real move
    note: a made-up crash
    reviewer: test
    reviewed_on: 2026-10-29
"""))
    _, tr = go(ex, **ALL)
    c = checks(tr)
    assert c["unexplained_jumps"]["passed"] and "1 reviewed as real moves" in c["unexplained_jumps"]["detail"]


@pytest.mark.db
def test_the_guard_holds_a_scheduled_first_run_but_not_a_refresh(s3, monkeypatch):
    with psycopg.connect(JOB, autocommit=True) as c:
        t = EquityCorporateActions()
        t.rules = {"weekly": "every 6 days"}
        held = t.due(c, "weekly", NOW)
        assert not held.due and "waiting for its first recorded run" in held.reason
        assert t.due(c, "refresh", NOW).due
    asked = []
    monkeypatch.setattr(eca, "equity_due", lambda db, key, wf, rule, requires: asked.append(requires) or rule)
    with psycopg.connect(JOB, autocommit=True) as c:
        t.due(c, "weekly", NOW)
    assert asked == ["equity.corporate_action"]
    from ft.equity import guard
    with psycopg.connect(JOB, autocommit=True) as c:
        missing = guard.equity_due(c, "x", "weekly", t.due(c, "refresh", NOW), requires="equity.nope")
    assert not missing.due and "migration isn't pushed" in missing.reason
