"""FOMC press-conference transcripts (Prompt 01 B5 part 2; Spec 01 §1.1 "Fed press conference"; Data Audit 01 #2):
the press-conference page's transcript link and the PDF's checks, on synthetic pages (policy_docs), then on the pages
and PDFs saved on the Mac (app/data/fixtures/fed, git-ignored) when they're on this machine."""
import os
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from policy_docs import fed_presconf_page, fed_transcript_pdf, tiny_pdf

from ft.policy import fed_transcript as ft

PAGE = "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20400314.htm"
PDF = "/mediacenter/files/FOMCpresconf20400314.pdf"


def test_the_page_links_the_transcript_once_it_is_posted():
    assert ft.pdf_link(fed_presconf_page("March 13-14, 2040", PDF).encode(), PAGE) == "https://www.federalreserve.gov" + PDF
    lower = "/mediacenter/files/fomcpresconf20400314.pdf"                    # Jul 2024's link is lower-case
    assert ft.pdf_link(fed_presconf_page("March 13-14, 2040", lower).encode(), PAGE).endswith(lower)
    assert ft.pdf_link(fed_presconf_page("March 13-14, 2040", None).encode(), PAGE) is None     # not posted yet
    # 16 Sep 2026: the PDF's path is in the page, but only inside an HTML comment: not linked, so not posted.
    assert ft.pdf_link(fed_presconf_page("March 13-14, 2040", PDF, commented=True).encode(), PAGE) is None


def test_a_page_that_isnt_a_press_conference_page_or_links_two_transcripts_is_refused():
    with pytest.raises(ft.TranscriptError):
        ft.pdf_link(b"<html><body><p>Maintenance</p></body></html>", PAGE)
    two = fed_presconf_page("March 13-14, 2040", PDF).replace(
        "</h4>", '</h4><p><a href="/mediacenter/files/other.pdf">Transcript (PDF)</a></p>')
    with pytest.raises(ft.TranscriptError, match="2 transcript links"):
        ft.pdf_link(two.encode(), PAGE)


def test_a_transcript_reads_its_day_and_speakers_and_passes():
    tr = ft.parse(fed_transcript_pdf("March 14, 2040"), "https://www.federalreserve.gov" + PDF)
    assert (tr.day, tr.chair, tr.external_key) == (date(2040, 3, 14), "CHAIR LANE", PDF)
    assert set(tr.speakers) == {"CHAIR LANE", "MICHELLE SMITH", "ADA REPORTER"}
    assert "Page 1 of" not in tr.text.text and tr.text.extractor_version == "pdf-1"
    assert all(r.passed for r in ft.checks(tr, date(2040, 3, 14)))
    bad = {r.name for r in ft.checks(tr, date(2040, 3, 15)) if not r.passed}
    assert bad == {"transcript_date"}


def test_a_short_transcript_or_one_without_the_chair_fails():
    short = ft.parse(fed_transcript_pdf("March 14, 2040", turns=4), "https://www.federalreserve.gov" + PDF)
    assert {r.name for r in ft.checks(short, date(2040, 3, 14)) if not r.passed} == {"transcript_text"}
    nochair = ft.parse(fed_transcript_pdf("March 14, 2040", chair="ADA REPORTER"), "https://www.federalreserve.gov" + PDF)
    assert {r.name for r in ft.checks(nochair, date(2040, 3, 14)) if not r.passed} == {"transcript_chair"}


def test_a_file_that_isnt_a_pdf_with_text_is_refused():
    with pytest.raises(ft.TranscriptError, match="not a PDF"):
        ft.parse(b"<html>moved</html>", PDF)
    with pytest.raises(ft.TranscriptError):
        ft.parse(tiny_pdf([[""]]), PDF)


# ---------------------------------------------------------------------------------------------- the saved pages
FIXTURES = Path(os.environ.get("FT_FIXTURES", Path(__file__).resolve().parents[2] / "data" / "fixtures"))
FED = FIXTURES / "fed"


@pytest.mark.skipif(not (FED / "FOMCpresconf20260729.pdf").exists(),
                    reason="the saved pages are only on the Mac (app/data/fixtures, git-ignored)")
def test_every_saved_transcript_the_pages_link_passes():
    """The press-conference pages saved 23 Oct 2026 (Jan 2019 to Sep 2026): 62 link a transcript, Sep 2026's doesn't
    (its link is commented out); every linked PDF that was saved passes. Jul 2024's link is lower-case, which the
    fixtures script saves only since 24 Oct 2026, so it's counted when it's there."""
    pages = sorted(FED.glob("fomcpres*conf*.htm"), key=lambda p: re.search(r"(\d{8})", p.name)[1])
    assert len(pages) == 63
    linked, passed = {}, []
    for p in pages:
        d8 = re.search(r"(\d{8})", p.name)[1]
        link = ft.pdf_link(p.read_bytes(), "https://www.federalreserve.gov/monetarypolicy/" + p.name)
        linked[d8] = link
        pdf = FED / link.rsplit("/", 1)[1] if link else None
        if pdf is None or not pdf.exists():
            continue
        tr = ft.parse(pdf.read_bytes(), link)
        bad = [r for r in ft.checks(tr, datetime.strptime(d8, "%Y%m%d").date()) if not r.passed]
        assert not bad, (d8, bad)
        passed.append(d8)
    assert [d for d, link in linked.items() if link is None] == ["20260916"]
    jul24 = (FED / "fomcpresconf20240731.pdf").exists()
    assert linked["20240731"].endswith("/mediacenter/files/fomcpresconf20240731.pdf")
    assert len(passed) == 61 + jul24 and ("20240731" in passed) == jul24
    warsh = ft.parse((FED / "FOMCpresconf20260617.pdf").read_bytes(), "x.pdf")
    assert warsh.chair == "CHAIRMAN WARSH" and warsh.text.text.startswith(
        "Transcript of Chairman Warsh's Press Conference June 17, 2026 CHAI\n\nRMAN WARSH. Good day.")   # as the PDF has it
