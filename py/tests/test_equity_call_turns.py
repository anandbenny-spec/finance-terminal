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
    chunks = chunk_text(ct.extracted, speakers="call", marks=a.marks)
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
