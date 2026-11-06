"""A PDF is stored only if it is kept (job 2B-07c, #287; Plan 04b P5). The pipeline gets every body unstored, reads it, and keeps it
(the task stores it) only when a document is made from it: a transcript or a cover letter. A PDF it reads and rejects, such as a
first-page candidate that is an annual report, is not stored and its URL is recorded, so the next run does not fetch it. Synthetic
PDFs (`call_pdfs.py`) and an in-memory stand-in for the task's keeper, decliner and releaser: no saved files, no database."""
import hashlib
from datetime import UTC, date, datetime

from call_pdfs import call_pdf, make_pdf

from ft.equity.calls import discovery as dc
from ft.equity.calls import pdfs, rows
from ft.equity.calls import pipeline as pl

CO = pl.CompanyRef(1, "asian-paints", "Asian Paints Limited", "ASIANPAINT")
NSE = "https://nsearchives.nseindia.com/corporate/"
HOST = "https://www.asianpaints.com/"


def at(month, day, year=2026, hour=12):
    return datetime(year, month, day, hour, 0, tzinfo=UTC)


Q1_RESULTS = rows.ResultsEvent(2, at(7, 29), date(2026, 6, 30))


class Store:
    """What the task does with the pipeline's getter, in memory. `files` is keyed by the whole URL. A body comes back unstored;
    `stored` is the object store, `recorded` the declined URLs, `released` the bodies nobody settled. A URL already recorded is
    not fetched and comes back declined; a URL already stored comes back as its stored copy (a 304)."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.fetched = files, []
        self.stored: dict[str, bytes] = {}
        self.recorded: set[str] = set()
        self.released: list[str] = []
        self.report = pl.Report()

    def getter(self, url: str, kind: str):
        if url in self.recorded:
            return pl.File(url, b"", b"", None, declined=True)
        self.fetched.append(url)
        body = self.files.get(url)
        if body is None:
            return None
        sha = hashlib.sha256(body).digest()
        return pl.File(url, body, sha, 1 if url in self.stored else None)

    def keeper(self, f: pl.File) -> pl.File:
        self.stored[f.url] = f.body
        return pl.File(f.url, f.body, f.sha256, len(self.stored))

    def decliner(self, f: pl.File) -> bool:
        self.recorded.add(f.url)
        return True

    def releaser(self, f: pl.File) -> None:
        self.released.append(f.url)

    def run(self) -> pl.Run:
        self.report = pl.Report()
        return pl.Run(self.getter, {}, self.report, keeper=self.keeper, decliner=self.decliner, releaser=self.releaser)


def announcement(name, when, kind, subject):
    return dc.Announcement("NSE", name, "ASIANPAINT", "INE021A01026", None, when.astimezone(dc.INDIA), subject, name, NSE + name,
                           kind, "", dc.season_of_filing(when), CO.entity_id)


def other(name, when):
    return announcement(name, when, "other", "Outcome of analyst meet")


def transcript(name, when):
    return announcement(name, when, "transcript", "Transcript of the earnings call")


FILLER = ["This document is shared for the information of the shareholders, the investors and the stock exchanges where",
          "the securities of the company are listed, and it is also available on the website of the company for the benefit of",
          "all members and stakeholders. Please read it together with the other documents filed on the same date."]


def notice(*lines):
    """A short PDF with a text layer (200 letters or more) that is no transcript: a notice, a report's cover, a first slide."""
    return make_pdf([[CO.name, *lines, *FILLER]])


def letter_pdf(*lines):
    """A cover letter: a short PDF with a text layer, one that mentions the transcript and links to it."""
    return notice("Transcript of the Q1 FY27 earnings call is available at", *lines)


def earnings(header_line="Q1 FY27 Earnings Conference Call", seed=1):
    return call_pdf([CO.name, header_line, "The call was held on 29 July 2026"], seed=seed)


def process(run, announcements, **kw):
    return pl.process_company(run, CO, announcements, [Q1_RESULTS], weekly=False, newest_call=None, **kw)


# ---------------------------------------------------------------------------------------------- first-page candidates
def test_a_first_page_candidate_that_is_no_transcript_is_not_stored_and_its_url_is_recorded_but_the_one_that_is_is_stored():
    report, deck, hidden = (other("AnnualReport.pdf", at(8, 3)), other("Presentation.pdf", at(8, 4)), other("Outcome.pdf", at(8, 5)))
    store = Store({NSE + "AnnualReport.pdf": notice("Annual Report 2025-26", "Notice of the 81st Annual General Meeting"),
                   NSE + "Presentation.pdf": notice("Investor presentation Q1 FY27", "Safe harbour statement"),
                   NSE + "Outcome.pdf": earnings("Transcript of the Q1 FY27 Earnings Conference Call")})
    run = store.run()
    [p] = process(run, [report, deck, hidden], first_pages_from="Q1 FY27")
    assert p.status == "stored" and p.is_stored and p.filing.file_name == "Outcome.pdf" and run.report.first_pages == 3
    assert set(store.stored) == {NSE + "Outcome.pdf"}                                    # only the transcript is kept
    assert store.recorded == {NSE + "AnnualReport.pdf", NSE + "Presentation.pdf"}        # the others are recorded, not stored
    assert run.report.not_kept == 2 and run.report.not_kept_bytes == sum(
        len(store.files[NSE + n]) for n in ("AnnualReport.pdf", "Presentation.pdf")) and store.released == []
    assert p.transcript.file.raw_file_id is not None                                     # the document's file is the stored one


def test_a_url_recorded_on_an_earlier_run_is_not_fetched_again_and_the_call_is_found_as_before():
    files = {NSE + "AnnualReport.pdf": notice("Annual Report 2025-26"), NSE + "Outcome.pdf": earnings("Transcript of the Q1 FY27 call")}
    announcements = [other("AnnualReport.pdf", at(8, 3)), other("Outcome.pdf", at(8, 5))]
    store = Store(files)
    [first] = process(store.run(), announcements, first_pages_from="Q1 FY27")
    assert store.recorded == {NSE + "AnnualReport.pdf"} and sorted(store.fetched) == sorted(files)
    store.fetched.clear()
    run = store.run()                                                                    # the next run
    [again] = process(run, announcements, first_pages_from="Q1 FY27")
    assert store.fetched == [NSE + "Outcome.pdf"]                                        # the recorded URL is not fetched twice
    assert run.report.already_declined == 1 and run.report.not_kept == 0 and run.report.not_served == []
    assert again.is_stored and again.filing.filing_key == first.filing.filing_key and set(store.stored) == {NSE + "Outcome.pdf"}


# ---------------------------------------------------------------------------------------------- filings named a transcript
def test_a_filing_named_a_transcript_that_reads_as_a_notice_is_not_stored_and_a_later_run_does_not_call_it_unserved():
    store = Store({NSE + "Intimation.pdf": notice("Intimation of the schedule of the analyst meet", "Pursuant to Regulation 30")})
    announcements = [transcript("Intimation.pdf", at(8, 3))]
    run = store.run()
    assert process(run, announcements) == [] and run.report.not_a_transcript and store.stored == {}
    assert store.recorded == {NSE + "Intimation.pdf"} and run.report.not_kept == 1
    run = store.run()                                                                    # recorded: not fetched, not "unserved" either
    store.fetched.clear()
    assert process(run, announcements) == [] and store.fetched == []
    assert run.report.not_served == [] and run.report.not_a_transcript == [] and run.report.already_declined == 1


def test_a_transcript_a_cover_letter_and_an_image_only_file_are_stored():
    transcript_pdf = earnings()
    letter = letter_pdf(HOST + "files/q1fy27-call.pdf")
    store = Store({NSE + "Call.pdf": transcript_pdf, NSE + "Letter.pdf": letter, HOST + "files/q1fy27-call.pdf": earnings(seed=3),
                   NSE + "Scan.pdf": make_pdf([[]])})
    assert pdfs.classify_pdf(pdfs.read_pdf(store.files[NSE + "Scan.pdf"])) == "image_only"
    [p] = process(store.run(), [transcript("Call.pdf", at(8, 3))])
    assert p.is_stored and p.route == "exchange_pdf" and set(store.stored) == {NSE + "Call.pdf"}
    store = Store(dict(store.files))
    [p] = process(store.run(), [transcript("Letter.pdf", at(8, 3))])
    assert p.is_stored and p.route == "company_link"                                     # the letter and the company's PDF
    assert set(store.stored) == {NSE + "Letter.pdf", HOST + "files/q1fy27-call.pdf"} and store.recorded == set()
    store = Store(dict(store.files))
    [p] = process(store.run(), [transcript("Scan.pdf", at(8, 3))])
    assert p.status == "unreadable" and set(store.stored) == {NSE + "Scan.pdf"} and store.recorded == set()      # kept, as before


# ---------------------------------------------------------------------------------------------- a cover letter's links
def test_a_company_link_that_is_no_transcript_is_recorded_and_one_that_is_another_companys_transcript_is_only_released():
    report_url, other_url = HOST + "investors/annual-report-2026.pdf", HOST + "files/q1fy27-other.pdf"
    letter = letter_pdf(report_url, other_url)
    elsewhere = call_pdf(["Berger Paints India Limited", "Q1 FY27 Earnings Conference Call", "The call was held on 29 July 2026"], seed=9)
    store = Store({NSE + "Letter.pdf": letter, report_url: notice("Annual Report 2025-26"), other_url: elsewhere})
    run = store.run()
    [p] = process(run, [transcript("Letter.pdf", at(8, 3))])
    assert p.status == "on_company_website" and not p.is_stored                          # neither link is this company's transcript
    assert set(store.stored) == {NSE + "Letter.pdf"}                                     # the letter alone is kept
    assert store.recorded == {report_url}                                                # no transcript at all: recorded
    assert store.released == [other_url]                                                 # a transcript of someone: another letter may link it
    run = store.run()                                                                    # the next run asks only for what is not recorded
    store.fetched.clear()
    [p] = process(run, [transcript("Letter.pdf", at(8, 3))])
    assert report_url not in store.fetched and other_url in store.fetched and p.status == "on_company_website"
    assert any("PDF that is not a call transcript" in x for x in run.report.unresolved)


def test_a_curated_row_that_is_a_notice_is_recorded_and_named_and_one_that_passes_is_stored():
    bad, good = HOST + "investors/old-notice.pdf", HOST + "files/q1fy27-call.pdf"
    letter = letter_pdf(HOST)                                                            # a letter that links only to the homepage
    curated = {(CO.slug, "Q1 FY27"): pl.CuratedRow(CO.slug, "Q1 FY27", bad)}
    store = Store({NSE + "Letter.pdf": letter, bad: notice("Notice"), good: earnings(seed=3)})
    run = pl.Run(store.getter, curated, store.report, keeper=store.keeper, decliner=store.decliner, releaser=store.releaser)
    [p] = process(run, [transcript("Letter.pdf", at(8, 3))])
    assert p.status == "on_company_website" and run.report.curated_failed and store.recorded == {bad}
    assert set(store.stored) == {NSE + "Letter.pdf"}
    store = Store(dict(store.files))
    curated = {(CO.slug, "Q1 FY27"): pl.CuratedRow(CO.slug, "Q1 FY27", good)}
    run = pl.Run(store.getter, curated, store.report, keeper=store.keeper, decliner=store.decliner, releaser=store.releaser)
    [p] = process(run, [transcript("Letter.pdf", at(8, 3))])
    assert p.is_stored and p.route == "curated_link" and set(store.stored) == {NSE + "Letter.pdf", good} and store.recorded == set()


# ---------------------------------------------------------------------------------------------- nothing settled
def test_a_candidate_that_passed_but_whose_season_was_not_built_is_released_not_recorded():
    store = Store({NSE + "Outcome.pdf": earnings("Transcript of the Q1 FY27 call")})
    run = store.run()
    run.get(NSE + "Outcome.pdf", "exchange")                                             # fetched, then nothing decides it
    run.settle()
    assert store.released == [NSE + "Outcome.pdf"] and store.recorded == set() and store.stored == {}


def test_a_general_meetings_transcript_is_kept_as_another_meeting_and_its_notice_is_not():
    agm = call_pdf([CO.name, "80th Annual General Meeting", "The meeting was held on 9 July 2026"], seed=7)
    store = Store({NSE + "Agm.pdf": agm, NSE + "AgmNotice.pdf": notice("Notice of the 80th Annual General Meeting"),
                   NSE + "Call.pdf": earnings()})
    run = store.run()
    filings = [announcement("Agm.pdf", at(7, 16), "transcript", "Transcript of the 80th AGM of the Company"),
               announcement("AgmNotice.pdf", at(7, 17), "transcript", "Intimation of the 80th AGM transcript"),
               transcript("Call.pdf", at(8, 3))]
    [p] = process(run, filings)
    assert p.filing.file_name == "Call.pdf" and [x.kind for x in p.extras] == ["other_meeting"]
    assert set(store.stored) == {NSE + "Agm.pdf", NSE + "Call.pdf"} and store.recorded == {NSE + "AgmNotice.pdf"}


def test_a_run_given_no_keeper_stores_and_records_nothing_as_before():
    files = {NSE + "Call.pdf": earnings(), NSE + "Report.pdf": notice("Annual Report 2025-26")}
    run = pl.Run(Store(files).getter, {}, pl.Report())
    [p] = process(run, [transcript("Call.pdf", at(8, 3)), other("Report.pdf", at(8, 4))])
    assert p.is_stored and run.report.not_kept == 0
