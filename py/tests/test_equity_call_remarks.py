"""Opening-remarks changes (Spec 02B §5.13, D9; job 2B-17): the masking, the similarity formula on fixed text, the threshold on
each side, a new CEO, the cap on pairs and their length, no opening, and the previous quarter's label. Synthetic text only."""
import pytest

from ft.equity.calls import remarks as rm


def build(*turns: tuple[str, str, str]) -> tuple[str, list[rm.TurnRef]]:
    """(speaker, role, body) turns, all in the opening, laid out one after another with their speaker labels."""
    text, refs = "", []
    for speaker, role, body in turns:
        block = f"{speaker}: {body}\n"
        refs.append(rm.TurnRef(len(text), len(text) + len(block) - 1, speaker, role, "opening", len(block.split())))
        text += block
    return text, refs


def opening_of(*turns):
    text, refs = build(*turns)
    return text, rm.opening(text, refs)


def test_mask_turns_each_run_of_digits_into_one_hash():
    assert rm.mask("Revenue grew 12.5% to Rs 1,200 crore in 2026") == "Revenue grew #.#% to Rs #,# crore in #"


def test_opening_keeps_management_opening_turns_only_and_maps_spans_back():
    text = "Operator: Welcome.\nAlice Rao: Sales were 1200 this quarter.\nBen Das: Ask away.\nAlice Rao: Margins held at 24.\n"
    turns, pos = [], 0
    for line, role, section in [("Operator: Welcome.", "moderator", "opening"),
                                ("Alice Rao: Sales were 1200 this quarter.", "management", "opening"),
                                ("Ben Das: Ask away.", "analyst", "opening"),
                                ("Alice Rao: Margins held at 24.", "management", "qa")]:
        turns.append(rm.TurnRef(pos, pos + len(line), line.split(":")[0], role, section, len(line.split())))
        pos += len(line) + 1
    o = rm.opening(text, turns)
    assert o.masked == "Alice Rao: Sales were # this quarter."
    start = text.index("Alice Rao")
    assert o.offsets[0] == start and o.ends[len(o.masked) - 1] == start + len("Alice Rao: Sales were 1200 this quarter.")
    hash_at = o.masked.index("#")
    assert text[o.offsets[hash_at]:o.ends[hash_at]] == "1200"
    assert o.speakers == {"alice rao": 7}


def test_no_management_opening_text_is_no_opening():
    text, refs = build(("Operator", "moderator", "Welcome to the call"), ("Ben Das", "analyst", "A question"))
    assert rm.opening(text, refs) is None
    assert rm.opening(text, []) is None


BASE = ("Alice Rao", "management",
        ("Revenue grew 12 percent this quarter. Margins held steady across every segment. We opened 3 new stores in the "
         "south. Our balance sheet remains strong with low leverage."))


def test_identical_openings_with_different_numbers_are_fully_similar():
    new_text, new = opening_of(("Alice Rao", "management",
        ("Revenue grew 15 percent this quarter. Margins held steady across every segment. We opened 4 new stores in the "
         "south. Our balance sheet remains strong with low leverage.")))
    old_text, old = opening_of(BASE)
    c = rm.compare(new_text, new, old_text, old)
    assert (c.words_added, c.words_removed, c.similarity) == (0, 0, 1.0)     # 12 vs 15 and 3 vs 4 are masked
    assert c.label is None and c.new_speakers == [] and c.pairs == []


def test_the_formula_on_fixed_text():
    """Old: 8 words. New: the first sentence kept, the second replaced (4 removed, 5 added): 1 - (5 + 4) / (8 + 9)."""
    old_text, old = opening_of(("Alice Rao", "management", "Alpha beta gamma delta. Epsilon zeta eta theta."))
    new_text, new = opening_of(("Alice Rao", "management", "Alpha beta gamma delta. Kappa lambda mu nu xi."))
    assert (old.words, new.words) == (10, 11)       # the label "Alice Rao" adds two words to each side
    c = rm.compare(new_text, new, old_text, old)
    assert (c.words_added, c.words_removed) == (5, 4)
    assert c.similarity == pytest.approx(1 - 9 / 21, abs=1e-4)


def test_no_label_at_any_similarity():
    """Amended 8 Nov 2026: no threshold, so no `largely_rewritten` at 0.0, near 0.5 or near 1."""
    def pair(new_body: str):
        old_text, old = opening_of(("Alice Rao", "management", "One two three four five six seven eight nine ten."))
        new_text, new = opening_of(("Alice Rao", "management", new_body))
        return rm.compare(new_text, new, old_text, old)
    sims = []
    for body in ("One two three four five six seven eight nine eleven.",
                 "Red blue green yellow orange purple brown black white grey.",
                 "One two three four five six seven eight nine ten.",
                 "One two three four five red blue green yellow orange."):
        c = pair(body)
        sims.append(c.similarity)
        assert c.label is None
    assert min(sims) < 0.5 <= max(sims)
    assert rm.RULE_VERSION == "remarks-3"


def test_threshold_on_each_side_of_half():
    def pair(new_body: str):
        old_text, old = opening_of(("Alice Rao", "management", "One two three four five six seven eight nine ten."))
        new_text, new = opening_of(("Alice Rao", "management", new_body))
        return rm.compare(new_text, new, old_text, old)
    # a sentence sharing 6 of 8 body words with its partner stays paired (word diff inside); the rest is rewritten
    kept = pair("One two three four five six seven eight nine eleven.")
    assert kept.similarity >= 0.5 and kept.label is None
    rewritten = pair("Red blue green yellow orange purple brown black white grey.")
    assert rewritten.similarity < 0.5 and rewritten.label is None


def test_a_new_ceo_with_a_third_of_the_words_is_a_new_speaker_and_the_opening_is_rewritten():
    body = "Revenue grew this quarter. Margins held steady across every segment."
    old_text, old = opening_of(("Alice Rao", "management", body), ("Ben Das", "management", "Thanks Alice. Costs fell."))
    new_text, new = opening_of(("Alice Rao", "management", body),
                               ("Mr. Carl Ito", "management", "Thanks Alice. Costs fell. Hiring picked up in sales."))
    c = rm.compare(new_text, new, old_text, old)
    assert c.new_speakers == ["Mr. Carl Ito"] and c.label == "new_speaker"
    assert c.similarity >= 0.5                      # the text alone wouldn't call it rewritten


def test_a_new_speaker_below_thirty_percent_of_the_words_is_not_flagged():
    body = " ".join(f"word{i}" for i in range(60)) + "."
    old_text, old = opening_of(("Alice Rao", "management", body))
    new_text, new = opening_of(("Alice Rao", "management", body), ("Carl Ito", "management", "Thanks all."))
    c = rm.compare(new_text, new, old_text, old)
    assert c.new_speakers == [] and c.label is None


def test_the_same_person_under_another_label_is_not_new():
    body = "Revenue grew this quarter. Margins held steady across every segment."
    old_text, old = opening_of(("Mr. S. Kumar", "management", body))
    new_text, new = opening_of(("S. Kumar CEO", "management", body))
    assert rm.compare(new_text, new, old_text, old).new_speakers == []


def test_at_most_five_pairs_with_the_most_changed_words_each_side_capped():
    old_sentences = [f"Old{i} " + " ".join(f"o{i}x{j}" for j in range(i + 3)) + "." for i in range(8)]
    new_sentences = [f"New{i} " + " ".join(f"n{i}x{j}" for j in range(i + 3)) + "." for i in range(8)]
    old_text, old = opening_of(("Alice Rao", "management", " ".join(old_sentences)))
    new_text, new = opening_of(("Alice Rao", "management", " ".join(new_sentences)))
    c = rm.compare(new_text, new, old_text, old)
    assert len(c.pairs) == rm.MAX_PAIRS
    counts = [p["changed_words"] for p in c.pairs]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] >= 10                          # the longest rewritten sentences lead


def test_a_long_sentence_is_cut_to_400_characters_with_its_true_span():
    long_old = "Alpha " + " ".join(f"a{i}" for i in range(300)) + "."
    long_new = "Beta " + " ".join(f"b{i}" for i in range(300)) + "."
    old_text, old = opening_of(("Alice Rao", "management", long_old))
    new_text, new = opening_of(("Alice Rao", "management", long_new))
    c = rm.compare(new_text, new, old_text, old)
    side = next(p["new"] for p in c.pairs if p["new"])
    assert len(side["text"]) <= rm.MAX_SIDE
    s, e = side["spans"][0]
    assert e - s > rm.MAX_SIDE and new_text[s:e].endswith("b299.")


def test_pairs_keep_the_figures_as_filed():
    old_text, old = opening_of(("Alice Rao", "management", "Revenue was 1,100 crore. Costs fell."))
    new_text, new = opening_of(("Alice Rao", "management", "Turnover reached 1,250 crore. Costs fell."))
    c = rm.compare(new_text, new, old_text, old)
    texts = [p[k]["text"] for p in c.pairs for k in ("old", "new") if p[k]]
    assert any("1,100" in t for t in texts) and any("1,250" in t for t in texts)


@pytest.mark.parametrize("label,previous", [("Q2 FY27", "Q1 FY27"), ("Q1 FY27", "Q4 FY26"), ("Q1 FY00", "Q4 FY99"),
                                            ("bad", None), (None, None)])
def test_previous_label(label, previous):
    assert rm.previous_label(label) == previous


def test_the_distribution_check_never_fails_on_low_similarities():
    from ft.jobs.tasks.equity_call_scores import EquityCallScores
    c = EquityCallScores._distribution([(i, 0.01 * i, []) for i in range(1, 21)])
    assert c.passed and not c.blocking and "n 20" in c.detail and "median" in c.detail
    assert EquityCallScores._distribution([]).passed


# ---- verified quotes (remarks-3, #414) ----
PAGES = [(1, 0, 60), (2, 60, 400)]


def _compare(old_body: str, new_body: str):
    old_text, old = opening_of(("Alice Rao", "management", old_body))
    new_text, new = opening_of(("Alice Rao", "management", new_body))
    return old_text, new_text, rm.compare(new_text, new, old_text, old)


def test_a_side_found_in_the_stored_text_verifies_with_its_page():
    old_text, new_text, c = _compare("Alpha beta gamma delta. Epsilon zeta eta theta.", "Alpha beta gamma delta. Nu xi omicron pi rho.")
    out = rm.verify_pairs(c.pairs, new_text, 7, PAGES, old_text, 6, PAGES)
    assert out and all(p[k]["verified"] is True for p in out for k in ("old", "new") if p[k])
    side = next(p["new"] for p in out if p["new"])
    assert side["document_id"] == 7 and new_text[side["start"]:side["end"]].split() == side["text"].split()
    assert side["page"] in (1, 2) and side["page_last"] >= side["page"]


def test_a_side_altered_by_one_character_does_not_verify_and_is_dropped():
    old_text, new_text, c = _compare("Alpha beta gamma delta.", "Nu xi omicron pi rho.")
    new_side = next(p["new"] for p in c.pairs if p["new"])
    new_side["text"] = new_side["text"].replace("omicron", "omicrom")
    out = rm.verify_pairs(c.pairs, new_text, 7, PAGES, old_text, 6, PAGES)
    assert all(p["new"] is None for p in out)
    assert out and all(p["old"] is not None for p in out)     # the other side still verifies


def test_a_pair_with_no_verified_side_is_dropped():
    old_text, new_text, c = _compare("Alpha beta gamma delta.", "Nu xi omicron pi rho.")
    for pair in c.pairs:
        for k in ("old", "new"):
            if pair[k]:
                pair[k]["spans"] = [[0, 5]]
                pair[k]["text"] = "Something else entirely"
    assert rm.verify_pairs(c.pairs, new_text, 7, PAGES, old_text, 6, PAGES) == []


def test_a_span_outside_the_text_does_not_verify():
    old_text, new_text, c = _compare("Alpha beta gamma delta.", "Nu xi omicron pi rho.")
    next(p["new"] for p in c.pairs if p["new"])["spans"] = [[0, len(new_text) + 10]]
    out = rm.verify_pairs(c.pairs, new_text, 7, None, old_text, 6, None)
    assert all(p["new"] is None for p in out)


def test_no_verified_side_is_over_400_code_points_and_no_page_map_means_no_page():
    long_old = "Alpha " + " ".join(f"a{i}" for i in range(300)) + "."
    long_new = "Beta " + " ".join(f"b{i}" for i in range(300)) + "."
    old_text, new_text, c = _compare(long_old, long_new)
    out = rm.verify_pairs(c.pairs, new_text, 7, None, old_text, 6, None)
    for p in out:
        for k in ("old", "new"):
            s = p[k]
            if s is None:
                continue
            assert len(s["text"]) <= rm.MAX_SIDE and s["end"] - s["start"] <= rm.MAX_SIDE and s["page"] is None
