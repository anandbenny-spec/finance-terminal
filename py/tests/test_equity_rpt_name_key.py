"""equity.rpt_name_key (migration 0040, issue #420) holds the same rule as ft.equity.related_parties.norm_name.

The `spelling` check of equity.related_parties finds counterparties spelt two ways with the Python rule; api.related_party_top
(0040) merges them with the SQL one. If the two ever disagree, the check would warn about names the view keeps apart or the
reverse, so this test holds them against each other on a fixed list of names. The list is ASCII: a letter outside ASCII is kept
or dropped by the database's character classes, which the migration's header says.
"""
from __future__ import annotations

import psycopg
import pytest
from conftest import dsn

from ft.equity import related_parties as rp

NAMES = [
    "Acme Ltd", "ACME LIMITED", "Acme Ltd.", "acme  limited", "Acme Private Limited",
    "Mahindra & Mahindra Limited", "MAHINDRA AND MAHINDRA LIMITED", "Mahindra and Mahindra Ltd.",
    "Gamma & Sons Limited", "Gamma and Sons Ltd", "Beta Trading Pvt. Ltd.", "Beta Trading Pvt Ltd",
    "XLtd", "Ltd", "LTD.", "ltd ltd", "Ltd-Co", "Co,Ltd", "A-B_C", "  A  B  ", "Tata Steel (Thailand) Public Co. Ltd",
    "Apollo Health & Lifestyle Limited", "Apollo Health and Lifestyle Ltd", "L&T Finance Ltd", "L & T Finance Limited",
    "ABC 123 Ltd", "ABC123 LIMITED", "Mr Arun Mehta", "MR. ARUN MEHTA", "Sub-1 Corp.", "Sub 1 Corp", "AT&T", "AT and T",
    "a_b", "A B", "---", "", "   ", "'Quoted' Ltd", "Quoted Limited",
]


@pytest.mark.db
def test_sql_key_equals_python_norm_name_on_a_fixed_list():
    with psycopg.connect(dsn("postgres"), autocommit=True) as c:
        for name in NAMES:
            (key,) = c.execute("select equity.rpt_name_key(%s)", (name,)).fetchone()
            assert key == rp.norm_name(name), f"{name!r}: SQL {key!r}, Python {rp.norm_name(name)!r}"


@pytest.mark.db
def test_sql_key_is_null_for_null():
    with psycopg.connect(dsn("postgres"), autocommit=True) as c:
        assert c.execute("select equity.rpt_name_key(null)").fetchone() == (None,)
