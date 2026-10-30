#!/bin/bash
# Tests for 0022 (rate sensitivity; issue #129, Spec 02A §3.4, §5.10, §7). Run after the other scripts on the same
# fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
# The script creates its own rows (entities, events and documents named r22-*, observations of its own task run),
# checks through the roles, and removes every row it made.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from core.event_reaction where event_id in (select id from core.event where slug like 'r22-%');
         delete from policy.rate_cycle where event_id in (select id from core.event where slug like 'r22-%');
         delete from policy.rate_setting where meeting_event_id in (select id from core.event where slug like 'r22-%');
         delete from policy.meeting where event_id in (select id from core.event where slug like 'r22-%');
         delete from core.event where slug like 'r22-%';
         delete from core.document where external_key like 'r22-%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'r22-%');
         delete from equity.peer_group where key like 'r22-%';
         delete from core.entity where slug like 'r22-%';
         delete from core.observation where task_run_id in (select id from ops.task_run where task_key = 'r22.task');
         delete from ops.task_run where task_key = 'r22.task';
         delete from ops.task where key = 'r22.task';
         update core.source set is_suppressed = false where key = 'nse';
         update core.field_policy set status = 'hidden', label = 'derived' where field_key = 'equity.rate_sensitivity';" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind
NIFTY_WAS=$(as_pg "select is_published from core.series where key = 'yf.^NSEI'")

############################################################ the rows the checks use
psql $H -U postgres -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || echo "FAIL  setup of the test rows"
insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
  values ('r22.task', 'test', '0022 test task', '{daily}', '20 hours', '4 days', false);
insert into ops.job_run (workflow, trigger, runner) values ('daily', 'cli', 'local');
insert into ops.task_run (job_run_id, task_key, status) values ((select max(id) from ops.job_run), 'r22.task', 'success');
update core.series set is_published = true where key = 'yf.^NSEI';
-- one observation a day through 2021 for the Nifty (the series rows' foreign keys), only where none is stored
insert into core.observation (series_id, obs_date, value, task_run_id)
  select (select id from core.series where key = 'yf.^NSEI'), d::date, 15000, (select id from ops.task_run where task_key = 'r22.task')
    from generate_series('2021-01-01'::date, '2021-12-31'::date, '1 day') d
  on conflict do nothing;

insert into equity.peer_group (key, label, source_note) values ('r22-pg', 'R22 group', 'test');
insert into core.entity (kind, slug, name, market, currency, is_published) values
  ('company','r22-a','R22 A Ltd','IN','INR',true), ('company','r22-b','R22 B Ltd','IN','INR',true),
  ('company','r22-c','R22 C Ltd','IN','INR',true), ('company','r22-d','R22 D Ltd','IN','INR',true),
  ('company','r22-unpub','R22 Unpublished Ltd','IN','INR',false);
insert into equity.company (entity_id, peer_group_key)
  select id, case when slug in ('r22-a','r22-b','r22-c') then 'r22-pg' end from core.entity where slug like 'r22-%';

insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
  select (select min(key) from core.doc_type where module = 'policy'), (select id from core.entity where slug = 'fed'),
         'r22 doc', (select id from core.source where key = 'fed'), 'r22-doc', 'https://example.test/r22';

-- six Fed cuts (2021-01-15 ... 2021-06-15), a Fed hold and hike, an RBI hold; a non-rate-decision meeting and a
-- scheduled (not held) one that must never appear
create temp table m (slug text, bank text, d date, status text, decision boolean, chg int);
insert into m values
  ('r22-f1','fed','2021-01-15','held',true,-25), ('r22-f2','fed','2021-02-15','held',true,-25),
  ('r22-f3','fed','2021-03-15','held',true,-25), ('r22-f4','fed','2021-04-15','held',true,-25),
  ('r22-f5','fed','2021-05-15','held',true,-25), ('r22-f6','fed','2021-06-15','held',true,-25),
  ('r22-fh','fed','2021-07-15','held',true,0),   ('r22-fk','fed','2021-08-15','held',true,25),
  ('r22-rh','rbi','2021-09-15','held',true,0),
  ('r22-rs','rbi','2021-10-15','held',false,0),  ('r22-rf','rbi','2021-11-15','scheduled',true,0);
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, is_published)
  select 'policy_meeting', (select id from core.entity where slug = m.bank), slug, slug, d, d + time '12:00', status, true from m;
insert into policy.meeting (event_id, kind, is_rate_decision)
  select e.id, 'scheduled', m.decision from m join core.event e on e.slug = m.slug;
insert into policy.rate_setting (meeting_event_id, instrument, value_pct, change_bps, document_id, char_start, char_end)
  select e.id, case m.bank when 'fed' then 'fed_target_upper' else 'repo' end, 1, m.chg,
         (select id from core.document where external_key = 'r22-doc'), 0, 10
    from m join core.event e on e.slug = m.slug;

-- reactions of the cuts: A moves i, B 2i, C 3i, the Nifty 1, all d1 over the day before to the day after; on the sixth
-- cut C's window ends a day later than the Nifty's (no excess for C there)
insert into core.event_reaction (event_id, series_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select e.id, (select id from core.series where key = 'yf.^NSEI'), 'd1', (select source_id from core.series where key = 'yf.^NSEI'),
         m.d - 1, m.d + 1, 15000, 15000, 1, 'pct', 'observed', (select id from ops.task_run where task_key = 'r22.task')
    from m join core.event e on e.slug = m.slug where m.slug ~ '^r22-(f[1-6]|rh)$';
insert into core.event_reaction (event_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select e.id, n.id, 'd1', (select id from core.source where key = 'nse'), m.d - 1,
         m.d + case when m.slug = 'r22-f6' and n.slug = 'r22-c' then 2 else 1 end, 100, 100,
         right(m.slug, 1)::int * case n.slug when 'r22-a' then 1 when 'r22-b' then 2 else 3 end, 'pct', 'adjusted_close',
         (select id from ops.task_run where task_key = 'r22.task')
    from m join core.event e on e.slug = m.slug join core.entity n on n.slug in ('r22-a','r22-b','r22-c')
   where m.slug ~ '^r22-f[1-6]$';
-- the unpublished company, the unrelated D, and the RBI hold: A alone (n = 1)
insert into core.event_reaction (event_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select e.id, n.id, 'd1', (select id from core.source where key = 'nse'), '2021-01-14', '2021-01-16', 100, 100, 7, 'pct', 'adjusted_close',
         (select id from ops.task_run where task_key = 'r22.task')
    from core.event e join core.entity n on n.slug in ('r22-unpub','r22-d') where e.slug = 'r22-f1';
insert into core.event_reaction (event_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select e.id, n.id, 'd1', (select id from core.source where key = 'nse'), '2021-09-14', '2021-09-16', 100, 100, 2, 'pct', 'adjusted_close',
         (select id from ops.task_run where task_key = 'r22.task')
    from core.event e, core.entity n where e.slug = 'r22-rh' and n.slug = 'r22-a';

-- two cycles of the Fed: an easing one (complete) and a tightening one (ongoing); A's, the Nifty's span moves
insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, status, is_published)
  select 'rate_cycle', id, 'r22-cyc-easing', 'r22 easing', date '2021-01-15', date '2021-03-15', 'complete', true from core.entity where slug = 'fed'
  union all
  select 'rate_cycle', id, 'r22-cyc-tight', 'r22 tightening', date '2021-07-15', date '2021-08-15', 'ongoing', true from core.entity where slug = 'fed';
insert into policy.rate_cycle (event_id, direction, first_meeting_event_id, last_meeting_event_id, moves, total_change_bps, rule_version)
  select c.id, 'easing', (select id from core.event where slug = 'r22-f1'), (select id from core.event where slug = 'r22-f3'), 3, -75, 1
    from core.event c where c.slug = 'r22-cyc-easing'
  union all
  select c.id, 'tightening', (select id from core.event where slug = 'r22-fh'), (select id from core.event where slug = 'r22-fk'), 2, 25, 1
    from core.event c where c.slug = 'r22-cyc-tight';
insert into core.event_reaction (event_id, series_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select c.id, (select id from core.series where key = 'yf.^NSEI'), 'span', (select source_id from core.series where key = 'yf.^NSEI'),
         '2021-01-14', '2021-03-16', 15000, 15000, 4, 'pct', 'observed', (select id from ops.task_run where task_key = 'r22.task')
    from core.event c where c.slug = 'r22-cyc-easing';
insert into core.event_reaction (event_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select c.id, n.id, 'span', (select id from core.source where key = 'nse'), '2021-01-14', '2021-03-16', 100, 110, 10, 'pct', 'adjusted_close',
         (select id from ops.task_run where task_key = 'r22.task')
    from core.event c, core.entity n where c.slug = 'r22-cyc-easing' and n.slug = 'r22-a';
SQL

############################################################ core.event_reaction: shape and checks
check "R01 RLS is on for the new tables" \
  "$(as_pg "select string_agg(relname||':'||relrowsecurity, ',' order by relname) from pg_class where oid in ('core.event_reaction'::regclass, 'policy.rate_cycle'::regclass)")" \
  '^event_reaction:true,rate_cycle:true$'
check "R02 a row with both a series and a company is refused" \
  "$(as_pg "insert into core.event_reaction select event_id, series_id, 1, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, 'observed', task_run_id from core.event_reaction where series_id is not null limit 1")" \
  'violates check constraint'
check "R03 a row with neither is refused" \
  "$(as_pg "insert into core.event_reaction select event_id, null, null, 'x1', source_id, pre_date, post_date, pre_value, post_value, change, change_unit, 'observed', task_run_id from core.event_reaction limit 1")" \
  'violates check constraint'
check "R04 a post date on or before the pre date is refused" \
  "$(as_pg "insert into core.event_reaction select event_id, null, entity_id, 'x2', source_id, post_date, pre_date, pre_value, post_value, change, change_unit, value_basis, task_run_id from core.event_reaction where entity_id is not null limit 1")" \
  'violates check constraint'
check "R05 a window key must be lower-case letters, digits and underscores" \
  "$(as_pg "insert into core.event_reaction select event_id, null, entity_id, 'D1', source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id from core.event_reaction where entity_id is not null limit 1")" \
  'violates check constraint'
check "R06 one row per event, subject and window: a duplicate is refused" \
  "$(as_pg "insert into core.event_reaction select * from core.event_reaction where entity_id is not null limit 1")" \
  'duplicate key value violates unique constraint "event_reaction_subject_window"'
check "R07 a series row must trace to its two observations: a date with none is refused" \
  "$(as_pg "insert into core.event_reaction select event_id, series_id, null, 'x3', source_id, '2030-01-01', '2030-01-02', pre_value, post_value, change, change_unit, value_basis, task_run_id from core.event_reaction where series_id is not null limit 1")" \
  'violates foreign key constraint'
check "R08 a company row carries no observation foreign key: its dates are free" \
  "$(as_pg "insert into core.event_reaction select event_id, null, entity_id, 'x4', source_id, '2030-01-01', '2030-01-02', pre_value, post_value, change, change_unit, value_basis, task_run_id from core.event_reaction where entity_id is not null limit 1; select count(*) from core.event_reaction where window_key = 'x4'")" \
  '^1$'
as_pg "delete from core.event_reaction where window_key = 'x4'" >/dev/null
check "R09 a series row must be observed and a company row adjusted" \
  "$(as_pg "insert into core.event_reaction select event_id, null, entity_id, 'x5', source_id, pre_date, post_date, pre_value, post_value, change, change_unit, 'observed', task_run_id from core.event_reaction where entity_id is not null limit 1")" \
  'violates check constraint'
check "R10 job_writer may insert, update and delete reactions (derived rows)" \
  "$(as_role job_writer "begin; update core.event_reaction set change = change where window_key = 'd1'; delete from core.event_reaction where window_key = 'span' and entity_id is not null; rollback")" \
  '^$'
check "R11 job_writer may not delete observations (source data stays)" \
  "$(as_role job_writer "delete from core.observation where task_run_id = (select id from ops.task_run where task_key = 'r22.task')")" \
  'permission denied'
check "R12 anon cannot write reactions" \
  "$(as_role anon "delete from core.event_reaction")" 'permission denied'

############################################################ policy.rate_cycle and the event type
check "R13 rate_cycle is a policy event type with statuses ongoing (open) and complete (final)" \
  "$(as_pg "select t.module||':'||string_agg(s.status||'='||s.is_final, ',' order by s.status) from core.event_type t join core.event_status s on s.event_type = t.key where t.key = 'rate_cycle' group by t.module")" \
  '^policy:complete=true,ongoing=false$'
check "R14 a cycle needs two moves" \
  "$(as_pg "insert into policy.rate_cycle select (select id from core.event where slug='r22-cyc-easing'), 'easing', first_meeting_event_id, last_meeting_event_id, 1, -25, 1 from policy.rate_cycle limit 1")" \
  'violates check constraint'
check "R15 easing needs a negative net change and tightening a positive one" \
  "$(as_pg "update policy.rate_cycle set total_change_bps = 75 where event_id = (select id from core.event where slug='r22-cyc-easing')")" \
  'violates check constraint'
check "R16 job_writer may write cycles, anon may not" \
  "$(as_role job_writer "begin; update policy.rate_cycle set rule_version = 2; rollback" | tr '\n' ' ')|$(as_role anon "delete from policy.rate_cycle")" \
  '^\|.*permission denied'

############################################################ the gate: hidden, every figure is null
as_pg "update core.field_policy set status = 'hidden' where field_key = 'equity.rate_sensitivity'" >/dev/null
check "R17 hidden: the moves view returns its rows with no date or figure" \
  "$(as_role anon "select count(*), count(excess_pct), count(own_change_pct), count(nifty_change_pct), count(pre_date), count(members) from api.rate_decision_moves where subject_key like 'r22-%'")" \
  '^[1-9][0-9]*\|0\|0\|0\|0\|0$'
check "R18 hidden: no statistic, not even n" \
  "$(as_role anon "select count(*), count(n), count(median_excess_pct), count(mean_excess_pct), count(share_positive) from api.rate_decision_stats where subject_key like 'r22-%'")" \
  '^[1-9][0-9]*\|0\|0\|0\|0$'
check "R19 hidden: no cycle figure" \
  "$(as_role anon "select count(*), count(direction), count(moves), count(total_change_bps), count(first_decision_on), count(status) from api.rate_cycles where cycle_slug like 'r22-%'")" \
  '^2\|0\|0\|0\|0\|0$'
check "R20 hidden: no company-over-cycle figure" \
  "$(as_role anon "select count(*), count(own_change_pct), count(nifty_change_pct), count(excess_pct), count(is_ongoing) from api.company_rate_cycles where company_slug like 'r22-%'")" \
  '^1\|0\|0\|0\|0$'
check "R21 hidden: the semantic view gives the command bar no figure either" \
  "$(as_role sql_reader "select count(*), count(excess_pct) from semantic.rate_decision_moves where subject_key like 'r22-%'")" \
  '^[1-9][0-9]*\|0$'

as_pg "update core.field_policy set status = 'labelled', label = 'derived' where field_key = 'equity.rate_sensitivity'" >/dev/null
############################################################ shown: the moves
check "R22 only held rate decisions appear: the cuts, the hold and the hike; not the special meeting or the scheduled one" \
  "$(as_role anon "select string_agg(distinct event_slug, ',' order by event_slug) from api.rate_decision_moves where subject_key like 'r22-%'")" \
  '^r22-f1,r22-f2,r22-f3,r22-f4,r22-f5,r22-f6,r22-rh$'
check "R23 decisions are classed by the policy-rate change: cut for the Fed's six, hold for the RBI's" \
  "$(as_role anon "select string_agg(distinct bank||':'||decision||':'||policy_change_bps, ',' order by bank||':'||decision||':'||policy_change_bps) from api.rate_decision_moves where subject_key like 'r22-%'")" \
  '^fed:cut:-25,rbi:hold:0$'
check "R24 the excess is the company's move minus the Nifty 50's over the same window (A on the third cut: 3 - 1)" \
  "$(as_role anon "select own_change_pct||'|'||nifty_change_pct||'|'||excess_pct from api.rate_decision_moves where subject_key = 'r22-a' and event_slug = 'r22-f3'")" \
  '^3(\.0+)?\|1(\.0+)?\|2(\.0+)?$'
check "R25 the excess only where both moves have the same pre and post dates: C on the sixth cut has the company's move but no Nifty or excess" \
  "$(as_role anon "select own_change_pct||'|'||coalesce(nifty_change_pct::text,'null')||'|'||coalesce(excess_pct::text,'null') from api.rate_decision_moves where subject_key = 'r22-c' and event_slug = 'r22-f6'")" \
  '^18(\.0+)?\|null\|null$'
check "R26 a peer group's move on an event is the equal-weighted mean of its members' excess moves (third cut: (2+5+8)/3 = 5)" \
  "$(as_role anon "select excess_pct||'|'||members from api.rate_decision_moves where subject_kind = 'peer_group' and subject_key = 'r22-pg' and event_slug = 'r22-f3'")" \
  '^5(\.0+)?\|3$'
check "R27 a member without an excess move is left out of the mean (sixth cut: A 5, B 11 -> 8, two members)" \
  "$(as_role anon "select excess_pct||'|'||members from api.rate_decision_moves where subject_kind = 'peer_group' and subject_key = 'r22-pg' and event_slug = 'r22-f6'")" \
  '^8(\.0+)?\|2$'
check "R28 a company outside every peer group has its own rows and no group row" \
  "$(as_role anon "select count(*) filter (where subject_kind = 'company'), count(*) filter (where subject_kind = 'peer_group' and subject_key <> 'r22-pg') from api.rate_decision_moves where subject_key in ('r22-d', 'r22-pg') or subject_name like 'R22 D%'")" \
  '^[0-9]+\|0$'
check "R29 an unpublished company's reactions are not visible" \
  "$(as_role anon "select count(*) from api.rate_decision_moves where subject_key = 'r22-unpub'")|$(as_role anon "select count(*) from core.event_reaction r join core.entity e on e.id = r.entity_id where e.slug = 'r22-unpub'")" \
  '^0\|0$'
check "R30 the source line is NSE's" \
  "$(as_role anon "select distinct attribution from api.rate_decision_moves where subject_key = 'r22-a'")" '^Source: NSE$'

############################################################ shown: the statistics
check "R31 six cuts for A: n 6, mean and median 2.5, quartiles 1.25 and 3.75, share positive 5/6" \
  "$(as_role anon "select n||'|'||round(mean_excess_pct::numeric,4)||'|'||round(median_excess_pct::numeric,4)||'|'||round(p25_excess_pct::numeric,4)||'|'||round(p75_excess_pct::numeric,4)||'|'||round(share_positive::numeric,4) from api.rate_decision_stats where subject_key = 'r22-a' and bank = 'fed' and decision = 'cut' and window_key = 'd1'")" \
  '^6\|2\.5000\|2\.5000\|1\.2500\|3\.7500\|0\.8333$'
check "R32 fewer than 5 moves: n is shown and no statistic (A on the RBI hold, one move)" \
  "$(as_role anon "select n, coalesce(median_excess_pct::text,'null'), coalesce(mean_excess_pct::text,'null'), coalesce(share_positive::text,'null') from api.rate_decision_stats where subject_key = 'r22-a' and bank = 'rbi' and decision = 'hold'")" \
  '^1\|null\|null\|null$'
check "R33 a peer group has statistics over its events (six cuts: 1, 3, 5, 7, 9 and 8 -> median 6)" \
  "$(as_role anon "select n||'|'||round(median_excess_pct::numeric,4) from api.rate_decision_stats where subject_kind = 'peer_group' and subject_key = 'r22-pg' and bank = 'fed' and decision = 'cut'")" \
  '^6\|6\.0000$'

############################################################ shown: the cycles
check "R34 the cycles list, with direction, moves and net change; ongoing labelled" \
  "$(as_role anon "select string_agg(cycle_slug||':'||direction||':'||status||':'||moves||':'||total_change_bps, ',' order by cycle_slug) from api.rate_cycles where cycle_slug like 'r22-%'")" \
  '^r22-cyc-easing:easing:complete:3:-75,r22-cyc-tight:tightening:ongoing:2:25$'
check "R35 a company's move over a cycle: 10, the Nifty's 4, the excess 6" \
  "$(as_role anon "select own_change_pct||'|'||nifty_change_pct||'|'||excess_pct||'|'||is_ongoing from api.company_rate_cycles where company_slug = 'r22-a' and cycle_slug = 'r22-cyc-easing'")" \
  '^10(\.0+)?\|4(\.0+)?\|6(\.0+)?\|false$'

############################################################ suppression and the semantic view
as_pg "update core.source set is_suppressed = true where key = 'nse'" >/dev/null
check "R36 suppressing NSE hides every company reaction at once, in every view" \
  "$(as_role anon "select (select count(*) from api.rate_decision_moves where subject_kind = 'company' and subject_key like 'r22-%'), (select count(*) from api.rate_decision_stats where subject_key like 'r22-%'), (select count(*) from api.company_rate_cycles where company_slug like 'r22-%')")" \
  '^0\|0\|0$'
as_pg "update core.source set is_suppressed = false where key = 'nse'" >/dev/null
check "R37 the semantic view is owned by semantic_owner, with a security barrier; the api views run as the caller" \
  "$(as_pg "select string_agg(n||':'||o||':'||coalesce(r,''), ',' order by n||o) from (select c.relname n, pg_get_userbyid(c.relowner) o, array_to_string(c.reloptions, ';') r from pg_class c join pg_namespace s on s.oid = c.relnamespace where (s.nspname, c.relname) in (('semantic','rate_decision_moves'),('api','rate_decision_moves'),('api','rate_decision_stats'),('api','rate_cycles'),('api','company_rate_cycles'))) x")" \
  '^company_rate_cycles:postgres:security_invoker=true,rate_cycles:postgres:security_invoker=true,rate_decision_moves:postgres:security_invoker=true,rate_decision_moves:semantic_owner:security_barrier=true,rate_decision_stats:postgres:security_invoker=true$'
check "R38 the semantic view and every one of its columns has a comment" \
  "$(as_pg "select (obj_description('semantic.rate_decision_moves'::regclass, 'pg_class') is not null)||'|'||count(*) filter (where col_description('semantic.rate_decision_moves'::regclass, attnum) is null) from pg_attribute where attrelid = 'semantic.rate_decision_moves'::regclass and attnum > 0")" \
  '^true\|0$'
check "R39 sql_reader reads the semantic view; anon does not" \
  "$(as_role sql_reader "select count(*) > 0 from semantic.rate_decision_moves where subject_key = 'r22-a'")|$(as_role anon "select count(*) from semantic.rate_decision_moves")" \
  '^t\|.*permission denied'
check "R40 sql_reader reads no base table" \
  "$(as_role sql_reader "select count(*) from core.event_reaction")" 'permission denied'

cleanup
as_pg "update core.series set is_published = $NIFTY_WAS where key = 'yf.^NSEI'" >/dev/null
echo
echo "passed $pass, failed $fail"
