"""#445, round 12 (Anand's decision of 13 Nov 2026, 13:40 IST, after the judge failed round 11 on two kinds of bullet):
`statement_diff_summary` v13 is v12 plus two rules, and from v13 `summaries.check_diff_json` gives a bullet two more checks,
both tightenings (`summaries.DIFF_WHOLE_FROM`):
- no claim in the bullet's own words that something stayed the same (`same_problem`): its quotes come only from changed
  sentences, so nothing it cites can show it (round 11: "The repo rate sentence is not among the changes.", dev's 2541;
  "The policy repo rate decision is unchanged, but …", dev's 2538);
- no quotation cut in the middle of a phrase (`cut_quote_problem`): a quotation the bullet doesn't go on from that stops
  where its cited sentence goes on, when it started where that sentence starts (round 11's front-page bullet, dev's 2546:
  "…determine the policy stance appropriate".) or ends on a word such as "the" or "to".
Targets answered with v11 or v12 keep the checks they ran with. No database and no model: the runs are in
test_ai_batch_task.py."""
import pytest
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD

from ft.ai import summaries
from ft.ai.client import SONNET, load_prompt, load_schema
from ft.ai.summaries import check_diff_json, cut_quote_problem, retry_note, same_problem
from ft.engines.document.diff import diff_texts
from ft.quality import reasons

D = diff_texts(FOMC_A, FOMC_B)
OLD_CHANGED, NEW_CHANGED = summaries.changed_spans(D.segments)
CHANGED = {OLD.document_id: OLD_CHANGED, NEW.document_id: NEW_CHANGED}
V12 = load_prompt("statement_diff_summary", "v12")
V13 = load_prompt("statement_diff_summary", "v13")
SAME_RULE = ("- Never say in your own words that something stayed the same, is unchanged, was kept or retained, or isn't "
             "among the changes: your quotes come only from sentences that changed, so nothing could show it. If a changed "
             "sentence itself says something was kept or left unchanged, quote its words.")
CUT_RULE = ('- End each quotation where a phrase of the document ends, never on a word that the document\'s next words '
            'complete, and never on a word such as "the", "of", "to" or "and". A quotation that starts where a sentence of '
            "the document starts runs to the end of that sentence, or stops where the document has a comma, a semicolon or "
            "a colon.")

RISKS = "The Committee is attentive to the risks to both sides of its dual mandate and judges that downside risks to employment have risen."
HOLDINGS = "The Committee will continue reducing its holdings of Treasury securities."
ADJUST = ("In considering additional adjustments to the target range, the Committee will carefully assess incoming data, the "
          "evolving outlook, and the balance of risks.")
DECISION = {"text": 'The Committee decided to "lower" the target range, where it had decided to "maintain" it.',
            "quotes": [{"document": "new", "text": "decided to lower the target range"},
                       {"document": "previous", "text": "decided to maintain the target range"}]}


def check(version, *rows):
    return check_diff_json({"bullets": list(rows)}, (OLD_SRC, NEW_SRC), CHANGED, version=version)


REQ = summaries.diff_summary_request(NEW, OLD, D.segments, V13)
OLD_SRC, NEW_SRC = REQ.sources


# ---------------------------------------------------------------------------------------------- the prompt
def test_v13_is_v12_plus_two_rules_with_v12s_schema_on_sonnet():
    twelve, thirteen = V12.text.splitlines(), V13.text.splitlines()
    assert [x for x in thirteen if x not in twelve] == [SAME_RULE, CUT_RULE]
    assert [x for x in thirteen if x not in (SAME_RULE, CUT_RULE)] == twelve
    assert thirteen[thirteen.index(SAME_RULE) - 1] == ("- Describe only what changed. Don't summarise wording that is the "
                                                       "same in both documents.")
    assert thirteen[thirteen.index(CUT_RULE) - 1].startswith("- Each quotation must fit the grammar of your own sentence.")
    assert "<!--" not in V13.text
    assert load_schema("statement_diff_summary", "v13") == load_schema("statement_diff_summary", "v12")
    assert (REQ.spec.route, REQ.model_id, REQ.spec.effort) == ("json", SONNET, "medium")
    assert summaries.DIFF_WHOLE_FROM == 13               # v16 is current (test_diff_summary_v16.py)
    assert summaries.DIFF_STRICT_FROM == 12 and summaries.diff_json("v13")


# ---------------------------------------------------------------------------------------------- same_problem
@pytest.mark.parametrize("text, said", [
    ('The stance moved from "change" to "continue". The repo rate sentence is not among the changes.',
     "not among the changes"),                                                    # round 11's 2541
    ('The policy repo rate decision is unchanged, but "will continue to remain data-dependent" was added.',
     "is unchanged"),                                                             # round 11's 2538
    ('The neutral stance was kept, but the dissent narrowed to "Prof. Ram Singh".', "was kept"),       # round 11's 2539
    ("The GDP projection stayed at 6.5 per cent, while Q1 was raised.", "stayed at"),                 # round 10's 2531
    ("The rate and stance are unchanged.", "are unchanged"),                                          # round 10's 2528
    ("The growth projection was retained at 9.5 per cent.", "was retained"),
    ("The vote isn't among the changes.", "n't among the changes"),
    ("The decision did not change.", "did not change"),
    ("The target remains the same.", "remains the same"),
])
def test_same_problem_fails_a_claim_in_the_bullets_own_words_that_something_stayed_the_same(text, said):
    assert same_problem(text) == ("a claim that something stayed the same, which no quote from a changed sentence can "
                                  f'show: "{said}"')


@pytest.mark.parametrize("text", [
    'The MPC moved from holding the repo rate "unchanged at 5.50 per cent" to cutting it.',          # the document's words
    'The previous resolution voted "to keep the policy repo rate unchanged", and the new one to cut it.',
    "The policy decision changed from a rate increase to no change.",           # round 11's 2546: a decision, described
    "The MPC moved from holding the repo rate to cutting it.",                   # round 11's 2539
    "The view changed, with risks still remaining.",                             # round 11's 2540, passed by the judge
    'The repo rate "remains unchanged" in the new resolution.',
])
def test_same_problem_lets_quoted_words_and_described_decisions_pass(text):
    assert same_problem(text) is None


# ---------------------------------------------------------------------------------------------- cut_quote_problem
def test_the_fixtures_sentences_are_the_changed_ones():
    news = {FOMC_B[s:e] for s, e in NEW_CHANGED}
    assert {RISKS, HOLDINGS, ADJUST} <= news


CUT_RISKS = ('A sentence was added saying "The Committee is attentive to the risks to both sides of its dual mandate and '
             'judges that downside risks".')
CUT_START = "a quotation cut in the middle of a sentence it starts: "


@pytest.mark.parametrize("text, problem", [
    # round 11's front-page bullet (2546): a sentence quoted from its start and stopped before its end
    (CUT_RISKS, CUT_START + '"…e to the risks to both sides of its dual mandate and judges that downside risks" stops before its end'),
    ('It now says "The Committee will continue reducing its holdings".',
     CUT_START + '"The Committee will continue reducing its holdings" stops before its end'),
    ('It now says "The Committee will continue reducing its holdings."',          # a full stop of its own inside the marks
     CUT_START + '"The Committee will continue reducing its holdings" stops before its end'),
    ('It now says "the Committee will continue reducing its holdings".',         # case aside
     CUT_START + '"the Committee will continue reducing its holdings" stops before its end'),
    ('The Committee now judges that "downside risks to".',
     'a quotation that ends on "to", in the middle of a phrase: "downside risks to"'),
])
def test_cut_quote_problem_fails_a_quotation_stopped_in_the_middle_of_a_phrase(text, problem):
    assert cut_quote_problem(text, [RISKS, HOLDINGS, ADJUST]) == problem


@pytest.mark.parametrize("text", [
    f'A sentence was added saying "{RISKS}"',                                   # the whole sentence, its full stop inside
    f'A sentence was added saying "{RISKS[:-1]}".',                              # the whole sentence, the full stop outside
    'The sentence now opens "In considering additional adjustments to the target range".',   # stops at the document's comma
    'It now says "The Committee will continue reducing its holdings" of Treasury securities alone.',   # the bullet goes on
    'Its view of the risks now includes "downside risks to employment".',        # mid-sentence, ending on a content word
    'The Committee judged "downside risks" to employment to have risen.',
    'It says the Committee "will continue reducing its holdings" of two kinds of securities in two sentences.',
    "No quotation at all.",
])
def test_cut_quote_problem_lets_whole_sentences_phrase_ends_and_quotations_the_bullet_goes_on_from_pass(text):
    assert cut_quote_problem(text, [RISKS, HOLDINGS, ADJUST]) is None


# ---------------------------------------------------------------------------------------------- check_diff_json
SAME = {"text": 'The Committee now judges that "downside risks to employment have risen". The vote is unchanged.',
        "quotes": [{"document": "new", "text": "judges that downside risks to employment have risen"}]}
CUT = {"text": CUT_RISKS,
       "quotes": [{"document": "new", "text": "The Committee is attentive to the risks to both sides of its dual mandate "
                                              "and judges that downside risks"}]}


@pytest.mark.parametrize("row, category", [(SAME, "citation"), (CUT, "format")])
def test_from_v13_the_two_checks_drop_a_bullet_and_v12_and_v11_are_checked_as_they_ran(row, category):
    for version in ("v11", "v12", None):
        assert check(version, row, DECISION).ok, version                         # as they ran
    c = check("v13", row, DECISION)
    assert not c.ok and c.bullets[1].problem is None
    problem = c.bullets[0].problem
    assert problem == (same_problem(row["text"]) if row is SAME else cut_quote_problem(row["text"], [RISKS]))
    assert reasons.categorise("rejected", "rejected: " + "; ".join(c.reasons())) == (category, True)
    assert problem in retry_note(c)                                              # the retry is told what failed


def test_v13_publishes_round_11s_clean_shapes():
    whole = {"text": f'A sentence was added saying "{RISKS[:-1]}".', "quotes": [{"document": "new", "text": RISKS[:-1]}]}
    c = check("v13", whole, DECISION)
    assert c.ok, c.reasons()
    assert [(x.document_id, x.start, x.end) for x in c.bullets[1].citations] == [
        (NEW.document_id, 458, 654), (OLD.document_id, 451, 580)]
