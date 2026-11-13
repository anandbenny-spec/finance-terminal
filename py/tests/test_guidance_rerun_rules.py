"""The extraction's re-run switch and prompt v5 (#511, 2B-15d), without a database: the re-run runs only in a dev backfill limited to
the extraction; production's prompt version stays v1 until #511 part 3; v5 is v2 with one section added and v1's definition of a
target (Spec 02B §5.9) and field rules word for word. The task itself is tested in test_equity_calls_ai_rerun.py."""
import pytest

from ft.ai import guidance_extraction as ge
from ft.ai.client import load_prompt, load_schema
from ft.jobs.tasks import equity_calls_ai as eca


def test_the_rerun_is_off_unless_asked_and_runs_only_in_a_dev_backfill_limited_to_the_extraction():
    assert eca.guidance_rerun("backfill", "dev", ge.TASK, {}) is None
    assert eca.guidance_rerun("weekly", "production", None, {eca.GUIDANCE_RERUN_ENV: ""}) is None
    assert eca.guidance_rerun("weekly", "production", None, {eca.GUIDANCE_RERUN_ENV: "none"}) is None
    assert eca.guidance_rerun("backfill", "dev", ge.TASK, {eca.GUIDANCE_RERUN_ENV: " all "}) == "all"
    assert eca.guidance_rerun("backfill", "dev", ge.TASK, {eca.GUIDANCE_RERUN_ENV: "price"}) == "price"
    with pytest.raises(ValueError, match="must be none, price or all"):
        eca.guidance_rerun("backfill", "dev", ge.TASK, {eca.GUIDANCE_RERUN_ENV: "v5"})
    for workflow in ("weekly", "daily", "refresh", "watch"):
        with pytest.raises(ValueError, match="runs only in a backfill"):
            eca.guidance_rerun(workflow, "dev", ge.TASK, {eca.GUIDANCE_RERUN_ENV: "all"})
    for only in (None, "call_summary", "call_tone"):
        with pytest.raises(ValueError, match="needs CALLS_AI_TASK=guidance_extraction"):
            eca.guidance_rerun("backfill", "dev", only, {eca.GUIDANCE_RERUN_ENV: "all"})
    with pytest.raises(ValueError, match="runs on dev only, not 'production'"):
        eca.guidance_rerun("backfill", "production", ge.TASK, {eca.GUIDANCE_RERUN_ENV: "all"})


def test_production_keeps_v1_and_only_dev_takes_the_rerun_until_511_part_3():
    assert eca.PROMPT_VERSION[ge.TASK] == "v1"
    assert eca.GUIDANCE_RERUN_VERSION == "v5" and eca.GUIDANCE_RERUN_TARGETS == ("dev",)


def test_prompt_v5_is_v2_with_one_section_and_keeps_v1s_definition_and_field_rules_word_for_word():
    v1, v2, v5 = (load_prompt(ge.TASK, v).text for v in ("v1", "v2", "v5"))
    assert v1[v1.index("TARGETS\n"):v1.index("STATED FIGURES\n")] in v5         # §5.9's definition, unchanged
    assert v1[v1.index("STATED FIGURES\n"):] in v5                               # stated figures, the fields, units and checks
    assert v2[v2.index("COMPLETENESS\n"):v2.index("TARGETS\nA target is")] in v5  # v2's completeness section
    assert v5.index("COMPLETENESS\n") < v5.index("SHAPES OF A TARGET\n") < v5.index("TARGETS\nA target is")
    shapes = v5[v5.index("SHAPES OF A TARGET\n"):v5.index("TARGETS\nA target is")]
    assert "add nothing to that definition" in shapes
    assert v5.replace(shapes, "") == v2                                          # nothing else changed
    assert load_schema(ge.TASK, "v5") == load_schema(ge.TASK, "v1")              # the same schema is sent
