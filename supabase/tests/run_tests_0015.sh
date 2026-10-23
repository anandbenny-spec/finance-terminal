#!/bin/bash
# Tests for 0015 (the Treasury yields' source line). Run after the other scripts on the same fresh database.
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

check "Y01 fred.DGS2 and fred.DGS10 name U.S. Treasury, then the Board's H.15 and FRED" \
  "$(as_pg "select string_agg(key||'='||attribution, ',' order by key) from core.series where key in ('fred.DGS2','fred.DGS10')")" \
  '^fred\.DGS10=Source: U\.S\. Treasury, via Federal Reserve Board \(H\.15\) and FRED,fred\.DGS2=Source: U\.S\. Treasury, via Federal Reserve Board \(H\.15\) and FRED$'
check "Y02 no other series names H.15 (the H.10 and other lines are unchanged)" \
  "$(as_pg "select coalesce(string_agg(key, ',' order by key), 'none') from core.series where attribution like '%H.15%' and key not in ('fred.DGS2','fred.DGS10')")" '^none$'
check "Y03 the H.10 series still read as before" \
  "$(as_pg "select count(*) from core.series where key in ('fred.DTWEXBGS','fred.DEXINUS') and attribution = 'Source: Federal Reserve Board (H.10), via FRED'")" '^2$'
check "Y04 api.series shows the new line (the series' own attribution wins over its source's)" \
  "$(as_pg "select attribution from api.series where key='fred.DGS10'")" '^Source: U\.S\. Treasury, via Federal Reserve Board \(H\.15\) and FRED$'

echo
echo "passed $pass, failed $fail"
