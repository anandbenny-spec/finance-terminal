#!/bin/bash
# Tests for 0036 (api.remarks_changes; issue #388, job 2B-19d). Run after the other scripts on the same fresh database. The script
# creates its own rows (companies ec36-a with two calls and one equity.call_remarks_change row, ec36-b with one call and none),
# checks through the roles and removes every row it made. Field policy flips happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.call_remarks_change where event_id in (select id from core.event where slug like 'ec36%');
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec36%');
         delete from core.document where external_key like 'ec36%';
         delete from core.event where slug like 'ec36%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'ec36%');
         delete from core.entity where slug like 'ec36%';
         update core.field_policy set status = 'hidden' where field_key = 'equity.remarks_change';
         update core.source set is_suppressed = false where key in ('nse','company_web');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
create function pg_temp.mk_call(p_ent text, p_label text, p_period date, p_filed timestamptz)
returns void language plpgsql as $f$
declare txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
        eid integer; evid bigint; did bigint; tag text := lower(p_ent || '-' || replace(p_label, ' ', ''));
begin
  select id into eid from core.entity where slug = p_ent;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec36-call-' || tag, 'ec36', p_period, 'stored',
            (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'ec36 call ' || tag, (select id from core.source where key = 'company_web'),
            'ec36-' || tag, 'https://example.com/ec36/' || tag || '.pdf', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at,
                                    exchange, route, transcript_document_id, words, unknown_role_share)
    values (evid, eid, p_label, p_period, lower(replace(p_label, ' ', '-')), 'results_link', p_filed, 'NSE',
            'company_link', did, 105, 0.10);
end $f$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec36-a', 'Ec36 A Ltd', 'ec36-a', 'IN', 'INR', true),
         ('company', 'ec36-b', 'Ec36 B Ltd', 'ec36-b', 'IN', 'INR', true);
insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000360' || (ascii(right(slug, 1)) - 96), 'EC36' || upper(right(slug, 1)), '536' || (ascii(right(slug, 1)) - 96)
  from core.entity where slug like 'ec36-%';
select pg_temp.mk_call('ec36-a', 'Q1 FY27', '2026-06-30', '2026-07-25');
select pg_temp.mk_call('ec36-a', 'Q2 FY27', '2026-09-30', '2026-10-25');
select pg_temp.mk_call('ec36-b', 'Q2 FY27', '2026-09-30', '2026-10-26');
insert into equity.call_remarks_change (event_id, base_event_id, words_base, words_new, words_added, words_removed, similarity,
                                        new_speakers, label, pairs, rule_version)
  select n.event_id, b.event_id, 200, 210, 40, 30, 0.8293, array['A. New Person'], 'new_speaker',
         jsonb_build_array(
           jsonb_build_object('kind', 'changed', 'changed_words', 9,
                              'old', jsonb_build_object('text', 'Old sentence about margins.', 'spans', jsonb_build_array(jsonb_build_array(0, 27))),
                              'new', jsonb_build_object('text', repeat('x', 400), 'spans', jsonb_build_array(jsonb_build_array(0, 400)))),
           jsonb_build_object('kind', 'added', 'changed_words', 5, 'old', null,
                              'new', jsonb_build_object('text', 'A new sentence.', 'spans', jsonb_build_array(jsonb_build_array(0, 15))))),
         'r1'
  from equity.earnings_call n
  join equity.earnings_call b on b.entity_id = n.entity_id and b.fiscal_label = 'Q1 FY27'
  where n.fiscal_label = 'Q2 FY27' and n.entity_id = (select id from core.entity where slug = 'ec36-a');
commit;
SQL
)"
check "R01 the test rows load (two companies, three calls, one remarks row)" \
  "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|])*$'
check "R02 the base table holds the one row" "$(as_pg "select count(*) from equity.call_remarks_change r join core.event e on e.id = r.event_id where e.slug like 'ec36%'")" '^1$'

N="select count(*) from api.remarks_changes where company_slug like 'ec36-%'"
check "V01 hidden (as seeded): anon sees no row" "$(as_role anon "$N")" '^0$'
check "V02 hidden: authenticated sees no row" "$(as_role authenticated "$N")" '^0$'
check "V03 shown: anon sees the one row" "$(flip anon "$(POL equity.remarks_change shown)" "$N")" '^1$'
check "V04 labelled: anon sees the one row" "$(flip anon "$(POL equity.remarks_change labelled)" "$N")" '^1$'
check "V05 no policy row at all: fails closed" \
  "$(flip anon "delete from core.field_policy where field_key = 'equity.remarks_change'" "$N")" '^0$'
check "V06 the row names its call, the previous call and the figures" \
  "$(flip anon "$(POL equity.remarks_change shown)" "select company_slug || '|' || fiscal_label || '|' || base_fiscal_label || '|' || words_added || '|' || words_removed || '|' || similarity || '|' || label || '|' || new_speakers[1] from api.remarks_changes where company_slug = 'ec36-a'")" \
  '^ec36-a[|]Q2 FY27[|]Q1 FY27[|]40[|]30[|]0.8293[|]new_speaker[|]A. New Person$'
check "V07 the policy label comes through when labelled" \
  "$(flip anon "update core.field_policy set status = 'labelled', label = 'derived' where field_key = 'equity.remarks_change'" "select policy_label from api.remarks_changes where company_slug = 'ec36-a'")" '^derived$'
check "V08 the first call of a company (no previous call) has no row" \
  "$(flip anon "$(POL equity.remarks_change shown)" "select count(*) from api.remarks_changes where company_slug = 'ec36-b' or fiscal_label = 'Q1 FY27'")" '^0$'
check "V09 the pairs are returned as stored: two pairs, no side longer than 400 characters" \
  "$(flip anon "$(POL equity.remarks_change shown)" "select jsonb_array_length(pairs) || ',' || (select max(char_length(s)) from jsonb_array_elements(pairs) p, lateral (values (p->'old'->>'text'), (p->'new'->>'text')) v(s)) from api.remarks_changes where company_slug = 'ec36-a'")" '^2,400$'

check "X01 nse suppressed: the call is gone" \
  "$(flip anon "$(POL equity.remarks_change shown); update core.source set is_suppressed = true where key = 'nse'" "$N")" '^0$'
check "X02 company_web suppressed (the transcripts' source): the call is gone" \
  "$(flip anon "$(POL equity.remarks_change shown); update core.source set is_suppressed = true where key = 'company_web'" "$N")" '^0$'

check "S01 the view is security_invoker" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname = 'remarks_changes' and c.reloptions @> array['security_invoker=true']")" '^1$'
check "S02 anon, authenticated and service_role can select it" \
  "$(as_pg "select count(*) from unnest(array['anon','authenticated','service_role']) r where has_table_privilege(r, 'api.remarks_changes'::regclass, 'select')")" '^3$'
check "S04 the view has a comment that says it is empty while the field is hidden" \
  "$(as_pg "select (obj_description('api.remarks_changes'::regclass) like '%Empty while field policy equity.remarks_change is hidden%')::text")" '^true$'
check "S05 the field policy is still hidden (the flip is Anand's)" \
  "$(as_pg "select status from core.field_policy where field_key = 'equity.remarks_change'")" '^hidden$'

cleanup
echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
