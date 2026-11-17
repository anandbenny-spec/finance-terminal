"""#445, round 22 (Anand's decision of 16 Nov 2026, 22:12 IST, after round 21's verdict failed four RBI fallbacks on rule (b)):
`statement_diff_summary` v25 is v24's text (v17's), marking a revision of the packed fallback (`summaries.DIFF_COUNTERPART_FROM`,
`fallback_bullets`' `counterparts`): a removed and an added sentence are one change only as each other's counterparts (never
across sections, an opening only with an opening, the best-matched first, never different financial years or economies); a
sentence split in two is shown with its counterpart; the changes the v1 output cites rank first and a change that changes no
meaning last; RBI paragraph numbers are left out of quotations and a list item is labelled as one. The texts are shaped on
the round 21 verdict's failures (mpc-2024-06-07, mpc-2023-06-08, mpc-2026-10-07) and close calls (mpc-2025-04-09). Fallbacks of
v24 keep their rules. No database and no model, but for the task test's fake transaction."""
from datetime import date
from types import SimpleNamespace

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import Doc, fallback_bullets
from ft.engines.document.diff import diff_texts
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION


def _docs(old: list[str], new: list[str]):
    a, b = "\n\n".join(old), "\n\n".join(new)
    return (diff_texts(a, b).segments, Doc(1, "mpc_resolution", "MPC resolution", "a", "RBI", date(2024, 4, 5), a),
            Doc(2, "mpc_resolution", "MPC resolution", "b", "RBI", date(2024, 6, 7), b))


def _v25(segments, old, new, v1=None, hi=5):
    return fallback_bullets(segments, old, new, [], hi, packed=True, restated=True, counterparts=True, v1=v1 or [])


def _v24(segments, old, new):
    return fallback_bullets(segments, old, new, [], packed=True, restated=True)


DECISION_OLD = "The MPC decided to keep the policy repo rate unchanged at 6.50 per cent."
DECISION_NEW = "The MPC decided to keep the policy repo rate unchanged at 6.50 per cent in this meeting."

# mpc-2024-06-07 (round 21, failure 1): the old paragraph 5 headline sentence was paired with paragraph 2's global one.
SECTIONS_OLD = ["Monetary Policy Decisions", DECISION_OLD, "Global Outlook",
                ("2. The global economy exhibits resilience and is likely to maintain its steady growth in 2024. Inflation is "
                 "treading down, supported by favourable base effects though stubborn services prices are keeping it "
                 "elevated relative to targets. Gold prices have surged on safe haven demand."),
                "Domestic Outlook",
                ("5. Headline inflation softened to 5.1 per cent during January-February 2024, from 5.7 per cent in December. "
                 "Fuel prices remained in deflation for the sixth consecutive month in February. Core inflation eased to "
                 "3.4 per cent in February."),
                "The risks are evenly balanced."]
SECTIONS_NEW = ["Monetary Policy Decisions", DECISION_NEW, "Global Outlook",
                ("2. Global economic activity is rebalancing and is expected to grow at a stable pace in 2024. Inflation has "
                 "been moderating unevenly, with services inflation staying elevated and slowing progress towards targets. "
                 "Gold prices have surged to record highs on safe haven demand."),
                "Domestic Outlook",
                ("5. Headline inflation has seen sequential moderation since February 2024, albeit in a narrow range from 5.1 "
                 "per cent in February to 4.8 per cent in April 2024. Deflation in fuel prices deepened during March-April, "
                 "reflecting the cut in liquified petroleum gas (LPG) prices. Core inflation eased further to 3.2 per cent "
                 "in April."),
                "The risks are evenly balanced."]


def test_v25_is_v24s_text_with_v24s_schema_on_sonnet_and_is_current():
    v24, v25 = load_prompt("statement_diff_summary", "v24"), load_prompt("statement_diff_summary", "v25")
    assert v25.text == v24.text and v25.version == "v25"
    assert load_schema("statement_diff_summary", "v25") == load_schema("statement_diff_summary", "v24")
    assert PROMPT_VERSION["statement_diff_summary"] == "v27" and summaries.DIFF_COUNTERPART_FROM == 25   # v27: round 24
    assert summaries.DIFF_MODEL_OF["v25"] == summaries.DIFF_MODEL_OF["v24"]
    assert summaries.DIFF_EFFORT_OF["v25"] == summaries.DIFF_EFFORT_OF["v24"]


def _pairs(bullets) -> list[tuple[str, str]]:
    """Each change of the bullets as (previous quotation, new quotation), '' for a side it lacks."""
    out = []
    for b in bullets:
        for part in b.text.split(" Also, "):
            old = part.split('said: "', 1)[1].split('"', 1)[0] if 'said: "' in part else ""
            if not old and "listed among its decisions: \"" in part and part.lower().startswith("the previous"):
                old = part.split('decisions: "', 1)[1].split('"', 1)[0]
            new = part.split('says: "', 1)[1].split('"', 1)[0] if 'says: "' in part else ""
            out.append((old, new))
    return out


def test_a_pair_never_crosses_sections_and_the_headline_sentence_keeps_its_counterpart():
    segments, old, new = _docs(SECTIONS_OLD, SECTIONS_NEW)
    pairs = _pairs(_v25(segments, old, new, hi=10))
    headline = [p for p in pairs if p[0].startswith("Headline inflation softened")]
    assert headline == [(("Headline inflation softened to 5.1 per cent during January-February 2024, from 5.7 per cent in "
                         "December."), headline[0][1])]
    assert headline[0][1].startswith("Headline inflation has seen sequential moderation") and "4.8 per cent" in headline[0][1]
    for o, n in pairs:                                 # the global sentences pair only with the global sentences
        assert not (("treading down" in o) and ("Headline" in n or "Deflation in fuel" in n))
        assert not (("Headline" in o or "Fuel prices" in o) and "moderating unevenly" in n)


def test_v24_ran_as_round_21_did_on_the_same_texts():
    segments, old, new = _docs(SECTIONS_OLD, SECTIONS_NEW)
    v24 = " ".join(b.text for b in _v24(segments, old, new))
    v25 = " ".join(b.text for b in _v25(segments, old, new, hi=10))
    assert v24 != v25                                  # the flag changes the fallback; v24 keeps its rules
    assert '"5. Headline inflation' in v24 and '"5. Headline inflation' not in v25    # paragraph numbers: v25 drops them


# mpc-2023-06-08 (round 21, failure 6): the opening and fuel sentences were crossed.
CROSS_OLD = ["Growth and Inflation Outlook",
             ("6. CPI headline inflation rose from 5.7 per cent in December 2022 to 6.4 per cent in February 2023 on the back "
              "of higher inflation in cereals, milk and fruits and slower deflation in vegetables prices. Fuel inflation "
              "remained elevated, though some softening was witnessed in February due to a fall in kerosene (PDS) prices "
              "and favourable base effects."),
             "7. India's foreign exchange reserves were placed at US$ 578.4 billion as on March 31, 2023."]
CROSS_NEW = ["Growth and Inflation Outlook",
             ("6. CPI inflation fell sharply to 4.7 per cent in April 2023 from 6.4 per cent in February on the back of large "
              "favourable base effects, with softening observed across all the three major groups. Food group inflation "
              "eased, with moderation in cereals, eggs, milk, fruits, meat and fish, spices and prepared meals inflation and "
              "deepening of deflation in edible oils. In the fuel group, inflation in LPG and firewood and chips prices fell "
              "and kerosene prices slipped into deflation."),
             "7. India's foreign exchange reserves were placed at US$ 595.1 billion as on June 2, 2023."]


def test_an_opening_pairs_with_an_opening_and_fuel_with_fuel():
    pairs = _pairs(_v25(*_docs(CROSS_OLD, CROSS_NEW), hi=10))
    opening = [n for o, n in pairs if o.startswith("CPI headline inflation rose")]
    fuel = [n for o, n in pairs if o.startswith("Fuel inflation remained elevated")]
    assert len(opening) == 1 and opening[0].startswith("CPI inflation fell sharply")
    assert fuel == [("In the fuel group, inflation in LPG and firewood and chips prices fell and kerosene prices slipped into "
                    "deflation.")]
    assert ("", ("Food group inflation eased, with moderation in cereals, eggs, milk, fruits, meat and fish, spices and "
                "prepared meals inflation and deepening of deflation in edible oils.")) in pairs


# mpc-2023-06-08 (round 21, close call 3): one sentence became two, and the second was shown as one the previous lacked.
SPLIT_OLD = ["Rationale", DECISION_OLD + " The MPC will continue to keep a strong vigil on the evolving inflation and growth "
             "outlook and will not hesitate to take further action as may be required in its future meetings.",
             "The minutes of the MPC's meeting will be published on April 20, 2023."]
SPLIT_NEW = ["Rationale", DECISION_NEW + " The MPC resolved to continue keeping a close vigil on the evolving inflation and "
             "growth outlook. It will take further monetary actions promptly and appropriately as required to keep inflation "
             "expectations firmly anchored and to bring down inflation to the target.",
             "The minutes of the MPC's meeting will be published on June 22, 2023."]


def test_a_sentence_split_in_two_is_shown_with_its_counterpart():
    texts = [b.text for b in _v25(*_docs(SPLIT_OLD, SPLIT_NEW))]
    vigil = [t for t in texts if "strong vigil" in t]
    assert len(vigil) == 1 and "close vigil" in vigil[0] and "It will take further monetary actions" in vigil[0]
    assert "doesn't have" not in vigil[0]


def test_the_changes_v1_cites_rank_first_and_a_change_of_no_meaning_last():
    old = ["Decisions", DECISION_OLD, "These decisions are in consonance with the objective of achieving the target.",
           "Outlook"] + [f"{k}. CPI inflation for Q{k}:2024-25 is projected at {4 + k / 10:.1f} per cent." for k in range(1, 5)] \
        + [f"Real GDP growth for Q{k}:2024-25 is projected at {7 + k / 10:.1f} per cent." for k in range(1, 5)] \
        + ["Exports of software services rose by 11 per cent in the quarter."]
    new = ["Decisions", DECISION_NEW, "This decision is in consonance with the objective of achieving the target.",
           "Outlook"] + [f"{k}. CPI inflation for Q{k}:2024-25 is projected at {5 + k / 10:.1f} per cent." for k in range(1, 5)] \
        + [f"Real GDP growth for Q{k}:2024-25 is projected at {6 + k / 10:.1f} per cent." for k in range(1, 5)] \
        + ["Exports of software services rose by 14 per cent in the quarter."]
    segments, a, b = _docs(old, new)
    plain = " ".join(x.text for x in _v25(segments, a, b, hi=3))
    s = b.text.index("Exports of software")
    cited = " ".join(x.text for x in _v25(segments, a, b, v1=[(2, s, s + 20)], hi=3))
    assert "Exports of software" not in plain and "Exports of software" in cited
    assert "This decision is" not in plain                            # minor: last
    assert summaries._minor("These decisions are in consonance with the objective.",
                            "This decision is in consonance with the objective.")
    assert not summaries._minor("continue with the neutral stance", "continue with the accommodative stance")
    assert not summaries._minor("Dr. Goyal voted to keep the rate.", "Dr. Varma voted to keep the rate.")


def test_a_paragraph_number_is_left_out_of_the_quotation_and_its_citation():
    old = ["Votes", "14. All members voted to keep the policy repo rate unchanged.", "Minutes"]
    new = ["Votes", "14. All members voted to keep the policy repo rate unchanged.",
           ("15. Two members - Dr. Nagesh Kumar and Prof. Ram Singh - were of the view that the stance be retained at "
            "neutral."), "Minutes"]
    segments, a, b = _docs(old, new)
    (bullet,) = _v25(segments, a, b)
    assert bullet.text.startswith('The new MPC resolution says: "Two members - Dr. Nagesh Kumar')
    (c,) = bullet.citations
    assert b.text[c.start:c.end].startswith("Two members") and b.text[c.start - 4:c.start] == "15. "


def test_a_list_item_is_labelled_and_a_lead_in_joins_its_first_item():
    old = ["Monetary Policy Decisions",
           "After assessing the current and evolving macroeconomic situation, the MPC unanimously decided to:",
           ("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points to 6.25 per cent "
            "with immediate effect;"),
           ("continue with the neutral monetary policy stance and remain unambiguously focussed on a durable alignment of "
            "inflation with the target, while supporting growth."),
           "Growth and Inflation Outlook"]
    new = ["Monetary Policy Decisions",
           ("2. After assessing the current and evolving macroeconomic situation, the MPC unanimously voted to reduce the "
            "policy repo rate by 25 basis points to 6.00 per cent with immediate effect."),
           "Growth and Inflation Outlook"]
    texts = [x.text for x in _v25(*_docs(old, new))]
    decision = [t for t in texts if "6.00 per cent" in t]
    assert len(decision) == 1 and 'decided to: reduce the policy repo rate' in decision[0] and "6.25 per cent" in decision[0]
    item = [t for t in texts if "continue with the neutral" in t]
    assert len(item) == 1 and 'The previous MPC resolution listed among its decisions: "continue with the neutral' in item[0]
    assert "The new MPC resolution doesn't have this item." in item[0]


def test_different_financial_years_or_economies_never_pair():
    def doc_pair(year: str, region: str):
        old = ["Domestic Outlook", "Real GDP growth for 2023-24 was placed at 8.2 per cent. Exports grew strongly.",
               "Global Outlook", ("Crude oil prices were volatile. In most EMEs, bond yields have softened in recent weeks on "
                                  "falling crude oil prices and steadying currencies."), "Projections", "Risks"]
        new = ["Domestic Outlook", "Exports grew strongly.", "Global Outlook",
               (f"Crude oil prices were volatile. The 10-year yield in {region}, which fell to a multi-month low in December, "
                "rose in January on the edging up of crude oil prices."),
               "Projections", f"Real GDP growth for {year} is projected at 7.2 per cent.", "Risks"]
        return _pairs(_v25(*_docs(old, new), hi=10))

    for o, n in doc_pair("Q1:2025-26", "the US"):        # mpc-2024-08-08 (a moved sentence), mpc-2019-02-07
        assert not (o and n), (o, n)
    same = doc_pair("Q1:2023-24", "most EMEs")           # the same year, the same economies: counterparts
    assert ("Real GDP growth for 2023-24 was placed at 8.2 per cent.",
            "Real GDP growth for Q1:2023-24 is projected at 7.2 per cent.") in same
    assert any(o.startswith("In most EMEs") and n.startswith("The 10-year yield in most EMEs") for o, n in same)


def test_the_task_reads_the_v1_citations_for_v25_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False, **later):
        seen.append((packed, restated, later))
        return []

    class Tx:
        def __init__(self):
            self.args = None

        def execute(self, sql, args):
            self.args = args
            return SimpleNamespace(fetchall=lambda: [(7, 10, 20), (8, 0, 5)])

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task = ai_batch.AiBatch()
    w = SimpleNamespace(segments={}, base=None, doc=SimpleNamespace(document_id=8), task="statement_diff_summary")
    for version in ("v24", "v25"):
        tx = Tx()
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(tx, w, call, 1, 1) is None
        assert tx.args == (None if version == "v24" else ("statement_diff_summary", 8))
    assert seen == [(True, True, {}), (True, True, {"counterparts": True, "v1": [(7, 10, 20), (8, 0, 5)]})]
