"""The sentence-matched differ (Prompt 01 B2; Spec 01 §2, "Diff (S)"): pairs at word similarity >= 0.55, 1→2 and 2→1
when the joined ratio beats the best single pair by >= 0.10, word diffs inside pairs, segments as diff version 2."""
import json
import random
import time

from policy_docs import FOMC_A, FOMC_B, rbi_page, rbi_resolution_body

from ft.engines.document.diff import DIFF_VERSION, PAIR_MIN, SPLIT_GAIN, diff_texts
from ft.engines.document.text import extract_html, paragraph_of, paragraph_starts, words


def check(old: str, new: str):
    """Every pair's spans, ¶ numbers, texts and ops agree with the two texts; the counts add up."""
    d = diff_texts(old, new)
    seg = d.segments
    assert seg["version"] == int(DIFF_VERSION) == 2 and d.diff_version == "2"
    assert seg["params"] == {"pair_min": PAIR_MIN, "split_gain": SPLIT_GAIN}
    assert seg["words"] == {"old": len(words(old)), "new": len(words(new))}
    assert json.loads(json.dumps(seg)) == seg
    added = removed = 0
    olds, news = [], []
    for p in seg["pairs"]:
        assert p["topic"] is None
        o = old[p["old"][0][0]:p["old"][-1][1]] if p["old"] else ""
        n = new[p["new"][0][0]:p["new"][-1][1]] if p["new"] else ""
        assert p["old_para"] == [paragraph_of(paragraph_starts(old), s) for s, _ in p["old"]]
        assert p["new_para"] == [paragraph_of(paragraph_starts(new), s) for s, _ in p["new"]]
        olds += p["old"]
        news += p["new"]
        if p["kind"] == "changed":
            assert "".join(x["text"] for x in p["ops"] if x["op"] != "ins") == o
            assert "".join(x["text"] for x in p["ops"] if x["op"] != "del") == n
            assert p["ratio"] >= PAIR_MIN and o != n
            added += sum(len(words(x["text"])) for x in p["ops"] if x["op"] == "ins")
            removed += sum(len(words(x["text"])) for x in p["ops"] if x["op"] == "del")
        elif p["kind"] == "same":
            assert o == n == p["text"]
        elif p["kind"] == "added":
            assert (p["old"], p["text"]) == ([], n)
            added += len(words(n))
        else:
            assert (p["kind"], p["new"], p["text"]) == ("removed", [], o)
            removed += len(words(o))
    assert olds == sorted(olds) and news == sorted(news)                 # both texts read in order
    assert (d.words_added, d.words_removed) == (added, removed)
    return d


def kinds(d):
    return [p["kind"] for p in d.segments["pairs"]]


def test_identical_statements_are_all_same():
    d = check(FOMC_A, FOMC_A)
    assert set(kinds(d)) == {"same"} and (d.words_added, d.words_removed) == (0, 0)


def test_consecutive_fomc_statements():
    d = check(FOMC_A, FOMC_B)
    assert kinds(d) == ["changed", "same", "same", "same", "removed", "changed", "changed", "changed", "added",
                        "changed", "same", "added"]
    pairs = d.segments["pairs"]
    assert [x for x in pairs[0]["ops"] if x["op"] != "eq"] == [{"op": "ins", "text": " further"}]
    assert pairs[4]["text"] == "Uncertainty about the economic outlook remains elevated."
    rate = pairs[6]
    assert {"op": "del", "text": "maintain"} in rate["ops"] and {"op": "ins", "text": "lower"} in rate["ops"]
    assert rate["old_para"] == rate["new_para"] == [3]
    assert pairs[-1]["text"].startswith("Voting against this action was Stephen I. Miran")
    assert (d.words_added, d.words_removed) == (62, 21)


def test_one_sentence_split_in_two_and_two_merged_into_one():
    one = "The Committee decided to lower the target range and will continue to reduce its holdings of Treasury securities."
    two = "The Committee decided to lower the target range. It will continue to reduce its holdings of Treasury securities."
    d = check("Before.\n\n" + one, "Before.\n\n" + two)
    split = d.segments["pairs"][1]
    assert (split["kind"], len(split["old"]), len(split["new"])) == ("changed", 1, 2)
    assert split["ratio"] >= SPLIT_GAIN + max(0.59, 0.62)
    merged = check(two, one).segments["pairs"]
    assert [(len(p["old"]), len(p["new"])) for p in merged] == [(2, 1)]


def test_a_split_needs_to_beat_the_best_single_pair_by_the_gain():
    old = "The Committee will continue reducing its holdings of Treasury securities and agency debt and agency mortgage-backed securities."
    new = ("The Committee will continue reducing its holdings of Treasury securities. "
           "It will continue reducing its holdings of agency debt and agency mortgage-backed securities.")
    d = check(old, new)
    assert [(p["kind"], len(p["old"]), len(p["new"])) for p in d.segments["pairs"]] == [("added", 0, 1), ("changed", 1, 1)]


def test_dissimilar_sentences_are_removed_and_added_removed_first():
    d = check("Inflation remains elevated.", "Labor market conditions have softened considerably.")
    assert kinds(d) == ["removed", "added"]
    assert (d.words_added, d.words_removed) == (6, 3)


def test_a_moved_sentence_reads_as_removed_and_added():
    old = "Alpha one two three. Beta four five six. Gamma seven eight nine."
    new = "Gamma seven eight nine. Alpha one two three. Beta four five six."
    d = check(old, new)
    assert sorted(kinds(d)) == ["added", "removed", "same", "same"]
    moved = [p["text"] for p in d.segments["pairs"] if p["kind"] in ("added", "removed")]
    assert moved == ["Gamma seven eight nine."] * 2


def test_word_diff_joins_a_rewritten_phrase():
    d = check("The Committee decided to lower federal funds rate targets today.",
              "The Committee decided to raise policy rate targets today.")
    ops = d.segments["pairs"][0]["ops"]
    assert [x for x in ops if x["op"] != "eq"] == [{"op": "del", "text": "lower federal funds"},
                                                   {"op": "ins", "text": "raise policy"}]
    for a, b, c in zip(ops, ops[1:], ops[2:], strict=False):
        assert not (b["op"] == "eq" and not b["text"].strip() and a["op"] != "eq" and c["op"] != "eq")


def test_abbreviations_and_initials_keep_sentences_whole():
    old = "The U.S. economy grew. Jerome H. Powell, Chair, voted for the action."
    new = "The U.S. economy grew solidly. Jerome H. Powell, Chair, voted for the action."
    d = check(old, new)
    assert kinds(d) == ["changed", "same"]


def test_resolution_sized_texts_diff_in_reasonable_time():
    a = extract_html(rbi_page(rbi_resolution_body(random.Random(1))).encode(), "rbi_press_release").text
    b = extract_html(rbi_page(rbi_resolution_body(random.Random(2))).encode(), "rbi_press_release").text
    big_a, big_b = "\n\n".join([a] * 3), "\n\n".join([b] * 3)
    t = time.perf_counter()
    d = check(big_a, big_b)
    assert time.perf_counter() - t < 10
    assert {"same", "changed"} <= set(kinds(d))
