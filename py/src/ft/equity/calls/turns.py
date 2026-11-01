"""Turns, sections and speaker roles of an earnings-call transcript (Spec 02B §5.3; job 2B-09). Pure functions: the
canonical text and the speaker label that opens each of its blocks (`text.pdf_text_pages(..., labels=)`, kept on
`pdfs.CallText`) in; turns, a role for every speaker and the marks the chunker needs out. No AI.

- **Turns:** one per speaker label, from the label's block to the next label's block (the last to the end of the text).
  Text before the first label (a cover letter, a title, a participants list) belongs to no turn and has no speaker.
- **Sections:** `opening` from the first turn that isn't the moderator's to the moderator's hand-over to questions;
  `qa` from that turn to the moderator's hand-back for closing comments (the hand-back turn is still `qa`); `closing`
  after it. The moderator's turns before the first non-moderator turn are `opening` too (the column cannot be null).
  A call with no hand-over found is all `qa`.
- **Roles:** `moderator` for the labels "Moderator" and "Operator"; `analyst` for a speaker the moderator introduces as
  asking ("the next question is from <name> from <firm>") or a participants list puts among analysts; `management` for
  a speaker the participants list puts among management, who speaks in the opening, or whose label carries an executive
  title; otherwise `unknown`. A speaker who matches two roles is `unknown`.
- **Unknown share:** unknown-role words over all turn words. Above 10% the call is role-held.

Decided by Claude (standing delegation, 1 Nov 2026): words before the first label are in no turn, so they count in
neither the turn words nor the unknown share; `earnings_call.words` stays the figure 2B-07 wrote (set here only when
null). A participants list is read from the text before the first label, under a header ("Management:", "Analysts:" …),
and only for names that carry an honorific, which is how the Indian filings write them.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ft.engines.document.text import Extracted, words

ROLE_HOLD_SHARE = 0.10
SECTION_NAMES = {"opening": "Opening remarks", "qa": "Q&A", "closing": "Closing remarks"}
MODERATORS = ("moderator", "operator")

_HONORIFIC = re.compile(r"^(?:mr|ms|mrs|dr|shri|smt|sh|sri|prof|mr\s*&\s*mrs)\b\.?\s*", re.IGNORECASE)
_HANDOVER = re.compile(
    r"first\s+question\s+(?:is|comes|will\s+be|today\s+is)?\s*from|"
    r"now\s+(?:begin|start|commence|open)\s+(?:with\s+)?(?:the\s+|our\s+)?(?:interactive\s+|live\s+)?"
    r"(?:question|q\s*&\s*a|q\s+and\s+a)|"
    r"(?:we|i)\s+(?:will|shall)\s+(?:now\s+)?(?:begin|start|open)\s+(?:with\s+)?(?:the\s+)?(?:question|q\s*&\s*a|q\s+and\s+a)|"
    r"now\s+(?:take|open|go\s+to)\s+(?:the\s+)?(?:floor|line|lines|questions)", re.IGNORECASE)
_HANDBACK = re.compile(r"closing\s+(?:comments|remarks|statement|observations)", re.IGNORECASE)
_NAME_TOKEN = r"[A-Z][A-Za-z.'’\-]*"
_INTRO = re.compile(
    rf"(?i:\bquestions?\s+(?:is\s+|comes\s+|today\s+is\s+|will\s+be\s+)?from\s+"
    rf"(?:the\s+line\s+of\s+|the\s+line\s+|line\s+of\s+|our\s+(?:online\s+)?participants?\s+|the\s+participants?\s+)?)"
    rf"(?P<name>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,4}})")
_MODERATOR_LABEL = re.compile(r"[-\u2013\u2014]\s*(?:the\s+)?(?:moderator|operator)\b", re.IGNORECASE)
_STOP_TOKENS = {"from", "of", "with", "at", "the", "line", "is", "and", "please", "go", "ahead", "over", "sir", "madam"}
_TITLE = re.compile(
    r"\b(?:ceo|cfo|coo|cto|cio|cmo|chro|md|c\.e\.o|c\.f\.o|chief|managing\s+director|executive\s+director|whole[\s-]*time"
    r"|chairman|chairperson|chairwoman|president|vice[\s-]+president|vice\s+chairman|company\s+secretary|investor\s+relations"
    r"|head\s+of|svp|evp|avp|general\s+manager|group\s+head|business\s+head|senior\s+vice)\b", re.IGNORECASE)
_HEADER = re.compile(
    r"(?P<h>MANAGEMENT(?:\s+TEAM)?|COMPANY\s+(?:REPRESENTATIVES?|PARTICIPANTS?|MANAGEMENT)|CORPORATE\s+PARTICIPANTS?|"
    r"ANALYSTS?(?:\s+PARTICIPANTS?)?|EXTERNAL\s+PARTICIPANTS?|MODERATOR|CONFERENCE\s+CALL\s+PARTICIPANTS?|PARTICIPANTS?)"
    r"\s*[:\-–—]", re.IGNORECASE)
_LISTED = re.compile(
    r"(?:Mr|Ms|Mrs|Dr|Shri|Smt|Sh|Sri|Prof)\.?\s+(?P<name>[A-Za-z.'’\-]+(?:\s+[A-Za-z.'’\-]+){0,3}?)"
    r"(?=\s*(?:[-–—,|:(;]|$|\s(?:Mr|Ms|Mrs|Dr|Shri|Smt|Sh|Sri|Prof)\b\.?))", re.IGNORECASE)
LIST_WINDOW = 1500


def name_key(label: str) -> str:
    """A speaker's identity: lower case, no honorific, no dots ("Mr. S. Kumar" -> "s kumar")."""
    s = _HONORIFIC.sub("", " ".join(label.split()))
    return " ".join(re.sub(r"[.’']", " ", s).lower().replace("-", " ").split())


def same_person(a: str, b: str) -> bool:
    """Two name keys naming one person: the same tokens, or one's tokens inside the other's with at least two tokens."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(small) >= 2 and all(t in big for t in small)


@dataclass(frozen=True)
class Turn:
    turn_no: int
    char_start: int
    char_end: int
    speaker: str            # the label as written
    key: str                # name_key of it
    block_start: int        # index of the first block of the turn
    block_end: int          # one past the last
    words: int
    section: str = "qa"
    role: str = "unknown"


@dataclass
class Analysis:
    turns: list[Turn]
    marks: list[tuple[bool, str | None, str | None]]    # per block: (starts a turn, speaker, chunk section)
    handover: bool                                      # a hand-over to questions was found
    handback: bool                                      # a hand-back for closing comments was found
    total_words: int = 0
    unknown_words: int = 0
    unknown_speakers: list[str] = field(default_factory=list)
    both_roles: list[str] = field(default_factory=list)

    @property
    def unknown_share(self) -> float:
        return 1.0 if self.total_words == 0 else self.unknown_words / self.total_words

    @property
    def role_held(self) -> bool:
        return self.unknown_share > ROLE_HOLD_SHARE


def display_label(paragraph: str, key: str) -> str:
    """The label as written at the start of a paragraph: the first run of text that reads as `key` (the honorific
    dropped), falling back to the key in title case."""
    head = paragraph[:160]
    low = head.lower()
    for want in (key, key.replace(" ", ".")):
        i = low.find(want)
        if 0 <= i <= 40:
            return head[i:i + len(want)]
    toks = key.split()
    first = low.find(toks[0]) if toks else -1
    if 0 <= first <= 40:                                   # initials spelled with dots ("S.K. Kumar")
        m = re.match(r"[A-Za-z.'’\- ]{1,60}?(?=\s*[:\n\-–—(]|$)", head[first:])
        if m and name_key(m.group(0)) == key:
            return m.group(0).strip()
    return key.title()


def split_turns(ex: Extracted, labels: list) -> list[Turn]:
    """Turns from the per-block speaker labels (one label per block, or None); the text before the first label is none's."""
    starts = [i for i, lab in enumerate(labels) if lab is not None]
    out = []
    for n, bi in enumerate(starts):
        nxt = starts[n + 1] if n + 1 < len(starts) else len(ex.blocks)
        b0 = ex.blocks[bi]
        end = ex.blocks[nxt].start if nxt < len(ex.blocks) else len(ex.text)
        body = ex.text[b0.start:b0.end]
        key = name_key(labels[bi])
        speaker = display_label(body, key)
        colon = body[:120].find(":")
        if key not in MODERATORS and colon > 0 and _MODERATOR_LABEL.search(body[:colon]):
            key = "moderator"                      # "Mr. Anil - Moderator, Company:" is the moderator's label
        out.append(Turn(n, b0.start, end, speaker, key, bi, nxt, len(words(ex.text[b0.start:end]))))
    return _drop_spurious(out)


def _drop_spurious(turns: list[Turn]) -> list[Turn]:
    """Label-like lines in a cover letter before the transcript ("Encl.: As above", "Membership No.: …") read as speakers
    who speak once. The call starts at the first turn of a moderator or of a speaker who has more than one turn; what
    comes before is the preamble (no turn). A call with no such turn keeps all of its turns."""
    count = Counter(t.key for t in turns)
    first = next((i for i, t in enumerate(turns) if t.key in MODERATORS or count[t.key] > 1), None)
    if not first:
        return turns
    return [Turn(n, t.char_start, t.char_end, t.speaker, t.key, t.block_start, t.block_end, t.words)
            for n, t in enumerate(turns[first:])]


def participants(preamble: str) -> tuple[list[str], list[str]]:
    """(management names, analyst names) as name keys, from the lists under a header in the text before the first label.
    Only names that carry an honorific are read."""
    heads = list(_HEADER.finditer(preamble))
    mgmt, analysts = [], []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(preamble)
        seg = preamble[h.end():min(end, h.end() + LIST_WINDOW)]
        kind = re.sub(r"\s+", " ", h.group("h").lower())
        if kind.startswith("moderator"):
            continue
        names = [name_key(m.group("name")) for m in _LISTED.finditer(seg)]
        if kind.startswith(("analyst", "external")):
            analysts += names
        elif kind.startswith(("management", "company", "corporate")):
            mgmt += names
        # a bare "Participants:" list says nothing about roles
    return mgmt, analysts


def introduced_analysts(moderator_texts: list[str]) -> list[str]:
    """Name keys the moderator introduces as asking ("the next question is from <name> from <firm>")."""
    out = []
    for text in moderator_texts:
        for m in _INTRO.finditer(text):
            toks = m.group("name").split()
            while toks and toks[-1].lower().strip(".") in _STOP_TOKENS:
                toks.pop()
            while toks and toks[0].lower().strip(".") in _STOP_TOKENS:
                toks.pop(0)
            if toks:
                out.append(name_key(" ".join(toks)))
    return out


def _title_in_label(turn_text: str) -> bool:
    """An executive title in a label written "Name - Title:" (only the part before a colon within 100 characters)."""
    head = turn_text[:100]
    colon = head.find(":")
    return colon > 0 and _TITLE.search(head[:colon]) is not None


def analyse(ex: Extracted, labels: list) -> Analysis:
    """Turns, sections, roles and the chunker's marks for a call's canonical text and block labels."""
    if len(labels) != len(ex.blocks):
        raise ValueError("one speaker label (or None) per block of the text")
    turns = split_turns(ex, labels)
    text = ex.text
    is_mod = [t.key in MODERATORS for t in turns]
    # sections ----------------------------------------------------------------------------------------------------
    qa_start = None
    for i in range(1, len(turns)):                 # the moderator's first turn is the welcome, which may preview the Q&A
        if is_mod[i] and _HANDOVER.search(text[turns[i].char_start:turns[i].char_end]):
            qa_start = i
            break
    closing_start = None
    if qa_start is not None:
        for i in range(qa_start, len(turns)):
            if is_mod[i] and _HANDBACK.search(text[turns[i].char_start:turns[i].char_end]) and i + 1 < len(turns):
                closing_start = i + 1
                break
    section = []
    for i in range(len(turns)):
        if qa_start is None:
            section.append("qa")
        elif i < qa_start:
            section.append("opening")
        elif closing_start is not None and i >= closing_start:
            section.append("closing")
        else:
            section.append("qa")
    # roles -------------------------------------------------------------------------------------------------------
    pre_end = turns[0].char_start if turns else len(text)
    listed_mgmt, listed_analysts = participants(text[:pre_end])
    intro = introduced_analysts([text[t.char_start:t.char_end] for t, m in zip(turns, is_mod, strict=True) if m])
    by_key: dict[str, list[int]] = {}
    for i, t in enumerate(turns):
        by_key.setdefault(t.key, []).append(i)
    for n in [n for n in intro if " " not in n]:          # "the first question is from Binay": a first name that names
        who = [k for k in by_key if k not in MODERATORS and k.split()[0] == n]   # exactly one speaker
        if len(who) == 1:
            intro.append(who[0])
    role_of: dict[str, str] = {}
    both = []
    for key, idxs in by_key.items():
        if key in MODERATORS:
            role_of[key] = "moderator"
            continue
        is_analyst = any(same_person(key, n) for n in intro + listed_analysts)
        is_mgmt = (any(same_person(key, n) for n in listed_mgmt)
                   or (qa_start is not None and any(section[i] == "opening" for i in idxs))
                   or any(_title_in_label(text[turns[i].char_start:turns[i].char_end]) for i in idxs))
        if is_analyst and is_mgmt:
            both.append(key)
        role_of[key] = "analyst" if is_analyst and not is_mgmt else "management" if is_mgmt and not is_analyst else "unknown"
    final = [Turn(t.turn_no, t.char_start, t.char_end, t.speaker, t.key, t.block_start, t.block_end, t.words,
                  section[i], role_of[t.key]) for i, t in enumerate(turns)]
    marks: list[tuple[bool, str | None, str | None]] = [(False, None, None)] * len(ex.blocks)
    marks = list(marks)
    for t in final:
        for bi in range(t.block_start, t.block_end):
            marks[bi] = (bi == t.block_start, t.speaker, SECTION_NAMES[t.section])
    total = sum(t.words for t in final)
    unknown = sum(t.words for t in final if t.role == "unknown")
    names = sorted({t.speaker for t in final if t.role == "unknown"})
    check_turns(text, final)
    return Analysis(final, marks, qa_start is not None, closing_start is not None, total, unknown, names, both)


def check_turns(text: str, turns: list[Turn]) -> None:
    """(B) turns are contiguous, don't overlap and lie inside the text, from the first label to the end of the text."""
    pos = turns[0].char_start if turns else 0
    for i, t in enumerate(turns):
        if t.turn_no != i or t.char_start != pos or t.char_end <= t.char_start or t.char_end > len(text):
            raise ValueError(f"turn {i} doesn't continue the text at offset {pos}")
        pos = t.char_end
    if turns and pos != len(text):
        raise ValueError("the turns don't reach the end of the text")
