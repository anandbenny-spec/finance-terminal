"""Unit tests for the KPI-table pilot's flags (DD-05, #513): the curated dictionary (`ft.equity.kpi.dictionary`) and the grid
with Plan 05 §1.3's six rules (`ft.equity.kpi.flags`). Hand-written grids and labels; no network, no database, no AI."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from ft.equity.kpi import dictionary as kdict
from ft.equity.kpi import flags as kf
from ft.equity.kpi.documents import COMPANIES, period_end


# ----------------------------------------------------------------------------------- the dictionary: normalisation
def test_norm_label_drops_figures_period_stamps_and_reads_qoq():
    assert kdict.norm_label("Q-o-Q CC Growth (%)") == "qoq cc growth"
    assert kdict.norm_label("q o q cc growth") == "qoq cc growth"
    assert kdict.norm_label("Composition (%) Q2 FY25") == "composition"
    assert kdict.norm_label("Y-o-YCC growth") == "yoy cc growth"
    assert kdict.norm_label("IT services' attrition") == "it services attrition"
    assert kdict.norm_label("Life Sciences & Healthcare") == "life sciences and healthcare"


def test_norm_member_keeps_period_words_so_a_full_year_member_is_not_the_total():
    assert kdict.norm_member("FY25") == "fy"
    assert kdict.norm_member("Q4") == "q"
    assert kdict.norm_member(None) == ""
    assert kdict.norm_member("Energy, Resources & Utilities") == "energy resources and utilities"


def test_core_takes_the_member_out_of_the_label_once():
    assert kdict.core("Geography Composition (%): UK", "UK") == "geography composition"
    assert kdict.core("BFSI (+1.1% QoQ CC)", "BFSI") == "qoq cc"
    assert kdict.core("Workforce", None) == "workforce"
    assert kdict.core("uk uk share", "UK") == "uk share"


# ----------------------------------------------------------------------------------- the dictionary: the file
@pytest.fixture(scope="module")
def dictionary():
    return kdict.load()


def test_the_file_covers_the_seven_pilot_companies(dictionary):
    assert set(dictionary) == set(COMPANIES)
    for co in dictionary.values():
        assert co.document_class in ("press_release", "fact_sheet")
        assert co.metrics and co.index


def test_k6_tcs_workforce_and_attrition_are_one_key_each_and_countries_is_not_workforce(dictionary):
    tcs = dictionary["tcs"]
    assert {tcs.key_of(x, None, "people") for x in ("Workforce strength", "Workforce", "Employee headcount")} == {"workforce"}
    assert tcs.key_of("Countries with workforce", None, "count") is None
    assert tcs.key_of("Countries with workforce", None, "people") is None
    assert tcs.key_of("AI workforce with higher order skills", None, "people") is None
    assert {tcs.key_of("LTM IT services attrition rate", "IT services", "percent"),
            tcs.key_of("IT services' attrition", "IT services", "percent"),
            tcs.key_of("LTM attrition: IT services", "IT services", "percent"),
            tcs.key_of("IT services attrition", None, "percent")} == {"attrition_ltm"}
    assert tcs.key_of("Workforce strength", None, "percent") is None          # the measure must match


def test_a_breakdown_member_is_its_own_key_and_aliases_resolve(dictionary):
    tcs = dictionary["tcs"]
    assert tcs.key_of("Q-o-Q CC Growth (%)", "BFSI", "percent_change") == "cc_growth_qoq_industry.bfsi"
    assert tcs.key_of("TSS (+1.8% QoQ CC)", "TSS", "percent_change") == "cc_growth_qoq_industry.technology_and_services"
    assert tcs.key_of("Q-o-Q CC Growth (%)", "UK", "percent_change") == "cc_growth_qoq_geography.uk"
    assert tcs.key_of("Q-o-Q CC Growth (%)", None, "percent_change") == "cc_growth_qoq"
    assert tcs.key_of("Q-o-Q CC Growth (%)", "Martian", "percent_change") is None
    assert tcs.metrics["industry_share.bfsi"].label == "Revenue share by industry: BFSI"
    assert "tss" in tcs.metrics["cc_growth_qoq_industry.technology_and_services"].member_terms


def test_a_full_year_member_never_maps_to_the_quarter_key(dictionary):
    assert dictionary["tcs"].key_of("Workforce strength", "FY25", "people") is None


def _write(tmp_path: Path, metrics: list[dict]) -> Path:
    p = tmp_path / "d.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "companies": {"acme": {
        "document_class": "press_release", "quarters": ["Q1 FY26", "Q2 FY26"], "metrics": metrics}}}))
    return p


def _metric(key, labels, **kw):
    return {"key": key, "name": key, "measure": ["percent"], "unit": "%", "terms": [key],
            "labels": [{"label": x, "seen": "11", **({"why": "same"} if i else {})} for i, x in enumerate(labels)], **kw}


def test_load_refuses_two_metrics_sharing_a_label_member_and_measure(tmp_path):
    with pytest.raises(kdict.DictionaryError, match="maps to both"):
        kdict.load(_write(tmp_path, [_metric("a", ["casa ratio"]), _metric("b", ["casa ratio q fy"])]))


def test_load_refuses_a_merge_without_its_reason(tmp_path):
    m = _metric("a", ["casa ratio", "casa"])
    del m["labels"][1]["why"]
    with pytest.raises(kdict.DictionaryError, match="no reason"):
        kdict.load(_write(tmp_path, [m]))


def test_load_refuses_an_alias_for_no_member(tmp_path):
    m = _metric("a", ["share"], members={"bfsi": "BFSI"}, aliases={"fs": "banking"})
    with pytest.raises(kdict.DictionaryError, match="alias"):
        kdict.load(_write(tmp_path, [m]))


def test_load_refuses_another_version(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text("version: 2\ncompanies: {}\n")
    with pytest.raises(kdict.DictionaryError, match="version"):
        kdict.load(p)


def test_the_file_holds_no_figures():
    text = kdict.PATH.read_text()
    for line in text.splitlines():
        if "label:" in line:
            label = line.split("label:", 1)[1].split(",", 1)[0]
            assert not any(c.isdigit() for c in label), line


# ----------------------------------------------------------------------------------- the grid: helpers
UNITS = {"a": "%", "b": "%", "c": "people", "n": "%", "p": "people"}
Q = ["Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26", "Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"]


PAD = {f"z{i}": "11111111" for i in range(9)}       # nine metrics every quarter, so one absence isn't rule 3's fall
UNITS.update({k: "count" for k in PAD})


def docs(pattern: dict[str, str], held: str = "", cls: dict[int, str] | None = None, pad: bool = True) -> list[kf.Doc]:
    """A grid from patterns: {key: "11.1h111"}; one character per quarter of Q ("1" present, "." absent); `held` marks
    held quarters by "h" at their position; `pad` adds nine metrics present every quarter."""
    pattern = {**PAD, **pattern} if pad else pattern
    out = []
    n = len(next(iter(pattern.values())))
    for i in range(n):
        status = "held" if held and held[i] == "h" else "read"
        present = {} if status == "held" else {
            k: kf.Reported(Decimal(i + 1), "%", 3, (i + 1,)) for k, p in pattern.items() if p[i] == "1"}
        out.append(kf.Doc(i + 1, Q[i], period_end(Q[i]), (cls or {}).get(i, "press_release"), status, present,
                          bytes([i]) * 32))
    return out


def flags(pattern, held="", pad=True, **kw):
    g = kf.grid(docs(pattern, held, kw.pop("cls", None), pad), UNITS, "press_release", **kw)
    return g, [(f.key, f.kind, f.current.fiscal_label) for f in g.flags]


# ----------------------------------------------------------------------------------- rule 1
def test_rule1_present_in_three_of_the_previous_four_flags_the_first_absence():
    g, out = flags({"a": "1111.111", "c": "11111111"})
    assert out == [("a", "missing", "Q3 FY26")]
    f = g.flags[0]
    assert [d.fiscal_label for d in f.previous] == ["Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26"]
    assert f.last.fiscal_label == "Q2 FY26" and f.last_value == Decimal(4)


def test_rule1_two_of_the_previous_four_is_not_enough():
    assert flags({"a": "1.1..111", "c": "11111111"})[1] == []


def test_rule1_a_held_quarter_is_neither_present_nor_absent():
    # Q4 FY25 held: Q3 FY26's previous four are Q2 FY26, Q1 FY26, (held), Q3 FY25 — all three others present
    assert flags({"a": "1.11.111", "c": "1.111111"}, held=".h......")[1] == [("a", "missing", "Q3 FY26")]
    # ... and one of the other three absent is too few (2 of 3)
    assert flags({"a": "1..1.111", "c": "1.111111"}, held=".h......")[1] == []


def test_a_held_current_quarter_never_flags_and_is_no_comparison():
    # Q3 FY26 held: no flag there; Q4 FY26 compares against Q2 FY26, its last usable document
    assert flags({"a": "1111.111", "c": "11111111"}, held="....h...")[1] == []
    assert flags({"a": "11111.11", "c": "11111111"}, held="....h...")[1] == [("a", "missing", "Q4 FY26")]


def test_a_read_document_with_nothing_mapped_is_treated_as_missing():
    # Q3 FY26 read but empty: neither absence nor comparison
    assert flags({"a": "1111.111", "c": "1111.111"}, pad=False)[1] == []


# ----------------------------------------------------------------------------------- rule 2
def test_rule2_another_document_class_is_not_compared():
    assert flags({"a": "1111.111", "c": "11111111"}, cls={4: "fact_sheet"})[1] == []
    # and as a previous document it doesn't count: Q4 FY26 then has Q2 FY26, Q1 FY26, Q4 FY25 known
    assert flags({"a": "11111.11", "c": "11111111"}, cls={4: "fact_sheet"})[1] == [("a", "missing", "Q4 FY26")]


# ----------------------------------------------------------------------------------- rule 3
def test_rule3_a_sharp_fall_in_the_metric_count_holds_the_document_whole():
    base = {k: "11111111" for k in "defghij"}            # 7 keys every quarter
    UNITS.update({k: "count" for k in "defghij"})
    sparse = dict(base, a="1111.111", **{k: "1111.111" for k in "defg"})   # Q3 FY26 keeps 3 of 8: below 70%
    g, out = flags(sparse, pad=False)
    assert g.held_rule3 == ["Q3 FY26"] and out == []
    assert g.usable["Q3 FY26"] is False and g.usable["Q4 FY26"] is True


def test_rule3_at_seventy_percent_or_more_the_document_stands():
    g, out = flags({"a": "1111.111", "b": "11111111", "c": "11111111", "p": "11111111"}, pad=False)   # 3 of 4 = 75%
    assert g.held_rule3 == [] and out == [("a", "missing", "Q3 FY26")]


# ----------------------------------------------------------------------------------- rule 4
def test_rule4_a_dropped_metric_and_a_new_one_with_the_same_unit_is_renamed_or_redefined():
    g, out = flags({"a": "1111....", "n": "....1111", "c": "11111111"})
    assert out == [("a", "renamed_or_redefined", "Q3 FY26")]
    assert g.flags[0].renamed_to == ["n"]


def test_rule4_a_new_metric_with_another_unit_does_not_explain_the_drop():
    assert flags({"a": "1111.111", "p": "....1111", "c": "11111111"})[1][0] == ("a", "missing", "Q3 FY26")


def test_rule4_a_metric_seen_in_the_previous_four_is_not_new():
    assert flags({"a": "1111.111", "b": "1...1111", "c": "11111111"})[1][0][1] == "missing"


def test_rule4_a_renamed_metric_is_not_escalated_to_dropped():
    assert flags({"a": "1111....", "n": "....1111", "c": "11111111"})[1] == [("a", "renamed_or_redefined", "Q3 FY26")]


# ----------------------------------------------------------------------------------- rule 5
def test_rule5_a_merger_after_the_last_report_annotates_the_flag():
    brk = [kf.Break(date(2025, 11, 1), "merger", "Acme Holdings into Acme")]
    g, _ = flags({"a": "1111.111", "c": "11111111"}, breaks=brk)
    assert g.flags[0].note == "Merger effective 1 Nov 2025: Acme Holdings into Acme"


def test_rule5_a_merger_before_the_last_report_does_not():
    brk = [kf.Break(date(2025, 8, 1), "merger", "earlier")]
    assert flags({"a": "1111.111", "c": "11111111"}, breaks=brk)[0].flags[0].note is None


# ----------------------------------------------------------------------------------- rule 6
def test_rule6_missing_at_the_first_absence_dropped_at_the_second():
    g, out = flags({"a": "1111..11", "c": "11111111"})
    assert out == [("a", "missing", "Q3 FY26"), ("a", "dropped", "Q4 FY26")]
    dropped = g.flags[1]
    assert dropped.last.fiscal_label == "Q2 FY26" and [d.fiscal_label for d in dropped.previous][-1] == "Q2 FY26"


def test_rule6_a_third_absence_adds_no_flag_and_a_return_resets():
    assert flags({"a": "1111...1", "c": "11111111"})[1] == [("a", "missing", "Q3 FY26"), ("a", "dropped", "Q4 FY26")]
    assert flags({"a": "1111.1.1", "c": "11111111"})[1] == [("a", "missing", "Q3 FY26"), ("a", "missing", "Q1 FY27")]


def test_rule6_a_held_quarter_between_the_absences_is_skipped():
    assert flags({"a": "1111...1", "c": "11111111"}, held=".....h..")[1] == [("a", "missing", "Q3 FY26"),
                                                                             ("a", "dropped", "Q1 FY27")]


def test_before_rules_counts_every_metric_in_the_previous_release_and_absent_from_this_one():
    g, _ = flags({"a": "1.1.1.1.", "c": "11111111"})
    assert g.before_rules == 4 and g.flags == []


# ----------------------------------------------------------------------------------- reported values
def test_reported_keeps_one_value_and_holds_back_a_disagreement():
    r = kf.reported([(1, "a", Decimal("12.5"), "%", 3), (7, "a", Decimal("12.5"), "%", 5), (2, "b", Decimal(1), "%", 3),
                     (3, "b", Decimal(2), "%", 3), (4, None, Decimal(9), "%", 3)])
    assert r["a"] == kf.Reported(Decimal("12.5"), "%", 3, (1, 7))
    assert r["b"].value is None and r["b"].unit is None and r["b"].rows == (2, 3)
    assert set(r) == {"a", "b"}


def test_quarter_index_is_consecutive_across_fiscal_years():
    assert kf.quarter_index("Q1 FY27") - kf.quarter_index("Q4 FY26") == 1
    with pytest.raises(ValueError):
        kf.quarter_index("FY26")


# ----------------------------------------------------------------------------------- the task: keys, grid and checks
def _plan(t, slug, label, rows, status="read"):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ft.equity.kpi import filters as fl
    plan = t.DocPlan(1, slug, label, period_end(label), "press_release", status,
                     **({} if status == "read" else {"held_reason": "no results filing found in the announcements feed"}))
    if status == "read":
        plan.source_url, plan.filing_key, plan.exchange = "https://x/pr.pdf", f"k{label}", "NSE"
        plan.filed_at, plan.sha256, plan.bytes, plan.page_count = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")), bytes(32), 1, 9
    plan.values = [t.Value(i + 1, fl.Row(lab, mem, meas, Decimal(i + 1), unit, 3), Decimal(i + 1),
                           fl.KEPT if keep else fl.FILTERED, None if keep else fl.QUOTE)
                   for i, (lab, mem, meas, unit, keep) in enumerate(rows)]
    return plan


TCS_ROWS = [("Workforce strength", None, "people", "people", True),
            ("LTM IT services attrition rate", "IT services", "percent", "%", True),
            ("Countries with workforce", None, "count", None, True),
            ("Women in the workforce", None, "percent", "%", False)] + [
            ("Industry Composition (%)", m, "percent", "%", True)
            for m in ("BFSI", "Consumer Business", "Manufacturing", "Life Sciences & Healthcare", "Technology & Services",
                      "Communication & Media", "Energy, Resources and Utilities", "Regional Markets & Others")]


def test_the_task_maps_kept_rows_only_and_builds_each_companys_grid():
    from ft.jobs.tasks import equity_kpi_tables as t
    h = t.Harvest(companies=[])
    labels = ["Q2 FY25", "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26"]
    for label in labels:
        if label == "Q4 FY25":
            h.plans.append(_plan(t, "tcs", label, [], status="held"))
        elif label == "Q2 FY26":                                  # K6: workforce and attrition left out
            h.plans.append(_plan(t, "tcs", label, TCS_ROWS[2:]))
        else:
            h.plans.append(_plan(t, "tcs", label, TCS_ROWS))
    t.EquityKpiTables._flags(None, h)
    keys = [v.dict_key for v in h.plans[0].values]
    assert keys[:4] == ["workforce", "attrition_ltm", None, None]   # countries isn't workforce; a filtered row gets none
    assert keys[4] == "industry_share.bfsi"
    g = h.grids["tcs"]
    assert {(f.key, f.kind, f.current.fiscal_label) for f in g.flags} == {
        ("workforce", "missing", "Q2 FY26"), ("attrition_ltm", "missing", "Q2 FY26")}
    import json
    checks = {c.name: c for c in t.EquityKpiTables._flag_checks(h)}
    assert json.loads(checks["flags"].detail)["k6_tcs_q2_fy26"] == {"workforce": "missing", "attrition_ltm": "missing"}
    assert json.loads(checks["flags"].detail)["by_company"]["tcs"]["missing"] == 2
    assert json.loads(checks["dictionary"].detail)["tcs"] == "38 of 42 kept rows mapped"
    assert not checks["flags"].blocking and not checks["dictionary"].blocking


def test_rule5_reads_the_corporate_action_record_and_the_curated_breaks():
    from ft.jobs.tasks import equity_kpi_tables as t

    class Db:
        def execute(self, sql, params):
            rows = ([(7, "acme-demerger-2025", "demerger", date(2025, 11, 1))] if "corporate_action" in sql
                    else [(7, "hdfcbank")])
            return type("R", (), {"fetchall": lambda self: rows})()
    out = t._breaks(Db(), [7])
    assert [(b.kind, b.effective) for b in out[7]] == [("demerger", date(2025, 11, 1)), ("merger", date(2023, 7, 1))]
    assert out[7][1].what.startswith("HDFC Bank: Disclosure under Regulation 30")


@pytest.mark.db
def test_the_curated_keys_are_written_with_the_rows_and_updated_on_a_reused_document():
    import psycopg
    from conftest import dsn

    from ft.jobs.tasks import equity_kpi_tables as t
    admin = dsn("postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zd-%'))")
        c.execute("delete from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zd-%')")
        c.execute("delete from core.entity where slug like 'zd-%'")
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company','zd-tcs','ZD TCS','IN','INR',true) returning id""").fetchone()[0]
        task = t.EquityKpiTables()
        plan = _plan(t, "zd-tcs", "Q2 FY27", TCS_ROWS[:3])
        plan.entity_id = eid
        plan.values[0].dict_key, plan.values[1].dict_key = "workforce", "attrition_ltm"
        task._write(c, plan)
        q = """select v.row_no, v.dict_key from equity.kpi_value v join equity.kpi_document d on d.id = v.document_id
                where d.entity_id = %s order by v.row_no"""
        assert c.execute(q, (eid,)).fetchall() == [(1, "workforce"), (2, "attrition_ltm"), (3, None)]
        plan.values[0].dict_key, plan.values[2].dict_key = None, "workforce"      # a dictionary change
        plan.reused = True
        task._write_keys(c, plan)
        assert c.execute(q, (eid,)).fetchall() == [(1, None), (2, "attrition_ltm"), (3, "workforce")]
        c.execute("delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id = %s)", (eid,))
        c.execute("delete from equity.kpi_document where entity_id = %s", (eid,))
        c.execute("delete from core.entity where id = %s", (eid,))
