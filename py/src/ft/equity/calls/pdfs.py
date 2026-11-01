"""Reading an earnings-call PDF: its pages, class, links, quarter and call date, and its canonical text with the page map
(Spec 02B §5.2, §13.4; job 2B-07). Ported from the audit kit's `kit/concalls.py` (V2: `analyse_pdf`, `classify_pdf`,
`candidate_links`, `unwrap`, `parse_quarter`, `parse_call_date`), not imported.

Pure: bytes in, facts out. Fetching and registering are the task's.

- **Class** (V2's order): `full` (a text layer, at least 3,000 words after repeated pages are dropped, at least 10
  speaker-label lines) → `image_only` (no text layer) → `cover_letter` (has links, and says "transcript" or links past a
  homepage) → `other`. A PDF pypdf cannot open or decrypt is `other` with `error` set.
- **Text:** repeated pages are dropped by hash (Gland Pharma's repeated cover letter), each page keeps its physical
  number, and `text.py`'s `pdf_text_pages` drops running headers and footers by their position at the page edges and
  joins lines into paragraphs, starting one at each speaker label (the transcript set, `call_speakers`). A two-column
  page is flattened by the text layer ("Name  text" lines); the label set reads those lines.
- **The page map check** (§13.4): for each page's range the first 8 and last 8 words of its text are found, in order, in
  that physical page's own extracted text. A document that fails it gets no page rows.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from ft.engines.document.text import (
    CallSpeakers,
    Extracted,
    PageSpan,
    call_speakers,
    normalize,
    pdf_text_pages,
)

FULL_MIN_WORDS = 3000
FULL_MIN_SPEAKER_LINES = 10
TEXT_LAYER_MIN_LETTERS = 200
TEXT_LAYER_MIN_LETTER_SHARE = 0.5
MAX_PAGES = 200
MAX_LINKS = 6
PARSER_VERSION = "calls-1"                 # stored with the call; the document's extractor_version is pdf-1/calls-1

_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
LINK_SKIP_HOSTS = ("youtube.com", "youtu.be", "zoom.us", "webex.com", "teams.microsoft.com", "teams.live.com",
                   "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "choruscall", "diamondpass",
                   "maps.google", "google.com/maps", "goo.gl/maps", "wa.me", "whatsapp.com")
EXCHANGE_HOSTS = ("nseindia.com", "bseindia.com", "sebi.gov.in")      # an exchange link is not the company's own file
_MEDIA = re.compile(r"\.(mp3|mp4|m4a|wav|aac|wma|mov|avi)(?:\?|$)", re.IGNORECASE)
_ANNOT_URI = re.compile(rb"/URI\s*\(([^)]*)\)")


def is_pdf_bytes(content: bytes | None) -> bool:
    return b"%PDF-" in (content or b"")[:1024]


@dataclass
class PdfInfo:
    """What the kit's `analyse_pdf` recorded, with the physical page numbers the page map needs."""
    is_pdf: bool
    page_texts: list[str] = field(default_factory=list)       # physical page i + 1 = page_texts[i]; "" for a failed page
    pages: int = 0
    encrypted: bool = False
    error: str | None = None
    unreadable: bool = False
    text_layer: bool = False
    dup_pages: list[int] = field(default_factory=list)        # physical numbers of pages that repeat an earlier page
    words: int = 0                                            # after repeated pages are dropped
    speakers: CallSpeakers = field(default_factory=lambda: CallSpeakers(0, {}, frozenset()))
    links_annot: list[str] = field(default_factory=list)
    links_text: list[str] = field(default_factory=list)
    mentions_transcript: bool = False
    call_date: dt.date | None = None
    quarter: str | None = None

    @property
    def kept(self) -> list[int]:
        """The physical numbers of the pages that go into the text (repeated pages dropped)."""
        gone = set(self.dup_pages)
        return [i for i in range(1, self.pages + 1) if i not in gone]

    @property
    def first_pages(self) -> str:
        return "\n".join(self.page_texts[:2])


def _page_key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", text).strip().lower().encode("utf-8", "replace")).hexdigest()


def read_pdf(content: bytes, max_pages: int = MAX_PAGES) -> PdfInfo:
    """Pages, links, class inputs, quarter and call date. Never raises."""
    if not is_pdf_bytes(content):
        return PdfInfo(False)
    info = PdfInfo(True)
    annots: list[str] = []
    page_errors = 0
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(content))
        if rd.is_encrypted:                         # owner-password PDFs open with an empty password
            info.encrypted = True
            try:
                rd.decrypt("")
            except Exception as e:                  # noqa: BLE001
                info.error = f"encrypted: {type(e).__name__}"
        info.pages = min(len(rd.pages), max_pages)
        for i, page in enumerate(rd.pages):
            if i >= max_pages:
                break
            try:
                info.page_texts.append(page.extract_text() or "")
            except Exception as e:                  # noqa: BLE001 - one bad page must not lose the file
                page_errors += 1
                info.error = info.error or f"page {i + 1}: {type(e).__name__}"
                info.page_texts.append("")
            try:
                arr = page.get("/Annots")
                for ref in (arr.get_object() if arr is not None else []) or []:
                    act = ref.get_object().get("/A")
                    uri = act.get_object().get("/URI") if act is not None else None
                    if uri:
                        u = str(uri).strip()
                        u = "https://" + u if u.lower().startswith("www.") else u
                        if u and u not in annots:
                            annots.append(u)
            except Exception:                       # noqa: BLE001, S110 - a bad annotation is no link
                pass
    except Exception as e:                          # noqa: BLE001
        info.error = f"{type(e).__name__}"
        for m in _ANNOT_URI.findall(content):        # salvage link annotations from a file pypdf can't open
            u = m.decode("latin-1", "replace").strip()
            if u and u not in annots:
                annots.append(u)
    full = "\n".join(info.page_texts)
    info.unreadable = bool(info.error) and (not info.page_texts or page_errors >= max(1, len(info.page_texts))
                                            or (info.encrypted and not full.strip()))
    letters = len(re.findall(r"[A-Za-z]", full))
    nonspace = len(re.findall(r"\S", full))
    info.text_layer = letters >= TEXT_LAYER_MIN_LETTERS and (letters / nonspace if nonspace else 0) >= TEXT_LAYER_MIN_LETTER_SHARE
    seen: set[str] = set()
    for n, t in enumerate(info.page_texts, 1):       # a repeated page is dropped (Gland Pharma's repeated cover letter)
        key = _page_key(t)
        if len(t.strip()) >= 20 and key in seen:
            info.dup_pages.append(n)
        else:
            seen.add(key)
    uniq = [info.page_texts[n - 1] for n in info.kept]
    info.words = len(_WORD.findall("\n".join(uniq)))
    info.speakers = call_speakers(uniq)
    info.links_annot = annots
    info.links_text = text_urls(full)
    info.mentions_transcript = "transcript" in full.lower()
    info.call_date = parse_call_date("\n".join(info.page_texts[:2]))
    info.quarter = parse_quarter("\n".join(info.page_texts[:2]))
    return info


def classify_pdf(info: PdfInfo) -> str:
    """full / image_only / cover_letter / other (V2's order)."""
    if not info.is_pdf or info.unreadable:
        return "other"
    if info.text_layer and info.words >= FULL_MIN_WORDS and info.speakers.count >= FULL_MIN_SPEAKER_LINES:
        return "full"
    if not info.text_layer:
        return "image_only"
    links = candidate_links(info)
    if links and (info.mentions_transcript or any(not _is_homepage(u) for u in links)):
        return "cover_letter"
    return "other"


# ---------------------------------------------------------------------------------------------- the canonical text
@dataclass(frozen=True)
class CallText:
    extracted: Extracted
    spans: list[PageSpan]
    page_map_ok: bool
    failed_pages: list[int]                 # physical pages whose range failed the check

    @property
    def page_rows(self) -> list[PageSpan]:
        """The rows to write: none if the page map failed (citations then show no page: absent, never guessed)."""
        return self.spans if self.page_map_ok else []


def call_text(info: PdfInfo) -> CallText:
    """The canonical text of a call PDF with its page map and the check on it."""
    numbers = info.kept
    texts = [info.page_texts[n - 1] for n in numbers]
    ex, spans = pdf_text_pages(texts, numbers, speaker=info.speakers.label)
    ex = Extracted(ex.text, ex.blocks, f"{ex.extractor_version}/{PARSER_VERSION}")
    failed = check_page_map(ex.text, spans, info.page_texts)
    return CallText(ex, spans, not failed, failed)


_JOIN = re.compile(r"[\s\-]+")


def _squash(s: str) -> str:
    """Words compared without spacing or hyphens: a word hyphen-joined at a line or page edge matches either half."""
    return _JOIN.sub("", normalize(s)).lower()


def check_page_map(text: str, spans: list[PageSpan], page_texts: list[str]) -> list[int]:
    """The physical pages whose range fails §13.4's check: the first 8 and the last 8 words of text[char_start:char_end]
    are found, in order, in that page's own extracted text. Independent of how the ranges were made."""
    failed = []
    for sp in spans:
        words = text[sp.char_start:sp.char_end].split()
        page = _squash(page_texts[sp.pdf_page - 1]) if 1 <= sp.pdf_page <= len(page_texts) else ""
        head, tail = _squash("".join(words[:8])), _squash("".join(words[-8:]))
        at = page.find(head) if head else -1
        if at < 0 or (tail and page.find(tail, at) < 0) or sp.char_end <= sp.char_start or not words:
            failed.append(sp.pdf_page)
    return failed


# ---------------------------------------------------------------------------------------------- links
def text_urls(text: str) -> list[str]:
    """URLs in a PDF's text layer. A URL that runs to the end of a line is rejoined with the next line only when it was
    clearly cut (it ends in / - _ = ? & % ~, ends in "." before a lower-case line, or the next line starts with one of
    . / - _ ? = & # %): 'www.srf.com' + 'Regd. Office' must not be glued."""
    out: list[str] = []
    lines = (text or "").splitlines()
    rx = re.compile(r"(?:https?:\s?//|www\.)[^\s<>\"'\])]+", re.IGNORECASE)
    cut_end = re.compile(r"[/\-_=?&%~]$")
    for i, line in enumerate(lines):
        for m in rx.finditer(line):
            u = m.group(0).replace(": //", "://").replace(":// ", "://")
            joined, j = False, i
            while m.end() >= len(line.rstrip()) and j + 1 < len(lines) and not re.search(r"\.(pdf|html?|aspx?|php|jsp)$", u, re.IGNORECASE):
                nxt = lines[j + 1].strip()
                mm = re.match(r"^[\w/%.\-?=&~+#:]+", nxt)
                if not mm or re.match(r"^(?:https?://|www\.)", nxt, re.IGNORECASE):
                    break
                if not (cut_end.search(u) or nxt[:1] in "./-_?=&#%" or (u.endswith(".") and nxt[:1].islower())):
                    break
                u += mm.group(0)
                joined = True
                j += 1
                if mm.end() < len(nxt):
                    break
            v = (u if joined else m.group(0)).replace(" ", "").rstrip(".,;:")
            v = "https://" + v if v.lower().startswith("www.") else v
            if v not in out:
                out.append(v)
    return out


def unwrap(u: str) -> str:
    """Outlook 'safelinks' wrap the company's own link: use the URL inside."""
    try:
        p = urlparse(u)
        if p.netloc.lower().endswith("safelinks.protection.outlook.com"):
            inner = parse_qs(p.query).get("url", [None])[0]
            if inner and inner.startswith("http"):
                return inner
    except ValueError:
        pass
    return u


def host_of(url: str | None) -> str:
    try:
        h = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    return h.removeprefix("www.")


def _is_homepage(u: str) -> bool:
    p = urlparse(u)
    return (p.path or "") in ("", "/") and not p.query


def _skip_link(u: str) -> bool:
    ul = u.lower()
    if not ul.startswith(("http://", "https://")):
        return True
    if _MEDIA.search(urlparse(ul).path or ""):
        return True                                  # an audio or video recording is never the transcript
    return any(h in ul for h in LINK_SKIP_HOSTS)


def candidate_links(info: PdfInfo) -> list[str]:
    """Links worth following, best first: PDFs and "transcript" links, then other pages, homepages last. Annotations
    come before text links at equal score; recordings, social, video and meeting links are dropped."""
    cands: list[tuple[int, int, str]] = []
    for order, links in ((0, info.links_annot), (1, info.links_text)):
        for u in links:
            u = unwrap(u.strip())
            if _skip_link(u) or any(u == c[2] for c in cands):
                continue
            p = urlparse(u)
            path = (p.path or "").lower()
            score = 0
            if path.endswith(".pdf"):
                score += 3
            if "transcript" in u.lower():
                score += 2
            if re.search(r"call|earning|concall|investor|result", u.lower()):
                score += 1
            if path in ("", "/") and not p.query:
                score -= 3
            if any(h in p.netloc.lower() for h in EXCHANGE_HOSTS):
                score -= 2
            cands.append((score, order, u))
    cands.sort(key=lambda c: (-c[0], c[1]))
    return [c[2] for c in cands]


# ---------------------------------------------------------------------------------------------- dates and quarters
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
_DATE_PATTERNS = [
    (rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS})[,.]?\s+(\d{{4}})", "dmy"),
    (rf"({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", "mdy"),
    (r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", "num"),
]


def _to_date(groups, kind) -> dt.date | None:
    try:
        if kind == "dmy":
            return dt.datetime.strptime(f"{groups[0]} {groups[1][:3]} {groups[2]}", "%d %b %Y").date()
        if kind == "mdy":
            return dt.datetime.strptime(f"{groups[1]} {groups[0][:3]} {groups[2]}", "%d %b %Y").date()
        return dt.date(int(groups[2]), int(groups[1]), int(groups[0]))
    except ValueError:
        return None


def parse_date_at(fragment: str) -> dt.date | None:
    """The date a fragment starts with ("5th November 2025, at 4 pm" -> 2025-11-05)."""
    for pat, kind in _DATE_PATTERNS:
        mm = re.match(r"\s*" + pat, fragment, re.IGNORECASE)
        if mm and (d := _to_date(mm.groups(), kind)):
            return d
    return None


def parse_call_date(text: str) -> dt.date | None:
    """The call date from "held on <date>" / "conducted on <date>" / "call on <date>" in the first pages."""
    t = re.sub(r"\s+", " ", text or "")
    pat = (r"(?:held|conducted|hosted|organi[sz]ed|scheduled)\s+on\s+(?:\w+day,?\s+)?(.{6,40})"
           r"|call\s+(?:dated|on)\s+(?:\w+day,?\s+)?(.{6,40})")
    for m in re.finditer(pat, t, re.IGNORECASE):
        d = parse_date_at(m.group(1) or m.group(2) or "")
        if d and 2018 <= d.year <= 2030:
            return d
    return None


def parse_quarter(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"\bQ\s*([1-4])\s*[-' ]?\s*(?:&\s*\d?\s*M\s*)?FY\s*'?(\d{2,4})", t, re.IGNORECASE)
    if m:
        return f"Q{m.group(1)} FY{m.group(2)[-2:]}"
    m = re.search(r"quarter (?:and (?:half|nine months) year |and year )?ended (?:on )?(.{6,30})", t, re.IGNORECASE)
    if m and (d := parse_date_at(m.group(1))):
        fq = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month)
        return f"Q{fq} FY{(d.year + 1 if d.month >= 4 else d.year) % 100:02d}" if fq else None
    return None


def dates_in(text: str) -> set[dt.date]:
    """Every date written in `text` in the forms above (for the "names the right quarter" test by period end)."""
    t = re.sub(r"\s+", " ", text or "")
    out = set()
    for pat, kind in _DATE_PATTERNS:
        for mm in re.finditer(pat, t, re.IGNORECASE):
            if d := _to_date(mm.groups(), kind):
                out.add(d)
    return out


# ---------------------------------------------------------------------------------------------- the company PDF tests
_SUFFIX = re.compile(r"\b(limited|ltd|co|corporation|corp|company|private|pvt|inc|plc)\b\.?", re.IGNORECASE)


def names_company(text: str, name: str, symbol: str) -> bool:
    """The company's name or NSE symbol on the pages: its symbol as a word, its name without the legal suffix, or the
    first two words of that name (a PDF may drop "Enterprises")."""
    squash = re.sub(r"[^a-z0-9]+", " ", text.lower())
    sym = re.sub(r"[^a-z0-9]+", " ", symbol.lower()).strip()
    base = re.sub(r"[^a-z0-9]+", " ", _SUFFIX.sub(" ", name).lower()).strip()
    first_two = " ".join(base.split()[:2])
    for cand in (sym if len(sym) >= 3 else "", base, first_two if len(first_two) >= 6 else ""):
        if cand and re.search(rf"(?<![a-z0-9]){re.escape(cand)}(?![a-z0-9])", squash):
            return True
    return False


def names_quarter(info: PdfInfo, label: str, period_end: dt.date | None) -> bool:
    """The right quarter on the first two pages: its label (Q2 FY27) or its period end date."""
    if info.quarter == label:
        return True
    return period_end is not None and period_end in dates_in(info.first_pages)
