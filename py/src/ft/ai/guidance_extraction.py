"""Guidance extraction (Spec 02B §5.9, §13.5 and Spec 02B.1 C1-C4, C8, C9; job 2B-13, #176): the request for one stored earnings
call and the checks on the reply. The task that sends it, `equity.calls_ai`, is in ft/jobs/tasks/equity_calls_ai.py.

The reply is the schema's JSON (the JSON route, as `ft/ai/extraction.py`): two lists, `targets` (management's measurable
forward-looking statements) and `stated_figures` (a figure management states for a period already reported). The transcript
goes as one plain-text document block, without citations, with a marker line before each turn ("[management · Name]",
"[analyst · Name]", "[moderator]", "[unknown · Name]"), so the model reads the role our rules gave each speaker
(`equity.call_turn`, by rules in 2B-09, never by the model). Every quote in the reply is located in our own copy of the
transcript before anything is stored; the markers aren't in that copy, so a quote that ran across two turns by copying a
marker is found nowhere and is counted as across turns.

**Which list an item belongs to is set by code, from the turn its quote lies in** (Spec 02B.1 C1-C2), never by the model:
- a quote wholly inside a management turn: the normal list (`speaker_role = 'management'`); targets and stated figures;
- a **target** wholly inside one turn whose role couldn't be read: the separate list (`speaker_role = 'unknown'`), never
  scored, and only if it passes the content test (no "?", and a first-person-plural word: we, we'll, we're, we've, we'd, our,
  ours, ourselves), and only while `unattributed_targets` is true in `config/equity/guidance_metrics.yaml` (the switch, C7);
- a stated figure in a turn whose role couldn't be read: dropped, since stated figures are management's own;
- anything in an analyst's or the moderator's turn, or across two turns, or outside every turn: dropped.

**Checks per item** (a failing item is dropped and counted by reason; the rest stand; a reply that isn't the schema's JSON is
retried once with a note, then rejected, as every extraction in `ft/ai/batch.py`):
- the quote is found exactly once and is at most 400 code points (an output's content is readable through `api.ai_outputs`,
  so the quotes in it keep the private-text cap: §13.5);
- `metric_text`, `period_text` and `scope_text`, the speaker's words as the model copied them, are each at most 80 characters
  (code points, trimmed, spaces joined, as stored): a longer one drops the item (`text_too_long`), so an item carries at most
  240 characters of the speaker's words outside the 400-character quote rule (it was 600: Review 02a F1-08; #379). The rule is
  **in this check only**: structured outputs take no length limit, the schema is part of every request's input hash, and prompt
  v3 is unchanged, so no unchanged input is asked again and no stored output is touched (published outputs are frozen);
- every figure in `value`, `low` and `high` appears in the quote as written ("double-digit" for a floor of 10, and "net debt
  free" for a ceiling of 0 on net debt, are the two mappings §5.9-§5.10 name). A figure the quote doesn't hold is dropped,
  never rounded or converted;
- **units** (decided by Claude under the standing delegation, 4 Nov 2026, so every stored figure means what it says: a
  column for the scale would be a migration the card doesn't name): `inr` means rupees **in crore**, so a rupee amount stated
  in any other scale is dropped; `pct` means per cent or percentage points, so a figure in basis points is dropped; an amount
  metric (revenue, profit after tax, capex, net debt) takes `inr` or `other`, every other metric `pct`;
- the period resolves through the company's fiscal-year end month (`period_dates`), or the item is `undated`; a target's period
  ends after the call date (else it is a reported figure and is dropped), a stated figure's on or before it;
- shape: a point, floor or ceiling has one figure, a range a low below its high, a direction none and a direction word; a
  statement that gives no exact figure ("mid-teens") has none and is `vague_figure`;
- duplicates: within a call a second item with the same metric, period, basis, kind (targets) and growth basis and scope is
  marked a duplicate of the first in document order; an `unknown` target duplicates a management target of the same call
  whatever the order. A duplicate keeps its row and its citation, and isn't shown.

**Order, citations and rows.** Kept targets are ordered management first, then the separate list, each in document order; the
stated figures follow, in document order. Each kept item has one citation, numbered in that order (targets, then stated
figures), at the item's quote. `equity.guidance` and `equity.stated_figure` rows are written in that order, so a duplicate's
row refers to an earlier one. No `core.forecast` row is written (that is #177, after 18 Nov).

**The output's content** (`content`) holds the kept items (quotes at most 400 code points, each with its span and its citation
number), the drops counted by reason, and no other transcript text: never the model's raw answer, whose dropped items may hold
long quotes or an analyst's words.
"""
from __future__ import annotations

import calendar
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from ft.ai.call_summary import CallDoc, Turn
from ft.ai.citations import _numbers, locate_quote
from ft.ai.client import CONFIG, Prompt, Reply, Request, Source
from ft.ai.summaries import Cited
from ft.engines.document.text import normalize_inline

TASK = "guidance_extraction"
METRICS_PATH = CONFIG / "equity" / "guidance_metrics.yaml"
MAX_QUOTE = 400                       # code points: the private-text cap (Spec 02B §3.2, §13.5)
MAX_AS_SAID = 80                      # characters (code points) in each of metric_text, period_text and scope_text (F1-08; #379)
MAX_STATED = 12                       # stated figures per call (§13.5)
MAX_ESTIMATED_TOKENS = 60_000         # §5.5: a transcript above this is skipped with a warning naming it
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December")
# The metrics whose figure is a rupee amount (Spec 02B §5.10's "Guidance about" column); every other metric is a ratio.
AMOUNT_METRICS = frozenset({"revenue", "pat", "capex", "net_debt"})
KINDS = ("point", "range", "floor", "ceiling", "direction")
TARGET_PERIODS = ("quarter", "half_year", "nine_months", "year", "multi_year", "undated")
STATED_PERIODS = ("quarter", "half_year", "nine_months", "year")
UNITS = ("pct", "inr", "other")
BASES = ("consolidated", "standalone")
TARGET_GROWTH = ("yoy", "qoq", "cagr", "level")
STATED_GROWTH = ("yoy", "qoq", "level")

# Why an item is dropped, in the order the checks find it (key: what the (W) line says). The first four are Spec 02B.1 C3's.
DROPS: dict[str, str] = {
    "analyst_turn": "quote in an analyst's turn",
    "moderator_turn": "quote in the moderator's turn",
    "across_turns": "quote across two turns",
    "content_test": "target in a turn whose role couldn't be read isn't a first-person statement (a \"?\" or no we/our)",
    "outside_turn": "quote outside every speaker turn",
    "unknown_turn_stated": "stated figure in a turn whose role couldn't be read",
    "unknown_turn_off": "target in a turn whose role couldn't be read, with unattributed_targets off",
    "not_found": "quote not found exactly once in the transcript",
    "too_long": f"quote over {MAX_QUOTE} code points",
    "malformed": "item isn't the schema's object",
    "metric": "metric isn't in the map",
    "text_too_long": f"metric, period or scope text over {MAX_AS_SAID} characters",
    "shape": "kind, figures, unit or direction don't fit together",
    "figure_not_in_quote": "a figure isn't in the quote as written",
    "unit_scale": "figure in basis points, or a rupee amount not in crore",
    "unit_metric": "unit doesn't fit the metric",
    "period_invalid": "period doesn't resolve to dates",
    "period_over": "target's period ended on or before the call date",
    "period_after_call": "stated figure's period ends after the call date",
    "over_limit": f"more than {MAX_STATED} stated figures",
}


# ---------------------------------------------------------------------------------------------- the metric map
@dataclass(frozen=True)
class Metrics:
    """`config/equity/guidance_metrics.yaml`: the keys the extraction may use, their labels, and the switch (C7)."""
    map_version: int
    unattributed_targets: bool
    labels: dict[str, str]


def load_metrics(path: Path = METRICS_PATH) -> Metrics:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    labels = {str(k): str(v) for k, v in raw["metrics"].items()}
    if "other" not in labels or not all(re.fullmatch(r"[a-z0-9_]+", k) for k in labels):
        raise ValueError(f"{path.name}: the metric keys must be [a-z0-9_]+ and include 'other'")
    if not isinstance(raw["unattributed_targets"], bool):
        raise TypeError(f"{path.name}: unattributed_targets must be true or false")
    return Metrics(int(raw["map_version"]), raw["unattributed_targets"], labels)


# ---------------------------------------------------------------------------------------------- requests
def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "date not recorded"


def _note(note: str | None) -> str | None:
    """The retry's note, in its own last block (so the retry can read the first attempt's cached prefix)."""
    if not note:
        return None
    return ("Your previous answer to this request couldn't be published: " + note
            + "\nAnswer again as the schema's JSON, following every rule. Copy every quote exactly from the transcript, "
              "character for character, with no ellipsis and no words added or changed.")


def marker(t: Turn) -> str:
    """The line before a turn: who speaks, by the role our rules read for the speaker."""
    return f"[{t.role}]" if t.role == "moderator" or not t.speaker else f"[{t.role} · {t.speaker}]"


def marked_text(call: CallDoc) -> str:
    """The transcript with a marker line before each turn; text outside any turn stays as it is. The model's quotes are
    located in the transcript without the markers (`check`), so a quote can't include one."""
    out, pos = [], 0
    for t in sorted(call.turns, key=lambda t: t.turn_no):
        if t.char_start > pos:
            out.append(call.text[pos:t.char_start])
        out.append(marker(t) + "\n" + call.text[max(t.char_start, pos):t.char_end])
        pos = max(pos, t.char_end)
    out.append(call.text[pos:])
    return "".join(out)


# A prompt version's `max_tokens` where it isn't the task's (16,000; client.TASKS). v5 (#511): its first dev run stopped at
# 16,000 on 68 of 294 calls (the longest: v1's published replies on those calls already reached about 11,400 tokens at the median),
# so v5 has twice the headroom. Only the ceiling changes; the reply is billed for the tokens it uses.
MAX_TOKENS_OF = {"v5": 32_000}


def guidance_request(call: CallDoc, prompt: Prompt, schema: dict, *, note: str | None = None) -> Request:
    if call.fy_end_month is None or not 1 <= call.fy_end_month <= 12:
        raise ValueError(f"{call.title}: the fiscal-year end month isn't known")
    quarter = f" · {call.fiscal_label}" if call.fiscal_label else ""
    src = Source(call.document_id, marked_text(call), call.title,
                 context=f"{call.company} · Earnings call{quarter} · {_when(call.day)}")
    instruction = (f"Extract the targets and the stated figures from the earnings call above ({call.title}), following the "
                   f"rules. {call.company}'s fiscal year ends in {MONTHS[call.fy_end_month - 1]}. The call date is "
                   f"{_when(call.day)}.")
    return Request(TASK, prompt, (src,), instruction, schema=schema, note=_note(note),
                   max_tokens=MAX_TOKENS_OF.get(prompt.version))


# ---------------------------------------------------------------------------------------------- periods
def _add_months(d: date, n: int) -> date:
    """`d` (a first of a month) moved by `n` months."""
    k = d.year * 12 + d.month - 1 + n
    return date(k // 12, k % 12 + 1, 1)


def fiscal_year_dates(fiscal_year: int, end_month: int) -> tuple[date, date]:
    """The fiscal year named for the calendar year it ends in: FY27 with a March year end is 1 Apr 2026 to 31 Mar 2027."""
    end = date(fiscal_year, end_month, calendar.monthrange(fiscal_year, end_month)[1])
    return _add_months(date(fiscal_year, end_month, 1), -11), end


def period_dates(kind: str, fiscal_year: int, part: int | None, start_fiscal_year: int | None,
                 end_month: int) -> tuple[date, date] | None:
    """The dates of a period, or None if it can't be resolved: a quarter or half needs its part, a nine months or a year none,
    a multi-year period its first and its last fiscal year."""
    first, last = fiscal_year_dates(fiscal_year, end_month)
    if kind == "quarter" and part in (1, 2, 3, 4):
        s = _add_months(first, 3 * (part - 1))
        return s, _add_months(s, 3) - timedelta(days=1)
    if kind == "half_year" and part in (1, 2):
        s = _add_months(first, 6 * (part - 1))
        return s, _add_months(s, 6) - timedelta(days=1)
    if kind == "nine_months" and part is None:
        return first, _add_months(first, 9) - timedelta(days=1)
    if kind == "year" and part is None:
        return first, last
    if kind == "multi_year" and part is None and start_fiscal_year is not None and start_fiscal_year < fiscal_year:
        return fiscal_year_dates(start_fiscal_year, end_month)[0], last
    return None


# ---------------------------------------------------------------------------------------------- reading a quote
MARKER = re.compile(r"\[(?:management|analyst|moderator|unknown)(?: · [^\]]*)?\]")
# C3's content test: a first-person-plural word as a whole word, in any case, with a straight or a curly apostrophe.
FIRST_PERSON = re.compile(r"(?<![\w'’])(?:we(?:['’](?:ll|re|ve|d))?|ours?|ourselves)(?!\w)", re.IGNORECASE)
DOUBLE_DIGIT = re.compile(r"\bdouble[\s-]*digits?\b", re.IGNORECASE)
NET_DEBT_FREE = re.compile(r"\bnet[\s-]+debt[\s-]*free\b", re.IGNORECASE)
CRORE = re.compile(r"\bcrores?\b|\bcr\b", re.IGNORECASE)
OTHER_SCALE = re.compile(r"\b(?:lakhs?|millions?|billions?|trillions?|thousands?|mn|bn)\b", re.IGNORECASE)
BASIS_POINTS = re.compile(r"\bbps\b|\bbp\b|\bbasis[\s-]+points?\b", re.IGNORECASE)


def passes_content_test(quote: str) -> bool:
    """A statement in a turn whose role couldn't be read must be a first-person statement: no question mark, and a
    first-person-plural word."""
    return "?" not in quote and FIRST_PERSON.search(quote) is not None


def _dec(v) -> Decimal | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _fmt(d: Decimal | None) -> str | None:
    return None if d is None else format(d.normalize(), "f")


def _text(v, n: int = 200) -> str | None:
    """A short text field as stored: trimmed, or None if empty or not a string."""
    if not isinstance(v, str) or not v.strip():
        return None
    return " ".join(v.split())[:n]


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _norm(s: str | None) -> str | None:
    return None if s is None else " ".join(s.lower().split())


# ---------------------------------------------------------------------------------------------- checking an answer
@dataclass
class Checked:
    answer: dict | None
    targets: list[dict] = field(default_factory=list)       # kept, in the order they're stored (management first)
    stated: list[dict] = field(default_factory=list)        # kept, in document order
    citations: list[tuple[str, Cited]] = field(default_factory=list)   # (claim_path, span), numbered 1.. in this order
    problems: list[str] = field(default_factory=list)       # a reply that isn't the schema's JSON: retried once, rejected
    drops: Counter = field(default_factory=Counter)         # items dropped, by `DROPS` key
    mapping: Counter = field(default_factory=Counter)       # quotes by how they were found (citation_integrity)
    hold: str | None = None                                 # never set: an extraction here is published or empty

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def empty(self) -> bool:
        """No item in any list: the output stays a draft (`no_targets`), with no citation to publish it on."""
        return not self.targets and not self.stated

    @property
    def management_targets(self) -> int:
        """The management targets shown: duplicates out (`equity.earnings_call.guidance_targets`)."""
        return sum(1 for t in self.targets if t["speaker_role"] == "management" and t["duplicate_of"] is None)

    def reasons(self) -> list[str]:
        return list(self.problems)

    def note(self) -> str:
        return "; ".join(self.problems) + "."


@dataclass(frozen=True)
class Where:
    start: int
    end: int
    turn: Turn
    quote: str                        # as our transcript has it


def _place(raw: dict, call: CallDoc, c: Checked, metrics: Metrics, *, target: bool) -> Where | None:
    """Locate an item's quote in the transcript and in its turn, or count why it can't be used."""
    quote = raw.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        c.drops["malformed"] += 1
        return None
    if MARKER.search(quote):
        c.drops["across_turns"] += 1                         # it copied a marker line: it ran from one turn into the next
        return None
    span = locate_quote(call.text, normalize_inline(quote))
    c.mapping["search" if span.verified else "failed"] += 1
    if not span.verified:
        c.drops["not_found"] += 1
        return None
    if span.end - span.start > MAX_QUOTE:
        c.drops["too_long"] += 1
        return None
    hit = [t for t in call.turns if t.char_start < span.end and span.start < t.char_end]
    if not hit:
        c.drops["outside_turn"] += 1
        return None
    turn = hit[0]
    if len(hit) > 1 or span.start < turn.char_start or span.end > turn.char_end:
        c.drops["across_turns"] += 1
        return None
    quote = call.text[span.start:span.end]
    if turn.role == "analyst":
        c.drops["analyst_turn"] += 1
    elif turn.role == "moderator":
        c.drops["moderator_turn"] += 1
    elif turn.role == "unknown" and not target:
        c.drops["unknown_turn_stated"] += 1
    elif turn.role == "unknown" and not metrics.unattributed_targets:
        c.drops["unknown_turn_off"] += 1
    elif turn.role == "unknown" and not passes_content_test(quote):
        c.drops["content_test"] += 1
    elif turn.role in ("management", "unknown"):
        return Where(span.start, span.end, turn, quote)
    else:
        c.drops["malformed"] += 1                            # a role the table doesn't have
    return None


def figures_ok(quote: str, metric: str, kind: str, value, low, high) -> bool:
    """Every figure is in the quote as written ("double-digit" for a floor of 10; "net debt free" for a net-debt ceiling of
    0). Never rounded, converted or added."""
    have = set(_numbers(quote))
    for v in (value, low, high):
        if v is None or v in have:
            continue
        if v == 10 and v == value and kind == "floor" and DOUBLE_DIGIT.search(quote):
            continue
        if v == 0 and v == value and kind == "ceiling" and metric == "net_debt" and NET_DEBT_FREE.search(quote):
            continue
        return False
    return True


def _unit_problem(quote: str, metric: str, unit: str | None, value, kind: str) -> str | None:
    """The drop key if the unit doesn't fit the metric or the quote's scale, else None."""
    if unit is None or metric == "other":
        return None
    if unit not in (("inr", "other") if metric in AMOUNT_METRICS else ("pct",)):
        return "unit_metric"
    if unit == "pct" and BASIS_POINTS.search(quote):
        return "unit_scale"
    if (unit == "inr" and not (value == 0 and NET_DEBT_FREE.search(quote))
            and (not CRORE.search(quote) or OTHER_SCALE.search(quote))):
        return "unit_scale"
    return None


def _common(raw: dict, where: Where, c: Checked, metrics: Metrics, *, target: bool) -> dict | None:
    """The fields both lists share, checked; None (counted) if one fails."""
    metric, metric_text = raw.get("metric"), _text(raw.get("metric_text"))
    allowed = metrics.labels if target else {k: v for k, v in metrics.labels.items() if k != "other"}
    if not isinstance(metric, str) or metric not in allowed or metric_text is None:
        c.drops["metric" if isinstance(metric, str) and metric_text else "malformed"] += 1
        return None
    unit = raw.get("unit")
    basis, growth = raw.get("basis"), raw.get("growth_basis")
    if (unit is not None and unit not in UNITS) or (basis is not None and basis not in BASES) or (
            growth is not None and growth not in (TARGET_GROWTH if target else STATED_GROWTH)):
        c.drops["malformed"] += 1
        return None
    period_text, scope_text = _text(raw.get("period_text")), _text(raw.get("scope_text"))
    # The three "as said" fields are the speaker's words outside the quote rule, so each is held to MAX_AS_SAID (F1-08; #379).
    # An item with a longer one is dropped, never cut: a cut phrase would be shown as if it were whole. `_text`'s 200 only
    # stops a huge string being carried; the length is checked on the value as it would be stored (trimmed, spaces joined).
    if any(len(t) > MAX_AS_SAID for t in (metric_text, period_text, scope_text) if t is not None):
        c.drops["text_too_long"] += 1
        return None
    return {"quote": where.quote, "span": [where.start, where.end], "metric": metric, "metric_label": allowed[metric],
            "metric_text": metric_text, "unit": unit, "period_text": period_text, "basis": basis,
            "growth_basis": growth, "scope_text": scope_text}


def _plausible(fy: int, call: CallDoc) -> bool:
    return call.day.year - 3 <= fy <= call.day.year + 10


def _target(raw, call: CallDoc, c: Checked, metrics: Metrics) -> dict | None:
    if not isinstance(raw, dict):
        c.drops["malformed"] += 1
        return None
    where = _place(raw, call, c, metrics, target=True)
    if where is None:
        return None
    item = _common(raw, where, c, metrics, target=True)
    if item is None:
        return None
    kind, direction, comparison = raw.get("kind"), raw.get("direction"), raw.get("comparison")
    value, low, high = _dec(raw.get("value")), _dec(raw.get("low")), _dec(raw.get("high"))
    if kind not in KINDS or comparison not in (None, "previous_period", "year_ago") or raw.get("period_kind") not in TARGET_PERIODS:
        c.drops["malformed"] += 1
        return None
    figures = [v for v in (value, low, high) if v is not None]
    shape_ok = {
        "direction": not figures and direction in ("increase", "decrease", "maintain"),
        "range": (not figures) or (value is None and low is not None and high is not None and low <= high),
        "point": (not figures) or (value is not None and low is None and high is None),
        "floor": (not figures) or (value is not None and low is None and high is None),
        "ceiling": (not figures) or (value is not None and low is None and high is None),
    }[kind]
    if not shape_ok or (figures and item["unit"] is None):
        c.drops["shape"] += 1
        return None
    if not figures:
        item["unit"] = None                                  # a unit with no figure means nothing
    if not figures_ok(where.quote, item["metric"], kind, value, low, high):
        c.drops["figure_not_in_quote"] += 1
        return None
    if (why := _unit_problem(where.quote, item["metric"], item["unit"], value, kind)) is not None:
        c.drops[why] += 1
        return None
    # The period: dated through the fiscal-year end month, or undated. A period the model couldn't pin to a fiscal year is
    # undated (§5.9: "else `undated`"), never guessed; an inconsistent or implausible one is dropped.
    pk, fy, part, start_fy = raw["period_kind"], _int(raw.get("fiscal_year")), _int(raw.get("part")), _int(
        raw.get("start_fiscal_year"))
    dates = None
    if pk != "undated" and fy is None:
        pk = "undated"
    if pk != "undated":
        if not _plausible(fy, call) or (start_fy is not None and not _plausible(start_fy, call)):
            dates = None
        else:
            dates = period_dates(pk, fy, part, start_fy, call.fy_end_month)
        if dates is None:
            c.drops["period_invalid"] += 1
            return None
        if dates[1] <= call.day:
            c.drops["period_over"] += 1
            return None
    item.update({"kind": kind, "value": _fmt(value), "low": _fmt(low), "high": _fmt(high),
                 "direction": direction if kind == "direction" else None, "comparison": comparison,
                 "period_kind": pk, "target_start": dates[0].isoformat() if dates else None,
                 "target_end": dates[1].isoformat() if dates else None, "speaker_role": where.turn.role,
                 "duplicate_of": None, "not_scored_reason": None, "citation_no": None})
    return item


def _stated(raw, call: CallDoc, c: Checked, metrics: Metrics) -> dict | None:
    if not isinstance(raw, dict):
        c.drops["malformed"] += 1
        return None
    where = _place(raw, call, c, metrics, target=False)
    if where is None:
        return None
    item = _common(raw, where, c, metrics, target=False)
    if item is None:
        return None
    value, pk, fy, part = _dec(raw.get("value")), raw.get("period_kind"), _int(raw.get("fiscal_year")), _int(raw.get("part"))
    if value is None or item["unit"] is None or pk not in STATED_PERIODS or fy is None:
        c.drops["shape" if value is None or item["unit"] is None else "malformed"] += 1
        return None
    if not figures_ok(where.quote, item["metric"], "point", value, None, None):
        c.drops["figure_not_in_quote"] += 1
        return None
    if (why := _unit_problem(where.quote, item["metric"], item["unit"], value, "point")) is not None:
        c.drops[why] += 1
        return None
    dates = period_dates(pk, fy, part, None, call.fy_end_month) if _plausible(fy, call) else None
    if dates is None:
        c.drops["period_invalid"] += 1
        return None
    if dates[1] > call.day:
        c.drops["period_after_call"] += 1
        return None
    item.update({"value": _fmt(value), "period_kind": pk, "period_start": dates[0].isoformat(),
                 "period_end": dates[1].isoformat(), "duplicate_of": None, "citation_no": None})
    return item


def _target_key(t: dict) -> tuple:
    metric = t["metric"] if t["metric"] != "other" else ("other", _norm(t["metric_text"]))
    period = (t["target_start"], t["target_end"]) if t["period_kind"] != "undated" else ("undated", _norm(t["period_text"]))
    return metric, period, t["basis"], t["kind"], t["growth_basis"], _norm(t["scope_text"])


def _stated_key(s: dict) -> tuple:
    return s["metric"], s["period_start"], s["period_end"], s["basis"], s["growth_basis"], _norm(s["scope_text"])


def not_scored_reason(t: dict) -> str | None:
    """The reason the statement alone decides (§5.10's list, in this order; `duplicate` is set where duplicates are found)."""
    if t["metric"] == "other":
        return "no_filed_figure"
    if t["unit"] == "other" and t["metric"] in AMOUNT_METRICS:
        return "currency"
    if t["scope_text"] is not None:
        return "scope"
    if t["period_kind"] == "undated":
        return "undated"
    if t["kind"] != "direction" and t["value"] is None and t["low"] is None:
        return "vague_figure"
    return None


def check(answer, call: CallDoc, metrics: Metrics) -> Checked:
    """Check a reply against our copy of the transcript and its stored turns."""
    c = Checked(answer if isinstance(answer, dict) else None)
    if (not isinstance(answer, dict) or not isinstance(answer.get("targets"), list)
            or not isinstance(answer.get("stated_figures"), list)):
        c.problems.append("the reply isn't the schema's JSON ({targets: [...], stated_figures: [...]})")
        return c
    if call.day is None or call.fy_end_month is None:
        raise ValueError(f"{call.title}: the call date and the fiscal-year end month are needed to check a reply")
    kept = [t for raw in answer["targets"] if (t := _target(raw, call, c, metrics)) is not None]
    stated = [s for raw in answer["stated_figures"] if (s := _stated(raw, call, c, metrics)) is not None]
    if len(stated) > MAX_STATED:
        c.drops["over_limit"] += len(stated) - MAX_STATED
        stated = stated[:MAX_STATED]
    # Management's list first, then the separate list, each in document order: a duplicate then refers to an earlier row.
    kept.sort(key=lambda t: (t["speaker_role"] != "management", t["span"][0]))
    stated.sort(key=lambda s: s["span"][0])
    first: dict[tuple, int] = {}
    for i, t in enumerate(kept):
        key = _target_key(t)
        if key in first:
            t["duplicate_of"], t["not_scored_reason"] = first[key], "duplicate"
        else:
            first[key] = i
            t["not_scored_reason"] = not_scored_reason(t)
    first = {}
    for i, s in enumerate(stated):
        key = _stated_key(s)
        if key in first:
            s["duplicate_of"] = first[key]
        else:
            first[key] = i
    c.targets, c.stated = kept, stated
    n = 0
    for i, t in enumerate(kept):
        n += 1
        t["citation_no"] = n
        c.citations.append((f"/targets/{i}", Cited(call.document_id, *t["span"], "search")))
    for i, s in enumerate(stated):
        n += 1
        s["citation_no"] = n
        c.citations.append((f"/stated_figures/{i}", Cited(call.document_id, *s["span"], "search")))
    return c


def answer_of(reply: Reply):
    """The reply's JSON, or None if it isn't JSON."""
    try:
        return reply.json()
    except (ValueError, TypeError):
        return None


def content(c: Checked, *, attempt: int, reply: Reply | None, metrics: Metrics) -> dict:
    """core.ai_output.content: the kept items (their quotes are at most 400 code points), the drops counted by reason and no
    other transcript text. A reply with no item in any list is marked `no_targets` (its output stays a draft)."""
    doc: dict = {"targets": c.targets, "stated_figures": c.stated, "dropped": dict(sorted(c.drops.items())),
                 "attempt": attempt, "stop_reason": reply.stop_reason if reply is not None else None,
                 "quote_mapping": dict(sorted(c.mapping.items())), "map_version": metrics.map_version,
                 "unattributed_targets": metrics.unattributed_targets}
    if c.problems:
        doc["problems"] = list(c.problems)
    elif c.empty:
        doc["no_targets"] = True
    return doc


# The stable phrases `check` writes into a reason, by the accuracy page's category (ft/quality/reasons.py reads this table;
# a test runs `check` on a crafted bad reply and asserts it lands where it says). Item drops aren't reasons (they're counted).
REASON_PHRASES: dict[str, tuple[str, ...]] = {
    "format": (r"the reply isn't the schema's JSON \(\{targets",),
}
