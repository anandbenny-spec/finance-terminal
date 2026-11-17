"""#445, round 25 (Anand's decision of 17 Nov 2026, about 14:10 IST, after round 24's verdict: rounds 25 and 26 open, and round
25 fixes the pattern of rounds 22 to 24, not only round 24's two failures): `statement_diff_summary` v28 is v27's text plus four
rules, with the changes every output must report (`summaries.DIFF_MUST_FROM`, `must_changes`): the decision, the vote's split,
the stance and guidance, the committee's own growth and inflation projections, and every change the target's v1 output cites.
They are listed in the request (`must_text`), a reply that leaves one out (or quotes a side of a change of the stance without
its words on the stance) is retried (`must_problems`), the fallback built by code shows every one (`fallback_bullets`' `must`),
and a predicate quoted after a preposition fails the grammar-fit check. The sentences below are cut down from mpc-2021-02-05's
resolutions (round 24's failure 1: 3702) and mpc-2026-10-07's (failure 2: 3710), and shaped on round 22's mpc-2022-09-30.
v27 keeps its rules. No database and no model, but for the task test's fake transaction."""
import re
from collections import Counter
from types import SimpleNamespace

import pytest
from test_diff_summary_v25 import _docs

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import Checked, check_diff_json, fallback_bullets, fit_problem, must_changes, must_problems
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION, Work

ADVERSE = "The outlook for inflation has turned adverse relative to expectations in the last two months."
TRAJECTORY = ("With the larger than anticipated deflation in vegetable prices in December bringing down headline closer to the "
              "target, it is likely that the food inflation trajectory will shape the near-term outlook.")
CPI_OLD = ("Taking into consideration all these factors, CPI inflation is projected at 6.8 per cent for Q3:2020-21, 5.8 per cent "
           "for Q4:2020-21; and 5.2 per cent to 4.6 per cent in H1:2021-22, with risks broadly balanced (Chart 1).")
CPI_NEW = ("Taking into consideration all these factors, the projection for CPI inflation has been revised to 5.2 per cent in "
           "Q4:2020-21, 5.2 per cent to 5.0 per cent in H1:2021-22 and 4.3 per cent in Q3: 2021-22, with risks broadly "
           "balanced (Chart 1).")
VIEW = ("The MPC is of the view that inflation is likely to remain elevated, barring transient relief in the winter months from "
        "prices of perishables.")
NOTES = ("The MPC notes that the sharp correction in food prices has improved the food price outlook, but some pressures "
         "persist, and core inflation remains elevated.")
CONSTRAINS = "This constrains monetary policy at the current juncture from using the space available to act in support of growth."
PUMP = "Pump prices of petrol and diesel have reached historical highs."
ACCORDINGLY = ("Accordingly, the MPC in its meeting today decided to maintain status quo on the policy rate and continue with the "
               "accommodative stance as long as necessary – at least during the current financial year and into the next "
               "financial year – to revive growth on a durable basis and mitigate the impact of COVID-19 on the economy, while "
               "ensuring that inflation remains within the target going forward.")
TAKING = ("Taking these developments into consideration, the MPC in its meeting today decided to continue with an accommodative "
          "stance of monetary policy till the prospects of a sustained recovery are well secured while closely monitoring the "
          "evolving outlook for inflation.")
VOTE_OLD = ("All members of the MPC – Dr. Shashanka Bhide, Dr. Ashima Goyal, Prof. Jayanth R. Varma, Dr. Mridul K. Saggar, Dr. "
            "Michael Debabrata Patra and Shri Shaktikanta Das – unanimously voted for keeping the policy repo rate unchanged.")
VOTE_NEW = ("All members of the MPC – Dr. Shashanka Bhide; Dr. Ashima Goyal; Prof. Jayanth R. Varma; Dr. Mridul K. Saggar; Dr. "
            "Michael Debabrata Patra; and Shri Shaktikanta Das – unanimously voted for keeping the policy repo rate unchanged at 4 "
            "per cent.")
# Paragraph 8 whole, as the resolutions have it: its sentences between the two stance sentences decide how the redline pairs.
EIGHT_OLD = ("At the same time, the signs of recovery are far from being broad-based and are dependent on sustained policy support. "
             "A small window is available for proactive supply management strategies to break the inflation spiral being "
             "fuelled by supply chain disruptions, excessive margins and indirect taxes. Further efforts are necessary to "
             "mitigate supply-side driven inflation pressures. Monetary policy will monitor closely all threats to price "
             "stability to anchor broader macroeconomic and financial stability.")
EIGHT_NEW = ("An unwinding of taxes on petroleum products by both the centre and the states could ease the cost push pressures. "
             "What is needed at this point is to create conditions that result in a durable disinflation. This is contingent "
             "also on proactive supply side measures. Growth is recovering, and the outlook has improved significantly with "
             "the rollout of the vaccine programme in the country. The Union Budget 2021-22 has introduced several measures to "
             "provide an impetus to growth. The projected increase in capital expenditure augurs well for capacity creation "
             "thereby improving the prospects for growth and building credibility around the quality of expenditure. The "
             "recovery, however, is still to gather firm traction and hence continued policy support is crucial.")
FEB_OLD = ["Outlook", f"6. {ADVERSE} {CPI_OLD}", f"8. {VIEW} {CONSTRAINS} {EIGHT_OLD} {ACCORDINGLY}", f"9. {VOTE_OLD}"]
FEB_NEW = ["Outlook", f"6. {TRAJECTORY} {CPI_NEW}", f"8. {NOTES} {PUMP} {EIGHT_NEW} {TAKING}", f"9. {VOTE_NEW}"]
# v1 (397)'s citations, by bullet: the projections; the outlook; the stance, with the removed "This constrains …".
FEB_V1 = [("old", CPI_OLD, 0), ("new", CPI_NEW, 0), ("old", ADVERSE, 1), ("old", CONSTRAINS, 2), ("old", ACCORDINGLY, 2),
          ("new", TAKING, 2)]


def _feb(v1=FEB_V1):
    seg, a, b = _docs(FEB_OLD, FEB_NEW)
    cites = [(d.document_id, d.text.index(s), d.text.index(s) + len(s), k)
             for side, s, k in v1 for d in (a if side == "old" else b,)]
    return seg, a, b, cites


def _sentences(m, a, b) -> tuple[str, str]:
    """A change's two sides' sentences, without an RBI paragraph number."""
    return tuple(re.sub(r"^\d{1,2}\. ", "", " ".join(d.text[s:e] for s, e in spans)) for d, spans in ((a, m.old), (b, m.new)))


def _answer(*bullets):
    """A json-route reply: each bullet as (text, [(document, quote), …])."""
    return {"bullets": [{"text": t, "quotes": [{"document": d, "text": q} for d, q in qs]} for t, qs in bullets]}


def _checked(seg, a, b, answer, version="v28") -> Checked:
    old, new = summaries.changed_spans(seg)
    req = summaries.diff_summary_request(b, a, seg, load_prompt("statement_diff_summary", version))
    old_all, new_all = summaries.sentence_spans(seg)
    return check_diff_json(answer, req.sources, {a.document_id: old, b.document_id: new}, version=version,
                           sentences={a.document_id: old_all, b.document_id: new_all})


# Round 24's 3702 (mpc-2021-02-05), cut down to these sentences: the stance quoted from the previous resolution's words on the
# policy rate, and neither "This constrains …" nor the outlook's turn reported.
ROUND_24 = _answer(
    (('The guidance on the stance was reworded: the previous resolution said the MPC decided to "maintain status quo on the '
      'policy rate", while the new one says the stance continues "till the prospects of a sustained recovery are well secured".'),
     [("previous", "maintain status quo on the policy rate"),
      ("new", "till the prospects of a sustained recovery are well secured")]),
    (('The CPI inflation projection was revised: the previous resolution gave "5.8 per cent for Q4:2020-21", while the new one '
      'gives "5.2 per cent in Q4:2020-21".'), [("previous", "5.8 per cent for Q4:2020-21"), ("new", "5.2 per cent in Q4:2020-21")]),
    ('The new resolution adds that "Pump prices of petrol and diesel have reached historical highs."', [("new", PUMP)]))
# The same, reporting what v1 does: the stance from each side's words on the stance, the removed sentences, and the vote.
COVERED = _answer(
    (('The stance moved from continuing "with the accommodative stance as long as necessary" to continuing "with an '
      'accommodative stance of monetary policy till the prospects of a sustained recovery are well secured".'),
     [("previous", "with the accommodative stance as long as necessary"),
      ("new", "with an accommodative stance of monetary policy till the prospects of a sustained recovery are well secured")]),
    (('The previous resolution\'s view that this "constrains monetary policy at the current juncture from using the space '
      'available to act in support of growth" is gone.'),
     [("previous", ("constrains monetary policy at the current juncture from using the space available to act in support of "
                    "growth"))]),
    ('The CPI inflation projection was revised from "5.8 per cent for Q4:2020-21" to "5.2 per cent in Q4:2020-21".',
     [("previous", "5.8 per cent for Q4:2020-21"), ("new", "5.2 per cent in Q4:2020-21")]),
    (('The previous resolution said the outlook for inflation "has turned adverse relative to expectations", while the new one '
      'says the food inflation trajectory "will shape the near-term outlook".'),
     [("previous", "has turned adverse relative to expectations"), ("new", "will shape the near-term outlook")]),
    (('The vote to keep the rate unchanged now gives the rate: members voted for keeping it "unchanged at 4 per cent", where '
      'before they voted for keeping it "unchanged".'),
     [("new", "unchanged at 4 per cent"), ("previous", "unanimously voted for keeping the policy repo rate unchanged")]))


def test_v28_is_v27s_text_plus_four_rules_and_is_current():
    v27, v28 = load_prompt("statement_diff_summary", "v27"), load_prompt("statement_diff_summary", "v28")
    assert v28.version == "v28" and v28.text != v27.text
    for rule in ("the instruction may list the changes that must each be reported", "under 80 words",
                 'not that it held the rate at "unchanged at 5.25 per cent"', "quote what each document says about the stance"):
        assert rule in v28.text and rule not in v27.text, rule
    assert PROMPT_VERSION["statement_diff_summary"] == "v28" and summaries.DIFF_MUST_FROM == 28
    assert summaries.DIFF_MODEL_OF["v28"] == summaries.DIFF_MODEL_OF["v27"]
    assert summaries.DIFF_EFFORT_OF["v28"] == summaries.DIFF_EFFORT_OF["v27"]
    s27, s28 = load_schema("statement_diff_summary", "v27"), load_schema("statement_diff_summary", "v28")
    text = s28["properties"]["bullets"]["items"]["properties"]["text"]["description"]
    assert "under 80 words" in text
    s28["properties"]["bullets"]["items"]["properties"]["text"]["description"] = (
        s27["properties"]["bullets"]["items"]["properties"]["text"]["description"])
    assert s28 == s27                                   # the schema is v27's but for the text's length


def test_the_changes_to_report_are_the_decision_stance_projections_and_v1s_changes():
    seg, a, b, v1 = _feb()
    must = must_changes(seg, a, b, v1)
    got = {_sentences(m, a, b): (m.why, m.group) for m in must}
    assert got[(CONSTRAINS, "")] == ("v1", None)                       # removed, and v1 cites it
    assert got[(ADVERSE, TRAJECTORY)] == ("v1", None)                  # the outlook's turn
    assert got[(CPI_OLD, CPI_NEW)][0] in ("projection", "v1")
    assert got[(ACCORDINGLY, "")][0] == "stance" and got[("", TAKING)][0] == "stance"
    assert [m.group for m in must].count("decision") == 1             # the decision once
    assert (VIEW, NOTES) not in got and ("", PUMP) not in got          # what v1 doesn't cite and no key kind
    # Without a v1 output only the key kinds are left: no outlook, no "This constrains …".
    bare = {_sentences(m, a, b) for m in must_changes(seg, a, b, [])}
    assert (ADVERSE, TRAJECTORY) not in bare and (CONSTRAINS, "") not in bare
    assert (ACCORDINGLY, "") in bare and ("", TAKING) in bare and (CPI_OLD, CPI_NEW) in bare


def test_round_24s_3702_is_retried_and_a_reply_that_reports_them_passes():
    seg, a, b, v1 = _feb()
    must = must_changes(seg, a, b, v1)
    c = _checked(seg, a, b, ROUND_24)
    assert c.ok                                          # v27's checks let it through
    problems = must_problems(c, must, a, b)
    assert any(CONSTRAINS[:60] in p and "left out" in p for p in problems)
    assert any(ADVERSE[:60] in p for p in problems)
    assert any("quotes the previous document without its words on the stance" in p and ACCORDINGLY[:60] in p
               for p in problems)
    c = _checked(seg, a, b, COVERED)
    assert c.ok, c.reasons()
    assert must_problems(c, must, a, b) == []


# mpc-2022-09-30 (round 22, failure 1): the vote went from unanimous to five members, with one for 35 basis points.
SPLIT_OLD = ["Decision", "The MPC decided to increase the policy repo rate by 50 basis points to 5.40 per cent.",
             "Voting", ("9. All members of the MPC – Dr. Shashanka Bhide, Dr. Ashima Goyal and Shri Shaktikanta Das – "
                        "unanimously voted to increase the policy repo rate by 50 basis points to 5.40 per cent.")]
SPLIT_NEW = ["Decision", "The MPC decided to increase the policy repo rate by 50 basis points to 5.90 per cent.",
             "Voting", ("11. Dr. Shashanka Bhide and Shri Shaktikanta Das voted to increase the policy repo rate by 50 basis "
                        "points. Dr. Ashima Goyal voted to increase the repo rate by 35 basis points.")]


def test_a_vote_unanimous_on_one_side_only_is_reported_besides_the_decision():
    seg, a, b = _docs(SPLIT_OLD, SPLIT_NEW)
    must = must_changes(seg, a, b, [])
    whys = [m.why for m in must]
    assert whys.count("decision") == 1 and "vote" in whys
    vote = next(m for m in must if m.why == "vote" and m.old)
    assert "unanimously" in _sentences(vote, a, b)[0]
    assert any("Goyal voted to increase the repo rate by 35" in _sentences(m, a, b)[1] for m in must)


IMF_OLD = ["Global", "2. The IMF projected global growth for 2021 at 5.2 per cent.",
           "Outlook", "6. Real GDP growth for 2021-22 is projected at 9.5 per cent.", "The risks are balanced."]
IMF_NEW = ["Global", "2. The IMF projected global growth for 2021 at 5.5 per cent, in line with the projection of 5.4 per cent.",
           "Outlook", "6. Real GDP growth for 2021-22 is projected at 10.5 per cent.", "The risks are evenly balanced."]


def test_only_the_committees_own_projection_must_be_reported():
    seg, a, b = _docs(IMF_OLD, IMF_NEW)
    got = {_sentences(m, a, b): m.why for m in must_changes(seg, a, b, [])}
    assert any("is projected at 10.5 per cent" in n and why == "projection" for (_, n), why in got.items())
    assert not any("IMF" in o + n for o, n in got)


FOMC_OLD = [("Voting for the monetary policy action were Jerome H. Powell, Chairman; John C. Williams, Vice Chairman; and "
             "Michelle W. Bowman."), "Inflation remains elevated."]
FOMC_NEW = [("Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice Chair; and Michelle W. "
             "Bowman."), "Inflation has eased over the past year but remains elevated."]


def test_a_change_only_in_names_or_titles_is_never_one_to_report():
    seg, a, b = _docs(FOMC_OLD, FOMC_NEW)
    v1 = [(a.document_id, 0, len(FOMC_OLD[0]), 0)]    # even when v1 cites it
    assert not any("Voting" in _sentences(m, a, b)[0] for m in must_changes(seg, a, b, v1))
    assert summaries._names_only(FOMC_OLD[0], FOMC_NEW[0]) and not summaries._names_only(VOTE_OLD, VOTE_NEW)


def _fallback(seg, a, b, v1, **later):
    return fallback_bullets(seg, a, b, [], packed=True, restated=True, counterparts=True, v1=v1, kinds=True, v1_pairs=True,
                            v1_all=True, **later)


def test_the_fallback_shows_every_change_to_report_and_a_lone_stance_beside_its_other_side():
    seg, a, b, _ = _feb()
    must = must_changes(seg, a, b, [])                 # no v1 output: v27's fallback has nothing to show first
    v28 = _fallback(seg, a, b, [], must=True)
    c = Checked("statement_diff_summary", v28, [], Counter())
    assert must_problems(c, must, a, b) == []
    both = [t.text for t in v28 if ACCORDINGLY[30:90] in t.text]
    assert len(both) == 1 and TAKING[:60] in both[0]   # the removed stance sentence beside the added one
    assert _fallback(seg, a, b, []) == _fallback(seg, a, b, [], must=False)


def test_the_fallback_without_must_is_v27s():
    seg, a, b, v1 = _feb()
    assert [x.text for x in _fallback(seg, a, b, v1)] == [x.text for x in fallback_bullets(
        seg, a, b, [], packed=True, restated=True, counterparts=True, v1=v1, kinds=True, v1_pairs=True, v1_all=True)]


@pytest.mark.parametrize("text", [
    ('The MPC moved from holding the policy repo rate at "unchanged at 5.25 per cent" to a decision to "increase the policy '
     'repo rate".'),                                    # round 24: mpc-2026-10-07's 3710
    'The rate was held at "steady at 4 per cent" in both.',
])
def test_a_predicate_after_a_preposition_fails_from_v28(text):
    assert fit_problem(text) is None                     # v27's check let it through
    assert "doesn't fit the grammar" in fit_problem(text, predicate=True)


def test_a_predicate_where_it_fits_passes():
    for text in ('The MPC kept the policy repo rate "unchanged at 5.25 per cent".',
                 'The SDF rate now "stands adjusted at 5.25 per cent".',
                 'Inflation is now projected at "5.2 per cent" for the year.'):
        assert fit_problem(text, predicate=True) is None, text


def test_the_v28_request_lists_the_changes_to_report_and_v27s_doesnt():
    seg, a, b, v1 = _feb()
    w = Work("statement_diff_summary", b, a, seg, v1=v1)
    v28 = w.request(load_prompt("statement_diff_summary", "v28")).instruction
    v27 = w.request(load_prompt("statement_diff_summary", "v27")).instruction
    assert "Each of these changes must be reported" in v28 and "Each of these changes must be reported" not in v27
    listed = v28.split("Each of these changes must be reported", 1)[1]
    assert f'Removed (only the previous document has it): "{CONSTRAINS}"' in listed
    assert listed.count("- The decision: ") == 1
    assert v28.rstrip().endswith("following the rules.")


def test_the_task_retries_a_reply_that_leaves_one_out_before_asking_the_second_model():
    seg, a, b, v1 = _feb()
    asked = []
    for version, answer, ok in (("v28", ROUND_24, False), ("v28", COVERED, True), ("v27", ROUND_24, True)):
        w = Work("statement_diff_summary", b, a, seg, v1=v1, verify=lambda req, c, v=version: asked.append(v))
        req = w.request(load_prompt("statement_diff_summary", version))
        c = w.check(req, SimpleNamespace(json=lambda answer=answer: answer))
        assert c.ok == ok, (version, c.reasons())
        if version == "v28" and not ok:
            assert any("left out" in r for r in c.reasons()) and "retried" not in " ".join(c.reasons())
    assert asked == ["v28", "v27"]                       # the second model only for the replies that passed


def test_the_task_passes_must_from_v28_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False, **later):
        seen.append(sorted(later))
        return []

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task = ai_batch.AiBatch()
    w = SimpleNamespace(segments={}, base=None, doc=SimpleNamespace(document_id=8), task="statement_diff_summary")
    tx = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchall=lambda: [(8, 0, 10, 2)]))
    for version in ("v27", "v28"):
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(tx, w, call, 1, 1) is None
    assert seen == [["counterparts", "kinds", "v1", "v1_all", "v1_pairs"],
                    ["counterparts", "kinds", "must", "v1", "v1_all", "v1_pairs"]]
