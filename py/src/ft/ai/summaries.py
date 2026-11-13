"""Module 1's cited summaries (Spec 01 §3.2-§3.3; Architecture 03 §1.2, §1.4): `doc_summary` (3-6 bullets on one
document) and `statement_diff_summary` (2-5 bullets on what changed from the previous statement or resolution).
Both use the citations route: the documents go as plain-text document blocks, and the reply's text blocks carry the
passages they cite.

**Checking a reply** (`check`), before anything is stored:
- Bullets are the reply's lines that start with "- " (or another list marker); text outside them (a preamble) is
  ignored and never stored. A bullet's citations are those of the reply's text blocks that overlap it.
- Each citation is mapped into our copy of the document (`citations.map_api_citation`): the API's offsets if our text
  there equals the cited text, else the cited text found exactly once; otherwise it isn't evidence.
- A bullet is kept only with at least one verified citation, and only if every figure it writes is a figure in its
  cited passages ("no number that isn't in a cited passage", Spec 01 §3.3). Figures are compared whole, as written:
  "3/4" doesn't match "3-3/4", "Q2" doesn't match "Q3", and a signed figure needs the same sign (`figures`).
- Text after the first bullet that isn't a bullet (a closing remark) fails the reply: it would sit under the last
  bullet's citation. A preamble before the first bullet is ignored and never stored. For `statement_diff_summary` a citation
  counts only if it falls on a sentence the redline marks changed, added or removed: so additions cite the new text
  and removals the old, as only the new text holds an addition and only the old a removal.
- The bullet count must be in range (3-6, 2-5).
- For `statement_diff_summary` from prompt v4 on (#445, R05-2; Anand's option 1, 12 Nov 2026; `drop_copies`), before
  these checks: a cited text block that is a word-for-word copy of its own cited text comes out of the bullet's text, and
  its citations stay on the bullet (`mark_copies`, `settle_copies`). The checks then run on the final text, unchanged.
- For `statement_diff_summary` from prompt v7 on (#445, round 6; Anand's decision of 12 Nov 2026, 22:36 IST;
  `check_quotes`), one more check, a tightening: every stretch a bullet puts in double quotation marks is found in that
  bullet's own cited passages, and its quotation marks are balanced (`quote_problem`). v7 also runs on Sonnet 5.5
  (`DIFF_MODEL_OF`).
- From v8 on (#445, round 7; `strict_copies`, `check_repeats`): only a copy that stands as a whole sentence comes out, and
  every other copy goes in quotation marks; and a bullet that repeats a stretch of its own words fails (`repeat_problem`).
- From v10 on (#445, round 9; `check_ends`): a bullet that doesn't end as a sentence ends fails (`end_problem`). v10 runs at
  effort medium (`DIFF_EFFORT_OF`).
- From v11 on (#445, round 10; Anand's decision of 13 Nov 2026, by 07:18 IST): the structured route (`DIFF_JSON_FROM`). The reply
  is the schema's JSON, each bullet its text and its quotes by document; `check_diff_json` finds each quote in that
  document's changed sentences, cites them, and gives the bullet the checks above (figures, quotations, repeats, ends).
- From v12 on (#445, round 11; `DIFF_STRICT_FROM`): also no run of a statement's words outside quotation marks, and no two
  quotations side by side.
- From v13 on (#445, round 12; Anand's decision of 13 Nov 2026, 13:40 IST; `DIFF_WHOLE_FROM`): also no claim in the bullet's
  own words that something stayed the same, and no quotation cut in the middle of a phrase.
- From v14 on (#445, round 13; Anand's pick of 13 Nov 2026, before 17:12 IST; `DIFF_SIDES_FROM`): also no "again" or "as
  before" in the bullet's own words, and no claim about the previous document without a quote from it.
A reply with any dropped bullet or the wrong count isn't published: the request is retried once, with a note naming
the problems (so the retry is a new input, with its own ops.ai_request row), then rejected with an alert
(`policy.ai_batch`). The database checks the published output's citations again at commit (Architecture 01 §1.4).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from ft.ai.citations import map_api_citation
from ft.ai.client import SONNET, Prompt, Reply, Request, Source, TextPart, load_schema

BULLETS = {"doc_summary": (3, 6), "statement_diff_summary": (2, 5)}
# The model of a statement_diff_summary prompt version that doesn't run on the task's (Haiku 5.5). v7 is Sonnet 5.5's (#445,
# round 6; Anand's decision of 12 Nov 2026, 22:36 IST): the judge failed Haiku's round 5 (v6) on 7 of 10 targets, mostly on
# the model's own sentences, and CLAUDE.md §6 steps up to Sonnet where an evaluation shows Haiku falls short (as call_tone's
# Sonnet round, #175). The task's effort and max_tokens stay. A version that isn't here runs on Haiku, so v1 to v6 are unchanged.
DIFF_MODEL_OF = {"v7": SONNET, "v8": SONNET, "v9": SONNET, "v10": SONNET, "v11": SONNET, "v12": SONNET,
                 "v13": SONNET, "v14": SONNET}   # v8: v7's text, marking the copies rule's revision (round 7); v9: v8 plus three rules
# (round 8); v10: v9 plus a rule on citation carriers (round 9); v11: the json route (round 10); v12: v11 plus a rule, with
# two more checks (round 11); v13: v12 plus two rules, with two more checks (round 12); v14: v13 plus two rules, with one
# more check and a wider same-claim check (round 13).
# The effort of a version that doesn't run at the task's (low): v10 runs at medium (round 9, 13 Nov 2026), so Sonnet plans
# its bullets; at low it wrote words only to hold a citation ("Previous new") in round 8.
DIFF_EFFORT_OF = {"v10": "medium", "v11": "medium", "v12": "medium", "v13": "medium", "v14": "medium"}
# From v11 (#445, round 10; Anand's decision of 13 Nov 2026, by 07:18 IST, after rounds 6 to 9 on the citations route kept
# showing words written only to hold a citation): the json route, as call_summary v6. The reply is v11.schema.json's JSON and
# our code places the quotes (`check_diff_json`), so the model has no text to carry a citation with.
DIFF_JSON_FROM = 11
DIFF_MAX_QUOTES = 6                    # quotes a bullet may rest on
# From v12 (#445, round 11; tightenings): a bullet fails when it copies UNQUOTED_MIN_WORDS or more of a statement's words in a
# row outside quotation marks (at least COPY_PLAIN_WORDS of them ordinary words: members are named plainly), or sets two quotations side
# by side with nothing of its own between them (`unquoted_copy_problem`, `side_by_side_problem`). Round 10 (v11 on dev):
# "…were of the view that the stance be retained at neutral." (2530), "…will slow the pace of decline of its securities
# holdings" (2533), "at least at the current pace" "over coming months" (2529).
DIFF_STRICT_FROM = 12
UNQUOTED_MIN_WORDS = 8
COPY_PLAIN_WORDS = 5                   # of which at least this many aren't names, initials or capitalised words
# From v13 (#445, round 12; Anand's decision of 13 Nov 2026, 13:40 IST, after the judge failed round 11; tightenings): a
# bullet fails when its own words (outside quotation marks) say that something stayed the same (`same_problem`): on this route
# its quotes come only from changed sentences, so nothing it cites can show that ("The repo rate sentence is not among the
# changes.", 2541; "The policy repo rate decision is unchanged, but …", 2538). It also fails when a quotation it doesn't go
# on from (punctuation or the bullet's end follows it, not a word of its own) stops where its cited sentence goes on with a
# word, if the quotation starts where that sentence starts or ends on a word such as "the" or "to" (`cut_quote_problem`):
# round 11's front-page bullet ended a sentence it had started quoting at "…determine the policy stance appropriate" (2546),
# as round 10's did at "…the policy stance" (2537). A quotation the bullet goes on from ("…would continue to increase"
# them, 2544) passes, as the judge passed it.
DIFF_WHOLE_FROM = 13
# From v14 (#445, round 13; Anand's pick of 13 Nov 2026, before 17:12 IST, after the build session's own read of round 12;
# tightenings): the same-claim check also fails "again" and "as before" in the bullet's own words ("…again voted to keep the
# policy rate unchanged", 2556), and a bullet whose own words describe the previous document ("moved from", "previously",
# "was removed", "instead of" …) fails unless one of its quotes is from the previous document (`previous_problem`; "Loretta
# J. Mester moved from voting for the action to dissent", 2552, quoting only the new statement; round 10's Bullard, Waller
# and Schmid bullets were the same). The new document's side has no check: no bullet in rounds 10 to 12 described it
# without quoting it.
DIFF_SIDES_FROM = 14


def diff_json(version: str) -> bool:
    """Whether a statement_diff_summary prompt version takes the json route (`DIFF_JSON_FROM`)."""
    return int(version.lstrip("v")) >= DIFF_JSON_FROM
_MARK = re.compile(r"^[ \t]*(?:[-*•–—]|\d{1,2}[.)])[ \t]+")


@dataclass(frozen=True)
class Doc:
    """A published document as the summaries see it."""
    document_id: int
    doc_type: str
    label: str                        # core.doc_type.label: 'FOMC statement'
    title: str                        # core.document.title: 'FOMC statement, 28 Oct 2026'
    bank: str                         # core.entity.short_name: 'Fed', 'RBI'
    day: date | None                  # the release date in the bank's time zone
    text: str                         # the canonical text (the chunks, concatenated)
    event_id: int | None = None       # its meeting (the stance is the meeting's: core.ai_output.event_id)
    event_day: date | None = None     # the meeting's decision date (a projection's vintage)
    raw_file_id: int | None = None    # the page it was read from (the stance cross-check re-parses it)
    source_url: str | None = None


@dataclass(frozen=True)
class Cited:
    document_id: int
    start: int
    end: int
    how: str                          # 'offsets' | 'search'


@dataclass
class Bullet:
    text: str
    citations: list[Cited] = field(default_factory=list)
    problem: str | None = None        # why it's dropped
    section: str | None = None        # a call summary's list ('management' | 'analysts'); None for module 1's


@dataclass
class Checked:
    task: str
    bullets: list[Bullet]
    problems: list[str]               # reply-level: the count, no bullets
    mapping: Counter                  # citations by how they mapped: offsets, search, failed, wrong_place, ...
    copies: Counter = field(default_factory=Counter)   # statement_diff_summary: copied blocks 'removed' or 'quoted' (#445)

    @property
    def kept(self) -> list[Bullet]:
        return [b for b in self.bullets if b.problem is None]

    @property
    def dropped(self) -> list[Bullet]:
        return [b for b in self.bullets if b.problem is not None]

    @property
    def ok(self) -> bool:
        return not self.problems and not self.dropped

    def reasons(self) -> list[str]:
        return self.problems + [f"bullet {i + 1}: {b.problem}" for i, b in enumerate(self.bullets) if b.problem]

    def note(self) -> str:
        """The retry's note about this reply (`retry_note`)."""
        return retry_note(self)


def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "date not recorded"


# ---------------------------------------------------------------------------------------------- requests
def _note(note: str | None) -> str | None:
    """The retry's note: sent as its own last block, after the unchanged instruction, so the retry can read the first
    attempt's cached prefix."""
    if not note:
        return None
    return ("Your previous answer to this request couldn't be published: " + note
            + "\nAnswer again, following every rule. Cite the passage that states each point, and write numbers "
              "only as the cited passage writes them.")


note_block = _note        # the retry's note block, shared with ft/ai/call_summary.py (the second user, #174)


def doc_summary_request(doc: Doc, prompt: Prompt, *, note: str | None = None) -> Request:
    lo, hi = BULLETS["doc_summary"]
    src = Source(doc.document_id, doc.text, doc.title, context=f"{doc.bank} · {doc.label} · released {_when(doc.day)}")
    instruction = f"Summarise the {doc.label} above ({doc.title}) in {lo} to {hi} bullets, following the rules."
    return Request("doc_summary", prompt, (src,), instruction, note=_note(note))


def changed_spans(segments: dict) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(spans in the old text, spans in the new text) of the sentences the redline marks changed, removed or added."""
    old, new = [], []
    for p in segments.get("pairs", []):
        if p.get("kind") in ("changed", "removed"):
            old += [tuple(x) for x in p.get("old", [])]
        if p.get("kind") in ("changed", "added"):
            new += [tuple(x) for x in p.get("new", [])]
    return old, new


def redline_text(segments: dict, old_text: str, new_text: str) -> str:
    """The changed sentences as plain text for the instruction (it's guidance, not a citable document)."""
    out = []
    for p in segments.get("pairs", []):
        o = " ".join(old_text[s:e] for s, e in p.get("old", []))
        n = " ".join(new_text[s:e] for s, e in p.get("new", []))
        if p.get("kind") == "changed":
            out.append(f'- Revised. Previous: "{o}" New: "{n}"')
        elif p.get("kind") == "added":
            out.append(f'- Added: "{n}"')
        elif p.get("kind") == "removed":
            out.append(f'- Removed: "{o}"')
    return "\n".join(out)


def diff_summary_request(new: Doc, old: Doc, segments: dict, prompt: Prompt, *, note: str | None = None) -> Request:
    lo, hi = BULLETS["statement_diff_summary"]
    sources = (Source(old.document_id, old.text, f"Previous {old.label}, {_when(old.day)}",
                      context=f"Document 1: the previous {old.label} ({old.bank}, released {_when(old.day)})"),
               Source(new.document_id, new.text, f"New {new.label}, {_when(new.day)}",
                      context=f"Document 2: the new {new.label} ({new.bank}, released {_when(new.day)})"))
    structured = diff_json(prompt.version)
    ask = (f"In {lo} to {hi} bullets, explain what changed, as the schema's JSON, following the rules." if structured
           else f"In {lo} to {hi} bullets, explain what changed, following the rules.")
    instruction = (f"Document 1 is the previous {old.label} ({_when(old.day)}); document 2 is the new one "
                   f"({_when(new.day)}). These sentences changed between them:\n\n"
                   f"{redline_text(segments, old.text, new.text)}\n\n" + ask)
    if structured:
        return Request("statement_diff_summary", prompt, sources, instruction, model=DIFF_MODEL_OF.get(prompt.version),
                       effort=DIFF_EFFORT_OF.get(prompt.version), route="json",
                       schema=load_schema("statement_diff_summary", prompt.version), note=_json_note(note))
    return Request("statement_diff_summary", prompt, sources, instruction, model=DIFF_MODEL_OF.get(prompt.version),
                   effort=DIFF_EFFORT_OF.get(prompt.version), note=_note(note))


def _json_note(note: str | None) -> str | None:
    """The json route's retry note (v11 on): as `_note`, in the route's own words."""
    if not note:
        return None
    return ("Your previous answer to this request couldn't be published: " + note
            + "\nAnswer again as the schema's JSON, following every rule. Copy each quote exactly from one changed sentence of "
              "the document it names, put every quotation of the text among the bullet's quotes, and write numbers only as "
              "the quoted sentences write them.")


# ---------------------------------------------------------------------------------------------- checking a reply
_LEAD = re.compile(r"^\s*(?:(?:[-*\u2022\u2013\u2014]|\d{1,2}[.)])[ \t]+)?")


_HEADING_FRAME = re.compile(r"^[\s#*_>]+|[\s*_:]+$")


@dataclass
class Scanned:
    bullets: list[tuple[int | None, str, list]]   # (the heading it sits under, its text, the API citations credited to it)
    stray: list[str]                              # the non-bullet lines after the first bullet (or heading)
    headings: list[int]                           # the headings found, as indexes into the given ones, in reply order


def _heading(line: str, headings: tuple[str, ...]) -> int | None:
    """The index of the heading a line is (case and markdown framing aside: '## What we said:', '**What we said**')."""
    flat = _HEADING_FRAME.sub("", line).strip().lower()
    return next((i for i, h in enumerate(headings) if flat == h.lower()), None)


def scan(parts: list[TextPart], headings: tuple[str, ...] = ()) -> Scanned:
    """The reply's bullets, each with the text blocks' citations credited to it. A text block's citations go to the bullet
    its text starts in (after any whitespace and list marker), so one block's citation never verifies two bullets. With
    `headings`, a line that is one of them is a heading, not a stray line, and the bullets after it sit under it."""
    text, spans = "", []
    for p in parts:
        lead = _LEAD.match(p.text)
        spans.append((len(text) + (lead.end() if lead else 0), p.citations))
        text += p.text
    bullets: list[list] = []                      # [region start, text start, text end, the heading it sits under]
    stray: list[str] = []
    seen: list[int] = []
    under: int | None = None
    pos = 0
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        pos = end + 1
        if headings and (h := _heading(line, headings)) is not None:
            seen.append(h)
            under = h
            continue
        m = _MARK.match(line)
        if m:
            bullets.append([start, start + m.end(), end, under])
        elif line.strip() and (bullets or seen):
            stray.append(line.strip())
    out = []
    for i, (region, s, e, h) in enumerate(bullets):
        nxt = bullets[i + 1][0] if i + 1 < len(bullets) else len(text) + 1
        cites = [c for at, cs in spans if region <= at < nxt for c in cs]
        body = " ".join(text[s:e].split())
        if body:
            out.append((h, body, cites))
    return Scanned(out, stray, seen)


def split_bullets(parts: list[TextPart]) -> tuple[list[tuple[str, list]], list[str]]:
    """(the bullets as (text, the API citations credited to it), the non-bullet lines after the first bullet)."""
    found = scan(parts)
    return [(body, cites) for _, body, cites in found.bullets], found.stray


# A figure as written: digits with whatever they're joined to by . , / - (and letters: "Q2", "FY27", "10-year"), with
# a sign only when it stands alone ("-0.25", not the hyphen of "COVID-19"). Dashes and the minus sign read as "-".
_FIGURE = re.compile(r"(?<![\w.])([-+]?)([A-Za-z]*\d\w*(?:[.,/-]\w+)*)")
_PLAIN = re.compile(r"^\d{1,3}(?:,\d{2,3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$")
_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-"})


def figures(text: str) -> list[tuple[str, str]]:
    """Every figure in `text` as (sign, canonical form): plain numbers by value ("5.50" and "5.5" agree; Indian and
    Western grouping read whole), everything else as written, lower-cased ("4-1/4", "q2", "fy2026-27")."""
    out = []
    for sign, tok in _FIGURE.findall(text.translate(_DASHES)):
        if _PLAIN.match(tok):
            whole, _, frac = tok.replace(",", "").partition(".")
            tok = (whole.lstrip("0") or "0") + ("." + frac.rstrip("0") if frac.rstrip("0") else "")
        out.append((sign, tok.lower()))
    return out


def unsupported_figures(bullet: str, passages: list[str]) -> list[str]:
    """The bullet's figures that aren't in its cited passages. An unsigned figure may match either sign; a signed one
    needs the same sign (Review 01, F4's rule for numbers)."""
    have = {f for p in passages for f in figures(p)}
    unsigned = {t for _, t in have}
    bad = []
    for sign, tok in figures(bullet):
        if (sign and (sign, tok) not in have) or (not sign and tok not in unsigned):
            bad.append(sign + tok)
    return list(dict.fromkeys(bad))


def _overlaps(s: int, e: int, spans: list[tuple[int, int]]) -> bool:
    return any(s < b and e > a for a, b in spans)


# The stable phrases `check` writes into a reason, by the accuracy page's category (ft/quality/reasons.py reads this table;
# tests run `check` on crafted bad replies and assert each phrase still lands where it says). Regexes, case-insensitive.
# Change a message above and this table together.
REASON_PHRASES: dict[str, tuple[str, ...]] = {
    "citation": (r"no verified citation", r"quoted words not in its cited passages",
                 r"isn't in the (?:previous|new) document's changed sentences", r"a claim that something stayed the same",
                 r"a claim about the previous document with no quote from it"),
    "numbers": (r"numbers not in its cited passages",),
    "format": (r"text outside the bullets", r"no bullets \(each bullet", r"\d+ bullets, not \d+ to \d+",
               r"an unclosed quotation mark", r"repeats its own words", r"doesn't end as a sentence ends",
               r"the reply isn't the schema's JSON", r"isn't the schema's object", r"\d+ quotes, not 1 to \d+",
               r"copies a statement's words without quotation marks", r"two quotations side by side",
               r"a quotation cut in the middle of", r"a quotation that ends on "),
}


def verify_bullet(body: str, cites: list, sources: tuple[Source, ...], mapping: Counter, *,
                  changed: dict[int, list[tuple[int, int]]] | None = None,
                  full_text: dict[int, str] | None = None, quotes: bool = False, repeats: bool = False,
                  ends: bool = False) -> Bullet:
    """One bullet against our copies of the documents: each citation mapped into them (`citations.map_api_citation`),
    then the bullet kept only with a verified citation and only figures from its cited passages. `mapping` counts how
    the citations mapped. `changed`: for statement_diff_summary, each document's changed spans. `quotes`: also only
    quotations from its cited passages (`quote_problem`; statement_diff_summary from v7 on). `repeats`: and no stretch of
    its own words twice (`repeat_problem`; from v8 on). `ends`: and it ends as a sentence ends (`end_problem`; v10 on)."""
    b = Bullet(body)
    seen = set()
    passages = []
    for c in cites:
        if c.kind != "char_location" or c.document_index is None or not 0 <= c.document_index < len(sources):
            mapping["not_a_passage"] += 1
            continue
        if c.start is None or c.end is None or c.end <= c.start or not c.cited_text.strip():
            mapping["empty"] += 1                        # an empty range quotes nothing
            continue
        src = sources[c.document_index]
        doc_text = (full_text or {}).get(src.document_id, src.text if src.seg_start == 0 else None)
        if doc_text is None:
            mapping["failed"] += 1
            continue
        span = map_api_citation(doc_text, src.seg_start, c.start, c.end, c.cited_text)
        if not span.verified:
            mapping["failed"] += 1
            continue
        if changed is not None and not _overlaps(span.start, span.end, changed.get(src.document_id, [])):
            mapping["unchanged_text"] += 1               # the diff summary cites only what changed
            continue
        mapping[span.how] += 1
        key = (src.document_id, span.start, span.end)
        if key not in seen:
            seen.add(key)
            b.citations.append(Cited(src.document_id, span.start, span.end, span.how))
            passages.append(doc_text[span.start:span.end])
    if not b.citations:
        b.problem = ("no verified citation on a changed sentence" if changed is not None and cites
                     else "no verified citation")
    elif extra := unsupported_figures(body, passages):
        b.problem = "numbers not in its cited passages: " + ", ".join(extra)
    elif quotes and (q := quote_problem(body, passages)):
        b.problem = q
    elif repeats and (r := repeat_problem(body)):
        b.problem = r
    elif ends and (e := end_problem(body)):
        b.problem = e
    return b


# ---------------------------------------------------------------------------------------------- quotations (statement_diff_summary)
# #445, round 6 (Anand's decision of 12 Nov 2026, 22:36 IST, after the judge failed round 5): from prompt v7 on
# (`ai_batch.QUOTES_FROM`), a bullet's quotations come only from its own cited passages. The citation check proves each
# citation exists, not that the words a bullet quotes come from them, and the judge's rule (b) fails were exactly that:
# words quoted from the previous statement in a bullet that cited only the new one. A tightening (the card allows it):
# - Each stretch between a pair of double quotation marks (`match_form`: curly ones straightened) is found in one of the
#   bullet's cited passages, or in its passages joined in order (a quoted copy of two cited sentences, `settle_copies`),
#   case aside. Punctuation the sentence puts just inside the closing mark (".", ",", ";", ":", "!", "?") doesn't count.
#   An elision ("...", "…") or a bracketed insertion ("reinvest[ed]") splits it: the pieces are found in order in one
#   haystack. Its ends are word-bounded where they are words ("solid" isn't found in "solidly").
# - An odd number of double quotation marks fails the bullet: what is quoted can't be told.
# A bullet that fails is dropped, so the reply is retried once with a note, then rejected (the published output stays).
_QUOTED = re.compile(r'"([^"]*)"')
_ELISION = re.compile(r"\s*(?:\.\.\.|…|\[[^\]]*\])\s*")
_INNER_END = ".,;:!?"


def _quote_found(q: str, hay: list[str]) -> bool:
    q = q.strip().rstrip(_INNER_END).strip().casefold()
    pieces = [x for x in _ELISION.split(q) if _WORD.search(x)]
    if not pieces:
        return True                                      # a quoted mark or elision alone quotes no words
    body = r".*?".join(re.escape(x) for x in pieces)
    head = r"(?<!\w)" if re.match(r"\w", q) else ""
    tail = r"(?!\w)" if re.search(r"\w$", q) else ""
    t = re.compile(head + body + tail)
    return any(t.search(h) for h in hay)


# #445, round 7 (from prompt v8 on, `ai_batch.REPEATS_FROM`; a tightening): a bullet that repeats a stretch of
# REPEAT_MIN_WORDS or more of its own words fails. Round 6's retry for one target (dev's 2492) wrote its first sentence
# twice. Words as `_WORD` reads them, case aside, in `match_form`; quoted or not.
REPEAT_MIN_WORDS = 12


def repeat_problem(body: str) -> str | None:
    """Why a bullet repeats itself (see above), or None."""
    words = _WORD.findall(match_form(body).casefold())
    seen: set[tuple[str, ...]] = set()
    for i in range(len(words) - REPEAT_MIN_WORDS + 1):
        gram = tuple(words[i:i + REPEAT_MIN_WORDS])
        if gram in seen:
            return f"repeats its own words: \"{' '.join(gram)}\""
        seen.add(gram)
    return None


# #445, round 9 (from prompt v10 on, `ai_batch.ENDS_FROM`; a tightening): a bullet ends as a sentence ends: a full stop, a
# question or exclamation mark, then at most closing quotation marks or brackets. Round 8 (v9, Sonnet) left words written
# only to hold a citation after a bullet's last sentence (dev's 2510: "…per cent". Previous new").
_ENDS = re.compile("[.!?][\"'\u201d\u2019)\\]]*$")


def end_problem(body: str) -> str | None:
    """Why a bullet doesn't end as a sentence ends (see above), or None."""
    return None if _ENDS.search(body.rstrip()) else "doesn't end as a sentence ends"


def quote_problem(body: str, passages: list[str]) -> str | None:
    """Why a bullet's quotations fail (see above), or None."""
    t = match_form(body)
    if t.count('"') % 2:
        return "an unclosed quotation mark"
    hay = [match_form(h).casefold() for h in [*passages, " ".join(passages)]]
    bad = [q for q in _QUOTED.findall(t) if not _quote_found(q, hay)]
    if bad:
        return "quoted words not in its cited passages: " + ", ".join(f'"{q.strip()[:80]}"' for q in bad)
    return None


# ---------------------------------------------------------------------------------------------- copies (statement_diff_summary)
# #445 (R05-2; Anand's option 1, 12 Nov 2026). On the citations route the model's cited text blocks are often word-for-word
# copies of the sentences they cite, and `scan` joins them into the bullet: "The assessment of economic growth changed from
# "solid" to "strong". Economic activity is expanding at a solid pace. ... Economic activity is expanding at a strong pace."
# Prompts v2 and v3 didn't stop it (two dev samples), so for this task only, from prompt v4 on, before the checks:
# - A copy is a cited text block whose text is found word for word, in `match_form`, in its own cited text (one citation's,
#   or its citations' joined in order), a closing punctuation mark aside ("…in several parts of the world." from "…world,
#   especially …"), with at least COPY_MIN_WORDS words. Its list marker, the whitespace around it and a pair of
#   parentheses or quotation marks around it don't count; it is one line. A block whose pieces, joined by " / ", " ... "
#   or "; ", are each such a copy holds one copy per piece. A block with any words of the model's own isn't a copy and is
#   never touched.
# - Copies joined only by separators share one fate. They come out of the bullet's text, with the separators, when the
#   text around them reads without them. Before: the bullet's start, a sentence's end, or a word that doesn't need what
#   follows (not a colon, nor "in", "as", "from", "reads" and the like: `_NEEDS_MORE`). After: the bullet's end,
#   punctuation, or a new sentence. Copies in their own parentheses go with them ("…to "been little changed" (<copy> /
#   <copy>)."), and so does a copy in its own quotation marks ("…retained at neutral." "<copy>""). A full stop that ended
#   a removed copy stays where the sentence would otherwise have none. Their citations stay on the bullet: the page shows
#   each cited sentence through its chip.
# - Any other copy is part of the model's own sentence ("…described as "moderate" in <copy>"): it goes in quotation marks
#   (one pair per piece), or keeps its own. So does every copy of a bullet that would otherwise keep fewer than
#   OWN_MIN_WORDS words, or none outside quotation marks (no sentence of the model's own: "Revised:", or nothing).
# - A copy inside the model's quotation marks with other words is left as it is.
# - From prompt v8 on (#445, round 7; `strict`): a copy comes out only when it stands as a whole sentence of its own: the
#   text before it is the bullet's start or ends a sentence, and after it comes the bullet's end or a new sentence (a full
#   stop of the model's own may close it first). Copies in their own parentheses still go. Every other copy goes in
#   quotation marks. Why: round 6 (v7, Sonnet 5.5) wrote its cited blocks as fragments inside its own sentences ("…says the
#   Committee <copy>."), and the looser test above took 11 of them out, leaving 8 broken sentences in 6 of 10 outputs.
# Our code only removes words copied from the source (with the separators, parentheses and quotation marks that held them),
# or puts them in quotation marks; it never adds a word. The checks then run on the final text: the figures check can only
# get stricter, since a copy's figures are in its cited text.
COPY_MIN_WORDS = 5                # the judge's measure counts copied stretches from five words (ft/eval/diff_quotes.py)
OWN_MIN_WORDS = 3                 # fewer words left, or none outside quotation marks, isn't a sentence of the model's own
_OPEN, _CLOSE = "\x01", "\x02"    # around a copy between `mark_copies` and `settle_copies`; never stored
_GAP, _GAP_STOP = "\x03", "\x04"  # where a removed copy was (`_GAP_STOP`: it ended with a full stop); never stored
_COPY = re.compile("\x01([^\x01\x02]*)\x02")
_WORD = re.compile(r"[^\W_]+")
_PIECES = re.compile(r"\s*;\s+|\s+(?:/|\||\.\.\.|…)\s+")   # between the pieces of a joined copy
_SEP = r"(?:\s|\.\.\.|…|[;|/]|(?<=\s)[-–—](?=\s|$))"
_SEP_TAIL = re.compile(_SEP + "+$")
_SEP_HEAD = re.compile(r"^(?:\s|\.\.\.|…|[;|/]|[-–—](?=\s))+")
_SENTENCE_END = re.compile("(?:^|[.!?][\"'”’)\\]]*)$")
_CLOSING_QUOTE = "\"'”’"
_LAST_WORD = re.compile(r"([^\W_][\w'’-]*)$")
# A word that leaves the sentence open when the copy after it comes out ("…described as "moderate" in <copy>").
_NEEDS_MORE = frozenset({"a", "an", "the", "in", "into", "as", "from", "to", "that", "which", "with", "by", "of",
    "for", "on", "at", "and", "or", "but", "nor", "than", "like", "including", "namely", "while", "whereas", "is",
    "are", "was", "were", "be", "been", "being", "become", "becomes", "became", "read", "reads", "reading", "say",
    "says", "said", "saying", "state", "states", "stated", "stating", "note", "notes", "noted", "noting", "add",
    "adds", "adding", "replace", "replaces", "replaced", "replacing", "now", "then", "where", "when"})


def _found(text: str, hay: list[str]) -> bool:
    if len(_WORD.findall(text)) < COPY_MIN_WORDS:
        return False
    words = match_form(text).rstrip(".,;:!? ")          # a full stop the model put after a shortened copy isn't a word
    t = re.compile(r"(?<!\w)" + re.escape(words) + r"(?!\w)")
    return any(t.search(h) for h in hay)


def copy_spans(core: str, cited: list[str]) -> list[tuple[int, int]]:
    """Where `core` (a cited block's text) copies its cited text: all of it, or each of its joined pieces; else nothing."""
    hay = [match_form(h) for h in [*cited, " ".join(cited)]]
    if _found(core, hay):
        return [(0, len(core))]
    spans, pos = [], 0
    for m in [*_PIECES.finditer(core), None]:
        a, b = pos, (m.start() if m else len(core))
        if not _found(core[a:b], hay):
            return []
        spans.append((a, b))
        pos = m.end() if m else b
    return spans if len(spans) > 1 else []


_PAIRS = ((("(",), r"\)[.,;:!?]?$"), (('"', "\u201c"), r"[\"\u201d][.,;:!?]?$"))


def mark_copies(parts: list[TextPart]) -> list[TextPart]:
    """The reply's text blocks with each copy of its own cited text marked for `settle_copies` (see above)."""
    if any(c in p.text for p in parts for c in (_OPEN, _CLOSE, _GAP, _GAP_STOP)):
        return parts                                     # never: our markers would be ambiguous, so nothing is touched
    out = []
    for p in parts:
        cited = [c.cited_text for c in p.citations if c.kind == "char_location" and c.cited_text.strip()]
        lead = _LEAD.match(p.text)
        s = lead.end() if lead else 0
        e = len(p.text.rstrip())
        for opens, closes in _PAIRS * 2:                 # parentheses or quotation marks around a copy stay outside it
            if p.text[s:s + 1] in opens and (m := re.search(closes, p.text[s + 1:e])):
                s, e = s + 1, s + 1 + m.start()
                s, e = s + len(p.text[s:e]) - len(p.text[s:e].lstrip()), s + len(p.text[s:e].rstrip())
        core = p.text[s:e]
        spans = copy_spans(core, cited) if cited and core and "\n" not in core else []
        if spans:
            text, pos = p.text[:s], 0
            for a, b in spans:
                text += core[pos:a] + _OPEN + core[a:b] + _CLOSE
                pos = b
            p = TextPart(text + core[pos:] + p.text[e:], p.citations)
        out.append(p)
    return out


def _own_words(text: str) -> int:
    """The words of `text` outside double quotation marks."""
    t, n, inside = match_form(text), 0, False
    for piece in re.split('(")', t):
        if piece == '"':
            inside = not inside
        elif not inside:
            n += len(_WORD.findall(piece))
    return n


def unmark(text: str) -> str:
    return text.replace(_OPEN, "").replace(_CLOSE, "")


def _start_ok(before: str, strict: bool = False) -> bool:
    """Whether the bullet's text before a copy reads on without it (see above). `strict` (v8 on): only the bullet's start
    or a sentence's end."""
    b = _SEP_TAIL.sub("", before).rstrip()
    if not b or _SENTENCE_END.search(b):
        return True
    if strict:
        return False
    if b[-1] in _CLOSING_QUOTE:
        return True
    b = b.rstrip(",").rstrip()
    m = _LAST_WORD.search(b)
    return bool(m) and m.group(1).lower() not in _NEEDS_MORE


def _end_ok(copy: str, after: str, strict: bool = False) -> bool:
    """Whether the bullet's text after a copy reads on without it (see above). `strict` (v8 on): only the bullet's end or
    a new sentence, after the copy's own full stop or one of the model's."""
    a = _SEP_HEAD.sub("", after)
    if strict:
        ended = bool(_SENTENCE_END.search(copy.rstrip()))
        if a[:1] in (".", "!", "?") and not ended:
            a, ended = a[1:].lstrip(), True
        return not a or (ended and (a[0].isupper() or a[0] in "\"\u201c"))
    return (not a or a[0] in ".,;:!?)"
            or (bool(_SENTENCE_END.search(copy.rstrip())) and (a[0].isupper() or a[0] in "\"“")))


def _close_gaps(t: str) -> str:
    """The text with each removed copy's gap closed: its separators and its own parentheses go with it, a chain of gaps is
    one gap, and a full stop that ended the copy stays where the sentence would otherwise have none."""
    gap = f"[{_GAP}{_GAP_STOP}]"
    t = re.sub(_SEP + "*(" + gap + ")" + r"(?:\s|\.\.\.|…|[;|/]|[-–—](?=\s))*", r"\1", t)
    t = re.sub(gap + "{2,}", lambda m: m.group(0)[-1], t)
    t = re.sub(r"\(\s*" + gap + r"\s*\)", _GAP, t)      # a parenthesis's full stop never ends the sentence around it
    while (m := re.search(gap, t)) is not None:
        x, y, stop = t[:m.start()].rstrip(), t[m.end():].lstrip(), m.group(0) == _GAP_STOP
        if y and y[0] in ".!?" and _SENTENCE_END.search(x):
            t = x + y[1:]                                # the model's full stop after a removed copy: the sentence has one
        elif y and y[0] in ".,;:!?)":
            t = (x[:-1] if y[0] != "," and x.endswith(",") else x) + y
        elif not y:
            x = x.rstrip(",:;").rstrip()
            t = x + ("." if stop and not _SENTENCE_END.search(x) else "")
        else:
            if stop and not _SENTENCE_END.search(x) and (y[0].isupper() or y[0] in '"\u201c'):
                x = x.rstrip(",:;").rstrip() + "."           # the copy's full stop, before a new sentence only
            t = x + " " + y
    return t


def settle_copies(body: str, tally: Counter, strict: bool = False) -> str:
    """A bullet's final text from its marked copies (see above); `tally` counts them 'removed' and 'quoted'. `strict`: from
    prompt v8 on, only a copy that stands as a whole sentence comes out."""
    if _OPEN not in body:
        return body
    tokens: list[str] = []                               # own text and copies, alternating: own, copy, own, ..., own
    pos = 0
    for m in _COPY.finditer(body):
        tokens += [unmark(body[pos:m.start()]), m.group(1)]
        pos = m.end()
    tokens.append(unmark(body[pos:]))
    copies = range(1, len(tokens), 2)
    marks = {i: match_form("".join(tokens[:i])).count('"') for i in copies}
    quoted = {i for i in copies if marks[i] % 2 and tokens[i - 1].rstrip()[-1:] in '"\u201c'
              and tokens[i + 1].lstrip()[:1] in '"\u201d' and '"' not in match_form(tokens[i])}
    inside = {i for i in copies if i not in quoted and (marks[i] % 2 or '"' in match_form(tokens[i]))}

    def bare(i: int, side: int) -> str:
        """The own text on one side of copy `i` (-1 before, +1 after) without the quotation marks of the copies it lies
        between."""
        t = tokens[i + side]
        if (side < 0 and i in quoted) or (side > 0 and i + 2 in quoted):
            t = t.rstrip()[:-1]
        if (side < 0 and i - 2 in quoted) or (side > 0 and i in quoted):
            t = t.lstrip()[1:]
        return t

    acts: dict[int, str] = {}                            # a copy's index → 'remove' | 'quote'; else it stays as it is
    chains: list[list[int]] = []                         # copies joined by separators only (" / ", " ... ", "; ") share a fate
    for i in copies:
        if i in inside:
            continue
        if chains and chains[-1][-1] == i - 2 and not _SEP_TAIL.sub("", bare(i, -1)):
            chains[-1].append(i)
        else:
            chains.append([i])
    for chain in chains:
        first, last = chain[0], chain[-1]
        before, after = "".join(tokens[:first - 1]) + bare(first, -1), bare(last, +1)
        if not quoted & set(chain) and before.rstrip().endswith("(") and after.lstrip().startswith(")"):
            ok = True                                    # copies in their own parentheses go with them
        else:
            ok = _start_ok(before, strict) and _end_ok(tokens[last], after, strict)
        for k in chain:
            if ok:
                acts[k] = "remove"
            elif k not in quoted:
                acts[k] = "quote"                        # a copy already in its own marks stays as it is

    def build(remove: bool) -> str:
        out = list(tokens)
        for k in sorted(acts):
            if acts[k] == "remove" and remove:
                out[k] = _GAP_STOP if _SENTENCE_END.search(tokens[k].rstrip()) else _GAP
                if k in quoted:                          # its own quotation marks go with it
                    out[k - 1], out[k + 1] = out[k - 1].rstrip()[:-1], out[k + 1].lstrip()[1:]
            elif k not in quoted:
                out[k] = f'"{tokens[k]}"'
        return " ".join(_close_gaps("".join(out)).split())

    removed = sum(1 for a in acts.values() if a == "remove")
    text = build(remove=True)
    if removed and (len(_WORD.findall(text)) < OWN_MIN_WORDS or not _own_words(text)):
        text, removed = build(remove=False), 0           # no sentence of the model's own would be left: quote them all
    quoted_now = sum(1 for k, a in acts.items() if k not in quoted and (a == "quote" or not removed))
    for key, n in (("removed", removed), ("quoted", quoted_now)):
        if n:
            tally[key] += n
    return text


def check(task: str, reply: Reply, sources: tuple[Source, ...], *,
          changed: dict[int, list[tuple[int, int]]] | None = None, full_text: dict[int, str] | None = None,
          drop_copies: bool = False, check_quotes: bool = False, strict_copies: bool = False,
          check_repeats: bool = False, check_ends: bool = False) -> Checked:
    """Check a reply against our copies of the documents it was given (`sources`; `full_text` by document id when a
    source is a segment). `changed`: for statement_diff_summary, each document's changed spans. `drop_copies`: for
    statement_diff_summary from prompt v4 on (`ai_batch.COPIES_FROM`), the copies rule above; `check_quotes`: from v7 on
    (`ai_batch.QUOTES_FROM`), the quotations rule above; `strict_copies` and `check_repeats`: from v8 on
    (`ai_batch.STRICT_COPIES_FROM`, `REPEATS_FROM`), the copies rule's strict form and the repeats rule; `check_ends`:
    from v10 on (`ENDS_FROM`), the ends rule. All five are ignored for other tasks."""
    lo, hi = BULLETS[task]
    mapping: Counter = Counter()
    copies: Counter = Counter()
    parts = reply.parts()
    diff = drop_copies and task == "statement_diff_summary"
    found, stray = split_bullets(mark_copies(parts) if diff else parts)
    if diff:
        found = [(settle_copies(body, copies, strict=strict_copies), cites) for body, cites in found]
        stray = [unmark(x) for x in stray]
    quotes = check_quotes and task == "statement_diff_summary"
    repeats = check_repeats and task == "statement_diff_summary"
    ends = check_ends and task == "statement_diff_summary"
    bullets = [verify_bullet(body, cites, sources, mapping, changed=changed, full_text=full_text, quotes=quotes,
                             repeats=repeats, ends=ends)
               for body, cites in found]
    problems = []
    if stray:
        problems.append("text outside the bullets: " + " / ".join(f'"{x[:120]}"' for x in stray))
    if not bullets:
        problems.append("no bullets (each bullet is a line starting with '- ')")
    elif not lo <= len(bullets) <= hi:
        problems.append(f"{len(bullets)} bullets, not {lo} to {hi}")
    return Checked(task, bullets, problems, mapping, copies)


# ---------------------------------------------------------------------------------------------- the json route (v11 on)
_JSON_DOC = {"previous": 0, "new": 1}     # a quote's document → its index in the request's sources (document 1, document 2)


def _flat(text: str) -> tuple[str, list[int]]:
    """`text` as `match_form` reads it (whitespace runs one space, curly quotes straight, dashes one hyphen), with each
    character's index in `text`."""
    out: list[str] = []
    back: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            if not out or out[-1] == " ":
                continue
            ch = " "
        out.append(ch)
        back.append(i)
    return "".join(out).translate(_STRAIGHT), back


def _find_in_changed(text: str, quote: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The changed sentences (`spans`, in `text`) that an exact copy of `quote` lies in or overlaps; [] if none. Matched as
    `match_form` reads both, case kept; failing that, case aside (a quote that opens with a capital the sentence lacks)."""
    q = match_form(quote).strip()
    if not q:
        return []
    flat, back = _flat(text)
    found: list[tuple[int, int]] = []
    for hay, needle in ((flat, q), (flat.lower(), q.lower())):
        if len(hay) != len(flat) or len(needle) != len(q):
            continue
        at = hay.find(needle)
        while at >= 0:
            start, end = back[at], back[at + len(needle) - 1] + 1
            found += [(s, e) for s, e in spans if s < end and start < e and (s, e) not in found]
            at = hay.find(needle, at + 1)
        if found:
            break
    return sorted(found)


def check_diff_json(answer, sources: tuple[Source, ...], changed: dict[int, list[tuple[int, int]]], *,
                    version: str | None = None) -> Checked:
    """Check a v11-on statement_diff_summary reply (`answer`: the reply's JSON) against our copies of the two documents
    (`sources`: document 1, document 2) and their changed sentences (`changed`, by document id). Each bullet's quotes are
    found in its document's changed sentences, which become its citations; then the bullet gets the citations route's
    checks from v10: only figures and quotations from those sentences, no repeats, and it ends as a sentence ends. The
    same `Checked` as `check`'s, so a published output and its citations read the same on either route. `version`: the
    prompt version that asked for it; from v12 (`DIFF_STRICT_FROM`) a bullet also gets the unquoted-copy and side-by-side
    checks, from v13 (`DIFF_WHOLE_FROM`) the same-claim and cut-quotation checks, from v14 (`DIFF_SIDES_FROM`) the wider
    same-claim check and the previous-document check, and an earlier version, or none, is checked as it ran (none: as v11
    was)."""
    task, mapping = "statement_diff_summary", Counter()
    number = int(version.lstrip("v")) if version is not None else 0
    strict, whole, sides = number >= DIFF_STRICT_FROM, number >= DIFF_WHOLE_FROM, number >= DIFF_SIDES_FROM
    rows = answer.get("bullets") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        return Checked(task, [], ["the reply isn't the schema's JSON ({bullets: [...]})"], mapping)
    bullets = [_diff_json_bullet(raw, sources, changed, mapping, strict=strict, whole=whole, sides=sides) for raw in rows]
    problems = []
    lo, hi = BULLETS[task]
    if not lo <= len(bullets) <= hi:
        problems.append(f"{len(bullets)} bullets, not {lo} to {hi}")
    return Checked(task, bullets, problems, mapping)


def _diff_json_bullet(raw, sources: tuple[Source, ...], changed: dict[int, list[tuple[int, int]]], mapping: Counter, *,
                      strict: bool = False, whole: bool = False, sides: bool = False) -> Bullet:
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not isinstance(raw.get("quotes"), list):
        b = Bullet("")
        b.problem = "isn't the schema's object"
        return b
    text = _LEAD.sub("", " ".join(raw["text"].split()), count=1)
    b = Bullet(text)
    quotes = raw["quotes"]
    if not text or not all(isinstance(q, dict) and q.get("document") in _JSON_DOC and isinstance(q.get("text"), str)
                           for q in quotes):
        b.problem = "isn't the schema's object"
        return b
    if not 1 <= len(quotes) <= DIFF_MAX_QUOTES:
        b.problem = f"{len(quotes)} quotes, not 1 to {DIFF_MAX_QUOTES}"
        return b
    passages: list[str] = []
    for q in quotes:
        src = sources[_JSON_DOC[q["document"]]]
        hits = _find_in_changed(src.text, q["text"], changed.get(src.document_id, []))
        mapping["search" if hits else "failed"] += 1
        if not hits:
            short = " ".join(q["text"].split())[:80]
            b.problem = f"a quote (\"{short}\") isn't in the {q['document']} document's changed sentences"
            return b
        for s, e in hits:
            cited = Cited(src.document_id, s, e, "search")
            if cited not in b.citations:
                b.citations.append(cited)
                passages.append(src.text[s:e])
    if extra := unsupported_figures(text, passages):
        b.problem = "numbers not in its cited passages: " + ", ".join(extra)
    else:
        b.problem = quote_problem(text, passages) or repeat_problem(text) or end_problem(text)
    if b.problem is None and strict:
        b.problem = side_by_side_problem(text) or unquoted_copy_problem(text, [src.text for src in sources])
    if b.problem is None and whole:
        b.problem = same_problem(text, again=sides) or cut_quote_problem(text, passages)
    if b.problem is None and sides and not any(q["document"] == "previous" for q in quotes):
        b.problem = previous_problem(text)
    return b


_NAME_WORDS = {"and", "dr", "prof", "shri", "smt", "mr", "ms"}     # with capitalised words and initials: a run of names


def unquoted_copy_problem(text: str, documents: list[str], min_words: int = UNQUOTED_MIN_WORDS) -> str | None:
    """Why a bullet copies a statement's words outside quotation marks (see `DIFF_STRICT_FROM`), or None. Words as `_WORD`
    reads them in `match_form`, case aside; a quotation splits the text, so no run crosses one."""
    t = match_form(text)
    if t.count('"') % 2:
        return None                                       # `quote_problem` has already failed it
    grams: set[tuple[str, ...]] = set()
    for d in documents:
        words = _WORD.findall(match_form(d).casefold())
        grams.update(tuple(words[i:i + min_words]) for i in range(len(words) - min_words + 1))
    for piece in _QUOTED.split(t)[::2]:                    # the text outside quotation marks
        raw = _WORD.findall(piece)
        low = [w.casefold() for w in raw]
        for i in range(len(low) - min_words + 1):
            run = range(i, i + min_words)
            plain = sum(not (raw[j][0].isupper() or low[j] in _NAME_WORDS) for j in run)
            if tuple(low[i:i + min_words]) in grams and plain >= COPY_PLAIN_WORDS:
                return f"copies a statement's words without quotation marks: \"{' '.join(raw[i:i + min_words])}\""
    return None


def side_by_side_problem(text: str) -> str | None:
    """Why a bullet sets two quotations side by side (nothing of its own but spaces or punctuation between them), or None."""
    t = match_form(text)
    if t.count('"') % 2:
        return None
    marks = [m.span() for m in re.finditer(r'"[^"]*"', t)]
    for (s1, e1), (s2, e2) in pairwise(marks):
        if not _WORD.search(t[e1:s2]):
            return f"two quotations side by side: {t[s1:e1][:60]} {t[s2:e2][:60]}"
    return None


# `same_problem` (v13 on): the bullet's own words saying that something stayed the same. Matched outside quotation marks, in
# `match_form`, case aside; a document's own words in quotation marks ("unchanged at 5.50 per cent") never count, and
# neither does a decision described in the model's words ("moved from holding the rate", "changed … to no change").
_SAME = re.compile(
    r"(?:\bnot|n't)\s+(?:among|one\s+of|part\s+of)\s+the\s+changes\b"
    r"|\b(?:is|are|was|were|remains?|remained|stays?|stayed)\s+(?:still\s+)?(?:unchanged|the\s+same|kept|retained)\b"
    r"|\b(?:remains?|remained|stays?|stayed)\s+(?:still\s+)?at\b"
    r"|\b(?:did|does|do|has|have|had)\s+not\s+change\b|\b(?:didn't|doesn't|don't|hasn't|haven't|hadn't)\s+change\b"
    r"|\b(?:is|are|was|were)\s+not\s+changed\b|\bno\s+change\s+(?:to|in)\s+(?:the\s+)?wording\b",
    re.IGNORECASE)
# From v14 (`DIFF_SIDES_FROM`) `same_problem` also matches these: a claim that something happened again or as before.
_AGAIN = re.compile(r"\bagain\b|\bas\s+(?:before|previously|it\s+did\s+before)\b|\bas\s+in\s+the\s+(?:previous|earlier)\b",
                    re.IGNORECASE)
# `previous_problem` (v14 on): the bullet's own words describing the previous document. "before" counts only where it ends a
# clause ("… to 6.25 per cent" before;), not as a conjunction ("… before moderating").
_ABOUT_PREVIOUS = re.compile(
    r"\b(?:previous(?:ly)?|earlier|formerly|had\s+been|used\s+to|no\s+longer|instead\s+of|rather\s+than"
    r"|replac(?:es|ed|ing)|removed|dropped|deleted|omitted"
    r"|(?:moved|changed|went|shifted|switched|fell|rose|lowered|raised|revised|cut|increased|reduced|narrowed|widened)"
    r"\s+(?:\w+\s+){0,2}?from)\b"
    r"|\bbefore(?=\s*(?:[.;,:)]|$))",
    re.IGNORECASE)
# `cut_quote_problem` (v13 on): a quotation ending on one of these words leaves its phrase open.
_OPEN_END = frozenset({"a", "an", "the", "of", "to", "in", "into", "for", "by", "with", "on", "at", "from", "as", "than",
                       "and", "or", "but", "nor", "that", "which", "its", "their", "is", "are", "was", "were", "be", "been",
                       "will", "would", "shall", "should", "can", "could", "may", "might", "has", "have", "had"})
_STARTS_SENTENCE = re.compile("(?:^|[.!?][\"'”’)\\]]*\\s)\\s*$")


def _outside_quotes(text: str) -> list[str] | None:
    """A bullet's own words: its text outside quotation marks, in `match_form`, piece by piece; None if its marks don't pair
    (`quote_problem` has already failed it)."""
    t = match_form(text)
    return None if t.count('"') % 2 else _QUOTED.split(t)[::2]


def same_problem(text: str, *, again: bool = False) -> str | None:
    """Why a bullet's own words say that something stayed the same (see `DIFF_WHOLE_FROM`), or None. With `again` (v14 on,
    `DIFF_SIDES_FROM`), "again" and "as before" count too."""
    patterns = (_SAME, _AGAIN) if again else (_SAME,)
    for piece in _outside_quotes(text) or []:
        for pattern in patterns:
            if m := pattern.search(piece):
                return (f"a claim that something stayed the same, which no quote from a changed sentence can show: "
                        f'"{m.group(0)}"')
    return None


def previous_problem(text: str) -> str | None:
    """Why a bullet's own words describe the previous document, for a bullet with no quote from it (see `DIFF_SIDES_FROM`),
    or None."""
    for piece in _outside_quotes(text) or []:
        if m := _ABOUT_PREVIOUS.search(piece):
            return f'a claim about the previous document with no quote from it: "{m.group(0)}"'
    return None


def cut_quote_problem(text: str, passages: list[str]) -> str | None:
    """Why a bullet ends a quotation in the middle of a phrase (see `DIFF_WHOLE_FROM`), or None. Only a quotation the bullet
    doesn't go on from: after its closing mark comes punctuation or the bullet's end, not a word. It is found in the cited
    passages (each a whole changed sentence) as `quote_problem` finds it, case aside; it fails only when every place it is
    found is cut: the passage goes on with a word, and the quotation either starts where the passage's sentence starts or
    ends on a word such as "the" or "to" (`_OPEN_END`)."""
    t = match_form(text)
    if t.count('"') % 2:
        return None
    hays = [match_form(p) for p in passages]
    for m in re.finditer(r'"([^"]*)"', t):
        if _WORD.match(t[m.end():].lstrip()[:1] or " "):
            continue                                       # the bullet goes on from it in its own words
        q = m.group(1).strip().rstrip(_INNER_END).strip()
        if not _WORD.search(q):
            continue
        last = _WORD.findall(q)[-1].casefold()
        head = r"(?<!\w)" if re.match(r"\w", q) else ""
        tail = r"(?!\w)" if re.search(r"\w$", q) else ""
        cut = []
        for h in hays:
            same_length = len(h.lower()) == len(h) and len(q.lower()) == len(q)
            hay, needle = (h.lower(), q.lower()) if same_length else (h, q)
            for x in re.finditer(head + re.escape(needle) + tail, hay):
                rest = h[x.end():].lstrip()
                starts = bool(_STARTS_SENTENCE.search(h[:x.start()]))
                cut.append(bool(_WORD.match(rest[:1] or " ")) and (starts or last in _OPEN_END))
        if cut and all(cut):
            shown = q if len(q) <= 80 else "…" + q[-79:]
            if last in _OPEN_END:
                return f'a quotation that ends on "{last}", in the middle of a phrase: "{shown}"'
            return f'a quotation cut in the middle of a sentence it starts: "{shown}" stops before its end'
    return None


def retry_note(c: Checked) -> str:
    """What the retry is told about the first reply (the bullets it dropped, the count)."""
    lines = []
    for i, b in enumerate(c.bullets):
        if b.problem:
            lines.append(f'bullet {i + 1} ("{b.text[:160]}") had {b.problem}')
    return "; ".join(c.problems + lines) + "."


def content(c: Checked, *, attempt: int, reply: Reply) -> tuple[dict, str, list[tuple[str, Cited]]]:
    """(core.ai_output.content, .body, the citations as (claim_path, span)) for a published or rejected output.
    A published output holds only verified bullets (it's published only when none was dropped); a rejected one also
    lists what was dropped and why, for the record (rejected outputs are never visible)."""
    kept = c.kept
    cites: list[tuple[str, Cited]] = []
    bullets = []
    for i, b in enumerate(kept):
        nos = []
        for x in b.citations:
            cites.append((f"/bullets/{i}", x))
            nos.append(len(cites))
        bullets.append({"text": b.text, "citations": nos, **({"list": b.section} if b.section else {})})
    doc = {"bullets": bullets, "attempt": attempt, "stop_reason": reply.stop_reason,
           "citation_mapping": dict(sorted(c.mapping.items()))}
    if +c.copies:
        doc["copies"] = dict(sorted((+c.copies).items()))  # the diff summary's copied blocks our code removed or quoted (#445)
    if not c.ok:
        doc["problems"] = c.reasons()
        doc["dropped"] = [{"text": b.text, "problem": b.problem} for b in c.dropped]
    return doc, "\n".join(b.text for b in kept), cites


# ---------------------------------------------------------------------------------------------- copied text (call summaries)
# Used by ft/ai/call_summary.py only (2B-11c, #301): module 1's checks above never call these, so `doc_summary` and
# `statement_diff_summary` are checked exactly as before. Matching is character for character after `match_form`: whitespace
# collapsed, curly quotes straight, dashes one hyphen, case kept. Every mapped character is one character, so an index in the
# matched form of a text whose whitespace is already single is an index in the text itself.
_STRAIGHT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
                           "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"'}) | _DASHES


def match_form(text: str) -> str:
    """`text` as it is compared for copying (see above)."""
    return " ".join(text.translate(_STRAIGHT).split())


def copied_runs(text: str, haystack: str, longer_than: int) -> list[tuple[int, int]]:
    """The stretches of `text` that are copied: every run of more than `longer_than` characters found verbatim in `haystack`
    (both in `match_form`), as (start, end) with overlapping or touching runs merged, in order."""
    n, j, found = len(text), 0, []
    for i in range(n):
        if n - i <= longer_than:
            break
        j = max(j, i)
        if j - i <= longer_than and text[i:i + longer_than + 1] not in haystack:
            continue                                   # no run longer than the limit starts here
        while j < n and text[i:j + 1] in haystack:     # j only moves right: a run from i + 1 ends no sooner than one from i
            j += 1
        if j - i > longer_than:
            found.append((i, j))
    merged: list[list[int]] = []
    for a, b in found:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def longest_copied(text: str, haystack: str) -> int:
    """The length of the longest stretch of `text` found verbatim in `haystack` (both in `match_form`)."""
    best = 0
    for i in range(len(text)):
        if len(text) - i <= best:
            break
        while i + best < len(text) and text[i:i + best + 1] in haystack:
            best += 1
    return best
