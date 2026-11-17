"""#445, round 24 (Anand's decision of 16 Nov 2026, 22:50 IST: rounds 23 and 24 open; round 23's verdict of 17 Nov 2026,
09:30 IST failed mpc-2024-08-08 and mpc-2022-02-10 on rule (c)): `statement_diff_summary` v27 is v26's text, marking one
revision of the fallback built by code (`summaries.DIFF_V1_ALL_FROM`, `fallback_bullets`' `v1_all`): every change the target's
v1 output cites is shown, past the packing's limits of kind and count, and a sentence one document alone has sits beside the
other document's side that the same v1 bullet compares it with. The sentences below are cut down from those two meetings'
resolutions. Fallbacks of v26 keep v26's rules. No database and no model, but for the task test's fake transaction."""
from types import SimpleNamespace

from test_diff_summary_v25 import _docs

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import fallback_bullets
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION


def _run(old, new, v1=(), v1_all=False, hi=5):
    seg, a, b = _docs(old, new)
    v1 = [(d.document_id, d.text.index(s), d.text.index(s) + len(s), k)
          for side, s, k in v1 for d in (a if side == "old" else b,)]
    return [x.text for x in fallback_bullets(seg, a, b, [], hi, packed=True, restated=True, counterparts=True, v1=v1,
                                             kinds=True, v1_pairs=True, v1_all=v1_all)]


# mpc-2024-08-08 (round 23, failure 1): v1 (576) cites ten changes, four of them about inflation, and the core inflation reading
# was left out for want of room.
GROWTH_OLD = ("Taking all these factors into consideration, real GDP growth for 2024-25 is projected at 7.2 per cent with Q1 at "
              "7.3 per cent; Q2 at 7.2 per cent; Q3 at 7.3 per cent; and Q4 at 7.2 per cent (Chart 1).")
GROWTH_NEW = ("Taking all these factors into consideration, real GDP growth for 2024-25 is projected at 7.2 per cent with Q1 at "
              "7.1 per cent; Q2 at 7.2 per cent; Q3 at 7.3 per cent; and Q4 at 7.2 per cent.")
GROWTH_Q1 = "Real GDP growth for Q1:2025-26 is projected at 7.2 per cent (Chart 1)."
HEADLINE_OLD = ("Headline inflation has seen sequential moderation since February 2024, albeit in a narrow range from 5.1 per "
                "cent in February to 4.8 per cent in April 2024.")
HEADLINE_NEW = "Headline inflation increased to 5.1 per cent in June 2024 after remaining steady at 4.8 per cent during April-May 2024."
FOOD_OLD = "Food inflation, however, remains elevated due to persistence of inflation pressures in vegetables, pulses, cereals, and spices."
FOOD_NEW = ("Worsening of food inflation pressures – driven primarily by a sharp increase in prices of vegetables, pulses and "
            "edible oils – pushed up headline inflation.")
CORE_OLD = ("Core (CPI excluding food and fuel) inflation eased further to 3.2 per cent in April, the lowest in the current CPI "
            "series, with core services inflation also falling to historic lows.")
CORE_NEW = ("Core (CPI excluding food and fuel) inflation at 3.1 per cent in May-June touched a new low in the current CPI "
            "series, with core services inflation also at its lowest in the series.")
CPI_OLD = ("Taking into account these factors, CPI inflation for 2024-25 is projected at 4.5 per cent with Q1 at 4.9 per cent; "
           "Q2 at 3.8 per cent; Q3 at 4.6 per cent; and Q4 at 4.5 per cent (Chart 2).")
CPI_NEW = ("Assuming a normal monsoon, CPI inflation for 2024-25 is projected at 4.5 per cent with Q2 at 4.4 per cent; Q3 at "
           "4.7 per cent; and Q4 at 4.3 per cent.")
EXPECTS = ("The MPC expects domestic growth to hold up on the strength of investment demand, steady urban consumption and rising "
           "rural consumption. There are also indications of core inflation bottoming out.")
DECIDED_OLD = "Accordingly, the MPC decided to keep the policy repo rate unchanged at 6.50 per cent in this meeting."
DECIDED_NEW = "In these circumstances, the MPC decided to keep the policy repo rate unchanged at 6.50 per cent in this meeting."
STANCE_OLD = ("Hence, the MPC also decided to remain focused on withdrawal of accommodation to ensure that inflation progressively "
              "aligns to the target, while supporting growth.")
STANCE_NEW = ("Hence the MPC also considers it appropriate to continue with the disinflationary stance of withdrawal of "
              "accommodation to ensure that inflation progressively aligns to the target, while supporting growth.")
IMD = ("Going forward, the Indian Meteorological Department's (IMD) projection of above normal southwest monsoon and healthy "
       "kharif sowing will support improving rural demand.")
ROOM_OLD = ["Assessment and Outlook", f"4. {GROWTH_OLD} The risks are evenly balanced.",
            f"5. {HEADLINE_OLD} {FOOD_OLD} {CORE_OLD}", f"6. {CPI_OLD} The risks are evenly balanced.",
            f"7. {DECIDED_OLD} {STANCE_OLD}", "10. The minutes of the MPC's meeting will be published on June 21, 2024."]
ROOM_NEW = ["Assessment and Outlook", f"4. {IMD} {GROWTH_NEW} {GROWTH_Q1} The risks are evenly balanced.",
            f"5. {HEADLINE_NEW} {FOOD_NEW} {CORE_NEW}", f"6. {CPI_NEW} The risks are evenly balanced.",
            f"7. {EXPECTS} {DECIDED_NEW} {STANCE_NEW}",
            "10. The minutes of the MPC's meeting will be published on August 22, 2024."]
ROOM_V1 = [("old", GROWTH_OLD, 0), ("new", GROWTH_NEW, 0), ("new", GROWTH_Q1, 0), ("new", HEADLINE_NEW, 1),
           ("new", FOOD_NEW, 1), ("old", HEADLINE_OLD, 1), ("old", CORE_OLD, 2), ("new", CORE_NEW, 2),
           ("new", "There are also indications of core inflation bottoming out.", 2), ("new", CPI_NEW, 3), ("old", CPI_OLD, 3),
           ("new", DECIDED_NEW, 4), ("old", STANCE_OLD, 4), ("new", STANCE_NEW, 4)]

# mpc-2022-02-10 (round 23, failure 2): v1 (301) bullet 4 gives the year's growth from the previous "retained at 9.5 per cent"
# to the new first advance estimate of 9.2 per cent; v26 quoted the first and left out the second.
NSO_OLD = ("On the domestic front, data released by the National Statistical Office (NSO) on November 30, 2021 showed that real "
           "gross domestic product (GDP) expanded by 8.4 per cent year-on-year (y-o-y) in Q2:2021-22, following a growth of "
           "20.1 per cent during Q1:2021-22.")
FAE = ("The first advance estimates (FAE) of national income released by the National Statistical Office (NSO) on January 7, "
       "2022 placed India's real gross domestic product (GDP) growth at 9.2 per cent for 2021-22, surpassing its pre-pandemic "
       "(2019-20) level.")
RETAINED = ("Taking all these factors into consideration and assuming no resurgence in COVID-19 infections in India, the "
            "projection for real GDP growth is retained at 9.5 per cent in 2021-22 consisting of 6.6 per cent in Q3; and 6.0 "
            "per cent in Q4:2021-22.")
NEXT_OLD = "Real GDP growth is projected at 17.2 per cent for Q1:2022-23 and at 7.8 per cent for Q2 (Chart 2)."
NEXT_NEW = ("Taking all these factors into consideration, the real GDP growth for 2022-23 is projected at 7.8 per cent with "
            "Q1:2022-23 at 17.2 per cent; Q2 at 7.0 per cent; Q3 at 4.3 per cent; and Q4:2022-23 at 4.5 per cent (Chart 2).")
SAME = "Rural demand is expected to remain resilient."
FAE_OLD = ["Domestic Economy", f"4. {NSO_OLD}", "Outlook", f"10. {RETAINED} {SAME} {NEXT_OLD}",
           "14. The minutes of the MPC's meeting will be published on December 22, 2021."]
FAE_NEW = ["Domestic Economy", f"4. {FAE}", "Outlook", f"10. {SAME} {NEXT_NEW}",
           "14. The minutes of the MPC's meeting will be published on February 24, 2022."]
FAE_V1 = [("new", FAE, 0), ("old", RETAINED, 0), ("new", NEXT_NEW, 0)]


def test_v27_is_v26s_text_with_v26s_schema_on_sonnet_and_is_current():
    v26, v27 = load_prompt("statement_diff_summary", "v26"), load_prompt("statement_diff_summary", "v27")
    assert v27.text == v26.text and v27.version == "v27"
    assert load_schema("statement_diff_summary", "v27") == load_schema("statement_diff_summary", "v26")
    assert PROMPT_VERSION["statement_diff_summary"] == "v27" and summaries.DIFF_V1_ALL_FROM == 27
    assert summaries.DIFF_MODEL_OF["v27"] == summaries.DIFF_MODEL_OF["v26"]
    assert summaries.DIFF_EFFORT_OF["v27"] == summaries.DIFF_EFFORT_OF["v26"]


def test_every_change_v1_cites_is_shown_past_the_packings_limits():
    v26 = _run(ROOM_OLD, ROOM_NEW, ROOM_V1)
    assert CORE_NEW not in " ".join(v26)                # round 23's mpc-2024-08-08: no room left for it
    v27 = _run(ROOM_OLD, ROOM_NEW, ROOM_V1, v1_all=True)
    assert len(v27) == 5
    shown = " ".join(v27)
    for _, sentence, _ in ROOM_V1:
        assert sentence in shown, sentence
    assert IMD not in shown                             # a change v1 doesn't cite takes only the room left
    assert all(t.count("The previous MPC resolution") + t.count("the previous MPC resolution")
               <= summaries.FALLBACK_V1_PER_BULLET for t in v27)


def test_a_sentence_one_document_alone_has_sits_beside_what_v1_compares_it_with():
    v26 = _run(FAE_OLD, FAE_NEW, FAE_V1)
    assert not any(RETAINED in t and FAE in t for t in v26)
    v27 = _run(FAE_OLD, FAE_NEW, FAE_V1, v1_all=True)
    both = [t for t in v27 if RETAINED in t]
    assert len(both) == 1 and FAE in both[0] and NEXT_NEW in both[0]          # not split off though bullets are spare
    assert f'"{RETAINED}" The new MPC resolution doesn\'t have this sentence.' in both[0]
    assert sum(FAE in t for t in v27) == 1


def test_without_a_v1_output_v27_is_v26():
    for old, new in ((ROOM_OLD, ROOM_NEW), (FAE_OLD, FAE_NEW)):
        assert _run(old, new, v1_all=True) == _run(old, new)


def test_the_task_passes_v1_all_from_v27_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False, **later):
        seen.append(later)
        return []

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task = ai_batch.AiBatch()
    w = SimpleNamespace(segments={}, base=None, doc=SimpleNamespace(document_id=8), task="statement_diff_summary")
    tx = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchall=lambda: [(8, 0, 10, 2)]))
    for version in ("v26", "v27"):
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(tx, w, call, 1, 1) is None
    assert [sorted(x) for x in seen] == [["counterparts", "kinds", "v1", "v1_pairs"],
                                         ["counterparts", "kinds", "v1", "v1_all", "v1_pairs"]]
    assert [x.get("v1") for x in seen] == [[(8, 0, 10, 2)], [(8, 0, 10, 2)]]
