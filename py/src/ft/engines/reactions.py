"""The reactions engine (Spec 02A §5.10; Plan 02 §2.3, §7.2; job 2A-22): how a value moved around an event.

Pure computation over loaded values; the engine writes no table (its callers do). Three parts:

- **Subject**: whose value is read. A stored series (``core.observation``, each value fixed at the series' fixing time
  in its zone) or a company (``equity.price_adjusted``'s adjusted close, fixed at the session's close from
  ``ft.calendars.close_time('xnse', date)``: 15:30 India, or a special session's own close).
- **Anchor**: the event's start and end instants. A decision has both at its ``occurs_at``; a rate cycle runs from its first
  decision's to its last's and is ``ongoing`` until it ends.
- **Window**: ``pre`` is always the last value fixed before the anchor's start; ``post`` is the *k*-th value fixed after its
  end (``d1`` k = 1, ``d5`` k = 5), or, for ``span``, the first value after the end, or the latest value while the cycle
  is ongoing.

``pre`` must be fixed within ``search_days`` calendar days before the start's local date and ``post`` within as many
days after the end's (module 1's 31); an ongoing ``span`` has no such limit. A value is "fixed before" only when its
instant is strictly earlier than the start and "after" only when strictly later, so a value fixed at the release
instant is neither.

A reaction is ``None`` with a reason when it can't be computed: ``no_pre``, ``no_post``, or ``held`` (a located value is null: an
adjusted close behind a held corporate-action factor; counted by the caller, never guessed). Yields (unit ``percent``) change
in basis points, (post − pre) × 100; everything else in percent, (post ÷ pre − 1) × 100, rounded to 4 places.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ft import calendars

PCT_PLACES = Decimal("0.0001")
DEFAULT_SEARCH_DAYS = 31
NSE_EXCHANGE = "xnse"


def change_of(unit: str, pre: Decimal, post: Decimal) -> tuple[Decimal, str]:
    """Yields in basis points ((post − pre) × 100); everything else in percent ((post ÷ pre − 1) × 100, 4 places)."""
    if unit == "percent":
        return (post - pre) * 100, "bps"
    return ((post / pre - 1) * 100).quantize(PCT_PLACES), "pct"


@dataclass(frozen=True)
class Anchor:
    event_id: int
    starts_at: datetime
    ends_at: datetime
    ongoing: bool = False

    @classmethod
    def decision(cls, event_id: int, occurs_at: datetime) -> Anchor:
        return cls(event_id, occurs_at, occurs_at)


@dataclass(frozen=True)
class Window:
    key: str                   # ^[a-z0-9_]+$: it becomes core.event_reaction.window_key
    after: int = 1             # post = the `after`-th value fixed after the anchor's end
    span: bool = False         # post = the first value after the end (the latest while the anchor is ongoing)

    def __post_init__(self):
        if self.after < 1:
            raise ValueError("a window looks at least one value ahead")


D1 = Window("d1", 1)
D5 = Window("d5", 5)
SPAN = Window("span", 1, span=True)


class Subject:
    """A subject's values by date, each with the instant it was fixed.

    ``values`` is (date, value) ascending by date; a value of ``None`` is a date with no usable value (a held adjusted
    close): it still occupies its place, so the window finds it and reports ``held``. ``fixing`` maps a date to the
    instant its value was fixed; a date it raises ``ValueError`` for (no session) is dropped and counted in ``dropped``.
    """

    def __init__(self, key: str, unit: str, values: Sequence[tuple[date, Decimal | None]],
                 fixing: Callable[[date], datetime], *, series_id: int | None = None,
                 entity_id: int | None = None, value_basis: str = "observed"):
        if (series_id is None) == (entity_id is None):
            raise ValueError("a subject is a series or a company, exactly one")
        self.key, self.unit, self.series_id, self.entity_id = key, unit, series_id, entity_id
        self.value_basis = value_basis
        dates, vals, instants, dropped = [], [], [], 0
        for d, v in sorted(values, key=lambda x: x[0]):
            try:
                at = fixing(d)
            except calendars.CalendarYearError:
                raise
            except ValueError:
                dropped += 1
                continue
            dates.append(d)
            vals.append(v)
            instants.append(at)
        self.dates, self.vals, self.instants, self.dropped = dates, vals, instants, dropped
        tz = instants[0].tzinfo if instants else None
        self.zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo("UTC")      # the zone local dates are read in

    def __len__(self) -> int:
        return len(self.dates)


@dataclass(frozen=True)
class Reaction:
    event_id: int
    subject_key: str
    series_id: int | None
    entity_id: int | None
    window_key: str
    pre_date: date
    post_date: date
    pre_value: Decimal
    post_value: Decimal
    change: Decimal
    change_unit: str
    value_basis: str


@dataclass(frozen=True)
class Outcome:
    """A reaction, or the reason there is none."""
    reaction: Reaction | None
    missing: str | None = None          # no_pre | no_post | held


def _local_date(at: datetime, zone: ZoneInfo) -> date:
    return at.astimezone(zone).date()


def react(subject: Subject, anchor: Anchor, window: Window, *, search_days: int = DEFAULT_SEARCH_DAYS) -> Outcome:
    """The subject's move around the anchor over the window."""
    inst = subject.instants
    if not inst:
        return Outcome(None, "no_pre")
    zone = subject.zone
    limit = timedelta(days=search_days)

    # pre: the last value fixed strictly before the start, within search_days of the start's local date
    i = bisect_left(inst, anchor.starts_at) - 1
    if i < 0 or subject.dates[i] < _local_date(anchor.starts_at, zone) - limit:
        return Outcome(None, "no_pre")

    # post: the `after`-th value fixed strictly after the end
    first_after = bisect_right(inst, anchor.ends_at)
    bound = _local_date(anchor.ends_at, zone) + limit
    if window.span and anchor.ongoing:
        j = len(inst) - 1                           # the latest value (no search limit while a cycle is ongoing)
        if j < first_after:
            return Outcome(None, "no_post")
    else:
        j = first_after + (1 if window.span else window.after) - 1
        if j >= len(inst) or subject.dates[j] > bound:
            return Outcome(None, "no_post")

    pre, post = subject.vals[i], subject.vals[j]
    if pre is None or post is None:
        return Outcome(None, "held")
    change, change_unit = change_of(subject.unit, pre, post)
    return Outcome(Reaction(anchor.event_id, subject.key, subject.series_id, subject.entity_id, window.key,
                            subject.dates[i], subject.dates[j], pre, post, change, change_unit, subject.value_basis))


# ---------------------------------------------------------------------------------------------------- loaders
def series_fixing(fixing_time: time, fixing_tz: str) -> Callable[[date], datetime]:
    zone = ZoneInfo(fixing_tz)
    return lambda d: datetime.combine(d, fixing_time, tzinfo=zone)


def company_fixing(exchange: str = NSE_EXCHANGE) -> Callable[[date], datetime]:
    """The session's close, from the trading calendar (a special session keeps its own time)."""
    return lambda d: calendars.close_time(exchange, d)


def load_series_subject(tx, series_id: int, lo: date, hi: date) -> Subject:
    """A stored series' observations between ``lo`` and ``hi`` (dates), read as the series' fixing time and zone say."""
    key, unit, fixing_time, fixing_tz = tx.execute(
        "select key, unit, fixing_time, fixing_tz from core.series where id = %s", (series_id,)).fetchone()
    if fixing_time is None or fixing_tz is None:
        raise ValueError(f"{key} has no fixing time and zone")
    rows = tx.execute("""select obs_date, value from core.observation
                          where series_id = %s and obs_date between %s and %s order by obs_date""",
                      (series_id, lo, hi)).fetchall()
    return Subject(key, unit, rows, series_fixing(fixing_time, fixing_tz), series_id=series_id)


def load_company_subject(tx, entity_id: int, lo: date, hi: date) -> Subject:
    """A company's adjusted close (``equity.price_adjusted``) between ``lo`` and ``hi``, fixed at the NSE close.

    A null ``adj_close`` (an earlier corporate action is held) stays in the subject as a null so the window reports
    ``held`` rather than reaching past it.
    """
    slug = tx.execute("select slug from core.entity where id = %s", (entity_id,)).fetchone()[0]
    rows = tx.execute("""select trade_date, adj_close from equity.price_adjusted
                          where entity_id = %s and trade_date between %s and %s order by trade_date""",
                      (entity_id, lo, hi)).fetchall()
    return Subject(slug, "price", rows, company_fixing(), entity_id=entity_id, value_basis="adjusted_close")
