#!/bin/bash
# Tests for 0057 (a US filing's "Filed" is the filing date SEC assigns; issue #448). Run after the other scripts on the same fresh
# database. Structural checks only: the new column, the view's appended column, and that the view kept its options and grants.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
Q() { psql $H -U postgres -v ON_ERROR_STOP=1 -c "$1" 2>&1; }

check "equity.filing.filing_date is a nullable date" \
  "$(Q "select data_type || ':' || is_nullable from information_schema.columns where table_schema = 'equity' and table_name = 'filing' and column_name = 'filing_date'")" '^date:YES$'
check "the column carries a comment" \
  "$(Q "select col_description('equity.filing'::regclass, (select attnum from pg_attribute where attrelid = 'equity.filing'::regclass and attname = 'filing_date')) is not null")" '^t$'
check "api.company_financials ends with filing_date, after attribution" \
  "$(Q "select string_agg(attname, ',' order by attnum) from (select attname, attnum from pg_attribute where attrelid = 'api.company_financials'::regclass and attnum > 0 and not attisdropped order by attnum desc limit 2) x")" '^attribution,filing_date$'
check "api.company_financials is still security_invoker" \
  "$(Q "select coalesce(reloptions::text, '') from pg_class where oid = 'api.company_financials'::regclass")" 'security_invoker=true'
check "anon can read filing_date through the view" \
  "$(Q "select has_column_privilege('anon', 'api.company_financials', 'filing_date', 'select')")" '^t$'
check "filed_at is still in the view (the acceptance instant stays)" \
  "$(Q "select count(*) from pg_attribute where attrelid = 'api.company_financials'::regclass and attname = 'filed_at'")" '^1$'
check "the view reads without error as anon" \
  "$(Q "set role anon; select count(*) >= 0 from api.company_financials where filing_date is null")" 't$'

echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
