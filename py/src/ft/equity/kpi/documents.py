"""Finding the pilot document per company and quarter, and cutting it from its bundle (DD-04, #512; Plan 05 §1.3, D7;
Data Audit 10 §1, §2). Pure: the curated rules (`config/equity/kpi_documents.yaml`), the quarter window, the fiscal-label
→ period-end map, the announcement match and the page cut. The network (fetching the announcements and the file) and the
PDF reading are the task's; this module decides which filing and which pages.

The 7 companies are Data Audit 10's eight IT and bank names less Wipro, which isn't in the company master. The window is
Q3 FY25 to Q2 FY27, with TCS from Q2 FY25, so each flagged quarter (Q3 FY26 to Q2 FY27, and TCS's Q2 FY26) has its
previous four documents.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parents[4] / "config" / "equity" / "kpi_documents.yaml"
PARSER_VERSION = 1

# The 7 pilot companies (slugs), and the window. TCS alone starts a quarter earlier (DA-10's known case needs Q2 FY25).
COMPANIES = ("tcs", "infy", "hcltech", "hdfcbank", "icicibank", "sbin", "axisbank")
WINDOW_FIRST = "Q3 FY25"
WINDOW_LAST = "Q2 FY27"
TCS_FIRST = "Q2 FY25"
RESULTS_WINDOW_DAYS = 120            # a results document is filed within this many days after the quarter end
_LABEL = re.compile(r"Q([1-4])\s*FY\s*(\d{2})", re.IGNORECASE)


@dataclass(frozen=True)
class Cut:
    start: str | None
    end: str | None


@dataclass(frozen=True)
class Rule:
    slug: str
    document_class: str
    source: str                      # 'own_filing' | 'bundle'
    subject: re.Pattern
    cut: Cut | None
    image_quarters: frozenset[str]
    overrides: dict[str, dict] = field(default_factory=dict)

    def for_quarter(self, fiscal_label: str) -> Rule:
        """This rule with any per-quarter override applied (HDFC Bank's separate Q3 FY25 'Press Release' filing)."""
        ov = self.overrides.get(fiscal_label)
        if not ov:
            return self
        cut = self.cut
        if "cut" in ov:
            cut = Cut(ov["cut"].get("start"), ov["cut"].get("end")) if ov["cut"] else None
        return Rule(self.slug, ov.get("document_class", self.document_class), ov.get("source", self.source),
                    re.compile(ov["subject"]) if "subject" in ov else self.subject, cut, self.image_quarters, {})

    def reads_as_image(self, fiscal_label: str) -> bool:
        return fiscal_label in self.image_quarters


def load_rules(path: Path = RULES_PATH) -> dict[str, Rule]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, Rule] = {}
    for slug, r in (doc.get("companies") or {}).items():
        cut = Cut(r["cut"].get("start"), r["cut"].get("end")) if r.get("cut") else None
        out[slug] = Rule(slug, r["document_class"], r["source"], re.compile(r["subject"]), cut,
                         frozenset(r.get("image_quarters") or []), dict(r.get("overrides") or {}))
    return out


# ---------------------------------------------------------------------------------------------- the quarter window
def period_end(fiscal_label: str) -> date:
    """The quarter end for 'Q2 FY27' (Indian fiscal year, March end): Q1→Jun, Q2→Sep, Q3→Dec of the year before FY;
    Q4→Mar of FY."""
    m = _LABEL.fullmatch(fiscal_label.strip())
    if not m:
        raise ValueError(f"not a fiscal-quarter label: {fiscal_label!r}")
    q, fy = int(m.group(1)), 2000 + int(m.group(2))
    month, year = {1: (6, fy - 1), 2: (9, fy - 1), 3: (12, fy - 1), 4: (3, fy)}[q]
    return date(year, month, calendar.monthrange(year, month)[1])


def _label(q: int, fy: int) -> str:
    return f"Q{q} FY{fy % 100:02d}"


def _step_back(label: str) -> str:
    m = _LABEL.fullmatch(label.strip())
    q, fy = int(m.group(1)), 2000 + int(m.group(2))
    return _label(q - 1, fy) if q > 1 else _label(4, fy - 1)


def quarters(slug: str) -> list[str]:
    """The pilot quarters for a company, oldest first: WINDOW_FIRST (TCS_FIRST for TCS) to WINDOW_LAST."""
    first = TCS_FIRST if slug == "tcs" else WINDOW_FIRST
    out: list[str] = []
    label = WINDOW_LAST
    while True:
        out.append(label)
        if label == first:
            break
        label = _step_back(label)
    return list(reversed(out))


def previous_four(slug: str, fiscal_label: str) -> list[str]:
    """The four quarters before `fiscal_label` for this company, within the window (oldest first); fewer at the window
    start."""
    seq = quarters(slug)
    if fiscal_label not in seq:
        return []
    i = seq.index(fiscal_label)
    return seq[max(0, i - 4):i]


# ---------------------------------------------------------------------------------------------- choosing the document
def select(anns, rule: Rule, fiscal_label: str):
    """The announcement that is the pilot document for `fiscal_label`, or None. `anns` are announcement objects with
    `subject`, `file_name`, `disseminated` (a datetime) and `entity_id` already joined; the match is the subject or file
    name against the (quarter-overridden) rule, filed within RESULTS_WINDOW_DAYS after the quarter end. The earliest such
    announcement wins (the results outcome and its press release are filed together; the earliest is the outcome/release,
    not a later correction)."""
    q = rule.for_quarter(fiscal_label)
    pe = period_end(fiscal_label)
    lo, hi = pe + timedelta(days=1), pe + timedelta(days=RESULTS_WINDOW_DAYS)
    hits = []
    for a in anns:
        when = a.disseminated.date() if hasattr(a.disseminated, "date") else a.disseminated
        if not (lo <= when <= hi):
            continue
        if q.subject.search(a.subject or "") or q.subject.search(a.file_name or ""):
            hits.append(a)
    return min(hits, key=lambda a: a.disseminated) if hits else None


# ---------------------------------------------------------------------------------------------- cutting the pages
def cut_pages(rule: Rule, fiscal_label: str, page_texts: list[str]) -> tuple[list[int], str | None]:
    """The physical page numbers (1-based) of the pilot document inside the file, and a reason it couldn't be cut (then
    the pages list is empty and the document is held). An image document isn't cut by text here: its pages are chosen by
    the task from the rendered pages, so this returns every page with the reason None for an image document is handled by
    the caller. With no cut rule, every page is read."""
    q = rule.for_quarter(fiscal_label)
    n = len(page_texts)
    if n == 0:
        return [], "the file has no readable pages"
    if q.cut is None:
        return list(range(1, n + 1)), None
    start_re = re.compile(q.cut.start) if q.cut.start else None
    end_re = re.compile(q.cut.end) if q.cut.end else None
    start = next((i for i, t in enumerate(page_texts, 1) if start_re and start_re.search(t)), None)
    if start is None:
        return [], f"the start heading was not found ({q.cut.start!r})"
    end = next((i for i, t in enumerate(page_texts[start:], start + 1) if end_re and end_re.search(t)), None)
    last = (end - 1) if end is not None else n
    if last < start:
        return [], "the end heading came before the start"
    return list(range(start, last + 1)), None
