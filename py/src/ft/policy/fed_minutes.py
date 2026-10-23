"""FOMC minutes (Spec 01 §1.1 "Fed minutes", "Canonical text"; §2 "Fed votes"; Prompt 01 B5 part 2). Parses one
minutes page into what policy.fed_documents stores, and runs the checks that need nothing but the page and the
meeting's decision date.

The page (`/monetarypolicy/fomcminutesYYYYMMDD.htm`, the calendar's link, three weeks after a scheduled meeting) has a
title (<h3>) and the minutes; the canonical text is the minutes without the title and the footnotes
(text.PROFILES["fed_minutes"]). Read from it:
- **the meeting's days:** the opening sentence ("A joint meeting of the Federal Open Market Committee and the Board of
  Governors was held … on Tuesday, January 29, 2019, at 10:00 a.m. and continued on Wednesday, January 30, 2019, at
  9:00 a.m."); its last date is the decision date;
- **the decision vote:** "Voting for this action: NAMES." and "Voting against this action: NAMES[, who …]." or
  "None." (left out once, 4 May 2022, when nobody dissented), after the statement the vote approved; "X voted as [an]
  alternate member at this meeting."; "Absent and not voting: X". The names carry no roles, except in Jan 2022, Oct
  2025 and Dec 2025 ("Jerome H. Powell, Chair; John C. Williams, Vice Chair; …");
- **another meeting's vote:** minutes can close with an unscheduled meeting held since the previous minutes, under its
  own heading. "Videoconference meeting of March 2" (in the 15 Mar 2020 minutes) holds the vote on the 3 Mar 2020 cut,
  which has no minutes of its own; "Videoconference meeting of October 4, 2019" (in the Oct 2019 minutes) set no rate
  and holds no vote. Such a vote belongs to the meeting held that day.

Checked on the 62 minutes saved 23 Oct 2026 (Jan 2019 to Sep 2026): one decision vote each outside those sections,
9 to 12 voters, the opening sentence's last date the decision date. The minutes name every voter, so they give the
votes of the statements that name only the dissenters (from 17 Jun 2026: decided by Anand, 23 Oct 2026).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit

from lxml import etree
from lxml import html as lxml_html

from ft.engines.document.text import Extracted, ExtractError, extract_html, normalize_inline, sentence_spans
from ft.policy.fed_statement import VOTERS, Result

PROFILE = "fed_minutes"
TITLE = "Minutes of the Federal Open Market Committee"
MIN_CHARS = 10_000        # the shortest saved minutes (16 Sep 2026) hold 34,801 characters

_DAY = re.compile(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Z][a-z]+ \d{1,2}, \d{4})")
_SECTION = re.compile(r"^[A-Z][A-Za-z -]{0,40}\b(?:[Mm]eeting|[Cc]all) of (?P<md>[A-Z][a-z]+ \d{1,2})(?:, (?P<y>\d{4}))?$")
VOTING_FOR = re.compile(r"^Voting for (?:this|the) action:\s*(?P<names>.+?)\.?$")
VOTING_AGAINST = re.compile(r"^Voting against (?:this|the) action:\s*(?P<names>.+?)\.?$")
ALTERNATE = re.compile(r"^(?P<who>.+?) voted as (?:an )?alternate member at this meeting\.?$")
ABSENT = re.compile(r"^Absent and not voting(?: (?:was|were))?:?\s*(?P<who>.+?)\.?$")
ROLES = {"chair", "chairman", "vice chair", "vice chairman"}
# A person is two to five capitalised words ("Jerome H. Powell", "Kathleen O'Neill Paese"); a list separates them with
# commas, semicolons or "and", each optionally followed by a role.
_PERSON = r"[A-Z][A-Za-z'’-]*\.?(?:\s+[A-Z][A-Za-z'’-]*\.?){1,4}"
_ROLE = r"(?:,\s*(?:Chair|Chairman|Vice Chair|Vice Chairman)\b)"
_SEP = r"(?:\s*[;,]\s*(?:and\s+)?|\s+and\s+)"
_LIST = rf"{_PERSON}{_ROLE}?(?:{_SEP}{_PERSON}{_ROLE}?)*"
_TRAILING = re.compile(rf"(?<![\w'’.-])(?P<names>{_LIST})\s*$")
_SPLIT = re.compile(r"\s*;\s*(?:and\s+)?|\s*,\s*(?:and\s+)?|\s+and\s+")
_TITLE_WORDS = re.compile(r"^(?:Mr|Ms|Mrs|Dr|President|Governor|Chair|Chairman|Vice Chair)\.?\s+")


class MinutesError(ValueError):
    """The page can't be read as FOMC minutes (a template change, or not minutes): nothing is stored."""


def names(text: str) -> list[str]:
    """A voting line's names, roles dropped ("Jerome H. Powell, Chair; John C. Williams, Vice Chair; …; and
    Christopher J. Waller"). Raises MinutesError on anything that isn't a list of names."""
    text = text.strip().rstrip(".").strip()
    if not re.fullmatch(_LIST, text):
        raise MinutesError(f"can't read {text[:160]!r} as a list of names")
    return [p.strip() for p in _SPLIT.split(text) if p.strip() and p.strip().lower() not in ROLES]


def against_names(text: str) -> list[str]:
    """The dissenters a "Voting against this action:" line names: "None."; "NAMES."; or "NAMES, who CLAUSE[, and |
    ; and | and] NAMES, who CLAUSE." (Oct 2025 on), whose clauses the statement already gives verbatim."""
    text = text.strip().rstrip(".").strip()
    if re.fullmatch(r"None", text):
        return []
    whos = list(re.finditer(r",\s+who\s+", text))
    if not whos:
        return names(text)
    out, pos = [], 0
    for w in whos:
        m = _TRAILING.search(text, pos, w.start())
        if not m:
            raise MinutesError(f"can't find the names before 'who' in {text[:160]!r}")
        out += names(m["names"])
        pos = w.end()
    return out


def same(ref: str, name: str) -> bool:
    """'Ms. Daly', 'President Goolsbee' or 'Patrick Harker' against a full name from the voting line."""
    ref = ref.strip()
    if ref == name:
        return True
    rest = _TITLE_WORDS.sub("", ref).strip()
    return bool(rest) and " " not in rest and name.split()[-1] == rest


@dataclass(frozen=True)
class Vote:
    """One decision vote as the minutes record it."""
    held_on: date | None             # None: the minutes' own meeting; else the day of the section's meeting
    heading: str | None              # the section's heading ("Videoconference meeting of March 2")
    voters_for: tuple[str, ...]
    against: tuple[str, ...]
    for_sentence: tuple[int, int]    # code-point span of "Voting for this action: …"
    against_sentence: tuple[int, int] | None
    alternates: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()

    @property
    def voters(self) -> int:
        return len(self.voters_for) + len(self.against)


@dataclass(frozen=True)
class Minutes:
    url: str
    external_key: str                # the URL's path: core.document.external_key
    title: str                       # the page's <h3>
    meeting_days: tuple[date, ...]   # the dates of the opening sentence
    text: Extracted
    votes: tuple[Vote, ...]          # every decision vote found, the minutes' own first
    problems: tuple[str, ...] = field(default=())

    @property
    def vote(self) -> Vote | None:
        """The vote on the minutes' own meeting's decision (exactly one, or None)."""
        own = [v for v in self.votes if v.held_on is None]
        return own[0] if len(own) == 1 else None


def _title(data: bytes) -> str:
    try:
        doc = lxml_html.fromstring(data)
    except (ValueError, etree.ParserError) as e:
        raise MinutesError(f"not an HTML page: {e}") from None
    h3 = doc.xpath("//div[@id='article']/h3")
    if not h3:
        raise MinutesError("no div#article title: not a Fed minutes page (or its template changed)")
    return normalize_inline(h3[0].text_content())


def _meeting_days(ex: Extracted) -> tuple[date, ...]:
    text = ex.text
    for s, e in sentence_spans(text, 0, min(len(text), 4000)):
        t = text[s:e]
        if ("was held" in t or " met " in t) and (found := _DAY.findall(t)):
            return tuple(datetime.strptime(d, "%B %d, %Y").date() for d in found)
    return ()


def _sections(ex: Extracted, year: int | None) -> list[tuple[int, int, date | None, str | None]]:
    """(start, end, the section's meeting day or None, heading) for the minutes and each closing section."""
    out, start, held, heading = [], 0, None, None
    for b in ex.blocks:
        line = ex.text[b.start:b.end].split("\n", 1)[0]       # a heading block, or a run-in heading (<strong>…<br>)
        if not (m := _SECTION.match(line)):
            continue
        y = int(m["y"]) if m["y"] else year
        if y is None:
            continue
        try:
            day = datetime.strptime(f"{m['md']}, {y}", "%B %d, %Y").date()
        except ValueError:
            continue
        out.append((start, b.start, held, heading))
        start, held, heading = b.start, day, line
    out.append((start, len(ex.text), held, heading))
    return out


def _votes(ex: Extracted, sections, problems: list[str]) -> list[Vote]:
    text, out = ex.text, []
    for s0, e0, held, heading in sections:
        sentences = [(s, e, text[s:e]) for s, e in sentence_spans(text, s0, e0)]
        fors = [(i, s, e, m) for i, (s, e, t) in enumerate(sentences) if (m := VOTING_FOR.match(t))]
        againsts = [(i, s, e, m) for i, (s, e, t) in enumerate(sentences) if (m := VOTING_AGAINST.match(t))]
        where = heading or "the minutes"
        if len(fors) > 1:
            problems.append(f"{len(fors)} 'Voting for this action' lines in {where}")
            continue
        if not fors:
            if againsts:
                problems.append(f"a 'Voting against this action' line without 'Voting for' in {where}")
            continue
        i, s, e, m = fors[0]
        if len(againsts) > 1 or (againsts and againsts[0][0] < i):
            problems.append(f"'Voting against' lines out of place in {where}")
            continue
        try:
            voters_for = names(m["names"])
            against = against_names(againsts[0][3]["names"]) if againsts else []
        except MinutesError as err:
            problems.append(f"{where}: {err}")
            continue
        alternates = tuple(a["who"] for _, _, t in sentences if (a := ALTERNATE.match(t)))
        absent = tuple(n for _, _, t in sentences if (a := ABSENT.match(t)) for n in names(a["who"]))
        out.append(Vote(held, heading, tuple(voters_for), tuple(against), (s, e),
                        againsts[0][1:3] if againsts else None, alternates, absent))
    return out


def parse(data: bytes, url: str) -> Minutes:
    """A minutes page. Raises MinutesError if it isn't one; what can't be read in one that is (no vote, no opening
    sentence) is left for the checks to report."""
    title = _title(data)
    try:
        ex = extract_html(data, PROFILE)
    except ExtractError as e:
        raise MinutesError(str(e)) from None
    days = _meeting_days(ex)
    problems: list[str] = []
    votes = _votes(ex, _sections(ex, days[-1].year if days else None), problems)
    return Minutes(url, urlsplit(url).path, title, days, ex, tuple(votes), tuple(problems))


def checks(mn: Minutes, decision_date: date) -> list[Result]:
    """The blocking minutes checks that need only the page (Arch 02 §2's statement checks, applied to the minutes):
    the title; the text's length; the meeting's last day is the decision date; one decision vote of 8-12 voters, nobody
    named twice, every alternate who voted among the voters."""
    out = [
        Result("minutes_title", mn.title == TITLE, f"title {mn.title!r}"),
        Result("minutes_text", mn.text.char_count > MIN_CHARS, f"{mn.text.char_count} characters"),
        Result("minutes_date", bool(mn.meeting_days) and mn.meeting_days[-1] == decision_date,
               f"the minutes are of a meeting held {', '.join(map(str, mn.meeting_days)) or '(no date found)'}, the "
               f"decision {decision_date}"),
    ]
    v = mn.vote
    if v is None:
        out.append(Result("minutes_vote", False, "; ".join(mn.problems) or
                          f"{sum(x.held_on is None for x in mn.votes)} decision votes"))
        return out
    every = v.voters_for + v.against
    lo, hi = VOTERS
    ok = lo <= v.voters <= hi and len(set(every)) == len(every) and not mn.problems
    out.append(Result("minutes_vote", ok, f"{len(v.voters_for)} for, {len(v.against)} against"
                      + (f"; {'; '.join(mn.problems)}" if mn.problems else "")))
    out.append(Result("minutes_alternates", all(any(same(a, n) for n in every) for a in v.alternates),
                      f"alternates {list(v.alternates)}"))
    return out
