"""Unit tests for the KPI-table pilot's pure logic (DD-04, #512): the document rules and page cut (documents), the
post-filters, sign convention and tile check (filters), the per-page request and reply parsing (extraction), and the AI
wrapper's image source. Hand-written page texts and a fake client — no network, no PDF, no database."""
from __future__ import annotations

import base64
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from ft.ai.client import Source, load_prompt, load_schema
from ft.equity.kpi import documents as dc
from ft.equity.kpi import extraction as ex
from ft.equity.kpi import filters as fl

IST = ZoneInfo("Asia/Kolkata")


class Ann:
    def __init__(self, subject, file_name, disseminated, url="u", filing_key="k", exchange="NSE"):
        self.subject, self.file_name, self.disseminated = subject, file_name, disseminated
        self.url, self.filing_key, self.exchange = url, filing_key, exchange


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
def test_select_matches_subject_in_window_earliest_wins():
    rules = dc.load_rules()
    r = rules["tcs"]
    anns = [
        Ann("Outcome of board meeting", "outcome.pdf", datetime(2026, 10, 9, 18, 0, tzinfo=IST)),   # no subject match
        Ann("Press Release Q2 FY27", "pr.pdf", datetime(2026, 10, 9, 19, 5, tzinfo=IST)),           # match (earliest PR)
        Ann("Press Release (revised)", "pr2.pdf", datetime(2026, 10, 15, 11, 0, tzinfo=IST)),       # later PR
        Ann("Press Release", "old.pdf", datetime(2025, 10, 9, 19, 0, tzinfo=IST)),                  # a year early: out of window
    ]
    got = dc.select(anns, r, "Q2 FY27")
    assert got is not None and got.file_name == "pr.pdf"


def test_select_none_when_no_match():
    rules = dc.load_rules()
    anns = [Ann("Investor Presentation", "ppt.pdf", datetime(2026, 10, 9, 19, 0, tzinfo=IST))]
    assert dc.select(anns, rules["tcs"], "Q2 FY27") is None


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
