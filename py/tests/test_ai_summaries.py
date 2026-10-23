"""Checking a cited summary before it's stored (Prompt 01 B7; Spec 01 §3.2-§3.3; Architecture 03 §1.2): bullets from
the reply's text blocks, citations mapped into our text (offsets, else search, else not evidence), the numbers rule,
the diff summary's changed-sentence rule, the count, and what a published or rejected output holds."""
from datetime import date

from ai_fakes import bullets, cite, good_summary, message
from anthropic.types import Message
from policy_docs import FOMC_A, FOMC_B

from ft.ai import summaries
from ft.ai.client import Reply, Usage, load_prompt
from ft.ai.summaries import Doc, check, content, retry_note, split_bullets
from ft.engines.document.diff import diff_texts
from ft.engines.document.text import sentence_spans

OLD = Doc(1, "fomc_statement", "FOMC statement", "FOMC statement, 16 Sep 2026", "Fed", date(2026, 9, 16), FOMC_A)
NEW = Doc(2, "fomc_statement", "FOMC statement", "FOMC statement, 28 Oct 2026", "Fed", date(2026, 10, 28), FOMC_B)


def reply(blocks, stop="end_turn") -> Reply:
    m = Message.model_validate(message(blocks, stop=stop))
    return Reply(m.model, m.id, m.stop_reason, Usage.of(m.usage), 0, list(m.content))


def sent(text: str, k: int) -> tuple[int, int]:
    return sentence_spans(text)[k]


def summary_request(doc=NEW):
    return summaries.doc_summary_request(doc, load_prompt("doc_summary"))


def test_bullets_come_from_list_lines_with_their_blocks_citations():
    r = reply(bullets([("First point", [cite(FOMC_B, 0, 10, 0)]), ("Second point", [])],
                      preamble="Here is the summary:\n\n") + [{"type": "text", "text": "\n  continued here"}])
    found, stray = split_bullets(r.parts())
    assert [b for b, _ in found] == ["First point", "Second point"]   # the preamble isn't a bullet
    assert [len(c) for _, c in found] == [1, 0]
    assert stray == ["continued here"]                                 # one line per bullet: the rest is stray


def test_a_good_summary_passes_with_every_citation_on_its_offsets():
    req = summary_request()
    c = check("doc_summary", reply(good_summary([(NEW.title, FOMC_B)])), req.sources)
    assert c.ok and len(c.kept) == 3 and c.mapping == {"offsets": 3}
    s, e = sent(FOMC_B, 0)
    assert (c.kept[0].citations[0].start, c.kept[0].citations[0].end) == (s, e)


def test_wrong_offsets_fall_back_to_finding_the_quote_once():
    s, e = sent(FOMC_B, 0)
    bad = cite(FOMC_B, s, e, 0)
    bad["start_char_index"] += 4
    bad["end_char_index"] += 4                                      # cited_text stays the real sentence
    c = check("doc_summary", reply(bullets([("Point " + x, [bad]) for x in "ABC"])), summary_request().sources)
    assert c.ok and c.mapping == {"search": 3} and c.kept[0].citations[0].start == s


def test_a_quote_not_in_our_text_drops_its_bullet():
    fake = {"type": "char_location", "cited_text": "The Committee raised rates sharply.", "document_index": 0,
            "start_char_index": 0, "end_char_index": 35}
    good = good_summary([(NEW.title, FOMC_B)])
    blocks = good + [{"type": "text", "text": "\n- "}, {"type": "text", "text": "Invented point", "citations": [fake]}]
    c = check("doc_summary", reply(blocks), summary_request().sources)
    assert not c.ok and [b.problem for b in c.dropped] == ["no verified citation"] and c.mapping["failed"] == 1
    assert c.reasons() == ["bullet 4: no verified citation"]


def test_a_number_not_in_the_cited_passage_drops_the_bullet():
    """'No number that isn't in a cited passage' (Spec 01 §3.3): '25 basis points' isn't how the statement says it."""
    s, e = next((s, e) for s, e in sentence_spans(FOMC_B) if "1/4" in FOMC_B[s:e])
    items = [("The Committee lowered the target range by 25 basis points.", [cite(FOMC_B, s, e, 0)]),
             ("The Committee lowered the target range by 1/4 percentage point.", [cite(FOMC_B, s, e, 0)]),
             ("Inflation remains somewhat elevated.", [cite(FOMC_B, *sent(FOMC_B, 2), 0)])]
    c = check("doc_summary", reply(bullets(items)), summary_request().sources)
    assert [b.problem for b in c.bullets] == ["numbers not in its cited passages: 25", None, None]
    assert "25" in retry_note(c) and "bullet 1" in retry_note(c)


def test_the_count_must_be_in_range():
    items = [(FOMC_B[s:e], [cite(FOMC_B, s, e, 0)]) for s, e in sentence_spans(FOMC_B)[:2]]
    c = check("doc_summary", reply(bullets(items)), summary_request().sources)
    assert c.problems == ["2 bullets, not 3 to 6"] and not c.dropped and not c.ok
    assert check("doc_summary", reply([{"type": "text", "text": "No list at all."}]), summary_request().sources).problems


def test_diff_summary_cites_only_changed_sentences_in_the_right_document():
    d = diff_texts(FOMC_A, FOMC_B)
    req = summaries.diff_summary_request(NEW, OLD, d.segments, load_prompt("statement_diff_summary"))
    assert [s.document_id for s in req.sources] == [1, 2]                # the previous document first
    assert "Revised. Previous:" in req.instruction or "Added:" in req.instruction
    old_changed, new_changed = summaries.changed_spans(d.segments)
    ns, ne = new_changed[0]
    os_, oe = old_changed[0]
    same = next((s, e) for s, e in sentence_spans(FOMC_B) if not any(s < b and e > a for a, b in new_changed))
    items = [("New wording", [cite(FOMC_B, ns, ne, 1)]),               # new text, a changed sentence: counts
             ("Old wording", [cite(FOMC_A, os_, oe, 0)]),              # old text, a changed sentence: counts
             ("Unchanged wording", [cite(FOMC_B, *same, 1)]),          # an unchanged sentence: doesn't
             ("Wrong document", [cite(FOMC_B, ns, ne, 0)])]            # the new sentence claimed in the old text
    changed = {1: old_changed, 2: new_changed}
    c = check("statement_diff_summary", reply(bullets(items)), req.sources, changed=changed)
    assert [b.problem for b in c.bullets][:2] == [None, None]
    assert c.bullets[2].problem == "no verified citation on a changed sentence"
    assert c.bullets[3].problem is not None and c.mapping["unchanged_text"] >= 1


def test_published_content_holds_only_verified_bullets_and_numbered_citations():
    c = check("doc_summary", reply(good_summary([(NEW.title, FOMC_B)])), summary_request().sources)
    doc, body, cites = content(c, attempt=1, reply=reply([]))
    assert [b["citations"] for b in doc["bullets"]] == [[1], [2], [3]] and "dropped" not in doc
    assert [p for p, _ in cites] == ["/bullets/0", "/bullets/1", "/bullets/2"] and body.count("\n") == 2
    bad = check("doc_summary", reply(bullets([("Lonely", [])])), summary_request().sources)
    doc, body, cites = content(bad, attempt=2, reply=reply([]))
    assert doc["bullets"] == [] and doc["dropped"] == [{"text": "Lonely", "problem": "no verified citation"}]
    assert doc["problems"] and cites == [] and body == ""


def test_the_retry_is_a_new_input():
    p = load_prompt("doc_summary")
    first = summaries.doc_summary_request(NEW, p)
    retry = summaries.doc_summary_request(NEW, p, note="bullet 1 had no verified citation.")
    assert first.input_sha256 != retry.input_sha256 and "couldn't be published" in retry.note
    assert first.sources[0].context == "Fed · FOMC statement · released 28 Oct 2026"


# ---------------------------------------------------------------------------------------------- B7 review regressions
def test_figures_are_compared_whole_as_written():
    """'3/4' isn't '3-3/4', 'Q2' isn't 'Q3', '-4' isn't '4' (review of B7, 23 Oct)."""
    from ft.ai.summaries import figures, unsupported_figures
    fed = "lower the target range by 1/4 percentage point to 3-3/4 to 4 percent"
    assert unsupported_figures("cut by 3/4 percentage point", [fed]) == ["3/4"]
    assert unsupported_figures("cut by 1/4 percentage point to 3-3/4 to 4 percent", [fed]) == []
    rbi = "Real GDP growth is projected at 6.7 per cent in Q2 and 7.0 per cent for FY2026-27"
    assert unsupported_figures("6.7 per cent in Q3", [rbi]) == ["q3"]
    assert unsupported_figures("Q2 at 6.7 per cent; 7 per cent for FY2026–27", [rbi]) == []
    assert unsupported_figures("a change of -4 percent", ["inflation at 4 percent"]) == ["-4"]
    assert unsupported_figures("inflation at 4 percent", ["a change of −4 percent"]) == []   # unsigned: either
    assert figures("₹1,23,456 and 5.50 and 0.250") == [("", "123456"), ("", "5.5"), ("", "0.25")]
    assert figures("the COVID-19 period and the 10-year yield") == [("", "19"), ("", "10-year")]


def test_a_closing_remark_after_the_bullets_fails_the_reply():
    blocks = good_summary([(NEW.title, FOMC_B)]) + [
        {"type": "text", "text": "\nThe Committee is clearly turning dovish and will cut again in December."}]
    c = check("doc_summary", reply(blocks), summary_request().sources)
    assert not c.ok and c.problems[0].startswith("text outside the bullets")
    assert all(b.problem is None for b in c.bullets) and "dovish" not in c.bullets[-1].text
    pre = check("doc_summary", reply([{"type": "text", "text": "Here are the points:\n\n"}]
                                     + good_summary([(NEW.title, FOMC_B)])), summary_request().sources)
    assert pre.ok                                                     # a preamble is ignored, never stored


def test_a_blocks_citation_counts_only_for_the_bullet_it_starts_in():
    s, e = sent(FOMC_B, 0)
    blocks = [{"type": "text", "text": "- "},
              {"type": "text", "text": "First point.\n- Second point", "citations": [cite(FOMC_B, s, e, 0)]},
              {"type": "text", "text": "\n- Third point"}]
    found, stray = split_bullets(reply(blocks).parts())
    assert [len(c) for _, c in found] == [1, 0, 0] and stray == []


def test_an_empty_citation_is_no_evidence():
    empty = {"type": "char_location", "cited_text": "", "document_index": 0, "start_char_index": 5,
             "end_char_index": 5}
    items = [(f"Point {x}", [empty]) for x in "ABC"]
    c = check("doc_summary", reply(bullets(items)), summary_request().sources)
    assert [b.problem for b in c.bullets] == ["no verified citation"] * 3 and c.mapping["empty"] == 3
    from ft.ai.citations import map_api_citation
    assert not map_api_citation(FOMC_B, 0, 5, 5, "").verified


def test_the_retry_note_is_its_own_last_block_so_the_prefix_stays_the_same():
    p = load_prompt("doc_summary")
    first = summaries.doc_summary_request(NEW, p).params()["messages"][0]["content"]
    retry = summaries.doc_summary_request(NEW, p, note="bullet 1 had no verified citation.").params()
    blocks = retry["messages"][0]["content"]
    assert blocks[:-1] == first and blocks[-1]["text"].startswith("Your previous answer")
