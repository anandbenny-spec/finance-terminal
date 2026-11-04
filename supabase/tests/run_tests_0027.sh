#!/bin/bash
# Tests for 0027 (guidance outcomes; issue #266, Spec 02B 3.3, 3.5, 5.10, 7). Run after the other scripts on the same fresh
# database. The script creates its own rows (entities named ec27-*), checks through the roles and removes every row it made.
# Gate flips happen inside one transaction that is rolled back, so the policy rows are never left changed.
# Company A has 8 targets (3 scored and counted, a pending, a not-scored, a not-counted one, a duplicate and a later
# statement), B has 4 counted ones (then a fifth), and C's call is unpublished.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
# flip ROLE "setup statements" "query": run the setup as postgres, then the query as ROLE, in a transaction rolled back
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.guidance_outcome; update equity.guidance set duplicate_of = null where metric like 'ec27%';
         delete from equity.guidance where metric like 'ec27%';
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec27%');
         delete from core.ai_output where task = 'guidance_extraction';
         delete from core.chunk where document_id in (select id from core.document where external_key like 'ec27%');
         delete from core.document where external_key like 'ec27%';
         delete from core.event where slug like 'ec27%';
         delete from equity.index_membership where entity_id in (select id from core.entity where slug like 'ec27%');
         delete from equity.company where entity_id in (select id from core.entity where slug like 'ec27%');
         delete from equity.peer_group where key like 'ec27%';
         delete from core.raw_file where object_key like 'ec27%';
         delete from core.entity where slug like 'ec27%';
         update core.field_policy set status = 'hidden' where field_key in ('ai.guidance_extraction','equity.guidance_outcome');
         update core.source set is_suppressed = false where key in ('nse','company_web');" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind

check "G01 the field policy is seeded hidden, labelled derived" \
  "$(as_pg "select status||':'||label from core.field_policy where field_key = 'equity.guidance_outcome'")" '^hidden:derived$'

############################################################ the rows the checks use
SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into equity.peer_group (key, market, label, damodaran_industry, source_note) values ('ec27-p', 'IN', 'Ec27 group', null, 'test');
insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 'ec27-raw', s.id, null, sha256('ec27'::bytea), 1, 'text/csv', now(), (select max(id) from ops.task_run)
from core.source s where s.key = 'nse';
do $$
declare co record; eid integer; evid bigint; did bigint; oid bigint; txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
begin
  for co in select * from (values ('ec27-a', true), ('ec27-b', true), ('ec27-c', false)) v(slug, pub) loop
    insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
      values ('company', co.slug, 'Ec27 ' || co.slug || ' Ltd', co.slug, 'IN', 'INR', true) returning id into eid;
    insert into equity.company (entity_id, isin, nse_symbol, bse_code, peer_group_key)
      values (eid, 'INE0000027' || lpad((ascii(right(co.slug, 1)) - 96)::text, 2, '0'), upper(co.slug), '5270', 'ec27-p');
    insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
      values ('earnings_call', eid, co.slug || '-call-q2-fy27', co.slug, '2026-10-20', 'stored',
              (select id from core.source where key = 'nse'), co.pub) returning id into evid;
    insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                               char_count, text_sha256, chunked_at, is_published)
      values ('call_transcript', eid, evid, 'Ec27 call ' || co.slug, (select id from core.source where key = 'company_web'),
              'ec27-' || co.slug, 'https://example.com/ec27/' || co.slug || '.pdf', now(),
              char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
    insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
      values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
    insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, call_date,
                                      call_date_source, filed_at, exchange, route, transcript_document_id, words)
      values (evid, eid, 'Q2 FY27', '2026-09-30', 'q2-fy27', 'results_link', '2026-10-20', 'text', '2026-10-21', 'NSE',
              'company_link', did, 105);
    insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
      values ('guidance_extraction', did, jsonb_build_object(), 'ec27 figures ' || co.slug, 'draft', 'claude-haiku-5-5', 'guidance_extraction', 'v1',
              sha256(convert_to('ec27 ' || co.slug, 'UTF8'))) returning id into oid;
    insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
      values (oid, 1, '/x', 'document_span', did, 0, 43, true);
    update core.ai_output set status = 'published' where id = oid;
  end loop;
end $$;
insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
select (select id from core.entity where slug = 'nifty-50'), '2026-11-01', e.id, (select id from core.raw_file where object_key = 'ec27-raw')
from core.entity e where e.slug in ('ec27-a', 'ec27-b');
create temp table ec27_v (co text, metric text, outcome text, counted boolean, is_first boolean, revision text, relation text,
                          reason text, tkey text);
insert into ec27_v values
  ('ec27-a', 'ec27_a1', 'delivered',  true,  true,  'first',  'within',  null, 'ec27-a|a1'),
  ('ec27-a', 'ec27_a2', 'delivered',  true,  true,  'first',  'above',   null, 'ec27-a|a2'),
  ('ec27-a', 'ec27_a3', 'missed',     true,  true,  'first',  'below',   null, 'ec27-a|a3'),
  ('ec27-a', 'ec27_a4', 'pending',    true,  true,  'first',  null,      null, 'ec27-a|a4'),
  ('ec27-a', 'ec27_a5', 'not_scored', true,  true,  'first',  null,      'vague_figure', 'ec27-a|a5'),
  ('ec27-a', 'ec27_a6', 'delivered',  false, true,  'first',  'within',  null, 'ec27-a|a6'),
  ('ec27-a', 'ec27_a7', 'delivered',  true,  true,  'first',  'within',  null, 'ec27-a|a1'),
  ('ec27-a', 'ec27_a8', 'delivered',  true,  false, 'raised', 'above',   null, 'ec27-a|a1'),
  ('ec27-b', 'ec27_b1', 'delivered',  true,  true,  'first',  'within',  null, 'ec27-b|b1'),
  ('ec27-b', 'ec27_b2', 'delivered',  true,  true,  'first',  'within',  null, 'ec27-b|b2'),
  ('ec27-b', 'ec27_b3', 'delivered',  true,  true,  'first',  'above',   null, 'ec27-b|b3'),
  ('ec27-b', 'ec27_b4', 'missed',     true,  true,  'first',  'below',   null, 'ec27-b|b4'),
  ('ec27-c', 'ec27_c1', 'delivered',  true,  true,  'first',  'within',  null, 'ec27-c|c1');
insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date, not_scored_reason, speaker_role)
select ec.event_id, ec.entity_id, o.id, 1, ec.transcript_document_id, 0, 43, v.metric, 'Label ' || v.metric, v.metric,
       'point', 10, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', '2026-10-20',
       case when v.outcome = 'not_scored' then v.reason end, 'management'
from ec27_v v join core.entity e on e.slug = v.co join equity.earnings_call ec on ec.entity_id = e.id
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
update equity.guidance set duplicate_of = (select id from equity.guidance where metric = 'ec27_a1') where metric = 'ec27_a7';
insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, horizon_share, counted, outcome, relation, actual,
                                     reason, inputs, rule_version)
select g.id, v.tkey, v.is_first, v.revision, case when v.counted then 0.1 else 0.6 end, v.counted, v.outcome, v.relation,
       case when v.outcome in ('delivered','missed') then 11 end, case when v.outcome = 'not_scored' then v.reason end,
       jsonb_build_object('rows', jsonb_build_array()), 'r1'
from ec27_v v join equity.guidance g on g.metric = v.metric;
commit;
SQL
)"
check "G02 the test rows load (guidance, outcomes, calls, an unpublished call)" \
  "$SETUP_OUT" '^(BEGIN|CREATE TABLE|INSERT 0 [0-9]+|UPDATE [0-9]+|DO|COMMIT|[[:space:]])*$'

CG="select count(*) from api.call_guidance where company_slug like 'ec27-%'"
BOTH="$(POL ai.guidance_extraction labelled); $(POL equity.guidance_outcome labelled)"

############################################################ the table
check "T01 RLS is on for equity.guidance_outcome, with job_writer_all and read_published" \
  "$(as_pg "select c.relrowsecurity || ',' || (select string_agg(policyname, ',' order by policyname) from pg_policies where schemaname = 'equity' and tablename = 'guidance_outcome') from pg_class c where c.oid = 'equity.guidance_outcome'::regclass")" \
  '^true,job_writer_all,read_published$'
check "T02 field policies hidden: anon sees no outcome row (the guidance's output is hidden too)" \
  "$(as_role anon "select count(*) from equity.guidance_outcome")" '^0$'
check "T03 with both released, anon sees the published calls' rows only: A's 8 and B's 4, not C's (unpublished call)" \
  "$(flip anon "$BOTH" "select count(*) filter (where target_key like 'ec27-a%') || ',' || count(*) filter (where target_key like 'ec27-b%') || ',' || count(*) filter (where target_key like 'ec27-c%') from equity.guidance_outcome")" \
  '^8,4,0$'
check "T04 suppressing nse hides every row at once (table and both views)" \
  "$(flip anon "$BOTH; update core.source set is_suppressed = true where key = 'nse'" \
     "select (select count(*) from equity.guidance_outcome) || ',' || ($CG) || ',' || (select count(*) from api.guidance_hit_rates)")" \
  '^0,0,0$'
check "T05 suppressing company_web (the transcript's source) hides every row too" \
  "$(flip anon "$BOTH; update core.source set is_suppressed = true where key = 'company_web'" \
     "select (select count(*) from equity.guidance_outcome) || ',' || ($CG) || ',' || (select count(*) from api.guidance_hit_rates)")" \
  '^0,0,0$'
check "T06 revision, outcome and relation are checked; inputs is required" \
  "$(for s in "revision = 'bad'" "outcome = 'bad'" "relation = 'bad'" "inputs = null"; do as_role job_writer "update equity.guidance_outcome set $s"; done)" \
  'guidance_outcome_revision_check.*guidance_outcome_outcome_check.*guidance_outcome_relation_check.*null value in column "inputs"'
check "T07 job_writer may delete outcome rows (the D table); it can't change the field policy" \
  "$(as_pg "begin; set role job_writer; with x as (delete from equity.guidance_outcome returning 1) select count(*) from x; rollback;" | tr '\n' ' ')
   $(as_role job_writer "update core.field_policy set status = 'shown' where field_key = 'equity.guidance_outcome'")" \
  '^13 .*permission denied for table field_policy'
check "T08 visitors and the semantic layer can read the table's grants but none can write it" \
  "$(for r in anon authenticated semantic_owner; do as_role $r "insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, outcome, inputs, rule_version) values (1, 'x', true, 'first', 'pending', jsonb_build_object(), 'r')"; done | grep -c 'permission denied')" \
  '^3$'
check "T09 both views are security_invoker" \
  "$(as_pg "select count(*) from pg_class where relnamespace = 'api'::regnamespace and relname in ('call_guidance','guidance_hit_rates') and 'security_invoker=true' = any(reloptions)")" '^2$'

############################################################ api.call_guidance
check "V01 both hidden: no rows in either view" \
  "$(as_role anon "select ($CG) || ',' || (select count(*) from api.guidance_hit_rates)")" '^0,0$'
for st in shown labelled; do
  check "V02 ai.guidance_extraction $st, outcomes hidden: 11 targets (the duplicate and C's unpublished call left out), every outcome column null, no hit rates" \
    "$(flip anon "$(POL ai.guidance_extraction $st)" \
       "select count(*) || ',' || (count(outcome) + count(counted) + count(is_first) + count(revision) + count(relation) + count(actual) + count(horizon_share) + count(first_call_slug) + count(outcome_label)) from api.call_guidance where company_slug like 'ec27-%'
        union all select count(*)::text from api.guidance_hit_rates")" \
    '^11,0[[:space:]]+0$'
done
check "V03 ai.guidance_extraction hidden, outcomes released: no rows" \
  "$(flip anon "$(POL equity.guidance_outcome labelled)" "select ($CG) || ',' || (select count(*) from api.guidance_hit_rates)")" '^0,0$'
for st in shown labelled; do
  check "V04 equity.guidance_outcome $st, extraction labelled: all 11 targets carry an outcome, with the labels" \
    "$(flip anon "$(POL ai.guidance_extraction labelled); $(POL equity.guidance_outcome $st)" \
       "select count(*) || ',' || count(outcome) || ',' || max(outcome_label) || ',' || max(extraction_label) from api.call_guidance where company_slug like 'ec27-%'")" \
    '^11,11,derived,AI-extracted$'
done
check "V05 the duplicate is left out; the later statement shows as raised, with its first call and date" \
  "$(flip anon "$BOTH" "select (select count(*) from api.call_guidance where metric = 'ec27_a7') || ',' || revision || ',' || first_call_slug || ',' || first_stated_on from api.call_guidance where metric = 'ec27_a8'")" \
  '^0,raised,ec27-a-call-q2-fy27,2026-10-20$'
check "V06 a not-scored target shows its reason (from the extraction) and its outcome, with no actual" \
  "$(flip anon "$BOTH" "select not_scored_reason || ',' || outcome || ',' || coalesce(actual::text, 'null') from api.call_guidance where metric = 'ec27_a5'")" \
  '^vague_figure,not_scored,null$'
check "V07 the target as stated, the citation pointer, the company's own currency and the call" \
  "$(flip anon "$BOTH" "select kind || ',' || value || ',' || unit || ',' || period_text || ',' || target_end || ',' || basis || ',' || citation_no || ',' || (ai_output_id > 0) || ',' || currency || ',' || fiscal_label || ',' || call_date || ',' || metric_label from api.call_guidance where metric = 'ec27_a1'")" \
  '^point,10,pct,FY27,2027-03-31,consolidated,1,true,INR,Q2 FY27,2026-10-20,Label ec27_a1$'
check "V08 anon reads no transcript text through either view, or through the chunk table" \
  "$(flip anon "$BOTH" "select (select count(*) from api.call_guidance t where row_to_json(t)::text like '%Revenue grew%') || ',' ||
                               (select count(*) from api.guidance_hit_rates t where row_to_json(t)::text like '%Revenue grew%') || ',' ||
                               (select count(*) from core.chunk where body like '%Revenue grew%')")" \
  '^0,0,0$'

############################################################ api.guidance_hit_rates
check "R01 outcomes released: A counts 2 delivered and 1 missed (n = 3, no rate): the pending, not-scored, not-counted, duplicate and later targets stay out" \
  "$(flip anon "$BOTH" "select delivered || ',' || missed || ',' || n || ',' || coalesce(rate::text, 'null') from api.guidance_hit_rates where scope = 'company' and scope_key = 'ec27-a'")" \
  '^2,1,3,null$'
check "R02 B at n = 4 has no rate; C (unpublished call) has no row" \
  "$(flip anon "$BOTH" "select (select delivered || ',' || missed || ',' || n || ',' || coalesce(rate::text, 'null') from api.guidance_hit_rates where scope_key = 'ec27-b') || ',' ||
                               (select count(*) from api.guidance_hit_rates where scope_key = 'ec27-c')")" \
  '^3,1,4,null,0$'
check "R03 a fifth target for B (written by job_writer) gives n = 5 and a rate of 0.8; the group and the Nifty 50 follow" \
  "$(psql $H -U postgres -c "set role job_writer;
       with g as (insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                                               metric_text, kind, value, unit, period_kind, target_start, target_end, vintage_date, speaker_role)
                  select ec.event_id, ec.entity_id, o.id, 1, ec.transcript_document_id, 0, 43, 'ec27_b5', 'x', 'point', 10, 'pct', 'year',
                         '2026-04-01', '2027-03-31', '2026-10-20', 'management'
                  from equity.earnings_call ec join core.entity e on e.id = ec.entity_id and e.slug = 'ec27-b'
                  join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction' returning id)
       insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, horizon_share, counted, outcome, relation, actual, inputs, rule_version)
       select id, 'ec27-b|b5', true, 'first', 0.1, true, 'delivered', 'within', 11, jsonb_build_object(), 'r1' from g" 2>&1 | grep -v '^SET$'
     flip anon "$BOTH" "select scope || ':' || scope_key || ':' || delivered || ':' || missed || ':' || n || ':' || rate::numeric(4,2) || ':' || first_stated_from || ':' || periods_to
                        from api.guidance_hit_rates where scope_key in ('ec27-b','ec27-p','nifty-50') order by scope")" \
  '^company:ec27-b:4:1:5:0\.80:2026-10-20:2027-03-31.*nifty50:nifty-50:6:2:8:0\.75:2026-10-20:2027-03-31.*peer_group:ec27-p:6:2:8:0\.75:2026-10-20:2027-03-31'
check "R04 outcomes hidden: no rate rows at all, whatever the extraction's state" \
  "$(flip anon "$(POL ai.guidance_extraction shown)" "select count(*) from api.guidance_hit_rates")" '^0$'
check "R05 peer-group rows need the peer-group field policy" \
  "$(flip anon "$BOTH; $(POL equity.peer_group hidden)" "select count(*) filter (where scope = 'peer_group') || ',' || count(*) filter (where scope = 'nifty50') from api.guidance_hit_rates")" \
  '^0,1$'

cleanup
echo; echo "passed $pass, failed $fail"
