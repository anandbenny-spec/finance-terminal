"""policy.rate_cycles: the cycle rule on Spec 02A §11's decision list, and the task through the runner against the test
database as job_writer (job 2A-23; Spec 02A §5.10). Each database test builds its own banks (entities under a fresh
prefix) and meetings, so the fixture's rows and other tests are untouched."""
import itertools
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType

import psycopg
import pytest
from conftest import dsn

from ft.jobs.runner import run
from ft.jobs.tasks.rate_cycles import Decision, RateCycles, derive_cycles, slug_of

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
KEY = "test.rate_cycles"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
_n = itertools.count(1)
D = date

# Every held rate decision of both banks from 1 Jan 2019 to 7 Oct 2026, as (decision date, change in basis points).
# Spec 02A §11 lists the moves; the holds between them are the meetings' dates (FOMC and MPC calendars), typed here.
FED = [
    (D(2019, 1, 30), 0), (D(2019, 3, 20), 0), (D(2019, 5, 1), 0), (D(2019, 6, 19), 0), (D(2019, 7, 31), -25),
    (D(2019, 9, 18), -25), (D(2019, 10, 30), -25), (D(2019, 12, 11), 0), (D(2020, 1, 29), 0),
    (D(2020, 3, 3), -50), (D(2020, 3, 15), -100), (D(2020, 4, 29), 0), (D(2020, 6, 10), 0), (D(2020, 7, 29), 0),
    (D(2020, 9, 16), 0), (D(2020, 11, 5), 0), (D(2020, 12, 16), 0),
    (D(2021, 1, 27), 0), (D(2021, 3, 17), 0), (D(2021, 4, 28), 0), (D(2021, 6, 16), 0), (D(2021, 7, 28), 0),
    (D(2021, 9, 22), 0), (D(2021, 11, 3), 0), (D(2021, 12, 15), 0), (D(2022, 1, 26), 0),
    (D(2022, 3, 16), 25), (D(2022, 5, 4), 50), (D(2022, 6, 15), 75), (D(2022, 7, 27), 75), (D(2022, 9, 21), 75),
    (D(2022, 11, 2), 75), (D(2022, 12, 14), 50), (D(2023, 2, 1), 25), (D(2023, 3, 22), 25), (D(2023, 5, 3), 25),
    (D(2023, 6, 14), 0), (D(2023, 7, 26), 25), (D(2023, 9, 20), 0), (D(2023, 11, 1), 0), (D(2023, 12, 13), 0),
    (D(2024, 1, 31), 0), (D(2024, 3, 20), 0), (D(2024, 5, 1), 0), (D(2024, 6, 12), 0), (D(2024, 7, 31), 0),
    (D(2024, 9, 18), -50), (D(2024, 11, 7), -25), (D(2024, 12, 18), -25), (D(2025, 1, 29), 0), (D(2025, 3, 19), 0),
    (D(2025, 5, 7), 0), (D(2025, 6, 18), 0), (D(2025, 7, 30), 0), (D(2025, 9, 17), -25), (D(2025, 10, 29), -25),
    (D(2025, 12, 10), -25), (D(2026, 1, 28), 0), (D(2026, 3, 18), 0), (D(2026, 4, 29), 0), (D(2026, 6, 17), 0),
    (D(2026, 7, 29), 0), (D(2026, 9, 16), 25),
]
RBI = [
    (D(2019, 2, 7), -25), (D(2019, 4, 4), -25), (D(2019, 6, 6), -25), (D(2019, 8, 7), -35), (D(2019, 10, 4), -25),
    (D(2019, 12, 5), 0), (D(2020, 2, 6), 0), (D(2020, 3, 27), -75), (D(2020, 5, 22), -40), (D(2020, 8, 6), 0),
    (D(2020, 10, 9), 0), (D(2020, 12, 4), 0), (D(2021, 2, 5), 0), (D(2021, 4, 7), 0), (D(2021, 6, 4), 0),
    (D(2021, 8, 6), 0), (D(2021, 10, 8), 0), (D(2021, 12, 8), 0), (D(2022, 2, 10), 0), (D(2022, 4, 8), 0),
    (D(2022, 5, 4), 40), (D(2022, 6, 8), 50), (D(2022, 8, 5), 50), (D(2022, 9, 30), 50), (D(2022, 12, 7), 35),
    (D(2023, 2, 8), 25), (D(2023, 4, 6), 0), (D(2023, 6, 8), 0), (D(2023, 8, 10), 0), (D(2023, 10, 6), 0),
    (D(2023, 12, 8), 0), (D(2024, 2, 8), 0), (D(2024, 4, 5), 0), (D(2024, 6, 7), 0), (D(2024, 8, 8), 0),
    (D(2024, 10, 9), 0), (D(2024, 12, 6), 0), (D(2025, 2, 7), -25), (D(2025, 4, 9), -25), (D(2025, 6, 6), -50),
    (D(2025, 8, 6), 0), (D(2025, 10, 1), 0), (D(2025, 12, 5), -25), (D(2026, 2, 6), 0), (D(2026, 4, 8), 0),
    (D(2026, 6, 5), 0), (D(2026, 8, 6), 0), (D(2026, 10, 7), 25),
]
# Spec 02A §5.10, "Applied to dev's rates (28 Oct)": (direction, first move's month, moves, net bps)
EXPECTED = {
    "fed": [("easing", "2019-07", 3, -75), ("easing", "2020-03", 2, -150), ("tightening", "2022-03", 11, 525),
            ("easing", "2024-09", 3, -100), ("easing", "2025-09", 3, -75)],
    "rbi": [("easing", "2019-02", 5, -135), ("easing", "2020-03", 2, -115), ("tightening", "2022-05", 6, 250),
            ("easing", "2025-02", 3, -100)],
}


def decisions(rows, start="5.00") -> list[Decision]:
    out, rate = [], Decimal(start)
    for i, (on, bps) in enumerate(rows, 1):
        rate += Decimal(bps) / 100
        out.append(Decision(i, on, bps, rate))
    return out


def summary(cycles):
    return [(c.direction, f"{c.first.on:%Y-%m}", c.moves, c.total_change_bps) for c in cycles]


# ---------------------------------------------------------------------------------------------- the rule
def test_the_rule_gives_exactly_spec_02a_5_10s_cycles():
    for bank, rows in (("fed", FED), ("rbi", RBI)):
        assert summary(derive_cycles(decisions(rows))) == EXPECTED[bank]


def test_the_lone_moves_are_no_cycles_and_none_is_ongoing_at_the_cutoff():
    cycles = {b: derive_cycles(decisions(r)) for b, r in (("fed", FED), ("rbi", RBI))}
    lone = {(b, d.on) for b, rows in (("fed", FED), ("rbi", RBI)) for d in decisions(rows) if d.change_bps}
    in_cycles = {(b, d.on) for b, cs in cycles.items() for c in cs
                 for d in decisions(FED if b == "fed" else RBI) if c.first.on <= d.on <= c.last.on and d.change_bps}
    # RBI 5 Dec 2025, Fed 16 Sep 2026 and RBI 7 Oct 2026 are the only moves outside a cycle
    assert sorted(lone - in_cycles) == [("fed", D(2026, 9, 16)), ("rbi", D(2025, 12, 5)), ("rbi", D(2026, 10, 7))]
    assert not any(c.ongoing for cs in cycles.values() for c in cs)


def test_the_june_2023_skip_does_not_end_the_fed_tightening_but_two_holds_do():
    fed = derive_cycles(decisions(FED))[2]
    assert (fed.first.on, fed.last.on, fed.moves) == (D(2022, 3, 16), D(2023, 7, 26), 11)
    # the same run with the July 2023 move replaced by a second hold: the cycle ends in May 2023 with 10 moves
    changed = [(on, 0 if on == D(2023, 7, 26) else b) for on, b in FED]
    assert derive_cycles(decisions(changed))[2].moves == 10


def test_an_ongoing_cycle_after_one_hold_and_complete_after_a_second():
    base = [(D(2025, 1, 1), 0), (D(2025, 2, 1), -25), (D(2025, 3, 1), -25)]
    one = derive_cycles(decisions(base + [(D(2025, 4, 1), 0)]))
    assert [(c.moves, c.ongoing) for c in one] == [(2, True)]
    two = derive_cycles(decisions(base + [(D(2025, 4, 1), 0), (D(2025, 5, 1), 0)]))
    assert [(c.moves, c.ongoing) for c in two] == [(2, False)]
    back = derive_cycles(decisions(base + [(D(2025, 4, 1), 0), (D(2025, 5, 1), 25), (D(2025, 6, 1), 25)]))
    assert [(c.direction, c.moves, c.ongoing) for c in back] == [("easing", 2, False), ("tightening", 2, True)]
    assert derive_cycles(decisions([(D(2025, 1, 1), -25), (D(2025, 2, 1), 0)])) == []      # one move: no cycle
    assert derive_cycles([]) == []


def test_a_move_the_other_way_ends_the_cycle_and_starts_the_next_without_a_hold():
    rows = [(D(2025, 1, 1), -25), (D(2025, 2, 1), -25), (D(2025, 3, 1), 25), (D(2025, 4, 1), 25), (D(2025, 5, 1), 0)]
    assert summary(derive_cycles(decisions(rows))) == [("easing", "2025-01", 2, -50), ("tightening", "2025-03", 2, 50)]


# ---------------------------------------------------------------------------------------------- the task
def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        cur = c.execute(sql, a)
        return cur.fetchall() if cur.description else []


class World:
    """Two test banks (a Fed-like and an RBI-like), their meetings and rate settings, and the task pointed at them."""

    def __init__(self):
        p = f"rc{next(_n)}x{datetime.now().strftime('%H%M%S%f')}"
        self.p, self.fed, self.rbi = p, f"{p}-fed", f"{p}-rbi"
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'rate cycles test', '{daily}', '20 hours', '3 days', false)
                         on conflict (key) do nothing""", (KEY,))
            for slug, mkt, cur in ((self.fed, "US", "USD"), (self.rbi, "IN", "INR")):
                c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                             values ('central_bank', %s, %s, %s, %s, true)""", (slug, f"Bank {slug}", mkt, cur))
            self.doc = c.execute(
                """insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
                   select (select min(key) from core.doc_type where module = 'policy'), e.id, 'rc doc', s.id, %s,
                          'https://example.test/rc'
                     from core.entity e, core.source s where e.slug = %s and s.key = 'fed' returning id""",
                (f"{p}-doc", self.fed)).fetchone()[0]

    def task(self) -> RateCycles:
        class T(RateCycles):
            key = KEY
            instrument_by_bank = MappingProxyType({self.fed: "fed_target_upper", self.rbi: "repo"})
        t = T()
        t.workflows = ("daily",)
        return t

    def decide(self, bank, rows, start="5.00", *, decision=True, status="held", instrument=None):
        """Meetings for (date, change in bps) rows; the policy rate runs on from `start`."""
        rate = Decimal(start)
        with psycopg.connect(ADMIN, autocommit=True) as c:
            for on, bps in rows:
                rate += Decimal(bps) / 100
                e = c.execute(
                    """insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id,
                                               is_published)
                       select 'policy_meeting', e.id, %s, %s, %s, %s::date + time '12:00', %s, s.id, true
                         from core.entity e, core.source s where e.slug = %s and s.key = 'fed' returning id""",
                    (f"{self.p}-{bank[-3:]}-{on}", f"{bank} {on}", on, on, status, bank)).fetchone()[0]
                c.execute("insert into policy.meeting (event_id, kind, is_rate_decision) values (%s, 'scheduled', %s)",
                          (e, decision))
                inst = instrument if instrument is not None else ("fed_target_upper" if bank == self.fed else "repo")
                if inst:
                    c.execute("""insert into policy.rate_setting (meeting_event_id, instrument, value_pct, change_bps,
                                                                  document_id, char_start, char_end)
                                 values (%s, %s, %s, %s, %s, 0, 10)""", (e, inst, rate, bps, self.doc))

    def cycles(self, bank=None):
        return q("""select b.slug, c.direction, to_char(ev.starts_on, 'YYYY-MM'), c.moves, c.total_change_bps, ev.status,
                           ev.starts_on, ev.ends_on, ev.is_published, ev.slug, ev.id
                      from policy.rate_cycle c join core.event ev on ev.id = c.event_id
                      join core.entity b on b.id = ev.entity_id where b.slug like %s order by b.slug, ev.starts_on""",
                 (bank or self.p + "-%"))


def last_run():
    return q("select status, rows_written, checks from ops.task_run where task_key = %s order by id desc limit 1", KEY)[0]


def go(task, now=NOW):
    return run(JOB, "daily", [task], force=True, clock=lambda: now)


@pytest.mark.db
def test_the_task_derives_spec_5_10s_cycles_as_published_events_and_a_rerun_writes_nothing():
    w = World()
    w.decide(w.fed, FED)
    w.decide(w.rbi, RBI)
    # the RBI's special meeting no. 39 held no decision, and a 2018 baseline decision is no decision of ours
    w.decide(w.rbi, [(D(2022, 11, 3), 0)], decision=False, start="5.90")
    w.decide(w.fed, [(D(2018, 12, 19), -25), (D(2018, 9, 26), -25)], start="2.00")
    w.decide(w.fed, [(D(2026, 11, 5), 25)], status="scheduled")                   # not held yet: no decision
    assert go(w.task()) == 0
    status, written, checks = last_run()
    assert status == "success" and written == 9
    got = {b: [(d, m, n, t) for bb, d, m, n, t, *_ in w.cycles() if bb == b] for b in (w.fed, w.rbi)}
    assert got[w.fed] == EXPECTED["fed"] and got[w.rbi] == EXPECTED["rbi"]
    rows = w.cycles(w.fed)
    first = rows[0]
    assert first[5:9] == ("complete", D(2019, 7, 31), D(2019, 10, 30), True)
    assert first[9] == f"{w.fed}-easing-2019-07"
    assert [c["name"] for c in checks] == ["decisions", "totals", "stale", "cycles"]
    assert all(c["passed"] for c in checks)
    # visitors read the events like the meetings: published
    with psycopg.connect(ADMIN) as c:
        c.execute("set role anon")
        n = c.execute("select count(*) from core.event where event_type = 'rate_cycle' and slug like %s",
                      (w.p + "-%",)).fetchone()[0]
    assert n == 9
    # a re-run re-derives and writes no row
    assert go(w.task()) == 0 and last_run()[1] == 0
    assert w.cycles(w.fed) == rows


@pytest.mark.db
def test_an_ongoing_cycle_extends_then_completes_in_place():
    w = World()
    w.decide(w.fed, [(D(2025, 1, 1), 0), (D(2025, 2, 1), -25), (D(2025, 3, 1), -25), (D(2025, 4, 1), 0)])
    go(w.task())
    (row,) = w.cycles(w.fed)
    assert (row[3], row[4], row[5], row[7]) == (2, -50, "ongoing", D(2025, 3, 1))
    event_id = row[10]
    # one more move extends the same cycle (same event, same slug)
    w.decide(w.fed, [(D(2025, 5, 1), -25)], start="4.50")
    go(w.task())
    (row,) = w.cycles(w.fed)
    assert (row[3], row[4], row[5], row[7], row[10]) == (3, -75, "ongoing", D(2025, 5, 1), event_id)
    assert last_run()[1] == 1
    # two holds complete it
    w.decide(w.fed, [(D(2025, 6, 1), 0), (D(2025, 7, 1), 0)], start="4.25")
    go(w.task())
    (row,) = w.cycles(w.fed)
    assert (row[3], row[5], row[10]) == (3, "complete", event_id)


@pytest.mark.db
def test_a_decision_without_its_rate_row_blocks_the_load():
    w = World()
    w.decide(w.fed, [(D(2025, 2, 1), -25), (D(2025, 3, 1), -25), (D(2025, 4, 1), 0), (D(2025, 5, 1), 0)])
    w.decide(w.fed, [(D(2025, 6, 1), 0)], start="4.50", instrument="")     # a held decision with no policy-rate row
    go(w.task())
    status, written, checks = last_run()
    assert status == "checks_failed" and written == 0
    assert [c["name"] for c in checks if not c["passed"]] == ["decisions"]
    assert w.cycles() == []


@pytest.mark.db
def test_rate_levels_that_disagree_with_the_summed_changes_block_the_load():
    w = World()
    w.decide(w.fed, [(D(2025, 2, 1), -25), (D(2025, 3, 1), -25), (D(2025, 4, 1), 0), (D(2025, 5, 1), 0)])
    q("""update policy.rate_setting set value_pct = value_pct + 0.25
          where meeting_event_id = (select id from core.event where slug = %s)""", f"{w.p}-fed-{D(2025, 3, 1)}")
    go(w.task())
    status, written, checks = last_run()
    assert status == "checks_failed" and written == 0
    assert [c["name"] for c in checks if not c["passed"]] == ["totals"]
    assert w.cycles() == []


@pytest.mark.db
def test_a_cycle_the_rule_no_longer_derives_is_unpublished_and_named_not_deleted():
    w = World()
    w.decide(w.fed, [(D(2025, 2, 1), -25), (D(2025, 3, 1), -25), (D(2025, 4, 1), 0), (D(2025, 5, 1), 0)])
    go(w.task())
    (row,) = w.cycles(w.fed)
    assert row[8] is True
    # a revised first decision (a hold) moves the first move to March: a lone move now, so no cycle at all
    q("""update policy.rate_setting set change_bps = 0, value_pct = 4.75
          where meeting_event_id = (select id from core.event where slug = %s)""", f"{w.p}-fed-{D(2025, 2, 1)}")
    q("""update policy.rate_setting set value_pct = 4.50
          where meeting_event_id = (select id from core.event where slug = %s)""", f"{w.p}-fed-{D(2025, 3, 1)}")
    go(w.task())
    (row,) = w.cycles(w.fed)
    assert row[8] is False and row[9] == f"{w.fed}-easing-2025-02"
    status, written, checks = last_run()
    assert status == "success" and written == 1
    assert [c["name"] for c in checks if c["name"] == "stale" and not c["passed"]] == ["stale"]
    # restoring the decision publishes it again, same event
    q("""update policy.rate_setting set change_bps = -25, value_pct = 4.75
          where meeting_event_id = (select id from core.event where slug = %s)""", f"{w.p}-fed-{D(2025, 2, 1)}")
    q("""update policy.rate_setting set value_pct = 4.50
          where meeting_event_id = (select id from core.event where slug = %s)""", f"{w.p}-fed-{D(2025, 3, 1)}")
    go(w.task())
    (again,) = w.cycles(w.fed)
    assert again[8] is True and again[10] == row[10]


def test_the_slug_names_the_bank_direction_and_month_of_the_first_move():
    (c,) = derive_cycles(decisions([(D(2024, 9, 18), -50), (D(2024, 11, 7), -25)]))
    assert slug_of("fed", c) == "fed-easing-2024-09"
