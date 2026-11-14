"""#445, round 15 (Anand's decision of 14 Nov 2026, about 00:38 IST, after the judge failed round 14): `statement_diff_summary`
v16 sends v15's text word for word and marks three more checks (`summaries.DIFF_FIT_FROM`), one for each pattern in round
14's verdict, each a check that drops the bullet, so the reply is retried with the reason, then rejected:
- a quotation set where its grammar can't fit (`fit_problem`; fomc-2025-09-17's "…at "maintain the target range …"" and
  "citing "in light of …"", output 2578);
- the statement's next words run on from a quotation outside quotation marks (`runs_on_problem`; fomc-2023-06-14's
  "…"Holding the target range steady at this meeting" allows the Committee to assess additional information", 2579);
- a claim resting on a sentence next to a quoted one that the bullet doesn't quote (`neighbour_problem`; mpc-2021-08-06's
  "…Prof. Jayanth R. Varma, who "expressed reservations on this part of the resolution", is the exception to the others",
  2577, where "except" is in the sentence before).
Replies made with v15 or earlier keep the checks they ran with. No database and no model."""
import json
from datetime import date

import pytest
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD, reply

from ft.ai import summaries
from ft.ai.client import SONNET, load_prompt, load_schema
from ft.ai.summaries import (
    Doc,
    check_diff_json,
    fit_problem,
    neighbour_problem,
    retry_note,
    runs_on_problem,
    sentence_spans,
)
from ft.engines.document.diff import diff_texts
from ft.jobs.tasks.ai_batch import Work
from ft.quality import reasons

D = diff_texts(FOMC_A, FOMC_B)
CHANGED = dict(zip((OLD.document_id, NEW.document_id), summaries.changed_spans(D.segments), strict=True))
SENTENCES = dict(zip((OLD.document_id, NEW.document_id), sentence_spans(D.segments), strict=True))
V15 = load_prompt("statement_diff_summary", "v15")
V16 = load_prompt("statement_diff_summary", "v16")
REQ = summaries.diff_summary_request(NEW, OLD, D.segments, V16)

# Round 14's 2578, bullet 1, word for word (its hyphens as the fixture writes them).
MISFIT = {"text": 'The Committee moved from holding the target range at "maintain the target range for the federal funds rate at '
                  '4-1/4 to 4-1/2 percent" to a cut, deciding to "lower the target range for the federal funds rate by 1/4 '
                  'percentage point to 4 to 4-1/4 percent", citing "in light of the shift in the balance of risks".',
          "quotes": [{"document": "previous", "text": "maintain the target range for the federal funds rate at 4-1/4 to "
                                                      "4-1/2 percent"},
                     {"document": "new", "text": "lower the target range for the federal funds rate by 1/4 percentage "
                                                 "point to 4 to 4-1/4 percent"},
                     {"document": "new", "text": "in light of the shift in the balance of risks"}]}
RISKS = {"text": 'The Committee now "judges that downside risks to employment have risen".',
         "quotes": [{"document": "new", "text": "judges that downside risks to employment have risen"}]}


def check(version, *rows, sentences=SENTENCES):
    return check_diff_json({"bullets": list(rows)}, REQ.sources, CHANGED, version=version, sentences=sentences)


def test_v16_sends_v15s_text_with_v15s_schema_on_sonnet():
    assert V16.text == V15.text and "<!--" not in V16.text
    assert load_schema("statement_diff_summary", "v16") == load_schema("statement_diff_summary", "v15")
    assert (REQ.spec.route, REQ.model_id, REQ.spec.effort) == ("json", SONNET, "medium")
    assert summaries.DIFF_FIT_FROM == 16 and V16.version == "v16"       # current from v17 on: test_diff_summary_v17.py
    assert summaries.DIFF_FP_FIX_FROM == 15 and summaries.DIFF_SIDES_FROM == 14


# ---------------------------------------------------------------------------------------------- pattern 1: grammar fit
def test_round_14s_misfit_publishes_on_v15_and_is_dropped_on_v16():
    assert check("v15", MISFIT, RISKS).ok                                          # as round 14 ran it
    c = check("v16", MISFIT, RISKS)
    assert not c.ok and c.bullets[1].problem is None
    assert c.bullets[0].problem == ('a quotation that doesn\'t fit the grammar of its sentence: at "maintain the target range '
                                    'for the federal funds rate at 4-1…" (quote words that fit where they stand, or word the '
                                    'sentence differently)')
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("format", True)
    assert "doesn't fit the grammar" in retry_note(c)


@pytest.mark.parametrize("text", [
    'It moved from holding the range at "maintain the target range" to a cut.',      # 2578: a preposition, a base verb
    'The cut came citing "in light of the shift in the balance of risks".',          # 2578: an object verb, a preposition
    'Purchases are now stated at "at least $70 billion per month".',                 # 2574 (v14): the same word twice
    'The Committee being "is continuing" that policy is new.',                       # 2561 (v14): two finite auxiliaries
    'The statement adds the "in light of the shift" clause.',                        # a determiner, a preposition
    'Inflation was described as having "rose" further.',                             # a perfect, a past form
    'The Committee said it will "remained" attentive.',                              # a modal, a past form
    'The range was moved at "lower the target range" this time.',                    # a noun-verb with a determiner after
    'The new statement says "Holding the target range steady" steady.',              # the last word repeated after it
])
def test_quotations_set_where_their_grammar_cant_fit_fail(text):
    assert fit_problem(text) is not None, text


@pytest.mark.parametrize("text", [
    'The stance moved from "maintain the neutral monetary policy stance" to "change the stance".',   # 2581, judge passed
    'The stance changed from "continue with the neutral stance" to "change the stance to calibrated tightening".',  # 2583
    'The economy\'s path changed from "will depend significantly on" the virus to "continues to depend on" it.',  # 2544
    'It is a direct impact of "about 36 basis points" on headline inflation.',      # 2538: "about" before a number
    'The Committee decided to "maintain" it, rather than "raise the target range".',
    'Activity was described as expanding at a "solid" pace.',
    'The Committee has "been attentive" before, and now it "judges that downside risks have risen".',
    'A new sentence says the Committee "will closely monitor incoming information".',
])
def test_quotations_that_fit_pass(text):
    assert fit_problem(text) is None, text


# ---------------------------------------------------------------------------------------------- pattern 2: running on
HOLDING = ("Holding the target range steady at this meeting allows the Committee to assess additional information and its "
           "implications for monetary policy.")                                    # fomc-2023-06-14, new statement
RAISE = ("In support of these goals, the Committee decided to raise the target range for the federal funds rate to 5 to "
         "5-1/4 percent.")


def test_round_14s_run_on_fails_with_the_words_it_copied():
    text = ('A sentence saying the Committee "will closely monitor incoming information" was removed, and a new sentence says '
            'that "Holding the target range steady at this meeting" allows the Committee to assess additional information.')
    assert runs_on_problem(text, [HOLDING]) == (
        'the statement\'s next words after a quotation, outside quotation marks: "…the target range steady at this meeting" '
        'is followed by "allows the Committee to assess additional information" in the statement (put them inside the '
        'quotation, or say it in your own words)')
    fixed = ('A new sentence says that "Holding the target range steady at this meeting allows the Committee to assess '
             'additional information".')
    assert runs_on_problem(fixed, [HOLDING]) is None


@pytest.mark.parametrize("text", [
    'The Committee decided to "raise" the target range in the previous statement.',  # three words, judges passed (2579)
    'It said "Holding the target range steady at this meeting". The Committee will assess additional information.',
    'It said "Holding the target range steady at this meeting", which lets the Committee look at more data.',
    'It said "Holding the target range steady" again at this meeting.',
])
def test_three_words_a_new_sentence_or_own_words_after_a_quotation_pass(text):
    assert runs_on_problem(text, [HOLDING, RAISE]) is None, text


def test_capitalised_words_alone_dont_run_on():
    passage = "The Committee decided to keep rates unchanged, said Jerome H. Powell, Chair."
    assert runs_on_problem('It decided "to keep rates unchanged, said" Jerome H. Powell.', [passage]) is None


# ---------------------------------------------------------------------------------------------- pattern 3: neighbours
OLD_MPC = ("1. The MPC decided to keep the policy repo rate unchanged at 4.0 per cent. 2. All members of the MPC unanimously "
           "voted to keep the policy repo rate unchanged at 4.0 per cent. Furthermore, all members of the MPC unanimously "
           "voted to continue with the accommodative stance as long as necessary. 3. The minutes of the MPC's meeting will "
           "be published on June 18, 2021.")
NEW_MPC = ("1. The MPC decided to keep the policy repo rate unchanged at 4.0 per cent. 2. All members of the MPC unanimously "
           "voted to keep the policy repo rate unchanged at 4.0 per cent. All members, except Prof. Jayanth R. Varma, voted to "
           "continue with the accommodative stance as long as necessary. Prof. Jayanth R. Varma expressed reservations on "
           "this part of the resolution. 3. The minutes of the MPC's meeting will be published on August 20, 2021.")
OLD_DOC = Doc(11, "mpc_resolution", "MPC resolution", "MPC resolution, 4 Jun 2021", "RBI", date(2021, 6, 4), OLD_MPC)
NEW_DOC = Doc(12, "mpc_resolution", "MPC resolution", "MPC resolution, 6 Aug 2021", "RBI", date(2021, 8, 6), NEW_MPC)
D_MPC = diff_texts(OLD_MPC, NEW_MPC)
REQ_MPC = summaries.diff_summary_request(NEW_DOC, OLD_DOC, D_MPC.segments, V16)
CHANGED_MPC = dict(zip((11, 12), summaries.changed_spans(D_MPC.segments), strict=True))
SENTENCES_MPC = dict(zip((11, 12), sentence_spans(D_MPC.segments), strict=True))
STANCE_VOTE = {"text": 'The previous resolution says all members "unanimously voted to continue with the accommodative '
                       'stance", while in the new one Prof. Jayanth R. Varma, who "expressed reservations on this part of the '
                       'resolution", is the exception to the others.',                 # round 14's 2577, bullet 1
               "quotes": [{"document": "previous", "text": "unanimously voted to continue with the accommodative stance"},
                          {"document": "new", "text": "expressed reservations on this part of the resolution"}]}
MINUTES = {"text": 'The minutes are now due on "August 20, 2021".',
           "quotes": [{"document": "new", "text": "August 20, 2021"}]}


def check_mpc(version, *rows, sentences=SENTENCES_MPC):
    return check_diff_json({"bullets": list(rows)}, REQ_MPC.sources, CHANGED_MPC, version=version, sentences=sentences)


def test_the_mpc_pair_splits_as_the_redline_does():
    new_changed = [NEW_MPC[s:e] for s, e in CHANGED_MPC[12]]
    assert any(x.startswith("All members, except Prof. Jayanth R. Varma") for x in new_changed)
    assert any(x.startswith("Prof. Jayanth R. Varma expressed reservations") for x in new_changed)
    assert "2. All members of the MPC unanimously voted to keep the policy repo rate unchanged at 4.0 per cent." in [
        NEW_MPC[s:e] for s, e in SENTENCES_MPC[12]]


def test_round_14s_neighbour_claim_publishes_on_v15_and_is_dropped_on_v16():
    assert check_mpc("v15", STANCE_VOTE, MINUTES).ok                                 # as round 14 ran it
    c = check_mpc("v16", STANCE_VOTE, MINUTES)
    assert c.bullets[0].problem == (
        'a claim resting on a sentence next to its quotes that it doesn\'t quote: "exception" is from "All members, except '
        'Prof. Jayanth R. Varma, voted to continue with the accommod…" (quote that sentence too, or leave the claim out)')
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("citation", True)


def test_quoting_the_neighbour_too_publishes():
    fixed = {"text": 'The previous resolution says all members "unanimously voted to continue with the accommodative stance", '
                     'while the new one says all members voted that way "except Prof. Jayanth R. Varma", who "expressed '
                     'reservations on this part of the resolution".',
             "quotes": [*STANCE_VOTE["quotes"], {"document": "new", "text": "except Prof. Jayanth R. Varma"}]}
    c = check_mpc("v16", fixed, MINUTES)
    assert c.ok, c.reasons()


def test_a_claim_from_an_unchanged_neighbour_is_told_to_go():
    row = {"text": 'The stance vote now has all members who "voted to continue with the accommodative stance as long as '
                   'necessary", unlike the unanimous repo vote.',
           "quotes": [{"document": "new", "text": "voted to continue with the accommodative stance as long as necessary"}]}
    problem = check_mpc("v16", row, MINUTES).bullets[0].problem
    assert problem.startswith('a claim resting on a sentence next to its quotes that it doesn\'t quote: "unanimous" is from '
                              '"2. All members of the MPC unanimously voted')
    assert problem.endswith("(that sentence didn't change, so it can't be quoted: leave the claim out)")


def test_without_the_sentences_the_neighbour_check_is_skipped():
    assert check_mpc("v16", STANCE_VOTE, MINUTES, sentences=None).ok


def _spans(*sentences: str) -> tuple[str, list[tuple[int, int]]]:
    text, spans = "", []
    for x in sentences:
        text += (" " if text else "")
        spans.append((len(text), len(text) + len(x)))
        text += x
    return text, spans


def test_topic_words_and_words_common_in_the_document_arent_claims():
    text, spans = _spans("The Committee will continue to increase its holdings.",
                         "These asset purchases help foster smooth market functioning.", "Inflation remains low.",
                         "Inflation expectations are anchored.", "Inflation is expected to rise.")
    texts, sentences = {1: text}, {1: spans}
    assert neighbour_problem('The purchase sentence now says it "will continue to increase its holdings".', [(1, *spans[0])],
                             texts, sentences, {1: [spans[0]]}) is None             # 2544: a topic, judge passed
    own = 'The inflation sentence says it "remains low", with expectations still a theme.'
    assert neighbour_problem(own, [(1, *spans[2])], texts, sentences, {1: [spans[2]]}) is not None   # one sentence's word
    text, spans = _spans("The Committee will continue to increase its holdings.",
                         "These asset purchases help foster smooth market functioning.", "Inflation remains low.",
                         "Inflation expectations are anchored.", "Inflation is expected to rise.",
                         "Inflation expectations matter.", "Expectations are key.")
    assert neighbour_problem(own, [(1, *spans[2])], {1: text}, {1: spans}, {1: [spans[2]]}) is None  # common: 3 sentences


# ---------------------------------------------------------------------------------------------- the task
def test_work_passes_the_redlines_sentences_so_v16_replies_get_the_neighbour_check():
    w = Work("statement_diff_summary", NEW_DOC, OLD_DOC, D_MPC.segments)
    req = w.request(V16)
    bad = reply([{"type": "text", "text": json.dumps({"bullets": [STANCE_VOTE, MINUTES]})}])
    c = w.check(req, bad)
    assert not c.ok and "a claim resting on a sentence next to its quotes" in c.bullets[0].problem
    assert w.check(w.request(V15), bad).ok                                           # v15 is checked as it ran


def test_the_fakes_default_diff_answer_passes_on_v16():
    from ai_fakes import good_diff_json
    docs = [(s.title, s.text) for s in REQ.sources]
    c = check("v16", *good_diff_json(docs)["bullets"])
    assert c.ok, c.reasons()
