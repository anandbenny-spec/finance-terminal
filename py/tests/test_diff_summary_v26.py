"""#445, round 23 (Anand's decision of 17 Nov 2026, 07:06 IST): `statement_diff_summary` v26 is v25's text, marking revisions
of the fallback built by code. `summaries.DIFF_KIND_JOIN_FROM` (`fallback_bullets`' `kinds`): a sentence joins another's change
(a sentence split in two, or two merged) only when it is of that change's kind. Round 22's mpc-2022-09-30 joined "Dr. Ashima
Goyal voted to increase the repo rate by 35 basis points." to the change of the vote on the policy repo rate, and the one
decision change per output then left the dissent out. `summaries.DIFF_V1_PAIRS_FROM` (`v1_pairs`, from round 22's verdict of 17
Nov 2026, 08:26 IST): a v1 bullet's removed and added sentences are one change (mpc-2020-08-06's guidance), and a change of
the decision's kind that v1 states in a bullet of its own is shown (mpc-2022-09-30's vote). Fallbacks of v25 keep v25's
rules. No database and no model, but for the task test's fake transaction."""
from types import SimpleNamespace

from test_diff_summary_v25 import SPLIT_NEW, SPLIT_OLD, _docs

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import fallback_bullets
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION

VOTE_OLD = ["Decisions", "The MPC decided to increase the policy repo rate by 50 basis points to 5.40 per cent.",
            "Votes", ("9. All members of the MPC - Dr. Shashanka Bhide, Dr. Ashima Goyal, Prof. Jayanth R. Varma, Dr. Rajiv "
                      "Ranjan, Dr. Michael Debabrata Patra and Shri Shaktikanta Das - unanimously voted to increase the policy "
                      "repo rate by 50 basis points to 5.40 per cent."), "Minutes"]
VOTE_NEW = ["Decisions", "The MPC decided to increase the policy repo rate by 50 basis points to 5.90 per cent.",
            "Votes", ("11. Dr. Shashanka Bhide, Prof. Jayanth R. Varma, Dr. Rajiv Ranjan, Dr. Michael Debabrata Patra and Shri "
                      "Shaktikanta Das voted to increase the policy repo rate by 50 basis points. Dr. Ashima Goyal voted to "
                      "increase the repo rate by 35 basis points."), "Minutes"]


def _run(old, new, kinds, v1=None, v1_pairs=False, segments=None):
    seg, a, b = _docs(old, new)
    cites = [(a if side == "old" else b, s, k) for side, s, k in v1 or []]
    v1 = [(d.document_id, d.text.index(s), d.text.index(s) + len(s), k) for d, s, k in cites]
    return [x.text for x in fallback_bullets(segments(seg) if segments else seg, a, b, [], 5, packed=True, restated=True,
                                             counterparts=True, v1=v1, kinds=kinds, v1_pairs=v1_pairs)]


GUIDE_OLD = ["Decisions", "The MPC decided to reduce the policy repo rate by 40 bps to 4.0 per cent.", "Outlook",
             ("11. Even as various measures work to mitigate the adverse impact of the pandemic, it is necessary to ease "
              "financial conditions further. With the inflation outlook remaining benign, the policy space to address growth "
              "concerns needs to be used now rather than later to support the economy."),
             "12. The minutes of the MPC's meeting will be published by June 5, 2020."]
GUIDE_NEW = ["Decisions", "The MPC decided to keep the policy repo rate unchanged at 4.0 per cent.", "Outlook",
             "15. The NSO has not provided inflation rates for April and May.",
             ("18. Accordingly, the MPC decides to stay on hold with regard to the policy rate and remain watchful for a durable "
              "reduction in inflation to use the available space to support the revival of the economy."),
             "20. The minutes of the MPC's meeting will be published by August 20, 2020."]
GUIDE_V1 = [("old", "The MPC decided to reduce", 0), ("new", "The MPC decided to keep", 0),
            ("old", "With the inflation outlook remaining benign", 1), ("new", "Accordingly, the MPC decides", 1)]
STAY = ("The new MPC resolution says: \"Accordingly, the MPC decides to stay on hold with regard to the policy rate and remain "
        "watchful for a durable reduction in inflation to use the available space to support the revival of the economy.\"")


def test_v26_is_v25s_text_with_v25s_schema_on_sonnet_and_is_current():
    v25, v26 = load_prompt("statement_diff_summary", "v25"), load_prompt("statement_diff_summary", "v26")
    assert v26.text == v25.text and v26.version == "v26"
    assert load_schema("statement_diff_summary", "v26") == load_schema("statement_diff_summary", "v25")
    assert PROMPT_VERSION["statement_diff_summary"] == "v26" and summaries.DIFF_KIND_JOIN_FROM == 26
    assert summaries.DIFF_MODEL_OF["v26"] == summaries.DIFF_MODEL_OF["v25"]
    assert summaries.DIFF_EFFORT_OF["v26"] == summaries.DIFF_EFFORT_OF["v25"]


def test_a_dissent_next_to_a_vote_stays_its_own_change():
    v25 = " ".join(_run(VOTE_OLD, VOTE_NEW, kinds=False))
    v26 = " ".join(_run(VOTE_OLD, VOTE_NEW, kinds=True))
    assert "Dr. Ashima Goyal voted to increase the repo rate by 35 basis points." not in v25     # round 22's defect
    assert ('The new MPC resolution says: "Dr. Ashima Goyal voted to increase the repo rate by 35 basis points." The '
            "previous MPC resolution doesn't have this sentence.") in v26


def test_a_sentence_of_the_same_kind_still_joins_its_counterpart():
    vigil = [t for t in _run(SPLIT_OLD, SPLIT_NEW, kinds=True) if "strong vigil" in t]
    assert len(vigil) == 1 and "close vigil" in vigil[0] and "It will take further monetary actions" in vigil[0]


def test_v1s_counterparts_are_one_change_and_the_guidance_is_not_a_second_decision():
    v25 = " ".join(_run(GUIDE_OLD, GUIDE_NEW, kinds=True, v1=GUIDE_V1))
    assert "stay on hold" not in v25                    # round 22's mpc-2020-08-06: left out as a second decision
    v26 = _run(GUIDE_OLD, GUIDE_NEW, kinds=True, v1=GUIDE_V1, v1_pairs=True)
    both = [t for t in v26 if "now rather than later" in t]
    assert len(both) == 1 and STAY in both[0] and "doesn't have" not in both[0]
    assert sum("stay on hold" in t for t in v26) == 1 and sum("unchanged at 4.0 per cent" in t for t in v26) == 1


def test_v1s_counterparts_need_one_change_on_each_side_and_one_topic():
    alone = _run(GUIDE_OLD, GUIDE_NEW, kinds=True, v1=GUIDE_V1[:2] + [GUIDE_V1[3]], v1_pairs=True)
    assert not any("now rather than later" in t and "stay on hold" in t for t in alone)   # v1 cites one side only

    seg, _, b = _docs(GUIDE_OLD, GUIDE_NEW)
    for p in seg["pairs"]:
        p["topic"] = "Outlook"
        for s, e in p["new"]:
            if "stay on hold" in b.text[s:e]:
                p["topic"] = "Decision and stance"
    apart = _run(GUIDE_OLD, GUIDE_NEW, kinds=True, v1=GUIDE_V1, v1_pairs=True, segments=lambda _: seg)
    assert not any("now rather than later" in t and "stay on hold" in t for t in apart)   # another part of the document


def test_v1s_vote_is_shown_with_the_dissent_and_a_restated_decision_is_not():
    v1 = [("old", "The MPC decided to increase", 0), ("new", "The MPC decided to increase", 0),
          ("old", "9. All members of the MPC", 1), ("new", "11. Dr. Shashanka Bhide", 1),
          ("new", "Dr. Ashima Goyal voted", 1)]
    v26 = _run(VOTE_OLD, VOTE_NEW, kinds=True, v1=v1, v1_pairs=True)
    vote = [t for t in v26 if "unanimously voted" in t]
    assert len(vote) == 1 and "voted to increase the policy repo rate by 50 basis points.\"" in vote[0]
    assert "Dr. Ashima Goyal voted to increase the repo rate by 35 basis points." in " ".join(v26)    # (few changes: its own)
    assert "unanimously voted" not in " ".join(_run(VOTE_OLD, VOTE_NEW, kinds=True, v1=v1))     # round 22: left out
    again_old = VOTE_OLD[:3] + ["Accordingly, the MPC decided to increase the policy repo rate by 50 basis points to 5.40 per cent."]
    again_new = VOTE_NEW[:3] + ["Accordingly, the MPC decided to increase the policy repo rate by 50 basis points to 5.90 per cent."]
    v1 = v1[:2] + [("old", "Accordingly, the MPC decided", 1), ("new", "Accordingly, the MPC decided", 1)]
    assert not any("Accordingly" in t for t in _run(again_old, again_new, kinds=True, v1=v1, v1_pairs=True))


def test_the_task_passes_kinds_from_v26_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False, **later):
        seen.append(later)
        return []

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task = ai_batch.AiBatch()
    w = SimpleNamespace(segments={}, base=None, doc=SimpleNamespace(document_id=8), task="statement_diff_summary")
    tx = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchall=lambda: [(8, 0, 10, 2)]))
    for version in ("v24", "v25", "v26"):
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(tx, w, call, 1, 1) is None
    assert [sorted(x) for x in seen] == [[], ["counterparts", "v1"], ["counterparts", "kinds", "v1", "v1_pairs"]]
    assert [x.get("v1") for x in seen] == [None, [(8, 0, 10)], [(8, 0, 10, 2)]]    # v26 reads each citation's bullet
