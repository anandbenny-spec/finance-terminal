"""A synthetic earnings call for the 2B-11 tests (`ft/ai/call_summary.py`, `equity.calls_ai`): a transcript of speaker turns
with their roles, and cited replies built from it. No real company, no real transcript text."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ai_fakes import cite, headed_lists, message
from anthropic.types import Message
from test_document_chunks import plain

from ft.ai.call_summary import HEADINGS, CallDoc, Turn
from ft.ai.client import Reply, Usage
from ft.engines.document.text import Extracted
from ft.equity.calls.turns import ROLE_HOLD_SHARE

TITLE = "Acme Limited · Q2 FY27 transcript"
Line = tuple[str, str, str]           # (speaker, role, the turn's text, label included)

LINES: list[Line] = [
    ("Moderator", "moderator", ("Moderator: Good day and welcome to the Acme Limited Q2 FY27 earnings conference call. "
                                "I now hand the conference over to Mr. Rao.")),
    ("Ravi Rao", "management", ("Ravi Rao: Thank you. Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27. "
                                "EBITDA margin was 21.3 per cent. We are glad to take your questions after these remarks.")),
    ("Meera Iyer", "management", ("Meera Iyer: Our order book stands at 9,200 crore. Demand in the export market remains "
                                  "soft because of weak freight rates. We plan capital expenditure of 800 crore this year.")),
    ("Moderator", "moderator", "Moderator: The first question is from Anil Shah of Orbit Securities."),
    ("Anil Shah", "analyst", "Anil Shah: Can you explain the margin outlook for the second half?"),
    ("Ravi Rao", "management", "Ravi Rao: We expect margins to stay near 21 per cent because input costs have eased."),
    ("Meera Iyer", "management", "Meera Iyer: Freight costs are also lower than last year."),
    ("Moderator", "moderator", "Moderator: The next question is from Pooja Menon of Delta Capital."),
    ("Pooja Menon", "analyst", "Pooja Menon: What is the plan for the dividend and the debt repayment?"),
    ("Meera Iyer", "management", "Meera Iyer: We will repay 300 crore of debt this year and maintain the dividend payout."),
    ("Moderator", "moderator", "Moderator: Thank you. I now hand the conference back to Mr. Rao for closing comments."),
    ("Ravi Rao", "management", "Ravi Rao: Thank you all for joining us."),
]
# turn numbers of LINES by what they are, for the tests that cite one
OPENING, ORDERS, MODERATOR_Q1, ANALYST_1, ANSWER_1, ANSWER_1B, MODERATOR_Q2, ANALYST_2, ANSWER_2 = 1, 2, 3, 4, 5, 6, 7, 8, 9

GOOD_MANAGEMENT = [
    ("Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27.",
     ["Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."]),
    ("EBITDA margin was 21.3 per cent.", ["EBITDA margin was 21.3 per cent."]),
    ("The order book stands at 9,200 crore.", ["Our order book stands at 9,200 crore."]),
]
GOOD_ANALYSTS = [
    ("The margin outlook for the second half, which management expects to stay near 21 per cent.",
     ["Can you explain the margin outlook for the second half?",
      "We expect margins to stay near 21 per cent because input costs have eased."]),
    ("The plan for the dividend and the debt repayment.",
     ["What is the plan for the dividend and the debt repayment?",
      "We will repay 300 crore of debt this year and maintain the dividend payout."]),
]


@dataclass(frozen=True)
class Built:
    text: str
    turns: tuple[Turn, ...]
    extracted: Extracted

    @property
    def unknown_share(self) -> float:
        """The unknown-role words over all the turns' words, as equity.call_text stores it."""
        words = [(t.role, len(self.text[t.char_start:t.char_end].split())) for t in self.turns]
        return sum(w for r, w in words if r == "unknown") / sum(w for _, w in words)

    @property
    def role_held(self) -> bool:
        return self.unknown_share > ROLE_HOLD_SHARE


def build(lines: list[Line] = LINES) -> Built:
    """The transcript (one paragraph a turn) and its turns: contiguous, each running to the next one's start."""
    paragraphs = [t for _, _, t in lines]
    ex = plain(paragraphs)
    starts, pos = [], 0
    for p in paragraphs:
        starts.append(pos)
        pos += len(p) + 2
    ends = starts[1:] + [len(ex.text)]
    n = len(lines)
    turns = tuple(Turn(i, starts[i], ends[i], sp, role, "opening" if i < 3 else "closing" if i >= n - 2 else "qa")
                  for i, (sp, role, _) in enumerate(lines))
    return Built(ex.text, turns, ex)


def calldoc(b: Built, document_id: int = 1) -> CallDoc:
    return CallDoc(document_id, b.text, TITLE, "Acme Limited", "Q2 FY27", date(2026, 10, 16), b.turns, b.role_held)


def span(text: str, snippet: str) -> tuple[int, int]:
    assert text.count(snippet) == 1, snippet
    i = text.index(snippet)
    return i, i + len(snippet)


def c(text: str, snippet: str, title: str = TITLE) -> dict:
    """The API's citation of `snippet` (found once in the text) at its true offsets, in document 0."""
    s, e = span(text, snippet)
    return cite(text, s, e, 0, title)


def lists(text: str, management=GOOD_MANAGEMENT, analysts=GOOD_ANALYSTS, unknown=(), title: str = TITLE,
          headings: tuple[str, ...] = HEADINGS) -> list[dict]:
    """A reply's text blocks: the headed lists that have items (a list given as () is left out), each claim cited at the
    snippets given."""
    return headed_lists([(heading, [(claim, [c(text, sn, title) for sn in sns]) for claim, sns in items])
                         for heading, items in zip(headings, (management, analysts, unknown), strict=False) if items])


def reply(blocks: list[dict], stop: str = "end_turn") -> Reply:
    m = Message.model_validate(message(blocks, stop=stop))
    return Reply(m.model, m.id, m.stop_reason, Usage.of(m.usage), 0, list(m.content))
