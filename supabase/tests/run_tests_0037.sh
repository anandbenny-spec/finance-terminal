#!/bin/bash
# Tests for 0037 (equity.rpt_line stores signed amounts; issue #190). Run after the other scripts on the same fresh database.
# Read-only: it looks at the catalogue. (0034's T09 inserts a negative row inside a rolled-back transaction.)
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg() { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

check "N01 no check constraint forbids a negative value_in_period" \
  "$(as_pg "select count(*) from pg_constraint where conrelid = 'equity.rpt_line'::regclass and contype = 'c' and pg_get_constraintdef(oid) like '%value_in_period%'")" '^0$'
check "N02 the value stays numeric and not null" \
  "$(as_pg "select data_type || ':' || is_nullable from information_schema.columns where table_schema = 'equity' and table_name = 'rpt_line' and column_name = 'value_in_period'")" '^numeric:NO$'
check "N03 the period check is untouched" \
  "$(as_pg "select count(*) from pg_constraint where conrelid = 'equity.rpt_line'::regclass and contype = 'c' and pg_get_constraintdef(oid) like '%period_end >= period_start%'")" '^1$'
check "N04 the column comment says amounts are signed" \
  "$(as_pg "select (col_description('equity.rpt_line'::regclass, (select attnum from pg_attribute where attrelid = 'equity.rpt_line'::regclass and attname = 'value_in_period')) like '%signed%')::text")" '^true$'

echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
