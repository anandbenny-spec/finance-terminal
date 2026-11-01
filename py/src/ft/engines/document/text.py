"""Canonical text: the versioned extractor (Spec 01 §1.1, "Canonical text"; Architecture 01 §1.3, §1.8).

A document's canonical text is what its chunks, citations and ¶ links point into: the press-release body of the HTML
page, or the PDF where there's no HTML body (Fed press-conference transcripts). Offsets count Unicode code points
(Python's len, Postgres's char_length). The version is stored on the document (`extractor_version`) together with
the body profile, e.g. "html-1/rbi_press_release", so the text can be reproduced from its raw file. A rule change that
alters any text is a new version; a document already chunked keeps its text (new text is a new document version).

The text's shape: blocks (paragraphs, headings, list items, tables) separated by one blank line ("\\n\\n"); lines inside
a block (a <br>, a table row) by "\\n"; table cells by " | ". Normalised: NFC; every Unicode space becomes " ", runs
collapse and lines are trimmed; curly quotes and primes become ' and "; zero-width characters, soft hyphens and
control characters are dropped; the ff/fi/fl ligatures are spelled out. Dashes, the minus sign, ₹ and other symbols
stay as they are. Decoding uses only the bytes (BOM, meta charset, else UTF-8, else Windows-1252), never an HTTP
header, so the raw file alone reproduces the text.
"""
from __future__ import annotations

import bisect
import codecs
import hashlib
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

HTML_VERSION = "html-1"
PDF_VERSION = "pdf-1"


class ExtractError(Exception):
    """The page doesn't have the shape the profile expects (a template change): fail the task, store nothing."""


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    kind: str          # 'para' | 'heading' | 'item' | 'table'
    level: int = 0     # headings: 1-6 for <h1>-<h6>, 7 for a paragraph that is all bold (a run-in heading)


@dataclass(frozen=True)
class Extracted:
    text: str
    blocks: tuple[Block, ...]
    extractor_version: str

    @property
    def sha256(self) -> bytes:                 # what core.document.text_sha256 holds
        return hashlib.sha256(self.text.encode("utf-8")).digest()

    @property
    def char_count(self) -> int:               # code points, as Postgres's char_length
        return len(self.text)

    def block_text(self, b: Block) -> str:
        return self.text[b.start:b.end]


# ---------------------------------------------------------------------------------------------- normalisation
# Code points, not literals, so nothing invisible sits in this file.
_SPACES_IN = (0x09, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000)      # tab and Unicode spaces
_DROPPED = (0xAD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)                              # soft hyphen, zero-widths, BOM
_SINGLE_QUOTES = (0x2018, 0x2019, 0x201A, 0x201B, 0x2032)
_DOUBLE_QUOTES = (0x201C, 0x201D, 0x201E, 0x201F, 0x2033)
_LINE_BREAKS = (0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029)
_TABLE = {
    **dict.fromkeys(_SPACES_IN, " "), **dict.fromkeys(_DROPPED), **dict.fromkeys(_SINGLE_QUOTES, "'"),
    **dict.fromkeys(_DOUBLE_QUOTES, '"'), **dict.fromkeys(_LINE_BREAKS, "\n"),
    0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl", 0xFB03: "ffi", 0xFB04: "ffl",
}
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x84\x86-\x9f\ud800-\udfff]")
_SPACES = re.compile(r" {2,}")


def normalize(s: str) -> str:
    """Normalise characters, keeping line breaks: each line's spaces collapsed and its ends trimmed."""
    s = _CONTROL.sub("", s.replace("\r\n", "\n").translate(_TABLE))
    s = unicodedata.normalize("NFC", s)
    return "\n".join(_SPACES.sub(" ", line).strip() for line in s.split("\n"))


def normalize_keeping_spaces(s: str) -> str:
    """`normalize` without collapsing a line's inner spaces: the same lines, one for one (two-column speaker lines)."""
    s = _CONTROL.sub("", s.replace("\r\n", "\n").translate(_TABLE))
    s = unicodedata.normalize("NFC", s)
    return "\n".join(line.strip() for line in s.split("\n"))


def normalize_inline(s: str) -> str:
    """Normalise HTML text: any whitespace, line breaks included, is one space (the browser's rule)."""
    return " ".join(normalize(s).split())


# ---------------------------------------------------------------------------------------------- HTML
@dataclass(frozen=True)
class HtmlProfile:
    name: str
    body: str          # XPath to the element(s) holding the body; outermost matches only, in document order
    note: str = ""


# Each document task adds its page's profile with tests against saved pages (Prompt 01 §1.3): the Fed's (B5) with
# B3's fixtures. A profile whose body isn't found fails the task rather than storing the page's furniture.
PROFILES: dict[str, HtmlProfile] = {
    "rbi_press_release": HtmlProfile(
        "rbi_press_release",
        "//table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')]"
        "/tr[contains(concat(' ', normalize-space(@class), ' '), ' tablecontent1 ')]/td"
        " | //table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')]"
        "/tbody/tr[contains(concat(' ', normalize-space(@class), ' '), ' tablecontent1 ')]/td",
        "RBI's old site (BS_PressReleaseDisplay.aspx): the row below the PDF link, date and title rows. Checked on the "
        "module 1 kit's nine saved press-release pages, 2019-2025 (23 Oct 2026)."),
    "rbi_view_content": HtmlProfile(
        "rbi_view_content",
        "//table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')]"
        "[not(ancestor::table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')])]"
        "/tr[th]/following-sibling::tr[1]/td"
        " | //table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')]"
        "[not(ancestor::table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')])]"
        "/tbody/tr[th]/following-sibling::tr[1]/td",
        "RBI's old site, bs_viewcontent.aspx (the Governor's statements to Oct 2020, the press-conference transcripts "
        "to Apr 2022): the outer tablebg table's row after its title row (a <th>, below the PDF link; no date row, no "
        "tablecontent1 class). A transcript's speakers sit in a nested table, a name cell beside each turn. Checked on "
        "the 29 saved pages (12 statements, 17 transcripts), Dec 2018 to Apr 2022 (B6 part 3, 24 Oct 2026)."),
    "rbi_speech": HtmlProfile(
        "rbi_speech",
        "//table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')]"
        "/tr[contains(concat(' ', normalize-space(@class), ' '), ' tablecontent2 ')]/td"
        " | //table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')]"
        "/tbody/tr[contains(concat(' ', normalize-space(@class), ' '), ' tablecontent2 ')]/td",
        "RBI's old site, BS_SpeechesView.aspx (the press-conference transcripts from Jun 2022): the press release's "
        "header (PDF link, date, title) above a tablecontent2 row. Checked on the 27 saved transcripts, Jun 2022 to "
        "Oct 2026 (B6 part 3, 24 Oct 2026)."),
    "fed_press_release": HtmlProfile(
        "fed_press_release",
        "//div[@id='article']/div[contains(concat(' ', normalize-space(@class), ' '), ' col-sm-8 ')]"
        "[not(contains(concat(' ', normalize-space(@class), ' '), ' heading '))]"
        "/*[not(starts-with(normalize-space(.), 'For media inquiries'))]"
        "[not(starts-with(normalize-space(.), 'Implementation Note issued'))]"
        "[not(contains(concat(' ', normalize-space(@class), ' '), ' panel-attachments '))]",
        "The Fed's press releases (/newsevents/pressreleases/*.htm): the body column of div#article, without the "
        "heading column (date, title, release time: read from the page by ft.policy.fed_statement) and without the "
        "closing lines: the media contact (its address is a Cloudflare placeholder), the implementation-note link and "
        "the related-links panel (15 Mar 2020). Checked on the 69 saved monetary-policy releases, Dec 2018 to Sep 2026 "
        "(B5, 23 Oct 2026)."),
    "fed_minutes": HtmlProfile(
        "fed_minutes",
        "//div[@id='article']/*[not(self::h3)][not(self::hr)][not(preceding-sibling::hr)]",
        "The FOMC's minutes (/monetarypolicy/fomcminutesYYYYMMDD.htm): div#article without its title (the <h3>, read "
        "by ft.policy.fed_minutes) and without the footnotes below the rule (<hr>) that follows the Secretary's "
        "signature, so the minutes end with the signature. Checked on the 62 saved minutes, Jan 2019 to Sep 2026 "
        "(B5 part 2, 24 Oct 2026)."),
}

_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "dl", "dt", "dd", "blockquote", "pre",
          "section", "article", "header", "footer", "main", "aside", "nav", "figure", "figcaption", "address",
          "center", "form", "fieldset", "caption", "details", "summary", "hr", "table", "tr", "thead", "tbody", "tfoot"}
_SKIP = {"script", "style", "noscript", "template", "head", "title", "meta", "link", "iframe", "object", "embed",
         "svg", "math", "img", "input", "select", "option", "button", "textarea", "map", "area", "audio", "video",
         "canvas", "picture", "source"}
_BOLD = {"b", "strong"}
_BLOCKISH = {"p", "div", "table", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section"}
_HIDDEN_CLASS = {"sr-only", "visually-hidden", "visuallyhidden", "hidden"}
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")
_WINDOWS = {"iso-8859-1", "iso8859-1", "latin-1", "latin1", "us-ascii", "ascii", "l1"}   # browsers read these as 1252


def decode_html(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if m := _META_CHARSET.search(data[:4096]):
        name = m.group(1).decode("ascii", "replace").lower()
        name = "cp1252" if name in _WINDOWS else name
        try:
            return data.decode(codecs.lookup(name).name, "replace")
        except LookupError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _hidden(el) -> bool:
    if el.get("hidden") is not None or (el.get("aria-hidden") or "").strip().lower() == "true":
        return True
    if "display:none" in (el.get("style") or "").replace(" ", "").lower():
        return True
    return bool(_HIDDEN_CLASS & set((el.get("class") or "").lower().split()))


def _is_layout_table(table) -> bool:
    """A table whose own cells hold blocks (paragraphs, tables, lists) lays the page out; its cells are containers.
    Otherwise it's a data table, kept as rows of cells."""
    for cell in table.iter("td", "th"):
        if next(cell.iterancestors("table"), None) is not table:
            continue
        if any(isinstance(d.tag, str) and d.tag.lower() in _BLOCKISH for d in cell.iterdescendants()):
            return True
    return False


class _Builder:
    """Collects blocks of lines from a walk over the body."""

    def __init__(self):
        self.blocks: list[tuple[str, int, list[str]]] = []
        self._lines: list[str] = []
        self._pieces: list[str] = []
        self._kind, self._level = "para", 0
        self._open = False
        self._bold = self._chars = 0
        self._brs = 0

    def text(self, s: str | None, *, bold: bool, kind: str, level: int) -> None:
        if not s:
            return
        if not self._open:
            if not s.strip():
                return
            self._open, self._kind, self._level = True, kind, level
        self._pieces.append(s)
        n = sum(not c.isspace() for c in s)
        self._chars += n
        self._bold += n if bold else 0
        self._brs = 0 if s.strip() else self._brs

    def line_break(self) -> None:
        """A <br> or a table row's end. A second <br> in a row ends the block."""
        if not self._open:
            return
        line = normalize_inline("".join(self._pieces))
        self._pieces = []
        if line:
            self._lines.append(line)
            self._brs = 1
        else:
            self._brs += 1
            if self._brs >= 2:
                self.end_block()

    def table(self, rows: list[str]) -> None:
        self.end_block()
        if rows:
            self.blocks.append(("table", 0, rows))

    def end_block(self) -> None:
        if self._open:
            line = normalize_inline("".join(self._pieces))
            if line:
                self._lines.append(line)
            if self._lines:
                kind, level = self._kind, self._level
                one = " ".join(self._lines)
                if (kind == "para" and len(self._lines) == 1 and self._chars and self._bold == self._chars
                        and len(one.split()) <= 15 and not one.endswith((".", ";", ","))):
                    kind, level = "heading", 7
                self.blocks.append((kind, level, self._lines))
        self._lines, self._pieces, self._open = [], [], False
        self._bold = self._chars = self._brs = 0


def _inline_text(el) -> str:
    """A data-table cell's text: everything visible inside it, on one line."""
    parts = [el.text or ""]
    for child in el:
        tag = child.tag.lower() if isinstance(child.tag, str) else None
        if tag is not None and tag not in _SKIP and not _hidden(child):
            parts.append(" " if tag == "br" else _inline_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _table_rows(table) -> list[str]:
    """A data table as lines: one per row, its non-empty cells joined by " | "."""
    rows = []
    for tr in table.iter("tr"):
        if next(tr.iterancestors("table"), None) is not table or _hidden(tr):
            continue
        cells = [normalize_inline(_inline_text(c)) for c in tr if isinstance(c.tag, str) and c.tag.lower() in ("td", "th")
                 and not _hidden(c)]
        if line := " | ".join(c for c in cells if c):
            rows.append(line)
    return rows


def _walk(el, out: _Builder, *, bold=False, kind="para", level=0) -> None:
    tag = el.tag.lower() if isinstance(el.tag, str) else None
    if tag is None or tag in _SKIP or _hidden(el):
        return                                     # comments, scripts, hidden text (the caller emits the tail)
    if tag == "br":
        out.line_break()
        return
    bold = bold or tag in _BOLD
    if tag == "table" and not _is_layout_table(el):
        out.table(_table_rows(el))
        return
    if tag in _BLOCK or tag in ("td", "th"):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            kind, level = "heading", int(tag[1])
        elif tag == "li":
            kind, level = "item", 0
        out.end_block()
        if tag != "hr":
            _children(el, out, bold=bold, kind=kind, level=level)
            out.end_block()
        return
    _children(el, out, bold=bold, kind=kind, level=level)


def _children(el, out: _Builder, **ctx) -> None:
    out.text(el.text, bold=ctx["bold"], kind=ctx["kind"], level=ctx["level"])
    for child in el:
        _walk(child, out, **ctx)
        out.text(child.tail, bold=ctx["bold"], kind=ctx["kind"], level=ctx["level"])


def _assemble(blocks: list[tuple[str, int, list[str]]], version: str) -> Extracted:
    parts, out, pos = [], [], 0
    for kind, level, lines in blocks:
        body = "\n".join(lines)
        if not body:
            continue
        if parts:
            pos += 2
        out.append(Block(pos, pos + len(body), kind, level))
        parts.append(body)
        pos += len(body)
    return Extracted("\n\n".join(parts), tuple(out), version)


def extract_html(data: bytes, profile: HtmlProfile | str) -> Extracted:
    """The canonical text of the body the profile names. Raises ExtractError if the page has no such body or it
    holds no text."""
    import lxml.html

    p = PROFILES[profile] if isinstance(profile, str) else profile
    doc = lxml.html.document_fromstring(_XML_DECL.sub("", decode_html(data), count=1))
    found = doc.xpath(p.body)
    ids = {id(e) for e in found}
    roots = [e for e in found if not any(id(a) in ids for a in e.iterancestors())]
    if not roots:
        raise ExtractError(f"profile {p.name}: no body found")
    out = _Builder()
    for root in roots:
        out.end_block()
        _children(root, out, bold=False, kind="para", level=0)
        out.end_block()
    ex = _assemble(out.blocks, f"{HTML_VERSION}/{p.name}")
    if not ex.text:
        raise ExtractError(f"profile {p.name}: the body holds no text")
    return ex


# ---------------------------------------------------------------------------------------------- PDF
_PAGE_NO = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.IGNORECASE)
_SPEAKER = re.compile(r"^([A-Z][A-Z'.-]*(?: [A-Z][A-Z'.-]*){0,5})\. (?=\S)")


def transcript_speaker(paragraph: str) -> str | None:
    """The speaker label opening a transcript paragraph ("CHAIR POWELL. Good afternoon." -> "CHAIR POWELL").
    Provisional: B5 checks it on the Fed's transcripts (F1 parsed 61 of 62)."""
    m = _SPEAKER.match(paragraph)
    if not m:
        return None
    name = m.group(1)
    return name if sum(c.isalpha() for c in name) >= 4 and any(len(w) >= 2 for w in name.split()) else None


# The RBI's press-conference transcripts (bs_viewcontent.aspx to Apr 2022, BS_SpeechesView.aspx from Jun 2022) label a
# turn with the speaker's name and a colon, alone in its paragraph ("Shaktikanta Das:", a name cell beside the turn) or
# opening it ("Govardhan Rangan: Economic Times Thank you, Governor."), sometimes with a role or outlet in brackets
# ("Yogesh Dayal (Moderator):"). A label is one to six capitalised words or initials. The participants list's own
# labels ("Participants from the RBI:", "Moderator:") aren't turns. Checked on the 44 saved transcripts (Dec 2018 to
# Oct 2026; B6 part 3, 24 Oct 2026): every label found is a person's name, and none is missed in a sentence such as
# "To summarise:" (a lower-case word).
_RBI_WORD = r"(?:[A-Z][A-Za-z'’\-]*\.?|[A-Z]\.(?:[A-Z]\.)*)"
_RBI_SPEAKER = re.compile(rf"^(?P<name>{_RBI_WORD}(?:[ ]{_RBI_WORD}){{0,5}})(?:[ ]?\([^()\n]{{1,40}}\))?[ ]?:(?=\s|$)")
_RBI_NOT_A_TURN = re.compile(r"^(?:Participants?\b.*\bfrom\b.*|Moderator)$"          # the participants list
                             r"|^(?:The|A|An|This|That|These|Those|Our|My|Its|In|On|At|As|For|Of|And|But|So|To|We|I)\b",
                             re.IGNORECASE)                                            # a run-in phrase, not a name


def rbi_transcript_speaker(paragraph: str) -> str | None:
    """The speaker label opening an RBI transcript paragraph ("Yogesh Dayal (Moderator):" -> "Yogesh Dayal"), without
    its bracket; None if the paragraph doesn't open with one."""
    m = _RBI_SPEAKER.match(paragraph)
    if not m or _RBI_NOT_A_TURN.match(m["name"]) or len(m["name"]) > 60:
        return None
    return m["name"]


# Earnings-call transcripts (Spec 02B §5.3; ported from the audit kit's `kit/concalls.py`, V2's two runs, Data Audit 05
# §6.2): the third speaker-label set, beside the Fed's and the RBI's. The forms seen in the 21 Oct 2026 run:
#   "Name:" and "Moderator:"; "Mr./Ms. Name:"; "- Mr. Name - MD & CEO, Company:" (a bullet with honorific and role);
#   "Name hh:mm - hh:mm (Company)" (a name with time stamps); "Name - Company" alone on its line; a name alone on its
#   line (Infosys, Hindalco); and the two-column "Name  text" (Mphasis, flattened by the text layer).
# A name alone on a line is a label only if that name stands alone at least twice in the document and isn't a running
# header or footer (at a page's first or last two lines on half the pages or more): a participants list names each
# person once. Running headers are never labels.
_CALL_BULLET = re.compile(r"^[\s\-\u2212\u2013\u2014\u2022\u00b7*\u25cf\u25aa]+")
_CALL_HON = r"(?:Mr|Ms|Mrs|Dr|Shri|Smt|Sh|Sri|Prof|Mr\s*&\s*Mrs)\.?"
_CALL_TOKEN = r"(?:[A-Z][A-Za-z.'\u2019\-]*|[A-Z]\.?)"
_CALL_NAME = rf"{_CALL_TOKEN}(?:\s+{_CALL_TOKEN}){{0,4}}"
_CALL_DASH = r"[-\u2013\u2014]"
_CALL_LABEL = re.compile(rf"^({_CALL_HON}\s+)?({_CALL_NAME})\s*(\([^)]{{0,80}}\))?\s*({_CALL_DASH}\s*[^:\n]{{1,120}}?)?\s*[:\uff1a](?:\s|$)")
_CALL_ALONE = re.compile(
    rf"^({_CALL_HON}\s+)?({_CALL_NAME})\s*(\d{{1,2}}:\d{{2}}(?::\d{{2}})?\s*{_CALL_DASH}\s*\d{{1,2}}:\d{{2}}(?::\d{{2}})?)?\s*"
    rf"(\([^)]{{0,60}}\))?\s*({_CALL_DASH}\s*[A-Z][^:\n]{{0,60}})?\s*$")
_CALL_GAP = re.compile(r"^((?:Mr|Ms|Mrs|Dr|Shri|Smt|Sh|Sri|Prof)\.?\s+)?([A-Z][a-z][A-Za-z.'\u2019\-]*(?:\s[A-Z][a-z][A-Za-z.'\u2019\-]*){1,3})\s{2,}\S")
_CALL_STOP = {
    "date", "dated", "subject", "sub", "to", "ref", "reference", "dear", "encl", "enclosure", "enclosures", "note", "website", "web",
    "email", "e-mail", "mail", "tel", "telephone", "phone", "fax", "mobile", "cin", "address", "scrip code", "scrip", "symbol", "time",
    "venue", "disclaimer", "page", "regd office", "registered office", "corporate office", "place", "for", "yours", "annexure",
    "link", "url", "dial", "conference id", "isin", "series", "attention", "kind attn", "kind attention", "attn", "re", "copy to",
    "nse symbol", "bse scrip code", "bse code", "nse code", "company secretary", "transcript", "audio", "video", "duration",
    "location", "participants", "management", "speakers", "moderator for the call", "q", "a", "ans", "question", "answer",
    "call participants", "presentation link", "meeting video", "weblink", "web link", "registered office address", "compere"}
_CALL_NOT_NAME = {
    "limited", "ltd", "ltd.", "bank", "india", "page", "transcript", "call", "calls", "earnings", "earning", "conference", "quarter",
    "results", "result", "financial", "financials", "highlights", "performance", "revenue", "business", "overview", "agenda",
    "disclaimer", "management", "participants", "participant", "question", "questions", "answer", "answers", "session", "thank",
    "thanks", "slide", "presentation", "update", "updates", "segment", "outlook", "key", "summary", "group", "company",
    "corporation", "industries", "services", "securities", "capital", "private", "analyst", "analysts", "investor", "investors",
    "opening", "remarks", "closing", "statement", "statements", "review", "fy", "q1", "q2", "q3", "q4", "h1", "h2", "the", "and",
    "annexure", "notes", "note", "contents", "table", "total", "growth", "strategy", "operations", "operational", "market",
    "markets", "portfolio", "outcome", "board", "meeting", "director", "directors", "secretary", "office", "regd", "registered",
    "website", "email", "tel", "fax", "cin", "news", "times", "standard", "express", "mint", "hindu", "reuters", "bloomberg",
    "moneycontrol", "businessline", "economic", "plc", "inc", "llp", "pvt", "co", "co.", "energy", "power", "motors", "steel",
    "copyright", "reserved", "rights", "confidential", "final", "draft", "edited", "unedited", "verbatim", "moderated"}


def _call_label(raw: str) -> tuple[str, str] | None:
    """(form, key) of a speaker label opening `raw`, or None. `form` names the pattern (as the audit kit counted them);
    for a name alone on its line the key is the lower-cased name and the label counts only if it repeats."""
    bullet = bool(_CALL_BULLET.match(raw)) and not raw[:1].isalpha()
    line = _CALL_BULLET.sub("", raw).strip()
    if not line:
        return None
    m = _CALL_LABEL.match(line)
    if m:
        lab = m.group(2).strip()
        if lab.lower() in _CALL_STOP or len(lab) < 2 or all(w.lower().strip(".") in _CALL_NOT_NAME for w in lab.split()):
            return None
        if lab.lower() in ("moderator", "operator"):
            return lab.capitalize() + ":", lab.lower()
        return (("- " if bullet else "") + ("Mr./Ms. " if m.group(1) else "") + "Name" + (" (Company)" if m.group(3) else "")
                + (" - Role/Company" if m.group(4) else "") + ":"), lab.lower()
    m = _CALL_ALONE.match(line)
    gap = None if m else _CALL_GAP.match(_CALL_BULLET.sub("", raw).rstrip())
    if not (m or gap):
        return None
    m = m or gap
    name = m.group(2).strip()
    toks, low = name.split(), name.lower()
    if low in ("moderator", "operator"):
        form = "Moderator (own line)"
    elif 2 <= len(toks) <= 4 and not any(t.lower().strip(".") in _CALL_NOT_NAME for t in toks) \
            and any(len(t.strip(".")) > 1 for t in toks):
        form = ("Name  text (two-column line)" if gap else "Name hh:mm - hh:mm (own line)" if m.group(3)
                else "Name - Role/Company (own line)" if m.group(5) else "Mr./Ms. Name (own line)" if m.group(1)
                else "Name (own line)")
    else:
        return None
    return form, "alone:" + low


@dataclass(frozen=True)
class CallSpeakers:
    """The speaker-label lines of one transcript: how many, in which forms, and a reader for `pdf_text_pages`."""
    count: int
    patterns: dict[str, int]
    names: frozenset[str]            # the lower-cased names that stand alone on their lines at least twice

    def label(self, line: str) -> str | None:
        """The speaker label opening `line` ("Name:" forms always; a name alone on its line if it repeats), or None."""
        found = _call_label(line)
        if not found:
            return None
        _, key = found
        return key.removeprefix("alone:") if (not key.startswith("alone:") or key[6:] in self.names) else None


def call_speakers(pages: list[str]) -> CallSpeakers:
    """Count a transcript's speaker-label lines over its page texts (the audit kit's V2 count)."""
    n, forms, patterns = 0, {}, Counter()
    where: dict[str, list[int]] = {}
    edge: Counter = Counter()
    for pi, page in enumerate(pages):
        plines = [x for x in page.splitlines() if x.strip()]
        edges = {_CALL_BULLET.sub("", x).strip().lower() for x in plines[:2] + plines[-2:]}
        for raw in page.splitlines():
            found = _call_label(raw)
            if not found:
                continue
            form, key = found
            if not key.startswith("alone:"):
                n += 1
                patterns[form] += 1
                continue
            where.setdefault(key, []).append(pi)
            forms[key] = form
            if _CALL_BULLET.sub("", raw).strip().lower() in edges:
                edge[key] += 1
    total = max(1, len(pages))
    names = set()
    for key, hits in where.items():
        if len(hits) < 2:
            continue
        if total >= 3 and edge[key] >= max(3, 0.5 * total):
            continue                                     # a running header or footer
        n += len(hits)
        patterns[forms[key]] += len(hits)
        names.add(key[6:])
    return CallSpeakers(n, dict(patterns), frozenset(names))


def _furniture(pages: list[list[str]]) -> set[str]:
    """Lines that open or close at least 60% of the pages (three or more): running headers and footers. Digits are
    masked, so "Page 3 of 25" and a dated header match across pages."""
    if len(pages) < 3:
        return set()
    seen: dict[str, int] = {}
    for lines in pages:
        for key in {re.sub(r"\d", "#", ln) for ln in lines[:2] + lines[-2:]}:
            seen[key] = seen.get(key, 0) + 1
    return {k for k, n in seen.items() if n >= max(3, 0.6 * len(pages))}


@dataclass(frozen=True)
class PageSpan:
    """The code points of the canonical text that belong to one physical PDF page (Spec 02B §13.4): from the page's
    first kept line to the next kept page's first line (the last page: to the end of the text)."""
    pdf_page: int          # 1-based physical page number
    char_start: int
    char_end: int


def pdf_text_pages(pages: list[str], numbers: list[int] | None = None,
                   speaker=None, labels: list | None = None,
                   label_raw: bool = False) -> tuple[Extracted, list[PageSpan]]:
    """Canonical text from a PDF's page texts: page furniture dropped (running headers and footers, page numbers),
    lines joined into paragraphs. A paragraph ends at a blank line and a new one starts at a speaker label (`speaker`,
    a function line -> label or None; the Fed's reader by default); a line ending in "-" before a lower-case letter
    joins without a space (the hyphen is kept).

    `numbers` gives each page's physical number (default 1, 2, ...), for callers that drop whole pages first (repeated
    pages). The second result has one span per page that keeps at least one line, ordered by page; a paragraph that
    crosses a page break belongs, from its continuation line on, to the page where that line is.

    `labels`, if given a list, is filled with one entry per block of the text: the speaker label that opens the block's
    first line (what `speaker` returns for it), or None. Turns and chunks read it, so they agree with the text's own
    paragraph breaks (a paragraph that starts at a label).

    `label_raw` hands `speaker` each line with its inner spaces kept (the lines are the same, one for one): a transcript
    whose text layer sets speaker and speech in two columns ("Name  text") is read by its double space, which the text
    itself collapses."""
    speaker = speaker or transcript_speaker
    numbers = list(range(1, len(pages) + 1)) if numbers is None else numbers
    norm = [[ln for ln in normalize(p).split("\n")] for p in pages]
    raw = [normalize_keeping_spaces(p).split("\n") for p in pages] if label_raw else norm
    furniture = _furniture([[ln for ln in p if ln] for p in norm])
    paras: list[list[tuple[int, str, str]]] = []    # each line with the physical page it came from, and as the reader sees it
    cur: list[tuple[int, str, str]] = []
    for number, lines, raw_lines in zip(numbers, norm, raw, strict=True):
        nonblank = [i for i, ln in enumerate(lines) if ln]
        edge = set(nonblank[:2] + nonblank[-2:])
        for i, ln in enumerate(lines):
            if not ln:
                if cur:
                    paras.append(cur)
                    cur = []
                continue
            if i in edge and (_PAGE_NO.match(ln) or re.sub(r"\d", "#", ln) in furniture):
                continue
            if cur and speaker(raw_lines[i]):
                paras.append(cur)
                cur = []
            cur.append((number, ln, raw_lines[i]))
    if cur:
        paras.append(cur)
    blocks, line_at = [], []                        # line_at: (page, offset of the line inside its paragraph's text)
    for para in paras:
        text, offsets = para[0][1], [(para[0][0], 0)]
        for number, ln, _ in para[1:]:
            glue = "" if text.endswith("-") and ln[:1].islower() else " "
            text += glue + ln
            offsets.append((number, len(text) - len(ln)))
        blocks.append(("para", 0, [text]))
        line_at.append(offsets)
    ex = _assemble(blocks, PDF_VERSION)
    if labels is not None:
        labels[:] = [speaker(para[0][2]) for para in paras]
    first: dict[int, int] = {}
    for block, offsets in zip(ex.blocks, line_at, strict=True):
        for number, off in offsets:
            first.setdefault(number, block.start + off)
    starts = sorted(first.items(), key=lambda kv: kv[1])
    spans = [PageSpan(number, start, starts[i + 1][1] if i + 1 < len(starts) else len(ex.text))
             for i, (number, start) in enumerate(starts)]
    return ex, spans


def pdf_text(pages: list[str]) -> Extracted:
    """Canonical text from a PDF's page texts (see `pdf_text_pages`, whose text this is)."""
    return pdf_text_pages(pages)[0]


def extract_pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    ex = pdf_text([page.extract_text() or "" for page in reader.pages])
    if not ex.text:
        raise ExtractError("the PDF holds no extractable text")
    return ex


# ---------------------------------------------------------------------------------------------- sentences and words
# Spec 01 §2 ("Diff (S)"): U.S., Dr., Prof., Shri, Smt., No.; plus titles, times, Latin abbreviations and months.
# Starting values, tuned on the backfill (Design 01 §10.6). Initials (H., U.S., J.P.) never end a sentence either.
ABBREVIATIONS = frozenset({
    "u.s.", "dr.", "prof.", "shri.", "smt.", "no.", "nos.", "mr.", "mrs.", "ms.", "jr.", "sr.", "st.", "vs.",
    "e.g.", "i.e.", "a.m.", "p.m.", "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "sept.", "oct.",
    "nov.", "dec.",
})
_END = re.compile(r"[.!?]+[\"')\]]*(?=\s)")
_INITIALS = re.compile(r"^(?:[A-Z]\.)+$")
_MARKER = re.compile(r"^(?:\d{1,3}|[ivxlc]{1,5}|[IVXLC]{1,5}|[a-z])\.$")
WORD = re.compile(r"\w+(?:[.,'/-]\w+)*")


def words(s: str) -> list[str]:
    return WORD.findall(s)


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def sentence_spans(text: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """Sentences in text[start:end] as (start, end) offsets, without surrounding whitespace. A line break always
    ends a sentence (blocks and table rows never share one)."""
    end = len(text) if end is None else end
    spans: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        nl = text.find("\n", pos, end)
        line_end = end if nl < 0 else nl
        s = pos
        for m in _END.finditer(text, pos, line_end):
            nxt = m.end()
            while nxt < line_end and text[nxt].isspace():
                nxt += 1
            if nxt >= line_end:
                break                                   # the line's end closes the sentence anyway
            if not (text[nxt].isupper() or text[nxt].isdigit() or text[nxt] in "\"'(["):
                continue
            tok_start = max(text.rfind(" ", s, m.start()) + 1, s)
            token = text[tok_start:m.start() + 1].lstrip("\"'([")
            if m.group().startswith("."):
                if token.lower() in ABBREVIATIONS or _INITIALS.match(token):
                    continue
                if _MARKER.match(token) and not text[s:tok_start].strip():
                    continue                            # "2. The MPC ..." : a paragraph number, not a sentence
            a, b = _trim(text, s, m.end())
            if a < b:
                spans.append((a, b))
            s = nxt
        a, b = _trim(text, s, line_end)
        if a < b:
            spans.append((a, b))
        pos = line_end + 1
    return spans


def paragraph_starts(text: str) -> list[int]:
    """Where each block starts (blocks are separated by "\\n\\n"): the ¶ numbers, 1-based by position in this list."""
    starts, i = [0], text.find("\n\n")
    while i >= 0:
        starts.append(i + 2)
        i = text.find("\n\n", i + 2)
    return starts


def paragraph_of(starts: list[int], offset: int) -> int:
    """The 1-based ¶ number of the block holding `offset`."""
    return bisect.bisect_right(starts, offset)
