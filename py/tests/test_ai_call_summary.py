"""The call summary's request and the checks on its reply (Spec 02B §5.6, §13.3 and Spec 02B.1 C10; job 2B-11, #174): headed lists
(management, analysts, and participants whose role couldn't be read), summaries.py's rules on every bullet (a verified citation,
only figures its passages hold), each list's count (which flexes on a role-held call), and the role rules from the call's turns.
A synthetic call (tests/call_fixtures.py); no database."""
from ai_fakes import bullets
from call_fixtures import (
    ANALYST_1,
    ANSWER_1,
    ANSWER_1B,
    ANSWER_2,
    GOOD_ANALYSTS,
    GOOD_MANAGEMENT,
    LINES,
    MODERATOR_Q1,
    OPENING,
    build,
    c,
    calldoc,
    lists,
    reply,
)

from ft.ai import call_summary as cs
from ft.ai import summaries
from ft.ai.call_summary import HEADINGS, Turn, answer_turns, check, ranges, roster
from ft.ai.client import TASKS, load_prompt
from ft.jobs.tasks.equity_calls_ai import PROMPT_VERSION
from ft.quality.reasons import categorise

PROMPT = load_prompt("call_summary", PROMPT_VERSION[cs.TASK])
B = build()
TEXT = B.text
QUESTION = "Can you explain the margin outlook for the second half?"
POOJA = "What is the plan for the dividend and the debt repayment?"
REVENUE = "Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."
THIRD = [("A participant asked about the plan for the dividend and the debt repayment.", [POOJA])]
FREIGHT = ("A participant said freight costs are lower than last year.", ["Freight costs are also lower than last year."])
# the analysts' bullets when Pooja Menon's turn is unreadable: only Anil Shah's question and the answers to it
KNOWN_ANALYSTS = [GOOD_ANALYSTS[0], ("Freight costs were also discussed.", ["Freight costs are also lower than last year."])]


def req(built=B):
    return cs.call_summary_request(calldoc(built), PROMPT)


def run(blocks, built=B):
    r = req(built)
    return check(reply(blocks), r.sources, built.turns, role_held=built.role_held)


def with_lines(role_of: dict[int, str]):
    """The call with some turns' roles changed."""
    return build([(sp, role_of.get(i, role), t) for i, (sp, role, t) in enumerate(LINES)])


ONE_UNKNOWN = with_lines({8: "unknown"})                                    # Pooja Menon's turn: about 8% of the words
ALL_UNKNOWN = with_lines({i: "unknown" for i in range(len(LINES))})
MOSTLY_UNKNOWN = with_lines({i: "unknown" for i in (4, 5, 6, 8, 9)})         # both analysts and their answers: about 40%
ALL_THIRD = [(f"A participant said {s}", [s]) for s in (REVENUE, "EBITDA margin was 21.3 per cent.",
                                                         "Our order book stands at 9,200 crore.")]


# --------------------------------------------------------------------------------------------------- the request
def test_the_request_is_one_plain_text_document_with_citations_on_and_names_the_speakers_and_two_lists():
    params = req().params(batch=True)
    (doc, instruction) = params["messages"][0]["content"]
    assert doc["type"] == "document" and doc["source"]["data"] == TEXT and doc["citations"] == {"enabled": True}
    assert doc["context"] == "Acme Limited · Earnings call · Q2 FY27 · 16 Oct 2026"
    text = instruction["text"]
    assert "- Management: Ravi Rao; Meera Iyer" in text and "- Analysts: Anil Shah; Pooja Menon" in text
    assert "- Moderator: Moderator" in text and "Role couldn't be read" not in text      # nobody's role is unread here
    assert f'"{HEADINGS[0]}" (3 to 6 bullets)' in text and f'"{HEADINGS[1]}" (2 to 4 bullets)' in text
    assert HEADINGS[2] not in text and "partly read" not in text
    assert TASKS["call_summary"].route == "citations" and TASKS["call_summary"].effort == "low"
    assert "cache_control" not in params and req().model_id == "claude-haiku-5-5"      # a batch request is uncached


def test_the_roster_sits_outside_the_cited_document_so_citations_map_to_our_text():
    (doc, instruction) = req(ONE_UNKNOWN).params(batch=True)["messages"][0]["content"]
    assert doc["source"]["data"] == ONE_UNKNOWN.text and "Pooja Menon" in instruction["text"]
    assert req(ONE_UNKNOWN).input_sha256 != req(B).input_sha256                          # the roster is part of the input


def test_a_call_with_an_unreadable_speaker_may_have_the_third_list_and_a_role_held_call_is_told_so():
    text = req(ONE_UNKNOWN).params(batch=True)["messages"][0]["content"][1]["text"]
    assert "- Role couldn't be read: Pooja Menon" in text and f'"{HEADINGS[2]}" (up to 4 bullets)' in text
    assert "only if those participants said something worth a bullet" in text and "partly read" not in text
    held = req(MOSTLY_UNKNOWN).params(batch=True)["messages"][0]["content"][1]["text"]
    assert f'"{HEADINGS[0]}" (up to 6 bullets)' in held and f'"{HEADINGS[1]}" (up to 4 bullets)' in held
    assert "roles are only partly read" in held and "at least 3 bullets in all" in held


def test_the_roster_lists_each_speaker_once_by_role_in_order_of_first_appearance():
    assert roster(B.turns) == [("Management", ["Ravi Rao", "Meera Iyer"]), ("Analysts", ["Anil Shah", "Pooja Menon"]),
                               ("Moderator", ["Moderator"])]
    assert roster(ALL_UNKNOWN.turns) == [("Role couldn't be read", ["Moderator", "Ravi Rao", "Meera Iyer", "Anil Shah",
                                                                    "Pooja Menon"])]
    assert roster([Turn(0, 0, 1, "", "unknown", "qa")]) == []                          # an unlabelled turn names no one


def test_the_counts_of_each_list_flex_on_a_role_held_call():
    assert ranges(role_held=False, has_unknown=False) == ((3, 6), (2, 4), (0, 0))
    assert ranges(role_held=False, has_unknown=True) == ((3, 6), (2, 4), (0, 4))
    assert ranges(role_held=True, has_unknown=True) == ((0, 6), (0, 4), (0, 4))
    assert not B.role_held and not ONE_UNKNOWN.role_held and MOSTLY_UNKNOWN.role_held and ALL_UNKNOWN.role_held


def test_the_prompt_names_the_three_headings_and_the_role_rules():
    p = PROMPT.text
    for h in HEADINGS:
        assert f'"{h}"' in p
    assert "only passages spoken by speakers listed as Management" in p and "never cites the moderator" in p
    assert "never guess a role" in p and "<!--" not in p and p.endswith("\n")
    assert "never a heading with no bullets under it" in p and "Never add, subtract, round, convert" in p
    assert "fewest bullets" in p and "Write no digit that isn't in a passage you cite" in p
    assert len({load_prompt("call_summary", v).text for v in ("v1", "v2", "v3", "v4")}) == 4  # each version that ran is kept


def test_v5_is_the_current_prompt_and_adds_cite_first_exact_figures_and_no_copied_transcript_text():
    """2B-11b (#284). v5 aims at v4's two largest dev rejection causes (bullets with no citation; figures the cited sentence
    lacks) and, by Anand's 5 Nov 2026 decision, at bullets that carried the transcript's own words. Every earlier version stays
    as it ran, and v4's own pinned rules (the test above) still hold for v5."""
    p = PROMPT.text
    assert PROMPT.version == "v5" == PROMPT_VERSION[cs.TASK]
    assert "find the passage first" in p and "Do not write a bullet first and look for support afterwards" in p
    assert "an analysts bullet cites the analyst's question" in p and "a bullet under the third heading cites" in p
    assert "Copy a figure character for character" in p and "INR130" in p and "ordinals (5th, 30th)" in p
    assert "never quote the transcript" in p and "never follow your sentence with the passage it rests on" in p
    assert "one sentence of your own of at most about 40 words" in p
    assert "Start your reply with the first heading line" in p and "no tags (such as <cite>)" in p
    assert "leave it out, heading included, and say nothing about it" in p
    assert len({load_prompt("call_summary", v).text for v in ("v1", "v2", "v3", "v4", "v5")}) == 5


# --------------------------------------------------------------------------------------------------- lists and bullets
def test_a_good_reply_passes_with_both_lists_cited_on_their_offsets():
    got = run(lists(TEXT))
    assert got.ok and [b.section for b in got.kept] == ["management"] * 3 + ["analysts"] * 2
    assert got.mapping == {"offsets": 3 + 4}                                          # 3 + (2 + 2) citations
    assert [len(b.citations) for b in got.kept] == [1, 1, 1, 2, 2]


def test_published_content_tags_each_bullet_with_its_list_and_numbers_the_citations():
    got = run(lists(TEXT))
    doc, body, cites = summaries.content(got, attempt=1, reply=reply(lists(TEXT)))
    assert [b["list"] for b in doc["bullets"]] == ["management"] * 3 + ["analysts"] * 2
    assert [b["citations"] for b in doc["bullets"]] == [[1], [2], [3], [4, 5], [6, 7]]
    assert [p for p, _ in cites] == ["/bullets/0", "/bullets/1", "/bullets/2", "/bullets/3", "/bullets/3",
                                     "/bullets/4", "/bullets/4"]
    assert body.splitlines()[0] == GOOD_MANAGEMENT[0][0] and len(body.splitlines()) == 5
    # module 1's content is unchanged: no list tag
    assert all("list" not in b for b in summaries.content(summaries.Checked("doc_summary", [], [], {}), attempt=1,
                                                          reply=reply(lists(TEXT)))[0]["bullets"])


def test_headings_are_read_whatever_their_markdown():
    for h in (("## What management said", "## What analysts asked about"),
              ("**What management said**", "**What analysts asked about**"),
              ("What management said:", "What analysts asked about:"),
              ("WHAT MANAGEMENT SAID", "what analysts asked about")):
        assert run(lists(TEXT, headings=h)).ok, h


def test_an_unknown_heading_is_text_outside_the_bullets_and_its_list_is_missing():
    got = run(lists(TEXT, headings=(HEADINGS[0], "Questions from analysts")))
    assert not got.ok and any(p.startswith("text outside the bullets") for p in got.problems)
    assert "0 bullets under 'What analysts asked about', not 2 to 4" in got.problems


def test_a_repeated_or_swapped_heading_fails_the_reply():
    swapped = run(lists(TEXT, headings=(HEADINGS[1], HEADINGS[0])))
    assert not swapped.ok and any("the lists aren't as asked" in p for p in swapped.problems)
    twice = run(lists(TEXT, headings=(HEADINGS[0], HEADINGS[0])))
    assert not twice.ok and any("the lists aren't as asked" in p for p in twice.problems)


def test_a_list_a_call_whose_roles_are_read_needs_cannot_be_left_out():
    got = run(lists(TEXT, analysts=()))
    assert not got.ok and got.problems == ["0 bullets under 'What analysts asked about', not 2 to 4"]
    got = run(lists(TEXT, management=()))
    assert got.problems == ["0 bullets under 'What management said', not 3 to 6"]


def test_bullets_before_the_first_heading_belong_to_no_list():
    blocks = [{"type": "text", "text": "- A bullet before any heading\n\n"}] + lists(TEXT)
    got = run(blocks)
    assert not got.ok and got.bullets[0].problem == "outside the lists" and got.bullets[0].section is None
    assert len(got.kept) == 5                                                           # the others are fine


def test_a_preamble_is_ignored_but_a_closing_remark_fails_the_reply():
    pre = [{"type": "text", "text": "Here is the summary of the call:\n\n"}] + lists(TEXT)
    assert run(pre).ok
    got = run(lists(TEXT) + [{"type": "text", "text": "\nI hope this helps."}])
    assert not got.ok and got.problems and got.problems[0].startswith("text outside the bullets")


def test_each_lists_count_must_be_in_range():
    got = run(lists(TEXT, management=GOOD_MANAGEMENT[:2]))
    assert not got.ok and got.problems == ["2 bullets under 'What management said', not 3 to 6"]
    many = GOOD_ANALYSTS + [(f"More {i}.", ["Our order book stands at 9,200 crore."]) for i in range(3)]
    got = run(lists(TEXT, analysts=many))
    assert any("bullets under 'What analysts asked about', not 2 to 4" in p for p in got.problems)


def test_a_bullet_without_a_verified_citation_or_with_a_foreign_number_is_dropped():
    uncited = [("The order book stands at 9,200 crore.", [])] + GOOD_MANAGEMENT[:2]
    got = run(lists(TEXT, management=uncited))
    assert not got.ok and got.dropped[0].problem == "no verified citation" and got.dropped[0].section == "management"
    foreign = [("Revenue grew 15 per cent.", [REVENUE])]
    got = run(lists(TEXT, management=foreign + GOOD_MANAGEMENT[1:]))
    assert got.dropped[0].problem == "numbers not in its cited passages: 15"
    other_quarter = [("Revenue grew in Q3 FY27.", [REVENUE])]
    got = run(lists(TEXT, management=other_quarter + GOOD_MANAGEMENT[1:]))
    assert "q3" in got.dropped[0].problem


def test_a_citation_of_text_that_is_not_in_our_copy_is_no_evidence():
    fake = c(TEXT, "EBITDA margin was 21.3 per cent.")
    fake["cited_text"] = "EBITDA margin was 99.9 per cent."
    blocks = lists(TEXT)
    blocks[4] = {**blocks[4], "citations": [fake]}                                     # the second management bullet
    got = run(blocks)
    assert not got.ok and got.dropped[0].problem == "no verified citation" and got.mapping["failed"] == 1


# --------------------------------------------------------------------------------------------------- the role rules
def test_answers_are_the_management_turns_that_follow_an_analyst_until_anyone_else_speaks():
    assert answer_turns(B.turns) == {ANSWER_1, ANSWER_1B, ANSWER_2}
    # the opening (after the moderator), the closing (after the moderator) and a moderator's hop are not answers
    assert OPENING not in answer_turns(B.turns) and 11 not in answer_turns(B.turns)
    hop = [Turn(0, 0, 1, "A", "analyst", "qa"), Turn(1, 1, 2, "M", "moderator", "qa"), Turn(2, 2, 3, "R", "management", "qa"),
           Turn(3, 3, 4, "U", "unknown", "qa"), Turn(4, 4, 5, "R", "management", "qa")]
    assert answer_turns(hop) == set()
    assert answer_turns(list(reversed(B.turns))) == answer_turns(B.turns)               # by turn number, not list order


def test_a_management_bullet_citing_an_analysts_question_or_the_moderator_is_dropped():
    bad = [(f"Management was asked: {QUESTION}", [QUESTION])] + GOOD_MANAGEMENT[1:]
    got = run(lists(TEXT, management=bad))
    assert got.dropped[0].problem == "a management bullet cites a turn whose role is analyst (management turns only)"
    mod = "Good day and welcome to the Acme Limited Q2 FY27 earnings conference call."
    got = run(lists(TEXT, management=[("The call was held for Q2 FY27.", [mod])] + GOOD_MANAGEMENT[1:]))
    assert "whose role is moderator" in got.dropped[0].problem


def test_a_management_bullet_may_cite_a_management_answer_in_the_q_and_a():
    ans = "We will repay 300 crore of debt this year and maintain the dividend payout."
    got = run(lists(TEXT, management=[("Debt repayment of 300 crore is planned this year.", [ans])] + GOOD_MANAGEMENT[1:]))
    assert got.ok


def test_an_analysts_bullet_may_cite_the_question_or_the_answer_that_follows_it_but_not_the_opening():
    bad = [("Revenue grew 12.5 per cent.", [REVENUE])] + GOOD_ANALYSTS[1:]
    got = run(lists(TEXT, analysts=bad))
    assert not got.ok and got.dropped[0].section == "analysts"
    assert got.dropped[0].problem == ("an analysts bullet cites a turn whose role is management (an analyst's question or "
                                      "the management answer that follows it only)")
    follow_on = "Freight costs are also lower than last year."                         # a second executive answering the question
    ok = [("Freight costs were also discussed.", [follow_on])] + GOOD_ANALYSTS[1:]
    assert run(lists(TEXT, analysts=ok)).ok


def test_a_turn_of_an_unreadable_role_cannot_support_the_management_or_analysts_lists():
    b = with_lines({ANSWER_1: "unknown"})                                             # who answers the first question isn't known
    ans = "We expect margins to stay near 21 per cent because input costs have eased."
    got = run(lists(b.text, management=[("Margins are expected near 21 per cent.", [ans])] + GOOD_MANAGEMENT[1:]), b)
    assert "whose role is unknown" in got.dropped[0].problem
    got = run(lists(b.text, analysts=[("The margin outlook.", [ans])] + GOOD_ANALYSTS[1:]), b)
    assert "whose role is unknown" in got.dropped[0].problem
    # the same call, the bullets resting on the known turns only: published
    kept = [("The margin outlook for the second half.", [QUESTION])] + GOOD_ANALYSTS[1:]
    assert run(lists(b.text, analysts=kept), b).ok


# --------------------------------------------------------------------------------------------------- the third list
def test_the_third_list_cites_only_turns_whose_role_couldnt_be_read():
    got = run(lists(ONE_UNKNOWN.text, analysts=KNOWN_ANALYSTS, unknown=THIRD), ONE_UNKNOWN)
    assert got.ok and [b.section for b in got.kept] == ["management"] * 3 + ["analysts"] * 2 + ["unknown"]
    bad = [("A participant said revenue grew 12.5 per cent.", [REVENUE])]
    got = run(lists(ONE_UNKNOWN.text, analysts=KNOWN_ANALYSTS, unknown=bad), ONE_UNKNOWN)
    assert not got.ok and got.dropped[0].section == "unknown" and got.dropped[0].problem == (
        "a bullet on participants whose role couldn't be read cites a turn whose role is management "
        "(turns whose role couldn't be read only)")


def test_the_third_list_is_optional_on_a_call_whose_roles_are_read_and_not_for_a_call_without_unknown_turns():
    assert run(lists(ONE_UNKNOWN.text, analysts=KNOWN_ANALYSTS), ONE_UNKNOWN).ok         # left out
    got = run(lists(TEXT, unknown=[("A participant asked about dividends.", [POOJA])]))
    assert not got.ok and f"'{HEADINGS[2]}' isn't for this call (no turn whose role couldn't be read)" in got.problems


def test_a_heading_with_no_bullets_under_it_fails_the_reply():
    blocks = lists(ONE_UNKNOWN.text, analysts=KNOWN_ANALYSTS) + [{"type": "text", "text": f"\n\n{HEADINGS[2]}\n"}]
    got = run(blocks, ONE_UNKNOWN)
    assert not got.ok and got.problems == [f"'{HEADINGS[2]}' has no bullets"]


def test_a_third_list_bullet_that_mixes_a_known_and_an_unknown_turn_fails():
    mixed = [("A participant asked about the debt repayment plan and management said 300 crore will be repaid.",
              [POOJA, "We will repay 300 crore of debt this year and maintain the dividend payout."])]
    got = run(lists(ONE_UNKNOWN.text, analysts=KNOWN_ANALYSTS, unknown=mixed), ONE_UNKNOWN)
    assert not got.ok and got.dropped[0].section == "unknown" and "whose role is management" in got.dropped[0].problem


def test_a_role_held_call_may_leave_lists_out_with_at_least_three_bullets_in_all():
    only_third = run(lists(ALL_UNKNOWN.text, management=(), analysts=(), unknown=ALL_THIRD), ALL_UNKNOWN)
    assert only_third.ok and [b.section for b in only_third.kept] == ["unknown"] * 3
    two = run(lists(ALL_UNKNOWN.text, management=(), analysts=(), unknown=ALL_THIRD[:2]), ALL_UNKNOWN)
    assert not two.ok and two.problems == ["2 bullets in all, not at least 3"]
    asked = ("A participant asked about the margin outlook for the second half.", [QUESTION])
    mixed = run(lists(MOSTLY_UNKNOWN.text, analysts=(), unknown=[asked, FREIGHT]), MOSTLY_UNKNOWN)
    assert mixed.ok and [b.section for b in mixed.kept] == ["management"] * 3 + ["unknown"] * 2


def test_on_a_role_held_call_a_management_or_analysts_bullet_still_needs_a_turn_of_that_role():
    got = run(lists(ALL_UNKNOWN.text), ALL_UNKNOWN)                                   # the usual two lists, nobody's role read
    assert not got.ok and len(got.dropped) == 5 and not got.kept
    assert all("whose role is unknown" in b.problem for b in got.dropped)


def test_a_reply_with_no_bullets_has_no_valid_form():
    got = run([{"type": "text", "text": "I can't summarise this."}], ALL_UNKNOWN)
    assert not got.ok and "no bullets (each bullet is a line starting with '- ')" in got.problems


# --------------------------------------------------------------------------------------------------- boundaries
def test_a_citation_that_runs_from_one_turn_into_the_next_fails_its_bullet():
    t = TEXT
    s = t.index("EBITDA margin was 21.3 per cent.")
    e = t.index("Moderator: The first question") + len("Moderator: The first question is from Anil Shah")
    crossing = {**c(t, "EBITDA margin was 21.3 per cent."), "cited_text": t[s:e], "start_char_index": s,
                "end_char_index": e}
    blocks = lists(TEXT)
    blocks[4] = {**blocks[4], "citations": [crossing]}
    got = run(blocks)
    assert got.dropped and "whose role is moderator" in got.dropped[0].problem


def test_a_citation_of_text_before_the_first_turn_fails_its_bullet():
    header = [("Acme Limited", "management", "Acme Limited Q2 FY27 earnings call transcript")] + LINES
    b = build(header)
    b = type(b)(b.text, b.turns[1:], b.extracted)                                     # the heading line has no turn
    got = run(lists(b.text, management=[("It is the Q2 FY27 call.", ["Acme Limited Q2 FY27 earnings call transcript"])]
                    + GOOD_MANAGEMENT[1:]), b)
    assert got.dropped[0].problem == "cites text outside any speaker turn"


def test_a_bullet_with_a_good_and_a_bad_citation_is_dropped_whole():
    mixed = [("Revenue grew and the question was asked.", [REVENUE, QUESTION])]
    got = run(lists(TEXT, management=mixed + GOOD_MANAGEMENT[1:]))
    assert not got.ok and got.dropped[0].problem.startswith("a management bullet cites a turn whose role is analyst")


def test_the_retry_note_names_what_was_dropped_and_the_retry_is_a_new_input():
    got = run(lists(TEXT, management=[(f"Management was asked: {QUESTION}", [QUESTION])] + GOOD_MANAGEMENT[1:]))
    note = got.note()
    assert 'bullet 1 ("Management was asked' in note and "whose role is analyst" in note
    first = cs.call_summary_request(calldoc(B), PROMPT)
    again = cs.call_summary_request(calldoc(B), PROMPT, note=note)
    assert first.input_sha256 != again.input_sha256 and again.params()["messages"][0]["content"][-1]["text"].startswith(
        "Your previous answer")


def test_every_reason_phrase_lands_in_its_accuracy_category():
    foreign_third = [("A participant said 12.5 per cent.", [REVENUE])]
    crafted = {
        "citation": [run(lists(TEXT, management=[("Asked: " + QUESTION, [QUESTION])] + GOOD_MANAGEMENT[1:])).reasons(),
                     run(lists(ONE_UNKNOWN.text, analysts=KNOWN_ANALYSTS, unknown=foreign_third), ONE_UNKNOWN).reasons(),
                     run(lists(TEXT, management=[("It is the call.", ["Acme Limited"])] + GOOD_MANAGEMENT[1:])).reasons()],
        "format": [run(lists(TEXT, management=GOOD_MANAGEMENT[:2])).reasons(),
                   run(lists(TEXT, analysts=())).reasons(),
                   run(lists(TEXT, headings=(HEADINGS[1], HEADINGS[0]))).reasons()[:1],       # the lists aren't as asked
                   run(lists(TEXT, unknown=[("A participant asked.", [POOJA])])).problems[-1:],  # isn't for this call
                   run(lists(ONE_UNKNOWN.text, analysts=KNOWN_ANALYSTS) + [{"type": "text", "text": f"\n\n{HEADINGS[2]}\n"}],
                       ONE_UNKNOWN).reasons(),
                   run(lists(ALL_UNKNOWN.text, management=(), analysts=(), unknown=ALL_THIRD[:2]), ALL_UNKNOWN).reasons(),
                   run([{"type": "text", "text": "- A bullet before any heading\n\n"}] + lists(TEXT)).reasons()],
        "numbers": [run(lists(TEXT, management=[("Revenue grew 15 per cent.", ["EBITDA margin was 21.3 per cent."])]
                              + GOOD_MANAGEMENT[1:])).reasons()],
    }
    for category, reason_sets in crafted.items():
        for reasons in reason_sets:
            assert reasons, category
            assert categorise("rejected", "rejected: " + "; ".join(reasons)) == (category, True), (category, reasons)
    stray = run(lists(TEXT) + [{"type": "text", "text": "\nHope this helps."}]).reasons()
    assert categorise("rejected", "; ".join(stray)) == ("format", True)


def test_the_estimated_size_of_a_transcript_is_what_the_limit_reads():
    big = calldoc(build(), 1)
    assert req().estimated_tokens() < cs.MAX_ESTIMATED_TOKENS < 100_000
    huge = type(big)(2, "x" * (cs.MAX_ESTIMATED_TOKENS * 3), big.title, big.company, big.fiscal_label, big.day, ())
    assert cs.call_summary_request(huge, PROMPT).estimated_tokens() > cs.MAX_ESTIMATED_TOKENS


def test_module_ones_summaries_are_unchanged_by_the_shared_scan():
    found, stray = summaries.split_bullets(reply(lists(TEXT)).parts())
    # without headings, the heading lines come before the first bullet only for the first list: later ones are stray
    assert [b for b, _ in found][:3] == [g[0] for g in GOOD_MANAGEMENT] and stray
    assert ANSWER_1 == 5 and MODERATOR_Q1 == 3 and ANALYST_1 == 4


# --------------------------------------------------------------------------------------------------- copied text (2B-11c, #301)
# Two long passages of the synthetic call: each is a management turn's text without its speaker label.
RAVI = "Thank you. Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27. EBITDA margin was 21.3 per cent."
MEERA = ("Our order book stands at 9,200 crore. Demand in the export market remains soft because of weak freight rates. "
         "We plan capital expenditure of 800 crore this year.")
LEAD = "The order book is 9,200 crore and capital expenditure of 800 crore is planned this year."
OWN = "Management described the outlook for demand and spending this year."       # a lead-in with no figure


def with_first(claim: str, cited: list[str]):
    """The good reply with the first management bullet replaced."""
    return run(lists(TEXT, management=[(claim, cited)] + GOOD_MANAGEMENT[1:]))


def test_the_copy_limits_are_named_constants_and_the_transcripts_are_long_enough_to_test_them():
    assert cs.MAX_ECHO_CHARS == 80 and cs.MIN_CUT_WORDS == 6 and cs.ECHO_TAIL_CHARS == 5
    assert TEXT.count(RAVI) == TEXT.count(MEERA) == 1 and len(RAVI) > 80 and len(MEERA) > 80


def test_a_copied_tail_is_cut_and_the_bullet_keeps_its_citations():
    got = with_first(f"{LEAD} {MEERA}", [MEERA])
    assert got.ok and got.kept[0].text == LEAD
    (cited,) = got.kept[0].citations
    assert (cited.start, cited.end) == span_of(MEERA) and len(got.kept[0].citations) == 1
    doc, body, cites = summaries.content(got, attempt=1, reply=reply(lists(TEXT)))
    assert doc["bullets"][0]["text"] == LEAD and body.splitlines()[0] == LEAD and cites[0][0] == "/bullets/0"
    assert [b.text for b in got.kept[1:3]] == [g[0] for g in GOOD_MANAGEMENT[1:]]             # the others are untouched


def test_a_cut_lead_in_loses_its_dangling_punctuation_and_quotes_and_ends_with_a_full_stop():
    for lead, tail in (("Management said the order book is 9,200 crore and capital expenditure is 800 crore:", f" {MEERA}"),
                       ("Management said the order book is 9,200 crore and capital expenditure is 800 crore,", f" {MEERA}"),
                       ("Management said the order book is 9,200 crore and capital expenditure is 800 crore \u2014", f" {MEERA}"),
                       ('Management said the order book is 9,200 crore and capital expenditure is 800 crore: "', f'{MEERA}"'),
                       ("Management said the order book is 9,200 crore and capital expenditure is 800 crore: \u201c",
                        f"{MEERA}\u201d")):
        got = with_first(lead + tail, [MEERA])
        assert got.ok and got.kept[0].text == ("Management said the order book is 9,200 crore and capital expenditure is "
                                               "800 crore."), lead


def test_two_copied_tails_in_a_row_are_both_cut():
    got = with_first(f"{LEAD} {RAVI} \u2026 {MEERA}", [RAVI, MEERA])
    assert got.ok and got.kept[0].text == LEAD and len(got.kept[0].citations) == 2
    got = with_first(f"{LEAD} {RAVI} {MEERA}", [RAVI, MEERA])                         # touching: one run, one cut
    assert got.ok and got.kept[0].text == LEAD


def test_copied_text_at_the_start_or_in_the_middle_fails_the_bullet():
    for claim in (f"{MEERA} {LEAD}", f"The company gave these figures: {MEERA} They cover the whole year."):
        got = with_first(claim, [MEERA])
        assert not got.ok and got.dropped[0].problem.startswith("copies transcript text"), claim
        assert got.dropped[0].text == claim                                         # the record keeps what the reply said
        assert "copies transcript text" in got.note() and categorise("rejected", "; ".join(got.reasons())) == ("format", True)


def test_a_bullet_that_is_only_the_passage_fails_and_so_does_a_lead_in_under_six_words():
    for claim in (MEERA, f"Capex is planned. {MEERA}", f"Order book and capex. {MEERA}"):
        got = with_first(claim, [MEERA])
        assert not got.ok and "copies transcript text" in got.dropped[0].problem, claim
    assert "under 6" in with_first(f"Capex is planned. {MEERA}", [MEERA]).dropped[0].problem
    assert with_first(f"Capex is planned for the year ahead now. {MEERA}", [MEERA]).ok              # six words stand


def test_a_figure_that_was_only_in_the_cut_tail_goes_with_it_and_the_rest_passes():
    got = with_first(f"{OWN} {MEERA}", [MEERA])
    assert got.ok and got.kept[0].text == OWN and "800" in MEERA and "800" not in got.kept[0].text


def test_a_cut_that_leaves_a_figure_the_passages_dont_hold_fails_the_bullet():
    transcript = summaries.match_form("A" + MEERA + "B")
    body = f"Capital expenditure is 700 crore and the order book is large this year. {MEERA}"
    echo = cs.apply_echo(body, [MEERA], transcript)
    assert echo.problem and echo.problem.startswith("copies transcript text") and "700" in echo.problem and echo.text == body


def test_a_passage_may_end_with_up_to_five_characters_of_punctuation_but_not_a_word():
    for tail, ok in ((MEERA + '."', True), (MEERA + " \u2026", True), (MEERA + ' )"..', True), (MEERA + " yes", False),
                     (MEERA + " ....!?", False)):
        got = with_first(f"{LEAD} {tail}", [MEERA])
        assert got.ok is ok, tail
        assert got.ok and got.kept[0].text == LEAD or not got.ok


def test_the_limit_is_eighty_characters_a_run_of_81_is_copied_and_a_run_of_80_is_not():
    words = " ".join(f"w{i}" for i in range(40))
    transcript = summaries.match_form("Q" + words + "Q")
    lead = "Management said the plan continues this year."
    for n, cut in ((79, False), (80, False), (81, True), (120, True)):
        body = f"{lead} {words[:n]}"
        echo = cs.apply_echo(body, [], transcript)
        assert echo.problem is None and echo.cut is cut, n
        assert echo.text == (lead if cut else body), n
        assert (summaries.copied_runs(summaries.match_form(body), transcript, 80) != []) is cut, n


def test_the_comparison_ignores_whitespace_curly_quotes_and_dashes_but_not_case():
    haystack = summaries.match_form('He said "we will repay 300 crore" - and then paused for the whole of the long second half of year.')
    same = "He said \u201cwe will  repay\n300 crore\u201d \u2014 and then paused for the whole of the long second half of year."
    assert summaries.copied_runs(summaries.match_form(same), haystack, 80) != []
    other_case = same.replace("He said", "he said").replace("and then", "And then").replace("whole", "Whole").replace("long", "Long")
    assert summaries.copied_runs(summaries.match_form(other_case), haystack, 80) == []
    assert summaries.longest_copied(summaries.match_form(same), haystack) == len(haystack)


def test_runs_that_overlap_or_touch_are_one_copied_stretch_and_the_longest_run_is_found():
    words = " ".join(f"w{i}" for i in range(60))
    transcript = summaries.match_form("Q" + words + "Q")
    text = "Plain lead in words here. " + words[:100] + " " + words[:100] + " tail"
    runs = summaries.copied_runs(text, transcript, 80)
    assert [(a, b) for a, b in runs] and all(b - a > 80 for a, b in runs)
    assert summaries.longest_copied(text, transcript) >= 100
    assert summaries.longest_copied("nothing here matches at all", transcript) < 10


def test_a_run_that_reaches_back_into_the_lead_in_cuts_where_the_cited_passage_begins():
    """The lead-in's last words equal the transcript's words before the passage, so the run starts inside the lead-in: the cut
    is where the cited passage begins, and the lead-in keeps its own last words."""
    passage = " ".join(f"w{i}" for i in range(30)) + "."
    transcript = summaries.match_form(f"Margin was 16.9%. {passage}")
    body = f"Management reported that the margin was 16.9%. {passage}"
    assert summaries.copied_runs(summaries.match_form(body), transcript, 80)[0][0] < body.index(passage)     # it reaches back
    echo = cs.apply_echo(body, ["Margin was 16.9%.", passage], transcript)
    assert echo.problem is None and echo.cut and echo.clean
    assert echo.text == "Management reported that the margin was 16.9%."


def test_a_run_that_starts_inside_a_word_of_the_lead_in_fails_rather_than_cut_the_word():
    passage = " ".join(f"w{i}" for i in range(30)) + "."
    transcript = summaries.match_form(f"Q marginal improvement in the margin {passage}")
    body = f"Management said unmarginal improvement in the margin {passage}"
    echo = cs.apply_echo(body, [], transcript)
    assert echo.problem and echo.problem.startswith("copies transcript text") and "inside a word" in echo.problem
    assert echo.text == body


def test_a_lead_in_with_no_clause_end_is_cut_where_the_run_starts_and_is_marked_unclean():
    passage = " ".join(f"w{i}" for i in range(30))
    transcript = summaries.match_form(f"Q{passage}Q")
    echo = cs.apply_echo(f"Management expects capital expenditure to be as follows {passage}", [], transcript)
    assert echo.problem is None and echo.cut and not echo.clean
    assert echo.text == "Management expects capital expenditure to be as follows."


def test_the_rule_runs_on_every_published_bullet_so_a_retry_sees_the_problem_in_its_note():
    got = with_first(f"The company gave these figures: {MEERA} They cover the whole year.", [MEERA])
    assert 'bullet 1 ("The company gave these figures: ' in got.note() and got.note().endswith(".")


def test_module_ones_summaries_are_checked_as_before_a_quoted_passage_over_80_characters_stands():
    from ft.ai.client import Source
    third = "Demand in the export market remains soft because of weak freight rates. We plan capital expenditure of 800 crore this year."
    quoted = [(p, [c(TEXT, p)]) for p in (RAVI, MEERA, third)]
    assert all(len(p) > 80 for p, _ in quoted)
    got = summaries.check("doc_summary", reply(bullets(quoted)), (Source(1, TEXT, "Acme Limited call"),))
    assert got.ok and [b.text for b in got.kept] == [p for p, _ in quoted]
    assert not hasattr(summaries.Bullet("x"), "cut")                                  # no new field on module 1's bullets


def span_of(snippet: str) -> tuple[int, int]:
    i = TEXT.index(snippet)
    return i, i + len(snippet)
