"""The call summary's structured route (2B-11c, #301; Anand's decision of 6 Nov 2026; prompts from v6): the model returns each
bullet as its own sentence plus, in a separate field, the passages that state its point; `check_json` finds each quote exactly once
in our copy of the transcript and the stored citation is that span. Everything else is as on the citations route (figures,
role rules, counts, the copied-text rule). A synthetic call (tests/call_fixtures.py); no database."""
import pytest
from ai_fakes import json_message
from anthropic.types import Message
from call_fixtures import (
    GOOD_ANALYSTS,
    GOOD_MANAGEMENT,
    LINES,
    build,
    calldoc,
    span,
)

from ft.ai import call_summary as cs
from ft.ai import summaries
from ft.ai.call_summary import LISTS, check_json, route_of
from ft.ai.client import TASKS, Reply, Request, Usage, load_prompt, load_schema
from ft.ai.guidance_extraction import marked_text
from ft.jobs.tasks import equity_calls_ai as eca
from ft.quality.reasons import categorise

PROMPT = load_prompt("call_summary", "v6")
SCHEMA = load_schema("call_summary", "v6")
B = build()
TEXT = B.text
CALL = calldoc(B)
QUESTION = "Can you explain the margin outlook for the second half?"
POOJA = "What is the plan for the dividend and the debt repayment?"
ANSWER = "We expect margins to stay near 21 per cent because input costs have eased."
RAVI = "Thank you. Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27. EBITDA margin was 21.3 per cent."
MEERA = ("Our order book stands at 9,200 crore. Demand in the export market remains soft because of weak freight rates. "
         "We plan capital expenditure of 800 crore this year.")
LEAD = "The order book is 9,200 crore and capital expenditure of 800 crore is planned this year."


def answer(management=GOOD_MANAGEMENT, analysts=GOOD_ANALYSTS, unknown=()):
    """A structured reply: one bullet per (claim, quotes) of each list."""
    return {"bullets": [{"list": kind, "text": claim, "quotes": list(quotes)}
                        for kind, items in zip(LISTS, (management, analysts, unknown), strict=True) for claim, quotes in items]}


def json_reply(got, stop: str = "end_turn") -> Reply:
    m = Message.model_validate(json_message(got, stop=stop))
    return Reply(m.model, m.id, m.stop_reason, Usage.of(m.usage), 0, list(m.content))


def run(got, call=CALL):
    return check_json(got, call)


def with_lines(role_of: dict[int, str]):
    return build([(sp, role_of.get(i, role), t) for i, (sp, role, t) in enumerate(LINES)])


ONE_UNKNOWN = with_lines({8: "unknown"})                                        # Pooja Menon's turn: about 8% of the words
MOSTLY_UNKNOWN = with_lines({i: "unknown" for i in (4, 5, 6, 8, 9)})            # both analysts and their answers: about 40%
KNOWN_ANALYSTS = [GOOD_ANALYSTS[0], ("Freight costs were also discussed.", ["Freight costs are also lower than last year."])]


def first_management(claim: str, quotes: list[str]):
    """The good reply with its first management bullet replaced."""
    return run(answer(management=[(claim, quotes)] + GOOD_MANAGEMENT[1:]))


# --------------------------------------------------------------------------------------------------- the route and the request
def test_prompts_from_v6_take_the_structured_route_and_v1_to_v5_the_citations_route():
    assert [route_of(f"v{n}") for n in range(1, 6)] == ["citations"] * 5
    assert [route_of(v) for v in ("v6", "v7", "v10")] == ["json"] * 3 and cs.JSON_FROM == 6
    assert eca.PROMPT_VERSION[cs.TASK] == "v6" and route_of(eca.PROMPT_VERSION[cs.TASK]) == "json"
    assert TASKS["call_summary"].route == "citations"          # the task's own route is unchanged: a request asks for the json one


def test_the_request_is_the_marked_transcript_without_citations_and_the_schema_as_the_output_format():
    r = cs.call_summary_request(CALL, PROMPT, schema=SCHEMA)
    assert r.spec.route == "json" and r.route == "json" and r.schema == SCHEMA and r.model_id == "claude-haiku-5-5"
    assert (r.spec.effort, r.spec.max_tokens) == ("low", 8_000)
    params = r.params(batch=True)
    (doc, instruction) = params["messages"][0]["content"]
    assert doc["type"] == "document" and "citations" not in doc and doc["source"]["data"] == marked_text(CALL)
    assert "[management · Ravi Rao]\nRavi Rao: Thank you." in doc["source"]["data"] and "[moderator]" in doc["source"]["data"]
    assert doc["context"] == "Acme Limited · Earnings call · Q2 FY27 · 16 Oct 2026"
    assert params["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    text = instruction["text"]
    assert "- Management: Ravi Rao; Meera Iyer" in text and "- Analysts: Anil Shah; Pooja Menon" in text
    assert '"management" (3 to 6 bullets)' in text and '"analysts" (2 to 4 bullets)' in text and '"unknown"' not in text
    assert "as the schema's JSON" in text and "partly read" not in text
    assert "cache_control" not in params and r.input_sha256 == cs.structured_request(CALL, PROMPT, SCHEMA).input_sha256


def test_the_marker_lines_are_not_in_the_text_the_quotes_are_found_in():
    assert marked_text(CALL) != TEXT and "[management" not in TEXT
    got = run(answer(management=[("Margins were discussed.", ["Ravi Rao: Thank you. Revenue from operations grew 12.5 per cent"])]
                     + GOOD_MANAGEMENT[1:]))
    assert got.kept[0].problem is None
    both = first_management("Management turned to the results.", ["[management · Ravi Rao]\nRavi Rao: Thank you."])
    assert both.dropped[0].problem.startswith("a quote (") and "isn't in the transcript exactly once" in both.dropped[0].problem


def test_the_third_list_and_a_role_held_call_are_asked_for_as_the_citations_route_asks():
    text = cs.call_summary_request(calldoc(ONE_UNKNOWN), PROMPT, schema=SCHEMA).instruction
    assert '"unknown" (up to 4 bullets)' in text and 'Write the "unknown" list only if' in text
    assert "- Role couldn't be read: Pooja Menon" in text
    held = cs.call_summary_request(calldoc(MOSTLY_UNKNOWN), PROMPT, schema=SCHEMA).instruction
    assert '"management" (up to 6 bullets)' in held and "partly read" in held and "at least 3 bullets in all" in held


def test_a_structured_request_needs_the_prompts_schema_and_an_earlier_prompt_is_unchanged():
    with pytest.raises(ValueError, match="needs the prompt's JSON schema"):
        cs.call_summary_request(CALL, PROMPT)
    old = cs.call_summary_request(CALL, load_prompt("call_summary", "v5"))
    assert old.route is None and old.spec.route == "citations" and old.schema is None
    assert old._canonical()["route"] == "citations" and cs.structured_request(CALL, PROMPT, SCHEMA)._canonical()["route"] == "json"
    with pytest.raises(ValueError, match="the json route needs a schema"):
        Request("call_summary", PROMPT, (), "x", route="json")
    assert TASKS["doc_summary"].route == TASKS["statement_diff_summary"].route == "citations"      # module 1 never takes the route


def test_the_retry_note_is_its_own_last_block_and_makes_a_new_input():
    got = first_management("Management gave the figure.", ["Acme Limited"])
    note = got.note()
    first = cs.structured_request(CALL, PROMPT, SCHEMA)
    again = cs.structured_request(CALL, PROMPT, SCHEMA, note=note)
    assert first.input_sha256 != again.input_sha256
    last = again.params()["messages"][0]["content"][-1]["text"]
    assert last.startswith("Your previous answer") and "Answer again as the schema's JSON" in last and note in last
    assert 'bullet 1 ("Management gave the figure.")' in note and "isn't in the transcript exactly once" not in note


def test_the_task_builds_and_checks_the_structured_request_for_a_summary_work():
    w = eca.Work(cs.TASK, CALL, "acme-q2", schema=SCHEMA)
    req = w.request(PROMPT)
    assert req.spec.route == "json" and req.input_sha256 == cs.structured_request(CALL, PROMPT, SCHEMA).input_sha256
    assert w.check(req, json_reply(answer())).ok
    assert w.check(req, json_reply("not json")).problems == ["the reply isn't the schema's JSON ({bullets: [...]})"]
    v5 = eca.Work(cs.TASK, CALL, "acme-q2")
    assert v5.request(load_prompt("call_summary", "v5")).spec.route == "citations"


# --------------------------------------------------------------------------------------------------- the prompt and its schema
def test_the_prompt_names_the_fields_the_role_rules_and_the_figure_rules():
    p = PROMPT.text
    assert PROMPT.version == "v6" and "<!--" not in p and p.endswith("\n")
    for field in ('"list"', '"text"', '"quotes"', '"management"', '"analysts"', '"unknown"'):
        assert field in p
    assert "never guess a role" in p and "never put one in a quote" in p and "Those marker lines are not part" in p
    assert "find the passage first" in p and "Do not write a bullet first and look for support afterwards" in p
    assert "copied character for character" in p and "must occur only once in the transcript" in p
    assert "never put a quote in the text" in p and "never copy a sentence or part of a sentence" in p
    assert "may quote only passages spoken by speakers listed as Management" in p and "never quotes the moderator" in p
    assert "Never add, subtract, round, convert" in p and "Write no digit that isn't in a quote of that bullet" in p
    assert "fewest bullets" in p and "of at most about 40 words" in p
    assert len({load_prompt("call_summary", f"v{n}").text for n in range(1, 7)}) == 6          # each version that ran is kept


def test_the_schema_is_one_list_of_bullets_with_a_list_text_and_quotes_and_nothing_else():
    assert SCHEMA["required"] == ["bullets"] and SCHEMA["additionalProperties"] is False
    item = SCHEMA["properties"]["bullets"]["items"]
    assert item["required"] == ["list", "text", "quotes"] and item["additionalProperties"] is False
    assert item["properties"]["list"]["enum"] == list(LISTS)
    assert item["properties"]["text"]["type"] == "string" and item["properties"]["quotes"]["items"]["type"] == "string"
    # structured outputs take no length or number limits: the code checks those
    assert not {"minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"} & set(str(SCHEMA).replace("'", " ").split())


# --------------------------------------------------------------------------------------------------- a good reply
def test_a_good_reply_passes_and_each_quote_is_stored_as_the_span_it_was_found_at():
    got = run(answer())
    assert got.ok and [b.section for b in got.kept] == ["management"] * 3 + ["analysts"] * 2
    assert got.mapping == {"search": 3 + 4} and [len(b.citations) for b in got.kept] == [1, 1, 1, 2, 2]
    first = got.kept[0].citations[0]
    assert (first.start, first.end, first.how) == (*span(TEXT, GOOD_MANAGEMENT[0][1][0]), "search")
    assert all(c.document_id == CALL.document_id for b in got.kept for c in b.citations)


def test_published_content_is_what_the_citations_route_stores():
    got = run(answer())
    doc, body, cites = summaries.content(got, attempt=1, reply=json_reply(answer()))
    assert [b["list"] for b in doc["bullets"]] == ["management"] * 3 + ["analysts"] * 2
    assert [b["citations"] for b in doc["bullets"]] == [[1], [2], [3], [4, 5], [6, 7]]
    assert [p for p, _ in cites] == ["/bullets/0", "/bullets/1", "/bullets/2", "/bullets/3", "/bullets/3",
                                     "/bullets/4", "/bullets/4"]
    assert body.splitlines()[0] == GOOD_MANAGEMENT[0][0] and len(body.splitlines()) == 5
    assert doc["citation_mapping"] == {"search": 7} and "quotes" not in str(doc)       # the model's copy of a quote is never stored


def test_the_lists_come_out_in_order_whatever_order_the_reply_gave_them():
    got = run({"bullets": list(reversed(answer()["bullets"]))})
    assert got.ok and [b.section for b in got.kept] == ["management"] * 3 + ["analysts"] * 2
    assert [b.text for b in got.kept[3:]] == [g[0] for g in reversed(GOOD_ANALYSTS)]       # reply order within a list


def test_a_quote_may_differ_from_the_transcript_in_spacing_only():
    spaced = "Revenue from operations  grew 12.5 per cent\nto 4,500 crore in Q2 FY27."
    got = first_management(GOOD_MANAGEMENT[0][0], [spaced])
    assert got.ok and span(TEXT, GOOD_MANAGEMENT[0][1][0]) == (got.kept[0].citations[0].start, got.kept[0].citations[0].end)


# --------------------------------------------------------------------------------------------------- the quotes
def test_a_quote_that_is_not_in_the_transcript_fails_its_bullet():
    got = first_management("Revenue grew.", ["Revenue from operations rose 12.5 per cent to 4,500 crore in Q2 FY27."])
    assert not got.ok and got.dropped[0].problem.startswith('a quote ("Revenue from operations rose')
    assert got.dropped[0].problem.endswith("isn't in the transcript exactly once") and got.mapping["failed"] == 1
    assert [b.problem for b in got.bullets[1:]] == [None] * 4


def test_a_quote_found_twice_is_not_evidence_and_an_empty_one_is_not_either():
    twice = first_management("Management thanked the participants.", ["Thank you."])
    assert twice.dropped[0].problem == 'a quote ("Thank you.") isn\'t in the transcript exactly once'
    empty = first_management("Management thanked the participants.", ["  "])
    assert empty.dropped[0].problem == 'a quote ("") isn\'t in the transcript exactly once'


def test_a_bullet_needs_one_to_three_quotes_and_each_at_most_400_characters():
    none = first_management("Revenue grew.", [])
    assert none.dropped[0].problem == "no verified citation"
    many = first_management("Revenue grew.", [GOOD_MANAGEMENT[0][1][0], GOOD_MANAGEMENT[1][1][0], GOOD_MANAGEMENT[2][1][0], ANSWER])
    assert many.dropped[0].problem == f"4 quotes, not 1 to {cs.MAX_QUOTES}" and cs.MAX_QUOTES == 3
    sentence = " ".join(f"segment{i} margin" for i in range(40))          # distinct words: no part of it occurs twice
    long_lines = [(sp, role, t) for sp, role, t in LINES]
    long_lines[1] = ("Ravi Rao", "management", "Ravi Rao: " + sentence + ". EBITDA margin was 21.3 per cent.")
    big = build(long_lines)
    quote = sentence + "."
    assert len(quote) > cs.MAX_QUOTE == 400
    got = run(answer(management=[("Management discussed margins.", [quote])] + GOOD_MANAGEMENT[1:]), calldoc(big))
    assert got.dropped[0].problem.startswith(f"a quote is over {cs.MAX_QUOTE} characters") and got.mapping["too_long"] == 1
    inside = quote[:350].rsplit(" ", 1)[0]
    assert len(inside) <= cs.MAX_QUOTE
    fine = run(answer(management=[("Management discussed margins.", [inside])] + GOOD_MANAGEMENT[1:]), calldoc(big))
    assert fine.ok


def test_the_same_passage_quoted_twice_is_one_citation():
    q = GOOD_MANAGEMENT[1][1][0]
    got = first_management("EBITDA margin was 21.3 per cent.", [q, q])
    assert got.ok and len(got.kept[0].citations) == 1
    assert got.mapping["search"] == run(answer()).mapping["search"] - len(GOOD_MANAGEMENT[0][1]) + 2     # both finds counted, one citation


# --------------------------------------------------------------------------------------------------- figures
def test_a_figure_the_quoted_passages_do_not_hold_fails_and_one_they_hold_passes():
    bad = first_management("Revenue grew 15 per cent.", ["EBITDA margin was 21.3 per cent."])
    assert bad.dropped[0].problem == "numbers not in its cited passages: 15"
    assert first_management("Revenue grew 12.5 per cent.", ["Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."]).ok
    label = first_management("Revenue grew in Q3.", ["Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."])
    assert label.dropped[0].problem == "numbers not in its cited passages: q3"


# --------------------------------------------------------------------------------------------------- roles
def test_a_management_bullet_quoting_an_analysts_question_or_the_moderator_fails():
    asked = first_management(f"Management was asked: {QUESTION}", [QUESTION])
    assert asked.dropped[0].problem == "a management bullet cites a turn whose role is analyst (management turns only)"
    moderator = first_management("The call began with a welcome.", ["Good day and welcome to the Acme Limited Q2 FY27 earnings conference call."])
    assert "role is moderator" in moderator.dropped[0].problem


def test_a_quote_that_runs_from_a_turn_into_the_next_of_another_role_fails_its_bullet():
    across = f"{QUESTION} Ravi Rao: {ANSWER}"
    got = first_management("Management expects margins to hold.", [across])
    assert got.dropped[0].problem == "a management bullet cites a turn whose role is analyst (management turns only)"
    opening = first_management("Acme held a call.", ["Acme Limited"])
    assert opening.dropped[0].problem == "a management bullet cites a turn whose role is moderator (management turns only)"


def test_an_analysts_bullet_may_quote_the_question_and_the_answer_that_follows_it_but_not_the_opening():
    ok = run(answer(analysts=[("The margin outlook was asked about.", [QUESTION, ANSWER])] + GOOD_ANALYSTS[1:]))
    assert ok.ok
    opening = run(answer(analysts=[("Revenue was discussed.", ["EBITDA margin was 21.3 per cent."])] + GOOD_ANALYSTS[1:]))
    assert opening.dropped[0].problem == ("an analysts bullet cites a turn whose role is management "
                                          "(an analyst's question or the management answer that follows it only)")


def test_the_third_list_quotes_only_turns_whose_role_could_not_be_read():
    third = [("A participant asked about the plan for the dividend and the debt repayment.", [POOJA])]
    ok = run(answer(analysts=KNOWN_ANALYSTS, unknown=third), calldoc(ONE_UNKNOWN))
    assert ok.ok and [b.section for b in ok.kept][-1] == "unknown"
    foreign = run(answer(analysts=KNOWN_ANALYSTS, unknown=[("A participant said 12.5 per cent.", [GOOD_MANAGEMENT[0][1][0]])]),
                  calldoc(ONE_UNKNOWN))
    assert "role is management" in foreign.dropped[0].problem
    assert run(answer(analysts=[("An analyst asked.", [POOJA])] + KNOWN_ANALYSTS[1:]), calldoc(ONE_UNKNOWN)).dropped[0].problem.startswith(
        "an analysts bullet cites a turn whose role is unknown")


# --------------------------------------------------------------------------------------------------- the reply as a whole
def test_the_count_of_each_list_must_be_in_range():
    two = run(answer(management=GOOD_MANAGEMENT[:2]))
    assert two.problems == ["2 bullets in the list 'management', not 3 to 6"]
    seven = run(answer(management=GOOD_MANAGEMENT * 3))
    assert seven.problems == ["9 bullets in the list 'management', not 3 to 6"]
    no_analysts = run(answer(analysts=()))
    assert no_analysts.problems == ["0 bullets in the list 'analysts', not 2 to 4"]


def test_the_third_list_is_for_a_call_with_unreadable_speakers_only():
    third = [("A participant asked about the plan for the dividend and the debt repayment.", [POOJA])]
    assert run(answer(unknown=third)).problems == ["the list 'unknown' isn't for this call (no turn whose role couldn't be read)"]
    assert run(answer(analysts=KNOWN_ANALYSTS), calldoc(ONE_UNKNOWN)).ok            # optional where there are unreadable turns


def test_a_role_held_call_may_leave_lists_out_with_at_least_three_bullets_in_all():
    held = calldoc(MOSTLY_UNKNOWN)
    assert held.role_held
    third = [(f"A participant said {s}", [s]) for s in ("Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27.",
                                                       "EBITDA margin was 21.3 per cent.")]
    assert run(answer(management=GOOD_MANAGEMENT[:1], analysts=(), unknown=third[:1]), held).problems == [
        "2 bullets in all, not at least 3"]
    assert run(answer(management=GOOD_MANAGEMENT[:1], analysts=(), unknown=third[:2]), held).problems == []


def test_a_reply_that_is_not_the_schemas_json_or_has_a_malformed_bullet_fails():
    for bad in (None, "text", [], {"bullets": "x"}, {"other": []}):
        assert run(bad).problems == ["the reply isn't the schema's JSON ({bullets: [...]})"], bad
    assert run({"bullets": []}).problems[0] == "no bullets (the reply lists none)"
    rows = answer()["bullets"]
    got = run({"bullets": [rows[0], "x", {**rows[1], "list": "board"}, {**rows[2], "text": ""}, {**rows[3], "quotes": "x"},
                           {**rows[4], "quotes": [3]}, {**rows[0], "list": None}]})
    # not an object / an unknown list / no list at all (they sort after the lists); then one good bullet and three malformed ones
    assert [b.problem for b in got.bullets if b.section is None] == ["isn't the schema's object", "outside the lists",
                                                                     "outside the lists"]
    assert [b.problem for b in got.bullets if b.section] == [None, "isn't the schema's object", "isn't the schema's object",
                                                           "isn't the schema's object"]


# --------------------------------------------------------------------------------------------------- copied text (80 characters, kept)
def test_a_copied_tail_in_the_text_is_cut_and_the_bullet_keeps_its_citations():
    got = first_management(f"{LEAD} {MEERA}", [MEERA])
    assert got.ok and got.kept[0].text == LEAD and len(got.kept[0].citations) == 1
    assert [b.text for b in got.kept[1:3]] == [g[0] for g in GOOD_MANAGEMENT[1:]]


def test_copied_text_at_the_start_fails_the_bullet():
    got = first_management(f"{MEERA} That is the plan for this year.", [MEERA])
    assert got.dropped[0].problem.startswith("copies transcript text")


def test_the_copy_limit_is_eighty_characters_through_check_json_too():
    words = " ".join(f"w{i}" for i in range(40))
    transcript = summaries.match_form("Q" + words + "Q")
    lead = "Management said the plan continues this year."
    assert cs.MAX_ECHO_CHARS == 80
    for n, cut in ((80, False), (81, True)):
        echo = cs.apply_echo(f"{lead} {words[:n]}", [], transcript)
        assert echo.cut is cut and echo.problem is None
    # and the rule runs on a structured bullet. The space before the tail matches the transcript too (it follows "Meera Iyer:"),
    # so a run is the tail plus one character: 80 characters of tail are cut, 78 are kept
    assert summaries.match_form(MEERA[:80]) in summaries.match_form(TEXT)
    cut = first_management(f"{LEAD} {MEERA[:80]}", [MEERA])
    assert cut.ok and cut.kept[0].text == LEAD
    kept = first_management(f"{LEAD} {MEERA[:78]}", [MEERA])
    assert kept.ok and kept.kept[0].text == f"{LEAD} {MEERA[:78]}"


# --------------------------------------------------------------------------------------------------- the accuracy page
def test_every_new_reason_phrase_lands_in_its_accuracy_category():
    third = [("A participant asked about the plan for the dividend and the debt repayment.", [POOJA])]
    crafted = {
        "citation": [first_management("Revenue grew.", ["Revenue rose steadily in the quarter."]).reasons(),
                     first_management("Revenue grew.", ["Thank you."]).reasons(),
                     first_management(f"Management was asked: {QUESTION}", [QUESTION]).reasons(),
                     first_management("Revenue grew.", ["x" * 20 + TEXT[:500]]).reasons(),
                     first_management("Revenue grew.", []).reasons()],
        "format": [run("text").reasons(), run({"bullets": []}).reasons(), run(answer(management=GOOD_MANAGEMENT[:2])).reasons(),
                   run(answer(unknown=third)).problems, run({"bullets": [*answer()["bullets"], "x"]}).reasons(),
                   run({"bullets": [{**answer()["bullets"][0], "list": "board"}, *answer()["bullets"][1:]]}).reasons(),
                   first_management("Revenue grew.", answer()["bullets"][1]["quotes"] * 4).reasons(),
                   first_management(f"{MEERA} That is the plan.", [MEERA]).reasons()],
        "numbers": [first_management("Revenue grew 15 per cent.", ["EBITDA margin was 21.3 per cent."]).reasons()],
    }
    for category, reason_sets in crafted.items():
        for reasons in reason_sets:
            assert reasons, category
            assert categorise("rejected", "rejected: " + "; ".join(reasons)) == (category, True), (category, reasons)
