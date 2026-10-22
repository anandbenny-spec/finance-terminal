-- 0006: api schema — what the site reads (exposed through the Data API; anon and authenticated)
-- Every view runs as the caller (security_invoker), so RLS on the base tables applies to visitors.
-- AI outputs show only when their field policy 'ai.<task>' is shown or labelled (missing row = hidden).

create view api.entities with (security_invoker = true) as
select e.kind, e.slug, e.name, e.short_name, e.market, e.currency, e.updated_at
from core.entity e;

create view api.search_index with (security_invoker = true) as
select 'entity'::text as item_type, e.kind as subtype, e.slug, e.name as title,
       null::text as parent_slug, null::date as on_date
from core.entity e
union all
select 'event', ev.event_type, ev.slug, ev.title, b.slug, ev.starts_on
from core.event ev join core.entity b on b.id = ev.entity_id;

create view api.field_policy with (security_invoker = true) as
select field_key, status, label from core.field_policy;

create view api.task_freshness with (security_invoker = true) as
select task_key, label, last_checked_at, last_success_at, status, next_due_at from core.task_freshness;

------------------------------------------------------------------ time series
create view api.series with (security_invoker = true) as
select s.key, s.name, s.kind, s.unit, s.frequency, s.fixing_time, s.fixing_tz, s.market, s.currency,
       coalesce(s.attribution, src.attribution) as attribution, s.source_url,
       r.first_date, r.last_date
from core.series s
join core.source src on src.id = s.source_id
left join lateral (select min(obs_date) first_date, max(obs_date) last_date
                   from core.observation o where o.series_id = s.id) r on true;

create view api.observations with (security_invoker = true) as
select s.key as series_key, o.obs_date, o.value, l.label
from core.observation o
join core.series s on s.id = o.series_id
left join core.series_label l on l.series_id = o.series_id and l.valid @> o.obs_date;

create view api.forecasts with (security_invoker = true) as
select s.key as series_key, s.name as series_name, f.vintage_date, f.target_label, f.target_start, f.target_end,
       f.value, f.value_low, f.value_high,
       case f.method when 'ai_extracted' then 'AI-extracted' end as label,
       d.id as document_id, d.source_url, f.char_start, f.char_end
from core.forecast f
join core.series s on s.id = f.series_id
left join core.document d on d.id = f.document_id;

------------------------------------------------------------------ documents
create view api.documents with (security_invoker = true) as
select d.id, d.doc_type, t.label as doc_type_label, n.slug as entity_slug, ev.slug as event_slug,
       d.title, d.source_url, src.attribution, d.published_at, d.version_no, d.char_count
from core.document d
join core.doc_type t on t.key = d.doc_type
join core.entity n on n.id = d.entity_id
join core.source src on src.id = d.source_id
left join core.event ev on ev.id = d.event_id;

create view api.document_chunks with (security_invoker = true) as
select document_id, chunk_no, char_start, char_end, section, speaker, body from core.chunk;

create view api.document_diffs with (security_invoker = true) as
select document_id, base_document_id, segments, words_added, words_removed from core.document_diff;

------------------------------------------------------------------ AI outputs
create view api.ai_outputs with (security_invoker = true) as
select o.id, o.task, o.document_id, ev.slug as event_slug, n.slug as entity_slug, o.scope,
       o.body, o.score, o.label as output_label, o.content,
       fp.label as display_label,        -- 'AI summary', 'AI-extracted'
       o.model, o.created_at
from core.ai_output o
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
left join core.event ev on ev.id = o.event_id
left join core.entity n on n.id = o.entity_id;

create view api.ai_citations with (security_invoker = true) as
select c.ai_output_id, c.citation_no, c.claim_path, c.kind,
       c.document_id, d.title as document_title, d.source_url, c.char_start, c.char_end,
       (select substr(string_agg(ch.body, '' order by ch.chunk_no),
                      c.char_start - min(ch.char_start) + 1, c.char_end - c.char_start)
          from core.chunk ch
         where ch.document_id = c.document_id
           and ch.char_start < c.char_end and ch.char_end > c.char_start) as quote,
       s.key as series_key, c.obs_date, ev.slug as event_slug
from core.ai_citation c
join core.ai_output o on o.id = c.ai_output_id
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
left join core.document d on d.id = c.document_id
left join core.series s on s.id = c.series_id
left join core.event ev on ev.id = c.event_id
where c.verified;

------------------------------------------------------------------ module 1
create view api.policy_meetings with (security_invoker = true) as
select ev.slug, ev.title, b.slug as bank_slug, b.short_name as bank, ev.status,
       ev.starts_on, coalesce(ev.ends_on, ev.starts_on) as decision_date, ev.occurs_at as decision_at,
       m.kind, m.is_rate_decision, m.meeting_no, m.has_press_conference, m.minutes_due_on,
       pr.value_pct  as policy_rate_pct,
       pr.change_bps as policy_rate_change_bps,
       st.label as stance, st.body as stance_text, fst.label as stance_display_label,
       hd.score as hawk_dove_score, fhd.label as hawk_dove_display_label,
       (select count(*) from policy.vote v where v.meeting_event_id = m.event_id and v.is_dissent) as dissent_count,
       src.attribution
from policy.meeting m
join core.event ev on ev.id = m.event_id
join core.entity b on b.id = ev.entity_id
left join core.source src on src.id = ev.source_id
left join policy.rate_setting pr on pr.meeting_event_id = m.event_id
       and pr.instrument = case b.slug when 'fed' then 'fed_target_upper' else 'repo' end
left join core.field_policy fst on fst.field_key = 'ai.stance_extraction' and fst.status in ('shown','labelled')
left join core.ai_output st on fst.field_key is not null and st.event_id = m.event_id
       and st.task = 'stance_extraction' and st.status = 'published' and st.scope = ''
left join core.field_policy fhd on fhd.field_key = 'ai.hawk_dove_score' and fhd.status in ('shown','labelled')
left join core.ai_output hd on fhd.field_key is not null and hd.event_id = m.event_id
       and hd.task = 'hawk_dove_score' and hd.status = 'published' and hd.scope = '';

create view api.next_meetings with (security_invoker = true) as
select b.short_name as bank, ev.slug, ev.title, ev.starts_on, coalesce(ev.ends_on, ev.starts_on) as decision_date,
       ev.occurs_at as decision_at
from policy.meeting m
join core.event ev on ev.id = m.event_id
join core.entity b on b.id = ev.entity_id
where ev.status = 'scheduled' and coalesce(ev.ends_on, ev.starts_on) >= current_date;

create view api.policy_rate_settings with (security_invoker = true) as
select ev.slug as meeting_slug, b.short_name as bank, coalesce(ev.ends_on, ev.starts_on) as decision_date,
       r.instrument, r.value_pct, r.change_bps, r.crosscheck,
       r.document_id, d.source_url, r.char_start, r.char_end
from policy.rate_setting r
join core.event ev on ev.id = r.meeting_event_id
join core.entity b on b.id = ev.entity_id
join core.document d on d.id = r.document_id;

create view api.policy_votes with (security_invoker = true) as
select ev.slug as meeting_slug, p.slug as member_slug, p.name as member_name, t.role, t.affiliation, t.is_voter,
       v.voted_change_bps, v.stance_vote, v.is_dissent, v.dissent_text,
       v.document_id, d.source_url, v.char_start, v.char_end,
       hd.score as member_hawk_dove_score, fhd.label as member_hawk_dove_display_label
from policy.vote v
join core.event ev on ev.id = v.meeting_event_id
join policy.member p on p.id = v.member_id
join core.document d on d.id = v.document_id
left join policy.member_term t on t.member_id = p.id and t.valid @> coalesce(ev.ends_on, ev.starts_on)
left join core.field_policy fhd on fhd.field_key = 'ai.member_hawk_dove_score' and fhd.status in ('shown','labelled')
left join core.ai_output hd on fhd.field_key is not null and hd.event_id = v.meeting_event_id
       and hd.task = 'member_hawk_dove_score' and hd.status = 'published' and hd.scope = 'member:' || p.id;

create view api.market_reactions with (security_invoker = true) as
select ev.slug as meeting_slug, s.key as series_key, s.name as series_name,
       r.pre_obs_date, r.post_obs_date, r.pre_value, r.post_value, r.change, r.change_unit, r.is_surprise_measure,
       coalesce(s.attribution, src.attribution) as attribution
from policy.market_reaction r
join core.event ev on ev.id = r.meeting_event_id
join core.series s on s.id = r.series_id
join core.source src on src.id = s.source_id;

grant select on all tables in schema api to anon, authenticated, service_role;
