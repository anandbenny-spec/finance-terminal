"""py/config/equity/brsr_fields.yaml and the migration's field list agree (#187, job VC-02; the card's decision 4).

The YAML holds each field's element and valid-from date for the task; the migration's `equity.brsr_card` holds the key, label,
item reference, kind and parent for the labels and the "Not reported" rows. A change to one without the other fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = ROOT / "py" / "config" / "equity" / "brsr_fields.yaml"
MIGRATION = ROOT / "supabase" / "migrations" / "20261110003400_value_chain.sql"

# The 13 fields of Data Audit 08's kit (kit/brsr.py FIELDS: 15) less the two loan and investment shares (D2).
EXPECTED_KEYS = {
    "exports_share", "states_served", "countries_served", "trading_house_share", "trading_houses", "top10_trading_share",
    "dealer_share", "dealers", "top10_dealer_share", "rpt_purchases_share", "rpt_sales_share", "msme_share",
    "within_india_share",
}
# The item references Anand fixed on #187 (29 Oct 2026, 22:49 IST).
DECIDED_ITEM_REFS = {
    "states_served": "A-III-19(a), States",
    "countries_served": "A-III-19(a), Countries",
    "exports_share": "A-III-19(b)",
    "trading_house_share": "P1-EI-9, Purchases (a)",
    "trading_houses": "P1-EI-9, Purchases (b)",
    "top10_trading_share": "P1-EI-9, Purchases (c)",
    "dealer_share": "P1-EI-9, Sales (a)",
    "dealers": "P1-EI-9, Sales (b)",
    "top10_dealer_share": "P1-EI-9, Sales (c)",
    "rpt_purchases_share": "P1-EI-9, RPTs (a)",
    "rpt_sales_share": "P1-EI-9, RPTs (b)",
    "msme_share": "P8-EI-4, MSMEs",
    "within_india_share": "P8-EI-4, Within India",
}


def _yaml() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def _sql_fields() -> list[tuple[str, int, str, str, str, str | None]]:
    """The VALUES rows between the BEGIN and END markers of equity.brsr_card, in file order."""
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("-- BEGIN brsr fields", 1)[1].split("-- END brsr fields", 1)[0]
    row = re.compile(
        r"\(\s*'(?P<key>[a-z0-9_]+)'\s*,\s*(?P<no>\d+)\s*,\s*'(?P<label>(?:[^']|'')*)'\s*,\s*'(?P<ref>(?:[^']|'')*)'\s*,"
        r"\s*'(?P<kind>share|count)'\s*,\s*(?:null(?:::text)?|'(?P<parent>[a-z0-9_]+)')\s*\)"
    )
    out = []
    for m in row.finditer(block):
        out.append((m["key"], int(m["no"]), m["label"].replace("''", "'"), m["ref"].replace("''", "'"), m["kind"], m["parent"]))
    return out


def test_the_yaml_holds_the_13_card_fields():
    fields = _yaml()["fields"]
    assert {f["key"] for f in fields} == EXPECTED_KEYS
    assert len(fields) == 13


def test_item_references_are_the_decided_strings():
    got = {f["key"]: f["item_ref"] for f in _yaml()["fields"]}
    assert got == DECIDED_ITEM_REFS


def test_elements_are_percentage_or_number_elements_and_unique():
    elements = [f["element"] for f in _yaml()["fields"]]
    assert len(set(elements)) == len(elements)
    for f in _yaml()["fields"]:
        assert f["element"].startswith(("PercentageOf", "NumberOf")), f["key"]  # never Amount... (Data Audit 08 §3.5)
        assert (f["kind"] == "share") == f["element"].startswith("PercentageOf"), f["key"]


def test_every_field_is_valid_from_the_first_shown_entry_point():
    y = _yaml()
    assert y["entry_points"]["shown"] == ["2024-04-30", "2025-05-31", "2026-02-28"]
    assert all(f["valid_from"] == "2024-04-30" for f in y["fields"])


def test_parents_exist_and_only_top_10_shares_have_one():
    keys = {f["key"] for f in _yaml()["fields"]}
    for f in _yaml()["fields"]:
        assert f["parent"] is None or f["parent"] in keys
        assert (f["parent"] is not None) == f["key"].startswith("top10_")


def test_the_migrations_field_list_agrees_with_the_yaml():
    yaml_fields = _yaml()["fields"]
    sql = _sql_fields()
    assert [s[0] for s in sql] == [f["key"] for f in yaml_fields], "same keys in the same order"
    assert [s[1] for s in sql] == list(range(1, 14)), "field_no runs 1 to 13"
    for s, f in zip(sql, yaml_fields):
        assert s[2] == f["label"], f"label of {f['key']}"
        assert s[3] == f["item_ref"], f"item reference of {f['key']}"
        assert s[4] == f["kind"], f"kind of {f['key']}"
        assert s[5] == f["parent"], f"parent of {f['key']}"


def test_the_migrations_shown_filter_is_the_first_shown_entry_point():
    sql = MIGRATION.read_text(encoding="utf-8")
    first = _yaml()["entry_points"]["shown"][0]
    assert f"format_version >= date '{first}'" in sql
    # equity.brsr_filing_shown, equity.brsr_card's source, and semantic.value_chain's own copy use the same date
    assert sql.count(f"format_version >= date '{first}'") == 2


def test_the_semantic_view_names_every_field():
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("create view semantic.value_chain", 1)[1].split("alter view semantic.value_chain", 1)[0]
    for key in EXPECTED_KEYS:
        assert f"v.field_key = '{key}'" in block, key
        assert f"as {key}" in block, key
