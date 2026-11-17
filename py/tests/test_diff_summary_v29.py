"""#445, round 26 (Anand's decision of 17 Nov 2026, about 14:10 IST: round 26 is round 25's version plus only what its verdict
names). Round 25's verdict (17 Nov 2026, 18:19 IST) failed mpc-2020-03-27's code-built 3747 on rule (c): the new resolution's
third decision, the reverse repo rate "reduced by 90 basis points to 4.0 per cent", and its open market purchases, both of
which v1 (346) reports, found no room. `statement_diff_summary` v29 (`summaries.DIFF_ROOM_FROM`) is v28's text plus one
sentence: a corridor rate's sentence alone joins its other side (`_corridor_mates`), the fallback places the changes v1 cites
before the ones only v28's list adds, a ratio to GDP is no projection, each item of a list of decisions in a change to report
is checked on the model's route, and the request shows each listed sentence. The sentences below are cut down from
mpc-2020-03-27's and its previous resolution's. v28 keeps its rules. No database and no model."""
from collections import Counter
from types import SimpleNamespace

from test_diff_summary_v25 import _docs
from test_diff_summary_v28 import _answer, _checked

from ft.ai import summaries
from ft.ai.client import load_prompt, load_schema
from ft.ai.summaries import Checked, fallback_bullets, must_changes, must_problems, must_text
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION, Work

LEAD = ("On the basis of an assessment of the current and evolving macroeconomic situation, the Monetary Policy Committee (MPC) "
        "at its meeting today ({}) decided to:")
KEEP = "keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 5.15 per cent."
CONSEQUENTLY = ("Consequently, the reverse repo rate under the LAF remains unchanged at 4.90 per cent and the marginal standing "
                "facility (MSF) rate and the Bank Rate at 5.40 per cent.")
REDUCE = ("reduce the policy repo rate under the liquidity adjustment facility (LAF) by 75 basis points to 4.40 per cent from "
          "5.15 per cent with immediate effect;")
MSF = ("accordingly, the marginal standing facility (MSF) rate and the Bank Rate stand reduced to 4.65 per cent from 5.40 per "
       "cent;")
REVERSE = ("further, consequent upon the widening of the LAF corridor as detailed in the accompanying Statement on Developmental "
           "and Regulatory Polices, the reverse repo rate under the LAF stands reduced by 90 basis points to 4.0 per cent.")
STANCE_OLD = ("The MPC also decided to continue with the accommodative stance as long as it is necessary to revive growth, while "
              "ensuring that inflation remains within the target.")
STANCE_NEW = ("The MPC also decided to continue with the accommodative stance as long as it is necessary to revive growth and "
              "mitigate the impact of coronavirus (COVID-19) on the economy, while ensuring that inflation remains within the "
              "target.")
MAR_OLD = [LEAD.format("February 6, 2020"), KEEP, CONSEQUENTLY, STANCE_OLD]
MAR_NEW = [LEAD.format("March 27, 2020"), REDUCE, MSF, REVERSE, STANCE_NEW]
# Paragraph 24's sentences, which v28 read as one projection: the fiscal deficit "placed at 3.8 per cent of GDP".
BUDGET = "The Union Budget 2020-21 has introduced several measures to provide an impetus to growth."
EMPHASIS = ("While the emphasis on boosting the rural economy and infrastructure should help the growth momentum in the "
            "near-term, the corporate tax rate cuts of September 2019 should help boost the growth potential over the "
            "medium-term.")
DEFICIT = ("The fiscal deficit of the Central Government for 2019-20 is placed at 3.8 per cent of GDP in the revised estimates as "
           "against 3.3 per cent of GDP in the budget estimates.")
GROWTH = ("Taking into consideration the above factors, GDP growth for 2020-21 is projected at 6.0 per cent – in the range of "
          "5.5-6.0 per cent in H1 and 6.2 per cent in Q3 (Chart 2).")


def _mar():
    seg, a, b = _docs(MAR_OLD, MAR_NEW)
    # v1 (346), bullet 0: the new decisions whole (one citation, all three items) and the previous decision and corridor.
    v1 = [(b.document_id, 0, b.text.index(REVERSE) + len(REVERSE), 0), (a.document_id, 0, a.text.index(CONSEQUENTLY)
                                                                        + len(CONSEQUENTLY), 0)]
    return seg, a, b, v1


def _span(d, s):
    i = d.text.index(s)
    return i, i + len(s)


def _fallback(seg, a, b, v1, **later):
    return fallback_bullets(seg, a, b, [], packed=True, restated=True, counterparts=True, v1=v1, kinds=True, v1_pairs=True,
                            v1_all=True, must=True, **later)


def test_v29_is_v28s_text_plus_one_sentence_and_is_current():
    v28, v29 = load_prompt("statement_diff_summary", "v28"), load_prompt("statement_diff_summary", "v29")
    added = "Where a listed change holds items of a list of decisions (the rates the committee set), report every one of those items."
    assert v29.version == "v29" and added in v29.text and added not in v28.text
    assert v29.text.replace(" " + added, "") == v28.text
    assert PROMPT_VERSION["statement_diff_summary"] == "v29" and summaries.DIFF_ROOM_FROM == 29
    assert summaries.DIFF_MODEL_OF["v29"] == summaries.DIFF_MODEL_OF["v28"]
    assert summaries.DIFF_EFFORT_OF["v29"] == summaries.DIFF_EFFORT_OF["v28"]
    assert load_schema("statement_diff_summary", "v29") == load_schema("statement_diff_summary", "v28")


def test_a_corridor_rates_item_alone_joins_its_other_side_from_v29():
    seg, a, b, v1 = _mar()
    reverse = _span(b, REVERSE)
    v29 = [m for m in must_changes(seg, a, b, v1, room=True) if reverse in m.new]
    assert len(v29) == 1 and v29[0].old == (_span(a, CONSEQUENTLY),) and v29[0].new == (_span(b, MSF), reverse)
    v28 = [m for m in must_changes(seg, a, b, v1) if reverse in m.new]
    assert len(v28) == 1 and v28[0].old == () and v28[0].new == (reverse,)      # v28: alone


def test_the_v29_fallback_shows_the_reverse_repo_rate_with_what_it_was():
    seg, a, b, v1 = _mar()
    bullets = _fallback(seg, a, b, v1, room=True)
    with_it = [x.text for x in bullets if "reduced by 90 basis points to 4.0 per cent" in x.text]
    assert len(with_it) == 1 and "remains unchanged at 4.90 per cent" in with_it[0]
    assert f'"{MSF} {REVERSE}"' in with_it[0]           # one quotation, as the list gives them
    assert "doesn't have this item" not in with_it[0]
    c = Checked("statement_diff_summary", bullets, [], Counter())
    assert must_problems(c, must_changes(seg, a, b, v1, room=True), a, b, room=True) == []


def _pack_case(v1_cited):
    """Two bullets: the decision's, and room for four changes in the other. Four changes only v28's list adds (the stance),
    and one change v1 cites (another kind), placed as `_pack` places them."""
    decision, stance, other = (summaries.FALLBACK_KINDS.index(k) for k in ("decision", "stance", "other"))
    d = [[(0, 1)], [(0, 1)]]
    musts = [[[(10 * i, 10 * i + 5)], []] for i in range(1, 5)]
    cited = [[], [(100, 105)]]
    by_kind = [d] + musts + [cited]
    kind = {id(d): decision, **{id(p): stance for p in musts}, id(cited): other}
    said = {id(p): (f"old {i}", f"new {i}") for i, p in enumerate(by_kind)}
    chosen = []

    def take(p):
        chosen.append(p)
        return True

    side = {id(d): "both", **{id(p): "old" for p in musts}, id(cited): "new"}
    bullets = summaries._pack(by_kind, by_kind, kind, said, take, 2, first={id(p) for p in by_kind},
                              v1_of={id(p): frozenset() for p in by_kind}, side=side,
                              v1_cited=None if v1_cited is None else {id(d), id(cited)})
    return [c for g in bullets for c in g], cited, musts


def test_the_v29_fallback_places_v1s_changes_before_the_ones_only_v28s_list_adds():
    shown, cited, musts = _pack_case({1})
    assert any(c is cited for c in shown)                # v1's change finds room first
    assert sum(any(c is p for c in shown) for p in musts) == 3
    shown, cited, musts = _pack_case(None)               # v28: by kind, so the stance filled the room
    assert not any(c is cited for c in shown) and all(any(c is p for c in shown) for p in musts)


def test_a_ratio_to_gdp_is_no_projection_from_v29():
    seg, a, b = _docs(["Outlook", f"24. {BUDGET} {EMPHASIS} {DEFICIT}", f"25. {GROWTH}"], ["Outlook", f"25. {GROWTH}"])
    v28 = [m.why for m in must_changes(seg, a, b, []) if _span(a, DEFICIT) in m.old]
    v29 = [m.why for m in must_changes(seg, a, b, [], room=True) if _span(a, DEFICIT) in m.old]
    assert v28 == ["projection"] and v29 == []
    assert summaries._own_projection(GROWTH) and not summaries._K_RATIO.search(GROWTH)


def test_a_reply_leaving_out_an_item_of_the_decisions_is_retried_from_v29():
    seg, a, b, v1 = _mar()
    answer = _answer(
        (('The MPC moved from deciding to "keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at '
          '5.15 per cent" to deciding to "reduce the policy repo rate under the liquidity adjustment facility (LAF) by 75 basis '
          'points to 4.40 per cent from 5.15 per cent with immediate effect".'),
         [("previous", KEEP.rstrip(".")), ("new", REDUCE.rstrip(";"))]),
        (('The previous resolution said that "the reverse repo rate under the LAF remains unchanged at 4.90 per cent", while the '
          'new one says that "the marginal standing facility (MSF) rate and the Bank Rate stand reduced to 4.65 per cent from '
          '5.40 per cent".'),
         [("previous", "the reverse repo rate under the LAF remains unchanged at 4.90 per cent"),
          ("new", "the marginal standing facility (MSF) rate and the Bank Rate stand reduced to 4.65 per cent from 5.40 per cent")]))
    c = _checked(seg, a, b, answer, version="v29")
    assert c.ok, c.reasons()
    v29 = must_problems(c, must_changes(seg, a, b, v1, room=True), a, b, room=True)
    assert any("list of decisions is left out" in p and "further, consequent upon the widening of the LAF" in p for p in v29)
    v28 = must_problems(c, must_changes(seg, a, b, v1), a, b)
    assert not any("list of decisions" in p for p in v28)


def test_the_v29_request_shows_each_listed_sentence():
    seg, a, b, v1 = _mar()
    w = Work("statement_diff_summary", b, a, seg, v1=v1)
    v29 = w.request(load_prompt("statement_diff_summary", "v29")).instruction
    listed = v29.split("Each of these changes must be reported", 1)[1]
    line = next(x for x in listed.splitlines() if "Consequently, the reverse repo rate" in x)
    assert "further, consequent upon the widening of the LAF corridor" in line      # its second item, shown too
    must = must_changes(seg, a, b, v1, room=True)
    each, v28 = must_text(must, a.text, b.text, each=True), must_text(must, a.text, b.text)
    assert len(each) > len(v28) and REVERSE[:100] in each and REVERSE[:100] not in v28


def test_the_task_passes_room_from_v29_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False, **later):
        seen.append(sorted(later))
        return []

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task = ai_batch.AiBatch()
    w = SimpleNamespace(segments={}, base=None, doc=SimpleNamespace(document_id=8), task="statement_diff_summary")
    tx = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchall=lambda: [(8, 0, 10, 2)]))
    for version in ("v28", "v29"):
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(tx, w, call, 1, 1) is None
    assert seen == [["counterparts", "kinds", "must", "v1", "v1_all", "v1_pairs"],
                    ["counterparts", "kinds", "must", "room", "v1", "v1_all", "v1_pairs"]]


def test_work_reads_the_changes_to_report_as_its_version_does():
    seg, a, b, v1 = _mar()
    w = Work("statement_diff_summary", b, a, seg, v1=v1)
    reverse = _span(b, REVERSE)
    assert any(m.new == (reverse,) for m in w.musts(28)) and not any(m.new == (reverse,) for m in w.musts(29))
    assert w.musts(29) is w.musts(29) and w.musts() is w.musts(28)


def test_a_corridor_rates_sentence_elsewhere_stays_alone():
    liquidity = "6. Net absorptions under the reverse repo window averaged ₹ 2.86 lakh crore in March."
    new = [x for x in MAR_NEW if x != REVERSE]          # no third decision: only a later sentence names the reverse repo
    seg, a, b = _docs(MAR_OLD + ["Liquidity"], new + ["Liquidity", liquidity])
    v1 = [(a.document_id, *_span(a, CONSEQUENTLY), 0), (b.document_id, *_span(b, MSF), 0),
          (b.document_id, *_span(b, liquidity), 1)]
    must = must_changes(seg, a, b, v1, room=True)
    there = _span(b, liquidity)
    assert any(m.new == (there,) and not m.old for m in must)                    # alone, as in v28
    assert any(m.old == (_span(a, CONSEQUENTLY),) and m.new == (_span(b, MSF),) for m in must)
