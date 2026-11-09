-- The definitions of api.earnings_calls, api.earnings_latest and api.guidance_hit_rates as they stood before 0032 (copied from
-- 0029 and 0028; earnings_latest reads the old earnings_calls), as temp views for run_tests_0032.sh's row-equality checks.
create temp view old_earnings_calls as
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

create temp view old_earnings_latest as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as fig_ok
  from core.field_policy where field_key = 'ai.guidance_extraction'
), w as (
  select min(filed_at)::date as window_from, max(filed_at)::date as window_to from old_earnings_calls
)
select co.slug as company_slug, co.name as company_name, co.short_name,
       n.call_slug, n.fiscal_label, n.period_end, n.season_key, n.call_date, n.call_date_source, n.filed_at, n.exchange,
       n.route, coalesce(n.status, 'none_found') as status, n.official_url, n.transcript_document_id,
       n.summary_output_id, n.summary_state, n.figures_output_id, n.target_count, n.stated_figure_count,
       n.unattributed_targets,
       dflt.call_slug as default_call_slug, dflt.fiscal_label as default_fiscal_label,
       dflt.roles_partial,
       case when g.fig_ok then
         (select coalesce(sum(a.unattributed_targets), 0) from old_earnings_calls a where a.company_slug = co.slug)
       end as unattributed_targets_all,
       case when n.call_slug is null then w.window_from end as window_from,
       case when n.call_slug is null then w.window_to end as window_to
from api.companies co
cross join g
cross join w
left join old_earnings_calls n on n.company_slug = co.slug and n.is_newest
left join old_earnings_calls dflt on dflt.company_slug = co.slug and dflt.is_default;

create temp view old_guidance_hit_rates as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.guidance_extraction'), false)
         and coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false) as ok,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer,
         max(label) filter (where field_key = 'equity.guidance_outcome' and status in ('shown','labelled')) as out_label
  from core.field_policy
  where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.peer_group')
), nifty as (
  select m.entity_id
  from equity.index_membership m
  join core.entity i on i.id = m.index_entity_id and i.slug = 'nifty-50'
  where m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id)
), t as (
  select x.entity_id, e.slug, e.name, c.peer_group_key, pg.label as peer_label,
         o.outcome, x.vintage_date, x.target_end,
         exists (select 1 from nifty n where n.entity_id = x.entity_id) as in_nifty
  from equity.guidance_outcome o
  join equity.guidance x on x.id = o.guidance_id and x.duplicate_of is null
  join core.ai_output ai on ai.id = x.ai_output_id and ai.task = 'guidance_extraction' and ai.status = 'published'
  join equity.earnings_call ec on ec.event_id = x.call_event_id
  join core.entity e on e.id = x.entity_id
  left join equity.company c on c.entity_id = x.entity_id
  left join equity.peer_group pg on pg.key = c.peer_group_key
  cross join g
  where g.ok and o.counted and o.is_first and o.outcome in ('delivered','missed')
    and x.speaker_role = 'management'
)
select 'company'::text as scope, t.slug as scope_key, t.name as scope_label,
       count(*) filter (where t.outcome = 'delivered') as delivered,
       count(*) filter (where t.outcome = 'missed') as missed,
       count(*) as n,
       case when count(*) >= 5 then (count(*) filter (where t.outcome = 'delivered'))::numeric / count(*) end as rate,
       min(t.vintage_date) as first_stated_from, max(t.target_end) as periods_to,
       (select out_label from g) as outcome_label
from t group by t.slug, t.name
union all
select 'peer_group', t.peer_group_key, t.peer_label,
       count(*) filter (where t.outcome = 'delivered'), count(*) filter (where t.outcome = 'missed'), count(*),
       case when count(*) >= 5 then (count(*) filter (where t.outcome = 'delivered'))::numeric / count(*) end,
       min(t.vintage_date), max(t.target_end), (select out_label from g)
from t where t.peer_group_key is not null and (select peer from g)
group by t.peer_group_key, t.peer_label
union all
select 'nifty50', 'nifty-50', 'Nifty 50',
       count(t.outcome) filter (where t.outcome = 'delivered'), count(t.outcome) filter (where t.outcome = 'missed'), count(t.outcome),
       case when count(t.outcome) >= 5 then (count(t.outcome) filter (where t.outcome = 'delivered'))::numeric / count(t.outcome) end,
       min(t.vintage_date), max(t.target_end), (select out_label from g)
from g left join t on t.in_nifty
where g.ok
group by g.ok
having count(t.outcome) > 0;   -- nothing visible (a suppressed source, no counted target yet) leaves no row at all
