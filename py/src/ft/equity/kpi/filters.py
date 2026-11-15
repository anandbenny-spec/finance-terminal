"""The post-filters, sign convention and tile check for the KPI-table pilot (Data Audit 10 §8, finding 1; Plan 05 §1.3,
DD-04 build step 3). Pure: an extracted row and its page text in, a verdict out. Nothing here deletes a row — a filtered
row is kept with its reason (migration 0047's `equity.kpi_value.filter_status`), so DD-05's flag logic and the judge see
why it was set aside.

Three in four of Data Audit 10's false positives were kinds the prompt already excludes (statement-total growth,
half-year and earlier periods, quotes, "About" boilerplate, thresholds). These filters are the safety net that marks them,
plus a sign convention per measure direction and a check that an infographic summary tile's figure also appears in a table.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

# ----------------------------------------------------------------------------------------- filter reasons (closed list)
STATEMENT_TOTAL = "statement_total"
OTHER_PERIOD = "other_period"
QUOTE = "quote"
ABOUT_PAGE = "about_page"
THRESHOLD = "threshold"
TILE_UNCONFIRMED = "tile_unconfirmed"
KEPT = "kept"
FILTERED = "filtered"

# Measures that are a change (a sign matters and follows the direction word): basis points and a percent change.
CHANGE_MEASURES = frozenset({"bps", "percent_change"})

# Other-period words (reading note, rule 3): a label about a period other than the quarter is set aside.
_PERIOD_WORDS = re.compile(
    r"\b(year[-\s]?ago|yoy|y-o-y|previous quarter|prior quarter|preceding quarter|sequential quarter|"
    r"half[-\s]?years?|first half|second half|nine[-\s]?months?|9[-\s]?months?|9m|h1|h2|"
    r"year[-\s]?to[-\s]?date|ytd|full[-\s]?year|fy\s?ended|twelve months ended|financial year ended)\b",
    re.IGNORECASE,
)
# Month-end total-deposit growth and other statement-total growth (rule 1, reading note 6). Average and split balances
# are KPI metrics (the banks' sentence), so they are NOT matched here.
_STATEMENT_GROWTH = re.compile(
    r"\b(total (deposits?|advances?|assets?|borrowings?|business)|net\s?worth|balance\s?sheet|"
    r"(net )?(revenue|income|profit|pat|pbt|ebitda|ebit)|earnings per share|eps)\b.*\b(growth|grew|rose|increase[d]?|"
    r"decline[d]?|de-?grew|yoy|qoq|y-o-y|q-o-q)\b",
    re.IGNORECASE,
)
_AVERAGE_OR_SPLIT = re.compile(
    r"\b(average|casa|savings|current account|term deposit|segment|retail|wholesale|corporate|sme|domestic|international)\b",
    re.IGNORECASE,
)
# A threshold phrase with no value (reading note 4): "crossed", "surpassed", "more than <n>" used as a milestone.
_THRESHOLD = re.compile(r"\b(cross(ed|ing)?|surpass(ed)?|breach(ed)?|milestone|touched the mark)\b", re.IGNORECASE)
# "About <Company>" boilerplate page (reading note 5).
_ABOUT = re.compile(r"^\s*about\s+[A-Z0-9]", re.IGNORECASE | re.MULTILINE)
_RISE = re.compile(r"\b(improv\w*|ros\w*|rais\w*|grew|grow\w*|increas\w*|higher|up|expand\w*|gain\w*|add\w*)\b", re.IGNORECASE)
_FALL = re.compile(r"\b(declin\w*|fell|fall\w*|drop\w*|decreas\w*|lower|down|contract\w*|reduc\w*|shrank|shrunk)\b", re.IGNORECASE)
_QUOTE_CHARS = "\"“”‘’'"


@dataclass(frozen=True)
class Row:
    """One extracted metric, as the model returned it (ft/equity/kpi/extraction.py normalises it to this)."""
    label: str
    member: str | None
    measure: str
    value: Decimal | None
    unit: str | None
    page: int


@dataclass(frozen=True)
class Verdict:
    status: str                 # 'kept' | 'filtered'
    reason: str | None = None   # a filter reason when filtered


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def is_about_page(page_text: str) -> bool:
    """A page that is 'About <Company>' boilerplate (reading note 5): every KPI-looking figure on it is set aside."""
    return bool(_ABOUT.search(_norm(page_text)))


def in_quote(label: str, page_text: str) -> bool:
    """True when the label sits inside a quotation on the page (reading note 5: figures inside quotations don't count).
    A quote is text between matched quotation marks that contains the label."""
    text = _norm(page_text)
    needle = _norm(label).strip()
    if not needle or needle not in text:
        return False
    # the spans between an opening and the next quotation mark
    positions = [i for i, c in enumerate(text) if c in _QUOTE_CHARS]
    for a, b in zip(positions[0::2], positions[1::2]):
        if needle in text[a + 1:b]:
            return True
    return False


def classify(row: Row, page_text: str) -> Verdict:
    """Mark one row kept or filtered, by the clear exclusions the prompt already lists (the safety net). The order is:
    an About page sets every row aside; then a quotation; then another period; then a statement-total growth line; then a
    threshold with no value. Anything else is kept (the tile check, which needs the whole document, runs afterwards)."""
    label = row.label or ""
    hay = f"{label} {row.member or ''}"
    if is_about_page(page_text):
        return Verdict(FILTERED, ABOUT_PAGE)
    if in_quote(label, page_text):
        return Verdict(FILTERED, QUOTE)
    if _PERIOD_WORDS.search(hay):
        return Verdict(FILTERED, OTHER_PERIOD)
    if _STATEMENT_GROWTH.search(hay) and not _AVERAGE_OR_SPLIT.search(hay):
        return Verdict(FILTERED, STATEMENT_TOTAL)
    if row.value is None and _THRESHOLD.search(hay):
        return Verdict(FILTERED, THRESHOLD)
    return Verdict(KEPT)


def signed_value(row: Row) -> Decimal | None:
    """The value with the sign a change metric's direction word implies (Data Audit 10 §8, the five sign errors:
    'improved by 13 bps' on a coverage ratio is a rise, a down arrow is a fall). Only a `bps` or `percent_change` row is
    touched, and only when the label carries a clear, unambiguous direction word; otherwise the model's value stands."""
    if row.value is None or row.measure not in CHANGE_MEASURES:
        return row.value
    text = _norm(row.label or "")
    rise, fall = bool(_RISE.search(text)), bool(_FALL.search(text))
    if rise == fall:                 # neither word, or both: don't guess
        return row.value
    magnitude = abs(row.value)
    return magnitude if rise else -magnitude


def _value_key(measure: str, value: Decimal | None) -> tuple[str, Decimal] | None:
    """A (measure, rounded value) key for the tile cross-check: a tile figure matches a table figure of the same measure
    and the same value (rounded to the measure's natural precision)."""
    if value is None:
        return None
    q = Decimal(1) if measure in ("count", "people", "volume") else Decimal("0.01")
    return measure, value.quantize(q)


def reconcile_tiles(rows: list[Row], verdicts: dict[int, Verdict], tile_pages: frozenset[int]) -> dict[int, Verdict]:
    """Keep a summary-tile figure only when the same figure appears in a table of the same document (Data Audit 10 §8,
    the 18 infographic misreads). `tile_pages` are the pages the task marked as infographic/summary pages; a kept row on
    such a page is set aside (`tile_unconfirmed`) unless a kept row with the same measure and value sits on a non-tile
    page. With no tile pages marked, nothing changes. `rows` is indexed by position; `verdicts` maps that index to the
    `classify` result. Returns a new verdict map."""
    if not tile_pages:
        return dict(verdicts)
    table_keys = {
        _value_key(r.measure, r.value)
        for i, r in enumerate(rows)
        if r.page not in tile_pages and verdicts[i].status == KEPT and _value_key(r.measure, r.value) is not None
    }
    out = dict(verdicts)
    for i, r in enumerate(rows):
        if r.page in tile_pages and out[i].status == KEPT:
            key = _value_key(r.measure, r.value)
            if key is None or key not in table_keys:
                out[i] = Verdict(FILTERED, TILE_UNCONFIRMED)
    return out
