-- 0007: semantic layer for text-to-SQL (module 1's views)
-- Security barrier: the caller's WHERE clause can't run before the view's own conditions, so a cast or
-- division in a generated query can't probe values the view hides (found in review).
-- These views run with their OWNER's rights (semantic_owner: not a table owner, no BYPASSRLS), so the
-- base tables' RLS policies for semantic_owner apply, and sql_reader needs no access to any base table.
-- The COMMENTs are the documentation the text-to-SQL prompt is generated from (piece 3).

create view semantic.policy_meetings with (security_barrier = true) as
select b.short_name as bank,
       coalesce(ev.ends_on, ev.starts_on) as meeting_date,
       ev.occurs_at as decision_released_at,
       ev.status, m.kind as meeting_kind, m.is_rate_decision, m.meeting_no as rbi_mpc_meeting_no,
       max(r.value_pct)  filter (where r.instrument = case b.slug when 'fed' then 'fed_target_upper' else 'repo' end) as policy_rate_pct,
       max(r.change_bps) filter (where r.instrument = case b.slug when 'fed' then 'fed_target_upper' else 'repo' end) as policy_rate_change_bps,
       max(r.value_pct) filter (where r.instrument = 'fed_target_lower') as fed_target_lower_pct,
       max(r.value_pct) filter (where r.instrument = 'fed_target_upper') as fed_target_upper_pct,
       max(r.value_pct) filter (where r.instrument = 'repo')         as rbi_repo_pct,
       max(r.value_pct) filter (where r.instrument = 'sdf')          as rbi_sdf_pct,
       max(r.value_pct) filter (where r.instrument = 'reverse_repo') as rbi_reverse_repo_pct,
       max(r.value_pct) filter (where r.instrument = 'msf')          as rbi_msf_pct,
       max(r.value_pct) filter (where r.instrument = 'crr')          as rbi_crr_pct,
       st.label  as stance,
       hd.score  as hawk_dove_score,
       (select count(*) from policy.vote v where v.meeting_event_id = m.event_id and v.is_dissent) as dissent_count,
       (select mr.change from policy.market_reaction mr
         where mr.meeting_event_id = m.event_id and mr.is_surprise_measure) as us_2y_change_bps,
       ev.title
from policy.meeting m
join core.event ev on ev.id = m.event_id
join core.entity b on b.id = ev.entity_id
left join policy.rate_setting r on r.meeting_event_id = m.event_id
left join core.field_policy fst on fst.field_key = 'ai.stance_extraction' and fst.status in ('shown','labelled')
left join core.ai_output st on fst.field_key is not null and st.event_id = m.event_id
       and st.task = 'stance_extraction' and st.status = 'published' and st.scope = ''
left join core.field_policy fhd on fhd.field_key = 'ai.hawk_dove_score' and fhd.status in ('shown','labelled')
left join core.ai_output hd on fhd.field_key is not null and hd.event_id = m.event_id
       and hd.task = 'hawk_dove_score' and hd.status = 'published' and hd.scope = ''
group by b.short_name, b.slug, ev.ends_on, ev.starts_on, ev.occurs_at, ev.status, m.kind,
         m.is_rate_decision, m.meeting_no, m.event_id, st.label, hd.score, ev.title;

comment on view semantic.policy_meetings is
  'One row per Fed (FOMC) or RBI (MPC) meeting since 1 Jan 2019, including scheduled future meetings (status = ''scheduled'', no decision yet). Rates are in percent; changes in basis points.';
comment on column semantic.policy_meetings.bank is 'Fed or RBI';
comment on column semantic.policy_meetings.meeting_date is 'Decision date (the second day of a two-day FOMC meeting; the last day of an MPC meeting)';
comment on column semantic.policy_meetings.policy_rate_pct is 'Fed: upper bound of the federal funds target range. RBI: repo rate. Percent.';
comment on column semantic.policy_meetings.policy_rate_change_bps is 'Change in policy_rate_pct at this meeting, in basis points (negative = cut)';
comment on column semantic.policy_meetings.rbi_sdf_pct is 'Standing deposit facility rate; exists from April 2022 (null before). Before that the corridor floor was the fixed reverse repo rate.';
comment on column semantic.policy_meetings.stance is 'AI-extracted stance label, cited to the statement; null if not published';
comment on column semantic.policy_meetings.hawk_dove_score is 'AI hawk/dove score; null while held back pending evaluation';
comment on column semantic.policy_meetings.us_2y_change_bps is 'Fed meetings only: 1-day change in the US 2-year Treasury yield around the decision (the surprise measure), basis points';

create view semantic.policy_votes with (security_barrier = true) as
select b.short_name as bank, coalesce(ev.ends_on, ev.starts_on) as meeting_date,
       p.name as member_name, t.role as member_role, t.affiliation,
       v.voted_change_bps, v.stance_vote, v.is_dissent, v.dissent_text
from policy.vote v
join core.event ev on ev.id = v.meeting_event_id
join core.entity b on b.id = ev.entity_id
join policy.member p on p.id = v.member_id
left join policy.member_term t on t.member_id = p.id and t.valid @> coalesce(ev.ends_on, ev.starts_on);

comment on view semantic.policy_votes is
  'One row per voting member per meeting. FOMC votes come from the statement''s voting line; RBI votes (rate and stance) from the MPC minutes.';
comment on column semantic.policy_votes.voted_change_bps is 'Policy-rate change this member voted for, basis points (a dissenter''s preferred change)';

create view semantic.policy_projections with (security_barrier = true) as
select f.vintage_date as meeting_date,
       case s.key when 'rbi.proj_cpi' then 'CPI inflation' when 'rbi.proj_gdp' then 'Real GDP growth' end as variable,
       f.target_label as target_period, f.target_start as period_start, f.target_end as period_end,
       f.value as value_pct, f.value_low as low_pct, f.value_high as high_pct,
       case f.method when 'ai_extracted' then 'AI-extracted' else 'parsed' end as extraction
from core.forecast f
join core.series s on s.id = f.series_id
where s.key in ('rbi.proj_cpi', 'rbi.proj_gdp');

comment on view semantic.policy_projections is
  'RBI''s CPI inflation and real GDP growth projections, as published in each MPC resolution. meeting_date is when the projection was made; period_start/period_end is the period it is for. Some projections are ranges (low_pct/high_pct).';

create view semantic.market_daily with (security_barrier = true) as
select s.name as series_name, s.key as series_key, o.obs_date as date, o.value, s.unit,
       s.market, s.currency, coalesce(s.attribution, src.attribution) as source
from core.observation o
join core.series s on s.id = o.series_id
join core.source src on src.id = s.source_id
where s.kind = 'observed';

comment on view semantic.market_daily is
  'Market and macro series used by the policy tracker: S&P 500, Nifty 50, Nifty Bank, Sensex, US 2-year and 10-year Treasury yields, India 10-year yield (monthly), USD/INR, dollar index, Fed target range, effective fed funds rate. Never compare levels across currencies.';

alter view semantic.policy_meetings    owner to semantic_owner;
alter view semantic.policy_votes       owner to semantic_owner;
alter view semantic.policy_projections owner to semantic_owner;
alter view semantic.market_daily       owner to semantic_owner;

revoke all on all tables in schema semantic from public;
grant select on all tables in schema semantic to sql_reader;
