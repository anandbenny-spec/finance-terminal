"""MPC resolutions (Spec 01 §1.1 "RBI documents", "RBI RSS", "Canonical text"; §2 "RBI rates", "RBI votes (D4)",
"Dissents (F23)", "Projections"; §3.3; Prompt 01 B6). Parses one resolution page into what policy.rbi_documents stores
and runs the checks that need nothing but the page and the meeting's decision date.

The page is the old site's press release (`BS_PressReleaseDisplay.aspx?prid=<id>`, the Annual Policy page's link): a
header (the PDF link, "Date : Oct 07, 2026", the title) above the body cell; the canonical text is the body
(text.PROFILES["rbi_press_release"]). Checked on the 49 resolutions saved 23 Oct 2026, Dec 2018 to Oct 2026 (the
Mac's app/data/fixtures/rbi/, INDEX.tsv).

**Rates** are read from the decision part (the text before the first heading after the repo rate's first mention):
exactly the instruments it states (repo; reverse repo before 8 Apr 2022, SDF from then; MSF; Bank Rate), in both
wordings ("keep … unchanged at 6.5 per cent", "reduce … by 25 basis points from 6.5 per cent to 6.25 per cent", "to
6.0 per cent from 6.25 per cent", "the MSF rate and the Bank Rate to 6.50 per cent", "the SDF rate … will be at 3.75
per cent"). Each instrument's value, its stated change ("by N basis points | bps", signed by the clause's verb) and its
stated previous value ("from X per cent") are kept with the sentence they came from.

**Stance:** the decision part's stance sentence, read into the closed vocabulary (§3.3): "from A to B" gives B.

**Votes** (Spec 01 §2; Anand's decisions of 23 Oct 2026 in GitHub issue #15): vote sentences name members ("Dr. A,
Prof. B and Shri C voted to …"; "Four members (…) voted …, while two members (…) voted …") or speak for all of them
("All members of the MPC – … – unanimously voted …", "The decision to change the monetary policy stance was
unanimous", "the MPC voted unanimously to …"). "All members" are the members the resolution names: the attendance
sentence (from Feb 2025: "under the chairmanship of X, Governor … The MPC members A, … and E attended the meeting"),
else the six named in its vote sentences; a resolution that names fewer leaves them to the roster (D4, gated: the
task). Rate votes: a change in bps, the decision, or "against the repo rate hike" (no change stated). Stance votes: a
vocabulary label, or 'against' ("voted against this part of the resolution", "expressed reservations on this part of
the resolution", "voting against this formulation"); the minutes never record stance votes (one Yes/No table, on the
rate), so these are the record.

**Projections** (CPI and real GDP growth): both word orders ("Q2 at 7.2 per cent", "26.2 per cent in Q1"),
quarters, halves and full years, ranges as low and high ("3.2-3.4 per cent", "5.2 per cent to 5.0 per cent", "in the
range of 26.2 to 8.3 per cent"), signs ("(-)7.5 per cent"), "Q3 and Q4 at 6.5 per cent each", "for Q1:2026-27 and
Q2 … at 3.9 per cent and 4.0 per cent, respectively". Only this resolution's projections ("is projected", "is
revised", "is retained", "is placed"), never the earlier vintages it restates ("In the October policy, … was
projected …", "from 7.0 per cent in the June policy"). A projection sentence the regex can't fully read is listed
for `projection_extraction` (AI, B7 part 2), which re-reads the stored text (`read_projections`) and checks the
model's periods and figures with the same grammar (`target_dates`, `quoted_pairs`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from lxml import etree
from lxml import html as lxml_html

from ft.engines.document.text import Block, Extracted, extract_html, normalize_inline, sentence_spans
from ft.policy.members import strip_title

BASE = "https://www.rbi.org.in/Scripts/"
RSS = "https://rbi.org.in/pressreleases_rss.xml"
PROFILE = "rbi_press_release"
SDF_FROM = date(2022, 4, 8)          # the SDF replaced the fixed-rate reverse repo as the corridor floor (Spec 01 §2)
MIN_CHARS = 2000                     # the shortest of the 49 saved resolutions is 5,466 characters (Feb 2025)
TITLE_MARK = "Resolution of the Monetary Policy Committee"
VOCABULARY = ("calibrated tightening", "neutral", "accommodative",
              "accommodative, focused on withdrawal of accommodation", "withdrawal of accommodation")
INSTRUMENTS = ("repo", "reverse_repo", "sdf", "msf", "bank_rate")

_PRID = re.compile(r"[?&]prid=(\d+)", re.IGNORECASE)
_DATE = re.compile(r"Date\s*:\s*([A-Z][a-z]{2})\w*\s+(\d{1,2}),\s*(\d{4})")
_TITLES = r"(?:Dr\.|Prof\.?|Professor|Shri|Smt\.?)"
NAME = re.compile(rf"{_TITLES}\s+[A-Z][A-Za-z'’\-]*\.?(?:\s+[A-Z][A-Za-z'’\-]*\.?){{0,4}}")


class ResolutionError(ValueError):
    """The page can't be read as a resolution (a template change, or not a resolution): nothing is stored."""


def page_url(url: str) -> str:
    """The canonical link (www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=N), whatever host or case the
    Annual Policy page or the feed wrote."""
    m = _PRID.search(url)
    if not m:
        raise ResolutionError(f"not a press-release link: {url!r}")
    return f"{BASE}BS_PressReleaseDisplay.aspx?prid={m[1]}"


def external_key(url: str) -> str:
    return urlsplit(page_url(url)).path + "?prid=" + parse_qs(urlsplit(page_url(url)).query)["prid"][0]


def pct(s: str) -> Decimal:
    return Decimal(s)


# ---------------------------------------------------------------------------------------------- data
@dataclass(frozen=True)
class Rate:
    instrument: str
    value: Decimal
    sentence: tuple[int, int]        # the sentence stating it (the row's span)
    direction: int | None            # the clause's verb: -1 cut, +1 raise, 0 unchanged, None (e.g. "stands adjusted")
    stated_bps: int | None           # "by N basis points", signed by the verb
    stated_from: Decimal | None      # "from X per cent"


@dataclass(frozen=True)
class Stance:
    label: str | None                # a vocabulary label, or None if it can't be read
    sentence: tuple[int, int]
    text: str


@dataclass
class MemberVote:
    name: str                        # as written, with its title ("Prof. Jayanth R. Varma")
    rate: tuple | None = None        # ('bps', n) | ('decision',) | ('against',) | None (not stated)
    rate_text: str | None = None     # the clause, verbatim
    rate_span: tuple[int, int] | None = None
    stance: str | None = None        # a vocabulary label | 'against' | None
    stance_text: str | None = None
    stance_span: tuple[int, int] | None = None


@dataclass
class Votes:
    members: tuple[str, ...]         # who "all members" are, as the resolution names them (empty: it doesn't)
    members_from: str                # 'attendance' | 'vote sentences' | 'none'
    named: dict[str, MemberVote]     # by key(name without title)
    all_rate: tuple | None = None    # a vote for all members: ('bps', n) | ('decision',) | None
    all_rate_span: tuple[int, int] | None = None
    all_stance: str | None = None    # a label for all members, or None
    all_stance_span: tuple[int, int] | None = None
    sentences: tuple[tuple[int, int], ...] = ()
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Projection:
    variable: str                    # 'cpi' | 'gdp'
    target_label: str                # 'Q2 2026-27', 'H1 2019-20', 'FY 2026-27'
    target_start: date
    target_end: date
    value: Decimal | None
    low: Decimal | None
    high: Decimal | None
    span: tuple[int, int]            # from the period to the value (or the value to the period)

    @property
    def key(self) -> tuple:
        return self.variable, self.target_start, self.target_end


@dataclass(frozen=True)
class Resolution:
    url: str
    external_key: str
    title: str
    page_date: date | None
    text: Extracted
    rates: dict[str, Rate]
    stance: Stance | None
    votes: Votes
    projections: tuple[Projection, ...]
    unparsed: tuple[tuple[int, int], ...]   # projection sentences the regex couldn't fully read
    meeting_no: int | None                  # "held its 63rd meeting" (from 2025)
    problems: tuple[str, ...] = ()

    @property
    def repo(self) -> Rate | None:
        return self.rates.get("repo")


# ---------------------------------------------------------------------------------------------- the page
def _header(data: bytes) -> tuple[str, date | None]:
    doc = lxml_html.fromstring(data)
    cells = [normalize_inline(td.text_content()) for td in doc.xpath(
        "//table[contains(concat(' ', normalize-space(@class), ' '), ' tablebg ')]"
        "//td[contains(concat(' ', normalize-space(@class), ' '), ' tableheader ')]")]
    if not cells:
        raise ResolutionError("no tableheader cells: not an RBI press-release page (or its template changed)")
    page_date = None
    for c in cells:
        if m := _DATE.search(c):
            page_date = datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%b %d %Y").date()
            break
    title = next((c for c in cells if not _DATE.search(c) and len(c) > 20), "")
    return title, page_date


def _decision_zone(ex: Extracted) -> tuple[int, int]:
    """The decision part: from the start to the first heading after the repo rate's first mention (else 4,000
    characters on)."""
    seen = False
    for b in ex.blocks:
        if seen and b.kind == "heading":
            return 0, b.start
        if not seen and re.search(r"\brepo rate\b", ex.block_text(b), re.IGNORECASE):
            seen = True
    return 0, min(len(ex.text), 4000)


# ---------------------------------------------------------------------------------------------- rates
_INSTR = re.compile(
    r"(?P<reverse_repo>\breverse repo rate\b)"
    r"|(?P<repo>\b(?:policy )?repo rate\b)"
    r"|(?P<sdf>\bstanding deposit facility(?: \(SDF\))? rate\b|\bSDF rate\b)"
    r"|(?P<msf>\bmarginal standing facility(?: \(MSF\))? rate\b|\bMSF rate\b)"
    r"|(?P<bank_rate>\bBank Rate\b)")
_VALUE = re.compile(r"(?P<v>\d+(?:\.\d+)?)\s*per\s*cent\b")
_BY = re.compile(r"\bby\s+(?P<n>\d+)\s*(?:basis points|bps)\b(?:\s*\(bps\))?", re.IGNORECASE)
_DOWN = re.compile(r"\b(?:reduc(?:e|ed|es|ing|tion)|cut|decreas(?:e|ed|es)|lower(?:ed)?)\b", re.IGNORECASE)
_UP = re.compile(r"\b(?:increas(?:e|ed|es)|rais(?:e|ed|es)|hike[sd]?)\b", re.IGNORECASE)
_SAME = re.compile(r"\b(?:keep|kept|maintain(?:ed)?|unchanged|remains?|retain(?:ed)?|continue)\b", re.IGNORECASE)


def _direction(clause: str) -> int | None:
    found = [(m.start(), d) for p, d in ((_DOWN, -1), (_UP, 1), (_SAME, 0)) for m in p.finditer(clause)]
    return min(found)[1] if found else None


def _rates(ex: Extracted, zone: tuple[int, int], problems: list[str]) -> dict[str, Rate]:
    text = ex.text
    out: dict[str, Rate] = {}
    for s, e in sentence_spans(text, *zone):
        sent = text[s:e]
        toks = sorted([(m.start(), "i", m.lastgroup, m) for m in _INSTR.finditer(sent)]
                      + [(m.start(), "v", None, m) for m in _VALUE.finditer(sent)]
                      + [(m.start(), "b", None, m) for m in _BY.finditer(sent)], key=lambda t: t[0])
        groups: list[dict] = []
        cur = None
        last_end = 0
        for pos, kind, name, m in toks:
            if kind == "i":
                if cur is None or cur["target"] is not None:
                    cur = {"names": [], "target": None, "from": None, "by": None, "start": last_end}
                    groups.append(cur)
                cur["names"].append(name)
            elif kind == "b" and cur is not None:
                cur["by"] = int(m["n"])
            elif kind == "v" and cur is not None:
                before = sent[max(0, pos - 12):pos].lower()
                if re.search(r"\bfrom\s*$", before):
                    cur["from"] = pct(m["v"])
                elif cur["target"] is None:
                    cur["target"] = pct(m["v"])
                    cur["end"] = m.end()
                    last_end = m.end()
        last_d = None
        for g in groups:
            if g["target"] is None:
                continue
            clause = sent[g["start"]:g["end"]]
            d = _direction(clause)
            if d is None and re.match(r"^\W*(?:and|while)\b", clause):
                d = last_d               # "… remains at 6.25 per cent, and the MSF rate and the Bank Rate at 6.75"
            last_d = d
            stated = None
            if g["by"] is not None:
                if d in (None, 0):
                    problems.append(f"'by {g['by']} basis points' without a direction: {clause[:120]!r}")
                else:
                    stated = d * g["by"]
            for name in dict.fromkeys(g["names"]):
                r = Rate(name, g["target"], (s, e), d, stated, g["from"])
                if name in out and out[name].value != r.value:
                    problems.append(f"{name} stated twice: {out[name].value} and {r.value}")
                out.setdefault(name, r)
    return out


# ---------------------------------------------------------------------------------------------- stance
_CW = re.compile(r"(?:remain\s+)?accommodative\s+while\s+focus(?:s|ing|ed)?\s+on\s+(?:the\s+)?withdrawal\s+of\s+"
                 r"accommodation", re.IGNORECASE)
_LABELS = [(_CW, "accommodative, focused on withdrawal of accommodation"),
           (re.compile(r"withdrawal\s+of\s+accommodation", re.IGNORECASE), "withdrawal of accommodation"),
           (re.compile(r"calibrated\s+tightening", re.IGNORECASE), "calibrated tightening"),
           (re.compile(r"\bneutral\b", re.IGNORECASE), "neutral"),
           (re.compile(r"\baccommodative\b", re.IGNORECASE), "accommodative")]
_STANCE_WORDS = re.compile(r"\bstance\b|withdrawal of accommodation|remain accommodative", re.IGNORECASE)


def stance_label(clause: str) -> str | None:
    """The vocabulary label a stance clause names; "from A to B" names B. None if it names none, or several
    without "from"."""
    taken: list[tuple[int, int, str]] = []
    for p, label in _LABELS:
        for m in p.finditer(clause):
            if not any(a < m.end() and m.start() < b for a, b, _ in taken):
                taken.append((m.start(), m.end(), label))
    taken.sort()
    if not taken:
        return None
    labels = {t[2] for t in taken}
    if len(labels) == 1:
        return taken[0][2]
    if re.search(r"\bfrom\b", clause[:taken[0][0]], re.IGNORECASE) and re.search(
            r"\bto\b", clause[taken[0][1]:taken[-1][0]], re.IGNORECASE):
        return taken[-1][2]
    return None


def _stance(ex: Extracted, zone: tuple[int, int]) -> Stance | None:
    text = ex.text
    for s, e in sentence_spans(text, *zone):
        if _STANCE_WORDS.search(text[s:e]) and (label := stance_label(text[s:e])) is not None:
            return Stance(label, (s, e), text[s:e])
    for s, e in sentence_spans(text):                       # the decision part doesn't state it: the vote sentences
        sent = text[s:e]
        if _STANCE_WORDS.search(sent) and re.search(r"\b(?:decided|voted|votes|decision)\b", sent) and not re.search(
                r"\b(?:Fed|ECB|central banks?|BoJ|BoE)\b", sent) and (label := stance_label(sent)) is not None:
            return Stance(label, (s, e), sent)
    return None


# ---------------------------------------------------------------------------------------------- votes
_VOTE_SENTENCE = re.compile(r"\bvot(?:ed|es|ing)\b|\bunanimous(?:ly)?\b|\bof the view that the stance\b|"
                            r"\bexpressed reservations\b|\ball\s+(?:the\s+)?members\b[^.]*\bdecided\b", re.IGNORECASE)
_SPLIT = re.compile(rf",\s+while\s+(?=\w)|,\s+with\s+(?={_TITLES})")
_VERB = re.compile(r"\b(?:voted|votes|voting|decided|were of the view|was of the view|expressed reservations)\b|"
                   r"\bwas unanimous\b", re.IGNORECASE)
_KEEP = re.compile(r"\b(?:keep(?:ing)?|maintain(?:ing)?)\s+(?:the\s+)?(?:policy\s+)?(?:repo\s+)?rate\b|"
                   r"\b(?:policy|repo)\s+rate\s+unchanged\b|\bcontinue\s+with\s+the\s+existing\s+policy\s+rate\b",
                   re.IGNORECASE)
_MOVE_BY = re.compile(r"\b(?P<verb>reduce|decrease|cut|lower|increase|raise|hike)\b[^.;]*?\b(?:policy\s+)?"
                      r"(?:repo\s+)?rate\b[^.;]*?\bby\s+(?P<n>\d+)\s*(?:basis points|bps)", re.IGNORECASE)
_FOR_N = re.compile(r"\bfor\s+an?\s+(?P<n>\d+)\s*(?:bps|basis points?)\s+(?P<verb>reduction|cut|increase|hike)\b",
                    re.IGNORECASE)
_FOR_BY = re.compile(r"\bfor\s+an?\s+(?P<verb>reduction|cut|increase|hike)\b(?:\s+in\s+the\s+(?:policy\s+)?"
                     r"(?:repo\s+)?rate)?\s+by\s+(?P<n>\d+)\s*(?:bps|basis points)", re.IGNORECASE)
_DIR_ONLY = re.compile(r"\b(?:to\s+)?(?:reduce|increase|for\s+an?\s+(?:sizable\s+)?(?:reduction|increase))\b"
                       r"(?:\s+in)?\s+the\s+policy\s+repo\s+rate\b", re.IGNORECASE)
_AGAINST_RATE = re.compile(r"\bagainst\s+the\s+(?:policy\s+)?(?:repo\s+)?rate\s+(?:hike|cut|reduction|increase)\b",
                           re.IGNORECASE)
_FAVOUR = re.compile(r"\bin favour of the decision\b(?P<rest>[^.;]*)", re.IGNORECASE)
_AGAINST_STANCE = re.compile(r"\bagainst\s+this\s+(?:part\s+of\s+the\s+resolution|formulation)\b|"
                             r"\bexpressed\s+reservations\s+on\s+this\s+part\s+of\s+the\s+resolution\b", re.IGNORECASE)
_RATE_WORDS = re.compile(r"\brepo rate\b|\bpolicy rate\b|\b(?:reduction|cut|hike)\b", re.IGNORECASE)
_ATTENDANCE = re.compile(rf"under the chairmanship of (?P<chair>{NAME.pattern}),\s+Governor[^.]*\.\s+The MPC members\s+"
                         r"(?P<members>.+?)\s+attended the meeting")


def _signed(verb: str, n: str) -> int:
    return -int(n) if verb.lower() in ("reduce", "decrease", "cut", "lower", "reduction") else int(n)


def name_key(name: str) -> str:
    from ft.policy.members import key
    return key(strip_title(name))


def _names(s: str) -> list[str]:
    return [m.group(0).strip() for m in NAME.finditer(s)]


def _rate_content(content: str, context: str) -> tuple | None:
    """What a vote clause says about the rate: ('bps', n), ('decision',), ('against',), ('direction', ±1) or None."""
    if _AGAINST_RATE.search(content):
        return ("against",)
    for p in (_MOVE_BY, _FOR_BY, _FOR_N):
        if m := p.search(content):
            return ("bps", _signed(m["verb"], m["n"]))
    if _KEEP.search(content):
        return ("bps", 0)
    if m := _FAVOUR.search(content):
        rest = m["rest"]
        if _STANCE_WORDS.search(rest) and not _RATE_WORDS.search(rest):
            return None
        if _RATE_WORDS.search(rest) or _RATE_WORDS.search(context) or not rest.strip():
            return ("decision",)
    if m := _DIR_ONLY.search(content):
        return ("direction", -1 if re.search(r"reduc", m.group(0), re.IGNORECASE) else 1)
    return None


def _stance_content(content: str, context: str) -> tuple[str | None, str | None]:
    """(label | 'against' | 'decision' | None, the part of the clause it came from)."""
    if _AGAINST_STANCE.search(content):
        return "against", content
    if m := _FAVOUR.search(content):
        rest = m["rest"]
        if _STANCE_WORDS.search(rest):
            return stance_label(rest), rest
        if not rest.strip() and _STANCE_WORDS.search(context):
            return "decision", context
        return None, None
    if (m := _STANCE_WORDS.search(content)) is None:
        return None, None
    # The stance part of a clause that may also vote on the rate: from the first stance word's phrase on.
    start = max(content.rfind(" and ", 0, m.start()), content.rfind(" to ", 0, m.start()), 0)
    part = content[start:]
    label = stance_label(part) or stance_label(content)
    if label is None and re.search(r"\bdecision\b|\bchange\s+the\s+(?:monetary\s+policy\s+)?stance\b", content,
                                   re.IGNORECASE):
        return "decision", content                    # "The decision to change the … stance was unanimous"
    return label, part


def _votes(ex: Extracted, problems: list[str]) -> Votes:
    text = ex.text
    sentences = [(s, e) for s, e in sentence_spans(text) if _VOTE_SENTENCE.search(text[s:e])]
    flat = " ".join(text.split())
    members: tuple[str, ...] = ()
    members_from = "none"
    if m := _ATTENDANCE.search(flat):
        members = (m["chair"].strip(), *_names(m["members"]))
        members_from = "attendance"
    v = Votes(members, members_from, {}, sentences=tuple(sentences))
    seen_names: dict[str, str] = {}
    prev_decided = ""
    for s, e in sentence_spans(text):
        sent = text[s:e]
        if not _VOTE_SENTENCE.search(sent):
            if re.search(r"\bdecided to\b", sent):
                prev_decided = sent
            continue
        if re.search(r"\bof the view that (?!the stance)", sent) and not re.search(r"\bvot", sent):
            continue
        # A leading "While X was unanimous, Y voted …" holds two clauses (after the paragraph's number, if any).
        body = re.sub(r"^\s*\d{1,3}\.\s+", "", sent)
        clauses: list[str] = []
        if lead := re.match(r"^\s*While\s+(?P<a>[^,]+),\s+(?P<b>.+)$", body):
            clauses += [lead["a"], lead["b"]]
        else:
            clauses += [c for c in _SPLIT.split(body) if c.strip()]
        for clause in clauses:
            cpos = text.find(clause, s)
            span = (cpos, cpos + len(clause)) if cpos >= 0 else (s, e)
            span = (span[0], min(span[1], e))
            vm = _VERB.search(clause)
            if vm is None:
                continue
            own = clause[:vm.start()]
            if vm.group(0).lower() == "was unanimous":
                content, context = own, own
            else:                 # "in favour of the decision": the clause's own topic, else the decision just stated
                content = clause[vm.start():]
                context = own if (_RATE_WORDS.search(own) or _STANCE_WORDS.search(own)) else own + " " + prev_decided
            subject = clause[:vm.start()]
            excepted = []
            if "except" in clause:
                head, _, tail = clause.partition("except")
                excepted = _names(tail.split(",")[0] + "," if tail else "")
                subject = head if vm.start() > len(head) else subject
            names = [n for n in _names(subject) if name_key(n) not in {name_key(x) for x in excepted}]
            if vm.group(0).lower() in ("voting",):              # ", with Prof. X voting against this formulation"
                names = _names(clause[:vm.start()])
            unanimous = bool(re.search(r"\bunanimous", clause, re.IGNORECASE))
            is_all = not names and (unanimous or re.search(r"\ball\s+(?:the\s+)?members\b", subject, re.IGNORECASE))
            if not names and not is_all:
                continue                                        # "the MPC voted to …" restates the decision
            rate = _rate_content(content, context)
            stance = _stance_content(content, context)[0]
            if is_all:
                if rate and rate[0] in ("bps", "decision"):
                    if v.all_rate and v.all_rate != rate:
                        problems.append(f"two different votes for all members on the rate: {v.all_rate}, {rate}")
                    v.all_rate, v.all_rate_span = rate, span
                if stance and stance != "against":
                    if v.all_stance and v.all_stance != stance:
                        problems.append(f"two different stance votes for all members: {v.all_stance}, {stance}")
                    v.all_stance, v.all_stance_span = stance, span
                continue
            for n in names:
                k = name_key(n)
                seen_names.setdefault(k, n)
                mv = v.named.setdefault(k, MemberVote(n))
                if rate and rate[0] != "direction":
                    if mv.rate and mv.rate != rate:
                        problems.append(f"{n}: two rate votes ({mv.rate}, {rate})")
                    mv.rate, mv.rate_text, mv.rate_span = rate, content.strip().rstrip("."), span
                elif rate and rate[0] == "direction" and mv.rate is None:
                    pass                                         # the quantum comes in its own sentence
                if stance:
                    if mv.stance and mv.stance != stance and stance != "against":
                        problems.append(f"{n}: two stance votes ({mv.stance}, {stance})")
                    if stance == "against" or mv.stance is None:
                        mv.stance, mv.stance_text, mv.stance_span = stance, content.strip().rstrip("."), span
    if not members and len(seen_names) == 6:
        v.members, v.members_from = tuple(seen_names.values()), "vote sentences"
    return v


# ---------------------------------------------------------------------------------------------- projections
_FY = r"(?:20\d\d)\s?[-–]\s?(?:\d\d)"
_PERIOD = re.compile(rf"(?P<q>\bQ[1-4]\b|\bH[12]\b)(?:\s?:\s?(?P<fy1>{_FY})|\s+of\s+(?P<fy2>{_FY}))?"
                     rf"|(?:\bthe\s+financial\s+year\s+|\bFY\s?)?(?P<fy>\b{_FY}\b)")
_NUM = r"(?:\(\s?(?P<sign{i}>[+\-−])\s?\)\s?)?(?P<n{i}>\d+(?:\.\d+)?)"
# "3.2-3.4 per cent" (a dash between bare numbers), "26.2 to 8.3 per cent", "5.2 per cent to 4.6 per cent"; never
# "6.1 per cent – 5.3 per cent in Q2" (a year's figure, then a quarter's).
_RANGE = re.compile(r"(?:in\s+the\s+range\s+of\s+)?" + _NUM.format(i=1)
                    + r"(?:\s?[-–]\s?|(?:\s*per\s*cent)?\s+to\s+)" + _NUM.format(i=2) + r"\s*per\s*cent\b")
_ONE = re.compile(_NUM.format(i=1) + r"\s*per\s*cent\b")
_CURRENT = re.compile(r"\b(?:is|are|has been|have been)\s+(?:now\s+)?(?:projected|placed|revised|retained|"
                      r"expected\s+to\s+be)\b|\bprojections?\s+(?:is|are)\s+retained\b", re.IGNORECASE)
_EARLIER = re.compile(r"\bfrom\s+[^;]*?\b(?:policy|resolution|projected\s+in\b[^,;]*?)(?:,\s*respectively)?,?\s+(?=to\b)",
                      re.IGNORECASE)
_SKIP_SUBJECT = re.compile(r"\b(?:core|food|fuel)\s+inflation\b|\bexcluding\b|\bGVA\b|\bgross value added\b",
                           re.IGNORECASE)
_SUBJECT = re.compile(r"(?P<gdp>\b(?:real\s+)?GDP\s+growth\b)|(?P<cpi>\binflation\b)", re.IGNORECASE)
_RESPECTIVELY = re.compile(r"(?P<periods>(?:Q[1-4]|H[12])(?:\s?:\s?" + _FY + r")?(?:,\s*(?:Q[1-4]|H[12]))*\s+and\s+"
                           r"(?:Q[1-4]|H[12])(?:\s?:\s?" + _FY + r")?)\s+(?:are|is)\s+(?:now\s+)?(?:projected|revised"
                           r"(?:\s+\w+)?)\s+(?:to\s+be\s+)?(?:at|to)\s+(?P<values>.+?),?\s+respectively", re.IGNORECASE)
_EACH = re.compile(r"(?P<periods>(?:Q[1-4]|H[12])(?:,\s*(?:Q[1-4]|H[12]))*\s+and\s+(?:Q[1-4]|H[12]))\s+at\s+"
                   + _NUM.format(i=1) + r"\s*per\s*cent\s+each", re.IGNORECASE)
_LINK_AFTER = re.compile(r"^\s*(?:in|for|during)\s+(?:the\s+range\s+of\s+)?$", re.IGNORECASE)
_LINK_BEFORE = re.compile(r"^\s*,?\s*(?:(?:is|are|has been|have been)\s+)?(?:now\s+|also\s+)?(?:(?:projected|placed|"
                          r"revised|retained|expected)(?:\s+\w+){0,2}?\s+)?(?:(?:negative|positive)\s+)?"
                          r"(?:to\s+be(?:\s+at)?|at|to|as)\s*$", re.IGNORECASE)


def _fy_bounds(fy: str) -> tuple[int, int]:
    a = int(fy[:4])
    return a, a + 1


def _target(q: str | None, fy: str) -> tuple[str, date, date]:
    a, b = _fy_bounds(fy)
    label_fy = f"{a}-{str(b)[2:]}"
    if q is None:
        return f"FY {label_fy}", date(a, 4, 1), date(b, 3, 31)
    spans = {"Q1": ((a, 4, 1), (a, 6, 30)), "Q2": ((a, 7, 1), (a, 9, 30)), "Q3": ((a, 10, 1), (a, 12, 31)),
             "Q4": ((b, 1, 1), (b, 3, 31)), "H1": ((a, 4, 1), (a, 9, 30)), "H2": ((a, 10, 1), (b, 3, 31))}
    lo, hi = spans[q.upper()]
    return f"{q.upper()} {label_fy}", date(*lo), date(*hi)


def _num(m: re.Match, i: int) -> Decimal:
    v = Decimal(m[f"n{i}"])
    return -v if (m[f"sign{i}"] or "") in ("-", "−") else v


def _norm_fy(fy: str) -> str:
    return re.sub(r"\s?[-–]\s?", "-", fy)


def _projection_sentence(text: str, s: int, e: int, para_fy: str | None, out: list[Projection]) -> tuple[bool, str | None]:
    """Projections in one sentence. Returns (fully read, the last FY named) and appends to `out`."""
    sent = text[s:e]
    subj = _SUBJECT.search(sent)
    variable = "gdp" if subj and subj.lastgroup == "gdp" else "cpi"
    work = _EARLIER.sub(lambda m: " " * len(m.group(0)), sent)     # earlier vintages ("from 7.0 per cent in …")
    work = re.sub(r"\b(?:level|target|band)\s+of\s+[\d.]+\s*per\s*cent", lambda m: " " * len(m.group(0)), work)
    periods = []
    for m in _PERIOD.finditer(work):
        fy = m["fy1"] or m["fy2"] or m["fy"]
        periods.append({"start": m.start(), "end": m.end(), "q": m["q"], "fy": _norm_fy(fy) if fy else None})
    last_fy = para_fy
    for p in periods:                     # a bare quarter takes the FY named before it, else the next one named
        if p["fy"]:
            last_fy = p["fy"]
        elif p["q"]:
            p["fy_ctx"] = last_fy
    for p in periods:
        if p["q"] and not p["fy"]:
            nxt = next((x["fy"] for x in periods if x["start"] > p["start"] and x["fy"]), None)
            p["fy"] = p.get("fy_ctx") or nxt
    values = []
    used = []
    for m in _RANGE.finditer(work):
        a, b = _num(m, 1), _num(m, 2)
        values.append({"start": m.start(), "end": m.end(), "value": None, "low": min(a, b), "high": max(a, b)})
        used.append((m.start(), m.end()))
    for m in _ONE.finditer(work):
        if any(a <= m.start() < b for a, b in used):
            continue
        values.append({"start": m.start(), "end": m.end(), "value": _num(m, 1), "low": None, "high": None})
    values.sort(key=lambda v: v["start"])
    found: list[tuple[dict, dict]] = []
    taken: set[int] = set()
    # "Q3 and Q4 at 6.5 per cent each"; "for Q1:2026-27 and Q2 are projected at 3.9 per cent and 4.0 per cent,
    # respectively".
    for m in _EACH.finditer(work):
        ps = [p for p in periods if m.start() <= p["start"] < m.end()]
        vs = [v for v in values if m.start() <= v["start"] < m.end()]
        if len(vs) == 1:
            found += [(p, vs[0]) for p in ps]
            taken.add(id(vs[0]))
    for m in _RESPECTIVELY.finditer(work):
        ps = [p for p in periods if m.start("periods") <= p["start"] < m.end("periods")]
        vs = [v for v in values if m.start("values") <= v["start"] < m.end("values")]
        if len(ps) == len(vs):
            found += list(zip(ps, vs, strict=True))
            taken |= {id(v) for v in vs}
    for v in values:
        if id(v) in taken:
            continue
        after = next((p for p in periods if p["start"] >= v["end"]), None)
        before = next((p for p in reversed(periods) if p["end"] <= v["start"]), None)
        if after is not None and _LINK_AFTER.match(work[v["end"]:after["start"]].replace("per cent", "")):
            found.append((after, v))
        elif before is not None and _LINK_BEFORE.match(work[before["end"]:v["start"]]):
            found.append((before, v))
        else:
            continue
        taken.add(id(v))
    if not values:
        return True, last_fy                  # no figure once earlier vintages and target levels are set aside
    complete = len(taken) == len(values)
    for p, v in found:
        if not p["fy"]:
            complete = False
            continue
        label, start, end = _target(p["q"], p["fy"])
        a, b = min(p["start"], v["start"]), max(p["end"], v["end"])
        out.append(Projection(variable, label, start, end, v["value"], v["low"], v["high"], (s + a, s + b)))
    return complete, last_fy


def _projections(ex: Extracted, problems: list[str]) -> tuple[tuple[Projection, ...], tuple[tuple[int, int], ...]]:
    text = ex.text
    found: list[Projection] = []
    unparsed = []
    para_fy: dict[int, str] = {}
    for b in ex.blocks:
        last = None
        for s, e in sentence_spans(text, b.start, b.end):
            sent = text[s:e]
            if not (_CURRENT.search(sent) and _SUBJECT.search(sent) and re.search(r"per\s*cent", sent)):
                continue
            if re.match(r"^\s*(?:\d+\.\s+)?In the\b", sent) or re.search(r"\bwas\s+(?:\w+\s+)?projected\b", sent):
                continue
            subj = _SUBJECT.search(sent)
            if _SKIP_SUBJECT.search(sent[:subj.end() + 5]) or re.search(r"per\s*cent\s+of\s+GDP", sent):
                continue
            ok, last = _projection_sentence(text, s, e, last, found)
            if not ok:
                unparsed.append((s, e))
        para_fy[b.start] = last
    out: dict[tuple, Projection] = {}
    for p in found:
        q = out.get(p.key)
        if q is None:
            out[p.key] = p
        elif (q.value, q.low, q.high) != (p.value, p.low, p.high):
            problems.append(f"{p.variable} {p.target_label} stated twice: {q.value or (q.low, q.high)} and "
                            f"{p.value or (p.low, p.high)}")
            out[p.key] = None
    return tuple(p for p in out.values() if p is not None), tuple(unparsed)


def reread(text: str, version: str = "stored") -> Extracted:
    """A stored canonical text with its blocks rebuilt from the blank lines between them (text.py's shape: blocks
    joined by "\\n\\n", and no block holds one), each block a paragraph. The projection reader looks only at block
    boundaries, so it reads a stored text exactly as it read the page; the decision part needs the headings, so the
    stance is read from the raw file instead (`parse`)."""
    blocks, pos = [], 0
    for part in text.split("\n\n"):
        blocks.append(Block(pos, pos + len(part), "para"))
        pos += len(part) + 2
    return Extracted(text, tuple(blocks), version)


def read_projections(text: str) -> tuple[tuple[Projection, ...], tuple[tuple[int, int], ...]]:
    """(the projections, the projection sentences the regex couldn't fully read) of a stored resolution's text: what
    `parse` gave at load time (policy.ai_batch's `projection_extraction` runs where this lists sentences)."""
    return _projections(reread(text), [])


_LABEL = re.compile(r"^(?:(?P<q>Q[1-4]|H[12])|FY) (?P<a>20\d\d)-(?P<b>\d\d)$")


def target_dates(label: str) -> tuple[date, date] | None:
    """The first and last day of a target period written as this module writes it ('Q2 2026-27', 'H1 2019-20',
    'FY 2026-27'); None for anything else."""
    m = _LABEL.match(label)
    if m is None or int(m["b"]) != (int(m["a"]) + 1) % 100:
        return None
    _, start, end = _target(m["q"], f"{m['a']}-{m['b']}")
    return start, end


def quoted_pairs(quote: str) -> list[tuple[str, str | None, Decimal | tuple[Decimal, Decimal]]] | None:
    """The (period, fiscal year, figure) triples a projection quote states, its periods and its figures paired in
    order ("Q1 at 3.9 per cent and Q2 at 4.0 per cent", "Q1:2026-27 and Q2 … at 3.9 per cent and 4.0 per cent,
    respectively", "26.2 per cent in Q1"); None unless it states exactly one figure for each period it names. A period
    is 'Q1'…'Q4', 'H1', 'H2' or 'FY'; its fiscal year '2026-27', or None for a bare quarter; a figure a value, or
    (low, high) for a range. Read with the regex's own grammar ("(-)7.5 per cent", "3.2-3.4 per cent", "5.2 per cent to
    5.0 per cent"), with earlier vintages and target levels set aside first, as the regex does."""
    work = _EARLIER.sub(lambda m: " " * len(m.group(0)), quote)
    work = re.sub(r"\b(?:level|target|band)\s+of\s+[\d.]+\s*per\s*cent", lambda m: " " * len(m.group(0)), work)
    periods = []
    for m in _PERIOD.finditer(work):
        fy = m["fy1"] or m["fy2"] or m["fy"]
        periods.append((m["q"].upper() if m["q"] else "FY", _norm_fy(fy) if fy else None))
    figures, used = [], []
    for m in _RANGE.finditer(work):
        a, b = _num(m, 1), _num(m, 2)
        figures.append((m.start(), (min(a, b), max(a, b))))
        used.append((m.start(), m.end()))
    figures += [(m.start(), _num(m, 1)) for m in _ONE.finditer(work) if not any(a <= m.start() < b for a, b in used)]
    figures.sort(key=lambda f: f[0])
    if not periods or len(periods) != len(figures):
        return None
    return [(p, fy, f) for (p, fy), (_, f) in zip(periods, figures, strict=True)]


def _set_aside(s: str) -> str:
    return _EARLIER.sub(lambda m: " " * len(m.group(0)), s)


def period_years(text: str, sentence: tuple[int, int]) -> list[tuple[int, int, str, str | None]]:
    """The periods a projection sentence names, as (start, end, 'Q2' | 'H1' | 'FY', fiscal year), each bare quarter
    given the fiscal year the regex gives it: the last one named before it in the sentence, else the last one named
    earlier in its paragraph, else the next one named after it in the sentence (None if there's none). Earlier
    vintages are set aside first, as the regex does."""
    s, e = sentence
    found = []
    for m in _PERIOD.finditer(_set_aside(text[s:e])):
        fy = m["fy1"] or m["fy2"] or m["fy"]
        found.append([s + m.start(), s + m.end(), m["q"].upper() if m["q"] else "FY", _norm_fy(fy) if fy else None])
    block = text.rfind("\n\n", 0, s)
    last = None
    for m in _PERIOD.finditer(_set_aside(text[(block + 2 if block >= 0 else 0):s])):
        if fy := m["fy1"] or m["fy2"] or m["fy"]:
            last = _norm_fy(fy)
    ctx = []
    for p in found:
        if p[3]:
            last = p[3]
        ctx.append(last)
    for i, p in enumerate(found):
        if p[3] is None:
            p[3] = ctx[i] or next((x[3] for x in found[i + 1:] if x[3]), None)
    return [tuple(p) for p in found]


def subject_at(text: str, sentence: tuple[int, int], pos: int) -> str | None:
    """What a projection at `pos` is about, by the regex's subjects: the last CPI-inflation or GDP-growth mention in
    its sentence before `pos` ('cpi' | 'gdp'); None if there's none, or it's core, food or fuel inflation, GVA or
    inflation 'excluding' something."""
    s, e = sentence
    last = None
    for m in _SUBJECT.finditer(text, s, e):
        if m.start() >= pos:
            break
        last = m
    if last is None or _SKIP_SUBJECT.search(text[max(s, last.start() - 12):min(e, last.end() + 12)]):
        return None
    return "gdp" if last.lastgroup == "gdp" else "cpi"


# ---------------------------------------------------------------------------------------------- parse
def parse(data: bytes, url: str) -> Resolution:
    """A resolution page. Raises ResolutionError if it isn't an RBI press release; what can't be read in one that is
    is left for the checks to report."""
    title, page_date = _header(data)
    ex = extract_html(data, PROFILE)
    problems: list[str] = []
    zone = _decision_zone(ex)
    rates = _rates(ex, zone, problems)
    stance = _stance(ex, zone)
    votes = _votes(ex, problems)
    problems += votes.problems
    projections, unparsed = _projections(ex, problems)
    m = re.search(r"\bheld its\s+(\d+)(?:st|nd|rd|th)\s+meeting\b", ex.text)
    return Resolution(page_url(url), external_key(url), title, page_date, ex, rates, stance, votes, projections,
                      unparsed, int(m[1]) if m else None, tuple(problems))


# ---------------------------------------------------------------------------------------------- each member's vote
@dataclass(frozen=True)
class Vote:
    """One member's vote as the resolution gives it (policy.vote, before the roster maps the name to a member)."""
    name: str                        # as written
    voted_change_bps: int | None     # None: no change stated ("voted against the repo rate hike"), or no rate vote
    rate_stated: bool                # the resolution gives this member a rate vote
    stance_vote: str | None          # a vocabulary label, 'against', or None (not stated)
    is_dissent: bool
    dissent_text: str | None         # the dissenting clause(s), verbatim
    span: tuple[int, int]
    rate_how: str | None             # 'named' | 'all members' (the resolution's names) | 'roster' (D4) | None
    stance_how: str | None           # the same, or 'elimination'


def member_votes(r: Resolution, decision_bps: int, roster_names: tuple[str, ...] = ()) -> list[Vote]:
    """Every member's vote (Spec 01 §2, "RBI votes (D4)", "Dissents (F23)"; Anand, 23 Oct 2026, issue #15).

    "All members" are the members the resolution names (the attendance sentence, or the six its vote sentences name);
    if it names fewer, `roster_names` (the roster's six on the date, passed only while D4's inference is switched on).
    Where the resolution names all its members and says the MPC "decided" a stance, a member it doesn't name as
    dissenting gets the adopted stance by elimination (the minutes never record stance votes)."""
    v = r.votes
    adopted = r.stance.label if r.stance and r.stance.label in VOCABULARY else None
    members = list(v.members) or list(roster_names)
    how_all = "all members" if v.members else "roster"
    in_all = {name_key(n) for n in members} | set(v.named)   # a member named anywhere is one of "all members"
    names = {name_key(n): n for n in members}
    for k, mv in v.named.items():
        names.setdefault(k, mv.name)
    out = []
    for k, name in names.items():
        mv = v.named.get(k, MemberVote(name))
        rate, rate_text, rate_span, rate_how = mv.rate, mv.rate_text, mv.rate_span, "named" if mv.rate else None
        if rate is None and k in in_all and v.all_rate:
            rate, rate_span, rate_how = v.all_rate, v.all_rate_span, how_all if k not in v.named else "all members"
        stance, stance_text, stance_span = mv.stance, mv.stance_text, mv.stance_span
        stance_how = "named" if stance else None
        if stance is None and k in in_all and v.all_stance:
            stance, stance_span, stance_how = v.all_stance, v.all_stance_span, (how_all if k not in v.named
                                                                                else "all members")
        if stance == "decision":
            stance = adopted
        if stance is None and k in in_all and v.members and adopted:
            stance, stance_span, stance_how = adopted, r.stance.sentence, "elimination"
        if rate is None and stance is None:
            continue
        bps = decision_bps if rate == ("decision",) else (rate[1] if rate and rate[0] == "bps" else None)
        rate_dissent = rate is not None and (rate[0] == "against" or bps != decision_bps)
        stance_dissent = stance is not None and adopted is not None and stance != adopted
        texts = [t for t, d in ((rate_text, rate_dissent), (stance_text, stance_dissent)) if d and t]
        out.append(Vote(name, bps, rate is not None, stance, rate_dissent or stance_dissent, "; ".join(texts) or None,
                        rate_span or stance_span, rate_how, stance_how))
    return out


# ---------------------------------------------------------------------------------------------- checks
@dataclass(frozen=True)
class Result:
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = True


def checks(r: Resolution, decision_date: date) -> list[Result]:
    """The checks that need only the page (Arch 02 §2; Spec 01 §2). The change against the previous meeting and the
    votes against the roster are checked in the task."""
    out = [
        Result("resolution_title", TITLE_MARK.lower() in r.title.lower(), f"title {r.title!r}"),
        Result("resolution_text", r.text.char_count > MIN_CHARS, f"{r.text.char_count} characters"),
        Result("resolution_date", r.page_date == decision_date,
               f"the page is dated {r.page_date}, the decision {decision_date}"),
    ]
    rate_problems = [p for p in r.problems if "stated twice" in p and not p.startswith(("cpi", "gdp"))
                     or "without a direction" in p]
    repo = r.repo
    out.append(Result("repo_rate", repo is not None and not rate_problems,
                      f"repo {repo.value if repo else None}" + ("; " + "; ".join(rate_problems) if rate_problems else "")))
    if repo is None:
        return out
    floor = "sdf" if decision_date >= SDF_FROM else "reverse_repo"
    wrong_floor = "reverse_repo" if floor == "sdf" else "sdf"
    have = set(r.rates)
    missing = [i for i in ("repo", "msf", "bank_rate", floor) if i not in have]
    out.append(Result("instruments", not missing and wrong_floor not in have,
                      f"stated: {sorted(have)}" + (f"; missing {missing}" if missing else "")
                      + (f"; {wrong_floor} isn't the floor on {decision_date}" if wrong_floor in have else "")))
    msf, bank, sdf = (r.rates.get(i) for i in ("msf", "bank_rate", "sdf"))
    q = Decimal("0.25")
    out.append(Result("msf_spread", msf is not None and msf.value == repo.value + q,
                      f"MSF {msf.value if msf else None}, repo {repo.value}"))
    out.append(Result("bank_rate", bank is not None and msf is not None and bank.value == msf.value,
                      f"Bank Rate {bank.value if bank else None}, MSF {msf.value if msf else None}"))
    if decision_date >= SDF_FROM:
        out.append(Result("sdf_spread", sdf is not None and sdf.value == repo.value - q,
                          f"SDF {sdf.value if sdf else None}, repo {repo.value}"))
    label = r.stance.label if r.stance else None
    out.append(Result("stance_label", label in VOCABULARY, f"stance {label!r}"
                      + (f" from {r.stance.text[:120]!r}" if r.stance else ": no stance sentence"), blocking=False))
    return out


# ---------------------------------------------------------------------------------------------- the RSS feed
@dataclass(frozen=True)
class RssItem:
    title: str
    link: str
    published: datetime | None

    @property
    def is_resolution(self) -> bool:
        """Titled as a resolution and linking a press release. Only a trigger: the page must still pass the checks,
        and the Annual Policy page's link, where it has one, decides (Spec 01 §1.1)."""
        return TITLE_MARK.lower() in self.title.lower() and bool(_PRID.search(self.link))


def parse_rss(data: bytes) -> list[RssItem]:
    """The RBI's press-release feed (latest 10, newest first; pubDate in India time without a zone)."""
    from zoneinfo import ZoneInfo
    try:
        root = etree.fromstring(data.lstrip(b"\xef\xbb\xbf"), parser=etree.XMLParser(
            resolve_entities=False, no_network=True, remove_blank_text=True))
    except etree.XMLSyntaxError as e:
        raise ResolutionError(f"the feed isn't XML: {e}") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise ResolutionError("the feed isn't RSS 2.0")
    items = []
    for it in root.iter("item"):
        when = None
        if pub := (it.findtext("pubDate") or "").strip():
            try:
                when = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            except ValueError:
                when = None
        items.append(RssItem(normalize_inline(it.findtext("title") or ""), (it.findtext("link") or "").strip(), when))
    return items
