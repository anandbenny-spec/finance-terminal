"""The Governor's statements and the MPC press-conference transcripts (Spec 01 §1.1 "RBI documents", "RBI transcripts",
"Canonical text"; Prompt 01 B6 part 3; Anand's decision of 23 Oct 2026 in GitHub issue #15: transcripts from Dec 2018,
not Feb 2023). Parses one page into what policy.rbi_documents stores and runs the checks that need nothing but the page,
the meeting's decision date and the Governor's names.

Which document is a meeting's is the Annual Policy page's call (rbi_calendar: the statement in the decision's group, the
transcript in its own group days later). Three page templates on the old site, each with an HTML body, so the canonical
text is always the page's (the PDF is only for a page without one, and none of the 92 saved lacks one):
- `BS_PressReleaseDisplay.aspx?prid=<id>`: the Governor's statements from Aug 2020 (but Oct 2020), in the resolution's
  template (header rows: PDF link, "Date : Oct 07, 2026", title; body `rbi_press_release`; RBI's press-release number
  at the end);
- `bs_viewcontent.aspx?Id=<id>`: the statements to Oct 2020 and the transcripts to Apr 2022 (header: the PDF link and
  a <th> title, no date row, no press-release number; body `rbi_view_content`);
- `BS_SpeechesView.aspx?Id=<id>`: the transcripts from Jun 2022 (the press release's header, no number; body
  `rbi_speech`).
Checked on the saved pages (Mac: app/data/fixtures/rbi/, INDEX.tsv; 23 Oct 2026): 48 statements (Feb 2019 to Oct 2026;
the Dec 2018 baseline has none) and 44 transcripts (Dec 2018 to Oct 2026: none for the four 2020 COVID meetings, 27 Mar,
22 May, 6 Aug and 9 Oct, a video address only, or the off-cycle 4 May 2022).

**Dates.** A statement is the decision day's: its page date and the date its title names, where it has them, are the
decision date (the viewcontent pages to Aug 2019 carry neither; the Annual Policy group they sit in dates them). A
transcript names its press conference's date: the title from Dec 2020 ("… Press Conference: December 04, 2020"), the
first line before ("December 05, 2018"); that's the decision date. Jun 2022's names none: its page date (the posting
day) is then within rbi_calendar.TRANSCRIPT_WITHIN days on or after the decision.

**Speakers.** A transcript's turns open with the speaker's name and a colon (text.rbi_transcript_speaker); the chunks
carry it (`chunk_text(…, speakers="rbi_transcript")`), and the Governor on the roster that day must be one of them.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from lxml import etree
from lxml import html as lxml_html

from ft.engines.document.text import Extracted, ExtractError, extract_html, normalize_inline, rbi_transcript_speaker
from ft.policy import members, rbi_calendar, rbi_resolution
from ft.policy.fed_statement import Result

BASE = rbi_calendar.BASE
STATEMENT_MIN_CHARS = 5_000          # the shortest of the 48 saved statements holds 10,275 characters (Feb 2019)
TRANSCRIPT_MIN_CHARS = 10_000        # the shortest of the 44 saved transcripts holds 27,505 (Oct 2021)
PROFILES = {"prid": "rbi_press_release", "view": "rbi_view_content", "speech": "rbi_speech"}

_PRID = re.compile(r"/BS_PressReleaseDisplay\.aspx\?(?:.*&)?prid=(\d+)", re.IGNORECASE)
_VIEW = re.compile(r"/bs_viewcontent\.aspx\?(?:.*&)?Id=(\d+)", re.IGNORECASE)
_SPEECH = re.compile(r"/BS_SpeechesView\.aspx\?(?:.*&)?Id=(\d+)", re.IGNORECASE)
_MPC = re.compile(r"\bMonetary Policy Committee\b|\bMPC\b")
_DATE = re.compile(r"Date\s*:\s*([A-Z][a-z]{2})\w*\s+(\d{1,2}),\s*(\d{4})")
_SIZE = re.compile(r"\(\s*\d+(?:\.\d+)?\s*[kKmM][bB]\s*\)")      # the PDF link's size: "(411 kb)"
_TB = "contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')"
_TH = "contains(concat(' ', normalize-space(@class), ' '), ' tableheader ')"


class StatementError(ValueError):
    """The page can't be read as a Governor's statement or a transcript (a template change, or another page): nothing
    is stored."""


def template(url: str) -> str:
    """'prid' | 'view' | 'speech': which of the old site's templates the link is. Raises StatementError otherwise."""
    for name, p in (("prid", _PRID), ("view", _VIEW), ("speech", _SPEECH)):
        if p.search(url):
            return name
    raise StatementError(f"not an old-site press-release, viewcontent or speech link: {url!r}")


def doc_url(url: str) -> str:
    """The canonical link, whatever host or case the Annual Policy page or the feed wrote: www.rbi.org.in/Scripts/
    BS_PressReleaseDisplay.aspx?prid=N, bs_viewcontent.aspx?Id=N or BS_SpeechesView.aspx?Id=N."""
    kind = template(url)
    if kind == "prid":
        return rbi_resolution.page_url(url)
    n = (_VIEW if kind == "view" else _SPEECH).search(url)[1]
    return f"{BASE}{'bs_viewcontent.aspx' if kind == 'view' else 'BS_SpeechesView.aspx'}?Id={n}"


def external_key(url: str) -> str:
    """The stable key at the source (core.document.external_key): the canonical link's path and its id."""
    u = urlsplit(doc_url(url))
    return f"{u.path}?{u.query}"


@dataclass(frozen=True)
class Doc:
    kind: str                        # 'statement' | 'transcript'
    url: str                         # canonical
    external_key: str
    title: str                       # the page's own
    page_date: date | None           # the header's "Date :" row (prid and speech pages)
    named_date: date | None          # the date the document names: its title's, else (a transcript) its first line's
    text: Extracted
    press_release: str | None        # "2026-2027/1266" (prid pages only: Brief §6.2, named, never linked)
    speakers: Counter                # a transcript's speaker labels -> turns


def header(data: bytes) -> tuple[str, date | None]:
    """(the title, the page's date): the press release's header cells (the PDF link's "(411 kb)", "Date : Oct 07,
    2026", the title, which can be as short as "Governor’s Statement", May 2022), else a viewcontent page's <th> title
    (it has no date row)."""
    try:
        doc = lxml_html.fromstring(data)
    except (ValueError, etree.ParserError) as e:
        raise StatementError(f"not an HTML page: {e}") from None
    outer = f"//table[{_TB}][not(ancestor::table[{_TB}])]"
    cells = [normalize_inline(td.text_content()) for td in doc.xpath(f"{outer}//td[{_TH}]")]
    if not cells and not doc.xpath(outer):
        raise StatementError("no tablebg table: not an old-site document page (or its template changed)")
    page_date = None
    titles = []
    for c in cells:
        if m := _DATE.search(c):
            page_date = page_date or datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%b %d %Y").date()
        elif c and not _SIZE.fullmatch(c):
            titles.append(c)
    th = [normalize_inline(e.text_content()) for e in doc.xpath(f"{outer}/tr/th | {outer}/tbody/tr/th")]
    title = (titles or [t for t in th if t] or [""])[-1]
    if not title:
        raise StatementError("the page has no title row (not an old-site document page, or its template changed)")
    return title, page_date


def parse(data: bytes, url: str, kind: str) -> Doc:
    """A Governor's statement (`kind` 'statement') or a press-conference transcript ('transcript') page."""
    if kind not in ("statement", "transcript"):
        raise ValueError(f"kind must be 'statement' or 'transcript', not {kind!r}")
    url = doc_url(url)
    title, page_date = header(data)
    try:
        ex = extract_html(data, PROFILES[template(url)])
    except ExtractError as e:
        raise StatementError(str(e)) from None
    named = rbi_calendar.title_date(title)
    speakers: Counter = Counter()
    if kind == "transcript":
        if named is None and ex.blocks:
            first = ex.block_text(ex.blocks[0]).split("\n")[0]
            named = rbi_calendar.title_date(first) if len(first) <= 40 else None
        speakers = Counter(s for b in ex.blocks if (s := rbi_transcript_speaker(ex.block_text(b))))
    press = rbi_resolution.press_release_no(ex.text) if template(url) == "prid" else None
    return Doc(kind, url, external_key(url), title, page_date, named, ex, press, speakers)


def statement_checks(st: Doc, decision_date: date) -> list[Result]:
    """The blocking statement checks: the MPC's statement (its length, naming the Committee) of the decision day."""
    dates = {"the page's date": st.page_date, "its title's date": st.named_date}
    wrong = [f"{what} is {d}" for what, d in dates.items() if d is not None and d != decision_date]
    stated = [what for what, d in dates.items() if d is not None]
    return [
        Result("statement_text", st.text.char_count > STATEMENT_MIN_CHARS and bool(_MPC.search(st.text.text)),
               f"{st.text.char_count} characters" + ("" if _MPC.search(st.text.text) else
                                                       "; it never names the Monetary Policy Committee")),
        Result("statement_date", not wrong, "; ".join(wrong) + f"; the decision {decision_date}" if wrong else
               (f"{' and '.join(stated)} {decision_date}" if stated else
                "undated: the Annual Policy page's decision group dates it")),
    ]


def governor_keys(roster: members.Roster, d: date) -> set[str]:
    """The name keys (name and aliases) of the Governor on the roster on the day: whom a transcript's labels must
    include."""
    out = set()
    for m in roster.members.values():
        t = m.term_on(d) if m.bank == "RBI" else None
        if t is not None and t.role == "rbi_governor":
            out |= {members.key(m.name), *map(members.key, m.aliases)}
    return out


def transcript_checks(tr: Doc, decision_date: date, governor: set[str], posted: date | None = None) -> list[Result]:
    """The blocking transcript checks: its length; the press conference it names is the decision day's (or, naming
    none, it was posted within rbi_calendar.TRANSCRIPT_WITHIN days of it); the Governor speaks."""
    if tr.named_date is not None:
        ok, detail = tr.named_date == decision_date, f"it names {tr.named_date}, the decision {decision_date}"
    else:
        when = tr.page_date or posted
        ok = when is not None and decision_date <= when <= decision_date + timedelta(rbi_calendar.TRANSCRIPT_WITHIN)
        detail = (f"it names no date; posted {when}, the decision {decision_date} (within "
                  f"{rbi_calendar.TRANSCRIPT_WITHIN} days)")
    gov = sorted(s for s in tr.speakers if members.key(members.strip_title(s)) in governor)
    return [
        Result("transcript_text", tr.text.char_count > TRANSCRIPT_MIN_CHARS, f"{tr.text.char_count} characters"),
        Result("transcript_date", ok, detail),
        Result("transcript_governor", bool(gov), f"{len(tr.speakers)} speakers; the Governor: "
               + (", ".join(gov) if gov else "not among them")),
    ]
