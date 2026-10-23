"""FOMC statements (Spec 01 §1.1 "Fed statement", "Fed RSS", "Canonical text"; §2 "Fed statement", "Fed votes",
"Dissents (F23)"; Prompt 01 B5). Parses one statement page into what policy.fed_documents stores, and runs the
statement checks that need nothing but the page and the meeting's decision date.

The page (`/newsevents/pressreleases/monetaryYYYYMMDDa.htm`, the calendar's link) has a heading column (date, title,
"For release at …") and a body column; the canonical text is the body (text.PROFILES["fed_press_release"]).

**The decision sentence:** the one sentence saying the Committee "decided [today] to raise | lower | reduce | increase |
maintain | keep the target range for the federal funds rate [by N percentage point | basis points] (at | to) X to Y
percent", fractions included ("0 to 1/4 percent", "3-3/4 to 4 percent"; the Fed writes the hyphen as U+2011 at times).
The decision-and-guidance paragraph (Spec 01 D2) is the block that holds it.

**Two voting formats** (checked on the 64 decision statements saved 23 Oct 2026, Dec 2018 to Sep 2026):
- to 29 Apr 2026: "Voting for the [FOMC] monetary policy action were[:] Jerome H. Powell, Chair; …; and Christopher J.
  Waller." and, when someone dissented, "Voting against (the | this) action (was | were)[:] NAMES, who …; and NAMES,
  who …." Also "X voted as an alternate member at this meeting." and "Absent and not voting was X.";
- from 17 Jun 2026 (Chair Warsh): "The Federal Open Market Committee approved the following statement for release by
  a 9 – 3 vote:" with the dissenters named ("Voting against the monetary policy action were …, who preferred …"), but
  not the members voting for. Decided by Anand, 23 Oct 2026: no vote rows from these statements; the minutes give
  every member's vote (B5 part 2). Their voting check reads the approval line instead (`voting_line`).

A dissent's clause says what the member wanted. "preferred [at this meeting] to lower | reduce | raise | increase |
maintain the target range … [by N …] [(at | to) X to Y percent]" and "preferred no change to the target range" are
about the rate; anything else ("supported maintaining the target range … but did not support inclusion of an easing
bias", Sep 2020's two guidance dissents, Mar 2025's balance-sheet dissent) is not, and stores no bps (Spec 01 §2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from lxml import etree
from lxml import html as lxml_html

from ft.engines.document.text import Extracted, extract_html, normalize_inline, sentence_spans
from ft.policy.fed_calendar import SITE

RSS = SITE + "/feeds/press_monetary.xml"
PROFILE = "fed_press_release"
STATEMENT_PATH = re.compile(r"^/newsevents/pressreleases/monetary(\d{8})a\.htm$")

# Checks with thresholds Anand set when the saved pages disagreed with the spec (23 Oct 2026; GitHub issue #14):
MIN_CHARS = 500           # was "over 1,000": 3 Mar 2020's body is 782 characters, 17 Jun 2026's 840, 16 Sep 2026's 846
TITLE_MARK = "FOMC statement"   # was "the text names the Federal Open Market Committee": 45 bodies (Apr 2020 to Apr 2026)
#                                 say only "the Committee"; every decision statement's page title reads
#                                 "Federal Reserve issues FOMC statement", and no notation-vote page's does
VOTERS = (8, 12)          # Arch 02 §2: voters 8-12

_DASH = "-\u2010\u2011\u2012\u2013"
_NUM = rf"\d+(?:\.\d+)?(?:\s?[{_DASH}]\s?\d/\d)?|\d/\d"
_RANGE = rf"(?P<lo>{_NUM})(?:\s+percent)?\s+to\s+(?P<hi>{_NUM})\s+percent"
_BY = rf"\s+by\s+(?P<by>{_NUM})\s+(?P<unit>percentage points?|basis points?)"
_VERB = r"raise|increase|lower|reduce|maintain|keep"
_TR = r"the target range(?: for the federal funds rate)?"
DECISION = re.compile(rf"\bdecided(?:\s+today)?\s+to\s+(?P<verb>{_VERB})\s+the target range for the federal funds rate"
                      rf"(?:{_BY})?,?\s+(?:at|to)\s+{_RANGE}")
PREFERS = re.compile(rf"\bpreferred\s+(?:at this meeting\s+)?to\s+(?P<verb>{_VERB})\s+{_TR}(?:{_BY})?"
                     rf"(?:,?\s+(?:at|to)\s+{_RANGE})?")
NO_CHANGE = re.compile(rf"\bpreferred\s+no change to {_TR}")
VOTING_FOR = re.compile(r"^Voting for the (?:FOMC )?monetary policy action were:?\s+")
VOTING_AGAINST = re.compile(r"^Voting against (?:the|this) (?:monetary policy )?action (?:was|were):?\s+")
APPROVED = re.compile(rf"approved the following statement for release by an? (\d{{1,2}})\s*[{_DASH}]\s*(\d{{1,2}}) vote")
ALTERNATE = re.compile(r"^(?P<who>.+?) voted as an alternate member at this meeting\.$")
ABSENT = re.compile(r"^Absent and not voting (?:was|were) (?P<who>.+?)\.$")
ROLES = {"chair", "chairman", "vice chair", "vice chairman"}
_NAME = r"(?:Mr\.|Ms\.|Mrs\.|Dr\.)?\s?[A-Z][A-Za-z'’-]*\.?(?:\s+[A-Z][A-Za-z'’-]*\.?){0,4}"
_NAMES = rf"{_NAME}(?:(?:,\s*(?:and\s+)?|\s+and\s+){_NAME})*"
_GROUP_START = re.compile(rf"(?:^|;\s*(?:and\s+)?|,\s+and\s+)(?P<names>{_NAMES})$")
_LIST_SPLIT = re.compile(r"\s*;\s*(?:and\s+)?|\s*,\s*(?:and\s+)?|\s+and\s+")


class StatementError(ValueError):
    """The page can't be read as a statement (a template change, or not a statement): nothing is stored."""


def pct(text: str) -> Decimal:
    """'3-3/4' -> 3.75, '1/4' -> 0.25, '4' -> 4, '0.5' -> 0.5 (the Fed's fractions of a percentage point)."""
    t = re.sub(rf"\s?[{_DASH}]\s?", "-", text.strip())
    whole, _, frac = t.partition("-") if "/" in t and "-" in t else (("0", "", t) if "/" in t else (t, "", ""))
    v = Decimal(whole)
    if frac:
        n, d = frac.split("/")
        v += Decimal(n) / Decimal(d)
    return v


def bps(points: Decimal) -> int:
    v = points * 100
    if v != v.to_integral_value():
        raise StatementError(f"{points} percentage points isn't a whole number of basis points")
    return int(v)


@dataclass(frozen=True)
class Range:
    lower: Decimal
    upper: Decimal

    @property
    def width_bps(self) -> int:
        return bps(self.upper - self.lower)

    def __str__(self) -> str:
        return f"{self.lower:g}-{self.upper:g}%"


def _range(m: re.Match) -> Range:
    return Range(pct(m["lo"]), pct(m["hi"]))


def _stated(m: re.Match) -> int | None:
    """The change a clause states ("by 1/4 percentage point", "by 25 basis points"), signed by its verb."""
    if not m["by"]:
        return None
    size = pct(m["by"])
    n = int(size) if m["unit"].startswith("basis") else bps(size)
    if m["verb"] in ("maintain", "keep"):
        raise StatementError(f"'{m['verb']} … by {m['by']}' doesn't say which way")
    return n if m["verb"] in ("raise", "increase") else -n


@dataclass(frozen=True)
class Decision:
    verb: str                       # raise | increase | lower | reduce | maintain | keep
    range: Range
    stated_bps: int | None          # the change the sentence states, if it does
    sentence: tuple[int, int]       # code-point span of the decision sentence
    paragraph: tuple[int, int]      # the decision-and-guidance paragraph (D2): the block holding the sentence

    @property
    def direction(self) -> int:
        return 0 if self.verb in ("maintain", "keep") else (1 if self.verb in ("raise", "increase") else -1)


@dataclass(frozen=True)
class Preference:
    """What a dissenter wanted. `about_rate` False: a dissent that isn't about the rate (no bps)."""
    about_rate: bool
    verb: str | None = None         # the clause's verb ('maintain' for "no change")
    stated_bps: int | None = None   # "by N"
    range: Range | None = None      # "(at | to) X to Y percent"

    def change_bps(self, previous: Range) -> int | None:
        """The change against the previous meeting's range that this member voted for (None: not about the rate).
        Raises StatementError if the clause contradicts itself (its "by" and its range disagree)."""
        if not self.about_rate:
            return None
        from_range = bps(self.range.upper - previous.upper) if self.range else None
        if self.verb in ("maintain", "keep"):
            if from_range not in (None, 0):
                raise StatementError(f"a dissent to maintain the range names {self.range}, not the previous {previous}")
            return 0
        if self.stated_bps is not None and from_range is not None and self.stated_bps != from_range:
            raise StatementError(f"a dissent says {self.stated_bps:+d} bps but names {self.range} "
                                 f"(from {previous}: {from_range:+d})")
        n = self.stated_bps if self.stated_bps is not None else from_range
        if n is None:
            raise StatementError(f"a dissent to {self.verb} the range states neither a change nor a range")
        return n


@dataclass(frozen=True)
class Dissent:
    name: str
    text: str                       # the clause after "who", verbatim (policy.vote.dissent_text)
    preference: Preference


@dataclass(frozen=True)
class Votes:
    format: str                     # 'named' (to Apr 2026) | 'tally' (from Jun 2026)
    sentence: tuple[int, int]       # "Voting for …" (named) or the approval line (tally)
    voters_for: tuple[str, ...]     # named format only
    dissents: tuple[Dissent, ...]
    against_sentence: tuple[int, int] | None
    tally: tuple[int, int] | None   # tally format: (for, against) from the approval line
    alternates: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()

    @property
    def voters(self) -> int:
        return sum(self.tally) if self.tally else len(self.voters_for) + len(self.dissents)


@dataclass(frozen=True)
class Statement:
    url: str
    external_key: str               # the URL's path: core.document.external_key
    title: str                      # the page's heading ("Federal Reserve issues FOMC statement")
    page_date: date | None          # the heading's date ("September 16, 2026")
    release_line: str | None        # "For release at 2:00 p.m. EDT"
    text: Extracted
    decisions: tuple[Decision, ...]  # every decision sentence found (a statement has exactly one)
    votes: Votes | None
    problems: tuple[str, ...] = field(default=())   # what couldn't be read (shown by the checks)

    @property
    def decision(self) -> Decision | None:
        return self.decisions[0] if len(self.decisions) == 1 else None

    @property
    def released_at(self) -> datetime | None:
        """The page's release time on its date, New York time ("For release at 2:00 p.m. EDT")."""
        from zoneinfo import ZoneInfo
        m = re.search(r"(\d{1,2}):(\d{2})\s*([ap])\.m\.", self.release_line or "")
        if not m or self.page_date is None:
            return None
        hour = int(m[1]) % 12 + (12 if m[3] == "p" else 0)
        return datetime(self.page_date.year, self.page_date.month, self.page_date.day, hour, int(m[2]),
                        tzinfo=ZoneInfo("America/New_York"))


# ---------------------------------------------------------------------------------------------- the page
def statement_date(url: str) -> date | None:
    m = STATEMENT_PATH.match(urlsplit(url).path)
    return datetime.strptime(m[1], "%Y%m%d").date() if m else None


def _heading(data: bytes) -> tuple[str, date | None, str | None]:
    doc = lxml_html.fromstring(data)
    art = doc.xpath("//div[@id='article']")
    if not art:
        raise StatementError("no div#article: not a Fed press-release page (or its template changed)")
    title = art[0].xpath(".//h3[contains(concat(' ', normalize-space(@class), ' '), ' title ')]")
    when = art[0].xpath(".//p[contains(concat(' ', normalize-space(@class), ' '), ' article__time ')]")
    rel = art[0].xpath(".//p[contains(concat(' ', normalize-space(@class), ' '), ' releaseTime ')]")
    page_date = None
    if when:
        try:
            page_date = datetime.strptime(normalize_inline(when[0].text_content()), "%B %d, %Y").date()
        except ValueError:
            page_date = None
    return (normalize_inline(title[0].text_content()) if title else "", page_date,
            normalize_inline(rel[0].text_content()) if rel else None)


def _block_of(ex: Extracted, start: int) -> tuple[int, int]:
    for b in ex.blocks:
        if b.start <= start < b.end:
            return b.start, b.end
    raise StatementError(f"offset {start} is in no block")


def _names(text: str) -> list[str]:
    """A list of names as the voting lines write them, roles dropped ("Jerome H. Powell, Chair; John C. Williams,
    Vice Chair; …; and Christopher J. Waller")."""
    out = []
    for tok in _LIST_SPLIT.split(text.strip().rstrip(".")):
        tok = tok.strip()
        if not tok or tok.lower() in ROLES:
            continue
        if not re.fullmatch(_NAME, tok):
            raise StatementError(f"can't read {tok!r} as a name in the voting line")
        out.append(tok)
    return out


def _preference(clause: str) -> Preference:
    if m := PREFERS.search(clause):
        return Preference(True, m["verb"], _stated(m), _range(m) if m["lo"] else None)
    if NO_CHANGE.search(clause):
        return Preference(True, "maintain")
    return Preference(False)


def _dissents(listed: str) -> list[Dissent]:
    """'NAMES, who CLAUSE[; and | , and] NAMES, who CLAUSE …' -> one Dissent per name."""
    whos = list(re.finditer(r",\s+who\s+", listed))
    if not whos:
        raise StatementError(f"a 'Voting against' line without 'who …': {listed[:120]!r}")
    groups, clauses, pos = [], [], 0
    for i, w in enumerate(whos):
        head = listed[pos:w.start()]                 # the previous clause, a separator and this group's names
        m = _GROUP_START.search(head)
        if not m:
            raise StatementError(f"can't find the names before 'who' in {listed[:160]!r}")
        if i:
            clauses.append(head[:m.start()])
        groups.append(m["names"])
        pos = w.end()
    clauses.append(listed[pos:])
    out = []
    for names, clause in zip(groups, clauses, strict=True):
        clause = clause.strip().rstrip(".").rstrip(";,").strip()
        pref = _preference(clause)
        out += [Dissent(n, clause, pref) for n in _names(names)]
    return out


def _votes(ex: Extracted) -> tuple[Votes | None, list[str]]:
    text, problems = ex.text, []
    sentences = [(s, e, text[s:e]) for s, e in sentence_spans(text)]
    fors = [(s, e, t) for s, e, t in sentences if VOTING_FOR.match(t)]
    against = [(s, e, t) for s, e, t in sentences if VOTING_AGAINST.match(t)]
    approved = [(s, e, m) for s, e, t in sentences if (m := APPROVED.search(t))]
    if len(against) > 1:
        problems.append(f"{len(against)} 'Voting against' sentences")
        return None, problems
    dissents: list[Dissent] = []
    if against:
        s, e, t = against[0]
        dissents = _dissents(t[VOTING_AGAINST.match(t).end():])
    alternates = tuple(m["who"] for _, _, t in sentences if (m := ALTERNATE.match(t)))
    absent = tuple(n for _, _, t in sentences if (m := ABSENT.match(t)) for n in _names(m["who"]))
    if fors and approved:
        problems.append("both a 'Voting for' line and an approval line")
        return None, problems
    if len(fors) == 1:
        s, e, t = fors[0]
        return Votes("named", (s, e), tuple(_names(t[VOTING_FOR.match(t).end():])), tuple(dissents),
                     against[0][:2] if against else None, None, alternates, absent), problems
    if len(approved) == 1:
        s, e, m = approved[0]
        return Votes("tally", (s, e), (), tuple(dissents), against[0][:2] if against else None,
                     (int(m[1]), int(m[2])), alternates, absent), problems
    problems.append("no voting line ('Voting for the monetary policy action were …' or the approval line)"
                    if not fors and not approved else f"{len(fors) + len(approved)} voting lines")
    return None, problems


def parse(data: bytes, url: str) -> Statement:
    """A statement page. Raises StatementError if it isn't a Fed press release; what can't be read in one that is
    (no decision sentence, no voting line) is left for the checks to report."""
    title, page_date, release = _heading(data)
    ex = extract_html(data, PROFILE)
    problems: list[str] = []
    decisions = []
    for s, e in sentence_spans(ex.text):
        if m := DECISION.search(ex.text, s, e):
            try:
                decisions.append(Decision(m["verb"], _range(m), _stated(m), (s, e), _block_of(ex, s)))
            except StatementError as err:
                problems.append(str(err))
    try:
        votes, more = _votes(ex)
        problems += more
    except StatementError as err:
        votes = None
        problems.append(str(err))
    return Statement(url, urlsplit(url).path, title, page_date, release, ex, tuple(decisions), votes, tuple(problems))


def ranges_in(text: str) -> list[Range]:
    """The range of every decision sentence in a stored statement's text (the previous meeting's, for the change)."""
    return [_range(m) for s, e in sentence_spans(text) if (m := DECISION.search(text, s, e))]


# ---------------------------------------------------------------------------------------------- checks
@dataclass(frozen=True)
class Result:
    name: str
    passed: bool
    detail: str = ""


def checks(st: Statement, decision_date: date) -> list[Result]:
    """The blocking statement checks that need only the page (Arch 02 §2; Spec 01 §2; Anand, 23 Oct 2026). The checks
    that need the previous meeting (the change) and the roster (the voters) run in the task."""
    out = [
        Result("statement_title", TITLE_MARK in st.title, f"title {st.title!r}"),
        Result("statement_text", st.text.char_count > MIN_CHARS, f"{st.text.char_count} characters"),
        Result("statement_date", st.page_date == decision_date,
               f"the page is dated {st.page_date}, the decision {decision_date}"),
    ]
    d = st.decision
    if d is None:
        out.append(Result("target_range", False, f"{len(st.decisions)} decision sentences "
                          + "; ".join(p for p in st.problems if "basis points" in p or "which way" in p)))
    else:
        ok = d.range.width_bps == 25 and d.range.lower >= 0
        out.append(Result("target_range", ok, f"{d.verb} at {d.range} (width {d.range.width_bps} bps)"))
    v = st.votes
    if v is None:
        out.append(Result("voting_line", False, "; ".join(st.problems) or "no voting line"))
        return out
    lo, hi = VOTERS
    if v.format == "named":
        both = sorted(set(v.voters_for) & {x.name for x in v.dissents})
        ok = lo <= v.voters <= hi and not both and len(set(v.voters_for)) == len(v.voters_for)
        out.append(Result("voting_line", ok, f"{len(v.voters_for)} for, {len(v.dissents)} against"
                          + (f"; named for and against: {both}" if both else "")))
        alt_ok = all(any(_same(a, n) for n in v.voters_for + tuple(x.name for x in v.dissents)) for a in v.alternates)
        out.append(Result("alternates_voted", alt_ok, f"alternates {list(v.alternates)}"))
    else:
        f, a = v.tally
        ok = lo <= f + a <= hi and a == len(v.dissents)
        out.append(Result("voting_line", ok, f"approved {f}–{a}; {len(v.dissents)} named against"))
    return out


def _same(ref: str, name: str) -> bool:
    """'Ms. Daly' or 'Patrick Harker' against a full name from the voting line."""
    ref = ref.strip()
    if ref == name:
        return True
    m = re.fullmatch(r"(?:Mr\.|Ms\.|Mrs\.|Dr\.)\s+([A-Z][A-Za-z'’-]+)", ref)
    return bool(m) and name.split()[-1] == m[1]


# ---------------------------------------------------------------------------------------------- the RSS feed
@dataclass(frozen=True)
class RssItem:
    title: str
    link: str
    published: datetime | None

    @property
    def is_statement(self) -> bool:
        """Titled "FOMC statement" and linking a statement page. Only a trigger: the page must still pass the
        statement checks, and the calendar's link, where it has one, decides (Spec 01 §1.1)."""
        return TITLE_MARK in self.title and statement_date(self.link) is not None


def parse_rss(data: bytes) -> list[RssItem]:
    """The Fed's monetary-policy feed: every item, newest first as served. Raises StatementError if it isn't RSS."""
    from email.utils import parsedate_to_datetime
    try:
        root = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True,
                                                             remove_blank_text=True))
    except etree.XMLSyntaxError as e:
        raise StatementError(f"the feed isn't XML: {e}") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise StatementError("the feed isn't RSS 2.0")
    items = []
    for it in root.iter("item"):
        title = normalize_inline(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        when = None
        if pub := (it.findtext("pubDate") or "").strip():
            try:
                when = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                when = None
        items.append(RssItem(title, link, when))
    return items
