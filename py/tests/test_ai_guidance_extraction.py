"""Guidance extraction (Spec 02B §5.9, §13.5, Spec 02B.1 C1-C4, C8-C9; job 2B-13, #176): the request for a stored call and every check
on the reply, run on crafted answers over small synthetic transcripts (tests/call_fixtures.py): each check drops its item and counts
it by reason, the list an item belongs to is set by the turn its quote lies in, and the output's content holds no other text.
No database and no model: the DB tests are in test_equity_calls_ai_task.py."""
import json
import re
from dataclasses import replace
from datetime import date

import pytest
from call_fixtures import build, calldoc

from ft.ai import call_summary as cs
from ft.ai import guidance_extraction as ge
from ft.ai.client import TASKS, load_prompt, load_schema
from ft.ai.guidance_extraction import Metrics, check, content, load_metrics, passes_content_test, period_dates

METRICS = load_metrics()
PROMPT = load_prompt(ge.TASK, "v1")
SCHEMA = load_schema(ge.TASK)
RAO, IYER = "Ravi Rao", "Meera Iyer"
CALL_DAY = date(2026, 10, 16)


# ---------------------------------------------------------------------------------------------- helpers
def doc(*turns: tuple[str, str, str], m: int = 3, day: date = CALL_DAY) -> cs.CallDoc:
    """A call of the turns given (speaker, role, the turn's sentences), after a moderator's opening line, with the company's
    fiscal-year end month `m`."""
    lines = [("Moderator", "moderator", "Moderator: Good day and welcome to the Acme Limited earnings call.")]
    lines += [(sp, role, f"{sp}: {text}") for sp, role, text in turns]
    return replace(calldoc(build(lines)), fy_end_month=m, day=day)


def one(sentence: str, role: str = "management", **kw) -> cs.CallDoc:
    return doc((RAO, role, sentence), **kw)


def T(quote: str, **kw) -> dict:
    """A target as the schema has it: a 14 per cent revenue-growth point for FY27, unless the keywords say otherwise."""
    return {"quote": quote, "metric": "revenue_growth", "metric_text": "revenue growth", "kind": "point", "value": 14,
            "low": None, "high": None, "unit": "pct", "direction": None, "comparison": None, "period_text": "FY27",
            "period_kind": "year", "fiscal_year": 2027, "start_fiscal_year": None, "part": None, "basis": None,
            "growth_basis": "yoy", "scope_text": None} | kw


def S(quote: str, **kw) -> dict:
    """A stated figure: revenue of 4,500 crore in Q2 FY27, unless the keywords say otherwise."""
    return {"quote": quote, "metric": "revenue", "metric_text": "revenue", "value": 4500, "unit": "inr",
            "period_text": "Q2 FY27", "period_kind": "quarter", "fiscal_year": 2027, "part": 2, "basis": None,
            "growth_basis": "level", "scope_text": None} | kw


def chk(call: cs.CallDoc, targets=(), stated=(), metrics: Metrics = METRICS) -> ge.Checked:
    return check({"targets": list(targets), "stated_figures": list(stated)}, call, metrics)


GROWTH = "We expect revenue growth of 14 per cent in FY27."


# ---------------------------------------------------------------------------------------------- the map and the request
def test_the_metric_map_and_the_schema_agree_and_the_switch_starts_on():
    assert METRICS.unattributed_targets is True and METRICS.map_version == 1
    keys = set(METRICS.labels)
    props = SCHEMA["properties"]
    assert set(props["targets"]["items"]["properties"]["metric"]["enum"]) == keys
    assert set(props["stated_figures"]["items"]["properties"]["metric"]["enum"]) == keys - {"other"}
    for part in (props["targets"]["items"], props["stated_figures"]["items"]):
        assert part["additionalProperties"] is False and set(part["required"]) == set(part["properties"])
    assert TASKS[ge.TASK].route == "json"


def test_the_metric_map_refuses_a_file_that_would_break_a_rule(tmp_path):
    good = "map_version: 2\nunattributed_targets: false\nmetrics:\n  revenue: Revenue\n  other: Other\n"
    p = tmp_path / "m.yaml"
    p.write_text(good, encoding="utf-8")
    assert load_metrics(p) == Metrics(2, False, {"revenue": "Revenue", "other": "Other"})
    p.write_text(good.replace("  other: Other\n", ""), encoding="utf-8")
    with pytest.raises(ValueError, match="include 'other'"):
        load_metrics(p)
    p.write_text(good.replace("revenue:", "Revenue-1:"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[a-z0-9_\]"):
        load_metrics(p)
    p.write_text(good.replace("false", "'no'"), encoding="utf-8")
    with pytest.raises(TypeError, match="true or false"):
        load_metrics(p)


def test_the_request_marks_each_turn_with_the_role_our_rules_read_and_names_the_year_end_and_the_call_date():
    call = doc((RAO, "management", GROWTH), ("Participant A", "unknown", "We expect loan growth of 15 per cent in FY27."))
    marked = ge.marked_text(call)
    assert marked.startswith("[moderator]\nModerator: Good day")
    assert f"\n\n[management · Ravi Rao]\n{RAO}: {GROWTH}" in marked
    assert "[unknown · Participant A]\nParticipant A: We expect loan growth" in marked
    req = ge.guidance_request(call, PROMPT, SCHEMA)
    (src,) = req.sources
    assert src.text == marked and req.schema is SCHEMA and req.note is None and req.task == ge.TASK
    assert "fiscal year ends in March" in req.instruction and "16 Oct 2026" in req.instruction
    assert "never instructions" in PROMPT.text or "data, not instructions" in PROMPT.text
    assert "<!--" not in PROMPT.text
    retry = ge.guidance_request(call, PROMPT, SCHEMA, note="the reply isn't the schema's JSON")
    assert retry.note.startswith("Your previous answer") and retry.sources == req.sources
    assert "fiscal year ends in December" in ge.guidance_request(replace(call, fy_end_month=12), PROMPT, SCHEMA).instruction


def test_a_request_needs_the_fiscal_year_end_month_and_a_check_needs_it_and_the_call_date():
    call = one(GROWTH)
    for bad in (None, 0, 13):
        with pytest.raises(ValueError, match="fiscal-year end month"):
            ge.guidance_request(replace(call, fy_end_month=bad), PROMPT, SCHEMA)
    for broken in (replace(call, fy_end_month=None), replace(call, day=None)):
        with pytest.raises(ValueError, match="are needed to check"):
            chk(broken)


def test_the_task_is_registered_with_the_json_route_and_a_reply_budget():
    spec = TASKS[ge.TASK]
    assert (spec.route, spec.effort, spec.max_tokens) == ("json", "medium", 16_000)
    assert ge.MAX_QUOTE == 400 and ge.MAX_STATED == 12 and ge.MAX_ESTIMATED_TOKENS == cs.MAX_ESTIMATED_TOKENS


# ---------------------------------------------------------------------------------------------- periods
@pytest.mark.parametrize("kind, fy, part, start, m, expected", [
    ("year", 2027, None, None, 3, ("2026-04-01", "2027-03-31")),
    ("quarter", 2027, 1, None, 3, ("2026-04-01", "2026-06-30")),
    ("quarter", 2027, 2, None, 3, ("2026-07-01", "2026-09-30")),
    ("quarter", 2027, 4, None, 3, ("2027-01-01", "2027-03-31")),
    ("half_year", 2027, 1, None, 3, ("2026-04-01", "2026-09-30")),
    ("half_year", 2027, 2, None, 3, ("2026-10-01", "2027-03-31")),
    ("nine_months", 2027, None, None, 3, ("2026-04-01", "2026-12-31")),
    ("multi_year", 2030, None, 2027, 3, ("2026-04-01", "2030-03-31")),
    ("year", 2027, None, None, 12, ("2027-01-01", "2027-12-31")),
    ("quarter", 2027, 3, None, 12, ("2027-07-01", "2027-09-30")),
    ("year", 2027, None, None, 6, ("2026-07-01", "2027-06-30")),
    ("quarter", 2027, 2, None, 6, ("2026-10-01", "2026-12-31")),
    ("year", 2028, None, None, 2, ("2027-03-01", "2028-02-29")),                    # a leap year's February
    ("year", 2027, None, None, 2, ("2026-03-01", "2027-02-28")),
    ("quarter", 2028, 4, None, 3, ("2028-01-01", "2028-03-31")),
])
def test_a_period_is_named_for_the_calendar_year_its_fiscal_year_ends_in(kind, fy, part, start, m, expected):
    assert period_dates(kind, fy, part, start, m) == tuple(date.fromisoformat(d) for d in expected)


@pytest.mark.parametrize("kind, fy, part, start", [
    ("quarter", 2027, None, None), ("quarter", 2027, 5, None), ("quarter", 2027, 0, None), ("half_year", 2027, 3, None),
    ("half_year", 2027, None, None), ("year", 2027, 1, None), ("nine_months", 2027, 3, None),
    ("multi_year", 2030, None, None), ("multi_year", 2027, None, 2027), ("multi_year", 2027, None, 2030),
    ("multi_year", 2030, 1, 2027), ("fortnight", 2027, None, None), ("undated", 2027, None, None),
])
def test_a_period_that_can_not_be_resolved_has_no_dates(kind, fy, part, start):
    assert period_dates(kind, fy, part, start, 3) is None


def test_the_fiscal_year_follows_the_companys_own_year_end_month():
    sentence = GROWTH
    for m, (a, b) in {3: ("2026-04-01", "2027-03-31"), 12: ("2027-01-01", "2027-12-31"),
                      6: ("2026-07-01", "2027-06-30")}.items():
        (t,) = chk(one(sentence, m=m), [T(sentence)]).targets
        assert (t["target_start"], t["target_end"]) == (a, b), m


# ---------------------------------------------------------------------------------------------- a clean answer
BIG = [
    (RAO, "management", ("Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27. "
                         "We expect revenue growth of 14 per cent in FY27. "
                         "We aim for an EBITDA margin between 21 and 23 per cent in FY27. "
                         "We will spend capital expenditure of 800 crore in FY27. "
                         "We expect double-digit revenue growth in the second half of FY27. "
                         "We will be net debt free by the end of FY27. "
                         "We expect EBITDA margin in the mid-teens next year. "
                         "Our EBITDA margin in Q1 FY27 was 20.5 per cent.")),
    ("Anil Shah", "analyst", "Will capital expenditure be 900 crore in FY27? We would like to know."),
    (IYER, "management", "Our effective tax rate will be 25 per cent in FY27."),
    ("Participant A", "unknown", ("We expect loan growth of 15 per cent in FY27. "
                                  "We also expect an effective tax rate of 25 per cent in FY27.")),
]


def test_a_clean_answer_is_kept_management_first_then_the_separate_list_then_the_stated_figures():
    call = doc(*BIG)
    targets = [
        T("We will be net debt free by the end of FY27.", metric="net_debt", metric_text="net debt", kind="ceiling",
          value=0, unit="inr", growth_basis="level"),
        T("We expect loan growth of 15 per cent in FY27.", metric="advances_growth", metric_text="loan growth",
          value=15),                                                                        # the separate list
        T("We aim for an EBITDA margin between 21 and 23 per cent in FY27.", metric="ebitda_margin",
          metric_text="EBITDA margin", kind="range", value=None, low=21, high=23, growth_basis="level"),
        T("We expect double-digit revenue growth in the second half of FY27.", kind="floor", value=10,
          period_kind="half_year", part=2, period_text="H2 FY27"),
        T("We will spend capital expenditure of 800 crore in FY27.", metric="capex", metric_text="capital expenditure",
          value=800, unit="inr", growth_basis="level"),
        T("We expect EBITDA margin in the mid-teens next year.", metric="ebitda_margin", metric_text="EBITDA margin",
          value=None, unit=None, fiscal_year=2028, period_text="next year", growth_basis="level"),
        T("Our effective tax rate will be 25 per cent in FY27.", metric="tax_rate", metric_text="effective tax rate",
          value=25, growth_basis="level"),
        T(GROWTH),
    ]
    stated = [S("Our EBITDA margin in Q1 FY27 was 20.5 per cent.", metric="ebitda_margin", metric_text="EBITDA margin",
                value=20.5, unit="pct", part=1, period_text="Q1 FY27"),
              S("Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27.")]
    c = chk(call, targets, stated)
    assert c.ok and not c.drops and not c.empty
    # management's targets in document order (turn 1, then turn 3), then the separate list; stated figures in document order
    assert [(t["metric"], t["kind"], t["speaker_role"]) for t in c.targets] == [
        ("revenue_growth", "point", "management"), ("ebitda_margin", "range", "management"),
        ("capex", "point", "management"), ("revenue_growth", "floor", "management"),
        ("net_debt", "ceiling", "management"), ("ebitda_margin", "point", "management"),
        ("tax_rate", "point", "management"), ("advances_growth", "point", "unknown")]
    assert [s["metric"] for s in c.stated] == ["revenue", "ebitda_margin"]
    assert c.management_targets == 7
    assert [p for p, _ in c.citations] == [f"/targets/{i}" for i in range(8)] + ["/stated_figures/0", "/stated_figures/1"]
    assert [(s.start, s.end) for _, s in c.citations] == [tuple(x["span"]) for x in c.targets + c.stated]
    assert [t["citation_no"] for t in c.targets] + [s["citation_no"] for s in c.stated] == list(range(1, 11))
    assert all(s.how == "search" and s.document_id == call.document_id for _, s in c.citations)
    assert c.mapping == {"search": 10}
    assert all(call.text[a:b] == x["quote"] for x in c.targets + c.stated for a, b in [x["span"]])
    by = {t["metric"] + ":" + t["kind"]: t for t in c.targets}
    # periods and figures as the model gave them, dated by the March year end; no item is a duplicate
    assert (by["capex:point"]["value"], by["capex:point"]["unit"], by["capex:point"]["target_start"],
            by["capex:point"]["target_end"]) == ("800", "inr", "2026-04-01", "2027-03-31")
    assert (by["ebitda_margin:range"]["low"], by["ebitda_margin:range"]["high"], by["ebitda_margin:range"]["value"]) == (
        "21", "23", None)
    assert (by["revenue_growth:floor"]["value"], by["revenue_growth:floor"]["target_start"],
            by["revenue_growth:floor"]["target_end"]) == ("10", "2026-10-01", "2027-03-31")
    assert (by["net_debt:ceiling"]["value"], by["net_debt:ceiling"]["metric_label"]) == ("0", "Net debt")
    vague = by["ebitda_margin:point"]
    assert (vague["value"], vague["unit"], vague["not_scored_reason"], vague["target_start"]) == (
        None, None, "vague_figure", "2027-04-01")
    assert all(t["duplicate_of"] is None for t in c.targets)
    assert [t["not_scored_reason"] for t in c.targets] == [None, None, None, None, None, "vague_figure", None, None]
    st = {s["metric"]: s for s in c.stated}
    assert (st["revenue"]["value"], st["revenue"]["period_start"], st["revenue"]["period_end"]) == (
        "4500", "2026-07-01", "2026-09-30")
    assert (st["ebitda_margin"]["value"], st["ebitda_margin"]["period_start"], st["ebitda_margin"]["period_end"]) == (
        "20.5", "2026-04-01", "2026-06-30")


def test_the_content_of_an_output_holds_the_kept_items_the_drops_and_no_other_text():
    secret = "Will capital expenditure be 900 crore in FY27?"                  # an analyst's words: never stored
    call = doc(*BIG)
    c = chk(call, [T("We expect revenue growth of 99 per cent in FY27.", value=99),         # not in the transcript
                   T(secret, metric="capex", value=900, unit="inr")])
    assert c.drops == {"not_found": 1, "analyst_turn": 1}
    doc_ = content(c, attempt=1, reply=None, metrics=METRICS)
    assert doc_["targets"] == [] and doc_["stated_figures"] == [] and doc_["no_targets"] is True and "problems" not in doc_
    assert doc_["dropped"] == {"analyst_turn": 1, "not_found": 1} and doc_["attempt"] == 1
    assert (doc_["map_version"], doc_["unattributed_targets"], doc_["stop_reason"]) == (1, True, None)
    assert doc_["quote_mapping"] == {"failed": 1, "search": 1}
    assert secret not in json.dumps(doc_) and "99 per cent" not in json.dumps(doc_)
    kept = chk(call, [T("Our effective tax rate will be 25 per cent in FY27.", metric="tax_rate", value=25)])
    doc2 = content(kept, attempt=2, reply=None, metrics=Metrics(3, False, METRICS.labels))
    (t,) = doc2["targets"]
    assert "no_targets" not in doc2 and t["quote"] == "Our effective tax rate will be 25 per cent in FY27."
    assert doc2["attempt"] == 2 and (doc2["map_version"], doc2["unattributed_targets"]) == (3, False)
    assert all(len(x["quote"]) <= ge.MAX_QUOTE for x in doc2["targets"] + doc2["stated_figures"])


def test_an_empty_answer_is_empty_with_no_citation_and_is_not_a_problem():
    c = chk(one(GROWTH))
    assert c.ok and c.empty and c.citations == [] and c.management_targets == 0
    assert content(c, attempt=1, reply=None, metrics=METRICS)["no_targets"] is True


# ---------------------------------------------------------------------------------------------- not JSON
class FakeReply:
    stop_reason = "end_turn"

    def __init__(self, text: str):
        self.text = text

    def json(self):
        return json.loads(self.text)


@pytest.mark.parametrize("answer", [None, [], "text", {"targets": []}, {"stated_figures": []},
                                    {"targets": {}, "stated_figures": []}, {"targets": [], "stated_figures": "none"}])
def test_a_reply_that_is_not_the_schemas_object_is_a_problem_to_retry_not_an_empty_answer(answer):
    c = check(answer, one(GROWTH), METRICS)
    assert not c.ok and c.reasons() == ["the reply isn't the schema's JSON ({targets: [...], stated_figures: [...]})"]
    assert c.note().endswith("stated_figures: [...]}).")
    doc_ = content(c, attempt=1, reply=None, metrics=METRICS)
    assert doc_["problems"] == c.reasons() and "no_targets" not in doc_


def test_answer_of_reads_the_json_and_gives_none_for_text():
    assert ge.answer_of(FakeReply('{"targets": [], "stated_figures": []}')) == {"targets": [], "stated_figures": []}
    assert ge.answer_of(FakeReply("Here are the targets: none")) is None


# ---------------------------------------------------------------------------------------------- whose turn it is
def test_what_counts_as_a_first_person_statement():
    for text in ("We expect loan growth of 15 per cent.", "we'll repay it", "We’re aiming for 15 per cent", "We've set 15",
                 "We'd like 15 per cent", "our margin will be 15", "That is ours", "We ourselves expect 15", "OUR target is 15",
                 "He said we", "growth, we think, is 15 per cent"):
        assert passes_content_test(text), text
    for text in ("Can we expect loan growth of 15 per cent?", "Wearable devices will grow 30 per cent.",
                 "The loan growth will be 15 per cent.", "The answer is 12 per cent", "Somewhere new, anywhere", "Newer ones",
                 "Hours of work", "We expect 15 per cent, don't we?", "Ours? No", "", "Twelve per cent"):
        assert not passes_content_test(text), text


@pytest.mark.parametrize("role, sentence, dropped", [
    ("analyst", "We expect revenue growth of 14 per cent in FY27.", "analyst_turn"),
    ("moderator", "We expect revenue growth of 14 per cent in FY27.", "moderator_turn"),
    ("unknown", "Can we expect revenue growth of 14 per cent in FY27?", "content_test"),
    ("unknown", "The revenue growth will be 14 per cent in FY27.", "content_test"),
    ("unknown", "Wearable devices drive revenue growth of 14 per cent in FY27.", "content_test"),
])
def test_a_target_is_kept_only_if_it_lies_in_a_management_turn_or_passes_the_content_test(role, sentence, dropped):
    c = chk(doc((RAO, role, sentence)), [T(sentence)])
    assert c.empty and c.drops == {dropped: 1}


@pytest.mark.parametrize("sentence", ["We expect revenue growth of 14 per cent in FY27.",
                                      "We're targeting revenue growth of 14 per cent in FY27.",
                                      "Our revenue growth will be 14 per cent in FY27.",
                                      "We'll see revenue growth of 14 per cent in FY27."])
def test_a_first_person_target_in_a_turn_whose_role_couldnt_be_read_goes_to_the_separate_list(sentence):
    c = chk(doc(("Participant A", "unknown", sentence)), [T(sentence)])
    (t,) = c.targets
    assert t["speaker_role"] == "unknown" and not c.drops and c.management_targets == 0
    assert t["not_scored_reason"] is None                                    # never scored is the batch's to set (#177)


def test_a_stated_figure_is_management_only():
    sentence = "Revenue from operations grew to 4,500 crore in Q2 FY27 and we are pleased."
    for role, why in (("analyst", "analyst_turn"), ("moderator", "moderator_turn"), ("unknown", "unknown_turn_stated")):
        c = chk(doc((RAO, role, sentence)), stated=[S(sentence)])
        assert c.empty and c.drops == {why: 1}, role
    c = chk(doc((RAO, "management", sentence)), stated=[S(sentence)])
    assert len(c.stated) == 1 and not c.drops


def test_the_switch_off_drops_a_separate_list_target_and_counts_it_but_leaves_management_alone():
    sentence = "We expect revenue growth of 14 per cent in FY27."
    call = doc((RAO, "management", sentence), ("Participant A", "unknown", "We expect loan growth of 15 per cent in FY27."))
    off = Metrics(1, False, METRICS.labels)
    c = chk(call, [T(sentence), T("We expect loan growth of 15 per cent in FY27.", metric="advances_growth", value=15)],
            metrics=off)
    assert [t["metric"] for t in c.targets] == ["revenue_growth"] and c.drops == {"unknown_turn_off": 1}
    on = chk(call, [T(sentence), T("We expect loan growth of 15 per cent in FY27.", metric="advances_growth", value=15)])
    assert [t["speaker_role"] for t in on.targets] == ["management", "unknown"] and not on.drops


def test_a_quote_across_two_turns_or_with_a_marker_in_it_or_outside_every_turn_is_dropped():
    call = doc((RAO, "management", "We expect revenue growth of 14 per cent in FY27."),
               ("Anil Shah", "analyst", "Will the margin be 21 per cent in FY27?"))
    across = "in FY27. Anil Shah: Will the margin be 21 per cent in FY27?"       # the end of one turn and the start of the next
    marked = "[management · Ravi Rao] We expect revenue growth of 14 per cent in FY27."
    c = chk(call, [T(across), T(marked)])
    assert c.empty and c.drops == {"across_turns": 2}
    # a turn that isn't in the stored turns is outside every turn
    gone = replace(call, turns=tuple(t for t in call.turns if t.speaker != RAO))
    assert chk(gone, [T("We expect revenue growth of 14 per cent in FY27.")]).drops == {"outside_turn": 1}


def test_a_quote_the_transcript_doesnt_hold_exactly_once_is_dropped():
    sentence = "We expect revenue growth of 14 per cent in FY27."
    call = doc((RAO, "management", sentence + " " + sentence), (IYER, "management", "We expect growth."))
    c = chk(call, [T(sentence), T("We expect revenue growth of about 14 per cent in FY27."), T("   "), T(None)])
    assert c.empty and c.drops == {"not_found": 2, "malformed": 2}
    assert c.mapping == {"failed": 2}
    # whitespace and line breaks in a quote are collapsed, as for every quote
    spaced = chk(one(sentence), [T("We expect  revenue growth\nof 14 per cent in FY27.")])
    assert len(spaced.targets) == 1 and spaced.targets[0]["quote"] == sentence
    # a curly apostrophe in a quote is the straight one of the stored text (the transcript is normalised before it is stored)
    stored = "We're targeting revenue growth of 14 per cent in FY27."
    curly = chk(one(stored), [T(stored.replace("'", "\u2019"))])
    assert len(curly.targets) == 1 and curly.targets[0]["quote"] == stored


def test_a_quote_over_400_code_points_is_dropped_and_one_of_exactly_400_stands():
    long = "We expect capital expenditure of 750 crore in FY28 " + "w" * 500
    call = one(long)
    for n, kept in ((400, True), (401, False)):
        quote = long[:n]
        c = chk(call, [T(quote, metric="capex", value=750, unit="inr", fiscal_year=2028)])
        assert bool(c.targets) is kept, n
        assert c.drops == ({} if kept else {"too_long": 1})
        if kept:
            assert len(c.targets[0]["quote"]) == 400 and c.targets[0]["span"][1] - c.targets[0]["span"][0] == 400
    stated = chk(one("Revenue was " + "w" * 500 + " 4,500 crore in Q2 FY27."), [S("Revenue was " + "w" * 500 + " 4,500 crore")])
    assert stated.empty and stated.drops == {"too_long": 1}


STATED = "Revenue was 4,500 crore in Q2 FY27."


def _as_said(kind: str, field: str, value: str) -> ge.Checked:
    """One item of `kind` ("target" or "stated") whose `field` is `value`, everything else valid."""
    if kind == "target":
        return chk(one(GROWTH), [T(GROWTH, **{field: value})])
    return chk(one(STATED), stated=[S(STATED, **{field: value})])


@pytest.mark.parametrize("kind", ["target", "stated"])
@pytest.mark.parametrize("field", ["metric_text", "period_text", "scope_text"])
def test_an_as_said_field_over_80_characters_drops_its_item_and_one_of_exactly_80_stands(kind, field):
    assert ge.MAX_AS_SAID == 80
    for n, kept in ((1, True), (79, True), (80, True), (81, False), (200, False), (300, False)):
        value = "w" * n
        c = _as_said(kind, field, value)
        items = c.targets if kind == "target" else c.stated
        assert bool(items) is kept, (field, n)
        assert c.drops == ({} if kept else {"text_too_long": 1}), (field, n)
        assert c.empty is (not kept)
        if kept:
            assert items[0][field] == value                      # stored as said, never cut


def test_an_item_with_several_long_fields_is_counted_once_and_a_long_one_never_becomes_a_cut_one():
    long = "w" * 150
    c = chk(one(GROWTH), [T(GROWTH, metric_text=long, period_text=long, scope_text=long)])
    assert c.empty and c.drops == {"text_too_long": 1}
    c = chk(one(STATED), stated=[S(STATED, metric_text=long, period_text=long, scope_text=long)])
    assert c.empty and c.drops == {"text_too_long": 1}


def test_the_80_characters_are_counted_as_stored_trimmed_with_spaces_joined_and_in_code_points():
    spaced = "a" * 40 + "   \n " + "b" * 39                    # 80 characters once the spaces are joined, 84 as sent
    c = chk(one(GROWTH), [T(GROWTH, metric_text=f"  {spaced}  ")])
    assert c.drops == {} and c.targets[0]["metric_text"] == "a" * 40 + " " + "b" * 39 and len(c.targets[0]["metric_text"]) == 80
    c = chk(one(GROWTH), [T(GROWTH, metric_text="a" * 40 + "   " + "b" * 40)])         # 81 once joined
    assert c.empty and c.drops == {"text_too_long": 1}
    astral = "\U0001d538"                                       # one code point, two UTF-16 units: 80 of them stand, 81 do not
    for n, kept in ((80, True), (81, False)):
        c = chk(one(GROWTH), [T(GROWTH, scope_text=astral * n)])
        assert bool(c.targets) is kept and c.drops == ({} if kept else {"text_too_long": 1}), n


def test_an_empty_or_missing_as_said_field_is_not_a_length_problem_and_a_dropped_item_leaves_the_others_and_their_numbering():
    c = chk(one(GROWTH), [T(GROWTH, period_text="", scope_text=None)])
    assert c.drops == {} and c.targets[0]["period_text"] is None and c.targets[0]["scope_text"] is None
    both = doc((RAO, "management", f"{GROWTH} {STATED}"))
    c = chk(both, [T(GROWTH, scope_text="w" * 81)], [S(STATED)])
    assert c.drops == {"text_too_long": 1} and not c.targets and len(c.stated) == 1
    assert [s["citation_no"] for s in c.stated] == [1] and len(c.citations) == 1
    c = chk(both, [T(GROWTH)], [S(STATED, metric_text="w" * 81)])
    assert c.drops == {"text_too_long": 1} and len(c.targets) == 1 and not c.stated
    assert [t["citation_no"] for t in c.targets] == [1] and len(c.citations) == 1


def test_an_item_that_fails_an_earlier_check_in_the_common_part_keeps_that_reason_over_a_long_field():
    c = chk(one(GROWTH), [T(GROWTH, metric="not_a_metric", metric_text="w" * 90)])
    assert c.drops == {"metric": 1}
    c = chk(one(GROWTH), [T(GROWTH, metric_text="", scope_text="w" * 90)])
    assert c.drops == {"malformed": 1}


def test_the_cap_is_in_the_check_not_the_schema_which_is_part_of_every_requests_hash():
    # Structured outputs take no length limits (ft/ai/client.py, load_schema), and a schema change would change every
    # request's input hash, so a call already extracted would be asked again. The 80 is held by the check alone.
    for part in (SCHEMA["properties"]["targets"]["items"], SCHEMA["properties"]["stated_figures"]["items"]):
        assert "maxLength" not in json.dumps(part["properties"])


def test_the_new_drop_reason_is_listed_with_a_label_that_names_the_cap():
    assert "text_too_long" in ge.DROPS and "80" in ge.DROPS["text_too_long"]


def test_a_quote_is_counted_in_code_points_not_in_utf16_units():
    pound = "We expect capital expenditure of 750 crore in FY28 ₹" + "😀" * 400                # 400 emoji: 800 UTF-16 units
    call = one(pound)
    quote_400 = pound[:400]
    assert len(quote_400) == 400 and len(quote_400.encode("utf-16-le")) // 2 > 400
    c = chk(call, [T(quote_400, metric="capex", value=750, unit="inr", fiscal_year=2028)])
    assert len(c.targets) == 1 and c.targets[0]["span"] == [call.text.index(quote_400), call.text.index(quote_400) + 400]


# ---------------------------------------------------------------------------------------------- figures and units
@pytest.mark.parametrize("quote, change, kept", [
    ("We expect revenue growth of 14 per cent in FY27.", {"value": 14}, True),
    ("We expect revenue growth of 14 per cent in FY27.", {"value": 15}, False),
    ("We expect revenue growth of 14.5 per cent in FY27.", {"value": 14}, False),
    ("We expect revenue growth of 14.5 per cent in FY27.", {"value": 14.5}, True),
    ("We expect revenue growth between 12 and 14 per cent in FY27.", {"kind": "range", "value": None, "low": 12, "high": 14}, True),
    ("We expect revenue growth between 12 and 14 per cent in FY27.", {"kind": "range", "value": None, "low": 12, "high": 15}, False),
    ("We expect double-digit revenue growth in FY27.", {"kind": "floor", "value": 10}, True),
    ("We expect Double Digit revenue growth in FY27.", {"kind": "floor", "value": 10}, True),
    ("We expect double-digit revenue growth in FY27.", {"kind": "floor", "value": 12}, False),
    ("We expect double-digit revenue growth in FY27.", {"kind": "point", "value": 10}, False),
    ("We expect revenue growth of at least 10 per cent in FY27.", {"kind": "floor", "value": 10}, True),
])
def test_every_figure_must_be_in_the_quote_as_written(quote, change, kept):
    c = chk(one(quote), [T(quote, **change)])
    assert bool(c.targets) is kept
    assert c.drops == ({} if kept else {"figure_not_in_quote": 1})


def test_net_debt_free_is_a_ceiling_of_zero_on_net_debt_and_nothing_else():
    sentence = "We will be net debt free by the end of FY27."
    ok = T(sentence, metric="net_debt", metric_text="net debt", kind="ceiling", value=0, unit="inr", growth_basis="level")
    assert len(chk(one(sentence), [ok]).targets) == 1
    for bad in ({"metric": "capex"}, {"kind": "point"}, {"value": 1}):
        c = chk(one(sentence), [ok | bad])
        assert c.empty, bad
    assert chk(one(sentence), [ok | {"metric": "capex"}]).drops == {"figure_not_in_quote": 1}


@pytest.mark.parametrize("sentence, change, why", [
    ("We will spend capital expenditure of 800 crore in FY27.", {"metric": "capex", "value": 800, "unit": "inr"}, None),
    ("We will spend capital expenditure of 800 Cr in FY27.", {"metric": "capex", "value": 800, "unit": "inr"}, None),
    ("We will spend capital expenditure of 800 crore in FY27.", {"metric": "capex", "value": 800, "unit": "pct"}, "unit_metric"),
    ("We will spend capital expenditure of 80 lakh in FY27.", {"metric": "capex", "value": 80, "unit": "inr"}, "unit_scale"),
    ("We will spend capital expenditure of 800 million in FY27.", {"metric": "capex", "value": 800, "unit": "inr"}, "unit_scale"),
    ("We will spend capital expenditure of 800 in FY27.", {"metric": "capex", "value": 800, "unit": "inr"}, "unit_scale"),
    ("We will spend capital expenditure of 800 crore, about 100 million dollars, in FY27.",
     {"metric": "capex", "value": 800, "unit": "inr"}, "unit_scale"),
    ("We will spend capital expenditure of 100 million dollars in FY27.", {"metric": "capex", "value": 100, "unit": "other"}, None),
    ("We expect revenue growth of 14 per cent in FY27.", {"unit": "inr"}, "unit_metric"),
    ("We expect EBITDA margin up 150 bps in FY27.", {"metric": "ebitda_margin", "value": 150, "unit": "pct"}, "unit_scale"),
    ("We expect EBITDA margin up 150 basis points in FY27.", {"metric": "ebitda_margin", "value": 150, "unit": "pct"}, "unit_scale"),
    ("We expect EBITDA margin up 150 bps in FY27.", {"metric": "ebitda_margin", "value": 150, "unit": "other"}, "unit_metric"),
    ("We expect a margin of 21 per cent in FY27.", {"metric": "ebitda_margin", "value": 21, "unit": "pct"}, None),
    ("We expect revenue growth of 14 per cent in FY27.", {"unit": "dollars"}, "malformed"),
    ("We expect revenue growth of 14 per cent in FY27.", {"unit": None}, "shape"),
])
def test_units_mean_what_they_say_crore_per_cent_or_other(sentence, change, why):
    c = chk(one(sentence), [T(sentence, **change)])
    assert c.drops == ({} if why is None else {why: 1}) and bool(c.targets) is (why is None)


def test_a_net_debt_free_ceiling_needs_no_crore_and_a_metric_outside_the_map_is_dropped():
    sentence = "We will be net debt free by the end of FY27."
    c = chk(one(sentence), [T(sentence, metric="net_debt", kind="ceiling", value=0, unit="inr", growth_basis="level"),
                            T(sentence, metric="free_cash_flow")])
    assert len(c.targets) == 1 and c.drops == {"metric": 1}
    c = chk(one(sentence), [T(sentence, metric_text="")])
    assert c.drops == {"malformed": 1}


def test_the_other_metric_is_kept_for_a_target_with_its_own_words_and_never_for_a_stated_figure():
    sentence = "We expect a new plant to add 40 per cent to our volumes in FY27."
    c = chk(one(sentence), [T(sentence, metric="other", metric_text="volumes from the new plant", value=40, unit="other")],
            [S(sentence, metric="other", value=40, unit="other")])
    assert [t["metric"] for t in c.targets] == ["other"] and c.targets[0]["not_scored_reason"] == "no_filed_figure"
    assert c.stated == [] and c.drops == {"metric": 1}


# ---------------------------------------------------------------------------------------------- shape
@pytest.mark.parametrize("sentence, change, why", [
    (GROWTH, {"low": 12}, "shape"),                                           # a point has one figure
    (GROWTH, {"kind": "range"}, "shape"),                                     # a range has a low and a high
    ("We expect revenue growth between 12 and 10 per cent in FY27.", {"kind": "range", "value": None, "low": 12, "high": 10},
     "shape"),
    ("We expect revenue growth between 12 and 14 per cent in FY27.", {"kind": "range", "value": 12, "low": 12, "high": 14},
     "shape"),
    ("We expect revenue growth to rise in FY27.", {"kind": "direction", "value": 14, "direction": "increase"}, "shape"),
    ("We expect revenue growth to rise in FY27.", {"kind": "direction", "value": None, "direction": None}, "shape"),
    ("We expect revenue growth to rise in FY27.", {"kind": "direction", "value": None, "direction": "up"}, "shape"),
    (GROWTH, {"kind": "guess"}, "malformed"),
    (GROWTH, {"comparison": "decade_ago"}, "malformed"),
    (GROWTH, {"period_kind": "decade"}, "malformed"),
    (GROWTH, {"basis": "pro_forma"}, "malformed"),
    (GROWTH, {"growth_basis": "cagr2"}, "malformed"),
])
def test_a_target_whose_kind_figures_and_direction_dont_fit_together_is_dropped(sentence, change, why):
    c = chk(one(sentence), [T(sentence, **change)])
    assert c.empty and c.drops == {why: 1}


def test_a_direction_target_keeps_its_word_and_no_figure_and_a_vague_one_has_none():
    sentence = "We expect revenue growth to rise in FY27."
    (t,) = chk(one(sentence), [T(sentence, kind="direction", value=None, unit="pct", direction="increase")]).targets
    assert (t["kind"], t["direction"], t["value"], t["unit"], t["not_scored_reason"]) == ("direction", "increase", None, None, None)
    vague = "We expect revenue growth in the mid-teens in FY27."
    (v,) = chk(one(vague), [T(vague, value=None, unit="pct")]).targets
    assert (v["value"], v["unit"], v["direction"], v["not_scored_reason"]) == (None, None, None, "vague_figure")


@pytest.mark.parametrize("change, reason", [
    ({"metric": "other"}, "no_filed_figure"),
    ({"metric": "capex", "unit": "other"}, "currency"),
    ({"scope_text": "the export business"}, "scope"),
    ({"period_kind": "undated"}, "undated"),
    ({"value": None}, "vague_figure"),
    ({"metric": "other", "scope_text": "exports"}, "no_filed_figure"),         # the first reason in the list wins
    ({"unit": "other"}, None),                                                  # a unit of "other" is a currency only for an amount
    ({"scope_text": "exports", "period_kind": "undated"}, "scope"),
    ({"kind": "direction", "value": None}, None),
    ({"kind": "range", "value": None, "low": "12"}, None),
    ({}, None),
])
def test_the_reason_a_statement_alone_decides_is_the_first_in_the_list(change, reason):
    base = {"metric": "revenue_growth", "unit": "pct", "scope_text": None, "period_kind": "year", "kind": "point",
            "value": "14", "low": None}
    assert ge.not_scored_reason(base | change) == reason


def test_the_reasons_a_stored_target_carries_come_from_the_check_itself():
    sentence = "We expect a new plant to add 40 per cent to our volumes in FY27."
    cases = {"no_filed_figure": T(sentence, metric="other", metric_text="plant volumes", value=40, unit="other"),
             "currency": T("We will spend capital expenditure of 100 million dollars in FY27.", metric="capex", value=100,
                           unit="other", growth_basis="level"),
             "scope": T(GROWTH, scope_text="the export business"),
             "undated": T(GROWTH, period_kind="undated", fiscal_year=None, period_text="in time"),
             "vague_figure": T("We expect revenue growth in the mid-teens in FY27.", value=None)}
    mid = "We expect revenue growth in the mid-teens in FY27."
    call = doc((RAO, "management", f"{sentence} We will spend capital expenditure of 100 million dollars in FY27. {GROWTH} {mid}"))
    for reason, item in cases.items():
        (t,) = chk(call, [item]).targets
        assert t["not_scored_reason"] == reason, reason


# ---------------------------------------------------------------------------------------------- periods of a statement
def test_a_target_must_end_after_the_call_and_a_stated_figure_on_or_before_it():
    past = "Last year we spent capital expenditure of 700 crore in FY26."
    sentence = "Revenue will be 5,000 crore in FY27. Revenue was 4,500 crore in Q2 FY27. Revenue was 1,000 crore in Q3 FY27."
    call = doc((RAO, "management", past + " " + sentence))
    c = chk(call, [T(past, metric="capex", value=700, unit="inr", fiscal_year=2026, growth_basis="level"),
                   T("Revenue was 4,500 crore in Q2 FY27.", metric="revenue", value=4500, unit="inr", period_kind="quarter",
                     part=2, growth_basis="level")],
            [S("Revenue will be 5,000 crore in FY27.", value=5000, period_kind="year", part=None),
             S("Revenue was 1,000 crore in Q3 FY27.", value=1000, part=3),
             S("Revenue was 4,500 crore in Q2 FY27.")])
    assert c.targets == [] and [s["period_end"] for s in c.stated] == ["2026-09-30"]
    assert c.drops == {"period_over": 2, "period_after_call": 2}
    # a period ending on the day of the call: a target's has ended, a stated figure's is reported
    day = date(2026, 9, 30)
    same = doc((RAO, "management", "Revenue was 4,500 crore in Q2 FY27."), day=day)
    assert chk(same, [T("Revenue was 4,500 crore in Q2 FY27.", metric="revenue", value=4500, unit="inr", period_kind="quarter",
                        part=2)]).drops == {"period_over": 1}
    assert len(chk(same, stated=[S("Revenue was 4,500 crore in Q2 FY27.")]).stated) == 1


def test_a_period_the_model_couldnt_pin_to_a_fiscal_year_is_undated_never_guessed():
    c = chk(one(GROWTH), [T(GROWTH, fiscal_year=None, period_text="next year")])
    (t,) = c.targets
    assert (t["period_kind"], t["target_start"], t["target_end"], t["not_scored_reason"]) == ("undated", None, None, "undated")
    (u,) = chk(one(GROWTH), [T(GROWTH, period_kind="undated", fiscal_year=2027, part=1)]).targets
    assert (u["period_kind"], u["target_start"]) == ("undated", None)


@pytest.mark.parametrize("change", [
    {"fiscal_year": 2099}, {"fiscal_year": 1990}, {"period_kind": "quarter", "part": None}, {"period_kind": "quarter", "part": 5},
    {"period_kind": "year", "part": 2}, {"period_kind": "multi_year", "start_fiscal_year": None},
    {"period_kind": "multi_year", "start_fiscal_year": 2027}, {"period_kind": "multi_year", "start_fiscal_year": 2090},
])
def test_a_period_that_doesnt_resolve_or_isnt_plausible_is_dropped(change):
    c = chk(one(GROWTH), [T(GROWTH, **change)])
    assert c.empty and c.drops == {"period_invalid": 1}


def test_a_multi_year_target_runs_from_the_first_fiscal_year_to_the_last():
    sentence = "We target revenue growth of 14 per cent from FY27 to FY30 on average."
    (t,) = chk(one(sentence), [T(sentence, period_kind="multi_year", fiscal_year=2030, start_fiscal_year=2027,
                                  growth_basis="cagr", period_text="FY27 to FY30")]).targets
    assert (t["period_kind"], t["target_start"], t["target_end"], t["growth_basis"]) == (
        "multi_year", "2026-04-01", "2030-03-31", "cagr")


def test_the_check_dates_a_stated_figure_of_a_non_march_company_by_its_own_year():
    sentence = "Revenue was 4,500 crore in Q3 FY26."
    s = S(sentence, fiscal_year=2026, part=3)
    (a,) = chk(one(sentence, m=3), stated=[s]).stated                         # March: Oct-Dec 2025
    (b,) = chk(one(sentence, m=12), stated=[s]).stated                        # December: Jul-Sep 2026
    (c,) = chk(one(sentence, m=6), stated=[s]).stated                         # June: Jan-Mar 2026
    assert [(x["period_start"], x["period_end"]) for x in (a, b, c)] == [
        ("2025-10-01", "2025-12-31"), ("2026-07-01", "2026-09-30"), ("2026-01-01", "2026-03-31")]


# ---------------------------------------------------------------------------------------------- stated figures
def test_a_stated_figure_needs_a_value_a_unit_a_fiscal_year_and_a_period_the_table_has():
    sentence = "Revenue was 4,500 crore in Q2 FY27."
    call = one(sentence)
    for change, why in (({"value": None}, "shape"), ({"unit": None}, "shape"), ({"fiscal_year": None}, "malformed"),
                        ({"period_kind": "multi_year"}, "malformed"), ({"period_kind": "undated"}, "malformed"),
                        ({"period_kind": "quarter", "part": None}, "period_invalid"), ({"value": 4400}, "figure_not_in_quote"),
                        ({"unit": "pct"}, "unit_metric"), ({"basis": "pro_forma"}, "malformed"),
                        ({"growth_basis": "cagr"}, "malformed")):
        c = chk(call, stated=[S(sentence, **change)])
        assert c.empty and c.drops == {why: 1}, change
    (s,) = chk(call, stated=[S(sentence, basis="consolidated", scope_text="  the group  ")]).stated
    assert (s["basis"], s["scope_text"], s["metric_label"]) == ("consolidated", "the group", "Revenue")


def test_no_more_than_twelve_stated_figures_a_call_and_the_rest_are_counted():
    sentences = [f"Revenue was {4000 + i} crore in Q1 FY27." for i in range(14)]
    call = one(" ".join(sentences))
    c = chk(call, stated=[S(s, value=4000 + i, part=1, period_text="Q1 FY27") for i, s in enumerate(sentences)])
    assert len(c.stated) == ge.MAX_STATED == 12 and c.drops == {"over_limit": 2}
    assert [s["value"] for s in c.stated] == [str(4000 + i) for i in range(12)]
    assert [s["citation_no"] for s in c.stated] == list(range(1, 13))


# ---------------------------------------------------------------------------------------------- duplicates
def test_a_second_statement_of_the_same_target_keeps_its_row_but_is_a_duplicate_of_the_first_in_the_call():
    a, b = "We expect revenue growth of 14 per cent in FY27.", "Revenue growth of 14 per cent in FY27 is what we expect."
    call = doc((RAO, "management", b + " " + a))                              # b is first in the transcript
    c = chk(call, [T(a), T(b)])
    first, second = c.targets
    assert first["quote"] == b and first["duplicate_of"] is None and first["not_scored_reason"] is None
    assert second["quote"] == a and second["duplicate_of"] == 0 and second["not_scored_reason"] == "duplicate"
    assert c.management_targets == 1                                          # duplicates aren't shown
    assert [t["citation_no"] for t in c.targets] == [1, 2]                    # each keeps its own citation


@pytest.mark.parametrize("change", [{"kind": "floor"}, {"basis": "standalone"}, {"growth_basis": "level"},
                                    {"fiscal_year": 2028}, {"period_kind": "half_year", "part": 2}, {"metric": "pat_growth"},
                                    {"scope_text": "the export business"}])
def test_targets_that_differ_in_kind_basis_period_metric_or_scope_are_not_duplicates(change):
    a, b = "We expect revenue growth of 14 per cent in FY27.", "We also expect revenue growth of 14 per cent in FY27."
    c = chk(doc((RAO, "management", a + " " + b)), [T(a), T(b, **change)])
    assert [t["duplicate_of"] for t in c.targets] == [None, None] and c.management_targets == 2


def test_an_undated_target_is_a_duplicate_only_of_one_with_the_same_words_for_its_period():
    a, b, c_ = "We expect revenue growth of 14 per cent soon.", "We expect revenue growth of 14 per cent soon too.", GROWTH
    call = doc((RAO, "management", f"{a} {b} {c_}"))
    undated = {"period_kind": "undated", "fiscal_year": None, "period_text": "soon"}
    c = chk(call, [T(a, **undated), T(b, **undated), T(c_)])
    assert [t["duplicate_of"] for t in c.targets] == [None, 0, None]
    other = chk(call, [T(a, **undated), T(b, **(undated | {"period_text": "later"}))])
    assert [t["duplicate_of"] for t in other.targets] == [None, None]


def test_a_target_in_the_separate_list_duplicating_a_management_one_goes_to_the_management_item_whatever_the_order():
    sentence = "We expect revenue growth of 14 per cent in FY27."
    call = doc(("Participant A", "unknown", sentence), (IYER, "management", "We also expect revenue growth of 14 per cent in FY27."))
    c = chk(call, [T(sentence), T("We also expect revenue growth of 14 per cent in FY27.")])
    assert [(t["speaker_role"], t["duplicate_of"]) for t in c.targets] == [("management", None), ("unknown", 0)]
    assert c.targets[1]["not_scored_reason"] == "duplicate" and c.management_targets == 1


def test_stated_figures_repeating_the_same_metric_period_and_basis_are_duplicates_of_the_first():
    a, b = "Revenue was 4,500 crore in Q2 FY27.", "Revenue of 4,500 crore in Q2 FY27 was a record."
    c = chk(one(b + " " + a), stated=[S(a), S(b)])
    assert [s["duplicate_of"] for s in c.stated] == [None, 0]
    other = chk(one(a + " " + b), stated=[S(a), S(b, basis="standalone")])
    assert [s["duplicate_of"] for s in other.stated] == [None, None]


# ---------------------------------------------------------------------------------------------- the stable reason phrases
def test_the_reason_phrase_table_matches_what_check_writes():
    (phrase,) = ge.REASON_PHRASES["format"]
    assert re.search(phrase, check("not json", one(GROWTH), METRICS).reasons()[0], re.IGNORECASE)


def test_a_v5_request_has_32000_max_tokens_and_a_v1_request_keeps_the_tasks_16000_and_its_input_hash():
    # #511: v5's own ceiling (MAX_TOKENS_OF). v1's requests are built as before, so production's stay unchanged.
    call = doc((RAO, "management", GROWTH))
    v1 = ge.guidance_request(call, PROMPT, SCHEMA)
    v5 = ge.guidance_request(call, load_prompt(ge.TASK, "v5"), load_schema(ge.TASK, "v5"))
    assert v1.max_tokens is None and v1.spec.max_tokens == 16_000
    assert v5.max_tokens == 32_000 and v5.spec.max_tokens == 32_000
    assert v5.params()["max_tokens"] == 32_000 and v1.params()["max_tokens"] == 16_000
    same = ge.guidance_request(call, PROMPT, SCHEMA)
    assert v1.input_sha256 == same.input_sha256 != v5.input_sha256


# ---------------------------------------------------------------------------------------------- the checks that came with v6 (#533)
def chk6(call: cs.CallDoc, targets=(), stated=()) -> ge.Checked:
    return check({"targets": list(targets), "stated_figures": list(stated)}, call, METRICS, version="v6")


@pytest.mark.parametrize("quote, figures", [
    ("over INR3,000 crores EBITDA", {3000}), ("Rs.9,000 crores", {9000}), ("Rs. 9,000 crores", {9000}), ("₹2,263 crores", {2263}),
    ("USD4.5 billion", {4.5}), ("US$4.5 billion", {4.5}), ("INR1,200 crores to INR1,400 crores", {1200, 1400}),
    ("Rs.500-Rs.600 crore", {500, 600}), ("INR 1,23,456 crore", {123456}),
    ("10%-12%", {10, 12}), ("400 crores-500 crores", {400, 500}), ("8-10%", {8, 10}), ("sub-10% growth", {10}),
    ("a margin of -5% in the quarter", {-5}), ("(−2.5%) then", {-2.5}), ("-3% at the start", {-3}),
    ("FY27 and Q3 and H2", set()), ("version x.5", set()), ("growth of 12.5 per cent in FY27", {12.5}),
    ("PINR3,000 isn't a currency code", set()), ("1.5x to 2x", {1.5, 2}),
])
def test_the_v6_reader_reads_a_figure_against_its_currency_code_and_a_ranges_second_figure(quote, figures):
    from decimal import Decimal
    assert ge.quote_figures(quote) == {Decimal(str(f)) for f in figures}


@pytest.mark.parametrize("quote", [
    "We expect revenue growth of 14 per cent in FY27.", "Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27.",
    "capex of ₹ 2,263 crores in FY'26", "between 17.5% and 18.5% for the year", "net debt below 1,00,000 crore by March 2027",
    "a range of 8-10% in H2", "at least 25 per cent, up from 21.3 per cent",
])
def test_the_v6_reader_reads_what_the_shared_reader_reads_where_no_code_or_range_dash_is_involved(quote):
    from ft.ai.citations import _numbers
    assert ge.quote_figures(quote) == set(_numbers(quote))


def test_a_figure_written_against_its_currency_code_is_dropped_under_v1_and_kept_under_v6():
    # #533's diagnosis: the shared reader reads "INR3,000" as 000, so v1 to v5 dropped the item (`figure_not_in_quote`).
    s = "We expect these assets to add over INR3,000 crores of revenue in FY27."
    t = T(s, metric="revenue", metric_text="revenue", kind="floor", value=3000, unit="inr", growth_basis="level")
    old, new = chk(one(s), [t]), chk6(one(s), [t])
    assert old.targets == [] and old.drops == {"figure_not_in_quote": 1}
    assert [(x["value"], x["unit"], x["not_scored_reason"]) for x in new.targets] == [("3000", "inr", None)] and not new.drops
    rng = "Capex will be INR1,200 crores to INR1,400 crores in FY27."
    r = T(rng, metric="capex", metric_text="capex", kind="range", value=None, low=1200, high=1400, unit="inr", growth_basis="level")
    assert chk(one(rng), [r]).drops == {"figure_not_in_quote": 1} and len(chk6(one(rng), [r]).targets) == 1
    # the rule itself is unchanged: a figure the quote doesn't hold is still dropped, and the artefact 000 is no figure
    assert chk6(one(s), [t | {"value": 3500}]).drops == {"figure_not_in_quote": 1}
    zero = "Net debt will be INR3,000 crores by FY27."
    z = T(zero, metric="net_debt", metric_text="net debt", kind="ceiling", value=0, unit="inr", growth_basis="level")
    assert chk6(one(zero), [z]).drops == {"figure_not_in_quote": 1}


def test_a_hyphenated_ranges_second_figure_is_read_under_v6():
    s = "We guide to EBITDA margin of 10%-12% in FY27."
    t = T(s, metric="ebitda_margin", metric_text="EBITDA margin", kind="range", value=None, low=10, high=12, growth_basis="level")
    assert chk(one(s), [t]).drops == {"figure_not_in_quote": 1}
    assert [(x["low"], x["high"]) for x in chk6(one(s), [t]).targets] == [("10", "12")]


@pytest.mark.parametrize("sentence, metric_text", [
    ("The revenue growth guidance for FY27 is 14 per cent in constant currency.", "revenue growth"),
    ("We expect 14 per cent growth in FY27.", "CC revenue growth"),
    ("Adjusted revenue growth will be 14 per cent in FY27.", "revenue growth"),
    ("Revenue growth excluding the divested unit will be 14 per cent in FY27.", "revenue growth"),
    ("Revenue growth ex-fuel will be 14 per cent in FY27.", "revenue growth"),
    ("Organic revenue growth will be 14 per cent in FY27.", "revenue growth"),
    ("This deal is expected to contribute 14 per cent revenue growth in FY27.", "revenue growth from the deal"),
    ("On a like-for-like basis revenue growth will be 14 per cent in FY27.", "revenue growth"),
])
def test_a_qualified_measure_under_a_listed_key_goes_to_other_under_v6_and_is_never_scored(sentence, metric_text):
    t = T(sentence, metric_text=metric_text)
    old, new = chk(one(sentence), [t]), chk6(one(sentence), [t])
    assert [(x["metric"], x["not_scored_reason"]) for x in old.targets] == [("revenue_growth", None)]       # v1: as before
    assert [(x["metric"], x["metric_label"], x["not_scored_reason"]) for x in new.targets] == [
        ("other", METRICS.labels["other"], "no_filed_figure")]
    assert new.targets[0]["metric_text"] == metric_text and new.moved == {"qualified_metric": 1} and not new.drops
    assert content(new, attempt=1, reply=None, metrics=METRICS)["moved"] == {"qualified_metric": 1}


def test_a_plain_measure_keeps_its_key_under_v6_and_v1s_content_has_no_moved_entry():
    for sentence in (GROWTH, "We expect revenue growth of 14 per cent in FY27, with 150 cc bikes leading.",
                     "We expect revenue growth of 14 per cent in FY27, as we execute the plan."):
        c = chk6(one(sentence), [T(sentence)])
        assert [(x["metric"], x["not_scored_reason"]) for x in c.targets] == [("revenue_growth", None)] and not c.moved
    assert "moved" not in content(chk(one(GROWTH), [T(GROWTH)]), attempt=1, reply=None, metrics=METRICS)


def test_a_qualified_stated_figure_is_dropped_under_v6_since_it_can_not_be_other():
    s = "Adjusted EBITDA margin was 21.3 per cent in Q2 FY27."
    st = S(s, metric="ebitda_margin", metric_text="adjusted EBITDA margin", value=21.3, unit="pct")
    assert len(chk(one(s), stated=[st]).stated) == 1
    c = chk6(one(s), stated=[st])
    assert c.stated == [] and c.drops == {"qualified_stated": 1} and "qualified_stated" in ge.DROPS


@pytest.mark.parametrize("scope, stored", [
    ("whole company", None), ("Whole Company", None), ("the company as a whole", None), ("company", None), ("the group", None),
    ("overall business", None), ("company-level", None), (None, None),
    ("India business", "India business"), ("the company's jewellery division", "the company's jewellery division"),
    ("group companies", "group companies"),
])
def test_whole_company_is_stored_as_no_scope_under_v6_and_a_part_keeps_its_name(scope, stored):
    c = chk6(one(GROWTH), [T(GROWTH, scope_text=scope)])
    assert [x["scope_text"] for x in c.targets] == [stored]
    assert c.targets[0]["not_scored_reason"] == (None if stored is None else "scope")
    if scope is not None and stored is None:
        assert c.moved == {"whole_company": 1}
        assert chk(one(GROWTH), [T(GROWTH, scope_text=scope)]).targets[0]["scope_text"] == scope       # v1: stored as given


def test_a_basis_point_or_non_crore_target_is_kept_as_other_with_the_unit_as_spoken():
    # Anand, 14 Nov 2026: prompt v6 keeps these under `other` (v1 to v5 said to leave them out). The 4 Nov unit rule in code
    # stays: under a listed key, basis points and a non-crore rupee amount are still dropped.
    bp = "We expect the restructuring impact to be 50 basis points in FY27."
    kept = chk6(one(bp), [T(bp, metric="other", metric_text="restructuring impact", value=50, unit="other", growth_basis=None)])
    assert [(x["metric"], x["value"], x["unit"], x["not_scored_reason"]) for x in kept.targets] == [("other", "50", "other", "no_filed_figure")]
    margin = "We expect EBITDA margin to improve by 50 basis points in FY27."
    assert chk6(one(margin), [T(margin, metric="ebitda_margin", metric_text="EBITDA margin", value=50)]).drops == {"unit_scale": 1}
    bn = "We expect bookings of 2.5 billion dollars in FY27."
    k2 = chk6(one(bn), [T(bn, metric="other", metric_text="bookings", value=2.5, unit="other", growth_basis="level")])
    assert [(x["value"], x["unit"]) for x in k2.targets] == [("2.5", "other")]


def test_a_v6_request_has_24000_max_tokens_and_its_own_input_hash():
    call = doc((RAO, "management", GROWTH))
    v5 = ge.guidance_request(call, load_prompt(ge.TASK, "v5"), load_schema(ge.TASK, "v5"))
    v6 = ge.guidance_request(call, load_prompt(ge.TASK, "v6"), load_schema(ge.TASK, "v6"))
    assert v6.max_tokens == 24_000 and v6.params()["max_tokens"] == 24_000 and v6.input_sha256 != v5.input_sha256
    assert "WHOSE FIGURE, AND WHICH FIGURE" in v6.params()["system"] and "guidance_extraction v6" not in v6.params()["system"]
