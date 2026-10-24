"""MPC minutes (Prompt 01 B6 part 2; Spec 01 §2, "RBI votes (D4)"): the parser and its page checks on synthetic minutes in
the saved pages' shapes (policy_docs.mpc_minutes), then over the 49 saved minutes (Dec 2018 to Oct 2026): every one
passes, the roster matches each (D4's check), and the minutes agree with the resolutions they record."""
import csv
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from policy_docs import mpc_minutes, rbi_page

from ft.engines.document.chunk import chunk_text
from ft.policy import members, rbi_minutes
from ft.policy import rbi_resolution as rr

FIXTURES = Path(os.environ.get("FT_FIXTURES", Path(__file__).resolve().parents[2] / "data" / "fixtures"))
RBI = FIXTURES / "rbi"
URL = "https://rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=99001"
SIX = ["Dr. Ada Rao", "Dr. Ben Iyer", "Prof. Cara N. Das", "Dr. Dev K. Shah", "Shri Eli Paul", "Shri Fay Roy"]
TITLE = "Minutes of the Monetary Policy Committee Meeting, August 4 to 6, 2040"
COPY = [("On the basis of an assessment, the MPC decided to reduce the policy repo rate by 35 basis points from 6.00 "
         "per cent to 5.65 per cent with immediate effect."),
        "The MPC also decided to continue with the accommodative stance.",
        ("Four members (Dr. Ada Rao, Dr. Ben Iyer, Prof. Cara N. Das and Dr. Dev K. Shah) voted to reduce the policy "
         "repo rate by 35 basis points, while two members (Shri Eli Paul and Shri Fay Roy) voted to reduce the policy "
         "repo rate by 25 basis points."),
        "All members of the MPC unanimously voted to continue with the accommodative stance."]


def page(body: str, *, title: str = TITLE, day: str = "Aug 20, 2040") -> bytes:
    return rbi_page(body, title=title, day=day).encode()


def body(**kw) -> str:
    args = {"no": 7, "days": "August 4 to 6, 2040", "members": SIX, "resolution": COPY,
            "heading": "Voting on the Resolution to reduce the policy repo rate",
            "rows": [(n, "Yes", 35) for n in SIX[:4]] + [(n, "Yes", 25) for n in SIX[4:]], "heading_in_table": True}
    return mpc_minutes(**(args | kw))


def failed(mn, d=date(2040, 8, 6)):
    return {c.name: c.detail for c in rbi_minutes.checks(mn, d) if not c.passed}


def test_magnitudes_the_heading_in_the_table_statements_and_the_resolutions_copy():
    mn = rbi_minutes.parse(page(body()), URL)
    assert failed(mn) == {} and mn.problems == ()
    assert (mn.url, mn.external_key) == ("https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=99001",
                                         "/Scripts/BS_PressReleaseDisplay.aspx?prid=99001")
    assert (mn.days, mn.page_date, mn.press_release) == ((date(2040, 8, 4), date(2040, 8, 6)), date(2040, 8, 20),
                                                         "2040-2041/456")
    t = mn.table
    assert (t.heading, t.direction, t.rate) == ("Voting on the Resolution to reduce the policy repo rate", -1, None)
    assert [(r.name, r.yes, r.magnitude, t.signed(r)) for r in t.rows] == [
        *[(n, True, 35, -35) for n in SIX[:4]], *[(n, True, 25, -25) for n in SIX[4:]]]
    for r in t.rows:                                         # each row's span is the row in the text
        assert mn.text.text[r.span[0]:r.span[1]] == r.text and r.text.startswith(r.name)
    assert [s.name for s in mn.statements] == SIX
    s = mn.statements[2]
    assert mn.text.text[s.span[0]:s.span[1]].startswith("Statement by Prof. Cara N. Das\n\n")
    assert mn.text.text[mn.statements[-1].span[1] - 30:mn.statements[-1].span[1]].endswith(
        "Press Release: 2040-2041/456")
    # The resolution's copy, read as a resolution is: the same votes the resolution states.
    zs, ze = mn.resolution
    assert mn.text.text[zs:ze].startswith("5. On the basis of an assessment") and "unanimously" in mn.text.text[zs:ze]
    copy = rr.read_copy(mn.text, mn.resolution)
    assert copy.stance.label == "accommodative" and copy.problems == ()
    votes = {v.name: (v.voted_change_bps, v.stance_vote, v.is_dissent) for v in rr.member_votes(copy, -35)}
    assert votes == {**{n: (-35, "accommodative", False) for n in SIX[:4]},
                     **{n: (-25, "accommodative", True) for n in SIX[4:]}}
    # The chunks: each member's statement is its own speaker's (the headings are plain paragraphs, as RBI sets them).
    chunks = chunk_text(mn.text, root="Minutes", speakers="minutes")
    assert {c.speaker for c in chunks} - {None} == set(SIX)
    assert all(c.section == f"Minutes > Statement by {c.speaker}" for c in chunks if c.speaker)


def test_a_hold_with_its_heading_before_the_table_and_no_votes():
    mn = rbi_minutes.parse(page(body(
        heading="Voting on the Resolution to keep the policy repo rate unchanged at 6.50 per cent", heading_in_table=False,
        rows=[(n, "No" if n in SIX[4:] else "Yes") for n in SIX])), URL)
    assert failed(mn) == {}
    t = mn.table
    assert (t.direction, t.rate) == (0, Decimal("6.50"))
    assert [(r.yes, r.magnitude, t.signed(r)) for r in t.rows] == [(True, None, None)] * 4 + [(False, None, None)] * 2


@pytest.mark.parametrize("heading,direction,rate", [
    ("Voting on the Resolution to reduce the policy repo rate by 25 bps to 6.25 per cent", -1, Decimal("6.25")),
    ("Voting on the Resolution to increase policy repo rate by 25 bps to 5.50 per cent", 1, Decimal("5.50")),
    ("Voting on the Resolution to keep the policy repo rate at 6.50 per cent", 0, Decimal("6.50")),
    ("Voting on the Resolution to keep policy repo rate unchanged at 5.25 per cent", 0, Decimal("5.25"))])
def test_the_resolution_voted_on(heading, direction, rate):
    t = rbi_minutes.parse(page(body(heading=heading, heading_in_table=False, rows=[(n, "Yes") for n in SIX])), URL).table
    assert (t.heading, t.direction, t.rate) == (heading, direction, rate)


def test_untitled_and_title_without_a_stop_names_are_statements():
    mn = rbi_minutes.parse(page(body(statements=["Prof Cara N. Das", "M. Eli Paul", *SIX[:4]])), URL)
    assert [s.name for s in mn.statements][:2] == ["Prof Cara N. Das", "M. Eli Paul"]


@pytest.mark.parametrize("change,check", [
    ({"rows": [(n, "Yes", 35) for n in SIX[:5]]}, "minutes_votes_table"),                      # five rows
    ({"rows": [(n, "Maybe") for n in SIX]}, "minutes_votes_table"),                            # not Yes or No
    ({"rows": [(n, "Yes") for n in SIX[:5]] + [(SIX[5], "Yes", 25)]}, "minutes_votes_table"),  # a Yes without its size
    ({"statements": SIX[:5]}, "minutes_statements"),                                          # a statement missing
    ({"statements": SIX[:5] + [SIX[0]]}, "minutes_statements"),                               # the same member twice
])
def test_the_page_checks_refuse_a_table_or_statements_that_arent_six(change, check):
    assert check in failed(rbi_minutes.parse(page(body(**change)), URL))


def test_the_page_checks_refuse_another_meetings_minutes_or_another_document():
    mn = rbi_minutes.parse(page(body()), URL)
    assert set(failed(mn, date(2040, 10, 8))) == {"minutes_date"}                           # another meeting's
    early = rbi_minutes.parse(page(body(), day="Aug 05, 2040"), URL)
    assert "minutes_date" in failed(early)                                                  # dated before the decision
    other = rbi_minutes.parse(page(body(), title="Resolution of the Monetary Policy Committee (MPC) August 4 to 6, 2040"),
                              URL)
    assert {"minutes_title", "minutes_date"} <= set(failed(other))
    short = rbi_minutes.parse(page("<p>Statement by Dr. Ada Rao</p><p>Too short.</p>"), URL)
    assert {"minutes_text", "minutes_votes_table", "minutes_statements"} <= set(failed(short))
    with pytest.raises(rbi_minutes.MinutesError):
        rbi_minutes.parse(b"<html><body><p>Not a press release.</p></body></html>", URL)


def test_press_release_numbers_and_titles():
    assert rr.press_release_no("...\n\nPress Release: 2026-2027/1264") == "2026-2027/1264"
    assert rr.press_release_no("Press Release : 2018-2019 / 1299\n\nfootnote 7") == "2018-2019/1299"
    assert rr.press_release_no("The press release said nothing.") is None
    assert rr.titled("MPC minutes, 7 Oct 2026", "2026-2027/1361") == "MPC minutes, 7 Oct 2026 · Press Release: 2026-2027/1361"
    assert rr.titled("MPC minutes, 7 Oct 2026", None) == "MPC minutes, 7 Oct 2026"


# ---------------------------------------------------------------------------------------------- the saved pages
def saved(kind: str) -> list[tuple[dict, bytes]]:
    rows = [r for r in csv.DictReader((RBI / "INDEX.tsv").open(encoding="utf-8"), delimiter="\t") if r["kind"] == kind]
    return [(r, (RBI / r["file"]).read_bytes()) for r in rows]


@pytest.mark.skipif(not (RBI / "INDEX.tsv").exists(), reason="the saved pages aren't checked out (data/fixtures)")
def test_every_saved_minutes_passes_matches_the_roster_and_agrees_with_its_resolution():
    roster = members.load()
    resolutions = {}
    prev = None
    for r, data in saved("resolution"):
        res = rr.parse(data, r["url"])
        change = 0 if prev is None else int((res.repo.value - prev) * 100)
        prev = res.repo.value
        resolutions[res.page_date] = (res, change)
    minutes = [(r, rbi_minutes.parse(data, r["url"])) for r, data in saved("minutes")]
    assert len(minutes) == 49
    tables = {}
    for r, mn in minutes:
        d = mn.days[1]
        assert failed(mn, d) == {} and mn.problems == (), (d, failed(mn, d), mn.problems)
        assert mn.press_release and mn.resolution, d
        on = sorted(m.slug for m in roster.voters_on("RBI", d))
        said = sorted(roster.match("RBI", s.name).slug for s in mn.statements)
        voted = sorted(roster.match("RBI", v.name).slug for v in mn.table.rows)
        assert len(on) == 6 and on == said == voted, d                         # D4's check (Spec 01 §5.1)
        res, change = resolutions[d]
        assert mn.table.rate in (None, res.repo.value), d
        assert mn.table.direction == (change > 0) - (change < 0), d
        # Resolution vs minutes (Spec 01 §5.1: zero differences, or each explained): the table against the rate votes
        # the resolution states, the copy's stance dissenters against the resolution's.
        stated = {roster.match("RBI", v.name).slug: v for v in rr.member_votes(res, change)}
        for row in mn.table.rows:
            v = stated.get(roster.match("RBI", row.name).slug)
            if v is None or not v.rate_stated:
                assert d == date(2018, 12, 5), d                               # only the baseline names one member
                continue
            want = mn.table.signed(row) if row.magnitude is not None else change
            assert (v.voted_change_bps == want) if row.yes else (v.voted_change_bps != change), (d, row.text)
        copy = rr.read_copy(mn.text, mn.resolution)
        adopted = res.stance.label
        copied = {roster.match("RBI", v.name).slug for v in rr.member_votes(copy, change)
                  if v.stance_vote and v.stance_vote != adopted}
        assert copied == {k for k, v in stated.items() if v.stance_vote and v.stance_vote != adopted}, d
        tables[d] = mn.table
    # The magnitude column appears at the meetings where members voted for different sizes of a move.
    assert sorted(d for d, t in tables.items() if any(r.magnitude for r in t.rows)) == [
        date(2019, 8, 7), date(2019, 10, 4), date(2020, 3, 27), date(2020, 5, 22), date(2025, 6, 6)]
    aug24 = tables[date(2024, 8, 8)]
    assert sorted(r.name for r in aug24.rows if not r.yes) == ["Dr. Ashima Goyal", "Prof. Jayanth R. Varma"]
