"""#445, round 27 (Anand's decision of 17 Nov 2026, about 19:40 IST: round 27 starts with a diagnosis, and the build fixes the
cause it finds). The diagnosis (#445, 17 Nov 2026, 23:00 IST): every rule (c) failure of rounds 22 to 26 was an RBI target
whose vote or projection was set beside the wrong sentence, or shown from one side only, in the fallback and on the model's
route alike, because what sits beside what was decided by word overlap. `statement_diff_summary` v30
(`summaries.DIFF_FACTS_FROM`) pairs the votes and the committee's projections by fact, read from each stored text by module
1's own readers (`ai_batch.diff_facts`). The sentences below are cut from the named resolutions' stored texts on dev (only
whole sentences, in their order; a paragraph keeps its number). v29 keeps its rules, and a Fed pair is read as before. No
database and no model."""
from types import SimpleNamespace

from test_diff_summary_v25 import _docs
from test_diff_summary_v28 import _answer, _checked

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import Facts, fallback_bullets, must_changes, must_problems, must_text
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION, Work, diff_facts

# ---- mpc-2025-02-07 (documents 226 and 227; round 26's 3770, built by code): the vote on the rate and the vote on the stance
LEAD_226 = ("After assessing the current and evolving macroeconomic situation, the Monetary Policy Committee (MPC) at its "
            "meeting today (December 6, 2024) decided to:")
KEEP_226 = "Keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.50 per cent."
LEAD_227 = ("After assessing the current and evolving macroeconomic situation, the MPC unanimously decided to:")
CUT_227 = ("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points to 6.25 per cent with "
           "immediate effect;")
GDP_226 = ("3. Headwinds from geo-political uncertainties, volatility in international commodity prices, and geo-economic "
           "fragmentation continue to pose risks to the outlook. Taking all these factors into consideration, real GDP growth "
           "for 2024-25 is projected at 6.6 per cent with Q3 at 6.8 per cent; and Q4 at 7.2 per cent. Real GDP growth for "
           "Q1:2025-26 is projected at 6.9 per cent; and Q2 at 7.3 per cent (Chart 1). The risks are evenly balanced.")
GDP_Q1_226 = "Real GDP growth for Q1:2025-26 is projected at 6.9 per cent; and Q2 at 7.3 per cent (Chart 1)."
GDP_227 = ("4. However, headwinds from geo-political tensions, protectionist trade policies, volatility in international "
           "commodity prices and financial market uncertainties, continue to pose downside risks to the outlook. Taking all "
           "these factors into consideration, real GDP growth for 2025-26 is projected at 6.7 per cent with Q1 at 6.7 per "
           "cent; Q2 at 7.0 per cent; and Q3 and Q4 at 6.5 per cent each (Chart 1). The risks are evenly balanced.")
GDP_FY_227 = ("Taking all these factors into consideration, real GDP growth for 2025-26 is projected at 6.7 per cent with Q1 at "
              "6.7 per cent; Q2 at 7.0 per cent; and Q3 and Q4 at 6.5 per cent each (Chart 1).")
VOTE_226 = ("6. Shri Saugata Bhattacharya, Dr. Rajiv Ranjan, Dr. Michael Debabrata Patra and Shri Shaktikanta Das voted to keep "
            "the policy repo rate unchanged at 6.50 per cent. Dr. Nagesh Kumar and Professor Ram Singh voted to reduce the "
            "policy repo rate by 25 basis points.")
FOUR_226 = ("Shri Saugata Bhattacharya, Dr. Rajiv Ranjan, Dr. Michael Debabrata Patra and Shri Shaktikanta Das voted to keep "
            "the policy repo rate unchanged at 6.50 per cent.")
TWO_226 = "Dr. Nagesh Kumar and Professor Ram Singh voted to reduce the policy repo rate by 25 basis points."
STANCE_226 = ("7. Dr. Nagesh Kumar, Shri Saugata Bhattacharya, Professor Ram Singh, Dr. Rajiv Ranjan, Dr. Michael Debabrata "
              "Patra and Shri Shaktikanta Das voted for continuing with the neutral stance of monetary policy and to remain "
              "unambiguously focused on a durable alignment of inflation with the target, while supporting growth.")
VOTE_227 = ("7. The MPC noted that inflation has declined. These growth-inflation dynamics open up policy space for the MPC to "
            "support growth, while remaining focussed on aligning inflation with the target. Accordingly, the MPC unanimously "
            "voted to reduce the policy repo rate by 25 basis points to 6.25 per cent.")
ALL_227 = "Accordingly, the MPC unanimously voted to reduce the policy repo rate by 25 basis points to 6.25 per cent."
STANCE_227 = ("8. At the same time, excessive volatility in global financial markets and continued uncertainties about global "
              "trade policies coupled with adverse weather events pose risks to the growth and inflation outlook. This calls "
              "for the MPC to remain watchful. Accordingly, the MPC unanimously voted to continue with a neutral stance. This "
              "will provide MPC the flexibility to respond to the evolving macroeconomic environment.")
NEUTRAL_227 = "Accordingly, the MPC unanimously voted to continue with a neutral stance."
VOLATILITY_227 = "At the same time, excessive volatility in global financial markets"
DEC_2024 = [LEAD_226, KEEP_226, GDP_226, VOTE_226, STANCE_226]
FEB_2025 = [LEAD_227, CUT_227, GDP_227, VOTE_227, STANCE_227]

# ---- mpc-2019-12-05 (documents 194 and 195; round 26's 3758, the model): the CPI projection, and the recap of October's
RECAP_194 = ("19. In the third bi-monthly resolution of August 2019, CPI inflation was projected at 3.1 per cent for Q2:2019-20, "
             "3.5-3.7 per cent for H2:2019-20 and 3.6 per cent for Q1: 2020-21 with risks evenly balanced. The actual inflation "
             "outcomes for Q2 so far (July-August) at 3.2 per cent have been broadly in line with these projections.")
CPI_194 = ("20. Finally, financial markets remain volatile with currencies of several emerging market economies trading with a "
           "depreciating bias in the recent period. Taking into consideration these factors and the impact of recent policy rate "
           "cuts, the CPI inflation projection is revised slightly upwards to 3.4 per cent for Q2:2019-20, while projections are "
           "retained at 3.5-3.7 per cent for H2:2019-20 and 3.6 per cent for Q1:2020-21, with risks evenly balanced (Chart 1).")
RECAP_195 = ("20. In the fourth bi-monthly resolution of October 2019, CPI inflation was projected at 3.4 per cent for "
             "Q2:2019-20, 3.5-3.7 per cent for H2:2019-20 and 3.6 per cent for Q1:2020-21 with risks evenly balanced. The actual "
             "inflation outcome for Q2 evolved broadly in line with projections – averaging 3.5 per cent.")
CPI_195 = ("21. Sixth, crude oil prices are expected to remain range bound, barring any supply disruptions due to geo-political "
           "tensions. Taking into consideration these factors, the CPI inflation projection is revised upwards to 5.1-4.7 per "
           "cent for H2:2019-20 and 4.0-3.8 per cent for H1:2020-21, with risks broadly balanced (Chart 1).")
VOTES_194 = ("23. All members of the MPC voted to reduce the policy repo rate and to continue with the accommodative stance of "
             "monetary policy. Dr. Chetan Ghate, Dr. Pami Dua, Dr. Michael Debabrata Patra, Shri Bibhu Prasad Kanungo and Shri "
             "Shaktikanta Das voted to reduce the repo rate by 25 basis points. Dr. Ravindra H. Dholakia voted to reduce the "
             "repo rate by 40 basis points.")
VOTES_195 = ("25. All members of the MPC – Dr. Chetan Ghate, Dr. Pami Dua, Dr. Ravindra H. Dholakia, Dr. Michael Debabrata "
             "Patra, Shri Bibhu Prasad Kanungo and Shri Shaktikanta Das – voted in favour of the decision.")
LEAD_2019 = ("On the basis of an assessment of the current and evolving macroeconomic situation, the Monetary Policy Committee "
             "(MPC) at its meeting today ({}) decided to:")
OCT_2019 = [LEAD_2019.format("October 4, 2019"), ("reduce the policy repo rate under the liquidity adjustment facility (LAF) by "
                                                  "25 basis points to 5.15 per cent from 5.40 per cent with immediate effect."),
            RECAP_194, CPI_194, VOTES_194]
DEC_2019 = [LEAD_2019.format("December 5, 2019"), ("keep the policy repo rate under the liquidity adjustment facility (LAF) "
                                                   "unchanged at 5.15 per cent."),
            RECAP_195, CPI_195, VOTES_195]

# ---- mpc-2022-09-30 (documents 212 and 213; round 22's 3653): unanimity on the rate on one side only
VOTES_212 = [LEAD_2019.format("August 5, 2022"),
             ("Increase the policy repo rate under the liquidity adjustment facility (LAF) by 50 basis points to 5.40 per cent "
              "with immediate effect."),
             ("9. All members of the MPC – Dr. Shashanka Bhide, Dr. Ashima Goyal, Prof. Jayanth R. Varma, Dr. Rajiv Ranjan, Dr. "
             "Michael Debabrata Patra and Shri Shaktikanta Das – unanimously voted to increase the policy repo rate by 50 basis "
             "points to 5.40 per cent."),
             ("10. All members - Dr. Shashanka Bhide, Dr. Ashima Goyal, Dr. Rajiv Ranjan, Dr. Michael Debabrata Patra and Shri "
             "Shaktikanta Das, except Prof. Jayanth R. Varma - voted to remain focused on withdrawal of accommodation to ensure "
             "that inflation remains within the target going forward, while supporting growth. Prof. Jayanth R. Varma "
             "expressed reservations on this part of the resolution.")]
VOTES_213 = [LEAD_2019.format("September 30, 2022"),
             ("Increase the policy repo rate under the liquidity adjustment facility (LAF) by 50 basis points to 5.90 per cent "
              "with immediate effect."),
             ("11. Dr. Shashanka Bhide, Prof. Jayanth R. Varma, Dr. Rajiv Ranjan, Dr. Michael Debabrata Patra and Shri "
             "Shaktikanta Das voted to increase the policy repo rate by 50 basis points. Dr. Ashima Goyal voted to increase the "
             "repo rate by 35 basis points."),
             ("12. Dr. Shashanka Bhide, Dr. Ashima Goyal, Dr. Rajiv Ranjan, Dr. Michael Debabrata Patra and Shri Shaktikanta "
             "Das voted to remain focused on withdrawal of accommodation to ensure that inflation remains within the target "
             "going forward, while supporting growth. Prof. Jayanth R. Varma voted against this part of the resolution.")]
FIVE_213 = ("Dr. Shashanka Bhide, Prof. Jayanth R. Varma, Dr. Rajiv Ranjan, Dr. Michael Debabrata Patra and Shri Shaktikanta "
            "Das voted to increase the policy repo rate by 50 basis points.")
GOYAL_213 = "Dr. Ashima Goyal voted to increase the repo rate by 35 basis points."

# ---- mpc-2019-06-06 (documents 191 and 192; the diagnosis's reproduction): a 4 to 2 vote and a 5 to 1 vote on the stance,
# then one unanimous decision on both
LEAD_2019_JUNE = ("On the basis of an assessment of the current and evolving macroeconomic situation, the Monetary Policy "
                  "Committee (MPC) at its meeting today decided to:")
VOTES_191 = [LEAD_2019_JUNE,
             ("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points to 6.0 per cent from "
              "6.25 per cent with immediate effect."),
             ("25. Dr. Pami Dua, Dr. Ravindra H. Dholakia, Dr. Michael Debabrata Patra and Shri Shaktikanta Das voted in favour "
             "of the decision to reduce the policy repo rate by 25 basis points. Dr. Chetan Ghate and Dr. Viral V. Acharya "
             "voted to keep the policy rate unchanged."),
             ("26. Dr. Chetan Ghate, Dr. Pami Dua, Dr. Michael Debabrata Patra, Dr. Viral V. Acharya and Shri Shaktikanta Das "
             "voted in favour of the decision to maintain the neutral stance of monetary policy. Dr. Ravindra H. Dholakia "
             "voted to change the stance from neutral to accommodative.")]
VOTES_192 = [LEAD_2019_JUNE,
             ("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points to 5.75 per cent from "
              "6.0 per cent with immediate effect."),
             ("24. Against this backdrop, all members of the MPC (Dr. Chetan Ghate, Dr. Pami Dua, Dr. Ravindra H. Dholakia, Dr. "
             "Michael Debabrata Patra, Dr. Viral V. Acharya and Shri Shaktikanta Das) unanimously decided to reduce the policy "
             "repo rate by 25 basis points and change the stance of monetary policy from neutral to accommodative.")]

# ---- mpc-2025-08-06 (documents 229 and 230; the diagnosis's reproduction): June's 5 to 1 vote, August's unanimous one
VOTES_229 = [("2. After assessing the current and evolving macroeconomic situation, the MPC voted to reduce the policy repo "
              "rate by 50 basis points (bps) to 5.50 per cent with immediate effect."),
             ("9. Thus, it is imperative to continue to stimulate domestic private consumption and investment through policy "
             "levers to step up the growth momentum. Accordingly, the MPC voted to reduce the policy repo rate by 50 bps to "
             "5.50 per cent. Dr. Nagesh Kumar, Prof. Ram Singh, Dr. Rajiv Ranjan, Dr. Poonam Gupta and Shri Sanjay Malhotra, "
             "voted to decrease the policy repo rate by 50 bps. Shri Saugata Bhattacharya voted for a 25 bps cut in repo rate.")]
VOTES_230 = [("2. After assessing the current and evolving macroeconomic situation, the MPC voted to maintain the policy repo "
              "rate at 5.50 per cent."),
             ("11. On balance, therefore, the current macroeconomic conditions, outlook and uncertainties call for continuation "
             "of the policy repo rate of 5.5 per cent and wait for further transmission of the front-loaded rate cuts to the "
             "credit markets and the broader economy. Accordingly, the MPC unanimously voted to keep the repo rate unchanged. "
             "The MPC further resolved to maintain a close vigil on the incoming data and the evolving domestic "
             "growth-inflation dynamics to chart out the appropriate monetary policy path. Accordingly, all members decided to "
             "continue with the neutral stance.")]


def _span(d, s):
    i = d.text.index(s)
    return i, i + len(s)


def _pair(old, new):
    seg, a, b = _docs(old, new)
    return seg, a, b, (diff_facts(a.text), diff_facts(b.text))


def _v30(seg, a, b, facts, v1=()):
    return fallback_bullets(seg, a, b, [], packed=True, restated=True, counterparts=True, v1=list(v1), kinds=True,
                            v1_pairs=True, v1_all=True, must=True, room=True, facts=facts)


def _v29(seg, a, b, v1=()):
    return fallback_bullets(seg, a, b, [], packed=True, restated=True, counterparts=True, v1=list(v1), kinds=True,
                            v1_pairs=True, v1_all=True, must=True, room=True)


def _cites(bullet, doc):
    return [(c.start, c.end) for c in bullet.citations if c.document_id == doc.document_id]


def _holds(bullet, doc, span):
    return any(s <= span[0] and span[1] <= e for s, e in _cites(bullet, doc))


def test_v30_is_v29s_text_plus_one_sentence_and_is_current():
    v29, v30 = load_prompt("statement_diff_summary", "v29"), load_prompt("statement_diff_summary", "v30")
    added = ("Set like beside like: the previous vote on the policy rate beside the new vote on the policy rate, the vote on "
             "the stance beside the vote on the stance, and each document's projection for the same period side by side, "
             "never one period's figure against another period's.")
    assert v30.version == "v30" and added in v30.text and added not in v29.text
    assert v30.text.replace(" " + added, "") == v29.text
    assert PROMPT_VERSION["statement_diff_summary"] == "v30" and summaries.DIFF_FACTS_FROM == 30
    assert summaries.DIFF_MODEL_OF["v30"] == summaries.DIFF_MODEL_OF["v29"]
    assert summaries.DIFF_EFFORT_OF["v30"] == summaries.DIFF_EFFORT_OF["v29"]
    assert load_schema("statement_diff_summary", "v30") == load_schema("statement_diff_summary", "v29")


def test_module_1s_readers_give_the_facts_of_a_stored_text():
    _, a, b, (fa, fb) = _pair(DEC_2024, FEB_2025)
    assert sorted(a.text[s:e].removeprefix("6. ") for s, e in fa.rate_votes) == [TWO_226, FOUR_226]
    assert [a.text[s:e] for s, e in fa.stance_votes] == [STANCE_226]
    assert [b.text[s:e] for s, e in fb.rate_votes] == [ALL_227] and [b.text[s:e] for s, e in fb.stance_votes] == [NEUTRAL_227]
    assert {(p[0], p[1]) for p in fa.projections} == {("gdp", "FY 2024-25"), ("gdp", "Q3 2024-25"), ("gdp", "Q4 2024-25"),
                                                      ("gdp", "Q1 2025-26"), ("gdp", "Q2 2025-26")}
    assert diff_facts("The Committee decided to maintain the target range for the federal funds rate.") == Facts()
    # Never the lead-in of the list of decisions ("… the MPC unanimously decided to:"), nor the decision's own statement.
    assert not any(b.text[s:e] == LEAD_227 for s, e in fb.rate_votes + fb.stance_votes)


def test_round_26s_vote_is_paired_rate_with_rate_and_stance_with_stance():
    # 3770's bullet 2 set the previous vote on the rate beside the new vote on the stance, and bullet 3 said the new
    # resolution doesn't have the previous vote on the stance.
    seg, a, b, facts = _pair(DEC_2024, FEB_2025)
    bullets = _v30(seg, a, b, facts)
    rate = [x for x in bullets if _holds(x, a, _span(a, FOUR_226))]
    assert len(rate) == 1 and _holds(rate[0], a, _span(a, TWO_226)) and _holds(rate[0], b, _span(b, ALL_227))
    stance = [x for x in bullets if _holds(x, a, _span(a, STANCE_226.removeprefix("7. ")))]
    assert len(stance) == 1 and _holds(stance[0], b, _span(b, NEUTRAL_227))
    text = rate[0].text
    four, two, unanimous = text.index(FOUR_226), text.index(TWO_226), text.index(ALL_227)
    assert four < two < unanimous and "doesn't have" not in text[:text.index("Also,") if "Also," in text else len(text)]
    assert not any(_holds(x, b, _span(b, ALL_227)) and _holds(x, b, _span(b, VOLATILITY_227)) and
                   x.text.index(VOLATILITY_227) < x.text.index(FOUR_226) for x in bullets if FOUR_226 in x.text)
    for x in bullets:                                  # no vote sentence is said to be missing from the other resolution
        for sentence in (FOUR_226, TWO_226, STANCE_226.removeprefix("7. "), ALL_227, NEUTRAL_227):
            if sentence in x.text:
                after = x.text[x.text.index(sentence) + len(sentence):]
                assert not after.lstrip('" ').startswith("The new MPC resolution doesn't have")
                assert not after.lstrip('" ').startswith("The previous MPC resolution doesn't have")
    # v29 read it by words, and set the four beside the new stance (what round 26's judge failed).
    old = _v29(seg, a, b)
    assert not any(_holds(x, a, _span(a, FOUR_226)) and _holds(x, b, _span(b, ALL_227)) for x in old)


def test_round_26s_growth_projection_for_q1_sits_beside_the_new_years():
    # 3770 said the new resolution doesn't have "Real GDP growth for Q1:2025-26 is projected at 6.9 per cent …", while
    # the new one projects Q1 and Q2 in its 2025-26 sentence.
    seg, a, b, facts = _pair(DEC_2024, FEB_2025)
    bullets = _v30(seg, a, b, facts)
    (gdp,) = [x for x in bullets if _holds(x, a, _span(a, GDP_Q1_226))]
    assert _holds(gdp, b, _span(b, GDP_FY_227)) and "doesn't have" not in gdp.text
    (m,) = [m for m in must_changes(seg, a, b, [], room=True, facts=facts) if m.fact == "gdp"]
    assert [p[0] for p in m.periods] == ["Q1 2025-26", "Q2 2025-26"]


def test_the_v30_list_names_each_vote_whole_and_the_check_wants_both_sides_in_one_bullet():
    seg, a, b, facts = _pair(DEC_2024, FEB_2025)
    must = must_changes(seg, a, b, [], room=True, facts=facts)
    assert [m.fact for m in must if m.fact] == ["rate vote", "stance vote", "gdp"]
    listed = must_text(must, a.text, b.text, each=True)
    line = next(x for x in listed.splitlines() if x.startswith("- The vote on the policy rate"))
    assert f'Previous: "{FOUR_226} {TWO_226}" New: "{ALL_227}"' in line
    assert next(x for x in listed.splitlines() if x.startswith("- The vote on the stance")).endswith(f'New: "{NEUTRAL_227}"')
    # A reply giving the two members' vote beside the new one, and the four in another bullet, is retried.
    split = _answer(
        (('Two members had voted to "reduce the policy repo rate by 25 basis points", and now "the MPC unanimously voted to '
         'reduce the policy repo rate by 25 basis points to 6.25 per cent".'),
         [("previous", TWO_226.rstrip(".")), ("new", ALL_227.rstrip("."))]),
        ('Four members had voted to "keep the policy repo rate unchanged at 6.50 per cent".', [("previous", FOUR_226.rstrip("."))]))
    c = _checked(seg, a, b, split, version="v30")
    problems = must_problems(c, must, a, b, room=True, facts=True)
    assert any(p.startswith("the vote on the policy rate must be reported in one bullet") for p in problems)
    assert not any(p.startswith("the vote on the policy rate") for p in must_problems(c, must, a, b, room=True))  # v29


def test_round_26s_inflation_projection_wants_the_same_periods_figures():
    # 3758's bullet 4 set the previous Q2 figure ("3.4 per cent for Q2:2019-20") beside the new H2 projection.
    seg, a, b, facts = _pair(OCT_2019, DEC_2019)
    must = must_changes(seg, a, b, [], room=True, facts=facts)
    (cpi,) = [m for m in must if m.fact == "cpi"]
    assert [p[0] for p in cpi.periods] == ["H2 2019-20"]
    listed = must_text(must, a.text, b.text, each=True)
    line = next(x for x in listed.splitlines() if x.startswith("- The CPI inflation projections"))
    assert "3.5-3.7 per cent for H2:2019-20" in line and "5.1-4.7 per cent for H2:2019-20" in line   # no 30-word cut
    assert "H2 2019-20 side by side" in line
    assert not any(m.old == (_span(a, RECAP_194.split(". The actual")[0].removeprefix("19. ") + "."),) for m in must)
    wrong = _answer(
        (('The CPI inflation projection moved from "revised slightly upwards to 3.4 per cent for Q2:2019-20" to "revised upwards '
         'to 5.1-4.7 per cent for H2:2019-20 and 4.0-3.8 per cent for H1:2020-21", with risks "broadly balanced".'),
         [("previous", "revised slightly upwards to 3.4 per cent for Q2:2019-20"),
          ("new", "revised upwards to 5.1-4.7 per cent for H2:2019-20 and 4.0-3.8 per cent for H1:2020-21"),
          ("new", "broadly balanced")]))
    problems = must_problems(_checked(seg, a, b, wrong, version="v30"), must, a, b, room=True, facts=True)
    assert any("H2 2019-20" in p and '"3.5-3.7 per cent for H2:2019-20"' in p for p in problems)
    right = _answer(
        (('The CPI inflation projection for H2:2019-20 moved from "3.5-3.7 per cent for H2:2019-20" to "5.1-4.7 per cent for '
         'H2:2019-20", with risks "evenly balanced" before and "broadly balanced" now.'),
         [("previous", "3.5-3.7 per cent for H2:2019-20"), ("new", "5.1-4.7 per cent for H2:2019-20"),
          ("previous", "evenly balanced"), ("new", "broadly balanced")]))
    assert not [p for p in must_problems(_checked(seg, a, b, right, version="v30"), must, a, b, room=True, facts=True)
                if "CPI" in p]


def test_round_26s_inflation_projection_in_the_v30_fallback():
    seg, a, b, facts = _pair(OCT_2019, DEC_2019)
    bullets = _v30(seg, a, b, facts)
    projection_194 = CPI_194[CPI_194.index("Taking into consideration"):]
    projection_195 = CPI_195[CPI_195.index("Taking into consideration"):]
    (x,) = [x for x in bullets if _holds(x, a, _span(a, projection_194))]
    assert _holds(x, b, _span(b, projection_195))


def test_round_22s_unanimity_on_one_side_is_one_change():
    seg, a, b, facts = _pair(VOTES_212, VOTES_213)
    bullets = _v30(seg, a, b, facts)
    unanimous = VOTES_212[2].removeprefix("9. ")
    (x,) = [x for x in bullets if _holds(x, a, _span(a, unanimous))]
    assert _holds(x, b, _span(b, FIVE_213)) and _holds(x, b, _span(b, GOYAL_213))


def test_june_2019s_two_votes_sit_beside_the_one_unanimous_decision():
    seg, a, b, facts = _pair(VOTES_191, VOTES_192)
    must = must_changes(seg, a, b, [], room=True, facts=facts)
    (votes,) = [m for m in must if m.fact]
    assert votes.fact == "votes" and len(votes.old) == 4 and len(votes.new) == 1
    bullets = _v30(seg, a, b, facts)
    assert not any("doesn't have" in x.text for x in bullets)


def test_august_2025s_unanimous_vote_sits_beside_junes_split():
    seg, a, b, facts = _pair(VOTES_229, VOTES_230)
    bullets = _v30(seg, a, b, facts)
    gupta = ("Dr. Nagesh Kumar, Prof. Ram Singh, Dr. Rajiv Ranjan, Dr. Poonam Gupta and Shri Sanjay Malhotra, voted to "
             "decrease the policy repo rate by 50 bps.")
    keep = "Accordingly, the MPC unanimously voted to keep the repo rate unchanged."
    (x,) = [x for x in bullets if _holds(x, a, _span(a, gupta))]
    assert _holds(x, a, _span(a, "Shri Saugata Bhattacharya voted for a 25 bps cut in repo rate.")) and _holds(x, b, _span(b, keep))


def test_without_facts_the_reading_is_v29s():
    seg, a, b, _facts = _pair(DEC_2024, FEB_2025)
    assert [x.text for x in fallback_bullets(seg, a, b, [], packed=True, restated=True, counterparts=True, v1=[], kinds=True,
                                             v1_pairs=True, v1_all=True, must=True, room=True, facts=None)] == \
        [x.text for x in _v29(seg, a, b)]
    assert must_changes(seg, a, b, [], room=True) == must_changes(seg, a, b, [], room=True, facts=None)


def test_a_v1_bullet_citing_a_vote_with_another_sentence_doesnt_set_them_together():
    # Round 26's cause on the v1 side: v1 (370) bullet 5 cites the four's vote and the new stance's paragraph together.
    seg, a, b, facts = _pair(DEC_2024, FEB_2025)
    v1 = [(a.document_id, *_span(a, FOUR_226), 4), (b.document_id, *_span(b, STANCE_227.removeprefix("8. ")), 4)]
    for x in _v30(seg, a, b, facts, v1):
        if _holds(x, a, _span(a, FOUR_226)):
            assert _holds(x, b, _span(b, ALL_227)) and x.text.index(ALL_227) < x.text.index(FOUR_226) + 1000


def test_work_reads_facts_from_v30_only():
    seg, a, b, facts = _pair(DEC_2024, FEB_2025)
    w = Work("statement_diff_summary", b, a, seg, v1=[], facts=facts)
    assert any(m.fact for m in w.musts(30)) and not any(m.fact for m in w.musts(29))
    assert w.musts(30) is w.musts(30)
    plain = Work("statement_diff_summary", b, a, seg, v1=[])
    assert not any(m.fact for m in plain.musts(30))                   # no facts (a Fed pair): v29's reading
    instruction = w.request(load_prompt("statement_diff_summary", "v30")).instruction
    assert "- The vote on the policy rate, in one bullet, previous beside new:" in instruction
    assert "- The vote on the policy rate" not in w.request(load_prompt("statement_diff_summary", "v29")).instruction


def test_the_task_passes_facts_from_v30_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False, **later):
        seen.append(sorted(later))
        return []

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task = ai_batch.AiBatch()
    tx = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchall=lambda: [(8, 0, 10, 2)]))
    for version, facts in (("v29", (Facts(), Facts())), ("v30", (Facts(), Facts())), ("v30", None)):
        w = SimpleNamespace(segments={}, base=None, doc=SimpleNamespace(document_id=8), task="statement_diff_summary",
                            facts=facts)
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(tx, w, call, 1, 1) is None
    assert seen == [["counterparts", "kinds", "must", "room", "v1", "v1_all", "v1_pairs"],
                    ["counterparts", "facts", "kinds", "must", "room", "v1", "v1_all", "v1_pairs"],
                    ["counterparts", "kinds", "must", "room", "v1", "v1_all", "v1_pairs"]]


# ---- mpc-2026-10-07 (documents 236 and 237; each bank's newest decision is in every sample): the decision is stated as a
# vote ("the MPC voted unanimously to …"), and the vote again later in the resolution
HEAD = "Monetary Policy Decisions"
DECIDE_236 = ("2. After a detailed assessment of the evolving macroeconomic and financial developments and the outlook, the MPC "
              "voted unanimously to keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.25 per "
              "cent. Consequently, the standing deposit facility (SDF) rate remains at 5.00 per cent and the marginal standing "
              "facility (MSF) rate and the Bank Rate remain at 5.50 per cent. The MPC also decided to continue with the neutral "
              "stance.")
DECIDE_237 = ("2. After a detailed assessment of the evolving macroeconomic and financial developments and the outlook, the MPC "
              "voted unanimously to increase the policy repo rate under the liquidity adjustment facility (LAF) by 25 bps to 5.50 "
              "per cent. Consequently, the standing deposit facility (SDF) rate stands adjusted at 5.25 per cent and the marginal "
              "standing facility (MSF) rate and the Bank Rate at 5.75 per cent. The MPC also decided to change the stance to "
              "calibrated tightening.")
KEPT_236 = ("13. Considering all these factors, the MPC voted to keep the policy rate unchanged. The MPC also decided to retain "
            "the neutral stance to respond appropriately to macroeconomic developments.")
RAISED_237 = ("14. Considering all these factors, the MPC unanimously voted to increase the policy repo rate by 25 basis points to "
              "5.50 per cent. The MPC also decided to change the stance to calibrated tightening.")
VIEW_237 = "15. Two members - Dr. Nagesh Kumar and Prof. Ram Singh - were of the view that the stance be retained at neutral."
FIRST_236 = DECIDE_236.removeprefix("2. ").split(" Consequently")[0]
FIRST_237 = DECIDE_237.removeprefix("2. ").split(" Consequently")[0]
VOTED_236 = "Considering all these factors, the MPC voted to keep the policy rate unchanged."
VOTED_237 = "Considering all these factors, the MPC unanimously voted to increase the policy repo rate by 25 basis points to 5.50 per cent."


def test_a_decision_stated_as_a_vote_stays_the_decision():
    seg, a, b, (fa, fb) = _pair([HEAD, DECIDE_236, KEPT_236], [HEAD, DECIDE_237, RAISED_237, VIEW_237])
    assert [a.text[s:e].removeprefix("2. ") for s, e in fa.decision] == [FIRST_236]
    assert [b.text[s:e].removeprefix("2. ") for s, e in fb.decision] == [FIRST_237]
    assert [a.text[s:e].removeprefix("13. ") for s, e in fa.rate_votes] == [VOTED_236]
    assert [b.text[s:e].removeprefix("14. ") for s, e in fb.rate_votes] == [VOTED_237]
    bullets = _v30(seg, a, b, (fa, fb))
    (decision,) = [x for x in bullets if _holds(x, a, _span(a, FIRST_236))]
    assert _holds(decision, b, _span(b, FIRST_237)) and not _holds(decision, b, _span(b, VOTED_237))
    (vote,) = [x for x in bullets if _holds(x, a, _span(a, VOTED_236))]
    assert _holds(vote, b, _span(b, VOTED_237)) and _holds(vote, b, _span(b, VIEW_237.removeprefix("15. ")))


def test_votes_with_no_other_side_join_a_decision_stated_as_a_vote():
    # The previous resolution's only vote is its decision's statement: the new one's votes are set against it, in the
    # decision's change, never as sentences the previous resolution "doesn't have".
    seg, a, b, facts = _pair([HEAD, DECIDE_236], [HEAD, DECIDE_237, RAISED_237])
    bullets = _v30(seg, a, b, facts)
    (decision,) = [x for x in bullets if _holds(x, a, _span(a, FIRST_236))]
    assert _holds(decision, b, _span(b, FIRST_237)) and _holds(decision, b, _span(b, VOTED_237))
    for x in bullets:
        if VOTED_237 in x.text:
            assert "The previous MPC resolution doesn't have" not in x.text[x.text.index(VOTED_237):][:200]
