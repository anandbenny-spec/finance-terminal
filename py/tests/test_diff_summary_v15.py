"""#445, round 14 (Anand's pick of 13 Nov 2026, before 17:36 IST, after round 13): `statement_diff_summary` v15 sends v14's
text word for word and marks a revision of our checks (`summaries.DIFF_FP_FIX_FROM`), which fixes two false positives that
round 13 hit:
- a quotation that starts where its sentence starts may stop right before that sentence's verb: the quoted words are the
  sentence's whole subject (fomc-2024-12-18: "…listed her among those "Voting for the monetary policy action"", where the
  statement goes on "were Jerome H. Powell, …"); the round 10 and 11 cuts still fail;
- "before" ahead of a quotation is a conjunction, not a claim about the previous document (mpc-2025-10-01: "…"1.6 per
  cent" in July 2025 before "rising to 2.1 per cent in August"").
Replies made with v13 or v14 keep the checks they ran with. No database and no model."""
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD

from ft.ai import summaries
from ft.ai.client import SONNET, load_prompt, load_schema
from ft.ai.summaries import check_diff_json, cut_quote_problem, previous_problem
from ft.engines.document.diff import diff_texts
from ft.jobs.tasks.ai_batch import PROMPT_VERSION

D = diff_texts(FOMC_A, FOMC_B)
OLD_CHANGED, NEW_CHANGED = summaries.changed_spans(D.segments)
CHANGED = {OLD.document_id: OLD_CHANGED, NEW.document_id: NEW_CHANGED}
V14 = load_prompt("statement_diff_summary", "v14")
V15 = load_prompt("statement_diff_summary", "v15")
REQ = summaries.diff_summary_request(NEW, OLD, D.segments, V15)
VOTING_FOR = "Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice Chair; Beth M. Hammack."
RISKS = ("The Committee is attentive to the risks to both sides of its dual mandate and judges that downside risks to "
         "employment have risen.")
DECISION = {"text": 'The Committee decided to "lower" the target range, where it had decided to "maintain" it.',
            "quotes": [{"document": "new", "text": "decided to lower the target range"},
                       {"document": "previous", "text": "decided to maintain the target range"}]}


def check(version, *rows):
    return check_diff_json({"bullets": list(rows)}, REQ.sources, CHANGED, version=version)


def test_v15_sends_v14s_text_with_v14s_schema_on_sonnet_and_is_current():
    assert V15.text == V14.text and "<!--" not in V15.text
    assert load_schema("statement_diff_summary", "v15") == load_schema("statement_diff_summary", "v14")
    assert (REQ.spec.route, REQ.model_id, REQ.spec.effort) == ("json", SONNET, "medium")
    assert PROMPT_VERSION["statement_diff_summary"] == "v15" and summaries.DIFF_FP_FIX_FROM == 15
    assert summaries.DIFF_SIDES_FROM == 14 and summaries.DIFF_WHOLE_FROM == 13


def test_from_v15_a_sentences_opening_quoted_up_to_its_verb_is_not_cut():
    text = 'The previous statement listed her among those "Voting for the monetary policy action".'   # round 13's 2573
    assert cut_quote_problem(text, [VOTING_FOR]) == ('a quotation cut in the middle of a sentence it starts: "Voting for '
                                                     'the monetary policy action" stops before its end')     # v14
    assert cut_quote_problem(text, [VOTING_FOR], subject_ok=True) is None


def test_from_v15_the_round_10_and_11_cuts_still_fail():
    for text in (('A new one says "The Committee is attentive to the risks to both sides of its dual mandate and judges '
                  'that downside risks".'),                                      # stops before "to", not a verb
                 'It says "The Committee is attentive to the risks to both sides of its dual mandate and judges that".'):
        assert cut_quote_problem(text, [RISKS], subject_ok=True) is not None, text


def test_from_v15_before_ahead_of_a_quotation_is_a_conjunction():
    text = 'Headline inflation is now "1.6 per cent" in July 2025 before "rising to 2.1 per cent in August".'  # round 13's 2569
    assert previous_problem(text) is not None                                    # v14
    assert previous_problem(text, whole_text=True) is None
    for clause_final in ('The cut is now "to 6.0 per cent", against a larger one before.',
                         'It was smaller before; the cut is now "to 6.0 per cent".'):
        assert previous_problem(clause_final, whole_text=True) is not None, clause_final


SUBJECT = {"text": 'A dissent was added under "Voting against this action".',
           "quotes": [{"document": "new", "text": "Voting against this action"}]}
BEFORE = {"text": 'The Committee now says it "will carefully assess incoming data" before "additional adjustments to the '
                  'target range".',
          "quotes": [{"document": "new", "text": "will carefully assess incoming data"},
                     {"document": "new", "text": "additional adjustments to the target range"}]}


def test_v14_replies_keep_v14s_checks_and_v15_replies_publish():
    for row in (SUBJECT, BEFORE):
        assert not check("v14", row, DECISION).ok                                 # as v14 ran
        c = check("v15", row, DECISION)
        assert c.ok, c.reasons()
