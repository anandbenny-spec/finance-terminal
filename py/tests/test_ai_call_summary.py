"""The call summary's request and the checks on its reply (Spec 02B §5.6, §13.3; job 2B-11, #174): two headed lists, summaries.py's
rules on every bullet (a verified citation, only figures its passages hold, the counts), and the role rules from the call's
turns (a management bullet cites only management turns; an analysts bullet an analyst's turn or the management answer that
follows it). A synthetic call (tests/call_fixtures.py); no database."""
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
from ft.ai.call_summary import HEADINGS, Turn, answer_turns, check
from ft.ai.client import TASKS, load_prompt
from ft.quality.reasons import categorise

B = build()
TEXT = B.text
REQ = cs.call_summary_request(calldoc(B), load_prompt("call_summary"))


def run(blocks, built=B):
    r = cs.call_summary_request(calldoc(built), load_prompt("call_summary"))
    return check(reply(blocks), r.sources, built.turns)


def with_lines(role_of: dict[int, str]):
    """The call with some turns' roles changed."""
    return build([(sp, role_of.get(i, role), t) for i, (sp, role, t) in enumerate(LINES)])


def test_the_request_is_one_plain_text_document_with_citations_on_and_names_both_lists():
    params = REQ.params(batch=True)
    (doc, instruction) = params["messages"][0]["content"]
    assert doc["type"] == "document" and doc["source"]["data"] == TEXT and doc["citations"] == {"enabled": True}
    assert doc["context"] == "Acme Limited · Earnings call · Q2 FY27 · 16 Oct 2026"
    assert HEADINGS[0] in instruction["text"] and HEADINGS[1] in instruction["text"]
    assert "3 to 6" in instruction["text"] and "2 to 4" in instruction["text"]
    assert TASKS["call_summary"].route == "citations" and TASKS["call_summary"].effort == "low"
    assert "cache_control" not in params                                   # a batch request is uncached
    assert REQ.model_id == "claude-haiku-5-5"


def test_the_prompt_names_the_two_headings_and_the_role_rules():
    p = load_prompt("call_summary").text
    assert f'"{HEADINGS[0]}"' in p and f'"{HEADINGS[1]}"' in p
    assert "only passages spoken by management" in p and "Don't cite the moderator" in p
    assert "<!--" not in p and p.endswith("\n")


def test_a_good_reply_passes_with_both_lists_cited_on_their_offsets():
    got = run(lists(TEXT))
    assert got.ok and [b.section for b in got.kept] == ["management"] * 3 + ["analysts"] * 2
    assert got.mapping == {"offsets": 3 + 4}                               # 3 + (2 + 2) citations
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


def test_an_unknown_heading_is_text_outside_the_bullets():
    got = run(lists(TEXT, headings=(HEADINGS[0], "Questions from analysts")))
    assert not got.ok and any("the two lists aren't there" in p for p in got.problems)
    assert any(p.startswith("text outside the bullets") for p in got.problems)


def test_a_missing_or_repeated_or_swapped_heading_fails_the_reply():
    swapped = run(lists(TEXT, headings=(HEADINGS[1], HEADINGS[0])))
    assert not swapped.ok and any("the two lists aren't there" in p for p in swapped.problems)
    one = lists(TEXT)[: 1 + 2 * len(GOOD_MANAGEMENT)]                       # only the management list
    got = run(one)
    assert not got.ok and any("the two lists aren't there" in p for p in got.problems)
    assert any("0 bullets under 'What analysts asked about', not 2 to 4" in p for p in got.problems)
    twice = lists(TEXT, headings=(HEADINGS[0], HEADINGS[0]))
    assert not run(twice).ok


def test_bullets_before_the_first_heading_belong_to_no_list():
    blocks = [{"type": "text", "text": "- A bullet before any heading\n\n"}] + lists(TEXT)
    got = run(blocks)
    assert not got.ok and got.bullets[0].problem == "outside the two lists" and got.bullets[0].section is None
    assert len(got.kept) == 5                                              # the others are fine


def test_a_preamble_is_ignored_but_a_closing_remark_fails_the_reply():
    pre = [{"type": "text", "text": "Here is the summary of the call:\n\n"}] + lists(TEXT)
    assert run(pre).ok
    post = lists(TEXT) + [{"type": "text", "text": "\nI hope this helps."}]
    got = run(post)
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
    foreign = [("Revenue grew 15 per cent.", ["Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."])]
    got = run(lists(TEXT, management=foreign + GOOD_MANAGEMENT[1:]))
    assert got.dropped[0].problem == "numbers not in its cited passages: 15"
    other_quarter = [("Revenue grew in Q3 FY27.", ["Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."])]
    got = run(lists(TEXT, management=other_quarter + GOOD_MANAGEMENT[1:]))
    assert "q3" in got.dropped[0].problem


def test_a_citation_of_text_that_is_not_in_our_copy_is_no_evidence():
    fake = c(TEXT, "EBITDA margin was 21.3 per cent.")
    fake["cited_text"] = "EBITDA margin was 99.9 per cent."
    blocks = lists(TEXT)
    blocks[4] = {**blocks[4], "citations": [fake]}                          # the second management bullet
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
    assert answer_turns(list(reversed(B.turns))) == answer_turns(B.turns)    # by turn number, not list order


def test_a_management_bullet_citing_an_analysts_question_or_the_moderator_is_dropped():
    q = "Can you explain the margin outlook for the second half?"
    bad = [(f"Management was asked: {q}", [q])] + GOOD_MANAGEMENT[1:]
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
    opening = "Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."
    bad = [("Revenue grew 12.5 per cent.", [opening])] + GOOD_ANALYSTS[1:]
    got = run(lists(TEXT, analysts=bad))
    assert not got.ok and got.dropped[0].section == "analysts"
    assert got.dropped[0].problem == ("an analysts bullet cites a turn whose role is management (an analyst's question or "
                                      "the management answer that follows it only)")
    follow_on = "Freight costs are also lower than last year."                  # a second executive answering the question
    ok = [("Freight costs were also discussed.", [follow_on])] + GOOD_ANALYSTS[1:]
    assert run(lists(TEXT, analysts=ok)).ok


def test_a_turn_of_an_unreadable_role_cannot_support_either_list():
    b = with_lines({ANSWER_1: "unknown"})                                      # who answers the first question isn't known
    ans = "We expect margins to stay near 21 per cent because input costs have eased."
    got = run(lists(b.text, management=[("Margins are expected near 21 per cent.", [ans])] + GOOD_MANAGEMENT[1:]), b)
    assert "whose role is unknown" in got.dropped[0].problem
    got = run(lists(b.text, analysts=[("The margin outlook.", [ans])] + GOOD_ANALYSTS[1:]), b)
    assert "whose role is unknown" in got.dropped[0].problem
    # the same call, the bullets resting on the known turns only: published
    kept = [("The margin outlook for the second half.", ["Can you explain the margin outlook for the second half?"])
            ] + GOOD_ANALYSTS[1:]
    assert run(lists(b.text, analysts=kept), b).ok


def test_a_call_with_no_readable_roles_can_only_be_rejected():
    b = with_lines({i: "unknown" for i in range(len(LINES))})
    got = run(lists(b.text), b)
    assert not got.ok and len(got.dropped) == 5 and not got.kept


def test_a_citation_that_runs_from_one_turn_into_the_next_fails_its_bullet():
    t = TEXT
    s = t.index("EBITDA margin was 21.3 per cent.")
    e = t.index("Moderator: The first question") + len("Moderator: The first question is from Anil Shah")
    crossing = {**c(t, "EBITDA margin was 21.3 per cent."), "cited_text": t[s:e], "start_char_index": s,
                "end_char_index": e}
    blocks = lists(TEXT)
    blocks[4] = {**blocks[4], "citations": [crossing]}
    got = run(blocks)
    assert got.dropped and "whose role is moderator" in got.dropped[0].problem or "whose role is management" in got.dropped[0].problem


def test_a_citation_of_text_before_the_first_turn_fails_its_bullet():
    header = [("Acme Limited", "management", "Acme Limited Q2 FY27 earnings call transcript")] + LINES
    b = build(header)
    b = type(b)(b.text, b.turns[1:], b.extracted)                               # the heading line has no turn
    got = run(lists(b.text, management=[("It is the Q2 FY27 call.", ["Acme Limited Q2 FY27 earnings call transcript"])]
                    + GOOD_MANAGEMENT[1:]), b)
    assert got.dropped[0].problem == "cites text outside any speaker turn"


def test_a_bullet_with_a_good_and_a_bad_citation_is_dropped_whole():
    mixed = [("Revenue grew and the question was asked.", ["Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27.",
                                                           "Can you explain the margin outlook for the second half?"])]
    got = run(lists(TEXT, management=mixed + GOOD_MANAGEMENT[1:]))
    assert not got.ok and got.dropped[0].problem.startswith("a management bullet cites a turn whose role is analyst")


def test_the_retry_note_names_what_was_dropped_and_the_retry_is_a_new_input():
    q = "Can you explain the margin outlook for the second half?"
    got = run(lists(TEXT, management=[(f"Management was asked: {q}", [q])] + GOOD_MANAGEMENT[1:]))
    note = got.note()
    assert 'bullet 1 ("Management was asked' in note and "whose role is analyst" in note
    first = cs.call_summary_request(calldoc(B), load_prompt("call_summary"))
    again = cs.call_summary_request(calldoc(B), load_prompt("call_summary"), note=note)
    assert first.input_sha256 != again.input_sha256 and again.params()["messages"][0]["content"][-1]["text"].startswith(
        "Your previous answer")


def test_every_reason_phrase_lands_in_its_accuracy_category():
    q = "Can you explain the margin outlook for the second half?"
    crafted = {
        "citation": [run(lists(TEXT, management=[("Asked: " + q, [q])] + GOOD_MANAGEMENT[1:])).reasons(),
                     run(lists(with_lines({}).text, management=[("It is the call.", ["Acme Limited"])] + GOOD_MANAGEMENT[1:])
                         ).reasons()],
        "format": [run(lists(TEXT, management=GOOD_MANAGEMENT[:2])).reasons(),
                   run(lists(TEXT)[: 1 + 2 * len(GOOD_MANAGEMENT)]).reasons(),            # no analysts list
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
    assert REQ.estimated_tokens() < cs.MAX_ESTIMATED_TOKENS < 100_000
    huge = type(big)(2, "x" * (cs.MAX_ESTIMATED_TOKENS * 3), big.title, big.company, big.fiscal_label, big.day, ())
    assert cs.call_summary_request(huge, load_prompt("call_summary")).estimated_tokens() > cs.MAX_ESTIMATED_TOKENS


def test_module_ones_summaries_are_unchanged_by_the_shared_scan():
    found, stray = summaries.split_bullets(reply(lists(TEXT)).parts())
    # without headings, the heading lines come before the first bullet only for the first list: later ones are stray
    assert [b for b, _ in found][:3] == [g[0] for g in GOOD_MANAGEMENT] and stray
    assert ANSWER_1 == 5 and MODERATOR_Q1 == 3 and ANALYST_1 == 4
