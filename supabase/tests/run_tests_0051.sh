#!/bin/bash
# Tests for 0051 (an index on equity.filing (event_id); issue #577, job 2B-16e). Run after the other scripts on the same fresh
# database; run_tests_0050.sh keeps checking the view that uses it. Connection from the environment (run_all.sh sets
# PGHOST/PGPORT/PGDATABASE). The script creates its own rows (named r51-*), checks the plan and removes them.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg() { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from equity.filing where document_id in (select id from core.document where external_key like 'r51-%');
         delete from core.document where external_key like 'r51-%';
         delete from core.event where slug like 'r51-%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'r51-%');
         delete from core.entity where slug like 'r51-%';" >/dev/null
}
cleanup

check "T1 the index exists on equity.filing" \
  "$(as_pg "select count(*) from pg_indexes where schemaname = 'equity' and tablename = 'filing' and indexname = 'filing_event_id_idx'")" '^1$'
check "T2 it covers the single column event_id" \
  "$(as_pg "select string_agg(a.attname, ',' order by k.ord) from pg_index i join pg_class c on c.oid = i.indexrelid join unnest(i.indkey) with ordinality k(attnum, ord) on true join pg_attribute a on a.attrelid = i.indrelid and a.attnum = k.attnum where c.relname = 'filing_event_id_idx' and c.relnamespace = 'equity'::regnamespace")" '^event_id$'
check "T3 it is valid and ready, and not unique or partial" \
  "$(as_pg "select i.indisvalid and i.indisready and not i.indisunique and i.indpred is null from pg_index i join pg_class c on c.oid = i.indexrelid where c.relname = 'filing_event_id_idx' and c.relnamespace = 'equity'::regnamespace")" '^t$'

psql $H -U postgres -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || echo "FAIL  setup of the test rows"
insert into core.entity (kind, slug, name, market, currency, is_published) values ('company','r51-a','R51 A Ltd','IN','INR',true);
insert into equity.company (entity_id) select id from core.entity where slug = 'r51-a';
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, is_published)
  select 'results_filing', id, 'r51-a1', 'r51-a1', date '2025-08-01', timestamptz '2025-08-01 18:00+05:30', 'filed', true
    from core.entity where slug = 'r51-a';
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
  select 'results_xbrl', e.entity_id, 'r51-a1', (select id from core.source where key = 'nse'), 'r51-a1', 'https://example.invalid/r51-a1'
    from core.event e where e.slug = 'r51-a1';
insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, source_url, exchange)
  select d.id, d.entity_id, e.id, 'consolidated', 'ind_as', date '2025-06-30', d.source_url, 'NSE'
    from core.document d join core.event e on e.slug = 'r51-a1' where d.external_key = 'r51-a1';
SQL

check "T4 a filing found by its event_id is found through filing_event_id_idx (sequential scans switched off)" \
  "$(psql $H -U postgres -c "begin; set local enable_seqscan = off; explain select 1 from equity.filing where event_id = (select id from core.event where slug = 'r51-a1'); rollback;" 2>&1 | grep -c 'filing_event_id_idx')" '^[1-9]'
check "T5 the lookup returns that one filing" \
  "$(as_pg "select count(*) from equity.filing f join core.event e on e.id = f.event_id where e.slug = 'r51-a1'")" '^1$'

cleanup
echo
echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
