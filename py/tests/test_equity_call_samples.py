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
    assert s.SEEDS == {"targets": 2026110701, "unattributed": 2026110702, "stated": 2026110703, "links": 2026110704}
    assert s.GATES == tuple(s.SEEDS) == tuple(s.RULES)
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

READ_ONLY_SQL = (s.POOL_TARGETS, s.POOL_STATED, s.POOL_LINKS, s.PAGE_MAP_SQL)
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

@pytest.mark.parametrize("inputs", [{"gate": "all", "seed": ""}, {}, {"gate": "targets", "seed": "7"}])
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
    assert wf["on"]["workflow_dispatch"]["inputs"]["gate"]["options"] == ["all", *s.GATES]


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
