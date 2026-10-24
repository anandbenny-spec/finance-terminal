"""policy.rbi_documents (Prompt 01 B6; Architecture 02 §2; Spec 01 §1.1, §2, §3.1): MPC resolutions (part 1) and
minutes (part 2). The Governor's statements and the press-conference transcripts are part 3 (issue #15).

**Which document is a meeting's resolution.** The Annual Policy page's link decides (Spec 01 §1.1): the old site
only, the current fiscal year's page as served, earlier years by the page's own form post (policy.calendar stores
those; this task reads its stored copies, or posts the form itself when a year isn't stored yet). The press-release
RSS (latest 10) is a trigger: an item counts only if it's titled as a resolution and the page it links passes the
resolution checks. On a decision day the feed can list the resolution before the Annual Policy page does; in that gap
the RSS item's link is used, and a later run's `annual_policy_link` check (W) confirms the page links the same one.
The minutes are found the same way, by the meeting's last day in their title ("Minutes of the Monetary Policy
Committee Meeting, October 5 to 7, 2026"), two weeks after the meeting (RBI Act s.45ZL), so possibly on the next
fiscal year's page.

**When it runs:**
- watch, on an MPC decision day until the resolution is stored: one look at the feed and the current Annual Policy
  page per poll (conditional requests), then the resolution once it appears; never the minutes;
- daily (every 20 hours): any rate decision of the last 60 days released without a stored resolution, or not yet
  marked held; and its minutes, once a page or the feed links them;
- backfill (`--task policy.rbi_documents [--since] [--until]`): every rate decision in the range, from the Dec 2018
  baseline, and the minutes of each one after the baseline (the baseline is its decision document only: Spec 01 §2).
  A document already stored is read back from its raw file, not fetched again, and an unchanged one writes nothing
  (B8); each meeting and each minutes loads in its own savepoint (Arch 02 §1.8).

**What a resolution stores:** the raw file (through the fetcher), the document (its canonical text), its chunks, the
diff against the previous meeting's resolution, a rate row per instrument it states (repo; reverse repo before 8 Apr
2022, SDF from then; MSF; Bank Rate; never CRR: D5), each with its change against the previous meeting and the
sentence's span, the votes, the CPI and GDP projections (core.forecast, vintage = the decision date; a target the
regex reads replaces an AI-extracted row for it, since policy.ai_batch's projection_extraction is only the fallback
where the regex failed), and the meeting
marked `held` and published with its document. If the previous meeting's resolution isn't stored, it's stored too,
unpublished, as the diff's base and for the change. The baseline's changes come from its own decision sentences.
The first SDF row's change is measured against the reverse repo it replaced as the corridor floor (+40 bps on 8 Apr
2022), with a `crosscheck` note. A corridor rate the resolution says it moved "from" a value other than the
previous meeting's (or by a different amount) was moved between meetings (17 Apr 2020: D5, Pass 2): the row's change
is against the rate in force before this meeting, as the resolution states it (−40 bps on 22 May 2020, not −65), with
a `crosscheck` note naming what the previous meeting set, and `between_meetings` (W) lists it. The repo never moves
between meetings: a repo that does fails `repo_change` (B).

**Votes** (Spec 01 §2; Anand's decisions of 23 Oct 2026, issue #15): what rbi_resolution.member_votes reads, the
names matched to config/members.yaml. A resolution that names none of its members (only the Dec 2018 baseline)
leaves its unanimous votes to D4's roster inference, which is on only once the roster has matched the six members'
statements and votes in every 2019→ minutes (`roster_matches_minutes`, computed by a backfill that holds them all;
until then, and if it fails, the votes wait: `votes_wait`, W; Prompt 01 §4's decided fallback).

**What the minutes store** (part 2): the raw file, the document (its chunks carry each member's statement as their
speaker), published, and the votes reconciled with the resolution's. The minutes' Yes/No table is the statutory record
(Spec 01 §2): a rate vote the resolution states that the table contradicts is replaced from the table, and so is a
stance vote where the resolution's copy in the minutes names different stance dissenters; either raises
`votes_changed:<slug>` (open for a week) and `minutes_changed_vote` (W). A member the resolution gives no rate vote
gets the table's. A "No" voter's preferred change comes from the resolution's sentence (the table doesn't give it).
Checks (B): the page is these minutes, with six votes and six statements; the vote is on this decision (its move and
the rate it names); a majority voted for the decision; every member is on the roster. A row the minutes write is
never overwritten from the resolution again (part 1's rule).

**Press-release numbers** (Brief §6.2; Anand, 24 Oct 2026; issue #15): every document's title carries RBI's number
where its page has one ("MPC minutes, 7 Oct 2026 · Press Release: 2026-2027/1361"), so the site names the document
instead of linking RBI's page. A stored document's title is brought up to date when it's read again.

**Alerts:** a failed resolution or minutes (a backfill's, one each), resolved once it's published; the minutes still
missing 15 days after a meeting (s.45ZL: published on the fourteenth day), resolved once stored; `votes_changed`; and
the old site unreachable for 3 days (`rbi_site_unreachable`: raised by a failing run whose fetch couldn't reach
rbi.org.in, once the failures since the last good run span 3 days; resolved by the next good run).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from ft.engines.document.chunk import chunk_text
from ft.engines.document.diff import DIFF_VERSION, diff_texts
from ft.engines.document.store import DocumentRef, save_chunks, save_diff, save_version
from ft.engines.document.text import ExtractError
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register
from ft.policy import dates, members, rbi_calendar, rbi_minutes, rbi_resolution
from ft.policy.meetings import FIRST_DAY
from ft.policy.rbi_minutes import Minutes, MinutesError
from ft.policy.rbi_resolution import Resolution, ResolutionError, Vote

INDIA = ZoneInfo("Asia/Kolkata")
DOC_TYPE = "mpc_resolution"
MINUTES_TYPE = "mpc_minutes"
CATCH_UP = timedelta(days=60)            # daily: rate decisions this recent without a stored resolution or minutes
CLOSED_AFTER = timedelta(days=60)        # a fiscal year's page is final this long after 31 March (as policy.calendar)
MINUTES_DUE = timedelta(days=14)         # RBI Act s.45ZL: the minutes on the fourteenth day after the meeting
CHANGED_ALERT = timedelta(days=7)        # how long `votes_changed:<slug>` stays open
UNREACHABLE_ALERT = timedelta(days=3)    # Arch 02 §2: the old site unreachable this long
SERIES = {"cpi": "rbi.proj_cpi", "gdp": "rbi.proj_gdp"}
SDF_NOTE = "first SDF: change measured against the reverse repo it replaced as the corridor floor (Spec 01 §2)"
GATE = "roster_matches_minutes"


def _bps(points: Decimal) -> int:
    v = points * 100
    if v != v.to_integral_value():
        raise ResolutionError(f"{points} percentage points isn't a whole number of basis points")
    return int(v)


def fiscal_end(d: date) -> int:
    """The fiscal year (April to March) holding `d`, by its end year: the Annual Policy page's `hdnYear` value."""
    return d.year + 1 if d.month >= 4 else d.year


class SiteUnreachable(Exception):
    """rbi.org.in didn't answer (a network error after the retries, 403, 418, 429 or 5xx): the run fails, and three
    days of it raise `rbi_site_unreachable` (Arch 02 §2)."""


def _unreachable(e: FetchError) -> bool:
    if e.status is None:
        return "attempts" in str(e)              # a network error on every attempt (not an unlisted host)
    return e.status in (403, 418, 429) or e.status >= 500


@dataclass(frozen=True)
class Meeting:
    event_id: int
    slug: str
    decision_date: date
    occurs_at: datetime | None
    status: str
    is_published: bool
    kind: str
    meeting_no: int | None
    resolution_id: int | None            # the latest stored resolution version with text
    resolution_raw: int | None
    resolution_url: str | None
    has_rates: bool                      # its rate rows are stored
    baseline: bool                       # the last meeting before 2019 (its changes come from its own sentences)
    minutes_due_on: date | None = None
    minutes_id: int | None = None        # the latest stored minutes version with text
    minutes_raw: int | None = None
    minutes_url: str | None = None

    def released(self, now: datetime) -> bool:
        return self.occurs_at <= now if self.occurs_at else self.decision_date < now.astimezone(INDIA).date()

    @property
    def due(self) -> date:
        return self.minutes_due_on or self.decision_date + MINUTES_DUE


@dataclass(frozen=True)
class Page:
    url: str
    how: str                             # 'annual policy' | 'rss' | 'stored'
    raw_file_id: int | None
    body: bytes


@dataclass
class Fetched:
    now: datetime
    workflow: str
    meetings: list[Meeting]              # every stored MPC rate decision, oldest first
    targets: list[str]                   # slugs to load, oldest first
    pages: dict[str, Page]               # by slug: targets and the previous resolutions they need
    links: dict[date, str]               # resolution links on the Annual Policy pages read, by decision date
    rss: dict[date, str]                 # resolution links in the feed, by decision date
    missing: list[str] = field(default_factory=list)
    minutes: dict[str, Page] = field(default_factory=dict)          # by slug: minutes to load
    minutes_resolutions: dict[str, Page] = field(default_factory=dict)  # by slug: the resolution each goes with
    minutes_links: dict[date, str] = field(default_factory=dict)    # on the Annual Policy pages read, by last day
    not_found: list[str] = field(default_factory=list)


class MeetingFailed(Exception):
    def __init__(self, checks: list[Check]):
        self.checks = checks


@dataclass
class Outcome:
    slug: str
    status: str                          # 'loaded' | 'waiting' | 'failed'
    checks: list[Check] = field(default_factory=list)
    written: int = 0
    resolution: Resolution | None = None
    how: str | None = None
    rates: int = 0
    votes: int = 0
    rate_votes: int = 0
    dissents: int = 0
    projections: int = 0


@dataclass
class MinutesOutcome:
    slug: str
    status: str                          # 'loaded' | 'failed'
    checks: list[Check] = field(default_factory=list)
    written: int = 0
    external_key: str | None = None
    char_count: int | None = None
    how: str | None = None
    votes: int | None = None             # vote rows the meeting holds after the minutes (None: the votes wait)
    from_minutes: int = 0                # rows the minutes wrote (filled or replaced)
    differ: list[str] = field(default_factory=list)    # members whose vote the minutes record differently
    changed: list[str] = field(default_factory=list)   # those whose stored vote this run changed to the minutes'


def _today(now: datetime) -> date:
    return now.astimezone(INDIA).date()


def _day(d: date) -> str:
    return f"{d.day} {d:%b} {d.year}"


def _title(d: date) -> str:
    return f"MPC resolution, {_day(d)}"


def _minutes_title(d: date) -> str:
    return f"MPC minutes, {_day(d)}"


def _rss_date(item: rbi_resolution.RssItem) -> date | None:
    """The decision date an RSS item is for: the meeting's last day in its title, else its publication date."""
    tail = re.split(r"Resolution of the Monetary Policy Committee(?:\s*\(MPC\))?", item.title, maxsplit=1,
                    flags=re.IGNORECASE)[-1]
    if re.search(r"\d{4}", tail):
        try:
            return dates.span(tail)[1]
        except dates.DateParseError:
            pass
    return item.published.astimezone(INDIA).date() if item.published else None


def _latest(alias: str, doc_type: str) -> str:
    return f"""left join lateral (select x.id, x.raw_file_id, x.source_url from core.document x
                                   where x.event_id = e.id and x.doc_type = '{doc_type}' and x.text_sha256 is not null
                                   order by x.version_no desc limit 1) {alias} on true"""


@register
class RbiDocuments(Task):
    key = "policy.rbi_documents"
    members_path: Path = members.CONFIG   # tests point this at their own file
    first_day: date = FIRST_DAY           # the meeting before this is the baseline; tests set their own
    scope: frozenset[str] | None = None   # tests: only these meetings
    D4_ROSTER: bool | None = None         # D4's roster inference: None = the minutes check decides; tests force it

    def __init__(self):
        self.result: dict = {}

    # ------------------------------------------------------------------ fetch (no transaction)
    def _meetings(self, db) -> list[Meeting]:
        rows = db.execute(
            f"""select e.id, e.slug, coalesce(e.ends_on, e.starts_on), e.occurs_at, e.status, e.is_published, m.kind,
                       m.meeting_no, d.id, d.raw_file_id, d.source_url,
                       exists (select 1 from policy.rate_setting r where r.meeting_event_id = e.id),
                       m.minutes_due_on, mi.id, mi.raw_file_id, mi.source_url
                  from core.event e
                  join core.entity b on b.id = e.entity_id and b.kind = 'central_bank' and b.slug = 'rbi'
                  join policy.meeting m on m.event_id = e.id
                  {_latest("d", DOC_TYPE)}
                  {_latest("mi", MINUTES_TYPE)}
                 where e.event_type = 'policy_meeting' and m.is_rate_decision and e.status <> 'cancelled'
                   and e.slug ~ '^mpc-[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'
                 order by coalesce(e.ends_on, e.starts_on), e.id""").fetchall()
        rows = [r for r in rows if self.scope is None or r[1] in self.scope]
        out = [Meeting(*r[:12], baseline=False, minutes_due_on=r[12], minutes_id=r[13], minutes_raw=r[14],
                       minutes_url=r[15]) for r in rows]
        if out and out[0].decision_date < self.first_day:
            out[0] = replace(out[0], baseline=True)
        return out

    def _annual_pages(self, f, years: set[int], current_too: bool, today: date) -> rbi_calendar.Calendar:
        """What the Annual Policy pages for these fiscal years (by end year) list: the current page as served, earlier
        years from policy.calendar's stored copies or by the page's form post."""
        if not years and not current_too:
            return rbi_calendar.Calendar((), {}, (), ())
        got = f.get(rbi_calendar.ANNUAL_POLICY, source="rbi")
        current = rbi_calendar.parse_page(got.body if got.body is not None else f.read(got.raw_file_id))
        pages = [current]
        offered = {int(v): label for label, v in rbi_calendar.fiscal_years(current, 2000)}
        latest = max(offered) if offered else fiscal_end(today)
        for y in sorted(years):
            if y >= latest:
                continue                                  # the page as served is the current fiscal year
            record = f"{rbi_calendar.ANNUAL_POLICY}#hdnYear={y}"
            stored = f.stored(record, source="rbi") if today > date(y, 3, 31) + CLOSED_AFTER else None
            if stored is None:
                if y not in offered:
                    raise rbi_calendar.PageError(f"the Annual Policy page's year selector doesn't offer {y - 1}-{y}")
                stored = f.post(rbi_calendar.ANNUAL_POLICY, rbi_calendar.year_form(current, str(y)), source="rbi",
                                record_url=record, media_type="text/html")
            page = rbi_calendar.parse_page(stored.body if stored.body is not None else f.read(stored.raw_file_id))
            if not page.groups or all(g.posted_on.year > y for g in page.groups):
                raise rbi_calendar.PageError(f"the form post for fiscal year {y - 1}-{y} returned another year")
            pages.append(page)
        return rbi_calendar.calendar(pages)

    def fetch(self, ctx: Context) -> Fetched:
        try:
            return self._fetch(ctx)
        except FetchError as e:
            if _unreachable(e):
                raise SiteUnreachable(f"rbi.org.in unreachable: {e}") from e
            raise

    def _fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        allm = self._meetings(f.db)
        by_slug = {m.slug: m for m in allm}
        now, wf = ctx.now, ctx.workflow
        today = _today(now)
        until = ctx.until or today
        if ctx.meeting is not None:                                   # watch: the meeting being watched
            targets = [by_slug[ctx.meeting.slug]] if ctx.meeting.slug in by_slug else []
            targets = [m for m in targets if m.resolution_id is None]
        elif wf == "backfill":
            targets = [m for m in allm if m.released(now) and m.decision_date <= until
                       and (ctx.since is None or m.decision_date >= ctx.since)]
        else:                                                         # daily, refresh: catch up, and retry a meeting
            targets = [m for m in allm if (m.resolution_id is None or m.status != "held") and m.released(now)
                       and m.decision_date >= today - CATCH_UP]
        # Minutes (never in watch; never the baseline's): the backfill's range, read back where stored; the daily run's
        # last 60 days, where not yet stored.
        if ctx.meeting is not None or wf == "watch":
            docs: list[Meeting] = []
        elif wf == "backfill":
            docs = [m for m in allm if not m.baseline and m.released(now) and m.decision_date <= until
                    and (ctx.since is None or m.decision_date >= ctx.since)]
        else:
            docs = [m for m in allm if not m.baseline and m.released(now) and m.minutes_id is None
                    and m.decision_date >= today - CATCH_UP]
        slugs = [m.slug for m in targets]
        needed = []                                                   # previous resolutions the targets need
        for m in targets:
            i = allm.index(m)
            p = allm[i - 1] if i else None
            if p is not None and p.slug not in slugs and p not in needed and not (p.has_rates and p.resolution_id):
                needed.append(p)                                      # its rates aren't stored: read its page
        to_get = [m for m in needed + targets if m.resolution_id is None]
        # Minutes to find (published on the fourteenth day, never before it: the 49 saved, Dec 2018 to Oct 2026), on the
        # pages after the meeting.
        to_link = [m for m in docs if m.minutes_id is None and m.due <= today]

        # Daily and refresh runs always read the current page (one conditional request), so a resolution first taken
        # from the RSS is checked against the page's link once the page lists it (`annual_policy_link`).
        years = {fiscal_end(m.decision_date) for m in to_get}
        years |= {y for m in to_link for y in (fiscal_end(m.decision_date), fiscal_end(m.due))}
        cal = self._annual_pages(f, years, wf not in ("backfill", "watch") or bool(to_get) or bool(to_link), today)
        links = {r.decision_date: rbi_resolution.page_url(r.url) for r in cal.resolutions}
        minutes_links = {d: rbi_resolution.page_url(u) for d, u in cal.minutes_links.items()}
        rss: dict[date, str] = {}
        rss_minutes: dict[date, str] = {}
        awaited = [m for m in to_link if m.decision_date not in minutes_links and m.due <= today]
        if (to_get or awaited) and wf != "backfill":                  # the trigger: only while a document is awaited
            got = f.get(rbi_resolution.RSS, source="rbi")
            for item in rbi_resolution.parse_rss(got.body if got.body is not None else f.read(got.raw_file_id)):
                if item.is_resolution and (d := _rss_date(item)) is not None:
                    rss.setdefault(d, rbi_resolution.page_url(item.link))
                elif rbi_minutes.TITLE_MARK.lower() in item.title.lower() and "prid=" in item.link.lower():
                    try:
                        rss_minutes.setdefault(rbi_calendar.minutes_days(item.title)[1],
                                               rbi_resolution.page_url(item.link))
                    except dates.DateParseError:
                        pass

        out: dict[str, Page] = {}
        missing = []
        for m in needed + targets:
            if m.resolution_id is not None:
                out[m.slug] = Page(m.resolution_url, "stored", m.resolution_raw, f.read(m.resolution_raw))
                continue
            url, how = links.get(m.decision_date), "annual policy"
            if url is None and m.decision_date in rss:
                url, how = rss[m.decision_date], "rss"
            if url is None:
                missing.append(m.slug)
                continue
            got = f.get(url, source="rbi", allow=(404,))
            if got.status == 404:
                missing.append(m.slug)
                continue
            out[m.slug] = Page(url, how, got.raw_file_id, got.body if got.body is not None else f.read(got.raw_file_id))
        fetched = Fetched(now, wf, allm, slugs, out, links, rss, missing, minutes_links=minutes_links)

        for m in docs:
            if m.minutes_id is not None:                              # backfill: read back
                page = Page(m.minutes_url, "stored", m.minutes_raw, f.read(m.minutes_raw))
            else:
                url, how = minutes_links.get(m.decision_date), "annual policy"
                if url is None and m.decision_date in rss_minutes:
                    url, how = rss_minutes[m.decision_date], "rss"
                if url is None:
                    continue                                          # not out yet (the alert counts the days)
                got = f.get(url, source="rbi", allow=(404,))
                if got.status == 404:
                    fetched.not_found.append(f"{m.slug}: minutes {url}")
                    continue
                page = Page(url, how, got.raw_file_id, got.body if got.body is not None else f.read(got.raw_file_id))
            fetched.minutes[m.slug] = page
            if m.slug in out:                                         # the resolution it goes with (the votes)
                fetched.minutes_resolutions[m.slug] = out[m.slug]
            elif m.resolution_raw is not None:
                fetched.minutes_resolutions[m.slug] = Page(m.resolution_url, "stored", m.resolution_raw,
                                                           f.read(m.resolution_raw))
        return fetched

    # ------------------------------------------------------------------ load (one transaction; a savepoint per document)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        roster = members.load(self.members_path)
        ids, written = members.sync(tx, roster)
        self._parsed: dict[str, Minutes | str] = {}
        self._d4, gate = self._roster_gate(tx, f, roster)
        outcomes = []
        for slug in f.targets:
            o = Outcome(slug, "waiting")
            try:
                with tx.transaction():                               # a savepoint: a failed meeting rolls back alone
                    o = self._meeting(tx, f, slug, roster, ids, task_run_id)
            except MeetingFailed as e:
                o = Outcome(slug, "failed", e.checks)
            except Exception as e:  # in a backfill, one meeting's error mustn't stop the others
                if f.workflow != "backfill":
                    raise
                o = Outcome(slug, "failed", [Check("load_error", False, f"{slug}: {type(e).__name__}: {e}")])
            outcomes.append(o)
            written += o.written
        order = {m.slug: i for i, m in enumerate(f.meetings)}
        docs: list[MinutesOutcome] = []
        for slug in sorted(f.minutes, key=order.__getitem__):
            d = MinutesOutcome(slug, "failed")
            try:
                with tx.transaction():
                    d = self._minutes(tx, f, slug, roster, ids)
            except MeetingFailed as e:
                d = MinutesOutcome(slug, "failed", e.checks)
            except Exception as e:
                if f.workflow != "backfill":
                    raise
                d = MinutesOutcome(slug, "failed", [Check("load_error", False, f"{slug} minutes: {type(e).__name__}: {e}")])
            docs.append(d)
            written += d.written
        self.result = {"outcomes": outcomes, "docs": docs, "gate": gate, "d4": self._d4, "today": _today(f.now),
                       "roster": sum(m.bank == "RBI" for m in roster.members.values())}
        return written

    def _fail(self, name: str, detail: str, earlier: list[Check] = ()):
        raise MeetingFailed([*earlier, Check(name, False, detail)])

    def _meeting(self, tx, f: Fetched, slug: str, roster: members.Roster, ids: dict[str, int], tr: int) -> Outcome:
        m = next(x for x in f.meetings if x.slug == slug)
        page = f.pages.get(slug)
        if page is None:
            if f.workflow == "backfill":
                self._fail("resolution_link", f"{slug}: no resolution link on the Annual Policy page")
            return Outcome(slug, "waiting")
        try:
            res = rbi_resolution.parse(page.body, page.url)
        except (ResolutionError, ValueError) as e:
            self._fail("resolution_page", f"{slug}: {e}")
        checks = [Check(r.name, r.passed, f"{slug}: {r.detail}", r.blocking)
                  for r in rbi_resolution.checks(res, m.decision_date)]
        if any(c.blocking and not c.passed for c in checks):
            raise MeetingFailed(checks)
        o = Outcome(slug, "loaded", checks, resolution=res, how=page.how)
        n = 0

        # The previous meeting's rates (for the changes) and its resolution (the diff's base).
        previous, base_id, n_base = self._previous(tx, f, m, checks)
        n += n_base
        changes, notes = self._changes(m, res, previous, checks)
        decision_bps = changes["repo"]

        # The document (titled with RBI's press-release number), its chunks and its diff.
        title = rbi_resolution.titled(_title(m.decision_date), res.press_release)
        ref = DocumentRef(DOC_TYPE, self._rbi_id(tx), self._source_id(tx), res.external_key, res.url, title,
                          m.event_id, page.raw_file_id, m.occurs_at)
        saved = save_version(tx, ref, res.text)
        doc_id = saved.document_id
        n += 1 if saved.new_text else 0
        n += self._retitle(tx, doc_id, title)
        if saved.new_text or not tx.execute("select 1 from core.chunk where document_id = %s limit 1",
                                            (doc_id,)).fetchone():
            n += save_chunks(tx, doc_id, res.text.text, chunk_text(res.text))
        if base_id is not None:
            have = tx.execute("select base_document_id, diff_version from core.document_diff where document_id = %s",
                              (doc_id,)).fetchone()
            if saved.new_text or have != (base_id, DIFF_VERSION):
                save_diff(tx, doc_id, base_id, diff_texts(self._text(tx, base_id), res.text.text))
                n += 1

        # One rate row per instrument the resolution states, the sentence as its span.
        for instrument, rate in res.rates.items():
            s, e = rate.sentence
            n += tx.execute(
                """insert into policy.rate_setting (meeting_event_id, instrument, value_pct, change_bps, document_id,
                                                    char_start, char_end, crosscheck)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (meeting_event_id, instrument) do update set value_pct = excluded.value_pct,
                     change_bps = excluded.change_bps, document_id = excluded.document_id,
                     char_start = excluded.char_start, char_end = excluded.char_end, crosscheck = excluded.crosscheck
                   where (policy.rate_setting.value_pct, policy.rate_setting.change_bps,
                          policy.rate_setting.document_id, policy.rate_setting.char_start,
                          policy.rate_setting.char_end, policy.rate_setting.crosscheck)
                         is distinct from (excluded.value_pct, excluded.change_bps, excluded.document_id,
                                           excluded.char_start, excluded.char_end, excluded.crosscheck)""",
                (m.event_id, instrument, rate.value, changes[instrument], doc_id, s, e, notes.get(instrument))).rowcount
        stale = tx.execute("""delete from policy.rate_setting where meeting_event_id = %s and document_id = %s
                               and not (instrument = any(%s))""", (m.event_id, doc_id, list(res.rates))).rowcount
        n += stale
        o.rates = len(res.rates)

        # Votes.
        rows, nv = self._votes(tx, m, res, decision_bps, roster, ids, doc_id, checks)
        n += nv
        o.votes, o.rate_votes, o.dissents = len(rows), sum(v.rate_stated for v in rows), sum(v.is_dissent for v in rows)

        # Projections (core.forecast), vintage = the decision date.
        n += self._projections(tx, m, res, doc_id, tr, checks)
        o.projections = len(res.projections)

        # (W) The meeting number the resolution states (from 2025) against the calendar's.
        if res.meeting_no is not None and m.meeting_no is not None:
            checks.append(Check("meeting_no", res.meeting_no == m.meeting_no,
                                f"{slug}: the resolution says no. {res.meeting_no}, the calendar no. {m.meeting_no}",
                                blocking=False))

        # The meeting is held and published with its document; the projection series are published with their rows.
        n += tx.execute("""update core.event set status = 'held', is_published = true, updated_at = now()
                            where id = %s and (status, is_published) is distinct from ('held', true)""",
                        (m.event_id,)).rowcount
        n += tx.execute("update core.document set is_published = true where id = %s and not is_published",
                        (doc_id,)).rowcount
        if res.projections:
            n += tx.execute("update core.series set is_published = true where key = any(%s) and not is_published",
                            (sorted({SERIES[p.variable] for p in res.projections}),)).rowcount
        o.checks, o.written = checks, n
        return o

    @staticmethod
    def _retitle(tx, doc_id: int, title: str) -> int:
        """A stored document's title brought up to date (the press-release number, from part 2)."""
        return tx.execute("update core.document set title = %s where id = %s and title is distinct from %s",
                          (title, doc_id, title)).rowcount

    def _previous(self, tx, f: Fetched, m: Meeting, checks: list[Check]) -> tuple[dict | None, int | None, int]:
        """(the previous meeting's rates by instrument, its resolution's document id, rows written storing it)."""
        if m.baseline:
            return None, None, 0
        i = next(k for k, x in enumerate(f.meetings) if x.slug == m.slug)
        if i == 0:
            self._fail("previous_rates", f"{m.slug}: no earlier MPC meeting is stored (the baseline is missing)",
                       checks)
        p = f.meetings[i - 1]
        stored = dict(tx.execute("select instrument, value_pct from policy.rate_setting where meeting_event_id = %s",
                                 (p.event_id,)).fetchall())
        row = tx.execute("""select id from core.document where event_id = %s and doc_type = %s
                             and text_sha256 is not null order by version_no desc limit 1""",
                         (p.event_id, DOC_TYPE)).fetchone()
        if stored and row:
            return stored, row[0], 0
        page = f.pages.get(p.slug)
        if page is None:
            self._fail("previous_rates", f"{m.slug}: {p.slug}'s resolution is neither stored nor on the Annual Policy "
                       "pages read", checks)
        try:
            prev = rbi_resolution.parse(page.body, page.url)
        except (ResolutionError, ValueError) as e:
            self._fail("previous_rates", f"{m.slug}: {p.slug}'s resolution: {e}", checks)
        bad = [r for r in rbi_resolution.checks(prev, p.decision_date) if r.blocking and not r.passed]
        if bad:
            self._fail("previous_rates", f"{m.slug}: {p.slug}'s resolution fails "
                       + ", ".join(f"{r.name} ({r.detail})" for r in bad), checks)
        rates = stored or {k: r.value for k, r in prev.rates.items()}
        if row:
            return rates, row[0], 0
        ref = DocumentRef(DOC_TYPE, self._rbi_id(tx), self._source_id(tx), prev.external_key, prev.url,
                          rbi_resolution.titled(_title(p.decision_date), prev.press_release), p.event_id,
                          page.raw_file_id, p.occurs_at)
        saved = save_version(tx, ref, prev.text)
        n = 1 if saved.new_text else 0
        if saved.new_text:
            n += save_chunks(tx, saved.document_id, prev.text.text, chunk_text(prev.text))
        return rates, saved.document_id, n

    def _changes(self, m: Meeting, res: Resolution, previous: dict | None,
                 checks: list[Check]) -> tuple[dict[str, int], dict[str, str]]:
        """Each instrument's change in bps against the previous meeting (the baseline: its own sentences), and the
        crosscheck notes. The repo's change must agree with what the resolution states (B)."""
        changes: dict[str, int] = {}
        notes: dict[str, str] = {}
        odd = []
        for name, r in res.rates.items():
            if previous is None:                                      # the baseline: from its own decision sentence
                if r.stated_bps is not None:
                    chg = r.stated_bps
                elif r.stated_from is not None:
                    chg = _bps(r.value - r.stated_from)
                elif r.direction == 0:
                    chg = 0
                else:
                    self._fail("baseline_change", f"{m.slug}: the baseline's {name} change isn't stated", checks)
                changes[name] = chg
                continue
            prev = previous.get(name)
            first_sdf = prev is None and name == "sdf" and "reverse_repo" in previous
            if first_sdf:
                prev = previous["reverse_repo"]
                notes[name] = f"{SDF_NOTE}: reverse repo {previous['reverse_repo']:g} per cent"
            if prev is None:
                self._fail("previous_rates", f"{m.slug}: the previous meeting has no {name} rate", checks)
            prev = Decimal(prev)
            chg = _bps(r.value - prev)
            if name == "repo":
                if r.stated_from is not None and r.stated_from != prev:
                    odd.append(f"repo from {r.stated_from} per cent, the previous meeting {prev}")
                if r.stated_bps is not None and r.stated_bps != chg:
                    odd.append(f"repo by {r.stated_bps:+d} bps, computed {chg:+d}")
            elif not first_sdf:
                # A corridor rate moved between meetings (17 Apr 2020: D5): the change is against the rate in force
                # before this meeting, as the resolution states it (Arch 01a: "vs the previous setting"; decided by
                # Claude, Anand delegated, issue #15, 23 Oct 2026), and the note keeps what the last meeting set.
                stated = (_bps(r.value - r.stated_from) if r.stated_from is not None
                          else r.stated_bps if r.stated_bps is not None else chg)
                if stated != chg:
                    notes[name] = (f"{stated:+d} bps from the rate in force, as the resolution states; the previous MPC "
                                   f"meeting set {prev.normalize():f} per cent ({chg:+d} bps from it): moved between meetings "
                                   "(Spec 01 D5)")
                    chg = stated
            changes[name] = chg
        repo = res.repo
        chg = changes["repo"]
        if repo.direction is not None and (chg > 0) - (chg < 0) != repo.direction:
            odd.append(f"the decision's verb says {['cut', 'no change', 'raise'][repo.direction + 1]}, "
                       f"the change is {chg:+d} bps")
        checks.append(Check("repo_change", not odd, f"{m.slug}: repo {repo.value} ({chg:+d} bps)"
                            + ("; " + "; ".join(odd) if odd else "")))
        if odd:
            raise MeetingFailed(checks)
        between = [f"{k}: {v}" for k, v in notes.items() if "between meetings" in v]
        checks.append(Check("between_meetings", not between, f"{m.slug}: " + "; ".join(between), blocking=False))
        return changes, notes

    def _roster_names(self, res: Resolution, roster: members.Roster, day: date, checks: list[Check] | None,
                      slug: str) -> tuple[str, ...]:
        """D4: the roster's six on the day, for a resolution that names none of its members, while the inference is on
        (`_roster_gate`); else none."""
        if not self._d4 or res.votes.members:
            return ()
        names = tuple(x.name for x in roster.voters_on("RBI", day))
        if len(names) != rbi_minutes.MEMBERS:
            if checks is not None:
                checks.append(Check("roster_six", False, f"{slug}: the roster lists {len(names)} members on {day}; "
                                    "unanimous votes wait for the minutes", blocking=False))
            return ()
        return names

    def _votes(self, tx, m: Meeting, res: Resolution, decision_bps: int, roster: members.Roster, ids: dict[str, int],
               doc_id: int, checks: list[Check]) -> tuple[list[Vote], int]:
        votes = rbi_resolution.member_votes(res, decision_bps,
                                            self._roster_names(res, roster, m.decision_date, checks, m.slug))
        unknown = sorted({v.name for v in votes if roster.match("RBI", v.name) is None}
                         | {n for n in res.votes.members if roster.match("RBI", n) is None})
        checks.append(Check("voters_on_roster", not unknown, f"{m.slug}: not in config/members.yaml: "
                            + ", ".join(unknown) if unknown else f"{m.slug}: {len(votes)} members"))
        if unknown:
            raise MeetingFailed(checks)
        want: dict[int, Vote] = {}
        dupes = []
        for v in votes:
            mid = ids[roster.match("RBI", v.name).slug]
            if mid in want:
                dupes.append(v.name)
            want[mid] = v
        rate = [v for v in votes if v.rate_stated]
        majority = sum(v.voted_change_bps == decision_bps for v in rate)
        problems = [p for p in res.problems if "vote" in p] + [f"{d} appears twice" for d in dupes]
        if len(rate) == 6 and majority * 2 <= len(rate):
            problems.append(f"only {majority} of {len(rate)} rate votes are for the decision ({decision_bps:+d} bps)")
        checks.append(Check("votes_consistent", not problems, f"{m.slug}: " + ("; ".join(problems) if problems else
                            f"rate {majority}-{len(rate) - majority}, {sum(v.is_dissent for v in votes)} dissents")))
        if problems:
            raise MeetingFailed(checks)
        checks.append(Check("votes_wait", len(rate) >= 6, f"{m.slug}: {len(rate)} rate votes in the resolution; the "
                            "rest wait for the minutes", blocking=False))
        odd = []
        for v in votes:
            t = roster.match("RBI", v.name).term_on(m.decision_date)
            if t is None:
                odd.append(f"{v.name}: no roster term on {m.decision_date}")
        checks.append(Check("voter_terms", not odd, f"{m.slug}: " + "; ".join(odd), blocking=False))

        n = 0
        stored = {r[0]: r[1] for r in tx.execute(
            "select member_id, document_id from policy.vote where meeting_event_id = %s", (m.event_id,)).fetchall()}
        for gone in [k for k, d in stored.items() if k not in want and d == doc_id]:
            n += tx.execute("delete from policy.vote where meeting_event_id = %s and member_id = %s",
                            (m.event_id, gone)).rowcount
        for mid, v in want.items():
            if mid in stored and stored[mid] != doc_id:
                continue                                       # written from another document (the minutes)
            n += self._write_vote(tx, m.event_id, mid, v.voted_change_bps, v.stance_vote, v.is_dissent, v.dissent_text,
                                  doc_id, v.span)
        return list(want.values()), n

    @staticmethod
    def _write_vote(tx, event_id: int, member_id: int, bps: int | None, stance: str | None, dissent: bool,
                    text: str | None, doc_id: int, span: tuple[int, int]) -> int:
        s, e = span
        return tx.execute(
            """insert into policy.vote (meeting_event_id, member_id, voted_change_bps, stance_vote, is_dissent,
                                        dissent_text, document_id, char_start, char_end)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (meeting_event_id, member_id) do update set voted_change_bps = excluded.voted_change_bps,
                 stance_vote = excluded.stance_vote, is_dissent = excluded.is_dissent,
                 dissent_text = excluded.dissent_text, document_id = excluded.document_id,
                 char_start = excluded.char_start, char_end = excluded.char_end
               where (policy.vote.voted_change_bps, policy.vote.stance_vote, policy.vote.is_dissent,
                      policy.vote.dissent_text, policy.vote.document_id, policy.vote.char_start,
                      policy.vote.char_end)
                     is distinct from (excluded.voted_change_bps, excluded.stance_vote, excluded.is_dissent,
                                       excluded.dissent_text, excluded.document_id, excluded.char_start,
                                       excluded.char_end)""",
            (event_id, member_id, bps, stance, dissent, text, doc_id, s, e)).rowcount

    def _projections(self, tx, m: Meeting, res: Resolution, doc_id: int, tr: int, checks: list[Check]) -> int:
        n = 0
        series = dict(tx.execute("select key, id from core.series where key = any(%s)",
                                 (list(SERIES.values()),)).fetchall())
        keep = set()
        for p in res.projections:
            sid = series[SERIES[p.variable]]
            keep.add((sid, p.target_start, p.target_end))
            s, e = p.span
            n += tx.execute(
                """insert into core.forecast (series_id, vintage_date, target_start, target_end, target_label, value,
                                              value_low, value_high, method, document_id, char_start, char_end,
                                              task_run_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, 'parsed', %s, %s, %s, %s)
                   on conflict (series_id, vintage_date, target_start, target_end) do update set
                     target_label = excluded.target_label, value = excluded.value, value_low = excluded.value_low,
                     value_high = excluded.value_high, method = excluded.method, document_id = excluded.document_id,
                     char_start = excluded.char_start, char_end = excluded.char_end, task_run_id = excluded.task_run_id,
                     ai_output_id = null
                   where core.forecast.method = 'ai_extracted'      -- the regex first: a target it now reads replaces
                      or (core.forecast.method = 'parsed'           -- the AI's row (B7 part 2's fallback)
                          and (core.forecast.target_label, core.forecast.value, core.forecast.value_low,
                               core.forecast.value_high, core.forecast.document_id, core.forecast.char_start,
                               core.forecast.char_end)
                              is distinct from (excluded.target_label, excluded.value, excluded.value_low,
                                                excluded.value_high, excluded.document_id, excluded.char_start,
                                                excluded.char_end))""",
                (sid, m.decision_date, p.target_start, p.target_end, p.target_label, p.value, p.low, p.high, doc_id,
                 s, e, tr)).rowcount
        for sid, start, end in tx.execute(
                """select series_id, target_start, target_end from core.forecast
                    where vintage_date = %s and series_id = any(%s) and method = 'parsed' and document_id = %s""",
                (m.decision_date, list(series.values()), doc_id)).fetchall():
            if (sid, start, end) not in keep:
                n += tx.execute("""delete from core.forecast where series_id = %s and vintage_date = %s
                                    and target_start = %s and target_end = %s""",
                                (sid, m.decision_date, start, end)).rowcount
        cpi = sum(p.variable == "cpi" for p in res.projections)
        checks.append(Check("cpi_projection", cpi > 0 or m.kind != "scheduled",
                            f"{m.slug}: no CPI projection at a scheduled meeting (expected only at 2020's COVID "
                            "meetings and off-cycle meetings)", blocking=False))
        checks.append(Check("projection_unparsed", not res.unparsed, f"{m.slug}: {len(res.unparsed)} projection "
                            "sentences the regex couldn't read (projection_extraction, B7 part 2): "
                            + " | ".join(res.text.text[s:e][:160] for s, e in res.unparsed), blocking=False))
        return n

    # ------------------------------------------------------------------ minutes
    def _parse_minutes(self, f: Fetched, slug: str) -> Minutes | str:
        """The minutes page, parsed once per run; the error's text if it can't be read."""
        if slug not in self._parsed:
            page = f.minutes[slug]
            try:
                self._parsed[slug] = rbi_minutes.parse(page.body, page.url)
            except (MinutesError, ExtractError, ValueError) as e:
                self._parsed[slug] = str(e)
        return self._parsed[slug]

    @staticmethod
    def _who(roster: members.Roster, names) -> list[str]:
        return sorted(m.slug if (m := roster.match("RBI", n)) else f"?{n}" for n in names)

    def _roster_mismatch(self, mn: Minutes, roster: members.Roster, day: date) -> str | None:
        """None if the roster's members on the day are the six whose statements and votes the minutes hold (D4's
        check, Spec 01 §2); else what differs."""
        on = sorted(m.slug for m in roster.voters_on("RBI", day))
        said = self._who(roster, [s.name for s in mn.statements])
        voted = self._who(roster, [r.name for r in mn.table.rows] if mn.table else [])
        if len(on) == rbi_minutes.MEMBERS and on == said == voted:
            return None
        return f"the roster on {day}: {', '.join(on)}; statements: {', '.join(said)}; votes: {', '.join(voted)}"

    def _roster_gate(self, tx, f: Fetched, roster: members.Roster) -> tuple[bool, Check | None]:
        """Whether D4's roster inference is on in this run, and the backfill's check that decides it.

        Spec 01 §2: the inference is switched on only after the roster's six match the six statements in every 2019→
        minutes. A backfill that holds every 2019→ meeting's minutes (due by today) checks them all here, before the
        resolutions load, and records `roster_matches_minutes`; any other run uses the last recorded result. A mismatch
        keeps the inference off (Prompt 01 §4's decided fallback: the votes wait for the minutes) and lists the
        meetings that differ. Tests can force it (`D4_ROSTER`)."""
        today = _today(f.now)
        required = [m for m in f.meetings if not m.baseline and m.decision_date >= self.first_day
                    and m.released(f.now) and m.due < today]
        check = None
        if f.workflow == "backfill" and required and all(m.slug in f.minutes for m in required):
            differ = []
            for m in required:
                mn = self._parse_minutes(f, m.slug)
                why = mn if isinstance(mn, str) else self._roster_mismatch(mn, roster, m.decision_date)
                if why:
                    differ.append(f"{m.slug}: {why}")
            check = Check(GATE, not differ, "; ".join(differ) if differ else
                          f"the roster's six match the six statements and votes in all {len(required)} minutes "
                          f"since {required[0].decision_date}: D4's inference is on", blocking=False)
            on = not differ
        else:
            row = tx.execute(
                """select (c->>'passed')::boolean from ops.task_run t cross join jsonb_array_elements(t.checks) c
                    where t.task_key = %s and t.status = 'success' and c->>'name' = %s
                    order by t.finished_at desc nulls last, t.id desc limit 1""", (self.key, GATE)).fetchone()
            on = bool(row and row[0])
        return (self.D4_ROSTER if self.D4_ROSTER is not None else on), check

    def _minutes(self, tx, f: Fetched, slug: str, roster: members.Roster, ids: dict[str, int]) -> MinutesOutcome:
        m = next(x for x in f.meetings if x.slug == slug)
        page = f.minutes[slug]
        mn = self._parse_minutes(f, slug)
        if isinstance(mn, str):
            self._fail("minutes_page", f"{slug}: {mn}")
        checks = [Check(r.name, r.passed, f"{slug}: {r.detail}", r.blocking)
                  for r in rbi_minutes.checks(mn, m.decision_date)]
        if any(c.blocking and not c.passed for c in checks):
            raise MeetingFailed(checks)
        published = datetime.combine(mn.page_date, time(0), INDIA) if mn.page_date else None
        title = rbi_resolution.titled(_minutes_title(m.decision_date), mn.press_release)
        ref = DocumentRef(MINUTES_TYPE, self._rbi_id(tx), self._source_id(tx), mn.external_key, mn.url, title,
                          m.event_id, page.raw_file_id, published)
        saved = save_version(tx, ref, mn.text)
        doc_id, n = saved.document_id, (1 if saved.new_text else 0)
        n += self._retitle(tx, doc_id, title)
        if saved.new_text or not tx.execute("select 1 from core.chunk where document_id = %s limit 1",
                                            (doc_id,)).fetchone():
            n += save_chunks(tx, doc_id, mn.text.text, chunk_text(mn.text, root="Minutes", speakers="minutes"))
        n += tx.execute("update core.document set is_published = true where id = %s and not is_published",
                        (doc_id,)).rowcount
        o = MinutesOutcome(slug, "loaded", checks, external_key=mn.external_key, char_count=mn.text.char_count,
                           how=page.how)

        # (W) D4's check for this meeting: the roster's six are the six who spoke and voted.
        why = self._roster_mismatch(mn, roster, m.decision_date)
        o.checks.append(Check("minutes_roster", why is None, f"{slug}: {why}", blocking=False))
        n += self._minutes_votes(tx, f, m, mn, doc_id, roster, ids, o)
        o.written = n
        return o

    def _minutes_votes(self, tx, f: Fetched, m: Meeting, mn: Minutes, doc_id: int, roster: members.Roster,
                       ids: dict[str, int], o: MinutesOutcome) -> int:
        """The minutes' votes against the resolution's (Spec 01 §2, "RBI votes (D4)"): the table is the statutory record
        of the rate vote; the resolution's copy in the minutes, of the stance votes."""
        slug, table = m.slug, mn.table
        repo = tx.execute("""select value_pct, change_bps from policy.rate_setting
                              where meeting_event_id = %s and instrument = 'repo'""", (m.event_id,)).fetchone()
        res_page = f.minutes_resolutions.get(slug)
        if repo is None or res_page is None:
            o.checks.append(Check("votes_wait", False, f"{slug}: the resolution isn't stored, so the minutes' votes "
                                  "wait", blocking=False))
            return 0
        value, decision_bps = Decimal(repo[0]), repo[1]
        res = rbi_resolution.parse(res_page.body, res_page.url)

        # (B) The vote is on this decision, and a majority voted for it.
        problems = []
        sign = (decision_bps > 0) - (decision_bps < 0)
        if table.direction is not None and table.direction != sign:
            problems.append(f"the minutes' vote is on {table.heading!r}; the decision was {decision_bps:+d} bps")
        if table.rate is not None and table.rate != value:
            problems.append(f"the minutes' vote names {table.rate} per cent; the repo rate stored is {value}")
        direction = table.direction if table.direction is not None else sign
        unknown = [r.name for r in table.rows if roster.match("RBI", r.name) is None]
        o.checks.append(Check("voters_on_roster", not unknown, f"{slug} minutes: not in config/members.yaml: "
                              + ", ".join(unknown) if unknown else f"{slug} minutes: {len(table.rows)} members"))
        if unknown:
            raise MeetingFailed(o.checks)

        resv = {roster.match("RBI", v.name).slug: v for v in rbi_resolution.member_votes(
            res, decision_bps, self._roster_names(res, roster, m.decision_date, None, slug))
            if roster.match("RBI", v.name)}
        copy = None
        if mn.resolution is not None:
            copy = rbi_resolution.read_copy(mn.text, mn.resolution)
        copyv = {roster.match("RBI", v.name).slug: v for v in rbi_resolution.member_votes(copy, decision_bps)
                 if roster.match("RBI", v.name)} if copy is not None else {}
        o.checks.append(Check("minutes_resolution_copy", copy is not None and copy.stance is not None,
                              f"{slug}: the resolution's copy in the minutes " + (
                                  "wasn't found" if copy is None else "states no stance"), blocking=False))
        adopted = res.stance.label if res.stance and res.stance.label in rbi_resolution.VOCABULARY else None

        def dissent(stance: str | None) -> bool:
            return stance is not None and adopted is not None and stance != adopted

        n, majority, written = 0, 0, 0
        for row in table.rows:
            who = roster.match("RBI", row.name).slug
            rv, cv = resv.get(who), copyv.get(who)
            if row.yes:
                signed = row.magnitude * direction if row.magnitude is not None and direction else None
                if row.magnitude is not None and signed is None:
                    problems.append(f"{row.text}: a magnitude, but the vote's direction isn't stated")
                mbps = signed if signed is not None else decision_bps
            else:                                        # "No": the preference is the resolution's, where it states one
                mbps = (rv.voted_change_bps if rv and rv.rate_stated and rv.voted_change_bps is not None
                        and rv.voted_change_bps != decision_bps else None)
            majority += mbps == decision_bps
            if rv is None or not rv.rate_stated:
                rate_from = "minutes"                    # the resolution gives no rate vote: the table's (a fill)
                changed_rate = False
            elif row.yes:
                changed_rate = rv.voted_change_bps != mbps
                rate_from = "minutes" if changed_rate else "resolution"
            else:
                changed_rate = rv.voted_change_bps == decision_bps
                rate_from = "minutes" if changed_rate else "resolution"
            res_stance = rv.stance_vote if rv else None
            copy_stance = cv.stance_vote if cv else None
            changed_stance = copy is not None and dissent(res_stance) != dissent(copy_stance)
            if rate_from == "resolution" and not changed_stance:
                continue                                 # the minutes agree: the resolution's row stands
            if changed_rate or changed_stance:
                o.differ.append(who)
            bps = mbps if rate_from == "minutes" else rv.voted_change_bps
            rate_dissent = (not row.yes or bps != decision_bps) if rate_from == "minutes" else (
                rv.voted_change_bps is None or rv.voted_change_bps != decision_bps)
            stance = copy_stance if changed_stance else (res_stance if res_stance is not None else copy_stance)
            stance_src = cv if changed_stance or res_stance is None else rv
            texts = []
            if rate_dissent:
                texts.append(row.text if rate_from == "minutes" else rv.rate_text)
            if dissent(stance) and stance_src is not None:
                texts.append(stance_src.stance_text)
            span = row.span if rate_from == "minutes" else (cv.stance_span if cv and cv.stance_span else row.span)
            written += 1
            k = self._write_vote(tx, m.event_id, ids[who], bps, stance, rate_dissent or dissent(stance),
                                 "; ".join(t for t in texts if t) or None, doc_id, span)
            n += k
            if k and who in o.differ:
                o.changed.append(who)
        if majority * 2 <= len(table.rows):
            problems.append(f"only {majority} of {len(table.rows)} votes are for the decision ({decision_bps:+d} bps)")
        o.checks.append(Check("minutes_decision", not problems, f"{slug} minutes: " + (
            "; ".join(problems) if problems else f"{majority} of {len(table.rows)} for {decision_bps:+d} bps")))
        if problems:
            raise MeetingFailed(o.checks)
        o.checks.append(Check("minutes_changed_vote", not o.changed, f"{slug}: the minutes changed the stored votes "
                              f"of {', '.join(o.changed)}", blocking=False))
        o.from_minutes = written
        o.votes = tx.execute("select count(*) from policy.vote where meeting_event_id = %s", (m.event_id,)).fetchone()[0]
        return n

    def _text(self, tx, document_id: int) -> str:
        return tx.execute("select string_agg(body, '' order by chunk_no) from core.chunk where document_id = %s",
                          (document_id,)).fetchone()[0] or ""

    def _rbi_id(self, tx) -> int:
        return tx.execute("select id from core.entity where kind = 'central_bank' and slug = 'rbi'").fetchone()[0]

    def _source_id(self, tx) -> int:
        return tx.execute("select id from core.source where key = 'rbi'").fetchone()[0]

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        outcomes: list[Outcome] = self.result["outcomes"]
        docs: list[MinutesOutcome] = self.result["docs"]
        backfill = f.workflow == "backfill"
        out: list[Check] = []

        failed = [o for o in outcomes if o.status == "failed"]
        failed_docs = [d for d in docs if d.status == "failed"]
        if backfill:
            def listed(items):
                return "; ".join(f"{o.slug}: " + ", ".join(f"{c.name} ({c.detail.split(': ', 1)[-1]})"
                                                          for c in o.checks if c.blocking and not c.passed)
                                 for o in items)
            out.append(Check("meetings_failed", not failed, listed(failed), blocking=False))
            out.append(Check("minutes_failed", not failed_docs, listed(failed_docs), blocking=False))
        else:
            by_name: dict[str, list[str]] = {}
            for o in [*failed, *failed_docs]:
                for c in o.checks:
                    if c.blocking and not c.passed:
                        by_name.setdefault(c.name, []).append(c.detail)
            out += [Check(name, False, "; ".join(details)) for name, details in by_name.items()]

        # (B) What loaded is in the database: text, chunks, diff (after the baseline), rates, votes, projections.
        loaded = [o for o in outcomes if o.status == "loaded"]
        wrong = []
        for o in loaded:
            row = tx.execute(
                """select e.status, e.is_published, d.is_published, d.char_count,
                          (select count(*) from core.chunk c where c.document_id = d.id),
                          (select count(*) from core.document_diff x where x.document_id = d.id),
                          (select count(*) from policy.rate_setting r where r.meeting_event_id = e.id),
                          (select count(*) from policy.vote v where v.meeting_event_id = e.id and v.document_id = d.id),
                          (select count(*) from core.forecast fc where fc.document_id = d.id and fc.method = 'parsed')
                     from core.event e
                     join core.document d on d.event_id = e.id and d.doc_type = %s and d.external_key = %s
                    where e.slug = %s order by d.version_no desc limit 1""",
                (DOC_TYPE, o.resolution.external_key, o.slug)).fetchone()
            baseline = next(m for m in f.meetings if m.slug == o.slug).baseline
            if row is None:
                wrong.append(f"{o.slug}: no document")
                continue
            ev_status, ev_pub, doc_pub, chars, chunks, diffs, rates, votes, projections = row
            bad = [what for what, ok in (
                ("not held", ev_status == "held"), ("text", chars == o.resolution.text.char_count),
                ("chunks", chunks > 0), ("diff", diffs == (0 if baseline else 1)), ("rates", rates == o.rates),
                ("published", ev_pub and doc_pub), ("votes", votes <= o.votes),
                ("projections", projections == o.projections)) if not ok]
            if bad:
                wrong.append(f"{o.slug}: {', '.join(bad)}")
        out.append(Check("resolutions_stored", not wrong, "; ".join(wrong) if wrong
                         else json.dumps(self.counts(f), sort_keys=True)))

        # (B) Minutes that loaded: text, chunks (each member's statement its own speaker), published, six votes.
        wrong = []
        for d in (x for x in docs if x.status == "loaded"):
            row = tx.execute(
                """select d.is_published, d.char_count, (select count(*) from core.chunk c where c.document_id = d.id),
                          (select count(distinct c.speaker) from core.chunk c where c.document_id = d.id),
                          (select count(*) from policy.vote v where v.meeting_event_id = e.id)
                     from core.document d join core.event e on e.id = d.event_id
                    where e.slug = %s and d.doc_type = %s and d.external_key = %s and d.text_sha256 is not null
                    order by d.version_no desc limit 1""", (d.slug, MINUTES_TYPE, d.external_key)).fetchone()
            if row is None:
                wrong.append(f"{d.slug} minutes: no document")
                continue
            pub, chars, chunks, speakers, votes = row
            bad = [what for what, ok in (("published", pub), ("text", chars == d.char_count), ("chunks", chunks > 0),
                                         ("speakers", speakers == rbi_minutes.MEMBERS),
                                         ("votes", d.votes is None or votes == d.votes == rbi_minutes.MEMBERS))
                   if not ok]
            if bad:
                wrong.append(f"{d.slug} minutes: {', '.join(bad)}")
        out.append(Check("minutes_stored", not wrong, "; ".join(wrong) if wrong else
                         f"{sum(d.status == 'loaded' for d in docs)} minutes, "
                         f"{sum(d.from_minutes for d in docs if d.status == 'loaded')} votes written from them"))

        # Warn checks from the documents that loaded, one per name.
        warn: dict[str, list[str]] = {}
        for o in [*loaded, *(d for d in docs if d.status == "loaded")]:
            for c in o.checks:
                if not c.blocking and not c.passed:
                    warn.setdefault(c.name, []).append(c.detail)
        for name in ("stance_label", "between_meetings", "votes_wait", "voter_terms", "roster_six", "cpi_projection",
                     "projection_unparsed", "meeting_no", "minutes_roster", "minutes_changed_vote",
                     "minutes_resolution_copy"):
            out.append(Check(name, name not in warn, "; ".join(warn.get(name, [])), blocking=False))
        if self.result.get("gate") is not None:
            out.append(self.result["gate"])

        # (W) The Annual Policy page links the resolution each meeting stored (the RSS can list it first).
        odd = []
        for o in loaded:
            m = next(x for x in f.meetings if x.slug == o.slug)
            link = f.links.get(m.decision_date)
            if o.how == "rss" and link is None:
                odd.append(f"{o.slug}: taken from the RSS item; the Annual Policy page doesn't link it yet")
            elif link is not None and link != o.resolution.url:
                odd.append(f"{o.slug}: the Annual Policy page links {link}, not {o.resolution.url}")
        for m in f.meetings:
            link = f.links.get(m.decision_date)
            if m.resolution_url and link and link != m.resolution_url and m.slug not in f.targets:
                odd.append(f"{m.slug}: stored {m.resolution_url}, the Annual Policy page links {link}")
            mlink = f.minutes_links.get(m.decision_date)
            if m.minutes_url and mlink and mlink != m.minutes_url:
                odd.append(f"{m.slug}: stored minutes {m.minutes_url}, the Annual Policy page links {mlink}")
        out.append(Check("annual_policy_link", not odd, "; ".join(odd), blocking=False))

        if f.workflow not in ("watch", "backfill"):
            out.append(Check("resolution_missing", not f.missing, "no resolution link yet: " + ", ".join(f.missing),
                             blocking=False))
        if f.workflow != "watch":
            out.append(Check("documents_missing", not f.not_found, "; ".join(f.not_found), blocking=False))
        return out

    def counts(self, f: Fetched) -> dict:
        outcomes: list[Outcome] = self.result["outcomes"]
        docs: list[MinutesOutcome] = self.result["docs"]
        c = Counter(o.status for o in outcomes)
        loaded = [o for o in outcomes if o.status == "loaded"]
        return {
            "targets": len(outcomes), "loaded": c["loaded"], "waiting": c["waiting"], "failed": c["failed"],
            "rates": sum(o.rates for o in loaded), "votes": sum(o.votes for o in loaded),
            "rate_votes": sum(o.rate_votes for o in loaded), "dissents": sum(o.dissents for o in loaded),
            "projections": sum(o.projections for o in loaded), "from_rss": sum(o.how == "rss" for o in loaded),
            "roster": self.result["roster"],
            "minutes": sum(d.status == "loaded" for d in docs), "minutes_failed": sum(d.status == "failed" for d in docs),
            "minutes_votes": sum(d.from_minutes for d in docs if d.status == "loaded"),
            "minutes_differ": sum(len(d.differ) for d in docs if d.status == "loaded"),
            "d4": self.result["d4"],
        }

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """A backfill's failed resolutions and minutes, one alert each, resolved once published; minutes still missing
        15 days after the meeting, resolved once stored; the votes the minutes changed, open for a week; and the site
        reachable again (resolves `rbi_site_unreachable`)."""
        out: list[Alert] = []
        docs: list[MinutesOutcome] = self.result.get("docs", [])
        failures = {
            ("rbi_resolution_failed", DOC_TYPE): {o.slug: o for o in self.result.get("outcomes", [])
                                                  if o.status == "failed"},
            ("rbi_minutes_failed", MINUTES_TYPE): {d.slug: d for d in docs if d.status == "failed"},
        }
        for (prefix, doc_type), failed in failures.items():
            out += [Alert(f"{prefix}:{s}", True, "warn", f"{s}: " + "; ".join(
                c.detail for c in o.checks if c.blocking and not c.passed)) for s, o in sorted(failed.items())]
            open_ = {r[0].split(":", 1)[1] for r in db.execute(
                "select dedupe_key from ops.alert where dedupe_key like %s and resolved_at is null", (prefix + ":%",))}
            done = {r[0] for r in db.execute(
                """select e.slug from core.event e join core.document d on d.event_id = e.id and d.doc_type = %s
                    where e.slug = any(%s) and d.is_published""", (doc_type, sorted(open_ - set(failed)))).fetchall()}
            out += [Alert(f"{prefix}:{s}", False) for s in sorted(done)]

        # Minutes still missing on the fifteenth day after the meeting (RBI Act s.45ZL; Arch 02 §2).
        today = self.result.get("today") or _today(datetime.now(INDIA))
        scope = sorted(self.scope) if self.scope is not None else None
        late = {r[0] for r in db.execute(
            """select e.slug from core.event e
                 join core.entity b on b.id = e.entity_id and b.slug = 'rbi'
                 join policy.meeting m on m.event_id = e.id
                where e.event_type = 'policy_meeting' and m.is_rate_decision and e.status = 'held'
                  and coalesce(e.ends_on, e.starts_on) >= %s
                  and coalesce(m.minutes_due_on, coalesce(e.ends_on, e.starts_on) + %s) < %s
                  and (%s::text[] is null or e.slug = any(%s::text[]))
                  and not exists (select 1 from core.document d where d.event_id = e.id and d.doc_type = %s
                                     and d.text_sha256 is not null)""",
            (self.first_day, MINUTES_DUE, today, scope, scope, MINUTES_TYPE)).fetchall()}
        open_ = {r[0].split(":", 1)[1] for r in db.execute(
            "select dedupe_key from ops.alert where dedupe_key like 'rbi_minutes_missing:%' and resolved_at is null")}
        out += [Alert(f"rbi_minutes_missing:{s}", True, "warn", f"{s}: no minutes 15 days after the meeting (RBI Act "
                      "s.45ZL: published on the fourteenth day); the Annual Policy page and the feed link none")
                for s in sorted(late)]
        out += [Alert(f"rbi_minutes_missing:{s}", False) for s in sorted(open_ - late) if scope is None or s in scope]

        # Votes the minutes changed: open for a week from the minutes' load.
        changed = {d.slug: d.changed for d in docs if d.status == "loaded" and d.changed}
        out += [Alert(f"votes_changed:{s}", True, "warn", f"{s}: the minutes' vote differs from the resolution's for "
                      f"{', '.join(who)}; the stored votes now follow the minutes") for s, who in sorted(changed.items())]
        open_ = {r[0].split(":", 1)[1] for r in db.execute(
            "select dedupe_key from ops.alert where dedupe_key like 'votes_changed:mpc-%' and resolved_at is null")}
        old = {r[0] for r in db.execute(
            """select e.slug from core.event e join core.document d on d.event_id = e.id and d.doc_type = %s
                where e.slug = any(%s) and d.extracted_at < now() - %s""",
            (MINUTES_TYPE, sorted(open_ - set(changed)), CHANGED_ALERT)).fetchall()}
        out += [Alert(f"votes_changed:{s}", False) for s in sorted(old)]

        out.append(Alert("rbi_site_unreachable", False))          # this run reached the site
        return out

    def failure_alerts(self, db, status: str, error: str | None) -> list[Alert]:
        """A failing run: the old site unreachable for 3 days (Arch 02 §2), when this run couldn't reach it and the
        first run since the last good one that couldn't began 3 or more days ago."""
        if "SiteUnreachable" not in (error or ""):
            return []
        since = db.execute(
            """select min(started_at) from ops.task_run
                where task_key = %s and status in ('failed', 'checks_failed') and error like '%%SiteUnreachable%%'
                  and started_at > coalesce((select max(finished_at) from ops.task_run
                                              where task_key = %s and status = 'success'), '-infinity')""",
            (self.key, self.key)).fetchone()[0]
        now = db.execute("select now()").fetchone()[0]
        if since is None or now - since < UNREACHABLE_ALERT:
            return []
        return [Alert("rbi_site_unreachable", True, "error",
                      f"rbi.org.in (the old site) unreachable since {since:%d %b %Y %H:%M} UTC: no MPC documents are "
                      "being fetched (the new site blocks scripts: Data Audit 01, red flag 1)")]
