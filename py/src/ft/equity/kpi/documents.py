"""Finding the pilot document per company and quarter, and cutting it from its bundle (DD-04, #512; Plan 05 §1.3, D7;
Data Audit 10 §1, §2). Pure: the curated rules (`config/equity/kpi_documents.yaml`), the quarter window, the fiscal-label
→ period-end map, the announcement match and the page cut. The network (fetching the announcements and the file) and the
PDF reading are the task's; this module decides which filing and which pages.

**Which filing (#512's fix round, 14 Nov 2026).** The quarter's results day comes first (`results_day`: the earliest
results filing within 75 days of the quarter end, call notices, clarifications, intimations and other listed companies'
filings rejected); the candidates are that day's and the next day's filings matching the company's subject, in the
curated `prefer` order (`candidates`); the task takes the first with enough pages whose cut finds its heading (`usable`).

The 7 companies are Data Audit 10's eight IT and bank names less Wipro, which isn't in the company master. The window is
Q3 FY25 to Q2 FY27, with TCS from Q2 FY25, so each flagged quarter (Q3 FY26 to Q2 FY27, and TCS's Q2 FY26) has its
previous four documents.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

RULES_PATH = Path(__file__).resolve().parents[4] / "config" / "equity" / "kpi_documents.yaml"
PARSER_VERSION = 1
INDIA = ZoneInfo("Asia/Kolkata")

# The 7 pilot companies (slugs), and the window. TCS alone starts a quarter earlier (DA-10's known case needs Q2 FY25).
COMPANIES = ("tcs", "infy", "hcltech", "hdfcbank", "icicibank", "sbin", "axisbank")
WINDOW_FIRST = "Q3 FY25"
WINDOW_LAST = "Q2 FY27"
TCS_FIRST = "Q2 FY25"
RESULTS_DAY_MAX_DAYS = 75            # the results day falls within this many days of the quarter end (SEBI: 45, Q4 60)
CANDIDATE_DAYS = 1                   # the pilot document is filed on the results day or the day after
MAX_CANDIDATES = 4                   # filings tried per quarter, in the rule's order of preference
_LABEL = re.compile(r"Q([1-4])\s*FY\s*(\d{2})", re.IGNORECASE)


@dataclass(frozen=True)
class Cut:
    start: str | None
    end: str | None


@dataclass(frozen=True)
class Filing:
    """One announcement as the selection reads it: the feed's fields plus NSE's category (`desc`, e.g. 'Outcome of Board
    Meeting'; None for a BSE row). The task builds it from `ft.equity.calls.discovery.Announcement` and the raw row."""
    exchange: str
    filing_key: str
    disseminated: datetime
    subject: str
    file_name: str
    url: str | None
    category: str | None = None


@dataclass(frozen=True)
class Shared:
    """The rules every company shares (`results_day` and `reject` in the YAML): what marks the results filing that
    fixes the results day, and the filings never taken (call notices, clarifications, intimations)."""
    results_names: re.Pattern
    results_kind: re.Pattern
    reject: re.Pattern


@dataclass(frozen=True)
class Rule:
    slug: str
    document_class: str
    source: str                      # 'own_filing' | 'bundle'
    subject: re.Pattern
    cut: Cut | None
    image_quarters: frozenset[str]
    overrides: dict[str, dict] = field(default_factory=dict)
    prefer: tuple[re.Pattern, ...] = ()       # candidates matching an earlier pattern are tried first
    reject: re.Pattern | None = None          # the company's own never-take list (another listed company's filings)
    min_pages: int = 1                        # a candidate with fewer pages is not the pilot document
    shared: Shared | None = None

    def for_quarter(self, fiscal_label: str) -> Rule:
        """This rule with any per-quarter override applied (HDFC Bank's separate Q3 FY25 'Press Release' filing)."""
        ov = self.overrides.get(fiscal_label)
        if not ov:
            return self
        cut = self.cut
        if "cut" in ov:
            cut = Cut(ov["cut"].get("start"), ov["cut"].get("end")) if ov["cut"] else None
        return replace(self, document_class=ov.get("document_class", self.document_class),
                       source=ov.get("source", self.source),
                       subject=re.compile(ov["subject"]) if "subject" in ov else self.subject, cut=cut,
                       prefer=tuple(re.compile(p) for p in ov["prefer"]) if "prefer" in ov else self.prefer,
                       min_pages=int(ov.get("min_pages", self.min_pages)), overrides={})

    def reads_as_image(self, fiscal_label: str) -> bool:
        return fiscal_label in self.image_quarters


def load_rules(path: Path = RULES_PATH) -> dict[str, Rule]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rd = doc["results_day"]
    shared = Shared(re.compile(rd["names"]), re.compile(rd["kind"]), re.compile(doc["reject"]))
    out: dict[str, Rule] = {}
    for slug, r in (doc.get("companies") or {}).items():
        cut = Cut(r["cut"].get("start"), r["cut"].get("end")) if r.get("cut") else None
        out[slug] = Rule(slug, r["document_class"], r["source"], re.compile(r["subject"]), cut,
                         frozenset(r.get("image_quarters") or []), dict(r.get("overrides") or {}),
                         tuple(re.compile(p) for p in r.get("prefer") or []),
                         re.compile(r["reject"]) if r.get("reject") else None, int(r.get("min_pages", 1)), shared)
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
def _day(f) -> date:
    d = f.disseminated
    if isinstance(d, datetime):
        return d.astimezone(INDIA).date() if d.tzinfo else d.date()
    return d


def rejected(f, rule: Rule) -> bool:
    """A filing never taken: a call notice, clarification or intimation (the shared list, on the subject and NSE's
    category), or another listed company's filing made through this one (the company's list, on the subject, category
    and file name: HDFC Bank files HDFC Life's board outcome). File names are not read for the shared list: HDFC Bank
    names its results outcome 'SEintimation…'."""
    text = f"{f.subject or ''} {getattr(f, 'category', None) or ''}"
    if rule.shared is not None and rule.shared.reject.search(text):
        return True
    return bool(rule.reject and rule.reject.search(f"{text} {f.file_name or ''}"))


def is_results_filing(f, rule: Rule) -> bool:
    """The filing that reports the quarter's results (the board-meeting outcome or the results submission): it names
    results and is an outcome, a submission or an approval, and isn't rejected."""
    if rule.shared is None or rejected(f, rule):
        return False
    text = f"{f.subject or ''} {getattr(f, 'category', None) or ''}"
    return bool(rule.shared.results_names.search(text) and rule.shared.results_kind.search(text))


def results_day(filings, rule: Rule, fiscal_label: str) -> date | None:
    """The day the quarter's results were filed: the earliest results filing within RESULTS_DAY_MAX_DAYS of the quarter
    end (#512's fix round: the nearest filing in a wide window took press releases, clarifications and call notices filed
    before results day). None when the feed holds none: the quarter is then held, never guessed."""
    pe = period_end(fiscal_label)
    lo, hi = pe + timedelta(days=1), pe + timedelta(days=RESULTS_DAY_MAX_DAYS)
    days = [_day(f) for f in filings if lo <= _day(f) <= hi and is_results_filing(f, rule)]
    return min(days) if days else None


def candidates(filings, rule: Rule, fiscal_label: str) -> tuple[date | None, list]:
    """The results day and the filings that may be the pilot document, in the order to try them: filed on the results
    day or the day after, matching the (quarter-overridden) rule's subject on the subject or file name, not rejected;
    ordered by the first `prefer` pattern they match (unmatched last), then by time. At most MAX_CANDIDATES. The task
    takes the first that passes `min_pages` and the cut."""
    q = rule.for_quarter(fiscal_label)
    rd = results_day(filings, q, fiscal_label)
    if rd is None:
        return None, []
    last = rd + timedelta(days=CANDIDATE_DAYS)
    pool = [f for f in filings
            if rd <= _day(f) <= last and not rejected(f, q)
            and (q.subject.search(f.subject or "") or q.subject.search(f.file_name or ""))]

    def rank(f):
        for i, p in enumerate(q.prefer):
            if p.search(f.subject or "") or p.search(f.file_name or ""):
                return i, f.disseminated
        return len(q.prefer), f.disseminated
    return rd, sorted(pool, key=rank)[:MAX_CANDIDATES]


def select(filings, rule: Rule, fiscal_label: str):
    """The first candidate (`candidates`), or None."""
    _rd, found = candidates(filings, rule, fiscal_label)
    return found[0] if found else None


def usable(rule: Rule, fiscal_label: str, page_texts: list[str]) -> tuple[list[int], str | None]:
    """Whether a fetched candidate is the pilot document: at least the rule's `min_pages`, and, for a text document, a
    successful cut. Returns the pages to read (every page for an image document) and None, or [] and the reason."""
    q = rule.for_quarter(fiscal_label)
    n = len(page_texts)
    if n < q.min_pages:
        return [], f"{n} page{'s' if n != 1 else ''}, fewer than {q.min_pages}"
    if rule.reads_as_image(fiscal_label):
        return list(range(1, n + 1)), None
    return cut_pages(rule, fiscal_label, page_texts)


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
