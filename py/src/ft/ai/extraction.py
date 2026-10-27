"""Module 1's structured extractions (Spec 01 §3.2-§3.3; Architecture 03 §1.2, §1.4): `stance_extraction` on MPC
resolutions only (no Fed stance label anywhere: Spec 01 D2) and `projection_extraction` where the regex couldn't fully
read a projection sentence. Both take the JSON route: the resolution goes as a plain-text document block without
citations, the reply is the schema's JSON (structured outputs), and every quote in it is located in our own copy of
the text before anything is stored (Architecture 03 §1.2: found exactly once, spacing differences allowed).

**stance_extraction** (`{stance, quote}`):
- the label is one of §3.3's vocabulary, or `other`, which is held: the output stays a draft and an alert asks for
  the list to be extended;
- the quote is found exactly once, and our own vocabulary reader (`rbi_resolution.stance_label`, the one B6's parser
  uses) reads the same label in it, so the cited passage states the stance. From prompt v2 (Anand, 27 Oct 2026, issue
  #16) the quote is the stance sentence with the sentence just before it: resolutions repeat the stance sentence word
  for word in their closing paragraph, so the sentence alone was found twice and held back 5 of production's 48;
- **cross-check (Anand, 24 Oct 2026, issue #16):** where B6's parser read the resolution's stance, the label must
  equal it. A disagreement is held (a draft with an alert), so the published stance never contradicts the stance the
  vote tally counts dissents against. Where the parser read none, the rules above decide alone.

**projection_extraction** (`{projections: [{variable, target_label, target_start, target_end, value | low and high,
quote}]}`), on the sentences the regex couldn't fully read (`rbi_resolution.read_projections`):
- each quote is found exactly once, inside one of those sentences;
- its period is written as the regex writes it ('Q2 2026-27', 'H1 2019-20', 'FY 2026-27') and its dates are that
  period's;
- its figure is one value, or a range with low below high;
- the quote states that period at exactly that figure "per cent", sign included: its periods and figures, read with
  the regex's own grammar, pair up one to one in order, and one pair is this projection's
  (`rbi_resolution.quoted_pairs`). So "its numbers equal the values" (Architecture 03 §1.2), and a "respectively"
  sentence can't swap two quarters' figures;
- the period's fiscal year is the one the regex gives it where the resolution states it (a bare "Q2" takes the year
  named before it in its sentence or paragraph: `rbi_resolution.period_years`), and the sentence is about the
  projection's variable there (the last CPI-inflation or GDP-growth mention before the quote, never core inflation or
  GVA: `rbi_resolution.subject_at`), so a quarter can't move to another year nor a CPI figure become GDP growth;
- no target twice, and at least one projection;
- **the same cross-check:** a target the regex also stored must have the same figure; otherwise the output is held.

A reply that isn't the schema's JSON or fails a check above (except a hold) is retried once with a note naming the
problems, then rejected with an alert (`policy.ai_batch`); a held reply is never retried. The checks take the
answer itself, so a held output can be checked again later without a new request (`policy.ai_batch` re-checks held
drafts each run: a stance once B6's parse agrees, a projection conflict once the stored figure is corrected).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ft.ai.citations import locate_quote
from ft.ai.client import Prompt, Reply, Request, Source
from ft.ai.summaries import Cited, Doc
from ft.engines.document.text import normalize_inline
from ft.policy import rbi_resolution
from ft.policy.rbi_resolution import VOCABULARY

TASKS = ("stance_extraction", "projection_extraction")
OTHER = "other"
VARIABLES = {"cpi": "CPI inflation", "gdp": "Real GDP growth"}


def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "date not recorded"


def _note(note: str | None) -> str | None:
    """The retry's note, in its own last block (so the retry can read the first attempt's cached prefix)."""
    if not note:
        return None
    return ("Your previous answer to this request couldn't be published: " + note
            + "\nAnswer again, following every rule. Copy every quote exactly from the resolution, character for "
              "character, with no ellipsis and no words added or changed.")


def _source(doc: Doc) -> Source:
    return Source(doc.document_id, doc.text, doc.title, context=f"{doc.bank} · {doc.label} · released {_when(doc.day)}")


# ---------------------------------------------------------------------------------------------- requests
# What the user turn asks the stance quote to be, by prompt version. v1's wording is kept as it ran, so its requests'
# input hashes don't change; from v2 the quote is the stance sentence with the sentence before it (issue #16).
STANCE_QUOTE_ASK = {"v1": "quote the sentence that states it"}
STANCE_QUOTE_ASK_DEFAULT = "quote the sentence that states it together with the sentence just before it"


def stance_request(doc: Doc, prompt: Prompt, schema: dict, *, note: str | None = None) -> Request:
    ask = STANCE_QUOTE_ASK.get(prompt.version, STANCE_QUOTE_ASK_DEFAULT)
    instruction = (f"Give the monetary policy stance the MPC decided in the resolution above ({doc.title}), and {ask}, "
                   "following the rules.")
    return Request("stance_extraction", prompt, (_source(doc),), instruction, schema=schema, note=_note(note))


def projection_request(doc: Doc, sentences: tuple[tuple[int, int], ...], prompt: Prompt, schema: dict, *,
                       note: str | None = None) -> Request:
    listed = "\n".join(f'{i}. "{doc.text[s:e]}"' for i, (s, e) in enumerate(sentences, start=1))
    instruction = (f"Read these sentences of the resolution above ({doc.title}):\n\n{listed}\n\n"
                   "List every CPI inflation and real GDP growth projection this resolution makes in them, following "
                   "the rules.")
    return Request("projection_extraction", prompt, (_source(doc),), instruction, schema=schema, note=_note(note))


# ---------------------------------------------------------------------------------------------- checking an answer
@dataclass(frozen=True)
class Parsed:
    """What B6's parser read for the cross-check: the stance label (None if it read none), or why it couldn't be
    read; for projections, the targets the regex stored, {(variable, start, end): (value, low, high)}."""
    stance: str | None = None
    stored: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Checked:
    task: str
    answer: dict | None               # the reply's JSON, as returned
    items: list[dict]                 # what is stored: the stance, or the projections, with their spans in our text
    citations: list[tuple[str, Cited]]   # (claim_path, span): one per quote
    problems: list[str]               # retried once, then rejected
    hold: str | None = None           # kept as a draft with an alert, never retried (Anand's call)
    mapping: Counter = field(default_factory=Counter)   # quotes by how they were found (citation_integrity)

    @property
    def ok(self) -> bool:
        return not self.problems and self.hold is None

    def reasons(self) -> list[str]:
        return self.problems + ([self.hold] if self.hold and not self.problems else [])

    def note(self) -> str:
        return "; ".join(self.problems) + "."


def _short(s: str, n: int = 120) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _locate(text: str, quote: str, mapping: Counter):
    span = locate_quote(text, normalize_inline(quote))
    mapping["search" if span.verified else "failed"] += 1
    return span


def check_stance(answer, text: str, document_id: int, parsed: Parsed) -> Checked:
    c = Checked("stance_extraction", answer if isinstance(answer, dict) else None, [], [], [])
    if not isinstance(answer, dict) or not isinstance(answer.get("stance"), str) or not isinstance(answer.get("quote"),
                                                                                                    str):
        c.problems.append("the reply isn't the schema's JSON ({stance, quote})")
        return c
    stance, quote = answer["stance"], answer["quote"]
    if stance != OTHER and stance not in VOCABULARY:
        c.problems.append(f"the stance {stance!r} isn't one of the schema's labels")
        return c
    span = _locate(text, quote, c.mapping) if quote.strip() else None
    item = {"stance": stance, "quote": text[span.start:span.end] if span and span.verified else quote,
            "span": [span.start, span.end] if span and span.verified else None}
    c.items.append(item)
    if stance == OTHER:
        c.hold = (f"other: the stance isn't in the vocabulary (Spec 01 §3.3), quoted as \"{_short(quote)}\": extend "
                  "the list")
        return c
    if span is None or not span.verified:
        c.problems.append(f"the quote \"{_short(quote)}\" isn't in the resolution exactly once (a resolution may repeat "
                          "its stance sentence word for word: quote it together with the sentence just before it)")
        return c
    reads = rbi_resolution.stance_label(text[span.start:span.end])
    if reads != stance:
        c.mapping["unread"] += 1
        c.problems.append(f"the quote doesn't state the stance {stance!r}: quote the sentence of the resolution that "
                          "states the stance the MPC decided")
        return c
    c.citations.append(("/quote", Cited(document_id, span.start, span.end, "search")))
    if parsed.error:
        c.hold = f"unchecked: the resolution's parsed stance couldn't be read ({parsed.error})"
    elif parsed.stance is not None and parsed.stance != stance:
        c.hold = f"mismatch: the AI read {stance!r}, the resolution's parsed stance is {parsed.stance!r}"
    return c


def _number(v) -> Decimal | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _date(v) -> date | None:
    try:
        return date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def _figure(value, low, high) -> str:
    return f"{value:f}" if value is not None else f"{low:f}-{high:f}"


def check_projections(answer, text: str, document_id: int, sentences: tuple[tuple[int, int], ...],
                      parsed: Parsed) -> Checked:
    c = Checked("projection_extraction", answer if isinstance(answer, dict) else None, [], [], [])
    rows = answer.get("projections") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        c.problems.append("the reply isn't the schema's JSON ({projections: [...]})")
        return c
    seen: set[tuple] = set()
    for i, p in enumerate(rows, start=1):
        bad = []
        if not isinstance(p, dict):
            c.problems.append(f"projection {i} isn't an object")
            continue
        variable, label, quote = p.get("variable"), p.get("target_label"), p.get("quote")
        start, end = _date(p.get("target_start")), _date(p.get("target_end"))
        value, low, high = _number(p.get("value")), _number(p.get("low")), _number(p.get("high"))
        if variable not in VARIABLES or not isinstance(label, str) or not isinstance(quote, str):
            c.problems.append(f"projection {i} isn't the schema's object")
            continue
        dates = rbi_resolution.target_dates(label)
        if dates is None:
            bad.append(f"its period {label!r} isn't written 'Q2 2026-27', 'H1 2026-27' or 'FY 2026-27'")
        elif (start, end) != dates:
            bad.append(f"{label} runs {dates[0]} to {dates[1]}, not {p.get('target_start')} to {p.get('target_end')}")
        single = value is not None and low is None and high is None
        ranged = value is None and low is not None and high is not None and low < high
        if not (single or ranged):
            bad.append("it needs one value, or a range with low below high")
        span = _locate(text, quote, c.mapping) if quote.strip() else None
        sentence = next(((s, e) for s, e in sentences if span is not None and span.verified and s <= span.start
                         and span.end <= e), None)
        if span is None or not span.verified:
            bad.append(f"its quote \"{_short(quote)}\" isn't in the resolution exactly once")
        elif sentence is None:
            c.mapping["outside"] += 1
            bad.append(f"its quote \"{_short(quote)}\" isn't inside one of the sentences given")
        elif dates is not None and (single or ranged):
            quoted = text[span.start:span.end]
            pairs = rbi_resolution.quoted_pairs(quoted)
            kind, fy = label.split(" ")
            want = value if single else (low, high)
            match = [g for k, f, g in pairs or [] if k == kind and f in (None, fy) and g == want]
            # The period's fiscal year as the regex reads it in context (a bare "Q2" takes the year named before it
            # in the sentence or paragraph), and what the sentence is about at the quote: CPI or GDP.
            years = {y for a, b, k, y in rbi_resolution.period_years(text, sentence)
                     if k == kind and span.start <= a and b <= span.end}
            about = rbi_resolution.subject_at(text, sentence, span.end)
            if pairs is None:
                bad.append("its quote doesn't state exactly one figure for each period it names")
            elif not match:
                bad.append(f"its quote doesn't state {label} at {_figure(value, low, high)} per cent")
            elif fy not in years:
                named = ", ".join(sorted(y for y in years if y)) or "no fiscal year named before it"
                bad.append(f"its {kind} is in {named} where the resolution states it, not {fy}")
            elif about != variable:
                bad.append(f"its sentence states {VARIABLES[about] if about else 'neither CPI inflation nor real GDP '
                           'growth'} there, not {VARIABLES[variable]}")
            elif single:     # the figure as the resolution writes it ("7.0", not 7)
                value = match[0]
            else:
                low, high = match[0]
        key = (variable, start, end)
        if key in seen:
            bad.append(f"{VARIABLES[variable]} {label} is listed twice")
        seen.add(key)
        if bad:
            c.problems.append(f"projection {i} ({variable} {label}): " + "; ".join(bad))
            continue
        c.items.append({"variable": variable, "target_label": label, "target_start": start.isoformat(),
                        "target_end": end.isoformat(), "value": None if value is None else f"{value:f}",
                        "low": None if low is None else f"{low:f}", "high": None if high is None else f"{high:f}",
                        "quote": quoted, "span": [span.start, span.end]})
        c.citations.append((f"/projections/{len(c.items) - 1}", Cited(document_id, span.start, span.end, "search")))
    if not rows:
        c.problems.append("no projection is listed: each sentence given states at least one")
    if c.problems:
        return c
    conflicts = []
    for it in c.items:
        key = (it["variable"], date.fromisoformat(it["target_start"]), date.fromisoformat(it["target_end"]))
        if key in parsed.stored:
            have = parsed.stored[key]
            mine = tuple(None if it[k] is None else Decimal(it[k]) for k in ("value", "low", "high"))
            if have != mine:
                conflicts.append(f"{it['variable']} {it['target_label']}: the AI read {_figure(*mine)}, the regex "
                                 f"stored {_figure(*have)}")
    if conflicts:
        c.hold = "conflict: " + "; ".join(conflicts)
    return c


def answer_of(reply: Reply):
    """The reply's JSON, or None if it isn't JSON."""
    try:
        return reply.json()
    except (ValueError, TypeError):
        return None


def content(c: Checked, *, attempt: int, reply: Reply | None) -> tuple[dict, str | None, str | None]:
    """(core.ai_output.content, .body, .label). A published stance shows its label and its quote (body); a held or
    rejected output keeps its answer and the reasons, for the record (drafts and rejected outputs are never
    visible)."""
    doc = {"answer": c.answer, "items": c.items, "attempt": attempt,
           "stop_reason": reply.stop_reason if reply is not None else None, "quote_mapping": dict(sorted(c.mapping.items()))}
    if c.problems:
        doc["problems"] = list(c.problems)
    if c.hold:
        doc["hold"] = c.hold
    if c.task == "stance_extraction" and c.items:
        return doc, c.items[0]["quote"], c.items[0]["stance"]
    return doc, None, None
