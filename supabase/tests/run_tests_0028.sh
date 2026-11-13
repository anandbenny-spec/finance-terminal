#!/bin/bash
# Tests for 0028 (guidance speaker role; issue #280, Spec 02B.1 C4, option C). Run after the other scripts on the same fresh
# database. The script creates its own rows (entity ec28-a, one call, one management and one unknown target), checks through
# the roles and removes every row it made. Gate flips happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.guidance_outcome; delete from equity.guidance where metric like 'ec28%';
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec28%');
         delete from core.ai_output where task = 'guidance_extraction';
         delete from core.chunk where document_id in (select id from core.document where external_key like 'ec28%');
         delete from core.document where external_key like 'ec28%';
         delete from core.event where slug like 'ec28%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'ec28%');
         delete from core.entity where slug like 'ec28%';
         update core.field_policy set status = 'hidden' where field_key in ('ai.guidance_extraction','equity.guidance_outcome');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
do $$
declare eid integer; evid bigint; did bigint; oid bigint; txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
begin
  insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
    values ('company', 'ec28-a', 'Ec28 Ltd', 'ec28-a', 'IN', 'INR', true) returning id into eid;
  insert into equity.company (entity_id, isin, nse_symbol, bse_code) values (eid, 'INE000002801', 'EC28A', '5280');
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec28-a-call-q2-fy27', 'ec28', '2026-10-20', 'stored',
            (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'Ec28 call', (select id from core.source where key = 'company_web'),
            'ec28-a', 'https://example.com/ec28/a.pdf', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
    values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, call_date,
                                    call_date_source, filed_at, exchange, route, transcript_document_id, words)
    values (evid, eid, 'Q2 FY27', '2026-09-30', 'q2-fy27', 'results_link', '2026-10-20', 'text', '2026-10-21', 'NSE',
            'company_link', did, 105);
  insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
    values ('guidance_extraction', did, jsonb_build_object(), 'ec28 figures', 'draft', 'claude-haiku-5-5', 'guidance_extraction', 'v1',
            sha256(convert_to('ec28', 'UTF8'))) returning id into oid;
  insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
    values (oid, 1, '/x', 'document_span', did, 0, 43, true);
  update core.ai_output set status = 'published' where id = oid;
end $$;
insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date, speaker_role)
select ec.event_id, ec.entity_id, o.id, 1, ec.transcript_document_id, 0, 43, v.metric, 'Label ' || v.metric, v.metric,
       'point', 10, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', '2026-10-20', v.role
from (values ('ec28_m', 'management'), ('ec28_u', 'unknown')) v(metric, role)
join core.entity e on e.slug = 'ec28-a' join equity.earnings_call ec on ec.entity_id = e.id
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
commit;
SQL
)"
check "G01 the test rows load (one management and one unknown target)" \
  "$SETUP_OUT" '^(BEGIN|INSERT 0 [0-9]+|UPDATE [0-9]+|DO|COMMIT|[[:space:]])*$'

INS="insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric, metric_text, kind, value, unit, period_kind, target_start, target_end, vintage_date"
SEL="select call_event_id, entity_id, ai_output_id, 2, document_id, 0, 43, 'ec28_x', 'x', 'point', 10, 'pct', 'year', '2026-04-01', '2027-03-31', '2026-10-20'"
check "S01 an insert without speaker_role fails (not null, no default), and one with 'analyst' fails (check)" \
  "$(as_pg "$INS) $SEL from equity.guidance where metric = 'ec28_m'" | tr '\n' ' ')
   $(as_pg "$INS, speaker_role) $SEL, 'analyst' from equity.guidance where metric = 'ec28_m'" | tr '\n' ' ')" \
  'null value in column "speaker_role".*guidance_speaker_role_check'
check "S02 an unknown row with forecast_series_id set fails (the check)" \
  "$(as_pg "update equity.guidance set forecast_series_id = (select min(id) from core.series) where metric = 'ec28_u'")" \
  'guidance_forecast_management'
check "S03 a management row may carry a forecast series" \
  "$(as_pg "begin; with u as (update equity.guidance set forecast_series_id = (select min(id) from core.series) where metric = 'ec28_m' returning 1) select count(*) from u; rollback" | tr '\n' ' ')" \
  '^1 '
OI="insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, outcome, counted, inputs, rule_version"
check "S04 an outcome row for an unknown guidance row fails (the foreign key)" \
  "$(as_pg "$OI) select id, 'ec28|u', true, 'first', 'delivered', true, jsonb_build_object(), 'r1' from equity.guidance where metric = 'ec28_u'")" \
  'guidance_outcome_management_only'
check "S05 an outcome row for a management row is accepted" \
  "$(as_pg "with i as ($OI) select id, 'ec28|m', true, 'first', 'delivered', true, jsonb_build_object(), 'r1' from equity.guidance where metric = 'ec28_m' returning 1) select count(*) from i")" \
  '^1$'
check "S06 an outcome with speaker_role = 'unknown' fails (the check)" \
  "$(as_pg "$OI, speaker_role) select id, 'ec28|u2', true, 'first', 'delivered', true, jsonb_build_object(), 'r1', 'unknown' from equity.guidance where metric = 'ec28_m'")" \
  'guidance_outcome_speaker_role_check'

# 0048 (#532): the hit rates also wait for equity.guidance_hit_rate, so "both released" flips that key too (setup only; no
# assertion changed).
BOTH="$(POL ai.guidance_extraction labelled); $(POL equity.guidance_outcome labelled); $(POL equity.guidance_hit_rate labelled)"
check "V01 api.call_guidance returns the management row and not the unknown row of the same call" \
  "$(flip anon "$BOTH" "select count(*) filter (where metric = 'ec28_m') || ',' || count(*) filter (where metric = 'ec28_u') from api.call_guidance where company_slug = 'ec28-a'")" \
  '^1,0$'
check "V02 api.guidance_hit_rates' company row counts only the management outcome" \
  "$(flip anon "$BOTH" "select delivered || ',' || missed || ',' || n from api.guidance_hit_rates where scope = 'company' and scope_key = 'ec28-a'")" \
  '^1,0,1$'
check "V03 anon still reads the unknown row in equity.guidance (its policy is unchanged)" \
  "$(flip anon "$BOTH" "select count(*) filter (where speaker_role = 'unknown') || ',' || count(*) filter (where speaker_role = 'management') from equity.guidance where metric like 'ec28%'")" \
  '^1,1$'

cleanup
echo; echo "passed $pass, failed $fail"
