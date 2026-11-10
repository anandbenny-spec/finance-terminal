"""call_summary v11 (#178, the Opus round, 10 Nov 2026; Anand's decision of 10 Nov, picks 1 to 6 of the Fable review): v9's text plus the
picks, written by Opus 5.5 at effort high with 16,000 tokens, and checked by one passage per claim (F, G) and five word checks (A to E)
from `STRICT_FROM` on. A synthetic call (tests/call_fixtures.py); no database. The earlier versions' behaviour is pinned here too: a
reply checked without a version, or under v9 or v10, is checked exactly as before."""
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from ai_fakes import json_message
from anthropic.types import Message
from call_fixtures import GOOD_ANALYSTS, GOOD_MANAGEMENT, LINES, build, calldoc

from ft.ai import call_summary as cs
from ft.ai.call_summary import LISTS, CallDoc, check_json, strict_of
from ft.ai.client import (
    EFFORTS,
    HAIKU,
    OPUS,
    SONNET,
    TASKS,
    NoPrice,
    Reply,
    Request,
    TaskSpec,
    Usage,
    cost_usd,
    load_prompt,
    load_schema,
    price_entry,
)
from ft.jobs.tasks import equity_calls_ai as eca
from ft.quality.reasons import categorise

V9, V11 = load_prompt("call_summary", "v9"), load_prompt("call_summary", "v11")
S9, S11 = load_schema("call_summary", "v9"), load_schema("call_summary", "v11")
B = build()
TEXT = B.text
CALL = calldoc(B)
REVENUE = "Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."
EBITDA = "EBITDA margin was 21.3 per cent."
CLOSING = "Thank you all for joining us."                       # the closing turn: under 40 characters
QUESTION = "Can you explain the margin outlook for the second half?"


def answer(management=GOOD_MANAGEMENT, analysts=GOOD_ANALYSTS, unknown=()):
    """A structured reply: one bullet per (claim, quotes) of each list."""
    return {"bullets": [{"list": kind, "text": claim, "quotes": list(quotes)}
                        for kind, items in zip(LISTS, (management, analysts, unknown), strict=True) for claim, quotes in items]}


def v11(got, call=CALL):
    return check_json(got, call, version="v11")


def first(claim: str, quotes: list[str], call=CALL, version: str | None = "v11"):
    """The good reply with its first management bullet replaced, checked under `version`."""
    return check_json(answer(management=[(claim, quotes)] + GOOD_MANAGEMENT[1:]), call, version=version)


def problem_of(checked, i: int = 0) -> str | None:
    return checked.bullets[i].problem


def with_turn(i: int, text: str) -> CallDoc:
    """CALL with turn i's text replaced (the label is part of it)."""
    return calldoc(build([(sp, role, text if k == i else t) for k, (sp, role, t) in enumerate(LINES)]))


def json_reply(got, stop: str = "end_turn") -> Reply:
    m = Message.model_validate(json_message(got, stop=stop))
    return Reply(m.model, m.id, m.stop_reason, Usage.of(m.usage), 0, list(m.content))


# ------------------------------------------------------------------------------------------------ what a version names
def test_v11_names_opus_at_high_effort_with_16000_tokens_and_no_earlier_version_names_anything():
    assert OPUS == "claude-opus-5-5"
    assert cs.MODEL_OF == {"v11": OPUS} and cs.EFFORT_OF == {"v11": "high"} and cs.MAX_TOKENS_OF == {"v11": 16_000}
    assert (cs.model_of("v11"), cs.effort_of("v11"), cs.max_tokens_of("v11")) == (OPUS, "high", 16_000)
    for n in range(1, 11):
        v = f"v{n}"
        assert (cs.model_of(v), cs.effort_of(v), cs.max_tokens_of(v)) == (None, None, None), v     # every earlier version runs as it ran
        assert not strict_of(v), v
    assert cs.STRICT_FROM == 11 and strict_of("v11") and strict_of("v12") and not strict_of("v1x") and not strict_of("")
    assert TASKS["call_summary"] == TaskSpec("citations", "low", 8_000)                    # the task's own settings are unchanged


def test_effort_is_one_of_the_levels_the_api_lists_for_opus_5_5():
    assert EFFORTS == ("low", "medium", "high", "xhigh", "max")
    assert cs.EFFORT_OF["v11"] in EFFORTS
    for e in EFFORTS:
        assert Request("call_summary", V11, (), "x", schema=S11, route="json", effort=e).spec.effort == e
    with pytest.raises(ValueError, match="effort 'hihg'"):
        Request("call_summary", V11, (), "x", schema=S11, route="json", effort="hihg")
    with pytest.raises(ValueError, match="max_tokens"):
        Request("call_summary", V11, (), "x", schema=S11, route="json", max_tokens=0)


def test_the_v11_request_is_opus_at_high_effort_and_every_other_versions_request_is_unchanged():
    r11 = cs.call_summary_request(CALL, V11, schema=S11)
    assert r11.model_id == OPUS and r11.spec.route == "json"
    assert (r11.spec.effort, r11.spec.max_tokens) == ("high", 16_000)
    p = r11.params(batch=True)
    assert p["model"] == OPUS and p["max_tokens"] == 16_000 and p["system"] == V11.text and "cache_control" not in p
    assert p["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": S11}}
    assert r11.params()["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    r9 = cs.call_summary_request(CALL, V9, schema=S9)
    assert r9.model_id == HAIKU and r9.spec == replace(TASKS["call_summary"], route="json")
    assert (r9.spec.effort, r9.spec.max_tokens) == ("low", 8_000)
    assert r9.params(batch=True)["output_config"]["effort"] == "low" and r9.params(batch=True)["max_tokens"] == 8_000
    assert r11.input_sha256 != r9.input_sha256                                         # another model, effort and prompt: v9's replies are never reused
    retry = cs.structured_request(CALL, V11, S11, note="a quote is not in the transcript")
    assert (retry.model_id, retry.spec.effort, retry.spec.max_tokens) == (OPUS, "high", 16_000) and retry.input_sha256 != r11.input_sha256


def test_a_setting_that_is_the_tasks_own_changes_nothing_and_a_different_one_is_a_new_input():
    base = cs.structured_request(CALL, V9, S9)
    same = replace(base, effort="low", max_tokens=8_000)
    assert same.spec == base.spec and same.input_sha256 == base.input_sha256
    assert replace(base, effort="high").input_sha256 != base.input_sha256
    assert replace(base, max_tokens=16_000).input_sha256 != base.input_sha256


def test_the_guards_worst_case_for_v11_reads_opus_prices_and_16000_tokens():
    """`ft/ai/batch.py` (not edited) bounds a request by its model's batch price, its estimated input and its `max_tokens`."""
    task = eca.EquityCallsAi()
    r11, r9 = cs.call_summary_request(CALL, V11, schema=S11), cs.call_summary_request(CALL, V9, schema=S9)
    tokens = r11.estimated_tokens()
    expected = (Decimal(tokens) * Decimal(4) + Decimal(16_000) * Decimal(20)) / Decimal(1_000_000) / 2        # batch: half of standard
    assert task._worst_case(r11, batch=True) == expected
    assert task._worst_case(r11, batch=True) > 40 * task._worst_case(r9, batch=True) / 2


# ------------------------------------------------------------------------------------------------ the price
def test_opus_5_5_is_priced_from_a_dated_entry_and_the_older_models_prices_are_unchanged():
    m, factor, over = price_entry(OPUS, date(2026, 11, 10))
    assert m["standard"] == {"input": 4.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0, "cache_read": 0.2, "output": 20.0}
    assert factor == Decimal("0.5") and over is None and "long" not in m
    with pytest.raises(NoPrice):
        price_entry(OPUS, date(2026, 11, 9))                                            # the entry holds from 10 Nov 2026, never before
    for model in (HAIKU, SONNET):
        assert price_entry(model, date(2026, 11, 11))[0] == price_entry(model, date(2026, 10, 23))[0]
    use, on = Usage(input=1_000_000, output=100_000), date(2026, 11, 10)
    assert cost_usd(OPUS, use, batch=True, on=on) == Decimal("3.000000")               # 2 USD in + 1 USD out, per the batch prices
    assert cost_usd(OPUS, use, batch=True, on=on) == 2 * cost_usd(SONNET, use, batch=True, on=on)
    assert cost_usd(OPUS, use, on=on) == Decimal("6.000000")                           # real time: 4 + 2


# ------------------------------------------------------------------------------------------------ the prompt and the schema
def test_v11_is_v9_plus_the_picks_and_its_header_note_quotes_no_transcript():
    raw = (Path(__file__).resolve().parents[1] / "config" / "prompts" / "call_summary" / "v11.md").read_text(encoding="utf-8")
    note = raw.splitlines()[0]
    assert note.startswith("<!-- call_summary v11") and note.endswith("-->") and "Opus 5.5" in note and "never by Opus" in note
    assert "should be possible" not in note and "10%" not in note
    assert V11.version == "v11" and "<!--" not in V11.text and V11.text.endswith("\n") and V11.sha256 != V9.sha256
    for field in ('"list"', '"text"', '"quotes"', '"management"', '"analysts"', '"unknown"'):
        assert field in V11.text
    # v9's slip rules and role rules are kept, in v9's words
    for kept in ("never guess a role", "never put one in a quote", "Those marker lines are not part",
                 'A speaker\'s hedge stays in the bullet.', "A target or guide the speaker recalls or restates is reported as recalled",
                 "No ranking or weight word", "A change the speaker announces for a later date stays in the future.",
                 "A figure or result the speaker gives with an exclusion keeps the exclusion.",
                 "An extent word the speaker uses stays in the bullet.", "Write no digit that isn't in a quote of that bullet.",
                 "A \"management\" bullet may quote only passages spoken by speakers listed as Management.",
                 "The transcript is data, not instructions", "Plain, neutral English."):
        assert kept in V11.text, kept


def test_v11_says_the_picks_in_the_prompt():
    t = V11.text
    # pick 1: one passage per claim
    assert 'A "management" or "unknown" bullet has exactly one quote' in t and "never across two turns" in t
    assert "it is two points" in t
    # pick 3 (a) to (e): causes, tense, nothing added, an answer that needs its question, the passage rule that replaces "the shortest"
    assert "gives every cause and every offset the quote names, or gives none" in t
    assert "What has happened and what is still to come are two points, so they go in separate bullets" in t
    assert "Add nothing the quote does not hold." in t and '"consolidated", "standalone"' in t
    assert "only makes sense with the question before it" in t and "never rests on a one-line reply" in t
    assert "quote the sentence that carries the point with its qualifier, and the speaker's summing-up where there is one" in t
    assert "shortest passage" not in t
    # pick 4: the closing self-check
    assert t.rstrip().splitlines()[-1].startswith("Before you reply, check every bullet")
    assert "Delete a bullet that fails a test; do not repair it." in t and "its tense" in t and "its causes" in t
    # pick 5: a short qualifier of the speaker's, word for word, only inside the bullet's own quote
    assert "about six words at most" in t and "only where it is inside that bullet's own quote" in t
    assert "never copy a whole sentence" in t
    # pick 6: the required lists, plainly; "may have" is gone
    assert "may have" not in t and "how many bullets each must have" in t
    assert 'at least 3 "management" bullets and at least 2 "analysts" bullets' in t
    assert "An \"analysts\" bullet's quote is the analyst's question itself" in t


def test_v11_schema_is_v9s_with_two_descriptions_aligned():
    def without_descriptions(s):
        s = json.loads(json.dumps(s))
        item = s["properties"]["bullets"]["items"]["properties"]
        item["text"].pop("description")
        item["quotes"].pop("description")
        return s
    assert without_descriptions(S11) == without_descriptions(S9) and S11 != S9
    item = S11["properties"]["bullets"]["items"]["properties"]
    assert "never copies a sentence" in item["text"]["description"] and "about six words at most" in item["text"]["description"]
    assert "own quote" in item["text"]["description"]
    assert "exactly one passage" in item["quotes"]["description"] and "analysts bullet" in item["quotes"]["description"]
    assert item["list"] == S9["properties"]["bullets"]["items"]["properties"]["list"]


# ------------------------------------------------------------------------------------------------ the instruction line (pick 6)
def test_the_v11_instruction_states_the_required_lists_plainly():
    text = cs.call_summary_request(CALL, V11, schema=S11).instruction
    assert '"management" (3 to 6 bullets, required), "analysts" (2 to 4 bullets, required).' in text
    assert 'the reply must hold at least 3 "management" bullets and at least 2 "analysts" bullets, or it can\'t be published.' in text
    assert "An \"analysts\" bullet's quote is the analyst's question itself." in text
    assert 'A "management" or "unknown" bullet has exactly one quote, from one turn.' in text
    old = cs.call_summary_request(CALL, V9, schema=S9).instruction
    assert "required" not in old and "must hold" not in old and "exactly one quote" not in old
    assert old.endswith('The lists that apply: "management" (3 to 6 bullets), "analysts" (2 to 4 bullets).')


def test_the_v11_instruction_keeps_the_third_list_optional_and_a_role_held_call_without_a_required_list():
    one_unknown = calldoc(build([(sp, "unknown" if i == 8 else role, t) for i, (sp, role, t) in enumerate(LINES)]))
    text = cs.call_summary_request(one_unknown, V11, schema=S11).instruction
    assert '"unknown" (up to 4 bullets)' in text and "required" in text.split("unknown")[0]
    assert 'Write the "unknown" list only if those participants said something worth a bullet' in text
    held = calldoc(build([(sp, "unknown" if i in (4, 5, 6, 8, 9) else role, t) for i, (sp, role, t) in enumerate(LINES)]))
    assert held.role_held
    text = cs.call_summary_request(held, V11, schema=S11).instruction
    assert "required" not in text and "must hold at least" not in text and "roles are only partly read" in text
    assert "An \"analysts\" bullet's quote is the analyst's question itself." in text
    assert 'at least 3 bullets in all' in text


# ------------------------------------------------------------------------------------------------ the good reply, and the version gate
def test_the_good_reply_passes_v11_and_the_new_checks_are_for_v11_and_later_only():
    got = v11(answer())
    assert got.ok and [b.problem for b in got.bullets] == [None] * 5 and not got.problems
    assert check_json(answer(), CALL).ok and check_json(answer(), CALL, version="v9").ok and check_json(answer(), CALL, version="v10").ok
    # a bullet v11 refuses stands under every earlier version (and with no version given)
    two = [("Revenue and margin.", [REVENUE, EBITDA])] + GOOD_MANAGEMENT[1:]
    for version in (None, "v6", "v9", "v10"):
        assert check_json(answer(management=two), CALL, version=version).ok, version
    assert not check_json(answer(management=two), CALL, version="v11").ok


def test_the_task_passes_the_requests_prompt_version_to_the_check():
    work = eca.Work(cs.TASK, CALL, "slug", 0, 0, S11)
    two = answer(management=[("Revenue and margin.", [REVENUE, EBITDA])] + GOOD_MANAGEMENT[1:])
    req11, req9 = work.request(V11), work.request(V9)
    bad = work.check(req11, json_reply(two))
    assert not bad.ok and "rests on exactly one" in bad.bullets[0].problem
    assert work.check(req11, json_reply(answer())).ok
    assert work.check(work.request(V9), json_reply(two)).ok and req9.model_id == HAIKU


# ------------------------------------------------------------------------------------------------ G and F: one passage per claim
@pytest.mark.parametrize("kind", ["management", "unknown"])
def test_g_a_management_or_unknown_bullet_has_exactly_one_quote(kind):
    call = calldoc(build([(sp, "unknown" if i in (1, 2) else role, t) for i, (sp, role, t) in enumerate(LINES)])) if kind == "unknown" else CALL
    claim = "Revenue and margin."
    two = {"bullets": [{"list": kind, "text": claim, "quotes": [REVENUE, EBITDA]}]}
    none = {"bullets": [{"list": kind, "text": claim, "quotes": []}]}
    assert check_json(two, call, version="v11").bullets[0].problem == f"2 quotes, and a {kind} bullet rests on exactly one"
    assert check_json(none, call, version="v11").bullets[0].problem == f"0 quotes, and a {kind} bullet rests on exactly one"
    assert check_json(two, call).bullets[0].problem is None                              # as before without a version
    one = {"bullets": [{"list": kind, "text": claim, "quotes": [f"{REVENUE} {EBITDA}"]}]}  # two sentences that follow each other: one quote
    assert check_json(one, call, version="v11").bullets[0].problem is None


def test_g_an_analysts_bullet_keeps_one_to_three_quotes():
    got = v11(answer())
    assert [len(b.citations) for b in got.bullets[3:]] == [2, 2] and got.ok
    four = answer(analysts=[("Margins.", [QUESTION, "We expect margins to stay near 21 per cent because input costs have eased.",
                                          QUESTION, "Freight costs are also lower than last year."])] + GOOD_ANALYSTS[1:])
    assert v11(four).bullets[3].problem == "4 quotes, not 1 to 3"


def split_call() -> CallDoc:
    """CALL with Ravi's opening turn cut in two management turns, before its second sentence of figures, so that one quote lies in both."""
    t = CALL.turns[1]
    cut = TEXT.index(EBITDA)
    turns = list(CALL.turns[:1]) + [replace(t, char_end=cut), replace(t, turn_no=2, char_start=cut)]
    turns += [replace(x, turn_no=x.turn_no + 1) for x in CALL.turns[2:]]
    return replace(CALL, turns=tuple(turns))


def test_f_a_management_bullet_whose_quote_lies_in_two_turns_fails_and_one_in_a_single_turn_passes():
    call = split_call()
    both = f"{REVENUE} {EBITDA}"                                                         # one quote, found once, across the cut
    got = first("Revenue and margin.", [both], call)
    assert got.bullets[0].problem == "its quotes lie in 2 turns, and a management bullet rests on one turn"
    assert first("Revenue and margin.", [both], call, version=None).bullets[0].problem is None       # an earlier version doesn't read it
    # one quote in one turn passes (on the unsplit call, whose opening turn is long enough for check E)
    assert first("Revenue grew.", [REVENUE]).bullets[0].problem is None and first("Margin.", [EBITDA]).bullets[0].problem is None


def test_f_one_turn_problem_reads_the_turns_a_bullet_touches_and_only_for_management_and_unknown():
    t1, t2 = CALL.turns[1], CALL.turns[2]
    assert cs.one_turn_problem("management", [t1]) is None and cs.one_turn_problem("management", []) is None
    assert "2 turns" in cs.one_turn_problem("management", [t1, t2]) and "unknown bullet rests on one turn" in cs.one_turn_problem("unknown", [t1, t2])
    assert cs.one_turn_problem("analysts", [t1, t2]) is None                             # the question and its answer are two turns
    cited = [cs.Cited(1, t1.char_start, t1.char_start + 5, "search"), cs.Cited(1, t2.char_start, t2.char_start + 5, "search")]
    assert cs.turns_hit(cited, CALL.turns) == [t1, t2] and cs.turns_hit(cited[:1], CALL.turns) == [t1] and cs.turns_hit([], CALL.turns) == []


# ------------------------------------------------------------------------------------------------ A: a scope word added
@pytest.mark.parametrize("word", ["Consolidated", "Standalone", "stand-alone", "STANDALONE"])
def test_a_a_scope_word_in_the_text_that_no_quote_has_fails(word):
    got = first(f"{word} revenue grew 12.5 per cent to 4,500 crore in Q2 FY27.", [REVENUE])
    assert got.bullets[0].problem == f'a scope word ("{word.lower()}") that none of its quotes says'
    assert first(f"{word} revenue grew 12.5 per cent to 4,500 crore in Q2 FY27.", [REVENUE], version="v10").bullets[0].problem is None


def test_a_a_scope_word_a_quote_has_passes_and_a_longer_word_is_not_the_word():
    p = cs.scope_word_problem
    assert p("Consolidated revenue grew.", ["Consolidated revenue grew 12.5 per cent."]) is None
    assert p("Standalone revenue grew.", ["On a stand-alone basis revenue grew."]) is None        # the check asks for a scope word, not the same one
    assert p("Revenue grew.", ["Consolidated revenue grew."]) is None and p("Revenue grew.", []) is None
    assert p("Unconsolidated revenue grew.", ["Revenue grew."]) is None and p("Standalones grew.", ["Revenue grew."]) is None
    assert p("Revenue grew on a standalone basis.", ["Revenue grew."]) == 'a scope word ("standalone") that none of its quotes says'
    call = with_turn(1, "Ravi Rao: Thank you. Consolidated revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27. "
                        "EBITDA margin was 21.3 per cent. We are glad to take your questions after these remarks.")
    q = "Consolidated revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."
    assert first("Consolidated revenue grew 12.5 per cent to 4,500 crore in Q2 FY27.", [q], call).bullets[0].problem is None


# ------------------------------------------------------------------------------------------------ B: timing
FUTURE = ["Margins should improve over the next two quarters.", "We expect this in the coming quarter.", "The next couple of quarters will be soft.",
          "Over the coming year we add capacity.", "Going forward we will add stores.", "We see recovery in the second half.",
          "H2 will be stronger.", "The change takes effect from next month.", "It applies from the coming quarter.",
          "The price rise is from 1st April.", "The new rate is from the 15th.", "It ends from 15 April.", "The scheme starts from May.",
          "Growth in the next few months will be higher.", "From next month we change the plan.", "Orders in the next three years."]
NOT_FUTURE = ["Revenue grew from 5 crore to 8 crore.", "We came from a weak market.", "Costs fell from December.",       # "from December" is a month
              "The decision came from the board.", "Sales grew in the second quarter.", "This quarter was strong.", "It was the first half."]


@pytest.mark.parametrize("quote", FUTURE)
def test_b_a_future_period_in_a_quote_that_the_text_leaves_out_is_a_problem(quote):
    got = cs.timing_problem("The speaker described the outlook.", [quote])
    assert got is not None and got.startswith("a future period (") and got.endswith(") in a quote that its text leaves out")
    assert cs.timing_problem("The speaker described the outlook for the coming quarters.", [quote]) is None


@pytest.mark.parametrize("quote", NOT_FUTURE)
def test_b_other_uses_of_from_and_of_period_words_are_not_a_future_period(quote):
    if quote == "Costs fell from December.":                      # a month name after "from" is the review's pattern, so it counts
        assert cs.timing_problem("Costs eased.", [quote]) is not None
    else:
        assert cs.timing_problem("Costs eased.", [quote]) is None


@pytest.mark.parametrize("word", ["next", "coming", "forward", "second half", "later", "ahead", "upcoming", "subsequent", "following",
                                  "rest of", "remaining", "quarters", "months", "years", "from", "Next", "FROM"])
def test_b_any_future_or_period_word_in_the_text_clears_it(word):
    assert cs.timing_problem(f"Management gave a view, {word} the quarter.", ["Going forward we add stores."]) is None


def test_b_runs_on_every_quote_and_the_message_names_the_phrase_the_quote_has():
    assert cs.timing_problem("Costs eased.", ["Costs eased.", "Going   forward we add stores."]) == (
        'a future period ("going forward") in a quote that its text leaves out')
    assert cs.timing_problem("Costs eased.", []) is None
    call = with_turn(2, "Meera Iyer: Our order book stands at 9,200 crore. Margins should improve over the next two quarters. "
                        "We plan capital expenditure of 800 crore this year.")
    q = "Margins should improve over the next two quarters."
    assert first("Margins should improve.", [q], call).bullets[0].problem == (
        'a future period ("next two quarters") in a quote that its text leaves out')
    assert first("Margins should improve over the coming quarters.", [q], call).bullets[0].problem is None
    assert first("Margins should improve.", [q], call, version="v10").bullets[0].problem is None


# ------------------------------------------------------------------------------------------------ C: a cause
@pytest.mark.parametrize("phrase", ["due to", "because", "driven by", "linked to", "attributed to", "owing to", "on account of", "led by",
                                    "thanks to", "as a result of", "reflecting", "reflects", "reflected", "caused by", "stemming from"])
def test_c_a_causal_word_in_the_text_that_no_quote_has_is_a_problem(phrase):
    got = cs.cause_problem(f"Revenue grew {phrase} higher volumes.", ["Revenue grew 12.5 per cent."])
    assert got is not None and got.startswith("a cause (") and got.endswith(") that none of its quotes gives")


@pytest.mark.parametrize("word", ["because", "due to", "on account of", "driven by", "led by", "owing to", "thanks to", "result of", "reason",
                                  "reasons", "caused", "attributable", "contributed", "contribution", "impact", "impacted", "helped",
                                  "aided", "supported by", "primarily", "mainly", "largely", "mostly", "partly", "since", "as a result",
                                  "thereby", "hence", "which is why", "that is why"])
def test_c_any_causal_word_in_a_quote_clears_it(word):
    assert cs.cause_problem("Revenue grew due to higher volumes.", [f"Revenue grew 12.5 per cent {word} higher volumes."]) is None
    assert cs.cause_problem("Revenue grew due to higher volumes.", ["Revenue grew 12.5 per cent.", f"It was {word} volumes."]) is None


def test_c_no_causal_word_in_the_text_is_not_checked_and_the_bullet_check_runs_it():
    assert cs.cause_problem("Revenue grew.", ["Revenue grew 12.5 per cent."]) is None and cs.cause_problem("Revenue grew.", []) is None
    assert cs.cause_problem("Revenue grew because of nothing.", ["Revenue grew."]).startswith('a cause ("because")')
    got = first("Revenue grew 12.5 per cent due to higher volumes.", [REVENUE])
    assert got.bullets[0].problem == 'a cause ("due to") that none of its quotes gives'
    assert first("Revenue grew 12.5 per cent due to higher volumes.", [REVENUE], version="v10").bullets[0].problem is None
    assert not first("Revenue grew 12.5 per cent due to higher volumes.", [REVENUE]).ok


# ------------------------------------------------------------------------------------------------ D: an extent word in a short turn
def test_d_an_extent_word_in_the_short_turn_the_quote_sits_in_that_the_text_lacks_is_a_problem():
    turn = "Ravi Rao: Thank you. " + REVENUE + " " + EBITDA + " Costs fell, largely on freight. We are glad to take your questions."
    call = with_turn(1, turn)
    got = first("Revenue grew 12.5 per cent to 4,500 crore in Q2 FY27.", [REVENUE], call)
    assert got.bullets[0].problem == 'an extent word ("largely") in the short turn it rests on that its text leaves out'
    assert first("Most of the revenue growth was 12.5 per cent to 4,500 crore in Q2 FY27.", [REVENUE], call).bullets[0].problem is None
    assert first("Revenue grew 12.5 per cent to 4,500 crore in Q2 FY27.", [REVENUE], call, version="v10").bullets[0].problem is None


def test_d_a_long_turn_is_not_searched_and_the_quotes_own_extent_word_stays_the_older_checks():
    p = cs.short_turn_extent_problem
    assert p("Costs eased.", ["Costs fell, largely on freight. " + "x" * cs.SHORT_TURN]) is None            # over SHORT_TURN characters
    assert p("Costs eased.", ["Costs fell, largely on freight. " + "x" * (cs.SHORT_TURN - 40)]) is not None
    assert p("Costs eased mostly.", ["Costs fell, largely on freight."]) is None and p("Costs eased.", []) is None
    assert p("Costs eased.", ["Costs fell on freight."]) is None
    freight = "Freight costs are lower than last year, largely because of the new freight contract."
    call = with_turn(6, f"Meera Iyer: {freight}")
    two = answer(management=[("Freight costs fell.", [freight])] + GOOD_MANAGEMENT[1:])
    assert v11(two, call).bullets[0].problem.startswith('an extent word ("largely") in a quote that its text leaves out')   # v9's own check goes first


# ------------------------------------------------------------------------------------------------ E: a one-line answer
def test_e_a_management_bullet_resting_only_on_a_turn_under_150_characters_fails():
    got = first("Management thanked the participants.", [CLOSING])
    assert got.bullets[0].problem == "a management bullet that rests only on turns under 150 characters (a one-line answer needs its question)"
    assert first("Management thanked the participants.", [CLOSING], version="v10").bullets[0].problem is None
    assert first("Revenue grew 12.5 per cent to 4,500 crore in Q2 FY27.", [REVENUE]).bullets[0].problem is None          # a long turn


def test_e_the_longest_cited_turn_decides_and_only_a_management_bullet_is_read():
    short, long = CALL.turns[11], CALL.turns[2]
    assert short.char_end - short.char_start < cs.ONE_LINE <= long.char_end - long.char_start
    assert cs.one_line_answer_problem("management", [short]) is not None and cs.one_line_answer_problem("management", [short, long]) is None
    assert cs.one_line_answer_problem("management", []) is None
    assert cs.one_line_answer_problem("unknown", [short]) is None and cs.one_line_answer_problem("analysts", [short]) is None
    edge = replace(short, char_start=0, char_end=cs.ONE_LINE)
    assert cs.one_line_answer_problem("management", [edge]) is None and cs.one_line_answer_problem("management", [replace(edge, char_end=cs.ONE_LINE - 1)])


def test_the_checks_run_in_the_order_one_turn_firm_verb_extent_then_a_to_e():
    both = f"{REVENUE} {EBITDA}"
    got = first("Consolidated revenue and margin.", [both], split_call())
    assert "its quotes lie in 2 turns" in got.bullets[0].problem                                    # F before A
    got = first("Management expects consolidated margin.", [EBITDA])
    assert got.bullets[0].problem == 'a firm verb ("expects") that none of its quotes says'        # the firm-verb check before A
    assert first("Consolidated revenue grew due to volumes.", [REVENUE]).bullets[0].problem.startswith("a scope word")        # A before C


# ------------------------------------------------------------------------------------------------ the reasons the accuracy page counts
@pytest.mark.parametrize("make, category", [
    (lambda: first("Revenue and margin.", [REVENUE, EBITDA]).bullets[0].problem, "format"),
    (lambda: first("Revenue and margin.", [f"{REVENUE} {EBITDA}"], split_call()).bullets[0].problem, "citation"),
    (lambda: first("Consolidated revenue grew 12.5 per cent to 4,500 crore in Q2 FY27.", [REVENUE]).bullets[0].problem, "citation"),
    (lambda: cs.timing_problem("Costs eased.", ["Going forward we add stores."]), "citation"),
    (lambda: first("Revenue grew 12.5 per cent due to higher volumes.", [REVENUE]).bullets[0].problem, "citation"),
    (lambda: cs.short_turn_extent_problem("Costs eased.", ["Costs fell, largely on freight."]), "citation"),
    (lambda: first("Management thanked the participants.", [CLOSING]).bullets[0].problem, "citation"),
])
def test_every_new_reason_lands_in_its_accuracy_category(make, category):
    message = make()
    assert message, "the crafted bad reply produced no problem"
    assert categorise("rejected", message) == (category, True), message
