-- The suppression drill (Prompt 01 D3; GitHub issue #26) on a hosted project. DEV ONLY, and only when Anand has said
-- yes (CLAUDE.md §7: it flips a source suppression). It hides every FRED series from visitors and text-to-SQL, then
-- restores them; nothing is deleted. run_tests_suppression.sh proves the same on the test database; this is the
-- real-data run (re-run it in Pass 1b, once policy.market_reaction holds reactions).
--
-- Dashboard → SQL Editor → New query. Paste ONE block at a time, run it, and copy its result table into the issue.
-- Each block is one transaction: the switch (blocks 2 and 3) is flipped as postgres, then the check runs as the Data
-- API's `anon` role, which is what the site reads with. Its first row must say "anon": if it says "postgres" (which
-- bypasses RLS), the check didn't run as a visitor, so read nothing into the counts. Text-to-SQL's view
-- (semantic.market_daily) and the stored rows are read separately, through the read-only connector.
-- Each result row is "<FRED rows> · <yfinance rows>" for one api view; series keys carry their source's prefix.


-- ========== Block 1: status (read-only, changes nothing) ==========
set local role anon;
select n, item, result from (
  select 0 as n, 'running as' as item, current_user::text as result
  union all select 1, 'core.source fred: is_suppressed', (select is_suppressed::text from core.source where key = 'fred')
  union all select 2, 'api.series (FRED · yfinance)', (select count(*) filter (where key like 'fred.%') || ' · ' || count(*) filter (where key like 'yf.%') from api.series)
  union all select 3, 'api.series_latest (tape)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.series_latest)
  union all select 4, 'api.observations', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observations)
  union all select 5, 'api.observation_steps (Fed band)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observation_steps)
  union all select 6, 'api.market_reactions (Pass 1b)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.market_reactions)
  union all select 7, 'control: api.policy_meetings · with both Fed bounds', (select count(*) || ' · ' || count(*) filter (where fed_target_lower_pct is not null and fed_target_upper_pct is not null) from api.policy_meetings)
  union all select 8, 'control: api.documents', (select count(*)::text from api.documents)
) r order by n;


-- ========== Block 2: suppress fred, then check (FRED rows must all read 0; yfinance and the controls unchanged) ==========
update core.source set is_suppressed = true where key = 'fred';
set local role anon;
select n, item, result from (
  select 0 as n, 'running as' as item, current_user::text as result
  union all select 1, 'core.source fred: is_suppressed', (select is_suppressed::text from core.source where key = 'fred')
  union all select 2, 'api.series (FRED · yfinance)', (select count(*) filter (where key like 'fred.%') || ' · ' || count(*) filter (where key like 'yf.%') from api.series)
  union all select 3, 'api.series_latest (tape)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.series_latest)
  union all select 4, 'api.observations', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observations)
  union all select 5, 'api.observation_steps (Fed band)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observation_steps)
  union all select 6, 'api.market_reactions (Pass 1b)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.market_reactions)
  union all select 7, 'control: api.policy_meetings · with both Fed bounds', (select count(*) || ' · ' || count(*) filter (where fed_target_lower_pct is not null and fed_target_upper_pct is not null) from api.policy_meetings)
  union all select 8, 'control: api.documents', (select count(*)::text from api.documents)
) r order by n;


-- ========== Block 3: restore (un-suppress fred, then check: every row as in block 1) ==========
update core.source set is_suppressed = false where key = 'fred';
set local role anon;
select n, item, result from (
  select 0 as n, 'running as' as item, current_user::text as result
  union all select 1, 'core.source fred: is_suppressed', (select is_suppressed::text from core.source where key = 'fred')
  union all select 2, 'api.series (FRED · yfinance)', (select count(*) filter (where key like 'fred.%') || ' · ' || count(*) filter (where key like 'yf.%') from api.series)
  union all select 3, 'api.series_latest (tape)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.series_latest)
  union all select 4, 'api.observations', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observations)
  union all select 5, 'api.observation_steps (Fed band)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observation_steps)
  union all select 6, 'api.market_reactions (Pass 1b)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.market_reactions)
  union all select 7, 'control: api.policy_meetings · with both Fed bounds', (select count(*) || ' · ' || count(*) filter (where fed_target_lower_pct is not null and fed_target_upper_pct is not null) from api.policy_meetings)
  union all select 8, 'control: api.documents', (select count(*)::text from api.documents)
) r order by n;
