#!/bin/bash
# Builds a fresh test database (Supabase's roles mimicked), applies every migration in order, loads the
# fixture and runs every test script. Needs Postgres 15+ with pgvector 0.7+ and trust auth for the test
# roles (CI uses the pgvector/pgvector image; locally, any throwaway cluster).
# Usage: PGHOST=... PGPORT=... supabase/tests/run_all.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
export PGDATABASE=${PGDATABASE:-ft}
P="psql -h ${PGHOST:-localhost} -p ${PGPORT:-5432} -U postgres -v ON_ERROR_STOP=1 -q"
$P -d postgres -c "drop database if exists $PGDATABASE" -c "create database $PGDATABASE" >/dev/null || exit 1
for r in pooler_admin anon authenticated service_role authenticator job_writer sql_reader semantic_owner retrieval_owner; do
  $P -d postgres -c "drop role if exists $r" >/dev/null 2>&1
done
$P -d $PGDATABASE -f "$HERE/mock_supabase_roles.sql" || exit 1
for f in "$HERE"/../migrations/*.sql; do
  $P -d $PGDATABASE -f "$f" 2>&1 | grep -v NOTICE
  [ "${PIPESTATUS[0]}" -eq 0 ] || { echo "migration failed: $f"; exit 1; }
done
$P -d $PGDATABASE -f "$HERE/test_fixture.sql" || exit 1
status=0
for t in run_tests.sh run_tests_ai.sh run_tests_review_01.sh run_tests_0012.sh run_tests_0013.sh run_tests_0014.sh run_tests_0015.sh run_tests_0016.sh run_tests_0017.sh run_tests_0018.sh run_tests_0019.sh run_tests_0021.sh \
         run_tests_suppression.sh; do
  out=$(bash "$HERE/$t"); echo "$out"
  echo "$out" | grep -q "failed 0$" || status=1
done
exit $status
