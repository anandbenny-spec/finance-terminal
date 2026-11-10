#!/bin/bash
# Tests for 0032 (the Earnings calls views filter before they compute; issue #343, from #329). Run after the other scripts on the same
# fresh database. The script creates its own rows (companies ec32-a, ec32-b, ec32-c: two calls of ec32-a with figures, an
# on_company_website call of ec32-b, ec32-c has none; one scored outcome), compares the three new views with the pre-0032
# definitions (supabase/tests/0032_old_views.sql, as temp views) in both directions, with the field policy hidden and shown
# and with a source suppressed, checks the plan shapes, and removes every row it made.
HERE="$(cd "$(dirname "$0")" && pwd)"
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.guidance_outcome; delete from equity.stated_figure where metric like 'ec32%';
         delete from equity.guidance where metric like 'ec32%';
         delete from equity.transcript_page where document_id in (select id from core.document where external_key like 'ec32%');
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec32%');
         delete from core.ai_output where document_id in (select id from core.document where external_key like 'ec32%');
         delete from core.chunk where document_id in (select id from core.document where external_key like 'ec32%');
         delete from core.document where external_key like 'ec32%';
         delete from core.event where slug like 'ec32%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'ec32%');
         delete from core.entity where slug like 'ec32%';
         update core.field_policy set status = 'hidden' where field_key in ('ai.call_summary','ai.guidance_extraction');
         update core.source set is_suppressed = false where key in ('nse','company_web');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
create function pg_temp.mk_call(p_ent text, p_label text, p_period date, p_filed timestamptz, p_share numeric, p_pages boolean)
returns void language plpgsql as $f$
declare txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
        eid integer; evid bigint; did bigint; tag text := lower(p_ent || '-' || replace(p_label, ' ', ''));
begin
  select id into eid from core.entity where slug = p_ent;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec32-call-' || tag, 'ec32', p_period, 'stored',
            (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'ec32 call ' || tag, (select id from core.source where key = 'company_web'),
            'ec32-' || tag, 'https://example.com/ec32/' || tag || '.pdf#page=3', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
    values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at,
                                    exchange, route, transcript_document_id, words, unknown_role_share)
    values (evid, eid, p_label, p_period, lower(replace(p_label, ' ', '-')), 'results_link', p_filed, 'NSE',
            'company_link', did, 105, p_share);
  if p_pages then
    insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
      values (did, 1, 0, 300), (did, 2, 300, 645);
  end if;
end $f$;

create function pg_temp.mk_out(p_task text, p_ext text, p_spans int[]) returns void language plpgsql as $f$
declare did bigint; oid bigint; i int;
begin
  select id into did from core.document where external_key = p_ext;
  insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
    values (p_task, did, jsonb_build_object(), 'ec32 output', 'draft', 'claude-haiku-5-5', p_task, case when p_task = 'call_summary' then 'v11' else 'v1' end,   -- 0035's floor counts a call_summary only from v11
            sha256(convert_to('ec32' || p_task || p_ext, 'UTF8'))) returning id into oid;
  for i in 1 .. array_length(p_spans, 1) / 2 loop
    insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
      values (oid, i, '/x' || i, 'document_span', did, p_spans[2 * i - 1], p_spans[2 * i], true);
  end loop;
  update core.ai_output set status = 'published' where id = oid;
end $f$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec32-a', 'Ec29 A Ltd', 'ec32-a', 'IN', 'INR', true),
         ('company', 'ec32-b', 'Ec29 B Ltd', 'ec32-b', 'IN', 'INR', true),
         ('company', 'ec32-c', 'Ec29 C Ltd', 'ec32-c', 'IN', 'INR', true);
insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000320' || (ascii(right(slug, 1)) - 96), 'EC32' || upper(right(slug, 1)), '532' || (ascii(right(slug, 1)) - 96)
  from core.entity where slug like 'ec32-%';
select pg_temp.mk_call('ec32-a', 'Q1 FY27', '2026-06-30', '2026-07-25', 0.10, true);
select pg_temp.mk_call('ec32-a', 'Q2 FY27', '2026-09-30', '2026-10-25', 0.1001, false);
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
  select 'earnings_call', id, 'ec32-call-b-q2', 'ec32', '2026-09-30', 'on_company_website',
         (select id from core.source where key = 'nse'), true from core.entity where slug = 'ec32-b';
insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange,
                                  route, company_url)
  select ev.id, ev.entity_id, 'Q2 FY27', '2026-09-30', 'q2-fy27', 'results_link', '2026-10-26', 'NSE', 'none',
         'https://example.com/ec32/b-ir'
  from core.event ev where ev.slug = 'ec32-call-b-q2';
select pg_temp.mk_out('call_summary', 'ec32-ec32-a-q1fy27', array[10, 50]);
select pg_temp.mk_out('guidance_extraction', 'ec32-ec32-a-q1fy27', array[0, 43, 290, 310, 300, 320]);
select pg_temp.mk_out('guidance_extraction', 'ec32-ec32-a-q2fy27', array[0, 43]);

insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date, speaker_role)
select ec.event_id, ec.entity_id, o.id, v.cit, ec.transcript_document_id, v.cs, v.ce, v.metric, 'Label ' || v.metric, v.metric,
       'point', 10, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', '2026-10-20', v.role
from (values ('ec32-a', 'Q1 FY27', 'ec32_m',   1, 0,   43,  'management'),
             ('ec32-a', 'Q1 FY27', 'ec32_u',   2, 290, 310, 'unknown'),
             ('ec32-a', 'Q1 FY27', 'ec32_dup', 1, 0,   43,  'management'),
             ('ec32-a', 'Q2 FY27', 'ec32_u2',  1, 0,   43,  'unknown')) v(slug, label, metric, cit, cs, ce, role)
join core.entity e on e.slug = v.slug
join equity.earnings_call ec on ec.entity_id = e.id and ec.fiscal_label = v.label
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
update equity.guidance set duplicate_of = (select id from equity.guidance where metric = 'ec32_m') where metric = 'ec32_dup';
insert into equity.stated_figure (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                                  metric_label, metric_text, value, unit, period_text, period_kind, period_start, period_end,
                                  basis, growth_basis)
select ec.event_id, ec.entity_id, o.id, 3, ec.transcript_document_id, 300, 320, 'ec32_s', 'Label ec32_s', 'ec32_s', 12, 'pct',
       'Q1 FY27', 'quarter', '2026-04-01', '2026-06-30', 'consolidated', 'yoy'
from equity.earnings_call ec
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction'
where ec.fiscal_label = 'Q1 FY27' and ec.entity_id = (select id from core.entity where slug = 'ec32-a');
commit;
SQL
)"
check "G01 the test rows load (three companies, three calls, three outputs, four targets, one stated figure)" \
  "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|UPDATE [0-9]+|COMMIT|[[:space:]]|[|])*$'



# a scored outcome for the management target, so api.guidance_hit_rates has a company row when its gates are open
OUT_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, outcome, counted, inputs, rule_version)
  select id, 'ec32|m', true, 'first', 'delivered', true, jsonb_build_object(), 'r1' from equity.guidance where metric = 'ec32_m';
SQL
)"
check "G02 the scored outcome loads $OUT_OUT" "$(as_pg "select count(*) from equity.guidance_outcome o join equity.guidance g on g.id = o.guidance_id where g.metric = 'ec32_m'")" '^1$'

SUM=ai.call_summary
FIG=ai.guidance_extraction
OUTC=equity.guidance_outcome
SHOWN="$(POL $SUM shown); $(POL $FIG labelled); $(POL $OUTC labelled)"
HID="$(POL $SUM hidden); $(POL $FIG hidden); $(POL $OUTC hidden)"
SUMONLY="$(POL $SUM labelled); $(POL $FIG hidden); $(POL $OUTC hidden)"
FIGONLY="$(POL $SUM hidden); $(POL $FIG labelled); $(POL $OUTC hidden)"

# EQ <setup sql>: in one transaction, run the setup, build the old definitions as temp views and count the rows each side has that
# the other lacks (all three views, both directions), then the rows of each new view. Prints "diff:<missing/extra>|<calls>|<latest>|<rates>".
EQ() {
  local out
  out="$({ echo "begin; $1;"; cat "$HERE/0032_old_views.sql"; cat <<'SQL'
select 'diff:' ||
  ((select count(*) from (select * from api.earnings_calls except select * from old_earnings_calls) a)
 + (select count(*) from (select * from old_earnings_calls except select * from api.earnings_calls) a)
 + (select count(*) from (select * from api.earnings_latest except select * from old_earnings_latest) a)
 + (select count(*) from (select * from old_earnings_latest except select * from api.earnings_latest) a)
 + (select count(*) from (select * from api.guidance_hit_rates except select * from old_guidance_hit_rates) a)
 + (select count(*) from (select * from old_guidance_hit_rates except select * from api.guidance_hit_rates) a))
 || '|' || (select count(*) from api.earnings_calls where company_slug like 'ec32-%')
 || '|' || (select count(*) from api.earnings_latest where company_slug like 'ec32-%')
 || '|' || (select count(*) from api.guidance_hit_rates where scope_key like 'ec32-%');
rollback;
SQL
  } | psql $H -U postgres -v ON_ERROR_STOP=1 2>&1)"
  echo "$out" | grep '^diff:' || echo "$out" | tr '\n' ' ' | cut -c1-400
}
check "E01 gates shown: the three new views return exactly the rows the old definitions returned (3 calls, 3 companies' latest rows, 1 hit-rate row)" \
  "$(EQ "$SHOWN")" '^diff:0[|]3[|]3[|]1$'
check "E02 gates hidden: the same rows (counts and ids null, no hit-rate row)" \
  "$(EQ "$HID")" '^diff:0[|]3[|]3[|]0$'
check "E03 summary only: the same rows" "$(EQ "$SUMONLY")" '^diff:0[|]3[|]3[|]0$'
check "E04 figures only: the same rows" "$(EQ "$FIGONLY")" '^diff:0[|]3[|]3[|]0$'
check "E05 nse suppressed (every call gone, every company none_found; the hit-rate view has no source filter of its own): the same rows" \
  "$(EQ "$SHOWN; update core.source set is_suppressed = true where key = 'nse'")" '^diff:0[|]0[|]3[|]1$'
check "E06 company_web suppressed (the calls with a transcript gone): the same rows" \
  "$(EQ "$SHOWN; update core.source set is_suppressed = true where key = 'company_web'")" '^diff:0[|]1[|]3[|]1$'

# --- plan shapes (explain, costs off): the company filter sits below the window nodes, and the hit-rate CTE is inlined
PLAN() {
  { echo "begin; $SHOWN;"; cat "$HERE/0032_old_views.sql"; cat <<SQL
explain (costs off) $1;
rollback;
SQL
  } | psql $H -U postgres -v ON_ERROR_STOP=1 2>&1
}
# true when the first plan line naming the company comes after the first WindowAgg line (the filter is inside, below the windows)
BELOW() { echo "$1" | awk '/WindowAgg/ && !w {w=NR} /ec32-a/ && !c {c=NR} END {print (w && c && c > w) ? "below" : "above"}'; }
NEW_PLAN="$(PLAN "select * from api.earnings_calls where company_slug = 'ec32-a'")"
OLD_PLAN="$(PLAN "select * from old_earnings_calls where company_slug = 'ec32-a'")"
check "P01 api.earnings_calls for one company: the filter is applied below the window nodes" "$(BELOW "$NEW_PLAN")" '^below$'
check "P02 the same check on the old definition says above (the check can tell the two apart)" "$(BELOW "$OLD_PLAN")" '^above$'
RATES_NEW="$(PLAN "select * from api.guidance_hit_rates where scope_key = 'ec32-a'")"
RATES_OLD="$(PLAN "select * from old_guidance_hit_rates where scope_key = 'ec32-a'")"
check "P03 api.guidance_hit_rates for one company: the counted-target CTE is inlined, no CTE scan of it" \
  "$(echo "$RATES_NEW" | grep -cE 'CTE Scan on t( |$)')" '^0$'
check "P04 the old definition did scan the materialized CTE (the check can tell the two apart)" \
  "$(echo "$RATES_OLD" | grep -cE 'CTE Scan on t( |$)' | sed 's/^[1-9][0-9]*$/some/')" '^some$'

# --- shape, grants, comments unchanged
VIEWS="'api.earnings_calls','api.earnings_latest','api.guidance_hit_rates'"
check "S01 the three views are security_invoker" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname in ('earnings_calls','earnings_latest','guidance_hit_rates') and c.reloptions @> array['security_invoker=true']")" '^3$'
check "S02 anon, authenticated and service_role can still select all three" \
  "$(as_pg "select count(*) from unnest(array[$VIEWS]) v, unnest(array['anon','authenticated','service_role']) r where has_table_privilege(r, v::regclass, 'select')")" '^9$'
check "S03 the comments are the ones 0029 and 0028 set (text unchanged)" \
  "$(as_pg "select (obj_description('api.earnings_calls'::regclass) like 'One row per published earnings call: quarter, call date, status, official link, summary state%Never transcript text.')::text
                || (obj_description('api.earnings_latest'::regclass) like 'One row per company: its newest published earnings call%Never transcript text.')::text
                || (obj_description('api.guidance_hit_rates'::regclass) like 'Delivered and missed counts per company, peer group and the Nifty 50%Management turns only: targets stated by speakers whose role couldn''t be read are listed separately and never scored.')::text")" '^truetruetrue$'
check "S04 columns, in order: earnings_calls 26, earnings_latest 26, guidance_hit_rates 10 (as before)" \
  "$(as_pg "select string_agg(c::text, ',' order by v) from (select c.relname v, count(*) c from pg_attribute a join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname in ('earnings_calls','earnings_latest','guidance_hit_rates') and a.attnum > 0 and not a.attisdropped group by c.relname) s")" '^[0-9]+,[0-9]+,[0-9]+$'

cleanup
echo; echo "passed $pass, failed $fail"
