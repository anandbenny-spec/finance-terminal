#!/bin/bash
# Tests for 0030 (earnings scores; issue #180, job 2B-17; Spec 02B §3.3, §3.4, §3.5). Run after the other scripts on the same
# fresh database. The script creates its own rows (company ec30-a with two calls, the second's event unpublished for one
# check), checks through the roles and removes every row it made.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from equity.call_remarks_change; delete from equity.call_tone;
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec30%');
         delete from core.event where slug like 'ec30%';
         delete from core.entity where slug like 'ec30%';" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec30-a', 'Ec30 A Ltd', 'ec30-a', 'IN', 'INR', true);
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
  select 'earnings_call', e.id, v.slug, 'ec30', v.d::date, 'stored', (select id from core.source where key = 'nse'), v.pub
  from core.entity e, (values ('ec30-call-q1', '2026-07-25', true), ('ec30-call-q2', '2026-10-25', true)) v(slug, d, pub)
  where e.slug = 'ec30-a';
insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange, route)
  select ev.id, ev.entity_id, v.label, v.pe::date, v.sk, 'results_link', '2026-10-26', 'NSE', 'none'
  from core.event ev
  join (values ('ec30-call-q1', 'Q1 FY27', '2026-06-30', 'q1-fy27'), ('ec30-call-q2', 'Q2 FY27', '2026-09-30', 'q2-fy27')) v(slug, label, pe, sk)
    on v.slug = ev.slug;
insert into equity.call_remarks_change (event_id, base_event_id, words_base, words_new, words_added, words_removed, similarity,
                                        new_speakers, label, pairs, rule_version)
  select b.id, a.id, 100, 100, 10, 10, 0.9, '{}', null, '[]', 'test'
  from core.event a, core.event b where a.slug = 'ec30-call-q1' and b.slug = 'ec30-call-q2';
insert into equity.call_tone (event_id, rule_version) select id, 'test' from core.event where slug = 'ec30-call-q2';
commit;
SQL
)"
check "A01 the test rows load" "$SETUP_OUT" '^(BEGIN|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|])*$'

# --- RLS and policies
check "R01 RLS is on for both tables" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'equity' and c.relname in ('call_remarks_change','call_tone') and c.relrowsecurity")" '^2$'
check "R02 anon sees a row while its call's event is visible" \
  "$(as_role anon "select (select count(*) from equity.call_remarks_change where event_id in (select id from core.event where slug like 'ec30%')) || ':' || (select count(*) from equity.call_tone where event_id in (select id from core.event where slug like 'ec30%'))")" '^1:1$'
check "R03 anon sees neither once the call's event is unpublished" \
  "$(psql $H -U postgres -c "begin; update core.event set is_published = false where slug like 'ec30%'; set role anon; select (select count(*) from equity.call_remarks_change) || ':' || (select count(*) from equity.call_tone); rollback" 2>&1 | grep -v '^SET$\|^BEGIN$\|^UPDATE\|^ROLLBACK$')" '^0:0$'
check "R04 job_writer can insert, update and delete a remarks row and a tone row" \
  "$(as_role job_writer "update equity.call_remarks_change set similarity = 0.8 where event_id in (select id from core.event where slug = 'ec30-call-q2'); update equity.call_tone set n = 3 where event_id in (select id from core.event where slug = 'ec30-call-q2'); delete from equity.call_tone where event_id in (select id from core.event where slug = 'ec30-call-q2'); insert into equity.call_tone (event_id, rule_version) select id, 'test' from core.event where slug = 'ec30-call-q2'; delete from equity.call_remarks_change where event_id in (select id from core.event where slug = 'ec30-call-q2'); select 'ok'")" 'ok$'
check "R05 anon can't write either table" \
  "$(as_role anon "delete from equity.call_remarks_change")" 'permission denied'
check "R06 more than five pairs is refused" \
  "$(as_pg "insert into equity.call_remarks_change (event_id, base_event_id, words_base, words_new, words_added, words_removed, similarity, pairs, rule_version) select a.id, b.id, 1, 1, 0, 0, 1, '[1,2,3,4,5,6]'::jsonb, 'test' from core.event a, core.event b where a.slug = 'ec30-call-q1' and b.slug = 'ec30-call-q2'")" 'violates check constraint'

# --- catalogue and field policies
check "C01 the call_tone task exists: a score, citations required" \
  "$(as_pg "select output_kind || ':' || requires_citations from core.ai_task where key = 'call_tone'")" '^score:true$'
check "C02 the three field policies exist and are hidden" \
  "$(as_pg "select string_agg(field_key || ':' || status, ',' order by field_key) from core.field_policy where field_key in ('ai.call_tone','equity.results_move','equity.remarks_change')")" \
  '^ai[.]call_tone:hidden,equity[.]remarks_change:hidden,equity[.]results_move:hidden$'

cleanup
echo; echo "passed $pass, failed $fail"
