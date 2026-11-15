#!/bin/bash
# Tests for 0052 (cmdbar_semantic: the command bar's call views, the semantic comments and the two settings; issue #579, job K-03,
# Spec 07 §5). Run after the other scripts on the same fresh database. The script makes its own rows (entities named k52-*),
# reads them through the roles and removes every row it made. Field-policy flips happen inside one transaction that is rolled
# back, so the policy rows are never left changed.
# The rows: company A (India, published) has five calls: Q1 and Q2 FY27 (published, transcripts stored, a tone score each, Q2's
# against Q1), Q4 FY26 (published, its transcript only on the company's website), Q3 FY27 (its event unpublished) and Q4 FY27
# (its transcript unpublished). Q2 FY27 has a published guidance output with four targets: a point (scored delivered), a range
# (pending), one whose speaker's role couldn't be read and a duplicate of the point. Company B (India) is unpublished, with one
# call. Company U (US, USD, published) has one call with one target. Only A's Q1, Q2 and Q4 FY26 calls, and A's first two targets,
# may reach the semantic views.
HERE="$(cd "$(dirname "$0")" && pwd)"
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
login()   { psql $H -U "$1" -c "$2" 2>&1; }                     # a real login as the role (sql_reader never uses SET ROLE live)
# flip ROLE "setup statements" "query": run the setup as postgres, then the query as ROLE, in a transaction rolled back
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.guidance_outcome where target_key like 'k52%';
         delete from equity.guidance where metric like 'k52%';
         delete from equity.call_tone where rule_version = 'k52';
         delete from core.ai_output where task = 'guidance_extraction' and body like 'k52%';
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'k52%');
         delete from core.chunk where document_id in (select id from core.document where external_key like 'k52%');
         delete from core.document where external_key like 'k52%';
         delete from core.event where slug like 'k52%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'k52%');
         delete from core.entity where slug like 'k52%';
         update core.field_policy set status = 'hidden'
          where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.guidance_hit_rate','ai.call_tone');" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind

############################################################ the migration's settings and catalogue (N1)
check "S01 the kill switch is off: cmdbar_ai_enabled is the JSON boolean false" \
  "$(as_pg "select jsonb_typeof(value) || '|' || value::text from ops.setting where key = 'cmdbar_ai_enabled'")" '^boolean[|]false$'
check "S02 the daily cap is the number 100" \
  "$(as_pg "select jsonb_typeof(value) || '|' || value::text from ops.setting where key = 'cmdbar_daily_cap'")" '^number[|]100$'
check "S03 both descriptions are updated (go-live and Spec 07 §3; per IST day, Anand 15 Nov 2026), with no semicolon in either" \
  "$(as_pg "select string_agg((description like '%' || w || '%' and position(';' in description) = 0)::text, ',' order by k)
            from (values ('cmdbar_ai_enabled', 'false until the command bar''s go-live (Spec 07 §3)'),
                         ('cmdbar_daily_cap', 'per IST day across all visitors (Anand, 15 Nov 2026)')) v(k, w)
            join ops.setting s on s.key = v.k")" '^true,true$'
check "S04 the per-visitor limit is untouched (10), and job_writer still can't change a setting" \
  "$(as_pg "select value::text from ops.setting where key = 'cmdbar_per_visitor_hourly'")|$(as_role job_writer "update ops.setting set value = 'true' where key = 'cmdbar_ai_enabled'" | grep -c 'permission denied')" \
  '^10[|]1$'
check "S05 no command-bar task in core.ai_task (not needed: client.py writes no row keyed on a task)" \
  "$(as_pg "select count(*) from core.ai_task where key like 'cmdbar%'")" '^0$'

############################################################ shape, owner, grants
check "V01 both views: owned by semantic_owner, security_barrier on" \
  "$(as_pg "select string_agg(c.relname || ':' || pg_get_userbyid(c.relowner) || ':' || (c.reloptions @> array['security_barrier=true'])::text, ',' order by c.relname)
            from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'semantic' and c.relname in ('earnings_calls','call_guidance')")" \
  '^call_guidance:semantic_owner:true,earnings_calls:semantic_owner:true$'
check "V02 semantic.earnings_calls' columns, in order (no transcript, summary or quote column)" \
  "$(as_pg "select string_agg(attname, ',' order by attnum) from pg_attribute where attrelid = 'semantic.earnings_calls'::regclass and attnum > 0 and not attisdropped")" \
  '^company,nse_symbol,market,currency,fiscal_quarter,quarter_end,call_date,filed_at,exchange,transcript_status,tone_mean,tone_change,tone_label$'
check "V03 semantic.call_guidance's columns, in order: no hit rate and none of the columns that wait for equity.guidance_hit_rate" \
  "$(as_pg "select string_agg(attname, ',' order by attnum) from pg_attribute where attrelid = 'semantic.call_guidance'::regclass and attnum > 0 and not attisdropped")" \
  '^company,nse_symbol,market,currency,fiscal_quarter,call_date,metric,metric_label,kind,value,low,high,unit,direction,comparison,growth_basis,basis,period_text,period_kind,target_start,target_end,scope_text,stated_on,not_scored_reason,extraction_label,outcome,relation,actual,base,outcome_reason,outcome_label$'
check "V04 select on both views for sql_reader only: not anon, authenticated, service_role or job_writer" \
  "$(as_pg "select string_agg(r || ':' || has_table_privilege(r, v::regclass, 'select')::text, ',' order by v, r)
            from unnest(array['semantic.earnings_calls','semantic.call_guidance']) v,
                 unnest(array['anon','authenticated','job_writer','service_role','sql_reader']) r")" \
  '^anon:false,authenticated:false,job_writer:false,service_role:false,sql_reader:true,anon:false,authenticated:false,job_writer:false,service_role:false,sql_reader:true$'
check "V05 anon and authenticated are refused both views" \
  "$(for r in anon authenticated; do for v in earnings_calls call_guidance; do as_role $r "select count(*) from semantic.$v"; done; done | grep -c 'permission denied')" '^4$'
check "V06 sql_reader, logged in as itself, still reads no base table (the calls, targets, outcomes, tone, field policy, settings)" \
  "$(for t in equity.earnings_call equity.guidance equity.guidance_outcome equity.call_tone core.field_policy ops.setting core.chunk; do login sql_reader "select count(*) from $t"; done | grep -c 'permission denied')" '^7$'

############################################################ every semantic view commented (Spec 07 §5 item 3)
check "C01 every column of every semantic view has a comment" \
  "$(as_pg "select count(*) || ' of ' || (select count(*) from pg_class c2 join pg_namespace n2 on n2.oid = c2.relnamespace where n2.nspname = 'semantic' and c2.relkind = 'v') || ' views; missing: ' || coalesce(string_agg(c.relname || '.' || a.attname, ' '), '-')
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
            where n.nspname = 'semantic' and c.relkind = 'v' and coalesce(col_description(c.oid, a.attnum), '') = ''")" \
  '^0 of (1[1-9]|[2-9][0-9]) views; missing: -$'
check "C02 every semantic view's comment names its row grain ('one row per')" \
  "$(as_pg "select count(*) || ': ' || coalesce(string_agg(c.relname, ' '), '-')
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'semantic' and c.relkind = 'v' and coalesce(obj_description(c.oid, 'pg_class'), '') !~* 'one row per'")" '^0: -$'
check "C03 the new views' comments: the grain, India only, no transcript text, the units and the currency rule, no hit rates" \
  "$(as_pg "select (obj_description('semantic.earnings_calls'::regclass, 'pg_class') ~ '^One row per published earnings call of a Nifty 50 company \(Indian companies only\).*Never transcript text or summaries')::text
               || (obj_description('semantic.call_guidance'::regclass, 'pg_class') ~ '^One row per guidance target .*inr = rupees in crore.*Never add or compare inr amounts with another currency\. No hit rates\.')::text")" '^truetrue$'
check "C04 the corrected comments: policy_meetings from December 2018, market_daily lists Damodaran's three series (and keeps 0012's checks)" \
  "$(as_pg "select (obj_description('semantic.policy_meetings'::regclass, 'pg_class') like '%December 2018%' and obj_description('semantic.policy_meetings'::regclass, 'pg_class') not like '%1 Jan 2019%')::text
               || (c like '%equity risk premium%' and c like '%country risk premium%' and c like '%default spread%' and c like '%fred.DEXINUS%'
                   and c like '%yf.INR=X%' and c like '%never combined%' and c like '%Never compare levels across currencies.')::text
            from obj_description('semantic.market_daily'::regclass, 'pg_class') c")" '^truetrue$'

############################################################ the rows the checks use
SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
create temp table k52_c (co text, fq text, pe date, ev_pub boolean, status text, doc text, call_date date) on commit drop;
insert into k52_c values
  ('k52-a', 'Q1 FY27', '2026-06-30', true,  'stored',             'published',   '2026-07-20'),
  ('k52-a', 'Q2 FY27', '2026-09-30', true,  'stored',             'published',   '2026-10-20'),
  ('k52-a', 'Q3 FY27', '2026-12-31', false, 'stored',             'published',   '2027-01-20'),
  ('k52-a', 'Q4 FY26', '2026-03-31', true,  'on_company_website', 'none',        null),
  ('k52-a', 'Q4 FY27', '2027-03-31', true,  'stored',             'unpublished', '2027-04-20'),
  ('k52-b', 'Q2 FY27', '2026-09-30', true,  'stored',             'published',   '2026-10-21'),
  ('k52-u', 'Q2 FY27', '2026-09-30', true,  'stored',             'published',   '2026-10-22');
do $$
declare r record; eid integer; evid bigint; did bigint; oid bigint; txt text := repeat('Margin came in at 27 percent this quarter. ', 15);
begin
  insert into core.entity (kind, slug, name, short_name, market, currency, is_published) values
    ('company', 'k52-a', 'K52 A Ltd', 'K52 A', 'IN', 'INR', true),
    ('company', 'k52-b', 'K52 B Ltd', 'K52 B', 'IN', 'INR', false),
    ('company', 'k52-u', 'K52 U Inc', 'K52 U', 'US', 'USD', true);
  insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000520' || upper(right(slug, 1)), upper(replace(slug, '-', '')), '5520' from core.entity where slug like 'k52-%';
  for r in select c.*, e.id as eid from k52_c c join core.entity e on e.slug = c.co loop
    insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
      values ('earnings_call', r.eid, lower(r.co || '-' || replace(r.fq, ' ', '-')), r.co || ' ' || r.fq, coalesce(r.call_date, r.pe + 20),
              r.status, (select id from core.source where key = 'nse'), r.ev_pub)
      returning id into evid;
    did := null;
    if r.doc <> 'none' then
      insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                                 char_count, text_sha256, chunked_at, is_published)
        values ('call_transcript', r.eid, evid, 'K52 call ' || r.co || ' ' || r.fq, (select id from core.source where key = 'company_web'),
                lower('k52-' || r.co || '-' || replace(r.fq, ' ', '-')), 'https://example.com/k52.pdf', now(),
                char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), r.doc = 'published') returning id into did;
      insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
        values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
    end if;
    insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, call_date,
                                      call_date_source, filed_at, exchange, route, transcript_document_id, company_url, words)
      values (evid, r.eid, r.fq, r.pe, lower(replace(r.fq, ' ', '-')), 'results_link', r.call_date,
              case when r.call_date is not null then 'text' end, coalesce(r.call_date, r.pe + 20) + 1, 'NSE',
              case when did is null then 'company_link' else 'exchange_pdf' end, did,
              case when did is null then 'https://example.com/k52-call' end, case when did is not null then 105 end);
    if r.fq = 'Q2 FY27' and r.co in ('k52-a', 'k52-u') then
      insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
        values ('guidance_extraction', did, jsonb_build_object(), 'k52 figures ' || r.co, 'draft', 'claude-haiku-5-5',
                'guidance_extraction', 'v1', sha256(convert_to('k52 ' || r.co, 'UTF8'))) returning id into oid;
      insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
        values (oid, 1, '/x', 'document_span', did, 0, 43, true);
      update core.ai_output set status = 'published' where id = oid;
    end if;
  end loop;
end $$;
-- A's four targets on its Q2 call (U's one on its own): a1 point, a2 range, a3 unknown speaker, a4 a duplicate of a1
insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, low, high, unit, period_text, period_kind, target_start,
                             target_end, basis, growth_basis, vintage_date, speaker_role)
select ec.event_id, ec.entity_id, o.id, 1, ec.transcript_document_id, 0, 43, v.metric, 'Label ' || v.metric, 'margin as said',
       v.kind, v.value, v.low, v.high, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'level', '2026-10-20', v.role
from (values ('k52-a', 'k52_a1', 'point', 27::numeric, null::numeric, null::numeric, 'management'),
             ('k52-a', 'k52_a2', 'range', null, 26, 28, 'management'),
             ('k52-a', 'k52_a3', 'point', 25, null, null, 'unknown'),
             ('k52-a', 'k52_a4', 'point', 27, null, null, 'management'),
             ('k52-u', 'k52_u1', 'point', 30, null, null, 'management')) v(co, metric, kind, value, low, high, role)
join core.entity e on e.slug = v.co
join equity.earnings_call ec on ec.entity_id = e.id and ec.fiscal_label = 'Q2 FY27'
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
update equity.guidance set duplicate_of = (select id from equity.guidance where metric = 'k52_a1') where metric = 'k52_a4';
insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, horizon_share, counted, outcome, relation, actual,
                                     base, reason, inputs, rule_version)
select g.id, 'k52|' || g.metric, true, 'first', 0.4, true, v.outcome, v.relation, v.actual, null, null,
       jsonb_build_object('rows', jsonb_build_array()), 'k52'
from (values ('k52_a1', 'delivered', 'within', 27.2::numeric), ('k52_a2', 'pending', null, null), ('k52_u1', 'missed', 'below', 20))
       v(metric, outcome, relation, actual)
join equity.guidance g on g.metric = v.metric;
-- tone: A's Q1 and Q2 (Q2 against Q1)
insert into equity.call_tone (event_id, n, mean, sd, prev_event_id, delta, se_delta, change, rule_version)
select ec.event_id, 12, case ec.fiscal_label when 'Q1 FY27' then 0.20 else 0.45 end, 0.5,
       case ec.fiscal_label when 'Q2 FY27' then (select p.event_id from equity.earnings_call p where p.entity_id = ec.entity_id and p.fiscal_label = 'Q1 FY27') end,
       case ec.fiscal_label when 'Q2 FY27' then 0.25 end, case ec.fiscal_label when 'Q2 FY27' then 0.1 end,
       case ec.fiscal_label when 'Q2 FY27' then 'more_positive' end, 'k52'
from equity.earnings_call ec join core.entity e on e.id = ec.entity_id
where e.slug = 'k52-a' and ec.fiscal_label in ('Q1 FY27', 'Q2 FY27');
commit;
SQL
)"
check "R01 the test rows load (3 companies, 7 calls, 5 targets, 3 outcomes, 2 tone scores)" \
  "$SETUP_OUT|$(as_pg "select (select count(*) from core.entity where slug like 'k52-%') || ',' || (select count(*) from equity.earnings_call ec join core.entity e on e.id = ec.entity_id where e.slug like 'k52-%')
                       || ',' || (select count(*) from equity.guidance where metric like 'k52%') || ',' || (select count(*) from equity.guidance_outcome where target_key like 'k52%')
                       || ',' || (select count(*) from equity.call_tone where rule_version = 'k52')")" \
  '^(BEGIN|CREATE TABLE|INSERT 0 [0-9]+|UPDATE [0-9]+|DO|COMMIT|[[:space:]])*[|]3,7,5,3,2$'

K="company like 'K52%'"
EXT="$(POL ai.guidance_extraction labelled)"
OUT="$EXT; $(POL equity.guidance_outcome labelled)"

############################################################ semantic.earnings_calls
check "E01 as sql_reader (logged in): only A's three visible calls, in its own currency; the unpublished event, the unpublished transcript, the unpublished company and the US company are absent" \
  "$(login sql_reader "select string_agg(company || ':' || coalesce(fiscal_quarter, '-') || ':' || market || ':' || currency || ':' || transcript_status, ' ' order by quarter_end) from semantic.earnings_calls where $K or company like 'K52%'")" \
  '^K52 A Ltd:Q4 FY26:IN:INR:on_company_website K52 A Ltd:Q1 FY27:IN:INR:stored K52 A Ltd:Q2 FY27:IN:INR:stored$'
check "E02 the same calls api.earnings_calls shows anon for the Indian companies (the semantic view adds no row and drops none)" \
  "$(as_role anon "select string_agg(fiscal_label, ',' order by period_end) from api.earnings_calls where company_slug in ('k52-a', 'k52-b')")|$(login sql_reader "select string_agg(fiscal_quarter, ',' order by quarter_end) from semantic.earnings_calls where $K")" \
  '^Q4 FY26,Q1 FY27,Q2 FY27[|]Q4 FY26,Q1 FY27,Q2 FY27$'
check "E03 api.earnings_calls does show the US call: the semantic view drops it by market (India only until K-03b)" \
  "$(as_role anon "select count(*) from api.earnings_calls where company_slug = 'k52-u'")|$(login sql_reader "select count(*) from semantic.earnings_calls where company = 'K52 U Inc' or market <> 'IN'")" '^1[|]0$'
check "E04 the call's fields as stored: Q2's quarter end, call date, filing time (IST), exchange and symbol; Q4 FY26's call date null" \
  "$(login sql_reader "set timezone = 'Asia/Kolkata'; select string_agg(fiscal_quarter || ':' || quarter_end || ':' || coalesce(call_date::text, 'null') || ':' || to_char(filed_at, 'YYYY-MM-DD') || ':' || exchange || ':' || nse_symbol, ' ' order by quarter_end) from semantic.earnings_calls where $K and fiscal_quarter in ('Q2 FY27', 'Q4 FY26')" | grep -v '^SET$')" \
  '^Q4 FY26:2026-03-31:null:2026-04-21:NSE:K52A Q2 FY27:2026-09-30:2026-10-20:2026-10-21:NSE:K52A$'
check "E05 tone held back (ai.call_tone hidden, as seeded): no tone mean, change or label on any call" \
  "$(login sql_reader "select count(*) || ',' || count(tone_mean) || ',' || count(tone_change) || ',' || count(tone_label) from semantic.earnings_calls where $K")" '^3,0,0,0$'
check "E06 tone labelled: Q1's and Q2's means, Q2's change against Q1, the policy's label; nothing on the call without a score" \
  "$(flip sql_reader "$(POL ai.call_tone labelled)" "select string_agg(fiscal_quarter || ':' || coalesce(tone_mean::text, '-') || ':' || coalesce(tone_change, '-') || ':' || coalesce(tone_label, '-'), ' ' order by quarter_end) from semantic.earnings_calls where $K")" \
  '^Q4 FY26:-:-:AI-scored Q1 FY27:0.20:-:AI-scored Q2 FY27:0.45:more_positive:AI-scored$'
check "E07 tone labelled, the previous call unpublished: Q2 keeps its mean but loses the change (as api.call_tone)" \
  "$(flip sql_reader "$(POL ai.call_tone labelled); update core.event set is_published = false where slug = 'k52-a-q1-fy27'" "select fiscal_quarter || ':' || tone_mean || ':' || coalesce(tone_change, 'null') from semantic.earnings_calls where $K and fiscal_quarter = 'Q2 FY27'")|$(flip anon "$(POL ai.call_tone labelled); update core.event set is_published = false where slug = 'k52-a-q1-fy27'" "select coalesce(change, 'null') from api.call_tone where call_slug = 'k52-a-q2-fy27'")" \
  '^Q2 FY27:0.45:null[|]null$'
check "E08 the source switch: with nse suppressed no call is visible (the call's event source)" \
  "$(flip sql_reader "update core.source set is_suppressed = true where key = 'nse'" "select count(*) from semantic.earnings_calls where $K")" '^0$'
check "E09 the transcript's source switch: with company_web suppressed only the call without a stored transcript stays" \
  "$(flip sql_reader "update core.source set is_suppressed = true where key = 'company_web'" "select string_agg(fiscal_quarter, ',') from semantic.earnings_calls where $K")" '^Q4 FY26$'
check "E10 an unpublished company: none of its calls, though its event and transcript are published" \
  "$(login sql_reader "select count(*) from semantic.earnings_calls where company = 'K52 B Ltd'")|$(flip sql_reader "update core.entity set is_published = true where slug = 'k52-b'" "select count(*) from semantic.earnings_calls where company = 'K52 B Ltd'")" '^0[|]1$'

############################################################ semantic.call_guidance
check "G01 the extraction held back (seeded hidden): no target at all" \
  "$(login sql_reader "select count(*) from semantic.call_guidance where $K or company like 'K52%'")" '^0$'
check "G02 extraction labelled, outcome hidden: A's two management targets (the unknown speaker's and the duplicate left out), the outcome columns null, the extraction label on each" \
  "$(flip sql_reader "$EXT" "select string_agg(metric || ':' || kind || ':' || coalesce(value::text, low || '-' || high) || ':' || unit || ':' || fiscal_quarter || ':' || stated_on || ':' || extraction_label, ' ' order by metric)
                             || '|' || (count(outcome) + count(relation) + count(actual) + count(base) + count(outcome_reason) + count(outcome_label))
                           from semantic.call_guidance where $K")" \
  '^k52_a1:point:27:pct:Q2 FY27:2026-10-20:AI-extracted k52_a2:range:26-28:pct:Q2 FY27:2026-10-20:AI-extracted[|]0$'
for st in shown labelled; do
  check "G03 extraction and outcome $st: a1 delivered within with its actual, a2 pending, the outcome label 'derived'" \
    "$(flip sql_reader "$EXT; $(POL equity.guidance_outcome $st)" "select string_agg(metric || ':' || outcome || ':' || coalesce(relation, '-') || ':' || coalesce(actual::text, '-') || ':' || outcome_label, ' ' order by metric) from semantic.call_guidance where $K")" \
    '^k52_a1:delivered:within:27.2:derived k52_a2:pending:-:-:derived$'
done
check "G04 the hit-rate key shown too changes nothing (no column reads it)" \
  "$(flip sql_reader "$OUT; $(POL equity.guidance_hit_rate shown)" "select string_agg(metric || ':' || outcome, ' ' order by metric) from semantic.call_guidance where $K")" \
  '^k52_a1:delivered k52_a2:pending$'
check "G05 the outcome alone (extraction hidden): no rows" \
  "$(flip sql_reader "$(POL equity.guidance_outcome labelled)" "select count(*) from semantic.call_guidance where $K")" '^0$'
check "G06 the US company's target: in api.call_guidance (anon), never in the semantic view" \
  "$(flip anon "$OUT" "select count(*) from api.call_guidance where company_slug = 'k52-u'")|$(flip sql_reader "$OUT" "select count(*) from semantic.call_guidance where company = 'K52 U Inc' or market <> 'IN'")" '^1[|]0$'
G07_API="$(flip anon "$OUT" "select string_agg(metric || ':' || kind || ':' || coalesce(value::text, '-') || ':' || coalesce(low::text, '-') || ':' || coalesce(high::text, '-') || ':' || fiscal_label || ':' || coalesce(outcome, '-') || ':' || coalesce(actual::text, '-'), ' ' order by metric) from api.call_guidance where company_slug = 'k52-a'")"
G07_SEM="$(flip sql_reader "$OUT" "select string_agg(metric || ':' || kind || ':' || coalesce(value::text, '-') || ':' || coalesce(low::text, '-') || ':' || coalesce(high::text, '-') || ':' || fiscal_quarter || ':' || coalesce(outcome, '-') || ':' || coalesce(actual::text, '-'), ' ' order by metric) from semantic.call_guidance where $K")"
check "G07 the same targets, figures and outcomes as api.call_guidance for the Indian company (anon), with both keys labelled" \
  "$([ "$G07_API" = "$G07_SEM" ] && echo "same:$G07_SEM" || echo "differ: $G07_API | $G07_SEM")" \
  '^same:k52_a1:point:27:-:-:Q2 FY27:delivered:27.2 k52_a2:range:-:26:28:Q2 FY27:pending:-$'
check "G08 the call's switches reach the targets: the event unpublished, or its transcript's source suppressed, leaves none" \
  "$(flip sql_reader "$OUT; update core.event set is_published = false where slug = 'k52-a-q2-fy27'" "select count(*) from semantic.call_guidance where $K")|$(flip sql_reader "$OUT; update core.source set is_suppressed = true where key = 'company_web'" "select count(*) from semantic.call_guidance where $K")" '^0[|]0$'

############################################################ no transcript text, and the security barrier
check "T01 no transcript text through either view, every key shown (the chunk text and the words as said appear nowhere)" \
  "$(flip sql_reader "$OUT; $(POL equity.guidance_hit_rate shown); $(POL ai.call_tone shown)" "select (select count(*) from semantic.earnings_calls t where row_to_json(t)::text ~ 'Margin came in|margin as said') + (select count(*) from semantic.call_guidance t where row_to_json(t)::text ~ 'Margin came in|margin as said')")" '^0$'
check "T02 a held-back outcome can't be probed through an error: a division by the hidden actual raises nothing" \
  "$(flip sql_reader "$EXT" "select count(*) from semantic.call_guidance where $K and 1 / (actual - 27.2) is not null")" '^0$'

cleanup
check "Z01 cleanup leaves no k52 row and the four keys hidden" \
  "$(as_pg "select (select count(*) from core.entity where slug like 'k52%') || ',' || (select count(*) from equity.guidance where metric like 'k52%') || ',' ||
                   (select string_agg(status, ',' order by field_key) from core.field_policy where field_key in ('ai.call_tone','ai.guidance_extraction','equity.guidance_outcome','equity.guidance_hit_rate'))")" \
  '^0,0,hidden,hidden,hidden,hidden$'
echo; echo "passed $pass, failed $fail"
