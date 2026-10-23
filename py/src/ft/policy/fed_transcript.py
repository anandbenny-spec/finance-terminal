"""FOMC press-conference transcripts (Spec 01 §1.1 "Fed press conference", "Canonical text"; Data Audit 01 #2;
Prompt 01 B5 part 2).

The calendar links each meeting's press-conference page (`/monetarypolicy/fomcpresconfYYYYMMDD.htm`; spelled
"fomcpressconf" once, Jan 2026), which links the transcript once it's posted, days after the meeting: "Press Conference
Transcript (PDF)" (`/mediacenter/files/FOMCpresconfYYYYMMDD.pdf`, lower-case once, Jul 2024). The canonical text is
the PDF's (text.extract_pdf: the running header and page numbers dropped, a paragraph per speaker turn, "CHAIR
POWELL." and "MICHELLE SMITH." starting new ones).

Checked on the 61 transcripts saved 23 Oct 2026 (Jan 2019 to Sep 2026; Jul 2024's PDF wasn't saved): each over 13,000
characters, its first line naming the press conference and its day ("Transcript of Chair Powell's Press Conference
January 30, 2019"; "… Press Conference Call March 15, 2020"), the Chair speaking. Jun 2026's text layer splits the
Chair's first label across two lines ("CHAI" / "RMAN WARSH."): the text keeps it as the PDF has it.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

from lxml import etree
from lxml import html as lxml_html

from ft.engines.document.text import Extracted, ExtractError, extract_pdf, normalize_inline, transcript_speaker
from ft.policy.fed_statement import Result

MIN_CHARS = 10_000        # Data Audit 01 #2 (F1's pass mark); the shortest saved transcript (3 Mar 2020) holds 13,225
_HEADER = re.compile(r"\bTranscript of [^\n]{0,80}?\bPress Conference(?: Call)?\s+(?P<day>[A-Z][a-z]+ \d{1,2}, \d{4})")
_CHAIR = re.compile(r"^(?:CHAIR|CHAIRMAN|CHAIRWOMAN) [A-Z]")


class TranscriptError(ValueError):
    """The press-conference page or the PDF can't be read (a template change, or not a transcript): nothing stored."""


def pdf_link(data: bytes, page_url: str) -> str | None:
    """The transcript PDF the press-conference page links ("Press Conference Transcript (PDF)"), or None while it
    isn't posted. Raises TranscriptError if the page isn't a Fed page with an article."""
    try:
        doc = lxml_html.fromstring(data)
    except (ValueError, etree.ParserError) as e:
        raise TranscriptError(f"not an HTML page: {e}") from None
    if not doc.xpath("//div[@id='article']"):
        raise TranscriptError("no div#article: not a Fed press-conference page (or its template changed)")
    found = []
    for a in doc.xpath("//div[@id='article']//a[@href]"):
        href = urljoin(page_url, a.get("href").strip()).split("?", 1)[0]
        if href.lower().endswith(".pdf") and "transcript" in normalize_inline(a.text_content()).lower():
            found.append(href)
    links = list(dict.fromkeys(found))
    if len(links) > 1:
        raise TranscriptError(f"{len(links)} transcript links on the press-conference page: {links}")
    return links[0] if links else None


@dataclass(frozen=True)
class Transcript:
    url: str                         # the PDF's URL
    external_key: str                # its path: core.document.external_key
    day: date | None                 # the day the first line names
    text: Extracted
    speakers: Counter                # speaker label -> turns

    @property
    def chair(self) -> str | None:
        return next((s for s, _ in self.speakers.most_common() if _CHAIR.match(s)), None)


def parse(data: bytes, url: str) -> Transcript:
    """A transcript PDF. Raises TranscriptError if it isn't a PDF with text."""
    if not data.startswith(b"%PDF"):
        raise TranscriptError("not a PDF")
    try:
        ex = extract_pdf(data)
    except ExtractError as e:
        raise TranscriptError(str(e)) from None
    except Exception as e:  # noqa: BLE001 - pypdf raises its own errors on a damaged file
        raise TranscriptError(f"the PDF can't be read: {type(e).__name__}: {e}") from None
    m = _HEADER.search(ex.text[:400])
    day = None
    if m:
        try:
            day = datetime.strptime(m["day"], "%B %d, %Y").date()
        except ValueError:
            day = None
    speakers = Counter(s for b in ex.blocks if (s := transcript_speaker(ex.text[b.start:b.end])))
    return Transcript(url, urlsplit(url).path, day, ex, speakers)


def checks(tr: Transcript, decision_date: date) -> list[Result]:
    """The blocking transcript checks: the text's length, its first line names the decision day, the Chair speaks."""
    return [
        Result("transcript_text", tr.text.char_count > MIN_CHARS, f"{tr.text.char_count} characters"),
        Result("transcript_date", tr.day == decision_date, f"the transcript is dated {tr.day}, the decision "
               f"{decision_date}"),
        Result("transcript_chair", tr.chair is not None, f"{len(tr.speakers)} speakers; the Chair: {tr.chair}"),
    ]
