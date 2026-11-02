-- The NSE suppression drill for release 2A (Spec 02A §9.5 item 12; GitHub issue #132, step 3). DEV ONLY, and only
-- because Anand said yes (CLAUDE.md §7 asks for it, since the drill flips a source suppression): his OK of 28 Oct 2026,
-- 07:35 IST, recorded on #132, covers this one drill on dev, suppress and restore. It hides every NSE-derived row from
-- visitors and text-to-SQL at once, checks that module 1's views did not change, then restores it. Nothing is deleted.
-- run_tests_0024.sh (A37, A43), run_tests_0026.sh (R36) and run_tests_0027.sh (T04) prove the same on the test
-- database; this is the real-data run.
--
-- Dashboard (the DEV project: check its name in the top bar) -> SQL Editor -> New query. Paste ONE block at a time, run
-- it, and copy its result table into #132. Run the blocks in order, and ALWAYS run block 3 after block 2, even if block
-- 2 looks wrong: block 3 is what turns the source back on.
-- Each block is one transaction: the switch (blocks 2 and 3) is flipped as postgres, then the checks run as the Data
-- API's `anon` role, which is what the site reads with. Row 0 must say "anon": if it says "postgres" (which bypasses
-- RLS), the check did not run as a visitor, so read nothing into the counts. The `semantic` views (text-to-SQL's
-- schema) are counted before the role switch: they are owned by `semantic_owner`, so the underlying tables' rules apply
-- to them whoever calls, and `anon` itself cannot read them. If that count cannot run, its row says why.
--
-- PASS when:
--   block 2: rows 10 to 25 all read 0 (a pair reads "0 · 0"); row 30 reads "<block 1's count> · 0 · 0" (the company
--            list stays, with no price and no index chips); row 31 equals block 1; rows 40 to 49 equal block 1;
--   block 3: every row equals block 1, and row 1 reads false.
-- Rows 10 to 25 are NSE-derived, and each must read more than 0 in block 1: dev holds data for every one of them.
-- Dev's totals on 3 Nov 2026, read with RLS bypassed (so block 1, which is what a visitor sees, may read a little less):
-- index_members 150, company_prices 94,912, company_financials 6,034, company_metrics 26,115, company_corporate_actions
-- 619, screener 50, peer_comps 164, company_valuation 85, valuation_scenarios 9,000, priced_in_operating 42,
-- priced_in_lenders 8, company_promoter_holding 950, rate_decision_moves 10,688 · 3,951, rate_decision_stats 600 · 216,
-- company_rate_cycles 436; api.companies 50 · 50 · 50; api.rate_cycles 9. A view whose field policy is still hidden
-- (the four flips are not made yet) still returns its rows, with the gated cells empty.
-- Series keys carry their source's prefix: "<FRED rows> · <yfinance rows>". The Nifty 50 index (yf.^NSEI) is Yahoo's,
-- so it must stay (row 48).


-- ========== Block 1: status (read-only, changes nothing) ==========
do $$ begin
  perform set_config('drill.sem_nse', (select count(*) from semantic.company_valuations) || ' · '
    || (select count(*) from semantic.company_financials) || ' · ' || (select count(*) from semantic.rate_decision_moves), true);
  perform set_config('drill.sem_id', (select count(*)::text from semantic.companies), true);
  perform set_config('drill.sem_ctl', (select count(*) from semantic.market_daily) || ' · '
    || (select count(*) from semantic.policy_meetings), true);
exception when others then
  perform set_config('drill.sem_nse', 'could not count: ' || sqlerrm, true);
  perform set_config('drill.sem_id', 'could not count: ' || sqlerrm, true);
  perform set_config('drill.sem_ctl', 'could not count: ' || sqlerrm, true);
end $$;
set local role anon;
select n, item, result from (
  select 0 as n, 'running as' as item, current_user::text as result
  union all select 1, 'core.source nse: is_suppressed', (select is_suppressed::text from core.source where key = 'nse')
  -- NSE-derived, 2A: must read 0 while suppressed
  union all select 10, 'api.index_members', (select count(*)::text from api.index_members)
  union all select 11, 'api.company_prices', (select count(*)::text from api.company_prices)
  union all select 12, 'api.company_financials', (select count(*)::text from api.company_financials)
  union all select 13, 'api.company_metrics', (select count(*)::text from api.company_metrics)
  union all select 14, 'api.company_corporate_actions', (select count(*)::text from api.company_corporate_actions)
  union all select 15, 'api.screener', (select count(*)::text from api.screener)
  union all select 16, 'api.peer_comps', (select count(*)::text from api.peer_comps)
  union all select 17, 'api.company_valuation', (select count(*)::text from api.company_valuation)
  union all select 18, 'api.valuation_scenarios', (select count(*)::text from api.valuation_scenarios)
  union all select 19, 'api.priced_in_operating', (select count(*)::text from api.priced_in_operating)
  union all select 20, 'api.priced_in_lenders', (select count(*)::text from api.priced_in_lenders)
  union all select 21, 'api.company_promoter_holding', (select count(*)::text from api.company_promoter_holding)
  union all select 22, 'api.rate_decision_moves (company · peer group)', (select count(*) filter (where subject_kind = 'company') || ' · ' || count(*) filter (where subject_kind = 'peer_group') from api.rate_decision_moves)
  union all select 23, 'api.rate_decision_stats (company · peer group)', (select count(*) filter (where subject_kind = 'company') || ' · ' || count(*) filter (where subject_kind = 'peer_group') from api.rate_decision_stats)
  union all select 24, 'api.company_rate_cycles', (select count(*)::text from api.company_rate_cycles)
  union all select 25, 'semantic: company_valuations · company_financials · rate_decision_moves', current_setting('drill.sem_nse')
  -- the company list: identity rows stay, price and index chips go
  union all select 30, 'api.companies (rows · with last_close · with index_slugs)', (select count(*) || ' · ' || count(last_close) || ' · ' || count(index_slugs) from api.companies)
  union all select 31, 'semantic.companies', current_setting('drill.sem_id')
  -- controls, module 1 and policy: unchanged
  union all select 40, 'api.series (FRED · yfinance)', (select count(*) filter (where key like 'fred.%') || ' · ' || count(*) filter (where key like 'yf.%') from api.series)
  union all select 41, 'api.series_latest (tape)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.series_latest)
  union all select 42, 'api.observations', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observations)
  union all select 43, 'api.observation_steps (Fed band)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observation_steps)
  union all select 44, 'api.market_reactions', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.market_reactions)
  union all select 45, 'api.policy_meetings · with both Fed bounds', (select count(*) || ' · ' || count(*) filter (where fed_target_lower_pct is not null and fed_target_upper_pct is not null) from api.policy_meetings)
  union all select 46, 'api.documents', (select count(*)::text from api.documents)
  union all select 47, 'api.rate_cycles (policy cycles)', (select count(*)::text from api.rate_cycles)
  union all select 48, 'api.series yf.^NSEI (Yahoo''s Nifty 50 index)', (select count(*)::text from api.series where key = 'yf.^NSEI')
  union all select 49, 'semantic: market_daily · policy_meetings', current_setting('drill.sem_ctl')
) r order by n;


-- ========== Block 2: suppress nse, then check (rows 10 to 25 must read 0; row 30 keeps its companies; the controls unchanged) ==========
update core.source set is_suppressed = true where key = 'nse';
do $$ begin
  perform set_config('drill.sem_nse', (select count(*) from semantic.company_valuations) || ' · '
    || (select count(*) from semantic.company_financials) || ' · ' || (select count(*) from semantic.rate_decision_moves), true);
  perform set_config('drill.sem_id', (select count(*)::text from semantic.companies), true);
  perform set_config('drill.sem_ctl', (select count(*) from semantic.market_daily) || ' · '
    || (select count(*) from semantic.policy_meetings), true);
exception when others then
  perform set_config('drill.sem_nse', 'could not count: ' || sqlerrm, true);
  perform set_config('drill.sem_id', 'could not count: ' || sqlerrm, true);
  perform set_config('drill.sem_ctl', 'could not count: ' || sqlerrm, true);
end $$;
set local role anon;
select n, item, result from (
  select 0 as n, 'running as' as item, current_user::text as result
  union all select 1, 'core.source nse: is_suppressed', (select is_suppressed::text from core.source where key = 'nse')
  -- NSE-derived, 2A: must read 0 while suppressed
  union all select 10, 'api.index_members', (select count(*)::text from api.index_members)
  union all select 11, 'api.company_prices', (select count(*)::text from api.company_prices)
  union all select 12, 'api.company_financials', (select count(*)::text from api.company_financials)
  union all select 13, 'api.company_metrics', (select count(*)::text from api.company_metrics)
  union all select 14, 'api.company_corporate_actions', (select count(*)::text from api.company_corporate_actions)
  union all select 15, 'api.screener', (select count(*)::text from api.screener)
  union all select 16, 'api.peer_comps', (select count(*)::text from api.peer_comps)
  union all select 17, 'api.company_valuation', (select count(*)::text from api.company_valuation)
  union all select 18, 'api.valuation_scenarios', (select count(*)::text from api.valuation_scenarios)
  union all select 19, 'api.priced_in_operating', (select count(*)::text from api.priced_in_operating)
  union all select 20, 'api.priced_in_lenders', (select count(*)::text from api.priced_in_lenders)
  union all select 21, 'api.company_promoter_holding', (select count(*)::text from api.company_promoter_holding)
  union all select 22, 'api.rate_decision_moves (company · peer group)', (select count(*) filter (where subject_kind = 'company') || ' · ' || count(*) filter (where subject_kind = 'peer_group') from api.rate_decision_moves)
  union all select 23, 'api.rate_decision_stats (company · peer group)', (select count(*) filter (where subject_kind = 'company') || ' · ' || count(*) filter (where subject_kind = 'peer_group') from api.rate_decision_stats)
  union all select 24, 'api.company_rate_cycles', (select count(*)::text from api.company_rate_cycles)
  union all select 25, 'semantic: company_valuations · company_financials · rate_decision_moves', current_setting('drill.sem_nse')
  -- the company list: identity rows stay, price and index chips go
  union all select 30, 'api.companies (rows · with last_close · with index_slugs)', (select count(*) || ' · ' || count(last_close) || ' · ' || count(index_slugs) from api.companies)
  union all select 31, 'semantic.companies', current_setting('drill.sem_id')
  -- controls, module 1 and policy: unchanged
  union all select 40, 'api.series (FRED · yfinance)', (select count(*) filter (where key like 'fred.%') || ' · ' || count(*) filter (where key like 'yf.%') from api.series)
  union all select 41, 'api.series_latest (tape)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.series_latest)
  union all select 42, 'api.observations', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observations)
  union all select 43, 'api.observation_steps (Fed band)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observation_steps)
  union all select 44, 'api.market_reactions', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.market_reactions)
  union all select 45, 'api.policy_meetings · with both Fed bounds', (select count(*) || ' · ' || count(*) filter (where fed_target_lower_pct is not null and fed_target_upper_pct is not null) from api.policy_meetings)
  union all select 46, 'api.documents', (select count(*)::text from api.documents)
  union all select 47, 'api.rate_cycles (policy cycles)', (select count(*)::text from api.rate_cycles)
  union all select 48, 'api.series yf.^NSEI (Yahoo''s Nifty 50 index)', (select count(*)::text from api.series where key = 'yf.^NSEI')
  union all select 49, 'semantic: market_daily · policy_meetings', current_setting('drill.sem_ctl')
) r order by n;


-- ========== Block 3: restore (un-suppress nse, then check: every row as in block 1) ==========
update core.source set is_suppressed = false where key = 'nse';
do $$ begin
  perform set_config('drill.sem_nse', (select count(*) from semantic.company_valuations) || ' · '
    || (select count(*) from semantic.company_financials) || ' · ' || (select count(*) from semantic.rate_decision_moves), true);
  perform set_config('drill.sem_id', (select count(*)::text from semantic.companies), true);
  perform set_config('drill.sem_ctl', (select count(*) from semantic.market_daily) || ' · '
    || (select count(*) from semantic.policy_meetings), true);
exception when others then
  perform set_config('drill.sem_nse', 'could not count: ' || sqlerrm, true);
  perform set_config('drill.sem_id', 'could not count: ' || sqlerrm, true);
  perform set_config('drill.sem_ctl', 'could not count: ' || sqlerrm, true);
end $$;
set local role anon;
select n, item, result from (
  select 0 as n, 'running as' as item, current_user::text as result
  union all select 1, 'core.source nse: is_suppressed', (select is_suppressed::text from core.source where key = 'nse')
  -- NSE-derived, 2A: must read 0 while suppressed
  union all select 10, 'api.index_members', (select count(*)::text from api.index_members)
  union all select 11, 'api.company_prices', (select count(*)::text from api.company_prices)
  union all select 12, 'api.company_financials', (select count(*)::text from api.company_financials)
  union all select 13, 'api.company_metrics', (select count(*)::text from api.company_metrics)
  union all select 14, 'api.company_corporate_actions', (select count(*)::text from api.company_corporate_actions)
  union all select 15, 'api.screener', (select count(*)::text from api.screener)
  union all select 16, 'api.peer_comps', (select count(*)::text from api.peer_comps)
  union all select 17, 'api.company_valuation', (select count(*)::text from api.company_valuation)
  union all select 18, 'api.valuation_scenarios', (select count(*)::text from api.valuation_scenarios)
  union all select 19, 'api.priced_in_operating', (select count(*)::text from api.priced_in_operating)
  union all select 20, 'api.priced_in_lenders', (select count(*)::text from api.priced_in_lenders)
  union all select 21, 'api.company_promoter_holding', (select count(*)::text from api.company_promoter_holding)
  union all select 22, 'api.rate_decision_moves (company · peer group)', (select count(*) filter (where subject_kind = 'company') || ' · ' || count(*) filter (where subject_kind = 'peer_group') from api.rate_decision_moves)
  union all select 23, 'api.rate_decision_stats (company · peer group)', (select count(*) filter (where subject_kind = 'company') || ' · ' || count(*) filter (where subject_kind = 'peer_group') from api.rate_decision_stats)
  union all select 24, 'api.company_rate_cycles', (select count(*)::text from api.company_rate_cycles)
  union all select 25, 'semantic: company_valuations · company_financials · rate_decision_moves', current_setting('drill.sem_nse')
  -- the company list: identity rows stay, price and index chips go
  union all select 30, 'api.companies (rows · with last_close · with index_slugs)', (select count(*) || ' · ' || count(last_close) || ' · ' || count(index_slugs) from api.companies)
  union all select 31, 'semantic.companies', current_setting('drill.sem_id')
  -- controls, module 1 and policy: unchanged
  union all select 40, 'api.series (FRED · yfinance)', (select count(*) filter (where key like 'fred.%') || ' · ' || count(*) filter (where key like 'yf.%') from api.series)
  union all select 41, 'api.series_latest (tape)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.series_latest)
  union all select 42, 'api.observations', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observations)
  union all select 43, 'api.observation_steps (Fed band)', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.observation_steps)
  union all select 44, 'api.market_reactions', (select count(*) filter (where series_key like 'fred.%') || ' · ' || count(*) filter (where series_key like 'yf.%') from api.market_reactions)
  union all select 45, 'api.policy_meetings · with both Fed bounds', (select count(*) || ' · ' || count(*) filter (where fed_target_lower_pct is not null and fed_target_upper_pct is not null) from api.policy_meetings)
  union all select 46, 'api.documents', (select count(*)::text from api.documents)
  union all select 47, 'api.rate_cycles (policy cycles)', (select count(*)::text from api.rate_cycles)
  union all select 48, 'api.series yf.^NSEI (Yahoo''s Nifty 50 index)', (select count(*)::text from api.series where key = 'yf.^NSEI')
  union all select 49, 'semantic: market_daily · policy_meetings', current_setting('drill.sem_ctl')
) r order by n;


-- ========== Recording the run ==========
-- ops.drill_run accepts only 'd3_suppression' and 'd4_freshness' (migration 0021's check), and a new drill name needs a
-- migration, which this job does not name (CLAUDE.md §7). So the three result tables are pasted into #132 and a session
-- records the pass or fail there, with the date. Nothing else is written.
