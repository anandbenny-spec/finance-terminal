-- 0029: earnings views, release 2B-lite (issue #181, job 2B-18; Spec 02B §13.6, §13.3, §13.4, §3.1, §3.5; columns of Spec 02B.1 C9).
-- Four `api` views, all security_invoker: api.citation_pages, api.earnings_calls, api.call_figures, api.earnings_latest.
-- Every view requires both the call event's source and the transcript document's source to be unsuppressed (§3.1), and is gated
-- by field policy with a CASE (§3.5). No view holds chunk text or a page link to our copy of a file: a page link is the
-- document's own source_url, minus any fragment, plus #page=N (§13.4). Calls are ordered by period_end, never by season_key
-- (a late call for an earlier quarter may be stored in a later season; #285).
-- Decided by Claude (standing delegation, 24 Oct 2026): core.ai_output's own policy hides an output while its ai.<task> is hidden,
-- so a visitor's query can't see that a summary is published-but-held. summary_state is therefore 'held_back' while
-- ai.call_summary is hidden for a call whose status is 'stored' (the calls_ai task summarises exactly those), or whenever the
-- querying role can see a published output; it is 'not_generated' for the other hidden calls. Never 'available' while hidden.

------------------------------------------------------------------ api.citation_pages
-- One row per verified document_span citation into a document that has page rows, whose output's ai.<task> is shown or labelled.
-- page_first holds char_start, page_last holds char_end - 1 (they differ for a span across a page break).
create view api.citation_pages with (security_invoker = true) as
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
  and (d.event_id is null or (ev.id is not null and es.id is not null and not es.is_suppressed));

------------------------------------------------------------------ api.earnings_calls
-- One row per published call. Counts and output ids are null while their field is hidden.
create view api.earnings_calls with (security_invoker = true) as
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
         row_number() over (partition by c.entity_id
                            order by c.period_end desc nulls last, c.filed_at desc, c.event_id desc) as rn_all,
         row_number() over (partition by c.entity_id, (c.status = 'stored')
                            order by c.period_end desc nulls last, c.filed_at desc, c.event_id desc) as rn_stored,
         row_number() over (partition by c.entity_id, (c.summary_state = 'available')
                            order by c.period_end desc nulls last, c.filed_at desc, c.event_id desc) as rn_avail,
         bool_or(c.status = 'stored') over (partition by c.entity_id) as has_stored,
         bool_or(c.summary_state = 'available') over (partition by c.entity_id) as has_avail
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

------------------------------------------------------------------ api.call_figures
-- One row per target (equity.guidance) or stated figure (equity.stated_figure) of a published guidance_extraction output,
-- duplicates left out, only while ai.guidance_extraction is shown or labelled. figure_kind is the target's own kind (point,
-- range, floor, ceiling, direction), null for a stated figure. A stated figure's speaker_role is 'management' always.
create view api.call_figures with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok,
         max(label) filter (where status in ('shown','labelled')) as lbl
  from core.field_policy where field_key = 'ai.guidance_extraction'
), f as (
  select 'target'::text as kind, x.call_event_id, x.ai_output_id, x.citation_no, x.metric, x.metric_label,
         x.metric_text, x.kind as figure_kind, x.value, x.low, x.high, x.unit, x.direction, x.comparison,
         x.period_text, x.period_kind, x.target_start as period_start, x.target_end as period_end,
         x.basis, x.growth_basis, x.scope_text, x.speaker_role
  from equity.guidance x
  where x.duplicate_of is null
  union all
  select 'stated'::text, s.call_event_id, s.ai_output_id, s.citation_no, s.metric, s.metric_label,
         s.metric_text, null::text, s.value, null::numeric, null::numeric, s.unit, null::text, null::text,
         s.period_text, s.period_kind, s.period_start, s.period_end,
         s.basis, s.growth_basis, s.scope_text, 'management'::text
  from equity.stated_figure s
  where s.duplicate_of is null
)
select f.kind, c.company_slug, c.call_slug, c.fiscal_label,
       f.metric, f.metric_label, f.metric_text, f.figure_kind, f.value, f.low, f.high, f.unit, f.direction, f.comparison,
       f.period_text, f.period_kind, f.period_start, f.period_end, f.basis, f.growth_basis, f.scope_text, f.speaker_role,
       f.ai_output_id, f.citation_no,
       p.page_first, p.page_last, p.page_url,
       g.lbl as extraction_label
from f
cross join g
join core.ai_output o on o.id = f.ai_output_id and o.task = 'guidance_extraction' and o.status = 'published'
join core.event ev on ev.id = f.call_event_id
join api.earnings_calls c on c.call_slug = ev.slug
left join api.citation_pages p on p.ai_output_id = f.ai_output_id and p.citation_no = f.citation_no
where g.ok;

------------------------------------------------------------------ api.earnings_latest
-- One row per company in 2A's universe (api.companies): its newest published call, the default call's fiscal label, or
-- status 'none_found' with the window the discovery covered (its earliest stored dissemination date, as data).
create view api.earnings_latest with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as fig_ok
  from core.field_policy where field_key = 'ai.guidance_extraction'
), w as (
  select min(filed_at)::date as window_from, max(filed_at)::date as window_to from api.earnings_calls
)
select co.slug as company_slug, co.name as company_name, co.short_name,
       n.call_slug, n.fiscal_label, n.period_end, n.season_key, n.call_date, n.call_date_source, n.filed_at, n.exchange,
       n.route, coalesce(n.status, 'none_found') as status, n.official_url, n.transcript_document_id,
       n.summary_output_id, n.summary_state, n.figures_output_id, n.target_count, n.stated_figure_count,
       n.unattributed_targets,
       dflt.call_slug as default_call_slug, dflt.fiscal_label as default_fiscal_label,
       dflt.roles_partial,
       case when g.fig_ok then
         (select coalesce(sum(a.unattributed_targets), 0) from api.earnings_calls a where a.company_slug = co.slug)
       end as unattributed_targets_all,
       case when n.call_slug is null then w.window_from end as window_from,
       case when n.call_slug is null then w.window_to end as window_to
from api.companies co
cross join g
cross join w
left join api.earnings_calls n on n.company_slug = co.slug and n.is_newest
left join api.earnings_calls dflt on dflt.company_slug = co.slug and dflt.is_default;

grant select on api.citation_pages, api.earnings_calls, api.call_figures, api.earnings_latest to anon, authenticated, service_role;

------------------------------------------------------------------ comments
comment on view api.citation_pages is 'Page of a verified transcript citation: first and last PDF page of its span and a link to the source''s own PDF at that page (never our copy). No row where the document has no page map. Never transcript text.';
comment on view api.earnings_calls is 'One row per published earnings call: quarter, call date, status, official link, summary state and the counts of AI-extracted targets and stated figures (null while their field is held back), and the flags is_newest, is_latest_stored and is_default (the call the page opens on). Never transcript text.';
comment on view api.call_figures is 'One row per target or stated figure management gave on an earnings call (AI-extracted, cited), duplicates left out, with the PDF page of its quote. Empty while ai.guidance_extraction is held back. Never transcript text.';
comment on view api.earnings_latest is 'One row per company: its newest published earnings call and the default call''s fiscal label, or status none_found with the window the discovery covered. Never transcript text.';
