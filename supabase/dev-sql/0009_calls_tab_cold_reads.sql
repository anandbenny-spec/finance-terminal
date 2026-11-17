-- 0009_calls_tab_cold_reads: #620 (the Earnings calls tab's guidance reads time out when several tabs are first-loaded together).
-- Part 1 measures with it, part 2's done-when re-runs it unchanged: every read the company page's Earnings calls tab makes,
-- timed as a visitor (role anon, the Data API's 3 s limit) for the 8 companies with the most guidance rows on dev, in one run,
-- with the tab's fields as they are on production on 17 Nov 2026 and ai.call_summary shown as well (the state #185's re-flip
-- leads to, so the strictest case): ai.guidance_extraction, ai.call_tone, equity.remarks_change, equity.guidance_outcome and
-- equity.results_move shown, equity.guidance_hit_rate hidden. #516's 0006 and #185's 0008 timed one company with
-- equity.guidance_outcome hidden, when the tab did not read api.call_guidance at all; production has shown it since
-- 14 Nov 2026, 19:00 IST (#532).
-- DEV ONLY, through the dev-sql workflow. It changes field policies inside the run's one transaction, which ends with ROLLBACK
-- (an error anywhere rolls it back too), so dev's visitors never see a field change and dev's rows are as before.
--   1. As service_role: the fields above are set for the length of the transaction, and the 8 companies are picked from the
--      base tables (published management targets of a published guidance_extraction output, duplicates left out).
--   2. As anon: company 1's default call (is_default, as the page picks it), then a cold probe: company 1's api.call_guidance
--      and api.call_figures reads, the first statements on those views in this session, under the 3 s limit.
--   3. As anon, with no limit: each company's default call and the output ids the tab quotes, picked from the api views as
--      web/lib/queries/calls.ts picks them (the summary's and the figures' outputs, every guidance-record row's output, the
--      call's tone output; api.ai_outputs for the summary and the tone).
--   4. As anon with a 3 s statement timeout: each read under explain (analyze, buffers, format json) in PostgREST's shape (the
--      page, limit 1000, and a filter-only count in one statement), company by company in the page's order. Its planning plus
--      execution time and the buffers it touched are kept in a setting ('TIMEOUT (3 s)' when the limit cancels it). Then the
--      two guidance views with no company filter.
--   5. The last statement prints ten GitHub notices with times, buffer and row counts only: no slug, no id, no row content.
-- Done-when bound (#620's card): every read under 300 ms for each company.
set local role service_role;

update core.field_policy set status = 'shown'
where field_key in ('ai.call_summary', 'ai.guidance_extraction', 'ai.call_tone', 'equity.remarks_change', 'equity.guidance_outcome', 'equity.results_move');
update core.field_policy set status = 'hidden' where field_key = 'equity.guidance_hit_rate';

select set_config('t.shown', (select count(*) from core.field_policy
  where field_key in ('ai.call_summary', 'ai.guidance_extraction', 'ai.call_tone', 'equity.remarks_change', 'equity.guidance_outcome', 'equity.results_move')
    and status = 'shown')::text, true) as fields_shown,
       set_config('t.hit_hidden', (select count(*) from core.field_policy where field_key = 'equity.guidance_hit_rate' and status = 'hidden')::text, true) as hit_rate_hidden;

-- The 8 companies with the most guidance rows a visitor's record would list.
select set_config('t.c' || p.rn || '_slug', p.slug, true) as picked, set_config('t.c' || p.rn || '_n', p.n::text, true) as rows_
from (
  select row_number() over (order by count(*) desc, e.slug) as rn, e.slug, count(*) as n
  from equity.guidance x
  join core.ai_output o on o.id = x.ai_output_id and o.task = 'guidance_extraction' and o.status = 'published'
  join core.entity e on e.id = x.entity_id and e.is_published
  where x.duplicate_of is null and x.speaker_role = 'management'
  group by e.slug
  order by count(*) desc, e.slug
  limit 8
) p;

do $$
begin
  if coalesce(current_setting('t.c8_slug', true), '') = '' then
    raise exception 'dev holds fewer than 8 companies with guidance rows';
  end if;
end $$;

set local role anon;

-- Company 1's default call, for the cold probe's figures read.
select set_config('t.c1_call', coalesce(v.call_slug, ''), true),
       set_config('t.c1_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c1_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;

set local statement_timeout = '3s';

-- The cold probe: company 1's guidance record and figures, the first statements on these views in this session.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.cold_cg_ms', ms::text, true);
  perform set_config('t.cold_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.cold_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.cold_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c1_fig', true), '') in ('', 'none') then
    perform set_config('t.cold_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c1_slug', true), current_setting('t.c1_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c1_slug', true), current_setting('t.c1_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.cold_cf_ms', ms::text, true);
  perform set_config('t.cold_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.cold_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.cold_cf_ms', '3000', true);
end $$;

-- The picks, with no limit (not timed).
set local statement_timeout = 0;
-- Company 1: its default call and the outputs the tab quotes.
select set_config('t.c1_call', coalesce(v.call_slug, ''), true),
       set_config('t.c1_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c1_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c1_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c1_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c1_slug', true) and call_slug = current_setting('t.c1_call', true) limit 1), 'none'), true);
select set_config('t.c1_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c1_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c1_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c1_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c1_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c1_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c1_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c1_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);
-- Company 2: its default call and the outputs the tab quotes.
select set_config('t.c2_call', coalesce(v.call_slug, ''), true),
       set_config('t.c2_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c2_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c2_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c2_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c2_slug', true) and call_slug = current_setting('t.c2_call', true) limit 1), 'none'), true);
select set_config('t.c2_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c2_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c2_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c2_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c2_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c2_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c2_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c2_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);
-- Company 3: its default call and the outputs the tab quotes.
select set_config('t.c3_call', coalesce(v.call_slug, ''), true),
       set_config('t.c3_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c3_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c3_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c3_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c3_slug', true) and call_slug = current_setting('t.c3_call', true) limit 1), 'none'), true);
select set_config('t.c3_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c3_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c3_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c3_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c3_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c3_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c3_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c3_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);
-- Company 4: its default call and the outputs the tab quotes.
select set_config('t.c4_call', coalesce(v.call_slug, ''), true),
       set_config('t.c4_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c4_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c4_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c4_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c4_slug', true) and call_slug = current_setting('t.c4_call', true) limit 1), 'none'), true);
select set_config('t.c4_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c4_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c4_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c4_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c4_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c4_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c4_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c4_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);
-- Company 5: its default call and the outputs the tab quotes.
select set_config('t.c5_call', coalesce(v.call_slug, ''), true),
       set_config('t.c5_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c5_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c5_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c5_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c5_slug', true) and call_slug = current_setting('t.c5_call', true) limit 1), 'none'), true);
select set_config('t.c5_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c5_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c5_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c5_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c5_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c5_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c5_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c5_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);
-- Company 6: its default call and the outputs the tab quotes.
select set_config('t.c6_call', coalesce(v.call_slug, ''), true),
       set_config('t.c6_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c6_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c6_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c6_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c6_slug', true) and call_slug = current_setting('t.c6_call', true) limit 1), 'none'), true);
select set_config('t.c6_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c6_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c6_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c6_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c6_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c6_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c6_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c6_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);
-- Company 7: its default call and the outputs the tab quotes.
select set_config('t.c7_call', coalesce(v.call_slug, ''), true),
       set_config('t.c7_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c7_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c7_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c7_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c7_slug', true) and call_slug = current_setting('t.c7_call', true) limit 1), 'none'), true);
select set_config('t.c7_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c7_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c7_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c7_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c7_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c7_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c7_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c7_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);
-- Company 8: its default call and the outputs the tab quotes.
select set_config('t.c8_call', coalesce(v.call_slug, ''), true),
       set_config('t.c8_sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.c8_fig', coalesce(v.figures_output_id::text, 'none'), true)
from (select call_slug, summary_output_id, figures_output_id from api.earnings_calls where company_slug = current_setting('t.c8_slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;
select set_config('t.c8_tone', coalesce((select ai_output_id::text from api.call_tone where company_slug = current_setting('t.c8_slug', true) and call_slug = current_setting('t.c8_call', true) limit 1), 'none'), true);
select set_config('t.c8_quoted', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c8_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c8_fig', true), 'none')::bigint as id union all select nullif(current_setting('t.c8_tone', true), 'none')::bigint as id union all select ai_output_id from api.call_guidance where company_slug = current_setting('t.c8_slug', true)) u where id is not null) d), 'none'), true),
       set_config('t.c8_outs', coalesce((select string_agg(id::text, ',' order by id) from (select distinct id from (select nullif(current_setting('t.c8_sum', true), 'none')::bigint as id union all select nullif(current_setting('t.c8_tone', true), 'none')::bigint as id) u where id is not null) d), 'none'), true);

-- The tab's reads, company by company, in the page's order (web/lib/queries/calls.ts readCallsTab: wave 1, wave 2, wave 3).
set local statement_timeout = '3s';

-- Company 1.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_ec_ms', ms::text, true);
  perform set_config('t.c1_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_fp_ms', ms::text, true);
  perform set_config('t.c1_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c1_fig', true), '') in ('', 'none') then
    perform set_config('t.c1_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c1_slug', true), current_setting('t.c1_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c1_slug', true), current_setting('t.c1_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_cf_ms', ms::text, true);
  perform set_config('t.c1_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_rc_ms', ms::text, true);
  perform set_config('t.c1_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_cg_ms', ms::text, true);
  perform set_config('t.c1_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_rm_ms', ms::text, true);
  perform set_config('t.c1_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c1_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_ct_ms', ms::text, true);
  perform set_config('t.c1_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c1_outs', true), '') in ('', 'none') then
    perform set_config('t.c1_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c1_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c1_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_ao_ms', ms::text, true);
  perform set_config('t.c1_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c1_quoted', true), '') in ('', 'none') then
    perform set_config('t.c1_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c1_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c1_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_ac_ms', ms::text, true);
  perform set_config('t.c1_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c1_quoted', true), '') in ('', 'none') then
    perform set_config('t.c1_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c1_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c1_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c1_cp_ms', ms::text, true);
  perform set_config('t.c1_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c1_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c1_cp_ms', '3000', true);
end $$;

-- Company 2.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_ec_ms', ms::text, true);
  perform set_config('t.c2_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_fp_ms', ms::text, true);
  perform set_config('t.c2_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c2_fig', true), '') in ('', 'none') then
    perform set_config('t.c2_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c2_slug', true), current_setting('t.c2_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c2_slug', true), current_setting('t.c2_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_cf_ms', ms::text, true);
  perform set_config('t.c2_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_rc_ms', ms::text, true);
  perform set_config('t.c2_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_cg_ms', ms::text, true);
  perform set_config('t.c2_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_rm_ms', ms::text, true);
  perform set_config('t.c2_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c2_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_ct_ms', ms::text, true);
  perform set_config('t.c2_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c2_outs', true), '') in ('', 'none') then
    perform set_config('t.c2_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c2_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c2_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_ao_ms', ms::text, true);
  perform set_config('t.c2_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c2_quoted', true), '') in ('', 'none') then
    perform set_config('t.c2_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c2_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c2_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_ac_ms', ms::text, true);
  perform set_config('t.c2_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c2_quoted', true), '') in ('', 'none') then
    perform set_config('t.c2_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c2_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c2_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c2_cp_ms', ms::text, true);
  perform set_config('t.c2_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c2_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c2_cp_ms', '3000', true);
end $$;

-- Company 3.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_ec_ms', ms::text, true);
  perform set_config('t.c3_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_fp_ms', ms::text, true);
  perform set_config('t.c3_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c3_fig', true), '') in ('', 'none') then
    perform set_config('t.c3_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c3_slug', true), current_setting('t.c3_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c3_slug', true), current_setting('t.c3_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_cf_ms', ms::text, true);
  perform set_config('t.c3_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_rc_ms', ms::text, true);
  perform set_config('t.c3_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_cg_ms', ms::text, true);
  perform set_config('t.c3_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_rm_ms', ms::text, true);
  perform set_config('t.c3_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c3_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_ct_ms', ms::text, true);
  perform set_config('t.c3_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c3_outs', true), '') in ('', 'none') then
    perform set_config('t.c3_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c3_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c3_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_ao_ms', ms::text, true);
  perform set_config('t.c3_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c3_quoted', true), '') in ('', 'none') then
    perform set_config('t.c3_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c3_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c3_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_ac_ms', ms::text, true);
  perform set_config('t.c3_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c3_quoted', true), '') in ('', 'none') then
    perform set_config('t.c3_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c3_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c3_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c3_cp_ms', ms::text, true);
  perform set_config('t.c3_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c3_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c3_cp_ms', '3000', true);
end $$;

-- Company 4.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_ec_ms', ms::text, true);
  perform set_config('t.c4_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_fp_ms', ms::text, true);
  perform set_config('t.c4_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c4_fig', true), '') in ('', 'none') then
    perform set_config('t.c4_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c4_slug', true), current_setting('t.c4_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c4_slug', true), current_setting('t.c4_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_cf_ms', ms::text, true);
  perform set_config('t.c4_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_rc_ms', ms::text, true);
  perform set_config('t.c4_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_cg_ms', ms::text, true);
  perform set_config('t.c4_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_rm_ms', ms::text, true);
  perform set_config('t.c4_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c4_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_ct_ms', ms::text, true);
  perform set_config('t.c4_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c4_outs', true), '') in ('', 'none') then
    perform set_config('t.c4_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c4_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c4_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_ao_ms', ms::text, true);
  perform set_config('t.c4_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c4_quoted', true), '') in ('', 'none') then
    perform set_config('t.c4_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c4_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c4_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_ac_ms', ms::text, true);
  perform set_config('t.c4_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c4_quoted', true), '') in ('', 'none') then
    perform set_config('t.c4_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c4_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c4_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c4_cp_ms', ms::text, true);
  perform set_config('t.c4_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c4_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c4_cp_ms', '3000', true);
end $$;

-- Company 5.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_ec_ms', ms::text, true);
  perform set_config('t.c5_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_fp_ms', ms::text, true);
  perform set_config('t.c5_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c5_fig', true), '') in ('', 'none') then
    perform set_config('t.c5_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c5_slug', true), current_setting('t.c5_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c5_slug', true), current_setting('t.c5_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_cf_ms', ms::text, true);
  perform set_config('t.c5_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_rc_ms', ms::text, true);
  perform set_config('t.c5_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_cg_ms', ms::text, true);
  perform set_config('t.c5_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_rm_ms', ms::text, true);
  perform set_config('t.c5_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c5_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_ct_ms', ms::text, true);
  perform set_config('t.c5_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c5_outs', true), '') in ('', 'none') then
    perform set_config('t.c5_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c5_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c5_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_ao_ms', ms::text, true);
  perform set_config('t.c5_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c5_quoted', true), '') in ('', 'none') then
    perform set_config('t.c5_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c5_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c5_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_ac_ms', ms::text, true);
  perform set_config('t.c5_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c5_quoted', true), '') in ('', 'none') then
    perform set_config('t.c5_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c5_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c5_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c5_cp_ms', ms::text, true);
  perform set_config('t.c5_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c5_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c5_cp_ms', '3000', true);
end $$;

-- Company 6.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_ec_ms', ms::text, true);
  perform set_config('t.c6_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_fp_ms', ms::text, true);
  perform set_config('t.c6_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c6_fig', true), '') in ('', 'none') then
    perform set_config('t.c6_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c6_slug', true), current_setting('t.c6_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c6_slug', true), current_setting('t.c6_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_cf_ms', ms::text, true);
  perform set_config('t.c6_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_rc_ms', ms::text, true);
  perform set_config('t.c6_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_cg_ms', ms::text, true);
  perform set_config('t.c6_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_rm_ms', ms::text, true);
  perform set_config('t.c6_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c6_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_ct_ms', ms::text, true);
  perform set_config('t.c6_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c6_outs', true), '') in ('', 'none') then
    perform set_config('t.c6_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c6_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c6_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_ao_ms', ms::text, true);
  perform set_config('t.c6_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c6_quoted', true), '') in ('', 'none') then
    perform set_config('t.c6_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c6_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c6_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_ac_ms', ms::text, true);
  perform set_config('t.c6_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c6_quoted', true), '') in ('', 'none') then
    perform set_config('t.c6_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c6_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c6_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c6_cp_ms', ms::text, true);
  perform set_config('t.c6_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c6_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c6_cp_ms', '3000', true);
end $$;

-- Company 7.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_ec_ms', ms::text, true);
  perform set_config('t.c7_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_fp_ms', ms::text, true);
  perform set_config('t.c7_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c7_fig', true), '') in ('', 'none') then
    perform set_config('t.c7_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c7_slug', true), current_setting('t.c7_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c7_slug', true), current_setting('t.c7_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_cf_ms', ms::text, true);
  perform set_config('t.c7_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_rc_ms', ms::text, true);
  perform set_config('t.c7_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_cg_ms', ms::text, true);
  perform set_config('t.c7_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_rm_ms', ms::text, true);
  perform set_config('t.c7_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c7_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_ct_ms', ms::text, true);
  perform set_config('t.c7_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c7_outs', true), '') in ('', 'none') then
    perform set_config('t.c7_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c7_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c7_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_ao_ms', ms::text, true);
  perform set_config('t.c7_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c7_quoted', true), '') in ('', 'none') then
    perform set_config('t.c7_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c7_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c7_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_ac_ms', ms::text, true);
  perform set_config('t.c7_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c7_quoted', true), '') in ('', 'none') then
    perform set_config('t.c7_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c7_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c7_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c7_cp_ms', ms::text, true);
  perform set_config('t.c7_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c7_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c7_cp_ms', '3000', true);
end $$;

-- Company 8.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_ec_ms', ms::text, true);
  perform set_config('t.c8_ec', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_ec_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_fp_ms', ms::text, true);
  perform set_config('t.c8_fp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_fp_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c8_fig', true), '') in ('', 'none') then
    perform set_config('t.c8_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c8_slug', true), current_setting('t.c8_call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.c8_slug', true), current_setting('t.c8_call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_cf_ms', ms::text, true);
  perform set_config('t.c8_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_cf_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_rc_ms', ms::text, true);
  perform set_config('t.c8_rc', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_rc_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_cg_ms', ms::text, true);
  perform set_config('t.c8_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_rm_ms', ms::text, true);
  perform set_config('t.c8_rm', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_rm_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.c8_slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_ct_ms', ms::text, true);
  perform set_config('t.c8_ct', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_ct_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c8_outs', true), '') in ('', 'none') then
    perform set_config('t.c8_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id in (%s)', current_setting('t.c8_outs', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id in (%s)', current_setting('t.c8_outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_ao_ms', ms::text, true);
  perform set_config('t.c8_ao', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_ao_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c8_quoted', true), '') in ('', 'none') then
    perform set_config('t.c8_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.c8_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.c8_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_ac_ms', ms::text, true);
  perform set_config('t.c8_ac', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_ac_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  if coalesce(current_setting('t.c8_quoted', true), '') in ('', 'none') then
    perform set_config('t.c8_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.c8_quoted', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.c8_quoted', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.c8_cp_ms', ms::text, true);
  perform set_config('t.c8_cp', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.c8_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.c8_cp_ms', '3000', true);
end $$;

-- The same two reads with no company filter.
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || 'true' || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.all_cg_ms', ms::text, true);
  perform set_config('t.all_cg', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.all_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.all_cg_ms', '3000', true);
end $$;
do $$
declare j json; ms numeric;
begin
  execute 'explain (analyze, buffers, format json) with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || 'true' || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  ms := round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1);
  perform set_config('t.all_cf_ms', ms::text, true);
  perform set_config('t.all_cf', ms::text || ' ms (plan ' || round((j->0->>'Planning Time')::numeric, 1)::text || ', ' || ((j->0->'Plan'->>'Shared Hit Blocks')::bigint + (j->0->'Plan'->>'Shared Read Blocks')::bigint)::text || ' buf)', true);
exception when query_canceled then
  perform set_config('t.all_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.all_cf_ms', '3000', true);
end $$;

-- The notices (times, buffer and row counts only).
set local statement_timeout = 0;
copy (
  select line from (
  select 0 as o, '::notice title=#620 dev timing setup and cold probe::' || 'fields shown ' || coalesce(current_setting('t.shown', true), '-') || ' of 6 and equity.guidance_hit_rate hidden ' || coalesce(current_setting('t.hit_hidden', true), '-') || ' | cold probe (company 1, the first statements on these views in this session): call_guidance ' || coalesce(current_setting('t.cold_cg', true), '-') || ', call_figures ' || coalesce(current_setting('t.cold_cf', true), '-') || ' | no company filter: call_guidance ' || coalesce(current_setting('t.all_cg', true), '-') || ', call_figures ' || coalesce(current_setting('t.all_cf', true), '-') as line
  union all select 1 as o, '::notice title=#620 company 1 of 8 (' || coalesce(current_setting('t.c1_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c1_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c1_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c1_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c1_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c1_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c1_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c1_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c1_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c1_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c1_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c1_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c1_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c1_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c1_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c1_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 2 as o, '::notice title=#620 company 2 of 8 (' || coalesce(current_setting('t.c2_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c2_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c2_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c2_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c2_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c2_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c2_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c2_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c2_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c2_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c2_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c2_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c2_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c2_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c2_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c2_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 3 as o, '::notice title=#620 company 3 of 8 (' || coalesce(current_setting('t.c3_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c3_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c3_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c3_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c3_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c3_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c3_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c3_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c3_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c3_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c3_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c3_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c3_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c3_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c3_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c3_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 4 as o, '::notice title=#620 company 4 of 8 (' || coalesce(current_setting('t.c4_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c4_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c4_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c4_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c4_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c4_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c4_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c4_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c4_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c4_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c4_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c4_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c4_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c4_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c4_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c4_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 5 as o, '::notice title=#620 company 5 of 8 (' || coalesce(current_setting('t.c5_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c5_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c5_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c5_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c5_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c5_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c5_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c5_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c5_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c5_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c5_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c5_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c5_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c5_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c5_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c5_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 6 as o, '::notice title=#620 company 6 of 8 (' || coalesce(current_setting('t.c6_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c6_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c6_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c6_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c6_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c6_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c6_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c6_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c6_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c6_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c6_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c6_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c6_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c6_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c6_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c6_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 7 as o, '::notice title=#620 company 7 of 8 (' || coalesce(current_setting('t.c7_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c7_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c7_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c7_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c7_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c7_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c7_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c7_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c7_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c7_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c7_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c7_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c7_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c7_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c7_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c7_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 8 as o, '::notice title=#620 company 8 of 8 (' || coalesce(current_setting('t.c8_n', true), '-') || ' guidance rows; ' || coalesce(array_length(string_to_array(nullif(current_setting('t.c8_quoted', true), 'none'), ','), 1), 0)::text || ' outputs quoted)::' || ' | wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.c8_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.c8_fp', true), '-') || ' | wave 2: ' || 'call_figures ' || coalesce(current_setting('t.c8_cf', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.c8_rc', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.c8_cg', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.c8_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.c8_ct', true), '-') || ' | wave 3: ' || 'ai_outputs ' || coalesce(current_setting('t.c8_ao', true), '-') || ', ' || 'ai_citations ' || coalesce(current_setting('t.c8_ac', true), '-') || ', ' || 'citation_pages ' || coalesce(current_setting('t.c8_cp', true), '-') || ' | sum ' || round(coalesce(nullif(current_setting('t.c8_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.c8_cp_ms', true), '')::numeric, 0), 1)::text || ' ms, slowest ' || round(greatest(coalesce(nullif(current_setting('t.c8_ec_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_fp_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_cf_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_rc_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_cg_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_rm_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_ct_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_ao_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_ac_ms', true), '')::numeric, 0), coalesce(nullif(current_setting('t.c8_cp_ms', true), '')::numeric, 0)), 1)::text || ' ms' as line
  union all select 9 as o, (select '::notice title=#620 verdict (bound 300 ms a read)::' || 'reads at or over 300 ms: ' || count(*) filter (where z.v >= 300)::text || ' of ' || count(*)::text || ', timeouts ' || count(*) filter (where z.v >= 3000)::text || ', slowest ' || coalesce(round(max(z.v), 1)::text, '-') || ' ms' from (select nullif(current_setting(k, true), '')::numeric as v from unnest(array['t.c1_ec_ms', 't.c1_fp_ms', 't.c1_cf_ms', 't.c1_rc_ms', 't.c1_cg_ms', 't.c1_rm_ms', 't.c1_ct_ms', 't.c1_ao_ms', 't.c1_ac_ms', 't.c1_cp_ms', 't.c2_ec_ms', 't.c2_fp_ms', 't.c2_cf_ms', 't.c2_rc_ms', 't.c2_cg_ms', 't.c2_rm_ms', 't.c2_ct_ms', 't.c2_ao_ms', 't.c2_ac_ms', 't.c2_cp_ms', 't.c3_ec_ms', 't.c3_fp_ms', 't.c3_cf_ms', 't.c3_rc_ms', 't.c3_cg_ms', 't.c3_rm_ms', 't.c3_ct_ms', 't.c3_ao_ms', 't.c3_ac_ms', 't.c3_cp_ms', 't.c4_ec_ms', 't.c4_fp_ms', 't.c4_cf_ms', 't.c4_rc_ms', 't.c4_cg_ms', 't.c4_rm_ms', 't.c4_ct_ms', 't.c4_ao_ms', 't.c4_ac_ms', 't.c4_cp_ms', 't.c5_ec_ms', 't.c5_fp_ms', 't.c5_cf_ms', 't.c5_rc_ms', 't.c5_cg_ms', 't.c5_rm_ms', 't.c5_ct_ms', 't.c5_ao_ms', 't.c5_ac_ms', 't.c5_cp_ms', 't.c6_ec_ms', 't.c6_fp_ms', 't.c6_cf_ms', 't.c6_rc_ms', 't.c6_cg_ms', 't.c6_rm_ms', 't.c6_ct_ms', 't.c6_ao_ms', 't.c6_ac_ms', 't.c6_cp_ms', 't.c7_ec_ms', 't.c7_fp_ms', 't.c7_cf_ms', 't.c7_rc_ms', 't.c7_cg_ms', 't.c7_rm_ms', 't.c7_ct_ms', 't.c7_ao_ms', 't.c7_ac_ms', 't.c7_cp_ms', 't.c8_ec_ms', 't.c8_fp_ms', 't.c8_cf_ms', 't.c8_rc_ms', 't.c8_cg_ms', 't.c8_rm_ms', 't.c8_ct_ms', 't.c8_ao_ms', 't.c8_ac_ms', 't.c8_cp_ms']) as k) z where z.v is not null) as line
  ) n order by o
) to stdout;

rollback;
