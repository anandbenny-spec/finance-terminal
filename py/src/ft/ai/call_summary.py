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

**Copied text** (2B-11c, #301; `apply_echo`). Visitors see only the quotes of verified citations, never a transcript's text
(`CLAUDE.md` §1), and the Earnings calls tab shows a bullet in full, so a bullet may not carry the transcript's own words
(on dev, 65% of v5's bullets were a short paraphrase followed by the cited passage verbatim). A run of more than
`MAX_ECHO_CHARS` characters found verbatim in the transcript (whitespace, curly quotes and dashes aside; case kept) is copied
text. One that reaches the bullet's end (up to `ECHO_TAIL_CHARS` characters of punctuation or quotes may follow) is cut, where
it starts, and the bullet stands on its lead-in with its citations unchanged (the quote function still shows the source,
at most 400 characters); this repeats while the new end is a copied run. Copied text anywhere else, or a lead-in under
`MIN_CUT_WORDS` words, or a figure that is no longer in the cited passages, fails the bullet ("copies transcript text").
The same function re-checks the outputs stored before the rule (`equity.calls_ai`'s re-check, no AI). Module 1's summaries
don't use it.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
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
MAX_ECHO_CHARS = 80                                   # a run longer than this found verbatim in the transcript is copied text
MIN_CUT_WORDS = 6                                     # a bullet cut to its lead-in keeps at least this many words
ECHO_TAIL_CHARS = 5                                   # punctuation or quotes allowed after a copied run that ends a bullet
ECHO_RULE = "echo-1"                                  # the rule's name: stamped on the outputs checked or re-checked under it
_TRAIL = " :;,-\u2014\u2013\"'\u201c\u201d\u2018\u2019\u2026"        # stripped from the end of a lead-in
_CLAUSE_END = ".!?:;,\u2014\u2013"                          # a lead-in that ends on one of these ends at a clause or sentence


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
    fy_end_month: int | None = None   # the company's fiscal-year end month (equity.company): the guidance extraction's


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


@dataclass(frozen=True)
class Echo:
    """A bullet after the copied-text rule: its text as it stands (cut, or as it was), whether a copied end was cut, whether the
    cut left the lead-in at a clause or sentence end (`clean`), and why it fails, if it does."""
    text: str
    cut: bool = False
    clean: bool = True
    problem: str | None = None


def _ends_clause(form: str, at: int) -> bool:
    return form[:at].rstrip(" \"'").endswith(tuple(_CLAUSE_END))


def _cut_point(form: str, run: tuple[int, int], starts: Sequence[str]) -> tuple[int, bool] | None:
    """Where to cut a copied tail, and whether the lead-in then ends at a clause or sentence end; None when the cut would fall
    inside a word. A run starts where the text stops matching on its left, so when the lead-in's last characters happen to
    equal those before the passage in the transcript (a figure and a full stop, say) the run reaches back into the lead-in.
    `starts` (the first characters of the cited passages, in `match_form`) say where the copied passage really begins: if
    one begins just inside the run, the cut is there, and the lead-in keeps its own last words."""
    start, end = run
    at = start
    if not any(form.startswith(p, start) for p in starts):
        at = min((i for p in starts if (i := form.find(p, start + 1, min(end, start + 40 + len(p)))) > start), default=start)
    if 0 < at < len(form) and form[at - 1].isalnum() and form[at].isalnum():
        return None
    return at, _ends_clause(form, at)


def _finish(text: str) -> str:
    """A cut lead-in as a bullet: trailing spaces, `:;,-` and dashes, quotes and an ellipsis off, and a full stop."""
    text = text.rstrip()
    while text and text[-1] in _TRAIL:
        text = text[:-1].rstrip()
    return text + "." if text and text[-1] not in ".!?" else text


def apply_echo(body: str, passages: Sequence[str], transcript: str) -> Echo:
    """The copied-text rule on one bullet that has passed `verify_bullet` and the role rules. `passages`: the text its verified
    citations cover; `transcript`: the call's text in `summaries.match_form`. A copied end is cut (and again while the new
    end is copied); copied text elsewhere fails the bullet; so does a cut bullet with under `MIN_CUT_WORDS` words, or with a
    figure its cited passages don't hold."""
    text, cut, clean = " ".join(body.split()), False, True
    starts = [f[:20] for p in passages if len(f := summaries.match_form(p)) >= 20]       # where each cited passage begins
    while True:
        form = summaries.match_form(text)
        runs = summaries.copied_runs(form, transcript, MAX_ECHO_CHARS)
        if not runs:
            break
        tail = next((r for r in runs if len(form) - r[1] <= ECHO_TAIL_CHARS
                     and not any(ch.isalnum() for ch in form[r[1]:])), None)
        if tail is None:
            longest = max(b - a for a, b in runs)
            return Echo(body, problem=f"copies transcript text ({longest} characters word for word, not at the bullet's end)")
        point = _cut_point(form, tail, starts)
        if point is None:
            return Echo(body, problem="copies transcript text (the copied passage starts inside a word of its lead-in)")
        text, cut, clean = _finish(text[:point[0]]), True, clean and point[1]
    if cut:
        words = len(text.split())
        if words < MIN_CUT_WORDS:
            return Echo(body, problem=f"copies transcript text (only {words} words are its own once the copied end is cut,"
                                      f" under {MIN_CUT_WORDS})")
        if extra := summaries.unsupported_figures(text, list(passages)):
            return Echo(body, problem="copies transcript text (once the copied end is cut, " + ", ".join(extra)
                                      + " is not in its cited passages)")
    return Echo(text, cut, clean)


@dataclass
class Rechecked:
    """A stored summary's bullets under the copied-text rule (`recheck_content`)."""
    bullets: list[dict]                                   # as they stand: the stored bullets, a cut one with its new text
    cut: list[int] = field(default_factory=list)          # the bullets whose copied end was cut
    unclean: list[int] = field(default_factory=list)      # of those, the ones whose lead-in doesn't end at a clause end
    failed: dict[int, str] = field(default_factory=dict)  # the bullets that fail, with why
    longest: int = 0                                      # the longest verbatim run left in any bullet that stands

    @property
    def verdict(self) -> str:
        return "rejected" if self.failed else "cut" if self.cut else "unchanged"


def recheck_content(content: dict, spans: dict[int, tuple[int, int]], text: str, transcript: str) -> Rechecked:
    """The copied-text rule on a stored output's bullets. `content`: its `core.ai_output.content` (`bullets` with their citation
    numbers); `spans`: its verified citations, `{citation_no: (char_start, char_end)}` in `text`, the call's canonical text;
    `transcript`: `text` in `summaries.match_form`. Nothing is written: the caller supersedes the output with the new bullets,
    or with a rejection, or leaves it."""
    out = Rechecked([])
    for i, b in enumerate(content.get("bullets") or []):
        nos = b.get("citations") or []
        if not nos or any(n not in spans for n in nos):
            out.failed[i] = "no verified citation (its stored citation is missing)"
            out.bullets.append(dict(b))
            continue
        echo = apply_echo(b["text"], [text[spans[n][0]:spans[n][1]] for n in nos], transcript)
        if echo.problem:
            out.failed[i] = echo.problem
            out.bullets.append(dict(b))
            continue
        if echo.cut:
            out.cut.append(i)
            if not echo.clean:
                out.unclean.append(i)
        out.bullets.append({**b, "text": echo.text})
        out.longest = max(out.longest, summaries.longest_copied(summaries.match_form(echo.text), transcript))
    return out


def check(reply: Reply, sources: tuple[Source, ...], turns: Sequence[Turn], *, role_held: bool = False) -> Checked:
    """Check a reply against our copy of the transcript (`sources`) and its stored turns."""
    mapping: Counter = Counter()
    found = summaries.scan(reply.parts(), HEADINGS)
    answers = answer_turns(turns)
    rng = ranges(role_held=role_held, has_unknown=any(t.role == "unknown" for t in turns))
    text_of = {s.document_id: s.text for s in sources}
    transcripts = {d: summaries.match_form(t) for d, t in text_of.items()}            # for the copied-text rule
    bullets = []
    for heading, body, cites in found.bullets:
        b = summaries.verify_bullet(body, cites, sources, mapping)
        b.section = LISTS[heading] if heading is not None else None
        if b.section is None:
            b.problem = "outside the lists"
        elif b.problem is None:
            b.problem = role_problem(b.section, b.citations, turns, answers)
        if b.problem is None and b.citations:
            doc = b.citations[0].document_id
            e = apply_echo(b.text, [text_of[c.document_id][c.start:c.end] for c in b.citations], transcripts[doc])
            b.problem, b.text = e.problem, (b.text if e.problem else e.text)
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
               r"isn't for this call", r"bullets in all, not at least", r"copies transcript text"),
}
