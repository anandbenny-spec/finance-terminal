-- 0022: rate sensitivity (module 2, release 2A; issue #129, Spec 02A §3.4, §3.5, §5.10, §7).
-- One core addition, OK'd by Anand on 28 Oct 2026, 00:18 IST (CLAUDE.md §7 is satisfied for exactly this design):
-- `core.event_reaction`, the general reactions engine's output (one row per event × subject × window). With it the
-- event type `rate_cycle` and its detail table `policy.rate_cycle`, and the rate-sensitivity views. Nothing else in
-- core changes; `policy.market_reaction` stays as it is (module 1's pages read it).
-- Every view that shows a figure derived here is gated by the field policy `equity.rate_sensitivity` (hidden at
-- first; Anand flips it after 2A-24's hand checks) with a CASE inside the view, never by a join alone (Arch 01 §1.4).
-- Visitor policies use inline subqueries only: 0001 revokes EXECUTE on functions, so no policy calls one.

------------------------------------------------------------------ core.event_reaction
create table core.event_reaction (
  event_id    bigint  not null references core.event(id),
  series_id   integer references core.series(id),
  entity_id   integer references core.entity(id),
  window_key  text    not null check (window_key ~ '^[a-z0-9_]+$'),        -- d1, d5, span; later m1, m3, m12
  source_id   integer not null references core.source(id),                  -- whose prices: nse, or the series' source
  pre_date    date    not null,
  post_date   date    not null,
  pre_value   numeric not null,
  post_value  numeric not null,
  change      numeric not null,
  change_unit text    not null check (change_unit in ('bps','pct')),
  value_basis text    not null check (value_basis in ('observed','adjusted_close')),
  task_run_id bigint  not null references ops.task_run(id),
  check ((series_id is null) <> (entity_id is null)),                       -- exactly one subject
  check (post_date > pre_date),
  check ((value_basis = 'observed') = (series_id is not null)),             -- a series is observed, a company adjusted
  -- a series row traces to its two observations (MATCH SIMPLE: unchecked for company rows, whose inputs are derived
  -- from equity.price_daily and the factors and reproducible from them)
  foreign key (series_id, pre_date)  references core.observation (series_id, obs_date),
  foreign key (series_id, post_date) references core.observation (series_id, obs_date)
);
create unique index event_reaction_subject_window
  on core.event_reaction (event_id, coalesce(series_id, 0), coalesce(entity_id, 0), window_key);
create index on core.event_reaction (entity_id, event_id);
create index on core.event_reaction (series_id, event_id);

alter table core.event_reaction enable row level security;
create policy job_writer_all on core.event_reaction for all to job_writer using (true) with check (true);
create policy read_published on core.event_reaction for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event e where e.id = event_id)
     and not exists (select 1 from core.source s where s.id = source_id and s.is_suppressed)
     and (   (series_id is not null and exists (select 1 from core.series s where s.id = series_id))
          or (entity_id is not null and exists (select 1 from core.entity n where n.id = entity_id))));
grant select on core.event_reaction to anon, authenticated, semantic_owner, service_role;
-- Derived rows: a job may insert, update and delete them (never the observations they come from).
grant select, insert, update, delete on core.event_reaction to job_writer;

------------------------------------------------------------------ the rate_cycle event type and its detail table
insert into core.event_type (key, label, module) values ('rate_cycle', 'Rate cycle', 'policy');
insert into core.event_status (event_type, status, is_final) values
  ('rate_cycle', 'ongoing',  false),
  ('rate_cycle', 'complete', true);

create table policy.rate_cycle (
  event_id               bigint primary key references core.event(id),
  direction              text   not null check (direction in ('easing','tightening')),
  first_meeting_event_id bigint not null references policy.meeting(event_id),
  last_meeting_event_id  bigint not null references policy.meeting(event_id),
  moves                  smallint not null check (moves >= 2),               -- a lone move is not a cycle (Spec 02A §5.10)
  total_change_bps       integer  not null,
  rule_version           smallint not null,
  check ((direction = 'easing' and total_change_bps < 0) or (direction = 'tightening' and total_change_bps > 0))
);
alter table policy.rate_cycle enable row level security;
create policy job_writer_all on policy.rate_cycle for all to job_writer using (true) with check (true);
create policy read_published on policy.rate_cycle for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event e where e.id = event_id));
grant select on policy.rate_cycle to anon, authenticated, semantic_owner, service_role;
grant select, insert, update, delete on policy.rate_cycle to job_writer;       -- derived: re-derived on every run

comment on table core.event_reaction is 'One row per event, subject (a series or a company) and window: the market move around the event (pre and post values, change in bps or percent). Written by the reactions engine; series rows trace to their two observations.';
comment on table policy.rate_cycle is 'One row per rate cycle (a core.event of type rate_cycle): a run of two or more policy-rate moves in one direction, with the meetings it spans and its net change in basis points.';

------------------------------------------------------------------ the views (Spec 02A §7)
-- The decision-day moves, once, as text: the api view (the browser's; runs as the caller) and the semantic view (the
-- command bar's; owned by semantic_owner, security barrier) are the same query.
do $mig$
declare
  q text := $q$
with g as (
  select exists (select 1 from core.field_policy f
                  where f.field_key = 'equity.rate_sensitivity' and f.status in ('shown','labelled')) as ok
), dec as (
  -- every held rate decision since 1 Jan 2019, classed by the change in its bank's policy rate (the repo rate; the
  -- Fed's upper bound) from the previous held decision
  select m.event_id, ev.slug as event_slug, b.slug as bank, coalesce(ev.ends_on, ev.starts_on) as meeting_date,
         rs.change_bps as policy_change_bps,
         case when rs.change_bps < 0 then 'cut' when rs.change_bps > 0 then 'hike' else 'hold' end as decision
    from policy.meeting m
    join core.event ev on ev.id = m.event_id and ev.status = 'held'
    join core.entity b on b.id = ev.entity_id
    join policy.rate_setting rs on rs.meeting_event_id = m.event_id
         and rs.instrument = case b.slug when 'fed' then 'fed_target_upper' else 'repo' end
   where m.is_rate_decision and coalesce(ev.ends_on, ev.starts_on) >= date '2019-01-01'
), nifty as (
  select r.event_id, r.window_key, r.pre_date, r.post_date, r.change
    from core.event_reaction r join core.series s on s.id = r.series_id
   where s.key = 'yf.^NSEI' and r.change_unit = 'pct'
), co as (
  -- a company's move on a decision; the Nifty 50's move only where it has the same pre and post dates
  select d.*, e.slug as company_slug, e.name as company_name, e.currency, pg.key as peer_key, pg.label as peer_label,
         r.window_key, r.pre_date, r.post_date, r.change as own_change,
         n.change as nifty_change,
         case when n.change is not null then r.change - n.change end as excess
    from dec d
    join core.event_reaction r on r.event_id = d.event_id and r.entity_id is not null and r.change_unit = 'pct'
    join core.entity e on e.id = r.entity_id
    left join equity.company c on c.entity_id = e.id
    left join equity.peer_group pg on pg.key = c.peer_group_key
    left join nifty n on n.event_id = r.event_id and n.window_key = r.window_key
         and n.pre_date = r.pre_date and n.post_date = r.post_date
), u as (
  select 'company'::text as subject_kind, company_slug as subject_key, company_name as subject_name, currency,
         bank, event_slug, meeting_date, decision, policy_change_bps, window_key,
         pre_date, post_date, own_change, nifty_change, excess, null::bigint as members
    from co
  union all
  -- a peer group's move on an event = the equal-weighted mean of its members' excess moves on that event
  select 'peer_group', peer_key, peer_label, null::text, bank, event_slug, meeting_date, decision, policy_change_bps,
         window_key, null::date, null::date, null::numeric, null::numeric, avg(excess), count(excess)
    from co where peer_key is not null and excess is not null
   group by peer_key, peer_label, bank, event_slug, meeting_date, decision, policy_change_bps, window_key
)
select u.subject_kind, u.subject_key, u.subject_name, u.currency, u.bank, u.event_slug, u.meeting_date, u.decision,
       u.policy_change_bps, u.window_key,
       case when g.ok then u.pre_date end        as pre_date,
       case when g.ok then u.post_date end       as post_date,
       case when g.ok then u.own_change end      as own_change_pct,
       case when g.ok then u.nifty_change end    as nifty_change_pct,
       case when g.ok then u.excess end          as excess_pct,
       case when g.ok then u.members end         as members,
       (select attribution from core.source where key = 'nse') as attribution
  from u cross join g
$q$;
begin
  execute 'create view api.rate_decision_moves with (security_invoker = true) as ' || q;
  execute 'create view semantic.rate_decision_moves with (security_barrier = true) as ' || q;
end $mig$;

-- Statistics of the excess moves per company or peer group × bank × decision × window. n < 5 gives no statistics
-- (the individual moves are listed instead: api.rate_decision_moves). No significance test (Spec 02A §10, decision 9).
create view api.rate_decision_stats with (security_invoker = true) as
with g as (
  select exists (select 1 from core.field_policy f
                  where f.field_key = 'equity.rate_sensitivity' and f.status in ('shown','labelled')) as ok
)
select m.subject_kind, m.subject_key, m.subject_name, m.bank, m.decision, m.window_key,
       case when g.ok then count(m.excess_pct) end as n,
       case when g.ok and count(m.excess_pct) >= 5 then avg(m.excess_pct) end as mean_excess_pct,
       case when g.ok and count(m.excess_pct) >= 5
            then percentile_cont(0.5) within group (order by m.excess_pct) end as median_excess_pct,
       case when g.ok and count(m.excess_pct) >= 5
            then percentile_cont(0.25) within group (order by m.excess_pct) end as p25_excess_pct,
       case when g.ok and count(m.excess_pct) >= 5
            then percentile_cont(0.75) within group (order by m.excess_pct) end as p75_excess_pct,
       case when g.ok and count(m.excess_pct) >= 5
            then count(m.excess_pct) filter (where m.excess_pct > 0)::numeric / count(m.excess_pct) end as share_positive,
       min(m.attribution) as attribution
  from api.rate_decision_moves m cross join g
 group by m.subject_kind, m.subject_key, m.subject_name, m.bank, m.decision, m.window_key, g.ok;

-- The cycles: one row each, every derived column gated.
create view api.rate_cycles with (security_invoker = true) as
with g as (
  select exists (select 1 from core.field_policy f
                  where f.field_key = 'equity.rate_sensitivity' and f.status in ('shown','labelled')) as ok
)
select b.slug as bank, b.name as bank_name, ev.slug as cycle_slug,
       case when g.ok then rc.direction end                           as direction,
       case when g.ok then ev.starts_on end                           as first_decision_on,
       case when g.ok then ev.ends_on end                             as last_decision_on,
       case when g.ok then ev.status end                              as status,
       case when g.ok then rc.moves end                               as moves,
       case when g.ok then rc.total_change_bps end                    as total_change_bps,
       case when g.ok then rc.rule_version end                        as rule_version
  from policy.rate_cycle rc
  join core.event ev on ev.id = rc.event_id
  join core.entity b on b.id = ev.entity_id
  cross join g;

-- A company's move over each cycle's `span` (the last close before the first move to the first close after the last
-- move; an ongoing cycle runs to the latest close), the Nifty 50's over the same dates, and the excess.
create view api.company_rate_cycles with (security_invoker = true) as
with g as (
  select exists (select 1 from core.field_policy f
                  where f.field_key = 'equity.rate_sensitivity' and f.status in ('shown','labelled')) as ok
), nifty as (
  select r.event_id, r.window_key, r.pre_date, r.post_date, r.change
    from core.event_reaction r join core.series s on s.id = r.series_id
   where s.key = 'yf.^NSEI' and r.change_unit = 'pct'
)
select e.slug as company_slug, e.name as company_name, e.currency, b.slug as bank, ev.slug as cycle_slug,
       case when g.ok then rc.direction end                           as direction,
       case when g.ok then ev.status end                              as status,
       case when g.ok then (ev.status = 'ongoing') end                as is_ongoing,
       case when g.ok then r.pre_date end                             as pre_date,
       case when g.ok then r.post_date end                            as post_date,
       case when g.ok then r.change end                               as own_change_pct,
       case when g.ok then n.change end                               as nifty_change_pct,
       case when g.ok and n.change is not null then r.change - n.change end as excess_pct,
       (select attribution from core.source where key = 'nse')        as attribution
  from core.event_reaction r
  join core.event ev on ev.id = r.event_id and ev.event_type = 'rate_cycle'
  join policy.rate_cycle rc on rc.event_id = ev.id
  join core.entity b on b.id = ev.entity_id
  join core.entity e on e.id = r.entity_id
  left join nifty n on n.event_id = r.event_id and n.window_key = r.window_key
       and n.pre_date = r.pre_date and n.post_date = r.post_date
  cross join g
 where r.window_key = 'span' and r.change_unit = 'pct';

grant select on api.rate_decision_moves, api.rate_decision_stats, api.rate_cycles, api.company_rate_cycles
  to anon, authenticated, service_role;

------------------------------------------------------------------ semantic view: comments and ownership
alter view semantic.rate_decision_moves owner to semantic_owner;
revoke all on semantic.rate_decision_moves from public;
grant select on semantic.rate_decision_moves to sql_reader;

comment on view semantic.rate_decision_moves is
  'One row per Nifty 50 company (or peer group) and Fed (FOMC) or RBI (MPC) rate decision since 1 Jan 2019 and window: how the stock moved around the decision day, and how much more or less than the Nifty 50 (the excess). Price moves excluding dividends, in percent, in the company''s own currency (INR); empty while held back. Moves on decision days, not a forecast; not investment advice.';
comment on column semantic.rate_decision_moves.subject_kind is 'company or peer_group (a peer group''s move is the equal-weighted mean of its members'' excess moves on that decision)';
comment on column semantic.rate_decision_moves.subject_key is 'The company''s slug (the NSE symbol, lower-cased) or the peer group''s key';
comment on column semantic.rate_decision_moves.subject_name is 'The company''s or peer group''s display name';
comment on column semantic.rate_decision_moves.currency is 'Reporting currency of the company (INR); null for a peer group';
comment on column semantic.rate_decision_moves.bank is 'fed or rbi: whose decision';
comment on column semantic.rate_decision_moves.event_slug is 'The meeting''s event slug';
comment on column semantic.rate_decision_moves.meeting_date is 'Decision date (second day of a two-day FOMC meeting; last day of an MPC meeting)';
comment on column semantic.rate_decision_moves.decision is 'cut, hold or hike: the change in the bank''s policy rate (repo rate; the Fed''s target range upper bound) at this decision';
comment on column semantic.rate_decision_moves.policy_change_bps is 'The policy-rate change at this decision in basis points (negative = cut)';
comment on column semantic.rate_decision_moves.window_key is 'd1 = the last close before the decision to the first close after it; d5 = to the fifth close after';
comment on column semantic.rate_decision_moves.pre_date is 'The close the window starts from (company rows only)';
comment on column semantic.rate_decision_moves.post_date is 'The close the window ends at (company rows only)';
comment on column semantic.rate_decision_moves.own_change_pct is 'The company''s price move over the window, percent, excluding dividends (company rows only)';
comment on column semantic.rate_decision_moves.nifty_change_pct is 'The Nifty 50''s move over the same window, percent; null where its dates differ from the company''s';
comment on column semantic.rate_decision_moves.excess_pct is 'The company''s move minus the Nifty 50''s over the same window, in percentage points (a peer group: the mean over its members); not beta-adjusted';
comment on column semantic.rate_decision_moves.members is 'Peer group rows: how many members contributed an excess move to the mean';
comment on column semantic.rate_decision_moves.attribution is 'Source line to show with the figures';

comment on view api.rate_decision_moves is 'Decision-day moves of each company and peer group against the Nifty 50, per decision and window; gated by the field policy equity.rate_sensitivity.';
comment on view api.rate_decision_stats is 'Per company or peer group, bank, decision class and window: n, mean, median, quartiles and share positive of the excess moves; no statistics with fewer than 5 moves; gated.';
comment on view api.rate_cycles is 'Each bank''s rate cycles (two or more moves in one direction); gated.';
comment on view api.company_rate_cycles is 'A company''s move, the Nifty 50''s and the excess over each rate cycle''s span; gated.';

revoke all on all tables in schema semantic from public;
grant select on all tables in schema semantic to sql_reader;
