#!/bin/bash
# Tests for 0047 (the KPI-table pilot: equity.kpi_document, equity.kpi_value, equity.kpi_change, equity.kpi_review, the
# field policy equity.kpi_changes and api.kpi_changes; issue #512, job DD-04). Run after the other scripts on the same
# fresh database. The script makes its own companies (kp47-a … kp47-c on NSE, kp47-x on BSE), checks through the roles and
# removes every row it made. Gate flips, suppression and reviews happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
SHOWN="update core.field_policy set status = 'shown' where field_key = 'equity.kpi_changes'"
KP="company_slug like 'kp47-%'"
ENTS="(select id from core.entity where slug like 'kp47-%')"
# A confirmed review for company a's flag, with the flag's own evidence hash.
REV_A="insert into equity.kpi_review (entity_id, dict_key, period_end, evidence_sha256, verdict, judge)
       select entity_id, dict_key, period_end, evidence_sha256, 'confirmed', 'fable-5.2' from equity.kpi_change
       where entity_id = (select id from core.entity where slug = 'kp47-a')"
# A confirmed review for a's flag but with a DIFFERENT (stale) evidence hash.
REV_A_STALE="insert into equity.kpi_review (entity_id, dict_key, period_end, evidence_sha256, verdict, judge)
       select entity_id, dict_key, period_end, sha256('stale'), 'confirmed', 'fable-5.2' from equity.kpi_change
       where entity_id = (select id from core.entity where slug = 'kp47-a')"

cleanup() {
  as_pg "delete from equity.kpi_review where entity_id in $ENTS;
         delete from equity.kpi_change where entity_id in $ENTS;
         delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id in $ENTS);
         delete from equity.kpi_document where entity_id in $ENTS;
         delete from core.document where entity_id in $ENTS;
         delete from core.entity where slug like 'kp47-%';
         update core.field_policy set status = 'hidden' where field_key = 'equity.kpi_changes';
         update core.source set is_suppressed = false where key in ('nse','bse');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  select 'company', 'kp47-' || s, 'Kp47 ' || upper(s) || ' Ltd', 'kp47-' || s, 'IN', 'INR', true
  from unnest(array['a','b','c','x']) as s;
-- a stored transcript for the quote citations (company a)
insert into core.document (doc_type, entity_id, title, char_count, text_sha256, source_id, external_key, source_url, is_published)
  select 'call_transcript', e.id, 'kp47-a transcript', 5000, sha256('kp47-a-tr'::bytea), (select id from core.source where key='nse'),
         'kp47-a-tr', 'https://example.com/kp47/a-transcript.pdf', true
  from core.entity e where e.slug = 'kp47-a';

-- company a: three previous press releases (Q3/Q4 FY26, Q1 FY27) + the current (Q2 FY27) that drops a metric
insert into equity.kpi_document (entity_id, fiscal_label, period_end, document_class, source_url, filing_key, exchange, filed_at, pages_used, page_count, sha256, bytes, status, parser_version)
  select e.id, v.fl, v.pe::date, 'press_release', 'https://example.com/kp47/a-' || v.fl || '.pdf', 'seq-a-' || v.fl, 'NSE',
         v.pe::date + 40, array[2,3], 8, sha256(('a' || v.fl)::bytea), 120000, 'read', 1
  from core.entity e, (values ('Q3 FY26','2025-12-31'),('Q4 FY26','2026-03-31'),('Q1 FY27','2026-06-30'),('Q2 FY27','2026-09-30')) v(fl,pe)
  where e.slug = 'kp47-a';
-- some values on the current document (kept and filtered)
insert into equity.kpi_value (document_id, row_no, label, member, measure, value, unit, page, filter_status, filter_reason, parser_version)
  select d.id, 1, 'Attrition (LTM)', 'IT services', 'percent', 12.5, '%', 2, 'kept', null, 1
    from equity.kpi_document d join core.entity e on e.id = d.entity_id where e.slug='kp47-a' and d.fiscal_label='Q2 FY27';
insert into equity.kpi_value (document_id, row_no, label, member, measure, value, unit, page, filter_status, filter_reason, parser_version)
  select d.id, 2, 'Total deposits growth', null, 'percent', 9.0, '%', 3, 'filtered', 'statement_total', 1
    from equity.kpi_document d join core.entity e on e.id = d.entity_id where e.slug='kp47-a' and d.fiscal_label='Q2 FY27';

-- the flag for company a: a dropped metric, reason stated (quote into the transcript) and an analyst asked
insert into equity.kpi_change (entity_id, dict_key, period_end, fiscal_label, metric_label, kind, previous_documents,
  current_document_id, last_value, last_value_unit, last_value_quarter, reason_stated, reason_document_id,
  reason_char_start, reason_char_end, reason_quote, reason_page, analyst_asked, analyst_document_id, analyst_char_start,
  analyst_char_end, analyst_quote, analyst_page, evidence_sha256, rule_version)
select e.id, 'headcount', '2026-09-30', 'Q2 FY27', 'Workforce (headcount)', 'dropped',
       (select array_agg(d.id) from equity.kpi_document d where d.entity_id = e.id and d.fiscal_label in ('Q3 FY26','Q4 FY26','Q1 FY27')),
       (select id from equity.kpi_document d where d.entity_id = e.id and d.fiscal_label = 'Q2 FY27'),
       613069, 'people', 'Q1 FY27', true, (select id from core.document where external_key='kp47-a-tr'),
       100, 180, 'We have stopped disclosing headcount from this quarter.', 4, true,
       (select id from core.document where external_key='kp47-a-tr'), 300, 360, 'Could you share the attrition number?', 9,
       sha256('a-evidence'), 1
from core.entity e where e.slug = 'kp47-a';

-- company b: a flag with reason NOT stated and no analyst question (no quotes)
insert into equity.kpi_document (entity_id, fiscal_label, period_end, document_class, source_url, filing_key, exchange, filed_at, pages_used, page_count, sha256, bytes, status, parser_version)
  select e.id, v.fl, v.pe::date, 'fact_sheet', 'https://example.com/kp47/b-' || v.fl || '.pdf', 'seq-b-' || v.fl, 'NSE',
         v.pe::date + 40, array[1], 4, sha256(('b' || v.fl)::bytea), 90000, 'read', 1
  from core.entity e, (values ('Q3 FY26','2025-12-31'),('Q4 FY26','2026-03-31'),('Q1 FY27','2026-06-30'),('Q2 FY27','2026-09-30')) v(fl,pe)
  where e.slug = 'kp47-b';
insert into equity.kpi_change (entity_id, dict_key, period_end, fiscal_label, metric_label, kind, previous_documents,
  current_document_id, last_value, last_value_unit, last_value_quarter, reason_stated, analyst_asked, evidence_sha256, rule_version)
select e.id, 'utilisation', '2026-09-30', 'Q2 FY27', 'Utilisation', 'missing',
       (select array_agg(d.id) from equity.kpi_document d where d.entity_id = e.id and d.fiscal_label in ('Q3 FY26','Q4 FY26','Q1 FY27')),
       (select id from equity.kpi_document d where d.entity_id = e.id and d.fiscal_label = 'Q2 FY27'),
       82.0, '%', 'Q1 FY27', false, false, sha256('b-evidence'), 1
from core.entity e where e.slug = 'kp47-b';

-- company c: a held document (could not be cut)
insert into equity.kpi_document (entity_id, fiscal_label, period_end, document_class, status, held_reason, parser_version)
  select e.id, 'Q2 FY27', '2026-09-30', 'press_release', 'held', 'release could not be cut from the outcome bundle', 1
  from core.entity e where e.slug = 'kp47-c';

-- company x: on BSE, a confirmed-review flag (for the suppression test)
insert into equity.kpi_document (entity_id, fiscal_label, period_end, document_class, source_url, filing_key, exchange, filed_at, pages_used, page_count, sha256, bytes, status, parser_version)
  select e.id, v.fl, v.pe::date, 'press_release', 'https://example.com/kp47/x-' || v.fl || '.pdf', 'news-x-' || v.fl, 'BSE',
         v.pe::date + 40, array[2], 6, sha256(('x' || v.fl)::bytea), 70000, 'read', 1
  from core.entity e, (values ('Q3 FY26','2025-12-31'),('Q4 FY26','2026-03-31'),('Q1 FY27','2026-06-30'),('Q2 FY27','2026-09-30')) v(fl,pe)
  where e.slug = 'kp47-x';
insert into equity.kpi_change (entity_id, dict_key, period_end, fiscal_label, metric_label, kind, previous_documents,
  current_document_id, reason_stated, analyst_asked, evidence_sha256, rule_version)
select e.id, 'branches', '2026-09-30', 'Q2 FY27', 'Branches', 'missing',
       (select array_agg(d.id) from equity.kpi_document d where d.entity_id = e.id and d.fiscal_label in ('Q3 FY26','Q4 FY26','Q1 FY27')),
       (select id from equity.kpi_document d where d.entity_id = e.id and d.fiscal_label = 'Q2 FY27'),
       false, false, sha256('x-evidence'), 1
from core.entity e where e.slug = 'kp47-x';
commit;
SQL
)"
check "A01 the test rows load" "$SETUP_OUT" '^(BEGIN|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|0-9])*$'

# --- tables, privileges and constraints
check "T01 row-level security is on for all four tables" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='equity' and c.relname in ('kpi_document','kpi_value','kpi_change','kpi_review') and c.relrowsecurity")" '^4$'
check "T02 job_writer may write and delete the three data tables" \
  "$(as_role job_writer "delete from equity.kpi_change where false; delete from equity.kpi_value where false; delete from equity.kpi_document where false; select 'ok'")" 'ok$'
check "T03 job_writer has NO privilege on kpi_review (insert denied)" \
  "$(as_role job_writer "insert into equity.kpi_review (entity_id, dict_key, period_end, evidence_sha256, verdict, judge) values (1,'x','2026-09-30', sha256('z'), 'confirmed','x')")" 'permission denied'
check "T04 anon can't write any table" \
  "$(as_role anon "delete from equity.kpi_change where false")" 'permission denied'
check "T05 authenticated can't write kpi_review" \
  "$(as_role authenticated "update equity.kpi_review set verdict='confirmed' where false")" 'permission denied'
check "T06 sql_reader can't read the base tables" \
  "$(as_role sql_reader "select count(*) from equity.kpi_change")" 'permission denied'
check "T07 a read document must carry the file facts" \
  "$(as_pg "insert into equity.kpi_document (entity_id, fiscal_label, period_end, document_class, status, parser_version)
            select id, 'Q2 FY27', '2026-09-30', 'data_sheet', 'read', 1 from core.entity where slug='kp47-c'")" 'violates check constraint'
check "T08 a held document must name a reason" \
  "$(as_pg "insert into equity.kpi_document (entity_id, fiscal_label, period_end, document_class, status, parser_version)
            select id, 'Q1 FY27', '2026-06-30', 'data_sheet', 'held', 1 from core.entity where slug='kp47-c'")" 'violates check constraint'
check "T09 a filtered value must name its reason, a kept one must not" \
  "$(as_pg "insert into equity.kpi_value (document_id, row_no, label, measure, page, filter_status, parser_version)
            select id, 99, 'x', 'count', 1, 'filtered', 1 from equity.kpi_document where entity_id=(select id from core.entity where slug='kp47-b') and fiscal_label='Q2 FY27'")
   $(as_pg "insert into equity.kpi_value (document_id, row_no, label, measure, page, filter_status, filter_reason, parser_version)
            select id, 98, 'x', 'count', 1, 'kept', 'because', 1 from equity.kpi_document where entity_id=(select id from core.entity where slug='kp47-b') and fiscal_label='Q2 FY27'")" \
  'violates check constraint.*violates check constraint'
check "T10 reason_stated=true needs a full quote" \
  "$(as_pg "insert into equity.kpi_change (entity_id, dict_key, period_end, fiscal_label, metric_label, kind, previous_documents, current_document_id, reason_stated, analyst_asked, evidence_sha256, rule_version)
            select id, 'z', '2026-03-31', 'Q4 FY26', 'Z', 'missing', array[1::bigint], (select id from equity.kpi_document where entity_id=(select id from core.entity where slug='kp47-a') and fiscal_label='Q2 FY27'), true, false, sha256('z'), 1 from core.entity where slug='kp47-a'")" 'violates check constraint'
check "T11 a reason quote over 400 code points is refused" \
  "$(as_pg "insert into equity.kpi_change (entity_id, dict_key, period_end, fiscal_label, metric_label, kind, previous_documents, current_document_id, reason_stated, reason_document_id, reason_char_start, reason_char_end, reason_quote, reason_page, analyst_asked, evidence_sha256, rule_version)
            select id, 'z2', '2026-03-31', 'Q4 FY26', 'Z', 'missing', array[1::bigint], (select id from equity.kpi_document where entity_id=(select id from core.entity where slug='kp47-a') and fiscal_label='Q2 FY27'), true, (select id from core.document where external_key='kp47-a-tr'), 0, 401, repeat('x',401), 1, false, sha256('z2'), 1 from core.entity where slug='kp47-a'")" 'violates check constraint'
check "T12 an analyst quote with analyst_asked=false is refused" \
  "$(as_pg "insert into equity.kpi_change (entity_id, dict_key, period_end, fiscal_label, metric_label, kind, previous_documents, current_document_id, reason_stated, analyst_asked, analyst_document_id, analyst_char_start, analyst_char_end, analyst_quote, analyst_page, evidence_sha256, rule_version)
            select id, 'z3', '2026-03-31', 'Q4 FY26', 'Z', 'missing', array[1::bigint], (select id from equity.kpi_document where entity_id=(select id from core.entity where slug='kp47-a') and fiscal_label='Q2 FY27'), false, false, (select id from core.document where external_key='kp47-a-tr'), 0, 5, 'hi?', 1, sha256('z3'), 1 from core.entity where slug='kp47-a'")" 'violates check constraint'
check "T13 the field policy is seeded hidden, labelled Pilot" \
  "$(as_pg "select status || ':' || label from core.field_policy where field_key='equity.kpi_changes'")" '^hidden:Pilot$'
check "T14 the view runs as the caller and anon may read it" \
  "$(as_pg "select ('security_invoker=true' = any(c.reloptions))::text || ':' || has_table_privilege('anon','api.kpi_changes','select')::text from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='api' and c.relname='kpi_changes'")" '^true:true$'
check "T15 the tables and the view carry comments" \
  "$(as_pg "select count(*) from (values ('equity.kpi_document'),('equity.kpi_value'),('equity.kpi_change'),('equity.kpi_review'),('api.kpi_changes')) v(r) where obj_description(r::regclass,'pg_class') is not null")" '^5$'

# --- the gate (hidden by default)
check "G01 hidden: anon sees no row in any table or the view" \
  "$(as_role anon "select (select count(*) from equity.kpi_document where entity_id in $ENTS) || ':' || (select count(*) from equity.kpi_change) || ':' || (select count(*) from equity.kpi_review) || ':' || (select count(*) from api.kpi_changes where $KP)")" '^0:0:0:0$'
check "G02 shown but UNreviewed: a flag returns no view row (no confirmed review yet)" \
  "$(flip anon "$SHOWN" "select count(*) from api.kpi_changes where $KP")" '^0$'
check "G03 shown + confirmed review with the matching hash: a's flag shows (one row)" \
  "$(flip anon "$SHOWN; $REV_A" "select count(*) over () || ':' || kind || ':' || metric_label || ':' || reason_stated::text || ':' || reason_quote || ':' || analyst_asked::text from api.kpi_changes where company_slug='kp47-a'")" \
  '^1:dropped:Workforce \(headcount\):true:We have stopped disclosing headcount from this quarter.:true$'
check "G04 shown + a STALE-hash review: the flag stays hidden (re-run un-shows it)" \
  "$(flip anon "$SHOWN; $REV_A_STALE" "select count(*) from api.kpi_changes where company_slug='kp47-a'")" '^0$'
check "G05 a rejected review never shows the flag" \
  "$(flip anon "$SHOWN; insert into equity.kpi_review (entity_id, dict_key, period_end, evidence_sha256, verdict, judge) select entity_id, dict_key, period_end, evidence_sha256, 'rejected', 'fable-5.2' from equity.kpi_change where entity_id=(select id from core.entity where slug='kp47-a')" "select count(*) from api.kpi_changes where company_slug='kp47-a'")" '^0$'
check "G06 anon can read only confirmed reviews directly; a rejected one stays hidden even shown" \
  "$(flip anon "$SHOWN; insert into equity.kpi_review (entity_id, dict_key, period_end, evidence_sha256, verdict, judge) select entity_id, dict_key, period_end, evidence_sha256, 'rejected', 'j' from equity.kpi_change where entity_id=(select id from core.entity where slug='kp47-b'); $REV_A" "select count(*) from equity.kpi_review")" '^1$'

# --- the view's content and the no-quote case
check "V01 b: reason not stated and no analyst question → booleans false, quotes null" \
  "$(flip anon "$SHOWN; insert into equity.kpi_review (entity_id, dict_key, period_end, evidence_sha256, verdict, judge) select entity_id, dict_key, period_end, evidence_sha256, 'confirmed', 'j' from equity.kpi_change where entity_id=(select id from core.entity where slug='kp47-b')" "select kind || ':' || reason_stated::text || ':' || coalesce(reason_quote,'-') || ':' || analyst_asked::text || ':' || coalesce(analyst_quote,'-') from api.kpi_changes where company_slug='kp47-b'")" \
  '^missing:false:-:false:-$'
check "V02 a: previous document urls are listed, source key is nse" \
  "$(flip anon "$SHOWN; $REV_A" "select array_length(previous_document_urls,1) || ':' || source_key from api.kpi_changes where company_slug='kp47-a'")" '^3:nse$'

# --- the switches
check "S01 suppressing nse hides a's and b's flags; a BSE flag would stay" \
  "$(flip anon "$SHOWN; $REV_A; insert into equity.kpi_review (entity_id, dict_key, period_end, evidence_sha256, verdict, judge) select entity_id, dict_key, period_end, evidence_sha256, 'confirmed','j' from equity.kpi_change where entity_id=(select id from core.entity where slug='kp47-x'); update core.source set is_suppressed=true where key='nse'" "select coalesce(string_agg(company_slug,','),'none') from api.kpi_changes where $KP")" '^kp47-x$'
check "S02 an unpublished company hides its rows" \
  "$(flip anon "$SHOWN; $REV_A; update core.entity set is_published=false where slug='kp47-a'" "select count(*) from api.kpi_changes where company_slug='kp47-a'")" '^0$'
check "S03 nothing above changed a stored row or the field policy" \
  "$(as_pg "select (select count(*) from equity.kpi_change where entity_id in $ENTS) || ':' || (select count(*) from equity.kpi_review where entity_id in $ENTS) || ':' || (select status from core.field_policy where field_key='equity.kpi_changes')")" '^3:0:hidden$'

cleanup
echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
