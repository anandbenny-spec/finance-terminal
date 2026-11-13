-- 0006_calls_tab_timing: #516 part 1, step 1 (measure before changing). Times, as a visitor (role anon, the Data API's 3 s limit),
-- EVERY read the company page's Earnings calls tab makes, plus the company header's and metadata's reads, for the company with the
-- most calls on dev, with ai.guidance_extraction and ai.call_tone shown (and equity.remarks_change, which production shows since
-- 13 Nov 2026) inside a transaction that ends with ROLLBACK. DEV ONLY, through the dev-sql workflow. Anand's OK for this file is
-- recorded on #516 (CLAUDE.md section 7: it changes field policies, inside a transaction that ends with ROLLBACK).
--
-- Nothing this file does is ever committed: the workflow runs it in one transaction (psql --single-transaction), the file ends with
-- ROLLBACK, and an error anywhere rolls the transaction back too, so dev's visitors never see a field shown and dev's rows are as
-- before.
--   1. As service_role, the three fields are set to 'shown' for the length of the transaction (equity.guidance_outcome and
--      equity.results_move keep dev's status, as on production; their status is printed).
--   2. As anon, the company with the most calls a visitor sees, its default call (is_default) and the output ids the tab reads are
--      picked from the api views, as the page picks them (web/lib/queries/calls.ts).
--   3. As anon with a 3 s statement timeout (set role alone does not apply the Data API's limit), each read runs under
--      explain (analyze, format json) in PostgREST's shape: for a read with { count: "exact" }, the page (limit 1000) and a
--      filter-only count in one statement. Its planning plus execution time is kept in a setting ('TIMEOUT (3 s)' when the limit
--      cancels it) with the rows the filter matches. The reads run one after another, in the page's order (header, the tab's
--      first wave, the guidance record's citations, the selected call's wave, the tone panel's evidence), twice: pass 1 is the
--      coldest dev gets from this file, pass 2 the same reads warm.
--   4. The last statement prints GitHub notices with times and row counts only: no slug, no id, no row content.
-- A read over 300 ms is a plan problem (#516's card): it stops for Anand's yes on a pushdown migration.
set local role service_role;

select set_config('t.policy_outcome', coalesce((select status from core.field_policy where field_key = 'equity.guidance_outcome'), 'no row'), true),
       set_config('t.policy_results', coalesce((select status from core.field_policy where field_key = 'equity.results_move'), 'no row'), true),
       set_config('t.policy_remarks', coalesce((select status from core.field_policy where field_key = 'equity.remarks_change'), 'no row'), true);

update core.field_policy set status = 'shown'
where field_key in ('ai.guidance_extraction', 'ai.call_tone', 'equity.remarks_change');

select set_config('t.shown', (select count(*) from core.field_policy
  where field_key in ('ai.guidance_extraction', 'ai.call_tone', 'equity.remarks_change') and status = 'shown')::text, true) as fields_shown;

set local role anon;

-- The company with the most calls a visitor sees, and its default call (the tab's call with no call in the path).
with c as (
  select company_slug, count(*) as n from api.earnings_calls group by company_slug order by count(*) desc, company_slug limit 1
)
select set_config('t.slug', c.company_slug, true), set_config('t.calls', c.n::text, true) from c;

do $$
begin
  if coalesce(current_setting('t.slug', true), '') = '' then
    raise exception 'dev holds no calls a visitor can see';
  end if;
end $$;

select set_config('t.call', v.call_slug, true),
       set_config('t.sum', coalesce(v.summary_output_id::text, 'none'), true),
       set_config('t.fig', coalesce(v.figures_output_id::text, 'none'), true),
       set_config('t.outs', coalesce(nullif(concat_ws(',', v.summary_output_id, v.figures_output_id), ''), 'none'), true),
       set_config('t.figcount', (coalesce(v.target_count, 0) + coalesce(v.stated_figure_count, 0))::text, true)
from (select call_slug, summary_output_id, figures_output_id, target_count, stated_figure_count, is_default, period_end from api.earnings_calls where company_slug = current_setting('t.slug', true)
      order by is_default desc nulls last, period_end desc, call_slug limit 1) v;

select set_config('t.gids', coalesce((select string_agg(distinct ai_output_id::text, ',') from api.call_guidance
  where company_slug = current_setting('t.slug', true)), 'none'), true);

select set_config('t.tone', coalesce((select ai_output_id::text from api.call_tone
  where company_slug = current_setting('t.slug', true) and call_slug = current_setting('t.call', true) limit 1), 'none'), true);

-- results_moves is read only while equity.results_move is shown or labelled (readResults).
select set_config('t.rm_open', case when current_setting('t.policy_results', true) in ('shown', 'labelled') then 'yes' else 'none' end, true);

set local statement_timeout = '3s';
-- Pass 1.
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'select coalesce(json_agg(_postgrest_t), ''[]'') as body from (select v.* from api.companies v where ' || format('slug = %L', current_setting('t.slug', true)) || ' order by slug limit 1) _postgrest_t' into j;
  execute 'select count(*) from api.companies where ' || format('slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_co', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_co_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_co', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_co_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select v.* from api.company_corporate_actions v where ' || format('slug = %L', current_setting('t.slug', true)) || ' order by ex_date desc, kind, subject limit 1000 offset 0), pgrst_source_count as (select 1 from api.company_corporate_actions where ' || format('slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.company_corporate_actions where ' || format('slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_ca', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_ca_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_ca', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_ca_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select index_slug, index_name, snapshot_date, slug, name, short_name, nse_symbol, market, currency from api.index_members v where ' || 'true' || ' order by index_slug, slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.index_members where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.index_members where ' || 'true' into n;
  perform set_config('t.p1_im', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_im_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_im', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_im_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select task_key, last_checked_at, last_success_at, status from api.task_freshness v where ' || 'true' || ' order by task_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.task_freshness where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.task_freshness where ' || 'true' into n;
  perform set_config('t.p1_tf', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_tf_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_tf', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_tf_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_ec', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_ec_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_ec_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.field_policy where ' || 'true' into n;
  perform set_config('t.p1_fp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_fp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_fp_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.call_guidance where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_cg', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_cg_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_cg_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select scope, scope_key, scope_label, delivered, missed, n, rate, first_stated_from, periods_to, outcome_label from api.guidance_hit_rates v where ' || format('scope = %L and scope_key = %L', 'company', current_setting('t.slug', true)) || ' order by scope_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.guidance_hit_rates where ' || format('scope = %L and scope_key = %L', 'company', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.guidance_hit_rates where ' || format('scope = %L and scope_key = %L', 'company', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_hr', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_hr_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_hr', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_hr_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_rc', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_rc_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_rc_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.gids', true), '') in ('', 'none') then
    perform set_config('t.p1_gac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) into n;
  perform set_config('t.p1_gac', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_gac_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_gac', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_gac_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.gids', true), '') in ('', 'none') then
    perform set_config('t.p1_gcp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) into n;
  perform set_config('t.p1_gcp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_gcp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_gcp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_gcp_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.sum', true), '') in ('', 'none') then
    perform set_config('t.p1_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id = %s', current_setting('t.sum', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id = %s', current_setting('t.sum', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_outputs where ' || format('id = %s', current_setting('t.sum', true)) into n;
  perform set_config('t.p1_ao', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_ao_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_ao_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.fig', true), '') in ('', 'none') then
    perform set_config('t.p1_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.slug', true), current_setting('t.call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.slug', true), current_setting('t.call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.slug', true), current_setting('t.call', true)) into n;
  perform set_config('t.p1_cf', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_cf_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_cf_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.outs', true), '') in ('', 'none') then
    perform set_config('t.p1_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) into n;
  perform set_config('t.p1_ac', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_ac_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_ac_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.outs', true), '') in ('', 'none') then
    perform set_config('t.p1_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) into n;
  perform set_config('t.p1_cp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_cp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_cp_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.rm_open', true), '') in ('', 'none') then
    perform set_config('t.p1_rm', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.results_moves where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_rm', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_rm_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_rm_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.call_tone where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p1_ct', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_ct_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_ct_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.tone', true), '') in ('', 'none') then
    perform set_config('t.p1_tao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id = %s', current_setting('t.tone', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id = %s', current_setting('t.tone', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_outputs where ' || format('id = %s', current_setting('t.tone', true)) into n;
  perform set_config('t.p1_tao', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_tao_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_tao', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_tao_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.tone', true), '') in ('', 'none') then
    perform set_config('t.p1_tac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) into n;
  perform set_config('t.p1_tac', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_tac_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_tac', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_tac_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.tone', true), '') in ('', 'none') then
    perform set_config('t.p1_tcp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) into n;
  perform set_config('t.p1_tcp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p1_tcp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p1_tcp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p1_tcp_ms', '3000', true);
end $$;
-- Pass 2.
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'select coalesce(json_agg(_postgrest_t), ''[]'') as body from (select v.* from api.companies v where ' || format('slug = %L', current_setting('t.slug', true)) || ' order by slug limit 1) _postgrest_t' into j;
  execute 'select count(*) from api.companies where ' || format('slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_co', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_co_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_co', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_co_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select v.* from api.company_corporate_actions v where ' || format('slug = %L', current_setting('t.slug', true)) || ' order by ex_date desc, kind, subject limit 1000 offset 0), pgrst_source_count as (select 1 from api.company_corporate_actions where ' || format('slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.company_corporate_actions where ' || format('slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_ca', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_ca_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_ca', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_ca_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select index_slug, index_name, snapshot_date, slug, name, short_name, nse_symbol, market, currency from api.index_members v where ' || 'true' || ' order by index_slug, slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.index_members where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.index_members where ' || 'true' into n;
  perform set_config('t.p2_im', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_im_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_im', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_im_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select task_key, last_checked_at, last_success_at, status from api.task_freshness v where ' || 'true' || ' order by task_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.task_freshness where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.task_freshness where ' || 'true' into n;
  perform set_config('t.p2_tf', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_tf_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_tf', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_tf_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default from api.earnings_calls v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.earnings_calls where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_ec', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_ec_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_ec', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_ec_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select field_key, status, label from api.field_policy v where ' || 'true' || ' order by field_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.field_policy where ' || 'true' || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.field_policy where ' || 'true' into n;
  perform set_config('t.p2_fp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_fp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_fp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_fp_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by target_end desc, metric, call_slug, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_guidance where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.call_guidance where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_cg', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_cg_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_cg', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_cg_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select scope, scope_key, scope_label, delivered, missed, n, rate, first_stated_from, periods_to, outcome_label from api.guidance_hit_rates v where ' || format('scope = %L and scope_key = %L', 'company', current_setting('t.slug', true)) || ' order by scope_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.guidance_hit_rates where ' || format('scope = %L and scope_key = %L', 'company', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.guidance_hit_rates where ' || format('scope = %L and scope_key = %L', 'company', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_hr', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_hr_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_hr', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_hr_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, filed_at, exchange, base_call_slug, base_fiscal_label, base_period_end, words_base, words_new, words_added, words_removed, similarity, new_speakers, label, pairs, policy_label from api.remarks_changes v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.remarks_changes where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_rc', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_rc_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_rc', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_rc_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.gids', true), '') in ('', 'none') then
    perform set_config('t.p2_gac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) into n;
  perform set_config('t.p2_gac', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_gac_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_gac', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_gac_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.gids', true), '') in ('', 'none') then
    perform set_config('t.p2_gcp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.gids', true)) into n;
  perform set_config('t.p2_gcp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_gcp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_gcp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_gcp_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.sum', true), '') in ('', 'none') then
    perform set_config('t.p2_ao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id = %s', current_setting('t.sum', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id = %s', current_setting('t.sum', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_outputs where ' || format('id = %s', current_setting('t.sum', true)) into n;
  perform set_config('t.p2_ao', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_ao_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_ao', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_ao_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.fig', true), '') in ('', 'none') then
    perform set_config('t.p2_cf', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures v where ' || format('company_slug = %L and call_slug = %L', current_setting('t.slug', true), current_setting('t.call', true)) || ' order by kind, period_end, metric, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.slug', true), current_setting('t.call', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.call_figures where ' || format('company_slug = %L and call_slug = %L', current_setting('t.slug', true), current_setting('t.call', true)) into n;
  perform set_config('t.p2_cf', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_cf_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_cf', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_cf_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.outs', true), '') in ('', 'none') then
    perform set_config('t.p2_ac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) into n;
  perform set_config('t.p2_ac', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_ac_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_ac', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_ac_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.outs', true), '') in ('', 'none') then
    perform set_config('t.p2_cp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.outs', true)) into n;
  perform set_config('t.p2_cp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_cp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_cp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_cp_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.rm_open', true), '') in ('', 'none') then
    perform set_config('t.p2_rm', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, company_name, currency, results_slug, call_slug, fiscal_label, occurs_at, window_key, pre_date, post_date, own_change_pct, nifty_change_pct, excess_pct, left_out_reason, attribution from api.results_moves v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by occurs_at, results_slug, window_key limit 1000 offset 0), pgrst_source_count as (select 1 from api.results_moves where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.results_moves where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_rm', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_rm_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_rm', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_rm_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone v where ' || format('company_slug = %L', current_setting('t.slug', true)) || ' order by period_end desc, call_slug limit 1000 offset 0), pgrst_source_count as (select 1 from api.call_tone where ' || format('company_slug = %L', current_setting('t.slug', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.call_tone where ' || format('company_slug = %L', current_setting('t.slug', true)) into n;
  perform set_config('t.p2_ct', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_ct_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_ct', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_ct_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.tone', true), '') in ('', 'none') then
    perform set_config('t.p2_tao', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs v where ' || format('id = %s', current_setting('t.tone', true)) || ' order by id limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_outputs where ' || format('id = %s', current_setting('t.tone', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_outputs where ' || format('id = %s', current_setting('t.tone', true)) into n;
  perform set_config('t.p2_tao', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_tao_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_tao', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_tao_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.tone', true), '') in ('', 'none') then
    perform set_config('t.p2_tac', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations v where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.ai_citations where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) into n;
  perform set_config('t.p2_tac', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_tac_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_tac', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_tac_ms', '3000', true);
end $$;
do $$
declare j json; n bigint;
begin
  if coalesce(current_setting('t.tone', true), '') in ('', 'none') then
    perform set_config('t.p2_tcp', 'not read', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || 'with pgrst_source as (select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages v where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ' order by ai_output_id, citation_no limit 1000 offset 0), pgrst_source_count as (select 1 from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) || ') select (select pg_catalog.count(*) from pgrst_source_count) as total_result_set, pg_catalog.count(_postgrest_t) as page_total, coalesce(json_agg(_postgrest_t), ''[]'') as body from pgrst_source _postgrest_t' into j;
  execute 'select count(*) from api.citation_pages where ' || format('ai_output_id in (%s)', current_setting('t.tone', true)) into n;
  perform set_config('t.p2_tcp', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text || ' ms/' || n || ' rows', true);
  perform set_config('t.p2_tcp_ms', round((j->0->>'Planning Time')::numeric + (j->0->>'Execution Time')::numeric, 1)::text, true);
exception when query_canceled then
  perform set_config('t.p2_tcp', 'TIMEOUT (3 s)', true);
  perform set_config('t.p2_tcp_ms', '3000', true);
end $$;
set local role service_role;
copy (
  select '::notice title=#516 calls tab timing on dev: setup::fields shown ' || current_setting('t.shown') || ' of 3, equity.guidance_outcome ' || current_setting('t.policy_outcome') || ', equity.results_move ' || current_setting('t.policy_results') || ', equity.remarks_change before the file ' || current_setting('t.policy_remarks') || ' | the company with the most calls: ' || current_setting('t.calls') || ' calls; its default call: ' || current_setting('t.figcount') || ' figures, summary ' || case when current_setting('t.sum') = 'none' then 'not given' else 'given' end || ', guidance outputs ' || case when current_setting('t.gids') = 'none' then '0' else (length(current_setting('t.gids')) - length(replace(current_setting('t.gids'), ',', '')) + 1)::text end || ', tone ' || case when current_setting('t.tone') = 'none' then 'none' else 'given' end
  union all select '::notice title=#516 calls tab reads on dev, pass 1 (cold as this file gets; anon, 3 s limit, figures, tone and remarks shown)::' || 'header: ' || 'companies ' || coalesce(current_setting('t.p1_co', true), '-') || ', ' || 'company_corporate_actions ' || coalesce(current_setting('t.p1_ca', true), '-') || ', ' || 'index_members ' || coalesce(current_setting('t.p1_im', true), '-') || ', ' || 'task_freshness ' || coalesce(current_setting('t.p1_tf', true), '-') || ' | ' || 'wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.p1_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.p1_fp', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.p1_cg', true), '-') || ', ' || 'guidance_hit_rates ' || coalesce(current_setting('t.p1_hr', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.p1_rc', true), '-') || ' | ' || 'guidance quotes: ' || 'ai_citations (guidance) ' || coalesce(current_setting('t.p1_gac', true), '-') || ', ' || 'citation_pages (guidance) ' || coalesce(current_setting('t.p1_gcp', true), '-') || ' | ' || 'selected call: ' || 'ai_outputs (summary) ' || coalesce(current_setting('t.p1_ao', true), '-') || ', ' || 'call_figures ' || coalesce(current_setting('t.p1_cf', true), '-') || ', ' || 'ai_citations (call) ' || coalesce(current_setting('t.p1_ac', true), '-') || ', ' || 'citation_pages (call) ' || coalesce(current_setting('t.p1_cp', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.p1_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.p1_ct', true), '-') || ' | ' || 'tone evidence: ' || 'ai_outputs (tone) ' || coalesce(current_setting('t.p1_tao', true), '-') || ', ' || 'ai_citations (tone) ' || coalesce(current_setting('t.p1_tac', true), '-') || ', ' || 'citation_pages (tone) ' || coalesce(current_setting('t.p1_tcp', true), '-') || ' | sum of every read: ' || round(coalesce(nullif(current_setting('t.p1_co_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_ca_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_im_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_tf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_hr_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_gac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_gcp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_cp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_tao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_tac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p1_tcp_ms', true), '')::numeric, 0), 1)::text || ' ms'
  union all select '::notice title=#516 calls tab reads on dev, pass 2 (warm; anon, 3 s limit, figures, tone and remarks shown)::' || 'header: ' || 'companies ' || coalesce(current_setting('t.p2_co', true), '-') || ', ' || 'company_corporate_actions ' || coalesce(current_setting('t.p2_ca', true), '-') || ', ' || 'index_members ' || coalesce(current_setting('t.p2_im', true), '-') || ', ' || 'task_freshness ' || coalesce(current_setting('t.p2_tf', true), '-') || ' | ' || 'wave 1: ' || 'earnings_calls ' || coalesce(current_setting('t.p2_ec', true), '-') || ', ' || 'field_policy ' || coalesce(current_setting('t.p2_fp', true), '-') || ', ' || 'call_guidance ' || coalesce(current_setting('t.p2_cg', true), '-') || ', ' || 'guidance_hit_rates ' || coalesce(current_setting('t.p2_hr', true), '-') || ', ' || 'remarks_changes ' || coalesce(current_setting('t.p2_rc', true), '-') || ' | ' || 'guidance quotes: ' || 'ai_citations (guidance) ' || coalesce(current_setting('t.p2_gac', true), '-') || ', ' || 'citation_pages (guidance) ' || coalesce(current_setting('t.p2_gcp', true), '-') || ' | ' || 'selected call: ' || 'ai_outputs (summary) ' || coalesce(current_setting('t.p2_ao', true), '-') || ', ' || 'call_figures ' || coalesce(current_setting('t.p2_cf', true), '-') || ', ' || 'ai_citations (call) ' || coalesce(current_setting('t.p2_ac', true), '-') || ', ' || 'citation_pages (call) ' || coalesce(current_setting('t.p2_cp', true), '-') || ', ' || 'results_moves ' || coalesce(current_setting('t.p2_rm', true), '-') || ', ' || 'call_tone ' || coalesce(current_setting('t.p2_ct', true), '-') || ' | ' || 'tone evidence: ' || 'ai_outputs (tone) ' || coalesce(current_setting('t.p2_tao', true), '-') || ', ' || 'ai_citations (tone) ' || coalesce(current_setting('t.p2_tac', true), '-') || ', ' || 'citation_pages (tone) ' || coalesce(current_setting('t.p2_tcp', true), '-') || ' | sum of every read: ' || round(coalesce(nullif(current_setting('t.p2_co_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_ca_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_im_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_tf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_ec_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_fp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_cg_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_hr_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_rc_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_gac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_gcp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_ao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_cf_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_ac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_cp_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_rm_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_ct_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_tao_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_tac_ms', true), '')::numeric, 0) + coalesce(nullif(current_setting('t.p2_tcp_ms', true), '')::numeric, 0), 1)::text || ' ms'
) to stdout;

rollback;
