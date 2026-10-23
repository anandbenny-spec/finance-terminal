#!/bin/bash
# Tests for 0014 (the DGS2 and DGS10 fixing times). Run after the other scripts on the same fresh database.
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

check "X01 fred.DGS2 and fred.DGS10 fix at 15:30 New York (H.15: Treasury's curve, quotes taken at or near 3:30 PM)" \
  "$(as_pg "select string_agg(key||' '||fixing_time::text||' '||fixing_tz, ',' order by key) from core.series where key in ('fred.DGS2','fred.DGS10')")" \
  '^fred\.DGS10 15:30:00 America/New_York,fred\.DGS2 15:30:00 America/New_York$'
check "X02 every Yahoo index fixes at its exchange's close (16:00 New York, 15:30 India)" \
  "$(as_pg "select coalesce(string_agg(key, ',' order by key), 'none') from core.series where key like 'yf.^%' and (fixing_time is null or (market='US' and (fixing_time,fixing_tz) <> ('16:00','America/New_York')) or (market='IN' and (fixing_time,fixing_tz) <> ('15:30','Asia/Kolkata')))")" '^none$'
check "X03 fred.SP500 is still unpublished (Spec 01 D3)" \
  "$(as_pg "select is_published from core.series where key='fred.SP500'")" '^f$'

echo
echo "passed $pass, failed $fail"
