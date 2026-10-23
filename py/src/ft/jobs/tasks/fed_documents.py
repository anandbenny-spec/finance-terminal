"""policy.fed_documents, part 1: FOMC statements (Prompt 01 B5; Architecture 02 §2; Spec 01 §1.1, §2, §3.1). Minutes,
press-conference transcripts and their alerts are part 2.

**Which document is a meeting's statement.** The calendar's link decides (Spec 01 §1.1): `policy.calendar` stores the
meetings, and this task reads the calendar pages for their "Statement: HTML" links (the 2018-2020 historical pages
from the copies the calendar backfill stored). The Fed's RSS is a trigger: an item counts only if it's titled
"FOMC statement", links the statement of the meeting being watched (monetaryYYYYMMDDa.htm on its decision date) and the
page passes the statement checks. On a decision day the feed can list the statement before the calendar links it; in
that gap the RSS item's link is used, and a later run's `calendar_link` check (W) confirms the calendar links the same
page. Notation votes aren't meetings, so their pages are never read; the implementation note (`a1`) never is either.

**When it runs:**
- watch, on an FOMC decision day until the statement is stored: one look at the feed and the calendar page per poll
  (conditional requests, so an unchanged page answers 304), then the statement once it appears;
- daily (every 20 hours): any rate decision of the last 60 days released without a stored statement, or not yet
  marked held (its statement stored only as the next meeting's diff base: read back, not fetched again);
- backfill (`--task policy.fed_documents [--since] [--until]`): every rate decision in the range, from the 2018
  baseline. A statement already stored is read back from its raw file, not fetched again, and an unchanged one writes
  nothing (B8); each meeting loads in its own savepoint, so one bad document doesn't block the rest (Arch 02 §1.8).

**What a statement stores:** the raw file (through the fetcher), the document (its canonical text), its chunks, the
diff against the previous meeting's statement, the target range (`fed_target_lower`, `fed_target_upper`, each with
its change against the previous meeting and the decision sentence's span), the votes, and the meeting marked `held`
and published with its document. If the previous meeting's statement isn't stored (the first meeting of a partial
backfill), it's fetched and stored too, unpublished, as the diff's base and for the change. The baseline's change is
measured against FRED's DFEDTARU/L the day before (Spec 01 §2); until policy.market_series has loaded those, its rate
rows wait (`baseline_change`, W) and the baseline stays unpublished.

**Votes:** a named statement (to Apr 2026) gives every member's vote; a tally statement (from Jun 2026) gives none
here, since the minutes name the voters (decided by Anand, 23 Oct 2026; part 2). The roster is config/members.yaml.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from ft.engines.document.chunk import chunk_text
from ft.engines.document.diff import DIFF_VERSION, diff_texts
from ft.engines.document.store import DocumentRef, save_chunks, save_diff, save_version
from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register
from ft.policy import fed_calendar, fed_statement, members
from ft.policy.fed_statement import Range, Statement, StatementError, bps
from ft.policy.meetings import FIRST_DAY

NY = ZoneInfo("America/New_York")
DOC_TYPE = "fomc_statement"
CATCH_UP = timedelta(days=60)            # daily: rate decisions this recent without a stored statement
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

    def released(self, now: datetime) -> bool:
        return self.occurs_at <= now if self.occurs_at else self.decision_date < now.astimezone(NY).date()


@dataclass(frozen=True)
class Page:
    url: str
    how: str                             # 'calendar' | 'rss' | 'stored'
    raw_file_id: int | None
    body: bytes


@dataclass
class Fetched:
    now: datetime
    workflow: str
    meetings: list[Meeting]              # every stored Fed rate decision, oldest first
    targets: list[str]                   # slugs to load, oldest first
    pages: dict[str, Page]               # by slug: targets and the previous statements they need
    calendar: dict[date, str]            # statement links on the calendar pages read, by decision date
    rss: dict[date, str]                 # statement links in the feed, by decision date
    missing: list[str] = field(default_factory=list)   # released targets with no link found


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


def _today(now: datetime) -> date:
    return now.astimezone(NY).date()


def _title(d: date) -> str:
    return f"FOMC statement, {d.day} {d:%b} {d.year}"


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
            """select e.id, e.slug, coalesce(e.ends_on, e.starts_on), e.occurs_at, e.status, e.is_published,
                      d.id, d.raw_file_id, d.source_url
                 from core.event e
                 join core.entity b on b.id = e.entity_id and b.kind = 'central_bank' and b.slug = 'fed'
                 join policy.meeting m on m.event_id = e.id
                 left join lateral (select d.id, d.raw_file_id, d.source_url from core.document d
                                     where d.event_id = e.id and d.doc_type = %s and d.text_sha256 is not null
                                     order by d.version_no desc limit 1) d on true
                where e.event_type = 'policy_meeting' and m.is_rate_decision and e.status <> 'cancelled'
                  and e.slug ~ '^fomc-[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                order by coalesce(e.ends_on, e.starts_on), e.id""", (DOC_TYPE,)).fetchall()
        rows = [r for r in rows if self.scope is None or r[1] in self.scope]
        out = [Meeting(*r[:9], baseline=False) for r in rows]
        if out and out[0].decision_date < self.first_day:
            out[0] = Meeting(*rows[0][:9], baseline=True)
        return out

    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        allm = self._meetings(f.db)
        by_slug = {m.slug: m for m in allm}
        now, wf = ctx.now, ctx.workflow
        if ctx.meeting is not None:                                   # watch: the meeting being watched
            targets = [by_slug[ctx.meeting.slug]] if ctx.meeting.slug in by_slug else []
            targets = [m for m in targets if m.statement_id is None]
        elif wf == "backfill":
            until = ctx.until or _today(now)
            targets = [m for m in allm if m.released(now) and m.decision_date <= until
                       and (ctx.since is None or m.decision_date >= ctx.since)]
        else:                                                         # daily, refresh: catch up, and retry a meeting
            targets = [m for m in allm if (m.statement_id is None or m.status != "held") and m.released(now)
                       and m.decision_date >= _today(now) - CATCH_UP]  # stored only as another's diff base
        slugs = [m.slug for m in targets]
        needed = []                                                   # previous statements the targets need
        for m in targets:
            i = allm.index(m)
            if i and allm[i - 1].statement_id is None and allm[i - 1].slug not in slugs:
                needed.append(allm[i - 1])
        to_get = [m for m in needed + targets if m.statement_id is None]

        calendar: dict[date, str] = {}
        rss: dict[date, str] = {}
        # Daily and refresh runs always read the current calendar page (one conditional request), so a statement first
        # taken from the RSS is checked against the calendar's link once the calendar lists it (`calendar_link`).
        if to_get or wf not in ("backfill", "watch"):
            years = {m.decision_date.year for m in to_get}
            pages = sorted({fed_calendar.historical(y) for y in years if y in HISTORICAL_YEARS}
                           | ({fed_calendar.CALENDAR} if wf not in ("backfill", "watch")
                              or any(y not in HISTORICAL_YEARS for y in years) else set()))
            for url in pages:
                got = f.stored(url, source="fed") if url != fed_calendar.CALENDAR else None
                if got is None:
                    got = f.get(url, source="fed")
                for row in fed_calendar.parse_page(got.body if got.body is not None else f.read(got.raw_file_id), url):
                    if row.label != "notation vote" and row.statement_url and row.statement_date:
                        calendar[row.statement_date] = row.statement_url
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
        return Fetched(now, wf, allm, slugs, out, calendar, rss, missing)

    # ------------------------------------------------------------------ load (one transaction; a savepoint per meeting)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        roster = members.load(self.members_path)
        ids, written = members.sync(tx, roster)
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
        self.result = {"outcomes": outcomes, "roster": len(roster.members), "roster_written": written - sum(
            o.written for o in outcomes)}
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
            have = tx.execute("select base_document_id, diff_version from core.document_diff where document_id = %s",
                              (doc_id,)).fetchone()
            if saved.new_text or have != (base_id, DIFF_VERSION):
                save_diff(tx, doc_id, base_id, diff_texts(self._text(tx, base_id), st.text.text))
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

        # Votes: every member a named statement lists (Spec 01 §2, "Fed votes"); none from a tally statement.
        v = st.votes
        if v.format == "named" and previous is not None:
            rows, nv = self._votes(tx, m, st, previous, change[1], roster, ids, doc_id, checks)
            n += nv
            o.votes, o.dissents = rows, len(v.dissents)
        elif v.format == "named":
            pass                                                     # the baseline without its change: votes wait too

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
        want: dict[int, tuple] = {}
        unknown, problems = [], []
        for name in v.voters_for:
            mem = roster.match("FED", name)
            if mem is None:
                unknown.append(name)
                continue
            want[ids[mem.slug]] = (decided_bps, False, None, v.sentence, mem, name)
        for ds in v.dissents:
            mem = roster.match("FED", ds.name)
            if mem is None:
                unknown.append(ds.name)
                continue
            try:
                pref = ds.preference.change_bps(previous)
            except StatementError as e:
                problems.append(f"{ds.name}: {e}")
                continue
            if pref is not None and pref % 25:
                problems.append(f"{ds.name}: {pref:+d} bps isn't a multiple of 25")
            if pref == decided_bps:
                problems.append(f"{ds.name} dissented but preferred the decision ({pref:+d} bps)")
            want[ids[mem.slug]] = (pref, True, ds.text, v.against_sentence, mem, ds.name)
        checks.append(Check("voters_on_roster", not unknown, f"{m.slug}: not in config/members.yaml: "
                            + ", ".join(unknown) if unknown else f"{m.slug}: {len(want)} voters"))
        if unknown:
            raise MeetingFailed(checks)
        dissenters = sorted(x[5] for x in want.values() if x[1])
        ok = not problems and dissenters == sorted(x.name for x in v.dissents) and len(want) == v.voters
        checks.append(Check("dissents_match", ok, f"{m.slug}: " + ("; ".join(problems) if problems
                                                                  else f"{len(dissenters)} dissents")))
        if not ok:
            raise MeetingFailed(checks)

        # (W) Each voter's term covers the meeting: a member, or an alternate the statement names.
        alternates = {x[4].slug for x in want.values() if any(fed_statement._same(a, x[5]) for a in v.alternates)}
        odd = []
        for mid, (_, _, _, _, mem, name) in want.items():
            t = mem.term_on(m.decision_date)
            if t is None:
                odd.append(f"{name}: no term on {m.decision_date}")
            elif t.is_voter == (mem.slug in alternates):
                odd.append(f"{name}: {'a voting member' if t.is_voter else 'not a voting member'} on the roster"
                           + (", but voted as an alternate" if mem.slug in alternates else ""))
        checks.append(Check("voter_terms", not odd, f"{m.slug}: " + "; ".join(odd), blocking=False))

        n = 0
        stored = {r[0] for r in tx.execute("select member_id from policy.vote where meeting_event_id = %s",
                                           (m.event_id,)).fetchall()}
        for gone in stored - set(want):
            n += tx.execute("delete from policy.vote where meeting_event_id = %s and member_id = %s",
                            (m.event_id, gone)).rowcount
        for mid, (chg, dissent, text, (s, e), _, _) in want.items():
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
                (m.event_id, mid, chg, dissent, text, doc_id, s, e)).rowcount
        return len(want), n

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
        backfill = f.workflow == "backfill"
        out: list[Check] = []

        failed = [o for o in outcomes if o.status == "failed"]
        if backfill:
            out.append(Check("meetings_failed", not failed, "; ".join(
                f"{o.slug}: " + ", ".join(f"{c.name} ({c.detail.split(': ', 1)[-1]})" for c in o.checks
                                          if c.blocking and not c.passed) for o in failed), blocking=False))
        else:
            by_name: dict[str, list[str]] = {}
            for o in failed:
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
                ("votes", votes == (o.votes if named and o.rates else 0)),
                ("dissents", dissents == (o.dissents if named and o.rates else 0))) if not ok]
            if bad:
                wrong.append(f"{o.slug}: {', '.join(bad)}")
        out.append(Check("statements_stored", not wrong, "; ".join(wrong) if wrong
                         else json.dumps(self.counts(f), sort_keys=True)))

        # Warn checks from the meetings that loaded (baseline_change, voter_terms), one per name.
        warn: dict[str, list[str]] = {}
        for o in loaded:
            for c in o.checks:
                if not c.blocking and not c.passed:
                    warn.setdefault(c.name, []).append(c.detail)
        for name in ("baseline_change", "voter_terms"):
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

        # (W) Released decisions in the catch-up window without a statement link (watch: not yet; that's normal).
        if f.workflow not in ("watch", "backfill"):
            out.append(Check("statement_missing", not f.missing, "no statement link yet: " + ", ".join(f.missing),
                             blocking=False))

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
        c = Counter(o.status for o in outcomes)
        loaded = [o for o in outcomes if o.status == "loaded"]
        return {
            "targets": len(outcomes), "loaded": c["loaded"], "waiting": c["waiting"], "failed": c["failed"],
            "named_votes": sum(o.votes for o in loaded), "dissents": sum(o.dissents for o in loaded),
            "tally_statements": sum(o.statement.votes.format == "tally" for o in loaded),
            "rates_pending": sum(not o.rates for o in loaded), "from_rss": sum(o.how == "rss" for o in loaded),
            "roster": self.result["roster"],
        }

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """A backfill's failed meetings, one alert each; resolved once the meeting's statement is published."""
        failed = {o.slug: o for o in self.result.get("outcomes", []) if o.status == "failed"}
        out = [Alert(f"fed_statement_failed:{s}", True, "warn", f"{s}: " + "; ".join(
            c.detail for c in o.checks if c.blocking and not c.passed)) for s, o in sorted(failed.items())]
        open_ = {r[0].split(":", 1)[1] for r in db.execute(
            "select dedupe_key from ops.alert where dedupe_key like 'fed_statement_failed:%' and resolved_at is null")}
        done = {r[0] for r in db.execute(
            """select e.slug from core.event e join core.document d on d.event_id = e.id and d.doc_type = %s
                where e.slug = any(%s) and d.is_published""", (DOC_TYPE, sorted(open_ - set(failed)))).fetchall()}
        return out + [Alert(f"fed_statement_failed:{s}", False) for s in sorted(done)]
