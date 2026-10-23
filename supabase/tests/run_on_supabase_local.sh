#!/bin/bash
# Day-1 check 5 (Architecture 01 §8 item 5; GitHub issue #1): the SQL tests against a local Supabase database,
# where `postgres` isn't a superuser and the roles, grants and default privileges are Supabase's own rather than
# mock_supabase_roles.sql. Run after `supabase db start` and `supabase migration up --local` (the CLI applies the
# migrations as it does on the hosted projects). The tests run inside the database container, so their psql calls
# reach Postgres the way CI's do. Anything this script changes to make the harness work (a test-only password, a
# SET grant) is printed as "harness:" and lives only in the throwaway container.
# Usage: supabase/tests/run_on_supabase_local.sh      (manual workflow supabase-local-check.yml)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DB=$(docker ps --format '{{.Names}}' | grep '^supabase_db_' | head -1)
[ -n "$DB" ] || { echo "::error title=supabase-local::no supabase_db_ container is running (did supabase db start fail?)"; exit 1; }
docker exec "$DB" rm -rf /tmp/ft_tests >/dev/null 2>&1
docker cp "$HERE" "$DB:/tmp/ft_tests" >/dev/null || exit 1
TEST_PW="ft-local-test-only"
in_db() { docker exec -i -e PGHOST=127.0.0.1 -e PGPORT=5432 -e PGDATABASE=postgres -e PGPASSFILE=/tmp/ft_tests/.pgpass "$DB" "$@"; }
q() { in_db psql -w -U "$1" -X -A -t -q -c "$2" 2>&1; }
note() { echo "harness: $*"; }

# the scripts log in as postgres, sql_reader, job_writer and (T20) pooler_admin; local passwords: postgres/postgres
printf '127.0.0.1:5432:*:postgres:postgres\n127.0.0.1:5432:*:supabase_admin:postgres\n*:*:*:*:%s\n' "$TEST_PW" | in_db sh -c 'cat > /tmp/ft_tests/.pgpass && chmod 600 /tmp/ft_tests/.pgpass'
if ! q postgres "select 1" | grep -qx 1; then
  echo "::error title=supabase-local::can't log in as postgres inside the container: $(q postgres 'select 1' | tail -1)"; exit 1
fi

echo "== the local database"
echo "container: $DB"
q postgres "select version()"
q postgres "select 'current_user=' || current_user || ', superuser=' || rolsuper || ', createrole=' || rolcreaterole from pg_roles where rolname = current_user"
q postgres "select 'schemas: ' || string_agg(nspname, ', ' order by nspname) from pg_namespace where nspname in ('ops','core','policy','api','semantic','extensions')"
q postgres "select 'extensions: ' || string_agg(extname || ' ' || extversion, ', ' order by extname) from pg_extension"
if ! q postgres "select count(*) from pg_namespace where nspname in ('ops','core','policy','api','semantic')" | grep -qx 5; then
  echo "::error title=supabase-local::our schemas aren't there: the migrations weren't applied (supabase migration up --local)"; exit 1
fi
hba=$(q supabase_admin "show hba_file")
echo "pg_hba ($hba), non-comment lines:"
docker exec -u root "$DB" sh -c "grep -v '^[[:space:]]*#' '$hba' | grep -v '^[[:space:]]*\$'" 2>/dev/null || echo "  (not readable)"

# --- logins as the job roles --------------------------------------------------------------------------------------------
# CI's container trusts local logins; the hosted pooler logins were checked separately (day-1 item 1, pooler_check.py).
# So if this database asks for passwords on 127.0.0.1, trust that one address inside the throwaway container.
if ! q sql_reader "select 1" | grep -qx 1; then
  if [ -n "$hba" ] && [ "${hba#/}" != "$hba" ] && docker exec -u root "$DB" sh -c "printf 'host all all 127.0.0.1/32 trust\n' | cat - '$hba' > /tmp/hba.new && cat /tmp/hba.new > '$hba'" \
     && q supabase_admin "select pg_reload_conf()" | grep -qx t && q sql_reader "select 1" | grep -qx 1; then
    note "local logins on 127.0.0.1 set to trust inside the container ($hba), as in CI"
  fi
fi
if ! q sql_reader "select 1" | grep -qx 1; then
  note "sql_reader can't log in without a password here; setting test-only passwords on job_writer and sql_reader"
  q postgres "alter role job_writer password '$TEST_PW'; alter role sql_reader password '$TEST_PW'" >/dev/null
  q sql_reader "select 1" | grep -qx 1 || echo "::warning title=supabase-local::sql_reader still can't log in: $(q sql_reader 'select 1' | tail -1)"
  note "T20 creates pooler_admin without a password, so its login may fail here for that reason alone"
fi

# --- SET ROLE: the tests act as anon, service_role and job_writer through SET ROLE from postgres ---------------------
for r in anon authenticated service_role job_writer sql_reader; do
  out=$(q postgres "set role $r; select current_user")
  if ! echo "$out" | grep -qx "$r"; then
    echo "postgres can't SET ROLE $r: $(echo "$out" | tail -1)"
    if err=$(in_db psql -w -U supabase_admin -X -q -v ON_ERROR_STOP=1 -c "grant $r to postgres with inherit false, set true" 2>&1); then
      note "granted SET (not INHERIT) on $r to postgres as supabase_admin, test-only"
    else
      echo "::warning title=supabase-local::couldn't grant SET on $r to postgres: $(echo "$err" | tail -1)"
    fi
  fi
done

# --- fixture and tests -----------------------------------------------------------------------------------------------
echo "== fixture (written as job_writer)"
in_db psql -U postgres -X -q -v ON_ERROR_STOP=1 -f /tmp/ft_tests/test_fixture.sql 2>&1 | tail -5
[ "${PIPESTATUS[0]}" -eq 0 ] || echo "::error title=supabase-local::the fixture failed to load; the tests below will mostly fail for that reason"

status=0
total_pass=0; total_fail=0
for t in run_tests.sh run_tests_ai.sh run_tests_review_01.sh run_tests_0012.sh run_tests_0013.sh run_tests_0014.sh run_tests_0015.sh; do
  echo "== $t"
  out=$(in_db bash "/tmp/ft_tests/$t" 2>&1); echo "$out"
  p=$(echo "$out" | grep -c '^PASS'); f=$(echo "$out" | grep -c '^FAIL')
  total_pass=$((total_pass + p)); total_fail=$((total_fail + f))
  echo "::notice title=$t::passed $p, failed $f"
  echo "$out" | grep '^FAIL' | head -10 | while read -r line; do echo "::error title=$t::$line"; done
  echo "$out" | grep -q "failed 0$" || status=1
done
echo "== total: passed $total_pass, failed $total_fail (CI on plain Postgres: 84 pass)"
{
  echo "### SQL tests on a local Supabase database"
  echo "passed **$total_pass**, failed **$total_fail** (CI on plain Postgres: 84 pass). Harness changes are the \`harness:\` lines in the log."
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
exit $status
