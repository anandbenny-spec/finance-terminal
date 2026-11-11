"""Segment line-up changes (job DD-01, #409; Plan 05 §1.2, D6, D8): the reader and the rule, then the task through the runner
on the test database, and api.segment_changes as a visitor sees it.

The saved results filings are in data/fixtures/equity/xbrl (the private fixtures repo; the tests that use them skip without it,
and CI checks it out). The synthetic tests build line-ups and small instances and always run."""
from __future__ import annotations

import functools
import gzip
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import segments as sg
from ft.equity import xbrl
from ft.jobs import config
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_segment_changes import EquitySegmentChanges

D = date
XBRL = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "xbrl"
needs_fixtures = pytest.mark.skipif(not XBRL.exists(), reason="the saved files aren't checked out (data/fixtures)")


# ---------------------------------------------------------------------------------------------- names and helpers
def test_spelling_is_not_a_new_segment_but_a_different_name_is():
    k = sg.key
    assert k("Investment and others") == k("Investments and Others")                  # Bajaj Finserv, 2019 to 2025
    assert k("Financial Services (1)") == k("Financial services")                     # Infosys' footnote, Sep 2024
    assert k("(a) Commercial Vehicle") == k("Commercial Vehicle") == k("b) Commercial vehicles")
    assert k("Beauty & Personal Care") == k("Beauty and personal care")
    assert k("Chemicals*") == k("Chemicals *") and k("Others #") == k("Others")
    assert k("Quick commerce business") != k("Quick commerce")
    assert k("Business") != k("Bus") and k("Glass") == k("glass")                       # "ss" isn't a plural


def test_reconciling_rows_are_not_segments():
    for d in ("Less: Inter segment revenue", "Less : Exceptional Items", "Add: Exceptional items (net of tax)",
              "(Add)/Less: Inter-segment margins on capital jobs", "Add/(Less): Exceptional items", "Unallocated",
              "Unallocated (before exceptional items)", "(c) Corporate/Unallocable"):
        assert sg.is_reconciling(d), d
    for d in ("Others", "Oil and Gas", "Lessons and learning", "Additives", "Retail Banking", "All other segments"):
        assert not sg.is_reconciling(d), d


def test_a_typo_as_filed_is_the_same_segment_only_on_long_names():
    assert sg.same_but_typo(sg.key("Beauty & Personal care"), sg.key("Beaty & Personal care"))      # HUL, Dec 2022
    assert sg.same_but_typo(sg.key("Other Banking operations"), sg.key("Other Banking oprations"))    # SBI, Sep 2023
    assert not sg.same_but_typo(sg.key("Foods"), sg.key("Food"))         # too short to call a typo (and a plural anyway)
    assert not sg.same_but_typo(sg.key("Energy Projects"), sg.key("Energy Products and Services"))


def test_quarter_ends_before():
    assert sg.quarter_end_before(D(2020, 12, 31)) == D(2020, 9, 30)
    assert sg.quarter_end_before(D(2020, 12, 31), 4) == D(2019, 12, 31)
    assert sg.quarter_end_before(D(2026, 3, 31), 1) == D(2025, 12, 31)
    assert sg.quarter_end_before(D(2026, 6, 30), 2) == D(2025, 12, 31)


# ---------------------------------------------------------------------------------------------- the rule, synthetic
QS = [D(2025, 9, 30), D(2025, 12, 31), D(2026, 3, 31), D(2026, 6, 30), D(2026, 9, 30)]


def lu(pe: date, segs: dict[str, int | None] | None = None, single: str | None = None) -> sg.Lineup:
    if single is not None:
        return sg.Lineup(pe, "single", [sg.Line(1, single, None)])
    return sg.Lineup(pe, "multi", [sg.Line(i, d, None if v is None else Decimal(v)) for i, (d, v) in enumerate(segs.items(), 1)])


def outcome(history: list[dict | str], latest: dict | str) -> sg.Outcome:
    """`history` oldest first (four quarters); a str is a single-segment quarter."""
    def mk(pe, x):
        return lu(pe, single=x) if isinstance(x, str) else lu(pe, x)
    prev = [mk(pe, x) for pe, x in zip(QS[:4], history, strict=True)]
    return sg.compare(mk(QS[4], latest), list(reversed(prev)))


BASE = {"Refining": 100, "Petrochemicals": 50, "Retail": 20}


def test_one_segment_gone_and_none_new_is_dropped_one_flag_each():
    o = outcome([BASE] * 4, {"Refining": 100, "Retail": 20})
    assert o.state == "changed" and [(c.kind, c.before, c.after) for c in o.changes] == [("dropped", ["Petrochemicals"], [])]
    o = outcome([BASE] * 4, {"Retail": 20})
    assert [(c.kind, c.before) for c in o.changes] == [("dropped", ["Refining"]), ("dropped", ["Petrochemicals"])]


def test_two_into_one_within_the_band_is_merged_outside_it_is_other():
    o = outcome([BASE] * 4, {"Oil to Chemicals": 140, "Retail": 20})
    assert [(c.kind, c.before, c.after) for c in o.changes] == [("merged", ["Refining", "Petrochemicals"], ["Oil to Chemicals"])]
    assert o.changes[0].revenue_ratio == Decimal(140) / Decimal(150)
    assert outcome([BASE] * 4, {"Oil to Chemicals": 113, "Retail": 20}).changes[0].kind == "merged"     # 0.753: inside ±25%
    assert outcome([BASE] * 4, {"Oil to Chemicals": 112, "Retail": 20}).changes[0].kind == "other"      # 0.747: outside
    assert outcome([BASE] * 4, {"Oil to Chemicals": 188, "Retail": 20}).changes[0].kind == "other"      # 1.253: outside


def test_one_for_one_is_renamed_and_a_split_is_other():
    o = outcome([BASE] * 4, {"Refining": 100, "Petrochem": 50, "Retail": 20})
    assert [(c.kind, c.before, c.after) for c in o.changes] == [("renamed", ["Petrochemicals"], ["Petrochem"])]
    o = outcome([BASE] * 4, {"Refining": 100, "Polymers": 30, "Polyester": 20, "Retail": 20})
    assert [(c.kind, c.before, c.after) for c in o.changes] == [("other", ["Petrochemicals"], ["Polymers", "Polyester"])]


def test_a_new_segment_alone_is_not_a_flag():
    assert outcome([BASE] * 4, {**BASE, "New Energy": 5}).state == "none_flagged"


def test_a_segment_not_in_all_four_earlier_quarters_is_never_absent():
    hist = [{"Refining": 100, "Retail": 20}] + [BASE] * 3            # Petrochemicals only from the second quarter
    assert outcome(hist, {"Refining": 100, "Retail": 20}).state == "none_flagged"


def test_a_row_filed_at_zero_every_quarter_never_flags_but_a_segment_at_zero_once_does():
    hist = [{**BASE, "Depreciation": 0}] * 4
    assert outcome(hist, BASE).state == "none_flagged"                                  # Grasim's zero rows
    hist = [{**BASE, "Commercial Vehicle": 200}] * 3 + [{**BASE, "Commercial Vehicle": 0}]  # Tata Motors, Sep 2025
    o = outcome(hist, BASE)
    assert [(c.kind, c.before) for c in o.changes] == [("dropped", ["Commercial Vehicle"])]
    assert outcome([BASE] * 4, {**BASE, "Hydrogen": 0}).state == "none_flagged"         # new at zero isn't new
    assert outcome([BASE] * 4, {"Refining": 100, "Petrochemicals": 0, "Retail": 20}).state == "none_flagged"   # filed at 0


def test_a_typo_in_the_latest_is_not_a_rename():
    assert outcome([BASE] * 4, {"Refining": 100, "Petrochemicalz": 50, "Retail": 20}).state == "none_flagged"


def test_reporting_one_segment_or_several():
    assert outcome(["Cars"] * 4, "Cars").state == "single_segment"
    o = outcome([BASE] * 4, "Energy")
    assert [(c.kind, c.before, c.after) for c in o.changes] == [("multi_to_single", ["Refining", "Petrochemicals", "Retail"], [])]
    o = outcome(["Energy"] * 4, BASE)
    assert [(c.kind, c.before, c.after) for c in o.changes] == [("single_to_multi", [], ["Refining", "Petrochemicals", "Retail"])]
    # the quarter after a switch to several segments compares nothing new (JioFin, Sep 2026)
    assert outcome(["Energy"] * 3 + [BASE], BASE).state == "none_flagged"


def test_a_missing_or_held_earlier_quarter_means_not_compared():
    prev = [lu(QS[3], BASE), None, lu(QS[1], BASE), lu(QS[0], BASE)]
    o = sg.compare(lu(QS[4], {"Retail": 20}), prev)
    assert (o.state, o.changes, o.reason) == ("not_compared", [], "3 of the 4 earlier quarters read")
    assert sg.compare(lu(QS[4], single="Cars"), prev).state == "single_segment"
    with pytest.raises(ValueError):
        sg.compare(lu(QS[4], BASE), [lu(QS[i], BASE) for i in range(4)])               # oldest first: refused


# ---------------------------------------------------------------------------------------------- the reader, synthetic
NS = ('xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
      'xmlns:c="http://example.org/c"')


def instance(segs: list[tuple[str, str]], *, flag="Multi segment", basis="Consolidated", unit="INR", q=("2025-07-01", "2025-09-30"),
             ytd=("2025-04-01", "2025-09-30"), single=None) -> bytes:
    """A small results instance: the header in OneD, and per segment a One (quarter) and a Four (half-year) member."""
    def ctx(cid, start, end, member=None):
        dims = (f'<xbrli:segment><xbrldi:explicitMember dimension="c:ReportableSegmentsAxis">c:{member}</xbrldi:explicitMember>'
                '</xbrli:segment>') if member else ""
        return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="s">x</xbrli:identifier>{dims}</xbrli:entity>'
                f'<xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>')
    ctxs = [ctx("OneD", *q)]
    facts = [f'<c:NatureOfReportStandaloneConsolidated contextRef="OneD">{basis}</c:NatureOfReportStandaloneConsolidated>',
             f'<c:DateOfStartOfReportingPeriod contextRef="OneD">{q[0]}</c:DateOfStartOfReportingPeriod>',
             f'<c:DateOfEndOfReportingPeriod contextRef="OneD">{q[1]}</c:DateOfEndOfReportingPeriod>',
             f'<c:DateOfStartOfFinancialYear contextRef="OneD">{ytd[0]}</c:DateOfStartOfFinancialYear>']
    if flag:
        facts.append(f'<c:IsCompanyReportingMultisegmentOrSingleSegment contextRef="OneD">{flag}</c:IsCompanyReportingMultisegmentOrSingleSegment>')
    if single:
        facts.append(f'<c:DescriptionOfSingleSegment contextRef="OneD">{single}</c:DescriptionOfSingleSegment>')
    for i, (desc, rev) in enumerate(segs, 1):
        for col, (start, end) in (("One", q), ("Four", ytd)):
            m = f"{col}ReportableSegmentRevenue{i:02d}Member"
            ctxs.append(ctx(f"{col}Seg{i}D", start, end, m))
            facts += [f'<c:DescriptionOfReportableSegment contextRef="{col}Seg{i}D">{desc}</c:DescriptionOfReportableSegment>',
                      f'<c:SegmentRevenue contextRef="{col}Seg{i}D" unitRef="{unit}" decimals="-5">{rev if col == "One" else int(rev) * 2}</c:SegmentRevenue>']
    return (f'<?xml version="1.0"?><xbrli:xbrl {NS}>' + "".join(ctxs) + f'<xbrli:unit id="{unit}"><xbrli:measure>iso4217:{unit}</xbrli:measure></xbrli:unit>'
            + "".join(facts) + "</xbrli:xbrl>").encode()


FILING = sg.Filing("consolidated", D(2025, 9, 30), D(2025, 4, 1))


def test_the_reader_takes_the_quarter_column_and_leaves_reconciling_rows_out():
    body = instance([("Automotive", "700"), ("Farm Equipment", "200"), ("Less: Inter segment revenue", "-50"), ("Unallocated", "0")])
    got = sg.read_lineup(body, FILING)
    assert (got.reporting, got.column) == ("multi", "One")
    assert [(ln.line_no, ln.description, ln.revenue) for ln in got.lines] == [(1, "Automotive", Decimal(700)), (2, "Farm Equipment", Decimal(200))]


def test_a_pre_2025_file_whose_year_to_date_is_dated_like_the_quarter_is_re_dated_and_never_read_as_the_quarter():
    q = ("2025-07-01", "2025-09-30")
    body = instance([("Automotive", "700")], ytd=q).replace(b"<c:DateOfStartOfFinancialYear contextRef=\"OneD\">2025-07-01",
                                                           b"<c:DateOfStartOfFinancialYear contextRef=\"OneD\">2025-04-01")
    got = sg.read_lineup(body, FILING)
    assert [(ln.description, ln.revenue) for ln in got.lines] == [("Automotive", Decimal(700))]      # One, not Four's 1400


def test_a_single_segment_filer_is_one_line_with_its_description_and_no_revenue():
    got = sg.read_lineup(instance([], flag="Single segment", single="Passenger vehicles and parts"), FILING)
    assert (got.reporting, [(ln.description, ln.revenue) for ln in got.lines]) == ("single", [("Passenger vehicles and parts", None)])


def test_a_bank_without_the_flag_but_with_members_is_multi_segment():
    got = sg.read_lineup(instance([("Treasury", "10"), ("Retail Banking", "30")], flag=None), FILING)
    assert got.reporting == "multi" and len(got.lines) == 2


@pytest.mark.parametrize("kwargs, check", [
    ({"segs": [], "flag": "Multi segment"}, "no_segment_facts"),
    ({"segs": [], "flag": None}, "no_disclosure"),
    ({"segs": [("Automotive", "7")], "unit": "USD"}, "unit"),
    ({"segs": [("Automotive", "7")], "basis": "Standalone"}, "basis"),
    ({"segs": [("Automotive", "7"), ("AUTOMOTIVE", "8")]}, "duplicate"),
])
def test_a_filing_that_gives_no_line_up_says_why(kwargs, check):
    with pytest.raises(sg.SegmentError) as e:
        sg.read_lineup(instance(**kwargs), FILING)
    assert e.value.check == check


def test_segment_facts_dated_as_neither_the_quarter_nor_its_column_are_undated():
    body = instance([("Automotive", "7")]).replace(b'id="OneSeg1D"', b'id="SixSeg1D"').replace(b'contextRef="OneSeg1D"', b'contextRef="SixSeg1D"')
    body = body.replace(b"<xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context><xbrli:context id=\"FourSeg1D\"",
                        b"<xbrli:startDate>2024-07-01</xbrli:startDate><xbrli:endDate>2024-09-30</xbrli:endDate></xbrli:period></xbrli:context><xbrli:context id=\"FourSeg1D\"")
    with pytest.raises(sg.SegmentError) as e:
        sg.read_lineup(body, FILING)
    assert e.value.check == "undated"


# ---------------------------------------------------------------------------------------------- the saved filings
@functools.cache
def consolidated(sym: str) -> dict[date, tuple[str, bytes, date | None]]:
    """Each quarter's consolidated file of a company in the fixtures (the last by name when a quarter has two)."""
    out = {}
    for p in sorted((XBRL / sym).glob("*.xml.gz")):
        with gzip.open(p) as f:
            body = f.read()
        inst = xbrl.parse(body)
        pe = xbrl.to_date(inst.header.get("period_end"))
        if inst.basis == "consolidated" and pe is not None:
            out[pe] = (p.name[:-3], body, xbrl.to_date(inst.header.get("fy_start")))
    return out


def lineups(sym: str) -> dict[date, sg.Lineup | None]:
    out = {}
    for pe, (_, body, fs) in consolidated(sym).items():
        try:
            out[pe] = sg.read_lineup(body, sg.Filing("consolidated", pe, fs))
        except sg.SegmentError:
            out[pe] = None
    return out


def at(sym: str, pe: date) -> sg.Outcome:
    ls = lineups(sym)
    return sg.compare(ls[pe], [ls.get(sg.quarter_end_before(pe, i)) for i in range(1, 5)])


@needs_fixtures
def test_reliance_dec_2020_is_refining_and_petrochemicals_merged_into_o2c():
    o = at("RELIANCE", D(2020, 12, 31))
    assert [(c.kind, c.before, c.after) for c in o.changes] == [("merged", ["Petrochemicals", "Refining"], ["Oil to Chemicals (O2C)"])]
    # O2C's ₹83,838 crore in the Dec 2020 quarter against Petrochemicals' ₹29,665 crore and Refining's ₹62,154 crore in Sep 2020
    assert o.changes[0].revenue_ratio == Decimal(838380000000) / Decimal(296650000000 + 621540000000)
    assert round(o.changes[0].revenue_ratio, 3) == Decimal("0.913")


@needs_fixtures
def test_reliance_sep_2020_reads_the_quarter_not_the_half_year():
    got = {ln.description: ln.revenue for ln in lineups("RELIANCE")[D(2020, 9, 30)].lines}
    assert got["Refining"] == Decimal(621540000000) and got["Petrochemicals"] == Decimal(296650000000)    # H1: 1,087,960 / 548,570


@needs_fixtures
def test_maruti_reports_one_segment_and_is_never_flagged():
    ls = lineups("MARUTI")
    assert ls and all(x is None or x.reporting == "single" for x in ls.values())
    for pe in ls:
        if ls[pe] is not None:
            assert not sg.compare(ls[pe], [ls.get(sg.quarter_end_before(pe, i)) for i in range(1, 5)]).changes


@needs_fixtures
def test_mm_stable_quarters_give_no_flag():
    ls = lineups("M&M")
    stable = [pe for pe in ls if pe >= D(2024, 6, 30)]
    assert len(stable) >= 6
    assert {at("M&M", pe).state for pe in stable} == {"none_flagged"}


# The rule over every quarter of the 26 saved companies (rule_version 1, fixed on 11 Nov 2026 before any production run; the
# handoff on #409 lists them). A change to the rule that moves this list is a new rule_version.
SWEEP = {
    ("COALINDIA", "2026-06-30", "single_to_multi"), ("ETERNAL", "2024-06-30", "renamed"), ("GRASIM", "2021-12-31", "renamed"),
    ("GRASIM", "2022-12-31", "renamed"), ("GRASIM", "2024-03-31", "other"), ("HDFCBANK", "2024-03-31", "renamed"),
    ("HINDUNILVR", "2021-06-30", "renamed"), ("HINDUNILVR", "2024-06-30", "other"), ("HINDUNILVR", "2024-12-31", "renamed"),
    ("HINDUNILVR", "2025-06-30", "renamed"), ("ICICIBANK", "2022-06-30", "dropped"), ("ITC", "2024-12-31", "dropped"),
    ("JIOFIN", "2026-06-30", "single_to_multi"), ("LT", "2021-06-30", "dropped"), ("LT", "2022-06-30", "other"),
    ("LT", "2026-06-30", "other"), ("M&M", "2023-06-30", "merged"), ("RELIANCE", "2020-06-30", "renamed"),
    ("RELIANCE", "2020-12-31", "merged"), ("RELIANCE", "2023-06-30", "dropped"), ("SBIN", "2020-12-31", "renamed"),
    ("TCS", "2023-09-30", "renamed"), ("TMPV", "2025-06-30", "dropped"), ("TMPV", "2025-12-31", "dropped"),
}


@needs_fixtures
def test_the_rule_over_every_saved_quarter_gives_the_recorded_flags():
    got, states = set(), {}
    for sym in sorted(p.name for p in XBRL.iterdir() if p.is_dir()):
        ls = lineups(sym)
        for pe, x in ls.items():
            if x is None:
                continue
            o = sg.compare(x, [ls.get(sg.quarter_end_before(pe, i)) for i in range(1, 5)])
            states[o.state] = states.get(o.state, 0) + 1
            got |= {(sym, pe.isoformat(), c.kind) for c in o.changes}
    assert got == SWEEP
    assert states["changed"] == 24 and states["none_flagged"] > 300 and states["single_segment"] > 100


# ---------------------------------------------------------------------------------------------- wiring
def test_the_task_is_wired_in_tasks_yaml():
    t = config.load()["equity.segment_changes"]
    assert t.rules == {"weekly": "every 6 days"} and t.run_after == ("equity.statements",)
    assert t.overdue_after.days == 10 and t.uses_nse is False
    assert registry()["equity.segment_changes"] is EquitySegmentChanges


def test_the_production_guard_holds_a_scheduled_first_run_and_a_missing_table():
    from ft.equity.guard import equity_due
    from ft.jobs.due import Due

    class Db:
        def __init__(self, present, ran):
            self.present, self.ran = present, ran

        def execute(self, sql, *a):
            outer = self

            class R:
                def fetchone(self_):
                    return (outer.present if "to_regclass" in sql else outer.ran,)
            return R()

    t = EquitySegmentChanges()
    assert t.requires == "equity.segment_change"
    assert not equity_due(Db(False, False), t.key, "weekly", Due(True, ""), requires=t.requires).due
    assert not equity_due(Db(True, False), t.key, "weekly", Due(True, ""), requires=t.requires).due
    assert equity_due(Db(True, False), t.key, "refresh", Due(True, ""), requires=t.requires).due


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5607, "equity-segment-changes-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.equity_segment_changes"
NOW = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)
RELIANCE_QS = [D(2019, 9, 30), D(2019, 12, 31), D(2020, 3, 31), D(2020, 6, 30), D(2020, 9, 30), D(2020, 12, 31)]


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'equity segment changes test', '{weekly}', '6 days', '10 days', false)
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
        cur = c.execute(sql, a or None)
        return cur.fetchall() if cur.description else []


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zs-%')"
        c.execute(f"delete from equity.segment_change where entity_id in {ids}")
        c.execute(f"delete from equity.segment_lineup where entity_id in {ids}")
        c.execute(f"delete from equity.filing where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zs-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))
        c.execute("delete from core.raw_file where source_url like 'https://example.test/zs/%'")


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def task() -> EquitySegmentChanges:
    class T(EquitySegmentChanges):
        key = KEY
    t = T()
    t.workflows = ("weekly", "refresh")
    return t


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"equity.segment_changes fetched {request.url}")


def go():
    http = HttpClient(transport=httpx.MockTransport(refuse), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "refresh", [task()], only=KEY, force=True, http=http, storage=storage, clock=lambda: NOW)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1""", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def seed(edit=None, standalone=True):
    """Reliance's six quarters to Dec 2020 and Maruti's latest five, as 2A's equity.filings leaves them (consolidated filings,
    and Reliance's Dec 2020 standalone one, which must never be read)."""
    go()                                            # a task run to hang the raw files on
    st = storage()
    plan = [("RELIANCE", "zs-reliance", "INE002A01018", [(pe, "consolidated") for pe in RELIANCE_QS])]
    if standalone:
        plan[0][3].append((D(2020, 12, 31), "standalone"))
    maruti = sorted(consolidated("MARUTI"))[-5:]
    plan.append(("MARUTI", "zs-maruti", "INE585B01010", [(pe, "consolidated") for pe in maruti]))
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for sym, slug, isin, files in plan:
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', true) returning id""", (slug, f"{sym} test")).fetchone()[0]
            c.execute("insert into equity.company (entity_id, isin, nse_symbol, fy_end_month) values (%s, %s, %s, 3)", (eid, isin, sym))
            for pe, basis in files:
                if basis == "consolidated":
                    name, body, fs = consolidated(sym)[pe]
                else:
                    name, body, fs = standalone_file(sym, pe)
                if edit is not None:
                    body = edit(sym, pe, basis, body)
                put = st.put(body, source="nse", media_type="application/xml", ext="xml")
                rf = c.execute("""insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
                                  values (%s, (select id from core.source where key = 'nse'), %s, %s, %s, 'application/xml', now(),
                                          (select max(id) from ops.task_run)) returning id""",
                               (put.object_key, f"https://example.test/zs/{name}", put.sha256, put.bytes)).fetchone()[0]
                ev = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
                                  values ('results_filing', %s, %s, 'results', %s, %s, 'filed', (select id from core.source where key = 'nse'), true)
                                  on conflict (event_type, slug) do update set updated_at = now() returning id""",
                               (eid, f"{slug}-results-{pe.isoformat()}", pe, datetime(pe.year, pe.month, pe.day, tzinfo=UTC))).fetchone()[0]
                doc = c.execute("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, raw_file_id)
                                   values ('results_xbrl', %s, %s, 'xbrl', (select id from core.source where key = 'nse'), %s, %s, %s)
                                   returning id""", (eid, ev, name, f"https://example.test/zs/{name}", rf)).fetchone()[0]
                c.execute("""insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end, filed_at,
                                                        source_url, exchange, parse_status)
                             values (%s, %s, %s, %s, 'ind_as', %s, %s, null, %s, %s, 'NSE', 'pending')""",
                          (doc, eid, ev, basis, pe, fs, datetime(pe.year, pe.month, pe.day, tzinfo=UTC),
                           f"https://example.test/zs/{name}"))


def standalone_file(sym: str, pe: date) -> tuple[str, bytes, date | None]:
    for p in sorted((XBRL / sym).glob("*.xml.gz")):
        with gzip.open(p) as f:
            body = f.read()
        inst = xbrl.parse(body)
        if inst.basis == "standalone" and xbrl.to_date(inst.header.get("period_end")) == pe:
            return p.name[:-3], body, xbrl.to_date(inst.header.get("fy_start"))
    raise LookupError(f"{sym} has no standalone file for {pe}")


def view(slug: str) -> list[tuple]:
    """api.segment_changes as a visitor sees it, with the field shown inside a rolled-back transaction."""
    with psycopg.connect(ADMIN) as c:
        c.execute("update core.field_policy set status = 'shown' where field_key = 'equity.segment_changes'")
        c.execute("set local role anon")
        rows = c.execute("""select state, period_end, kind, segments_before, segments_after, latest_filing_url, previous_filing_url,
                                   state_reason from api.segment_changes where company_slug = %s order by change_no nulls first""",
                         (slug,)).fetchall()
        c.rollback()
    return rows


@pytest.mark.db
@needs_fixtures
def test_a_refresh_stores_six_quarters_and_reliances_merged_flag(s3):
    seed()
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr[3]
    got = q("""select l.period_end, count(*), bool_and(l.basis = 'consolidated') from equity.segment_lineup l
                 join core.entity e on e.id = l.entity_id where e.slug = 'zs-reliance' group by 1 order by 1""")
    assert [r[0] for r in got] == RELIANCE_QS and all(r[2] for r in got)
    flags = q("""select c.period_end, c.kind, c.segments_before, c.segments_after, round(c.revenue_ratio, 3), c.previous_period_end, c.rule_version
                   from equity.segment_change c join core.entity e on e.id = c.entity_id where e.slug = 'zs-reliance'""")
    assert flags == [(D(2020, 12, 31), "merged", ["Petrochemicals", "Refining"], ["Oil to Chemicals (O2C)"], Decimal("0.913"),
                      D(2020, 9, 30), 1)]
    assert q("""select distinct reporting from equity.segment_lineup l join core.entity e on e.id = l.entity_id
                 where e.slug = 'zs-maruti'""") == [("single",)]
    c = checks(tr)
    assert c["states"]["passed"] and c["parsed"]["passed"]
    assert "zs-reliance 2020-12-31 (merged)" in c["flags"]["detail"] and "Refining" not in c["flags"]["detail"]
    assert c["filings_held"]["passed"] and c["no_segment_facts"]["passed"] and c["latest_unread"]["passed"]


@pytest.mark.db
@needs_fixtures
def test_a_second_run_writes_nothing(s3):
    seed()
    go()
    before = q("select count(*), sum(coalesce(revenue, 0)) from equity.segment_lineup where entity_id in (select id from core.entity where slug like 'zs-%%')")
    code, tr = go()
    assert code == 0 and tr[1] == 0
    assert q("select count(*), sum(coalesce(revenue, 0)) from equity.segment_lineup where entity_id in (select id from core.entity where slug like 'zs-%%')") == before


@pytest.mark.db
@needs_fixtures
def test_a_visitor_sees_the_flag_with_both_filings_and_the_single_segment_state(s3):
    seed()
    go()
    rows = view("zs-reliance")
    assert len(rows) == 1
    state, pe, kind, before, after, latest_url, previous_url, _ = rows[0]
    assert (state, pe, kind, before, after) == ("changed", D(2020, 12, 31), "merged", ["Petrochemicals", "Refining"], ["Oil to Chemicals (O2C)"])
    assert latest_url.endswith(consolidated("RELIANCE")[D(2020, 12, 31)][0])
    assert previous_url.endswith(consolidated("RELIANCE")[D(2020, 9, 30)][0])
    assert [r[0] for r in view("zs-maruti")] == ["single_segment"]
    with psycopg.connect(ADMIN) as c:                                                     # hidden: nothing at all
        c.execute("set local role anon")
        assert c.execute("select count(*) from api.segment_changes where company_slug like 'zs-%%'").fetchone()[0] == 0
        c.rollback()


@pytest.mark.db
@needs_fixtures
def test_a_quarter_with_no_segment_facts_is_named_and_never_filled_from_standalone(s3):
    def edit(sym, pe, basis, body):
        return body.replace(b"ReportableSegmentsAxis", b"SomeOtherAxis") if (sym, pe) == ("RELIANCE", D(2020, 9, 30)) else body
    seed(edit=edit)
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr[3]
    c = checks(tr)
    assert not c["no_segment_facts"]["passed"] and "zs-reliance 2020-09-30" in c["no_segment_facts"]["detail"]
    assert not c["not_compared"]["passed"] and "zs-reliance: 2020-09-30 (no_segment_facts)" in c["not_compared"]["detail"]
    assert c["states"]["passed"]
    assert q("select count(*) from equity.segment_change where entity_id = (select id from core.entity where slug = 'zs-reliance')") == [(0,)]
    assert q("""select count(*) from equity.segment_lineup l join core.entity e on e.id = l.entity_id
                 where e.slug = 'zs-reliance' and l.period_end = '2020-09-30'""") == [(0,)]
    assert [(r[0], r[1], r[7]) for r in view("zs-reliance")] == [("not_compared", D(2020, 12, 31), "3 of the 4 earlier quarters have a line-up")]


@pytest.mark.db
@needs_fixtures
def test_a_superseded_latest_announcement_is_not_read_and_its_old_rows_never_count(s3):
    seed()
    go()
    q("update core.event set status = 'superseded' where slug = 'zs-reliance-results-2020-12-31'")
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr[3]
    assert checks(tr)["states"]["passed"]
    assert [(r[0], r[1]) for r in view("zs-reliance")] == [("none_flagged", D(2020, 9, 30))]
