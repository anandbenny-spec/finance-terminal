#!/bin/bash
# Tests for 0050 (a call is tied to its quarter's first release; issue #573, job 2B-16d, #567's finding d). Run after the other
# scripts on the same fresh database; 0044's own script (run_tests_0044.sh) keeps checking the gate, the arithmetic, the left-out
# reasons, the grants and security_invoker on the same view. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
# The script creates its own rows (entities, events, documents named r50-*), checks through the roles and removes them.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from equity.earnings_call where event_id in (select id from core.event where slug like 'r50-%');
         delete from equity.filing where document_id in (select id from core.document where external_key like 'r50-%');
         delete from core.document where external_key like 'r50-%';
         delete from core.event where slug like 'r50-%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'r50-%');
         delete from core.entity where slug like 'r50-%';
         update core.field_policy set status = 'hidden', label = 'derived' where field_key = 'equity.results_move';" >/dev/null
}
cleanup

psql $H -U postgres -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || echo "FAIL  setup of the test rows"
insert into core.entity (kind, slug, name, market, currency, is_published) values
  ('company','r50-a','R50 A Ltd','IN','INR',true), ('company','r50-b','R50 B Ltd','IN','INR',true),
  ('company','r50-c','R50 C Ltd','IN','INR',true), ('company','r50-d','R50 D Ltd','IN','INR',true),
  ('company','r50-e','R50 E Ltd','IN','INR',true);
insert into equity.company (entity_id) select id from core.entity where slug like 'r50-%';

-- results filings: slug, company, release time, status, and the quarter's period_end (null: no equity.filing row)
create temp table rf (slug text, co text, at timestamptz, st text, pe date);
insert into rf values
  -- (a) A's Q4 FY25: first release Thu 8 May 2025 20:01 IST (superseded), its -r2 Sat 14 Jun 2025 13:19 IST (filed); A's next quarter apart
  ('r50-a1',   'r50-a','2025-05-08 20:01+05:30','superseded','2025-03-31'),
  ('r50-a1-r2','r50-a','2025-06-14 13:19+05:30','filed',     '2025-03-31'),
  ('r50-a2',   'r50-a','2025-08-01 18:00+05:30','filed',     '2025-06-30'),
  -- (b) B: one version
  ('r50-b1',   'r50-b','2025-07-10 18:00+05:30','filed',     '2025-06-30'),
  -- (c) C: a same-day revision 43 minutes later
  ('r50-c1',   'r50-c','2025-07-20 17:00+05:30','superseded','2025-06-30'),
  ('r50-c1-r2','r50-c','2025-07-20 17:43+05:30','filed',     '2025-06-30'),
  -- (d) D: two versions with no equity.filing row at all, each its own quarter
  ('r50-d1',   'r50-d','2025-08-05 18:00+05:30','superseded',null),
  ('r50-d1-r2','r50-d','2025-08-09 18:00+05:30','filed',     null),
  -- E: the first release is not published (a held-back version): the call linked to the published later one stays on it
  ('r50-e1',   'r50-e','2025-08-10 18:00+05:30','superseded','2025-06-30'),
  ('r50-e1-r2','r50-e','2025-08-12 18:00+05:30','filed',     '2025-06-30');
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, is_published)
  select 'results_filing', n.id, rf.slug, rf.slug, (rf.at at time zone 'Asia/Kolkata')::date, rf.at, rf.st, rf.slug <> 'r50-e1'
    from rf join core.entity n on n.slug = rf.co;
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
  select 'results_xbrl', e.entity_id, rf.slug, (select id from core.source where key = 'nse'), 'r50-' || rf.slug, 'https://example.invalid/' || rf.slug
    from rf join core.event e on e.slug = rf.slug where rf.pe is not null;
insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, source_url, exchange)
  select d.id, d.entity_id, e.id, 'consolidated', 'ind_as', rf.pe, d.source_url, 'NSE'
    from rf join core.event e on e.slug = rf.slug join core.document d on d.external_key = 'r50-' || rf.slug where rf.pe is not null;

-- calls: A's and C's are linked to the later version, B's to its only one, D's to its later one, E's to the published later one
create temp table cl (co text, fl text, linked text);
insert into cl values ('r50-a','Q4 FY25','r50-a1-r2'), ('r50-b','Q1 FY26','r50-b1'), ('r50-c','Q1 FY26','r50-c1-r2'),
                      ('r50-d','Q1 FY26','r50-d1-r2'), ('r50-e','Q1 FY26','r50-e1-r2');
insert into core.event (event_type, entity_id, slug, title, starts_on, status, is_published)
  select 'earnings_call', n.id, cl.co || '-call', 'r50', '2025-08-20', 'on_company_website', true from cl join core.entity n on n.slug = cl.co;
insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange, route,
                                  company_url, results_event_id)
  select ev.id, ev.entity_id, cl.fl, '2025-03-31', lower(replace(cl.fl, ' ', '-')), 'results_link', '2025-08-20', 'NSE', 'none', 'https://example.com/r50',
         (select id from core.event where slug = cl.linked)
    from cl join core.event ev on ev.slug = cl.co || '-call';
update core.field_policy set status = 'shown' where field_key = 'equity.results_move';
SQL

# one string per filing: the call and label on its rows ('none' when null), both windows folded together
Q="select string_agg(distinct coalesce(call_slug,'none')||'/'||coalesce(fiscal_label,'none'), ',') from api.results_moves where results_slug = "

check "T01 (a) the call linked to the -r2 is tied to the first release (8 May 2025)" \
  "$(as_role anon "${Q}'r50-a1'")" '^r50-a-call/Q4 FY25$'
check "T02 (a) the linked later version keeps its rows with no call and no label" \
  "$(as_role anon "${Q}'r50-a1-r2'")" '^none/none$'
check "T03 (a) the later version still has its two window rows" \
  "$(as_role anon "select count(*) from api.results_moves where results_slug = 'r50-a1-r2'")" '^2$'
check "T04 (a) the company's next quarter is not touched by the tie" \
  "$(as_role anon "${Q}'r50-a2'")" '^none/none$'
check "T05 (b) a call linked to its quarter's only version stays on it" \
  "$(as_role anon "${Q}'r50-b1'")" '^r50-b-call/Q1 FY26$'
check "T06 (c) a same-day revision 43 minutes later: the call goes to the earlier one" \
  "$(as_role anon "${Q}'r50-c1'")" '^r50-c-call/Q1 FY26$'
check "T07 (c) and the 43-minute revision has none" \
  "$(as_role anon "${Q}'r50-c1-r2'")" '^none/none$'
check "T08 (d) versions with no equity.filing row are their own quarters: the call stays on the one it is linked to" \
  "$(as_role anon "${Q}'r50-d1-r2'")" '^r50-d-call/Q1 FY26$'
check "T09 (d) and the earlier one has no call" \
  "$(as_role anon "${Q}'r50-d1'")" '^none/none$'
check "T10 an unpublished first release is not a row and does not take the call: it stays on the published version" \
  "$(as_role anon "select count(*) from api.results_moves where results_slug = 'r50-e1'")|$(as_role anon "${Q}'r50-e1-r2'")" '^0\|r50-e-call/Q1 FY26$'
check "T11 each call appears on exactly one filing (5 calls, no duplicates across the test companies)" \
  "$(as_role anon "select count(distinct results_slug) || '/' || count(distinct call_slug) from api.results_moves where call_slug like 'r50-%'")" '^5/5$'
check "T12 the row count is unchanged by the tie: 9 published filings x 2 windows" \
  "$(as_role anon "select count(*) from api.results_moves where company_slug like 'r50-%'")" '^18$'
check "T13 a company filter still returns that company only" \
  "$(as_role anon "select count(distinct company_slug) from api.results_moves where company_slug = 'r50-a'")" '^1$'
check "T14 the view keeps security_invoker and its comment now says the first release" \
  "$(as_pg "select coalesce(reloptions::text,'') like '%security_invoker=true%' and obj_description('api.results_moves'::regclass, 'pg_class') like '%first release%'
              from pg_class where oid = 'api.results_moves'::regclass")" '^t$'
check "T15 the view's columns are 0044's, in order" \
  "$(as_pg "select string_agg(attname, ',' order by attnum) from pg_attribute where attrelid = 'api.results_moves'::regclass and attnum > 0 and not attisdropped")" \
  '^company_slug,company_name,currency,results_slug,call_slug,fiscal_label,occurs_at,window_key,pre_date,post_date,own_change_pct,nifty_change_pct,excess_pct,left_out_reason,attribution$'
check "T16 hidden again: no row" \
  "$(as_pg "update core.field_policy set status = 'hidden' where field_key = 'equity.results_move'" >/dev/null; as_role anon "select count(*) from api.results_moves where company_slug like 'r50-%'")" '^0$'

cleanup
echo
echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
