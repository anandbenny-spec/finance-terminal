#!/bin/bash
# Tests for 0018 (the coingecko source row; issue #106). Run after the other scripts on the same fresh database.
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

check "G01 the coingecko row exists once, with its name and homepage" \
  "$(as_pg "select count(*)||'|'||min(name)||'|'||min(homepage_url) from core.source where key = 'coingecko'")" \
  '^1\|CoinGecko\|https://www.coingecko.com$'
check "G02 its attribution is the wording CoinGecko's terms require, and its terms link is stored" \
  "$(as_pg "select attribution||'|'||terms_url from core.source where key = 'coingecko'")" \
  '^Powered by CoinGecko\|https://www.coingecko.com/en/api_terms$'
check "G03 not official, no raw export, not suppressed" \
  "$(as_pg "select is_official||'|'||allow_raw_export||'|'||is_suppressed from core.source where key = 'coingecko'")" '^false\|false\|false$'
check "G04 the notes record decision C9" \
  "$(as_pg "select notes from core.source where key = 'coingecko'")" 'C9'
check "G05 job_writer can read the row (the fetcher looks it up)" \
  "$(as_role job_writer "select count(*) from core.source where key = 'coingecko'")" '^1$'
check "G06 job_writer can't insert into core.source" \
  "$(as_role job_writer "insert into core.source (key, name, attribution) values ('g06', 'x', 'x')")" 'permission denied|row-level security'
check "G07 job_writer can't update core.source" \
  "$(as_role job_writer "update core.source set is_suppressed = true where key = 'coingecko'")" 'permission denied|row-level security'
check "G08 the row is still unsuppressed after those attempts" \
  "$(as_pg "select is_suppressed::text from core.source where key = 'coingecko'")" '^false$'

echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
