"""The reason categoriser (issue #134, Accuracy 2): every category is reached by running the REAL check functions (and the
batch task's own Call) on crafted bad replies, so a reworded check message fails here instead of drifting into `other`."""
import re

import pytest
from ai_fakes import bullets, cite, good_summary
from test_ai_extraction import DISSENT, GOOD, STANCE, TEXT, proj, stance
from test_ai_summaries import FOMC_B, NEW, reply, sent, summary_request

from ft.ai import extraction, summaries
from ft.ai.summaries import check
from ft.engines.document.text import sentence_spans
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import Call
from ft.quality import reasons


def rejected(problems: list[str], prefix: str = "rejected: ") -> str:
    """The status_reason `ai_batch._store` writes for a rejected reply."""
    return (prefix + "; ".join(problems))[:1000]


def category(problems: list[str]) -> str:
    cat, matched = reasons.categorise("rejected", rejected(problems))
    assert matched, problems
    return cat


# ---------------------------------------------------------------------------------------------- summaries
def summary_reasons(blocks) -> list[str]:
    c = check("doc_summary", reply(blocks), summary_request().sources)
    assert c.reasons()
    return c.reasons()


def test_a_bullet_without_a_verified_citation_is_citation():
    fake = {"type": "char_location", "cited_text": "The Committee raised rates sharply.", "document_index": 0,
            "start_char_index": 0, "end_char_index": 35}
    blocks = good_summary([(NEW.title, FOMC_B)]) + [{"type": "text", "text": "\n- "},
                                                      {"type": "text", "text": "Invented point", "citations": [fake]}]
    assert category(summary_reasons(blocks)) == "citation"


def test_a_number_outside_the_cited_passage_is_numbers():
    s, e = next((s, e) for s, e in sentence_spans(FOMC_B) if "1/4" in FOMC_B[s:e])
    items = [("The Committee lowered the target range by 25 basis points.", [cite(FOMC_B, s, e, 0)])] + [
        (f"Inflation point {i}.", [cite(FOMC_B, *sent(FOMC_B, 2), 0)]) for i in range(2)]
    assert category(summary_reasons(bullets(items))) == "numbers"


def test_the_bullet_count_stray_text_and_no_bullets_are_format():
    items = [(FOMC_B[s:e], [cite(FOMC_B, s, e, 0)]) for s, e in sentence_spans(FOMC_B)[:2]]
    assert category(summary_reasons(bullets(items))) == "format"                       # 2 bullets, not 3 to 6
    assert category(summary_reasons([{"type": "text", "text": "No list at all."}])) == "format"
    stray = bullets(items + [(FOMC_B[s:e], [cite(FOMC_B, s, e, 0)]) for s, e in sentence_spans(FOMC_B)[2:5]])
    assert category(summary_reasons(stray + [{"type": "text", "text": "\nand a closing remark"}])) == "format"


def test_a_citation_and_a_number_problem_together_take_the_citation_first():
    assert category(["bullet 1: numbers not in its cited passages: 25", "bullet 2: no verified citation"]) == "citation"
    assert category(["3 bullets, not 4 to 6", "bullet 1: numbers not in its cited passages: 25"]) == "numbers"


# ---------------------------------------------------------------------------------------------- extraction
def test_stance_problems():
    c = stance({"stance": "neutral", "quote": "The MPC decided to change the stance to neutral."})
    assert category(c.problems) == "citation"                                          # not found exactly once
    assert category(stance({"stance": "neutral", "quote": DISSENT}).problems) == "citation"   # doesn't state it
    for bad in (None, [], {"stance": "neutral"}, {"stance": "hawkish", "quote": STANCE}):
        assert category(stance(bad).problems) == "format", bad


@pytest.mark.parametrize("change, expected", [
    ({"quote": "0.5 per cent in the last quarter", "target_label": "Q4 2040-41", "target_start": "2041-01-01",
      "target_end": "2041-03-31", "value": 0.5}, "numbers"),
    ({"value": 4.7}, "numbers"),
    ({"target_end": "2040-09-29"}, "format"),
    ({"target_label": "Q2 FY41"}, "format"),
    ({"low": 4.5, "high": 4.7}, "format"),
    ({"quote": "Q2 4.7 per cent"}, "citation"),
    ({"target_label": "Q2 2019-20", "target_start": "2019-07-01", "target_end": "2019-09-30"}, "numbers"),
    ({"variable": "gdp"}, "numbers"),
])
def test_projection_problems(change, expected):
    c = proj({"projections": [GOOD[0], {**GOOD[1], **change}]})
    assert c.problems, change
    assert category(c.problems) == expected, c.problems


def test_projection_list_shape_problems():
    elsewhere = "Liquidity in the banking system stayed in surplus"
    assert category(proj({"projections": [GOOD[1]]}, sentences=((0, TEXT.index(elsewhere)),)).problems) == "citation"
    assert category(proj({"projections": []}).problems) == "format"
    assert category(proj({"projections": [GOOD[1], GOOD[1]]}).problems) == "format"
    assert category(proj(None).problems) == "format"
    assert category(proj({"projections": [7]}).problems) == "format"


# ---------------------------------------------------------------------------------------------- the batch task's own reasons
def test_a_reply_that_stopped_early_is_stopped():
    call = Call("c", None, 1, failed="max_tokens")
    assert category(call.reasons()) == "stopped"
    assert reasons.categorise("rejected", rejected(call.reasons(), "retried: ")) == ("stopped", True)


def test_a_hold_is_held_for_review_only_while_it_is_a_draft():
    c = stance({"stance": "other", "quote": "growth-supportive"})
    assert c.hold
    assert reasons.categorise("draft", "held: " + c.hold) == ("held_review", True)
    assert reasons.categorise("published", None) == (None, True)
    assert reasons.categorise("draft", None) == (None, True)                            # in flight: no category
    assert reasons.categorise("superseded", "superseded by output 7") == (None, True)


def test_the_tasks_own_reasons_are_other_and_matched_but_a_new_phrase_is_not():
    assert reasons.categorise("rejected", "stale: the target's input changed before its batch was collected") == ("other", True)
    assert reasons.categorise("rejected", "retried: the reply couldn't be stored (DataError)") == ("other", True)
    assert reasons.categorise("rejected", "rejected: something nobody wrote a phrase for") == ("other", False)
    assert reasons.categorise("rejected", None) == ("other", False)


def test_the_phrase_tables_use_only_known_categories_and_compile():
    for table in (summaries.REASON_PHRASES, extraction.REASON_PHRASES, ai_batch.REASON_PHRASES):
        assert set(table) <= set(reasons.PRIORITY)
        for patterns in table.values():
            for p in patterns:
                re.compile(p)
    assert set(reasons.CATEGORIES) == {*reasons.PRIORITY, "other"}
