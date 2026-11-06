"""From a company's transcript filings to its call plans (Spec 02B §5.2, §13.3; job 2B-07). No network, no database: the
task supplies `getter` (the fetch function, which stores every body as a raw file first) and the company's results
events; this module decides what each filing is, follows cover letters and curated links, and builds the plan the task
writes.

Per company: a weekly or refresh run processes the newest season with a transcript filing, and the season before it too
only if the newest call doesn't end `stored` (at most two). **A backfill processes every season in its window** (Plan 04b
P2), oldest first, so the first filing linked to a quarter's results is that quarter's call (§4). **First pages are opened
only for seasons from `first_pages_from`** (Plan 04b P3: `config/equity/calls.yaml`); earlier seasons are classified from
the subject and the file name.

**A call is chosen by fiscal quarter** (job 2B-07b, #285; Spec 02B §4: one call per company per fiscal quarter). Every
filing of a season is resolved (fetch, the full test, the quarter by `rows.decide_quarter`), then:
- a filing that names a general meeting (subject, file name, or the page before the first speaker label) is set aside
  before any quarter is decided: registered as another meeting, never a call (rule 3);
- a stored filing held `no_quarter` or `quarter_conflict` never takes a slot;
- the season's call is the first stored filing whose quarter is the season's own; a later stored filing of the same
  quarter is a revised version of it or another meeting (§4);
- a stored filing of another quarter (a **late call**) is that quarter's call if the company has no stored call for it,
  in this run or in the database (a weekly or refresh run is given the database's calls); else it is compared with that
  call as above (rule 1);
- no filing resolves: the season's outcome is the first filing's, held with its reason, as before.
`one_call_per_quarter` then leaves one call per quarter across the seasons of the run.

**A file is stored only if it is kept** (job 2B-07c, #287). The getter returns every body unstored; the pipeline reads it, and
`Run.keep` stores it only when a document is made from it: an earnings-call transcript or a call's cover letter (and an image-only
file, whose call is `unreadable`). A file that was read and is not one, such as a first-page candidate whose page does not say
"transcript" or a company link to an annual report, is not stored: `Run.reject` records its URL so the next run does not fetch it
again. Whether a company PDF is *this company's, this quarter's* transcript depends on the cover letter that links to it, so a
full transcript that fails that test is released, not recorded (another letter may link to it); a file that is not a transcript
at all is recorded.
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
    """A fetched body: stored as a raw file once `Run.keep` has kept it."""
    url: str
    body: bytes
    sha256: bytes
    raw_file_id: int | None = None       # None until `Run.keep` stores it (a getter's deferred body)
    declined: bool = False               # read on an earlier run and not kept (#287): nothing was fetched, `body` is empty


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
    reason: str                          # why it is not the call (an other meeting) or how it is the same (a revision)


@dataclass
class StoredCall:
    """A call already in the database for a quarter (given to a weekly or refresh run; #285 rule 1): its call date, and a
    function returning its transcript text, called only when a late filing of the same quarter has to be compared."""
    label: str
    call_date: date | None
    text: Callable[[], str | None]


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

    @property
    def late(self) -> bool:
        """Filed in a season other than its quarter's (#285): a late call. Held calls have no quarter."""
        return self.quarter.label is not None and self.quarter.label != self.season


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
    other_meetings: list[str] = field(default_factory=list)                   # with the reason each is not a call
    late_calls: list[str] = field(default_factory=list)                       # company, quarter, season filed in, filed date
    general_meetings: list[str] = field(default_factory=list)                 # filings set aside as a general meeting
    gm_filings: list[tuple[int, str, str]] = field(default_factory=list)      # the same, as (entity id, exchange, filing key)
    stored_revised: list[str] = field(default_factory=list)                   # a late filing that revises a call in the database
    first_pages: int = 0                                                       # filings whose first page was opened
    not_kept: int = 0                                                          # files read and not stored (#287)
    not_kept_bytes: int = 0
    already_declined: int = 0                                                  # URLs recorded on an earlier run: not fetched


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
    not fetched again). `keeper`, `decliner` and `releaser` (the task's) settle a deferred body: store it, record that it was
    read and not kept, or forget it (#287). Without them (a test serving files) nothing is stored or recorded."""

    def __init__(self, getter: Getter, curated: dict[tuple[str, str], CuratedRow], report: Report, *,
                 keeper: Callable[[File], File] | None = None, decliner: Callable[[File], bool] | None = None,
                 releaser: Callable[[File], None] | None = None):
        self.getter, self.curated, self.report = getter, curated, report
        self.keeper, self.decliner, self.releaser = keeper, decliner, releaser
        self._cache: dict[str, File | None] = {}
        self._declined: set[str] = set()      # URLs an earlier run read and did not keep
        self._settled: set[str] = set()       # URLs this run declined

    def get(self, url: str, kind: str) -> File | None:
        if url not in self._cache:
            f = self.getter(url, kind)
            self._cache[url] = f
            if f is not None and f.declined:
                self._declined.add(url)
                self.report.already_declined += 1
        return self._cache[url]

    def exchange_file(self, a: Announcement) -> File | None:
        for u in exchange_urls(a):
            f = self.get(u, "exchange")
            if f is not None and f.declined:
                continue
            if f is not None and pdfs.is_pdf_bytes(f.body):
                return f
        return None

    def was_declined(self, a: Announcement) -> bool:
        """The filing's file was read on an earlier run and not kept (a file that cannot be fetched is not the same)."""
        return any(u in self._declined for u in exchange_urls(a))

    def keep(self, f: File) -> File:
        """Store a file a document is about to be made from (a transcript or a cover letter). Returns the stored file; a
        later `get` of the URL returns it too."""
        have = self._cache.get(f.url)
        if have is not None and have.raw_file_id is not None:
            return have
        if f.raw_file_id is not None or self.keeper is None:
            return f
        kept = self.keeper(f)
        self._cache[f.url] = kept
        return kept

    def reject(self, f: File) -> None:
        """The file was read and is not a transcript or a cover letter: it is not stored and its URL is recorded, so the next
        run does not fetch it again. A copy already stored (a 304's) is left as it is."""
        if f.raw_file_id is not None or f.declined or f.url in self._settled:
            return
        self._settled.add(f.url)
        if self.decliner is not None and self.decliner(f):
            self.report.not_kept += 1
            self.report.not_kept_bytes += len(f.body)

    def settle(self) -> None:
        """Forget every body still deferred (neither kept nor rejected): nothing is stored or recorded for it, and a later run
        looks at it again. A first-page candidate that passed but whose season this run did not build is one."""
        if self.releaser is None:
            return
        for url, f in self._cache.items():
            if f is not None and f.raw_file_id is None and not f.declined and url not in self._settled:
                self.releaser(f)


def _name(co: CompanyRef, a: Announcement) -> str:
    return f"{co.slug} {a.season}"


def _doc(doc_type: str, a: Announcement, f: File, info: pdfs.PdfInfo, co: CompanyRef, label: str, *, meeting: bool = False) -> Doc:
    source = a.exchange.lower()
    kind = "general meeting transcript" if meeting else "call transcript" if doc_type == "call_transcript" else "call cover letter"
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
        if f.declined:
            attempts.append(f"{host} PDF that is not a call transcript")
            continue
        if not pdfs.is_pdf_bytes(f.body):
            attempts.append(f"{host} web page")
            continue
        info = pdfs.read_pdf(f.body)
        if company_pdf_ok(info, co, label):
            f = run.keep(f)
            return _company_doc(f.url, f, info, co, label, a), attempts
        if pdfs.classify_pdf(info) != "full":                  # not a transcript of any call: not stored, its URL recorded
            run.reject(f)
        attempts.append(f"{host} PDF that is not this company's {label} transcript")
    return None, attempts


def curated_doc(run: Run, co: CompanyRef, a: Announcement, label: str) -> Doc | None:
    """D13: the curated file for this company and quarter, if there is one and it passes the same three tests; a row
    that fails is named and not used."""
    row = run.curated.get((co.slug, label))
    if row is None:
        return None
    f = run.get(row.url, "company")
    if f is not None and not f.declined and pdfs.is_pdf_bytes(f.body):
        info = pdfs.read_pdf(f.body)
        if company_pdf_ok(info, co, label):
            return _company_doc(f.url, run.keep(f), info, co, label, a)
        if pdfs.classify_pdf(info) != "full":
            run.reject(f)
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
    meeting: str | None = None           # general_meeting: set aside before any quarter is decided (#285 rule 3)


def quarter_of(r: Resolved, results: list[rows.ResultsEvent]) -> rows.Quarter:
    """§4's quarter of a resolved filing: the results link, checked against the quarter parsed from its first pages."""
    parsed = (r.transcript.info.quarter if r.transcript else None) or (r.cover.info.quarter if r.cover else None) \
        or (r.info.quarter if r.info else None)
    return rows.decide_quarter(r.filing.disseminated, results, parsed)


def resolve_filing(run: Run, co: CompanyRef, a: Announcement, results: list[rows.ResultsEvent]) -> Resolved:
    """Fetch and classify one transcript filing; for a cover letter follow its links, then the curated table. A filing that
    names a general meeting, in its subject, its file name or the page before its first speaker label, is set aside as one
    (#285 rule 3): its transcript is kept as another meeting's, and no quarter is decided for it."""
    named = cl.is_general_meeting(a.subject) or cl.is_general_meeting(a.file_name)
    f = run.exchange_file(a)
    if f is None:
        if named:
            return Resolved(a, None, meeting="general_meeting")
        if run.was_declined(a):                                # read on an earlier run and not kept (#287): not a failure to serve
            return Resolved(a, None)
        run.report.not_served.append(f"{_name(co, a)} ({a.exchange} {a.file_name or a.filing_key})")
        return Resolved(a, None)
    info = pdfs.read_pdf(f.body)
    cls = pdfs.classify_pdf(info)
    if named or (cls in ("full", "cover_letter") and cl.is_general_meeting(pdfs.header_text(info), short=False)):
        doc = None
        if cls == "full":
            doc = _with_text(_doc("call_transcript", a, run.keep(f), info, co,
                                  f"filed {rows.india_date(a.disseminated).isoformat()}", meeting=True), run, co, a)
        else:
            run.reject(f)                                      # a general meeting's letter or notice: not a call's file
        return Resolved(a, None, "none", doc, None, None, info, meeting="general_meeting")
    if cls == "full":
        label = rows.decide_quarter(a.disseminated, results, info.quarter).label or a.season      # #285 rule 4: the call's quarter
        return Resolved(a, "stored", "exchange_pdf",
                        _with_text(_doc("call_transcript", a, run.keep(f), info, co, label), run, co, a), None, None, info)
    if cls == "cover_letter":
        f = run.keep(f)
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
        run.keep(f)                                            # kept as before: the call is `unreadable`, this file is the evidence
        return Resolved(a, "unreadable", "none", None, None, None, info)
    run.reject(f)                                              # named a transcript, read as something else: not stored (#287)
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
        else:
            run.reject(f)                                      # not a transcript: not stored, its URL recorded (#287)
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
                    weekly: bool, newest_call: str | None, history: bool = False,
                    first_pages_from: str | None = None, stored: dict[str, StoredCall] | None = None) -> list[CallPlan]:
    """The company's call plans. A weekly or refresh run: its newest season with a transcript filing, and the season before
    it if the newest doesn't end stored (§13.3). A backfill (`history`, Plan 04b P2): every season with a transcript
    filing, oldest first. `first_pages_from` (P3): seasons before it are not given first-page checks. `stored` (#285): the
    company's calls already in the database by quarter, for the late-call rule; a backfill decides everything afresh and
    ignores it. `newest_call` is the quarter of the company's newest call (`rows.newest_quarter`)."""
    mine = [a for a in announcements if a.entity_id == co.entity_id]
    by_season: dict[str, list[Announcement]] = {}
    for a in mine:
        if a.is_transcript:
            by_season.setdefault(a.season, []).append(a)
    recent = sorted({a.season for a in mine}, key=rows.season_order, reverse=True)[:MAX_SEASONS + 1]
    for season in recent:                  # company-seasons whose results are filed but no transcript was found
        if first_pages_from and rows.season_order(season) < rows.season_order(first_pages_from):
            continue                       # P3: an older season is classified from the subject and file name only
        if by_season.get(season) or not any(_season_of(r.occurs_at) == season for r in results):
            continue
        found = first_page_transcripts(run, co, season, [a for a in mine if a.season == season])
        if found:
            by_season[season] = found
    plans: list[CallPlan] = []
    seasons = rows.seasons_to_process(by_season, newest_call, weekly)
    if history:
        seasons = sorted(seasons, key=rows.season_order)       # the first filing linked to a quarter's results is its call (§4)
    done = 0
    for season in seasons:
        filings = sorted(by_season[season], key=lambda a: a.disseminated)
        made = build_calls(run, co, season, filings, [a for a in mine if a.kind == "intimation" and a.season == season], results,
                           None if history else stored)
        if not made:
            continue
        plans += made
        done += 1
        if not history and (any(p.is_stored and p.quarter.label == season for p in made) or done >= MAX_SEASONS):
            break
    plans = one_call_per_quarter(run.report, co, plans)
    for p in plans:
        if p.late:
            run.report.late_calls.append(f"{co.slug} {p.quarter.label} (filed {rows.india_date(p.filing.disseminated).isoformat()} "
                                         f"in the {p.season} season)")
    run.settle()
    return plans


# ---------------------------------------------------------------------------------------------- history reporting
@dataclass(frozen=True)
class SeasonCoverage:
    """One season's outcome (Plan 04b P4): companies with a transcript filing, and what became of their calls."""
    season: str
    companies: int              # with a transcript filing in the season
    stored: int                 # call stored, quarter not in doubt
    on_company_website: int
    unreadable: int
    held: int                   # a quarter conflict or no quarter
    no_call: int                # a transcript filing, but no call (its file couldn't be fetched, or it read as something else)

    @property
    def share(self) -> int:
        return round(100 * self.stored / self.companies) if self.companies else 0


def coverage_by_season(announcements: list[Announcement], plans: list[CallPlan]) -> list[SeasonCoverage]:
    """Per season, oldest first: the companies with a transcript filing, and the calls this run built for them by outcome.
    A season's companies come from the discovered filings (a first-page find counts as a filing through its plan)."""
    filed: dict[str, set[int]] = {}
    for a in announcements:
        if a.is_transcript and a.entity_id is not None:
            filed.setdefault(a.season, set()).add(a.entity_id)
    for p in plans:
        filed.setdefault(p.season, set()).add(p.company.entity_id)
    out = []
    for season in sorted(filed, key=rows.season_order):
        mine = [p for p in plans if p.season == season and not p.late]       # a late call is its own quarter's, not the season's
        held = sum(1 for p in mine if p.status_reason)
        out.append(SeasonCoverage(
            season, len(filed[season]),
            sum(1 for p in mine if p.is_stored),
            sum(1 for p in mine if not p.status_reason and p.status == "on_company_website"),
            sum(1 for p in mine if not p.status_reason and p.status == "unreadable"),
            held, len(filed[season]) - len(mine)))
    return out


def format_coverage(cov: list[SeasonCoverage]) -> str:
    return "; ".join(
        f"{c.season}: {c.companies} companies with a transcript filing, {c.stored} stored ({c.share}%), "
        f"{c.on_company_website} on_company_website, {c.unreadable} unreadable, {c.held} held, {c.no_call} with no call"
        for c in cov) or "no transcript filings"


MB = 1024 * 1024


def storage_summary(run_files: int, run_bytes: int, stored_total: int | None, stop_mb: int) -> tuple[bool, str]:
    """The `raw_storage` check (Plan 04b P5): what this run stored (source sizes) and what the raw bucket holds (stored
    sizes) against the stop. `stored_total` None when the object store can't be listed."""
    ran = f"this run stored {run_files} files, {run_bytes / MB:.1f} MB at source size"
    if stored_total is None:
        return False, f"{ran}; the raw bucket's size couldn't be read (read Storage in the dashboard; the stop is {stop_mb} MB)"
    ok = stored_total <= stop_mb * MB
    return ok, (f"{ran}; the raw bucket holds {stored_total / MB:.1f} MB of stored objects, "
                f"{'under' if ok else 'OVER'} the {stop_mb} MB stop")


def _season_of(occurs_at: datetime) -> str:
    from ft.equity.calls.discovery import season_of_filing
    return season_of_filing(occurs_at)


def _line(co: CompanyRef, a: Announcement, why: str) -> str:
    return f"{_name(co, a)} ({a.file_name or a.filing_key}): {why}"


def _file_date(r: Resolved) -> date | None:
    """The call date a filing's own pages give."""
    return (r.transcript.info.call_date if r.transcript else None) or (r.info.call_date if r.info else None)


def _add_extra(rep: Report, co: CompanyRef, plan: CallPlan, r: Resolved, why: str) -> bool:
    """A filing that isn't the plan's call: a revised version of it if `rows.is_revised` says so (§4), else another meeting,
    named with `why`. Only filings with a transcript text are compared (False for a cover letter or an unreadable file)."""
    if plan.transcript is None or plan.transcript.text is None or r.transcript is None or r.transcript.text is None:
        return False
    if rows.is_revised(plan.call_date, _file_date(r), plan.transcript.text.extracted.text, r.transcript.text.extracted.text):
        plan.extras.append(Extra("revised", r.filing, r.transcript, "same call date or at least 90% the same text"))
    else:
        plan.extras.append(Extra("other_meeting", r.filing, r.transcript, why))
        rep.other_meetings.append(_line(co, r.filing, why))
    return True


def _against_stored(rep: Report, co: CompanyRef, label: str, stored: StoredCall, group: list[Resolved]) -> None:
    """Late filings of a quarter that already has a call in the database: each is compared with it by `rows.is_revised`.
    Nothing is written for them (a stored call is never rewritten); a revision is named, another meeting is named."""
    for r in group:
        text = r.transcript.text.extracted.text if r.transcript and r.transcript.text else None
        old = stored.text() if text is not None else None
        if old is not None and rows.is_revised(stored.call_date, _file_date(r), old, text):
            rep.stored_revised.append(_line(co, r.filing, f"a revised version of the stored {label} call"))
        else:
            rep.other_meetings.append(_line(co, r.filing, f"the {label} quarter already has a call"))


HELD_WHY = {"no_quarter": "no quarter", "quarter_conflict": "quarter conflict"}


def build_calls(run: Run, co: CompanyRef, season: str, filings: list[Announcement], intimations: list[Announcement],
                results: list[rows.ResultsEvent], stored: dict[str, StoredCall] | None = None) -> list[CallPlan]:
    """The calls of one season's filings, by fiscal quarter (#285). Normally one, the season's own; a late call adds its own
    quarter's. Empty when no filing of the season resolves to anything."""
    rep = run.report
    resolved = [resolve_filing(run, co, a, results) for a in filings]
    for r in resolved:
        if r.meeting:
            rep.general_meetings.append(_line(co, r.filing, "names an annual or extraordinary general meeting"))
            rep.gm_filings.append((co.entity_id, r.filing.exchange, r.filing.filing_key))
    pairs = [(r, quarter_of(r, results)) for r in resolved if r.status is not None]
    groups: dict[str, list[Resolved]] = {}                  # stored filings with a decided quarter, by quarter, in filing order
    for r, q in pairs:
        if r.status == "stored" and not q.held_reason:
            groups.setdefault(q.label, []).append(r)

    def plan_of(r: Resolved) -> CallPlan:
        return _plan(co, season, r, intimations, results, rep)

    def with_extras(group: list[Resolved]) -> CallPlan:
        plan = plan_of(group[0])
        why = "a different call" if plan.quarter.label == season else f"the {plan.quarter.label} quarter already has a call"
        for r in group[1:]:                                 # a later filing of the same quarter: §4
            _add_extra(rep, co, plan, r, why)
        return plan

    own = with_extras(groups[season]) if season in groups else None
    late: list[CallPlan] = []
    for label in sorted((lb for lb in groups if lb != season), key=lambda lb: groups[lb][0].filing.disseminated):
        if stored and label in stored:
            _against_stored(rep, co, label, stored[label], groups[label])       # the database's call stays; these are named
        else:
            late.append(with_extras(groups[label]))
    primary = own
    if primary is None:                                      # no stored filing of the season's own quarter
        alt = next((r for r, q in pairs if r.status != "stored" and q.label == season), None)
        if alt is not None:                                  # a cover letter or an unreadable file for this quarter
            primary = plan_of(alt)
        elif not groups:                                     # nothing resolved: the first filing's outcome, held with its reason
            first = next((r for r, _ in pairs if r.status == "stored"), None) or (pairs[0][0] if pairs else None)
            primary = plan_of(first) if first is not None else None
    plans = ([primary] if primary is not None else []) + late
    head = plans[0] if plans else None
    if head is not None:
        taken = {p.filing.filing_key for p in plans}
        for r, q in pairs:                                   # held filings: never a call, registered as another meeting or revision
            if r.status == "stored" and q.held_reason and r.filing.filing_key not in taken:
                _add_extra(rep, co, head, r, HELD_WHY[q.held_reason])
        for r in resolved:                                   # general meetings: registered as other meetings, never a call
            if r.meeting and r.transcript is not None and r.transcript.text is not None:
                head.extras.append(Extra("other_meeting", r.filing, r.transcript, "general meeting"))
                rep.other_meetings.append(_line(co, r.filing, "general meeting"))
    return plans


def one_call_per_quarter(report: Report, co: CompanyRef, plans: list[CallPlan]) -> list[CallPlan]:
    """Two plans of one run for the same quarter (the same late call seen from two seasons, or a late call and the season's
    own, as a newest-first run meets them): the stored one stays, then the one filed in its own season, then the earlier
    filing (the first linked to the results, §4). The other becomes a revised version or another meeting of it."""
    def rank(p: CallPlan) -> tuple:
        return (p.is_stored, not p.late, -p.filing.disseminated.timestamp())

    by_label: dict[str, list[CallPlan]] = {}
    for p in plans:
        if p.quarter.label:
            by_label.setdefault(p.quarter.label, []).append(p)
    gone: set[int] = set()
    for label, group in by_label.items():
        if len(group) < 2:
            continue
        win = max(group, key=rank)
        for p in group:
            if p is win:
                continue
            gone.add(id(p))
            win.extras += p.extras
            r = Resolved(p.filing, p.status, p.route, p.transcript, p.cover, p.company_url, p.transcript.info if p.transcript else None)
            if not _add_extra(report, co, win, r, f"the {label} quarter already has a call"):
                report.other_meetings.append(_line(co, p.filing, f"the {label} quarter already has a call"))
    return [p for p in plans if id(p) not in gone]


def _plan(co: CompanyRef, season: str, r: Resolved, intimations: list[Announcement], results: list[rows.ResultsEvent],
          report: Report) -> CallPlan:
    a = r.filing
    plan = CallPlan(co, season, a, r.status or "unreadable", r.route, rows.Quarter(None, None, None), None, None,
                    r.transcript, r.cover, r.company_url)
    plan.quarter = quarter_of(r, results)
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
