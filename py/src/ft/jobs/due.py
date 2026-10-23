"""Due rules (Architecture 02 §1.3; Prompt 01 A2), as config/tasks.yaml names them.

- `every <interval>`: since the task's last success, in any workflow.
- `fomc_decision_day` / `mpc_decision_day`: the bank's decision date is today in its own time zone, its decision
  document (the statement or the resolution) isn't stored, and the run is inside that meeting's watch window.
- `after_decision_document`: a decision-day document is stored, the AI step hasn't succeeded in a watch run since it
  was stored, and the meeting's watch window is still open (the AI step polls like the fetch: Architecture 03 §1.3).
  Only watch runs count: a daily, refresh or backfill run of the AI step sends nothing in real time (B7), so it
  mustn't stand in for the decision-day step.

The watch window is anchored to the meeting's release time (core.event.occurs_at, else the bank's usual time),
not to when the run started, so a run started early isn't cut short and a delayed start still polls. It opens
LEAD before the release, so a manual start a few minutes early polls from the start; a run that starts earlier
than that (the other bank's scheduled watch, hours away) finds the task not due and exits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ft.jobs.config import every_interval

LEAD = timedelta(minutes=10)


@dataclass(frozen=True)
class Bank:
    slug: str                # core.entity slug
    name: str                # in reasons and logs
    tz: ZoneInfo
    decision_doc: str        # core.doc_type of the decision document
    release: time            # the usual release time, local, when occurs_at isn't stored
    window: timedelta        # polling continues this long after the release (Architecture 02 §1.1)


BANKS = {
    "fomc_decision_day": Bank("fed", "FOMC", ZoneInfo("America/New_York"), "fomc_statement", time(14, 0),
                              timedelta(minutes=40)),
    "mpc_decision_day": Bank("rbi", "MPC", ZoneInfo("Asia/Kolkata"), "mpc_resolution", time(10, 0),
                             timedelta(minutes=60)),
}


@dataclass(frozen=True)
class Meeting:
    event_id: int
    slug: str
    bank: Bank
    decision_date: date
    release_at: datetime            # aware
    stored_at: datetime | None      # when its decision document was first stored, if it is

    @property
    def opens_at(self) -> datetime:
        return self.release_at - LEAD

    @property
    def closes_at(self) -> datetime:
        return self.release_at + self.bank.window

    def is_open(self, now: datetime) -> bool:
        return self.opens_at <= now < self.closes_at


@dataclass(frozen=True)
class Due:
    due: bool
    reason: str
    meeting: Meeting | None = None    # decision-day rules: the meeting being watched
    until: datetime | None = None     # decision-day rules: when the watch window closes


def local(ts: datetime, bank: Bank) -> str:
    return ts.astimezone(bank.tz).strftime("%Y-%m-%d %H:%M %Z")


_MEETING = """
select e.id, e.slug, e.occurs_at, coalesce(e.ends_on, e.starts_on) as last_day,
       (select min(d.created_at) from core.document d where d.event_id = e.id and d.doc_type = %(doc)s) as stored_at
from core.event e
join core.entity b on b.id = e.entity_id and b.kind = 'central_bank'
join policy.meeting m on m.event_id = e.id
where e.event_type = 'policy_meeting' and b.slug = %(bank)s and e.status <> 'cancelled' and m.is_rate_decision
  and coalesce((e.occurs_at at time zone %(tz)s)::date, coalesce(e.ends_on, e.starts_on)) = %(day)s
order by e.occurs_at nulls last, e.id
limit 1"""


def decision_meeting(db, bank: Bank, day: date) -> Meeting | None:
    """The bank's rate-decision meeting whose decision date, in the bank's time zone, is `day`."""
    row = db.execute(_MEETING, {"doc": bank.decision_doc, "bank": bank.slug, "tz": bank.tz.key, "day": day}).fetchone()
    if row is None:
        return None
    event_id, slug, occurs_at, last_day, stored_at = row
    release_at = occurs_at or datetime.combine(last_day, bank.release, tzinfo=bank.tz)
    return Meeting(event_id, slug, bank, release_at.astimezone(bank.tz).date(), release_at, stored_at)


def meetings_on(db, day: date) -> list[Meeting]:
    """Every decision meeting on `day` (each bank's own calendar date), earliest release first."""
    found = [m for b in BANKS.values() if (m := decision_meeting(db, b, day))]
    return sorted(found, key=lambda m: m.release_at)


def _last_success(db, task_key: str, workflow: str | None = None) -> datetime | None:
    if workflow is None:
        return db.execute("select max(finished_at) from ops.task_run where task_key = %s and status = 'success'",
                          (task_key,)).fetchone()[0]
    return db.execute("""select max(t.finished_at) from ops.task_run t join ops.job_run j on j.id = t.job_run_id
                          where t.task_key = %s and t.status = 'success' and j.workflow = %s""",
                      (task_key, workflow)).fetchone()[0]


def _decision_day(db, bank: Bank, now: datetime) -> Due:
    m = decision_meeting(db, bank, now.astimezone(bank.tz).date())
    if m is None:
        return Due(False, f"not an {bank.name} decision day")
    if m.stored_at is not None:
        return Due(False, f"{m.slug}: the {bank.decision_doc} is stored")
    if now < m.opens_at:
        return Due(False, f"{m.slug}: too early, the watch window opens at {local(m.opens_at, bank)}")
    if now >= m.closes_at:
        return Due(False, f"{m.slug}: the watch window closed at {local(m.closes_at, bank)}")
    return Due(True, f"{m.slug}: {bank.name} decision day, watching until {local(m.closes_at, bank)}",
               meeting=m, until=m.closes_at)


def _after_decision_document(db, task_key: str, now: datetime) -> Due:
    reasons = []
    for bank in BANKS.values():
        m = decision_meeting(db, bank, now.astimezone(bank.tz).date())
        if m is None:
            continue
        if m.stored_at is None:
            reasons.append(f"{m.slug}: the {bank.decision_doc} isn't stored yet")
        elif not m.is_open(now):
            reasons.append(f"{m.slug}: outside the watch window")
        elif (last := _last_success(db, task_key, "watch")) is not None and last > m.stored_at:
            reasons.append(f"{m.slug}: already ran after the {bank.decision_doc} was stored")
        else:
            return Due(True, f"{m.slug}: the {bank.decision_doc} is stored", meeting=m, until=m.closes_at)
    return Due(False, "; ".join(reasons) or "not a decision day")


def evaluate(db, task_key: str, rule: str, now: datetime) -> Due:
    """Is the task due under `rule` at `now` (aware)? `db` is the runner's control connection."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if (interval := every_interval(rule)) is not None:
        last = _last_success(db, task_key)
        if last is None:
            return Due(True, "never succeeded")
        age = now - last
        return Due(age >= interval, f"last success {age} ago (rule: {rule})")
    if rule in BANKS:
        return _decision_day(db, BANKS[rule], now)
    if rule == "after_decision_document":
        return _after_decision_document(db, task_key, now)
    raise ValueError(f"unknown due rule {rule!r}")


def utcnow() -> datetime:
    return datetime.now(UTC)
