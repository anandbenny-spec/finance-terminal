"""The FOMC statement parser and its checks (Prompt 01 B5; Spec 01 §2 "Fed statement", "Fed votes", "Dissents";
Anand's 23 Oct 2026 decisions on the checks), on synthetic pages in the real template (tests/policy_docs.py). The last
tests read the statements saved in data/fixtures/fed (the private fixtures repo) when they're on this machine."""
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from policy_docs import FED_ECONOMY, fed_named, fed_page, fed_rss, fed_tally

from ft.policy import fed_statement as fs
from ft.policy import members

URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
DAY = date(2026, 9, 16)
FIXTURES = Path(os.environ.get("FT_FIXTURES", Path(__file__).resolve().parents[2] / "data" / "fixtures"))
VOTERS_12 = ("Kevin Warsh, Chairman; John C. Williams, Vice Chair; Michael S. Barr; Michelle W. Bowman; Lisa D. Cook; "
             "Beth M. Hammack; Philip N. Jefferson; Neel Kashkari; Lorie K. Logan; Anna Paulson; Jerome H. Powell; and "
             "Christopher J. Waller")


def parse(paragraphs, **kw) -> fs.Statement:
    return fs.parse(fed_page(paragraphs, **kw).encode(), URL)


def failed(st: fs.Statement, day: date = DAY) -> dict[str, str]:
    return {r.name: r.detail for r in fs.checks(st, day) if not r.passed}


# ---------------------------------------------------------------------------------------------- numbers and ranges
@pytest.mark.parametrize("text, value", [
    ("3-3/4", "3.75"), ("3\u20111/2", "3.5"), ("1/4", "0.25"), ("0", "0"), ("4", "4"), ("0.5", "0.5"),
    ("2 - 1/4", "2.25"), ("5-1/2", "5.5"),
])
def test_pct_reads_the_feds_fractions(text, value):
    assert fs.pct(text) == Decimal(value)


@pytest.mark.parametrize("decision, verb, lo, hi, stated", [
    ("decided to maintain the target range for the federal funds rate at 3\u20111/2 to 3\u20113/4 percent",
     "maintain", "3.5", "3.75", None),
    ("decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent",
     "raise", "3.75", "4", 25),
    ("decided to lower the target range for the federal funds rate to 2 to 2-1/4 percent", "lower", "2", "2.25", None),
    (("decided today to lower the target range for the federal funds rate by 1/2 percentage point, to 1 to 1\u20111/4 "
     "percent"), "lower", "1", "1.25", -50),
    (("decided to keep the target range for the federal funds rate at 0 to 1/4 percent and expects it will be "
     "appropriate to maintain this target range"), "keep", "0", "0.25", None),
    (("decided to raise the target range for the federal funds rate to 1/4 to 1/2 percent and anticipates that "
     "ongoing increases in the target range will be appropriate"), "raise", "0.25", "0.5", None),
    ("decided to lower the target range for the federal funds rate by 25 basis points to 4 to 4-1/4 percent",
     "lower", "4", "4.25", -25),
])
def test_the_decision_sentence_in_each_wording(decision, verb, lo, hi, stated):
    st = parse(fed_named(decision, f"Voting for the monetary policy action were {VOTERS_12}."))
    d = st.decision
    assert (d.verb, d.range, d.stated_bps) == (verb, fs.Range(Decimal(lo), Decimal(hi)), stated)
    # The decision-and-guidance paragraph (D2) is the block holding the sentence, guidance included.
    para = st.text.text[d.paragraph[0]:d.paragraph[1]]
    assert para.startswith("In support of its goals") and "carefully assess incoming data" in para
    assert st.text.text[d.sentence[0]:d.sentence[1]].startswith("In support of its goals, the Committee decided")
    assert not failed(st)


def test_the_text_is_the_body_without_the_heading_media_line_note_or_panel():
    st = parse(fed_named("decided to maintain the target range for the federal funds rate at 4 to 4-1/4 percent",
                         f"Voting for the monetary policy action were {VOTERS_12}."), panel=True)
    t = st.text.text
    assert t.startswith(FED_ECONOMY[0]) and t.endswith("and Christopher J. Waller.")
    for furniture in ("For media inquiries", "Implementation Note", "Related actions", "Share", "Federal Reserve issues",
                      "email protected", "Last Update"):
        assert furniture not in t
    assert st.text.extractor_version == "html-1/fed_press_release"
    assert (st.title, st.page_date, st.release_line) == ("Federal Reserve issues FOMC statement", DAY,
                                                          "For release at 2:00 p.m. EDT")
    assert st.released_at == datetime(2026, 9, 16, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    assert st.external_key == "/newsevents/pressreleases/monetary20260916a.htm"


# ---------------------------------------------------------------------------------------------- votes, named format
DECISION_CUT = "decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent"
PREV = fs.Range(Decimal(4), Decimal("4.25"))


def test_named_votes_with_roles_dropped_and_every_kind_of_dissent():
    voting = ("Voting for the monetary policy action were Jerome H. Powell, Chair, John C. Williams, Vice Chair; Michael S. "
              "Barr; Lisa D. Cook; Philip N. Jefferson; and Alberto G. Musalem. Voting against this action were Stephen "
              "I. Miran, who preferred to lower the target range for the federal funds rate by 1/2 percentage point at "
              "this meeting, and Jeffrey R. Schmid, who preferred no change to the target range for the federal funds "
              "rate at this meeting; and Esther L. George and Eric S. Rosengren, who preferred at this meeting to "
              "maintain the target range at 4 percent to 4-1/4 percent; and James Bullard, who preferred at this "
              "meeting to lower the target range for the federal funds rate to 3-1/2 to 3-3/4 percent; and Beth M. "
              "Hammack, Neel Kashkari, and Lorie K. Logan, who supported maintaining the target range for the federal "
              "funds rate but did not support inclusion of an easing bias in the statement at this time.")
    st = parse(fed_named(DECISION_CUT, voting))
    v = st.votes
    assert v.format == "named"
    assert v.voters_for == ("Jerome H. Powell", "John C. Williams", "Michael S. Barr", "Lisa D. Cook",
                            "Philip N. Jefferson", "Alberto G. Musalem")
    got = {d.name: d.preference.change_bps(PREV) for d in v.dissents}
    assert got == {"Stephen I. Miran": -50, "Jeffrey R. Schmid": 0, "Esther L. George": 0, "Eric S. Rosengren": 0,
                   "James Bullard": -50, "Beth M. Hammack": None, "Neel Kashkari": None, "Lorie K. Logan": None}
    texts = {d.name: d.text for d in v.dissents}
    assert texts["Stephen I. Miran"] == ("preferred to lower the target range for the federal funds rate by 1/2 "
                                         "percentage point at this meeting")
    assert texts["Lorie K. Logan"].startswith("supported maintaining") and texts["Lorie K. Logan"].endswith("at this time")
    assert v.voters == 14 and failed(st) == {"voting_line": "6 for, 8 against"}     # over 12: blocked


@pytest.mark.parametrize("clause", [
    # Sep 2020's two guidance dissents and Mar 2025's balance-sheet dissent: not about the rate (no bps)
    ("expects that it will be appropriate to maintain the current target range until the Committee is confident that "
    "the economy has weathered recent events, but prefers that the Committee retain greater policy rate flexibility "
    "beyond that point"),
    ("prefers that the Committee indicate that it expects to maintain the current target range until core inflation "
    "has reached 2 percent on a sustained basis"),
    ("supported no change for the federal funds target range but preferred to continue the current pace of decline in "
    "securities holdings"),
])
def test_a_dissent_that_isnt_about_the_rate_has_no_bps(clause):
    st = parse(fed_named("decided to keep the target range for the federal funds rate at 0 to 1/4 percent",
                         f"Voting for the monetary policy action were {VOTERS_12.rsplit(';', 1)[0]}. Voting against "
                         f"this action was Neel Kashkari, who {clause}."))
    (d,) = st.votes.dissents
    assert (d.name, d.preference.about_rate, d.preference.change_bps(PREV), d.text) == ("Neel Kashkari", False, None,
                                                                                        clause)


def test_a_rate_dissent_with_a_range_and_a_by_amount_must_agree():
    st = parse(fed_named("decided to raise the target range for the federal funds rate to 1/4 to 1/2 percent",
                         "Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice "
                         "Chair; Michelle W. Bowman; Lael Brainard; Esther L. George; Patrick Harker; Loretta J. "
                         "Mester; and Christopher J. Waller. Voting against this action was James Bullard, who "
                         "preferred at this meeting to raise the target range for the federal funds rate by 0.5 "
                         "percentage point to 1/2 to 3/4 percent. Patrick Harker voted as an alternate member at this "
                         "meeting."))
    (d,) = st.votes.dissents
    zero = fs.Range(Decimal(0), Decimal("0.25"))
    assert d.preference.change_bps(zero) == 50
    with pytest.raises(fs.StatementError, match="says \\+50 bps but names"):
        d.preference.change_bps(fs.Range(Decimal("0.25"), Decimal("0.5")))
    assert st.votes.alternates == ("Patrick Harker",) and not failed(st)
    keep = fs.Preference(True, "maintain", None, fs.Range(Decimal(1), Decimal("1.25")))
    with pytest.raises(fs.StatementError, match="not the previous"):
        keep.change_bps(zero)


def test_alternates_absent_members_and_mr_ms_references():
    st = parse(fed_named("decided to keep the target range for the federal funds rate at 0 to 1/4 percent",
                         "Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice "
                         "Chair; Michelle W. Bowman; Lael Brainard; Richard H. Clarida; Mary C. Daly; Patrick Harker; "
                         "Robert S. Kaplan; and Loretta J. Mester. Ms. Daly voted as an alternate member at this "
                         "meeting. Absent and not voting was Randal K. Quarles."))
    assert (st.votes.alternates, st.votes.absent, st.votes.voters) == (("Ms. Daly",), ("Randal K. Quarles",), 9)
    assert not failed(st)
    st = parse(fed_named("decided to keep the target range for the federal funds rate at 0 to 1/4 percent",
                         f"Voting for the monetary policy action were {VOTERS_12}. Ms. Daly voted as an alternate "
                         "member at this meeting."))
    assert failed(st) == {"alternates_voted": "alternates ['Ms. Daly']"}           # an alternate who isn't listed


# ---------------------------------------------------------------------------------------------- votes, tally format
def test_the_tally_format_from_june_2026():
    st = parse(fed_tally("9 – 3", "decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 "
                         "percent", "Voting against the monetary policy action were Beth M. Hammack, Neel Kashkari, and "
                         "Lorie K. Logan, who preferred to raise the target range for the federal funds rate by 1/4 "
                         "percentage point at this meeting."))
    v = st.votes
    assert (v.format, v.tally, v.voters_for, v.voters) == ("tally", (9, 3), (), 12)
    assert [d.name for d in v.dissents] == ["Beth M. Hammack", "Neel Kashkari", "Lorie K. Logan"]
    assert {d.preference.change_bps(fs.Range(Decimal("3.5"), Decimal("3.75"))) for d in v.dissents} == {25}
    assert 800 < st.text.char_count < 1200 and not failed(st)
    st = parse(fed_tally("12 – 0", "decided to raise the target range for the federal funds rate by 1/4 percentage "
                         "point to 3-3/4 to 4 percent"))
    assert (st.votes.tally, st.votes.dissents, failed(st)) == ((12, 0), (), {})


@pytest.mark.parametrize("tally, against, why", [
    ("10 – 2", ("Voting against the monetary policy action was Beth M. Hammack, who preferred to raise the target "
     "range for the federal funds rate by 1/4 percentage point at this meeting."), "approved 10–2; 1 named against"),
    ("11 – 0", None, None),
    ("9 – 0", None, None),
    ("12 – 1", ("Voting against the monetary policy action was Beth M. Hammack, who preferred to raise the target "
     "range for the federal funds rate by 1/4 percentage point at this meeting."), "approved 12–1; 1 named against"),
])
def test_the_tally_must_add_up(tally, against, why):
    st = parse(fed_tally(tally, "decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 "
                         "percent", against))
    assert failed(st) == ({"voting_line": why} if why else {})


# ---------------------------------------------------------------------------------------------- the checks
def test_the_checks_refuse_what_isnt_a_statement():
    named = fed_named("decided to maintain the target range for the federal funds rate at 4 to 4-1/4 percent",
                      f"Voting for the monetary policy action were {VOTERS_12}.")
    # a notation-vote page: another title, no decision sentence, no voting line
    st = parse([*FED_ECONOMY, ("Effective March 23, 2020, the Federal Open Market Committee directs the Desk to maintain "
                "the federal funds rate in a target range of 0 to 1/4 percent."), "x " * 300],
               title="Statement Regarding Monetary Policy Implementation")
    assert set(failed(st)) == {"statement_title", "target_range", "voting_line"}
    # a short body (500 characters or fewer: Anand, 23 Oct 2026)
    short = parse([("The Committee decided to maintain the target range for the federal funds rate at 4 to 4-1/4 "
                   "percent."), f"Voting for the monetary policy action were {VOTERS_12}."])
    assert short.text.char_count <= fs.MIN_CHARS and set(failed(short)) == {"statement_text"}
    # the page's date isn't the decision date
    assert set(failed(parse(named), date(2026, 9, 17))) == {"statement_date"}
    # a range 50 bps wide; two decision sentences
    wide = named[:2] + [("In support of its goals, the Committee decided to maintain the target range for the federal "
                        "funds rate at 4 to 4-1/2 percent.")] + named[3:]
    assert set(failed(parse(wide))) == {"target_range"}
    two = named[:3] + [("The Committee also decided to lower the target range for the federal funds rate to 3-3/4 to 4 "
                       "percent.")] + named[3:]
    assert failed(parse(two))["target_range"].startswith("2 decision sentences")
    # no voting line; a voting line with 7 voters; both formats at once
    assert set(failed(parse(named[:-1]))) == {"voting_line"}
    seven = named[:-1] + [("Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice "
                          "Chair; Michael S. Barr; Michelle W. Bowman; Lisa D. Cook; Philip N. Jefferson; and "
                          "Christopher J. Waller.")]
    assert failed(parse(seven)) == {"voting_line": "7 for, 0 against"}
    both = named + ["The Federal Open Market Committee approved the following statement for release by a 12 – 0 vote:"]
    assert "both" in failed(parse(both))["voting_line"]
    # not a Fed page at all
    with pytest.raises(fs.StatementError, match="div#article"):
        fs.parse(b"<html><body><p>Not found</p></body></html>", URL)


def test_ranges_in_a_stored_text():
    st = parse(fed_named(DECISION_CUT, f"Voting for the monetary policy action were {VOTERS_12}."))
    assert fs.ranges_in(st.text.text) == [fs.Range(Decimal("3.75"), Decimal(4))]


# ---------------------------------------------------------------------------------------------- the RSS feed
def test_the_feed_is_a_trigger_for_statement_items_only():
    base = "https://www.federalreserve.gov/newsevents/pressreleases/"
    feed = fed_rss(("Minutes of the Board&#39;s discount rate meetings on August 17, 2026", base + "monetary20261013a.htm",
                    "Tue, 13 Oct 2026 18:00:00 GMT"),
                   ("Federal Reserve issues FOMC statement", base + "monetary20260916a.htm", "Wed, 16 Sep 2026 18:00:00 GMT"),
                   (("Federal Reserve Board and Federal Open Market Committee release economic projections from the "
                    "September 15-16 FOMC meeting"), base + "monetary20260916b.htm", "Wed, 16 Sep 2026 18:00:00 GMT"),
                   ("Minutes of the Federal Open Market Committee, July 28–29, 2026", base + "monetary20260819a.htm",
                    "Wed, 19 Aug 2026 18:00:00 GMT"))
    items = fs.parse_rss(feed.encode())
    assert [i.is_statement for i in items] == [False, True, False, False]
    assert fs.statement_date(items[1].link) == DAY and items[1].published.hour == 18
    with pytest.raises(fs.StatementError):
        fs.parse_rss(b"<html><body>maintenance</body></html>")


# ---------------------------------------------------------------------------------------------- the saved statements
FED = FIXTURES / "fed"
NOTATION = {"20191011", "20200323", "20200331", "20200827", "20250822"}


@pytest.mark.skipif(not (FED / "monetary20260916a.htm").exists(),
                    reason="the saved pages aren't checked out (data/fixtures: the private fixtures repo)")
def test_every_saved_statement_passes_and_every_notation_page_fails():
    """The 64 decision statements saved on 23 Oct 2026 (the Dec 2018 baseline and 63 decisions to Sep 2026) pass
    every page check, with the changes, dissents and formats Spec 01 §5.1 names; the 5 notation-vote pages fail."""
    pages = sorted(FED.glob("monetary*a.htm"))
    assert len(pages) == 69
    prev, out = None, {}
    for p in pages:
        d8 = re.search(r"(\d{8})", p.name)[1]
        day = datetime.strptime(d8, "%Y%m%d").date()
        st = fs.parse(p.read_bytes(), fs.SITE + "/newsevents/pressreleases/" + p.name)
        bad = {r.name for r in fs.checks(st, day) if not r.passed}
        if d8 in NOTATION:
            assert {"target_range", "voting_line"} <= bad, d8
            continue
        assert not bad, (d8, bad)
        d = st.decision
        change = fs.bps(d.range.upper - prev.upper) if prev else None
        if d.stated_bps is not None:
            assert d.stated_bps == change, d8
        dissents = {x.name: x.preference.change_bps(prev) for x in st.votes.dissents} if prev else {}
        out[d8] = (st, change, dissents)
        prev = d.range
    assert len(out) == 64
    assert {k for k, v in out.items() if v[0].votes.format == "tally"} == {"20260617", "20260729", "20260916"}
    assert out["20181219"][0].decision.range == fs.Range(Decimal("2.25"), Decimal("2.5"))
    assert [out[k][1] for k in ("20200303", "20200315", "20220615", "20260916")] == [-50, -100, 75, 25]
    assert out["20200916"][2] == {"Robert S. Kaplan": None, "Neel Kashkari": None}      # Sep 2020: no bps
    assert out["20190918"][2] == {"James Bullard": -50, "Esther L. George": 0, "Eric S. Rosengren": 0}
    assert out["20200315"][2] == {"Loretta J. Mester": -50}
    assert out["20260429"][2] == {"Stephen I. Miran": -25, "Beth M. Hammack": None, "Neel Kashkari": None,
                                  "Lorie K. Logan": None}
    assert out["20260729"][2] == {"Beth M. Hammack": 25, "Neel Kashkari": 25, "Lorie K. Logan": 25}
    assert out["20250730"][0].votes.absent == ("Adriana D. Kugler",) and out["20250730"][0].votes.voters == 11
    assert sum(len(v[2]) for v in out.values()) == 34       # 34 dissenters at 19 meetings
    assert min(v[0].text.char_count for v in out.values()) == 782                   # 3 Mar 2020


@pytest.mark.skipif(not (FED / "monetary20260916a.htm").exists(),
                    reason="the saved pages aren't checked out (data/fixtures: the private fixtures repo)")
def test_the_roster_names_every_2025_26_voter_with_the_right_status():
    """config/members.yaml against every named statement from the 2025 organizational meeting on: every voter is on
    the roster with a term covering the meeting, voting members as voters and alternates as alternates; 12 voters each
    meeting date."""
    roster = members.load()
    for p in sorted(FED.glob("monetary202[56]*a.htm")):
        d8 = re.search(r"(\d{8})", p.name)[1]
        if d8 in NOTATION:
            continue
        day = datetime.strptime(d8, "%Y%m%d").date()
        st = fs.parse(p.read_bytes(), fs.SITE + "/newsevents/pressreleases/" + p.name)
        assert len(roster.voters_on("FED", day)) == 12, d8
        if st.votes.format != "named":
            continue
        for name in st.votes.voters_for + tuple(x.name for x in st.votes.dissents):
            m = roster.match("FED", name)
            assert m is not None, (d8, name)
            t = m.term_on(day)
            alternate = any(fs._same(a, name) for a in st.votes.alternates)
            assert t is not None and t.is_voter != alternate, (d8, name)
        for name in st.votes.absent:
            assert roster.match("FED", name).term_on(day).is_voter, (d8, name)
