"""A tally statement's dissenters (#159; Anand, 29 Oct 2026): the rows policy.fed_documents writes from the statement,
and which stored statements still need them. No database: the rows are built by pure functions
(`FedDocuments._dissent_rows`, `_dissents_due`); the database half is in test_fed_documents_task.py.

The saved pages (data/fixtures/fed, the private fixtures repo) give the real 17 Jun (12-0) and 29 Jul 2026 (9-3, three
dissenters) statements; the other cases are synthetic statements in the real template."""
import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from policy_docs import fed_page, fed_tally

from ft.jobs.tasks.fed_documents import CATCH_UP, FedDocuments, Meeting, VoteRow
from ft.policy import fed_statement, members
from ft.policy.fed_statement import Range

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "fed"
SAVED = pytest.mark.skipif(not (FIXTURES / "monetary20260729a.htm").exists(),
                           reason="the saved pages aren't checked out (data/fixtures: the private fixtures repo)")
NY = ZoneInfo("America/New_York")
URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary{}a.htm"
HOLD = "decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent"
RAISE = "decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent"
PREVIOUS = Range(Decimal("3.5"), Decimal("3.75"))
DOC = 4242
ROSTER = members.load()
IDS = {slug: i for i, slug in enumerate(ROSTER.members, start=1)}


def saved(name: str) -> fed_statement.Statement:
    return fed_statement.parse((FIXTURES / name).read_bytes(), URL.format(name[8:16]))


def synthetic(tally: str, decision: str, against: str | None) -> fed_statement.Statement:
    page = fed_page(fed_tally(tally, decision, against), day="September 16, 2026")
    return fed_statement.parse(page.encode("utf-8"), URL.format("20260916"))


def rows(st, previous=PREVIOUS, decided=0):
    return FedDocuments._dissent_rows(st, previous, decided, ROSTER, IDS, DOC)


# ---------------------------------------------------------------------------------------------- the rows
@SAVED
def test_the_29_jul_2026_statement_gives_its_three_dissenters_rows():
    st = saved("monetary20260729a.htm")
    want, who, unknown, problems = rows(st)
    clause = "preferred to raise the target range for the federal funds rate by 1/4 percentage point at this meeting"
    span = st.votes.against_sentence
    assert (unknown, problems) == ([], [])
    assert want == {IDS[s]: VoteRow(25, True, clause, DOC, span)
                    for s in ("beth-m-hammack", "neel-kashkari", "lorie-k-logan")}
    assert sorted(name for _, name in who) == ["Beth M. Hammack", "Lorie K. Logan", "Neel Kashkari"]
    # The 'Voting against' sentence is the span, as `_tally_votes` writes it from the minutes.
    assert st.text.text[slice(*span)].startswith("Voting against the monetary policy action were Beth M. Hammack")


@SAVED
@pytest.mark.parametrize("name", ["monetary20260617a.htm", "monetary20260916a.htm"])
def test_a_unanimous_tally_statement_gives_no_rows(name):
    st = saved(name)
    assert st.votes.tally == (12, 0)
    assert rows(st) == ({}, [], [], [])


def test_each_dissenter_keeps_the_change_the_clause_states():
    against = ("Voting against the monetary policy action were Beth M. Hammack and Neel Kashkari, who preferred to raise "
               "the target range for the federal funds rate by 1/4 percentage point at this meeting; and Lisa D. Cook, "
               "who preferred to lower the target range for the federal funds rate by 1/4 percentage point at this "
               "meeting.")
    st = synthetic("9 – 3", HOLD, against)
    want, who, unknown, problems = rows(st)
    assert (unknown, problems) == ([], [])
    assert {s: want[IDS[s]].change_bps for s in ("beth-m-hammack", "neel-kashkari", "lisa-d-cook")} == {
        "beth-m-hammack": 25, "neel-kashkari": 25, "lisa-d-cook": -25}
    assert all(r.dissent and r.document_id == DOC and r.span == st.votes.against_sentence for r in want.values())
    assert want[IDS["lisa-d-cook"]].text.startswith("preferred to lower the target range")      # the clause, verbatim
    assert len(who) == 3


def test_a_roster_member_is_matched_and_a_stranger_is_reported():
    against = ("Voting against the monetary policy action were Beth M. Hammack and Zed Q. Nobody, who preferred to "
               "raise the target range for the federal funds rate by 1/4 percentage point at this meeting.")
    st = synthetic("10 – 2", HOLD, against)
    want, who, unknown, problems = rows(st)
    assert list(want) == [IDS["beth-m-hammack"]] and want[IDS["beth-m-hammack"]].change_bps == 25
    assert [name for _, name in who] == ["Beth M. Hammack"]
    assert (unknown, problems) == (["Zed Q. Nobody"], [])


def test_a_dissenter_who_preferred_the_decision_is_a_problem():
    against = ("Voting against the monetary policy action was Beth M. Hammack, who preferred to raise the target range "
               "for the federal funds rate by 1/4 percentage point at this meeting.")
    st = synthetic("11 – 1", RAISE, against)
    _, _, unknown, problems = rows(st, decided=25)
    assert unknown == [] and problems == ["Beth M. Hammack dissented but preferred the decision (+25 bps)"]


def test_a_dissent_that_is_not_about_the_rate_stores_no_bps():
    against = ("Voting against the monetary policy action was Beth M. Hammack, who supported maintaining the target "
               "range but did not support the inclusion of an easing bias.")
    st = synthetic("11 – 1", HOLD, against)
    want, _, unknown, problems = rows(st)
    assert (unknown, problems) == ([], [])
    assert want[IDS["beth-m-hammack"]].change_bps is None and want[IDS["beth-m-hammack"]].dissent


# ---------------------------------------------------------------------------------------------- which statements are due
class Raw:
    """The fetcher's `read`: stored raw files by id. A request would be a different method, which this lacks."""

    def __init__(self, files: dict[int, bytes]):
        self.files, self.read_ids = files, []

    def read(self, raw_file_id: int) -> bytes:
        self.read_ids.append(raw_file_id)
        return self.files[raw_file_id]


def meeting(d: date, raw: int | None, *, votes: int = 0, stored: bool = True) -> Meeting:
    return Meeting(event_id=int(d.strftime("%y%m%d")), slug=f"fomc-{d}", decision_date=d,
                   occurs_at=datetime(d.year, d.month, d.day, 14, tzinfo=NY), status="held", is_published=True,
                   statement_id=raw if stored else None, statement_raw=raw if stored else None,
                   statement_url=URL.format(f"{d:%Y%m%d}"), baseline=False, votes=votes)


APR, JUN, JUL, SEP = date(2026, 4, 29), date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16)
NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


@SAVED
class TestDue:
    def setup_method(self):
        self.files = {1: (FIXTURES / "monetary20260429a.htm").read_bytes(),
                      2: (FIXTURES / "monetary20260617a.htm").read_bytes(),
                      3: (FIXTURES / "monetary20260729a.htm").read_bytes(),
                      4: (FIXTURES / "monetary20260916a.htm").read_bytes()}
        self.meetings = [meeting(APR, 1, votes=12), meeting(JUN, 2), meeting(JUL, 3), meeting(SEP, 4)]

    def due(self, meetings=None, now=NOW):
        raw = Raw(self.files)
        got = FedDocuments._dissents_due(raw, meetings or self.meetings, now, now.date())
        return got, raw.read_ids

    def test_a_stored_tally_statement_with_dissenters_and_no_rows_is_due_and_nothing_else(self):
        # 17 Jun is outside the 60 days; 16 Sep is 12-0 (nothing to write); 29 Apr has its rows (a named statement).
        got, read = self.due()
        assert got == {"fomc-2026-07-29"} and (JUL - JUN).days < 60
        assert 3 in read                                 # the stored text was read back: no request exists to make

    def test_the_window_is_the_daily_runs_60_days(self):
        assert CATCH_UP == timedelta(days=60)
        assert self.due(now=datetime(2026, 9, 27, 12, tzinfo=UTC))[0] == {"fomc-2026-07-29"}      # day 60
        assert self.due(now=datetime(2026, 9, 28, 12, tzinfo=UTC))[0] == set()                    # day 61

    def test_a_meeting_that_has_rows_is_not_due(self):
        self.meetings[2] = dataclasses.replace(self.meetings[2], votes=3)
        assert self.due()[0] == set()

    def test_a_meeting_before_its_release_is_not_due(self):
        assert self.due(now=datetime(2026, 7, 29, 17, tzinfo=UTC))[0] == set()      # 2:00 p.m. New York is 18:00 UTC

    def test_a_meeting_whose_previous_statement_is_not_stored_is_left_alone(self):
        self.meetings[1] = meeting(JUN, None, stored=False)
        assert self.due()[0] == set()

    def test_the_first_meeting_and_a_baseline_are_left_alone(self):
        assert self.due(self.meetings[2:])[0] == set()                               # i == 0: no previous meeting
        self.meetings[2] = dataclasses.replace(self.meetings[2], baseline=True)
        assert self.due()[0] == set()

    def test_a_statement_that_cannot_be_read_is_not_due_and_does_not_stop_the_run(self):
        self.files[3] = b"<html><body>not a statement</body></html>"
        assert self.due()[0] == set()
