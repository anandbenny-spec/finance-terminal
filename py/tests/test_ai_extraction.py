"""Module 1's structured extractions (Prompt 01 B7 part 2; Spec 01 §3.2-§3.3; Architecture 03 §1.2, §1.4): the requests
on the JSON route, and the checks on `stance_extraction` and `projection_extraction` answers, including the
cross-check with B6's parse (Anand, 24 Oct 2026, issue #16). No network and no database: answers are written here."""
import json
from datetime import date
from decimal import Decimal

import pytest
from policy_docs import mpc_old, mpc_title, rbi_page

from ft.ai import extraction
from ft.ai.client import PROMPTS, load_prompt, load_schema
from ft.ai.extraction import Parsed
from ft.ai.summaries import Doc
from ft.policy import rbi_resolution as rr

URL = "https://rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=91234"
STANCE = "The MPC also decided to change the monetary policy stance from calibrated tightening to neutral."
DISSENT = "Dr. Ben Iyer voted to retain the stance of calibrated tightening."
PROJECTIONS = "CPI inflation is projected at 4.4 per cent for 2040-41: Q2 4.6 per cent; Q3 4.0 per cent."
NEGATIVE = "Real GDP growth is projected at (-)7.5 per cent for 2040-41, and at 0.5 per cent in the last quarter."


def resolution(*, stance: str = STANCE, votes=(DISSENT,), projections=(PROJECTIONS, NEGATIVE)) -> rr.Resolution:
    body = mpc_old(decision="reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis "
                            "points from 6.5 per cent to 6.25 per cent with immediate effect.",
                   consequently="Consequently, the reverse repo rate under the LAF stands adjusted to 6.0 per cent, and "
                                "the marginal standing facility (MSF) rate and the Bank Rate to 6.5 per cent.",
                   stance=stance, votes=list(votes), projections=list(projections))
    return rr.parse(rbi_page(body, title=mpc_title("May 2 to 4, 2040"), day="May 04, 2040").encode(), URL)


R = resolution()
TEXT = R.text.text
DOC = Doc(41, "mpc_resolution", "MPC resolution", "MPC resolution, 4 May 2040", "RBI", date(2040, 5, 4), TEXT, 7,
          date(2040, 5, 4), 99, URL)


NEUTRAL, NOTHING = Parsed(stance="neutral"), Parsed()


def stance(answer, parsed=NEUTRAL):
    return extraction.check_stance(answer, TEXT, DOC.document_id, parsed)


def proj(answer, sentences=None, parsed=NOTHING):
    return extraction.check_projections(answer, TEXT, DOC.document_id,
                                        R.unparsed if sentences is None else sentences, parsed)


def item(variable, label, start, end, quote, value=None, low=None, high=None) -> dict:
    return {"variable": variable, "target_label": label, "target_start": start, "target_end": end, "value": value,
            "low": low, "high": high, "quote": quote}


# ---------------------------------------------------------------------------------------------- prompts and requests
def test_the_schemas_use_only_what_structured_outputs_accept_and_the_stance_enum_is_the_vocabulary():
    banned = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
              "maxItems", "pattern"}

    def walk(node):
        if isinstance(node, dict):
            assert not banned & node.keys(), node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False and set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for task in extraction.TASKS:
        walk(load_schema(task))
        assert load_prompt(task).version == "v1" and not load_prompt(task).text.startswith("<!--")
    assert load_schema("stance_extraction")["properties"]["stance"]["enum"] == [*rr.VOCABULARY, "other"]
    assert (PROMPTS / "stance_extraction" / "schema.json").exists()


def test_the_requests_take_the_json_route_without_citations_and_name_the_unread_sentences():
    s = extraction.stance_request(DOC, load_prompt("stance_extraction"), load_schema("stance_extraction"))
    p = s.params()
    assert p["output_config"] == {"effort": "low", "format": {"type": "json_schema",
                                                              "schema": load_schema("stance_extraction")}}
    assert p["max_tokens"] == 4000 and "citations" not in p["messages"][0]["content"][0]
    assert p["messages"][0]["content"][0]["source"]["data"] == TEXT
    assert not {"temperature", "top_p", "top_k"} & p.keys() and "cache_control" not in s.params(batch=True)
    assert len(R.unparsed) == 2
    pr = extraction.projection_request(DOC, R.unparsed, load_prompt("projection_extraction"),
                                       load_schema("projection_extraction"))
    instruction = pr.params()["messages"][0]["content"][1]["text"]
    assert f'1. "{PROJECTIONS}"' in instruction and f'2. "{NEGATIVE}"' in instruction
    retry = extraction.stance_request(DOC, load_prompt("stance_extraction"), load_schema("stance_extraction"),
                                      note="the quote isn't in the resolution exactly once.")
    assert retry.input_sha256 != s.input_sha256 and retry.params()["messages"][0]["content"][-1]["text"].startswith(
        "Your previous answer to this request couldn't be published: the quote")


def test_the_text_only_reread_finds_what_the_page_parse_found():
    for body in (R, resolution(projections=(PROJECTIONS,)), resolution(projections=())):
        assert rr.read_projections(body.text.text) == (body.projections, body.unparsed)
    assert [p.target_label for p in R.projections] == ["FY 2040-41", "FY 2040-41"]


# ---------------------------------------------------------------------------------------------- stance
def test_a_stance_quoting_the_decision_is_published_with_its_span():
    c = stance({"stance": "neutral", "quote": STANCE})
    assert c.ok and c.hold is None and c.problems == []
    (path, cited), = c.citations
    assert path == "/quote" and TEXT[cited.start:cited.end] == STANCE
    # Spacing and curly quotes don't matter; the stored quote is our text's.
    c = stance({"stance": "neutral", "quote": STANCE.replace(" stance ", "  stance\n").replace("MPC", "MPC")})
    assert c.ok and c.items[0]["quote"] == STANCE
    assert extraction.content(c, attempt=1, reply=None)[1:] == (STANCE, "neutral")


def test_other_is_held_for_the_vocabulary_to_be_extended():
    c = stance({"stance": "other", "quote": "growth-supportive"})
    assert not c.ok and c.problems == [] and c.hold.startswith("other: the stance isn't in the vocabulary")
    assert c.citations == [] and c.reasons() == [c.hold]


def test_a_quote_that_isnt_found_once_or_doesnt_state_the_label_is_retried():
    c = stance({"stance": "neutral", "quote": "The MPC decided to change the stance to neutral."})
    assert c.problems and "isn't in the resolution exactly once" in c.problems[0] and c.hold is None
    # The dissent names calibrated tightening, not neutral: the cited passage must state the stance given.
    c = stance({"stance": "neutral", "quote": DISSENT})
    assert c.problems and "doesn't state the stance 'neutral'" in c.problems[0] and c.mapping["unread"] == 1
    assert "Copy every quote exactly" in extraction._note(c.note())
    for bad in (None, [], {"stance": "neutral"}, {"stance": "hawkish", "quote": STANCE}):
        assert stance(bad).problems and not stance(bad).ok


def test_the_cross_check_holds_a_disagreement_with_the_parsed_stance():
    # The AI reads the dissent's label from the dissent: it states it, but the MPC decided neutral (Anand, 24 Oct 2026).
    c = stance({"stance": "calibrated tightening", "quote": DISSENT})
    assert not c.ok and c.problems == [] and c.hold == ("mismatch: the AI read 'calibrated tightening', the "
                                                         "resolution's parsed stance is 'neutral'")
    assert R.stance.label == "neutral"
    # Where the parser read none, the spec's rules decide alone; an unreadable parse is held, never published.
    assert stance({"stance": "calibrated tightening", "quote": DISSENT}, Parsed()).ok
    held = stance({"stance": "neutral", "quote": STANCE}, Parsed(error="its raw file no longer reads as the stored text"))
    assert held.hold.startswith("unchecked: the resolution's parsed stance couldn't be read")


# ---------------------------------------------------------------------------------------------- projections
GOOD = [item("cpi", "FY 2040-41", "2040-04-01", "2041-03-31", "4.4 per cent for 2040-41", value=4.4),
        item("cpi", "Q2 2040-41", "2040-07-01", "2040-09-30", "Q2 4.6 per cent", value=4.6),
        item("cpi", "Q3 2040-41", "2040-10-01", "2040-12-31", "Q3 4.0 per cent", value=4.0),
        item("gdp", "FY 2040-41", "2040-04-01", "2041-03-31", "(-)7.5 per cent for 2040-41", value=-7.5)]


def test_projections_in_the_unread_sentences_are_stored_as_the_resolution_writes_them():
    c = proj({"projections": GOOD})
    assert c.ok, c.problems
    assert [(i["variable"], i["target_label"], i["value"]) for i in c.items] == [
        ("cpi", "FY 2040-41", "4.4"), ("cpi", "Q2 2040-41", "4.6"), ("cpi", "Q3 2040-41", "4.0"),
        ("gdp", "FY 2040-41", "-7.5")]
    assert [p for p, _ in c.citations] == ["/projections/0", "/projections/1", "/projections/2", "/projections/3"]
    assert all(TEXT[x.start:x.end] == i["quote"] for (_, x), i in zip(c.citations, c.items, strict=True))
    # Agreeing with what the regex stored is fine; a different figure for a target it stored is held.
    stored = {("cpi", date(2040, 4, 1), date(2041, 3, 31)): (Decimal("4.4"), None, None)}
    assert proj({"projections": GOOD}, parsed=Parsed(stored=stored)).ok
    stored[("gdp", date(2040, 4, 1), date(2041, 3, 31))] = (Decimal("-7.0"), None, None)
    held = proj({"projections": GOOD}, parsed=Parsed(stored=stored))
    assert not held.ok and held.problems == [] and held.hold == ("conflict: gdp FY 2040-41: the AI read -7.5, the regex "
                                                                 "stored -7.0")


@pytest.mark.parametrize("change, problem", [
    ({"quote": "0.5 per cent in the last quarter", "target_label": "Q4 2040-41", "target_start": "2041-01-01",
      "target_end": "2041-03-31", "value": 0.5}, "doesn't state exactly one figure for each period"),
    ({"value": 4.7}, "doesn't state Q2 2040-41 at 4.7 per cent"),
    ({"target_end": "2040-09-29"}, "Q2 2040-41 runs 2040-07-01 to 2040-09-30"),
    ({"target_label": "Q2 FY41"}, "isn't written 'Q2 2026-27'"),
    ({"low": 4.5, "high": 4.7}, "one value, or a range"),
    ({"quote": "Q2 4.7 per cent"}, "isn't in the resolution exactly once"),
    ({"quote": "per cent"}, "isn't in the resolution exactly once"),
    # A bare quarter's year is the one the resolution gives it in context, never another (review of #45).
    ({"target_label": "Q2 2019-20", "target_start": "2019-07-01", "target_end": "2019-09-30"},
     "its Q2 is in 2040-41 where the resolution states it, not 2019-20"),
    # A CPI sentence's figure can't become GDP growth (review of #45).
    ({"variable": "gdp"}, "its sentence states CPI inflation there, not Real GDP growth"),
])
def test_a_projection_whose_quote_period_or_figure_doesnt_check_is_retried(change, problem):
    c = proj({"projections": [GOOD[0], {**GOOD[1], **change}]})
    assert not c.ok and c.hold is None and len(c.problems) == 1 and problem in c.problems[0], c.problems
    assert c.problems[0].startswith("projection 2 (")


def test_the_fiscal_year_and_the_variable_are_read_in_context_as_the_regex_reads_them():
    text = ("Real GDP growth for 2040-41 is projected at 7.0 per cent. Core inflation is projected at 4.2 per cent for "
            "2040-41.\n\nCPI inflation is projected at 4.5 per cent, with Q1 at 4.9 per cent and 2041-42 at 4.1 per "
            "cent.")
    a, b = text.index("Core"), text.index("CPI")
    first, core, third = (0, a - 1), (a, text.index("\n\n")), (b, len(text))
    assert text[first[0]:first[1]].endswith("per cent.") and text[core[0]:core[1]].endswith("2040-41.")
    # A bare quarter takes the year named before it in its paragraph, else the next one in its sentence.
    assert [(k, y) for _, _, k, y in rr.period_years(text, first)] == [("FY", "2040-41")]
    assert [(k, y) for _, _, k, y in rr.period_years(text, third)] == [("Q1", "2041-42"), ("FY", "2041-42")]
    para = "Real GDP growth for 2040-41 is projected at 7.0 per cent. It is placed at 6.8 per cent in Q2."
    assert [(k, y) for _, _, k, y in rr.period_years(para, (58, len(para)))] == [("Q2", "2040-41")]
    assert rr.subject_at(text, first, 55) == "gdp" and rr.subject_at(text, third, len(text)) == "cpi"
    assert rr.subject_at(text, core, core[1]) is None                        # core inflation isn't headline CPI
    c = extraction.check_projections({"projections": [item("cpi", "FY 2040-41", "2040-04-01", "2041-03-31",
                                                           "4.2 per cent for 2040-41", value=4.2)]},
                                     text, 1, (core,), Parsed())
    assert "neither CPI inflation nor real GDP growth" in c.problems[0]


def test_a_quote_outside_the_unread_sentences_an_empty_list_or_a_repeated_target_is_retried():
    elsewhere = "Liquidity in the banking system stayed in surplus"
    assert elsewhere in TEXT
    c = proj({"projections": [GOOD[1]]}, sentences=((0, TEXT.index(elsewhere)),))
    assert c.problems and "isn't inside one of the sentences given" in c.problems[0] and c.mapping["outside"] == 1
    assert "no projection is listed" in proj({"projections": []}).problems[0]
    assert "listed twice" in proj({"projections": [GOOD[1], GOOD[1]]}).problems[0]
    assert "isn't the schema's JSON" in proj(None).problems[0] and proj({"projections": [7]}).problems


def test_periods_and_figures_pair_in_order_so_respectively_cant_swap_them():
    text = ("Taking all these factors into consideration, CPI inflation for Q1:2041-42 and Q2 is placed at 5.0 per "
            "cent and 5.4 per cent, as the case may be, and real GDP growth in the range of 6.2-6.5 per cent in H1.")
    whole = ((0, len(text)),)
    q = "Q1:2041-42 and Q2 is placed at 5.0 per cent and 5.4 per cent"
    q1 = item("cpi", "Q1 2041-42", "2041-04-01", "2041-06-30", q, value=5.0)
    q2 = item("cpi", "Q2 2041-42", "2041-07-01", "2041-09-30", q, value=5.4)
    h1 = item("gdp", "H1 2041-42", "2041-04-01", "2041-09-30", "6.2-6.5 per cent in H1", low=6.2, high=6.5)
    ok = extraction.check_projections({"projections": [q1, q2, h1]}, text, 1, whole, Parsed())
    assert ok.ok, ok.problems
    assert [(i["value"], i["low"], i["high"]) for i in ok.items] == [("5.0", None, None), ("5.4", None, None),
                                                                     (None, "6.2", "6.5")]
    swapped = extraction.check_projections({"projections": [{**q1, "value": 5.4}, {**q2, "value": 5.0}]}, text, 1,
                                           whole, Parsed())
    assert len(swapped.problems) == 2 and "doesn't state Q1 2041-42 at 5.4 per cent" in swapped.problems[0]
    wrong_year = extraction.check_projections({"projections": [{**q1, "target_label": "Q1 2040-41",
                                                                "target_start": "2040-04-01",
                                                                "target_end": "2040-06-30"}]}, text, 1, whole, Parsed())
    assert "doesn't state Q1 2040-41" in wrong_year.problems[0]
    assert rr.quoted_pairs("in the range of 26.2 to 8.3 per cent in Q1") == [("Q1", None, (Decimal("8.3"),
                                                                                            Decimal("26.2")))]
    assert rr.quoted_pairs("revised from 7.2 per cent in the April policy to 7.0 per cent for 2040-41") == [
        ("FY", "2040-41", Decimal("7.0"))]                       # an earlier vintage is set aside, as the regex does
    assert rr.target_dates("H2 2040-41") == (date(2040, 10, 1), date(2041, 3, 31)) and rr.target_dates("Q5 2040-41") \
        is None and rr.target_dates("FY 2040-42") is None


class StubDB:
    """Enough of a connection for AiBatch._work: the diffable document types, and nothing else stored."""

    def execute(self, sql, args=None):
        rows = [("fomc_statement",), ("mpc_resolution",)] if "diffable" in sql else []
        return type("Rows", (), {"fetchall": lambda self: rows, "fetchone": lambda self: None})()


class RawFiles:
    def __init__(self, data: bytes | Exception):
        self.data, self.reads = data, 0

    def read(self, raw_file_id: int) -> bytes:
        self.reads += 1
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def test_only_mpc_resolutions_get_extraction_targets_so_no_fed_stance_exists():
    from ft.jobs.tasks.ai_batch import AiBatch
    fed = Doc(40, "fomc_statement", "FOMC statement", "FOMC statement, 4 May 2040", "Fed", date(2040, 5, 4),
              "The Committee decided to maintain the target range. Its stance remains neutral and accommodative.", 6,
              date(2040, 5, 4), 98, "https://www.federalreserve.gov/x.htm")
    works = AiBatch()._work(StubDB(), [fed, DOC])
    assert [(w.task, w.doc.document_id) for w in works] == [
        ("doc_summary", 40), ("doc_summary", 41), ("stance_extraction", 41), ("projection_extraction", 41)]
    assert works[-1].sentences == R.unparsed
    # A resolution the regex reads in full gets a stance and no projection request.
    full = resolution(projections=("Real GDP growth for 2040-41 is projected at 7.1 per cent.",))
    assert full.unparsed == ()
    whole = Doc(42, "mpc_resolution", "MPC resolution", "t", "RBI", None, full.text.text, 8, date(2040, 5, 4), 1, URL)
    assert [w.task for w in AiBatch()._work(StubDB(), [whole])] == ["doc_summary", "stance_extraction"]


def test_the_cross_check_reads_the_stance_from_the_raw_file_once_and_holds_what_it_cant_read():
    from ft.jobs.tasks.ai_batch import AiBatch
    raw = rbi_page(mpc_old(decision="keep the policy repo rate unchanged at 6.5 per cent.", consequently="x",
                           stance=STANCE, votes=[]), title=mpc_title("May 2 to 4, 2040"), day="May 04, 2040").encode()
    parsed = rr.parse(raw, URL)
    doc = Doc(43, "mpc_resolution", "MPC resolution", "t", "RBI", None, parsed.text.text, 9, date(2040, 5, 4), 5, URL)
    t = AiBatch()
    t._fetcher = files = RawFiles(raw)
    assert t._parsed_stance(doc) == Parsed(stance="neutral") and t._parsed_stance(doc) == Parsed(stance="neutral")
    assert files.reads == 1                                                     # read once per run
    t._parsed_stances = {}
    changed = Doc(*[*doc.__dict__.values()][:6], doc.text + " ", *[*doc.__dict__.values()][7:])
    assert t._parsed_stance(changed).error == "its raw file no longer reads as the stored text"
    t._fetcher = RawFiles(b"<html><body>no press release here</body></html>")
    t._parsed_stances = {}
    assert "ResolutionError" in t._parsed_stance(doc).error
    assert AiBatch()._parsed_stance(doc).error == "the resolution has no stored raw file"   # no fetcher outside a run
    # Storage down while a batch is collected: held 'unchecked' and read again later, never a failed run (review of
    # #45); real time reads it before sending, strictly, so its poll fails and retries instead.
    t._fetcher, t._parsed_stances = RawFiles(OSError("storage unreachable")), {}
    assert t._parsed_stance(doc).error == "its raw file couldn't be read (OSError: storage unreachable)"
    assert t._parsed_stances == {}                                            # not kept: the next look reads again
    with pytest.raises(OSError):
        t._parsed_stance(doc, strict=True)
    held = extraction.check_stance({"stance": "neutral", "quote": STANCE}, parsed.text.text, 43, t._parsed_stance(doc))
    assert held.hold.startswith("unchecked: the resolution's parsed stance couldn't be read")


def test_content_keeps_the_answer_and_why_it_wasnt_published():
    c = proj({"projections": [GOOD[1], {**GOOD[2], "value": 4.1}]})
    doc, body, label = extraction.content(c, attempt=2, reply=None)
    assert (body, label) == (None, None) and doc["attempt"] == 2 and doc["problems"] == c.problems
    assert doc["answer"]["projections"][1]["value"] == 4.1 and json.dumps(doc)
