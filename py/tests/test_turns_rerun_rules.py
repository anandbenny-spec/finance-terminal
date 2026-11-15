"""The re-run of the calls whose turns changed (#554, 2B-09c), without a database: the switch runs only in a backfill limited to one
task, and the input it compares (`Request.input_sha256`) is the same for a call built afresh from the same turns and another once
a turn the request reads has changed, for each task's current prompt version. The task itself is tested in
test_equity_calls_ai_turns_rerun.py."""
from dataclasses import replace

import pytest
from call_fixtures import LINES, build, calldoc
from test_equity_calls_ai_tone import LONG

from ft.ai import call_summary as cs
from ft.ai import call_tone as ct
from ft.ai import guidance_extraction as ge
from ft.ai.client import load_prompt, load_schema
from ft.jobs.tasks import equity_calls_ai as eca

ENV = eca.TURNS_RERUN_ENV
LABEL = "Management: "
QUESTION, OUTLOOK = (LINES[i][2].split(": ", 1)[1] for i in (4, 5))      # Anil Shah's question and Ravi Rao's answer
# management's answer written inline in the analyst's turn, as SBI Life's and NTPC's transcripts write it (#542, step 1)
MERGED = LINES[:4] + [("Anil Shah", "analyst", f"Anil Shah: {QUESTION} {LABEL}{OUTLOOK}")] + LINES[6:]


def test_the_switch_is_off_unless_asked_and_runs_only_in_a_backfill_limited_to_one_task():
    for v in ("", "none", " none "):
        assert eca.turns_rerun("weekly", None, {ENV: v}) is None
    assert eca.turns_rerun("backfill", ge.TASK, {}) is None
    for only in (cs.TASK, ge.TASK, ct.TASK):
        assert eca.turns_rerun("backfill", only, {ENV: " all "}) == "all"
        assert eca.turns_rerun("backfill", only, {ENV: "price"}) == "price"
    with pytest.raises(ValueError, match="must be none, price or all"):
        eca.turns_rerun("backfill", ge.TASK, {ENV: "yes"})
    for workflow in ("weekly", "daily", "refresh", "watch"):                   # a scheduled run's choice never changes
        with pytest.raises(ValueError, match="runs only in a backfill"):
            eca.turns_rerun(workflow, ge.TASK, {ENV: "all"})
    with pytest.raises(ValueError, match="needs CALLS_AI_TASK set to one task"):
        eca.turns_rerun("backfill", None, {ENV: "price"})
    with pytest.raises(ValueError, match="don't run together"):
        eca.turns_rerun("backfill", ge.TASK, {ENV: "all", eca.GUIDANCE_RERUN_ENV: "price"})
    assert eca.turns_rerun("backfill", ge.TASK, {ENV: "all", eca.GUIDANCE_RERUN_ENV: "none"}) == "all"   # the workflow's default
    with pytest.raises(ValueError, match="don't run together"):
        eca.turns_rerun("backfill", ct.TASK, {ENV: "all", eca.TONE_SET_ENV: "earnings_tone"})


def _split(call: cs.CallDoc, at: str, role: str = "management", speaker: str = "Management") -> cs.CallDoc:
    """The call with the turn that holds `at` cut at its offset, the second part a turn of `role` (#551's rule)."""
    cut, turns = call.text.index(at), []
    for t in call.turns:
        if t.char_start < cut < t.char_end:
            turns += [replace(t, char_end=cut), cs.Turn(t.turn_no, cut, t.char_end, speaker, role, t.section)]
        else:
            turns.append(t)
    return replace(call, turns=tuple(replace(t, turn_no=i) for i, t in enumerate(turns)))


def _requests(call: cs.CallDoc, chunks=()) -> dict[str, bytes]:
    """The input hash of each task's request for the call: the extraction (production's v1), the summary on both routes (v11, the
    current one, and v5), the tone (v3)."""
    out = {"guidance_extraction v1": ge.guidance_request(call, load_prompt(ge.TASK, "v1"), load_schema(ge.TASK, "v1")),
           "call_summary v11": cs.call_summary_request(call, load_prompt(cs.TASK, "v11"), schema=load_schema(cs.TASK, "v11")),
           "call_summary v5": cs.call_summary_request(call, load_prompt(cs.TASK, "v5"))}
    if items := ct.passages(call, chunks):
        out["call_tone v3"] = ct.tone_request(call, items, load_prompt(ct.TASK, "v3"), load_schema(ct.TASK, "v3"))
    return {k: r.input_sha256 for k, r in out.items()}


def _chunks(call: cs.CallDoc) -> list[ct.Chunk]:
    return [ct.Chunk(t.turn_no, t.char_start, t.char_end) for t in call.turns]           # one a turn, as the call chunker cuts


def test_an_unchanged_call_builds_the_same_input_and_a_changed_turn_another_for_every_task():
    assert eca.PROMPT_VERSION == {cs.TASK: "v11", ge.TASK: "v1", ct.TASK: "v3"}       # the versions the re-run sends
    call =replace(calldoc(build(MERGED)), fy_end_month=3)
    again = replace(calldoc(build(MERGED)), fy_end_month=3)                            # built afresh from the same rows
    assert _requests(call) == _requests(again)                                          # never chosen
    split = _split(call, LABEL)
    assert split.text == call.text and len(split.turns) == len(call.turns) + 1
    before, after = _requests(call), _requests(split)
    assert before.keys() == after.keys() and all(before[k] != after[k] for k in before)   # chosen, for each task that reads it
    # the tone reads only management's passages: a change it doesn't read leaves its input as it was
    tone = replace(calldoc(build(LONG)), fy_end_month=3)
    flipped = replace(tone, turns=tuple(replace(t, role="unknown") if t.role == "moderator" and t.turn_no == 2 else t
                                        for t in tone.turns))
    assert _requests(tone, _chunks(tone))["call_tone v3"] == _requests(flipped, _chunks(flipped))["call_tone v3"]
    answer = next(t.turn_no for t in tone.turns if t.speaker == "Meera Iyer")
    moved = replace(tone, turns=tuple(replace(t, role="analyst") if t.turn_no == answer else t for t in tone.turns))
    assert _requests(tone, _chunks(tone))["call_tone v3"] != _requests(moved, _chunks(moved))["call_tone v3"]
