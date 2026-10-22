import os

import pytest

PG = os.environ.get("TEST_PG_CONNINFO", "host=localhost port=5432 dbname=ft")


def dsn(user: str) -> str:
    return f"{PG} user={user}"


def pytest_collection_modifyitems(config, items):
    """Database tests need the test database built by supabase/tests/run_all.sh; skip them if it's absent."""
    if os.environ.get("TEST_PG_CONNINFO"):
        return
    skip = pytest.mark.skip(reason="TEST_PG_CONNINFO not set (build the test DB with supabase/tests/run_all.sh)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)
