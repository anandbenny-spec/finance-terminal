#!/bin/bash
# Tests for 0044 (results-day moves; issue #471, Spec 02B §5.12, Spec 02A §5.10). Run after the other scripts on the same
# fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
# The script creates its own rows (entities, events and reactions named r44-*), checks through the roles and removes them.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from core.event_reaction where event_id in (select id from core.event where slug like 'r44-%');
         delete from equity.earnings_call where event_id in (select id from core.event where slug like 'r44-%');
         delete from equity.adjustment_factor where entity_id in (select id from core.entity where slug like 'r44-%');
         delete from equity.corporate_action where event_id in (select id from core.event where slug like 'r44-%');
         delete from core.event where slug like 'r44-%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'r44-%');
         delete from core.entity where slug like 'r44-%';
         delete from core.observation where task_run_id in (select id from ops.task_run where task_key = 'r44.task');
         delete from ops.task_run where task_key = 'r44.task';
         delete from ops.task where key = 'r44.task';
         update core.field_policy set status = 'hidden', label = 'derived' where field_key = 'equity.results_move';" >/dev/null
}
cleanup
NIFTY_WAS=$(as_pg "select is_published from core.series where key = 'yf.^NSEI'")

psql $H -U postgres -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || echo "FAIL  setup of the test rows"
insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
  values ('r44.task', 'test', '0044 test task', '{daily}', '20 hours', '4 days', false);
insert into ops.job_run (workflow, trigger, runner) values ('daily', 'cli', 'local');
insert into ops.task_run (job_run_id, task_key, status) values ((select max(id) from ops.job_run), 'r44.task', 'success');
update core.series set is_published = true where key = 'yf.^NSEI';
insert into core.observation (series_id, obs_date, value, task_run_id)
  select (select id from core.series where key = 'yf.^NSEI'), d::date, 15000, (select id from ops.task_run where task_key = 'r44.task')
    from generate_series('2025-01-01'::date, current_date + 1, '1 day') d
  on conflict do nothing;

insert into core.entity (kind, slug, name, market, currency, is_published) values
  ('company','r44-a','R44 A Ltd','IN','INR',true), ('company','r44-b','R44 B Ltd','IN','INR',true),
  ('company','r44-c','R44 C Ltd','IN','INR',true), ('company','r44-d','R44 D Ltd','IN','INR',true),
  ('company','r44-e','R44 E Ltd','IN','INR',true), ('company','r44-f','R44 F Ltd','IN','INR',true);
insert into equity.company (entity_id) select id from core.entity where slug like 'r44-%';

-- results filings: A in market hours (12:00 IST), B two days ago, C/D/E/F in 2025, A again before 2025 and an unpublished one
create temp table rf (slug text, co text, at timestamptz, pub boolean);
insert into rf values
  ('r44-a1','r44-a','2025-06-10 12:00+05:30',true),
  ('r44-b1','r44-b', now() - interval '2 days',true),
  ('r44-c1','r44-c','2025-07-01 18:00+05:30',true),
  ('r44-d1','r44-d','2025-08-01 18:00+05:30',true),
  ('r44-e1','r44-e','2025-09-01 18:00+05:30',true),
  ('r44-f1','r44-f','2025-10-01 18:00+05:30',true),
  ('r44-a0','r44-a','2024-12-31 18:00+05:30',true),
  ('r44-au','r44-a','2025-06-20 18:00+05:30',false);
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, is_published)
  select 'results_filing', n.id, rf.slug, rf.slug, (rf.at at time zone 'Asia/Kolkata')::date, rf.at, 'filed', rf.pub
    from rf join core.entity n on n.slug = rf.co;

-- A's call, linked to A's first results filing
insert into core.event (event_type, entity_id, slug, title, starts_on, status, is_published)
  select 'earnings_call', id, 'r44-a1-call', 'r44', '2025-06-11', 'on_company_website', true from core.entity where slug = 'r44-a';
insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange, route,
                                  company_url, results_event_id)
  select ev.id, ev.entity_id, 'Q4 FY25', '2025-03-31', 'q4-fy25', 'results_link', '2025-06-11', 'NSE', 'none',
         'https://example.com/r44', (select id from core.event where slug = 'r44-a1')
    from core.event ev where ev.slug = 'r44-a1-call';

-- reactions. Nifty (series, observed) and company (adjusted_close) rows
create temp table rx (ev text, co text, w text, pre date, post date, chg numeric);
insert into rx values
  -- A1 d1: same dates, company +3.0, Nifty +1.0 -> excess 2.0;   d5: dates differ
  ('r44-a1','r44-a','d1','2025-06-09','2025-06-10', 3.0), ('r44-a1', null,'d1','2025-06-09','2025-06-10', 1.0),
  ('r44-a1','r44-a','d5','2025-06-09','2025-06-16', 5.0), ('r44-a1', null,'d5','2025-06-09','2025-06-17', 2.0),
  -- C1: only the Nifty has rows, a held factor of C has an ex-date after the pre date
  ('r44-c1', null,'d1','2025-07-01','2025-07-02', 1.0), ('r44-c1', null,'d5','2025-07-01','2025-07-08', 1.0),
  -- D1: only the Nifty; a dividend of D has its ex-date inside d1's window and inside d5's
  ('r44-d1', null,'d1','2025-08-01','2025-08-04', 1.0), ('r44-d1', null,'d5','2025-08-01','2025-08-08', 1.0),
  -- E1: only the Nifty d1; nothing explains the company's gap
  ('r44-e1', null,'d1','2025-09-01','2025-09-02', 1.0),
  -- F1: the company d1 only, the Nifty has none
  ('r44-f1','r44-f','d1','2025-10-01','2025-10-03', 2.0);
insert into core.event_reaction (event_id, series_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select e.id,
         case when x.co is null then (select id from core.series where key = 'yf.^NSEI') end,
         case when x.co is not null then (select id from core.entity where slug = x.co) end,
         x.w,
         case when x.co is null then (select source_id from core.series where key = 'yf.^NSEI') else (select id from core.source where key = 'nse') end,
         x.pre, x.post, 100, 100, x.chg, 'pct', case when x.co is null then 'observed' else 'adjusted_close' end,
         (select id from ops.task_run where task_key = 'r44.task')
    from rx x join core.event e on e.slug = x.ev;
-- A reaction for the pre-2025 and the unpublished filing: the view must not show them
insert into core.event_reaction (event_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change, change_unit, value_basis, task_run_id)
  select e.id, (select id from core.entity where slug = 'r44-a'), 'd1', (select id from core.source where key = 'nse'),
         '2025-06-19', '2025-06-20', 100, 100, 9, 'pct', 'adjusted_close', (select id from ops.task_run where task_key = 'r44.task')
    from core.event e where e.slug = 'r44-au';

-- C: a held factor (a split, ex 2025-07-10); D: a dividend (ex 2025-08-02)
insert into core.event (event_type, entity_id, slug, title, starts_on, status, is_published)
  select 'corporate_action', id, 'r44-split-c', 'r44', date '2025-07-10', 'effective', true from core.entity where slug = 'r44-c'
  union all
  select 'corporate_action', id, 'r44-div-d', 'r44', date '2025-08-02', 'effective', true from core.entity where slug = 'r44-d';
insert into equity.corporate_action (event_id, kind, ex_date, subject, exchange)
  select id, case when slug = 'r44-split-c' then 'split' else 'dividend' end, starts_on, 'r44', 'NSE'
    from core.event where slug in ('r44-split-c','r44-div-d');
insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method, status, reason)
  select e.id, e.entity_id, e.starts_on, null, 'ratio', 'held', 'r44' from core.event e where e.slug = 'r44-split-c';
SQL

Q="select window_key||'='||coalesce(left_out_reason,'-')||'/'||coalesce(excess_pct::text,'-') from api.results_moves where results_slug = "

# the gate: hidden gives no row, shown gives rows
as_pg "update core.field_policy set status = 'hidden' where field_key = 'equity.results_move'" >/dev/null
check "M01 hidden: anon gets no row at all" \
  "$(as_role anon "select count(*) from api.results_moves where company_slug like 'r44-%'")" '^0$'
as_pg "update core.field_policy set status = 'shown' where field_key = 'equity.results_move'" >/dev/null
check "M02 shown: anon gets the rows of the 2025+ published filings, 2 windows each (A1, B1, C1, D1, E1, F1)" \
  "$(as_role anon "select count(*) from api.results_moves where company_slug like 'r44-%'")" '^12$'
check "M03 labelled also opens it" \
  "$(as_pg "update core.field_policy set status = 'labelled', label = 'derived' where field_key = 'equity.results_move'" >/dev/null; as_role anon "select count(*) from api.results_moves where company_slug like 'r44-%'")" '^12$'
as_pg "update core.field_policy set status = 'shown' where field_key = 'equity.results_move'" >/dev/null

# the arithmetic and the reasons
check "M04 excess = company - Nifty 50 over the same dates (3.0 - 1.0 = 2.0)" \
  "$(as_role anon "${Q}'r44-a1' and window_key = 'd1'")" '^d1=-/2\.0$'
check "M05 different post dates: no excess, reason dates_differ" \
  "$(as_role anon "${Q}'r44-a1' and window_key = 'd5'")" '^d5=dates_differ/-$'
check "M06 a recent release with neither row: waiting" \
  "$(as_role anon "select string_agg(left_out_reason, ',') from api.results_moves where results_slug = 'r44-b1'")" '^waiting,waiting$'
check "M07 a held adjustment factor after the pre date: held_factor (both windows)" \
  "$(as_role anon "select string_agg(left_out_reason, ',') from api.results_moves where results_slug = 'r44-c1'")" '^held_factor,held_factor$'
check "M08 an ex-dividend date inside the window: dividend" \
  "$(as_role anon "select string_agg(left_out_reason, ',') from api.results_moves where results_slug = 'r44-d1'")" '^dividend,dividend$'
check "M09 no explanation: no_price (the Nifty's row but none for the company; an old release with neither row)" \
  "$(as_role anon "${Q}'r44-e1' order by window_key")" '^d1=no_price/-\s*d5=no_price/-$|^d1=no_price/-$'
check "M10 the company's row with no Nifty 50 row: no_index, no excess" \
  "$(as_role anon "${Q}'r44-f1' and window_key = 'd1'")" '^d1=no_index/-$'
check "M11 a left-out window has no figure and no date from the company" \
  "$(as_role anon "select count(own_change_pct) + count(excess_pct) + count(pre_date) from api.results_moves where results_slug in ('r44-b1','r44-c1','r44-d1','r44-e1')")" '^0$'
check "M12 the filing before 2025 and the unpublished one never appear" \
  "$(as_role anon "select count(*) from api.results_moves where results_slug in ('r44-a0','r44-au')")" '^0$'
check "M13 the call of a linked filing is named; an unlinked filing has no call" \
  "$(as_role anon "select string_agg(distinct coalesce(call_slug,'none')||'/'||coalesce(fiscal_label,'none'), ',') from api.results_moves where company_slug in ('r44-a','r44-b')")" '^none/none,r44-a1-call/Q4 FY25$'
check "M14 the view carries the NSE attribution and the company's currency" \
  "$(as_role anon "select count(*) filter (where attribution is not null and currency = 'INR') = count(*) from api.results_moves where company_slug like 'r44-%'")" '^t$'
check "M15 a company filter returns that company only" \
  "$(as_role anon "select count(distinct company_slug) from api.results_moves where company_slug = 'r44-a'")" '^1$'

# access and shape
check "M16 the view is security_invoker" \
  "$(as_pg "select coalesce(reloptions::text,'') from pg_class where oid = 'api.results_moves'::regclass")" 'security_invoker=true'
check "M17 anon cannot write core.event_reaction" \
  "$(as_role anon "delete from core.event_reaction")" 'permission denied'
check "M18 the view has a comment" \
  "$(as_pg "select obj_description('api.results_moves'::regclass, 'pg_class') is not null")" '^t$'
check "M19 anon cannot write through the view" \
  "$(as_role anon "delete from api.results_moves")" 'permission denied|cannot delete'
check "M20 there is no semantic view for it (the command bar gets it after 18 Nov)" \
  "$(as_pg "select count(*) from pg_class where relname = 'results_moves' and relnamespace = 'semantic'::regnamespace")" '^0$'

cleanup
as_pg "update core.series set is_published = $NIFTY_WAS where key = 'yf.^NSEI'" >/dev/null
echo
echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
