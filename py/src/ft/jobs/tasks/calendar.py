"""policy.calendar: the FOMC and MPC meeting calendars (Prompt 01 B3; Architecture 02 §2; Spec 01 §1.1, §2 "Meetings").

Fetches:
- weekly (every 25 days): the FOMC calendar page (2021 to the end of next year) and the RBI's current Annual Policy
  page (this fiscal year's resolutions, minutes and schedule);
- backfill (`--workflow backfill --task policy.calendar`): also the Fed's 2018-2020 historical pages and every
  earlier fiscal year's Annual Policy page by form post, from 2018-19, for the two 2018 baselines. A closed fiscal
  year's page already stored is read back instead of fetched again (a re-run writes no new raw files);
- every run: the "next meeting" line of the latest stored MPC resolution (stored by policy.rbi_documents), for a
  meeting the schedule doesn't list yet.
A backfill's --since and --until don't apply: the calendar always reads from the 2018 baselines (13 pages in all).

Loads one core.event + policy.meeting per meeting (meetings.py has the rules): upcoming meetings as `scheduled` and
published at once (the countdown); past ones unpublished until their decision document passes its checks (B5, B6
mark them `held`). The exception is a special meeting (RBI's no. 39, 3 Nov 2022): it is `held` from the start and has
no decision document to publish it later, so the calendar publishes it itself and its page answers (Spec 01 §4.5;
launch check #87). A meeting the calendar has moved keeps its row under its new date. Statuses only move forward.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ft.jobs.runner import Alert, Check, Context, Task
from ft.jobs.tasks import register
from ft.policy import fed_calendar, meetings, rbi_calendar
from ft.policy.meetings import Meeting

NEW_YORK, INDIA = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")


def publishes_at_once(m: Meeting, today: date) -> bool:
    """Whether the calendar itself publishes this meeting: an upcoming scheduled one (the countdown), or a held special
    meeting. Every other past meeting waits for its decision document to pass its checks (B5, B6). A special meeting has
    no decision document, so without this it stayed unpublished and its page answered 404 (launch check #87)."""
    return (m.status == "scheduled" and m.decision_date >= today) or (m.kind == "special" and m.status == "held")
HISTORICAL_YEARS = (2018, 2019, 2020)    # the calendar page covers 2021 on (Spec 01 §1.1)
FIRST_FISCAL_END = 2019                  # FY 2018-19 holds the RBI's Dec 2018 baseline
CLOSED_AFTER = timedelta(days=60)        # a fiscal year's page is final this long after 31 March
MOVE_WINDOW = timedelta(days=31)         # an upcoming meeting this close to a dropped one is the same meeting, moved
NEXT_WITHIN = timedelta(days=120)        # Architecture 02 §2 (W)
NOTICE_RECENT = timedelta(days=45)
SLUG = re.compile(r"^(fomc|mpc)-\d{4}-\d{2}-\d{2}$")


def _today(now: datetime, bank: str) -> date:
    return now.astimezone(NEW_YORK if bank == "fed" else INDIA).date()


@dataclass
class Fetched:
    now: datetime
    full: bool
    fed_rows: list[fed_calendar.Row]
    rbi: rbi_calendar.Calendar
    curated: meetings.CuratedConfig
    pages: list[str] = field(default_factory=list)       # what was read, for the counts check


@dataclass
class Stored:
    event_id: int
    bank: str
    slug: str
    decision_date: date
    status: str
    is_published: bool
    meeting_no: int | None


@register
class PolicyCalendar(Task):
    key = "policy.calendar"
    curated_path = meetings.CURATED      # tests point this at their own file

    def __init__(self):
        self.result: dict = {}

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        full = ctx.workflow == "backfill"
        f = ctx.fetcher
        curated = meetings.load_curated(self.curated_path)
        pages, rows = [], []
        fed_pages = [fed_calendar.CALENDAR] + ([fed_calendar.historical(y) for y in HISTORICAL_YEARS] if full else [])
        for url in fed_pages:
            got = f.get(url, source="fed")
            rows += fed_calendar.parse_page(got.body if got.body is not None else f.read(got.raw_file_id), url)
            pages.append(url)
        got = f.get(rbi_calendar.ANNUAL_POLICY, source="rbi")
        current = rbi_calendar.parse_page(got.body if got.body is not None else f.read(got.raw_file_id))
        rbi_pages = [current]
        pages.append(rbi_calendar.ANNUAL_POLICY)
        if full:
            years = rbi_calendar.fiscal_years(current, FIRST_FISCAL_END)
            if not years:
                raise rbi_calendar.PageError("the Annual Policy page's year selector lists no fiscal year from 2018-19")
            latest = max(int(v) for _, v in years)
            today = _today(ctx.now, "rbi")
            for label, value in years:
                if int(value) == latest:
                    continue          # the page as served is the current fiscal year
                record = f"{rbi_calendar.ANNUAL_POLICY}#hdnYear={value}"
                closed = today > date(int(value), 3, 31) + CLOSED_AFTER
                got = f.stored(record, source="rbi") if closed else None
                if got is None:
                    got = f.post(rbi_calendar.ANNUAL_POLICY, rbi_calendar.year_form(current, value), source="rbi",
                                 record_url=record, media_type="text/html")
                body = got.body if got.body is not None else f.read(got.raw_file_id)
                page = rbi_calendar.parse_page(body)
                if not page.groups or all(g.posted_on.year > int(value) for g in page.groups):
                    raise rbi_calendar.PageError(f"the form post for fiscal year {label} returned another year")
                rbi_pages.append(page)
                pages.append(record)
        return Fetched(ctx.now, full, rows, rbi_calendar.calendar(rbi_pages), curated, pages)

    # ------------------------------------------------------------------ load (one transaction)
    def _stored(self, tx) -> dict[str, Stored]:
        rows = tx.execute(
            """select e.id, b.slug, e.slug, coalesce(e.ends_on, e.starts_on), e.status, e.is_published, m.meeting_no
                 from core.event e
                 join core.entity b on b.id = e.entity_id and b.kind = 'central_bank'
                 join policy.meeting m on m.event_id = e.id
                where e.event_type = 'policy_meeting' and b.slug in ('fed', 'rbi')""").fetchall()
        return {r[2]: Stored(*r) for r in rows if SLUG.match(r[2])}

    def _next_lines(self, tx, now: datetime) -> list[tuple[date, date]]:
        """The "next meeting" line of the latest MPC resolution stored and released by the run's clock, if any."""
        row = tx.execute(
            """select string_agg(c.body, '' order by c.chunk_no)
                 from core.document d join core.chunk c on c.document_id = d.id
                where d.id = (select d2.id from core.document d2
                               where d2.doc_type = 'mpc_resolution' and d2.text_sha256 is not null
                                 and d2.published_at <= %s
                               order by d2.published_at desc, d2.id desc limit 1)""", (now,)).fetchone()
        nxt = rbi_calendar.next_meeting(row[0]) if row and row[0] else None
        return [nxt] if nxt else []

    def load(self, tx, f: Fetched, task_run_id) -> int:
        today = {b: _today(f.now, b) for b in ("fed", "rbi")}
        fed, votes = meetings.fed_meetings(f.fed_rows, f.curated, full=f.full)
        rbi = meetings.rbi_meetings(f.rbi, f.curated, today["rbi"], full=f.full, next_lines=self._next_lines(tx, f.now))
        stored = self._stored(tx)

        # Moves: an upcoming stored meeting the calendar no longer lists, inside the dates the calendar covers, is the
        # same meeting under a new date if an unstored upcoming one sits within MOVE_WINDOW; otherwise it disappeared.
        # The dates a calendar covers end at its last listed meeting (a resolution's "next meeting" line doesn't count).
        covers = {"fed": max((m.decision_date for m in fed), default=today["fed"]),
                  "rbi": max([r.decision_date for r in f.rbi.resolutions] + [s.days[1] for s in f.rbi.schedule],
                             default=today["rbi"])}
        moves, gone = [], []
        for bank, parsed in (("fed", fed), ("rbi", rbi)):
            slugs = {m.slug for m in parsed}
            upto = covers[bank]
            fresh = [m for m in parsed if m.slug not in stored and m.status == "scheduled"
                     and m.decision_date >= today[bank]]
            for s in sorted(stored.values(), key=lambda s: s.decision_date):
                if s.bank != bank or s.slug in slugs or s.status != "scheduled" or not today[bank] <= s.decision_date <= upto:
                    continue
                near = [m for m in fresh if abs(m.decision_date - s.decision_date) <= MOVE_WINDOW]
                if near:
                    m = min(near, key=lambda m: abs(m.decision_date - s.decision_date))
                    fresh.remove(m)
                    moves.append((s, m))
                else:
                    gone.append(s)
        for s, m in moves:
            tx.execute("update core.event set slug = %s where id = %s", (m.slug, s.event_id))
            del stored[s.slug]
            stored[m.slug] = Stored(s.event_id, s.bank, m.slug, m.decision_date, s.status, s.is_published, s.meeting_no)

        numbers = {s.decision_date: s.meeting_no for s in stored.values() if s.bank == "rbi" and s.status != "cancelled"}
        rbi = meetings.number_rbi(rbi, numbers, f.curated)

        written = 0
        for m in fed + rbi:
            written += self._upsert(tx, m, stored.get(m.slug), today[m.bank])
        self.result = {"fed": fed, "rbi": rbi, "votes": votes, "moves": moves, "gone": gone, "today": today,
                       "written": written}
        return written

    def _upsert(self, tx, m: Meeting, s: Stored | None, today: date) -> int:
        publish = publishes_at_once(m, today)
        if s is None:
            eid = tx.execute(
                """insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at, status,
                                           source_id, source_url, is_published)
                   select 'policy_meeting', b.id, %s, %s, %s, %s, %s, %s, src.id, %s, %s
                     from core.entity b, core.source src
                    where b.kind = 'central_bank' and b.slug = %s and src.key = %s
                   returning id""",
                (m.slug, m.title, m.starts_on, m.ends_on, m.occurs_at, m.status, m.source_url, publish,
                 m.bank, m.bank)).fetchone()[0]
            tx.execute("""insert into policy.meeting (event_id, meeting_no, kind, is_rate_decision, has_press_conference,
                                                      minutes_due_on)
                          values (%s, %s, %s, %s, %s, %s)""",
                       (eid, m.meeting_no, m.kind, m.is_rate_decision, m.has_press_conference, m.minutes_due_on))
            return 1
        # Statuses only move forward: a scheduled meeting can become cancelled, or held where there's no decision
        # document to wait for; held (set by the documents tasks) and cancelled stay as they are.
        status = m.status if s.status == "scheduled" and m.status != "scheduled" else s.status
        # Each update writes only when a value changes, so a re-run writes nothing (rows_written 0).
        v = {"id": s.event_id, "title": m.title, "starts": m.starts_on, "ends": m.ends_on, "at": m.occurs_at,
             "status": status, "url": m.source_url, "publish": publish, "no": m.meeting_no, "kind": m.kind,
             "rate": m.is_rate_decision, "press": m.has_press_conference, "minutes": m.minutes_due_on}
        ev = tx.execute(
            """update core.event set title = %(title)s, starts_on = %(starts)s, ends_on = %(ends)s, occurs_at = %(at)s,
                      status = %(status)s, source_url = %(url)s, is_published = is_published or %(publish)s
                where id = %(id)s
                  and (title, starts_on, ends_on, occurs_at, status, source_url, is_published)
                      is distinct from (%(title)s, %(starts)s::date, %(ends)s::date, %(at)s::timestamptz, %(status)s,
                                        %(url)s, is_published or %(publish)s)""", v).rowcount
        pm = tx.execute(
            """update policy.meeting set meeting_no = coalesce(%(no)s, meeting_no), kind = %(kind)s,
                      is_rate_decision = %(rate)s, has_press_conference = coalesce(%(press)s, has_press_conference),
                      minutes_due_on = %(minutes)s
                where event_id = %(id)s
                  and (meeting_no, kind, is_rate_decision, has_press_conference, minutes_due_on)
                      is distinct from (coalesce(%(no)s::integer, meeting_no), %(kind)s, %(rate)s,
                                        coalesce(%(press)s::boolean, has_press_conference), %(minutes)s::date)""",
            v).rowcount
        return 1 if ev or pm else 0

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        fed, rbi, today = r["fed"], r["rbi"], r["today"]
        out = []

        # (B) Every meeting parsed is stored as parsed: the load did what the calendars say.
        stored = {row[0]: row[1:] for row in tx.execute(
            """select e.slug, e.starts_on, coalesce(e.ends_on, e.starts_on), e.occurs_at, m.kind, m.is_rate_decision,
                      m.minutes_due_on, m.meeting_no
                 from core.event e join policy.meeting m on m.event_id = e.id
                where e.event_type = 'policy_meeting'""").fetchall()}
        wrong = [m.slug for m in fed + rbi
                 if (got := stored.get(m.slug)) is None
                 or got[:6] != (m.starts_on, m.decision_date, m.occurs_at, m.kind, m.is_rate_decision, m.minutes_due_on)
                 or (m.meeting_no is not None and got[6] != m.meeting_no)]
        out.append(Check("meetings_stored", not wrong, "differ after the load: " + ", ".join(wrong[:10]) if wrong
                         else json.dumps(self.counts(f), sort_keys=True)))

        # (B) Dates parse and make sense; upcoming meetings lie in the future (Architecture 02 §2).
        bad = [m.slug for m in fed + rbi if not (date(2018, 1, 1) <= m.starts_on <= m.decision_date
                                                <= today[m.bank] + timedelta(days=3 * 366))
               or (m.decision_date - m.starts_on).days > 6]
        late = [m.slug for m in fed + rbi if m.from_schedule and m.decision_date < today[m.bank] - timedelta(days=1)]
        out.append(Check("dates_valid", not bad and not late,
                         "; ".join(filter(None, [bad and "bad dates: " + ", ".join(bad),
                                                 late and "listed as upcoming but past (no decision document linked): "
                                                 + ", ".join(late)]))))

        # (B) Every FOMC rate decision's statement is dated its decision day (the calendar decides which document is
        # the statement: Spec 01 §1.1; the 2 Mar 2020 call is curated to its 3 Mar release).
        off = [f"{m.slug} (statement {m.decision_url.rsplit('/', 1)[-1]})" for m in fed
               if m.decision_url and _statement_day(m.decision_url) != m.decision_date]
        out.append(Check("statement_dates", not off, ", ".join(off)))

        # (B) MPC numbering: unique, consecutive from the anchor, and no. 39 on 3 Nov 2022 (Spec 01 §2).
        nums = [m.meeting_no for m in rbi if m.meeting_no is not None]
        problems = []
        if len(nums) != len(set(nums)):
            problems.append("duplicate numbers")
        for (bank, d), c in f.curated.rows.items():
            got = next((m.meeting_no for m in rbi if m.decision_date == d), "absent")
            if bank == "rbi" and c.meeting_no is not None and got not in (c.meeting_no, "absent", None):
                problems.append(f"mpc-{d} is no. {got}, curated as no. {c.meeting_no}")
        if f.full and any(m.meeting_no is None for m in rbi):
            problems.append("a meeting without a number after a full read")
        out.append(Check("mpc_numbers", not problems, "; ".join(problems)))

        # (B) A full read finds every curated meeting and each bank's December 2018 baseline.
        if f.full:
            missing = [f"{b}.{d}" for (b, d) in f.curated.rows
                       if all(m.decision_date != d for m in (fed if b == "fed" else rbi))]
            base = {b: min(m.decision_date for m in ms) for b, ms in (("fed", fed), ("rbi", rbi))}
            wrong_base = [f"{b} {d}" for b, d in base.items() if (d.year, d.month) != (2018, 12)]
            out.append(Check("curated_and_baselines", not missing and not wrong_base,
                             "; ".join(filter(None, [missing and "curated rows not on the calendars: " + ", ".join(missing),
                                                     wrong_base and "baseline not in Dec 2018: " + ", ".join(wrong_base)]))))

        # (B) No upcoming meeting dropped off a calendar without a nearby replacement (a parse that lost a meeting
        # must not cancel a decision day).
        out.append(Check("no_meeting_dropped", not r["gone"], "upcoming meetings no longer on the calendar: "
                         + ", ".join(s.slug for s in r["gone"]) if r["gone"] else ""))

        # (W) The next meeting of each bank is known within 120 days (Architecture 02 §2).
        far = []
        for bank, ms in (("fed", fed), ("rbi", rbi)):
            nxt = [m.decision_date for m in ms if m.status == "scheduled" and m.decision_date >= today[bank]]
            if not nxt or min(nxt) - today[bank] > NEXT_WITHIN:
                far.append(f"{bank}: {'none listed' if not nxt else min(nxt)}")
        out.append(Check("next_meeting_known", not far, "; ".join(far), blocking=False))

        # (W) Moved meetings and recent RBI reschedule notices: worth a look (the dates came from the page).
        out.append(Check("meetings_moved", not r["moves"],
                         ", ".join(f"{s.slug} -> {m.slug}" for s, m in r["moves"]), blocking=False))
        recent = [n for n in f.rbi.reschedules if n.posted_on >= today["rbi"] - NOTICE_RECENT]
        out.append(Check("rbi_reschedule_notice", not recent,
                         "; ".join(f"{n.posted_on}: {n.title} ({n.url}): check the next meeting's dates" for n in recent),
                         blocking=False))
        return out

    def counts(self, f: Fetched) -> dict:
        """The counts the done-when check records (Prompt 01 B3), by bank."""
        r, out = self.result, {}
        for bank, ms in (("fed", r["fed"]), ("rbi", r["rbi"])):
            t = r["today"][bank]
            since = [m for m in ms if m.decision_date >= meetings.FIRST_DAY]
            out[bank] = {
                "stored": len(ms),
                "baseline": min(m.decision_date for m in ms).isoformat() if f.full and ms else None,
                "since_2019_rate_decisions_past": sum(m.is_rate_decision and m.decision_date < t for m in since),
                "since_2019_unscheduled": sum(m.kind == "unscheduled" for m in since),
                "since_2019_without_rate_decision": sum(not m.is_rate_decision and m.status != "cancelled"
                                                        for m in since),
                "cancelled": sum(m.status == "cancelled" for m in ms),
                "upcoming": sum(m.status == "scheduled" and m.decision_date >= t for m in ms),
            }
        out["fed"]["notation_votes_skipped"] = len(r["votes"])
        out["pages"] = len(f.pages)
        return out

    # ------------------------------------------------------------------ alerts (after the load)
    def alerts(self, db) -> list[Alert]:
        """A published meeting still `scheduled` two days after its decision date (Architecture 02 §2): its decision
        document didn't arrive. One alert per meeting; cleared ones resolve."""
        late = {r[0] for r in db.execute(
            """select e.slug from core.event e join policy.meeting m on m.event_id = e.id
                where e.event_type = 'policy_meeting' and e.status = 'scheduled' and e.is_published
                  and m.is_rate_decision and coalesce(e.ends_on, e.starts_on) < current_date - 2""").fetchall()
                if SLUG.match(r[0])}
        open_ = {r[0].split(":", 1)[1] for r in db.execute(
            "select dedupe_key from ops.alert where dedupe_key like 'meeting_not_held:%' and resolved_at is null")}
        return ([Alert(f"meeting_not_held:{s}", True, "warn", f"{s} is still scheduled two days after its decision "
                       "date: its decision document hasn't been stored") for s in sorted(late)]
                + [Alert(f"meeting_not_held:{s}", False) for s in sorted(open_ - late)])


def _statement_day(url: str) -> date | None:
    m = re.search(r"monetary(\d{8})a\.htm$", url)
    return datetime.strptime(m[1], "%Y%m%d").date() if m else None
