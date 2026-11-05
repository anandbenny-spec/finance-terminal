"""A call is chosen by fiscal quarter, not by season (job 2B-07b, #285; Spec 02B §4: one call per company per fiscal quarter).
Synthetic transcripts (`call_pdfs.py`), so these tests need no saved files and no database: general meetings are never calls,
a filing held for no quarter never takes a slot, a late call is its own quarter's call unless that quarter has one, a results
period counts at its first filing, and calls are ordered by quarter."""
import hashlib
from datetime import UTC, date, datetime

import pytest
from call_pdfs import call_pdf

from ft.equity.calls import classify as cl
from ft.equity.calls import discovery as dc
from ft.equity.calls import pdfs, rows
from ft.equity.calls import pipeline as pl

CO = pl.CompanyRef(1, "asian-paints", "Asian Paints Limited", "ASIANPAINT")
NSE = "https://nsearchives.nseindia.com/corporate/"


def at(month, day, year=2026, hour=9):
    return datetime(year, month, day, hour, 0, tzinfo=UTC)


class Served:
    """A getter serving PDFs by file name, and recording what was asked."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.asked = files, []

    def __call__(self, url: str, kind: str):
        self.asked.append(url)
        body = self.files.get(url.rsplit("/", 1)[-1]) if kind == "exchange" else None
        return None if body is None else pl.File(url, body, hashlib.sha256(body).digest(), None)


def run_for(files: dict[str, bytes]):
    return pl.Run(Served(files), {}, pl.Report())


def filing(name, when, subject="Transcript of the earnings call", key=None):
    when = when.replace(hour=12)
    return dc.Announcement("NSE", key or name, "ASIANPAINT", "INE021A01026", None, when.astimezone(dc.INDIA), subject, name, NSE + name,
                           "transcript", "", dc.season_of_filing(when), CO.entity_id)


def earnings(quarter, held_on, seed):
    """A transcript of `quarter`'s earnings call, held on the date written (a string like "29 July 2026")."""
    return call_pdf([CO.name, f"{quarter} Earnings Conference Call", f"The call was held on {held_on}"], seed=seed)


Q4_RESULTS = rows.ResultsEvent(1, at(5, 29), date(2026, 3, 31))
Q1_RESULTS = rows.ResultsEvent(2, at(7, 29), date(2026, 6, 30))


def labels(plans):
    return [(p.quarter.label, p.season, p.late) for p in plans]


# ---------------------------------------------------------------------------------------------- rule 3: general meetings
@pytest.mark.parametrize("by", ["subject and file name", "first page"])
def test_a_general_meeting_filed_first_is_set_aside_and_the_earnings_call_filed_later_is_the_call(by):
    named = by.startswith("subject")
    gm_name = "SEIntimation80thAGMTranscriptandVideosigned.pdf" if named else "Proceedings.pdf"
    gm_subject = "Video recording and Transcript of the 80th AGM of the Company" if named else "Transcript of the proceedings"
    agm = call_pdf([CO.name, "80th Annual General Meeting", "The meeting was held on 9 July 2026"], seed=7)
    files = {gm_name: agm, "Call.pdf": earnings("Q1 FY27", "29 July 2026", seed=1)}
    run = run_for(files)
    [p] = pl.process_company(run, CO, [filing(gm_name, at(7, 16), gm_subject), filing("Call.pdf", at(8, 3))], [Q4_RESULTS, Q1_RESULTS],
                             weekly=False, newest_call=None)
    assert p.filing.file_name == "Call.pdf" and p.is_stored and labels([p]) == [("Q1 FY27", "Q1 FY27", False)]
    assert len(run.report.general_meetings) == 1 and gm_name in run.report.general_meetings[0]
    assert run.report.gm_filings == [(1, "NSE", gm_name)]
    assert [(x.kind, x.filing.file_name, x.reason) for x in p.extras] == [("other_meeting", gm_name, "general meeting")]
    assert "general meeting" in run.report.other_meetings[0]
    assert p.extras[0].doc.title == "Asian Paints Limited general meeting transcript, filed 2026-07-16"      # never titled a call


def test_a_season_of_general_meetings_alone_has_no_call_and_names_them():
    agm = call_pdf([CO.name, "Extra-Ordinary General Meeting", "Held on 9 July 2026"], seed=7)
    run = run_for({"Egm.pdf": agm})
    assert pl.process_company(run, CO, [filing("Egm.pdf", at(7, 16))], [Q1_RESULTS], weekly=False, newest_call=None) == []
    assert len(run.report.general_meetings) == 1 and run.report.late_calls == []


@pytest.mark.parametrize("text, is_gm", [
    ("SEIntimation80thAGMTranscriptandVideosigned.pdf", True),
    ("Video recording and Transcript of the 80th AGM of the Company", True),
    ("Transcript of the Extra-Ordinary General Meeting", True), ("EGM transcript", True), ("Annual_General_Meeting_2026.pdf", True),
    ("Transcript of the Investor Conference held on Wednesday, 29th July 2026", False),
    ("ASIANPAINT_03082026144133_Regulation_30_Intimation_-_Transcript_for_Investor_Conference.pdf", False),
    ("Q1 FY27 earnings call, magma and pragmatic", False), ("", False), (None, False)])
def test_the_general_meeting_terms_match_the_names_companies_use_and_nothing_else(text, is_gm):
    assert cl.is_general_meeting(text) is is_gm


def test_an_earnings_call_that_mentions_the_agm_in_its_text_is_still_a_call():
    call = call_pdf([CO.name, "Q1 FY27 Earnings Conference Call", "The call was held on 29 July 2026"], seed=1,
                    mention="Shareholders asked about the AGM and the Annual General Meeting of next year.")
    info = pdfs.read_pdf(call)
    assert "AGM" in info.page_texts[0] and not cl.is_general_meeting(pdfs.header_text(info))        # on page 1, but not in its header
    run = run_for({"Call.pdf": call})
    [p] = pl.process_company(run, CO, [filing("Call.pdf", at(8, 3))], [Q1_RESULTS], weekly=False, newest_call=None)
    assert p.is_stored and p.quarter.label == "Q1 FY27" and run.report.general_meetings == [] and run.report.gm_filings == []


@pytest.mark.parametrize("text, is_gm", [
    ("Yours faithfully, (Ajay Garg) AGM (Finance) Investor Relations Department", False),         # NTPC's signature: a manager
    ("Dr Reddy's Laboratories Limited 45th AGM Shareholders' Meeting", True),
    ("Cipla Limited 88th Annual General Meeting held on 16 July 2025", True),
    ("Notice of the Extra-Ordinary General Meeting", True), ("Q1 FY27 Earnings Conference Call", False)])
def test_on_a_first_page_a_bare_agm_is_not_a_meeting_but_the_long_names_and_a_numbered_agm_are(text, is_gm):
    assert cl.is_general_meeting(text, short=False) is is_gm


def test_an_earnings_call_whose_cover_letter_is_signed_by_an_agm_is_still_a_call():
    header = [CO.name, "Q1 FY27 Earnings Conference Call", "The call was held on 29 July 2026", "Yours faithfully,", "(Ajay Garg)",
              "AGM (Finance)", "Investor Relations Department"]
    call = call_pdf(header, seed=1)
    assert "AGM (Finance)" in pdfs.header_text(pdfs.read_pdf(call))
    run = run_for({"Call.pdf": call})
    [p] = pl.process_company(run, CO, [filing("Call.pdf", at(8, 3))], [Q1_RESULTS], weekly=False, newest_call=None)
    assert p.is_stored and p.quarter.label == "Q1 FY27" and run.report.general_meetings == [] and run.report.gm_filings == []


def test_the_header_is_the_page_before_the_first_speaker_label():
    info = pdfs.read_pdf(call_pdf(["Header one", "Header two"], seed=3))
    assert pdfs.header_text(info) == "Header one\nHeader two"
    assert pdfs.header_text(pdfs.read_pdf(b"%PDF-1.4 broken")) == ""


# ---------------------------------------------------------------------------------------------- rule 1: the first filing that resolves
def test_a_filing_held_for_no_quarter_never_takes_the_slot_and_the_next_filing_is_the_call():
    first = call_pdf([CO.name, "Investor conference call", "The call was held on 9 July 2026"], seed=3)           # names no quarter
    files = {"A.pdf": first, "B.pdf": earnings("Q1 FY27", "28 July 2026", seed=1)}
    run = run_for(files)
    [p] = pl.process_company(run, CO, [filing("A.pdf", at(7, 15)), filing("B.pdf", at(7, 28))],
                             [Q4_RESULTS, rows.ResultsEvent(2, at(7, 24), date(2026, 6, 30))], weekly=False, newest_call=None)
    assert p.filing.file_name == "B.pdf" and p.is_stored and p.quarter.source == "results_link"
    assert [(x.kind, x.filing.file_name, x.reason) for x in p.extras] == [("other_meeting", "A.pdf", "no quarter")]
    assert "no quarter" in run.report.other_meetings[0] and run.report.held == []


def test_when_no_filing_resolves_the_outcome_is_the_first_filings_held_with_its_reason():
    first = call_pdf([CO.name, "Investor conference call", "The call was held on 9 July 2026"], seed=3)
    run = run_for({"A.pdf": first, "B.pdf": call_pdf([CO.name, "Analyst call"], seed=4)})
    [p] = pl.process_company(run, CO, [filing("A.pdf", at(7, 15)), filing("B.pdf", at(7, 28))], [Q4_RESULTS], weekly=False,
                             newest_call=None)
    assert p.filing.file_name == "A.pdf" and p.status == "stored" and p.status_reason == "no_quarter" and not p.is_stored
    assert p.quarter.label is None and not p.late and len(run.report.held) == 1
    assert [(x.kind, x.filing.file_name, x.reason) for x in p.extras] == [("other_meeting", "B.pdf", "no quarter")]


# ---------------------------------------------------------------------------------------------- rule 1: late calls
def test_a_filing_of_an_earlier_quarter_with_no_stored_call_is_that_quarters_late_call_titled_with_its_quarter():
    files = {"Late.pdf": earnings("Q4 FY26", "9 July 2026", seed=2), "Own.pdf": earnings("Q1 FY27", "29 July 2026", seed=1)}
    run = run_for(files)
    plans = pl.process_company(run, CO, [filing("Late.pdf", at(7, 20)), filing("Own.pdf", at(8, 3))], [Q4_RESULTS, Q1_RESULTS],
                               weekly=False, newest_call=None)
    assert labels(plans) == [("Q1 FY27", "Q1 FY27", False), ("Q4 FY26", "Q1 FY27", True)]
    own, late = plans
    assert late.is_stored and late.quarter.source == "text" and late.filing.file_name == "Late.pdf"
    assert late.transcript.title == "Asian Paints Limited call transcript, Q4 FY26"                  # rule 4: the call's quarter
    assert own.transcript.title == "Asian Paints Limited call transcript, Q1 FY27"
    assert run.report.late_calls == ["asian-paints Q4 FY26 (filed 2026-07-20 in the Q1 FY27 season)"]
    assert pl.coverage_by_season([filing("Late.pdf", at(7, 20)), filing("Own.pdf", at(8, 3))], plans)[0].stored == 1       # the season's


def test_a_late_call_alone_in_its_season_is_still_that_quarters_call():
    run = run_for({"Late.pdf": earnings("Q4 FY26", "9 July 2026", seed=2)})
    plans = pl.process_company(run, CO, [filing("Late.pdf", at(7, 20))], [Q4_RESULTS], weekly=False, newest_call=None)
    assert labels(plans) == [("Q4 FY26", "Q1 FY27", True)] and len(run.report.late_calls) == 1


@pytest.mark.parametrize("history", [True, False])
@pytest.mark.parametrize("same, kind, reason", [
    (True, "revised", "same call date or at least 90% the same text"),
    (False, "other_meeting", "the Q4 FY26 quarter already has a call")])
def test_a_late_filing_of_a_quarter_stored_in_the_run_is_a_revised_version_or_another_meeting(history, same, kind, reason):
    q4 = earnings("Q4 FY26", "2 June 2026", seed=1)
    again = earnings("Q4 FY26", "2 June 2026", seed=1) if same else earnings("Q4 FY26", "9 July 2026", seed=2)
    run = run_for({"Q4.pdf": q4, "Again.pdf": again})
    plans = pl.process_company(run, CO, [filing("Q4.pdf", at(6, 3)), filing("Again.pdf", at(7, 20))], [Q4_RESULTS], weekly=False,
                               newest_call=None, history=history)
    [p] = plans
    assert labels([p]) == [("Q4 FY26", "Q4 FY26", False)] and p.filing.file_name == "Q4.pdf"          # the season's own call keeps it
    assert [(x.kind, x.filing.file_name, x.reason) for x in p.extras] == [(kind, "Again.pdf", reason)]
    assert run.report.late_calls == [] and len(run.report.other_meetings) == (1 if kind == "other_meeting" else 0)


@pytest.mark.parametrize("same, revised", [(True, True), (False, False)])
def test_a_late_filing_of_a_quarter_stored_only_in_the_database_is_compared_with_that_call_and_makes_no_call(same, revised):
    stored_pdf = earnings("Q4 FY26", "2 June 2026", seed=1)
    stored_text = pdfs.call_text(pdfs.read_pdf(stored_pdf)).extracted.text
    read = []
    stored = {"Q4 FY26": pl.StoredCall("Q4 FY26", date(2026, 6, 2), lambda: read.append(1) or stored_text)}
    late = earnings("Q4 FY26", "2 June 2026", seed=1) if same else earnings("Q4 FY26", "9 July 2026", seed=2)
    filings = [filing("Late.pdf", at(7, 20))]
    run = run_for({"Late.pdf": late})
    plans = pl.process_company(run, CO, filings, [Q4_RESULTS], weekly=True, newest_call="Q4 FY26", stored=stored)
    assert plans == [] and run.report.late_calls == []                                  # the stored call stays the quarter's
    assert bool(run.report.stored_revised) is revised and bool(run.report.other_meetings) is (not revised)
    assert "Q4 FY26" in (run.report.stored_revised or run.report.other_meetings)[0] and read == [1]
    # with no stored call in the database, the same filing is the quarter's late call
    plans = pl.process_company(run_for({"Late.pdf": late}), CO, filings, [Q4_RESULTS], weekly=True, newest_call="Q4 FY26", stored={})
    assert labels(plans) == [("Q4 FY26", "Q1 FY27", True)]


# ---------------------------------------------------------------------------------------------- rule 2: a results period's first filing
def test_a_refiling_does_not_restart_the_45_days_but_a_first_filing_inside_45_days_links():
    refiled = rows.ResultsEvent(3, at(7, 15), date(2026, 3, 31), first_at=at(5, 29))        # the March quarter's -r3, filed on 15 Jul
    agm = at(7, 16, hour=12)                                                                     # 48 days after the first filing
    assert rows.decide_quarter(agm, [refiled], None).held_reason == "no_quarter"
    assert rows.decide_quarter(agm, [refiled], "Q4 FY26").source == "text"                       # not the results link
    assert rows.decide_quarter(agm, [rows.ResultsEvent(3, at(7, 15), date(2026, 3, 31))], None).source == "results_link"    # as it was
    q1 = rows.ResultsEvent(5, at(8, 20), date(2026, 6, 30), first_at=at(7, 29))              # first filed 29 Jul, re-filed later
    got = rows.decide_quarter(at(8, 3, hour=12), [q1], None)
    assert (got.label, got.source, got.results_event_id) == ("Q1 FY27", "results_link", 5)
    assert rows.decide_quarter(at(7, 28), [q1], None).held_reason == "no_quarter"                # before its first filing: not its results


def test_every_version_of_a_results_filing_gives_its_periods_first_filing_time():
    versions = [(1, at(5, 29), date(2026, 3, 31), True), (2, at(6, 10), date(2026, 3, 31), True), (3, at(7, 15), date(2026, 3, 31), False),
                (4, at(7, 29), date(2026, 6, 30), False), (9, at(1, 10), date(2025, 12, 31), True)]
    got = rows.with_first_filings(versions)
    assert [(r.event_id, r.period_end, r.occurs_at, r.first) for r in got] == [
        (3, date(2026, 3, 31), at(7, 15), at(5, 29)), (4, date(2026, 6, 30), at(7, 29), at(7, 29))]    # a period with no current version: skipped
    assert rows.with_first_filings([]) == []


def test_a_transcript_linked_only_to_a_refiling_has_no_results_link_in_the_pipeline():
    agm_like = call_pdf([CO.name, "Investor conference call", "The call was held on 9 July 2026"], seed=3)
    run = run_for({"X.pdf": agm_like})
    refiled = rows.ResultsEvent(3, at(7, 15), date(2026, 3, 31), first_at=at(5, 29))
    [p] = pl.process_company(run, CO, [filing("X.pdf", at(7, 16))], [refiled], weekly=False, newest_call=None)
    assert p.status_reason == "no_quarter" and p.quarter.results_event_id is None


# ---------------------------------------------------------------------------------------------- rule 1: ordering
def test_calls_are_ordered_by_their_quarter_never_by_the_season_they_were_filed_in():
    assert rows.newest_quarter([("Q4 FY26", "q1-fy27"), ("Q1 FY27", "q1-fy27")]) == "Q1 FY27"
    assert rows.newest_quarter([("Q4 FY26", "q1-fy27")]) == "Q4 FY26"                  # a late Q4 call filed in the Q1 season
    assert rows.newest_quarter([(None, "q2-fy26"), ("Q1 FY26", "q1-fy26")]) == "Q2 FY26"            # a held call counts at its season
    assert rows.newest_quarter([]) is None
    assert rows.quarter_order("Q4 FY26") < rows.quarter_order("Q1 FY27") and rows.quarter_order(None) == (0, 0)


def test_a_weekly_run_still_looks_at_the_season_of_a_late_call_until_its_own_call_arrives():
    late = earnings("Q4 FY26", "9 July 2026", seed=2)
    stored = {"Q4 FY26": pl.StoredCall("Q4 FY26", date(2026, 7, 9), lambda: None)}
    newest = rows.newest_quarter([("Q4 FY26", "q1-fy27")])                                # the late call is the newest the database has
    # the Q1 call has arrived since: the weekly run finds it, though a call with the season's key is already stored
    files = {"Late.pdf": late, "Own.pdf": earnings("Q1 FY27", "29 July 2026", seed=1)}
    filings = [filing("Late.pdf", at(7, 20)), filing("Own.pdf", at(8, 3))]
    run = run_for(files)
    plans = pl.process_company(run, CO, filings, [Q4_RESULTS, Q1_RESULTS], weekly=True, newest_call=newest, stored=stored)
    assert labels(plans) == [("Q1 FY27", "Q1 FY27", False)]
    assert len(run.report.other_meetings) == 1                                          # the late filing, against the stored Q4 call
    # once the Q1 call is stored, there is nothing newer to fetch
    run = run_for(files)
    assert pl.process_company(run, CO, filings, [Q4_RESULTS, Q1_RESULTS], weekly=True, newest_call="Q1 FY27", stored=stored) == []
    assert run.getter.asked == []
