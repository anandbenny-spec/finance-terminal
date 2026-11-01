"""Earnings-call PDFs: classification, text with page offsets, links, quarters, call dates, revised versions and the
selection of seasons (Spec 02B §4, §5.2, §13.3, §13.4; job 2B-07). No database and no network: the saved files are in
data/fixtures/equity/calls (the private fixtures repo; these tests skip without it and CI checks it out). Expected
values come from `audit/files.json` (the audit kit's results) and `pages/*.pages.json` (recorded with pypdf alone), not
from the app's code."""
import glob
import hashlib
import io
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ft.engines.document.text import (
    PageSpan,
    call_speakers,
    pdf_text,
    pdf_text_pages,
    transcript_speaker,
)
from ft.equity.calls import discovery as dc
from ft.equity.calls import pdfs, rows
from ft.equity.calls import pipeline as pl

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
CALLS = FIXTURES / "equity" / "calls"
needs_fixtures = pytest.mark.skipif(not (CALLS / "audit").exists(), reason="the saved files aren't checked out (data/fixtures)")
AUDIT = json.loads((CALLS / "audit" / "files.json").read_text()) if (CALLS / "audit").exists() else {"files": []}
BY_NAME = {Path(f["file"]).name: f for f in AUDIT["files"]}
PAGES = sorted(glob.glob(str(CALLS / "pages" / "*.pages.json")))


def pdf_bytes(name: str) -> bytes:
    path = next(CALLS.glob(f"pdf/*/{name}"))
    return path.read_bytes()


def info_of(name: str) -> pdfs.PdfInfo:
    return pdfs.read_pdf(pdf_bytes(name))


# ---------------------------------------------------------------------------------------------- classification
@needs_fixtures
@pytest.mark.parametrize("name", sorted(BY_NAME))
def test_every_fixture_pdf_is_classified_as_the_audit_kit_did(name):
    f, info = BY_NAME[name], info_of(name)
    assert pdfs.classify_pdf(info) == f["kind"]
    assert info.pages == f["pages"] and info.words == f["words"] and len(info.dup_pages) == f["dup_pages"]
    assert info.quarter == f["quarter_parsed"]
    assert (info.call_date.isoformat() if info.call_date else None) == f["call_date_parsed"]
    if f["dup_pages"] == 0:
        assert info.speakers.count == f["speaker_lines"]
        assert info.speakers.patterns == f["speaker_patterns"]


@needs_fixtures
def test_repeated_pages_are_dropped_by_hash_and_keep_the_others_physical_numbers():
    name = "IREDAEQ_19072025165116_SignedSEPresentationmumbai.pdf"
    expected = json.loads((CALLS / "pages" / f"{name}.pages.json").read_text())["detail"]
    info = info_of(name)
    assert info.dup_pages == [d["page"] for d in expected if d["duplicate_of_page"]]
    assert len(info.kept) == info.pages - 26 and info.kept[:3] == [1, 2, 3]
    text = pdfs.call_text(info)
    assert [s.pdf_page for s in text.spans] and all(s.pdf_page in info.kept for s in text.spans)
    assert text.page_map_ok


def _blank_pdf(pages: int = 1) -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_an_image_only_pdf_has_no_text_layer():
    info = pdfs.read_pdf(_blank_pdf(3))
    assert info.is_pdf and not info.text_layer and pdfs.classify_pdf(info) == "image_only"


@needs_fixtures
def test_an_encrypted_pdf_is_opened_with_an_empty_password():
    from pypdf import PdfReader, PdfWriter
    name = "CIPLA_15052026182859_EarningscalltranscriptMay26signedrc.pdf"
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes(name))))
    w.encrypt("", owner_password="owner", algorithm="AES-256")
    out = io.BytesIO()
    w.write(out)
    info = pdfs.read_pdf(out.getvalue())
    plain = info_of(name)
    assert info.encrypted and not info.unreadable and pdfs.classify_pdf(info) == "full" and info.words == plain.words


def test_a_file_that_is_not_a_pdf_or_cannot_be_read_is_other():
    assert pdfs.classify_pdf(pdfs.read_pdf(b"<html>Access Denied</html>")) == "other"
    broken = pdfs.read_pdf(b"%PDF-1.4\nnot really\n")
    assert broken.is_pdf and pdfs.classify_pdf(broken) == "other"


# ---------------------------------------------------------------------------------------------- pages
def _expected(path: str) -> dict:
    return json.loads(Path(path).read_text())


@needs_fixtures
@pytest.mark.parametrize("path", [p for p in PAGES if "IREDA" not in p])
def test_the_page_rows_match_the_independent_page_records(path):
    rec = _expected(path)
    info = pdfs.read_pdf(next(CALLS.glob(f"pdf/{rec['file']}")).read_bytes())
    ct = pdfs.call_text(info)
    kept = [d for d in rec["detail"] if d["body_words"] > 0]
    assert [s.pdf_page for s in ct.spans] == [d["page"] for d in kept]            # pages with text, physical numbers
    assert ct.page_map_ok and ct.failed_pages == [] and len(ct.page_rows) == len(kept)
    text = ct.extracted.text
    for s, d in zip(ct.spans, kept, strict=True):
        w = text[s.char_start:s.char_end].split()
        # compared without spacing: the existing hyphen rule joins "quarter -" and "on-quarter." (module 1's, unchanged)
        assert "".join(w[:10]).startswith("".join(d["body_first8"])), (rec["file"], d["page"])
        assert "".join(w[-10:]).endswith("".join(d["body_last8"])), (rec["file"], d["page"])
    assert all(a.char_end == b.char_start and a.char_end > a.char_start for a, b in zip(ct.spans, ct.spans[1:], strict=False))
    assert ct.spans[0].char_start == 0 or text[:ct.spans[0].char_start].strip() == ""
    assert ct.spans[-1].char_end == len(text)


@needs_fixtures
def test_a_paragraph_and_a_hyphenated_word_across_a_page_break_belong_to_the_page_where_the_line_is():
    # Hindalco: a paragraph crosses page breaks; VBL: page 8 ends in a hyphen and page 9 begins in lower case.
    ct = pdfs.call_text(info_of("HINDALCOIND_06112025122106_Novelis_Q2FY26_Transcript_061125_signed.pdf"))
    text = ct.extracted.text
    joined = [s for s in ct.spans if s.char_start > 0 and text[s.char_start - 1] == " "]
    assert joined, "a continuation line joins its paragraph with a space"
    ct = pdfs.call_text(info_of("VBL_04112025104837_TranscriptSE.pdf"))
    text = ct.extracted.text
    p8, p9 = (next(s for s in ct.spans if s.pdf_page == n) for n in (8, 9))
    assert p8.char_end == p9.char_start and text[p8.char_start:p8.char_end].split()[-1] == "-"
    assert text[p9.char_start:].startswith("added dairy")                              # page 9's first kept line
    assert ct.page_map_ok


def test_a_hyphenated_word_and_a_paragraph_across_a_page_break_belong_to_the_page_where_the_line_is():
    speaker = lambda ln: "x" if ln.startswith(("A:", "B:")) else None
    ex, spans = pdf_text_pages(["A: we ship inter-", "national goods and\nthen more", "B: next speaker"], [1, 2, 3], speaker=speaker)
    assert ex.text == "A: we ship inter-national goods and then more\n\nB: next speaker"
    p1, p2, p3 = spans
    assert ex.text[p2.char_start:].startswith("national goods") and ex.text[p2.char_start - 1] == "-"   # no space: the hyphen rule
    assert ex.text[p1.char_start:p1.char_end].endswith("inter-") and p3.char_start == ex.text.index("B:")
    ex, spans = pdf_text_pages(["A: first line of the answer", "continues on the next page."], [3, 4], speaker=speaker)
    assert ex.text[spans[1].char_start - 1] == " " and ex.text[spans[1].char_start:] == "continues on the next page."
    assert "\n\n" not in ex.text                                                          # one paragraph over two pages


def test_a_blank_page_and_a_page_of_furniture_get_no_row():
    pages = ["Header\nA: hello there friend\nPage 1", "", "Header\nPage 3", "Header\nB: and again my friend\nPage 4"] * 1
    pages = [f"Header\nPage {i}" if i == 3 else p for i, p in enumerate(pages, 1)]
    ex, spans = pdf_text_pages(pages, [1, 2, 3, 4], speaker=lambda ln: ln.split(":")[0] if ":" in ln else None)
    assert [s.pdf_page for s in spans] == [1, 4] or [s.pdf_page for s in spans] == [1, 3, 4]
    assert all(s.char_end > s.char_start for s in spans) and spans[-1].char_end == len(ex.text)
    assert 2 not in [s.pdf_page for s in spans]


def test_pdf_text_pages_keeps_the_numbers_it_is_given():
    ex, spans = pdf_text_pages(["A: one two three", "B: four five six"], [5, 9], speaker=lambda ln: "x" if ":" in ln else None)
    assert [s.pdf_page for s in spans] == [5, 9] and ex.text == "A: one two three\n\nB: four five six"
    assert spans == [PageSpan(5, 0, 18), PageSpan(9, 18, len(ex.text))]


@needs_fixtures
def test_a_deliberately_shifted_page_map_fails_the_check_and_writes_no_rows():
    info = info_of("VBL_04112025104837_TranscriptSE.pdf")
    ct = pdfs.call_text(info)
    shifted = [PageSpan(s.pdf_page + 1, s.char_start, s.char_end) for s in ct.spans[:-1]]
    failed = pdfs.check_page_map(ct.extracted.text, shifted, info.page_texts)
    assert len(failed) >= len(shifted) - 1
    bad = pdfs.CallText(ct.extracted, shifted, False, failed)
    assert bad.page_rows == [] and ct.page_rows == ct.spans


@needs_fixtures
def test_module_ones_pdf_text_and_hashes_are_unchanged():
    from pypdf import PdfReader
    golden = json.loads((Path(__file__).parent / "golden_pdf_text.json").read_text())
    assert len(golden) >= 100
    for rel, want in golden.items():
        path = FIXTURES / rel
        if not path.exists():
            continue
        r = PdfReader(str(path))
        if r.is_encrypted:
            r.decrypt("")
        text = pdf_text([p.extract_text() or "" for p in r.pages]).text
        assert hashlib.sha256(text.encode()).hexdigest() == want, rel
    assert sum((FIXTURES / rel).exists() for rel in golden) >= 100


# ---------------------------------------------------------------------------------------------- speaker labels
@pytest.mark.parametrize("line, form", [
    ("Rahul Mehta: Thank you, Operator.", "Name:"),
    ("Moderator: Ladies and gentlemen, good day.", "Moderator:"),
    ("Operator: Thank you. The first question is from", "Operator:"),
    ("Mr. Satish Rao: Good morning.", "Mr./Ms. Name:"),
    ("- Mr. Satyanarayana Raju - MD & CEO, Canara Bank: Thank you.", "- Mr./Ms. Name - Role/Company:"),
    ("Anil Kumar (Jio Platforms): Yes, we", "Name (Company):"),
])
def test_speaker_label_patterns_with_a_colon(line, form):
    n = call_speakers([line])
    assert n.count == 1 and n.patterns == {form: 1}, n
    assert n.label(line)


def test_a_name_alone_on_a_line_counts_only_if_it_repeats_and_is_not_a_running_header():
    body = "Salil Parekh\nThank you for joining us today.\n"
    twice = call_speakers([body, body])
    assert twice.count == 2 and twice.patterns == {"Name (own line)": 2}
    assert twice.label("Salil Parekh") and not twice.label("Priya Shah")
    once = call_speakers([body, "Nilanjan Roy\nGood evening."])
    assert once.count == 0 and not once.label("Salil Parekh")
    header = [f"Salil Parekh\nbody of page {i}\nmore text" for i in range(6)]
    assert call_speakers(header).count == 0                                       # a running header is never a label


def test_other_own_line_forms_a_name_with_a_time_stamp_a_company_and_two_columns():
    stamp = "Sh Anshuman Thakur 00:06:44 - 00:21:15 (Jio Platforms)"
    assert call_speakers([stamp, stamp]).patterns == {"Name hh:mm - hh:mm (own line)": 2}
    comp = "Piyush Choudhary - HSBC"
    assert call_speakers([comp, comp]).patterns == {"Name - Role/Company (own line)": 2}
    col = "Nitin Padmanabhan  Was on the mortgage refinance."
    assert call_speakers([col, col]).patterns == {"Name  text (two-column line)": 2}
    mod = "Moderator"
    assert call_speakers([mod, mod]).patterns == {"Moderator (own line)": 2}
    hon = "Mr. Amit Shah"
    assert call_speakers([hon, hon]).patterns == {"Mr./Ms. Name (own line)": 2}


def test_running_headers_and_ordinary_words_are_not_labels():
    for line in ("Date: 5 November 2025", "Subject: Transcript of the earnings call", "Page 3", "Thank you", "Q2 FY26 Earnings Call",
                 "Tel: 022 1234 5678", "Disclaimer: this document"):
        assert call_speakers([line]).count == 0, line
    assert transcript_speaker("CHAIR POWELL. Good afternoon.") == "CHAIR POWELL"   # the Fed's set is unchanged


# ---------------------------------------------------------------------------------------------- links
@needs_fixtures
def test_an_outlook_safelink_is_unwrapped_and_the_pdf_link_ranks_before_the_homepage():
    atgl = info_of("ATGL_29102024182703_Intimation.pdf")
    links = pdfs.candidate_links(atgl)
    assert links[0].startswith("https://www.adanigas.com/-/media/") and links[0].endswith("Q2-FY25.pdf")
    assert "safelinks" not in " ".join(links) and links[-1] == "https://www.adanigas.com"
    wrapped = ("https://ind01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.example.com%2Fq2.pdf&data=05%7C01")
    assert pdfs.unwrap(wrapped) == "https://www.example.com/q2.pdf"
    assert pdfs.unwrap("https://www.example.com/a") == "https://www.example.com/a"


def test_links_are_ranked_and_recordings_social_and_meeting_links_are_dropped():
    info = pdfs.PdfInfo(True, links_annot=[
        "https://www.co.in", "https://www.co.in/investors", "https://youtu.be/abc", "https://zoom.us/j/1",
        "https://www.co.in/audio/call.mp3", "https://www.linkedin.com/company/x", "mailto:ir@co.in",
        "https://www.co.in/files/Q2-transcript.pdf", "https://www.co.in/investors/earnings-call"],
        links_text=["https://www.co.in/x/other.pdf", "https://www.bseindia.com/corporates/"])
    got = pdfs.candidate_links(info)
    assert got[0] == "https://www.co.in/files/Q2-transcript.pdf"
    assert got.index("https://www.co.in/x/other.pdf") < got.index("https://www.co.in/investors")
    assert got.index("https://www.co.in/investors/earnings-call") < got.index("https://www.co.in")
    assert not any(bad in " ".join(got) for bad in ("youtu", "zoom", ".mp3", "linkedin", "mailto"))
    assert got[-1] in ("https://www.bseindia.com/corporates/", "https://www.co.in")


def test_a_text_url_cut_at_a_line_end_is_rejoined_but_a_following_address_is_not_glued():
    assert pdfs.text_urls("see https://www.co.in/investors/\nearnings/q2.pdf for it") == ["https://www.co.in/investors/earnings/q2.pdf"]
    assert pdfs.text_urls("website www.srf.com\nRegd. Office: Block C") == ["https://www.srf.com"]


# ---------------------------------------------------------------------------------------------- the company PDF tests
CO = {"apollo": pl.CompanyRef(1, "apollo-hospitals", "Apollo Hospitals Enterprise Limited", "APOLLOHOSP"),
      "atgl": pl.CompanyRef(2, "adani-total-gas", "Adani Total Gas Limited", "ATGL"),
      "cipla": pl.CompanyRef(3, "cipla", "Cipla Limited", "CIPLA"),
      "maruti": pl.CompanyRef(4, "maruti-suzuki", "Maruti Suzuki India Limited", "MARUTI"),
      "nestle": pl.CompanyRef(5, "nestle-india", "Nestle India Limited", "NESTLEIND"),
      "hul": pl.CompanyRef(6, "hindustan-unilever", "Hindustan Unilever Limited", "HINDUNILVR")}


@needs_fixtures
def test_a_company_pdf_for_the_wrong_company_or_quarter_is_rejected():
    apollo = info_of("apollohospitals.com_transcript-of-apollo-hospitals---q1-fy27-earnings-call.pdf")
    assert pl.company_pdf_ok(apollo, CO["apollo"], "Q1 FY27")
    assert not pl.company_pdf_ok(apollo, CO["cipla"], "Q1 FY27")                  # the wrong company
    assert not pl.company_pdf_ok(apollo, CO["apollo"], "Q2 FY27")                 # the wrong quarter
    atgl = info_of("adanigas.com_Q2-FY25.pdf")
    assert pl.company_pdf_ok(atgl, CO["atgl"], "Q2 FY25") and not pl.company_pdf_ok(atgl, CO["apollo"], "Q2 FY25")
    letter = info_of("APOLLOHOSP_19082026180025_transcript_Q1FY27.pdf")
    assert not pl.company_pdf_ok(letter, CO["apollo"], "Q1 FY27")                 # a cover letter isn't the full test


def test_the_quarter_is_named_by_its_label_or_its_period_end():
    info = pdfs.PdfInfo(True, page_texts=["Earnings call for the quarter ended September 30, 2026"], quarter="Q2 FY27")
    assert pdfs.names_quarter(info, "Q2 FY27", date(2026, 9, 30)) and not pdfs.names_quarter(info, "Q1 FY27", date(2026, 6, 30))
    info = pdfs.PdfInfo(True, page_texts=["Results for the period ended 30th September 2026"], quarter=None)
    assert pdfs.names_quarter(info, "Q2 FY27", date(2026, 9, 30))
    assert pdfs.names_company("Apollo Hospitals Enterprise Ltd. Q1 FY27", "Apollo Hospitals Enterprise Limited", "APOLLOHOSP")
    assert pdfs.names_company("Welcome to the CIPLA call", "Cipla Limited", "CIPLA")
    assert not pdfs.names_company("Apollo Tyres call", "Apollo Hospitals Enterprise Limited", "APOLLOHOSP")


# ---------------------------------------------------------------------------------------------- the quarter, the date
def res(event_id, day, period_end):
    return rows.ResultsEvent(event_id, datetime(*day, 9, 0, tzinfo=UTC), period_end)


def test_fiscal_labels_and_period_ends():
    assert rows.fiscal_label(date(2026, 9, 30)) == "Q2 FY27" and rows.fiscal_label(date(2026, 3, 31)) == "Q4 FY26"
    assert rows.fiscal_label(date(2026, 6, 30)) == "Q1 FY27" and rows.fiscal_label(date(2026, 12, 31)) == "Q3 FY27"
    assert rows.fiscal_label(date(2026, 8, 31)) is None
    assert rows.period_end_of("Q2 FY27") == date(2026, 9, 30) and rows.period_end_of("Q4 FY26") == date(2026, 3, 31)
    assert rows.period_end_of("bad") is None and rows.season_key("Q2 FY27") == "q2-fy27"


def test_the_quarter_comes_from_the_latest_results_within_45_days_checked_against_the_text():
    r_q1, r_q2 = res(1, (2026, 8, 5), date(2026, 6, 30)), res(2, (2026, 10, 20), date(2026, 9, 30))
    disseminated = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    q = rows.decide_quarter(disseminated, [r_q1, r_q2], "Q2 FY27")
    assert (q.label, q.source, q.results_event_id, q.period_end) == ("Q2 FY27", "results_link", 2, date(2026, 9, 30))
    assert rows.decide_quarter(disseminated, [r_q1, r_q2], None).label == "Q2 FY27"               # not parsed: results alone
    # results filed after the transcript, or more than 45 days before it, are not its results
    assert rows.decide_quarter(disseminated, [res(3, (2026, 11, 2), date(2026, 9, 30))], "Q2 FY27").source == "text"
    assert rows.decide_quarter(disseminated, [r_q1], "Q2 FY27").source == "text"


def test_a_quarter_that_disagrees_with_the_text_holds_the_call():
    q = rows.decide_quarter(datetime(2026, 10, 29, tzinfo=UTC), [res(2, (2026, 10, 20), date(2026, 9, 30))], "Q1 FY27")
    assert q.label is None and q.held_reason == "quarter_conflict" and q.results_event_id == 2 and q.source is None


def test_with_no_results_event_the_parsed_quarter_is_used_and_with_neither_the_call_is_held():
    when = datetime(2026, 10, 29, tzinfo=UTC)
    q = rows.decide_quarter(when, [], "Q2 FY27")
    assert (q.label, q.source, q.period_end, q.held_reason) == ("Q2 FY27", "text", date(2026, 9, 30), None)
    q = rows.decide_quarter(when, [], None)
    assert q.label is None and q.held_reason == "no_quarter"


def ann(kind="intimation", subject="", when=datetime(2026, 10, 20, 8, 0, tzinfo=UTC), key="1", file="x.pdf", url="https://nsearchives.nseindia.com/corporate/x.pdf",
        entity_id=1, exchange="NSE", season=None):
    season = season or dc.season_of_filing(when)
    return dc.Announcement(exchange, key, "SYM", "INE000A01010", None, when.astimezone(dc.INDIA), subject, file, url, kind, "", season, entity_id)


def test_the_call_date_comes_from_the_text_else_an_intimation_else_none():
    filed = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    intim = ann(subject="Intimation of earnings conference call to be held on 27th October 2026 at 4 pm",
                when=datetime(2026, 10, 20, 8, 0, tzinfo=UTC))
    assert rows.decide_call_date(date(2026, 10, 28), [intim], filed) == (date(2026, 10, 28), "text")
    assert rows.decide_call_date(None, [intim], filed) == (date(2026, 10, 27), "intimation")
    assert rows.decide_call_date(None, [], filed) == (None, None)
    # an intimation whose subject names no date gives none (its own filing date is not the call's); one filed more than
    # 10 days before the transcript, or after it, doesn't count
    assert rows.decide_call_date(None, [ann(subject="Intimation of earnings call")], filed) == (None, None)
    old = ann(subject="Earnings call on 8th October 2026", when=datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    assert rows.decide_call_date(None, [old], filed) == (None, None)


# ---------------------------------------------------------------------------------------------- revised versions
def test_a_later_filing_of_the_quarter_is_a_revised_version_by_call_date_or_by_text():
    base = " ".join(f"word{i}" for i in range(400))
    text = "\n\n".join(" ".join(f"w{p}x{i}" for i in range(40)) + "." for p in range(12))
    near = text.replace("w0x0", "changed", 1)
    other = "\n\n".join(" ".join(f"z{p}y{i}" for i in range(40)) + "." for p in range(12))
    assert rows.text_similarity(text, near) >= 0.9 and rows.text_similarity(text, other) < 0.5
    assert rows.is_revised(date(2026, 10, 28), date(2026, 10, 28), text, other)                # the same call date
    assert rows.is_revised(None, None, text, near)                                              # at least 90% the same
    assert not rows.is_revised(date(2026, 10, 28), date(2026, 7, 4), text, other)               # another meeting
    assert not rows.is_revised(None, None, text, other) and base


def test_the_seasons_to_process_newest_first_and_a_weekly_run_skips_older_ones():
    by = {"Q1 FY27": [ann()], "Q4 FY26": [ann()], "Q3 FY26": []}
    assert rows.seasons_to_process(by, None, weekly=False) == ["Q1 FY27", "Q4 FY26"]
    assert rows.seasons_to_process(by, "Q4 FY26", weekly=True) == ["Q1 FY27"]
    assert rows.seasons_to_process(by, "Q1 FY27", weekly=True) == []
    assert rows.seasons_to_process(by, "Q4 FY26", weekly=False) == ["Q1 FY27", "Q4 FY26"]


# ---------------------------------------------------------------------------------------------- the pipeline
NSE = "https://nsearchives.nseindia.com/corporate/"


class Files:
    """A fake getter serving the saved PDFs by URL, and recording what was asked for."""

    def __init__(self, exchange: dict[str, str], company: dict[str, str | bytes] | None = None):
        self.exchange, self.company, self.asked = exchange, company or {}, []

    def __call__(self, url: str, kind: str):
        self.asked.append(url)
        if kind == "exchange":
            name = url.rsplit("/", 1)[-1]
            body = pdf_bytes(self.exchange[name]) if name in self.exchange else None
        else:
            v = self.company.get(url)
            body = pdf_bytes(v) if isinstance(v, str) else v
        return None if body is None else pl.File(url, body, hashlib.sha256(body).digest(), None)


def run_for(getter, curated=None):
    return pl.Run(getter, curated or {}, pl.Report())


def filing(co, file_name, when, kind="transcript", subject="", key=None):
    return ann(kind, subject, when, key or file_name, file_name, NSE + file_name, co.entity_id)


@needs_fixtures
def test_an_exchange_transcript_is_a_stored_call_with_its_text_and_pages():
    name = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"
    when = datetime(2026, 8, 4, 11, 43, tzinfo=UTC)
    f = Files({name: name})
    results = [res(7, (2026, 7, 24), date(2026, 6, 30))]
    plans = pl.process_company(run_for(f), CO["hul"], [filing(CO["hul"], name, when)], results, weekly=False, newest_call=None)
    assert len(plans) == 1
    p = plans[0]
    assert (p.status, p.route, p.season) == ("stored", "exchange_pdf", "Q1 FY27") and p.is_stored
    assert (p.quarter.label, p.quarter.source, p.quarter.results_event_id) == ("Q1 FY27", "results_link", 7)
    t = p.transcript
    assert t.doc_type == "call_transcript" and t.source == "nse" and t.source_url == NSE + name and t.external_key == f"NSE/{name}"
    assert t.text.page_map_ok and len(t.text.page_rows) == 26 and len(t.text.extracted.sha256) == 32
    assert t.text.extracted.extractor_version == "pdf-1/calls-1"


@needs_fixtures
def test_a_cover_letter_is_followed_to_the_companys_own_pdf_which_becomes_the_transcript():
    letter = "APOLLOHOSP_19082026180025_transcript_Q1FY27.pdf"
    url = BY_NAME["apollohospitals.com_transcript-of-apollo-hospitals---q1-fy27-earnings-call.pdf"]["source_url"]
    f = Files({letter: letter}, {url: "apollohospitals.com_transcript-of-apollo-hospitals---q1-fy27-earnings-call.pdf"})
    a = filing(CO["apollo"], letter, datetime(2026, 8, 19, 12, 30, tzinfo=UTC))
    [p] = pl.process_company(run_for(f), CO["apollo"], [a], [], weekly=False, newest_call=None)
    assert (p.status, p.route) == ("stored", "company_link") and p.is_stored
    assert p.cover.doc_type == "call_cover_letter" and p.cover.source == "nse"
    t = p.transcript
    assert t.source == "company_web" and t.source_url == url                          # the company's PDF, never our copy
    assert t.external_key == f"{url}#{t.file.sha256.hex()}" and p.company_url == url
    assert (p.quarter.label, p.quarter.source) == ("Q1 FY27", "text")                # no results event: the parsed quarter
    assert f.asked[1:] == [url]                                                        # the first-ranked link was the PDF


@needs_fixtures
def test_a_safelink_in_a_letter_leads_to_the_pdf():
    letter = "ATGL_29102024182703_Intimation.pdf"
    url = BY_NAME["adanigas.com_Q2-FY25.pdf"]["source_url"]
    f = Files({letter: letter}, {url: "adanigas.com_Q2-FY25.pdf"})
    a = filing(CO["atgl"], letter, datetime(2024, 10, 29, 12, 57, tzinfo=UTC), subject="Transcript of earnings call")
    [p] = pl.process_company(run_for(f), CO["atgl"], [a], [], weekly=False, newest_call=None)
    assert p.status == "stored" and p.transcript.source_url == url and f.asked[1] == url
    assert p.call_date is None or p.call_date_source == "text"


@needs_fixtures
def test_a_letter_that_links_only_to_a_web_page_is_on_the_company_website_and_is_named():
    letter = "MARUTIASHISH_02022026205924_Transcript_02022026.pdf"
    page = "https://www.marutisuzuki.com/corporate/investors/company-reports"
    f = Files({letter: letter}, {page: b"<html>reports</html>"})
    a = filing(CO["maruti"], letter, datetime(2026, 2, 2, 15, 29, tzinfo=UTC))
    run = run_for(f)
    [p] = pl.process_company(run, CO["maruti"], [a], [], weekly=False, newest_call=None)
    assert (p.status, p.route, p.transcript) == ("on_company_website", "none", None) and not p.is_stored
    assert p.company_url == page and p.cover.doc_type == "call_cover_letter"
    assert run.report.unresolved and "maruti-suzuki Q3 FY26" in run.report.unresolved[0] and "marutisuzuki.com web page" in run.report.unresolved[0]


@needs_fixtures
def test_a_dead_link_in_a_letter_is_named_with_its_host():
    letter = "APOLLOHOSP_19082026180025_transcript_Q1FY27.pdf"
    f = Files({letter: letter}, {})
    run = run_for(f)
    [p] = pl.process_company(run, CO["apollo"], [filing(CO["apollo"], letter, datetime(2026, 8, 19, 12, 30, tzinfo=UTC))], [],
                             weekly=False, newest_call=None)
    assert p.status == "on_company_website" and "apollohospitals.com unreachable" in run.report.unresolved[0]


@needs_fixtures
def test_a_curated_link_is_used_when_the_letter_leads_nowhere_and_a_failing_row_is_named(tmp_path):
    letter = "APOLLOHOSP_19082026180025_transcript_Q1FY27.pdf"
    good = "https://www.apollohospitals.com/curated/q1fy27.pdf"
    when = datetime(2026, 8, 19, 12, 30, tzinfo=UTC)
    yaml = tmp_path / "links.yaml"
    yaml.write_text(f"links:\n  - company: apollo-hospitals\n    fiscal_label: Q1 FY27\n    url: {good}\n    found_by: test\n"
                    "    found_on: 2026-11-12\n    note: a test row\n")
    curated = pl.load_curated(yaml)
    f = Files({letter: letter}, {good: "apollohospitals.com_transcript-of-apollo-hospitals---q1-fy27-earnings-call.pdf"})
    [p] = pl.process_company(run_for(f, curated), CO["apollo"], [filing(CO["apollo"], letter, when)], [], weekly=False, newest_call=None)
    assert (p.status, p.route) == ("stored", "curated_link") and p.transcript.source_url == good
    # a row whose file is the wrong company's fails the same tests: named, not used
    wrong = Files({letter: letter}, {good: "adanigas.com_Q2-FY25.pdf"})
    run = run_for(wrong, curated)
    [p] = pl.process_company(run, CO["apollo"], [filing(CO["apollo"], letter, when)], [], weekly=False, newest_call=None)
    assert p.status == "on_company_website" and run.report.curated_failed == ["apollo-hospitals Q1 FY27 (apollohospitals.com)"]
    assert pl.load_curated(tmp_path / "missing.yaml") == {}


def test_the_shipped_curated_file_is_empty_until_2b_08():
    assert pl.load_curated() == {}


@needs_fixtures
def test_image_only_gives_unreadable_and_a_non_transcript_is_named_not_made_a_call(monkeypatch):
    blank = _blank_pdf(3)

    def getter(url, kind):
        return pl.File(url, blank, hashlib.sha256(blank).digest(), None)
    a = filing(CO["cipla"], "CIPLA_scan.pdf", datetime(2026, 7, 27, 12, 0, tzinfo=UTC))
    [p] = pl.process_company(run_for(getter), CO["cipla"], [a], [], weekly=False, newest_call=None)
    assert (p.status, p.route, p.transcript, p.cover) == ("unreadable", "none", None, None)
    nestle = "NESTLEIND1_12052026171823_AnalystMeetOutcome12May2026signed.pdf"
    run = run_for(Files({nestle: nestle}))
    plans = pl.process_company(run, CO["nestle"], [filing(CO["nestle"], nestle, datetime(2026, 5, 12, 12, 0, tzinfo=UTC))], [],
                               weekly=False, newest_call=None)
    assert plans == [] and "nestle-india Q4 FY26" in run.report.not_a_transcript[0]


@needs_fixtures
def test_the_newest_stored_season_wins_and_the_older_one_is_not_fetched():
    q1 = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"
    older = filing(CO["hul"], "HUL_Q4_old.pdf", datetime(2026, 5, 7, 12, 0, tzinfo=UTC))
    newest = filing(CO["hul"], q1, datetime(2026, 8, 4, 11, 43, tzinfo=UTC))
    f = Files({q1: q1})
    plans = pl.process_company(run_for(f), CO["hul"], [older, newest], [], weekly=False, newest_call=None)
    assert [p.season for p in plans] == ["Q1 FY27"] and f.asked == [NSE + q1]


@needs_fixtures
def test_a_newest_season_that_ends_on_the_company_website_brings_in_the_season_before():
    letter = "MARUTIASHISH_02022026205924_Transcript_02022026.pdf"
    page = "https://www.marutisuzuki.com/corporate/investors/company-reports"
    earlier = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"      # stands in for Q2 FY26's file
    f = Files({letter: letter, earlier: earlier}, {page: b"<html></html>"})
    old = filing(CO["maruti"], earlier, datetime(2025, 11, 3, 12, 0, tzinfo=UTC))
    new = filing(CO["maruti"], letter, datetime(2026, 2, 2, 15, 29, tzinfo=UTC))
    plans = pl.process_company(run_for(f), CO["maruti"], [old, new], [], weekly=False, newest_call=None)
    assert [(p.season, p.status) for p in plans] == [("Q3 FY26", "on_company_website"), ("Q2 FY26", "stored")]
    # never more than two seasons
    letters = [filing(CO["maruti"], letter, datetime(y, m, 3, 12, 0, tzinfo=UTC), key=f"l{y}{m}") for y, m in ((2025, 8), (2025, 11), (2026, 2))]
    plans = pl.process_company(run_for(Files({letter: letter}, {page: b"x"})), CO["maruti"], letters, [], weekly=False, newest_call=None)
    assert [p.season for p in plans] == ["Q3 FY26", "Q2 FY26"]


@needs_fixtures
def test_the_weekly_run_skips_a_season_that_is_not_newer_than_the_companys_newest_call():
    name = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"
    f = Files({name: name})
    a = filing(CO["hul"], name, datetime(2026, 8, 4, 11, 43, tzinfo=UTC))
    assert pl.process_company(run_for(f), CO["hul"], [a], [], weekly=True, newest_call="Q1 FY27") == [] and f.asked == []
    assert len(pl.process_company(run_for(f), CO["hul"], [a], [], weekly=True, newest_call="Q4 FY26")) == 1


@needs_fixtures
def test_a_quarter_conflict_holds_the_call_and_it_does_not_count_as_stored():
    name = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"       # the text says Q1 FY27
    when = datetime(2026, 8, 4, 11, 43, tzinfo=UTC)
    run = run_for(Files({name: name}))
    plans = pl.process_company(run, CO["hul"], [filing(CO["hul"], name, when)], [res(9, (2026, 7, 24), date(2026, 3, 31))],
                               weekly=False, newest_call=None)
    [p] = plans
    assert p.status == "stored" and p.status_reason == "quarter_conflict" and not p.is_stored
    assert p.quarter.label is None and run.report.quarter_conflicts and "hindustan-unilever Q1 FY27" in run.report.quarter_conflicts[0]


@needs_fixtures
def test_a_later_filing_of_the_season_is_a_revised_version_or_another_meeting():
    name = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"
    other = "CIPLA_15052026182859_EarningscalltranscriptMay26signedrc.pdf"
    first = filing(CO["hul"], name, datetime(2026, 8, 4, 11, 43, tzinfo=UTC), key="1")
    again = filing(CO["hul"], "HUL_again.pdf", datetime(2026, 8, 5, 10, 0, tzinfo=UTC), key="2")
    meeting = filing(CO["hul"], "HUL_agm.pdf", datetime(2026, 8, 6, 10, 0, tzinfo=UTC), key="3")
    f = Files({name: name, "HUL_again.pdf": name, "HUL_agm.pdf": other})
    run = run_for(f)
    [p] = pl.process_company(run, CO["hul"], [first, again, meeting], [], weekly=False, newest_call=None)
    assert [(x.kind, x.filing.filing_key) for x in p.extras] == [("revised", "2"), ("other_meeting", "3")]
    assert p.filing.filing_key == "1" and p.transcript.external_key == f"NSE/{name}"            # the call keeps its first version
    assert run.report.other_meetings and "HUL_agm.pdf" in run.report.other_meetings[0]


@needs_fixtures
def test_a_first_page_that_says_transcript_makes_a_filing_a_transcript_for_a_season_with_none():
    name = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"
    hidden = ann("other", "Outcome of analyst meet", datetime(2026, 8, 4, 11, 43, tzinfo=UTC), "9", "Outcome_of_meet.pdf",
                 NSE + "Outcome_of_meet.pdf", CO["hul"].entity_id)
    f = Files({"Outcome_of_meet.pdf": name})
    run = run_for(f)
    plans = pl.process_company(run, CO["hul"], [hidden], [res(1, (2026, 7, 24), date(2026, 6, 30))], weekly=False, newest_call=None)
    assert [p.status for p in plans] == ["stored"] and run.report.first_pages == 1
    # an outcome whose first page does not say transcript stays other
    nestle = "NESTLEIND1_12052026171823_AnalystMeetOutcome12May2026signed.pdf"
    o = ann("other", "Analyst meet outcome", datetime(2026, 5, 12, 12, 0, tzinfo=UTC), "8", "Outcome.pdf", NSE + "Outcome.pdf", CO["nestle"].entity_id)
    run = run_for(Files({"Outcome.pdf": nestle}))
    assert pl.process_company(run, CO["nestle"], [o], [res(2, (2026, 5, 10), date(2026, 3, 31))], weekly=False, newest_call=None) == []
    assert run.report.first_pages == 1


def test_at_most_three_first_pages_are_opened_per_company_season():
    others = [ann("other", "con call outcome", datetime(2026, 8, 4, 11, 0, tzinfo=UTC), str(i), f"o{i}.pdf", NSE + f"o{i}.pdf") for i in range(6)]
    f = Files({})
    run = run_for(f)
    pl.first_page_transcripts(run, CO["hul"], "Q1 FY27", others)
    assert len(f.asked) == 3 and run.report.first_pages == 3


def test_a_bse_attachment_is_tried_in_the_live_archive_then_the_history_archive():
    live = dc.BSE_ATTACH[0] + "abc.pdf"
    a = ann("transcript", "t", url=live, exchange="BSE")
    assert pl.exchange_urls(a) == [live, dc.BSE_ATTACH[1] + "abc.pdf"]
    assert pl.exchange_urls(ann(url=NSE + "x.pdf")) == [NSE + "x.pdf"] and pl.exchange_urls(ann(url=None)) == []


def test_two_runs_over_the_same_filings_make_the_same_plan():
    name = "HINDUNILVR_04082026171319_SEIntimationTranscriptofEarningsCallSigned.pdf"
    if not (CALLS / "audit").exists():
        pytest.skip("the saved files aren't checked out")
    a = filing(CO["hul"], name, datetime(2026, 8, 4, 11, 43, tzinfo=UTC))
    [p1] = pl.process_company(run_for(Files({name: name})), CO["hul"], [a], [], weekly=False, newest_call=None)
    [p2] = pl.process_company(run_for(Files({name: name})), CO["hul"], [a], [], weekly=False, newest_call=None)
    assert p1.transcript.text.extracted.sha256 == p2.transcript.text.extracted.sha256
    assert [(s.pdf_page, s.char_start) for s in p1.transcript.text.spans] == [(s.pdf_page, s.char_start) for s in p2.transcript.text.spans]
    assert timedelta(0) == timedelta(0) and os.path.exists(str(CALLS))
