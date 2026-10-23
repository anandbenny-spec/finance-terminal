"""Module 1's cited summaries (Spec 01 §3.2-§3.3; Architecture 03 §1.2, §1.4): `doc_summary` (3-6 bullets on one
document) and `statement_diff_summary` (2-5 bullets on what changed from the previous statement or resolution).
Both use the citations route: the documents go as plain-text document blocks, and the reply's text blocks carry the
passages they cite.

**Checking a reply** (`check`), before anything is stored:
- Bullets are the reply's lines that start with "- " (or another list marker); text outside them (a preamble) is
  ignored and never stored. A bullet's citations are those of the reply's text blocks that overlap it.
- Each citation is mapped into our copy of the document (`citations.map_api_citation`): the API's offsets if our text
  there equals the cited text, else the cited text found exactly once; otherwise it isn't evidence.
- A bullet is kept only with at least one verified citation, and only if every number it writes is a number in its
  cited passages ("no number that isn't in a cited passage", Spec 01 §3.3). For `statement_diff_summary` a citation
  counts only if it falls on a sentence the redline marks changed, added or removed: so additions cite the new text
  and removals the old, as only the new text holds an addition and only the old a removal.
- The bullet count must be in range (3-6, 2-5).
A reply with any dropped bullet or the wrong count isn't published: the request is retried once, with a note naming
the problems (so the retry is a new input, with its own ops.ai_request row), then rejected with an alert
(`policy.ai_batch`). The database checks the published output's citations again at commit (Architecture 01 §1.4).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from ft.ai.citations import map_api_citation, numbers_in
from ft.ai.client import Prompt, Reply, Request, Source, TextPart

BULLETS = {"doc_summary": (3, 6), "statement_diff_summary": (2, 5)}
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


@dataclass
class Checked:
    task: str
    bullets: list[Bullet]
    problems: list[str]               # reply-level: the count, no bullets
    mapping: Counter                  # citations by how they mapped: offsets, search, failed, wrong_place, ...

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


def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "date not recorded"


# ---------------------------------------------------------------------------------------------- requests
def _note(note: str | None) -> str:
    if not note:
        return ""
    return ("\n\nYour previous answer to this request couldn't be published: " + note
            + "\nAnswer again, following every rule. Cite the passage that states each point, and write numbers "
              "only as the cited passage writes them.")


def doc_summary_request(doc: Doc, prompt: Prompt, *, note: str | None = None) -> Request:
    lo, hi = BULLETS["doc_summary"]
    src = Source(doc.document_id, doc.text, doc.title, context=f"{doc.bank} · {doc.label} · released {_when(doc.day)}")
    instruction = (f"Summarise the {doc.label} above ({doc.title}) in {lo} to {hi} bullets, following the rules."
                   + _note(note))
    return Request("doc_summary", prompt, (src,), instruction)


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
    instruction = (f"Document 1 is the previous {old.label} ({_when(old.day)}); document 2 is the new one "
                   f"({_when(new.day)}). These sentences changed between them:\n\n"
                   f"{redline_text(segments, old.text, new.text)}\n\n"
                   f"In {lo} to {hi} bullets, explain what changed, following the rules." + _note(note))
    return Request("statement_diff_summary", prompt, sources, instruction)


# ---------------------------------------------------------------------------------------------- checking a reply
def split_bullets(parts: list[TextPart]) -> list[tuple[str, list]]:
    """(bullet text, the API citations of the text blocks overlapping it), in order."""
    text, spans = "", []
    for p in parts:
        spans.append((len(text), len(text) + len(p.text), p.citations))
        text += p.text
    ranges: list[list[int]] = []
    current: list[int] | None = None
    pos = 0
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        pos = end + 1
        m = _MARK.match(line)
        if m:
            current = [start + m.end(), end]
            ranges.append(current)
        elif not line.strip():
            current = None
        elif current is not None:
            current[1] = end                                 # a bullet wrapped onto another line
    out = []
    for s, e in ranges:
        body = " ".join(text[s:e].split())
        cites = [c for bs, be, cs in spans if bs < e and be > s for c in cs]
        if body:
            out.append((body, cites))
    return out


def _overlaps(s: int, e: int, spans: list[tuple[int, int]]) -> bool:
    return any(s < b and e > a for a, b in spans)


def check(task: str, reply: Reply, sources: tuple[Source, ...], *,
          changed: dict[int, list[tuple[int, int]]] | None = None, full_text: dict[int, str] | None = None) -> Checked:
    """Check a reply against our copies of the documents it was given (`sources`; `full_text` by document id when a
    source is a segment). `changed`: for statement_diff_summary, each document's changed spans."""
    lo, hi = BULLETS[task]
    mapping: Counter = Counter()
    bullets = []
    for body, cites in split_bullets(reply.parts()):
        b = Bullet(body)
        seen = set()
        passages = []
        for c in cites:
            if c.kind != "char_location" or c.document_index is None or not 0 <= c.document_index < len(sources):
                mapping["not_a_passage"] += 1
                continue
            src = sources[c.document_index]
            doc_text = (full_text or {}).get(src.document_id, src.text if src.seg_start == 0 else None)
            if doc_text is None or c.start is None or c.end is None:
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
        else:
            have = {abs(n) for p in passages for n in numbers_in(p)}
            extra = [str(n) for n in numbers_in(body) if abs(n) not in have]
            if extra:
                b.problem = "numbers not in its cited passages: " + ", ".join(dict.fromkeys(extra))
        bullets.append(b)
    problems = []
    if not bullets:
        problems.append("no bullets (each bullet is a line starting with '- ')")
    elif not lo <= len(bullets) <= hi:
        problems.append(f"{len(bullets)} bullets, not {lo} to {hi}")
    return Checked(task, bullets, problems, mapping)


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
        bullets.append({"text": b.text, "citations": nos})
    doc = {"bullets": bullets, "attempt": attempt, "stop_reason": reply.stop_reason,
           "citation_mapping": dict(sorted(c.mapping.items()))}
    if not c.ok:
        doc["problems"] = c.reasons()
        doc["dropped"] = [{"text": b.text, "problem": b.problem} for b in c.dropped]
    return doc, "\n".join(b.text for b in kept), cites
