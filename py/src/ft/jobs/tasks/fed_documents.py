"""policy.fed_documents (Prompt 01 B5; Architecture 02 §2; Spec 01 §1.1, §2, §3.1): FOMC statements (part 1), minutes and
press-conference transcripts (part 2).

**Which document is a meeting's statement.** The calendar's link decides (Spec 01 §1.1): `policy.calendar` stores the
meetings, and this task reads the calendar pages for their "Statement: HTML" links (the 2018-2020 historical pages
from the copies the calendar backfill stored). The Fed's RSS is a trigger: an item counts only if it's titled
"FOMC statement", links the statement of the meeting being watched (monetaryYYYYMMDDa.htm on its decision date) and the
page passes the statement checks. On a decision day the feed can list the statement before the calendar links it; in
that gap the RSS item's link is used, and a later run's `calendar_link` check (W) confirms the calendar links the same
page. Notation votes aren't meetings, so their pages are never read; the implementation note (`a1`) never is either.
The calendar links the minutes and the press-conference page the same way; the press-conference page links the
transcript PDF once it's posted (ft.policy.fed_transcript).

**When it runs:**
- watch, on an FOMC decision day until the statement is stored: one look at the feed and the calendar page per poll
  (conditional requests, so an unchanged page answers 304), then the statement once it appears; never the minutes or
  the transcript;
- daily (every 20 hours): any rate decision of the last 60 days released without a stored statement, or not yet
  marked held (its statement stored only as the next meeting's diff base: read back, not fetched again); the minutes
  of the last 60 days' meetings once the calendar links them; the transcript while the press-conference page is
  within 30 days of the meeting (Arch 02 §2: after that the meeting is skipped and an alert opens); and every document
  of those meetings already stored, again by a conditional request, so a page whose text changed becomes a new version
  (with an alert);
- backfill (`--task policy.fed_documents [--since] [--until]`): every rate decision in the range, from the 2018
  baseline, with its minutes and transcript (none for the baseline: Spec 01 §2). A document already stored is read
  back from its raw file, not fetched again, and an unchanged one writes nothing (B8); each meeting's statement,
  minutes and transcript load in their own savepoints, so one bad document doesn't block the rest (Arch 02 §1.8).

**What a statement stores:** the raw file (through the fetcher), the document (its canonical text), its chunks, the
diff against the previous meeting's statement, the target range (`fed_target_lower`, `fed_target_upper`, each with
its change against the previous meeting and the decision sentence's span), the votes, and the meeting marked `held`
and published with its document. If the previous meeting's statement isn't stored (the first meeting of a partial
backfill), it's fetched and stored too, unpublished, as the diff's base and for the change. The baseline's change is
measured against FRED's DFEDTARU/L the day before (Spec 01 §2); until policy.market_series has loaded those, its rate
rows wait (`baseline_change`, W) and the baseline stays unpublished.

**Minutes and transcripts** store the raw file, the document and its chunks (a transcript's by speaker), published once
their checks pass. A transcript's text is the PDF's (Spec 01 §1.1).

**Votes:** a named statement (to Apr 2026) gives every member's vote; a tally statement (from Jun 2026) gives none, and
the minutes do: every name in "Voting for this action" gets the decision, each dissenter the change the statement's
"Voting against … who preferred …" clause states, verbatim (decided by Anand, 23 Oct 2026). For a named statement the
minutes are a cross-check (`minutes_votes`, W). The roster is config/members.yaml.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from ft.engines.document.chunk import chunk_text
from ft.engines.document.diff import DIFF_VERSION, diff_texts
from ft.engines.document.store import DocumentRef, save_chunks, save_diff, save_version
from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register
from ft.policy import fed_calendar, fed_minutes, fed_statement, fed_transcript, members, topics
from ft.policy.fed_minutes import Minutes, MinutesError
from ft.policy.fed_statement import Range, Statement, StatementError, bps
from ft.policy.fed_transcript import TranscriptError
from ft.policy.meetings import FIRST_DAY

NY = ZoneInfo("America/New_York")
DOC_TYPE = "fomc_statement"
MINUTES_TYPE = "fomc_minutes"
TRANSCRIPT_TYPE = "fomc_presconf_transcript"
DOC_TYPES = (DOC_TYPE, MINUTES_TYPE, TRANSCRIPT_TYPE)
CATCH_UP = timedelta(days=60)            # daily: meetings this recent (statements, minutes, re-checks)
TRANSCRIPT_WAIT = timedelta(days=30)     # Arch 02 §2: a transcript still missing after 30 days alerts; daily stops looking
CHANGED_ALERT = timedelta(days=7)        # a stored document whose text changed: its alert stays open this long
MINUTES_RELEASE = time(14, 0)            # the minutes come out at 2:00 p.m. New York time on the calendar's "Released"
#                                          day (the press-conference pages: "Released October 07, 2026 at 2:00 p.m.";
#                                          the feed's minutes items at 18:00 GMT in summer)
HISTORICAL_YEARS = (2018, 2019, 2020)    # on fomchistoricalYYYY.htm; the calendar page covers 2021 on
EFFECTIVE_AFTER = timedelta(days=1)      # FRED's DFEDTARU/L show a decision from the day after its release


@dataclass(frozen=True)
class Meeting:
    event_id: int
    slug: str
    decision_date: date
    occurs_at: datetime | None
    status: str
    is_published: bool
    statement_id: int | None             # the latest stored statement version with text
    statement_raw: int | None
    statement_url: str | None
    baseline: bool                       # the last meeting before 2019 (the change comes from FRED)
    starts_on: date | None = None
    minutes_id: int | None = None        # the latest stored minutes version with text
    minutes_raw: int | None = None
    minutes_url: str | None = None
    transcript_id: int | None = None     # the latest stored transcript version with text
    transcript_raw: int | None = None
    transcript_url: str | None = None    # the PDF's
    has_press_conference: bool | None = None

    def released(self, now: datetime) -> bool:
        return self.occurs_at <= now if self.occurs_at else self.decision_date < now.astimezone(NY).date()


@dataclass(frozen=True)
class Page:
    url: str
    how: str                             # 'calendar' | 'rss' | 'stored' | 'recheck' (a stored page fetched again)
    raw_file_id: int | None
    body: bytes


@dataclass
class Fetched:
    now: datetime
    workflow: str
    meetings: list[Meeting]              # every stored Fed rate decision, oldest first
    targets: list[str]                   # slugs whose statement to load, oldest first
    pages: dict[str, Page]               # by slug: targets and the previous statements they need
    calendar: dict[date, str]            # statement links on the calendar pages read, by decision date
    rss: dict[date, str]                 # statement links in the feed, by decision date
    missing: list[str] = field(default_factory=list)   # released targets with no link found
    rows: dict[date, fed_calendar.Row] = field(default_factory=dict)   # calendar entries read, by decision date
    minutes: dict[str, Page] = field(default_factory=dict)        # by slug: minutes to load
    transcripts: dict[str, Page] = field(default_factory=dict)    # by slug: transcript PDFs to load
    statements: dict[str, Page] = field(default_factory=dict)     # by slug: the statement each minutes target goes with
    not_found: list[str] = field(default_factory=list)           # linked pages that answered 404 ("slug: what")
    page_errors: dict[str, str] = field(default_factory=dict)    # by slug: a press-conference page that can't be read
    waiting: list[str] = field(default_factory=list)             # press-conference pages with no transcript link yet


class MeetingFailed(Exception):
    def __init__(self, checks: list[Check]):
        self.checks = checks


@dataclass
class Outcome:
    slug: str
    status: str                          # 'loaded' | 'waiting' (no statement yet) | 'failed'
    checks: list[Check] = field(default_factory=list)
    written: int = 0
    statement: Statement | None = None
    how: str | None = None
    votes: int = 0
    dissents: int = 0
    rates: bool = False


@dataclass
class DocOutcome:
    """A minutes or transcript load."""
    slug: str
    kind: str                            # 'minutes' | 'transcript'
    status: str                          # 'loaded' | 'failed'
    checks: list[Check] = field(default_factory=list)
    written: int = 0
    external_key: str | None = None
    char_count: int = 0
    votes: int | None = None             # minutes: vote rows written from them (tally statements); None: not theirs
    how: str | None = None


@dataclass(frozen=True)
class VoteRow:
    change_bps: int | None
    dissent: bool
    text: str | None
    document_id: int
    span: tuple[int, int]


def _today(now: datetime) -> date:
    return now.astimezone(NY).date()


def _day(d: date) -> str:
    return f"{d.day} {d:%b} {d.year}"


def _title(d: date) -> str:
    return f"FOMC statement, {_day(d)}"


def _minutes_title(m: Meeting) -> str:
    return f"FOMC minutes, {_day(m.decision_date)}"


def _transcript_title(m: Meeting) -> str:
    return f"FOMC press conference transcript, {_day(m.decision_date)}"


def _latest(alias: str, doc_type: str) -> str:
    return f"""left join lateral (select d.id, d.raw_file_id, d.source_url from core.document d
                                     where d.event_id = e.id and d.doc_type = '{doc_type}' and d.text_sha256 is not null
                                     order by d.version_no desc limit 1) {alias} on true"""


@register
class FedDocuments(Task):
    key = "policy.fed_documents"
    members_path: Path = members.CONFIG   # tests point this at their own file
    first_day: date = FIRST_DAY           # the meeting before this is the baseline; tests set their own
    scope: frozenset[str] | None = None   # tests: only these meetings (the test database holds other tests' too)

    def __init__(self):
        self.result: dict = {}

    # ------------------------------------------------------------------ fetch (no transaction)
    def _meetings(self, db) -> list[Meeting]:
        rows = db.execute(
            f"""select e.id, e.slug, coalesce(e.ends_on, e.starts_on), e.occurs_at, e.status, e.is_published,
                       d.id, d.raw_file_id, d.source_url, e.starts_on, mi.id, mi.raw_file_id, mi.source_url,
                       tr.id, tr.raw_file_id, tr.source_url, m.has_press_conference
                  from core.event e
                  join core.entity b on b.id = e.entity_id and b.kind = 'central_bank' and b.slug = 'fed'
                  join policy.meeting m on m.event_id = e.id
                  {_latest("d", DOC_TYPE)}
                  {_latest("mi", MINUTES_TYPE)}
                  {_latest("tr", TRANSCRIPT_TYPE)}
                 where e.event_type = 'policy_meeting' and m.is_rate_decision and e.status <> 'cancelled'
                   and e.slug ~ '^fomc-[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'
                 order by coalesce(e.ends_on, e.starts_on), e.id""").fetchall()
        rows = [r for r in rows if self.scope is None or r[1] in self.scope]
        out = [Meeting(*r[:9], baseline=False, starts_on=r[9], minutes_id=r[10], minutes_raw=r[11], minutes_url=r[12],
                       transcript_id=r[13], transcript_raw=r[14], transcript_url=r[15], has_press_conference=r[16])
               for r in rows]
        if out and out[0].decision_date < self.first_day:
            out[0] = replace(out[0], baseline=True)
        return out

    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        allm = self._meetings(f.db)
        by_slug = {m.slug: m for m in allm}
        now, wf = ctx.now, ctx.workflow
        today = _today(now)
        until = ctx.until or today
        if ctx.meeting is not None:                                   # watch: the meeting being watched
            targets = [by_slug[ctx.meeting.slug]] if ctx.meeting.slug in by_slug else []
            targets = [m for m in targets if m.statement_id is None]
        elif wf == "backfill":
            targets = [m for m in allm if m.released(now) and m.decision_date <= until
                       and (ctx.since is None or m.decision_date >= ctx.since)]
        else:                                                         # daily, refresh: catch up, and retry a meeting
            targets = [m for m in allm if (m.statement_id is None or m.status != "held") and m.released(now)
                       and m.decision_date >= today - CATCH_UP]       # stored only as another's diff base
        # Minutes and transcripts (never in watch): the backfill's range, or the daily run's last 60 days.
        if ctx.meeting is not None or wf == "watch":
            docs: list[Meeting] = []
        elif wf == "backfill":
            docs = [m for m in allm if not m.baseline and m.released(now) and m.decision_date <= until
                    and (ctx.since is None or m.decision_date >= ctx.since)]
        else:
            docs = [m for m in allm if not m.baseline and m.released(now) and m.decision_date >= today - CATCH_UP]

        slugs = [m.slug for m in targets]
        needed = []                                                   # previous statements the targets need
        for m in targets:
            i = allm.index(m)
            if i and allm[i - 1].statement_id is None and allm[i - 1].slug not in slugs:
                needed.append(allm[i - 1])
        to_get = [m for m in needed + targets if m.statement_id is None]
        to_link = to_get + [m for m in docs if m.minutes_id is None or m.transcript_id is None]

        calendar: dict[date, str] = {}
        rows: dict[date, fed_calendar.Row] = {}
        rss: dict[date, str] = {}
        # Daily and refresh runs always read the current calendar page (one conditional request), so a statement first
        # taken from the RSS is checked against the calendar's link once the calendar lists it (`calendar_link`).
        if to_link or wf not in ("backfill", "watch"):
            years = {m.decision_date.year for m in (to_link if wf == "backfill" else to_get)}
            pages = sorted({fed_calendar.historical(y) for y in years if y in HISTORICAL_YEARS}
                           | ({fed_calendar.CALENDAR} if wf not in ("backfill", "watch")
                              or any(y not in HISTORICAL_YEARS for y in years) else set()))
            for url in pages:
                got = f.stored(url, source="fed") if url != fed_calendar.CALENDAR else None
                if got is None:
                    got = f.get(url, source="fed")
                for row in fed_calendar.parse_page(got.body if got.body is not None else f.read(got.raw_file_id), url):
                    if row.label == "notation vote":
                        continue
                    if row.statement_url and row.statement_date:
                        calendar[row.statement_date] = row.statement_url
                    rows[row.statement_date or row.last] = row
            if to_get and wf != "backfill":                          # the trigger: only while a statement is awaited
                got = f.get(fed_statement.RSS, source="fed")
                for item in fed_statement.parse_rss(got.body if got.body is not None else f.read(got.raw_file_id)):
                    if item.is_statement:
                        rss.setdefault(fed_statement.statement_date(item.link), item.link)

        out: dict[str, Page] = {}
        missing = []
        for m in needed + targets:
            if m.statement_id is not None:
                out[m.slug] = Page(m.statement_url, "stored", m.statement_raw, f.read(m.statement_raw))
                continue
            url, how = calendar.get(m.decision_date), "calendar"
            if url is None and m.decision_date in rss:
                url, how = rss[m.decision_date], "rss"
            if url is None:
                missing.append(m.slug)
                continue
            got = f.get(url, source="fed", allow=(404,))
            if got.status == 404:
                missing.append(m.slug)
                continue
            out[m.slug] = Page(url, how, got.raw_file_id, got.body if got.body is not None else f.read(got.raw_file_id))
        fetched = Fetched(now, wf, allm, slugs, out, calendar, rss, missing, rows)

        if wf not in ("backfill", "watch") and ctx.meeting is None:   # daily: stored statements fetched again
            for m in allm:
                if (m.statement_id is None or m.status != "held" or m.slug in slugs
                        or m.decision_date < today - CATCH_UP):
                    continue
                if page := self._again(f, m.statement_url, fetched):
                    out[m.slug] = page
                    fetched.targets.append(m.slug)
            fetched.targets.sort(key=lambda s: by_slug[s].decision_date)
        for m in docs:
            self._docs(f, m, fetched, wf, today)
        return fetched

    def _again(self, f, url: str, fetched: Fetched) -> Page | None:
        """A stored document's page fetched again (conditional): the page if its bytes changed, else None."""
        got = f.get(url, source="fed", allow=(404,))
        if got.status == 404:
            fetched.not_found.append(f"{url} (stored, now 404)")
            return None
        if got.status == 200 and got.changed:
            return Page(url, "recheck", got.raw_file_id, got.body)
        return None

    def _docs(self, f, m: Meeting, fetched: Fetched, wf: str, today: date) -> None:
        """The minutes and the transcript of one meeting: stored ones read back (backfill) or fetched again (daily);
        missing ones from the calendar's links."""
        row = fetched.rows.get(m.decision_date)
        backfill = wf == "backfill"
        if m.minutes_id is not None:
            page = Page(m.minutes_url, "stored", m.minutes_raw, f.read(m.minutes_raw)) if backfill \
                else self._again(f, m.minutes_url, fetched)
            if page:
                fetched.minutes[m.slug] = page
        elif row is not None and row.minutes_url:
            got = f.get(row.minutes_url, source="fed", allow=(404,))
            if got.status == 404:
                fetched.not_found.append(f"{m.slug}: minutes {row.minutes_url}")
            else:
                fetched.minutes[m.slug] = Page(row.minutes_url, "calendar", got.raw_file_id,
                                               got.body if got.body is not None else f.read(got.raw_file_id))
        if m.slug in fetched.minutes:                                 # the statement it goes with (tally votes)
            if m.slug in fetched.pages:
                fetched.statements[m.slug] = fetched.pages[m.slug]
            elif m.statement_raw is not None:
                fetched.statements[m.slug] = Page(m.statement_url, "stored", m.statement_raw, f.read(m.statement_raw))

        if m.transcript_id is not None:
            page = Page(m.transcript_url, "stored", m.transcript_raw, f.read(m.transcript_raw)) if backfill \
                else self._again(f, m.transcript_url, fetched)
            if page:
                fetched.transcripts[m.slug] = page
        elif row is not None and row.presconf_url and (backfill or m.decision_date >= today - TRANSCRIPT_WAIT):
            got = f.get(row.presconf_url, source="fed", allow=(404,))
            if got.status == 404:
                fetched.not_found.append(f"{m.slug}: press-conference page {row.presconf_url}")
                return
            try:
                link = fed_transcript.pdf_link(got.body if got.body is not None else f.read(got.raw_file_id),
                                               row.presconf_url)
            except TranscriptError as e:
                fetched.page_errors[m.slug] = f"{row.presconf_url}: {e}"
                return
            if link is None:
                fetched.waiting.append(m.slug)
                return
            got = f.get(link, source="fed", allow=(404,))
            if got.status == 404:
                fetched.not_found.append(f"{m.slug}: transcript {link}")
                return
            fetched.transcripts[m.slug] = Page(link, "calendar", got.raw_file_id,
                                               got.body if got.body is not None else f.read(got.raw_file_id))

    # ------------------------------------------------------------------ load (one transaction; a savepoint per document)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        roster = members.load(self.members_path)
        ids, written = members.sync(tx, roster)
        roster_written = written
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
        docs: list[DocOutcome] = []
        for kind, pages, fn in (("minutes", f.minutes, self._minutes), ("transcript", f.transcripts, self._transcript)):
            for slug in sorted(pages, key=order.__getitem__):
                d = DocOutcome(slug, kind, "failed")
                try:
                    with tx.transaction():
                        d = fn(tx, f, slug, roster, ids)
                except MeetingFailed as e:
                    d = DocOutcome(slug, kind, "failed", e.checks)
                except Exception as e:
                    if f.workflow != "backfill":
                        raise
                    d = DocOutcome(slug, kind, "failed", [Check("load_error", False,
                                                                f"{slug} {kind}: {type(e).__name__}: {e}")])
                docs.append(d)
                written += d.written
        self.result = {"outcomes": outcomes, "docs": docs, "roster": len(roster.members),
                       "roster_written": roster_written, "today": _today(f.now)}
        return written

    def _fail(self, name: str, detail: str, earlier: list[Check] = ()):
        raise MeetingFailed([*earlier, Check(name, False, detail)])

    def _meeting(self, tx, f: Fetched, slug: str, roster: members.Roster, ids: dict[str, int], tr: int) -> Outcome:
        m = next(x for x in f.meetings if x.slug == slug)
        page = f.pages.get(slug)
        if page is None:
            if f.workflow == "backfill":
                self._fail("statement_link", f"{slug}: no statement link on the calendar")
            return Outcome(slug, "waiting")
        try:
            st = fed_statement.parse(page.body, page.url)
        except StatementError as e:
            self._fail("statement_page", f"{slug}: {e}")
        checks = [Check(r.name, r.passed, f"{slug}: {r.detail}") for r in fed_statement.checks(st, m.decision_date)]
        if not all(c.passed for c in checks):
            raise MeetingFailed(checks)
        o = Outcome(slug, "loaded", checks, statement=st, how=page.how)
        d = st.decision
        n = 0

        # The previous meeting's range: the change and the diff's base (Spec 01 §2).
        previous, base_id, n_base = self._previous(tx, f, m, checks)
        n += n_base
        change: tuple[int, int] | None = None
        if previous is not None:
            change = (bps(d.range.lower - previous.lower), bps(d.range.upper - previous.upper))
            lo, up = change
            ok = lo == up and up % 25 == 0 and (up > 0) - (up < 0) == d.direction \
                and (d.stated_bps is None or d.stated_bps == up)
            checks.append(Check("rate_change", ok, f"{slug}: {d.verb} to {d.range} from {previous}: {up:+d} bps"
                                + (f" (the statement says {d.stated_bps:+d})" if d.stated_bps is not None else "")))
            if not ok:
                raise MeetingFailed(checks)
        elif m.baseline:
            checks.append(Check("baseline_change", False, f"{slug}: the baseline's change is measured against "
                                f"fred.DFEDTARU/L on {m.decision_date - timedelta(days=1)}, not stored yet "
                                "(policy.market_series); its rate rows wait", blocking=False))
        else:
            self._fail("previous_range", f"{slug}: the previous meeting's statement is neither stored nor fetched",
                       checks)

        # The document, its chunks and its diff.
        ref = DocumentRef(DOC_TYPE, self._fed_id(tx), self._source_id(tx), st.external_key, page.url,
                          _title(m.decision_date), m.event_id, page.raw_file_id, st.released_at or m.occurs_at)
        saved = save_version(tx, ref, st.text)
        doc_id = saved.document_id
        n += 1 if saved.new_text else 0
        if saved.new_text or not tx.execute("select 1 from core.chunk where document_id = %s limit 1",
                                            (doc_id,)).fetchone():
            n += save_chunks(tx, doc_id, st.text.text, chunk_text(st.text))
        if base_id is not None:
            have = tx.execute("select base_document_id, diff_version, segments->'params'->>'topics' "
                              "from core.document_diff where document_id = %s", (doc_id,)).fetchone()
            if saved.new_text or have != (base_id, DIFF_VERSION, str(topics.version())):
                old_text = self._text(tx, base_id)
                new_diff = diff_texts(old_text, st.text.text)
                topics.assign_topics("Fed", new_diff.segments, old_text, st.text.text)
                save_diff(tx, doc_id, base_id, new_diff)
                n += 1

        # The target range: one row per bound, the decision sentence as its span.
        if change is not None:
            s, e = d.sentence
            for instrument, value, chg in (("fed_target_lower", d.range.lower, change[0]),
                                           ("fed_target_upper", d.range.upper, change[1])):
                n += tx.execute(
                    """insert into policy.rate_setting (meeting_event_id, instrument, value_pct, change_bps, document_id,
                                                        char_start, char_end)
                       values (%s, %s, %s, %s, %s, %s, %s)
                       on conflict (meeting_event_id, instrument) do update set value_pct = excluded.value_pct,
                         change_bps = excluded.change_bps, document_id = excluded.document_id,
                         char_start = excluded.char_start, char_end = excluded.char_end,
                         crosscheck = case when policy.rate_setting.value_pct = excluded.value_pct
                                           then policy.rate_setting.crosscheck end
                       where (policy.rate_setting.value_pct, policy.rate_setting.change_bps,
                              policy.rate_setting.document_id, policy.rate_setting.char_start,
                              policy.rate_setting.char_end)
                             is distinct from (excluded.value_pct, excluded.change_bps, excluded.document_id,
                                               excluded.char_start, excluded.char_end)""",
                    (m.event_id, instrument, value, chg, doc_id, s, e)).rowcount
            o.rates = True

        # Votes: every member a named statement lists (Spec 01 §2, "Fed votes"); a tally statement's come from the
        # minutes (_minutes).
        v = st.votes
        if v.format == "named" and previous is not None:
            rows, nv = self._votes(tx, m, st, previous, change[1], roster, ids, doc_id, checks)
            n += nv
            o.votes, o.dissents = rows, len(v.dissents)

        # The meeting is held; published with its document once its rates are stored.
        publish = change is not None
        n += tx.execute("""update core.event set status = 'held', is_published = is_published or %s, updated_at = now()
                            where id = %s and (status, is_published) is distinct from ('held', is_published or %s)""",
                        (publish, m.event_id, publish)).rowcount
        if publish:
            n += tx.execute("update core.document set is_published = true where id = %s and not is_published",
                            (doc_id,)).rowcount
        o.checks, o.written = checks, n
        return o

    def _previous(self, tx, f: Fetched, m: Meeting, checks: list[Check]) -> tuple[Range | None, int | None, int]:
        """(the previous meeting's range, its statement's document id, rows written storing it)."""
        if m.baseline:
            rows = dict(tx.execute(
                """select s.key, o.value from core.observation o join core.series s on s.id = o.series_id
                    where s.key in ('fred.DFEDTARL', 'fred.DFEDTARU') and o.obs_date = %s""",
                (m.decision_date - timedelta(days=1),)).fetchall())
            if len(rows) == 2:
                return Range(Decimal(rows["fred.DFEDTARL"]), Decimal(rows["fred.DFEDTARU"])), None, 0
            return None, None, 0
        i = next(k for k, x in enumerate(f.meetings) if x.slug == m.slug)
        if i == 0:
            return None, None, 0
        p = f.meetings[i - 1]
        row = tx.execute("""select id from core.document where event_id = %s and doc_type = %s and text_sha256 is not null
                             order by version_no desc limit 1""", (p.event_id, DOC_TYPE)).fetchone()
        if row:
            ranges = fed_statement.ranges_in(self._text(tx, row[0]))
            if len(ranges) != 1:
                self._fail("previous_range", f"{m.slug}: {len(ranges)} decision sentences in {p.slug}'s statement",
                           checks)
            return ranges[0], row[0], 0
        page = f.pages.get(p.slug)
        if page is None:
            return None, None, 0
        try:
            prev = fed_statement.parse(page.body, page.url)
        except StatementError as e:
            self._fail("previous_range", f"{m.slug}: {p.slug}'s statement: {e}", checks)
        bad = [r for r in fed_statement.checks(prev, p.decision_date) if not r.passed]
        if bad or prev.decision is None:
            self._fail("previous_range", f"{m.slug}: {p.slug}'s statement fails "
                       + ", ".join(f"{r.name} ({r.detail})" for r in bad), checks)
        ref = DocumentRef(DOC_TYPE, self._fed_id(tx), self._source_id(tx), prev.external_key, page.url,
                          _title(p.decision_date), p.event_id, page.raw_file_id, prev.released_at or p.occurs_at)
        saved = save_version(tx, ref, prev.text)
        n = 1 if saved.new_text else 0
        if saved.new_text:
            n += save_chunks(tx, saved.document_id, prev.text.text, chunk_text(prev.text))
        return prev.decision.range, saved.document_id, n

    def _votes(self, tx, m: Meeting, st: Statement, previous: Range, decided_bps: int, roster: members.Roster,
               ids: dict[str, int], doc_id: int, checks: list[Check]) -> tuple[int, int]:
        v = st.votes
        want: dict[int, VoteRow] = {}
        who: dict[int, tuple[members.Member, str]] = {}
        unknown, problems = [], []
        for name in v.voters_for:
            mem = roster.match("FED", name)
            if mem is None:
                unknown.append(name)
                continue
            want[ids[mem.slug]] = VoteRow(decided_bps, False, None, doc_id, v.sentence)
            who[ids[mem.slug]] = (mem, name)
        for ds in v.dissents:
            mem = roster.match("FED", ds.name)
            if mem is None:
                unknown.append(ds.name)
                continue
            pref = self._preference(ds, previous, decided_bps, problems)
            want[ids[mem.slug]] = VoteRow(pref, True, ds.text, doc_id, v.against_sentence)
            who[ids[mem.slug]] = (mem, ds.name)
        checks.append(Check("voters_on_roster", not unknown, f"{m.slug}: not in config/members.yaml: "
                            + ", ".join(unknown) if unknown else f"{m.slug}: {len(want)} voters"))
        if unknown:
            raise MeetingFailed(checks)
        dissenters = sorted(name for mid, (_, name) in who.items() if want[mid].dissent)
        ok = not problems and dissenters == sorted(x.name for x in v.dissents) and len(want) == v.voters
        checks.append(Check("dissents_match", ok, f"{m.slug}: " + ("; ".join(problems) if problems
                                                                  else f"{len(dissenters)} dissents")))
        if not ok:
            raise MeetingFailed(checks)

        # (W) Each voter's term covers the meeting: a member, or an alternate the statement names.
        odd = self._terms(m.decision_date, [(mem, name, any(fed_statement._same(a, name) for a in v.alternates))
                                            for mem, name in who.values()])
        checks.append(Check("voter_terms", not odd, f"{m.slug}: " + "; ".join(odd), blocking=False))
        return len(want), self._write_votes(tx, m.event_id, want)

    @staticmethod
    def _preference(ds: fed_statement.Dissent, previous: Range, decided_bps: int, problems: list[str]) -> int | None:
        try:
            pref = ds.preference.change_bps(previous)
        except StatementError as e:
            problems.append(f"{ds.name}: {e}")
            return None
        if pref is not None and pref % 25:
            problems.append(f"{ds.name}: {pref:+d} bps isn't a multiple of 25")
        if pref == decided_bps:
            problems.append(f"{ds.name} dissented but preferred the decision ({pref:+d} bps)")
        return pref

    @staticmethod
    def _terms(day: date, voters: list[tuple[members.Member, str, bool]]) -> list[str]:
        """Voters whose roster term doesn't fit the vote: no term that day, or a member's status that disagrees with
        voting as a member (an alternate who votes keeps voter: false)."""
        odd = []
        for mem, name, alternate in voters:
            t = mem.term_on(day)
            if t is None:
                odd.append(f"{name}: no term on {day}")
            elif t.is_voter == alternate:
                odd.append(f"{name}: {'a voting member' if t.is_voter else 'not a voting member'} on the roster"
                           + (", but voted as an alternate" if alternate else ""))
        return odd

    def _write_votes(self, tx, event_id: int, want: dict[int, VoteRow]) -> int:
        n = 0
        stored = {r[0] for r in tx.execute("select member_id from policy.vote where meeting_event_id = %s",
                                           (event_id,)).fetchall()}
        for gone in stored - set(want):
            n += tx.execute("delete from policy.vote where meeting_event_id = %s and member_id = %s",
                            (event_id, gone)).rowcount
        for mid, w in want.items():
            n += tx.execute(
                """insert into policy.vote (meeting_event_id, member_id, voted_change_bps, is_dissent, dissent_text,
                                            document_id, char_start, char_end)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (meeting_event_id, member_id) do update set voted_change_bps = excluded.voted_change_bps,
                     is_dissent = excluded.is_dissent, dissent_text = excluded.dissent_text,
                     document_id = excluded.document_id, char_start = excluded.char_start, char_end = excluded.char_end
                   where (policy.vote.voted_change_bps, policy.vote.is_dissent, policy.vote.dissent_text,
                          policy.vote.document_id, policy.vote.char_start, policy.vote.char_end)
                         is distinct from (excluded.voted_change_bps, excluded.is_dissent, excluded.dissent_text,
                                           excluded.document_id, excluded.char_start, excluded.char_end)""",
                (event_id, mid, w.change_bps, w.dissent, w.text, w.document_id, *w.span)).rowcount
        return n

    # ------------------------------------------------------------------ minutes
    def _minutes(self, tx, f: Fetched, slug: str, roster: members.Roster, ids: dict[str, int]) -> DocOutcome:
        m = next(x for x in f.meetings if x.slug == slug)
        page = f.minutes[slug]
        try:
            mn = fed_minutes.parse(page.body, page.url)
        except MinutesError as e:
            self._fail("minutes_page", f"{slug}: {e}")
        checks = [Check(r.name, r.passed, f"{slug}: {r.detail}") for r in fed_minutes.checks(mn, m.decision_date)]
        if not all(c.passed for c in checks):
            raise MeetingFailed(checks)
        row = f.rows.get(m.decision_date)
        released = datetime.combine(row.minutes_released, MINUTES_RELEASE, NY) \
            if row is not None and row.minutes_released else None
        ref = DocumentRef(MINUTES_TYPE, self._fed_id(tx), self._source_id(tx), mn.external_key, page.url,
                          _minutes_title(m), m.event_id, page.raw_file_id, released)
        doc_id, n = self._store(tx, ref, mn.text, None)
        o = DocOutcome(slug, "minutes", "loaded", checks, external_key=mn.external_key,
                       char_count=mn.text.char_count, how=page.how)

        # The decision's votes: written from the minutes for a tally statement; a cross-check for a named one.
        st_page = f.statements.get(slug)
        st = None
        if st_page is not None:
            try:
                st = fed_statement.parse(st_page.body, st_page.url)
            except StatementError:
                st = None
        if st is not None and st.votes is not None and st.votes.format == "tally":
            n += self._tally_votes(tx, m, mn, st, doc_id, roster, ids, o)
        else:
            o.checks.append(self._compare(tx, m, mn.vote, roster))
        # A closing section's vote (the 2 Mar 2020 call, in the 15 Mar 2020 minutes) checks that meeting's votes.
        for v in mn.votes:
            if v.held_on is None:
                continue
            other = next((x for x in f.meetings if v.held_on in (x.starts_on, x.decision_date)), None)
            if other is None:
                o.checks.append(Check("minutes_votes", False, f"{slug}: '{v.heading}' names no stored rate decision",
                                      blocking=False))
            else:
                o.checks.append(self._compare(tx, other, v, roster, where=f" ({slug}'s minutes, '{v.heading}')"))
        for v in mn.votes:
            day = v.held_on or m.decision_date
            if gaps := self._roster_gaps(roster, day, v):
                o.checks.append(Check("minutes_roster", False, f"{slug}{'' if v.held_on is None else ' ' + str(day)}: "
                                      + "; ".join(gaps), blocking=False))
        o.written = n
        return o

    def _tally_votes(self, tx, m: Meeting, mn: Minutes, st: Statement, doc_id: int, roster: members.Roster,
                     ids: dict[str, int], o: DocOutcome) -> int:
        """Votes for a statement that names only its dissenters (from Jun 2026): the minutes name everyone who voted
        for the decision; each dissenter's preference and clause come from the statement (Anand, 23 Oct 2026)."""
        v, sv = mn.vote, st.votes
        rates = {r[0]: (r[1], r[2]) for r in tx.execute(
            """select instrument, value_pct, change_bps from policy.rate_setting
                where meeting_event_id = %s and instrument in ('fed_target_lower', 'fed_target_upper')""",
            (m.event_id,)).fetchall()}
        st_doc = tx.execute("""select id from core.document where event_id = %s and doc_type = %s
                                 and text_sha256 is not null order by version_no desc limit 1""",
                            (m.event_id, DOC_TYPE)).fetchone()
        if len(rates) != 2 or st_doc is None:
            o.checks.append(Check("votes_wait", False, f"{m.slug}: the statement's rates aren't stored, so the minutes' "
                                  "votes wait", blocking=False))
            return 0
        (lo, lo_chg), (up, up_chg) = rates["fed_target_lower"], rates["fed_target_upper"]
        previous = Range(Decimal(lo) - Decimal(lo_chg) / 100, Decimal(up) - Decimal(up_chg) / 100)
        want: dict[int, VoteRow] = {}
        who: list[tuple[members.Member, str, bool]] = []
        unknown, problems = [], []
        for name in v.voters_for:
            mem = roster.match("FED", name)
            if mem is None:
                unknown.append(name)
                continue
            want[ids[mem.slug]] = VoteRow(up_chg, False, None, doc_id, v.for_sentence)
            who.append((mem, name, any(fed_minutes.same(a, name) for a in v.alternates)))
        for ds in sv.dissents:
            mem = roster.match("FED", ds.name)
            if mem is None:
                unknown.append(ds.name)
                continue
            want[ids[mem.slug]] = VoteRow(self._preference(ds, previous, up_chg, problems), True, ds.text, st_doc[0],
                                          sv.against_sentence)
            who.append((mem, ds.name, any(fed_minutes.same(a, ds.name) for a in v.alternates)))
        o.checks.append(Check("voters_on_roster", not unknown, f"{m.slug} minutes: not in config/members.yaml: "
                              + ", ".join(unknown) if unknown else f"{m.slug} minutes: {len(want)} voters"))
        if unknown:
            raise MeetingFailed(o.checks)
        minutes_against = sorted(roster.match("FED", n).slug if roster.match("FED", n) else n for n in v.against)
        statement_against = sorted(roster.match("FED", d.name).slug for d in sv.dissents)
        total = sum(sv.tally)
        ok = not problems and minutes_against == statement_against and v.voters == total == len(want)
        o.checks.append(Check("dissents_match", ok, f"{m.slug} minutes: " + (
            "; ".join(problems) if problems else f"against {minutes_against} (the statement: {statement_against}); "
            f"{v.voters} voters in the minutes, {total} in the statement's tally")))
        if not ok:
            raise MeetingFailed(o.checks)
        odd = self._terms(m.decision_date, who)
        o.checks.append(Check("voter_terms", not odd, f"{m.slug} minutes: " + "; ".join(odd), blocking=False))
        o.votes = len(want)
        return self._write_votes(tx, m.event_id, want)

    def _compare(self, tx, m: Meeting, v: fed_minutes.Vote | None, roster: members.Roster, where: str = "") -> Check:
        """(W) The minutes' vote against the votes stored from the statement: the same members, the same dissenters."""
        stored = {r[0]: r[1] for r in tx.execute(
            """select p.slug, v.is_dissent from policy.vote v join policy.member p on p.id = v.member_id
                where v.meeting_event_id = %s""", (m.event_id,)).fetchall()}
        if v is None:
            return Check("minutes_votes", False, f"{m.slug}{where}: no decision vote", blocking=False)
        if not stored:
            return Check("minutes_votes", False, f"{m.slug}{where}: no stored votes to compare with the minutes'",
                         blocking=False)
        said = {}
        for name, dissent in [(n, False) for n in v.voters_for] + [(n, True) for n in v.against]:
            mem = roster.match("FED", name)
            said[mem.slug if mem else f"?{name}"] = dissent
        if said == stored:
            return Check("minutes_votes", True, f"{m.slug}{where}: {len(said)} votes agree", blocking=False)
        diff = sorted(set(said.items()) ^ set(stored.items()))
        return Check("minutes_votes", False, f"{m.slug}{where}: the minutes and the stored votes differ: "
                     + ", ".join(f"{s} ({'dissent' if d else 'for'})" for s, d in diff), blocking=False)

    @staticmethod
    def _roster_gaps(roster: members.Roster, day: date, v: fed_minutes.Vote) -> list[str]:
        """The roster's voting members on the day against the minutes' vote (Spec 01 §5.1: the roster matches every
        minutes). Everyone who voted is a voting member, or an alternate the minutes name; every voting member voted,
        is named absent, or (a Reserve Bank president) had an alternate vote in their place; an alternate votes only
        for such a president or for a vacant Reserve Bank seat (the Committee has five: New York's and four others)."""
        voted = {}
        for name in v.voters_for + v.against:
            mem = roster.match("FED", name)
            voted[mem.slug if mem else f"?{name}"] = any(fed_minutes.same(a, name) for a in v.alternates)
        on = {mm.slug: mm.term_on(day).role for mm in roster.voters_on("FED", day)}
        absent = {mm.slug for n in v.absent if (mm := roster.match("FED", n))}
        alternates = {s for s, alt in voted.items() if alt}
        out = []
        if extra := sorted(set(voted) - set(on) - alternates):
            out.append("voted, not a voting member on the roster: " + ", ".join(extra))
        unaccounted = sorted(set(on) - set(voted) - absent)
        board = [s for s in unaccounted if on[s] in ("fed_chair", "fed_governor")]
        presidents = [s for s in unaccounted if s not in board]
        seats = sum(r in ("fed_vice_chair", "fed_bank_president") for r in on.values())
        if board:
            out.append("Board members neither voting nor named absent: " + ", ".join(board))
        if not len(presidents) <= len(alternates) <= len(presidents) + (5 - seats):
            out.append(f"presidents who didn't vote: {', '.join(presidents) or 'none'}; alternates who did: "
                       f"{', '.join(sorted(alternates)) or 'none'}; vacant Reserve Bank seats: {5 - seats}")
        return out

    # ------------------------------------------------------------------ transcripts
    def _transcript(self, tx, f: Fetched, slug: str, roster: members.Roster, ids: dict[str, int]) -> DocOutcome:
        m = next(x for x in f.meetings if x.slug == slug)
        page = f.transcripts[slug]
        try:
            tr = fed_transcript.parse(page.body, page.url)
        except TranscriptError as e:
            self._fail("transcript_pdf", f"{slug}: {e}")
        checks = [Check(r.name, r.passed, f"{slug}: {r.detail}")
                  for r in fed_transcript.checks(tr, m.decision_date)]
        if not all(c.passed for c in checks):
            raise MeetingFailed(checks)
        ref = DocumentRef(TRANSCRIPT_TYPE, self._fed_id(tx), self._source_id(tx), tr.external_key, page.url,
                          _transcript_title(m), m.event_id, page.raw_file_id, None)
        _, n = self._store(tx, ref, tr.text, "transcript")
        return DocOutcome(slug, "transcript", "loaded", checks, n, tr.external_key, tr.text.char_count, how=page.how)

    def _store(self, tx, ref: DocumentRef, ex, speakers: str | None) -> tuple[int, int]:
        """A minutes or transcript document: its version, chunks, published. (document id, rows written)."""
        saved = save_version(tx, ref, ex)
        doc_id, n = saved.document_id, (1 if saved.new_text else 0)
        if saved.new_text or not tx.execute("select 1 from core.chunk where document_id = %s limit 1",
                                            (doc_id,)).fetchone():
            n += save_chunks(tx, doc_id, ex.text, chunk_text(ex, speakers=speakers))
        n += tx.execute("update core.document set is_published = true where id = %s and not is_published",
                        (doc_id,)).rowcount
        return doc_id, n

    def _text(self, tx, document_id: int) -> str:
        return tx.execute("select string_agg(body, '' order by chunk_no) from core.chunk where document_id = %s",
                          (document_id,)).fetchone()[0] or ""

    def _fed_id(self, tx) -> int:
        return tx.execute("select id from core.entity where kind = 'central_bank' and slug = 'fed'").fetchone()[0]

    def _source_id(self, tx) -> int:
        return tx.execute("select id from core.source where key = 'fed'").fetchone()[0]

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        outcomes: list[Outcome] = self.result["outcomes"]
        docs: list[DocOutcome] = self.result["docs"]
        backfill = f.workflow == "backfill"
        out: list[Check] = []

        failed = [o for o in outcomes if o.status == "failed"]
        failed_docs = {k: [d for d in docs if d.kind == k and d.status == "failed"] for k in ("minutes", "transcript")}
        if backfill:
            def listed(items):
                return "; ".join(f"{o.slug}: " + ", ".join(f"{c.name} ({c.detail.split(': ', 1)[-1]})"
                                                          for c in o.checks if c.blocking and not c.passed)
                                 for o in items)
            out.append(Check("meetings_failed", not failed, listed(failed), blocking=False))
            out.append(Check("minutes_failed", not failed_docs["minutes"], listed(failed_docs["minutes"]),
                             blocking=False))
            out.append(Check("transcripts_failed", not failed_docs["transcript"], listed(failed_docs["transcript"]),
                             blocking=False))
        else:
            by_name: dict[str, list[str]] = {}
            for o in [*failed, *failed_docs["minutes"], *failed_docs["transcript"]]:
                for c in o.checks:
                    if c.blocking and not c.passed:
                        by_name.setdefault(c.name, []).append(c.detail)
            out += [Check(name, False, "; ".join(details)) for name, details in by_name.items()]

        # (B) What loaded is in the database: text, chunks, diff (after the baseline), rates, votes (named format).
        loaded = [o for o in outcomes if o.status == "loaded"]
        wrong = []
        for o in loaded:
            row = tx.execute(
                """select e.status, e.is_published, d.is_published, d.char_count,
                          (select count(*) from core.chunk c where c.document_id = d.id),
                          (select count(*) from core.document_diff x where x.document_id = d.id),
                          (select count(*) from policy.rate_setting r where r.meeting_event_id = e.id),
                          (select count(*) from policy.vote v where v.meeting_event_id = e.id),
                          (select count(*) from policy.vote v where v.meeting_event_id = e.id and v.is_dissent)
                     from core.event e
                     join core.document d on d.event_id = e.id and d.doc_type = %s and d.external_key = %s
                    where e.slug = %s order by d.version_no desc limit 1""",
                (DOC_TYPE, o.statement.external_key, o.slug)).fetchone()
            baseline = next(m for m in f.meetings if m.slug == o.slug).baseline
            if row is None:
                wrong.append(f"{o.slug}: no document")
                continue
            ev_status, ev_pub, doc_pub, chars, chunks, diffs, rates, votes, dissents = row
            expect_rates = 2 if o.rates else 0
            named = o.statement.votes.format == "named"
            bad = [what for what, ok in (
                ("not held", ev_status == "held"), ("text", chars == o.statement.text.char_count), ("chunks", chunks > 0),
                ("diff", diffs == (0 if baseline else 1)), ("rates", rates == expect_rates),
                ("published", (ev_pub and doc_pub) if o.rates else not doc_pub),
                # a tally statement's votes come from the minutes (documents_stored checks those)
                ("votes", not named or votes == (o.votes if o.rates else 0)),
                ("dissents", not named or dissents == (o.dissents if o.rates else 0))) if not ok]
            if bad:
                wrong.append(f"{o.slug}: {', '.join(bad)}")
        out.append(Check("statements_stored", not wrong, "; ".join(wrong) if wrong
                         else json.dumps(self.counts(f), sort_keys=True)))

        # (B) Minutes and transcripts that loaded: text, chunks, published; the votes the minutes gave.
        wrong = []
        for d in (x for x in docs if x.status == "loaded"):
            row = tx.execute(
                """select d.is_published, d.char_count, (select count(*) from core.chunk c where c.document_id = d.id),
                          (select count(*) from policy.vote v where v.meeting_event_id = e.id)
                     from core.document d join core.event e on e.id = d.event_id
                    where e.slug = %s and d.doc_type = %s and d.external_key = %s and d.text_sha256 is not null
                    order by d.version_no desc limit 1""",
                (d.slug, MINUTES_TYPE if d.kind == "minutes" else TRANSCRIPT_TYPE, d.external_key)).fetchone()
            if row is None:
                wrong.append(f"{d.slug} {d.kind}: no document")
                continue
            pub, chars, chunks, votes = row
            bad = [what for what, ok in (("published", pub), ("text", chars == d.char_count), ("chunks", chunks > 0),
                                         ("votes", d.votes is None or votes == d.votes)) if not ok]
            if bad:
                wrong.append(f"{d.slug} {d.kind}: {', '.join(bad)}")
        out.append(Check("documents_stored", not wrong, "; ".join(wrong) if wrong else
                         f"{sum(d.kind == 'minutes' and d.status == 'loaded' for d in docs)} minutes, "
                         f"{sum(d.kind == 'transcript' and d.status == 'loaded' for d in docs)} transcripts"))

        # Warn checks from the documents that loaded, one per name.
        warn: dict[str, list[str]] = {}
        for o in [*loaded, *(d for d in docs if d.status == "loaded")]:
            for c in o.checks:
                if not c.blocking and not c.passed:
                    warn.setdefault(c.name, []).append(c.detail)
        for name in ("baseline_change", "voter_terms", "minutes_votes", "minutes_roster", "votes_wait"):
            out.append(Check(name, name not in warn, "; ".join(warn.get(name, [])), blocking=False))

        # (W) The calendar links the statement each meeting stored (the RSS can list it first: Spec 01 §1.1).
        odd = []
        for o in loaded:
            m = next(x for x in f.meetings if x.slug == o.slug)
            cal = f.calendar.get(m.decision_date)
            if o.how == "rss" and cal is None:
                odd.append(f"{o.slug}: taken from the RSS item; the calendar doesn't link it yet")
            elif cal is not None and cal != o.statement.url:
                odd.append(f"{o.slug}: the calendar links {cal}, not {o.statement.url}")
        for m in f.meetings:
            cal = f.calendar.get(m.decision_date)
            if m.statement_url and cal and cal != m.statement_url and m.slug not in f.targets:
                odd.append(f"{m.slug}: stored {m.statement_url}, the calendar links {cal}")
        out.append(Check("calendar_link", not odd, "; ".join(odd), blocking=False))

        # (W) Released decisions in the catch-up window without a statement link (watch: not yet; that's normal), and
        # documents the calendar links that can't be fetched or read.
        if f.workflow not in ("watch", "backfill"):
            out.append(Check("statement_missing", not f.missing, "no statement link yet: " + ", ".join(f.missing),
                             blocking=False))
        if f.workflow != "watch":
            gone = [*f.not_found, *(f"{s}: {e}" for s, e in sorted(f.page_errors.items()))]
            out.append(Check("documents_missing", not gone, "; ".join(gone), blocking=False))

        # (W) The release time on the page against the meeting's (curated where unscheduled: meeting_times.yaml).
        late = []
        for o in loaded:
            m = next(x for x in f.meetings if x.slug == o.slug)
            if o.statement.released_at and m.occurs_at and o.statement.released_at != m.occurs_at:
                late.append(f"{o.slug}: the page says {o.statement.release_line}, the calendar {m.occurs_at:%H:%M %Z}")
        out.append(Check("release_time", not late, "; ".join(late), blocking=False))

        out.append(self._fred_check(tx, [m.event_id for m in f.meetings]))
        return out

    def _fred_check(self, tx, event_ids: list[int]) -> Check:
        """(W) Every stored Fed range equals FRED's DFEDTARU/L from its effective date, the day after the release
        (Spec 01 §5.1). FRED lags, so this is checked whenever FRED has the day; a match is recorded on the rows."""
        rows = tx.execute(
            """select e.slug, r.instrument, r.value_pct, o.value
                 from policy.rate_setting r
                 join core.event e on e.id = r.meeting_event_id
                 join core.entity b on b.id = e.entity_id and b.slug = 'fed'
                 left join core.series s on s.key = case r.instrument when 'fed_target_lower' then 'fred.DFEDTARL'
                                                                      else 'fred.DFEDTARU' end
                 left join core.observation o on o.series_id = s.id
                       and o.obs_date = coalesce(e.ends_on, e.starts_on) + %s
                where r.instrument in ('fed_target_lower', 'fed_target_upper') and r.meeting_event_id = any(%s)
                  and (r.crosscheck is null or o.value is distinct from r.value_pct)""",
            (EFFECTIVE_AFTER.days, event_ids)).fetchall()
        waiting = sorted({r[0] for r in rows if r[3] is None})
        wrong = sorted({f"{r[0]} {r[1]} {r[2]} vs FRED {r[3]}" for r in rows if r[3] is not None and r[3] != r[2]})
        tx.execute(
            """update policy.rate_setting r set crosscheck = 'matches FRED ' || s.source_code
                 from core.event e, core.series s, core.observation o
                where e.id = r.meeting_event_id and r.crosscheck is null
                  and r.instrument in ('fed_target_lower', 'fed_target_upper')
                  and s.key = case r.instrument when 'fed_target_lower' then 'fred.DFEDTARL' else 'fred.DFEDTARU' end
                  and o.series_id = s.id and o.obs_date = coalesce(e.ends_on, e.starts_on) + %s
                  and o.value = r.value_pct and r.meeting_event_id = any(%s)""", (EFFECTIVE_AFTER.days, event_ids))
        detail = "; ".join(filter(None, [wrong and "differs: " + ", ".join(wrong),
                                         waiting and f"{len(waiting)} meetings wait for FRED's effective-date value"]))
        return Check("fred_range", not wrong, detail, blocking=False)

    def counts(self, f: Fetched) -> dict:
        outcomes: list[Outcome] = self.result["outcomes"]
        docs: list[DocOutcome] = self.result["docs"]
        c = Counter(o.status for o in outcomes)
        loaded = [o for o in outcomes if o.status == "loaded"]
        return {
            "targets": len(outcomes), "loaded": c["loaded"], "waiting": c["waiting"], "failed": c["failed"],
            "named_votes": sum(o.votes for o in loaded), "dissents": sum(o.dissents for o in loaded),
            "tally_statements": sum(o.statement.votes.format == "tally" for o in loaded),
            "rates_pending": sum(not o.rates for o in loaded), "from_rss": sum(o.how == "rss" for o in loaded),
            "roster": self.result["roster"],
            "minutes": sum(d.kind == "minutes" and d.status == "loaded" for d in docs),
            "minutes_votes": sum(d.votes or 0 for d in docs if d.status == "loaded"),
            "transcripts": sum(d.kind == "transcript" and d.status == "loaded" for d in docs),
            "transcripts_waiting": len(f.waiting),
            "documents_failed": sum(d.status == "failed" for d in docs),
        }

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """A backfill's failed statements, minutes and transcripts, one alert each, resolved once the document is
        published; a transcript still missing 30 days after its press conference (Arch 02 §2), resolved once stored;
        a stored document whose source text changed (a new version), open for a week."""
        out: list[Alert] = []
        docs: list[DocOutcome] = self.result.get("docs", [])
        failures = {
            ("fed_statement_failed", DOC_TYPE): {o.slug: o for o in self.result.get("outcomes", []) if o.status == "failed"},
            ("fed_minutes_failed", MINUTES_TYPE): {d.slug: d for d in docs if d.kind == "minutes" and d.status == "failed"},
            ("fed_transcript_failed", TRANSCRIPT_TYPE): {d.slug: d for d in docs
                                                         if d.kind == "transcript" and d.status == "failed"},
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

        # A transcript still missing 30 days after the press conference (the daily run stops looking then).
        today = self.result.get("today") or _today(datetime.now(NY))
        scope = sorted(self.scope) if self.scope is not None else None
        late = {r[0] for r in db.execute(
            """select e.slug from core.event e
                 join core.entity b on b.id = e.entity_id and b.slug = 'fed'
                 join policy.meeting m on m.event_id = e.id
                where e.event_type = 'policy_meeting' and m.is_rate_decision and m.has_press_conference
                  and e.status = 'held' and coalesce(e.ends_on, e.starts_on) >= %s
                  and coalesce(e.ends_on, e.starts_on) + %s < %s
                  and (%s::text[] is null or e.slug = any(%s::text[]))
                  and not exists (select 1 from core.document d where d.event_id = e.id and d.doc_type = %s
                                     and d.text_sha256 is not null)""",
            (self.first_day, TRANSCRIPT_WAIT, today, scope, scope, TRANSCRIPT_TYPE)).fetchall()}
        open_ = {r[0].split(":", 1)[1] for r in db.execute(
            "select dedupe_key from ops.alert where dedupe_key like 'fed_transcript_missing:%' and resolved_at is null")}
        out += [Alert(f"fed_transcript_missing:{s}", True, "warn", f"{s}: no press-conference transcript "
                      f"{TRANSCRIPT_WAIT.days} days after the meeting (the page links none, or it can't be fetched)")
                for s in sorted(late)]
        out += [Alert(f"fed_transcript_missing:{s}", False) for s in sorted(open_ - late)
                if scope is None or s in scope]

        # A stored document whose text changed at the source: a new version superseding the old one.
        changed = {r[0]: r[1] for r in db.execute(
            """select d.external_key, e.slug from core.document d
                 join core.source s on s.id = d.source_id and s.key = 'fed'
                 left join core.event e on e.id = d.event_id
                where d.supersedes_id is not null and d.doc_type = any(%s) and d.extracted_at > now() - %s
                  and (%s::text[] is null or e.slug = any(%s::text[]))""",
            (list(DOC_TYPES), CHANGED_ALERT, scope, scope)).fetchall()}
        open_ = {r[0].split(":", 1)[1] for r in db.execute(
            "select dedupe_key from ops.alert where dedupe_key like 'fed_document_changed:%' and resolved_at is null")}
        out += [Alert(f"fed_document_changed:{k}", True, "warn", f"{s}: the source's text of {k} changed; a new "
                      "version was stored (its AI outputs are for the old text)") for k, s in sorted(changed.items())]
        if scope is None:
            out += [Alert(f"fed_document_changed:{k}", False) for k in sorted(open_ - set(changed))]
        return out
