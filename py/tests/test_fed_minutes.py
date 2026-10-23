"""FOMC minutes (Prompt 01 B5 part 2; Spec 01 §1.1, §2 "Fed votes"): the parser and its checks on synthetic pages in the
saved template (policy_docs.fed_minutes_page), then on the 62 minutes saved in data/fixtures/fed
(the private fixtures repo) when they're on this machine."""
import os
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from policy_docs import fed_minutes_page

from ft.policy import fed_minutes as fm

URL = "https://www.federalreserve.gov/monetarypolicy/fomcminutes20400314.htm"
DAYS = {"days": "March 13-14, 2040", "held": ["Tuesday, March 13, 2040", "Wednesday, March 14, 2040"]}
TWELVE = ["Ada B. Lane", "Ben C. Ford", "Cara D. Hill", "Dev E. Rao", "Eva F. Shaw", "Finn G. Park", "Gia H. Ross",
          "Hal I. Webb", "Ida J. Kerr", "Jon K. Moss", "Kai L. Burt", "Lea M. Dunn"]
FOR = "<strong>Voting for this action:</strong> "
AGAINST = "<strong>Voting against this action:</strong> "


def listed(names: list[str], roles: bool = False) -> str:
    if roles:
        parts = [f"{names[0]}, Chair", f"{names[1]}, Vice Chair", *names[2:]]
        return "; ".join(parts[:-1]) + f"; and {parts[-1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def page(voting: list[str], **kw) -> bytes:
    return fed_minutes_page(**{**DAYS, **kw}, voting=voting).encode()


def parse(voting: list[str], **kw) -> fm.Minutes:
    return fm.parse(page(voting, **kw), URL)


def failed(mn: fm.Minutes, d: date = date(2040, 3, 14)) -> set[str]:
    return {r.name for r in fm.checks(mn, d) if not r.passed}


def test_a_unanimous_vote_with_roles_reads_every_voter_and_passes():
    mn = parse([FOR + listed(TWELVE, roles=True) + ".", AGAINST + "None."])
    assert mn.title == fm.TITLE and mn.external_key == "/monetarypolicy/fomcminutes20400314.htm"
    assert mn.meeting_days == (date(2040, 3, 13), date(2040, 3, 14))
    v = mn.vote
    assert v.voters_for == tuple(TWELVE) and v.against == () and v.voters == 12 and v.held_on is None
    s, e = v.for_sentence
    assert mn.text.text[s:e].startswith("Voting for this action: Ada B. Lane, Chair; Ben C. Ford, Vice Chair;")
    assert mn.text.text.endswith("Ada B. Clerk\nSecretary")                  # the footnotes below the rule aren't text
    assert "Return to text" not in mn.text.text and mn.text.extractor_version == "html-1/fed_minutes"
    assert failed(mn) == set()


@pytest.mark.parametrize("against, names", [
    ("Kai L. Burt and Lea M. Dunn.", ("Kai L. Burt", "Lea M. Dunn")),
    (("Kai L. Burt, who preferred to lower the target range for the federal funds rate by 1/4 percentage point at this "
      "meeting, and Lea M. Dunn, who preferred no change to the target range for the federal funds rate at this "
      "meeting."), ("Kai L. Burt", "Lea M. Dunn")),
    # Apr 2026's shape: no comma before the second group
    (("Kai L. Burt, who preferred to lower the target range for the federal funds rate by 1/4 percentage point at this "
      "meeting and Jon K. Moss and Lea M. Dunn, who supported maintaining the target range but did not support the "
      "Committee's guidance."), ("Kai L. Burt", "Jon K. Moss", "Lea M. Dunn")),
])
def test_dissenters_are_read_with_or_without_their_clauses(against, names):
    voters = [n for n in TWELVE if n not in names]
    mn = parse([FOR + listed(voters) + ".", AGAINST + against])
    assert mn.vote.against == names and mn.vote.voters == 12 and failed(mn) == set()


def test_a_missing_against_line_alternates_and_absences():
    """4 May 2022 has no "Voting against" line; an alternate votes in a member's place; an absent member is named."""
    voters = TWELVE[:10] + ["Max N. Vale"]
    mn = parse([FOR + listed(voters) + ".", "Max N. Vale voted as an alternate member at this meeting.",
                "Absent and not voting: Lea M. Dunn"])
    v = mn.vote
    assert (v.against, v.alternates, v.absent, v.voters) == ((), ("Max N. Vale",), ("Lea M. Dunn",), 11)
    assert failed(mn) == set()
    mn = parse([FOR + listed(TWELVE[:11]) + ".", "Ms. Vale voted as alternate member at this meeting."])
    assert failed(mn) == {"minutes_alternates"}                               # an alternate who isn't among the voters
    assert fm.same("Ms. Vale", "Max N. Vale") and fm.same("President Vale", "Max N. Vale")
    assert not fm.same("Ms. Vale", "Lea M. Dunn")


def test_a_closing_section_holds_another_meetings_vote():
    """The 15 Mar 2020 minutes close with "Videoconference meeting of March 2": its vote is that meeting's."""
    closing = ("<p><strong>Videoconference meeting of March 2</strong><br />\nA joint meeting of the Federal Open Market "
               "Committee and the Board of Governors was held by videoconference on March 2, 2040, at 7:30 p.m.</p>"
               f"<p>{FOR}{listed(TWELVE[:10])}.</p>")
    mn = parse([FOR + listed(TWELVE[:9]) + ".", AGAINST + "Jon K. Moss"], closing=closing)
    own, other = mn.votes
    assert mn.vote is own and own.against == ("Jon K. Moss",) and own.voters == 10
    assert (other.held_on, other.heading, other.voters) == (date(2040, 3, 2), "Videoconference meeting of March 2", 10)
    assert failed(mn) == set()
    # A closing section without a vote (Oct 2019's "Videoconference meeting of October 4, 2019") adds none.
    closing = ("<p><strong>Videoconference meeting of October 4, 2039</strong><br />\nThe Committee met by "
               "videoconference on October 4, 2039, to review developments in money markets.</p>")
    assert len(parse([FOR + listed(TWELVE) + "."], closing=closing).votes) == 1


@pytest.mark.parametrize("voting, kw, bad", [
    ([FOR + listed(TWELVE) + "."], {"title": "Minutes of the Board's discount rate meetings"}, {"minutes_title"}),
    ([FOR + listed(TWELVE) + "."], {"body_paragraphs": 2}, {"minutes_text"}),
    ([FOR + listed(TWELVE[:7]) + "."], {}, {"minutes_vote"}),                                # 7 voters
    ([FOR + listed(TWELVE) + ".", FOR + listed(TWELVE) + "."], {}, {"minutes_vote"}),        # two votes
    (["The Committee voted unanimously."], {}, {"minutes_vote"}),                            # no vote
    ([FOR + listed(TWELVE) + ".", AGAINST + "Lea M. Dunn."], {}, {"minutes_vote"}),          # named for and against
    ([FOR + "the members present."], {}, {"minutes_vote"}),                                  # not a list of names
    ([AGAINST + "None.", FOR + listed(TWELVE) + "."], {}, {"minutes_vote"}),                 # against before for
])
def test_minutes_that_dont_read_right_fail_their_checks(voting, kw, bad):
    assert failed(parse(voting, **kw)) == bad


def test_the_meeting_days_must_end_on_the_decision_date():
    mn = parse([FOR + listed(TWELVE) + "."])
    assert failed(mn, date(2040, 3, 15)) == {"minutes_date"}
    mn = parse([FOR + listed(TWELVE) + "."], days="March 15, 2040", held=["Sunday, March 15, 2040"])
    assert mn.meeting_days == (date(2040, 3, 15),) and failed(mn, date(2040, 3, 15)) == set()


def test_a_page_that_isnt_minutes_is_refused():
    with pytest.raises(fm.MinutesError):
        fm.parse(b"<html><body><p>Maintenance</p></body></html>", URL)
    with pytest.raises(fm.MinutesError):
        fm.parse(b'<html><body><div id="article"><h3>Minutes</h3></div></body></html>', URL)


# ---------------------------------------------------------------------------------------------- the saved minutes
FIXTURES = Path(os.environ.get("FT_FIXTURES", Path(__file__).resolve().parents[2] / "data" / "fixtures"))
FED = FIXTURES / "fed"


@pytest.mark.skipif(not (FED / "fomcminutes20260916.htm").exists(),
                    reason="the saved pages aren't checked out (data/fixtures: the private fixtures repo)")
def test_every_saved_minutes_passes_with_the_votes_the_statements_name():
    """The 62 minutes saved 23 Oct 2026 (Jan 2019 to Sep 2026) pass every check, with the votes Spec 01 §5.1 names."""
    pages = sorted(FED.glob("fomcminutes*.htm"))
    assert len(pages) == 62
    out = {}
    for p in pages:
        d8 = re.search(r"(\d{8})", p.name)[1]
        mn = fm.parse(p.read_bytes(), "https://www.federalreserve.gov/monetarypolicy/" + p.name)
        bad = [r for r in fm.checks(mn, datetime.strptime(d8, "%Y%m%d").date()) if not r.passed]
        assert not bad, (d8, bad)
        out[d8] = mn
    assert {d8: mn.vote.voters for d8, mn in out.items() if mn.vote.voters < 10} == {
        "20220126": 9, "20220316": 9, "20220504": 9}                                    # three Board seats empty
    assert out["20200916"].vote.against == ("Robert S. Kaplan", "Neel Kashkari")
    assert out["20260729"].vote.against == ("Beth M. Hammack", "Neel Kashkari", "Lorie K. Logan")
    assert out["20260617"].vote.voters_for[0] == "Kevin Warsh" and out["20260617"].vote.voters == 12
    assert out["20260429"].vote.against == ("Stephen I. Miran", "Beth M. Hammack", "Neel Kashkari", "Lorie K. Logan")
    assert out["20220504"].vote.against_sentence is None and out["20220504"].vote.alternates == ("Patrick Harker",)
    assert out["20250730"].vote.absent == ("Adriana D. Kugler",)
    mar = out["20200315"]                                 # the 3 Mar 2020 cut's vote, in the 15 Mar 2020 minutes
    assert [(v.held_on, v.voters, v.against) for v in mar.votes] == [
        (None, 10, ("Loretta J. Mester",)), (date(2020, 3, 2), 10, ())]
    assert len(out["20191030"].votes) == 1                # the 4 Oct 2019 call set no rate
    assert min(mn.text.char_count for mn in out.values()) == 34801                       # 16 Sep 2026
