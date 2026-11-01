"""The rules that turn fetched transcripts into call rows (Spec 02B §4, §13.3; job 2B-07). Pure functions.

- **Selection** (§13.3): per company, the newest season with a transcript filing; the season before it too only if the
  newest doesn't end `stored`. A weekly run processes only a season newer than the company's newest call.
- **The quarter** (§4): the company's latest `results_filing` at or before the transcript's dissemination time and no
  more than 45 days before it (`results_link`), checked against the quarter parsed from the first pages: a
  disagreement holds the call (`quarter_conflict`). No results event: the parsed quarter (`text`). Neither: held.
- **The call date** (§4): from the cover letter or the first pages; else the date an intimation filed by the same
  company in the 10 days before the transcript gives for the call; else none.
- **A revised version** (§4): a later filing in the same season whose call date matches the first's, or whose text is at
  least 90% the same by the differ's word count.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ft.engines.document.diff import diff_texts
from ft.equity.calls.discovery import INDIA, Announcement
from ft.equity.calls.pdfs import parse_date_at

RESULTS_WITHIN = timedelta(days=45)
INTIMATION_WITHIN = timedelta(days=10)
SAME_TEXT = 0.9


# ---------------------------------------------------------------------------------------------- fiscal quarters
def fiscal_label(period_end: date) -> str | None:
    """"Q2 FY27" for a period ending 30 Sep 2026 (the fiscal year ends in March and is named for the year it ends in);
    None for a date that doesn't end a quarter."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(period_end.month)
    if q is None:
        return None
    return f"Q{q} FY{(period_end.year + 1 if period_end.month >= 4 else period_end.year) % 100:02d}"


def season_key(season: str) -> str:
    """"Q2 FY27" -> "q2-fy27"."""
    return season.lower().replace(" ", "-")


def season_order(season: str) -> tuple[int, int]:
    """Sortable (fiscal year, quarter) of "Q2 FY27"."""
    m = re.fullmatch(r"Q([1-4]) FY(\d{2})", season)
    return (int(m.group(2)), int(m.group(1))) if m else (0, 0)


@dataclass(frozen=True)
class ResultsEvent:
    event_id: int
    occurs_at: datetime
    period_end: date

    @property
    def label(self) -> str | None:
        return fiscal_label(self.period_end)


@dataclass(frozen=True)
class Quarter:
    label: str | None
    period_end: date | None
    source: str | None                 # results_link | text | None (held)
    results_event_id: int | None = None
    held_reason: str | None = None     # quarter_conflict | no_quarter


def period_end_of(label: str) -> date | None:
    m = re.fullmatch(r"Q([1-4]) FY(\d{2})", label or "")
    if not m:
        return None
    q, fy = int(m.group(1)), 2000 + int(m.group(2))
    return {1: date(fy - 1, 6, 30), 2: date(fy - 1, 9, 30), 3: date(fy - 1, 12, 31), 4: date(fy, 3, 31)}[q]


def decide_quarter(disseminated: datetime, results: Iterable[ResultsEvent], parsed: str | None) -> Quarter:
    """§4's quarter rule."""
    near = [r for r in results if r.label and r.occurs_at <= disseminated and disseminated - r.occurs_at <= RESULTS_WITHIN]
    if near:
        best = max(near, key=lambda r: (r.occurs_at, r.period_end))
        if parsed and parsed != best.label:
            return Quarter(None, None, None, best.event_id, "quarter_conflict")
        return Quarter(best.label, best.period_end, "results_link", best.event_id)
    if parsed:
        return Quarter(parsed, period_end_of(parsed), "text")
    return Quarter(None, None, None, None, "no_quarter")


# ---------------------------------------------------------------------------------------------- the call date
def intimation_date(intimations: Iterable[Announcement], disseminated: datetime) -> date | None:
    """The call date an intimation gives: a date written in the subject of an intimation filed by the company in the 10
    days before the transcript, falling between the intimation's own filing date and the transcript's. An intimation
    whose subject names no date gives none (its filing date is not the call's)."""
    best = None
    for a in sorted(intimations, key=lambda a: a.disseminated, reverse=True):
        if not (disseminated - INTIMATION_WITHIN <= a.disseminated <= disseminated):
            continue
        for m in re.finditer(r"\bon\s+(?:\w+day,?\s+)?(.{6,40})", a.subject, re.IGNORECASE):
            d = parse_date_at(m.group(1))
            if d and a.disseminated.astimezone(INDIA).date() <= d <= disseminated.astimezone(INDIA).date():
                best = best or d
        if best:
            break
    return best


def decide_call_date(parsed: date | None, intimations: Iterable[Announcement], disseminated: datetime) -> tuple[date | None, str | None]:
    if parsed:
        return parsed, "text"
    d = intimation_date(intimations, disseminated)
    return (d, "intimation") if d else (None, None)


# ---------------------------------------------------------------------------------------------- revised versions
def text_similarity(old: str, new: str) -> float:
    """Share of the longer text's words that are unchanged, by the differ's word counts (diff.py)."""
    d = diff_texts(old, new)
    old_n, new_n = d.segments["words"]["old"], d.segments["words"]["new"]
    longest = max(old_n, new_n)
    return (old_n - d.words_removed) / longest if longest else 1.0


def is_revised(first_date: date | None, new_date: date | None, first_text: str, new_text: str) -> bool:
    """§4: a later filing of the same quarter is the same transcript again if its call date matches the first's or its
    text is at least 90% the same."""
    if first_date is not None and new_date is not None and first_date == new_date:
        return True
    return text_similarity(first_text, new_text) >= SAME_TEXT


# ---------------------------------------------------------------------------------------------- selection
def seasons_to_process(filings_by_season: dict[str, list[Announcement]], newest_call: str | None,
                       weekly: bool) -> list[str]:
    """The seasons with a transcript filing, newest first. A weekly run keeps only seasons newer than the company's
    newest call; the caller processes the first, and the next only if the first doesn't end `stored`."""
    seasons = sorted((s for s, a in filings_by_season.items() if a), key=season_order, reverse=True)
    if weekly and newest_call:
        seasons = [s for s in seasons if season_order(s) > season_order(newest_call)]
    return seasons


def india_date(t: datetime) -> dt.date:
    return t.astimezone(INDIA).date()
