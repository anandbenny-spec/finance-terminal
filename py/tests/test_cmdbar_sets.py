"""The command bar's three evaluation sets (Spec 07 §6, N3, N4, N5; job K-02, #581): `cmdbar_sql`, `cmdbar_router` and
`retrieval` in py/config/eval/sets/. Ids unique; the splits and per-family counts; at least 8 cross-currency items and 8
unanswerable router items in the test splits; every gold through the guard with the full `semantic` view list (K-04 grows the
guard's VIEWS to it); no rows in the files (golds are SQL only); and, on the test database, every gold runs as sql_reader inside
the guard's read-only, 3-second wrapper."""
import re
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg
import pytest
import yaml
from conftest import dsn

from ft.ai import sqlguard

ROOT = Path(__file__).resolve().parents[2]
SETS = ROOT / "py" / "config" / "eval" / "sets"
MIGRATIONS = ROOT / "supabase" / "migrations"

POLICY_DOC_TYPES = {"fomc_statement", "fomc_minutes", "fomc_presconf_transcript", "mpc_resolution", "mpc_minutes",
                    "rbi_governor_statement", "rbi_presconf_transcript"}
BANK_OF = {"fomc_statement": "fed", "fomc_minutes": "fed", "fomc_presconf_transcript": "fed", "mpc_resolution": "rbi",
           "mpc_minutes": "rbi", "rbi_governor_statement": "rbi", "rbi_presconf_transcript": "rbi"}


def load(name: str) -> list[dict]:
    return yaml.safe_load((SETS / f"{name}.yaml").read_text())["items"]


SQL, ROUTER, RETRIEVAL = load("cmdbar_sql"), load("cmdbar_router"), load("retrieval")
GOLDS = [i for i in SQL if i["answerable"]]


def semantic_views() -> set[str]:
    """Every view in the `semantic` schema after all migrations, read from the migration files in order."""
    views: set[str] = set()
    made = re.compile(r"create\s+(?:or\s+replace\s+)?view\s+semantic\.(\w+)|drop\s+view\s+(?:if\s+exists\s+)?semantic\.(\w+)",
                      re.IGNORECASE)
    for f in sorted(MIGRATIONS.glob("*.sql")):
        for m in made.finditer(f.read_text()):
            if m.group(1):
                views.add(m.group(1).lower())
            else:
                views.discard(m.group(2).lower())
    return views


VIEWS = semantic_views()


@pytest.fixture
def full_guard(monkeypatch):
    """The guard with every semantic view allowed, as K-04 makes it; the module-1 list is what main holds today."""
    monkeypatch.setattr(sqlguard, "VIEWS", VIEWS)
    return sqlguard.guard


# ------------------------------------------------------------------ shared
def test_the_semantic_views_include_the_bars_eleven():
    assert VIEWS >= {"policy_meetings", "policy_votes", "policy_projections", "market_daily", "companies",
                     "company_financials", "company_valuations", "rate_decision_moves", "value_chain", "earnings_calls",
                     "call_guidance"}


def test_ids_are_unique_within_and_across_the_sets():
    ids = [i["id"] for i in SQL + ROUTER + RETRIEVAL]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"(sql|route|ret)-[a-z0-9-]+", i) for i in ids)


@pytest.mark.parametrize("items,fields,required", [
    (SQL, {"id", "split", "family", "question", "answerable", "gold_sql", "cross_currency", "ordered", "notes"},
     {"id", "split", "family", "question", "answerable", "cross_currency", "ordered", "notes"}),
    (ROUTER, {"id", "split", "question", "route", "page_slug", "filters", "notes"}, {"id", "split", "question", "route", "notes"}),
    (RETRIEVAL, {"id", "split", "question", "bank", "doc_type", "event_slug", "char_start", "char_end", "notes"},
     {"id", "split", "question", "bank", "doc_type", "event_slug", "char_start", "char_end", "notes"}),
], ids=["cmdbar_sql", "cmdbar_router", "retrieval"])
def test_items_carry_only_their_fields(items, fields, required):
    """No other key, so no stored result, row or answer can ride along (Spec 07 N4: golds are SQL, never rows)."""
    for i in items:
        assert required <= set(i) <= fields, i["id"]
        assert i["split"] in ("test", "dev"), i["id"]
        assert isinstance(i["question"], str) and i["question"].strip(), i["id"]
        assert isinstance(i["notes"], str) and i["notes"].strip(), i["id"]


def numbers_outside_quotes(sql: str) -> list[str]:
    return re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", re.sub(r"'[^']*'", "''", sql))


def test_no_figure_from_a_row_is_in_the_files():
    """Golds hold only the small thresholds and dates their questions state (a pasted figure from a row would be a large or
    precise number); numbers appear as YAML values only as retrieval's character offsets."""
    for i in GOLDS:
        for n in numbers_outside_quotes(i["gold_sql"]):
            assert float(n) <= 5000 and len(n.partition(".")[2]) <= 2, (i["id"], n)
    for i in SQL + ROUTER:
        assert not any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in i.values()), i["id"]


# ------------------------------------------------------------------ cmdbar_sql
def test_sql_splits_and_family_counts():
    assert Counter((i["split"], i["family"]) for i in SQL) == {
        ("test", "module1"): 16, ("test", "equity"): 25, ("test", "calls"): 10, ("test", "cross_currency"): 9,
        ("dev", "module1"): 3, ("dev", "equity"): 5, ("dev", "calls"): 2, ("dev", "cross_currency"): 2,
    }


def test_sql_has_at_least_eight_cross_currency_test_items():
    assert sum(i["cross_currency"] for i in SQL if i["split"] == "test") >= 8
    assert all(i["cross_currency"] == (i["family"] == "cross_currency") for i in SQL)
    assert all(i["answerable"] for i in SQL if i["cross_currency"])   # answered per market or by a ratio (Anand's option A, #580)


def test_sql_answerable_items_have_a_gold_and_the_rest_none():
    for i in SQL:
        assert isinstance(i["answerable"], bool) and isinstance(i["ordered"], bool), i["id"]
        assert ("gold_sql" in i) == i["answerable"], i["id"]
        if not i["answerable"]:
            assert not i["ordered"], i["id"]
    assert sum(not i["answerable"] for i in SQL if i["split"] == "test") >= 4


def test_sql_ordered_only_where_the_gold_orders():
    for i in GOLDS:
        if i["ordered"]:
            assert re.search(r"\border\s+by\b", i["gold_sql"], re.IGNORECASE), i["id"]


def test_spec_01_chips_one_to_five_are_test_items():
    questions = {i["question"] for i in SQL if i["split"] == "test"}
    assert {"When did the RBI last change the repo rate, and by how much?",
            "How many FOMC dissents were there in each year since 2019?",
            "Which MPC members dissented in 2024, and what did they want?",
            "What did the RBI project for CPI inflation at its latest meeting?",
            "How did the US 2-year yield move after each Fed decision in 2024?"} <= questions


@pytest.mark.parametrize("item", GOLDS, ids=[i["id"] for i in GOLDS])
def test_every_gold_passes_the_guard(item, full_guard):
    wrapped = full_guard(item["gold_sql"])
    assert wrapped.endswith(f"LIMIT {sqlguard.MAX_ROWS}")


def test_the_guard_still_refuses_a_view_outside_the_semantic_schema(full_guard):
    with pytest.raises(sqlguard.Rejected):
        full_guard("select * from equity.company")


def test_cross_currency_golds_never_aggregate_or_rank_money_across_markets():
    """A money column (revenue, profit, market value, price, an index level) is never summed, averaged or ordered unless the
    gold keeps one market per group or partition, and a company's money is shown with its currency; currency-neutral ratios
    (margins, growth, P/E, percent changes) may be ranked."""
    money = r"\b(revenue|pat|market_cap|price|value)\b"
    for i in (i for i in GOLDS if i["cross_currency"]):
        sql = i["gold_sql"].lower()
        if re.search(rf"\b(sum|avg|max|min)\s*\(\s*{money}", sql) or re.search(rf"order\s+by\s+{money}", sql):
            assert re.search(r"group\s+by\s+market|partition\s+by\s+(market|series_key|series_name|company)", sql), i["id"]
        first_select = sql.split("from")[0]
        if re.search(r"\b(revenue|pat|market_cap|price)\b", first_select):
            assert "currency" in first_select, i["id"]


# ------------------------------------------------------------------ cmdbar_router
def test_router_splits_and_route_counts():
    assert Counter((i["split"], i["route"]) for i in ROUTER) == {
        ("test", "data"): 13, ("test", "document"): 11, ("test", "page"): 8, ("test", "unsupported"): 8,
        ("dev", "data"): 3, ("dev", "document"): 3, ("dev", "page"): 2, ("dev", "unsupported"): 2,
    }


def test_router_has_at_least_eight_unanswerable_test_items():
    assert sum(i["route"] == "unsupported" for i in ROUTER if i["split"] == "test") >= 8


def test_router_page_slugs_and_filters():
    for i in ROUTER:
        assert ("page_slug" not in i) or i["route"] == "page", i["id"]
        assert ("filters" not in i) or i["route"] == "document", i["id"]
        f = i.get("filters")
        if f is None:
            continue
        assert set(f) <= {"banks", "doc_types", "date_from", "date_to"} and f, i["id"]
        assert set(f.get("banks", [])) <= {"fed", "rbi"}, i["id"]
        assert set(f.get("doc_types", [])) <= POLICY_DOC_TYPES, i["id"]
        if "date_from" in f or "date_to" in f:
            lo, hi = date.fromisoformat(f["date_from"]), date.fromisoformat(f["date_to"])
            assert lo <= hi, i["id"]
        for t in f.get("doc_types", []):
            assert BANK_OF[t] in f.get("banks", [BANK_OF[t]]), i["id"]


# ------------------------------------------------------------------ retrieval
def test_retrieval_splits():
    assert Counter(i["split"] for i in RETRIEVAL) == {"test": 20, "dev": 5}


def test_retrieval_covers_both_banks_the_seven_types_and_the_years():
    test = [i for i in RETRIEVAL if i["split"] == "test"]
    assert {i["doc_type"] for i in test} == POLICY_DOC_TYPES
    assert Counter(i["bank"] for i in test) == {"fed": 9, "rbi": 11}
    years = {i["event_slug"].rsplit("-", 3)[1] for i in test}
    assert len(years) >= 6


def test_retrieval_items_are_policy_documents_only():
    """Spec 07 N3: Fed and RBI policy documents only, never an earnings call."""
    for i in RETRIEVAL:
        assert i["doc_type"] in POLICY_DOC_TYPES, i["id"]
        assert i["bank"] == BANK_OF[i["doc_type"]], i["id"]
        prefix = "fomc" if i["bank"] == "fed" else "mpc"
        assert re.fullmatch(rf"{prefix}-\d{{4}}-\d{{2}}-\d{{2}}", i["event_slug"]), i["id"]
        assert "call" not in i["doc_type"], i["id"]


def test_retrieval_spans_are_offsets():
    seen = set()
    for i in RETRIEVAL:
        s, e = i["char_start"], i["char_end"]
        assert isinstance(s, int) and isinstance(e, int) and 0 <= s < e, i["id"]
        key = (i["doc_type"], i["event_slug"], s)
        assert key not in seen, i["id"]
        seen.add(key)


# ------------------------------------------------------------------ database: every gold runs as sql_reader
@pytest.mark.db
@pytest.mark.parametrize("item", GOLDS, ids=[i["id"] for i in GOLDS])
def test_every_gold_runs_as_sql_reader(item, full_guard):
    """Against the test database built from every migration: the guard's re-printed query runs as sql_reader, read-only, with
    the 3-second limit (Arch 03 §1.7), so every column and view a gold names exists and sql_reader can read it."""
    with psycopg.connect(dsn("sql_reader"), autocommit=True) as db, db.transaction():
        db.execute("set local transaction read only")
        db.execute("set local statement_timeout = '3s'")
        db.execute(full_guard(item["gold_sql"])).fetchall()
