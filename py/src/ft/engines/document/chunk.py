"""The chunker (Architecture 03 §1.5; Spec 01 §2, "Chunks"). Cuts a document's canonical text into contiguous spans of
about 1,800 characters that together reproduce it exactly; the database checks that again at commit
(`core.check_document_chunks`), so a chunk set that doesn't is never stored.

- Chunks break between blocks (paragraphs). A block longer than `MAX_CHARS` breaks between sentences, a sentence
  longer than that between words, and a run with no spaces at `MAX_CHARS`.
- A heading always starts a chunk (headings in a row stay together, and stay with the text after them), and so does a
  change of speaker.
- A chunk's last piece is folded into the one before when it's shorter than `MIN_TAIL` and the two fit in `MAX_CHARS`.
- `section` is the heading path at the chunk's start under an optional root ("Minutes > Statement by Dr. Nagesh
  Kumar"); `speaker` comes from "Statement by …" headings (minutes) or speaker labels (transcripts: the Fed's
  "CHAIR POWELL.", the RBI's "Shaktikanta Das:").
- The whitespace between two blocks belongs to the chunk before it, so every chunk starts on text.
Starting values, like the differ's: checked on the backfill (Architecture 03 §1.5: about 400 tokens a chunk).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ft.engines.document.text import Block, Extracted, rbi_transcript_speaker, sentence_spans, transcript_speaker

TARGET = 1800
MAX_CHARS = 2400
MIN_TAIL = 450

_STATEMENT_BY = re.compile(r"^statement by\s+(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Chunk:
    chunk_no: int
    char_start: int
    char_end: int
    section: str | None
    speaker: str | None
    body: str


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    brk: bool                  # a chunk must start here (a heading, a new speaker)
    heading: bool
    section: str | None
    speaker: str | None


def minutes_speaker(heading: str) -> str | None:
    """The member a minutes heading introduces ("Statement by Dr. Nagesh Kumar" -> "Dr. Nagesh Kumar")."""
    m = _STATEMENT_BY.match(" ".join(heading.split()))
    return m.group(1) if m else None


def _plain_statement_heading(body: str) -> bool:
    """A paragraph that is only "Statement by <member>": the RBI's minutes set these in plain type, so the extractor
    reads them as paragraphs (checked on the 49 minutes saved 23 Oct 2026). One short line, no closing stop."""
    one = " ".join(body.split())
    return ("\n" not in body.strip() and len(one.split()) <= 12 and not one.endswith((".", ":", ";", ","))
            and minutes_speaker(one) is not None)


def _pieces(text: str, start: int, end: int, target: int, max_chars: int) -> list[tuple[int, int]]:
    """A block as units no longer than max_chars: whole, else its sentences, else words, else hard cuts."""
    if end - start <= max_chars:
        return [(start, end)]
    out = []
    for s, e in sentence_spans(text, start, end):
        while e - s > max_chars:
            cut = text.rfind(" ", s + 1, s + target + 1)
            cut = s + max_chars if cut <= s else cut
            out.append((s, cut))
            s = cut
            while s < e and text[s].isspace():
                s += 1
        if s < e:
            out.append((s, e))
    return out


def _units(doc: Extracted, root: str | None, speakers: str | None, target: int, max_chars: int,
           marks: Sequence[tuple[bool, str | None, str | None]] | None = None) -> list[_Unit]:
    text, units = doc.text, []
    stack: list[tuple[int, str]] = []
    speaker, speaker_level, after_heading = None, None, False

    def path() -> str | None:
        p = " > ".join(h for _, h in stack)
        return (f"{root} > {p}" if p else root) if root else (p or None)

    for bi, b in enumerate(doc.blocks):
        body = text[b.start:b.end]
        if speakers == "call":
            starts, who, where = marks[bi]
            for k, (s, e) in enumerate(_pieces(text, b.start, b.end, target, max_chars)):
                units.append(_Unit(s, e, starts and k == 0, False, where, who))
            continue
        if speakers == "minutes" and b.kind != "heading" and _plain_statement_heading(body):
            b = Block(b.start, b.end, "heading", 7)             # the RBI's minutes: "Statement by …" in plain type
        if b.kind == "heading":
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            stack.append((b.level, " ".join(body.split())))
            if speakers == "minutes":
                if name := minutes_speaker(body):
                    speaker, speaker_level = name, b.level
                elif speaker_level is not None and b.level <= speaker_level:
                    speaker, speaker_level = None, None
            units.append(_Unit(b.start, b.end, not after_heading, True, path(), speaker))
            after_heading = True
            continue
        changed = False
        label = {"transcript": transcript_speaker, "rbi_transcript": rbi_transcript_speaker}.get(speakers)
        if label is not None and (name := label(body)) and name != speaker:
            speaker, changed = name, True
        for k, (s, e) in enumerate(_pieces(text, b.start, b.end, target, max_chars)):
            units.append(_Unit(s, e, changed and k == 0 and not after_heading, False, path(), speaker))
        after_heading = False
    return units


def chunk_text(doc: Extracted, *, root: str | None = None, speakers: str | None = None, target: int = TARGET,
               max_chars: int = MAX_CHARS, min_tail: int = MIN_TAIL,
               marks: Sequence[tuple[bool, str | None, str | None]] | None = None) -> list[Chunk]:
    """speakers: None, "minutes" (from "Statement by …" headings), "transcript" (the Fed's speaker labels: "CHAIR
    POWELL.") or "rbi_transcript" (the RBI's: "Shaktikanta Das:") or "call" (an earnings call: `marks`, one
    (starts a turn, speaker, section) per block of `doc`; every chunk then lies inside one turn)."""
    if speakers not in (None, "minutes", "transcript", "rbi_transcript", "call"):
        raise ValueError(f"speakers must be None, 'minutes', 'transcript', 'rbi_transcript' or 'call', not {speakers!r}")
    if speakers == "call" and (marks is None or len(marks) != len(doc.blocks)):
        raise ValueError("speakers='call' needs one mark per block of the text")
    text = doc.text
    if not text:
        return []
    groups: list[list[_Unit]] = []
    for u in _units(doc, root, speakers, target, max_chars, marks):
        cur = groups[-1] if groups else None
        if cur is None or u.brk or (not all(x.heading for x in cur) and u.end - cur[0].start > target):
            groups.append([u])
        else:
            cur.append(u)
    merged: list[list[_Unit]] = []
    for i, g in enumerate(groups):
        last_of_segment = i + 1 == len(groups) or groups[i + 1][0].brk
        if (merged and not g[0].brk and last_of_segment and g[-1].end - g[0].start < min_tail
                and g[-1].end - merged[-1][0].start <= max_chars):
            merged[-1].extend(g)
        else:
            merged.append(list(g))
    chunks = []
    for i, g in enumerate(merged):
        start = 0 if i == 0 else g[0].start
        end = merged[i + 1][0].start if i + 1 < len(merged) else len(text)
        lead = next((u for u in g if not u.heading), g[-1])
        chunks.append(Chunk(i, start, end, lead.section, lead.speaker, text[start:end]))
    check_chunks(text, chunks)
    return chunks


def check_chunks(text: str, chunks: list[Chunk]) -> None:
    """The rule the database enforces at commit, checked here first: contiguous chunks from 0 that reproduce the text
    exactly, each body the text between its offsets."""
    pos = 0
    for i, c in enumerate(chunks):
        if c.chunk_no != i or c.char_start != pos or c.char_end <= c.char_start or c.body != text[c.char_start:c.char_end]:
            raise ValueError(f"chunk {i} doesn't continue the text at offset {pos}")
        pos = c.char_end
    if pos != len(text) or (text and not chunks):
        raise ValueError("the chunks don't cover the whole text")
