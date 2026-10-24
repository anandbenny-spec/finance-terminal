"""MPC minutes (Spec 01 §1.1 "RBI documents"; §2 "RBI votes (D4)"; Prompt 01 B6 part 2). Parses one minutes page into
what policy.rbi_documents stores and runs the checks that need nothing but the page and the meeting's decision date.

The page is the old site's press release (`BS_PressReleaseDisplay.aspx?prid=<id>`, the Annual Policy page's link, two
weeks after the meeting: RBI Act s.45ZL), in the resolution's template (rbi_resolution's header and body profile). Its
title names the meeting's days ("Minutes of the Monetary Policy Committee Meeting, October 5 to 7, 2026"); the body
holds, in order (checked on the 49 minutes saved 23 Oct 2026, Dec 2018 to Oct 2026):
- the meeting's opening paragraphs, then a paragraph reading "Resolution" and the resolution adopted, copied in full
  (its vote sentences included);
- the vote, the statutory record (s.45ZL(b)): a table "Member | Vote", one row per member ("Dr. Ashima Goyal | No"),
  with a third column, "Magnitude of policy repo rate reduction (basis points)", when members voted for different
  sizes of a move. The resolution voted on ("Voting on the Resolution to reduce the policy repo rate by 25 bps to
  6.25 per cent") is the table's first row or the paragraph before it. "Yes" is a vote for that resolution, "No"
  against it; a "No" voter's preferred change isn't in the table (the resolution's own vote sentence gives it);
- each member's statement under a paragraph "Statement by Dr. Nagesh Kumar" (s.45ZL(c)); the chunks carry the member
  as their speaker (`chunk_text(…, speakers="minutes")`);
- the signature and the press-release number ("Press Release: 2026-2027/1361").

The minutes never record stance votes (one Yes/No table, on the rate); the resolution's copy is compared with the
resolution itself by the task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ft.engines.document.chunk import minutes_speaker
from ft.engines.document.text import Extracted, extract_html
from ft.policy import dates, rbi_calendar
from ft.policy.rbi_resolution import NAME, PROFILE, ResolutionError, external_key, header, page_url, press_release_no

TITLE_MARK = "Minutes of the Monetary Policy Committee"
MIN_CHARS = 10000                    # the shortest of the 49 saved minutes holds 5,847 words (May 2022)
MEMBERS = 6                          # the MPC's size (RBI Act s.45ZB(2))

_HEADER = re.compile(r"^\s*Member\s*\|\s*Vote\b(?P<rest>.*)$", re.IGNORECASE)
_VOTING = re.compile(r"^\s*Voting on the Resolution\b", re.IGNORECASE)
_ROW = re.compile(r"^(?P<name>[^|]+?)\s*\|\s*(?P<vote>Yes|No)\s*(?:\|\s*(?P<n>\d+)\s*)?$", re.IGNORECASE)
_RESOLUTION = re.compile(r"^\s*Resolution\s*$", re.IGNORECASE)
# A statement's heading names its member with a title ("Dr. Nagesh Kumar", "Prof Ram Singh") or without one ("M.
# Rajeshwar Rao", Feb 2025): two to six capitalised words. The roster decides who it is.
_BARE_NAME = re.compile(r"[A-Z][A-Za-z'’\-]*\.?(?:\s+[A-Z][A-Za-z'’\-]*\.?){1,5}")
_DOWN = re.compile(r"\b(?:reduc(?:e|tion)|cut|lower|decrease)\b", re.IGNORECASE)
_UP = re.compile(r"\b(?:increas(?:e|ing)|rais(?:e|ing)|hike)\b", re.IGNORECASE)
_KEEP = re.compile(r"\b(?:keep|unchanged|maintain|retain)\b", re.IGNORECASE)
_RATE = re.compile(r"\b(?:to|at)\s+(?P<v>\d+(?:\.\d+)?)\s*per\s*cent\b", re.IGNORECASE)


class MinutesError(ResolutionError):
    """The page can't be read as minutes (a template change, or not the minutes): nothing is stored."""


@dataclass(frozen=True)
class TableVote:
    name: str                        # as written, with its title ("Prof. Jayanth R. Varma")
    yes: bool                        # a vote for the resolution voted on
    magnitude: int | None            # the magnitude column, in bps, unsigned (None: no such column)
    span: tuple[int, int]            # the row in the text
    text: str                        # the row, verbatim ("Dr. Ashima Goyal | No")


@dataclass(frozen=True)
class VoteTable:
    heading: str | None              # the resolution voted on, as written
    direction: int | None            # its move: -1 reduce, +1 increase, 0 keep; None if it doesn't say
    rate: Decimal | None             # the repo rate it names ("to 6.25 per cent"), if it names one
    rows: tuple[TableVote, ...]
    span: tuple[int, int]            # the table block

    def signed(self, v: TableVote) -> int | None:
        """A "Yes" row's magnitude as a change in bps, signed by the move voted on; None without a magnitude or a
        direction."""
        if v.magnitude is None or not self.direction:
            return None
        return self.direction * v.magnitude


@dataclass(frozen=True)
class Statement:
    name: str                        # as the heading writes it
    span: tuple[int, int]            # the heading to the next member's heading (the last: to the end of the text)


@dataclass(frozen=True)
class Minutes:
    url: str
    external_key: str
    title: str
    page_date: date | None
    days: tuple[date, date] | None   # the meeting's first and last day, from the title
    text: Extracted
    table: VoteTable | None
    statements: tuple[Statement, ...]
    resolution: tuple[int, int] | None   # the resolution's copy: from after the "Resolution" paragraph to the vote
    press_release: str | None            # "2026-2027/1361" (Brief §6.2: named, never linked)
    problems: tuple[str, ...] = field(default_factory=tuple)


def _direction(s: str) -> int | None:
    for p, d in ((_DOWN, -1), (_UP, 1), (_KEEP, 0)):
        if p.search(s):
            return d
    return None


def _table(ex: Extracted, problems: list[str]) -> VoteTable | None:
    text = ex.text
    found = []
    for i, b in enumerate(ex.blocks):
        if b.kind != "table":
            continue
        lines = text[b.start:b.end].split("\n")
        k = next((j for j, ln in enumerate(lines) if _HEADER.match(ln)), None)
        if k is not None:
            found.append((i, b, lines, k))
    if not found:
        return None
    if len(found) > 1:
        problems.append(f"{len(found)} vote tables")
    i, b, lines, k = found[0]
    heading = next((ln for ln in lines[:k] if _VOTING.match(ln)), None)
    if heading is None and i > 0 and _VOTING.match(ex.block_text(ex.blocks[i - 1])):
        heading = " ".join(ex.block_text(ex.blocks[i - 1]).split())
    rest = _HEADER.match(lines[k])["rest"]
    direction = _direction(heading) if heading else None
    if direction is None and rest:
        direction = _direction(rest)                       # "Magnitude of policy repo rate reduction (basis points)"
    rate = None
    if heading and (m := _RATE.search(heading)):
        rate = Decimal(m["v"])
    rows = []
    pos = b.start + sum(len(ln) + 1 for ln in lines[:k + 1])
    for ln in lines[k + 1:]:
        start, end = pos, pos + len(ln)
        pos = end + 1
        m = _ROW.match(ln)
        if m is None or not NAME.fullmatch(m["name"].strip()):
            problems.append(f"vote table row {ln!r} isn't 'member | Yes or No'")
            continue
        if m["n"] is not None and not rest.strip():
            problems.append(f"vote table row {ln!r} has a magnitude without its column")
        rows.append(TableVote(m["name"].strip(), m["vote"].lower() == "yes", int(m["n"]) if m["n"] else None,
                              (start, end), ln))
    if rest.strip() and any(r.yes and r.magnitude is None for r in rows):
        problems.append("a 'Yes' row without its magnitude")
    return VoteTable(heading, direction, rate, tuple(rows), (b.start, b.end))


def _statements(ex: Extracted) -> tuple[Statement, ...]:
    heads = []
    for b in ex.blocks:
        body = ex.block_text(b)
        if ("\n" not in body and len(body.split()) <= 12 and (name := minutes_speaker(body))
                and (NAME.fullmatch(name) or _BARE_NAME.fullmatch(name))):
            heads.append((b.start, name))
    out = []
    for k, (start, name) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(ex.text)
        while end > start and ex.text[end - 1].isspace():
            end -= 1
        out.append(Statement(name, (start, end)))
    return tuple(out)


def _resolution_zone(ex: Extracted, table: VoteTable | None, statements: tuple[Statement, ...]) -> tuple[int, int] | None:
    start = next((b.end for b in ex.blocks if _RESOLUTION.match(ex.block_text(b))), None)
    if start is None:
        return None
    ends = [b.start for b in ex.blocks if b.start > start and _VOTING.match(ex.block_text(b))]
    if table is not None:
        ends.append(table.span[0])
    if statements:
        ends.append(statements[0].span[0])
    ends = [e for e in ends if e > start]
    if not ends:
        return None
    end = min(ends)
    while end > start and ex.text[end - 1].isspace():
        end -= 1
    while start < end and ex.text[start].isspace():
        start += 1
    return start, end


def parse(data: bytes, url: str) -> Minutes:
    """A minutes page. Raises MinutesError if it isn't an RBI press release; what can't be read in one that is is left
    for the checks to report."""
    try:
        title, page_date = header(data)
    except ResolutionError as e:
        raise MinutesError(str(e)) from None
    ex = extract_html(data, PROFILE)
    problems: list[str] = []
    days = None
    try:
        days = rbi_calendar.minutes_days(title)
    except dates.DateParseError as e:
        problems.append(f"the title's meeting days: {e}")
    table = _table(ex, problems)
    statements = _statements(ex)
    return Minutes(page_url(url), external_key(url), title, page_date, days, ex, table, statements,
                   _resolution_zone(ex, table, statements), press_release_no(ex.text), tuple(problems))


# ---------------------------------------------------------------------------------------------- checks
@dataclass(frozen=True)
class Result:
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = True


def checks(mn: Minutes, decision_date: date) -> list[Result]:
    """The checks that need only the page (Arch 02 §2; Spec 01 §2): the minutes of this meeting, with six votes and
    six members' statements. The votes against the decision and the roster are checked in the task."""
    out = [
        Result("minutes_title", TITLE_MARK.lower() in mn.title.lower(), f"title {mn.title!r}"),
        Result("minutes_text", mn.text.char_count > MIN_CHARS, f"{mn.text.char_count} characters"),
        Result("minutes_date", mn.days is not None and mn.days[1] == decision_date
               and (mn.page_date is None or mn.page_date >= decision_date),
               f"the title's meeting ends {mn.days[1] if mn.days else None}, the page is dated {mn.page_date}, "
               f"the decision {decision_date}"),
    ]
    rows = mn.table.rows if mn.table else ()
    table_problems = [p for p in mn.problems if "vote table" in p or "'Yes' row" in p]
    out.append(Result("minutes_votes_table", mn.table is not None and len(rows) == MEMBERS and not table_problems,
                      ("no 'Member | Vote' table" if mn.table is None else
                       f"{len(rows)} rows: " + ", ".join(r.text for r in rows))
                      + ("; " + "; ".join(table_problems) if table_problems else "")))
    names = [s.name for s in mn.statements]
    out.append(Result("minutes_statements", len(names) == MEMBERS and len(set(names)) == MEMBERS,
                      f"{len(names)} statements: " + ", ".join(names)))
    return out
