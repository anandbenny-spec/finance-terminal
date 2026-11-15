-- 0005_call_tone_timing: #482 part 2 (the judge of ai.call_tone), before Anand's flip on production. DEV ONLY, through the dev-sql
-- workflow. With ai.call_tone shown inside a transaction that ends with ROLLBACK, it (a) counts api.call_tone's rows against the tone
-- rows of published calls (the card's item 1: the view returns nothing to anyone, postgres included, while the field is hidden, so
-- the count can only be taken with it shown), and (b) times the Earnings calls tab's four tone reads as a visitor (role anon, the Data
-- API's 3 s limit) for the company with the most tone rows: readCallTone (api.call_tone by company), its exact count, and the selected
-- call's readOutput (api.ai_outputs by id), readOutputCitations (api.ai_citations by output) and readOutputPages (api.citation_pages
-- by output), the filters PostgREST sends (web/lib/queries/calls.ts). The first half's lesson (#185, 13 Nov 2026): a gated view is
-- timed with its field shown before the flip. Anand's OK for this file is recorded on #482 (CLAUDE.md section 7: it changes a field
-- policy, inside a transaction that ends with ROLLBACK).
--
-- Nothing this file does is ever committed: the workflow runs it in one transaction, the file ends with ROLLBACK, and an error anywhere
-- rolls the transaction back too, so dev's visitors never see the field shown and dev's rows are as before. The last statement prints
-- one GitHub notice with counts and times only: no slug, no id, no row content.
set local role service_role;

update core.field_policy set status = 'shown' where field_key = 'ai.call_tone';

select set_config('t.rows', (select count(*) from equity.call_tone)::text, true) as tone_rows;

select set_config('t.expected', (
  select count(*)
  from equity.call_tone t
  join equity.earnings_call ec on ec.event_id = t.event_id
  join core.event ev on ev.id = ec.event_id and ev.is_published
  join core.entity e on e.id = ec.entity_id and e.is_published
  join core.source es on es.id = ev.source_id and not es.is_suppressed
  join core.document d on d.id = ec.transcript_document_id and d.is_published
  join core.source ds on ds.id = d.source_id and not ds.is_suppressed)::text, true) as expected_rows;

select set_config('t.view_sr', (select count(*) from api.call_tone)::text, true) as view_rows_service_role;

set local role anon;

-- The company with the most tone rows a visitor can see, and its newest call's output (the tab's default call).
with c as (
  select company_slug, count(*) as n, max(period_end) as newest
  from api.call_tone group by company_slug order by count(*) desc, company_slug limit 1
)
select set_config('t.slug', c.company_slug, true), set_config('t.calls', c.n::text, true),
       set_config('t.out', (select v.ai_output_id from api.call_tone v where v.company_slug = c.company_slug
                            order by v.period_end desc, v.call_slug limit 1)::text, true)
from c;

do $$
begin
  if coalesce(current_setting('t.slug', true), '') = '' then
    raise exception 'dev holds no visible tone rows to time';
  end if;
end $$;

set local statement_timeout = '3s';

select set_config('t.view_anon', (select count(*) from api.call_tone)::text, true) as view_rows_anon;
select set_config('t.bad_change', (select count(*) from api.call_tone where change is not null and (held_reason is not null or prev_call_slug is null or delta is null or se_delta is null))::text, true) as change_without_both;

do $$
declare j json;
begin
  execute 'explain (analyze, format json) ' || format('select company_slug, call_slug, fiscal_label, period_end, call_date, filed_at, exchange, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, prev_call_slug, prev_fiscal_label, delta, se_delta, change, held_reason, rule_version, policy_label from api.call_tone where company_slug = %L order by period_end desc, call_slug', current_setting('t.slug', true)) into j;
  perform set_config('t.ct', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.ct', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  execute 'explain (analyze, format json) ' || format('select count(*) from api.call_tone where company_slug = %L', current_setting('t.slug', true)) into j;
  perform set_config('t.cc', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms', true);
exception when query_canceled then
  perform set_config('t.cc', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  execute 'explain (analyze, format json) ' || format('select id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at from api.ai_outputs where id = %s order by id', current_setting('t.out', true)) into j;
  perform set_config('t.ao', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.ao', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  execute 'explain (analyze, format json) ' || format('select ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public from api.ai_citations where ai_output_id in (%s) order by ai_output_id, citation_no', current_setting('t.out', true)) into j;
  perform set_config('t.ac', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.ac', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare j json;
begin
  execute 'explain (analyze, format json) ' || format('select ai_output_id, citation_no, page_first, page_last, page_url from api.citation_pages where ai_output_id in (%s) order by ai_output_id, citation_no', current_setting('t.out', true)) into j;
  perform set_config('t.cp', round((j->0->>'Execution Time')::numeric, 1)::text || 'ms/' || (j->0->'Plan'->>'Actual Rows') || ' rows', true);
exception when query_canceled then
  perform set_config('t.cp', 'TIMEOUT (3 s)', true);
end $$;

set local role service_role;
copy (select '::notice title=#482 call-tone reads on dev (anon, 3 s limit, tone shown)::tone rows ' || current_setting('t.rows')
 || ', of published calls ' || current_setting('t.expected')
 || ', api.call_tone rows as service_role ' || current_setting('t.view_sr')
 || ', as anon ' || current_setting('t.view_anon')
 || ', anon rows with a change label but no comparison ' || current_setting('t.bad_change')
 || ' | the company with the most tone rows (' || current_setting('t.calls') || ' calls): call_tone ' || coalesce(current_setting('t.ct', true), '-')
 || ', its count ' || coalesce(current_setting('t.cc', true), '-')
 || ', newest call: ai_outputs ' || coalesce(current_setting('t.ao', true), '-')
 || ', ai_citations ' || coalesce(current_setting('t.ac', true), '-')
 || ', citation_pages ' || coalesce(current_setting('t.cp', true), '-')) to stdout;

rollback;
