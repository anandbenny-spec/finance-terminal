"""Earnings-call summaries (Spec 02B §5.6, §13.3 and Spec 02B.1 C10; job 2B-11, #174): the request for one transcript and the
checks on the reply. The task that sends it, `equity.calls_ai`, is in ft/jobs/tasks/equity_calls_ai.py.

The transcript goes as one plain-text document block with citations on (the speaker labels are in the text). The request's
instruction carries a roster of the speakers by role, as the rules read it (`equity.call_turn`, written by rules in 2B-09,
never by the model; the labels are the transcript's own, so the roster adds no inference), outside the cited block so that
citations still map to our text. The reply is up to three lists, each under its own heading line: "What management said",
"What analysts asked about", and (only on a call that has turns whose role couldn't be read) "Said by participants whose role
couldn't be read", which isn't attributed to management.

**Checking a reply** (`check`), before anything is stored. Every bullet gets `summaries.py`'s rules (`verify_bullet`): at
least one citation verified in our copy of the transcript, and only figures its cited passages hold; nothing stands outside
the bullets, and each list's count is in range (`ranges`). Then the role rules, from the call's stored turns:
- a management bullet cites only management turns;
- an analysts bullet cites only an analyst's turn, or the management answer that follows it (the management turns that
  follow an analyst's turn directly, several executives answering one question included, until anyone else speaks);
- a bullet of the third list cites only turns whose role couldn't be read;
- a citation that touches a turn of another role (a moderator's included) or text before the first turn fails its bullet;
  so does one that runs from a turn of the right role into another.
A role-held call (more than 10% unknown-role words) keeps every list but may leave a list out when none of its speakers can
support it, with at least three bullets in all; a call whose roles are read has the first two lists in full. A reply with any
dropped bullet, a missing or misplaced list or a count out of range isn't published: the task retries it once with a note
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
HEADINGS = ("What management said", "What analysts asked about", "Said by participants whose role couldn't be read")
LISTS = ("management", "analysts", "unknown")         # the content's tag for each heading's bullets
MIN_ROLE_HELD = 3                                     # a role-held call's summary has at least this many bullets in all
ROSTER_ROLES = (("management", "Management"), ("analyst", "Analysts"), ("moderator", "Moderator"),
                ("unknown", "Role couldn't be read"))
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
    role_held: bool = False           # more than 10% of the call's words have an unknown role (turns.ROLE_HOLD_SHARE)


def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "date not recorded"


def ranges(*, role_held: bool, has_unknown: bool) -> tuple[tuple[int, int], ...]:
    """The bullet count (lowest, highest) of each list (Spec 02B.1 C10). A call whose roles are read: management 3-6,
    analysts 2-4, and the third list 0-4 only when the call has turns whose role couldn't be read. A role-held call: each
    list 0-6, 0-4 and 0-4 (a list may be left out), with `MIN_ROLE_HELD` bullets in all."""
    if role_held:
        return ((0, 6), (0, 4), (0, 4))
    return ((3, 6), (2, 4), (0, 4) if has_unknown else (0, 0))


def roster(turns: Sequence[Turn]) -> list[tuple[str, list[str]]]:
    """The speakers by role as the rules read it, in order of first appearance: [(role label, labels as the transcript
    gives them)], for the roles that have speakers."""
    by: dict[str, list[str]] = {}
    for t in sorted(turns, key=lambda t: t.turn_no):
        if t.speaker and t.speaker not in by.setdefault(t.role, []):
            by[t.role].append(t.speaker)
    return [(label, by[role]) for role, label in ROSTER_ROLES if by.get(role)]


def call_summary_request(call: CallDoc, prompt: Prompt, *, note: str | None = None) -> Request:
    has_unknown = any(t.role == "unknown" for t in call.turns)
    rng = ranges(role_held=call.role_held, has_unknown=has_unknown)
    quarter = f" · {call.fiscal_label}" if call.fiscal_label else ""
    src = Source(call.document_id, call.text, call.title, context=f"{call.company} · Earnings call{quarter} · {_when(call.day)}")
    lines = ["The speakers, as the transcript labels them, by the role our rules read for each:"]
    lines += [f"- {label}: {'; '.join(names)}" for label, names in roster(call.turns)]
    lists = [f'"{HEADINGS[i]}" ({lo} to {hi} bullets)' if lo else f'"{HEADINGS[i]}" (up to {hi} bullets)'
             for i, (lo, hi) in enumerate(rng) if hi > 0]
    lines.append(f"Summarise the earnings call above ({call.title}) in these lists, following the rules: "
                 + ", ".join(lists) + ".")
    if call.role_held:
        lines.append(f"This call's roles are only partly read. Write each list you can support from turns of the right "
                     f"role, and leave out entirely (heading included) a list those turns can't support, with at least "
                     f"{MIN_ROLE_HELD} bullets in all.")
    elif has_unknown:
        lines.append(f'Write "{HEADINGS[2]}" only if those participants said something worth a bullet; otherwise leave it '
                     f"out entirely, heading included.")
    return Request(TASK, prompt, (src,), "\n".join(lines), note=summaries.note_block(note))


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
    who, what = {"management": ("a management bullet", "management turns only"),
                 "analysts": ("an analysts bullet", "an analyst's question or the management answer that follows it only"),
                 "unknown": ("a bullet on participants whose role couldn't be read",
                             "turns whose role couldn't be read only")}[kind]
    for c in cited:
        hit = [t for t in turns if t.char_start < c.end and c.start < t.char_end]
        if not hit:
            return "cites text outside any speaker turn"
        for t in hit:
            allowed = (t.role == "management" if kind == "management"
                       else t.role == "unknown" if kind == "unknown" else (t.role == "analyst" or t.turn_no in answers))
            if not allowed:
                return f"{who} cites a turn whose role is {t.role} ({what})"
    return None


def check(reply: Reply, sources: tuple[Source, ...], turns: Sequence[Turn], *, role_held: bool = False) -> Checked:
    """Check a reply against our copy of the transcript (`sources`) and its stored turns."""
    mapping: Counter = Counter()
    found = summaries.scan(reply.parts(), HEADINGS)
    answers = answer_turns(turns)
    rng = ranges(role_held=role_held, has_unknown=any(t.role == "unknown" for t in turns))
    bullets = []
    for heading, body, cites in found.bullets:
        b = summaries.verify_bullet(body, cites, sources, mapping)
        b.section = LISTS[heading] if heading is not None else None
        if b.section is None:
            b.problem = "outside the lists"
        elif b.problem is None:
            b.problem = role_problem(b.section, b.citations, turns, answers)
        bullets.append(b)
    problems = []
    if found.headings != sorted(set(found.headings)):
        got = " then ".join(f"'{HEADINGS[i]}'" for i in found.headings)
        problems.append(f"the lists aren't as asked (each heading at most once, in this order: "
                        f"{' · '.join(HEADINGS)}): found {got}")
    if found.stray:
        problems.append("text outside the bullets: " + " / ".join(f'"{x[:120]}"' for x in found.stray))
    if not bullets:
        problems.append("no bullets (each bullet is a line starting with '- ')")
    for i, (lo, hi) in enumerate(rng):
        n = sum(b.section == LISTS[i] for b in bullets)
        if i in found.headings and hi == 0:
            problems.append(f"'{HEADINGS[i]}' isn't for this call (no turn whose role couldn't be read)")
        elif i in found.headings and n == 0:
            problems.append(f"'{HEADINGS[i]}' has no bullets")
        elif not lo <= n <= hi:
            problems.append(f"{n} bullets under '{HEADINGS[i]}', not {lo} to {hi}")
    if role_held and bullets and len(bullets) < MIN_ROLE_HELD:
        problems.append(f"{len(bullets)} bullets in all, not at least {MIN_ROLE_HELD}")
    return Checked(TASK, bullets, problems, mapping)


# The stable phrases `check` writes into a reason, by the accuracy page's category (ft/quality/reasons.py reads this table;
# tests run `check` on crafted bad replies and assert each phrase still lands where it says). Regexes, case-insensitive.
REASON_PHRASES: dict[str, tuple[str, ...]] = {
    "citation": (r"cites a turn whose role is", r"cites text outside any speaker turn"),
    "format": (r"the lists aren't as asked", r"outside the lists", r"\d+ bullets under", r"has no bullets",
               r"isn't for this call", r"bullets in all, not at least"),
}
