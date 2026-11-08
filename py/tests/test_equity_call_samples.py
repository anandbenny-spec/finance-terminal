"""Drawing #178's gate samples (Spec 02B §5.11, §13.8; Spec 02B.1 C7): seeded, order-independent, within every cap and cell,
short of a minimum only when the pool is, and holding no text; the SQL that shows and records the items; the workflow
that reads dev only."""
import hashlib
import random
import re

import pytest
from test_workflows import resolve, secrets

from ft.equity.calls import samples as s
from ft.equity.calls.turns import ROLE_HOLD_SHARE

KINDS = ("range", "point", "floor", "ceiling", "direction")
METRICS = ("revenue_growth", "ebitda_margin", "capex", "advances_growth", "other", "other", "other", "pat_growth", "tax_rate")


def target_pool(n=900, companies=40, seed=7):
    rnd = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        metric = rnd.choice(METRICS)
        out.append(s.Item(id=i, company=rnd.randint(1, companies), group=s.KIND_GROUP[rnd.choice(KINDS)],
                          family=s.family_of(metric), not_scored=metric == "other" or rnd.random() < 0.2,
                          role_held=rnd.random() < 0.25, no_mgmt_turn=rnd.random() < 0.05, document=i))
    return out


def ids(d):
    return [it.id for it in d.items]


# ---------------------------------------------------------------------------------------------- the draw

def test_the_seeds_are_recorded_one_a_gate_and_the_rank_is_a_fixed_hash():
    assert s.SEEDS == {"targets": 2026110701, "unattributed": 2026110702, "stated": 2026110703, "links": 2026110704,
                       "summaries": 2026110801, "recall": 2026110802, "scoring": 2026110803}
    assert s.GATES == ("targets", "unattributed", "stated", "links") and s.GATES_2 == ("summaries", "recall", "scoring")
    assert s.ALL_GATES == tuple(s.SEEDS) == tuple(s.RULES)
    assert [s.SITTING_OF[g] for g in s.ALL_GATES] == [1, 1, 1, 1, 2, 2, 2]
    # a hash, not Python's hash(): the same on every machine and every run
    assert s.rank(1, "targets", 5) == hashlib.sha256(b"1:targets:5").hexdigest()


def test_the_same_seed_gives_the_same_items_whatever_the_pool_order():
    pool = target_pool()
    shuffled = pool[:]
    random.Random(1).shuffle(shuffled)
    a, b = s.draw(pool, "targets"), s.draw(shuffled, "targets")
    assert ids(a) == ids(b) and a.shortfalls == b.shortfalls and a.attempt == b.attempt
    assert ids(s.draw(pool, "targets", 99)) != ids(a)                     # a new seed is a new sample
    assert a.seed == s.SEEDS["targets"] and s.draw(pool, "targets", 99).seed == 99


def test_the_target_sample_keeps_its_caps_cells_and_minimums():
    d = s.draw(target_pool(), "targets")
    assert len(d.items) == 40 and d.shortfalls == []
    assert max(sum(1 for it in d.items if it.company == c) for c in {it.company for it in d.items}) <= 2
    rules = s.RULES["targets"]
    cells = {c: sum(1 for it in d.items if rules.cell(it) == c) for c in rules.quotas}
    assert cells == rules.quotas and sum(v for c, v in cells.items() if c.endswith("/ns")) == 8
    assert sum(it.role_held for it in d.items) >= 8
    fam = {f: sum(1 for it in d.items if it.family == f) for f in ("pnl", "margins", "capex_balance", "banks")}
    assert fam["pnl"] >= 8 and fam["margins"] >= 8 and fam["capex_balance"] >= 5 and fam["banks"] >= 3
    assert len({it.id for it in d.items}) == 40


def test_a_pool_that_cannot_meet_a_minimum_reports_the_shortfall_instead_of_loosening_a_rule():
    pool = [s.Item(id=i, company=i, group="range", family="pnl", role_held=False) for i in range(1, 60)]
    d = s.draw(pool, "targets")
    assert len(d.items) == 10                                             # range/sc 10 is all a scored range-only pool fills
    assert "role_held: 0 of 8" in d.shortfalls and "banks: 0 of 3" in d.shortfalls
    assert "cell point/sc: 0 of 8" in d.shortfalls and "cell range/ns: 0 of 2" in d.shortfalls
    assert "total: 10 of 40" in d.shortfalls
    one_company = [s.Item(id=i, company=1, group="range", family="pnl") for i in range(1, 30)]
    assert len(s.draw(one_company, "targets").items) == 2                 # never more than 2 from one company


def test_the_unattributed_sample_takes_three_calls_with_no_management_turn_where_there_are_some():
    rnd = random.Random(3)
    pool = [s.Item(id=i, company=rnd.randint(1, 30), no_mgmt_turn=i % 10 == 0) for i in range(1, 211)]
    d = s.draw(pool, "unattributed")
    assert len(d.items) == 20 and sum(it.no_mgmt_turn for it in d.items) >= 3 and d.shortfalls == []
    assert all(sum(1 for x in d.items if x.company == c) <= 2 for c in {it.company for it in d.items})
    none = s.draw([s.Item(id=i, company=i) for i in range(1, 40)], "unattributed")
    assert none.shortfalls == ["no_mgmt_turn: 0 of 3"] and len(none.items) == 20


def test_the_stated_figure_sample_spreads_over_metric_families():
    rnd = random.Random(5)
    metrics = ("revenue_growth", "revenue", "pat", "pat_growth", "ebitda_margin", "tax_rate", "capex", "net_debt",
               "advances_growth", "gnpa", "nnpa", "cet1")
    pool = [s.Item(id=i, company=rnd.randint(1, 35), family=s.family_of(rnd.choice(metrics))) for i in range(1, 400)]
    d = s.draw(pool, "stated")
    assert len(d.items) == 20 and d.shortfalls == []
    fam = by_family(d.items)
    assert fam["pnl"] >= 6 and fam["margins"] >= 4 and fam["capex_balance"] >= 3 and fam["banks"] >= 4


def by_family(items):
    out = {}
    for it in items:
        out[it.family] = out.get(it.family, 0) + 1
    return out


def test_the_link_sample_is_five_links_from_five_companies_two_from_summary_bullets():
    rnd = random.Random(9)
    pool = [s.Item(id=f"{i}:1", company=rnd.randint(1, 30), source=rnd.choice(("summary", "figure")), document=i)
            for i in range(1, 300)]
    d = s.draw(pool, "links")
    assert len(d.items) == 5 and d.shortfalls == []
    assert [it.source for it in d.items].count("summary") == 2 and [it.source for it in d.items].count("figure") == 3
    assert len({it.company for it in d.items}) == 5 and len({it.document for it in d.items}) == 5
    assert all(re.fullmatch(r"\d+:\d+", str(it.id)) for it in d.items)


def scarce_floor_pool():
    """Five companies hold every scored floor and also most of the scored range and point items (as on dev), so a plain greedy
    fill spends their company slots on the common cells before the floor cell is reached."""
    out, n = [], 0
    for company in range(1, 6):
        for kind in ("floor", "floor", "floor", "range", "range", "range", "point", "point", "point", "direction"):
            n += 1
            out.append(s.Item(id=n, company=company, group=s.KIND_GROUP[kind], family="pnl", document=n))
    for company in range(6, 31):
        for kind in ("range", "point", "direction", "range", "point", "direction"):
            n += 1
            out.append(s.Item(id=n, company=company, group=s.KIND_GROUP[kind], family="margins", document=n))
    for company in range(1, 41):
        for kind in ("range", "point", "floor", "direction"):
            n += 1
            out.append(s.Item(id=n, company=company, group=s.KIND_GROUP[kind], family="other", not_scored=True,
                              role_held=company % 3 == 0, document=n))
    return out


def test_the_scarcest_cell_is_filled_first_so_common_cells_cannot_starve_it():
    pool = scarce_floor_pool()
    rules = s.RULES["targets"]
    assert s._cell_order(pool, rules)[0] == "floor_ceiling/sc"           # five companies for six slots
    d = s.draw(pool, "targets")
    st = d.strata()
    assert st["floor_ceiling/sc"] == 6 and len(d.items) == 40, d.shortfalls
    assert all(sum(1 for it in d.items if it.company == c) <= 2 for c in range(1, 41))


def test_a_short_attempt_is_retried_with_another_seeded_order_and_the_attempt_is_kept():
    pool = scarce_floor_pool()
    d = s.draw(pool, "targets")
    again = s.draw(pool[::-1], "targets")
    assert 0 <= d.attempt < s.ATTEMPTS and again.attempt == d.attempt and ids(again) == ids(d)
    # an impossible pool uses every attempt and reports the best one, not an arbitrary one
    impossible = [s.Item(id=i, company=1, group="range", family="pnl") for i in range(1, 30)]
    worst = s.draw(impossible, "targets")
    assert len(worst.items) == 2 and "total: 2 of 40" in worst.shortfalls


def test_the_strata_are_counted_for_the_record():
    d = s.draw(target_pool(), "targets")
    st = d.strata()
    assert st["range/sc"] == 10 and st["direction/ns"] == 2 and st["companies"] >= 20 and st["role_held"] >= 8


# ---------------------------------------------------------------------------------------------- the families and the share

def test_the_metric_families_cover_the_spec_map_and_nothing_else():
    assert {k for k in s.FAMILY} == {"revenue", "revenue_growth", "pat", "pat_growth", "ebitda_margin", "ebit_margin",
                                     "tax_rate", "capex", "net_debt", "advances_growth", "deposits_growth", "gnpa", "nnpa", "cet1"}
    assert s.family_of("other") == "other" and s.family_of("volumes") == "other"
    assert s.KIND_GROUP["floor"] == s.KIND_GROUP["ceiling"] == "floor_ceiling"


def test_a_summary_links_company_comes_from_its_document_not_from_the_output():
    # `core.ai_output.entity_id` is null for every call summary (checked on dev, 7 Nov 2026), which made one company of them all
    summary_branch = s.POOL_LINKS.split("union all")[0]
    assert "sd.entity_id" in summary_branch and "o.entity_id" not in summary_branch
    assert "join core.document sd on sd.id = c.document_id" in summary_branch


def test_the_role_held_share_in_the_pool_query_is_the_one_the_views_use():
    # `api.earnings_calls.roles_partial` is `unknown_role_share > 0.10`, and so is POOL_TARGETS (Spec 02B.1 C9)
    assert ROLE_HOLD_SHARE == 0.10 and "unknown_role_share > 0.10" in s.POOL_TARGETS


# ---------------------------------------------------------------------------------------------- the SQL

READ_ONLY_SQL = (s.POOL_TARGETS, s.POOL_STATED, s.POOL_LINKS, s.PAGE_MAP_SQL, s.POOL_SUMMARIES, s.POOL_RECALL, s.POOL_SCORING)
WRITES = re.compile(r"\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|copy)\b", re.IGNORECASE)


@pytest.mark.parametrize("sql", READ_ONLY_SQL)
def test_the_pool_and_check_queries_only_read(sql):
    assert sql.lstrip().lower().startswith(("select", "with")) and not WRITES.search(sql)


@pytest.mark.parametrize("sql", READ_ONLY_SQL)
def test_the_pool_queries_return_no_text(sql):
    assert "core.chunk" not in sql and ".body" not in sql and "content" not in sql


def test_item_sql_rebuilds_the_quote_from_the_chunks_and_only_for_the_ids_given():
    for gate in ("targets", "unattributed", "stated"):
        sql = s.item_sql(gate, [3, 1, 2])
        assert "where x.id in (3, 1, 2)" in sql and "core.chunk" in sql and "string_agg" in sql
        assert not WRITES.search(sql)
    assert "equity.guidance x" in s.item_sql("targets", [1]) and "equity.stated_figure x" in s.item_sql("stated", [1])
    with pytest.raises(ValueError):
        s.item_sql("links", [1])
    with pytest.raises(ValueError):
        s.item_sql("targets", [])
    with pytest.raises((ValueError, TypeError)):
        s.item_sql("targets", ["1); drop table x; --"])


def test_link_sql_takes_output_and_citation_numbers():
    sql = s.link_sql([(10, 2), (11, 1)])
    assert "(x.ai_output_id, x.citation_no) in ((10, 2), (11, 1))" in sql and "#page=" in sql
    with pytest.raises(ValueError):
        s.link_sql([])
    with pytest.raises((ValueError, TypeError)):
        s.link_sql([("1)); drop table x; --", 1)])


def test_the_page_link_is_the_sources_own_file_never_ours():
    sql = s.item_sql("targets", [1])
    assert "regexp_replace(d.source_url, '#.*$', '') || '#page='" in sql
    assert "storage." not in sql and "raw_file" not in sql


# ---------------------------------------------------------------------------------------------- recording verdicts

RUN = {"eval_set": "guidance_extraction_targets", "set_version": "abc1234", "model": "claude-haiku-5-5", "prompt_version": "v1",
       "n_items": 2, "metrics": {"fully_right": 1, "n": 2}, "passed": False, "notes": "Anand's verdicts; seed 2026110701",
       "results": [{"item_key": "guidance:12", "expected": {"verdict": "right"}, "predicted": {"metric": "capex"}, "correct": True},
                   {"item_key": "guidance:13", "expected": {"verdict": "wrong", "fields": ["period"]}, "predicted": None,
                    "correct": False}]}


def test_record_sql_is_one_transaction_with_a_run_and_its_results():
    sql = s.record_sql([RUN])
    assert sql.startswith("begin;") and sql.rstrip().endswith("commit;")
    assert "insert into ops.eval_run (eval_set, set_version, model, prompt_version, n_items, metrics, passed, notes)" in sql
    assert "insert into ops.eval_result (eval_run_id, item_key, expected, predicted, correct)" in sql
    assert "('guidance:12', '{\"verdict\":\"right\"}'::jsonb, '{\"metric\":\"capex\"}'::jsonb, true::boolean)" in sql
    assert "null::jsonb, false::boolean" in sql and "returning id" in sql
    assert sql.count("insert into ops.eval_run") == 1


def test_record_sql_escapes_quotes_and_checks_the_count():
    sql = s.record_sql([{**RUN, "notes": "Anand's \"verdicts\""}])
    assert "'Anand''s \"verdicts\"'" in sql
    with pytest.raises(ValueError):
        s.record_sql([{**RUN, "n_items": 3}])
    assert s.lit(None) == "null" and s.lit(True) == "true" and s.lit(2.5) == "2.5" and s.lit("a'b") == "'a''b'"


# ---------------------------------------------------------------------------------------------- the workflow

@pytest.mark.parametrize("inputs", [{"gate": "all", "seed": ""}, {}, {"gate": "targets", "seed": "7"},
                                    {"gate": "sitting2", "seed": ""}, {"gate": "recall", "seed": "9"}])
@pytest.mark.parametrize("dev_secrets", [True, False])
def test_the_workflow_reads_dev_only(inputs, dev_secrets):
    job_env, steps = resolve("gate-samples", "workflow_dispatch", inputs, secrets(dev_secrets))
    step = next(e for e in steps if "JOB_DATABASE_URL" in e)
    assert job_env["FT_TARGET"] == "dev"
    assert step["JOB_DATABASE_URL"] == ("DEV:JOB_DATABASE_URL" if dev_secrets else "")
    assert not [v for e in [job_env, *steps] for v in e.values() if "PROD:" in v]
    assert set(step) == {"GATE", "SEED", "JOB_DATABASE_URL"}


def test_the_workflow_passes_its_inputs_through_the_environment():
    from test_workflows import load
    wf = load("gate-samples")
    run = next(st["run"] for st in wf["jobs"]["run"]["steps"] if st.get("name") == "Draw")
    assert "${{" not in run and '"$GATE"' in run and '"$SEED"' in run
    assert wf["on"]["workflow_dispatch"]["inputs"]["gate"]["options"] == ["all", "sitting2", *s.ALL_GATES]


def test_the_page_map_check_uses_no_reserved_word_as_a_column_name():
    # `overlaps` is reserved in Postgres: the first run of this query failed on dev with a syntax error (7 Nov 2026)
    assert not re.search(r"\bas (overlaps|gaps)\b", s.PAGE_MAP_SQL) and "n_overlap" in s.PAGE_MAP_SQL
    assert "count(*) filter (where n_pages > 0)" in s.PAGE_MAP_SQL


def test_link_sql_takes_the_keys_the_draw_prints_and_finds_the_company_through_the_document():
    sql = s.link_sql(["1400:6", (948, 5)])
    assert "(1400, 6), (948, 5)" in sql
    assert "join core.entity e on e.id = d.entity_id" in sql and "o.entity_id" not in sql
    with pytest.raises(ValueError):
        s.link_sql(["1400:6:1"])


# ---------------------------------------------------------------------------------------------- sitting 2: the summaries

PEERS = ("autos", "banks", "fmcg", "it-services", "pharma", "steel", "nbfc", "power", "transport", "consumer-retail")


def summary_pool(n=64, seed=11):
    rnd = random.Random(seed)
    return [s.Item(id=1000 + i, company=i, peer=PEERS[rnd.randrange(len(PEERS))], role_held=rnd.random() < 0.25,
                   no_mgmt_turn=rnd.random() < 0.06, document=i) for i in range(1, n + 1)]


def test_the_summary_sample_is_ten_one_a_company_two_a_peer_group_with_the_role_held_and_no_turn_calls():
    for seed in (1, 2, 3, 4):
        pool = summary_pool(seed=seed)
        d = s.draw(pool, "summaries")
        assert len(d.items) == 10 and d.shortfalls == []
        assert len({it.company for it in d.items}) == 10
        assert max(Counterish(it.peer for it in d.items).values()) <= 2
        assert sum(it.role_held for it in d.items) >= 3
        if any(it.no_mgmt_turn for it in pool):
            assert sum(it.no_mgmt_turn for it in d.items) >= 1


class Counterish(dict):
    def __init__(self, it):
        super().__init__()
        for x in it:
            self[x] = self.get(x, 0) + 1


def test_a_call_with_no_management_turn_is_a_minimum_only_where_the_pool_has_one():
    pool = [s.Item(id=i, company=i, peer=PEERS[i % 10], role_held=i % 3 == 0, no_mgmt_turn=False) for i in range(1, 60)]
    d = s.draw(pool, "summaries")
    assert d.shortfalls == [] and len(d.items) == 10                       # none stored: 0 of 0 is not a shortfall
    with_one = pool[:-1] + [s.Item(id=999, company=999, peer="pharma", role_held=False, no_mgmt_turn=True)]
    assert any(it.no_mgmt_turn for it in s.draw(with_one, "summaries").items)


def test_the_role_held_minimum_is_not_cut_when_the_pool_is_short_of_it():
    pool = [s.Item(id=i, company=i, peer=PEERS[i % 10], role_held=i == 1) for i in range(1, 40)]
    d = s.draw(pool, "summaries")
    assert "role_held: 1 of 3" in d.shortfalls and len(d.items) == 10      # reported, never filled by loosening another rule


def test_the_peer_group_cap_is_a_rule_of_the_draw():
    pool = [s.Item(id=i, company=i, peer="banks" if i <= 30 else "fmcg") for i in range(1, 61)]
    d = s.draw(pool, "summaries")
    assert len(d.items) == 4 and "total: 4 of 10" in d.shortfalls          # two peer groups at two each


def test_the_summary_pool_reads_the_final_prompt_version_and_the_company_from_the_document():
    assert s.SUMMARY_VERSION == "v8"
    assert "o.prompt_version = %s" in s.POOL_SUMMARIES and "o.status = 'published'" in s.POOL_SUMMARIES
    assert "d.entity_id" in s.POOL_SUMMARIES and "o.entity_id" not in s.POOL_SUMMARIES
    assert "unknown_role_share > 0.10" in s.POOL_SUMMARIES


# ---------------------------------------------------------------------------------------------- sitting 2: recall

def recall_pool(per_bucket=12, others=10):
    out, n = [], 0
    for peers in s.RECALL_BUCKETS.values():
        for k in range(per_bucket):
            n += 1
            out.append(s.Item(id=n, company=n, peer=peers[k % len(peers)], bucket=s.bucket_of(peers[k % len(peers)]), document=n))
    for k in range(others):
        n += 1
        out.append(s.Item(id=n, company=n, peer="pharma", bucket=s.bucket_of("pharma"), document=n))
    return out


def test_recall_takes_one_call_from_each_of_five_sectors_then_three_more_from_other_companies():
    pool = recall_pool()
    d = s.draw_recall(pool)
    assert len(d.items) == 5 and len(d.extra) == 3 and d.shortfalls == []
    assert sorted(it.bucket for it in d.items) == sorted(s.RECALL_BUCKETS)
    everyone = [*d.items, *d.extra]
    assert len({it.company for it in everyone}) == 8 and len({it.id for it in everyone}) == 8
    st = d.strata()
    assert st["extra"] == 3 and st["companies"] == 8 and all(st[b] == 1 for b in s.RECALL_BUCKETS)


def test_recall_is_the_same_whatever_the_pool_order_and_a_new_seed_is_a_new_sample():
    pool = recall_pool()
    shuffled = pool[:]
    random.Random(4).shuffle(shuffled)
    a, b = s.draw_recall(pool), s.draw_recall(shuffled)
    assert ids(a) == ids(b) and [it.id for it in a.extra] == [it.id for it in b.extra]
    c = s.draw_recall(pool, 77)
    assert c.seed == 77 and (ids(c), [it.id for it in c.extra]) != (ids(a), [it.id for it in a.extra])


def test_recall_reports_a_sector_with_no_call_and_never_borrows_from_another():
    pool = [it for it in recall_pool() if it.bucket != "banks"]
    d = s.draw_recall(pool)
    assert "cell banks: 0 of 1" in d.shortfalls and len(d.items) == 4
    assert all(it.bucket != "banks" for it in [*d.items, *d.extra])


def test_a_peer_group_in_no_bucket_can_only_be_an_extra_call():
    assert s.bucket_of("pharma") == "" and s.bucket_of("nbfc") == "" and s.bucket_of("life-insurance") == ""
    assert s.bucket_of("fmcg") == s.bucket_of("consumer-retail") == "consumer"
    assert s.bucket_of("it-services") == "it_services" and s.bucket_of("banks") == "banks"
    pool = [s.Item(id=i, company=i, peer="pharma", bucket="") for i in range(1, 20)]
    d = s.draw_recall(pool)
    assert d.items == [] and len(d.extra) == 3
    assert "extra: 3 of 3" not in d.shortfalls


def test_recall_buckets_cover_every_nifty_peer_group_of_the_five_sectors_once():
    flat = [p for peers in s.RECALL_BUCKETS.values() for p in peers]
    assert len(flat) == len(set(flat)) == 12
    assert set(s.RECALL_BUCKETS) == {"it_services", "banks", "consumer", "industrials_autos", "energy_materials"}


def test_the_recall_pool_is_calls_whose_roles_were_read_and_whose_extraction_is_published():
    assert "ec.unknown_role_share <= 0.10" in s.POOL_RECALL and "o.status = 'published'" in s.POOL_RECALL
    assert "o.task = 'guidance_extraction'" in s.POOL_RECALL


# ---------------------------------------------------------------------------------------------- sitting 2: scoring

def scoring_pool(banks=3, ceilings=0):
    rnd = random.Random(21)
    kinds = ["range"] * 24 + ["point"] * 20 + ["floor"] * 11 + ["direction"] * 11 + ["ceiling"] * ceilings
    out = []
    for i, kind in enumerate(kinds, start=1):
        out.append(s.Item(id=i, company=1 + i % 21, kind=kind, family="banks" if i <= banks else "pnl", document=i,
                          counted=rnd.random() < 0.45))
    return out


def test_the_scoring_sample_is_twenty_with_three_of_each_type_present_and_three_bank_metrics():
    pool = scoring_pool(banks=3)
    d = s.draw(pool, "scoring")
    assert len(d.items) == 20 and d.shortfalls == []
    kinds = Counterish(it.kind for it in d.items)
    assert all(kinds[k] >= 3 for k in ("range", "point", "floor", "direction")) and "ceiling" not in kinds
    assert sum(it.family == "banks" for it in d.items) >= 3
    assert max(Counterish(it.company for it in d.items).values()) <= 2


def test_a_type_the_pool_does_not_hold_is_not_a_shortfall_but_a_short_bank_count_is_reported():
    d = s.draw(scoring_pool(banks=1), "scoring")                         # no ceilings, one bank outcome: as on dev, 8 Nov 2026
    assert d.shortfalls == ["banks: 1 of 3"] and len(d.items) == 20
    assert sum(it.family == "banks" for it in d.items) == 1              # the one it has is in the sample
    with_ceiling = s.draw(scoring_pool(banks=3, ceilings=4), "scoring")
    assert sum(it.kind == "ceiling" for it in with_ceiling.items) >= 3 and with_ceiling.shortfalls == []


def test_a_type_with_fewer_than_three_items_is_cut_to_what_the_pool_has():
    pool = [s.Item(id=i, company=i, kind="range", family="pnl") for i in range(1, 30)]
    pool += [s.Item(id=100, company=100, kind="ceiling", family="pnl"), s.Item(id=101, company=101, kind="ceiling", family="pnl")]
    d = s.draw(pool, "scoring")
    assert sum(it.kind == "ceiling" for it in d.items) == 2 and not [x for x in d.shortfalls if x.startswith("ceiling")]


def test_all_of_a_small_pool_is_drawn():
    pool = scoring_pool(banks=3)[:15]
    d = s.draw(pool, "scoring")
    assert len(d.items) == 15 and {it.id for it in d.items} == {it.id for it in pool}
    assert d.shortfalls == ["total: 15 of 20"]


def test_the_scoring_sample_is_seeded_and_reports_how_many_outcomes_count():
    pool = scoring_pool()
    a, b = s.draw(pool, "scoring"), s.draw(pool[::-1], "scoring")
    assert ids(a) == ids(b) and ids(s.draw(pool, "scoring", 5)) != ids(a)
    assert a.strata()["counted"] == sum(it.counted for it in a.items)


def test_the_scoring_pool_is_management_outcomes_that_were_scored():
    assert "o.outcome in ('delivered', 'missed')" in s.POOL_SCORING and "o.speaker_role = 'management'" in s.POOL_SCORING
    assert "g.duplicate_of is null" in s.POOL_SCORING


# ---------------------------------------------------------------------------------------------- sitting 2: what is shown

def test_the_recomputers_packet_holds_the_statement_and_nothing_of_the_scorers_result():
    sql = s.scoring_packet_sql([5, 3])
    assert "where x.id in (5, 3)" in sql and "x.target_start" in sql and "x.comparison" in sql and "c.fy_end_month" in sql
    assert not re.search(r"guidance_outcome|outcome|\bactual\b|\binputs\b|relation|\bbase\b|counted|is_first", sql)
    stored = s.scoring_stored_sql([5, 3])
    assert "equity.guidance_outcome o" in stored and "o.inputs" in stored and "where o.guidance_id in (5, 3)" in stored


def test_the_readers_queries_never_touch_the_extraction_and_the_comparison_query_is_the_only_one_that_does():
    calls = s.recall_calls_sql([1, 2])
    chunks = s.recall_chunks_sql(5241, 1, 25)
    for sql in (calls, chunks):
        assert not re.search(r"equity\.guidance|stated_figure|ai_output|ai_citation", sql)
    assert "ch.chunk_no between 1 and 25" in chunks and "ch.document_id = 5241" in chunks and "t.role" in chunks
    assert "equity.guidance x" in s.recall_extraction_sql([1]) and "x.speaker_role = 'management'" in s.recall_extraction_sql([1])


def test_the_display_queries_take_numbers_only_and_only_read():
    queries = [s.summary_sql([1, 2]), s.summary_citations_sql([1, 2]), s.recall_calls_sql([1]), s.recall_chunks_sql(1, 2, 3),
               s.recall_extraction_sql([1]), s.scoring_packet_sql([1]), s.scoring_stored_sql([1])]
    for sql in queries:
        assert sql.lstrip().lower().startswith(("select", "with")) and not WRITES.search(sql)
    for call in (lambda: s.summary_sql([]), lambda: s.scoring_packet_sql([]), lambda: s.recall_extraction_sql([])):
        with pytest.raises(ValueError):
            call()
    for call in (lambda: s.summary_sql(["1); drop table x; --"]), lambda: s.recall_chunks_sql("1; drop table x", 1, 2),
                 lambda: s.scoring_stored_sql(["1) or (1=1"])):
        with pytest.raises((ValueError, TypeError)):
            call()


def test_the_summary_citations_use_the_link_builder_the_page_links_use():
    sql = s.summary_citations_sql([7, 8])
    assert "where x.ai_output_id in (7, 8)" in sql and "#page=" in sql and "join core.entity e on e.id = d.entity_id" in sql
    assert "o.entity_id" not in sql


# ---------------------------------------------------------------------------------------------- the command line

class FakeConn:
    def __init__(self, pools):
        self.pools, self.seen = pools, []

    def execute(self, sql, params=None):
        self.seen.append((sql, params))
        rows = self.pools.get(sql, [])

        class Result:
            def fetchall(self_):
                return rows

            def fetchone(self_):
                return rows[0] if rows else None
        return Result()


def fake_pools():
    rnd = random.Random(2)
    summaries = [(1000 + i, i, i % 4 == 0, i == 9, PEERS[i % 10], i) for i in range(1, 65)]
    recall = [(2000 + i, i, p, i) for i in range(1, 80) for p in [rnd.choice(
        [*sum(s.RECALL_BUCKETS.values(), ()), "pharma", "nbfc"])]]
    scoring = [(3000 + i, 1 + i % 21, k, "advances_growth" if i < 4 else "revenue", i, i % 2 == 0)
               for i, k in enumerate(["range"] * 20 + ["point"] * 20 + ["floor"] * 10 + ["direction"] * 10, start=1)]
    return {s.POOL_SUMMARIES: summaries, s.POOL_RECALL: recall, s.POOL_SCORING: scoring}


def run_main(monkeypatch, argv):
    import contextlib
    import json as _json

    from ft.jobs import runner
    conn = FakeConn(fake_pools())

    @contextlib.contextmanager
    def connect(dsn, autocommit=True, role=""):
        assert dsn == "postgresql://dev" and role == "gate-samples"
        yield conn
    monkeypatch.setattr(runner, "connect", connect)
    lines = []
    assert s.main(argv, env={"JOB_DATABASE_URL": "postgresql://dev"}, out=lines.append) == 0
    bodies = [_json.loads(x.split("::", 1)[1].split("::", 1)[1].replace("%0A", "\n")) for x in lines]
    return conn, lines, bodies


def test_sitting2_prints_three_samples_with_their_seeds_strata_and_the_extra_recall_calls(monkeypatch):
    conn, lines, bodies = run_main(monkeypatch, ["--gate", "sitting2"])
    assert [b["gate"] for b in bodies] == ["summaries", "recall", "scoring"] and {b["sitting"] for b in bodies} == {2}
    assert [b["seed"] for b in bodies] == [2026110801, 2026110802, 2026110803]
    assert all(b["rule_version"] == s.RULE_VERSION == 3 for b in bodies)
    summaries, recall, scoring = bodies
    assert summaries["n"] == 10 and recall["n"] == 5 and len(recall["extra_ids"]) == 3 and scoring["n"] == 20
    assert "extra_ids" not in summaries and "page map check" not in " ".join(lines)
    assert ("set default_transaction_read_only = on", None) == conn.seen[0]
    assert (s.POOL_SUMMARIES, ("v8",)) in conn.seen


def test_all_still_draws_sitting_ones_four_samples_and_the_page_map_check(monkeypatch):
    import contextlib
    import json as _json

    from ft.jobs import runner
    fc = FakeConn({s.PAGE_MAP_SQL: [(328, 307, 21, 0, 0, 0, 0, 0)]})

    @contextlib.contextmanager
    def connect(dsn, autocommit=True, role=""):
        yield fc
    monkeypatch.setattr(runner, "connect", connect)
    out = []
    assert s.main(["--gate", "all"], env={"JOB_DATABASE_URL": "postgresql://dev"}, out=out.append) == 0
    titles = [x.split("title=", 1)[1].split("::", 1)[0] for x in out]
    assert titles == [f"sample {g} seed {s.SEEDS[g]}" for g in s.GATES] + ["page map check"]
    page = _json.loads(out[-1].split("::", 2)[2])
    assert page["transcripts"] == 328 and page["with_pages"] == 307 and page["without_pages"] == 21


def test_one_gate_with_a_new_seed_and_the_refusals(monkeypatch, capsys):
    _conn, _lines, bodies = run_main(monkeypatch, ["--gate", "recall", "--seed", "99"])
    assert len(bodies) == 1 and bodies[0]["seed"] == 99 and bodies[0]["gate"] == "recall"
    with pytest.raises(SystemExit):
        s.main(["--gate", "sitting2", "--seed", "5"], env={"JOB_DATABASE_URL": "x"})
    with pytest.raises(SystemExit):
        s.main(["--gate", "scoring"], env={})
