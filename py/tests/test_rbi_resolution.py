"""MPC resolutions (Prompt 01 B6; Spec 01 §2 "RBI rates", "RBI votes (D4)", "Dissents (F23)", "Projections"; §3.3):
the parser and its page checks, against synthetic resolutions in the real template (policy_docs; real pages stay out of
the repo). The last tests read the 49 resolutions saved on the Mac (app/data/fixtures/rbi, git-ignored) when they're on
this machine, and check the spot values of Prompt 01 B6's done-when."""
import csv
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from policy_docs import mpc_new, mpc_old, mpc_title, rbi_page

from ft.policy import rbi_resolution as rr

FIXTURES = Path(os.environ.get("FT_FIXTURES", Path(__file__).resolve().parents[2] / "data" / "fixtures"))
URL = "https://rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=91234"
OLD_MEMBERS = ["Dr. Ada Rao", "Dr. Ben Iyer", "Prof. Cara N. Das", "Dr. Dev K. Shah", "Shri Eli Paul", "Shri Fay Roy"]


def page(body: str, *, day: str = "May 04, 2040", title: str | None = None) -> bytes:
    return rbi_page(body, title=title or mpc_title("May 2 to 4, 2040"), day=day).encode()


def parse(body: str, **kw) -> rr.Resolution:
    return rr.parse(page(body, **kw), URL)


def values(r: rr.Resolution) -> dict:
    return {k: (str(v.value), v.stated_bps, v.stated_from and str(v.stated_from), v.direction) for k, v in r.rates.items()}


def projections(r: rr.Resolution) -> dict:
    return {(p.variable, p.target_label): (str(p.value) if p.value is not None else f"{p.low}-{p.high}")
            for p in r.projections}


def votes(r: rr.Resolution, decision: int) -> dict:
    return {v.name: (v.voted_change_bps, v.stance_vote, v.is_dissent) for v in rr.member_votes(r, decision)}


CUT = mpc_old(
    decision="reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points from 6.5 "
             "per cent to 6.25 per cent with immediate effect.",
    consequently="Consequently, the reverse repo rate under the LAF stands adjusted to 6.0 per cent, and the marginal "
                 "standing facility (MSF) rate and the Bank Rate to 6.5 per cent.",
    stance="The MPC also decided to change the monetary policy stance from calibrated tightening to neutral.",
    projections=[
        "In the April policy, CPI inflation was projected at 2.4 per cent for Q4:2039-40 and 2.9-3.0 per cent for H1.",
        ("Taking these factors into consideration, the path of CPI inflation is revised to 3.0-3.1 per cent for "
        "H1:2040-41 and to 3.4-3.7 per cent for H2:2040-41, with risks broadly balanced (Chart 1)."),
        ("Taking into consideration the above factors, GDP growth for 2040-41 is revised downwards from 7.2 per cent in "
        "the April policy to 7.0 per cent – in the range of 6.4-6.7 per cent for H1:2040-41 and 7.2-7.5 per cent for "
        "H2 – with risks evenly balanced (Chart 2).")],
    votes=[("The decision to change the monetary policy stance was unanimous. As regards the reduction in the policy "
           "repo rate, Dr. Ada Rao, Dr. Ben Iyer, Prof. Cara N. Das and Shri Fay Roy voted in favour of the decision. "
           "Dr. Dev K. Shah and Shri Eli Paul voted to keep the policy rate unchanged.")])


def test_an_old_era_cut_rates_stance_projections_and_votes():
    r = parse(CUT, day="May 04, 2019")
    assert (r.title, r.page_date, r.url) == (mpc_title("May 2 to 4, 2040"), date(2019, 5, 4),
                                             "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=91234")
    assert r.external_key == "/Scripts/BS_PressReleaseDisplay.aspx?prid=91234"
    assert values(r) == {"repo": ("6.25", -25, "6.5", -1), "reverse_repo": ("6.0", None, None, None),
                         "msf": ("6.5", None, None, None), "bank_rate": ("6.5", None, None, None)}
    s, e = r.repo.sentence
    assert r.text.text[s:e].startswith("reduce the policy repo rate")
    assert r.stance.label == "neutral"
    assert projections(r) == {("cpi", "H1 2040-41"): "3.0-3.1", ("cpi", "H2 2040-41"): "3.4-3.7",
                              ("gdp", "FY 2040-41"): "7.0", ("gdp", "H1 2040-41"): "6.4-6.7",
                              ("gdp", "H2 2040-41"): "7.2-7.5"}          # not the April vintage it restates
    gdp = next(p for p in r.projections if p.target_label == "H2 2040-41" and p.variable == "gdp")
    assert (gdp.target_start, gdp.target_end, gdp.low, gdp.high) == (date(2040, 10, 1), date(2041, 3, 31),
                                                                     Decimal("7.2"), Decimal("7.5"))
    assert r.unparsed == () and r.problems == ()
    assert r.votes.members_from == "vote sentences" and len(r.votes.members) == 6
    assert votes(r, -25) == {"Dr. Ada Rao": (-25, "neutral", False), "Dr. Ben Iyer": (-25, "neutral", False),
                             "Prof. Cara N. Das": (-25, "neutral", False), "Shri Fay Roy": (-25, "neutral", False),
                             "Dr. Dev K. Shah": (0, "neutral", True), "Shri Eli Paul": (0, "neutral", True)}
    shah = next(v for v in rr.member_votes(r, -25) if v.name == "Dr. Dev K. Shah")
    assert shah.dissent_text == "voted to keep the policy rate unchanged"
    assert all(c.passed for c in rr.checks(r, date(2019, 5, 4)))


def test_an_off_cycle_cut_with_a_wider_corridor_and_quantum_votes():
    r = parse(mpc_old(
        decision="reduce the policy repo rate under the liquidity adjustment facility (LAF) by 75 basis points to 4.40 "
                 "per cent from 5.15 per cent with immediate effect;",
        consequently="accordingly, the marginal standing facility (MSF) rate and the Bank Rate stand reduced to 4.65 "
                     "per cent from 5.40 per cent;</p><p>further, consequent upon the widening of the LAF corridor, "
                     "the reverse repo rate under the LAF stands reduced by 90 basis points to 4.0 per cent.",
        stance="The MPC also decided to continue with the accommodative stance as long as it is necessary to revive "
               "growth.",
        votes=[("All members voted for a reduction in the policy repo rate and maintaining the accommodative stance as "
               "long as it is necessary to revive growth."),
               ("Dr. Ada Rao, Dr. Ben Iyer, Prof. Cara N. Das and Shri Fay Roy voted for a 75 bps reduction in the policy "
               "repo rate. Dr. Dev K. Shah voted for a 50 bps reduction in the policy repo rate, while Shri Eli Paul "
               "voted for a reduction by 25 bps.")]))
    assert values(r) == {"repo": ("4.40", -75, "5.15", -1), "msf": ("4.65", None, "5.40", -1),
                         "bank_rate": ("4.65", None, "5.40", -1), "reverse_repo": ("4.0", -90, None, -1)}
    assert r.stance.label == "accommodative" and r.projections == ()
    v = votes(r, -75)
    assert v["Dr. Dev K. Shah"] == (-50, "accommodative", True) and v["Shri Eli Paul"] == (-25, "accommodative", True)
    assert v["Shri Fay Roy"] == (-75, "accommodative", False) and len(v) == 6


SDF_HIKE = mpc_old(
    decision="Increase the policy repo rate under the liquidity adjustment facility (LAF) by 35 basis points to 6.25 "
             "per cent with immediate effect.",
    consequently="Consequently, the standing deposit facility (SDF) rate stands adjusted to 6.00 per cent and the "
                 "marginal standing facility (MSF) rate and the Bank Rate to 6.50 per cent.",
    stance="The MPC also decided to remain focused on withdrawal of accommodation to ensure that inflation remains "
           "within the target going forward, while supporting growth.",
    votes=[("Dr. Ada Rao, Dr. Ben Iyer, Prof. Cara N. Das, Shri Eli Paul and Shri Fay Roy voted to increase the policy "
           "repo rate by 35 basis points. Dr. Dev K. Shah voted against the repo rate hike."),
           ("Dr. Ada Rao, Prof. Cara N. Das, Shri Eli Paul and Shri Fay Roy voted to remain focused on withdrawal of "
           "accommodation to ensure that inflation remains within the target going forward, while supporting growth. "
           "Dr. Ben Iyer and Dr. Dev K. Shah voted against this part of the resolution.")],
    projections=[("Taking all these factors into consideration, the real GDP growth for 2040-41 is projected at 6.8 per "
                 "cent with Q3 at 4.4 per cent and Q4 at 4.2 per cent, with risks evenly balanced."),
                 "CPI inflation for Q1:2041-42 is projected at 5.0 per cent and for Q2 at 5.4 per cent (Chart 1)."])


def test_the_sdf_era_against_the_hike_and_stance_dissents():
    r = parse(SDF_HIKE, day="Dec 07, 2040")
    assert values(r) == {"repo": ("6.25", 35, None, 1), "sdf": ("6.00", None, None, None),
                         "msf": ("6.50", None, None, None), "bank_rate": ("6.50", None, None, None)}
    assert r.stance.label == "withdrawal of accommodation"
    assert projections(r) == {("gdp", "FY 2040-41"): "6.8", ("gdp", "Q3 2040-41"): "4.4", ("gdp", "Q4 2040-41"): "4.2",
                              ("cpi", "Q1 2041-42"): "5.0", ("cpi", "Q2 2041-42"): "5.4"}
    v = {x.name: x for x in rr.member_votes(r, 35)}
    assert (v["Dr. Dev K. Shah"].voted_change_bps, v["Dr. Dev K. Shah"].stance_vote) == (None, "against")
    assert v["Dr. Dev K. Shah"].dissent_text == ("voted against the repo rate hike; voted against this part of the "
                                                "resolution")
    assert (v["Dr. Ben Iyer"].voted_change_bps, v["Dr. Ben Iyer"].stance_vote, v["Dr. Ben Iyer"].is_dissent) == \
        (35, "against", True)
    assert sum(x.is_dissent for x in v.values()) == 2                      # each member counted once
    assert all(c.passed for c in rr.checks(r, date(2040, 12, 7)))


def test_named_with_all_members_except_and_reservations():
    r = parse(mpc_old(
        decision="keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 4.0 per cent.",
        consequently="The reverse repo rate under the LAF remains unchanged at 3.35 per cent and the marginal standing "
                     "facility (MSF) rate and the Bank Rate at 4.25 per cent.",
        stance="The MPC also decided to continue with the accommodative stance as long as necessary.",
        votes=[("All members of the MPC – Dr. Ada Rao; Dr. Ben Iyer; Prof. Cara N. Das; Dr. Dev K. Shah; Shri Eli Paul; "
               "and Shri Fay Roy – unanimously voted to keep the policy repo rate unchanged at 4.0 per cent."),
               ("All members, namely, Dr. Ada Rao, Dr. Ben Iyer, Dr. Dev K. Shah, Shri Eli Paul and Shri Fay Roy, except "
               "Prof. Cara N. Das, voted to continue with the accommodative stance as long as necessary. Prof. Cara N. "
               "Das expressed reservations on this part of the resolution.")]), day="Oct 08, 2039")
    assert values(r)["repo"] == ("4.0", None, None, 0) and values(r)["reverse_repo"][0] == "3.35"
    v = votes(r, 0)
    assert v["Prof. Cara N. Das"] == (0, "against", True) and v["Dr. Ada Rao"] == (0, "accommodative", False)
    assert len(v) == 6 and sum(x[2] for x in v.values()) == 1
    das = next(x for x in rr.member_votes(r, 0) if x.name == "Prof. Cara N. Das")
    assert das.dissent_text == "expressed reservations on this part of the resolution"


NEW = mpc_new(
    no=73, days="October 5 to 7, 2040", chair="Shri Gita Mehra",
    members=["Dr. Hari Lal", "Shri Ivan Sen", "Prof. Jaya Ram", "Dr. Kiran Bose", "Shri Lata Nair"],
    decision="voted unanimously to increase the policy repo rate under the liquidity adjustment facility (LAF) by 25 "
             "bps to 5.50 per cent. Consequently, the standing deposit facility (SDF) rate stands adjusted at 5.25 per "
             "cent and the marginal standing facility (MSF) rate and the Bank Rate at 5.75 per cent.",
    stance="change the stance to calibrated tightening.",
    projections=[("Taking all these factors into consideration, real GDP growth for 2040-41 is projected at (-)7.5 per "
                 "cent, with Q2 at 7.2 per cent; and Q3 and Q4 at 6.5 per cent each."),
                 ("Considering all factors, CPI inflation is projected to be 5.2 per cent for 2040-41 with Q2 at 4.9 per "
                 "cent; Q3 at 6.0 per cent; and Q4 at 5.7 per cent (Chart 2). CPI inflation for Q1:2041-42 and Q2 are "
                 "projected at 3.9 per cent and 4.0 per cent, respectively."),
                 "Core inflation is projected at 4.4 per cent for 2040-41."],
    rationale=[("Considering all these factors, the MPC unanimously voted to increase the policy repo rate by 25 basis "
               "points to 5.50 per cent. The MPC also decided to change the stance to calibrated tightening."),
               ("Two members - Dr. Hari Lal and Prof. Jaya Ram - were of the view that the stance be retained at "
               "neutral.")])


def test_a_new_era_resolution_names_its_members_and_dissenters():
    r = parse(NEW, day="Oct 07, 2040", title=mpc_title("October 5 to 7, 2040"))
    assert values(r) == {"repo": ("5.50", 25, None, 1), "sdf": ("5.25", None, None, None),
                         "msf": ("5.75", None, None, None), "bank_rate": ("5.75", None, None, None)}
    assert r.stance.label == "calibrated tightening" and r.meeting_no == 73
    assert r.votes.members_from == "attendance" and r.votes.members[0] == "Shri Gita Mehra"
    assert projections(r) == {("gdp", "FY 2040-41"): "-7.5", ("gdp", "Q2 2040-41"): "7.2", ("gdp", "Q3 2040-41"): "6.5",
                              ("gdp", "Q4 2040-41"): "6.5", ("cpi", "FY 2040-41"): "5.2", ("cpi", "Q2 2040-41"): "4.9",
                              ("cpi", "Q3 2040-41"): "6.0", ("cpi", "Q4 2040-41"): "5.7", ("cpi", "Q1 2041-42"): "3.9",
                              ("cpi", "Q2 2041-42"): "4.0"}                  # no core inflation
    v = {x.name: x for x in rr.member_votes(r, 25)}
    assert len(v) == 6 and all(x.voted_change_bps == 25 for x in v.values())
    assert {n for n, x in v.items() if x.is_dissent} == {"Dr. Hari Lal", "Prof. Jaya Ram"}
    assert v["Dr. Hari Lal"].stance_vote == "neutral" and v["Shri Ivan Sen"].stance_vote == "calibrated tightening"
    assert v["Shri Ivan Sen"].stance_how == "elimination" and v["Dr. Hari Lal"].rate_how == "all members"
    assert v["Dr. Hari Lal"].dissent_text == "were of the view that the stance be retained at neutral"


def test_a_resolution_that_names_nobody_leaves_unanimous_votes_to_the_roster():
    r = parse(mpc_old(
        decision="keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.5 per cent.",
        consequently="Consequently, the reverse repo rate under the LAF remains at 6.25 per cent, and the marginal "
                     "standing facility (MSF) rate and the Bank Rate at 6.75 per cent.",
        stance="The decision of the MPC is consistent with the stance of calibrated tightening of monetary policy.",
        votes=[("Against this backdrop, the MPC decided to keep the policy repo rate on hold. While the decision on "
               "keeping the policy rate unchanged was unanimous, Dr. Ben Iyer voted to change the stance to neutral.")]),
        day="Dec 05, 2038")
    assert r.votes.members == () and r.stance.label == "calibrated tightening"
    assert votes(r, 0) == {"Dr. Ben Iyer": (0, "neutral", True)}
    # D4's roster inference (the task passes the roster's six only while it's switched on).
    with_roster = {x.name: (x.voted_change_bps, x.stance_vote, x.rate_how)
                   for x in rr.member_votes(r, 0, tuple(OLD_MEMBERS))}
    assert with_roster["Dr. Ada Rao"] == (0, None, "roster") and with_roster["Dr. Ben Iyer"][:2] == (0, "neutral")
    assert len(with_roster) == 6


@pytest.mark.parametrize("clause, label", [
    ("change the monetary policy stance from calibrated tightening to neutral", "neutral"),
    ("maintain the neutral stance of monetary policy", "neutral"),
    ("continue with the accommodative stance as long as it is necessary", "accommodative"),
    ("remain accommodative while focusing on withdrawal of accommodation to ensure",
     "accommodative, focused on withdrawal of accommodation"),
    ("remain focused on withdrawal of accommodation", "withdrawal of accommodation"),
    ("voted for a change in stance from withdrawal of accommodation to 'neutral'", "neutral"),
    ("change the stance to calibrated tightening", "calibrated tightening"),
    ("the stance be changed from neutral to accommodative", "accommodative"),
    ("the stance be retained at neutral", "neutral"),
    ("a neutral or an accommodative stance", None),
    ("continue with the existing policy rate", None),
])
def test_stance_labels(clause, label):
    assert rr.stance_label(clause) == label


def test_the_page_checks_refuse_a_wrong_corridor_floor_date_or_title():
    def failed(body: str, d: date, **kw) -> set[str]:
        return {c.name for c in rr.checks(parse(body, day=f"{d:%b %d, %Y}", **kw), d) if not c.passed}

    may19 = date(2019, 5, 4)
    assert failed(CUT, may19) == set()
    assert failed(CUT.replace("the Bank Rate to 6.5 per cent", "the Bank Rate to 6.75 per cent"), may19) == \
        {"msf_spread"}                                                 # one clause: MSF and Bank Rate both 6.75
    assert failed(CUT.replace("rate and the Bank Rate to 6.5 per cent", "rate to 6.5 per cent and the Bank Rate to "
                              "6.75 per cent"), may19) == {"bank_rate"}
    assert failed(CUT.replace("MSF) rate and the Bank Rate to 6.5", "MSF) rate to 6.5"), may19) == \
        {"instruments", "bank_rate"}
    assert rr.checks(parse(CUT, day="May 05, 2019"), may19)[2].name == "resolution_date"
    assert failed(CUT, may19, title="Statement on Developmental and Regulatory Policies") == {"resolution_title"}
    # From 8 Apr 2022 the floor is the SDF (= repo - 25 bps): a reverse repo floor fails, and so does an SDF before.
    assert failed(CUT, date(2022, 5, 4)) == {"instruments", "sdf_spread"}
    assert failed(SDF_HIKE, date(2021, 12, 7)) == {"instruments"}
    assert failed(SDF_HIKE.replace("adjusted to 6.00 per cent", "adjusted to 5.90 per cent"), date(2040, 12, 7)) == \
        {"sdf_spread"}
    # A stance outside the vocabulary warns (the stance votes and the AI output stay drafts), never blocks.
    odd = [c for c in rr.checks(parse(CUT.replace("from calibrated tightening to neutral", "to growth-supportive"),
                                      day="May 04, 2019"), may19) if not c.passed]
    assert [(c.name, c.blocking) for c in odd] == [("stance_label", False)]
    with pytest.raises(rr.ResolutionError, match="tableheader"):
        rr.parse(b"<html><body><p>Press Releases</p></body></html>", URL)


def test_the_rss_feed_and_links():
    feed = ("﻿<?xml version=\"1.0\" encoding=\"utf-8\"?><rss version=\"2.0\"><channel><title>PRESS RELEASES FROM RBI"
            "</title><item><title><![CDATA[Monetary Policy Statement, 2040-41 Resolution of the Monetary Policy Committee"
            " (MPC) October 5 to 7, 2040]]></title><link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?"
            "prid=99001</link><pubDate>Sun, 07 Oct 2040 10:00:00</pubDate></item><item><title><![CDATA[Auction of State "
            "Government Securities]]></title><link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=99002"
            "</link><pubDate>Sun, 07 Oct 2040 09:40:00</pubDate></item></channel></rss>").encode()
    items = rr.parse_rss(feed)
    assert [i.is_resolution for i in items] == [True, False]
    assert items[0].published == datetime(2040, 10, 7, 4, 30, tzinfo=UTC)
    assert rr.page_url(items[0].link) == "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=99001"
    with pytest.raises(rr.ResolutionError):
        rr.parse_rss(b"<html></html>")
    with pytest.raises(rr.ResolutionError):
        rr.page_url("https://www.rbi.org.in/Scripts/BS_SpeechesView.aspx?Id=1583")


# ---------------------------------------------------------------------------------------------- the saved resolutions
RBI = FIXTURES / "rbi"


def saved() -> list[tuple[date, rr.Resolution]]:
    rows = [r for r in csv.DictReader((RBI / "INDEX.tsv").open(encoding="utf-8"), delimiter="\t")
            if r["kind"] == "resolution"]
    return [(date.fromisoformat(r["posted_on"]), rr.parse((RBI / r["file"]).read_bytes(), r["url"])) for r in rows]


@pytest.mark.skipif(not (RBI / "INDEX.tsv").exists(), reason="the saved pages are only on the Mac (app/data/fixtures)")
def test_every_saved_resolution_passes_and_the_spot_checks_hold():
    res = saved()
    assert len(res) == 49 and res[0][0] == date(2018, 12, 5) and res[-1][0] == date(2026, 10, 7)
    by = {}
    prev = None
    for d, r in res:
        failed = [f"{c.name}: {c.detail}" for c in rr.checks(r, d) if not c.passed]
        assert not failed, (d, failed)
        assert r.problems == () and r.unparsed == (), d
        change = 0 if prev is None else int((r.repo.value - prev) * 100)
        prev = r.repo.value
        by[d] = (r, change, {v.name.split()[-1]: v for v in rr.member_votes(r, change)})
    # Prompt 01 B6, done when: the spot checks.
    assert [by[d][1] for d in (date(2019, 8, 7), date(2020, 3, 27), date(2020, 5, 22), date(2022, 5, 4))] == \
        [-35, -75, -40, 40]
    apr22 = by[date(2022, 4, 8)][0]
    assert apr22.rates["sdf"].value == Decimal("3.75") and "reverse_repo" not in apr22.rates
    oct26 = by[date(2026, 10, 7)]
    assert (oct26[0].repo.value, oct26[1]) == (Decimal("5.50"), 25)
    # Votes: every 2019→ meeting gives six rate votes and six stance votes; Aug 2024 "Rate 4–2" with Goyal and Varma
    # at −25 bps; Oct 2026's stance dissenters counted, each member once.
    for d, (r, change, v) in by.items():
        if d.year >= 2019:
            assert sum(x.rate_stated for x in v.values()) == 6 and sum(x.stance_vote is not None for x in v.values()) \
                == 6, d
    aug24 = by[date(2024, 8, 8)][2]
    assert sorted((n, x.voted_change_bps) for n, x in aug24.items() if x.voted_change_bps != 0) == \
        [("Goyal", -25), ("Varma", -25)]
    assert sum(x.voted_change_bps == 0 for x in aug24.values()) == 4
    oct26v = oct26[2]
    assert {n for n, x in oct26v.items() if x.is_dissent} == {"Kumar", "Singh"}
    assert sum(x.stance_vote == "calibrated tightening" for x in oct26v.values()) == 4
    # Projections: every resolution that publishes them has CPI and GDP rows (none at 2020's COVID meetings or the
    # May 2022 off-cycle meeting).
    none = {d for d, (r, _, _) in by.items() if not r.projections}
    assert none == {date(2020, 3, 27), date(2020, 5, 22), date(2020, 8, 6), date(2022, 5, 4)}
    assert all({p.variable for p in r.projections} == {"cpi", "gdp"} for r, _, _ in by.values() if r.projections)
    assert sum(len(r.projections) for r, _, _ in by.values()) == 419
