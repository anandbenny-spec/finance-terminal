"""#445, round 17 (Anand's decision of 14 Nov 2026, about 22:19 IST, after round 16's third dev run): `statement_diff_summary`
v20 is v19's text (v17's), marking two fixes (`summaries.DIFF_OPEN_END_FROM`, `summaries._kind`):
- a quotation ending on an open word ("a", "the", "to" …) in the middle of a phrase fails even where the bullet goes on after it
  in its own words: mpc-2026-10-07's 3449 quoted "action ahead can only be a" and went on "hike or a pause", the statement's
  "rate hike or a pause" with a word left out;
- the fallback takes one change of each kind first (decision, stance, dissent, projections, inflation, economy, balance
  sheet, corridor rates), the decision being the policy rate or the target range with a verb of deciding: mpc-2019-02-07's
  3467 ranked two call-rate sentences ("traded below the policy repo rate") with the decision and left out the stance change,
  the dissent and the projections; and an added and a removed sentence of the same kind and topic are one change.
Replies made with v19 or earlier keep the checks they ran with. No database and no model."""
from datetime import date

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import FALLBACK_KINDS, Doc, cut_quote_problem, fallback_bullets
from ft.engines.document.diff import diff_texts
from ft.jobs.tasks.ai_batch import PROMPT_VERSION


def kind(text: str) -> str:
    return FALLBACK_KINDS[summaries._kind(text)]


def _pair(old: str, new: str):
    a = Doc(1, "mpc_resolution", "MPC resolution", "a", "RBI", date(2018, 12, 5), old)
    b = Doc(2, "mpc_resolution", "MPC resolution", "b", "RBI", date(2019, 2, 7), new)
    return diff_texts(old, new).segments, a, b


def test_v20_and_v21_are_v19s_text_with_v19s_schema_and_v21_is_current():
    v19, v20 = load_prompt("statement_diff_summary", "v19"), load_prompt("statement_diff_summary", "v20")
    v21 = load_prompt("statement_diff_summary", "v21")             # round 18's revision of the fallback
    v22 = load_prompt("statement_diff_summary", "v22")             # round 19's
    assert v22.text == v21.text == v20.text == v19.text and v20.version == "v20" and "<!--" not in v20.text
    assert load_schema("statement_diff_summary", "v21") == load_schema("statement_diff_summary", "v19")
    assert load_schema("statement_diff_summary", "v22") == load_schema("statement_diff_summary", "v19")
    assert load_schema("statement_diff_summary", "v20") == load_schema("statement_diff_summary", "v19")
    assert PROMPT_VERSION["statement_diff_summary"] == "v22" and summaries.DIFF_OPEN_END_FROM == 20   # v22: round 19
    assert summaries.DIFF_MODEL_OF["v20"] == summaries.DIFF_MODEL_OF["v19"]
    assert summaries.DIFF_EFFORT_OF["v20"] == summaries.DIFF_EFFORT_OF["v19"]


# ---------------------------------------------------------------------------------------------- the open-ended quotation
PASSAGE = ("It only signals that given the current conditions, rate cuts are off the table in the near term and policy action "
           "ahead can only be a rate hike or a pause, depending on the evolving conditions and the outlook.")


def test_a_quotation_ending_on_an_open_word_fails_from_v20_even_where_the_bullet_goes_on():
    # mpc-2026-10-07's 3449, bullet 2 (round 16's third dev run).
    text = ('The new statement adds that "rate cuts are off the table in the near term" and that "action ahead can only be a" '
            "hike or a pause.")
    problem = cut_quote_problem(text, [PASSAGE], subject_ok=True, open_end=True)
    assert problem is not None and problem.startswith('a quotation that ends on "a", in the middle of a phrase, and goes on')
    assert cut_quote_problem(text, [PASSAGE], subject_ok=True) is None        # v19 and earlier: as it ran


def test_a_quotation_the_bullet_goes_on_from_still_passes_when_it_ends_on_a_closed_word_or_where_its_passage_ends():
    goes_on = 'The new statement says "rate cuts are off the table" for now.'
    assert cut_quote_problem(goes_on, [PASSAGE], subject_ok=True, open_end=True) is None
    whole = 'The new statement says "policy action ahead can only be a rate hike or a pause" from here.'
    assert cut_quote_problem(whole, [PASSAGE], subject_ok=True, open_end=True) is None
    # Ending on an open word where the passage itself ends there isn't cut ("… a target of 2 percent or a" never ends a
    # sentence, but a quotation of a whole passage that ends on "to" would be the passage's own end).
    ends = "The Committee decided that it is appropriate to"
    assert cut_quote_problem(f'The new statement says "{ends}" act.', [ends], open_end=True) is None


def test_v20s_open_end_check_is_gated_by_version_in_the_json_route():
    old = "The MPC decided to keep the policy repo rate unchanged at 5.25 per cent."
    new = f"The MPC decided to raise the policy repo rate by 25 bps to 5.50 per cent. {PASSAGE}"
    segs, a, b = _pair(old, new)
    changed = dict(zip((1, 2), summaries.changed_spans(segs), strict=True))
    sentences = dict(zip((1, 2), summaries.sentence_spans(segs), strict=True))
    sources = summaries.diff_summary_request(b, a, segs, load_prompt("statement_diff_summary", "v20")).sources
    answer = {"bullets": [
        {"text": 'The repo rate was raised "by 25 bps to 5.50 per cent", where it had been kept "unchanged at 5.25 per cent".',
         "quotes": [{"document": "new", "text": "by 25 bps to 5.50 per cent"},
                    {"document": "previous", "text": "unchanged at 5.25 per cent"}]},
        {"text": 'The new resolution says "action ahead can only be a" hike or a pause.',
         "quotes": [{"document": "new", "text": "action ahead can only be a"}]}]}
    v20 = summaries.check_diff_json(answer, sources, changed, version="v20", sentences=sentences)
    v19 = summaries.check_diff_json(answer, sources, changed, version="v19", sentences=sentences)
    assert v20.bullets[0].problem is None and v19.bullets[0].problem is None
    assert v20.bullets[1].problem is not None and 'ends on "a", in the middle of a phrase, and goes on' in v20.bullets[1].problem
    assert v19.bullets[1].problem is None or "goes on in the bullet's own words" not in v19.bullets[1].problem


# ---------------------------------------------------------------------------------------------- the fallback's kinds
def test_the_kinds_of_change():
    assert kind("keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.5 per cent.") == "decision"
    assert kind("In support of these goals, the Committee decided to raise the target range for the federal funds rate to "
                "3/4 to 1 percent.") == "decision"
    # A market rate against the repo rate is no decision (mpc-2019-02-07's 3467), nor a corridor rate or a vote.
    assert kind("The weighted average call rate (WACR) traded below the policy repo rate on 12 out of 20 days in December.") \
        == "other"
    assert kind("The WACR was below the repo rate on an average by 4 basis points in December.") == "other"
    assert kind("Consequently, the reverse repo rate under the LAF remains unchanged at 3.35 per cent, and the marginal "
                "standing facility (MSF) rate and the Bank Rate at 4.25 per cent.") == "corridor"
    assert kind("As regards the reduction in the policy repo rate, Dr. Pami Dua and Shri Shaktikanta Das voted in favour of "
                "the decision.") == "votes"
    # Dissents: a vote against, "preferred", "were of the view", and a vote for another rate action.
    assert kind("Voting against this action was James Bullard, who preferred at this meeting to raise the target range for "
                "the federal funds rate by 0.5 percentage point to 1/2 to 3/4 percent.") == "dissent"
    assert kind("Dr. Chetan Ghate and Dr. Viral V. Acharya voted to keep the policy rate unchanged.") == "dissent"
    assert kind("Two members - Dr. Nagesh Kumar and Prof. Ram Singh - were of the view that the stance be retained at "
                "neutral.") == "dissent"
    assert kind("The MPC also decided to change the monetary policy stance from calibrated tightening to neutral.") == "stance"
    assert kind("Global financial markets remain supported by highly accommodative monetary and liquidity conditions.") \
        == "other"
    assert kind("The path of CPI inflation is revised downwards to 2.8 per cent in Q4:2018-19.") == "projections"
    assert kind("The outlook for inflation has turned adverse relative to expectations in the last two months.") == "inflation"
    assert kind("Although household spending has been rising at a strong pace, business fixed investment and exports have "
                "weakened.") == "economy"
    assert kind("The Committee will conclude the reduction of its aggregate securities holdings in the System Open Market "
                "Account in August.") == "balance sheet"


MPC_OLD = ("The MPC decided to: keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.5 per "
           "cent. Consequently, the reverse repo rate under the LAF remains at 6.25 per cent, and the marginal standing "
           "facility (MSF) rate and the Bank Rate at 6.75 per cent. The decision of the MPC is consistent with the stance of "
           "calibrated tightening of monetary policy. Taking all these factors into consideration, inflation is projected at "
           "2.7-3.2 per cent in H2:2018-19 and 3.8-4.2 per cent in H1:2019-20. The weighted average call rate (WACR) traded "
           "below the policy repo rate on 14 out of 21 days in October. While the decision on keeping the policy rate "
           "unchanged was unanimous, Dr. Ravindra H. Dholakia voted to change the stance to neutral.")
MPC_NEW = ("The MPC decided to: reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points "
           "from 6.5 per cent to 6.25 per cent with immediate effect. Consequently, the reverse repo rate under the LAF stands "
           "adjusted to 6.0 per cent, and the MSF rate and the Bank Rate to 6.5 per cent. The MPC also decided to change the "
           "monetary policy stance from calibrated tightening to neutral. Taking into consideration these developments, the "
           "path of CPI inflation is revised downwards to 2.8 per cent in Q4:2018-19 and 3.2-3.4 per cent in H1:2019-20. The "
           "weighted average call rate (WACR) traded below the policy repo rate on 12 out of 20 days in December. The WACR was "
           "below the repo rate on an average by 4 basis points in December. Dr. Chetan Ghate and Dr. Viral V. Acharya voted "
           "to keep the policy rate unchanged.")


def test_the_fallback_takes_the_decision_the_stance_the_dissent_and_the_projections_before_the_call_rate():
    # Round 16's third dev run: mpc-2019-02-07's fallback (3467) quoted the decision, the votes for it, the corridor rates and
    # two WACR sentences, and left out the stance change, the dissent and the projections that v1 (514) had.
    wacr = [("new", "The WACR was below the repo rate on an average by 4 basis points in December."),
            ("new", "The weighted average call rate (WACR) traded below the policy repo rate on 12 out of 20 days")]
    for prefer in ([], wacr):                               # even where the rejected reply quoted the call rate first
        bullets = fallback_bullets(*_pair(MPC_OLD, MPC_NEW), prefer)
        texts = [b.text for b in bullets]
        assert len(bullets) == 5 and "reduce the policy repo rate" in texts[0]
        assert any("change the monetary policy stance from calibrated tightening to neutral" in t for t in texts)
        assert any("Chetan Ghate" in t for t in texts) and any("revised downwards" in t for t in texts)
        assert not any("WACR" in t for t in texts)
        kinds = [summaries._kind(t) for t in texts]
        assert kinds == sorted(kinds)                       # read in kind order, the decision first


def test_an_added_and_a_removed_sentence_of_the_same_kind_and_topic_are_one_change():
    # mpc-2020-12-04 (3462) showed its GDP projection as two bullets, one for each document.
    old = ("Real GDP growth in 2020-21 is projected to be negative at (-)9.5 per cent, with risks tilted to the downside. "
           "The outlook for agriculture is robust.")
    new = ("The outlook for agriculture is robust. Taking these factors into consideration, real GDP growth is projected at "
           "(-)7.5 per cent in 2020-21, with risks broadly balanced.")
    a = Doc(1, "mpc_resolution", "MPC resolution", "a", "RBI", date(2020, 10, 9), old)
    b = Doc(2, "mpc_resolution", "MPC resolution", "b", "RBI", date(2020, 12, 4), new)
    o, n = old.index("Real GDP"), new.index("Taking these")
    segments = {"pairs": [{"kind": "removed", "old": [[o, old.index(" The outlook")]]},
                          {"kind": "added", "new": [[n, len(new)]]}]}
    bullets = fallback_bullets(segments, a, b, [])
    assert len(bullets) == 1
    assert bullets[0].text.startswith('The previous MPC resolution said: "Real GDP growth in 2020-21 is projected')
    assert '" The new MPC resolution says: "Taking these factors into consideration' in bullets[0].text
    assert "doesn't have this sentence" not in bullets[0].text
    # Of different kinds, they stay apart, each labelled.
    segments2 = {"pairs": [{"kind": "removed", "old": [[o, old.index(" The outlook")]]},
                           {"kind": "added", "new": [[0, new.index(" Taking")]]}]}
    apart = fallback_bullets(segments2, a, b, [])
    assert len(apart) == 2 and all("doesn't have this sentence" in x.text for x in apart)


def test_a_quotation_that_names_the_words_it_quotes_is_no_cut_phrase():
    # 2578 (v15, published): measured on dev's stored bullets, the one quotation v20's check would have failed wrongly.
    passage = "In considering the extent and timing of additional adjustments to the target range, the Committee will assess."
    text = 'The guidance dropped the words "the extent and timing of" from the phrase on adjustments to the target range.'
    assert cut_quote_problem(text, [passage], subject_ok=True, open_end=True) is None
    cut = 'The guidance now weighs "the extent and timing of" adjustments to the target range.'
    assert cut_quote_problem(cut, [passage], subject_ok=True, open_end=True) is not None


# ---------------------------------------------------------------------------------------------- round 18 (v21)
def test_growth_projections_are_their_own_kind_beside_the_inflation_projections():
    assert kind("Taking into consideration these factors, real GDP growth for 2019-20 is revised downwards from 6.1 per cent "
                "to 5.0 per cent.") == "growth projections"
    assert kind("Taking into consideration these factors, the CPI inflation projection is revised upwards to 6.5 per cent for "
                "Q4:2019-20.") == "projections"
    assert kind("Real GDP growth for 2025-26 is projected at 6.5 per cent.") == "growth projections"


def test_the_fallback_keeps_both_projections_before_the_inflation_reading_and_activity():
    # Round 17: mpc-2020-02-06's fallback (3493) left out the GDP projection revision that v1 (404) had.
    old = ("Accordingly, the MPC decided to keep the policy repo rate unchanged and continue with the accommodative stance as "
           "long as it is necessary to revive growth. However, given the evolving growth-inflation dynamics, the MPC felt it "
           "appropriate to take a pause at this juncture. Taking into consideration these factors, the CPI inflation projection "
           "is revised upwards to 5.1-4.7 per cent for H2:2019-20. Retail inflation increased sharply to 4.6 per cent in "
           "October. Taking into consideration these factors, real GDP growth for 2019-20 is revised downwards from 6.1 per cent "
           "to 5.0 per cent. Service sector activity generally remained weak in October.")
    new = ("Accordingly, the MPC decided to keep the policy repo rate unchanged and persevere with the accommodative stance as "
           "long as necessary to revive growth. Given the evolving growth-inflation dynamics, the MPC felt it appropriate to "
           "maintain status quo. Taking into consideration these factors, the CPI inflation projection is revised upwards to "
           "6.5 per cent for Q4:2019-20. Retail inflation surged from 4.6 per cent in October to 7.4 per cent in December. "
           "Taking into consideration these factors, real GDP growth for 2020-21 is projected at 6.0 per cent. Some "
           "high-frequency indicators point to a lift in the momentum of economic activity.")
    bullets = fallback_bullets(*_pair(old, new), [])
    texts = [b.text for b in bullets]
    assert len(bullets) == 5 and "persevere" in texts[0]
    assert any("CPI inflation projection" in t for t in texts) and any("real GDP growth for 2020-21" in t for t in texts)
    assert not any("momentum of economic activity" in t for t in texts)       # the fifth kind waits for a sixth slot


def test_up_to_three_consecutive_new_sentences_are_one_change_quoted_together():
    # Round 17: fomc-2023-03-22's fallback (3490) gave its three new banking sentences three of its five bullets.
    old = "The Committee decided to raise the target range to 4-1/2 to 4-3/4 percent. Job gains have been robust."
    banking = ("The U.S. banking system is sound and resilient. Recent developments are likely to result in tighter credit "
               "conditions. The extent of these effects is uncertain. The Committee will closely monitor incoming information.")
    new = f"The Committee decided to raise the target range to 4-3/4 to 5 percent. {banking} Job gains have picked up."
    a = Doc(1, "fomc_statement", "FOMC statement", "a", "Fed", date(2023, 2, 1), old)
    b = Doc(2, "fomc_statement", "FOMC statement", "b", "Fed", date(2023, 3, 22), new)
    starts = [new.index(x) for x in ("The U.S. banking", "Recent developments", "The extent", "The Committee will")]
    ends = [new.index(x) + len(x) for x in ("resilient.", "credit conditions.", "is uncertain.", "incoming information.")]
    segments = {"pairs": [{"kind": "changed", "old": [[0, old.index(" Job")]], "new": [[0, new.index(" The U.S.")]]}]
                + [{"kind": "added", "new": [[s, e]]} for s, e in zip(starts, ends, strict=True)]
                + [{"kind": "changed", "old": [[old.index("Job"), len(old)]], "new": [[new.index("Job"), len(new)]]}]}
    bullets = fallback_bullets(segments, a, b, [])
    texts = [x.text for x in bullets]
    together = [t for t in texts if "banking system is sound" in t]
    assert len(together) == 1 and "The extent of these effects is uncertain." in together[0]
    assert together[0].endswith("The previous FOMC statement doesn't have these sentences.")
    assert "closely monitor" not in together[0]                                  # at most three in one change
    alone = [t for t in texts if "closely monitor" in t]
    assert alone and alone[0].endswith("The previous FOMC statement doesn't have this sentence.")
    assert any("Job gains have picked up" in t for t in texts) and len(bullets) == 4


# ---------------------------------------------------------------------------------------------- round 19 (v22)
def test_the_committees_own_vote_is_the_decision_and_other_central_banks_policy_rates_never_are():
    # Round 18: mpc-2026-10-07's fallback (3522) led with "higher global policy rates" and left out its rate hike.
    assert kind("2. After a detailed assessment of the evolving macroeconomic and financial developments and the outlook, the "
                "MPC voted unanimously to increase the policy repo rate under the liquidity adjustment facility (LAF) by 25 bps "
                "to 5.50 per cent with immediate effect.") == "decision"
    assert kind("13. Considering all these factors, the MPC voted to keep the policy rate unchanged.") == "decision"
    assert kind("All members of the MPC – Dr. Shashanka Bhide and Shri Shaktikanta Das – unanimously voted for keeping the "
                "policy repo rate unchanged.") == "decision"
    assert kind("The Fed commentary thereafter along with rate tightening by major systemically important central banks have "
                "reinforced expectations of higher global policy rates. Persistent inflation has prompted several central "
                "banks to raise rates.") != "decision"
    # Guidance naming the target range isn't a second decision; a member's vote for another cut is a dissent.
    assert kind("The Committee anticipates that ongoing increases in the target range will be appropriate in order to attain "
                "a stance of monetary policy that is sufficiently restrictive.") == "stance"
    assert kind('The previous FOMC statement said: "In support of these goals, the Committee decided to raise the target '
                'range for the federal funds rate to 4-1/2 to 4-3/4 percent."') == "decision"
    assert kind('The previous MPC resolution said: "keep the policy repo rate under the liquidity adjustment facility (LAF) '
                'unchanged at 6.5 per cent."') == "decision"
    assert kind("Shri Saugata Bhattacharya voted for a 25 bps cut in repo rate.") == "dissent"


RBI_2026_OLD = ("2. After a detailed assessment of the evolving macroeconomic and financial developments and the outlook, the MPC "
                "voted unanimously to keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.25 "
                "per cent. Persistent inflation has prompted several central banks to raise rates while others remain vigilant. "
                "The MPC also decided to continue with the neutral stance. 9. CPI inflation for 2026-27 is projected to be 5.0 "
                "per cent. 13. Considering all these factors, the MPC voted to keep the policy rate unchanged.")
RBI_2026_NEW = ("2. After a detailed assessment of the evolving macroeconomic and financial developments and the outlook, the MPC "
                "voted unanimously to increase the policy repo rate under the liquidity adjustment facility (LAF) by 25 bps to "
                "5.50 per cent with immediate effect. The Fed commentary thereafter along with rate tightening by major "
                "systemically important central banks have reinforced expectations of higher global policy rates. The MPC also "
                "decided to change the stance to calibrated tightening. 9. CPI inflation is projected to be 5.2 per cent for "
                "2026-27. 14. Considering all these factors, the MPC unanimously voted to increase the policy repo rate by 25 "
                "basis points to 5.50 per cent.")


def test_the_fallback_leads_with_the_committees_vote_shows_the_decision_once_and_leaves_out_other_central_banks():
    bullets = fallback_bullets(*_pair(RBI_2026_OLD, RBI_2026_NEW), [])
    texts = [b.text for b in bullets]
    assert "voted unanimously to increase the policy repo rate" in texts[0]
    assert sum("increase the policy repo rate" in t for t in texts) == 1               # the decision once
    assert any("change the stance to calibrated tightening" in t for t in texts)
    assert "global policy rates" not in texts[0] and kind(texts[0]) == "decision"   # never the decision (3522)


def test_a_run_of_consecutive_new_sentences_stops_at_a_paragraph_number():
    old = "The MPC decided to keep the policy repo rate unchanged at 6.50 per cent."
    new = ("The MPC decided to keep the policy repo rate unchanged at 6.50 per cent. Real GDP growth for Q1:2024-25 is "
           "projected at 6.6 per cent. 9. The headline inflation is likely to witness a sharp spike in July.")
    a = Doc(1, "mpc_resolution", "MPC resolution", "a", "RBI", date(2023, 6, 8), old)
    b = Doc(2, "mpc_resolution", "MPC resolution", "b", "RBI", date(2023, 8, 10), new)
    s1, s2 = new.index("Real GDP"), new.index("9. The headline")
    segments = {"pairs": [{"kind": "added", "new": [[s1, new.index(" 9. The")]]}, {"kind": "added", "new": [[s2, len(new)]]}]}
    bullets = fallback_bullets(segments, a, b, [])
    assert len(bullets) == 2 and all("doesn't have this sentence." in x.text for x in bullets)
