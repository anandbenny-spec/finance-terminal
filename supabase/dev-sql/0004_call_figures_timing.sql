-- 0004_call_figures_timing: times the Earnings calls tab's three reads as a visitor (role anon, the Data API's 3 s limit) for the three
-- calls with the most figures on dev, with ai.guidance_extraction shown (issue #489, step 6; migration 0046). DEV ONLY, through the
-- dev-sql workflow. Anand's OK for this file: his "ok." of 13 Nov 2026, 09:38 IST, recorded on #489 and #185 (CLAUDE.md section 7: it
-- changes a field policy, inside a transaction that ends with ROLLBACK).
--
-- Nothing this file does is ever committed. The workflow runs it in one transaction and the file ends with ROLLBACK, and an error
-- anywhere rolls the transaction back too, so dev's visitors never see the field shown and dev's rows are as before:
--   1. as service_role, ai.guidance_extraction is set to 'shown' for the length of the transaction, and the three calls with the most
--      figures (targets and stated figures, duplicates left out) are picked from the base tables;
--   2. as anon, with a 3 s statement timeout (set role alone does not apply the Data API's limit), each call's three reads run under
--      explain (analyze, format json): readCallFigures (api.call_figures by company and call), readOutputPages (api.citation_pages
--      by the call's output) and readGuidanceRecord (api.call_guidance by company), the filters PostgREST sends;
--   3. each execution time is kept in a setting ('timeout' when the 3 s limit cancels the read), and the last statement prints them
--      as one GitHub notice: the "before" run (on main before 0046 reaches dev) and the "after" run (once Anand has pushed it).
-- The notice holds times and row counts only, in this order for calls 1, 2 and 3 (ranked by figures):
--   call_figures ms/rows, citation_pages ms/rows, call_guidance ms/rows. No slug, no id, no row content.
set local role service_role;

update core.field_policy set status = 'shown' where field_key = 'ai.guidance_extraction';

select set_config('t.n', (select count(*) from (
  select 1 from equity.guidance where duplicate_of is null group by call_event_id
  union all select 1 from equity.stated_figure where duplicate_of is null group by call_event_id) x)::text, true) as calls_with_figures;

with n as (
  select call_event_id, count(*) as n
  from (select call_event_id from equity.guidance where duplicate_of is null
        union all select call_event_id from equity.stated_figure where duplicate_of is null) u
  group by call_event_id order by count(*) desc, call_event_id limit 3
), r as (
  select row_number() over (order by n.n desc, n.call_event_id) as rk, e.slug as company_slug, ev.slug as call_slug,
         coalesce((select g.ai_output_id from equity.guidance g where g.call_event_id = n.call_event_id and g.duplicate_of is null limit 1),
                  (select s.ai_output_id from equity.stated_figure s where s.call_event_id = n.call_event_id and s.duplicate_of is null limit 1)) as out_id,
         n.n
  from n
  join equity.earnings_call ec on ec.event_id = n.call_event_id
  join core.event ev on ev.id = ec.event_id
  join core.entity e on e.id = ec.entity_id
)
select set_config('t.slug' || rk, company_slug, true), set_config('t.call' || rk, call_slug, true),
       set_config('t.out' || rk, out_id::text, true), set_config('t.figs' || rk, n::text, true)
from r;

do $$
begin
  if coalesce(current_setting('t.slug1', true), '') = '' then
    raise exception 'dev holds no figures to time: ai.guidance_extraction has no stored rows';
  end if;
end $$;

set local role anon;
set local statement_timeout = '3s';
do $$
declare j json;
begin
  if coalesce(current_setting('t.slug1', true), '') = '' then
    perform set_config('t.cf1', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures where company_slug = %L and call_slug = %L order by kind, period_end, metric, citation_no', current_setting('t.slug1', true), current_setting('t.call1', true)) into j;
  perform set_config('t.cf1', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cf1', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug1', true), '') = '' then
    perform set_config('t.cp1', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages where ai_output_id in (%s) order by ai_output_id, citation_no', current_setting('t.out1', true)) into j;
  perform set_config('t.cp1', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cp1', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug1', true), '') = '' then
    perform set_config('t.cg1', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance where company_slug = %L order by target_end desc, metric, call_slug, citation_no', current_setting('t.slug1', true)) into j;
  perform set_config('t.cg1', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cg1', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug2', true), '') = '' then
    perform set_config('t.cf2', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures where company_slug = %L and call_slug = %L order by kind, period_end, metric, citation_no', current_setting('t.slug2', true), current_setting('t.call2', true)) into j;
  perform set_config('t.cf2', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cf2', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug2', true), '') = '' then
    perform set_config('t.cp2', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages where ai_output_id in (%s) order by ai_output_id, citation_no', current_setting('t.out2', true)) into j;
  perform set_config('t.cp2', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cp2', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug2', true), '') = '' then
    perform set_config('t.cg2', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance where company_slug = %L order by target_end desc, metric, call_slug, citation_no', current_setting('t.slug2', true)) into j;
  perform set_config('t.cg2', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cg2', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug3', true), '') = '' then
    perform set_config('t.cf3', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label from api.call_figures where company_slug = %L and call_slug = %L order by kind, period_end, metric, citation_no', current_setting('t.slug3', true), current_setting('t.call3', true)) into j;
  perform set_config('t.cf3', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cf3', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug3', true), '') = '' then
    perform set_config('t.cp3', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages where ai_output_id in (%s) order by ai_output_id, citation_no', current_setting('t.out3', true)) into j;
  perform set_config('t.cp3', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cp3', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  if coalesce(current_setting('t.slug3', true), '') = '' then
    perform set_config('t.cg3', 'none', true);
    return;
  end if;
  execute 'explain (analyze, format json) ' || format('select company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on from api.call_guidance where company_slug = %L order by target_end desc, metric, call_slug, citation_no', current_setting('t.slug3', true)) into j;
  perform set_config('t.cg3', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cg3', 'TIMEOUT (3 s)', true);
end $$;

set local role service_role;
copy (select '::notice title=#489 call-figures timing on dev (anon, 3 s limit, figures shown)::calls with figures: ' || current_setting('t.n')
 || ' | call 1 (' || current_setting('t.figs1', true) || ' figures): call_figures ' || coalesce(current_setting('t.cf1', true), '-') || ', citation_pages ' || coalesce(current_setting('t.cp1', true), '-') || ', call_guidance ' || coalesce(current_setting('t.cg1', true), '-')
 || ' | call 2 (' || coalesce(current_setting('t.figs2', true), '-') || ' figures): call_figures ' || coalesce(current_setting('t.cf2', true), '-') || ', citation_pages ' || coalesce(current_setting('t.cp2', true), '-') || ', call_guidance ' || coalesce(current_setting('t.cg2', true), '-')
 || ' | call 3 (' || coalesce(current_setting('t.figs3', true), '-') || ' figures): call_figures ' || coalesce(current_setting('t.cf3', true), '-') || ', citation_pages ' || coalesce(current_setting('t.cp3', true), '-') || ', call_guidance ' || coalesce(current_setting('t.cg3', true), '-')) to stdout;

rollback;
