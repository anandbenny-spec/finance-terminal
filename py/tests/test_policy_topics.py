"""Key changes' topic map (ft.policy.topics; config/topics.yaml): sentences take topics from anchors (FOMC) and headings
(RBI), "Other" is the fallback, and a diff's pairs carry them. The sample texts are short constructed paragraphs in the
documents' shapes, not saved pages."""
from __future__ import annotations

from ft.engines.document.diff import diff_texts
from ft.policy import topics

FED_OLD = (
    "Recent indicators suggest that growth of economic activity moderated. Job gains have slowed. "
    "Inflation has moved up and remains somewhat elevated.\n\n"
    "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. "
    "Uncertainty about the economic outlook remains elevated.\n\n"
    "The Committee decided to lower the target range for the federal funds rate to 4 to 4-1/4 percent. "
    "The Committee will continue reducing its holdings of Treasury securities. "
    "The Committee is strongly committed to returning inflation to its 2 percent objective.\n\n"
    "The Committee will continue to monitor the implications of incoming information for the economic outlook.\n\n"
    "Voting for the monetary policy action were A, Chair; and B. Voting against this action was C."
)
FED_NEW = FED_OLD.replace("Job gains have slowed.", "Job gains have picked up.").replace(
    "lower the target range", "maintain the target range").replace(
    "Uncertainty about the economic outlook remains elevated.", "Uncertainty about the economic outlook has diminished.")

RBI_OLD = (
    "On the basis of an assessment of the current macroeconomic situation, the MPC decided to keep the policy repo rate "
    "unchanged at 5.50 per cent.\n\n"
    "Assessment\n\nGlobal Economy\n\nGlobal growth is moderating amid trade tensions.\n\n"
    "Domestic Economy\n\nDomestic activity has been resilient.\n\n"
    "Outlook\n\nCPI inflation is projected at 3.1 per cent. Real GDP growth is projected at 6.5 per cent."
)
RBI_NEW = RBI_OLD.replace("5.50", "5.25").replace("Global growth is moderating", "Global growth is firming").replace(
    "3.1 per cent", "2.9 per cent")


def _starts(bank, text):
    """Topic by the sentence's beginning: got["Voting for"] finds the sentence that starts with it."""
    spans = [(text[s:e], t) for s, e, t in topics.sentence_topics(bank, text)]

    class Got:
        def __getitem__(self, prefix):
            (hit,) = [t for sent, t in spans if sent.startswith(prefix)]
            return hit
    return Got()


def _by_topic(bank, old, new):
    d = diff_texts(old, new)
    topics.assign_topics(bank, d.segments, old, new)
    return d.segments, {(p["kind"], p["topic"]) for p in d.segments["pairs"] if p["kind"] != "same"}


def test_fomc_sentences_take_their_topic_by_anchor():
    got = _starts("Fed", FED_OLD)
    assert got["Recent indicators su"] == "Economy and labour market"
    assert got["Inflation has moved u"] == "Inflation"
    assert got["The Committee seeks"] == "Goals and risks"
    assert got["Uncertainty about th"] == "Goals and risks"
    assert got["The Committee decide"] == "Decision and guidance"
    assert got["The Committee will continue reducing"] == "Balance sheet"
    assert got["The Committee will continue to monitor"] == "Monitoring"
    assert got["The Committee is str"] == "Goals and risks"
    assert got["Voting for the monet"] == "Votes"


def test_fomc_pairs_carry_topics_and_the_map_version():
    seg, kinds = _by_topic("Fed", FED_OLD, FED_NEW)
    assert ("changed", "Economy and labour market") in kinds
    assert ("changed", "Decision and guidance") in kinds
    assert ("changed", "Goals and risks") in kinds
    assert seg["params"]["topics"] == topics.version()
    assert all(p["topic"] for p in seg["pairs"])


def test_rbi_topics_follow_the_headings_and_split_the_outlook():
    got = _starts("RBI", RBI_OLD)
    assert got["On the basis of an a"] == "Decision and stance"
    assert got["Global growth is mod"] == "Global"
    assert got["Domestic activity ha"] == "Domestic"
    assert got["CPI inflation is pro"] == "Inflation"
    assert got["Real GDP growth is p"] == "Growth"
    _seg, kinds = _by_topic("RBI", RBI_OLD, RBI_NEW)
    assert {("changed", "Decision and stance"), ("changed", "Global"), ("changed", "Inflation")} <= kinds


def test_removed_pairs_take_the_old_sides_topic_and_unknown_text_is_other():
    old = "Voting for the monetary policy action were A.\n\nThe weather was fine."
    _seg, kinds = _by_topic("Fed", old, "Voting for the monetary policy action were A.")
    assert ("removed", topics.OTHER) in kinds
