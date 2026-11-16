#!/bin/bash
# Tests for 0045 (api.call_tone; issue #482, job 2B-12b). Run after the other scripts on the same fresh database. The script creates
# its own rows (ec45-a: two calls, the first carried by BSE, with tone rows and the second's change against the first; ec45-b: one
# call whose tone is held; ec45-c: one call with no tone row), checks through the roles and removes every row it made. Field policy
# flips, suppressions and unpublishing happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.call_tone where event_id in (select id from core.event where slug like 'ec45%');
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec45%');
         delete from core.document where external_key like 'ec45%';
         delete from core.event where slug like 'ec45%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'ec45%');
         delete from core.entity where slug like 'ec45%';
         update core.field_policy set status = 'hidden' where field_key = 'ai.call_tone';
         update core.source set is_suppressed = false where key in ('nse','bse','company_web');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
create function pg_temp.mk_call(p_ent text, p_label text, p_period date, p_filed timestamptz, p_src text)
returns void language plpgsql as $f$
declare txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
        eid integer; evid bigint; did bigint; tag text := lower(p_ent || '-' || replace(p_label, ' ', ''));
begin
  select id into eid from core.entity where slug = p_ent;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec45-call-' || tag, 'ec45', p_period, 'stored',
            (select id from core.source where key = p_src), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'ec45 call ' || tag, (select id from core.source where key = 'company_web'),
            'ec45-' || tag, 'https://example.com/ec45/' || tag || '.pdf', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at,
                                    exchange, route, transcript_document_id, words, unknown_role_share)
    values (evid, eid, p_label, p_period, lower(replace(p_label, ' ', '-')), 'results_link', p_filed, case when p_src = 'bse' then 'BSE' else 'NSE' end,
            'company_link', did, 105, 0.10);
end $f$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec45-a', 'Ec45 A Ltd', 'ec45-a', 'IN', 'INR', true),
         ('company', 'ec45-b', 'Ec45 B Ltd', 'ec45-b', 'IN', 'INR', true),
         ('company', 'ec45-c', 'Ec45 C Ltd', 'ec45-c', 'IN', 'INR', true);
insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000450' || (ascii(right(slug, 1)) - 96), 'EC45' || upper(right(slug, 1)), '545' || (ascii(right(slug, 1)) - 96)
  from core.entity where slug like 'ec45-%';
select pg_temp.mk_call('ec45-a', 'Q1 FY27', '2026-06-30', '2026-07-25', 'bse');
select pg_temp.mk_call('ec45-a', 'Q2 FY27', '2026-09-30', '2026-10-25', 'nse');
select pg_temp.mk_call('ec45-b', 'Q2 FY27', '2026-09-30', '2026-10-26', 'nse');
select pg_temp.mk_call('ec45-c', 'Q2 FY27', '2026-09-30', '2026-10-27', 'nse');
insert into equity.call_tone (event_id, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean,
                              prev_event_id, delta, se_delta, change, held_reason, rule_version)
  select n.event_id, null, 18, 0.8333, 0.9, 6, 1.0, 12, 0.75,
         b.event_id, 0.6333, 0.3, 'more_positive', null, 'r1'
  from equity.earnings_call n
  join equity.earnings_call b on b.entity_id = n.entity_id and b.fiscal_label = 'Q1 FY27'
  where n.fiscal_label = 'Q2 FY27' and n.entity_id = (select id from core.entity where slug = 'ec45-a');
insert into equity.call_tone (event_id, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, rule_version)
  select event_id, null, 20, 0.2, 0.8, 8, 0.25, 12, 0.1667, 'r1'
  from equity.earnings_call where fiscal_label = 'Q1 FY27' and entity_id = (select id from core.entity where slug = 'ec45-a');
insert into equity.call_tone (event_id, ai_output_id, n, mean, sd, opening_n, opening_mean, answers_n, answers_mean, held_reason, rule_version)
  select event_id, null, 5, null, null, 2, 1.0, 3, 0.0, 'too little management text to score', 'r1'
  from equity.earnings_call where entity_id = (select id from core.entity where slug = 'ec45-b');
commit;
SQL
)"
check "T01 the test rows load (three companies, four calls, three tone rows)" \
  "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|])*$'
check "T02 the base table holds the three rows" "$(as_pg "select count(*) from equity.call_tone t join core.event e on e.id = t.event_id where e.slug like 'ec45%'")" '^3$'

N="select count(*) from api.call_tone where company_slug like 'ec45-%'"
check "V01 hidden (as seeded): anon sees no row" "$(as_role anon "$N")" '^0$'
check "V02 hidden: authenticated sees no row" "$(as_role authenticated "$N")" '^0$'
check "V03 shown: anon sees the three rows (a call with no tone row has none)" "$(flip anon "$(POL ai.call_tone shown)" "$N")" '^3$'
check "V04 labelled: authenticated sees the three rows" "$(flip authenticated "$(POL ai.call_tone labelled)" "$N")" '^3$'
check "V05 no policy row at all: fails closed" \
  "$(flip anon "delete from core.field_policy where field_key = 'ai.call_tone'" "$N")" '^0$'
check "V06 the row names its call, the previous call and the figures" \
  "$(flip anon "$(POL ai.call_tone shown)" "select company_slug || '|' || fiscal_label || '|' || n || '|' || mean || '|' || opening_n || '|' || answers_mean || '|' || prev_fiscal_label || '|' || delta || '|' || se_delta || '|' || change from api.call_tone where company_slug = 'ec45-a' and fiscal_label = 'Q2 FY27'")" \
  '^ec45-a[|]Q2 FY27[|]18[|]0[.]8333[|]6[|]0[.]75[|]Q1 FY27[|]0[.]6333[|]0[.]3[|]more_positive$'
check "V07 the policy label comes through when labelled" \
  "$(flip anon "update core.field_policy set status = 'labelled', label = 'AI-scored' where field_key = 'ai.call_tone'" "select distinct policy_label from api.call_tone where company_slug like 'ec45-%'")" '^AI-scored$'
check "V08 a held call keeps its held_reason, with a null mean and no change" \
  "$(flip anon "$(POL ai.call_tone shown)" "select held_reason || '|' || coalesce(mean::text, 'null') || '|' || coalesce(change, 'null') from api.call_tone where company_slug = 'ec45-b'")" \
  '^too little management text to score[|]null[|]null$'
check "V09 the first call of a company (no previous call) has no previous label and no change" \
  "$(flip anon "$(POL ai.call_tone shown)" "select coalesce(prev_fiscal_label, 'null') || '|' || coalesce(change, 'null') from api.call_tone where company_slug = 'ec45-a' and fiscal_label = 'Q1 FY27'")" '^null[|]null$'
check "V10 the company filter returns only that company" \
  "$(flip anon "$(POL ai.call_tone shown)" "select count(*) from api.call_tone where company_slug = 'ec45-a'")" '^2$'

check "X01 nse suppressed: the nse-carried calls are gone, the bse-carried one stays" \
  "$(flip anon "$(POL ai.call_tone shown); update core.source set is_suppressed = true where key = 'nse'" "$N")" '^1$'
check "X02 company_web suppressed (the transcripts' source): every call is gone" \
  "$(flip anon "$(POL ai.call_tone shown); update core.source set is_suppressed = true where key = 'company_web'" "$N")" '^0$'
check "X03 bse suppressed: the bse-carried call is gone and the next call's comparison with it is not shown" \
  "$(flip anon "$(POL ai.call_tone shown); update core.source set is_suppressed = true where key = 'bse'" "select count(*) || '|' || coalesce(max(prev_fiscal_label), 'null') || '|' || coalesce(max(change), 'null') || '|' || coalesce(max(delta::text), 'null') from api.call_tone where company_slug = 'ec45-a'")" \
  '^1[|]null[|]null[|]null$'
check "U01 an unpublished call has no row" \
  "$(flip anon "$(POL ai.call_tone shown); update core.event set is_published = false where slug = 'ec45-call-ec45-b-q2fy27'" "select count(*) from api.call_tone where company_slug = 'ec45-b'")" '^0$'

check "S01 the view is security_invoker" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname = 'call_tone' and c.reloptions @> array['security_invoker=true']")" '^1$'
check "S02 anon, authenticated and service_role can select it" \
  "$(as_pg "select count(*) from unnest(array['anon','authenticated','service_role']) r where has_table_privilege(r, 'api.call_tone'::regclass, 'select')")" '^3$'
check "S03 no column of the view can hold transcript text (no text, quote or body column)" \
  "$(as_pg "select count(*) from information_schema.columns where table_schema = 'api' and table_name = 'call_tone' and column_name ~ '(text|quote|body)'")" '^0$'
check "S04 the view has a comment that says it is empty while the field is hidden" \
  "$(as_pg "select (obj_description('api.call_tone'::regclass) like '%Empty while field policy ai.call_tone is hidden%')::text")" '^true$'
check "S05 the field policy is still hidden (the flip is Anand's)" \
  "$(as_pg "select status from core.field_policy where field_key = 'ai.call_tone'")" '^hidden$'
check "S06 anon cannot read the speaker turns (no visitor policy)" \
  "$(as_role anon "select count(*) from equity.call_turn")" '^(0|ERROR:.*permission denied.*)$'

cleanup
echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
