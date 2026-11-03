"""Earnings-call summaries (Spec 02B §5.6, §13.3; job 2B-11, #174): the request for one transcript and the checks on the
reply. The task that sends it, `equity.calls_ai`, is in ft/jobs/tasks/equity_calls_ai.py.

The transcript goes as one plain-text document block with citations on (the speaker labels are in the text). The reply is
two lists, each under its own heading line: "What management said" (3-6 bullets) and "What analysts asked about" (2-4).

**Checking a reply** (`check`), before anything is stored. Every bullet gets `summaries.py`'s rules (`verify_bullet`): at
least one citation verified in our copy of the transcript, and only figures its cited passages hold; nothing stands outside
the bullets, and each list's count is in range. Then the role rules, from the call's stored turns (`equity.call_turn`,
written by rules in 2B-09 and never by the model):
- a management bullet cites only management turns;
- an analysts bullet cites only an analyst's turn, or the management answer that follows it (the management turns that
  follow an analyst's turn directly, several executives answering one question included, until anyone else speaks);
- a citation that touches a moderator turn, a turn of a speaker whose role couldn't be read, or text before the first
  turn fails its bullet; so does one that runs from a turn of the right role into another.
So on a call whose roles couldn't be read (above 10% unknown-role words) the summary still runs, but a bullet can rest only
on the turns whose roles are known; where none can, the reply is rejected with an alert, never published with a guess.
A reply with any dropped bullet, a missing list or a count out of range isn't published: the task retries it once with a note
naming the problems, then rejects it (`ft/ai/batch.py`).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ft.ai import summaries
from ft.ai.client import Prompt, Reply, Request, Source
from ft.ai.summaries import Checked, Cited

TASK = "call_summary"
HEADINGS = ("What management said", "What analysts asked about")
LISTS = ("management", "analysts")                    # the content's tag for each heading's bullets
RANGES = ((3, 6), (2, 4))                             # the bullet count of each list
MAX_ESTIMATED_TOKENS = 60_000                         # §5.5: a transcript above this is skipped with a warning naming it


@dataclass(frozen=True)
class Turn:
    """One speaker turn of the transcript, as `equity.call_turn` holds it (code-point offsets into the canonical text)."""
    turn_no: int
    char_start: int
    char_end: int
    speaker: str
    role: str                         # management | analyst | moderator | unknown
    section: str                      # opening | qa | closing


@dataclass(frozen=True)
class CallDoc:
    """A stored transcript as the summary sees it."""
    document_id: int
    text: str                         # the canonical text (the chunks, concatenated)
    title: str                        # 'Infosys Limited · Q2 FY27 earnings call transcript'
    company: str
    fiscal_label: str | None          # 'Q2 FY27'
    day: date | None                  # the call date, else the day the transcript was filed
    turns: tuple[Turn, ...] = ()


def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "date not recorded"


def call_summary_request(call: CallDoc, prompt: Prompt, *, note: str | None = None) -> Request:
    (lo1, hi1), (lo2, hi2) = RANGES
    quarter = f" · {call.fiscal_label}" if call.fiscal_label else ""
    src = Source(call.document_id, call.text, call.title, context=f"{call.company} · Earnings call{quarter} · {_when(call.day)}")
    instruction = (f"Summarise the earnings call above ({call.title}) in the two lists, following the rules: "
                   f"\"{HEADINGS[0]}\" ({lo1} to {hi1} bullets) and \"{HEADINGS[1]}\" ({lo2} to {hi2} bullets).")
    return Request(TASK, prompt, (src,), instruction, note=summaries.note_block(note))


def answer_turns(turns: Sequence[Turn]) -> set[int]:
    """The turn numbers of the management answers: management turns that follow an analyst's turn directly, as one run
    (several executives answering one question), until any other role speaks. A moderator or an unknown turn between an
    analyst and a management turn means the management turn isn't an answer we can tie to the question."""
    out: set[int] = set()
    prev = None                       # 'analyst' | 'answer' | the previous turn's other role
    for t in sorted(turns, key=lambda t: t.turn_no):
        if t.role == "management":
            if prev in ("analyst", "answer"):
                out.add(t.turn_no)
                prev = "answer"
            else:
                prev = "management"
        else:
            prev = t.role
    return out


def role_problem(kind: str, cited: Sequence[Cited], turns: Sequence[Turn], answers: set[int]) -> str | None:
    """Why a bullet of this list (`LISTS`) rests on turns it may not, or None. Every turn a citation touches must be one
    the list allows."""
    for c in cited:
        hit = [t for t in turns if t.char_start < c.end and c.start < t.char_end]
        if not hit:
            return "cites text outside any speaker turn"
        for t in hit:
            allowed = t.role == "management" if kind == "management" else (t.role == "analyst" or t.turn_no in answers)
            if not allowed:
                who = ("a management bullet", "management turns only") if kind == "management" else \
                      ("an analysts bullet", "an analyst's question or the management answer that follows it only")
                return f"{who[0]} cites a turn whose role is {t.role} ({who[1]})"
    return None


def check(reply: Reply, sources: tuple[Source, ...], turns: Sequence[Turn]) -> Checked:
    """Check a reply against our copy of the transcript (`sources`) and its stored turns."""
    mapping: Counter = Counter()
    found = summaries.scan(reply.parts(), HEADINGS)
    answers = answer_turns(turns)
    bullets = []
    for heading, body, cites in found.bullets:
        b = summaries.verify_bullet(body, cites, sources, mapping)
        b.section = LISTS[heading] if heading is not None else None
        if b.section is None:
            b.problem = "outside the two lists"
        elif b.problem is None:
            b.problem = role_problem(b.section, b.citations, turns, answers)
        bullets.append(b)
    problems = []
    if found.headings != [0, 1]:
        got = " then ".join(f"'{HEADINGS[i]}'" for i in found.headings) or "none"
        problems.append(f"the two lists aren't there as asked ('{HEADINGS[0]}' then '{HEADINGS[1]}', each once as a "
                        f"heading line of its own): found {got}")
    if found.stray:
        problems.append("text outside the bullets: " + " / ".join(f'"{x[:120]}"' for x in found.stray))
    if not bullets:
        problems.append("no bullets (each bullet is a line starting with '- ')")
    else:
        for i, (lo, hi) in enumerate(RANGES):
            n = sum(b.section == LISTS[i] for b in bullets)
            if not lo <= n <= hi:
                problems.append(f"{n} bullets under '{HEADINGS[i]}', not {lo} to {hi}")
    return Checked(TASK, bullets, problems, mapping)


# The stable phrases `check` writes into a reason, by the accuracy page's category (ft/quality/reasons.py reads this table;
# tests run `check` on crafted bad replies and assert each phrase still lands where it says). Regexes, case-insensitive.
REASON_PHRASES: dict[str, tuple[str, ...]] = {
    "citation": (r"cites a turn whose role is", r"cites text outside any speaker turn"),
    "format": (r"the two lists aren't there", r"outside the two lists", r"\d+ bullets under"),
}
