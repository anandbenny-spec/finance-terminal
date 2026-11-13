"""#445, round 13 (Anand's pick of 13 Nov 2026, about 17:15 IST, after the build session's own read of round 12):
`statement_diff_summary` v14 is v13 plus two rules, and from v14 `summaries.check_diff_json` gives a bullet one more check and
widens another, both tightenings (`summaries.DIFF_SIDES_FROM`):
- the same-claim check (`same_problem`) also fails "again" and "as before" in the bullet's own words (round 12, dev's 2556:
  "…again voted to keep the policy rate unchanged");
- a bullet whose own words describe the previous document ("moved from", "previously", "was removed" …) fails unless one of
  its quotes is from the previous document (`previous_problem`; round 12, dev's 2552: "Loretta J. Mester moved from voting
  for the action to dissent", quoting only the new statement).
Targets answered with v13 or earlier keep the checks they ran with. No database and no model."""
import pytest
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD

from ft.ai import summaries
from ft.ai.client import SONNET, load_prompt, load_schema
from ft.ai.summaries import check_diff_json, previous_problem, retry_note, same_problem
from ft.engines.document.diff import diff_texts
from ft.jobs.tasks.ai_batch import PROMPT_VERSION
from ft.quality import reasons

D = diff_texts(FOMC_A, FOMC_B)
OLD_CHANGED, NEW_CHANGED = summaries.changed_spans(D.segments)
CHANGED = {OLD.document_id: OLD_CHANGED, NEW.document_id: NEW_CHANGED}
V13 = load_prompt("statement_diff_summary", "v13")
V14 = load_prompt("statement_diff_summary", "v14")
REQ = summaries.diff_summary_request(NEW, OLD, D.segments, V14)
SOURCES = REQ.sources
SAME_RULE_START = "- Never say in your own words that something stayed the same"
AGAIN_RULE = ("- Don't say in your own words that something happened again or as before, either: that too is a claim about "
              "both documents.")
SIDES_RULE = ("- Say what the previous document said only with a quote from the previous document, and what the new one says "
              "only with a quote from the new one. A bullet that says a passage was removed or dropped, or how a member voted "
              "before, quotes the previous document.")
DECISION = {"text": 'The Committee decided to "lower" the target range, where it had decided to "maintain" it.',
            "quotes": [{"document": "new", "text": "decided to lower the target range"},
                       {"document": "previous", "text": "decided to maintain the target range"}]}


def check(version, *rows):
    return check_diff_json({"bullets": list(rows)}, SOURCES, CHANGED, version=version)


# ---------------------------------------------------------------------------------------------- the prompt
def test_v14_is_v13_plus_two_rules_after_the_same_claim_rule_with_v13s_schema_on_sonnet_and_is_current():
    thirteen, fourteen = V13.text.splitlines(), V14.text.splitlines()
    assert [x for x in fourteen if x not in thirteen] == [AGAIN_RULE, SIDES_RULE]
    assert [x for x in fourteen if x not in (AGAIN_RULE, SIDES_RULE)] == thirteen
    at = fourteen.index(AGAIN_RULE)
    assert fourteen[at - 1].startswith(SAME_RULE_START) and fourteen[at + 1] == SIDES_RULE
    assert "<!--" not in V14.text
    assert load_schema("statement_diff_summary", "v14") == load_schema("statement_diff_summary", "v13")
    assert (REQ.spec.route, REQ.model_id, REQ.spec.effort) == ("json", SONNET, "medium")
    assert PROMPT_VERSION["statement_diff_summary"] == "v14" and summaries.DIFF_SIDES_FROM == 14
    assert summaries.DIFF_WHOLE_FROM == 13 and summaries.DIFF_STRICT_FROM == 12


# ---------------------------------------------------------------------------------------------- "again", "as before"
SAME_MESSAGE = "a claim that something stayed the same, which no quote from a changed sentence can show: "


@pytest.mark.parametrize("text, said", [
    ('Now Dr. Dholakia "voted to change the stance", while Dr. Ghate and Dr. Acharya again voted to keep the rate.',
     "again"),                                                                    # round 12's 2556
    ('Stephen I. Miran again dissented in favour of a larger cut.', "again"),     # round 10's 2535
    ('The target range was lowered again, now to "3-3/4 to 4 percent".', "again"),
    ('The MPC voted as before to keep the rate, but "Prof. Varma" dissented.', "as before"),
    ("As in the previous statement, the Committee is attentive to the risks.", "As in the previous"),
])
def test_from_v14_same_problem_also_fails_again_and_as_before(text, said):
    assert same_problem(text, again=True) == SAME_MESSAGE + f'"{said}"'
    assert same_problem(text) is None                                             # v13 as it ran


def test_again_inside_quotation_marks_is_the_documents_word():
    assert same_problem('The MPC "again voted unanimously" to hold.', again=True) is None


# ---------------------------------------------------------------------------------------------- previous_problem
PREVIOUS_MESSAGE = "a claim about the previous document with no quote from it: "


@pytest.mark.parametrize("text, said", [
    ('Loretta J. Mester moved from voting for the action to dissent, as she "preferred to reduce the target range".',
     "moved from"),                                                               # round 12's 2552
    ('New text says "The invasion of Ukraine by Russia is causing hardship". Earlier language on the virus was removed.',
     "Earlier"),                                                                  # round 10's 2526
    ('The sentence about reducing holdings was removed. The Committee "decided to conclude the reduction".', "removed"),
    ('The vote was no longer described as unanimous: Dr. Dholakia "voted to reduce the repo rate by 40 basis points".',
     "no longer"),                                                                # round 10's 2536
    ('The cut is now "to 6.0 per cent", against a larger one before.', "before"),
    ('Activity is now "strong" rather than moderate.', "rather than"),
    ('The Committee previously saw balanced risks and now "judges that downside risks to employment have risen".',
     "previously"),
])
def test_previous_problem_names_a_claim_about_the_previous_document(text, said):
    assert previous_problem(text) == PREVIOUS_MESSAGE + f'"{said}"'


@pytest.mark.parametrize("text", [
    'Inflation "surged by 2.6 percentage points to 7.4 per cent in July" before moderating to 6.8 per cent.',  # a conjunction
    'The new statement adds that the Committee "judges that downside risks to employment have risen".',
    'The Committee now says the "previous" guidance holds.',                       # the document's word, in quotation marks
    'Adriana D. Kugler was added to the list of members voting for the action.',
])
def test_previous_problem_lets_claims_about_the_new_document_pass(text):
    assert previous_problem(text) is None


# ---------------------------------------------------------------------------------------------- check_diff_json
MOVED = {"text": 'The Committee moved from a balanced view to judging that "downside risks to employment have risen".',
         "quotes": [{"document": "new", "text": "judges that downside risks to employment have risen"}]}
MOVED_QUOTED = {"text": 'The Committee moved from being "attentive to the risks to both sides of its dual mandate" to judging '
                        'that "downside risks to employment have risen".',
                "quotes": [{"document": "previous", "text": "attentive to the risks to both sides of its dual mandate"},
                           {"document": "new", "text": "judges that downside risks to employment have risen"}]}
AGAIN = {"text": 'The Committee again decided to change the range, to "lower" it where it had decided to "maintain" it.',
         "quotes": DECISION["quotes"]}


@pytest.mark.parametrize("row, problem", [
    (MOVED, PREVIOUS_MESSAGE + '"moved from"'),
    (AGAIN, SAME_MESSAGE + '"again"'),
])
def test_from_v14_a_bullet_fails_and_v13_and_earlier_are_checked_as_they_ran(row, problem):
    for version in ("v11", "v12", "v13", None):
        assert check(version, row, DECISION).ok, version                         # as they ran
    c = check("v14", row, DECISION)
    assert not c.ok and c.bullets[0].problem == problem and c.bullets[1].problem is None
    assert reasons.categorise("rejected", "rejected: " + "; ".join(c.reasons())) == ("citation", True)
    assert problem in retry_note(c)                                              # the retry is told what failed


def test_v14_publishes_the_claim_once_it_quotes_the_previous_document():
    c = check("v14", MOVED_QUOTED, DECISION)
    assert c.ok, c.reasons()
    assert {x.document_id for x in c.bullets[0].citations} == {OLD.document_id, NEW.document_id}
