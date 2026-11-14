"""Turns, sections and speaker roles of an earnings call (job 2B-09; Spec 02B §5.3): the pure rules in
`ft/equity/calls/turns.py` and the chunker's call mode, on scripted calls (invented text, one script per label pattern)
and on the saved transcripts in data/fixtures/equity/calls (the private fixtures repo; those tests skip without it)."""
import glob
from pathlib import Path

import pytest

from ft.engines.document.chunk import chunk_text
from ft.engines.document.text import call_speakers, pdf_text_pages
from ft.equity.calls import pdfs, turns
from ft.jobs.tasks.equity_call_text import inside_one_turn

CALLS = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity" / "calls"
needs_fixtures = pytest.mark.skipif(not (CALLS / "pdf").exists(), reason="the saved files aren't checked out (data/fixtures)")

SPEECH = "Thanks for the question and for joining us today. We see steady demand and healthy margins across the segments."


def pages_of(script, style="colon", preamble=()):
    """Page texts of a scripted call: `script` is [(who, text)], written in one of the label styles."""
    lines = list(preamble)
    for who, text in script:
        if style == "colon":
            lines.append(f"{who}: {text}")
        elif style == "own":
            lines += [who, text]
        elif style == "stamp":
            lines += [f"{who} 00:01:10 – 00:05:20", text]
        elif style == "company":
            lines += [f"{who} - Acme Capital", text]
        elif style == "two":
            lines.append(f"{who}  {text}")
        lines.append("")
    half = len(lines) // 2
    return ["\n".join(lines[:half]), "\n".join(lines[half:])]


def read(pages):
    labels: list = []
    ex, _ = pdf_text_pages(pages, speaker=call_speakers(pages).label, labels=labels, label_raw=True)
    return ex, labels


def analyse(script, style="colon", preamble=()):
    ex, labels = read(pages_of(script, style, preamble))
    return ex, turns.analyse(ex, labels)


FULL = [
    ("Moderator", "Good evening and welcome to the call. Over to Anita Rao for the opening remarks."),
    ("Anita Rao", SPEECH),
    ("Moderator", "Thank you. We will now begin the question and answer session. The first question is from Dev Patel from Acme Capital."),
    ("Dev Patel", "What is the outlook for margins?"),
    ("Anita Rao", SPEECH),
    ("Dev Patel", "Thank you, that is clear."),
    ("Moderator", "The next question is from Mira Shah of Beta Securities."),
    ("Mira Shah", "How is demand in the south?"),
    ("Anita Rao", SPEECH),
    ("Mira Shah", "Thank you for the answer."),
    ("Moderator", "I now hand the conference back to Anita Rao for closing comments."),
    ("Anita Rao", "Thank you all for joining us."),
    ("Moderator", "Thank you. That concludes the conference."),
]


# ---------------------------------------------------------------------------------------------- turns, per label pattern
@pytest.mark.parametrize("style", ["colon", "own", "stamp", "company", "two"])
def test_one_turn_per_speaker_label_in_every_label_pattern(style):
    script = [x for x in FULL if x[0] != "Moderator"] if style == "two" else FULL     # a one-word name has no two-column form
    ex, a = analyse(script, style)
    assert [t.speaker for t in a.turns] == [w for w, _ in script]
    turns.check_turns(ex.text, a.turns)


def test_an_honorific_and_a_role_in_the_label_name_the_same_speaker():
    script = [("Mr. Anita Rao", SPEECH), ("Anita Rao - Chief Executive Officer", SPEECH)]
    _, a = analyse(script)
    assert [t.key for t in a.turns] == ["anita rao", "anita rao"]


def test_turns_are_contiguous_from_the_first_label_to_the_end_and_text_before_it_belongs_to_none():
    ex, a = analyse(FULL, preamble=["Acme Limited earnings conference call", ""])
    assert a.turns[0].char_start > 0 and ex.text[:a.turns[0].char_start].startswith("Acme Limited")
    assert all(x.char_end == y.char_start for x, y in zip(a.turns, a.turns[1:], strict=False))
    assert a.turns[-1].char_end == len(ex.text)
    assert a.marks[0] == (False, None, None)


def test_the_turn_check_names_a_gap_an_overlap_and_a_short_end():
    ex, a = analyse(FULL)
    t = a.turns
    broken = [t[0], turns.Turn(1, t[1].char_start + 1, t[1].char_end, "x", "x", 0, 0, 1), *t[2:]]
    for bad in (broken, [t[0], t[0], *t[2:]], t[:-1]):
        with pytest.raises(ValueError):
            turns.check_turns(ex.text, bad)


def test_label_like_lines_of_a_cover_letter_before_the_call_are_not_turns():
    pre = ["Encl.: As above", "Membership No.: ACS 12345", ""]
    ex, a = analyse(FULL, preamble=pre)
    assert [t.speaker for t in a.turns] == [w for w, _ in FULL]
    assert ex.text.startswith("Encl.")


# ---------------------------------------------------------------------------------------------- sections
def test_sections_run_from_the_first_speaker_to_the_handover_then_qa_then_closing_after_the_handback():
    _, a = analyse(FULL)
    assert [t.section for t in a.turns] == ["opening"] * 2 + ["qa"] * 9 + ["closing"] * 2
    assert a.handover and a.handback


def test_a_call_with_no_handover_is_all_qa_and_says_so():
    script = [("Moderator", "Welcome to the call."), ("Anita Rao", SPEECH), ("Dev Patel", "What is the margin outlook?"),
              ("Anita Rao", SPEECH)]
    _, a = analyse(script)
    assert {t.section for t in a.turns} == {"qa"} and not a.handover and not a.handback


def test_a_welcome_that_only_previews_the_qa_is_not_the_handover():
    script = [("Moderator", "Welcome. After the opening remarks we will take your questions in the question and answer session."),
              ("Anita Rao", SPEECH),
              ("Moderator", "We will now begin the question and answer session."),
              ("Dev Patel", "What is the margin outlook?")]
    _, a = analyse(script)
    assert [t.section for t in a.turns] == ["opening", "opening", "qa", "qa"]


def test_a_handover_with_no_handback_leaves_the_rest_qa():
    _, a = analyse(FULL[:9])
    assert a.handover and not a.handback and a.turns[-1].section == "qa"


# ---------------------------------------------------------------------------------------------- roles
def roles(a):
    return {t.speaker: t.role for t in a.turns}


def test_roles_from_the_moderators_introductions_and_from_speaking_in_the_opening():
    _, a = analyse(FULL)
    assert roles(a) == {"Moderator": "moderator", "Anita Rao": "management", "Dev Patel": "analyst", "Mira Shah": "analyst"}
    assert a.unknown_share == 0 and not a.role_held


def test_the_label_operator_and_a_label_naming_the_moderators_role_are_the_moderator():
    script = [("Operator", "Welcome."), ("Mr. Sam Lee - Moderator, Acme Ltd", "Good evening.")]
    _, a = analyse(script)
    assert [t.role for t in a.turns] == ["moderator", "moderator"] and [t.key for t in a.turns] == ["operator", "moderator"]


def test_roles_from_a_participants_list_when_the_moderator_does_not_introduce_anyone():
    pre = ["MANAGEMENT: MR. RAVI KUMAR – CHIEF EXECUTIVE OFFICER, MR. SAM LEE – CFO", "ANALYSTS: MR. DEV PATEL, MS. MIRA SHAH", ""]
    script = [("Moderator", "Welcome to the call. We will now begin the question and answer session."),
              ("Dev Patel", "What is the outlook?"), ("Ravi Kumar", SPEECH), ("Mira Shah", "And demand?"), ("Sam Lee", SPEECH)]
    _, a = analyse(script, preamble=pre)
    assert roles(a) == {"Moderator": "moderator", "Dev Patel": "analyst", "Ravi Kumar": "management", "Mira Shah": "analyst",
                        "Sam Lee": "management"}


def test_an_executive_title_in_the_label_makes_management_and_a_company_in_it_does_not():
    script = [("Moderator", "We will now begin the question and answer session. The first question is from Dev Patel."),
              ("Dev Patel - Acme Capital", "Margins?"), ("Sunil Rao - Chief Financial Officer", SPEECH), ("Pat Kim - Acme Bank", "More?")]
    _, a = analyse(script)
    assert roles(a) == {"Moderator": "moderator", "Dev Patel": "analyst", "Sunil Rao": "management", "Pat Kim": "unknown"}


def test_a_first_name_in_the_introduction_names_the_one_speaker_with_that_first_name():
    script = [("Moderator", "Welcome. We will now begin the question and answer session. The first question comes from Dev with Acme."),
              ("Dev Patel", "Margins?"), ("Anita Rao", SPEECH)]
    _, a = analyse(script)
    assert roles(a)["Dev Patel"] == "analyst"


def test_a_person_the_welcome_introduces_with_a_title_is_management_even_if_they_only_answer_in_the_qa():
    welcome = ("Welcome. With us today are Mr. Ravi Kumar, CEO of Acme and Ms. Sam Lee - Chief Financial Officer. "
               "Mr. Pat Kim, a guest, is also here. Over to Ravi Kumar.")
    handover = ("We will now begin the question and answer session. The first question is from Dev Patel. "
                "Mr. Lou Ray, Head of Research, is on the line.")
    script = [("Moderator", welcome), ("Ravi Kumar", SPEECH), ("Moderator", handover),
              ("Dev Patel", "Margins?"), ("Sam Lee", SPEECH), ("Pat Kim", "A comment."), ("Lou Ray", "More.")]
    _, a = analyse(script)
    assert roles(a) == {"Moderator": "moderator", "Ravi Kumar": "management", "Dev Patel": "analyst", "Sam Lee": "management",
                        "Pat Kim": "unknown", "Lou Ray": "unknown"}               # no title / named after the hand-over


def test_the_welcome_rule_reads_the_first_moderator_turn_when_no_handover_is_found():
    script = [("Moderator", "Welcome. Joining us are Mr. Ravi Kumar, MD and CEO."), ("Dev Patel", "Margins?"),
              ("Ravi Kumar", SPEECH)]
    _, a = analyse(script)
    assert roles(a)["Ravi Kumar"] == "management" and not a.handover


def test_a_speaker_who_matches_two_roles_is_unknown():
    script = [("Moderator", "Welcome. Over to Anita Rao."), ("Anita Rao", SPEECH),
              ("Moderator", "We will now begin the question and answer session. The first question is from Anita Rao from Acme."),
              ("Anita Rao", "A question."), ("Dev Patel", "Another.")]
    _, a = analyse(script)
    assert roles(a)["Anita Rao"] == "unknown" and a.both_roles == ["anita rao"]


def test_unknown_role_words_above_ten_percent_hold_the_call_and_at_ten_percent_do_not():
    def call(pad):
        script = [("Moderator", "Welcome. Over to Anita Rao."), ("Anita Rao", " ".join([SPEECH] * 10)),
                  ("Moderator", "We will now begin the question and answer session. The first question is from Dev Patel."),
                  ("Dev Patel", "Margins?"), ("Pat Kim", " ".join(["More words."] * pad))]
        return analyse(script)[1]
    small, big = call(5), call(60)
    assert not small.role_held and 0 < small.unknown_share < 0.10
    assert big.role_held and big.unknown_speakers == ["Pat Kim"] and big.unknown_share > 0.10


def test_a_call_with_no_turns_cannot_have_its_roles_read():
    ex, labels = read(["Just a page of ordinary text with no speaker labels at all.", ""])
    a = turns.analyse(ex, labels)
    assert a.turns == [] and a.unknown_share == 1.0 and a.role_held


# ---------------------------------------------------------------------------------------------- chunks
def test_every_chunk_lies_inside_one_turn_and_carries_its_turns_speaker_and_section():
    long = " ".join([SPEECH] * 40)
    script = [(w, long if w == "Anita Rao" else t) for w, t in FULL]
    ex, a = analyse(script, preamble=["Acme Limited earnings call", ""])
    chunks = chunk_text(ex, speakers="call", marks=a.marks)
    assert "".join(c.body for c in chunks) == ex.text
    assert inside_one_turn(chunks, a)
    assert len(chunks) > len(a.turns)                         # the long turns were cut inside themselves
    for c in chunks:
        owner = next((t for t in a.turns if t.char_start <= c.char_start < t.char_end), None)
        if owner is None:
            assert c.speaker is None and c.section is None    # the text before the first label
        else:
            assert (c.speaker, c.section) == (owner.speaker, turns.SECTION_NAMES[owner.section])
    assert {c.section for c in chunks} == {None, "Opening remarks", "Q&A", "Closing remarks"}


def test_two_turns_by_the_same_speaker_in_a_row_are_two_chunks():
    script = [("Moderator", "Welcome."), ("Anita Rao", SPEECH), ("Anita Rao", SPEECH)]
    ex, a = analyse(script)
    chunks = chunk_text(ex, speakers="call", marks=a.marks)
    assert [c.speaker for c in chunks] == ["Moderator", "Anita Rao", "Anita Rao"] and inside_one_turn(chunks, a)


def test_the_call_mode_needs_one_mark_per_block():
    ex, _ = analyse(FULL)
    with pytest.raises(ValueError):
        chunk_text(ex, speakers="call", marks=[])


def test_the_chunk_check_finds_a_chunk_across_a_turn():
    ex, a = analyse(FULL)
    plain = chunk_text(ex, target=100_000, max_chars=200_000, min_tail=0)       # no marks: one chunk over every turn
    assert not inside_one_turn(plain, a)


# ---------------------------------------------------------------------------------------------- the saved transcripts
def full_pdfs():
    out = []
    for f in sorted(glob.glob(str(CALLS / "pdf" / "*" / "*.pdf"))):
        info = pdfs.read_pdf(Path(f).read_bytes())
        if pdfs.classify_pdf(info) == "full":
            out.append(pytest.param(Path(f).name, info, id=Path(f).name[:24]))
    return out


@needs_fixtures
@pytest.mark.parametrize("name,info", full_pdfs() if (CALLS / "pdf").exists() else [])
def test_every_saved_transcript_has_contiguous_turns_and_chunks_inside_them(name, info):
    ct = pdfs.call_text(info)
    a = turns.analyse(ct.extracted, ct.labels)
    turns.check_turns(ct.extracted.text, a.turns)
    # #542: the chunker reads the analysis's blocks (cut where an inline label opens a turn); the text is the same string,
    # and a transcript without an inline label keeps its own blocks
    assert a.doc.text == ct.extracted.text
    named = turns.split_turns(ct.extracted, ct.labels)
    assert (a.doc is ct.extracted) == (not turns.inline_labels(ct.extracted, named))
    chunks = chunk_text(a.doc, speakers="call", marks=a.marks)
    assert "".join(c.body for c in chunks) == ct.extracted.text and inside_one_turn(chunks, a)
    assert a.turns and 0 <= a.unknown_share <= 1
    assert {t.section for t in a.turns} <= {"qa"} or a.handover


@needs_fixtures
def test_saved_transcripts_with_a_moderator_hand_over_and_hand_back_read_their_sections_and_roles():
    def run(fragment):
        f = next(p for p in glob.glob(str(CALLS / "pdf" / "*" / "*.pdf")) if fragment in p)
        ct = pdfs.call_text(pdfs.read_pdf(Path(f).read_bytes()))
        return turns.analyse(ct.extracted, ct.labels)
    cipla = run("CIPLA")
    assert cipla.handover and cipla.handback and not cipla.role_held
    assert {t.role for t in cipla.turns} >= {"moderator", "management", "analyst"}
    assert cipla.turns[0].section == "opening" and cipla.turns[-1].section == "closing"
    hul = run("HINDUNILVR")
    assert hul.handover and not hul.handback and not hul.role_held
    assert not run("kavinavora").handover and run("kavinavora").role_held             # no moderator: roles can't be read


def test_role_hold_share_is_ten_percent():
    # api.earnings_calls.roles_partial (migration 0029) hardcodes `unknown_role_share > 0.10`; change both together.
    assert turns.ROLE_HOLD_SHARE == 0.10


# ---------------------------------------------------------------------------------------------- inline generic labels (#542)
WELCOME = "Moderator: Good evening and welcome. We will now begin the question and answer session. The first question is from Dev Patel."
ANSWER = "Margins should stay near the guided range this year, and we expect volume growth in the low teens."


def call_of(lines, preamble=()):
    """A call written line by line (a blank line ends a paragraph), read as 2B-07 reads it, and analysed."""
    pages = ["\n".join([*preamble, *lines]), ""]
    ex, labels = read(pages)
    return ex, labels, turns.analyse(ex, labels)


def spoken(ex, a):
    return [(t.speaker, t.role, ex.text[t.char_start:t.char_end].strip()) for t in a.turns]


def test_an_inline_management_label_inside_a_paragraph_opens_a_management_turn_at_its_offset_and_the_text_is_unchanged():
    lines = [WELCOME, "", "Dev Patel: What is the margin outlook for the year?", f"Management: {ANSWER}", "",
             "Dev Patel: Thank you."]
    ex, _, a = call_of(lines)
    # the text is what the extractor made: the label line was joined into the analyst's paragraph, no break added
    assert ex.text == read(["\n".join(lines), ""])[0].text
    assert f"year? Management: {ANSWER}" in ex.text and len(ex.blocks) == 3
    assert spoken(ex, a) == [
        ("Moderator", "moderator", WELCOME),
        ("Dev Patel", "analyst", "Dev Patel: What is the margin outlook for the year?"),
        ("Management", "management", f"Management: {ANSWER}"),
        ("Dev Patel", "analyst", "Dev Patel: Thank you.")]
    at = ex.text.index("Management:")
    assert a.turns[2].char_start == at and a.turns[1].char_end == at
    turns.check_turns(ex.text, a.turns)
    # the chunker reads the paragraph cut at the label: each chunk lies inside one turn and the chunks are the text
    assert a.doc.text == ex.text and len(a.doc.blocks) == len(ex.blocks) + 1 and len(a.marks) == len(a.doc.blocks)
    chunks = chunk_text(a.doc, speakers="call", marks=a.marks)
    assert "".join(c.body for c in chunks) == ex.text and inside_one_turn(chunks, a)
    assert [c.speaker for c in chunks] == ["Moderator", "Dev Patel", "Management", "Dev Patel"]
    assert chunks[2].char_start == at and chunks[1].body.endswith("year? ")      # the space before the label stays behind


def test_an_inline_label_at_a_paragraphs_start_opens_a_turn_there_and_cuts_no_paragraph():
    lines = [WELCOME, "", "Dev Patel: What is the margin outlook?", "", "Management:", "", ANSWER, "", "Dev Patel: Thanks."]
    ex, _, a = call_of(lines)
    assert [(s, r) for s, r, _ in spoken(ex, a)] == [("Moderator", "moderator"), ("Dev Patel", "analyst"),
                                                    ("Management", "management"), ("Dev Patel", "analyst")]
    assert [b.start for b in a.doc.blocks] == [b.start for b in ex.blocks]
    assert a.turns[2].char_start == ex.blocks[2].start and a.turns[2].block_start == 2 and a.turns[2].block_end == 4


def test_a_bulleted_generic_label_opens_its_turn_at_the_bullet():
    lines = [WELCOME, "", "- Ravi Kumar - Chief Executive Officer:", "", f"- {ANSWER}", "", "- Analyst:", "",
             "- What about demand in the south?"]
    ex, _, a = call_of(lines)
    assert [(s, r) for s, r, _ in spoken(ex, a)] == [("Moderator", "moderator"), ("Ravi Kumar", "management"),
                                                    ("Analyst", "analyst")]
    assert ex.text[a.turns[2].char_start:].startswith("- Analyst:")


def test_a_participants_list_before_the_call_stays_a_header_and_still_names_the_roles():
    pre = ["Acme Limited Q2 FY27 Earnings Conference Call", "", "Management: Mr. Ravi Kumar – Chief Executive Officer",
           "Mr. Sam Lee – Chief Financial Officer", "Analysts: Mr. Dev Patel, Ms. Mira Shah", ""]
    lines = [WELCOME, "", "Dev Patel: Margins?", "", f"Ravi Kumar: {ANSWER}", "", "Mira Shah: And demand?", "",
             f"Sam Lee: {ANSWER}"]
    ex, labels, a = call_of(lines, preamble=pre)
    assert [t.speaker for t in a.turns] == ["Moderator", "Dev Patel", "Ravi Kumar", "Mira Shah", "Sam Lee"]
    assert roles(a) == {"Moderator": "moderator", "Dev Patel": "analyst", "Ravi Kumar": "management", "Mira Shah": "analyst",
                        "Sam Lee": "management"}
    assert a.doc is ex and turns.inline_labels(ex, turns.split_turns(ex, labels)) == []


def test_a_participants_list_after_the_first_turn_but_before_the_moderators_first_turn_is_not_a_turn():
    pre = ["Ref: Acme Limited earnings call", "", "Subject: Transcript", ""]
    # the cover letter's signatory speaks again in the call, so the call's turns start at the letter (`_drop_spurious`)
    lines = ["Ravi Kumar: Please find the transcript of the call below.", "",
             "Management: Mr. Ravi Kumar – Chief Executive Officer", "", WELCOME, "", "Dev Patel: Margins?", "",
             f"Ravi Kumar: {ANSWER}", "", "Dev Patel: Thanks.", "", f"Ravi Kumar: {ANSWER}"]
    _, _, a = call_of(lines, preamble=pre)
    assert a.turns[0].speaker == "Ravi Kumar" and a.turns[1].speaker == "Moderator"
    assert "Management" not in [t.speaker for t in a.turns]


def test_a_call_without_an_inline_label_keeps_its_turns_blocks_and_chunks_exactly():
    ex, a = analyse(FULL, preamble=["Acme Limited earnings call", ""])
    labels = read(pages_of(FULL, preamble=["Acme Limited earnings call", ""]))[1]
    assert a.doc is ex
    assert [(t.char_start, t.char_end, t.speaker, t.key, t.block_start, t.block_end, t.words) for t in a.turns] == \
        [(t.char_start, t.char_end, t.speaker, t.key, t.block_start, t.block_end, t.words) for t in turns.split_turns(ex, labels)]
    assert chunk_text(a.doc, speakers="call", marks=a.marks) == chunk_text(ex, speakers="call", marks=a.marks)


def test_management_in_a_named_label_is_part_of_that_label_even_in_a_call_that_labels_its_answers_management():
    lines = [WELCOME, ""]
    for _ in range(3):                                             # the call's style: "Management:" after the questions
        lines += ["Dev Patel: What is the margin outlook?", f"Management: {ANSWER}", ""]
    lines += ["Mr. Chirag Shah - New Asset Management:", "", "Hi, sir. What about the capex plan?", "",
              f"Ravi Kumar - Chief Financial Officer: {ANSWER}"]
    _, _, a = call_of(lines)
    assert [t.speaker for t in a.turns] == ["Moderator", *["Dev Patel", "Management"] * 3, "Chirag Shah", "Ravi Kumar"]


def test_a_label_repeated_with_nothing_said_between_opens_no_second_turn():
    lines = ["Moderator: Moderator: Good evening and welcome. We will now begin the question and answer session.", ""]
    for _ in range(3):                                             # a call that labels its answers "Management:"
        lines += ["Dev Patel: What is the margin outlook?", f"Management: {ANSWER}", ""]
    lines += ["Dev Patel: And demand?", f"Management: Management: {ANSWER}"]
    ex, _, a = call_of(lines)
    assert [t.speaker for t in a.turns] == ["Moderator", *["Dev Patel", "Management"] * 4]
    assert ex.text[a.turns[-1].char_start:].startswith("Management: Management:")


def test_inline_analyst_and_participant_labels_are_analysts_and_an_inline_moderator_introduces_the_next_one():
    lines = [WELCOME, "", f"Ravi Kumar - Chief Executive Officer: {ANSWER} Participant: Sir, what is the debt plan?",
             f"Ravi Kumar - Chief Executive Officer: {ANSWER} Moderator: The next question is from Mira Shah.", "",
             "Mira Shah: And demand?", f"Management: {ANSWER} Analyst: Thank you."]
    ex, _, a = call_of(lines)
    assert [(s, r) for s, r, _ in spoken(ex, a)] == [
        ("Moderator", "moderator"), ("Ravi Kumar", "management"), ("Participant", "analyst"), ("Ravi Kumar", "management"),
        ("Moderator", "moderator"), ("Mira Shah", "analyst"), ("Management", "management"), ("Analyst", "analyst")]
    chunks = chunk_text(a.doc, speakers="call", marks=a.marks)
    assert "".join(c.body for c in chunks) == ex.text and inside_one_turn(chunks, a)


def test_a_generic_label_never_merges_with_a_named_speaker_and_its_role_stays_its_words():
    # "Management" answers only in the Q&A and the moderator introduces no one by that name: still management, never unknown
    lines = ["Moderator: Welcome. We will now begin the question and answer session. The first question is from Management Kumar.",
             "", "Management Kumar: Is that me? A question on margins.", f"Management: {ANSWER}"]
    _, _, a = call_of(lines)
    assert [(t.speaker, t.key, t.role) for t in a.turns] == [
        ("Moderator", "moderator", "moderator"), ("Management Kumar", "management kumar", "analyst"),
        ("Management", "management", "management")]
    assert a.unknown_speakers == [] and a.both_roles == []
    assert not turns.same_person("management", "management kumar")


def test_after_a_comma_or_a_number_a_label_counts_only_in_a_call_that_writes_it_so_and_never_after_an_article():
    def call(n_strict, tail):
        body = [WELCOME, ""]
        for _ in range(n_strict):
            body += ["Dev Patel: What is the margin outlook?", f"Management: {ANSWER}", ""]
        body += [f"Dev Patel: The figure for 2026 was {tail}", f"Management: {ANSWER}"]
        _, _, a = call_of(body)
        return [t.speaker for t in a.turns].count("Management") - n_strict
    assert call(2, "20%,") == 0                    # two labels after a sentence: not the call's style
    assert call(3, "20%,") == 1                    # three: the call labels its answers so
    assert call(3, "given by 2030") == 1
    assert call(5, "asked of the") == 0            # "of the Management:" reads as a phrase, never a label


def test_a_lower_case_or_capitalised_word_that_is_not_followed_by_a_colon_is_no_label():
    lines = [WELCOME, "", "Dev Patel: How does the management see it? MANAGEMENT: is the header style, management: too.",
             f"Ravi Kumar - Chief Executive Officer: Our Management team: {ANSWER}"]
    _, _, a = call_of(lines)
    assert [t.speaker for t in a.turns] == ["Moderator", "Dev Patel", "Ravi Kumar"]
