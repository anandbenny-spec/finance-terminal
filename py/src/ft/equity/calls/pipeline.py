"""From a company's transcript filings to its call plans (Spec 02B §5.2, §13.3; job 2B-07). No network, no database: the
task supplies `getter` (the fetch function, which stores every body as a raw file first) and the company's results
events; this module decides what each filing is, follows cover letters and curated links, and builds the plan the task
writes.

Per company: the newest season with a transcript filing is processed; the season before it too only if the newest call
doesn't end `stored` (at most two). Within a season the call is the first filing that yields a stored transcript (else
the first filing's outcome); the season's other filings are revised versions of it or other meetings (§4).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

import yaml

from ft.engines.document.text import Extracted
from ft.equity.calls import classify as cl
from ft.equity.calls import pdfs, rows
from ft.equity.calls.discovery import BSE_ATTACH, Announcement

CURATED = Path(__file__).resolve().parents[4] / "config" / "equity" / "transcript_links.yaml"
FIRST_PAGE_MAX = 3                       # §5.1: first pages opened for at most 3 filings per company-season
MAX_SEASONS = 2                          # §13.3


@dataclass(frozen=True)
class CompanyRef:
    entity_id: int
    slug: str
    name: str
    symbol: str


@dataclass(frozen=True)
class File:
    """A fetched body, already stored as a raw file."""
    url: str
    body: bytes
    sha256: bytes
    raw_file_id: int | None = None


# getter(url, kind) -> File or None; kind is "exchange" or "company". The task's getter logs a failure on a company host.
Getter = Callable[[str, str], "File | None"]


@dataclass
class Doc:
    """A document to register."""
    doc_type: str                        # call_transcript | call_cover_letter
    source: str                          # nse | bse | company_web
    external_key: str
    source_url: str
    title: str
    file: File
    info: pdfs.PdfInfo
    published_at: datetime
    text: pdfs.CallText | None = None    # transcripts

    @property
    def words(self) -> int:
        return self.info.words


@dataclass
class Extra:
    """A later filing of the season: a revised version of the call's transcript, or another meeting's."""
    kind: str                            # revised | other_meeting
    filing: Announcement
    doc: Doc
    reason: str


@dataclass
class CallPlan:
    company: CompanyRef
    season: str
    filing: Announcement                 # the filing the call is built on
    status: str                          # stored | on_company_website | unreadable
    route: str                           # exchange_pdf | company_link | curated_link | none
    quarter: rows.Quarter
    call_date: date | None = None
    call_date_source: str | None = None
    transcript: Doc | None = None
    cover: Doc | None = None
    company_url: str | None = None
    status_reason: str | None = None
    extras: list[Extra] = field(default_factory=list)

    @property
    def is_stored(self) -> bool:
        """Counts as the company's latest call: a stored transcript whose quarter isn't in doubt."""
        return self.status == "stored" and self.status_reason is None


@dataclass
class Report:
    """What the run names (the task's checks)."""
    host_failures: list[tuple[str, str]] = field(default_factory=list)       # (host, error class)
    unresolved: list[str] = field(default_factory=list)                       # a cover letter: company, quarter, host, status
    curated_failed: list[str] = field(default_factory=list)
    quarter_conflicts: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    page_map_failed: list[str] = field(default_factory=list)
    not_served: list[str] = field(default_factory=list)                       # an exchange file that could not be fetched
    not_a_transcript: list[str] = field(default_factory=list)                 # classified a transcript, read as something else
    other_meetings: list[str] = field(default_factory=list)
    first_pages: int = 0                                                       # filings whose first page was opened


# ---------------------------------------------------------------------------------------------- curated links
@dataclass(frozen=True)
class CuratedRow:
    company: str                         # company slug
    fiscal_label: str
    url: str
    found_by: str = ""
    found_on: str = ""
    note: str = ""


def load_curated(path: Path = CURATED) -> dict[tuple[str, str], CuratedRow]:
    """D13's curated table: one row per company slug and fiscal quarter (labelled curated). Empty until 2B-08."""
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for r in doc.get("links") or []:
        out[(r["company"], r["fiscal_label"])] = CuratedRow(r["company"], r["fiscal_label"], r["url"], r.get("found_by", ""),
                                                            str(r.get("found_on", "")), r.get("note", ""))
    return out


# ---------------------------------------------------------------------------------------------- fetching a filing
def exchange_urls(a: Announcement) -> list[str]:
    """BSE attachments are in the live archive for recent filings and in the history archive for older ones."""
    if not a.url:
        return []
    if a.exchange == "BSE" and a.url.startswith(BSE_ATTACH[0]):
        return [a.url, BSE_ATTACH[1] + a.url[len(BSE_ATTACH[0]):]]
    return [a.url]


class Run:
    """One run's state for a company: the getter, the report, and files already fetched (a first-page check's file is
    not fetched again)."""

    def __init__(self, getter: Getter, curated: dict[tuple[str, str], CuratedRow], report: Report):
        self.getter, self.curated, self.report = getter, curated, report
        self._cache: dict[str, File | None] = {}

    def get(self, url: str, kind: str) -> File | None:
        if url not in self._cache:
            self._cache[url] = self.getter(url, kind)
        return self._cache[url]

    def exchange_file(self, a: Announcement) -> File | None:
        for u in exchange_urls(a):
            f = self.get(u, "exchange")
            if f is not None and pdfs.is_pdf_bytes(f.body):
                return f
        return None


def _name(co: CompanyRef, a: Announcement) -> str:
    return f"{co.slug} {a.season}"


def _doc(doc_type: str, a: Announcement, f: File, info: pdfs.PdfInfo, co: CompanyRef, label: str) -> Doc:
    source = a.exchange.lower()
    kind = "call transcript" if doc_type == "call_transcript" else "call cover letter"
    return Doc(doc_type, source, f"{a.exchange}/{a.file_name or a.filing_key}", f.url,
               f"{co.name} {kind}, {label}", f, info, a.disseminated)


# ---------------------------------------------------------------------------------------------- company PDFs
def company_pdf_ok(info: pdfs.PdfInfo, co: CompanyRef, label: str) -> bool:
    """The three tests a company-hosted PDF must pass: the full test, the right company on its first two pages, the
    right quarter (its label or its period end) on them."""
    return (pdfs.classify_pdf(info) == "full" and pdfs.names_company(info.first_pages, co.name, co.symbol)
            and pdfs.names_quarter(info, label, rows.period_end_of(label)))


def _company_doc(url: str, f: File, info: pdfs.PdfInfo, co: CompanyRef, label: str, a: Announcement) -> Doc:
    return Doc("call_transcript", "company_web", f"{url}#{f.sha256.hex()}", url,
               f"{co.name} call transcript, {label}", f, info, a.disseminated)


def follow_links(run: Run, co: CompanyRef, a: Announcement, cover: Doc, label: str) -> tuple[Doc | None, list[str]]:
    """D12: the letter's links, best first, at most 6; the first PDF that passes the three tests is the transcript.
    Returns it (or None) and the attempts as "host status" strings for the unresolved line."""
    attempts: list[str] = []
    for url in pdfs.candidate_links(cover.info)[:pdfs.MAX_LINKS]:
        f = run.get(url, "company")
        host = pdfs.host_of(url)
        if f is None:
            attempts.append(f"{host} unreachable")
            continue
        if not pdfs.is_pdf_bytes(f.body):
            attempts.append(f"{host} web page")
            continue
        info = pdfs.read_pdf(f.body)
        if company_pdf_ok(info, co, label):
            return _company_doc(f.url, f, info, co, label, a), attempts
        attempts.append(f"{host} PDF that is not this company's {label} transcript")
    return None, attempts


def curated_doc(run: Run, co: CompanyRef, a: Announcement, label: str) -> Doc | None:
    """D13: the curated file for this company and quarter, if there is one and it passes the same three tests; a row
    that fails is named and not used."""
    row = run.curated.get((co.slug, label))
    if row is None:
        return None
    f = run.get(row.url, "company")
    if f is not None and pdfs.is_pdf_bytes(f.body):
        info = pdfs.read_pdf(f.body)
        if company_pdf_ok(info, co, label):
            return _company_doc(f.url, f, info, co, label, a)
    run.report.curated_failed.append(f"{co.slug} {label} ({pdfs.host_of(row.url)})")
    return None


@dataclass
class Resolved:
    """What one filing turned out to be."""
    filing: Announcement
    status: str | None                   # stored | on_company_website | unreadable | None (not a call)
    route: str = "none"
    transcript: Doc | None = None
    cover: Doc | None = None
    company_url: str | None = None
    info: pdfs.PdfInfo | None = None     # the exchange file's
    quarter: rows.Quarter | None = None


def resolve_filing(run: Run, co: CompanyRef, a: Announcement, results: list[rows.ResultsEvent]) -> Resolved:
    """Fetch and classify one transcript filing; for a cover letter follow its links, then the curated table."""
    f = run.exchange_file(a)
    if f is None:
        run.report.not_served.append(f"{_name(co, a)} ({a.exchange} {a.file_name or a.filing_key})")
        return Resolved(a, None)
    info = pdfs.read_pdf(f.body)
    cls = pdfs.classify_pdf(info)
    if cls == "full":
        label = a.season
        return Resolved(a, "stored", "exchange_pdf", _with_text(_doc("call_transcript", a, f, info, co, label), run, co, a), None,
                        None, info)
    if cls == "cover_letter":
        cover = _doc("call_cover_letter", a, f, info, co, a.season)
        qd = rows.decide_quarter(a.disseminated, results, info.quarter)
        label = qd.label or a.season
        t, attempts = follow_links(run, co, a, cover, label)
        route = "company_link"
        if t is None:
            t, route = curated_doc(run, co, a, label), "curated_link"
        if t is not None:
            return Resolved(a, "stored", route, _with_text(t, run, co, a), cover, t.source_url, info)
        links = pdfs.candidate_links(info)
        company_url = next((u for u in links if not any(h in pdfs.host_of(u) for h in pdfs.EXCHANGE_HOSTS)), None)
        run.report.unresolved.append(f"{_name(co, a)} ({'; '.join(attempts) if attempts else 'no company link'})")
        return Resolved(a, "on_company_website", "none", None, cover, company_url, info)
    if cls == "image_only":
        return Resolved(a, "unreadable", "none", None, None, None, info)
    run.report.not_a_transcript.append(f"{_name(co, a)} ({a.file_name or a.filing_key}: {cls})")
    return Resolved(a, None, info=info)


def _with_text(doc: Doc, run: Run, co: CompanyRef, a: Announcement) -> Doc:
    doc.text = pdfs.call_text(doc.info)
    if not doc.text.page_map_ok:
        run.report.page_map_failed.append(f"{_name(co, a)} (pages {', '.join(map(str, doc.text.failed_pages[:5]))})")
    return doc


# ---------------------------------------------------------------------------------------------- first pages
def first_page_transcripts(run: Run, co: CompanyRef, season: str, others: list[Announcement]) -> list[Announcement]:
    """§5.1: for a company-season with no transcript found by subject or file name, open the first page of at most 3
    filings (those most likely to hide one, by the kit's score) and keep those whose first page says "transcript" and
    isn't an audio/video notice or an intimation."""
    out = []
    ranked = sorted((a for a in others if a.url and a.kind == "other"), key=lambda a: (-first_page_score(a), a.disseminated))
    for a in ranked[:FIRST_PAGE_MAX]:
        f = run.exchange_file(a)
        run.report.first_pages += 1
        if f is None:
            continue
        info = pdfs.read_pdf(f.body)
        page = info.page_texts[0] if info.page_texts else ""
        subject = subject_line(page)
        if cl.says_transcript(page) and not cl.is_audio_video(subject) and not cl.is_intimation(subject):
            out.append(replace(a, kind="transcript", why="first page"))
    return out


def first_page_score(a: Announcement) -> int:
    """How likely a generic filing hides a transcript (orders the first-page checks; not a rule): the kit's score."""
    s = (a.subject or "").lower()
    f = cl.file_words(a.file_name)
    score = 0
    if any(t in s for t in ("con. call", "con call", "concall", "conference call", "earnings call", "analyst", "investor meet",
                            "institutional investor", "investors meet", "earning call", "post results", "post-results")):
        score += 2
    if any(t in f for t in ("con call", "concall", "earning", "conf", "call", "analyst", "investor")):
        score += 1
    if "outcome" in s:
        score += 1
    return score


def subject_line(page_text: str) -> str:
    import re
    m = re.search(r"\b(?:subject|sub)\s*[:.\-–]\s*(.{3,300})|\bre\s*:\s*(.{3,300})", page_text or "", re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(1) or m.group(2)).strip()[:300]
    return re.sub(r"\s+", " ", (page_text or "")[:400]).strip()


# ---------------------------------------------------------------------------------------------- one company
def process_company(run: Run, co: CompanyRef, announcements: list[Announcement], results: list[rows.ResultsEvent], *,
                    weekly: bool, newest_call: str | None) -> list[CallPlan]:
    """The company's call plans: its newest season with a transcript filing, and the season before it if the newest
    doesn't end stored (§13.3)."""
    mine = [a for a in announcements if a.entity_id == co.entity_id]
    by_season: dict[str, list[Announcement]] = {}
    for a in mine:
        if a.is_transcript:
            by_season.setdefault(a.season, []).append(a)
    recent = sorted({a.season for a in mine}, key=rows.season_order, reverse=True)[:MAX_SEASONS + 1]
    for season in recent:                  # company-seasons whose results are filed but no transcript was found
        if by_season.get(season) or not any(_season_of(r.occurs_at) == season for r in results):
            continue
        found = first_page_transcripts(run, co, season, [a for a in mine if a.season == season])
        if found:
            by_season[season] = found
    plans: list[CallPlan] = []
    for season in rows.seasons_to_process(by_season, newest_call, weekly):
        filings = sorted(by_season[season], key=lambda a: a.disseminated)
        plan = build_call(run, co, season, filings, [a for a in mine if a.kind == "intimation" and a.season == season], results)
        if plan is not None:
            plans.append(plan)
            if plan.is_stored or len(plans) >= MAX_SEASONS:
                break
    return plans


def _season_of(occurs_at: datetime) -> str:
    from ft.equity.calls.discovery import season_of_filing
    return season_of_filing(occurs_at)


def build_call(run: Run, co: CompanyRef, season: str, filings: list[Announcement], intimations: list[Announcement],
               results: list[rows.ResultsEvent]) -> CallPlan | None:
    """The season's call (the first filing that yields a stored transcript, else the first filing's outcome) and its
    other filings."""
    resolved: dict[str, Resolved] = {}
    chosen: Resolved | None = None
    for a in filings:
        resolved[a.filing_key] = r = resolve_filing(run, co, a, results)
        if r.status == "stored":
            chosen = r
            break
    if chosen is None:
        chosen = next((r for r in resolved.values() if r.status is not None), None)
        if chosen is None:
            return None
    plan = _plan(co, season, chosen, intimations, results, run.report)
    if plan.transcript is None or plan.transcript.text is None:
        return plan
    for a in filings:
        if a.filing_key == chosen.filing.filing_key or a.disseminated < chosen.filing.disseminated:
            continue
        r = resolved.get(a.filing_key) or resolve_filing(run, co, a, results)
        if r.status != "stored" or r.transcript is None or r.transcript.text is None:
            continue
        new_date = r.transcript.info.call_date or (r.info.call_date if r.info else None)
        revised = rows.is_revised(plan.call_date, new_date, plan.transcript.text.extracted.text, r.transcript.text.extracted.text)
        why = "same call date or at least 90% the same text" if revised else "another meeting (not analysed)"
        plan.extras.append(Extra("revised" if revised else "other_meeting", a, r.transcript, why))
        if not revised:
            run.report.other_meetings.append(f"{_name(co, a)} ({a.file_name or a.filing_key})")
    return plan


def _plan(co: CompanyRef, season: str, r: Resolved, intimations: list[Announcement], results: list[rows.ResultsEvent],
          report: Report) -> CallPlan:
    a = r.filing
    plan = CallPlan(co, season, a, r.status or "unreadable", r.route, rows.Quarter(None, None, None), None, None,
                    r.transcript, r.cover, r.company_url)
    parsed_q = (r.transcript.info.quarter if r.transcript else None) or (r.cover.info.quarter if r.cover else None) \
        or (r.info.quarter if r.info else None)
    plan.quarter = rows.decide_quarter(a.disseminated, results, parsed_q)
    parsed_date = (r.transcript.info.call_date if r.transcript else None) or (r.cover.info.call_date if r.cover else None) \
        or (r.info.call_date if r.info else None)
    plan.call_date, plan.call_date_source = rows.decide_call_date(parsed_date, intimations, a.disseminated)
    if plan.quarter.held_reason:
        plan.status_reason = plan.quarter.held_reason
        (report.quarter_conflicts if plan.quarter.held_reason == "quarter_conflict" else report.held).append(
            f"{_name(co, a)} ({a.file_name or a.filing_key})")
    return plan


# ---------------------------------------------------------------------------------------------- canonical text helper
def transcript_text(doc: Doc) -> Extracted:
    assert doc.text is not None
    return doc.text.extracted
