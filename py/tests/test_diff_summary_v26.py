"""#445, round 23 (Anand's decision of 17 Nov 2026, 07:06 IST): `statement_diff_summary` v26 is v25's text, marking one revision
of the fallback built by code (`summaries.DIFF_KIND_JOIN_FROM`, `fallback_bullets`' `kinds`): a sentence joins another's change
(a sentence split in two, or two merged) only when it is of that change's kind. Round 22's mpc-2022-09-30 joined "Dr. Ashima
Goyal voted to increase the repo rate by 35 basis points." to the change of the vote on the policy repo rate, and the one
decision change per output then left the dissent out. Fallbacks of v25 keep v25's rules. No database and no model, but for
the task test's fake transaction."""
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


def _run(old, new, kinds):
    segments, a, b = _docs(old, new)
    return [x.text for x in fallback_bullets(segments, a, b, [], 5, packed=True, restated=True, counterparts=True, v1=[],
                                             kinds=kinds)]


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


def test_the_task_passes_kinds_from_v26_only(monkeypatch):
    seen = []

    def fake(segments, old, new, prefer, hi=5, *, packed=False, restated=False, **later):
        seen.append(sorted(later))
        return []

    monkeypatch.setattr(summaries, "fallback_bullets", fake)
    task = ai_batch.AiBatch()
    w = SimpleNamespace(segments={}, base=None, doc=SimpleNamespace(document_id=8), task="statement_diff_summary")
    tx = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchall=list))
    for version in ("v24", "v25", "v26"):
        call = SimpleNamespace(reply=SimpleNamespace(json=lambda: {"bullets": []}),
                               request=SimpleNamespace(prompt=SimpleNamespace(version=version)))
        assert task._store_fallback(tx, w, call, 1, 1) is None
    assert seen == [[], ["counterparts", "v1"], ["counterparts", "kinds", "v1"]]
