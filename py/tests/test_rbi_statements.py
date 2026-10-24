"""The Governor's statements and the MPC press-conference transcripts (Prompt 01 B6 part 3; issue #15): the Annual Policy
page's links (rbi_calendar), the three old-site templates (rbi_statements), the speaker labels and the chunker.
Synthetic pages in the real templates' shape (policy_docs); the saved pages, where they're checked out (data/fixtures),
for the 48 statements and 44 transcripts."""
import csv
from datetime import date
from pathlib import Path

import pytest
from policy_docs import (
    rbi_page,
    rbi_speech_page,
    rbi_statement_body,
    rbi_transcript_speech,
    rbi_transcript_view,
    rbi_view_page,
)
from test_policy_calendar import rbi_group, rbi_item
from test_policy_calendar import rbi_page as annual_page

from ft.engines.document.chunk import chunk_text
from ft.engines.document.text import rbi_transcript_speaker
from ft.policy import members, rbi_calendar, rbi_statements
from ft.policy.rbi_statements import StatementError

S = "https://www.rbi.org.in/Scripts/"
PR, VIEW, SPEECH = S + "BS_PressReleaseDisplay.aspx?prid=", S + "bs_viewcontent.aspx?Id=", S + "BS_SpeechesView.aspx?Id="
RESOLUTION = "Resolution of the Monetary Policy Committee (MPC), Reserve Bank of India"
PARTICIPANTS = ["Shri Shaktikanta Das – Governor", "Dr. Michael D. Patra – Deputy Governor"]


def roster(tmp_path: Path) -> members.Roster:
    p = tmp_path / "members.yaml"
    p.write_text("rbi:\n  shaktikanta-das:\n    name: Shaktikanta Das\n    terms:\n"
                 "      - {role: rbi_governor, affiliation: RBI, voter: true, from: 2019-02-05, to: 2024-12-06, source: t}\n"
                 "  sanjay-malhotra:\n    name: Sanjay Malhotra\n    terms:\n"
                 "      - {role: rbi_governor, affiliation: RBI, voter: true, from: 2025-02-05, source: t}\n"
                 "  michael-debabrata-patra:\n    name: Michael Debabrata Patra\n    aliases: [Michael D. Patra]\n"
                 "    terms:\n      - {role: rbi_deputy_governor, affiliation: RBI, voter: true, from: 2020-02-04, "
                 "to: 2024-12-06, source: t}\n")
    return members.load(p)


# ---------------------------------------------------------------------------------------------- links
def test_links_are_canonical_whatever_host_or_case_the_page_wrote():
    assert rbi_statements.doc_url("https://rbi.org.in/scripts/bs_viewcontent.aspx?Id=3636") == VIEW + "3636"
    assert rbi_statements.doc_url("https://www.rbi.org.in/scripts/BS_SpeechesView.aspx?Id=1583") == SPEECH + "1583"
    assert rbi_statements.doc_url("https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63744") == PR + "63744"
    assert rbi_statements.external_key(VIEW + "3636") == "/Scripts/bs_viewcontent.aspx?Id=3636"
    assert rbi_statements.external_key(SPEECH + "1583") == "/Scripts/BS_SpeechesView.aspx?Id=1583"
    assert rbi_statements.template(PR + "1") == "prid"
    for bad in ("https://youtu.be/xD2yQmoHXr4", "https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR1.PDF"):
        with pytest.raises(StatementError):
            rbi_statements.doc_url(bad)


# ---------------------------------------------------------------------------------------------- the Annual Policy page
def test_the_annual_policy_page_gives_each_meeting_its_statement_and_its_transcript():
    page = annual_page(
        rbi_group("Feb 10, 2022", "Bi-monthly Monetary Policy Statement, 2021-2022",       # dated title, 5 days on
                  rbi_item("Edited Transcript of Reserve Bank of India’s Monetary Policy Press Conference: February "
                           "05, 2022", "https://rbi.org.in/scripts/bs_viewcontent.aspx?Id=4084")),
        rbi_group("Feb 05, 2022", "Bi-monthly Monetary Policy Statement, 2021-2022",
                  rbi_item("RBI Governor’s address", "https://youtu.be/x")
                  + rbi_item("Governor’s Statement: February 5, 2022", PR + "53249")
                  + rbi_item("Resolution of the Monetary Policy Committee (MPC) February 3-5, 2022", PR + "53247")),
        rbi_group("Dec 08, 2021", "Bi-monthly Monetary Policy Statement, 2021-2022",
                  rbi_item("Governor’s Statement", PR + "52686") + rbi_item(RESOLUTION, PR + "52687", True)),
        rbi_group("May 05, 2021", "Governor’s Statement",                                   # not a decision's group
                  rbi_item("Governor’s Statement - May 5, 2021", PR + "51526")),
        rbi_group("Apr 05, 2019", "First Bi-monthly Monetary Policy Statement for 2019-20",  # undated: the heading
                  rbi_item("Edited Transcript of Reserve Bank of India’s First Bi-Monthly Monetary Policy Press "
                           "Conference", VIEW + "3661")
                  + rbi_item("Edited Transcript of Reserve Bank of India’s First Bi-Monthly Monetary Policy "
                             "Tele-Conference with Researchers", VIEW + "3662")),
        rbi_group("Apr 04, 2019", "First Bi-monthly Monetary Policy Statement for 2019-20",
                  rbi_item("Governor’s Statement on Framework for Resolution of Stressed Assets", VIEW + "3659")
                  + rbi_item("Statement by Governor - First Bi-monthly Monetary Policy, 2019-20", VIEW + "3660")
                  + rbi_item(RESOLUTION, PR + "46722", True)),
        rbi_group("Feb 07, 2019", "Sixth Bi-monthly Monetary Policy Statement for 2018-19",
                  rbi_item("MPC Press Conference - Governor’s Opening Remarks", PR + "46236")
                  + rbi_item("Statement by Governor - Sixth Bi-monthly Monetary Policy Press Conference for 2018-19",
                             VIEW + "3636") + rbi_item(RESOLUTION, PR + "46235", True)))
    cal = rbi_calendar.calendar([rbi_calendar.parse_page(page)])
    assert {d: link.url for d, link in cal.statements.items()} == {
        date(2022, 2, 5): PR + "53249", date(2021, 12, 8): PR + "52686", date(2019, 4, 4): VIEW + "3660",
        date(2019, 2, 7): VIEW + "3636"}
    assert {d: (link.posted_on, link.url) for d, link in cal.transcripts.items()} == {
        date(2022, 2, 5): (date(2022, 2, 10), "https://rbi.org.in/scripts/bs_viewcontent.aspx?Id=4084"),
        date(2019, 4, 4): (date(2019, 4, 5), VIEW + "3661")}
    assert cal.doc_problems == ()


def test_links_that_fit_no_single_meeting_are_listed_not_guessed():
    page = annual_page(
        rbi_group("Mar 20, 2040", "Bi-monthly Monetary Policy Statement, 2039-40",
                  rbi_item("Edited Transcript of the Reserve Bank of India’s Post-Monetary Policy Press Conference: "
                           "March 17, 2040 (Friday)", SPEECH + "9001")),
        rbi_group("Feb 08, 2040", "Bi-monthly Monetary Policy Statement, 2039-40",
                  rbi_item("Edited Transcript of the Reserve Bank of India’s Post-Monetary Policy Press Conference: "
                           "February 6, 2040", SPEECH + "9000")
                  + rbi_item("Edited Transcript of the Reserve Bank of India’s Post-Monetary Policy Press Conference: "
                             "February 6, 2040 (corrected)", SPEECH + "9002")),
        rbi_group("Feb 06, 2040", "Bi-monthly Monetary Policy Statement, 2039-40",
                  rbi_item("Governor’s Statement: February 6, 2040", PR + "90001")
                  + rbi_item("Governor’s Statement, February 6, 2040", PR + "90002")
                  + rbi_item(RESOLUTION, PR + "90000", True)))
    cal = rbi_calendar.calendar([rbi_calendar.parse_page(page)])
    assert cal.statements == {} and cal.transcripts == {}
    assert len(cal.doc_problems) == 3
    assert "lists 2 Governor's statements" in cal.doc_problems[0]
    assert "names 2040-03-17, not a decision" in cal.doc_problems[1]
    assert "2 transcripts for the decision of 2040-02-06" in cal.doc_problems[2]


def test_titles():
    assert [rbi_calendar.is_governor_statement(t) for t in (
        "Statement by Governor - Sixth Bi-monthly Monetary Policy Press Conference for 2018-19",
        "Governor’s Statement – May 22, 2020", "Governor's Statement, December 4, 2020", "Governor’s Statement",
        "Governor’s Statement on Framework for Resolution of Stressed Assets",
        "MPC Press Conference - Governor’s Opening Remarks", "RBI Governor’s address")] == [
        True, True, True, True, False, False, False]
    assert rbi_calendar.is_transcript("Edited Transcript of Reserve Bank of India’s Fifth Bi-monthly Monetary Policy "
                                      "Press Conference")
    assert not rbi_calendar.is_transcript("Edited Transcript of Reserve Bank of India’s Sixth Bi-Monthly Monetary "
                                          "Policy Tele-Conference with Researchers")
    assert rbi_calendar.title_date("Governor’s Statement: October 07, 2026") == date(2026, 10, 7)
    assert rbi_calendar.title_date("… Press Conference: February 7, 2025 (Friday)") == date(2025, 2, 7)
    assert rbi_calendar.title_date("Statement by Governor - First Bi-monthly Monetary Policy, 2019-20") is None


# ---------------------------------------------------------------------------------------------- statements
def test_a_press_release_statement_is_dated_numbered_and_names_the_mpc():
    page = rbi_page(rbi_statement_body("The Monetary Policy Committee (MPC) met on 5th, 6th and 7th October.",
                                       press_release="2026-2027/1266"),
                    title="Governor’s Statement, October 7, 2026", day="Oct 07, 2026")
    st = rbi_statements.parse(page.encode(), "https://rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63744",
                              "statement")
    assert (st.url, st.title, st.page_date, st.named_date, st.press_release) == (
        PR + "63744", "Governor's Statement, October 7, 2026", date(2026, 10, 7), date(2026, 10, 7), "2026-2027/1266")
    assert st.text.extractor_version == "html-1/rbi_press_release" and st.speakers == {}
    assert all(r.passed for r in rbi_statements.statement_checks(st, date(2026, 10, 7)))
    wrong = {r.name: r for r in rbi_statements.statement_checks(st, date(2026, 10, 6))}
    assert not wrong["statement_date"].passed and "the page's date is 2026-10-07" in wrong["statement_date"].detail


def test_a_viewcontent_statement_without_a_date_is_dated_by_its_group():
    page = rbi_view_page(rbi_statement_body("Over the past two and a half days the MPC reviewed conditions."),
                         title="Statement by Governor - Sixth Bi-monthly Monetary Policy Press Conference for 2018-19")
    st = rbi_statements.parse(page.encode(), VIEW + "3636", "statement")
    assert (st.page_date, st.named_date, st.press_release) == (None, None, None)
    assert st.text.extractor_version == "html-1/rbi_view_content" and st.text.text.startswith("Over the past")
    checks = {r.name: r for r in rbi_statements.statement_checks(st, date(2019, 2, 7))}
    assert all(r.passed for r in checks.values()) and "undated" in checks["statement_date"].detail


def test_a_statement_that_is_short_or_never_names_the_mpc_fails():
    short = rbi_view_page("<p>Hon'ble Supreme Court has held the RBI circular dated February 12, 2018 as ultra vires."
                          "</p>", title="Governor’s Statement on Framework for Resolution of Stressed Assets")
    st = rbi_statements.parse(short.encode(), VIEW + "3659", "statement")
    assert not {r.name: r.passed for r in rbi_statements.statement_checks(st, date(2019, 4, 4))}["statement_text"]
    body = "".join(f"<p>{k}. Today, humanity faces perhaps the trial of its time, and the Reserve Bank stands ready to "
                   "use every instrument to preserve financial stability and keep credit flowing to the economy.</p>"
                   for k in range(1, 40))
    st = rbi_statements.parse(rbi_view_page(body, title="Governor’s Statement").encode(), VIEW + "3853", "statement")
    r = {r.name: r for r in rbi_statements.statement_checks(st, date(2020, 4, 17))}["statement_text"]
    assert not r.passed and "never names the Monetary Policy Committee" in r.detail


def test_a_page_that_isnt_an_old_site_document_raises():
    with pytest.raises(StatementError):
        rbi_statements.parse(b"<html><body><p>Moved</p></body></html>", PR + "1", "statement")
    with pytest.raises(ValueError):
        rbi_statements.parse(b"", PR + "1", "minutes")


# ---------------------------------------------------------------------------------------------- transcripts
def test_rbi_speaker_labels():
    assert rbi_transcript_speaker("Shaktikanta Das:") == "Shaktikanta Das"
    assert rbi_transcript_speaker("Yogesh Dayal (Moderator):\nGood afternoon.") == "Yogesh Dayal"
    assert rbi_transcript_speaker("Govardhan Rangan: Economic Times Thank you, Governor.") == "Govardhan Rangan"
    assert rbi_transcript_speaker("Dr. Michael D. Patra: Thank you.") == "Dr. Michael D. Patra"
    assert rbi_transcript_speaker("Swaminathan J.: The banks are well capitalised.") == "Swaminathan J."
    assert rbi_transcript_speaker("Participant: My question is on liquidity.") == "Participant"
    for not_a_turn in ("Participants from the Reserve Bank of India:", "Participants From RBI:", "Moderator:",
                       "To summarise: growth is firm.", "I will mention the three primary ones:",
                       "Turning to domestic developments:", "December 05, 2018", "The MPC: a review"):
        assert rbi_transcript_speaker(not_a_turn) is None, not_a_turn


def test_a_viewcontent_transcript_names_its_day_on_its_first_line_and_the_governor_speaks(tmp_path):
    turns = ["Shaktikanta Das:", "Latha Venkatesh:", "Shaktikanta Das:", "Dr. Michael D. Patra:", "Shaktikanta Das:"]
    body = rbi_transcript_view(turns * 6, date_line="February 07, 2019", participants=PARTICIPANTS)
    page = rbi_view_page(body, title="Edited Transcript of Reserve Bank of India’s Sixth Bi-monthly Monetary Policy "
                                     "Press Conference")
    tr = rbi_statements.parse(page.encode(), VIEW + "3637", "transcript")
    assert (tr.named_date, tr.page_date, tr.press_release) == (date(2019, 2, 7), None, None)
    assert set(tr.speakers) == {"Shaktikanta Das", "Latha Venkatesh", "Dr. Michael D. Patra"}
    gov = rbi_statements.governor_keys(roster(tmp_path), date(2019, 2, 7))
    assert gov == {"shaktikanta das"}
    checks = {r.name: r for r in rbi_statements.transcript_checks(tr, date(2019, 2, 7), gov)}
    assert all(r.passed for r in checks.values()), checks
    assert checks["transcript_governor"].detail.endswith("the Governor: Shaktikanta Das")
    # The chunks carry each turn's speaker; the participants list has none.
    chunks = chunk_text(tr.text, speakers="rbi_transcript")
    assert chunks[0].speaker is None and {c.speaker for c in chunks} - {None} == set(tr.speakers)
    assert all(c.body.startswith(f"{c.speaker}:") for c in chunks if c.speaker and c.chunk_no
               and chunks[c.chunk_no - 1].speaker != c.speaker)
    # Another meeting's day, or a Governor who doesn't speak, fails.
    later = {r.name: r for r in rbi_statements.transcript_checks(tr, date(2019, 4, 4), gov)}
    assert not later["transcript_date"].passed
    nobody = {r.name: r for r in rbi_statements.transcript_checks(
        tr, date(2026, 10, 7), rbi_statements.governor_keys(roster(tmp_path), date(2026, 10, 7)))}
    assert not nobody["transcript_governor"].passed and "not among them" in nobody["transcript_governor"].detail


def test_a_speech_page_transcript_dates_by_its_title_or_else_its_posting_day(tmp_path):
    gov = rbi_statements.governor_keys(roster(tmp_path), date(2026, 10, 7))
    body = rbi_transcript_speech(["Puneet Pancholy:", "Sanjay Malhotra:", "Anup Roy:", "Sanjay Malhotra:"] * 8,
                                 participants=["Shri Sanjay Malhotra - Governor"])
    dated = rbi_speech_page(body, title="Edited Transcript of the Reserve Bank of India’s Post-Monetary Policy Press "
                                        "Conference: October 7, 2026 (Wednesday)", day="Oct 10, 2026")
    tr = rbi_statements.parse(dated.encode(), SPEECH + "1583", "transcript")
    assert (tr.named_date, tr.page_date) == (date(2026, 10, 7), date(2026, 10, 10))
    assert tr.text.extractor_version == "html-1/rbi_speech"
    assert set(tr.speakers) == {"Puneet Pancholy", "Sanjay Malhotra", "Anup Roy"}       # not "Moderator"
    assert all(r.passed for r in rbi_statements.transcript_checks(tr, date(2026, 10, 7), gov))
    undated = rbi_speech_page(body, title="Edited Transcript of Reserve Bank of India’s Monetary Policy Press "
                                          "Conference", day="Oct 10, 2026")
    tr = rbi_statements.parse(undated.encode(), SPEECH + "1583", "transcript")
    assert tr.named_date is None
    assert all(r.passed for r in rbi_statements.transcript_checks(tr, date(2026, 10, 7), gov))
    late = {r.name: r for r in rbi_statements.transcript_checks(tr, date(2026, 9, 1), gov)}["transcript_date"]
    assert not late.passed and "posted 2026-10-10" in late.detail
    short = rbi_speech_page("<p>Sanjay Malhotra:<br>Good afternoon.</p>", title="Edited Transcript", day="Oct 10, 2026")
    tr = rbi_statements.parse(short.encode(), SPEECH + "1", "transcript")
    assert not {r.name: r.passed for r in rbi_statements.transcript_checks(tr, date(2026, 10, 7), gov)}[
        "transcript_text"]


# ---------------------------------------------------------------------------------------------- the saved pages
FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "rbi"


@pytest.mark.skipif(not (FIXTURES / "INDEX.tsv").exists(), reason="the saved pages aren't checked out (data/fixtures)")
def test_every_saved_statement_and_transcript_the_pages_link_parses_and_passes():
    """The 48 statements (Feb 2019 to Oct 2026) and 44 transcripts (Dec 2018 to Oct 2026) the saved Annual Policy pages
    give a meeting from the Dec 2018 baseline, against the real roster; the pages' other statements left out."""
    pages = [rbi_calendar.parse_page(p.read_bytes()) for p in sorted((FIXTURES / "annualpolicy").glob(
        "annualpolicy_*.html"))]
    cal = rbi_calendar.calendar(pages)
    assert cal.doc_problems == ()
    decisions = [r.decision_date for r in cal.resolutions if r.decision_date >= date(2018, 12, 5)]
    assert len(decisions) == 49
    files = {rbi_statements.doc_url(r["url"]): r["file"] for r in csv.DictReader(
        (FIXTURES / "INDEX.tsv").open(encoding="utf-8"), delimiter="\t") if r["kind"] in ("governor_statement",
                                                                                         "transcript")}
    ros = members.load()
    seen = {"statement": [], "transcript": []}
    for kind, links in (("statement", cal.statements), ("transcript", cal.transcripts)):
        for d in decisions:
            if d not in links:
                continue
            url = rbi_statements.doc_url(links[d].url)
            doc = rbi_statements.parse((FIXTURES / files[url]).read_bytes(), url, kind)
            results = (rbi_statements.statement_checks(doc, d) if kind == "statement" else
                       rbi_statements.transcript_checks(doc, d, rbi_statements.governor_keys(ros, d), links[d].posted_on))
            assert all(r.passed for r in results), (d, kind, [r for r in results if not r.passed])
            seen[kind].append((d, url, doc.press_release))
    assert len(seen["statement"]) == 48 and seen["statement"][0][0] == date(2019, 2, 7)
    assert len(seen["transcript"]) == 44 and seen["transcript"][0][0] == date(2018, 12, 5)
    assert sorted(set(decisions) - {d for d, _, _ in seen["transcript"]}) == [
        date(2020, 3, 27), date(2020, 5, 22), date(2020, 8, 6), date(2020, 10, 9), date(2022, 5, 4)]
    assert sum(pr is not None for _, _, pr in seen["statement"]) == 38               # the prid pages, Aug 2020 on (but Oct 2020)
    assert (date(2026, 10, 7), PR + "63744", "2026-2027/1266") in seen["statement"]
    stored = {u for k in seen.values() for _, u, _ in k}
    for left_out in ("3659", "3853"):                                                # 4 Apr 2019, 17 Apr 2020
        assert VIEW + left_out not in stored
    for left_out in ("51526", "56892"):                                              # 5 May 2021, 8 Dec 2023
        assert PR + left_out not in stored
