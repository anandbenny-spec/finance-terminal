"""The tone gate's set `earnings_tone` (Spec 02B §5.8; job 2B-12, #175): the picking rule on a synthetic candidate table, and the
committed items file (chunk ids only, no text)."""
import json
from collections import Counter
from pathlib import Path

import pytest

from ft.eval import tone_set as ts

SEASONS = ["Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26", "Q3 FY26", "Q4 FY26", "Q1 FY27", "Q2 FY27"]
PEERS = ["banks", "it", "autos", "pharma", "fmcg", "power", "steel", "nbfc"]


def table(n_companies=36) -> list[ts.Cand]:
    """Companies spread over the strata, groups, seasons and peer groups; a call per (company, season)."""
    out, doc = [], 1000
    for c in range(n_companies):
        for s, season in enumerate(SEASONS):
            doc += 1
            pat = [-0.30, 0.02, 0.40][(c + s) % 3]
            for chunk, (section, group) in enumerate((("opening", "open_close"), ("qa", "answers"))):
                out.append(ts.Cand(f"co{c:02d}", season, PEERS[c % len(PEERS)], pat, doc, chunk + 3, section, group,
                                   ts.rank(ts.SEED, doc, chunk + 3)))
    return out


def test_the_strata_are_the_ten_per_cent_bands_on_each_side():
    def f(v):
        return ts.Cand("a", "Q1 FY26", "x", v, 1, 1, "qa", "answers", "0").stratum

    assert (f(-0.1001), f(-0.10), f(0.10), f(0.1001), f(0.0)) == ("fell", "within", "within", "rose", "within")


def test_the_set_has_thirty_five_per_cell_with_the_caps_and_the_season_spread():
    chosen = ts.pick(table())
    assert len(chosen) == 30
    assert Counter((c.stratum, c.group) for c in chosen) == {(s, g): 5 for s in ts.STRATA for g in ts.GROUPS}
    assert Counter(c.group for c in chosen) == {"open_close": 15, "answers": 15}
    assert len({c.slug for c in chosen}) == 30 or max(Counter(c.slug for c in chosen).values()) == 1
    assert max(Counter(c.peer_group for c in chosen).values()) <= ts.PEER_CAP
    assert len({c.season for c in chosen}) >= ts.MIN_SEASONS


def test_the_pick_is_deterministic_and_does_not_depend_on_the_order_of_the_candidates():
    rows = table()
    assert [c.id for c in ts.pick(rows)] == [c.id for c in ts.pick(list(reversed(rows)))]


def test_thinning_keeps_the_smallest_hash_per_call_and_group():
    a = ts.Cand("a", "Q1 FY26", "x", 0.0, 1, 1, "qa", "answers", "bbb")
    b = ts.Cand("a", "Q1 FY26", "x", 0.0, 1, 2, "qa", "answers", "aaa")
    c = ts.Cand("a", "Q1 FY26", "x", 0.0, 1, 3, "opening", "open_close", "zzz")
    assert sorted(x.chunk_no for x in ts.thin([a, b, c])) == [2, 3]


def test_a_shortfall_stops_with_the_cell_named():
    rows = [c for c in table() if c.stratum != "rose"]
    with pytest.raises(ts.NotEnough, match="no set satisfies"):
        ts.pick(rows)


def test_a_scarce_stratum_that_shares_its_companies_with_the_others_still_gets_its_set():
    """Plain round-robin greedy fails here (the scarce stratum's companies are taken by the other cells first); the pick backs up."""
    rows, doc = [], 9000
    for c in range(14):                                     # 14 companies: each has a "fell" call and a "rose" call
        for s, season in enumerate(SEASONS[:7]):
            doc += 1
            for chunk, (section, group) in enumerate((("opening", "open_close"), ("qa", "answers"))):
                pat = -0.30 if (c < 12 and s < 2) else 0.40 if s % 2 else 0.02
                rows.append(ts.Cand(f"sc{c:02d}", season, PEERS[c % len(PEERS)], pat, doc, chunk + 3, section, group,
                                    ts.rank(ts.SEED, doc, chunk + 3)))
    for c in range(14, 40):                                 # plenty of companies for the other strata
        for s, season in enumerate(SEASONS):
            doc += 1
            for chunk, (section, group) in enumerate((("opening", "open_close"), ("qa", "answers"))):
                rows.append(ts.Cand(f"sc{c:02d}", season, PEERS[c % len(PEERS)], [0.4, 0.02][s % 2], doc, chunk + 3, section, group,
                                    ts.rank(ts.SEED, doc, chunk + 3)))
    chosen = ts.pick(rows)
    assert len(chosen) == 30 and len({c.slug for c in chosen}) == 30
    assert Counter((c.stratum, c.group) for c in chosen) == {(s, g): 5 for s in ts.STRATA for g in ts.GROUPS}


def test_too_few_seasons_stops():
    rows = [c for c in table() if c.season in SEASONS[:3]]
    with pytest.raises(ts.NotEnough):
        ts.pick(rows)


def test_the_hash_matches_the_sql():
    # CANDIDATE_SQL: md5(seed || ':' || doc || ':' || chunk_no)
    assert ts.rank("earnings_tone-1", 5073, 19) == __import__("hashlib").md5(b"earnings_tone-1:5073:19").hexdigest()
    assert "md5(%(seed)s || ':' || c.doc || ':' || k.chunk_no)" in ts.CANDIDATE_SQL and ts.SEED == "earnings_tone-1"


def test_the_items_file_lists_chunk_ids_only():
    path = Path(ts.__file__).resolve().parents[3] / "config" / "eval" / "sets" / "earnings_tone_items.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["set"] == ts.SET_NAME and doc["rule_version"] == ts.RULE_VERSION and doc["seed"] == ts.SEED
    for it in doc["items"]:
        assert set(it) <= {"id", "document_id", "chunk_no", "section", "group", "stratum", "slug", "season", "peer_group"}
        assert it["id"] == f"earnings_tone:{it['document_id']}-{it['chunk_no']}"


def test_the_payload_carries_the_rule_and_the_seed():
    p = ts.payload(ts.pick(table()), "dev")
    assert p["rule_version"] == 1 and p["seed"] == ts.SEED and len(p["items"]) == 30 and p["items"][0]["id"].startswith("earnings_tone:")


def test_the_committed_set_is_thirty_items_in_the_six_cells_with_the_caps():
    path = Path(ts.__file__).resolve().parents[3] / "config" / "eval" / "sets" / "earnings_tone_items.json"
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    assert len(items) == 30 and len({i["id"] for i in items}) == 30
    assert Counter((i["stratum"], i["group"]) for i in items) == {(s, g): 5 for s in ts.STRATA for g in ts.GROUPS}
    assert len({i["slug"] for i in items}) == 30
    assert max(Counter(i["peer_group"] for i in items).values()) <= ts.PEER_CAP
    assert len({i["season"] for i in items}) >= ts.MIN_SEASONS


def test_the_labels_file_covers_the_set_and_applies_its_tie_break():
    """earnings_tone.labels.json (#175; Anand, 10 Nov 2026): Fable's labels are the gold ones, Opus's the second rater's; where their
    three classes differ the main labeller's label stands. The agreement it records is recomputed here."""
    sets = Path(ts.__file__).resolve().parents[3] / "config" / "eval" / "sets"
    items = json.loads((sets / "earnings_tone_items.json").read_text(encoding="utf-8"))["items"]
    doc = json.loads((sets / "earnings_tone.labels.json").read_text(encoding="utf-8"))
    assert doc["set"] == ts.SET_NAME and doc["rule_version"] == ts.RULE_VERSION and doc["complete"] is True
    labels = doc["labels"]
    assert [lb["id"] for lb in labels] == [i["id"] for i in items]
    assert [lb["section"] for lb in labels] == [i["section"] for i in items]

    def cls(s: str) -> int:
        return -1 if int(s) <= -1 else (1 if int(s) >= 1 else 0)

    for lb in labels:
        for who in (lb, lb["fable"], lb["opus"]):
            assert who["label"] in ("-2", "-1", "0", "+1", "+2")
            assert 0 < len(who["reason"].strip()) <= 200 and "\n" not in who["reason"] and '"' not in who["reason"]
        assert lb["label"] == lb["fable"]["label"] and lb["reason"] == lb["fable"]["reason"]
        assert lb["tie_break"] is (cls(lb["fable"]["label"]) != cls(lb["opus"]["label"]))
    a = doc["agreement"]
    assert a["n"] == len(labels) == 30
    assert a["three_class"] == sum(cls(lb["fable"]["label"]) == cls(lb["opus"]["label"]) for lb in labels)
    assert a["exact"] == sum(lb["fable"]["label"] == lb["opus"]["label"] for lb in labels)
    assert a["tie_breaks"] == sum(lb["tie_break"] for lb in labels)
    assert a["opposite_sides"] == sum(cls(lb["fable"]["label"]) * cls(lb["opus"]["label"]) == -1 for lb in labels)


def test_the_payload_goes_out_in_parts_that_join_back():
    body = json.dumps(ts.payload(ts.pick(table()), "dev", "earnings_tone_2"), separators=(",", ":"))
    lines = ts.annotations(body)
    assert len(lines) == -(-len(body) // ts.ANNOTATION_CHARS) > 1
    parts = [line.split("::", 2)[2] for line in lines]
    assert all(len(p) <= ts.ANNOTATION_CHARS for p in parts) and json.loads("".join(parts))["set"] == "earnings_tone_2"
    assert lines[0].startswith(f"::notice title=tone items (1 of {len(lines)})::")


def test_the_second_set_has_its_own_seed_and_leaves_out_the_first_sets_passages():
    spec = ts.SETS["earnings_tone_2"]
    assert spec["seed"] != ts.SEED and spec["rule_version"] == 2 and spec["excludes"] == (ts.SET_NAME,)
    first = json.loads(ts.items_file(ts.SET_NAME).read_text(encoding="utf-8"))["items"]
    assert ts.excluded("earnings_tone_2") == sorted(f"{i['document_id']}:{i['chunk_no']}" for i in first) and ts.excluded(ts.SET_NAME) == []
    assert "<> all(%(exclude)s::text[])" in ts.CANDIDATE_SQL
    p = ts.payload([], "dev", "earnings_tone_2")
    assert (p["seed"], p["rule_version"], p["excludes"]) == ("earnings_tone-2", 2, ["earnings_tone"])
    assert "excludes" not in ts.payload([], "dev")
    c = ts.Cand("a", "Q1 FY26", "x", 0.0, 7, 3, "qa", "answers", "0", "earnings_tone_2")
    assert c.id == "earnings_tone_2:7-3"
