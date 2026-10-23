#!/bin/bash
# Tests for 0012 (module 1's seeds, Spec 01 §1.3). Run after run_tests_review_01.sh on the same fresh database.
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE). Non-ASCII expectations (the middle
# dot and the copyright sign) are written as U& escapes, so a migration applied in the wrong client encoding fails.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
col_comment() { echo "col_description('$1'::regclass, (select attnum from pg_attribute where attrelid = '$1'::regclass and attname = '$2'))"; }
NEW="'yf.^GSPC','yf.^IXIC','yf.^NDX','yf.^DJI','yf.INR=X'"

check "S01 the five new series: entity, source, code, unit, daily, fixing time and zone, market and currency (Spec 01 §1.3)" \
  "$(as_pg "with e(key, entity, src, code, unit, freq, rule, fix, tz, market, cur, kind) as (values
      ('yf.^GSPC', 'sp-500',           'yfinance', '^GSPC', 'index_points', 'D', 'as_published', '16:00'::time, 'America/New_York', 'US',     'USD',     'observed'),
      ('yf.^IXIC', 'nasdaq-composite', 'yfinance', '^IXIC', 'index_points', 'D', 'as_published', '16:00'::time, 'America/New_York', 'US',     'USD',     'observed'),
      ('yf.^NDX',  'nasdaq-100',       'yfinance', '^NDX',  'index_points', 'D', 'as_published', '16:00'::time, 'America/New_York', 'US',     'USD',     'observed'),
      ('yf.^DJI',  'dow-jones',        'yfinance', '^DJI',  'index_points', 'D', 'as_published', '16:00'::time, 'America/New_York', 'US',     'USD',     'observed'),
      ('yf.INR=X', null,               'yfinance', 'INR=X', 'inr_per_usd',  'D', 'as_published', null::time,    null,               'GLOBAL', null,      'observed')),
    a(key, entity, src, code, unit, freq, rule, fix, tz, market, cur, kind) as (select s.key, n.slug, src.key, s.source_code, s.unit, s.frequency, s.date_rule, s.fixing_time, s.fixing_tz, s.market, s.currency, s.kind
          from core.series s left join core.entity n on n.id = s.entity_id join core.source src on src.id = s.source_id
          where s.key in ($NEW))
    select (select count(*) from (select * from e except select * from a) x) || ',' || (select count(*) from (select * from a except select * from e) y)")" \
  '^0,0$'
check "S02 source and copyright lines: S&P Dow Jones Indices LLC on ^GSPC and ^DJI, Nasdaq, Inc. on ^IXIC and ^NDX; Yahoo Finance on INR=X" \
  "$(as_pg "select count(*) from core.series s join (values
      ('yf.^GSPC', 'Source: S&P Dow Jones Indices LLC, via Yahoo Finance ' || U&'\00B7 \00A9' || ' S&P Dow Jones Indices LLC'),
      ('yf.^DJI',  'Source: S&P Dow Jones Indices LLC, via Yahoo Finance ' || U&'\00B7 \00A9' || ' S&P Dow Jones Indices LLC'),
      ('yf.^IXIC', 'Source: Nasdaq, Inc., via Yahoo Finance ' || U&'\00B7 \00A9' || ' Nasdaq, Inc.'),
      ('yf.^NDX',  'Source: Nasdaq, Inc., via Yahoo Finance ' || U&'\00B7 \00A9' || ' Nasdaq, Inc.'),
      ('yf.INR=X', 'Source: Yahoo Finance')) e(key, attr) on e.key = s.key and e.attr = s.attribution")" \
  '^5$'
check "S03 the new series and fred.SP500 start unpublished, and visitors see none of them" \
  "$(as_pg "select count(*) from core.series where is_published and key in ($NEW, 'fred.SP500')"),$(as_role anon "select count(*) from api.series where key in ($NEW, 'fred.SP500')")" \
  '^0,0$'
check "S04 the catalogue: 17 observed series (the 16 to publish plus fred.SP500) and RBI's 2 projection series" \
  "$(as_pg "select count(*) filter (where kind = 'observed') || '+' || count(*) filter (where kind = 'forecast') from core.series")" \
  '^17\+2$'
check "S05 once published, yf.^GSPC carries its copyright line to visitors (api.series) and to text-to-SQL (semantic.market_daily)" \
  "$(as_pg "begin;
      update core.series set is_published = true where key = 'yf.^GSPC';
      insert into core.observation (series_id, obs_date, value, task_run_id)
        select id, '2026-09-18', 6600.5, (select max(id) from ops.task_run) from core.series where key = 'yf.^GSPC';
      set local role anon;
      select 'api=' || (attribution like '%' || U&'\00A9' || ' S&P Dow Jones Indices LLC')::text || '#' from api.series where key = 'yf.^GSPC';
      reset role;
      set local role sql_reader;
      select 'sem=' || (source like '%' || U&'\00A9' || ' S&P Dow Jones Indices LLC')::text || ':' || value || ':' || currency || '#'
        from semantic.market_daily where series_key = 'yf.^GSPC';
      rollback;")" \
  'api=true#.*sem=true:6600\.5:USD#'
check "S06 fred.SP500 stays hidden from visitors and text-to-SQL even with stored observations (D3: cross-check only)" \
  "$(as_role anon "select count(*) from api.observations where series_key = 'fred.SP500'"),$(as_role sql_reader "select count(*) from semantic.market_daily where series_key = 'fred.SP500'"),$(as_pg "select count(*) from core.observation o join core.series s on s.id = o.series_id where s.key = 'fred.SP500'")" \
  '^0,0,5$'
check "S07 semantic.market_daily's comment lists the new series and keeps the two USD/INR series apart (D6)" \
  "$(as_pg "select (c like '%Nasdaq Composite%' and c like '%Nasdaq-100%' and c like '%Dow Jones Industrial Average%' and c like '%S&P 500%'
                and c like '%fred.DEXINUS%' and c like '%yf.INR=X%' and c like '%never combined%' and c like '%Never compare levels across currencies.')::text
           from obj_description('semantic.market_daily'::regclass, 'pg_class') c")" \
  '^true$'
check "S08 semantic.policy_meetings.rbi_crr_pct reads as Spec 01 §1.3 words it (CRR is Pass 2: D5)" \
  "$(as_pg "select $(col_comment semantic.policy_meetings rbi_crr_pct)")" \
  "^null until Pass 2 \(CRR is set in the Governor's statement, with its own effective dates\)\$"
check "S09 0011's comment on dissent_count is still in place (rate or stance, each member once)" \
  "$(as_pg "select $(col_comment semantic.policy_meetings dissent_count)")" \
  '^Members who dissented on the rate or \(RBI\) on the stance; each member counted once$'
check "S10 every observed series a visitor can be shown has a source line (its own attribution or its source's)" \
  "$(as_pg "select coalesce(string_agg(s.key, ',' order by s.key), 'none') from core.series s join core.source src on src.id = s.source_id
           where s.kind = 'observed' and s.key <> 'fred.SP500' and coalesce(s.attribution, src.attribution) not like 'Source: %'")" \
  '^none$'

echo
echo "passed $pass, failed $fail"
