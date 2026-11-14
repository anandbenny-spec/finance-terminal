"""#445, round 16 (Anand's decision of 14 Nov 2026, 14:48 IST, after the judge failed round 15): `statement_diff_summary` v17 is
v16 plus three rules, with four more checks in code, a second model's check and a fallback built by code
(`summaries.DIFF_VERIFY_FROM`):
- no run of five or more of a statement's words outside quotation marks, names and labels included (`long_run_problem`), after
  code has taken such a run into a quotation where a cited passage allows (`settle_runs`; fomc-2026-10-28's 3376, bullet 3);
- no "adds" for words the previous statement's changed sentences already had (`added_problem`; mpc-2023-12-08's 3375);
- no claim resting on the sentence a quoted one refers back to, however common its words (`neighbour_problem`'s
  `refers_back`; mpc-2020-10-09's 3383, "supply shocks"), and no claim about who voted for the action without its voting
  sentence (`vote_for_problem`; fomc-2025-12-10's 3384);
- the second model's request and verdicts (`verify_request`, `apply_verdicts`), which `Work.check` asks for when every bullet
  passed;
- the fallback's bullets (`fallback_bullets`).
Replies made with v16 or earlier keep the checks they ran with. No database and no model (the task's database tests are in
test_ai_batch_task.py)."""
import json
import re
from collections import Counter
from datetime import date

import pytest
from ai_fakes import sentence_spans as text_spans
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD, reply

from ft.ai import summaries
from ft.ai.client import HAIKU, SONNET, load_prompt, load_schema
from ft.ai.summaries import (
    Checked,
    Doc,
    added_problem,
    apply_verdicts,
    check_diff_json,
    fallback_bullets,
    long_run_problem,
    neighbour_problem,
    retry_note,
    sentence_spans,
    settle_runs,
    verify_request,
    vote_for_problem,
)
from ft.engines.document.diff import diff_texts
from ft.eval import diff_quotes
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION, Work, _vnum
from ft.quality import reasons

D = diff_texts(FOMC_A, FOMC_B)
CHANGED = dict(zip((OLD.document_id, NEW.document_id), summaries.changed_spans(D.segments), strict=True))
SENTENCES = dict(zip((OLD.document_id, NEW.document_id), sentence_spans(D.segments), strict=True))
V16 = load_prompt("statement_diff_summary", "v16")
V17 = load_prompt("statement_diff_summary", "v17")
VERIFY = load_prompt("statement_diff_summary", "verify2")
REQ = summaries.diff_summary_request(NEW, OLD, D.segments, V17)

# An RBI pair shaped like mpc-2023-12-08 (3375) and mpc-2020-10-09 (3383): a sentence reworded, and one that refers back.
RBI_OLD = ("The MPC decided to keep the policy repo rate unchanged at 6.50 per cent. "
           "Headline inflation eased in October on lower food prices. "
           "Hence, monetary policy needs to remain actively disinflationary. "
           "Supply chain pressures have eased across sectors. "
           "The MPC will remain watchful of incoming data.")
RBI_NEW = ("The MPC decided to keep the policy repo rate unchanged at 6.50 per cent. "
           "Core disinflation has been steady. "
           "While inflation has been above the band for several months, the MPC judges that the underlying factors are "
           "essentially supply shocks which should dissipate over the ensuing months. "
           "Accordingly, they can be looked through at this juncture while setting the stance of monetary policy. "
           "Monetary policy must continue to be actively disinflationary to ensure anchoring of inflation expectations. "
           "Supply shocks to vegetables eased. "
           "Food supply shocks are expected to fade. "
           "Supply side shocks were met by the government.")
OLD_RBI = Doc(901, "mpc_resolution", "MPC resolution", "MPC resolution, 6 Oct 2023", "RBI", date(2023, 10, 6), RBI_OLD)
NEW_RBI = Doc(902, "mpc_resolution", "MPC resolution", "MPC resolution, 8 Dec 2023", "RBI", date(2023, 12, 8), RBI_NEW)
D_RBI = diff_texts(RBI_OLD, RBI_NEW)
CH_RBI = dict(zip((901, 902), summaries.changed_spans(D_RBI.segments), strict=True))
SE_RBI = dict(zip((901, 902), sentence_spans(D_RBI.segments), strict=True))
REQ_RBI = summaries.diff_summary_request(NEW_RBI, OLD_RBI, D_RBI.segments, V17)

DISINFLATION = "Monetary policy must continue to be actively disinflationary to ensure anchoring of inflation expectations"
ADDS = {"text": f'The new resolution adds that "{DISINFLATION}".', "quotes": [{"document": "new", "text": DISINFLATION}]}
REWORDED = {"text": f'The guidance was reworded from "Hence, monetary policy needs to remain actively disinflationary" to '
                    f'"{DISINFLATION}".',
            "quotes": [{"document": "previous", "text": "Hence, monetary policy needs to remain actively disinflationary"},
                       {"document": "new", "text": DISINFLATION}]}
LOOKED = "can be looked through at this juncture"
SHOCKS = {"text": f'Inflation pressures are described as supply shocks that "{LOOKED}".',
          "quotes": [{"document": "new", "text": LOOKED}]}
CORE = {"text": 'The resolution now notes that "Core disinflation has been steady".',
        "quotes": [{"document": "new", "text": "Core disinflation has been steady"}]}


def check(version, *rows, req=REQ, changed=CHANGED, sentences=SENTENCES):
    return check_diff_json({"bullets": list(rows)}, req.sources, changed, version=version, sentences=sentences)


def check_rbi(version, *rows):
    return check(version, *rows, req=REQ_RBI, changed=CH_RBI, sentences=SE_RBI)


def test_v17_is_v16_plus_three_rules_with_v16s_schema_on_sonnet_and_v18_and_v19_are_its_text():
    assert "<!--" not in V17.text and V17.text != V16.text
    gone = 'Name members in plain words ("Lisa D. Cook and Beth M. Hammack")'
    assert gone in V16.text and gone not in V17.text
    for rule in ("Never write five or more words in a row from a document outside quotation marks",
                 "If a quoted sentence refers back to an earlier one", "the sentence was reworded: say so, and quote both"):
        assert rule in V17.text and rule not in V16.text
    assert set(V16.text.splitlines()) - set(V17.text.splitlines()) == {
        line for line in V16.text.splitlines() if line.startswith("- Name members")}
    assert load_schema("statement_diff_summary", "v17") == load_schema("statement_diff_summary", "v16")
    assert (REQ.spec.route, REQ.model_id, REQ.spec.effort) == ("json", SONNET, "medium")
    assert summaries.DIFF_VERIFY_FROM == 17 and summaries.DIFF_FIT_FROM == 16
    v18 = load_prompt("statement_diff_summary", "v18")             # v17's text, marking the checks' revision before the judge
    v19 = load_prompt("statement_diff_summary", "v19")             # and again, marking the third run's revision
    assert v18.text == V17.text == v19.text and PROMPT_VERSION["statement_diff_summary"] == "v19"
    assert load_schema("statement_diff_summary", "v19") == load_schema("statement_diff_summary", "v17")
    assert load_schema("statement_diff_summary", "v18") == load_schema("statement_diff_summary", "v17")
    assert summaries.diff_summary_request(NEW, OLD, D.segments, v18).model_id == SONNET


def test_the_run_gate_reads_words_as_the_packs_measure_does():
    assert summaries._RUN_WORD.pattern == diff_quotes._WORD.pattern
    assert summaries.RUN_MIN_WORDS == diff_quotes.MIN_WORDS == 5


# ---------------------------------------------------------------------------------------------- runs outside quotation marks
def test_round_15s_run_on_the_front_page_fails_when_no_quotation_can_take_it():
    # 3376 bullet 3's shape: the quotation stops, and the bullet goes on in the statement's words after a word of its own.
    text = ('A new sentence says "The Committee will carefully assess incoming data" for the evolving outlook, and the '
            'balance of risks.')
    problem = long_run_problem(text, [FOMC_A, FOMC_B])
    assert problem == ('a run of a statement\'s words outside quotation marks: "the evolving outlook, and the balance of '
                       'risks" (put them inside the quotation, names and figure labels too, or say it in your own words)')
    assert reasons.categorise("rejected", "bullet 1: " + problem) == ("format", True)
    assert long_run_problem('It says "the evolving outlook, and the balance of risks".', [FOMC_A, FOMC_B]) is None
    assert long_run_problem("Four words like the evolving outlook pass.", [FOMC_A, FOMC_B]) is None


def test_a_run_that_touches_a_quotation_joins_it_where_a_cited_passage_has_them_together():
    passage = ("In considering the extent and timing of additional adjustments to the target range, the Committee will "
               "carefully assess incoming data, the evolving outlook, and the balance of risks.")
    assert passage in FOMC_A
    after = 'The Committee "will carefully assess incoming data, the evolving outlook," and the balance of risks.'
    assert settle_runs(after, [passage], [FOMC_A]) == (
        'The Committee "will carefully assess incoming data, the evolving outlook, and the balance of risks".', 1)
    before = 'It notes that the Committee will carefully assess "incoming data, the evolving outlook".'
    assert settle_runs(before, [passage], [FOMC_A]) == (
        'It notes that "the Committee will carefully assess incoming data, the evolving outlook".', 1)
    alone = "It will now weigh the evolving outlook, and the balance of risks."
    assert settle_runs(alone, [passage], [FOMC_A]) == (
        'It will now weigh "the evolving outlook, and the balance of risks".', 1)
    elsewhere = "It will weigh the evolving outlook, and the balance of risks."     # not a cited passage's words: left
    assert settle_runs(elsewhere, ["Inflation remains somewhat elevated."], [FOMC_A]) == (elsewhere, 0)


def test_settled_runs_are_counted_and_the_checks_read_the_result():
    row = {"text": "The guidance now says the Committee will carefully assess incoming data, the evolving outlook, and the "
                   "balance of risks.",
           "quotes": [{"document": "previous", "text": "the Committee will carefully assess incoming data"}]}
    c = check("v17", row, CORE_FOMC)
    assert c.bullets[0].text.count('"') == 2 and c.copies["quoted"] == 1
    assert check("v16", row, CORE_FOMC).bullets[0].text == row["text"]               # v16 is checked as it ran


CORE_FOMC = {"text": 'The new statement says the Committee "judges that downside risks to employment have risen".',
             "quotes": [{"document": "new", "text": "judges that downside risks to employment have risen"}]}


def test_names_count_as_a_statements_words_unless_quoted():
    text = "Voting against this action was Stephen I. Miran, who preferred a larger cut."
    assert "Stephen I. Miran" in FOMC_B
    assert long_run_problem(text, [FOMC_A, FOMC_B]) is not None
    assert long_run_problem('"Voting against this action was Stephen I. Miran", who wanted more.', [FOMC_A, FOMC_B]) is None
    assert long_run_problem("Miran dissented in favour of a larger cut.", [FOMC_A, FOMC_B]) is None


# ---------------------------------------------------------------------------------------------- "adds" for reworded words
def test_round_15s_adds_for_a_reworded_sentence_fails_and_quoting_both_passes():
    assert check_rbi("v16", ADDS, CORE).ok                                            # as round 15 ran it
    c = check_rbi("v17", ADDS, CORE)
    assert not c.ok and c.bullets[1].problem is None
    assert c.bullets[0].problem.startswith('claims words were added that the previous statement already had: "actively '
                                           'disinflationary" is in its "Hence, monetary policy needs to remain actively '
                                           'disinflationary."')
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("citation", True)
    assert "say it was reworded, and quote that sentence too" in retry_note(c)
    assert check_rbi("v17", REWORDED, CORE).ok


def test_added_words_the_previous_statement_never_had_pass_and_so_does_a_bullet_that_claims_no_addition():
    assert check_rbi("v17", CORE, REWORDED).ok                                        # "now notes": core disinflation is new
    says = {**ADDS, "text": f'The new resolution says "{DISINFLATION}".'}
    assert added_problem(says["text"], [], {901: RBI_OLD}, 901, CH_RBI, SE_RBI) is None


# ---------------------------------------------------------------------------------------------- what a quoted sentence leans on
def test_round_15s_claim_from_the_sentence_a_quoted_one_refers_back_to_fails_however_common_its_words():
    assert check_rbi("v16", SHOCKS, CORE).ok                             # "supply" and "shocks" are in four sentences
    c = check_rbi("v17", SHOCKS, CORE)
    assert not c.ok and c.bullets[0].problem.startswith(
        'a claim resting on the sentence a quoted one refers back to: "supply" is from "While inflation has been above')
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("citation", True)
    whole = {"text": f'The resolution calls the factors "essentially supply shocks", which "{LOOKED}".',
             "quotes": [{"document": "new", "text": "essentially supply shocks"}, {"document": "new", "text": LOOKED}]}
    assert check_rbi("v17", whole, CORE).ok


@pytest.mark.parametrize("opening, leans", [
    ("Accordingly, they can be looked through.", True), ("These pressures persist.", True),
    ("Taking into account all these factors, the MPC decided.", True), ("8. Hence the MPC decided.", True),
    ("Inflation eased in October.", False), ("The MPC decided to keep the rate.", False)])
def test_what_counts_as_referring_back(opening, leans):
    assert bool(summaries._REFERS_BACK.match(opening)) is leans


def test_a_neighbours_common_word_still_passes_where_the_quoted_sentence_doesnt_refer_back():
    text = "Supply chains eased. Supply rose. Food supply improved. Prices of food fell."
    spans = text_spans(text)
    own = 'Food prices are said to have "fell" on supply.'
    assert neighbour_problem(own, [(1, *spans[3])], {1: text}, {1: spans}, {1: [spans[3]]}, refers_back=True) is None


# ---------------------------------------------------------------------------------------------- who voted for the action
def test_a_claim_about_who_voted_for_the_action_needs_its_voting_sentence():
    against = "Voting against this action was Stephen I. Miran"
    text = 'Miran is no longer among those voting for the action: "' + against + '".'
    assert vote_for_problem(text, [against + ", who preferred to lower the target range."]).startswith(
        'a claim about who voted for the action that quotes no sentence listing those votes: "among those voting"')
    assert vote_for_problem(text, ["Voting for the monetary policy action were Jerome H. Powell, Chair."]) is None
    assert vote_for_problem('The dissent is new: "' + against + '".', [against]) is None
    assert reasons.categorise("rejected", "bullet 1: " + vote_for_problem(text, [against])) == ("citation", True)


# ---------------------------------------------------------------------------------------------- the second model
def test_the_second_models_request_is_haiku_on_the_json_route_with_each_bullet_and_only_its_cited_sentences():
    c = check("v17", CORE_FOMC, {"text": 'The decision changed from "maintain" to "lower" the target range.',
                                 "quotes": [{"document": "previous", "text": "decided to maintain the target range"},
                                            {"document": "new", "text": "decided to lower the target range"}]})
    assert c.ok, c.reasons()
    v = verify_request(REQ, c, VERIFY)
    assert (v.task, v.model_id, v.spec.route, v.spec.effort, v.spec.max_tokens) == (
        "statement_diff_summary", HAIKU, "json", "medium", 16_000)
    assert v.prompt.version == summaries.VERIFY_VERSION == "verify2" and _vnum(v.prompt.version) < 0   # never pins a target
    assert v.schema == load_schema("statement_diff_summary", "verify2") and next(iter(v.schema["properties"])) == "verdicts"
    assert v.sources == REQ.sources and v.input_sha256 != REQ.input_sha256
    assert re.findall(r"^Bullet (\d+): ", v.instruction, flags=re.MULTILINE) == ["1", "2"]
    for b in c.bullets:
        for x in b.citations:
            src = FOMC_A if x.document_id == OLD.document_id else FOMC_B
            assert " ".join(src[x.start:x.end].split()) in v.instruction
    assert "Answer for each of the 2 bullets, in order" in v.instruction
    for rule in ("supported: is everything the bullet says stated in its cited sentences?",
                 "changes_true: is every claim of change true against both documents?",
                 "reads_well: does the bullet read as correct, plain English?",
                 "\"Added\" or \"new\" is false if the previous statement already said the same thing"):
        assert rule in VERIFY.text


def _two_ok():
    c = check("v17", CORE_FOMC, {"text": 'The decision changed from "maintain" to "lower" the target range.',
                                 "quotes": [{"document": "previous", "text": "decided to maintain the target range"},
                                            {"document": "new", "text": "decided to lower the target range"}]})
    assert c.ok
    return c


def test_an_objection_drops_its_bullet_with_the_reason_and_the_retry_reads_it():
    c = _two_ok()
    apply_verdicts(c, {"verdicts": [
        {"bullet": 1, "supported": True, "changes_true": True, "objection": ""},
        {"bullet": 2, "supported": True, "changes_true": False, "objection": "The previous statement also said lower."}]})
    assert c.bullets[0].problem is None
    assert c.bullets[1].problem == "a second model's check found: The previous statement also said lower."
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("citation", True)
    assert "a second model's check found: The previous statement also said lower." in retry_note(c)


def test_a_bullet_that_doesnt_read_as_plain_english_is_dropped_and_verify1s_answers_have_no_such_question():
    c = _two_ok()
    apply_verdicts(c, {"verdicts": [
        {"bullet": 1, "supported": True, "changes_true": True, "reads_well": False,
         "objection": 'It says the crisis "was said to "poses" risks", which isn\'t English.'},
        {"bullet": 2, "supported": True, "changes_true": True, "reads_well": True, "objection": ""}]})
    assert c.bullets[0].problem.startswith("a second model's check found: It says the crisis") and c.bullets[1].problem is None
    c = _two_ok()
    apply_verdicts(c, {"verdicts": [{"bullet": i, "supported": True, "changes_true": True, "objection": ""} for i in (1, 2)]})
    assert c.ok
    assert "reads_well" in load_schema("statement_diff_summary", "verify2")["properties"]["verdicts"]["items"]["required"]


def test_a_missing_verdict_or_an_answer_that_isnt_the_schemas_fails():
    c = _two_ok()
    apply_verdicts(c, {"verdicts": [{"bullet": 1, "supported": True, "changes_true": True, "objection": ""}]})
    assert c.bullets[1].problem == "the second model's check gave no answer for it"
    assert reasons.categorise("rejected", "bullet 2: " + c.bullets[1].problem) == ("citation", True)
    c = _two_ok()
    apply_verdicts(c, {"verdict": []})
    assert not c.ok and c.problems == ["not checked by a second model: its answer isn't the schema's JSON"]
    assert reasons.categorise("rejected", "rejected: " + "; ".join(c.reasons())) == ("format", True)
    assert reasons.categorise("rejected", "rejected: not checked by a second model: APITimeoutError") == ("other", True)
    c = _two_ok()
    apply_verdicts(c, {"verdicts": [{"bullet": True, "supported": True, "changes_true": True, "objection": ""},
                                    {"bullet": 2, "supported": True, "changes_true": True, "objection": ""}]})
    assert c.bullets[0].problem == "the second model's check gave no answer for it" and c.bullets[1].problem is None


def test_work_asks_the_second_model_only_when_every_bullet_passed():
    asked = []

    def verify(req, c):
        asked.append(req.prompt.version)
        apply_verdicts(c, {"verdicts": [{"bullet": i, "supported": True, "changes_true": True, "objection": ""}
                                        for i in range(1, len(c.bullets) + 1)]})
    w = Work("statement_diff_summary", NEW, OLD, D.segments, verify=verify)
    req = w.request(V17)
    good = reply([{"type": "text", "text": json.dumps({"bullets": [CORE_FOMC, CORE_FOMC]})}])
    assert w.check(req, good).ok and asked == ["v17"]
    bad = reply([{"type": "text", "text": json.dumps({"bullets": [CORE_FOMC]})}])      # one bullet: no second model
    assert not w.check(req, bad).ok and asked == ["v17"]
    assert w.check(w.request(V16), good).ok and asked == ["v17"]                      # v16 is checked as it ran
    alone = Work("statement_diff_summary", NEW, OLD, D.segments)
    c = alone.check(req, good)
    assert not c.ok and c.problems == ["not checked by a second model: no check was set up for this target"]


def test_the_second_models_answer_is_kept_in_the_outputs_content():
    c = _two_ok()
    c.verify = {"custom_id": "x", "model": HAIKU, "answer": {"verdicts": []}}
    content, _, _ = summaries.content(c, attempt=1, reply=reply([{"type": "text", "text": "{}"}]))
    assert content["verify"] == c.verify


# ---------------------------------------------------------------------------------------------- the fallback
def test_the_fallback_quotes_whole_changed_sentences_labelled_previous_or_new_with_their_citations():
    bullets = fallback_bullets(D.segments, OLD, NEW, [])
    assert 1 <= len(bullets) <= 5
    first = bullets[0]
    assert first.text.startswith('The previous FOMC statement said: "In support of its goals, the Committee decided to '
                                 'maintain the target range')
    assert '" The new FOMC statement says: "' in first.text
    texts = {OLD.document_id: FOMC_A, NEW.document_id: FOMC_B}
    for b in bullets:
        assert b.citations and all(x.how == "redline" for x in b.citations)
        for x in b.citations:
            assert (x.start, x.end) in CHANGED[x.document_id]
            assert " ".join(texts[x.document_id][x.start:x.end].split()) in b.text
        own = re.sub(r'"[^"]*"', "", b.text)
        assert long_run_problem(b.text, [FOMC_A, FOMC_B]) is None and "added" not in own and "adds" not in own


def test_the_fallback_follows_the_rejected_replys_quotes_first():
    miran = "Voting against this action was Stephen I. Miran"
    bullets = fallback_bullets(D.segments, OLD, NEW, [("new", miran)])
    assert miran in bullets[0].text and "The previous FOMC statement doesn't have this sentence." in bullets[0].text


def test_the_fallback_leaves_out_a_change_only_in_dates_and_shows_a_reworded_sentence_as_one_change():
    old = "The MPC met on 4 to 6 October 2023. Hence, monetary policy needs to remain actively disinflationary."
    new = ("The MPC met on 6 to 8 December 2023. Monetary policy must continue to be actively disinflationary to ensure "
           "anchoring of inflation expectations.")
    a = Doc(1, "mpc_resolution", "MPC resolution", "a", "RBI", date(2023, 10, 6), old)
    b = Doc(2, "mpc_resolution", "MPC resolution", "b", "RBI", date(2023, 12, 8), new)
    d = diff_texts(old, new)
    bullets = fallback_bullets(d.segments, a, b, [])
    assert len(bullets) == 1 and "met on" not in bullets[0].text
    assert bullets[0].text.startswith('The previous MPC resolution said: "Hence, monetary policy needs to remain actively '
                                      'disinflationary." The new MPC resolution says: "Monetary policy must continue')
    assert "doesn't have this sentence" not in bullets[0].text


def _pair(old: str, new: str):
    a = Doc(1, "mpc_resolution", "MPC resolution", "a", "RBI", date(2022, 5, 4), old)
    b = Doc(2, "mpc_resolution", "MPC resolution", "b", "RBI", date(2022, 6, 8), new)
    return diff_texts(old, new).segments, a, b


def test_a_changed_rate_or_projection_is_never_read_as_a_change_of_dates():
    # Round 16's first dev run: mpc-2022-06-08's fallback left out the 50 bp hike, its numbers read as day numbers.
    old = ("Increase the policy repo rate by 40 basis points to 4.40 per cent with immediate effect. "
           "Inflation is projected at 5.7 per cent in 2022-23. The MPC met on May 2 and 4, 2022.")
    new = ("Increase the policy repo rate by 50 basis points to 4.90 per cent with immediate effect. "
           "Inflation is projected at 6.7 per cent in 2022-23. The MPC met on June 6 and 8, 2022.")
    bullets = fallback_bullets(*_pair(old, new), [])
    assert len(bullets) == 2 and "4.90 per cent" in bullets[0].text and "6.7 per cent" in bullets[1].text
    assert not any("met on" in b.text for b in bullets)
    assert summaries._dates_only("The MPC met on May 2 and 4, 2022.", "The MPC met on June 6 and 8, 2022.")
    assert not summaries._dates_only("by 40 basis points to 4.40 per cent", "by 50 basis points to 4.90 per cent")


def test_a_change_the_redline_shows_twice_is_shown_once_and_the_decision_comes_first():
    stance_old = "The MPC also decided to remain accommodative while focusing on withdrawal of accommodation."
    stance_new = "The MPC also decided to remain focused on withdrawal of accommodation."
    old = (f"{stance_old} Growth is expected to hold up. Keep the policy repo rate unchanged at 4.00 per cent. "
           f"{stance_old}")
    new = (f"{stance_new} Growth is expected to hold up. Increase the policy repo rate by 40 basis points to 4.40 per cent. "
           f"{stance_new}")
    bullets = fallback_bullets(*_pair(old, new), [("new", stance_new)])
    assert [b.text.count("withdrawal") for b in bullets].count(2) == 1               # the stance once, not twice
    assert "4.40 per cent" in bullets[0].text                                      # the decision first, before the reply's


def test_two_sentences_the_redline_pairs_but_that_share_few_words_are_shown_apart():
    old = ("Dr. Ashima Goyal and Shri Shaktikanta Das voted to keep the policy repo rate unchanged at 6.50 per cent. "
           "Growth is expected to hold up.")
    new = ("The MPC members Dr. Ashima Goyal and Shri Shaktikanta Das attended the meeting. "
           "Growth is expected to hold up.")
    bullets = fallback_bullets(*_pair(old, new), [])
    texts = sorted(b.text for b in bullets)
    assert len(bullets) == 2 and all("doesn't have this sentence" in t for t in texts)
    assert not any("said:" in t and "says:" in t for t in texts)


def test_units_are_never_the_distinctive_pair_that_makes_an_addition_a_rewording():
    old = "The increase implies a direct impact of about 36 basis points on headline inflation."
    new = "It turned out to be lower by 30 basis points than projected."
    text = f'The new resolution adds that it "{new[:-1]}".'
    segs, _, _ = _pair(old, new)
    changed = dict(zip((1, 2), summaries.changed_spans(segs), strict=True))
    sentences = dict(zip((1, 2), sentence_spans(segs), strict=True))
    assert added_problem(text, [], {1: old, 2: new}, 1, changed, sentences) is None


def test_the_documents_everyday_words_are_never_the_pair_that_makes_an_addition_a_rewording():
    # Round 16's second dev run: "target range", "neutral stance", "policy rate" and "inflation outlook" rejected 8 replies.
    old = "The MPC also decided to retain the neutral stance to respond appropriately. Growth held up."
    new = "The MPC also decided to change the stance to calibrated tightening from the neutral stance. Growth held up."
    text = 'The new resolution now says the MPC decided "to change the stance to calibrated tightening from the neutral stance".'
    segs, _, _ = _pair(old, new)
    changed = dict(zip((1, 2), summaries.changed_spans(segs), strict=True))
    sentences = dict(zip((1, 2), sentence_spans(segs), strict=True))
    assert added_problem(text, [], {1: old, 2: new}, 1, changed, sentences) is None
    assert summaries._pairs("the target range for the federal funds rate and the policy rate") == set()
    assert ("actively", "disinflationary") in summaries._pairs("policy needs to remain actively disinflationary")


def test_the_fallback_skips_a_change_of_a_connective_and_quotes_no_sentence_twice():
    old = ("The SDF rate remains unchanged at 6.25 per cent. The MPC decided to keep the repo rate at 6.50 per cent. "
           "The MPC decided to retain the neutral stance. The MPC also decided to retain the neutral stance to respond.")
    new = ("Consequently, the SDF rate remains unchanged at 6.25 per cent. The MPC decided to raise the repo rate to 6.75 "
           "per cent. The MPC decided to change the stance to calibrated tightening.")
    bullets = fallback_bullets(*_pair(old, new), [])
    assert not any("SDF" in b.text for b in bullets)
    quoted = [x for b in bullets for x in b.citations if x.document_id == 2]
    assert len(quoted) == len(set(quoted))
    assert summaries._trivial("8. The rate is 5 per cent.", "9. Consequently, the rate is 5 per cent.")


def test_a_v17_target_gets_three_replies_and_every_other_target_two():
    t = ai_batch.AiBatch()
    w = Work("statement_diff_summary", NEW, OLD, D.segments)
    assert t._max_attempts(w, w.request(V17)) == 3 == ai_batch.DIFF_ATTEMPTS
    assert t._max_attempts(w, w.request(V16)) == 2
    doc = Work("doc_summary", NEW)
    assert t._max_attempts(doc, doc.request(load_prompt("doc_summary", "v1"))) == 2


def test_a_second_replys_note_says_which_answer_it_was_so_a_repeated_mistake_still_gets_its_third_reply():
    # CI on 3188aea: a reply that repeated its mistakes left the note the first had left, so the third reply was the second's
    # input again and was never sent (never twice); the target stayed "retried" with no fallback.
    from ft.ai.batch import BatchSteps, Call
    t = ai_batch.AiBatch()
    w = Work("statement_diff_summary", NEW, OLD, D.segments)
    first, second = Call("a", w.request(V17), 1), Call("b", w.request(V17, note="x."), 2)
    assert t._retry_note(first, "bullet 1 had a problem.") == "bullet 1 had a problem."
    note = t._retry_note(second, "bullet 1 had a problem.")
    assert note == "this was answer 2, and it still couldn't be published: bullet 1 had a problem."
    assert w.request(V17, note=note).input_sha256 != w.request(V17, note="bullet 1 had a problem.").input_sha256
    assert BatchSteps._retry_note(t, second, "same.") == "same."                 # every other task: unchanged


def test_the_fallbacks_model_says_no_model_wrote_it():
    assert ai_batch.FALLBACK_MODEL == "none: the changed sentences, quoted by code"
    assert Checked("statement_diff_summary", [], [], Counter()).verify is None


def test_the_fakes_default_diff_answer_passes_on_v17():
    from ai_fakes import good_diff_json
    docs = [(s.title, s.text) for s in REQ.sources]
    c = check("v17", *good_diff_json(docs)["bullets"])
    assert c.ok, c.reasons()
