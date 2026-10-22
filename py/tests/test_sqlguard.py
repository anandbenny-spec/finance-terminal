"""Text-to-SQL guard: attacks rejected; realistic queries accepted, re-printed, wrapped and run as sql_reader.
These cases are the shared fixture the guard must always pass (Architecture 03 §1.7)."""
import psycopg
import pytest
from conftest import dsn

from ft.ai.sqlguard import Rejected, guard

ATTACKS = {
    "set_config":            "select set_config('statement_timeout','0',false)",
    "advisory lock":         "select pg_advisory_lock(42)",
    "pg_stat_activity":      "select query from pg_stat_activity",
    "terminate backend":     "select pg_terminate_backend(123)",
    "base table":            "select * from core.event",
    "ops via subquery":      "select (select count(*) from ops.alert)",
    "writing CTE":           "with x as (insert into ops.setting values ('a','1','x') returning 1) select * from x",
    "two statements":        "select 1; select 2",
    "for update":            "select * from policy_meetings for update",
    "select into":           "select * into t from policy_meetings",
    "pg_sleep":              "select pg_sleep(10)",
    "current_setting":       "select current_setting('search_path')",
    "regclass cast":         "select 'core.event'::regclass",
    "set-returning in FROM": "select * from generate_series(1,1000000000)",
    "retrieval function":    "select * from semantic.search_chunks(null, 'x')",
    "information_schema":    "select * from information_schema.tables",
    "copy":                  "copy (select 1) to stdout",
    "explain":               "explain analyze select * from policy_meetings",
    "tablesample":           "select * from policy_meetings tablesample system (10)",
    "unknown view":          "select * from \"Policy_Meetings\"",
    "pg_catalog table":      "select * from pg_catalog.pg_roles",
    "db-qualified":          "select * from ft.semantic.policy_meetings",
    "xml function":          "select xmlparse(document '<a/>')",
    "current_user":          "select current_user",
    "lo_import":             "select lo_import('/etc/passwd')",
    "dblink":                "select * from dblink('host=x','select 1') as t(a int)",
    "not SQL":               "drop table core.event",
    "recursive CTE":         "with recursive r(n) as (select 1 union all select n + 1 from r) select count(*) from r",
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_rejects(name):
    with pytest.raises(Rejected):
        guard(ATTACKS[name])


VALID = [
    "select bank, meeting_date, policy_rate_pct from policy_meetings where bank = 'Fed' order by meeting_date desc limit 8",
    "select bank, count(*) filter (where is_dissent) as dissents from policy_votes group by bank",
    "select date_trunc('month', date) as m, avg(value) from market_daily where series_key = 'fred.DGS2' group by 1 order by 1",
    "with r as (select meeting_date, policy_rate_pct from policy_meetings where bank = 'Fed') select * from r",
    "select meeting_date, policy_rate_pct - lag(policy_rate_pct) over (order by meeting_date) as d from policy_meetings where bank = 'Fed'",
    "select extract(year from meeting_date) as y, sum(policy_rate_change_bps) from policy_meetings group by 1",
    "select * from semantic.policy_meetings where meeting_date >= current_date - interval '10 years'",
    "select percentile_cont(0.5) within group (order by value) from market_daily where series_key = 'fred.DGS2'",
    "select m.meeting_date, v.member_name from policy_meetings m join policy_votes v on v.meeting_date = m.meeting_date and v.bank = m.bank where v.is_dissent",
    "select coalesce(stance, 'n/a'), greatest(1, 2), nullif(1, 1), round(policy_rate_pct::numeric, 1) from policy_meetings -- trailing comment; drop table x",
    "select substr(title, 1, 4), substring(title from 1 for 4) from policy_meetings",
    "select trim(bank), trim(leading from title), trim(trailing ' ' from title), btrim(bank), ltrim(title), rtrim(title) from policy_meetings",
]


@pytest.mark.parametrize("sql", VALID)
def test_accepts_and_wraps(sql):
    wrapped = guard(sql)
    assert wrapped.endswith("LIMIT 200") and "drop table" not in wrapped.lower()


@pytest.mark.db
@pytest.mark.parametrize("sql", VALID)
def test_runs_as_sql_reader(sql):
    with psycopg.connect(dsn("sql_reader"), autocommit=True) as db, db.transaction():
        db.execute("set local transaction read only")
        db.execute("set local statement_timeout = '3s'")
        db.execute(guard(sql)).fetchall()
