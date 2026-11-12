"""#445 (R05-2; Anand's option 1, 12 Nov 2026): for `statement_diff_summary` from prompt v4 on, a cited text block that is a
word-for-word copy of its own cited text comes out of the bullet's text and its citations stay on the bullet; a copy inside
the model's own sentence, or one whose removal would leave no sentence of the model's own, goes in quotation marks instead.
The checks are unchanged and run on the final text. No database and no model."""
from ai_fakes import cite
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD, reply

from ft.ai import summaries
from ft.ai.client import TextPart, load_prompt
from ft.ai.summaries import check, content, mark_copies
from ft.engines.document.diff import diff_texts
from ft.eval import diff_quotes
from ft.jobs.tasks.ai_batch import Work

D = diff_texts(FOMC_A, FOMC_B)
REQ = summaries.diff_summary_request(NEW, OLD, D.segments, load_prompt("statement_diff_summary", "v3"))
OLD_CHANGED, NEW_CHANGED = summaries.changed_spans(D.segments)
CHANGED = {1: OLD_CHANGED, 2: NEW_CHANGED}
A_GROWTH, A_RISK, A_ATTENTIVE, A_RATE = (0, 73), (318, 374), (375, 449), (451, 580)     # changed in the old statement
B_GROWTH, B_RISKS, B_RATE = (0, 81), (326, 456), (458, 654)                             # changed in the new one


def old(span):
    s, e = span
    return {"type": "text", "text": FOMC_A[s:e], "citations": [cite(FOMC_A, s, e, 0)]}


def new(span):
    s, e = span
    return {"type": "text", "text": FOMC_B[s:e], "citations": [cite(FOMC_B, s, e, 1)]}


def own(text):
    return {"type": "text", "text": text}


def diff_check(blocks, task="statement_diff_summary", drop_copies=True):
    return check(task, reply(blocks), REQ.sources, changed=CHANGED, drop_copies=drop_copies)


def test_the_spans_are_the_changed_sentences():
    assert {A_GROWTH, A_RISK, A_ATTENTIVE, A_RATE} <= set(OLD_CHANGED)
    assert {B_GROWTH, B_RISKS, B_RATE} <= set(NEW_CHANGED)


def test_a_copy_after_the_models_sentence_comes_out_and_its_citations_stay():
    """The front page's finding (Review 05, finding 2; dev's output 2433 under v3): the bullet repeated both cited
    sentences, word for word, after its own sentence."""
    blocks = [own('- Growth of economic activity changed from "has moderated" to "has moderated further". '),
              old(A_GROWTH), own(" ... "), new(B_GROWTH),
              own('\n- The policy decision changed from "maintain" to "lower" the target range. '),
              old(A_RATE), own(" "), new(B_RATE)]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [
        'Growth of economic activity changed from "has moderated" to "has moderated further".',
        'The policy decision changed from "maintain" to "lower" the target range.']
    assert [[(x.document_id, x.start, x.end) for x in b.citations] for b in c.kept] == [
        [(1, *A_GROWTH), (2, *B_GROWTH)], [(1, *A_RATE), (2, *B_RATE)]]
    assert c.copies == {"removed": 4}
    doc, body, cites = content(c, attempt=1, reply=reply([]))
    assert doc["copies"] == {"removed": 4} and len(cites) == 4 and "\x01" not in body and "\x02" not in str(doc)
    # The judge's measure finds no copied stretch outside quotation marks any more.
    passages = [FOMC_A[slice(*A_GROWTH)], FOMC_B[slice(*B_GROWTH)], FOMC_A[slice(*A_RATE)], FOMC_B[slice(*B_RATE)]]
    assert all(diff_quotes.unquoted_copies(b.text, passages) == [] for b in c.kept)


def test_a_copy_that_starts_the_bullet_or_spans_two_citations_comes_out_too():
    two = {"type": "text", "text": FOMC_A[A_RISK[0]:A_ATTENTIVE[1]],
           "citations": [cite(FOMC_A, *A_RISK, 0), cite(FOMC_A, *A_ATTENTIVE, 0)]}
    blocks = [own("- "), two, own(" The new statement drops the sentence on uncertainty and adds a judgment on risks."),
              own("\n- The decision changed from holding the range to lowering it. "), new(B_RATE)]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [
        "The new statement drops the sentence on uncertainty and adds a judgment on risks.",
        "The decision changed from holding the range to lowering it."]
    assert [len(b.citations) for b in c.kept] == [2, 1] and c.copies == {"removed": 2}


def test_a_copy_inside_the_models_sentence_goes_in_quotation_marks():
    """Dev's output 2436 under v3: "…with the pace now described as "moderate" in <the cited sentence>"."""
    blocks = [own("- The new statement adds a judgment on risks: "), new(B_RISKS),
              own("\n- The statement now reads "), new(B_GROWTH), own(" in place of the earlier wording.")]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [
        f'The new statement adds a judgment on risks: "{FOMC_B[slice(*B_RISKS)]}"',
        f'The statement now reads "{FOMC_B[slice(*B_GROWTH)]}" in place of the earlier wording.']
    assert c.copies == {"quoted": 2}


def test_a_bullet_left_without_a_sentence_of_its_own_keeps_its_copies_in_quotation_marks():
    blocks = [own("- "), new(B_GROWTH), own("\n- Revised: "), old(A_RATE), own(" ... "), new(B_RATE)]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [
        f'"{FOMC_B[slice(*B_GROWTH)]}"',
        f'Revised: "{FOMC_A[slice(*A_RATE)]}" ... "{FOMC_B[slice(*B_RATE)]}"']
    assert c.copies == {"quoted": 3}


def test_what_isnt_a_copy_is_never_touched():
    """A copy already in quotation marks, a cited block of fewer than five words, and a block with words of the model's own
    (the fakes' "Added: <sentence>") keep their text; so does every other task's bullet."""
    s, e = B_GROWTH
    short = {"type": "text", "text": "moderated further.", "citations": [cite(FOMC_B, s + 63, e, 1)]}
    mixed = {"type": "text", "text": "Added: " + FOMC_B[s:e], "citations": [cite(FOMC_B, s, e, 1)]}
    blocks = [own('- The statement now says "'), new(B_RISKS), own('"'),
              own("\n- Growth has "), short, own("\n- "), mixed]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [f'The statement now says "{FOMC_B[slice(*B_RISKS)]}"',
                                        "Growth has moderated further.", "Added: " + FOMC_B[s:e]]
    assert not c.copies and "copies" not in content(c, attempt=1, reply=reply([]))[0]
    # doc_summary: the same shape of reply is checked exactly as before.
    doc_req = summaries.doc_summary_request(NEW, load_prompt("doc_summary"))
    blocks = [own("- Growth of economic activity slowed further. "),
              {"type": "text", "text": FOMC_B[s:e], "citations": [cite(FOMC_B, s, e, 0)]},
              own("\n- The Committee lowered the target range. "),
              {"type": "text", "text": FOMC_B[slice(*B_RATE)], "citations": [cite(FOMC_B, *B_RATE, 0)]},
              own("\n- Risks to employment have risen. "),
              {"type": "text", "text": FOMC_B[slice(*B_RISKS)], "citations": [cite(FOMC_B, *B_RISKS, 0)]}]
    d = check("doc_summary", reply(blocks), doc_req.sources, drop_copies=True)
    assert d.ok and d.kept[0].text == "Growth of economic activity slowed further. " + FOMC_B[s:e] and not d.copies


def test_the_checks_run_on_the_final_text():
    """Removing a copy never hides a figure of the model's own: the figures check still reads it, and the bullet is
    dropped as before (the retry's note quotes the final text, with no marker in it)."""
    blocks = [own("- The range was lowered by 3/8 percentage point. "), new(B_RATE),
              own("\n- Growth of economic activity changed. "), old(A_GROWTH), own(" "), new(B_GROWTH)]
    c = diff_check(blocks)
    assert not c.ok
    assert c.bullets[0].text == "The range was lowered by 3/8 percentage point."
    assert c.bullets[0].problem == "numbers not in its cited passages: 3/8" and c.bullets[1].problem is None
    assert "\x01" not in c.note() and "\x02" not in c.note()
    # A stray line holding a copy is reported without our markers.
    stray = diff_check([own("- Growth of economic activity changed, the new statement says. "), new(B_GROWTH),
                        own("\n"), old(A_GROWTH), own("\n- The decision changed. "), new(B_RATE)])
    assert any(p.startswith("text outside the bullets") for p in stray.problems)
    assert "\x01" not in str(stray.problems)


def test_a_reply_holding_our_markers_is_left_as_it_is():
    parts = [TextPart("- Odd \x01 text. ", ()), TextPart(FOMC_B[slice(*B_GROWTH)], ())]
    assert mark_copies(parts) is parts


FRONT_PAGE = [own('- Growth of economic activity changed from "has moderated" to "has moderated further". '),
              old(A_GROWTH), own(" ... "), new(B_GROWTH),
              own('\n- The policy decision changed from "maintain" to "lower" the target range. '), new(B_RATE)]


def test_the_rule_applies_from_v4_and_a_pinned_older_reply_is_checked_as_before():
    """`ai_batch.Work.check` asks for the rule only for a v4 (or later) request; a target pinned to v1, v2 or v3 keeps
    its copies, exactly as before this change. v5 (the current version) and v4 are checked the same way."""
    w = Work("statement_diff_summary", NEW, OLD, D.segments)
    texts = {}
    for version in ("v1", "v2", "v3", "v4", "v5"):
        req = w.request(load_prompt("statement_diff_summary", version))
        c = w.check(req, reply(FRONT_PAGE))
        assert c.ok, (version, c.reasons())
        texts[version] = ([b.text for b in c.kept], dict(c.copies))
    assert texts["v4"] == (['Growth of economic activity changed from "has moderated" to "has moderated further".',
                            'The policy decision changed from "maintain" to "lower" the target range.'], {"removed": 3})
    assert texts["v5"] == texts["v4"]
    plain = check("statement_diff_summary", reply(FRONT_PAGE), REQ.sources, changed=CHANGED)
    assert texts["v1"] == texts["v2"] == texts["v3"] == ([b.text for b in plain.kept], {})
    assert texts["v1"][0][0].endswith(FOMC_A[slice(*A_GROWTH)] + " ... " + FOMC_B[slice(*B_GROWTH)])


def test_a_shortened_copy_ending_in_the_models_full_stop_is_still_a_copy():
    """Dev's output 2441 under v3: the cited block stops part-way through its passage and ends with a full stop the passage
    doesn't have there ("…in several parts of the world." from "…world, especially …")."""
    s, _ = A_GUIDANCE = (581, 763)
    assert A_GUIDANCE in OLD_CHANGED
    short = "In considering the extent and timing of additional adjustments to the target range."
    assert FOMC_A[s:s + len(short) - 1] == short[:-1] and FOMC_A[s + len(short) - 1] == ","
    blocks = [own('- The guidance drops "the extent and timing of" from its first clause. '),
              {"type": "text", "text": short, "citations": [cite(FOMC_A, *A_GUIDANCE, 0)]},
              own("\n- Growth of economic activity changed. "), new(B_GROWTH)]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == ['The guidance drops "the extent and timing of" from its first clause.',
                                        "Growth of economic activity changed."]
    assert c.copies == {"removed": 2}


def test_copies_in_their_own_parentheses_go_with_them():
    """Dev's output 2445 (round 3, v4): "…to "been little changed in recent months" (<copy> / <copy>)." The pair may come
    as one cited block or as two blocks with the separator between them."""
    one = {"type": "text", "text": "(" + FOMC_A[slice(*A_GROWTH)] + " / " + FOMC_B[slice(*B_GROWTH)] + ").",
           "citations": [cite(FOMC_A, *A_GROWTH, 0), cite(FOMC_B, *B_GROWTH, 1)]}
    blocks = [own('- Growth of economic activity changed from "has moderated" to "has moderated further" '), one,
              own('\n- The policy decision changed from "maintain" to "lower" the target range ('),
              old(A_RATE), own(" / "), new(B_RATE), own(").")]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [
        'Growth of economic activity changed from "has moderated" to "has moderated further".',
        'The policy decision changed from "maintain" to "lower" the target range.']
    assert [len(b.citations) for b in c.kept] == [2, 2] and c.copies == {"removed": 3}


def test_a_copy_after_a_word_that_closes_the_clause_comes_out_and_one_after_an_open_word_is_quoted():
    """Dev's output 2452 (round 3, v4): "…in the previous resolution <copy>, and is now described as …" and "…was added
    <copy>" read on without the copy (the copy's full stop stays); "…described as "moderate" in <copy>" doesn't."""
    blocks = [own('- Growth was described as having "moderated" in the previous statement '), old(A_GROWTH),
              own(', and is now described as having "moderated further" '), new(B_GROWTH),
              own("\n- A sentence on the risks to employment was added "), new(B_RISKS),
              own('\n- The decision was described as "lower" in '), new(B_RATE)]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [
        ('Growth was described as having "moderated" in the previous statement, and is now described as having '
         '"moderated further".'),
        "A sentence on the risks to employment was added.",
        f'The decision was described as "lower" in "{FOMC_B[slice(*B_RATE)]}"']
    assert c.copies == {"removed": 3, "quoted": 1}


def test_a_joined_copy_inside_the_models_sentence_is_quoted_piece_by_piece():
    joined = {"type": "text", "text": FOMC_A[slice(*A_GROWTH)] + " / " + FOMC_B[slice(*B_GROWTH)],
              "citations": [cite(FOMC_A, *A_GROWTH, 0), cite(FOMC_B, *B_GROWTH, 1)]}
    blocks = [own("- The growth sentence now reads "), joined, own("\n- The decision changed. "), new(B_RATE)]
    c = diff_check(blocks)
    assert c.ok, c.reasons()
    assert c.kept[0].text == (f'The growth sentence now reads "{FOMC_A[slice(*A_GROWTH)]}" / '
                              f'"{FOMC_B[slice(*B_GROWTH)]}"')
    assert c.copies == {"quoted": 1, "removed": 1}
