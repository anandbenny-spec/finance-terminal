#!/bin/bash
# Tests for 0033 (equity.earnings_call.analysis_version; issue #341, job 2B-09b; Spec 02B §5.3). Run after the other scripts on the same
# fresh database. The script creates its own rows (company ec33-a with one call), checks through the roles and removes every row it made.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec33%');
         delete from core.event where slug like 'ec33%';
         delete from core.entity where slug like 'ec33%';" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec33-a', 'Ec33 A Ltd', 'ec33-a', 'IN', 'INR', true);
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
  select 'earnings_call', e.id, 'ec33-call', 'ec33', '2026-10-25'::date, 'stored', (select id from core.source where key = 'nse'), true
  from core.entity e where e.slug = 'ec33-a';
insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange, route)
  select ev.id, ev.entity_id, 'Q2 FY27', '2026-09-30'::date, 'q2-fy27', 'results_link', '2026-10-26', 'NSE', 'none'
  from core.event ev where ev.slug = 'ec33-call';
commit;
SQL
)"
check "A01 the test rows load" "$SETUP_OUT" '^(BEGIN|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|])*$'

# --- the column
check "C01 analysis_version is a nullable text column on equity.earnings_call" \
  "$(as_pg "select data_type || ':' || is_nullable from information_schema.columns where table_schema = 'equity' and table_name = 'earnings_call' and column_name = 'analysis_version'")" '^text:YES$'
check "C02 a call written without it has null (existing rows stay null)" \
  "$(as_pg "select analysis_version is null from equity.earnings_call where event_id in (select id from core.event where slug = 'ec33-call')")" '^t$'
check "C03 the column has a comment that says what it holds" \
  "$(as_pg "select col_description('equity.earnings_call'::regclass, (select attnum from pg_attribute where attrelid = 'equity.earnings_call'::regclass and attname = 'analysis_version')) like '%transcript%analysed under%'")" '^t$'
check "C04 the table keeps its other columns: parser_version is still there" \
  "$(as_pg "select count(*) from information_schema.columns where table_schema = 'equity' and table_name = 'earnings_call' and column_name in ('parser_version','analysis_version')")" '^2$'

# --- privileges
check "P01 job_writer can set, change and clear it" \
  "$(as_role job_writer "update equity.earnings_call set analysis_version = 'abc123' where event_id in (select id from core.event where slug = 'ec33-call'); update equity.earnings_call set analysis_version = 'def456' where event_id in (select id from core.event where slug = 'ec33-call'); update equity.earnings_call set analysis_version = null where event_id in (select id from core.event where slug = 'ec33-call'); select 'ok'")" 'ok$'
check "P02 the value written is the value read back" \
  "$(as_pg "update equity.earnings_call set analysis_version = 'v-test-1' where event_id in (select id from core.event where slug = 'ec33-call'); select analysis_version from equity.earnings_call where event_id in (select id from core.event where slug = 'ec33-call')" | tail -1)" '^v-test-1$'
check "P03 anon can't write it" \
  "$(as_role anon "update equity.earnings_call set analysis_version = 'x' where event_id in (select id from core.event where slug = 'ec33-call')")" 'permission denied'
check "P04 authenticated can't write it" \
  "$(as_role authenticated "update equity.earnings_call set analysis_version = 'x' where event_id in (select id from core.event where slug = 'ec33-call')")" 'permission denied'
check "P05 sql_reader can't read the base table (the semantic views only)" \
  "$(as_role sql_reader "select analysis_version from equity.earnings_call")" 'permission denied'

# --- visitors never see it
check "V01 no api or semantic view carries the column" \
  "$(as_pg "select count(*) from information_schema.columns where table_schema in ('api','semantic') and column_name = 'analysis_version'")" '^0$'

cleanup
echo; echo "passed $pass, failed $fail"
