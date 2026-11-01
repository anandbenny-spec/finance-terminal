#!/bin/bash
# Tests for 0020 (semantic.policy_votes' view comment; issue #159). Run after the other scripts on the same fresh
# database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
# The migration changes a comment and nothing else: the checks read the comment, and that the view is as it was.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

COMMENT="$(as_pg "select obj_description('semantic.policy_votes'::regclass, 'pg_class')")"

check "K01 the comment says one row per voting member per meeting" \
  "$COMMENT" '^One row per voting member per meeting\.'
check "K02 FOMC votes come from the statement's voting line to April 2026 and from the minutes from June 2026" \
  "$COMMENT" "statement's voting line to April 2026 and from the minutes from June 2026"
check "K03 until a meeting's minutes are out only its dissenters have rows, taken from the statement; a unanimous meeting has none" \
  "$COMMENT" "until a meeting's minutes are out only its dissenters have rows \(taken from the statement\), and a unanimous meeting has none"
check "K04 the old claim, that FOMC votes come from the statement's voting line alone, is gone" \
  "$([[ "$COMMENT" == *"voting line; RBI votes"* ]] && echo 'still there' || echo 'gone')" '^gone$'
check "K05 the RBI sentence is kept" \
  "$COMMENT" 'RBI votes \(rate and stance\) come from the MPC minutes\.$'
check "K06 the view is as it was: same columns, in order" \
  "$(as_pg "select string_agg(attname, ',' order by attnum) from pg_attribute where attrelid = 'semantic.policy_votes'::regclass and attnum > 0 and not attisdropped")" \
  '^bank,meeting_date,member_name,member_role,affiliation,voted_change_bps,stance_vote,is_dissent,dissent_text$'
check "K07 still owned by semantic_owner, with security_barrier on" \
  "$(as_pg "select pg_get_userbyid(c.relowner)||'|'||coalesce((select option_value from pg_options_to_table(c.reloptions) where option_name = 'security_barrier'), 'off') from pg_class c where c.oid = 'semantic.policy_votes'::regclass")" \
  '^semantic_owner\|true$'
check "K08 the column comments are untouched" \
  "$(as_pg "select col_description('semantic.policy_votes'::regclass, (select attnum from pg_attribute where attrelid = 'semantic.policy_votes'::regclass and attname = 'voted_change_bps'))||' | '||col_description('semantic.policy_votes'::regclass, (select attnum from pg_attribute where attrelid = 'semantic.policy_votes'::regclass and attname = 'is_dissent'))")" \
  "dissenter's preferred change\) \| True if the member dissented on the rate or \(RBI\) on the stance"
check "K09 sql_reader still reads the view" \
  "$(as_role sql_reader "select count(*) from semantic.policy_votes")" '^[0-9]+$'
check "K10 the api view is not touched by this migration (no comment on it says the new thing)" \
  "$(as_pg "select coalesce(obj_description('api.policy_votes'::regclass, 'pg_class'), '') like '%dissenters have rows%'")" '^f$'

echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
