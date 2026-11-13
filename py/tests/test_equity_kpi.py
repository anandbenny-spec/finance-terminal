"""Unit tests for the KPI-table pilot's pure logic (DD-04, #512): the document rules and page cut (documents), the
post-filters, sign convention and tile check (filters), the per-page request and reply parsing (extraction), and the AI
wrapper's image source. Hand-written page texts and a fake client — no network, no PDF, no database."""
from __future__ import annotations

import base64
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from ft.ai.client import Source, load_prompt, load_schema
from ft.equity.kpi import documents as dc
from ft.equity.kpi import extraction as ex
from ft.equity.kpi import filters as fl

IST = ZoneInfo("Asia/Kolkata")
FEED = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "calls"
needs_feed = pytest.mark.skipif(not (FEED / "nse").exists(), reason="the saved files aren't checked out (data/fixtures)")


class Ann:
    def __init__(self, subject, file_name, disseminated, url="u", filing_key="k", exchange="NSE", category=None):
        self.subject, self.file_name, self.disseminated = subject, file_name, disseminated
        self.url, self.filing_key, self.exchange, self.category = url, filing_key, exchange, category


def results(day, company="Acme Limited", **kw):
    """NSE's results submission on `day` (the filing that fixes the results day)."""
    return Ann(f"{company} has submitted to the Exchange, the financial results for the period ended", "results.pdf",
               day, category="Outcome of Board Meeting", **kw)


# ----------------------------------------------------------------------------------- documents: window and periods
def test_period_end_indian_fiscal():
    assert dc.period_end("Q1 FY27").isoformat() == "2026-06-30"
    assert dc.period_end("Q2 FY27").isoformat() == "2026-09-30"
    assert dc.period_end("Q3 FY26").isoformat() == "2025-12-31"
    assert dc.period_end("Q4 FY25").isoformat() == "2025-03-31"
    with pytest.raises(ValueError):
        dc.period_end("not a quarter")


def test_window_and_previous_four():
    assert dc.quarters("infy")[0] == "Q3 FY25" and dc.quarters("infy")[-1] == "Q2 FY27"
    assert dc.quarters("tcs")[0] == "Q2 FY25"               # TCS starts a quarter earlier (the known case)
    assert dc.previous_four("infy", "Q2 FY27") == ["Q2 FY26", "Q3 FY26", "Q4 FY26", "Q1 FY27"]
    # at the window start there are fewer than four earlier quarters
    assert dc.previous_four("infy", "Q4 FY25") == ["Q3 FY25"]
    assert dc.previous_four("infy", "Q3 FY25") == []


# ----------------------------------------------------------------------------------- documents: choosing and cutting
def test_select_takes_the_results_day_press_release():
    rules = dc.load_rules()
    r = rules["tcs"]
    anns = [
        Ann("Press Release", "notice.pdf", datetime(2026, 10, 1, 21, 42, tzinfo=IST)),      # before results day (dev run 2)
        results(datetime(2026, 10, 9, 15, 30, tzinfo=IST)),                                # results day: 9 Oct
        Ann("Press Release Q2 FY27", "pr.pdf", datetime(2026, 10, 9, 15, 58, tzinfo=IST)),  # the pilot document
        Ann("Press Release (revised)", "pr2.pdf", datetime(2026, 10, 15, 11, 0, tzinfo=IST)),  # days later: not a candidate
        Ann("Press Release", "old.pdf", datetime(2025, 10, 9, 19, 0, tzinfo=IST)),           # a year early: out of window
    ]
    got = dc.select(anns, r, "Q2 FY27")
    assert got is not None and got.file_name == "pr.pdf"
    rd, cands = dc.candidates(anns, r, "Q2 FY27")
    assert rd.isoformat() == "2026-10-09" and [a.file_name for a in cands] == ["pr.pdf"]


def test_select_none_when_no_match():
    rules = dc.load_rules()
    anns = [results(datetime(2026, 10, 9, 15, 30, tzinfo=IST)),
            Ann("Investor Presentation", "ppt.pdf", datetime(2026, 10, 9, 19, 0, tzinfo=IST))]
    assert dc.select(anns, rules["tcs"], "Q2 FY27") is None
    # and with no results filing at all, there is no results day: the quarter is held, never guessed
    assert dc.candidates([Ann("Press Release", "pr.pdf", datetime(2026, 10, 9, 19, 0, tzinfo=IST))],
                         rules["tcs"], "Q2 FY27") == (None, [])


def test_results_day_skips_notices_clarifications_and_intimations():
    rules = dc.load_rules()
    r = rules["infy"]
    anns = [
        Ann("Infosys Limited has informed the Exchange regarding 'Infosys to Announce First Quarter Results on July 17'",
            "PR.pdf", datetime(2025, 7, 7, 15, 0, tzinfo=IST), category="Updates"),
        Ann("Infosys Limited has informed the Exchange regarding Clarification on financial results", "Clarification_letter.pdf",
            datetime(2025, 7, 8, 12, 3, tzinfo=IST), category="Clarification - Financial Results"),
        Ann("Infosys Limited has informed the Exchange about Board Meeting to be held on 17-Jul-2025 to consider and approve "
            "the financial results", "BM.pdf", datetime(2025, 7, 9, 10, 0, tzinfo=IST), category="Board Meeting Intimation"),
        Ann("Infosys Limited has informed the Exchange about Earnings call on financial results", "call.pdf",
            datetime(2025, 7, 10, 18, 0, tzinfo=IST), category="Analysts/Institutional Investor Meet/Con. Call Updates"),
        results(datetime(2025, 7, 17, 16, 5, tzinfo=IST), "Infosys Limited"),
    ]
    assert dc.results_day(anns, r, "Q1 FY26").isoformat() == "2025-07-17"
    assert [a.file_name for a in dc.candidates(anns, r, "Q1 FY26")[1]] == ["results.pdf"]


def test_results_day_is_close_to_the_quarter_end():
    """A missing Q4 results filing must not let the next quarter's results day (about 100 days on) stand in for it."""
    rules = dc.load_rules()
    anns = [results(datetime(2025, 7, 10, 15, 30, tzinfo=IST)),
            Ann("Press Release", "q1.pdf", datetime(2025, 7, 10, 16, 0, tzinfo=IST))]
    assert dc.results_day(anns, rules["tcs"], "Q4 FY25") is None            # 31 Mar + 101 days: past the 75-day limit
    assert dc.results_day(anns, rules["tcs"], "Q1 FY26").isoformat() == "2025-07-10"


def test_another_listed_companys_filing_is_never_taken():
    """Dev run 2 took HDFC Life's board outcome (filed through HDFC Bank) for HDFC Bank's Q4 FY26."""
    rules = dc.load_rules()
    r = rules["hdfcbank"]
    anns = [
        Ann("HDFC Bank Limited has informed the Exchange regarding Outcome of Board Meeting held on April 16, 2026 - "
            "audited financial results approved", "Outcome_of_BM-16APRIL2026_-_HDFC_Life.pdf",
            datetime(2026, 4, 16, 16, 39, tzinfo=IST), category="Outcome of Board Meeting"),
        results(datetime(2026, 4, 18, 15, 0, tzinfo=IST), "HDFC Bank Limited", url="hdfc"),
    ]
    rd, cands = dc.candidates(anns, r, "Q4 FY26")
    assert rd.isoformat() == "2026-04-18" and [a.url for a in cands] == ["hdfc"]
    # HDFC Bank names its own results outcome 'SEintimation…': the file name never rejects
    own = results(datetime(2025, 10, 18, 14, 8, tzinfo=IST), "HDFC Bank Limited", url="own")
    own.file_name = "HDFCBANK_18102025140719_SEintimationBMFinancialResults18102025.pdf"
    assert [a.url for a in dc.candidates([own], r, "Q2 FY26")[1]] == ["own"]


def test_preference_order_axis_press_release_before_results_statements():
    rules = dc.load_rules()
    r = rules["axisbank"]
    day = datetime(2025, 7, 17, 15, 39, tzinfo=IST)
    anns = [
        Ann("Axis Bank Limited has submitted to the Exchange, the financial results for the period ended June 30, 2025",
            "Signed_UFR_and_Audit_report_Q1FY26.pdf", day, category="Outcome of Board Meeting", url="ufr"),
        Ann("Axis Bank Limited has informed the Exchange about Investor Presentation", "InvestorPresentation.pdf",
            day.replace(hour=16, minute=30), category="Investor Presentation", url="ppt"),
        Ann("Axis Bank Limited has informed the Exchange about Press Release", "InvestorPressReleaseSigned.pdf",
            day.replace(hour=16, minute=45), category="Press Release", url="pr"),
        Ann("Axis Bank Limited has informed the Exchange about Earnings call", "SE_Earningscall.pdf",
            day.replace(hour=17), category="Analysts/Institutional Investor Meet/Con. Call Updates", url="call"),
    ]
    _rd, cands = dc.candidates(anns, r, "Q1 FY26")
    assert [a.url for a in cands] == ["pr", "ppt"]                          # the results statements aren't a candidate


def test_usable_needs_min_pages_and_the_cut():
    rules = dc.load_rules()
    icici = rules["icicibank"]
    assert dc.usable(icici, "Q2 FY26", ["cover letter", "earnings call details"]) == ([], "2 pages, fewer than 10")
    pages = ["cover"] + ["statement"] * 20 + ["ICICI Bank\nNews Release October 18, 2025", "table", "table",
                                              "Except for the historical information"] + ["annex"] * 5
    assert dc.usable(icici, "Q2 FY26", pages) == ([22, 23, 24], None)
    assert dc.usable(icici, "Q2 FY26", ["statement"] * 12)[1].startswith("the start heading was not found")
    # an image quarter isn't cut by text: every page is read (HDFC Bank)
    hdfc = rules["hdfcbank"]
    assert dc.usable(hdfc, "Q2 FY26", [""] * 24) == (list(range(1, 25)), None)
    assert dc.usable(hdfc, "Q2 FY26", [""]) == ([], "1 page, fewer than 5")


def test_hdfc_q3fy25_override_switches_to_own_filing():
    rules = dc.load_rules()
    base = rules["hdfcbank"]
    assert base.source == "bundle"
    over = base.for_quarter("Q3 FY25")
    assert over.source == "own_filing"
    # image route for the image-only quarters, text for the others
    assert base.reads_as_image("Q3 FY25") and base.reads_as_image("Q1 FY27")
    assert not base.reads_as_image("Q2 FY27")


def test_cut_pages_bundle_start_to_before_end():
    rules = dc.load_rules()
    r = rules["axisbank"]            # cut: start a press release, end before "Safe Harbor"
    pages = [
        "Cover letter to BSE and NSE",
        "Press Release\nAxis Bank announces Q2 FY27 results",
        "Net interest margin 4.1%\nCASA ratio 42%",
        "Safe Harbor\nThis document contains forward-looking statements",
        "Investor Presentation slide 1",
    ]
    nums, reason = dc.cut_pages(r, "Q2 FY27", pages)
    assert reason is None and nums == [2, 3]


def test_cut_pages_no_rule_reads_all():
    rules = dc.load_rules()
    nums, reason = dc.cut_pages(rules["tcs"], "Q2 FY27", ["p1", "p2", "p3"])
    assert reason is None and nums == [1, 2, 3]


def test_cut_pages_start_not_found_is_held():
    rules = dc.load_rules()
    nums, reason = dc.cut_pages(rules["infy"], "Q2 FY27", ["no heading here", "still none"])
    assert nums == [] and "start heading" in reason


# ----------------------------------------------------------------------------------- filters: the exclusions
def row(label, measure="percent", value=Decimal(1), member=None, unit="%", page=1):
    return fl.Row(label, member, measure, value, unit, page)


def test_about_page_sets_every_row_aside():
    page = "About Acme Ltd\nAcme operates in 55 countries with 100,000 people."
    assert fl.classify(row("Workforce", "people", Decimal(100000), unit="people"), page).reason == fl.ABOUT_PAGE


def test_quote_span_filtered():
    page = 'The CEO said, "our attrition stayed at 12.5% this quarter" during the call.'
    v = fl.classify(row("attrition stayed at 12.5%", value=Decimal("12.5")), page)
    assert v.reason == fl.QUOTE


def test_other_period_filtered():
    page = "Figures for the quarter."
    assert fl.classify(row("Net interest margin (year-ago)"), page).reason == fl.OTHER_PERIOD
    assert fl.classify(row("Revenue (nine months)"), page).reason == fl.OTHER_PERIOD


def test_statement_total_growth_filtered_but_average_kept():
    page = "A results page."
    assert fl.classify(row("Total deposits growth", "percent_change", Decimal(9)), page).reason == fl.STATEMENT_TOTAL
    assert fl.classify(row("Net profit growth", "percent_change", Decimal(12)), page).reason == fl.STATEMENT_TOTAL
    # average-balance and split figures are KPI metrics (the banks' sentence), never statement totals
    assert fl.classify(row("Average deposits growth", "percent_change", Decimal(8)), page).status == fl.KEPT
    assert fl.classify(row("CASA deposits growth", "percent_change", Decimal(7)), page).status == fl.KEPT


def test_threshold_without_a_value_filtered_but_approximate_kept():
    page = "A results page."
    assert fl.classify(row("Crossed 100 million active users", "count", None, unit="users"), page).reason == fl.THRESHOLD
    assert fl.classify(row("~38% revenue share", value=Decimal(38)), page).status == fl.KEPT   # a value: kept


# ----------------------------------------------------------------------------------- filters: sign and tiles
def test_signed_value_follows_direction_for_change_measures():
    assert fl.signed_value(row("PCR improved by 13 bps", "bps", Decimal(13))) == Decimal(13)
    assert fl.signed_value(row("Gross NPA down 20 bps", "bps", Decimal(20))) == Decimal(-20)
    # a down arrow already made it negative; a fall word keeps it negative (no double flip)
    assert fl.signed_value(row("Margin declined", "percent_change", Decimal(-50))) == Decimal(-50)
    # ambiguous (both or neither word): the model's value stands
    assert fl.signed_value(row("Attrition 12.5%", "percent", Decimal("12.5"))) == Decimal("12.5")
    assert fl.signed_value(row("Up then down", "bps", Decimal(5))) == Decimal(5)


def test_reconcile_tiles():
    rows = [
        fl.Row("BFSI share", None, "percent", Decimal("31.0"), "%", page=1),   # a tile page
        fl.Row("BFSI share", None, "percent", Decimal("31.0"), "%", page=2),   # a table page: confirms the tile
        fl.Row("Healthcare share", None, "percent", Decimal("9.0"), "%", page=1),  # tile, unconfirmed
    ]
    v = {i: fl.Verdict(fl.KEPT) for i in range(3)}
    out = fl.reconcile_tiles(rows, v, frozenset({1}))
    assert out[0].status == fl.KEPT                      # confirmed by page 2
    assert out[2].reason == fl.TILE_UNCONFIRMED          # no table match
    # with no tile pages marked, nothing changes
    assert fl.reconcile_tiles(rows, v, frozenset())[2].status == fl.KEPT


# ----------------------------------------------------------------------------------- extraction: parse and assemble
def test_parse_rows_reads_values_and_skips_malformed():
    answer = {"metrics": [
        {"label": "Attrition (LTM)", "member": "IT services", "measure": "percent", "value": 12.5, "unit": "%"},
        {"label": "", "measure": "percent", "value": 1, "unit": "%"},            # no label: skipped
        {"label": "Branches", "member": None, "measure": "count", "value": 6500, "unit": None},
        {"measure": "percent"},                                                   # no label: skipped
        {"label": "Phrase only", "member": None, "measure": "other", "value": None, "unit": None},
    ]}
    rows = ex.parse_rows(answer, page=3)
    assert [r.label for r in rows] == ["Attrition (LTM)", "Branches", "Phrase only"]
    assert rows[0].value == Decimal("12.5") and rows[0].member == "IT services" and rows[0].page == 3
    assert rows[1].value == Decimal(6500) and rows[1].unit is None
    assert rows[2].value is None


def test_extract_document_classifies_signs_and_tiles():
    pages = [ex.Page(1, text="Highlights tile"), ex.Page(2, text="Key metrics table")]
    rows_by_page = {
        1: [fl.Row("BFSI share", None, "percent", Decimal("31.0"), "%", 1),
            fl.Row("PCR improved by 13 bps", None, "bps", Decimal(13), "bps", 1)],
        2: [fl.Row("BFSI share", None, "percent", Decimal("31.0"), "%", 2),
            fl.Row("Total advances growth", None, "percent_change", Decimal(10), "%", 2)],
    }
    out = ex.extract_document(pages, rows_by_page, tile_pages=frozenset({1}))
    by = {(e.row.label, e.row.page): e for e in out}
    assert by[("BFSI share", 1)].filter_status == fl.KEPT            # tile confirmed by page 2
    assert by[("PCR improved by 13 bps", 1)].value == Decimal(13)  # sign kept positive
    assert by[("Total advances growth", 2)].filter_reason == fl.STATEMENT_TOTAL
    assert len(out) == 4


# ----------------------------------------------------------------------------------- extraction: the request, incl. image
def _prompt():
    return load_prompt("kpi_extraction")


def test_page_request_text_builds_a_document_block_with_context():
    schema = load_schema("kpi_extraction")
    prev = ex.Page(1, text="line a\nline b\n" + "\n".join(f"ctx{i}" for i in range(20)))
    page = ex.Page(2, text="CASA ratio 42%")
    req = ex.page_request(99, "HDFC Bank", "Q2 FY27", page, prev, _prompt(), schema)
    params = req.params()
    blocks = params["messages"][0]["content"]
    docs = [b for b in blocks if b["type"] == "document"]
    assert len(docs) == 1 and docs[0]["source"]["data"] == "CASA ratio 42%"
    assert "ctx19" in docs[0]["context"] and "ctx10" in docs[0]["context"]      # the last 10 non-empty lines
    assert "ctx9" not in docs[0]["context"]
    assert params["output_config"]["format"]["schema"]["properties"]["metrics"]


def test_page_request_image_builds_an_image_block():
    schema = load_schema("kpi_extraction")
    b64 = base64.b64encode(b"\x89PNG fake").decode()
    page = ex.Page(5, image_base64=b64, image_media_type="image/png")
    req = ex.page_request(99, "HDFC Bank", "Q3 FY25", page, None, _prompt(), schema)
    params = req.params()
    blocks = params["messages"][0]["content"]
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 1 and images[0]["source"]["data"] == b64 and images[0]["source"]["media_type"] == "image/png"


def test_image_source_needs_both_fields_and_rejects_citations():
    with pytest.raises(ValueError):
        Source(1, "", "t", image_base64="x")        # media type missing
    img = Source(1, "", "t", image_base64="Zm9v", image_media_type="image/png")
    assert img.is_image and img.text_sha256 == Source(1, "", "t2").text_sha256 or img.is_image  # hashes the image
    # the citations route can't send an image
    from ft.ai.client import Prompt as P
    from ft.ai.client import Request
    cites_req = Request("doc_summary", P("doc_summary", "v1", "x"), (img,), "do it")
    with pytest.raises(ValueError):
        cites_req.params()


def test_kpi_request_sends_through_the_wrapper():
    import ai_fakes
    schema = load_schema("kpi_extraction")
    answer = {"metrics": [{"label": "Branches", "member": None, "measure": "count", "value": 6500, "unit": None}]}
    client = ai_fakes.claude(ai_fakes.FakeClaude(answer=lambda body: answer))
    page = ex.Page(2, text="Branches 6500")
    req = ex.page_request(1, "SBI", "Q2 FY27", page, None, _prompt(), schema)
    reply = client.send(req)
    rows = ex.parse_rows(reply.json(), page=2)
    assert rows[0].label == "Branches" and rows[0].value == Decimal(6500)
    assert reply.cost_usd > 0


# ----------------------------------------------------------------------------------- the task: checks (pure) and the write path
class _Co:
    def __init__(self, entity_id, slug):
        self.entity_id, self.slug = entity_id, slug


def _read_plan(task_mod, entity_id, slug, label, values):
    plan = task_mod.DocPlan(entity_id, slug, label, dc.period_end(label), "press_release", "read",
                            source_url="https://x/pr.pdf", filing_key="seq1", exchange="NSE",
                            filed_at=datetime(2026, 10, 9, 19, 0, tzinfo=IST), pages_used=[2, 3], page_count=8,
                            image_page_count=0, sha256=bytes(32), bytes=1000)
    plan.values = [task_mod.Value(i + 1, r, v, st, rsn) for i, (r, v, st, rsn) in enumerate(values)]
    return plan


def test_task_checks_count_documents_and_rows():
    from ft.jobs.tasks import equity_kpi_tables as t
    h = t.Harvest()
    h.companies = [_Co(1, "tcs"), _Co(2, "infy")]
    # pretend every quarter resolved for both (so `documents` passes), with one read doc carrying a kept and a filtered row
    for co in h.companies:
        for label in dc.quarters(co.slug):
            h.plans.append(t.DocPlan(co.entity_id, co.slug, label, dc.period_end(label), "press_release", "held",
                                     held_reason="placeholder"))
    h.plans[0] = _read_plan(t, 1, "tcs", dc.quarters("tcs")[0], [
        (fl.Row("Attrition", None, "percent", Decimal("12.5"), "%", 2), Decimal("12.5"), fl.KEPT, None),
        (fl.Row("Total deposits growth", None, "percent_change", Decimal(9), "%", 3), Decimal(9), fl.FILTERED, fl.STATEMENT_TOTAL),
    ])
    h.cost = Decimal("0.1234")
    checks = {c.name: c for c in t.EquityKpiTables().checks(None, h, 1)}
    assert checks["documents"].passed                       # resolved == expected
    import json as _j
    extracted = _j.loads(checks["extracted"].detail)
    assert extracted["rows_kept"] == 1 and extracted["rows_filtered"] == {"statement_total": 1}
    assert extracted["ai_cost_usd"] == "0.1234"
    assert checks["budget"].passed and not checks["held"].blocking


def test_task_checks_ai_unavailable():
    from ft.jobs.tasks import equity_kpi_tables as t
    h = t.Harvest(unavailable="no_key")
    checks = {c.name: c for c in t.EquityKpiTables().checks(None, h, 1)}
    assert checks["ai_unavailable"].passed and checks["documents"].passed


@pytest.mark.db
def test_task_write_inserts_document_and_values_and_replaces():
    import psycopg
    from conftest import dsn

    from ft.jobs.tasks import equity_kpi_tables as t
    admin = dsn("postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zk-%'))")
        c.execute("delete from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zk-%')")
        c.execute("delete from core.entity where slug like 'zk-%'")
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company','zk-tcs','ZK TCS','IN','INR',true) returning id""").fetchone()[0]
    task = t.EquityKpiTables()
    read = _read_plan(t, eid, "zk-tcs", "Q2 FY27", [
        (fl.Row("Attrition (LTM)", "IT services", "percent", Decimal("12.5"), "%", 2), Decimal("12.5"), fl.KEPT, None),
        (fl.Row("Total deposits growth", None, "percent_change", Decimal(9), "%", 3), Decimal(9), fl.FILTERED, fl.STATEMENT_TOTAL),
    ])
    held = t.DocPlan(eid, "zk-tcs", "Q1 FY27", dc.period_end("Q1 FY27"), "press_release", "held",
                     held_reason="no document found in the announcements feed")
    with psycopg.connect(admin, autocommit=True) as c:
        n1 = task._write(c, read)
        n2 = task._write(c, held)
        assert (n1, n2) == (3, 1)                            # 1 doc + 2 values; 1 doc + 0 values
        rows = c.execute("""select status, pages_used, image_page_count, (select count(*) from equity.kpi_value v where v.document_id = d.id)
                              from equity.kpi_document d join core.entity e on e.id = d.entity_id
                             where e.slug = 'zk-tcs' order by period_end""").fetchall()
        assert [r[0] for r in rows] == ["held", "read"]      # Q1 FY27 (held) then Q2 FY27 (read)
        assert rows[1][1] == [2, 3] and rows[1][3] == 2      # pages and two value rows
        kept = c.execute("""select filter_status, filter_reason from equity.kpi_value v join equity.kpi_document d on d.id = v.document_id
                             join core.entity e on e.id = d.entity_id where e.slug = 'zk-tcs' order by row_no""").fetchall()
        assert kept[0] == ("kept", None) and kept[1] == ("filtered", "statement_total")
        # re-write the read quarter with one row: it replaces (delete + insert), not appends
        read2 = _read_plan(t, eid, "zk-tcs", "Q2 FY27", [
            (fl.Row("Branches", None, "count", Decimal(6500), None, 2), Decimal(6500), fl.KEPT, None)])
        task._write(c, read2)
        after = c.execute("""select count(*) from equity.kpi_value v join equity.kpi_document d on d.id = v.document_id
                             join core.entity e on e.id = d.entity_id where e.slug = 'zk-tcs'""").fetchone()[0]
        assert after == 1                                    # the two old rows gone, one new row
        c.execute("delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id = %s)", (eid,))
        c.execute("delete from equity.kpi_document where entity_id = %s", (eid,))
        c.execute("delete from core.entity where id = %s", (eid,))


@pytest.mark.db
def test_a_reused_documents_changed_rows_are_updated_in_place():
    """#512's fix round: a reused document keeps its document row and row numbers; only the re-filtered rows change."""
    import psycopg
    from conftest import dsn

    from ft.jobs.tasks import equity_kpi_tables as t
    admin = dsn("postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zr-%'))")
        c.execute("delete from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zr-%')")
        c.execute("delete from core.entity where slug like 'zr-%'")
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company','zr-hdfc','ZR HDFC','IN','INR',true) returning id""").fetchone()[0]
    task = t.EquityKpiTables()
    first = _read_plan(t, eid, "zr-hdfc", "Q2 FY26", [
        (fl.Row("CASA ratio", None, "percent", Decimal("34.5"), "%", 2), Decimal("34.5"), fl.FILTERED, fl.TILE_UNCONFIRMED),
        (fl.Row("Net profit growth", None, "percent_change", Decimal(12), "%", 3), Decimal(12), fl.FILTERED, fl.STATEMENT_TOTAL),
    ])
    with psycopg.connect(admin, autocommit=True) as c:
        task._write(c, first)
        doc = c.execute("select id from equity.kpi_document where entity_id = %s", (eid,)).fetchone()[0]
        again = _read_plan(t, eid, "zr-hdfc", "Q2 FY26", [])
        again.reused = True
        again.refiltered = [t.Value(1, first.values[0].row, Decimal("34.5"), fl.KEPT, None)]
        assert task._rewrite_filters(c, again) == 1
        rows = c.execute("""select v.document_id, v.row_no, v.filter_status, v.filter_reason from equity.kpi_value v
                             where v.document_id = %s order by v.row_no""", (doc,)).fetchall()
        assert rows == [(doc, 1, "kept", None), (doc, 2, "filtered", "statement_total")]
        assert task._rewrite_filters(c, _read_plan(t, eid, "zr-hdfc", "Q2 FY26", [])) == 0     # nothing changed: no write
        c.execute("delete from equity.kpi_value where document_id = %s", (doc,))
        c.execute("delete from equity.kpi_document where entity_id = %s", (eid,))
        c.execute("delete from core.entity where id = %s", (eid,))


# ----------------------------------------------------------------------------------- regressions from the first dev run (13 Nov)
class _Http:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))


class _RefusingFetcher:
    """A fetcher whose own `get` refuses `record_url`, as Fetcher.get does (ops.task_run 238 failed on that)."""
    def __init__(self, replies=()):
        self.http, self.counts, self.replies, self.gets = _Http(), {"403": 0}, list(replies), 0

    def get(self, url, *, source, params=None, headers=None, conditional=True, media_type=None, ext=None, keep=None,
            allow=()):
        self.gets += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_warm_goes_through_the_http_client():
    from types import SimpleNamespace

    from ft.equity import filings as flg
    from ft.jobs.tasks import equity_kpi_tables as t
    f = _RefusingFetcher()
    t.EquityKpiTables._warm(SimpleNamespace(fetcher=f))
    assert f.http.calls == [(flg.HOME, {"record_url": flg.HOME, "stats": f.counts})] and f.gets == 0


def test_a_refused_feed_request_warms_and_retries_once():
    from types import SimpleNamespace

    from ft.jobs.fetch import FetchError
    from ft.jobs.tasks import equity_kpi_tables as t
    f = _RefusingFetcher([FetchError("u", "HTTP 403", 403), SimpleNamespace(body=b"[]")])
    ans = t.EquityKpiTables()._get_json(SimpleNamespace(fetcher=f), "https://www.nseindia.com/api/x", "nse", {}, {}, warm=True)
    assert (ans.status, ans.reply) == (200, []) and f.gets == 2 and len(f.http.calls) == 1


def test_a_stop_resolves_every_later_quarter_as_held(monkeypatch):
    from types import SimpleNamespace

    from ft.jobs.tasks import equity_kpi_tables as t
    task = t.EquityKpiTables()
    monkeypatch.setattr(t.batch, "monthly_budget", lambda db: Decimal(25))
    monkeypatch.setattr(t.batch, "month_spend", lambda db: Decimal(0))
    monkeypatch.setattr(task, "companies", lambda db, start: [_Co(1, "tcs"), _Co(2, "infy")])
    monkeypatch.setattr(task, "_claude", lambda: object())
    monkeypatch.setattr(task, "discover", lambda ctx, cos, start, end: {})

    def one(ctx, db, co, rule, label, anns, prompt, schema, budget, spent, h):
        h.stopped = "run_cap"                              # the first document hits the cap
        return t.DocPlan(co.entity_id, co.slug, label, dc.period_end(label), "press_release", "held",
                         held_reason="stopped on the run_cap")
    monkeypatch.setattr(task, "_one", one)
    ctx = SimpleNamespace(fetcher=SimpleNamespace(db=None), now=datetime(2026, 11, 13, 16, 0, tzinfo=IST), until=None)
    h = task.fetch(ctx)
    assert len(h.plans) == len(dc.quarters("tcs")) + len(dc.quarters("infy"))
    assert all(p.status == "held" and "run_cap" in p.held_reason for p in h.plans)
    checks = {c.name: c for c in task.checks(None, h, 1)}
    assert checks["documents"].passed and not checks["budget"].passed    # loads what was read; the stop is a warning


def test_read_reaches_past_200_pages(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    seen = {}
    monkeypatch.setattr(t.pdfs, "read_pdf", lambda body, max_pages=200: seen.setdefault("max_pages", max_pages))
    t.EquityKpiTables._read(b"%PDF-")
    assert seen["max_pages"] >= 380                                       # Infosys's longest bundle (DA-10 K3)


def test_the_run_cap_is_what_is_left_of_the_pilot():
    """The pilot's $5 (pre-approval 2) less DA-10's $0.3864 and dev run 2's $0.6196 (13 Nov 2026), rounded down: no
    single run can take the pilot past $5 (#512's fix round tightened it from $4.61)."""
    from ft.jobs.tasks import equity_kpi_tables as t
    assert Decimal("3.99") == t.RUN_LIMIT_USD <= Decimal(5) - Decimal("0.3864") - Decimal("0.6196")


# ----------------------------------------------------------------------------------- the fix round (#512 part 2, 14 Nov)
def test_an_image_only_document_gets_no_tile_check():
    """Dev run 2 filtered every row of HDFC Bank's image quarters as tile_unconfirmed: no text table to confirm them."""
    img = [ex.Page(n, image_base64="x", image_media_type="image/png") for n in (1, 2)]
    mixed = [ex.Page(1, text="a table"), ex.Page(2, image_base64="x", image_media_type="image/png")]
    assert ex.tile_pages(img) == frozenset() and ex.tile_pages(mixed) == frozenset({2})
    rows = [fl.Row("CASA ratio", None, "percent", Decimal("34.5"), "%", 1),
            fl.Row("Branches", None, "count", Decimal(9500), None, 2)]
    out = ex.filter_rows(rows, img)
    assert [e.filter_status for e in out] == [fl.KEPT, fl.KEPT]
    # in a mixed document the image page's figure still needs a table match
    out = ex.filter_rows([fl.Row("CASA ratio", None, "percent", Decimal("34.5"), "%", 2)], mixed)
    assert (out[0].filter_status, out[0].filter_reason) == (fl.FILTERED, fl.TILE_UNCONFIRMED)


class _Got:
    def __init__(self, body):
        self.body = body


class _DocFetcher:
    """A fetcher serving each candidate's body by URL (nothing stored); records the URLs asked."""
    def __init__(self, bodies):
        self.bodies, self.asked, self.db = bodies, [], None

    def get(self, url, *, source, keep=None, allow=()):
        self.asked.append(url)
        return _Got(self.bodies[url])


def _info(texts):
    from ft.equity.calls import pdfs
    return pdfs.PdfInfo(True, page_texts=list(texts), pages=len(texts), text_layer=True)


def test_one_tries_candidates_in_order_and_takes_the_first_usable(monkeypatch):
    """ICICI Bank: the results day's first candidate is too short; the next one holds the news release."""
    from types import SimpleNamespace

    from ft.jobs.tasks import equity_kpi_tables as t
    task = t.EquityKpiTables()
    day = datetime(2025, 10, 18, 14, 41, tzinfo=IST)
    anns = [Ann("ICICI Bank Limited has submitted to the Exchange, the unaudited financial results", "a.pdf", day,
                url="short", category="Outcome of Board Meeting"),
            Ann("ICICI Bank Limited has informed the Exchange regarding Outcome of Board Meeting", "b.pdf",
                day.replace(minute=54), url="full", category="Outcome of Board Meeting")]
    texts = {b"%PDF-short": ["cover", "letter"],
             b"%PDF-full": ["cover"] + ["statement"] * 12 + ["News Release", "table", "Except for the historical"]}
    monkeypatch.setattr(t.pdfs, "is_pdf_bytes", lambda b: b.startswith(b"%PDF"))
    monkeypatch.setattr(task, "_read", lambda body: _info(texts[body]))
    monkeypatch.setattr(task, "_already_read", lambda *a: False)
    sent = {}

    def extract(pages, co, label, prompt, schema, budget, spent, h):
        sent["pages"] = [p.page for p in pages]
        return {14: [fl.Row("CASA ratio", None, "percent", Decimal("38.5"), "%", 14)]}, Decimal("0.01"), None
    monkeypatch.setattr(task, "_extract", extract)
    f = _DocFetcher({"short": b"%PDF-short", "full": b"%PDF-full"})
    ctx = SimpleNamespace(fetcher=f)
    h = t.Harvest()
    plan = task._one(ctx, None, _Co(5, "icicibank"), dc.load_rules()["icicibank"], "Q2 FY26", anns, None, None,
                     Decimal(25), Decimal(0), h)
    assert f.asked == ["short", "full"] and plan.status == "read" and plan.source_url == "full"
    assert plan.pages_used == sent["pages"] == [14, 15] and plan.page_count == 16
    assert [v.filter_status for v in plan.values] == [fl.KEPT] and h.cost == Decimal("0.01")


def test_one_holds_when_no_candidate_is_usable(monkeypatch):
    from types import SimpleNamespace

    from ft.jobs.tasks import equity_kpi_tables as t
    task = t.EquityKpiTables()
    day = datetime(2025, 10, 18, 14, 41, tzinfo=IST)
    anns = [Ann("ICICI Bank Limited has submitted to the Exchange, the unaudited financial results", "a.pdf", day,
                url="short", category="Outcome of Board Meeting")]
    monkeypatch.setattr(t.pdfs, "is_pdf_bytes", lambda b: True)
    monkeypatch.setattr(task, "_read", lambda body: _info(["cover", "letter"]))
    f = _DocFetcher({"short": b"%PDF"})
    plan = task._one(SimpleNamespace(fetcher=f), None, _Co(5, "icicibank"), dc.load_rules()["icicibank"], "Q2 FY26",
                     anns, None, None, Decimal(25), Decimal(0), t.Harvest())
    assert plan.status == "held" and plan.source_url == "short"
    assert plan.held_reason == "results day 18 Oct 2025: no usable filing (1 tried: 2 pages, fewer than 10)"
    none = task._one(SimpleNamespace(fetcher=f), None, _Co(5, "icicibank"), dc.load_rules()["icicibank"], "Q2 FY26",
                     [], None, None, Decimal(25), Decimal(0), t.Harvest())
    assert none.held_reason == "no results filing found in the announcements feed"


class _StoredDb:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params):
        return SimpleNamespace(fetchall=lambda: self.rows)


def test_a_reused_image_document_is_refiltered_without_ai(monkeypatch):
    """HDFC Bank's image quarters keep their files (same SHA-256): their stored rows are re-filtered, not re-asked."""
    from ft.jobs.tasks import equity_kpi_tables as t
    stored = [(1, "CASA ratio", None, "percent", Decimal("34.5"), "%", 1, "filtered", "tile_unconfirmed"),
              (2, "Net profit growth", None, "percent_change", Decimal(12), "%", 1, "filtered", "statement_total"),
              (3, "Branches", None, "count", Decimal(9500), None, 2, "filtered", "tile_unconfirmed")]
    plan = t.DocPlan(5, "hdfcbank", "Q2 FY26", dc.period_end("Q2 FY26"), "press_release", "read", pages_used=[1, 2],
                     image_page_count=2, reused=True)
    plan.values = values = t.EquityKpiTables()._refilter(_StoredDb(stored), plan, _info(["", ""]))
    assert [(v.row_no, v.filter_status, v.filter_reason) for v in values] == [
        (1, fl.KEPT, None), (2, fl.FILTERED, fl.STATEMENT_TOTAL), (3, fl.KEPT, None)]
    assert [v.row_no for v in plan.refiltered] == [1, 3]                    # only the changed rows are rewritten
    checks = {c.name: c for c in t.EquityKpiTables().checks(None, t.Harvest(plans=[plan], companies=[]), 1)}
    assert '"rows_refiltered": 2' in checks["extracted"].detail and "1 of 1 read documents" in checks["empty"].detail


@needs_feed
def test_the_saved_feed_picks_the_results_outcome():
    """On the saved NSE replies (1 Oct 2025 to 30 Sep 2026): ICICI Bank's results outcome, never its call notices (dev
    run 2 held all eight ICICI quarters on 2-page notices); Infosys's outcome bundle, as before."""
    import gzip
    import json

    from ft.equity.calls import discovery as disc
    rules = dc.load_rules()
    want = {("ICICIBANK", "icicibank"): {"Q2 FY26": "18 Oct 14:41", "Q3 FY26": "17 Jan 15:06", "Q4 FY26": "18 Apr 14:59",
                                         "Q1 FY27": "18 Jul 14:23"},
            ("INFY", "infy"): {"Q2 FY26": "16 Oct 16:06", "Q3 FY26": "14 Jan 16:05", "Q4 FY26": "23 Apr 17:01",
                               "Q1 FY27": "23 Jul 16:20"}}
    for (sym, slug), quarters in want.items():
        with gzip.open(next((FEED / "nse").glob(f"nse_ann_{sym}_*.json.gz"))) as fh:
            rows = json.loads(fh.read())
        filings = []
        for r in rows:
            a = disc.norm_nse(r)
            if a is not None:
                filings.append(dc.Filing(a.exchange, a.filing_key, a.disseminated, a.subject, a.file_name, a.url,
                                         r.get("desc")))
        for label, first in quarters.items():
            _rd, cands = dc.candidates(filings, rules[slug], label)
            assert cands and cands[0].disseminated.strftime("%d %b %H:%M") == first, (slug, label)
            assert not any("call" in (c.subject or "").lower() for c in cands)
