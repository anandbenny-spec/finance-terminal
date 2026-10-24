#!/bin/bash
# The source switch on time series (Prompt 01 D3, the suppression drill; Arch 01 §1.4 "Source switch"; Spec 01 §5.1).
# T10 covers events, documents, chunks and AI outputs. These cover the series side: suppressing `fred` hides every
# FRED series, and the reactions built on them, from every api view the site reads and from the text-to-SQL views, at
# once; other sources' series and the banks' own meetings stay; nothing is deleted; un-suppressing restores every row.
# Everything runs in one session and one transaction that is rolled back, so the fixture is unchanged for any script
# that runs after this one. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

# Each probe prints one line: "<label> <view>=<FRED rows>/<yfinance rows> …". Series keys carry their source's
# prefix (fred., yf.), which the seeds keep.
fy() { echo "count(*) filter (where $1 like 'fred.%')||'/'||count(*) filter (where $1 like 'yf.%')"; }
api_probe() { # what a visitor (anon) or the admin's session (authenticated) sees: the views the site reads
  echo "set local role $2;
select '$1 series='||(select $(fy key) from api.series)
     ||' latest='||(select $(fy series_key) from api.series_latest)
     ||' obs='||(select $(fy series_key) from api.observations)
     ||' steps='||(select $(fy series_key) from api.observation_steps)
     ||' reactions='||(select $(fy series_key) from api.market_reactions);
reset role;"; }
sem_probe() { # what text-to-SQL sees (the views run as semantic_owner, under RLS)
  echo "set local role sql_reader;
select '$1 market_daily='||(select $(fy series_key) from semantic.market_daily)
     ||' us2y='||coalesce((select us_2y_change_bps::text from semantic.policy_meetings where meeting_date = '2026-09-16'), 'null');
reset role;"; }
control_probe() { # the banks' own rows (source fed), which a fred switch must not touch
  echo "set local role anon;
select '$1 meetings='||(select count(*) from api.policy_meetings)
     ||' fed_bounds='||(select count(*) from api.policy_meetings where fed_target_lower_pct is not null and fed_target_upper_pct is not null)
     ||' documents='||(select count(*) from api.documents);
reset role;"; }
base_probe() { # the stored rows themselves (postgres): the switch hides, never deletes
  echo "select '$1 series='||(select $(fy key) from core.series)
     ||' obs='||(select count(*) filter (where s.key like 'fred.%')||'/'||count(*) filter (where s.key like 'yf.%') from core.observation o join core.series s on s.id = o.series_id)
     ||' reactions='||(select count(*) filter (where s.key like 'fred.%')||'/'||count(*) filter (where s.key like 'yf.%') from policy.market_reaction r join core.series s on s.id = r.series_id);"; }
probes() { api_probe "$1-anon" anon; api_probe "$1-auth" authenticated; sem_probe "$1-sem"; control_probe "$1-ctl"; base_probe "$1-base"; }

out=$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<SQL
begin;
-- A published yfinance series with observations and a reaction, so "other sources stay" has rows to show
-- (the fixture publishes only fred.DGS2, with one reaction on it).
update core.series set is_published = true where key = 'yf.^NSEI';
insert into core.observation (series_id, obs_date, value, task_run_id)
select s.id, d::date, 25000 + extract(day from d), (select max(id) from ops.task_run)
from core.series s, generate_series('2026-09-14'::date, '2026-09-18', '1 day') d where s.key = 'yf.^NSEI';
insert into policy.market_reaction (meeting_event_id, series_id, pre_obs_date, post_obs_date, pre_value, post_value, change, change_unit)
select e.id, s.id, '2026-09-16', '2026-09-17', 25016, 25017, 0.004, 'pct'
from core.event e, core.series s where e.slug = 'fomc-2026-09-16' and s.key = 'yf.^NSEI';
$(probes before)
update core.source set is_suppressed = true where key = 'fred';
$(probes during)
update core.source set is_suppressed = false where key = 'fred';
$(probes after)
rollback;
SQL
)
line() { echo "$out" | grep "^$1 " | head -1 | cut -d' ' -f2-; }
nonzero='[1-9][0-9]*'

check "S01 before: visitors see FRED and yfinance rows in every series view (the drill isn't vacuous)" \
  "$(line before-anon)" "^series=$nonzero/$nonzero latest=$nonzero/$nonzero obs=$nonzero/$nonzero steps=$nonzero/$nonzero reactions=$nonzero/$nonzero$"
check "S02 suppressing fred hides every FRED series, observation, step and reaction from visitors at once" \
  "$(line during-anon)" '^series=0/[0-9]+ latest=0/[0-9]+ obs=0/[0-9]+ steps=0/[0-9]+ reactions=0/[0-9]+$'
check "S03 … and from the admin's session (authenticated)" \
  "$(line during-auth)" '^series=0/[0-9]+ latest=0/[0-9]+ obs=0/[0-9]+ steps=0/[0-9]+ reactions=0/[0-9]+$'
check "S04 … and from text-to-SQL: market_daily has no FRED row, the 2-year reaction reads null" \
  "$(echo "$(line before-sem) | $(line during-sem)")" "^market_daily=$nonzero/$nonzero us2y=-8 \| market_daily=0/$nonzero us2y=null$"
check "S05 yfinance's rows are the same while fred is suppressed (one source's switch)" \
  "$(echo "$(line before-anon | sed 's|=[0-9]*/|=|g') $(line before-sem | sed 's|=[0-9]*/|=|g;s| us2y=.*||') = $(line during-anon | sed 's|=[0-9]*/|=|g') $(line during-sem | sed 's|=[0-9]*/|=|g;s| us2y=.*||')")" \
  '^(.+) = \1$'
check "S06 the Fed's own meetings, rate bounds and documents stay visible (fed isn't fred)" \
  "$(echo "$(line before-ctl) = $(line during-ctl)")" "^(meetings=$nonzero fed_bounds=$nonzero documents=$nonzero) = \\1$"
check "S07 nothing is deleted: the stored series, observations and reactions are the same throughout" \
  "$(echo "$(line before-base) = $(line during-base) = $(line after-base)")" '^(series=[0-9]+/[0-9]+ obs=[0-9]+/[0-9]+ reactions=1/1) = \1 = \1$'
check "S08 un-suppressing restores exactly what visitors, the admin and text-to-SQL saw before" \
  "$(echo "$(line before-anon) $(line before-auth) $(line before-sem) = $(line after-anon) $(line after-auth) $(line after-sem)")" '^(.+) = \1$'

if [ $((pass + fail)) -eq 0 ] || echo "$out" | grep -q 'ERROR'; then echo "$out" | grep 'ERROR' | head -3; fi
echo
echo "passed $pass, failed $fail"
