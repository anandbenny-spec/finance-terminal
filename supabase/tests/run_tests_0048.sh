#!/bin/bash
# Tests for 0048 (guidance outcomes per target, hit rates behind their own key `equity.guidance_hit_rate`; issue #532, job 2B-15e;
# Anand's option A, 13 Nov 2026). Run after the other scripts on the same fresh database. The script makes its own rows (entities
# named gh48-*), checks through the roles and removes every row it made. Gate flips happen inside one transaction that is rolled
# back, so the policy rows are never left changed. Company A has 5 counted first statements scored (4 delivered, 1 missed: n = 5,
# rate 0.8), a later statement of its first target (raised), a pending one and a not-scored one; B has one delivered target. Both
# are Nifty 50 members in one peer group. The row-equality checks compare the views with their pre-0048 definitions
# (0048_old_views.sql), as postgres, in the same transaction.
HERE="$(cd "$(dirname "$0")" && pwd)"
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
# flip ROLE "setup statements" "query": run the setup as postgres, then the query as ROLE, in a transaction rolled back
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.guidance_outcome where target_key like 'gh48%';
         delete from equity.guidance where metric like 'gh48%';
         delete from core.ai_output where task = 'guidance_extraction' and body like 'gh48%';
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'gh48%');
         delete from core.chunk where document_id in (select id from core.document where external_key like 'gh48%');
         delete from core.document where external_key like 'gh48%';
         delete from core.event where slug like 'gh48%';
         delete from equity.index_membership where entity_id in (select id from core.entity where slug like 'gh48%');
         delete from equity.company where entity_id in (select id from core.entity where slug like 'gh48%');
         delete from equity.peer_group where key like 'gh48%';
         delete from core.raw_file where object_key like 'gh48%';
         delete from core.entity where slug like 'gh48%';
         update core.field_policy set status = 'hidden'
          where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.guidance_hit_rate');" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind

############################################################ the new key
check "F01 equity.guidance_hit_rate is seeded hidden, labelled derived, with its reason (no semicolon in it)" \
  "$(as_pg "select status || '|' || label || '|' || reason || '|' || (position(';' in reason) = 0) from core.field_policy where field_key = 'equity.guidance_hit_rate'")" \
  "^hidden[|]derived[|]Hit rates held back until the extraction's recall check passes \(Spec 02B 5.11, Anand 13 Nov 2026\)[|]true$"
check "F02 job_writer can't flip it (an editorial switch), and anon can't either" \
  "$(for r in job_writer anon; do as_role $r "update core.field_policy set status = 'shown' where field_key = 'equity.guidance_hit_rate'"; done | grep -c 'permission denied')
   $(as_pg "select status from core.field_policy where field_key = 'equity.guidance_hit_rate'")" \
  '^2[[:space:]]+hidden$'

############################################################ the rows the checks use
SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into equity.peer_group (key, market, label, damodaran_industry, source_note) values ('gh48-p', 'IN', 'Gh48 group', null, 'test');
insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 'gh48-raw', s.id, null, sha256('gh48'::bytea), 1, 'text/csv', now(), (select max(id) from ops.task_run)
from core.source s where s.key = 'nse';
do $$
declare co text; eid integer; evid bigint; did bigint; oid bigint; txt text := repeat('Margin came in at 27 percent this quarter. ', 15);
begin
  foreach co in array array['gh48-a', 'gh48-b'] loop
    insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
      values ('company', co, 'Gh48 ' || co || ' Ltd', co, 'IN', 'INR', true) returning id into eid;
    insert into equity.company (entity_id, isin, nse_symbol, bse_code, peer_group_key)
      values (eid, 'INE0000048' || lpad((ascii(right(co, 1)) - 96)::text, 2, '0'), upper(co), '5480', 'gh48-p');
    insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
      values ('earnings_call', eid, co || '-call-q2-fy27', co, '2026-10-20', 'stored', (select id from core.source where key = 'nse'), true)
      returning id into evid;
    insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                               char_count, text_sha256, chunked_at, is_published)
      values ('call_transcript', eid, evid, 'Gh48 call ' || co, (select id from core.source where key = 'company_web'),
              'gh48-' || co, 'https://example.com/gh48/' || co || '.pdf', now(),
              char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
    insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
      values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
    insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, call_date,
                                      call_date_source, filed_at, exchange, route, transcript_document_id, words)
      values (evid, eid, 'Q2 FY27', '2026-09-30', 'q2-fy27', 'results_link', '2026-10-20', 'text', '2026-10-21', 'NSE',
              'company_link', did, 105);
    insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
      values ('guidance_extraction', did, jsonb_build_object(), 'gh48 figures ' || co, 'draft', 'claude-haiku-5-5', 'guidance_extraction', 'v1',
              sha256(convert_to('gh48 ' || co, 'UTF8'))) returning id into oid;
    insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
      values (oid, 1, '/x', 'document_span', did, 0, 43, true);
    update core.ai_output set status = 'published' where id = oid;
  end loop;
end $$;
insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
select (select id from core.entity where slug = 'nifty-50'), '2026-11-02', e.id, (select id from core.raw_file where object_key = 'gh48-raw')
from core.entity e where e.slug in ('gh48-a', 'gh48-b');
create temp table gh48_v (co text, metric text, outcome text, counted boolean, is_first boolean, revision text, relation text,
                          reason text, tkey text);
insert into gh48_v values
  ('gh48-a', 'gh48_a1', 'delivered',  true,  true,  'first',  'within', null, 'gh48-a|a1'),
  ('gh48-a', 'gh48_a2', 'delivered',  true,  true,  'first',  'above',  null, 'gh48-a|a2'),
  ('gh48-a', 'gh48_a3', 'delivered',  true,  true,  'first',  'within', null, 'gh48-a|a3'),
  ('gh48-a', 'gh48_a4', 'delivered',  true,  true,  'first',  'within', null, 'gh48-a|a4'),
  ('gh48-a', 'gh48_a5', 'missed',     true,  true,  'first',  'below',  null, 'gh48-a|a5'),
  ('gh48-a', 'gh48_a6', 'delivered',  true,  false, 'raised', 'above',  null, 'gh48-a|a1'),
  ('gh48-a', 'gh48_a7', 'pending',    true,  true,  'first',  null,     null, 'gh48-a|a7'),
  ('gh48-a', 'gh48_a8', 'not_scored', true,  true,  'first',  null,     'vague_figure', 'gh48-a|a8'),
  ('gh48-b', 'gh48_b1', 'delivered',  true,  true,  'first',  'within', null, 'gh48-b|b1');
insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date, not_scored_reason, speaker_role)
select ec.event_id, ec.entity_id, o.id, 1, ec.transcript_document_id, 0, 43, v.metric, 'Label ' || v.metric, v.metric,
       'point', 10, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', '2026-10-20',
       case when v.outcome = 'not_scored' then v.reason end, 'management'
from gh48_v v join core.entity e on e.slug = v.co join equity.earnings_call ec on ec.entity_id = e.id
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, horizon_share, counted, outcome, relation, actual,
                                     base, reason, inputs, rule_version)
select g.id, v.tkey, v.is_first, v.revision, 0.1, v.counted, v.outcome, v.relation,
       case when v.outcome in ('delivered','missed') then 11 end, case when v.outcome in ('delivered','missed') then 9 end,
       case when v.outcome = 'not_scored' then v.reason end, jsonb_build_object('rows', jsonb_build_array()), 'r1'
from gh48_v v join equity.guidance g on g.metric = v.metric;
commit;
SQL
)"
check "G01 the test rows load (2 companies, 9 targets, 9 outcomes)" \
  "$SETUP_OUT" '^(BEGIN|CREATE TABLE|INSERT 0 [0-9]+|UPDATE [0-9]+|DO|COMMIT|[[:space:]])*$'

GH="company_slug like 'gh48-%'"
EXT="$(POL ai.guidance_extraction labelled)"
OUTONLY="$EXT; $(POL equity.guidance_outcome labelled)"
BOTH="$OUTONLY; $(POL equity.guidance_hit_rate labelled)"
# the six columns that wait for the new key, counted non-null
SIX="count(is_first) + count(revision) + count(horizon_share) + count(counted) + count(first_call_slug) + count(first_stated_on)"

############################################################ outcome shown, hit rate hidden
for st in shown labelled; do
  check "O01 equity.guidance_outcome $st, hit rate hidden: every target's outcome, relation, actual, base and reason as stored; the six dependent columns null; no hit-rate rows" \
    "$(flip anon "$EXT; $(POL equity.guidance_outcome $st)" \
       "select count(*) || ',' || count(outcome) || ',' || count(relation) || ',' || count(actual) || ',' || count(base) || ',' || count(reason)
               || ',' || ($SIX) || ',' || max(outcome_label) || ',' || (select count(*) from api.guidance_hit_rates)
        from api.call_guidance where $GH")" \
    '^9,9,7,7,7,1,0,derived,0$'
done
check "O02 the outcomes read as scored: a1 delivered within with its actual and base, a5 missed below, a7 pending, a8 not scored with its reason" \
  "$(flip anon "$OUTONLY" "select string_agg(metric || ':' || outcome || ':' || coalesce(relation, '-') || ':' || coalesce(actual::text, '-') || ':' || coalesce(base::text, '-') || ':' || coalesce(reason, '-'), ' ' order by metric)
                           from api.call_guidance where metric in ('gh48_a1','gh48_a5','gh48_a7','gh48_a8')")" \
  '^gh48_a1:delivered:within:11[.0]*:9[.0]*:- gh48_a5:missed:below:11[.0]*:9[.0]*:- gh48_a7:pending:-:-:-:- gh48_a8:not_scored:-:-:-:vague_figure$'
check "O03 the later statement shows its outcome but no revision label, no first statement and nothing on the rate" \
  "$(flip anon "$OUTONLY" "select outcome || ',' || coalesce(revision, 'null') || ',' || coalesce(is_first::text, 'null') || ',' || coalesce(counted::text, 'null') || ',' || coalesce(first_call_slug, 'null') || ',' || coalesce(first_stated_on::text, 'null') || ',' || coalesce(horizon_share::text, 'null')
                           from api.call_guidance where metric = 'gh48_a6'")" \
  '^delivered,null,null,null,null,null,null$'
check "O04 no company, peer-group or Nifty 50 rate row while the hit rate is hidden, as anon and as authenticated" \
  "$(for r in anon authenticated; do flip $r "$OUTONLY" "select count(*) from api.guidance_hit_rates"; done | tr '\n' ' ')" \
  '^0 0 $'
check "O05 the extraction label and the target as stated are unchanged" \
  "$(flip anon "$OUTONLY" "select extraction_label || ',' || kind || ',' || value || ',' || unit || ',' || period_text || ',' || fiscal_label || ',' || call_slug from api.call_guidance where metric = 'gh48_a1'")" \
  '^AI-extracted,point,10,pct,FY27,Q2 FY27,gh48-a-call-q2-fy27$'

############################################################ both keys shown
for st in shown labelled; do
  check "B01 both keys $st: the six columns and the rates come back (A n = 5, rate 0.8; the raised statement with its first call and date)" \
    "$(flip anon "$EXT; $(POL equity.guidance_outcome $st); $(POL equity.guidance_hit_rate $st)" \
       "select (select ($SIX) from api.call_guidance where $GH) || ',' ||
               (select delivered || ':' || missed || ':' || n || ':' || rate::numeric(4,2) from api.guidance_hit_rates where scope = 'company' and scope_key = 'gh48-a') || ',' ||
               (select revision || ':' || is_first || ':' || first_call_slug || ':' || first_stated_on from api.call_guidance where metric = 'gh48_a6')")" \
    '^54,4:1:5:0\.80,raised:false:gh48-a-call-q2-fy27:2026-10-20$'
done

############################################################ the new key alone
check "N01 the hit-rate key alone (outcome hidden): no outcome column, none of the six and no rates" \
  "$(flip anon "$EXT; $(POL equity.guidance_hit_rate labelled)" \
     "select count(*) || ',' || (count(outcome) + count(relation) + count(actual) + count(base) + count(reason) + count(outcome_label) + $SIX) || ',' || (select count(*) from api.guidance_hit_rates)
      from api.call_guidance where $GH")" \
  '^9,0,0$'
check "N02 the hit-rate and outcome keys with the extraction hidden: no rows at all" \
  "$(flip anon "$(POL equity.guidance_outcome labelled); $(POL equity.guidance_hit_rate labelled)" \
     "select (select count(*) from api.call_guidance where $GH) || ',' || (select count(*) from api.guidance_hit_rates)")" \
  '^0,0$'

############################################################ against the definitions before 0048 (as postgres, one transaction)
# EQ <setup sql> <old call_guidance select list>: the rows each side has that the other lacks (both views, both directions), then
# the rows of each new view for the test companies. Prints "diff:<n>|<call_guidance rows>|<hit-rate rows>".
EQ() {
  local out
  out="$({ echo "begin; $1;"; cat "$HERE/0048_old_views.sql"; cat <<SQL
create temp view old_cg as select $2 from old_call_guidance;
select 'diff:' ||
  ((select count(*) from (select * from api.call_guidance except all select * from old_cg) a)
 + (select count(*) from (select * from old_cg except all select * from api.call_guidance) a)
 + (select count(*) from (select * from api.guidance_hit_rates except all select * from $3) a)
 + (select count(*) from (select * from $3 except all select * from api.guidance_hit_rates) a))
 || '|' || (select count(*) from api.call_guidance where $GH)
 || '|' || (select count(*) from api.guidance_hit_rates where scope_key like 'gh48-%');
rollback;
SQL
  } | psql $H -U postgres -v ON_ERROR_STOP=1 2>&1)"
  echo "$out" | grep '^diff:' || echo "$out" | tr '\n' ' ' | cut -c1-400
}
ALL="*"
# the old view with the six dependent columns nulled: what the new one must equal while the hit rate is hidden
NULLED="company_slug, company_name, short_name, market, currency, peer_group_key, peer_group_label, call_slug, fiscal_label, call_date,
        metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end,
        basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label,
        null::boolean as is_first, null::text as revision, null::numeric as horizon_share, null::boolean as counted,
        outcome, relation, actual, base, reason, null::text as first_call_slug, null::date as first_stated_on"
NO_RATES="(select * from old_guidance_hit_rates where false) z"
check "E01 both keys labelled: both views return exactly the rows the old definitions return (9 targets; A, B and their group's rate rows)" \
  "$(EQ "$BOTH" "$ALL" "old_guidance_hit_rates")" '^diff:0[|]9[|]3$'
check "E02 both keys shown, peer group hidden: the same rows (no group row)" \
  "$(EQ "$EXT; $(POL equity.guidance_outcome shown); $(POL equity.guidance_hit_rate shown); $(POL equity.peer_group hidden)" "$ALL" "old_guidance_hit_rates")" '^diff:0[|]9[|]2$'
check "E03 outcome labelled, hit rate hidden: call_guidance is the old one with only the six columns nulled; no rate rows" \
  "$(EQ "$OUTONLY" "$NULLED" "$NO_RATES")" '^diff:0[|]9[|]0$'
check "E04 everything hidden but the extraction: the same rows as before (outcome columns null, no rates)" \
  "$(EQ "$EXT" "$ALL" "old_guidance_hit_rates")" '^diff:0[|]9[|]0$'
check "E05 the hit-rate key alone: the same rows as before with the outcome hidden" \
  "$(EQ "$EXT; $(POL equity.guidance_hit_rate labelled)" "$ALL" "old_guidance_hit_rates")" '^diff:0[|]9[|]0$'
check "E06 both keys labelled, nse suppressed: the same rows (the calls gone)" \
  "$(EQ "$BOTH; update core.source set is_suppressed = true where key = 'nse'" "$ALL" "old_guidance_hit_rates")" '^diff:0[|][0-9]+[|][0-9]+$'
check "E07 outcome labelled, hit rate hidden, against the old definitions unchanged: they differ (the check can tell the two apart)" \
  "$(EQ "$OUTONLY" "$ALL" "old_guidance_hit_rates")" '^diff:[1-9][0-9]*[|]9[|]0$'

############################################################ shape, grants, comments
check "S01 both views: the same column names and types in the same order as the old definitions" \
  "$(psql $H -U postgres -c "begin; $(cat "$HERE/0048_old_views.sql")
       select (select string_agg(attname || ':' || format_type(atttypid, atttypmod), ',' order by attnum) from pg_attribute where attrelid = 'api.call_guidance'::regclass and attnum > 0 and not attisdropped)
            = (select string_agg(attname || ':' || format_type(atttypid, atttypmod), ',' order by attnum) from pg_attribute where attrelid = 'old_call_guidance'::regclass and attnum > 0 and not attisdropped),
              (select string_agg(attname || ':' || format_type(atttypid, atttypmod), ',' order by attnum) from pg_attribute where attrelid = 'api.guidance_hit_rates'::regclass and attnum > 0 and not attisdropped)
            = (select string_agg(attname || ':' || format_type(atttypid, atttypmod), ',' order by attnum) from pg_attribute where attrelid = 'old_guidance_hit_rates'::regclass and attnum > 0 and not attisdropped);
       rollback" 2>&1 | grep -E '^[tf]\|[tf]$')" \
  '^t[|]t$'
check "S02 both views are still security_invoker, and anon, authenticated and service_role can still select both" \
  "$(as_pg "select (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname in ('call_guidance','guidance_hit_rates') and c.reloptions @> array['security_invoker=true'])
                || ',' || (select count(*) from unnest(array['api.call_guidance','api.guidance_hit_rates']) v, unnest(array['anon','authenticated','service_role']) r where has_table_privilege(r, v::regclass, 'select'))")" \
  '^2,6$'
check "S03 the comments name the new key" \
  "$(as_pg "select (obj_description('api.call_guidance'::regclass) like '%also wait for equity.guidance_hit_rate%Never transcript text.%')::text
                || (obj_description('api.guidance_hit_rates'::regclass) like 'Delivered and missed counts%Empty while equity.guidance_hit_rate or equity.guidance_outcome is hidden%')::text")" \
  '^truetrue$'
check "S04 no transcript text through either view with both keys shown" \
  "$(flip anon "$BOTH" "select (select count(*) from api.call_guidance t where row_to_json(t)::text like '%Margin came in%') || ',' ||
                               (select count(*) from api.guidance_hit_rates t where row_to_json(t)::text like '%Margin came in%')")" \
  '^0,0$'

cleanup
check "C01 cleanup leaves no gh48 row and the three keys hidden" \
  "$(as_pg "select (select count(*) from core.entity where slug like 'gh48%') || ',' || (select count(*) from equity.guidance where metric like 'gh48%') || ',' ||
                   (select string_agg(status, ',' order by field_key) from core.field_policy where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.guidance_hit_rate'))")" \
  '^0,0,hidden,hidden,hidden$'
echo; echo "passed $pass, failed $fail"
