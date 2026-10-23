"""Meetings as the calendars list them, with the rules of Spec 01 §2 ("Meetings") and the curated facts in
config/meeting_times.yaml (Spec 01 D8):

- one meeting per FOMC and MPC decision from 1 Jan 2019, plus each bank's last 2018 meeting as the baseline;
- slug `fomc-YYYY-MM-DD` / `mpc-YYYY-MM-DD` on the decision date (the last meeting day, or the curated release day:
  the Fed's 2 Mar 2020 call was announced on 3 Mar);
- `occurs_at`: the decision's release, 14:00 America/New_York (FOMC) or 10:00 Asia/Kolkata (MPC), unless
  meeting_times.yaml gives the meeting its own time; null where nothing is released;
- `minutes_due_on`: +21 days (FOMC, regularly scheduled meetings: the Fed's three-week rule) and +14 days (MPC, every
  meeting: RBI Act s.45ZL); null where no minutes are due (a cancelled meeting, MPC no. 39);
- MPC numbers counted from the curated anchor (the Dec 2018 meeting is no. 14), so no. 39 falls on 3 Nov 2022.

A notation vote (the FOMC voting without meeting) isn't a meeting. A cancelled meeting is kept as `cancelled`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ft.policy import fed_calendar, rbi_calendar

CURATED = Path(__file__).resolve().parents[3] / "config" / "meeting_times.yaml"
FIRST_DAY = date(2019, 1, 1)          # history starts here; the last meeting before it is the baseline

BANKS = {
    "fed": {"prefix": "fomc", "name": "FOMC", "tz": ZoneInfo("America/New_York"), "release": time(14, 0),
            "minutes_after": timedelta(days=21)},
    "rbi": {"prefix": "mpc", "name": "MPC", "tz": ZoneInfo("Asia/Kolkata"), "release": time(10, 0),
            "minutes_after": timedelta(days=14)},
}
KINDS = ("scheduled", "unscheduled", "special")


class CalendarError(ValueError):
    """The calendars and the curated config don't agree (a blocking problem: nothing is loaded)."""


@dataclass(frozen=True)
class Curated:
    bank: str
    decision_date: date
    kind: str
    is_rate_decision: bool
    release: time | None              # local time of the decision's release
    held: date | None                 # the calendar's date when it isn't the decision date (Fed, 2 Mar 2020)
    meeting_no: int | None
    source: str


@dataclass(frozen=True)
class CuratedConfig:
    rows: dict[tuple[str, date], Curated]
    rbi_anchor: tuple[date, int]      # (decision date, MPC meeting number)
    rbi_anchor_source: str


def _date(v, where: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise CalendarError(f"meeting_times.yaml {where}: not a date: {v!r}") from None


def load_curated(path: Path = CURATED) -> CuratedConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows: dict[tuple[str, date], Curated] = {}
    for bank in ("fed", "rbi"):
        for key, v in (raw.get(bank) or {}).items():
            d = _date(key, f"{bank}.{key}")
            where = f"{bank}.{d}"
            if not isinstance(v, dict) or not str(v.get("source") or "").strip():
                raise CalendarError(f"meeting_times.yaml {where}: every row needs a source note")
            unknown = set(v) - {"kind", "rate_decision", "release", "held", "meeting_no", "source"}
            if unknown:
                raise CalendarError(f"meeting_times.yaml {where}: unknown keys {sorted(unknown)}")
            kind = v.get("kind", "scheduled")
            if kind not in KINDS:
                raise CalendarError(f"meeting_times.yaml {where}: kind must be one of {KINDS}")
            rate = bool(v.get("rate_decision", True))
            rel = v.get("release")
            if rel is not None:
                try:
                    hh, mm = str(rel).split(":")
                    rel = time(int(hh), int(mm))
                except ValueError:
                    raise CalendarError(f"meeting_times.yaml {where}: release must be HH:MM, local") from None
            if rate and rel is None and kind != "scheduled":
                raise CalendarError(f"meeting_times.yaml {where}: an unscheduled decision needs its release time")
            rows[(bank, d)] = Curated(bank, d, kind, rate, rel, _date(v["held"], where) if v.get("held") else None,
                                      v.get("meeting_no"), str(v["source"]).strip())
    num = raw.get("rbi_numbering") or {}
    if not num.get("source") or "anchor" not in num or "meeting_no" not in num:
        raise CalendarError("meeting_times.yaml: rbi_numbering needs anchor, meeting_no and source")
    return CuratedConfig(rows, (_date(num["anchor"], "rbi_numbering"), int(num["meeting_no"])), str(num["source"]))


@dataclass(frozen=True)
class Meeting:
    bank: str                         # 'fed' | 'rbi' (core.entity slug)
    decision_date: date
    starts_on: date
    kind: str                         # scheduled | unscheduled | special
    is_rate_decision: bool
    status: str                       # what the calendar alone says: scheduled, cancelled, or held (no decision to wait for)
    occurs_at: datetime | None
    minutes_due_on: date | None
    has_press_conference: bool | None
    source_url: str                   # the calendar page that lists it
    decision_url: str | None = None   # the statement or resolution the calendar links (B5, B6 read it)
    meeting_no: int | None = None     # MPC only
    from_schedule: bool = False       # listed as upcoming (no decision document linked yet)

    @property
    def slug(self) -> str:
        return f"{BANKS[self.bank]['prefix']}-{self.decision_date.isoformat()}"

    @property
    def title(self) -> str:
        name = BANKS[self.bank]["name"]
        if self.status == "cancelled":
            return f"{name} meeting (cancelled)"
        if self.kind == "special":
            return f"{name} special meeting"
        return f"{name} {'meeting' if self.kind == 'scheduled' else 'unscheduled meeting'}"

    @property
    def ends_on(self) -> date | None:
        return self.decision_date if self.decision_date != self.starts_on else None


def _occurs_at(bank: str, d: date, release: time | None) -> datetime:
    b = BANKS[bank]
    return datetime.combine(d, release or b["release"], tzinfo=b["tz"])


def _minutes_due(bank: str, d: date, kind: str, status: str, rate: bool) -> date | None:
    if status == "cancelled" or kind == "special":
        return None
    if bank == "fed" and kind != "scheduled":
        return None                   # the three-week rule is for regularly scheduled meetings
    return d + BANKS[bank]["minutes_after"]


def baseline_cut(decision_dates: list[date]) -> date:
    """The baseline: the last meeting before FIRST_DAY. Meetings before it are left out."""
    before = [d for d in decision_dates if d < FIRST_DAY]
    if not before:
        raise CalendarError(f"no meeting before {FIRST_DAY}: the 2018 baseline is missing")
    return max(before)


def fed_meetings(rows: list[fed_calendar.Row], curated: CuratedConfig, *, full: bool) -> tuple[list[Meeting], list]:
    """FOMC meetings from the calendar rows; notation votes are returned separately (they aren't meetings).
    `full`: the rows include the historical pages, so the baseline is cut and checked."""
    held_on = {c.held: c for (b, _), c in curated.rows.items() if b == "fed" and c.held}
    out: dict[date, Meeting] = {}
    votes = []
    for r in rows:
        if r.label == "notation vote":
            votes.append(r)
            continue
        cur = held_on.get(r.last) or curated.rows.get(("fed", r.last))
        decision = cur.decision_date if cur else r.last
        if r.label == "unscheduled" and cur is None:
            raise CalendarError(f"the unscheduled FOMC meeting {r.text!r} has no meeting_times.yaml row "
                                "(its release time and kind are curated: Spec 01 D8)")
        if cur and cur.kind == "unscheduled" and r.label != "unscheduled":
            raise CalendarError(f"meeting_times.yaml marks fed.{decision} unscheduled, but the calendar lists "
                                f"{r.text!r} as a regular meeting")
        status = "cancelled" if r.label == "cancelled" else ("held" if cur and not cur.is_rate_decision else "scheduled")
        kind = cur.kind if cur else "scheduled"
        rate = (cur.is_rate_decision if cur else True) and status != "cancelled"
        m = Meeting(
            bank="fed", decision_date=decision, starts_on=r.first, kind=kind, is_rate_decision=rate, status=status,
            occurs_at=_occurs_at("fed", decision, cur.release if cur else None) if rate else None,
            minutes_due_on=_minutes_due("fed", decision, kind, status, rate),
            has_press_conference=True if r.presconf_url else None,
            source_url=r.page_url, decision_url=r.statement_url if rate else None,
            from_schedule=status == "scheduled" and r.statement_url is None)
        if decision in out and out[decision] != m:
            raise CalendarError(f"two calendar entries for the FOMC decision of {decision}")
        out[decision] = m
    meetings = sorted(out.values(), key=lambda m: m.decision_date)
    if full:
        cut = baseline_cut([m.decision_date for m in meetings if m.is_rate_decision])
        meetings = [m for m in meetings if m.decision_date >= cut]
    else:
        meetings = [m for m in meetings if m.decision_date >= FIRST_DAY]
    return meetings, votes


def rbi_meetings(cal: rbi_calendar.Calendar, curated: CuratedConfig, today: date, *, full: bool,
                 next_lines: list[tuple[date, date]] = ()) -> list[Meeting]:
    """MPC meetings: every resolution the pages list, the curated meetings without one (MPC no. 39), and the upcoming
    meetings of the schedule and of stored resolutions' "next meeting" lines."""
    url = rbi_calendar.ANNUAL_POLICY
    out: dict[date, Meeting] = {}
    for r in cal.resolutions:
        d = r.decision_date
        cur = curated.rows.get(("rbi", d))
        first = (r.days[0] if r.days else None) or cal.minutes.get(d) or \
            next((s.days[0] for s in cal.schedule if s.days[1] == d), None) or (cur.held if cur else None)
        if first is None:
            raise CalendarError(f"the MPC meeting decided on {d} has no meeting days (resolution title, minutes "
                                "title or schedule)")
        kind = cur.kind if cur else "scheduled"
        out[d] = Meeting("rbi", d, first, kind, True, "scheduled", _occurs_at("rbi", d, cur.release if cur else None),
                         _minutes_due("rbi", d, kind, "scheduled", True), True if r.has_press_conference else None,
                         url, r.url)
    for (bank, d), cur in curated.rows.items():
        if bank != "rbi" or d in out:
            continue
        if cur.is_rate_decision:
            if full or d >= today - timedelta(days=60):
                raise CalendarError(f"meeting_times.yaml lists rbi.{d}, but no resolution of that date is on the "
                                    "Annual Policy pages")
            continue
        if not full and d < today - timedelta(days=60):
            continue                  # a past curated meeting outside the pages read this run
        out[d] = Meeting("rbi", d, cur.held or d, cur.kind, False, "held" if d < today else "scheduled",
                         _occurs_at("rbi", d, cur.release) if cur.release else None,
                         _minutes_due("rbi", d, cur.kind, "held", False), None, url, None,
                         meeting_no=cur.meeting_no)
    upcoming = [(s.days, "schedule") for s in cal.schedule] + [(days, "resolution") for days in next_lines]
    for (first, last), _ in upcoming:
        if last < today or last in out:
            continue
        cur = curated.rows.get(("rbi", last))
        kind = cur.kind if cur else "scheduled"
        out[last] = Meeting("rbi", last, first, kind, True, "scheduled",
                            _occurs_at("rbi", last, cur.release if cur else None),
                            _minutes_due("rbi", last, kind, "scheduled", True), None, url, None, from_schedule=True)
    meetings = sorted(out.values(), key=lambda m: m.decision_date)
    if full:
        cut = baseline_cut([m.decision_date for m in meetings if m.is_rate_decision])
        meetings = [m for m in meetings if m.decision_date >= cut]
    return meetings


def number_rbi(meetings: list[Meeting], stored: dict[date, int | None], curated: CuratedConfig) -> list[Meeting]:
    """MPC numbers: consecutive from the curated anchor across every meeting known (stored and parsed). Without the
    anchor in view (a first run that read only this year's page), meetings keep the number already stored, if any."""
    anchor_date, anchor_no = curated.rbi_anchor
    dates = sorted({m.decision_date for m in meetings} | set(stored))
    if anchor_date not in dates:
        return [replace(m, meeting_no=stored.get(m.decision_date)) for m in meetings]
    i0 = dates.index(anchor_date)
    numbers = {d: anchor_no + i - i0 for i, d in enumerate(dates) if i >= i0}
    return [replace(m, meeting_no=numbers.get(m.decision_date)) for m in meetings]
