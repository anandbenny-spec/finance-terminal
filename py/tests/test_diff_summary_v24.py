"""#445, round 21 (Anand's decision of 15 Nov 2026, about 13:25 IST, after round 20's dev run): `statement_diff_summary` v24 is
v23's text (v17's), marking one revision of the packed fallback (`summaries.DIFF_RESTATED_FROM`): a change of the decision's
other than the one shown that also states the stance is the stance. mpc-2021-02-05's 3592 (v23) left out "decided to continue
with an accommodative stance of monetary policy till the prospects of a sustained recovery are well secured", which restates
the rate decision too, so it was a second decision, and the decision is shown once; v1 (397) has it. Fallbacks of v23 keep
their rules. No database and no model."""
from datetime import date

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import Doc, fallback_bullets
from ft.jobs.tasks.ai_batch import PROMPT_VERSION

OLD = [
    ("9. All members of the MPC – Dr. Shashanka Bhide, Dr. Ashima Goyal and Shri Shaktikanta Das – unanimously voted for "
     "keeping the policy repo rate unchanged."),
    ("Accordingly, the MPC decided to maintain status quo on the policy rate and continue with the accommodative stance as "
     "long as necessary – at least during the current financial year and into the next financial year."),
    "Further, all members of the MPC voted unanimously to continue with the accommodative stance as long as necessary.",
    "CPI inflation is projected at 5.8 per cent for Q4:2020-21.",
    "Real GDP growth is projected at (-)7.5 per cent in 2020-21.",
    "CPI inflation rose sharply to 7.6 per cent in October 2020.",
    "Rural demand is expected to strengthen further.",
    "Global trade was recovering.",
]
NEW = [
    ("9. All members of the MPC – Dr. Shashanka Bhide, Dr. Ashima Goyal and Shri Shaktikanta Das – unanimously voted for "
     "keeping the policy repo rate unchanged at 4 per cent."),
    ("Accordingly, the MPC decided to continue with an accommodative stance of monetary policy till the prospects of a "
     "sustained recovery are well secured while closely monitoring the evolving outlook for inflation."),
    "Furthermore, all members of the MPC voted to continue with the accommodative stance as long as necessary.",
    "CPI inflation is projected at 5.2 per cent for Q4:2020-21.",
    "Real GDP growth is projected at 10.5 per cent in 2021-22.",
    "CPI inflation fell to 4.6 per cent in December.",
    "Rural demand is likely to remain resilient.",
    "Global trade has recovered further.",
]


def _pair():
    def spans(sentences):
        out, at = [], 0
        for s in sentences:
            out.append([at, at + len(s)])
            at += len(s) + 1
        return out

    so, sn = spans(OLD), spans(NEW)
    segments = {"pairs": [{"kind": "changed", "old": [so[i]], "new": [sn[i]]} for i in range(len(OLD))]}
    a = Doc(1, "mpc_resolution", "MPC resolution", "a", "RBI", date(2020, 12, 4), " ".join(OLD))
    b = Doc(2, "mpc_resolution", "MPC resolution", "b", "RBI", date(2021, 2, 5), " ".join(NEW))
    return segments, a, b


def test_v24_is_v23s_text_with_v23s_schema_on_sonnet_and_is_current():
    v23, v24 = load_prompt("statement_diff_summary", "v23"), load_prompt("statement_diff_summary", "v24")
    assert v24.text == v23.text and v24.version == "v24"
    assert load_schema("statement_diff_summary", "v24") == load_schema("statement_diff_summary", "v23")
    assert PROMPT_VERSION["statement_diff_summary"] == "v26" and summaries.DIFF_RESTATED_FROM == 24   # v26: round 23
    assert summaries.DIFF_MODEL_OF["v24"] == summaries.DIFF_MODEL_OF["v23"]
    assert summaries.DIFF_EFFORT_OF["v24"] == summaries.DIFF_EFFORT_OF["v23"]


def test_a_further_decision_sentence_that_states_the_stance_is_the_stance():
    texts = [x.text for x in fallback_bullets(*_pair(), [], packed=True, restated=True)]
    assert len(texts) == 5
    assert "unchanged at 4 per cent" in texts[0] and "till the prospects" not in texts[0]          # the decision, once
    stance = [t for t in texts if "till the prospects of a sustained recovery are well secured" in t]
    assert len(stance) == 1 and stance is not texts[0]
    assert stance[0].startswith('The previous MPC resolution said: "Accordingly, the MPC decided to maintain status quo')
    assert "Also, the previous MPC resolution said: \"Further, all members of the MPC voted unanimously" in stance[0]
    assert sum("policy repo rate unchanged" in t for t in texts) == 1


def test_v23s_fallback_left_it_out_as_a_second_decision():
    texts = [x.text for x in fallback_bullets(*_pair(), [], packed=True)]
    assert len(texts) == 5 and not any("till the prospects" in t for t in texts)                    # as 3592 ran
    assert [x.text for x in fallback_bullets(*_pair(), [])] == [x.text for x in fallback_bullets(*_pair(), [],
                                                                                                restated=True)]  # packed only
