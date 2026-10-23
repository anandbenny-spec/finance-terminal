"""config/members.yaml and its loader (Spec 01 D12; Prompt 01 B5): the file is a valid roster with a source note per
term, the voters from Dec 2018 on are on it, and sync() loads it into policy.member and policy.member_term (re-runs write
nothing)."""
from datetime import date

import psycopg
import pytest
from conftest import dsn

from ft.policy import members

JOB, ADMIN = dsn("job_writer"), dsn("postgres")


def test_the_roster_file_loads_with_the_voting_members_of_every_meeting_from_dec_2018():
    r = members.load()
    assert sum(m.bank == "FED" for m in r.members.values()) == 31          # everyone who voted from Dec 2018 (B5 part 2)
    assert all(t.source for m in r.members.values() for t in m.terms)
    for day in (date(2025, 1, 29), date(2025, 5, 7), date(2025, 9, 17), date(2025, 12, 10), date(2026, 1, 28),
                date(2026, 4, 29), date(2026, 6, 17), date(2026, 10, 28)):
        assert len(r.voters_on("FED", day)) == 12, day
    # Vacancies, as the minutes' attendance lists them: the Board had five members to Dec 2020 and three in early 2022
    # (Clarida and Quarles gone, Cook, Jefferson and Barr not yet appointed), Boston's seat was empty to Jul 2022,
    # Brainard left in Feb 2023 (Kugler joined in Sep) and Cleveland's seat was empty in Jul 2024.
    seats = {date(2018, 12, 19): 10, date(2019, 6, 19): 10, date(2020, 3, 15): 10, date(2021, 1, 27): 11,
             date(2022, 3, 16): 8, date(2022, 6, 15): 10, date(2022, 7, 27): 12, date(2023, 3, 22): 11,
             date(2023, 9, 20): 12, date(2024, 7, 31): 11, date(2024, 9, 18): 12}
    assert {d: len(r.voters_on("FED", d)) for d in seats} == seats
    assert r.match("FED", "Eric Rosengren").slug == r.match("FED", "Eric S. Rosengren").slug == "eric-s-rosengren"
    harker = r.match("FED", "Patrick Harker")                               # Boston's alternate in 2022, then a member
    assert [harker.term_on(d).is_voter for d in (date(2022, 3, 16), date(2023, 3, 22))] == [False, True]
    chair = {d: next(m.name for m in r.voters_on("FED", d) if m.term_on(d).role == "fed_chair")
             for d in (date(2026, 5, 21), date(2026, 5, 22))}
    assert chair == {date(2026, 5, 21): "Jerome H. Powell", date(2026, 5, 22): "Kevin Warsh"}
    assert r.match("FED", "Jerome H. Powell").term_on(date(2026, 6, 17)).role == "fed_governor"
    voters_2026 = {m.slug for m in r.voters_on("FED", date(2026, 10, 28)) if m.term_on(date(2026, 10, 28)).role
                   == "fed_bank_president"}
    assert voters_2026 == {"beth-m-hammack", "neel-kashkari", "lorie-k-logan", "anna-paulson"}
    assert r.match("FED", "Neel Kashkari").term_on(date(2025, 5, 7)).is_voter is False      # an alternate in 2025


def test_names_match_without_case_periods_or_spacing_and_through_aliases():
    r = members.load()
    for name in ("Jerome H. Powell", "jerome h powell", "Jerome  H Powell", "Jerome Powell"):
        assert r.match("FED", name).slug == "jerome-h-powell", name
    assert r.match("FED", "Kevin M. Warsh").slug == "kevin-warsh"
    assert r.match("FED", "Nobody Here") is None and r.match("RBI", "Jerome H. Powell") is None


# Every MPC meeting from the Dec 2018 baseline to Oct 2026 (its last day), and its members in the minutes (B6).
MPC_MEETINGS = [date(2018, 12, 5), date(2019, 8, 7), date(2020, 2, 6), date(2020, 8, 6), date(2020, 10, 9),
                date(2022, 5, 4), date(2024, 10, 9), date(2025, 2, 7), date(2025, 6, 6), date(2025, 10, 1),
                date(2026, 10, 7)]


def test_the_mpc_roster_has_six_members_on_every_meeting_day_with_one_governor():
    r = members.load()
    assert sum(m.bank == "RBI" for m in r.members.values()) == 21
    for d in MPC_MEETINGS:
        six = r.voters_on("RBI", d)
        roles = sorted(m.term_on(d).role for m in six)
        assert roles == ["mpc_external_member"] * 3 + ["rbi_deputy_governor", "rbi_executive_director",
                                                       "rbi_governor"], d
    gov = {d: next(m.slug for m in r.voters_on("RBI", d) if m.term_on(d).role == "rbi_governor")
           for d in (date(2018, 12, 5), date(2024, 12, 6), date(2025, 2, 7))}
    assert gov == {date(2018, 12, 5): "urjit-r-patel", date(2024, 12, 6): "shaktikanta-das",
                   date(2025, 2, 7): "sanjay-malhotra"}
    patra = r.match("RBI", "Dr. Michael Debabrata Patra")          # nominated officer, then Deputy Governor
    assert [t.role for t in patra.terms] == ["rbi_executive_director", "rbi_deputy_governor"]
    assert {m.slug for m in r.voters_on("RBI", date(2026, 12, 4))} == {
        "sanjay-malhotra", "poonam-gupta", "indranil-bhattacharyya", "nagesh-kumar", "saugata-bhattacharya",
        "ram-singh"}                                               # the next meeting: the open terms


@pytest.mark.parametrize("written", ["Prof. Ram Singh", "Prof Ram Singh", "Professor Ram Singh", "Ram Singh",
                                     "Dr. Ram Singh"])
def test_rbi_names_match_with_or_without_their_title(written):
    r = members.load()
    assert r.match("RBI", written).slug == "ram-singh"
    assert r.match("RBI", "Shri M Rajeshwar Rao").slug == "m-rajeshwar-rao"
    assert r.match("FED", "Prof. Ram Singh") is None and members.strip_title("Smt. Gia Menon") == "Gia Menon"


def write(tmp_path, text: str):
    p = tmp_path / "members.yaml"
    p.write_text(text)
    return p


TERM = "{role: fed_governor, affiliation: Board of Governors, voter: true, from: 2040-01-01, source: test}"


@pytest.mark.parametrize("body, error", [
    ("fed:\n  a-b:\n    name: A B\n    terms: [{role: fed_governor, voter: true, from: 2040-01-01}]", "source note"),
    ("fed:\n  a-b:\n    name: A B\n    terms: [{role: rbi_governor, voter: true, from: 2040-01-01, source: x}]", "role"),
    ("fed:\n  a-b:\n    name: A B\n    terms:\n      - " + TERM + "\n      - " + TERM, "overlap"),
    ("fed:\n  a-b:\n    name: A B\n    terms: [" + TERM + "]\n  c-d:\n    name: C D\n    aliases: [a  b.]\n    terms: ["
     + TERM + "]", "also names"),
    ("fed:\n  A_B:\n    name: A B\n    terms: [" + TERM + "]", "slug"),
    ("fed:\n  a-b:\n    name: A B\n    terms: [{role: fed_governor, voter: yes please, from: 2040-01-01, source: x}]",
     "voter"),
    (("fed:\n  a-b:\n    name: A B\n    terms: [{role: fed_governor, voter: true, from: 2040-02-01, to: 2040-01-01, "
     "source: x}]"), "ends before"),
    ("ecb:\n  a-b:\n    name: A B", "unknown banks"),
])
def test_an_invalid_roster_is_refused(tmp_path, body, error):
    with pytest.raises(members.RosterError, match=error):
        members.load(write(tmp_path, body))


@pytest.mark.db
def test_sync_loads_members_and_terms_and_a_rerun_writes_nothing(tmp_path):
    path = write(tmp_path, """fed:
  b5-sync-una-v-sync:
    name: Una V. Sync
    aliases: [Una Sync]
    terms:
      - {role: fed_bank_president, affiliation: FRB Test, voter: false, from: 2040-01-01, to: 2040-12-31, source: one}
      - {role: fed_bank_president, affiliation: FRB Test, voter: true, from: 2041-01-01, source: two}
""")
    r = members.load(path)
    with psycopg.connect(JOB) as tx:
        ids, n = members.sync(tx, r)
        assert n == 3
        assert members.sync(tx, r)[1] == 0
    rows = psycopg.connect(ADMIN).execute(
        """select m.name, t.is_voter, lower(t.valid), upper(t.valid), t.source_note from policy.member m
             join policy.member_term t on t.member_id = m.id where m.slug = 'b5-sync-una-v-sync' order by 3""").fetchall()
    assert rows == [("Una V. Sync", False, date(2040, 1, 1), date(2041, 1, 1), "one"),
                    ("Una V. Sync", True, date(2041, 1, 1), None, "two")]
    # A changed term replaces the member's terms; the member keeps its id.
    path.write_text(path.read_text().replace("source: two", "source: two (corrected)"))
    with psycopg.connect(JOB) as tx:
        ids2, n = members.sync(tx, members.load(path))
    assert ids2 == ids and n == 2
