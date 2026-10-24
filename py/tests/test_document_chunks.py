"""The chunker (Prompt 01 B2): every test document's chunks reproduce its text exactly (the database checks this again
at commit: test_document_store), break at paragraphs, sentences and speaker changes, and carry section and speaker."""
import random

import pytest
from policy_docs import FOMC_B, _paragraph, rbi_minutes_body, rbi_page, rbi_resolution_body, transcript_pages

from ft.engines.document.chunk import MAX_CHARS, MIN_TAIL, TARGET, Chunk, check_chunks, chunk_text, minutes_speaker
from ft.engines.document.text import Block, Extracted, extract_html, pdf_text


def plain(paragraphs: list[str], kinds: list[tuple[str, int]] | None = None) -> Extracted:
    """An Extracted from paragraphs (and optional (kind, level) per block)."""
    blocks, pos = [], 0
    for i, p in enumerate(paragraphs):
        kind, level = kinds[i] if kinds else ("para", 0)
        blocks.append(Block(pos, pos + len(p), kind, level))
        pos += len(p) + 2
    return Extracted("\n\n".join(paragraphs), tuple(blocks), "test-1")


def long_resolution() -> Extracted:
    rng = random.Random(3)
    body = "".join(f"<p><b>Section {k}</b></p>" + "".join(f"<p>{n}. {_paragraph(rng, rng.randint(2, 12))}</p>"
                                                          for n in range(rng.randint(3, 8))) for k in range(1, 9))
    return extract_html(rbi_page(body).encode(), "rbi_press_release")


def documents() -> dict[str, tuple[Extracted, dict]]:
    rng = random.Random(5)
    return {
        "rbi_resolution": (extract_html(rbi_page(rbi_resolution_body()).encode(), "rbi_press_release"), {}),
        "rbi_minutes": (extract_html(rbi_page(rbi_minutes_body()).encode(), "rbi_press_release"),
                        {"root": "Minutes", "speakers": "minutes"}),
        "fed_transcript": (pdf_text(transcript_pages()), {"speakers": "transcript"}),
        "fomc_statement": (plain(FOMC_B.split("\n\n")), {"root": "Statement"}),
        "long_resolution": (long_resolution(), {}),
        "one_huge_paragraph": (plain([_paragraph(rng, 140)]), {}),
        "giant_sentence": (plain([" ".join(f"word{i}" for i in range(1500))]), {}),
        "no_spaces": (plain(["x" * 5000]), {}),
        "multibyte": (plain(["भारतीय रिज़र्व बैंक ने रेपो दर ₹ 📈 बढ़ाई। " * 40] * 6), {}),
        "tiny": (plain(["Hello."]), {}),
    }


@pytest.mark.parametrize("name", list(documents()))
def test_chunks_reproduce_every_test_documents_text_exactly(name):
    doc, kw = documents()[name]
    chunks = chunk_text(doc, **kw)
    assert "".join(c.body for c in chunks) == doc.text
    check_chunks(doc.text, chunks)
    for c in chunks:
        assert c.body == doc.text[c.char_start:c.char_end] and len(c.body) == c.char_end - c.char_start
        assert not c.body[0].isspace()                                   # every chunk starts on text
        assert len(c.body) <= MAX_CHARS + 2                              # + the blank line it carries
    assert chunk_text(doc, **kw) == chunks                               # deterministic


def test_long_documents_land_near_the_target_size():
    for name in ("long_resolution", "one_huge_paragraph"):
        doc, kw = documents()[name]
        sizes = [len(c.body) for c in chunk_text(doc, **kw)]
        assert TARGET * 0.5 < sum(sizes) / len(sizes) <= TARGET + 2, (name, sizes)


def test_a_long_paragraph_breaks_between_sentences_and_a_long_sentence_between_words():
    doc, _ = documents()["one_huge_paragraph"]
    chunks = chunk_text(doc)
    assert len(chunks) > 3
    for c in chunks[1:]:
        before = doc.text[:c.char_start].rstrip()
        assert before.endswith((".", "!", "?", "\"", "'", ")")), before[-30:]
    doc, _ = documents()["giant_sentence"]
    chunks = chunk_text(doc)
    assert len(chunks) > 1 and all(doc.text[c.char_start - 1] == " " for c in chunks[1:])
    doc, _ = documents()["no_spaces"]
    assert [len(c.body) for c in chunk_text(doc)] == [MAX_CHARS, MAX_CHARS, 5000 - 2 * MAX_CHARS]


def test_headings_start_chunks_and_set_the_section_path():
    doc, _ = documents()["rbi_resolution"]
    chunks = chunk_text(doc, root="Resolution")
    starts = {c.char_start for c in chunks}
    headings = [b for b in doc.blocks if b.kind == "heading"]
    assert headings and all(h.start in starts for h in headings)
    by_start = {c.char_start: c for c in chunks}
    assert [by_start[h.start].section for h in headings] == [
        "Resolution > Monetary Policy Decisions", "Resolution > Growth and Inflation Outlook",
        "Resolution > Rationale for Monetary Policy Decisions"]
    assert chunks[0].section == "Resolution"                             # before the first heading: the root alone


def test_headings_in_a_row_stay_together_and_nest_by_level():
    doc = plain(["Part A", "Detail one", "Body one.", "Detail two", "Body two.", "Part B", "Body three."],
                [("heading", 2), ("heading", 3), ("para", 0), ("heading", 3), ("para", 0), ("heading", 2), ("para", 0)])
    chunks = chunk_text(doc)
    assert [(c.body.strip(), c.section) for c in chunks] == [
        ("Part A\n\nDetail one\n\nBody one.", "Part A > Detail one"),
        ("Detail two\n\nBody two.", "Part A > Detail two"),
        ("Part B\n\nBody three.", "Part B")]


def test_minutes_chunks_carry_each_members_statement():
    doc, kw = documents()["rbi_minutes"]
    chunks = chunk_text(doc, **kw)
    member_chunks = [c for c in chunks if c.speaker]
    assert {c.speaker for c in member_chunks} == {"Dr. Nagesh Kumar", "Prof. Ram Singh", "Dr. Poonam Gupta",
                                                   "Shri Indranil Bhattacharyya", "Dr. Rajiv Ranjan", "Shri Sanjay Malhotra"}
    for c in member_chunks:
        assert c.section == f"Minutes > Statement by {c.speaker}"
        assert c.body.count("Statement by") <= 1                         # never two members in one chunk
    assert chunks[0].section.startswith("Minutes > Minutes of the Monetary Policy Committee Meeting")
    assert (chunks[-1].section, chunks[-1].speaker) == ("Minutes > Annex", None)  # the next heading ends the statement
    assert minutes_speaker("Statement by  Shri Sanjay Malhotra ") == "Shri Sanjay Malhotra"
    assert minutes_speaker("Voting on the Resolution") is None


def test_the_rbis_plain_statement_headings_carry_their_speaker_too():
    """The RBI's minutes set "Statement by …" in plain type (the 49 saved, Dec 2018 to Oct 2026), so the extractor reads
    paragraphs: in minutes mode a paragraph that is only "Statement by <member>" starts that member's statement (B6
    part 2). A sentence that merely begins so doesn't."""
    body = _paragraph(random.Random(9), 6)
    doc = plain(["Resolution", "5. The MPC decided to keep the policy repo rate unchanged.", "Statement by Dr. Nagesh Kumar",
                 body, "Statement by M. Rajeshwar Rao", body,
                 "Statement by the Governor on the outlook was read into the record at the end of the meeting.", body])
    chunks = chunk_text(doc, root="Minutes", speakers="minutes")
    assert [(c.speaker, c.section) for c in chunks] == [
        (None, "Minutes"), ("Dr. Nagesh Kumar", "Minutes > Statement by Dr. Nagesh Kumar"),
        ("M. Rajeshwar Rao", "Minutes > Statement by M. Rajeshwar Rao")]
    assert chunks[2].body.count("Statement by") == 2                   # the sentence stays in Rao's statement
    assert chunk_text(doc) == chunk_text(doc, speakers=None)            # outside minutes mode, nothing changes
    assert {c.speaker for c in chunk_text(doc)} == {None}


def test_transcript_chunks_break_at_each_change_of_speaker():
    doc, kw = documents()["fed_transcript"]
    chunks = chunk_text(doc, **kw)
    assert [c.speaker for c in chunks] == ["CHAIR POWELL", "MICHELLE SMITH", "STEVE LIESMAN", "CHAIR POWELL"]
    assert all(c.body.startswith(c.speaker + ".") for c in chunks)
    assert "economic develop-ments" in chunks[0].body                   # an unlabelled paragraph stays with its speaker


def test_a_short_tail_folds_into_the_chunk_before():
    a, b, tail = "A" * 1500 + ".", "B" * 1700 + ".", "C" * 100 + "."
    doc = plain([a, b, tail])                                            # b + tail is over the target: tail alone...
    assert [len(c.body) for c in chunk_text(doc)] == [len(a) + 2, len(b) + 2 + len(tail)]   # ...folds into b
    big = plain(["A" * 1700 + ".", "B" * 1700 + ".", "C" * (MIN_TAIL + 10) + "."])
    assert len(chunk_text(big)) == 3                                     # long enough to stand alone


def test_check_chunks_refuses_gaps_overlaps_and_wrong_bodies():
    text = "One. Two. Three."
    good = [Chunk(0, 0, 5, None, None, "One. "), Chunk(1, 5, 16, None, None, "Two. Three.")]
    check_chunks(text, good)
    for bad in ([good[0]], [good[0], Chunk(1, 6, 16, None, None, "wo. Three.")],
                [Chunk(0, 0, 5, None, None, "One! "), good[1]], [good[1]], []):
        with pytest.raises(ValueError):
            check_chunks(text, bad)
    assert chunk_text(Extracted("", (), "x")) == []
    with pytest.raises(ValueError):
        chunk_text(plain(["x"]), speakers="everyone")
