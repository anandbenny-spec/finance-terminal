"""Tone of an earnings call (Spec 02B §5.7; job 2B-12, #175): the management passages, the request, the checks on a reply, the
aggregation and the change label. Synthetic text only."""
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from ft.ai import call_tone as ct
from ft.ai.call_summary import CallDoc, Turn
from ft.ai.client import TASKS, load_prompt, load_schema
from ft.jobs.tasks import equity_calls_ai as eca

WORDS = " ".join(f"word{i}" for i in range(45))              # 45 words: a passage
SHORT = "Thank you all for joining us today."                # a few words: not a passage


def call_with(*parts: tuple[str, str, str, str]) -> tuple[CallDoc, list[ct.Chunk]]:
    """A call of (speaker, role, section, text) turns, one chunk per turn."""
    text, turns, chunks, pos = "", [], [], 0
    for i, (speaker, role, section, body) in enumerate(parts):
        turns.append(Turn(i, pos, pos + len(body), speaker, role, section))
        chunks.append(ct.Chunk(i, pos, pos + len(body)))
        text += body
        pos += len(body)
    return (CallDoc(1, text, "Acme Limited · Q2 FY27 earnings call transcript", "Acme Limited", "Q2 FY27", date(2026, 10, 16),
                    tuple(turns)), chunks)


# ---------------------------------------------------------------------------------------------- the passages
def test_only_management_chunks_of_forty_words_inside_one_turn_are_passages():
    call, chunks = call_with(("Mod", "moderator", "opening", WORDS), ("Rao", "management", "opening", WORDS),
                             ("Iyer", "analyst", "qa", WORDS), ("Rao", "management", "qa", WORDS),
                             ("Rao", "management", "closing", SHORT), ("Rao", "management", "closing", WORDS))
    got = ct.passages(call, chunks)
    assert [(p.p, p.chunk_no, p.section) for p in got] == [(1, 1, "opening"), (2, 3, "qa"), (3, 5, "closing")]
    assert all(p.words == 45 and p.speaker == "Rao" and p.text == call.text[p.char_start:p.char_end] for p in got)


def test_a_chunk_that_runs_across_two_turns_is_not_a_passage():
    call, _ = call_with(("Rao", "management", "opening", WORDS), ("Mod", "moderator", "opening", WORDS))
    assert ct.passages(call, [ct.Chunk(0, 10, len(call.text) - 10)]) == []


def test_the_exact_forty_word_boundary():
    call, chunks = call_with(("Rao", "management", "qa", " ".join(["w"] * 40)), ("Rao", "management", "qa", " ".join(["w"] * 39)))
    assert [p.chunk_no for p in ct.passages(call, chunks)] == [0]


def test_the_listing_numbers_the_passages_with_section_and_speaker_and_nothing_else():
    call, chunks = call_with(("Rao", "management", "opening", WORDS), ("Rao", "management", "qa", WORDS))
    text = ct.listing(ct.passages(call, chunks))
    assert text.startswith("[1] (opening remarks, Rao)\n") and "\n\n[2] (answer, Rao)\n" in text
    assert "Moderator" not in text and "analyst" not in text


# ---------------------------------------------------------------------------------------------- the request and the files
def test_the_request_is_the_json_route_at_medium_effort_and_the_prompt_and_schema_agree():
    assert TASKS[ct.TASK].route == "json" and TASKS[ct.TASK].effort == "medium"
    schema = load_schema(ct.TASK, "v1")
    for part in (schema["properties"]["scores"]["items"], schema["properties"]["evidence"]["items"]):
        assert part["additionalProperties"] is False and set(part["required"]) == set(part["properties"])
    prompt = load_prompt(ct.TASK, "v1")
    call, chunks = call_with(("Rao", "management", "opening", WORDS))
    req = ct.tone_request(call, ct.passages(call, chunks), prompt, schema)
    assert req.task == ct.TASK and req.schema == schema and "[1] (opening remarks, Rao)" in req.sources[0].text
    assert "1 numbered management passages" in req.instruction
    for score in ("-2, strongly negative", "-1, mildly negative", "0, neutral", "+1, mildly positive", "+2, strongly positive"):
        assert score in prompt.text                       # the rubric's five rows (the same words as GUIDE.md)


def test_a_call_without_passages_is_not_requested():
    call, _ = call_with(("Rao", "management", "opening", SHORT))
    with pytest.raises(ValueError):
        ct.tone_request(call, [], load_prompt(ct.TASK, "v1"), load_schema(ct.TASK, "v1"))


# ---------------------------------------------------------------------------------------------- checking a reply
def three() -> list[ct.Passage]:
    texts = ["Demand was very weak and we are cutting our outlook for the year ahead " + WORDS,
             "Revenue was steady and we are on track for the full year " + WORDS,
             "Figures were as reported in the filing " + WORDS]
    call, chunks = call_with(*[("Rao", "management", "qa", t) for t in texts])
    return ct.passages(call, chunks)


def reply(scores, evidence=()):
    return {"scores": [{"p": p, "score": s} for p, s in scores], "evidence": [{"p": p, "quote": q} for p, q in evidence]}


def test_a_good_reply_keeps_its_scores_and_its_evidence_with_spans():
    items = three()
    q1, q2 = "we are cutting our outlook", "we are on track for the full year"
    c = ct.check(reply([(1, -2), (2, 1), (3, 0)], [(1, q1), (2, q2)]), items)
    assert c.ok and [s["score"] for s in c.scores] == [-2, 1, 0] and not c.drops and not c.fallback_citation
    assert [e["citation_no"] for e in c.evidence] == [1, 2]
    for e, p in zip(c.evidence, (items[0], items[1]), strict=True):
        a, b = e["span"]
        assert p.char_start <= a < b <= p.char_end
    text = items[0].text
    assert c.citations[0] == ("/evidence/0", (items[0].char_start + text.index(q1), items[0].char_start + text.index(q1) + len(q1)))


@pytest.mark.parametrize("bad", [
    reply([(1, 3), (2, 0), (3, 0)]), reply([(1, -3), (2, 0), (3, 0)]), reply([(1, 1), (2, 0)]),
    reply([(1, 1), (1, 0), (2, 0), (3, 0)]), reply([(1, 1), (2, 0), (3, 0), (4, 0)]),
    {"scores": [{"p": 1, "score": 0.5}, {"p": 2, "score": 0}, {"p": 3, "score": 0}], "evidence": []},
    {"scores": [{"p": 1, "score": True}, {"p": 2, "score": 0}, {"p": 3, "score": 0}], "evidence": []},
    {"scores": "x", "evidence": []}, None, []])
def test_a_reply_that_breaks_the_score_rules_is_a_failed_reply(bad):
    c = ct.check(bad, three())
    assert not c.ok and c.problems and not c.scores


def test_a_whole_number_sent_as_a_float_is_accepted():
    c = ct.check({"scores": [{"p": 1.0, "score": 2.0}, {"p": 2, "score": 0}, {"p": 3, "score": 0}], "evidence": []}, three())
    assert c.ok and c.scores[0]["score"] == 2


def test_evidence_is_dropped_by_reason_and_the_scores_stand():
    items = three()
    good = "we are on track for the full year"
    c = ct.check(reply([(1, -1), (2, 1), (3, 0)], [
        (3, "Figures were as reported"),                 # from a passage scored 0
        (9, "x"),                                        # a passage that isn't in the list
        (1, "this text is nowhere"),                     # not found
        (1, "x" * 301),                                  # too long
        (2, good), (2, "we are on track for the full year"), (1, "Demand was very weak"), (1, "we are cutting our outlook"),
    ]), items)
    assert c.ok and len(c.evidence) == 3 and c.drops == {"zero_passage": 1, "unknown_passage": 1, "not_found": 1,
                                                          "too_long": 1, "over_limit": 1}


def test_a_quote_that_appears_twice_in_its_passage_is_not_found_exactly_once():
    call, chunks = call_with(("Rao", "management", "qa", ("steady demand " * 3) + WORDS))
    items = ct.passages(call, chunks)
    c = ct.check(reply([(1, 1)], [(1, "steady demand")]), items)
    assert c.ok and c.drops == {"not_found": 1}


def test_a_malformed_evidence_item_is_dropped():
    c = ct.check({"scores": [{"p": 1, "score": 1}, {"p": 2, "score": 0}, {"p": 3, "score": 0}],
                  "evidence": ["nope", {"p": 1}]}, three())
    assert c.ok and c.drops == {"malformed": 2}


def test_with_no_kept_evidence_the_first_passage_is_cited_so_a_neutral_call_can_be_published():
    items = three()
    c = ct.check(reply([(1, 0), (2, 0), (3, 0)]), items)
    assert c.ok and c.evidence == [] and c.fallback_citation
    assert c.citations == [("/scores/0", (items[0].char_start, items[0].char_end))]


def test_the_stored_content_has_scores_and_quotes_but_no_passage_text():
    items = three()
    c = ct.check(reply([(1, -2), (2, 1), (3, 0)], [(1, "we are cutting our outlook")]), items)
    doc = ct.content(c, attempt=1, reply=None)
    blob = str(doc)
    assert doc["rule_version"] == ct.RULE_VERSION and doc["scores"][0] == {
        "p": 1, "score": -2, "chunk_no": items[0].chunk_no, "section": "qa", "speaker": "Rao", "words": items[0].words}
    assert "word44" not in blob and "we are cutting our outlook" in blob


# ---------------------------------------------------------------------------------------------- aggregation
def scored(values, section="qa"):
    return [{"score": v, "section": section} for v in values]


def test_the_tone_is_the_mean_with_n_and_the_sample_sd_and_sections_have_their_own_means():
    scores = scored([1, 1, 0, 2], "opening") + scored([-1, 0, 1, 1], "qa") + scored([2, 0], "closing")
    t = ct.aggregate(scores)
    assert t.n == 10 and t.mean == Decimal("0.7000") and t.held_reason is None
    assert t.sd == Decimal("0.9487")                                    # sample sd of the ten scores
    assert (t.opening_n, t.opening_mean, t.answers_n, t.answers_mean) == (4, Decimal("1.0000"), 4, Decimal("0.2500"))


def test_fewer_than_eight_passages_is_held_with_n_and_no_mean():
    t = ct.aggregate(scored([1] * 7))
    assert t.held and t.n == 7 and t.mean is None and t.sd is None and t.held_reason == "too little management text to score"
    assert ct.aggregate(scored([1] * 8)).mean == Decimal("1.0000")


def tone(values):
    return ct.aggregate(scored(values))


def test_change_is_called_beyond_two_standard_errors():
    prev = tone([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    up = ct.change(tone([1, 1, 1, 1, 1, 1, 1, 1, 1, 1]), prev, 7)
    assert (up.prev_event_id, up.label, up.delta, up.se_delta) == (7, "more_positive", Decimal("1.0000"), Decimal("0.0000"))
    down = ct.change(tone([-1] * 10), prev, 7)
    assert down.label == "more_negative" and down.delta == Decimal("-1.0000")


def test_a_change_inside_two_standard_errors_is_little_changed():
    prev = tone([-1, 1, -1, 1, -1, 1, -1, 1, 0, 0])
    now = tone([0, 1, -1, 1, -1, 1, 0, 1, 0, 0])
    got = ct.change(now, prev, 3)
    assert got.label == "little_changed" and abs(got.delta) <= 2 * got.se_delta


def test_the_threshold_on_each_side_of_two_se():
    prev = tone([0, 1, 0, 1, 0, 1, 0, 1])                  # mean 0.5, sd about 0.5345
    for values, label in (([1, 1, 1, 1, 1, 1, 1, 1], "more_positive"), ([0, 1, 0, 1, 0, 1, 1, 1], "little_changed")):
        assert ct.change(tone(values), prev, 1).label == label


def test_no_change_when_the_previous_call_is_missing_or_held_or_this_one_is():
    now = tone([1] * 10)
    assert ct.change(now, None, None) == ct.Change(None, None, None, None)
    held = ct.aggregate(scored([1] * 3))
    assert ct.change(now, held, 5) == ct.Change(5, None, None, None)
    assert ct.change(held, now, 5).label is None


def test_the_rule_is_the_specs():
    assert (ct.MIN_WORDS, ct.MIN_PASSAGES, ct.MAX_EVIDENCE, ct.MAX_QUOTE, ct.SIGMAS) == (40, 8, 3, 300, 2)
    assert replace(ct.aggregate(scored([0] * 8)), n=8).mean == 0


def test_the_blank_space_after_a_chunk_does_not_keep_it_from_being_inside_its_turn():
    call, _ = call_with(("Rao", "management", "qa", WORDS))
    padded = CallDoc(call.document_id, call.text + "\n\n", call.title, call.company, call.fiscal_label, call.day, call.turns)
    got = ct.passages(padded, [ct.Chunk(0, 0, len(call.text) + 2)])
    assert len(got) == 1 and got[0].text == call.text and got[0].char_end == len(call.text)


def test_a_failed_check_gives_the_batch_steps_its_reasons_and_a_retry_note():
    c = ct.Checked(None, problems=["p1: score 3 is out of range", "p2: no score"])
    assert c.reasons() == ["p1: score 3 is out of range", "p2: no score"]
    assert c.note() == "p1: score 3 is out of range; p2: no score."
    assert ct.Checked(None).reasons() == []


def test_the_quotes_are_counted_by_how_they_were_found_for_the_citation_integrity_check():
    assert ct.Checked(None).mapping == {}


def test_v2_is_v1s_text_unchanged_on_sonnet():
    """#175, the tone gate's Sonnet round (Spec 02B §5.8): no rubric or prompt change after the model has run on the set, so the
    text sent under v2 is v1's; only the model differs."""
    from ft.ai.client import HAIKU, SONNET
    v1, v2 = load_prompt(ct.TASK, "v1"), load_prompt(ct.TASK, "v2")
    assert v2.text == v1.text and load_schema(ct.TASK, "v2") == load_schema(ct.TASK, "v1")
    assert ct.model_of("v1") is None and ct.model_of("v2") == SONNET
    call, chunks = call_with(("Rao", "management", "opening", WORDS), ("Rao", "management", "qa", WORDS))
    items = ct.passages(call, chunks)
    assert ct.tone_request(call, items, v1, load_schema(ct.TASK, "v1")).model_id == HAIKU
    req = ct.tone_request(call, items, v2, load_schema(ct.TASK, "v2"))
    assert req.model_id == SONNET and req.input_sha256 != ct.tone_request(call, items, v1, load_schema(ct.TASK, "v1")).input_sha256


def test_the_tone_set_names_its_documents_and_refuses_any_other_use():
    items = json.loads(eca.TONE_SETS["earnings_tone"].read_text(encoding="utf-8"))["items"]
    want = sorted({int(i["document_id"]) for i in items})
    env = {eca.TONE_SET_ENV: "earnings_tone"}
    assert len(want) == 30 and eca.tone_set_documents(ct.TASK, "dev", env) == want
    assert eca.tone_set_documents(ct.TASK, "dev", {}) is None and eca.tone_set_documents(None, "production", {eca.TONE_SET_ENV: " "}) is None
    with pytest.raises(ValueError, match="must be empty or one of"):
        eca.tone_set_documents(ct.TASK, "dev", {eca.TONE_SET_ENV: "policy_hawk_dove"})
    for only in (None, "call_summary", "guidance_extraction"):
        with pytest.raises(ValueError, match="needs CALLS_AI_TASK=call_tone"):
            eca.tone_set_documents(only, "dev", env)
    with pytest.raises(ValueError, match="runs on dev only"):
        eca.tone_set_documents(ct.TASK, "production", env)
