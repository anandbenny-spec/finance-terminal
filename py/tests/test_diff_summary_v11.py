"""#445, round 10 (Anand's decision of 13 Nov 2026, by 07:18 IST, after rounds 6 to 9 on the citations route kept showing words
written only to hold a citation): `statement_diff_summary` v11 takes the json route. The reply is v11.schema.json's JSON, each
bullet its text and its quotes by document; `summaries.check_diff_json` finds each quote in that document's changed
sentences, cites them, and gives the bullet the checks of v7 to v10 (figures, quotations, repeats, ends). Targets pinned to
v1 to v10 are asked and checked exactly as before. No database and no model: the runs are in test_ai_batch_task.py."""
import json

import pytest
from ai_fakes import good_diff_json, sentence_spans
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD, reply

from ft.ai import summaries
from ft.ai.client import SONNET, load_prompt, load_schema
from ft.ai.summaries import check_diff_json
from ft.engines.document.diff import diff_texts
from ft.jobs.tasks.ai_batch import PROMPT_VERSION, Work
from ft.quality import reasons

D = diff_texts(FOMC_A, FOMC_B)
OLD_CHANGED, NEW_CHANGED = summaries.changed_spans(D.segments)
CHANGED = {OLD.document_id: OLD_CHANGED, NEW.document_id: NEW_CHANGED}
A_GROWTH, A_RATE = (0, 73), (451, 580)                  # changed in the old statement (test_diff_summary_copies.py)
B_GROWTH, B_RATE = (0, 81), (458, 654)                  # changed in the new one
V11 = load_prompt("statement_diff_summary", "v11")
REQ = summaries.diff_summary_request(NEW, OLD, D.segments, V11)

GROWTH = {"text": 'Growth changed from "has moderated" to "has moderated further".',
          "quotes": [{"document": "previous", "text": "growth of economic activity has moderated"},
                     {"document": "new", "text": "has moderated further"}]}
DECISION = {"text": 'The Committee decided to "lower" the target range, where it had decided to "maintain" it.',
            "quotes": [{"document": "new", "text": "decided to lower the target range"},
                       {"document": "previous", "text": "decided to maintain the target range"}]}


def check(*rows):
    return check_diff_json({"bullets": list(rows)}, REQ.sources, CHANGED)


def spans(b):
    return [(c.document_id, c.start, c.end) for c in b.citations]


# ---------------------------------------------------------------------------------------------- the request
def test_v11_takes_the_json_route_on_sonnet_at_effort_medium_and_is_current():
    assert PROMPT_VERSION["statement_diff_summary"] == "v11" and summaries.DIFF_JSON_FROM == 11
    assert (REQ.spec.route, REQ.model_id, REQ.spec.effort) == ("json", SONNET, "medium")
    assert REQ.schema == load_schema("statement_diff_summary", "v11")
    params = REQ.params(batch=True)
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": REQ.schema}
    assert all("citations" not in b for b in params["messages"][0]["content"])       # no citations on the json route
    assert REQ.instruction.endswith("explain what changed, as the schema's JSON, following the rules.")
    # v10 and earlier stay on the citations route, as they ran.
    for version in ("v1", "v6", "v10"):
        req = summaries.diff_summary_request(NEW, OLD, D.segments, load_prompt("statement_diff_summary", version))
        assert req.spec.route == "citations" and req.schema is None, version
    assert not summaries.diff_json("v10") and summaries.diff_json("v11") and summaries.diff_json("v12")


def test_v11_keeps_v10s_opening_and_states_the_routes_rules():
    v10 = load_prompt("statement_diff_summary", "v10").text
    assert V11.text.startswith(v10.split("Rules:\n", 1)[0]) and "<!--" not in V11.text
    for line in ("Answer with the schema's JSON: the bullets, each with its text and the quotes it rests on.",
                 "Rules for each bullet's text:", "Rules for each bullet's quotes:",
                 "- Give each bullet at least one quote, and at most six."):
        assert line in V11.text.splitlines()
    assert "cite" not in V11.text.lower()                               # the route has no citations to place


def test_the_json_retry_note_speaks_the_routes_words():
    req = summaries.diff_summary_request(NEW, OLD, D.segments, V11, note="bullet 1 had no verified citation.")
    assert req.note.startswith("Your previous answer to this request couldn't be published: bullet 1 had no verified")
    assert "Answer again as the schema's JSON" in req.note


# ---------------------------------------------------------------------------------------------- check_diff_json
def test_quotes_found_in_their_documents_changed_sentences_become_the_citations():
    c = check(GROWTH, DECISION)
    assert c.ok, c.reasons()
    assert spans(c.bullets[0]) == [(OLD.document_id, *A_GROWTH), (NEW.document_id, *B_GROWTH)]
    assert spans(c.bullets[1]) == [(NEW.document_id, *B_RATE), (OLD.document_id, *A_RATE)]
    assert dict(c.mapping) == {"search": 4} and not c.copies


def test_a_quote_is_matched_as_match_form_reads_it_and_case_aside_if_need_be():
    row = {"text": 'Growth changed to "has moderated further".',
           "quotes": [{"document": "new", "text": "Growth  of economic activity has moderated further"}]}
    assert check(row, DECISION).ok                                             # whitespace collapsed
    row = {"text": 'Growth "has moderated further".', "quotes": [{"document": "new", "text": "Has moderated further"}]}
    assert check(row, DECISION).ok                                             # a capital the sentence lacks
    row = {"text": "- Growth changed.", "quotes": [{"document": "new", "text": "has moderated further"}]}
    c = check(row, DECISION)
    assert c.ok and c.bullets[0].text == "Growth changed."                    # a leading bullet mark comes off


@pytest.mark.parametrize("quote, document", [
    ("has moderated further", "previous"),                                     # the new statement's words, named previous
    ("words that are in neither statement", "new"),
])
def test_a_quote_that_isnt_in_its_documents_changed_sentences_drops_the_bullet(quote, document):
    row = {"text": "Growth changed.", "quotes": [{"document": document, "text": quote}]}
    c = check(row, DECISION)
    assert c.bullets[0].problem == f'a quote ("{quote}") isn\'t in the {document} document\'s changed sentences'
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("citation", True)
    assert c.mapping["failed"] == 1


def test_a_quote_from_an_unchanged_sentence_drops_the_bullet():
    same = next(FOMC_A[s:e] for s, e in sentence_spans(FOMC_A) if FOMC_A[s:e] in FOMC_B)
    row = {"text": "The Committee said the same.", "quotes": [{"document": "previous", "text": same}]}
    assert "isn't in the previous document's changed sentences" in check(row, DECISION).bullets[0].problem


def test_the_checks_of_v7_to_v10_hold_on_the_json_route():
    figure = {"text": 'The range is now "lower" at 9 percent.', "quotes": [{"document": "new", "text": "lower"}]}
    assert check(figure, DECISION).bullets[0].problem == "numbers not in its cited passages: 9"
    sentence = "The Committee decided to lower the target range for the federal funds rate at this meeting today."
    repeat = {"text": f"{sentence} {sentence}", "quotes": [{"document": "new", "text": "decided to lower"}]}
    assert check(repeat, DECISION).bullets[0].problem.startswith("repeats its own words: ")
    unended = {"text": 'The Committee decided to "lower" the target range', "quotes": [{"document": "new", "text": "lower"}]}
    assert check(unended, DECISION).bullets[0].problem == "doesn't end as a sentence ends"


def test_a_quotation_of_the_previous_statement_needs_a_previous_quote():
    """Round 5's rule (b) fails, on the json route: words quoted from the previous statement in a bullet whose quotes name
    only the new one."""
    row = {"text": 'The statement dropped "Uncertainty about the economic outlook remains elevated" and now says the '
                   'Committee "judges that downside risks to employment have risen".',
           "quotes": [{"document": "new", "text": "judges that downside risks to employment have risen"}]}
    c = check(row, DECISION)
    assert c.bullets[0].problem == ('quoted words not in its cited passages: '
                                    '"Uncertainty about the economic outlook remains elevated"')
    row["quotes"].append({"document": "previous", "text": "Uncertainty about the economic outlook remains elevated"})
    assert check(row, DECISION).ok


@pytest.mark.parametrize("answer, problem", [
    ("not an object", "the reply isn't the schema's JSON ({bullets: [...]})"),
    ({"bullets": "none"}, "the reply isn't the schema's JSON ({bullets: [...]})"),
])
def test_a_reply_that_isnt_the_schemas_json_is_a_format_reason(answer, problem):
    c = check_diff_json(answer, REQ.sources, CHANGED)
    assert c.problems == [problem] and not c.ok
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("format", True)


@pytest.mark.parametrize("row, problem", [
    ("a string", "isn't the schema's object"),
    ({"text": "Growth changed.", "quotes": [{"document": "old", "text": "has moderated"}]}, "isn't the schema's object"),
    ({"text": "Growth changed.", "quotes": []}, "0 quotes, not 1 to 6"),
    ({"text": "Growth changed.", "quotes": [{"document": "new", "text": "has moderated further"}] * 7}, "7 quotes, not 1 to 6"),
])
def test_a_bullet_that_isnt_the_schemas_object_or_has_too_few_or_many_quotes(row, problem):
    c = check(row, DECISION)
    assert c.bullets[0].problem == problem
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("format", True)


def test_the_bullet_count_holds():
    c = check(GROWTH)
    assert c.problems == ["1 bullets, not 2 to 5"] and not c.ok


def test_work_checks_v11_replies_on_the_json_route_and_v10s_on_the_citations_route():
    w = Work("statement_diff_summary", NEW, OLD, D.segments)
    req = w.request(V11)
    good = reply([{"type": "text", "text": json.dumps({"bullets": [GROWTH, DECISION]})}])
    assert w.check(req, good).ok
    bad = reply([{"type": "text", "text": "Here are the bullets: none"}])
    assert w.check(req, bad).problems == ["the reply isn't the schema's JSON ({bullets: [...]})"]
    assert w.request(load_prompt("statement_diff_summary", "v10")).spec.route == "citations"


def test_the_fakes_default_diff_answer_passes():
    docs = [(s.title, s.text) for s in REQ.sources]
    assert check_diff_json(good_diff_json(docs), REQ.sources, CHANGED).ok
    assert not check_diff_json(good_diff_json(docs, "uncited"), REQ.sources, CHANGED).ok
