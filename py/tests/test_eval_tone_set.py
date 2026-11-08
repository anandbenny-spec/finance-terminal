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
