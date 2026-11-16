-- 0046 · api.call_figures filters by call before it computes (issue #489; #185's figures flip failed on production on 13 Nov 2026,
-- 09:31 to 09:34 IST, with a statement timeout in api.call_figures).
-- One view body replaced, same columns, same column order, same rows, same grants and comment; no table, index, policy or function
-- changes, and no field-policy row changes (ai.guidance_extraction stays as it is).
--
-- Why: PostgREST asks for one call as `company_slug = '...' and call_slug = '...'`. The old body built `f`, the union of EVERY
-- company's equity.guidance and equity.stated_figure rows (about 3,300 on production), and joined api.earnings_calls to it by the
-- event's slug, so the call filter reached the base tables only after every row had been read and run through the visitor's
-- row-level checks (each row's call, output, document and sources). While the field was hidden the first row's output check failed
-- and the query ended in 22 ms as anon, which hid the cost; shown, every row paid it (a statement timeout on a cold free-plan
-- database; Reliance's Calls tab answered 500).
-- Now the view starts at the call (equity.earnings_call with its event, company and sources), reads that call's targets and stated
-- figures in a LATERAL subquery keyed by the call's event (both tables are indexed on call_event_id), and looks each row's page up
-- through api.citation_pages one citation at a time (its primary key). The gate (ai.guidance_extraction shown or labelled) is read
-- once. api.earnings_calls is no longer joined: the three columns it supplied (company_slug, call_slug, fiscal_label) come from the
-- call directly, under the same visibility rules (the call event's source and the transcript document's source unsuppressed, 0029).
-- The rule is 0032's, 0042's and 0043's: compute per key where the filter can reach the base tables.
-- Meaning unchanged: one row per target or stated figure of a published guidance_extraction output, duplicates left out, only while
-- ai.guidance_extraction is shown or labelled, for a published call whose event source and transcript source are unsuppressed.
--
-- Left as they are (Decided by Claude, standing delegation, 24 Oct 2026, recorded on #489):
--  * api.citation_pages (0035's body): a rewrite that starts at core.ai_citation and finds the pages by LATERAL lookup was built and
--    measured on 60 companies x 3 calls (4,000 figures, 40 pages a transcript): Postgres pulled the lookup back into a join and read
--    every transcript page twice (about 200 ms against 5 ms for 0035's body for three outputs), so the old body stays. It already
--    starts at core.ai_citation's primary key when `ai_output_id in (...)` is given, and inside api.call_figures it is read one
--    citation at a time. Alternative: the rewrite.
--  * api.call_guidance (0028's body): with a company filter its plan starts at the company and reads equity.guidance by entity_id
--    (about 12 ms for 45 rows in the same data), so it is unchanged. #489's timing file reads it as a visitor on dev and shows the
--    numbers. Alternative: restructure it anyway.
--  * The page lookup in api.call_figures uses `limit 1`. A citation (ai_output_id, citation_no) is one row and the page map's rows do
--    not overlap (unique document_id and char_start), so it is one row either way; the limit keeps Postgres from turning the lookup
--    back into a join over every citation. Alternative: no limit.

------------------------------------------------------------------ api.call_figures (body: 0029's)
create or replace view api.call_figures with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok,
         max(label) filter (where status in ('shown','labelled')) as lbl
  from core.field_policy where field_key = 'ai.guidance_extraction'
)
select f.kind, e.slug as company_slug, ev.slug as call_slug, ec.fiscal_label,
       f.metric, f.metric_label, f.metric_text, f.figure_kind, f.value, f.low, f.high, f.unit, f.direction, f.comparison,
       f.period_text, f.period_kind, f.period_start, f.period_end, f.basis, f.growth_basis, f.scope_text, f.speaker_role,
       f.ai_output_id, f.citation_no,
       p.page_first, p.page_last, p.page_url,
       g.lbl as extraction_label
from g
cross join equity.earnings_call ec
join core.event ev on ev.id = ec.event_id
join core.source es on es.id = ev.source_id and not es.is_suppressed
join core.entity e on e.id = ec.entity_id
left join core.document d on d.id = ec.transcript_document_id
left join core.source ds on ds.id = d.source_id
cross join lateral (   -- this call's targets and stated figures, duplicates left out
  select 'target'::text as kind, x.ai_output_id, x.citation_no, x.metric, x.metric_label,
         x.metric_text, x.kind as figure_kind, x.value, x.low, x.high, x.unit, x.direction, x.comparison,
         x.period_text, x.period_kind, x.target_start as period_start, x.target_end as period_end,
         x.basis, x.growth_basis, x.scope_text, x.speaker_role
  from equity.guidance x
  where x.call_event_id = ec.event_id and x.duplicate_of is null
  union all
  select 'stated'::text, s.ai_output_id, s.citation_no, s.metric, s.metric_label,
         s.metric_text, null::text, s.value, null::numeric, null::numeric, s.unit, null::text, null::text,
         s.period_text, s.period_kind, s.period_start, s.period_end,
         s.basis, s.growth_basis, s.scope_text, 'management'::text
  from equity.stated_figure s
  where s.call_event_id = ec.event_id and s.duplicate_of is null
) f
join core.ai_output o on o.id = f.ai_output_id and o.task = 'guidance_extraction' and o.status = 'published'
left join lateral (   -- the page of this one citation (api.citation_pages is keyed by output and number)
  select cp.page_first, cp.page_last, cp.page_url
  from api.citation_pages cp
  where cp.ai_output_id = f.ai_output_id and cp.citation_no = f.citation_no
  limit 1
) p on true
where g.ok
  and (ec.transcript_document_id is null or (d.id is not null and ds.id is not null and not ds.is_suppressed));

------------------------------------------------------------------ comments (restated unchanged; grants are kept by create or replace)
comment on view api.call_figures is 'One row per target or stated figure management gave on an earnings call (AI-extracted, cited), duplicates left out, with the PDF page of its quote. Empty while ai.guidance_extraction is held back. Never transcript text.';
