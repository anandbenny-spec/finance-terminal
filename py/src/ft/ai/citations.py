"""Citation verification and number grounding (prototype)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass
class Span:
    start: int
    end: int
    verified: bool
    how: str          # 'offsets' (API indices matched), 'search' (quote found once), 'failed'


def _ws_map(text: str):
    """Collapse runs of whitespace to one space; return the collapsed text and a map back to original offsets."""
    out, back, prev_space = [], [], False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" "); back.append(i); prev_space = True
        else:
            out.append(ch); back.append(i); prev_space = False
    back.append(len(text))
    return "".join(out), back


def locate_quote(text: str, quote: str) -> Span:
    """Find a model-supplied quote in the document text. Verified only if it occurs exactly once
    (after collapsing whitespace); an ambiguous or missing quote is not evidence."""
    q = " ".join(quote.split())
    if not q:
        return Span(0, 0, False, "failed")
    flat, back = _ws_map(text)
    first = flat.find(q)
    if first < 0 or flat.find(q, first + 1) >= 0:
        return Span(0, 0, False, "failed")
    return Span(back[first], back[first + len(q) - 1] + 1, True, "search")


def map_api_citation(doc_text: str, seg_start: int, start_idx: int, end_idx: int, cited_text: str) -> Span:
    """Map a native citation (character indices inside the segment we sent) to document offsets.
    Trust the indices only if the text there is exactly the cited text; otherwise fall back to searching
    for the cited text. Either way the stored span is checked against our own copy of the document."""
    s, e = seg_start + start_idx, seg_start + end_idx
    if e <= s or not cited_text.strip():
        return Span(0, 0, False, "failed")               # an empty range or quote is no evidence (B7 review)
    if doc_text[s:e] == cited_text:
        return Span(s, e, True, "offsets")
    return locate_quote(doc_text, cited_text)


# A number as written in text. Indian grouping (1,23,456) is tried before Western grouping (123,456), which
# would split it into 1 and 23,456; then plain digits. A written sign (-, −, +) is kept, so a signed figure is
# compared with its sign (Review 01, F4).
_NUM = re.compile(r"(?<![\w.])[-−+]?(?:\d{1,2}(?:,\d{2})+,\d{3}|\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_SIGNS = ("-", "−", "+")
# A tenor ("10-year yield", "3-month bill") names an instrument, not a figure from the result.
_TENOR = re.compile(r"-(?:year|yr|month|week|day)s?\b", re.IGNORECASE)


def _dec(token: str) -> Decimal:
    return Decimal(token.replace(",", "").replace("−", "-"))


def _numbers(s: str) -> list[Decimal]:
    out = []
    for m in _NUM.findall(s):
        try:
            out.append(_dec(m))
        except InvalidOperation:
            pass
    return out


def numbers_grounded(summary: str, rows: list[dict], sql: str = "") -> tuple[bool, list[str]]:
    """Every number in a one-line answer summary must come from the result rows (allowing the rounding the
    summary shows), be the row count, appear in a returned date, or be a literal in the guarded query itself
    (a year in its filter). A number written with a sign must match a cell with that sign; an unsigned one
    ("cut by 25 bps") may match either sign. Tenors ("10-year") aren't figures and are skipped. Otherwise the
    route falls back to a plain 'N rows' summary rather than show a number the database didn't return."""
    cells: list[Decimal] = []
    texts: list[str] = []
    for r in rows:
        for v in r.values():
            if v is None:
                continue
            if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
                cells.append(Decimal(str(v)))
            else:
                texts.append(str(v))
                cells.extend(_numbers(str(v)))
    allowed_extra = {Decimal(len(rows)), *_numbers(sql)}
    bad = []
    for m in _NUM.finditer(summary):
        token = m.group()
        if _TENOR.match(summary, m.end()):
            continue
        n = _dec(token)
        places = -n.as_tuple().exponent if n.as_tuple().exponent < 0 else 0
        tol = Decimal(1).scaleb(-places) / 2
        if token.startswith(_SIGNS):
            if n in allowed_extra or any(abs(c - n) <= tol for c in cells):
                continue
        elif n in {abs(x) for x in allowed_extra} or any(abs(abs(c) - n) <= tol for c in cells):
            continue
        bad.append(token)
    return (not bad, bad)
