-- 0035: the call summary version floor (issue #371, job 2B-19c; Anand, 10 Nov 2026, 19:09 and 19:16 IST on #178 and #371).
-- Production holds call summaries from older prompts (v5 to v9) that were judged short of the bar; the quote-led display ships only
-- v11 and later. Anand chose a view floor over superseding stored outputs: it fails closed and changes no stored row.
--  * core.call_summary_version_ok(prompt_version): true only when the string reads as `v<n>` with n >= 11 (no leading zero, at most
--    nine digits). Anything else, an older version or an unreadable string, is false. The floor is written once, here.
--  * api.earnings_calls: the `so` lateral takes the newest published call_summary AT OR ABOVE the floor, so a call whose only
--    summaries are older gets no summary_output_id and `not_generated` ("not yet"); the default-call flags follow, because they
--    read summary_state. While ai.call_summary is hidden the view answers as before (`held_back` for a stored call). api.earnings_latest
--    reads its summary columns from api.earnings_calls, so it needs no new definition and carries the floor with it.
--  * api.ai_outputs, api.ai_citations, api.citation_pages: rows whose task is call_summary count only at or above the floor, so the
--    content, the verified quotes and the page numbers of an older summary leave the Data API too. Every other task's rows are untouched.
-- No column, grant, security_invoker setting or COMMENT changes: four `create or replace view`s of the same shapes (api.ai_citations
-- and api.citation_pages are the latest definitions, 0023 and 0029). The function is executable by the roles that read these views, and
-- pins its search_path like every function in our schemas (it is called once per output row, so not being inlined costs nothing).
-- Decided by Claude (standing delegation, 24 Oct 2026), recorded on #371: api.citation_pages gets the floor too, though the card's list
-- names the other three. It only removes call_summary rows, so it tightens; leaving it would keep an older summary's page numbers readable.
-- Alternative: leave it out.

create function core.call_summary_version_ok(p_version text) returns boolean
language sql immutable parallel safe
set search_path = pg_catalog      -- every function in our schemas pins its search_path (0013, test H01); it uses only built-ins
as $$
  select case when p_version ~ '^v[1-9][0-9]{0,8}$' then substr(p_version, 2)::integer >= 11 else false end
$$;
revoke all on function core.call_summary_version_ok(text) from public;
grant execute on function core.call_summary_version_ok(text) to anon, authenticated, service_role;
comment on function core.call_summary_version_ok(text) is
  'The call summary version floor: true only when a call_summary output''s prompt_version reads as v<n> with n >= 11; an older or unreadable version is false (fails closed). The api views count a call_summary output only when this is true (#371).';

create or replace view api.earnings_calls with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.call_summary'), false) as sum_ok,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.guidance_extraction'), false) as fig_ok
  from core.field_policy
  where field_key in ('ai.call_summary','ai.guidance_extraction')
), c as (
  select ec.event_id, ec.entity_id, e.slug as company_slug, e.name as company_name, e.short_name,
         ev.slug as call_slug, ec.fiscal_label, ec.period_end, ec.season_key, ec.call_date, ec.call_date_source,
         ec.filed_at, ec.exchange, ec.route, ev.status,
         case when ev.status = 'on_company_website' then coalesce(ec.company_url, d.source_url)
              else coalesce(d.source_url, ec.company_url) end as official_url,
         ec.transcript_document_id, ec.unknown_role_share,
         case when ec.unknown_role_share is not null then ec.unknown_role_share > 0.10 end as roles_partial,
         case when g.sum_ok and so.id is not null then so.id end as summary_output_id,
         case when g.sum_ok and so.id is not null then 'available'
              when not g.sum_ok and (so.id is not null or ev.status = 'stored') then 'held_back'
              else 'not_generated' end as summary_state,
         case when g.fig_ok and fo.id is not null then fo.id end as figures_output_id,
         case when g.fig_ok and fo.id is not null then
           (select count(*) from equity.guidance x
             where x.call_event_id = ec.event_id and x.ai_output_id = fo.id
               and x.duplicate_of is null and x.speaker_role = 'management') end as target_count,
         case when g.fig_ok and fo.id is not null then
           (select count(*) from equity.stated_figure x
             where x.call_event_id = ec.event_id and x.ai_output_id = fo.id
               and x.duplicate_of is null) end as stated_figure_count,
         case when g.fig_ok and fo.id is not null then
           (select count(*) from equity.guidance x
             where x.call_event_id = ec.event_id and x.ai_output_id = fo.id
               and x.duplicate_of is null and x.speaker_role = 'unknown') end as unattributed_targets
  from equity.earnings_call ec
  cross join g
  join core.event ev on ev.id = ec.event_id
  join core.source es on es.id = ev.source_id and not es.is_suppressed
  join core.entity e on e.id = ec.entity_id
  left join core.document d on d.id = ec.transcript_document_id
  left join core.source ds on ds.id = d.source_id
  left join lateral (
    select o.id from core.ai_output o
    where o.task = 'call_summary' and o.status = 'published'
      and core.call_summary_version_ok(o.prompt_version)
      and (o.document_id = ec.transcript_document_id or o.event_id = ec.event_id)
    order by o.id desc limit 1) so on true
  left join lateral (
    select o.id from core.ai_output o
    where o.task = 'guidance_extraction' and o.status = 'published'
      and (o.document_id = ec.transcript_document_id or o.event_id = ec.event_id)
    order by o.id desc limit 1) fo on true
  where ec.transcript_document_id is null or (d.id is not null and ds.id is not null and not ds.is_suppressed)
), r as (
  select c.*,
         row_number() over (partition by c.company_slug, c.entity_id
                            order by c.period_end desc nulls last, c.filed_at desc, c.event_id desc) as rn_all,
         row_number() over (partition by c.company_slug, c.entity_id, (c.status = 'stored')
                            order by c.period_end desc nulls last, c.filed_at desc, c.event_id desc) as rn_stored,
         row_number() over (partition by c.company_slug, c.entity_id, (c.summary_state = 'available')
                            order by c.period_end desc nulls last, c.filed_at desc, c.event_id desc) as rn_avail,
         bool_or(c.status = 'stored') over (partition by c.company_slug, c.entity_id) as has_stored,
         bool_or(c.summary_state = 'available') over (partition by c.company_slug, c.entity_id) as has_avail
  from c
)
select r.company_slug, r.company_name, r.short_name, r.call_slug, r.fiscal_label, r.period_end, r.season_key,
       r.call_date, r.call_date_source, r.filed_at, r.exchange, r.route, r.status, r.official_url,
       r.transcript_document_id,
       r.summary_output_id, r.summary_state,
       r.figures_output_id, r.target_count, r.stated_figure_count,
       r.unknown_role_share, r.roles_partial, r.unattributed_targets,
       (r.rn_all = 1) as is_newest,
       (r.status = 'stored' and r.rn_stored = 1) as is_latest_stored,
       case when r.has_avail  then (r.summary_state = 'available' and r.rn_avail = 1)
            when r.has_stored then (r.status = 'stored' and r.rn_stored = 1)
            else (r.rn_all = 1) end as is_default
from r;

create or replace view api.ai_outputs with (security_invoker = true) as
select o.id, o.task, o.document_id, ev.slug as event_slug, n.slug as entity_slug, o.scope,
       o.body, o.score, o.label as output_label, o.content,
       fp.label as display_label,        -- 'AI summary', 'AI-extracted'
       o.model, o.created_at
from core.ai_output o
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
left join core.event ev on ev.id = o.event_id
left join core.entity n on n.id = o.entity_id
where o.task <> 'call_summary' or core.call_summary_version_ok(o.prompt_version);

create or replace view api.ai_citations with (security_invoker = true) as
select c.ai_output_id, c.citation_no, c.claim_path, c.kind,
       c.document_id, d.title as document_title, d.source_url, c.char_start, c.char_end,
       coalesce((select substr(string_agg(ch.body, '' order by ch.chunk_no),
                               c.char_start - min(ch.char_start) + 1, c.char_end - c.char_start)
                   from core.chunk ch
                  where ch.document_id = c.document_id
                    and ch.char_start < c.char_end and ch.char_end > c.char_start),
                core.citation_quote(c.ai_output_id, c.citation_no)) as quote,
       s.key as series_key, c.obs_date, ev.slug as event_slug,
       t.text_public
from core.ai_citation c
join core.ai_output o on o.id = c.ai_output_id
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
left join core.document d on d.id = c.document_id
left join core.doc_type t on t.key = d.doc_type
left join core.series s on s.id = c.series_id
left join core.event ev on ev.id = c.event_id
where c.verified
  and (o.task <> 'call_summary' or core.call_summary_version_ok(o.prompt_version));

create or replace view api.citation_pages with (security_invoker = true) as
select c.ai_output_id, c.citation_no,
       p1.pdf_page as page_first,
       p2.pdf_page as page_last,
       regexp_replace(d.source_url, '#.*$', '') || '#page=' || p1.pdf_page as page_url
from core.ai_citation c
join core.ai_output o on o.id = c.ai_output_id and o.status = 'published'
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
join core.document d on d.id = c.document_id and d.source_url is not null
join core.source ds on ds.id = d.source_id and not ds.is_suppressed
left join core.event ev on ev.id = d.event_id
left join core.source es on es.id = ev.source_id
join equity.transcript_page p1 on p1.document_id = c.document_id
                              and c.char_start >= p1.char_start and c.char_start < p1.char_end
join equity.transcript_page p2 on p2.document_id = c.document_id
                              and c.char_end - 1 >= p2.char_start and c.char_end - 1 < p2.char_end
where c.verified and c.kind = 'document_span'
  and (o.task <> 'call_summary' or core.call_summary_version_ok(o.prompt_version))
  and (d.event_id is null or (ev.id is not null and es.id is not null and not es.is_suppressed));

-- comments (restated unchanged; api.ai_outputs and api.ai_citations have none)
comment on view api.earnings_calls is 'One row per published earnings call: quarter, call date, status, official link, summary state and the counts of AI-extracted targets and stated figures (null while their field is held back), and the flags is_newest, is_latest_stored and is_default (the call the page opens on). Never transcript text.';
comment on view api.citation_pages is 'Page of a verified transcript citation: first and last PDF page of its span and a link to the source''s own PDF at that page (never our copy). No row where the document has no page map. Never transcript text.';
