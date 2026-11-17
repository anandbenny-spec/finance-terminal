"""#445, round 20 (Anand's decision of 15 Nov 2026, 12:10 IST, after round 19's dev run, whose likely failures were both the
fallback's): `statement_diff_summary` v23 is v22's text (v17's), marking the packed fallback (`summaries.DIFF_PACK_FROM`):
- a bullet carries up to two changes of one kind (the inflation and growth projections one kind, the corridor rates with the
  decision), and a second change moves to a bullet of its own while there are bullets to spare: mpc-2020-03-27's 3561 spent
  two of five bullets on restated projections and left out what v1 (346) had;
- the balance sheet's words rank before the stance's and come before the projections: fomc-2025-10-29's 3557 quoted "will
  continue reducing its holdings" alone, as removed, and left out "decided to conclude the reduction of its aggregate
  securities holdings", which v1 (27) has;
- the decision whose sentence gives the rate comes first; "will continue" is guidance only with a policy maker as its
  subject; the RBI's forward guidance is guidance; and no change quotes the same words as another.
Fallbacks of v22 or earlier keep their rules. No database and no model."""
from datetime import date
from types import SimpleNamespace

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import FALLBACK_KINDS, Doc, fallback_bullets
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION


def kind(text: str, packed: bool = True) -> str:
    return FALLBACK_KINDS[summaries._kind(text, packed=packed)]


def _texts(old: list[str], new: list[str], pairs: list[tuple[str, int | None, int | None]], label: str, bank: str):
    """Two documents of sentences and a redline of (kind, old sentence, new sentence) by index."""
    def spans(sentences: list[str]) -> list[tuple[int, int]]:
        out, at = [], 0
        for s in sentences:
            out.append((at, at + len(s)))
            at += len(s) + 1
        return out

    so, sn = spans(old), spans(new)
    segments = {"pairs": [{"kind": k, **({"old": [list(so[o])]} if o is not None else {}),
                           **({"new": [list(sn[n])]} if n is not None else {})} for k, o, n in pairs]}
    a = Doc(1, "x", label, "a", bank, date(2025, 9, 17), " ".join(old))
    b = Doc(2, "x", label, "b", bank, date(2025, 10, 29), " ".join(new))
    return segments, a, b


def test_v23_is_v22s_text_with_v22s_schema_on_sonnet_and_is_current():
    v22, v23 = load_prompt("statement_diff_summary", "v22"), load_prompt("statement_diff_summary", "v23")
    assert v23.text == v22.text and v23.version == "v23"
    assert load_schema("statement_diff_summary", "v23") == load_schema("statement_diff_summary", "v22")
    assert PROMPT_VERSION["statement_diff_summary"] == "v29" and summaries.DIFF_PACK_FROM == 23   # v29: round 26
    assert summaries.DIFF_MODEL_OF["v23"] == summaries.DIFF_MODEL_OF["v22"]
    assert summaries.DIFF_EFFORT_OF["v23"] == summaries.DIFF_EFFORT_OF["v22"]
    assert summaries.FALLBACK_PER_BULLET == 2


# ---------------------------------------------------------------------------------------------- the kinds
def test_the_packed_kinds():
    holdings = ("The Committee will continue reducing its holdings of Treasury securities and agency debt and agency "
                "mortgage-backed securities.")
    assert kind(holdings) == "balance sheet" and kind(holdings, packed=False) == "stance"     # v22 read it as guidance
    assert kind("The Committee decided to conclude the reduction of its aggregate securities holdings on December 1.") \
        == "balance sheet"
    # The RBI's forward guidance (mpc-2026-10-07; v1 58 has it, v22 read it as the economy for "the outlook").
    guidance = ("It only signals that given the current conditions, rate cuts are off the table in the near term and policy "
                "action ahead can only be a rate hike or a pause, depending on the evolving conditions and the outlook.")
    assert kind(guidance) == "stance" and kind(guidance, packed=False) == "economy"
    # "will continue" with a policy maker as its subject only; "future" a word of its own.
    assert kind("6. Looking ahead, global economic uncertainty will continue to have some bearing on domestic economic "
                "activity.") == "economy"
    assert kind("The Committee will continue to monitor the implications of incoming information for the economic "
                "outlook.") == "stance"
    assert kind("The crude oil futures curve has become downward sloping since December 2020.") != "stance"
    # The central bank's balance sheet, not banks' and corporates'.
    assert kind("Healthy balance sheets of banks and corporates are expected to support investment activity.") \
        != "balance sheet"
    assert kind("Plans for Reducing the Size of the Federal Reserve's Balance Sheet were issued.") == "balance sheet"
    # What v22 read stays as it was.
    assert kind("The crude oil futures curve has become downward sloping since December 2020.", packed=False) == "stance"


# ---------------------------------------------------------------------------------------------- fomc-2025-10-29's shape
FED_OLD = [
    "Recent indicators suggest that growth of economic activity moderated in the first half of the year.",
    "Job gains have slowed, and the unemployment rate has edged up but remains low.",
    "Inflation has moved up and remains somewhat elevated.",
    ("In support of its goals, the Committee decided to lower the target range for the federal funds rate by 1/4 percentage "
     "point to 4 to 4-1/4 percent."),
    ("The Committee will continue reducing its holdings of Treasury securities and agency debt and agency mortgage-backed "
     "securities."),
    ("Voting against this action was Stephen I. Miran, who preferred to lower the target range for the federal funds rate by "
     "1/2 percentage point at this meeting."),
]
FED_NEW = [
    "Available indicators suggest that economic activity has been expanding at a moderate pace.",
    "Job gains have slowed this year, and the unemployment rate has edged up but remained low through August.",
    "Inflation has moved up since earlier in the year and remains somewhat elevated.",
    ("In support of its goals, the Committee decided to lower the target range for the federal funds rate by 1/4 percentage "
     "point to 3-3/4 to 4 percent."),
    "The Committee decided to conclude the reduction of its aggregate securities holdings on December 1.",
    ("Voting against this action were Stephen I. Miran, who preferred to lower the target range for the federal funds rate by "
     "1/2 percentage point at this meeting, and Jeffrey R. Schmid, who preferred no change to the target range for the federal "
     "funds rate at this meeting."),
]
FED_PAIRS = [("changed", 0, 0), ("changed", 1, 1), ("changed", 2, 2), ("changed", 3, 3), ("removed", 4, None),
             ("added", None, 4), ("changed", 5, 5)]


def _fed():
    return _texts(FED_OLD, FED_NEW, FED_PAIRS, "FOMC statement", "Fed")


def test_v22s_fallback_left_out_the_end_of_the_reduction_in_holdings():
    # As 3557 ran: the old holdings sentence, read as guidance, shown alone as removed; the new one left out.
    texts = [b.text for b in fallback_bullets(*_fed(), [])]
    assert len(texts) == 5 and not any("conclude the reduction" in t for t in texts)
    assert any("will continue reducing its holdings" in t and "doesn't have this sentence" in t for t in texts)
    assert not any("Also," in t for t in texts)


def test_the_packed_fallback_pairs_the_holdings_and_carries_both_economy_changes_in_one_bullet():
    segments, a, b = _fed()
    bullets = fallback_bullets(segments, a, b, [], packed=True)
    texts = [x.text for x in bullets]
    assert len(bullets) == 5
    assert "3-3/4 to 4 percent" in texts[0] and "Jeffrey R. Schmid" in texts[1]       # the decision, then the dissent
    holdings = [t for t in texts if "conclude the reduction" in t]
    assert len(holdings) == 1 and holdings[0].startswith('The previous FOMC statement said: "The Committee will continue '
                                                         'reducing its holdings')
    assert '" The new FOMC statement says: "The Committee decided to conclude the reduction' in holdings[0]
    assert texts.index(holdings[0]) == 2                                               # the balance sheet before the rest
    economy = [t for t in texts if "economic activity" in t]
    assert len(economy) == 1 and "Job gains have slowed this year" in economy[0]
    assert '" Also, the previous FOMC statement said: "Job gains have slowed, and' in economy[0]
    # Every sentence of the seven changes is quoted, and each bullet cites exactly the sentences it quotes.
    for bullet in bullets:
        for c in bullet.citations:
            words = (a if c.document_id == a.document_id else b).text[c.start:c.end]
            assert f'"{words}"' in bullet.text
    assert sum(len(x.citations) for x in bullets) == 12


def test_with_bullets_to_spare_each_change_keeps_a_bullet_of_its_own():
    pairs = [("changed", 0, 0), ("changed", 1, 1), ("changed", 2, 2)]
    segments, a, b = _texts(FED_OLD[:3], FED_NEW[:3], pairs, "FOMC statement", "Fed")
    texts = [x.text for x in fallback_bullets(segments, a, b, [], packed=True)]
    assert len(texts) == 3 and not any("Also," in t for t in texts)
    assert texts == [x.text for x in fallback_bullets(segments, a, b, [])]


# ---------------------------------------------------------------------------------------------- the RBI's shapes
RBI_OLD = [
    "keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.15 per cent.",
    ("Consequently, the reverse repo rate under the LAF remains unchanged at 4.90 per cent and the marginal standing facility "
     "(MSF) rate and the Bank Rate at 5.40 per cent."),
    "The MPC also decided to continue with the accommodative stance as long as it is necessary to revive growth.",
    "CPI inflation is projected at 6.5 per cent for Q4:2019-20.",
    "Real GDP growth for 2020-21 is projected at 6.0 per cent.",
    "Retail inflation increased to 7.6 per cent in January.",
    "Global economic activity was slow-paced.",
    "All members voted in favour of the decision.",
]
RBI_NEW = [
    "11. All members voted for a reduction in the policy repo rate and for maintaining the accommodative stance.",
    ("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 75 basis points to 4.40 per cent from 5.15 "
     "per cent with immediate effect;"),
    ("accordingly, the marginal standing facility (MSF) rate and the Bank Rate stand reduced to 4.65 per cent from 5.40 per "
     "cent;"),
    ("The MPC also decided to continue with the accommodative stance as long as it is necessary to revive growth and mitigate "
     "the impact of coronavirus (COVID-19) on the economy."),
    "CPI inflation is projected at 4.8 per cent for H1:2020-21.",
    "Real GDP growth for 2020-21 is projected at 5.5 per cent.",
    "Retail inflation eased to 6.6 per cent in February.",
    "Global economic activity has come to a near standstill.",
    "Dr. Chetan Ghate and Dr. Pami Dua voted for a 50 bps reduction in the policy repo rate.",
]
RBI_PAIRS = [("added", None, 0), ("changed", 0, 1), ("changed", 1, 2), ("changed", 2, 3), ("changed", 3, 4),
             ("changed", 4, 5), ("changed", 5, 6), ("changed", 6, 7), ("removed", 7, None), ("added", None, 8)]


def _rbi():
    return _texts(RBI_OLD, RBI_NEW, RBI_PAIRS, "MPC resolution", "RBI")


def test_the_corridor_rates_join_the_decision_and_the_projections_share_a_bullet():
    texts = [x.text for x in fallback_bullets(*_rbi(), [], packed=True)]
    assert len(texts) == 5
    assert "by 75 basis points to 4.40 per cent" in texts[0] and "Also, the previous MPC resolution said: \"Consequently, " \
        "the reverse repo rate" in texts[0]                                            # the decision, with its corridor
    projections = [t for t in texts if "CPI inflation is projected" in t]
    assert len(projections) == 1 and "Real GDP growth for 2020-21 is projected at 5.5 per cent." in projections[0]
    assert any("Dr. Chetan Ghate and Dr. Pami Dua voted for a 50 bps reduction" in t for t in texts)
    assert any("Retail inflation eased" in t for t in texts)


def test_the_decision_whose_sentence_gives_the_rate_comes_first():
    # mpc-2020-03-27: the committee's vote "for a reduction" comes first in the resolution, but names no rate.
    packed = fallback_bullets(*_rbi(), [], packed=True)
    assert "4.40 per cent" in packed[0].text and "All members voted for a reduction" not in packed[0].text
    assert not any("All members voted for a reduction" in x.text for x in packed)       # the decision once
    as_v22 = fallback_bullets(*_rbi(), [])
    assert "All members voted for a reduction" in as_v22[0].text                        # v22: by position


def test_the_same_words_are_never_quoted_in_two_changes():
    # mpc-2026-10-07: the resolution states its stance in two paragraphs, and the redline pairs each with the new one.
    old = ["The MPC also decided to continue with the neutral stance.",
           "Growth held up.",
           "The MPC also decided to retain the neutral stance to respond appropriately to macroeconomic developments."]
    new = ["The MPC also decided to change the stance to calibrated tightening.",
           "Growth held up.",
           "The MPC also decided to change the stance to calibrated tightening."]
    segments, a, b = _texts(old, new, [("changed", 0, 0), ("changed", 2, 2)], "MPC resolution", "RBI")
    bullets = fallback_bullets(segments, a, b, [], packed=True)
    assert len(bullets) == 1 and bullets[0].text.count("calibrated tightening") == 1


def test_the_fallback_is_packed_from_v23_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False):
        seen.append((packed, restated))
        return []

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task, w = ai_batch.AiBatch(), SimpleNamespace(segments={}, base=None, doc=None)
    for version in ("v17", "v22", "v23", "v24"):
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(None, w, call, 1, 1) is None                       # nothing built: nothing stored
    assert seen == [(False, False), (False, False), (True, False), (True, True)]      # v24: round 21 (#445)
