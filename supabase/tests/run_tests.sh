#!/bin/bash
# Security and integrity tests for the piece-1 schema. Each test prints PASS or FAIL.
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }   # SET ROLE from postgres
login()   { psql $H -U "$1" -c "$2" 2>&1; }                                       # real login as the role
pass=0; fail=0
check() { # name, actual, expected-regex
  if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

# --- visitors (anon) -------------------------------------------------------------
check "T01 anon sees published + scheduled meetings only" \
  "$(as_role anon "select string_agg(slug, ',' order by slug) from api.policy_meetings")" '^fomc-2026-09-16,fomc-2036-10-28$'
check "T02 stance shown and labelled, hawk/dove held back" \
  "$(as_role anon "select stance||'|'||stance_display_label||'|'||coalesce(hawk_dove_score::text,'NULL')||'|'||policy_rate_pct from api.policy_meetings where slug='fomc-2026-09-16'")" '^easing\|AI-extracted\|NULL\|4\.000$'
check "T03 anon cannot read ops tables" \
  "$(as_role anon "select count(*) from ops.task_run")" 'permission denied for schema ops'
check "T04 anon cannot read raw files or embeddings" \
  "$(as_role anon "select count(*) from core.raw_file"; as_role anon "select count(*) from core.chunk_embedding")" 'permission denied for table raw_file.*permission denied for table chunk_embedding'
check "T05 anon reading a base table directly still gets published rows only" \
  "$(as_role anon "select count(*) from core.document")" '^1$'
check "T06 citation quote is rebuilt across two chunks" \
  "$(as_role anon "select quote from api.ai_citations")" '^Committee decided to lower the target range by 1/4 point\. Inflation remains $'
check "T07 published series visible with date-ranged label; unpublished series hidden" \
  "$(as_role anon "select string_agg(series_key||':'||coalesce(label,'-'), ',' order by obs_date) from api.observations")" '^fred\.DGS2:back-calculated,fred\.DGS2:back-calculated,fred\.DGS2:-,fred\.DGS2:-,fred\.DGS2:-$'
check "T08 next-meeting countdown" \
  "$(as_role anon "select slug from api.next_meetings")" '^fomc-2036-10-28$'
check "T09 hawk/dove output hidden from api.ai_outputs; stance visible" \
  "$(as_role anon "select string_agg(task, ',') from api.ai_outputs")" '^stance_extraction$'
as_pg "update core.source set is_suppressed = true where key = 'fed'" >/dev/null
check "T10 suppressing a source hides its events, documents and AI outputs at once" \
  "$(as_role anon "select (select count(*) from api.policy_meetings)||','||(select count(*) from api.documents)||','||(select count(*) from api.ai_outputs)||','||(select count(*) from api.document_chunks)")" '^0,0,0,0$'
as_pg "update core.source set is_suppressed = false where key = 'fed'" >/dev/null
check "T11 anon cannot see the health view" \
  "$(as_role anon "select count(*) from ops.task_health")" 'permission denied for schema ops'

# --- text-to-SQL role (real login) -------------------------------------------------
check "T12 sql_reader reads the semantic view (published only, hidden field null)" \
  "$(login sql_reader "select string_agg(meeting_date||'|'||status||'|'||coalesce(policy_rate_pct::text,'-')||'|'||coalesce(hawk_dove_score::text,'NULL')||'|'||coalesce(stance,'-')||'|'||coalesce(us_2y_change_bps::text,'-'), ';' order by meeting_date) from policy_meetings")" \
  '^2026-09-16\|held\|4\.000\|NULL\|easing\|-8;2036-10-28\|scheduled\|-\|NULL\|-\|-$'
check "T13 sql_reader cannot touch base tables" \
  "$(login sql_reader "select count(*) from core.event")" 'permission denied for schema core'
check "T14 sql_reader cannot see settings" \
  "$(login sql_reader "select count(*) from ops.setting")" 'permission denied for schema ops'
check "T15 sql_reader sessions are read-only" \
  "$(login sql_reader "create temp table t(x int)")" 'read-only transaction'
check "T16 statement timeout from the role default" \
  "$(login sql_reader "select pg_sleep(5)")" 'statement timeout'
check "T17 GAP: set_config() inside a SELECT turns the role's timeout off for the session" \
  "$(login sql_reader "select set_config('statement_timeout','0',false); select pg_sleep(4.0)::text || 'slept'")" 'slept'
check "T18 wrapper's SET LOCAL restores the timeout even after that" \
  "$(login sql_reader "select set_config('statement_timeout','0',false); begin read only; set local statement_timeout='2s'; select pg_sleep(4); rollback;")" 'statement timeout'
check "T19 direct login: set_config('role', ...) cannot escalate" \
  "$(login sql_reader "select set_config('role','postgres',false)")" 'permission denied to set role'
as_pg "drop role if exists pooler_admin; create role pooler_admin login; grant sql_reader to pooler_admin" >/dev/null
check "T20 GAP: connect-as-admin + SET ROLE sql_reader can be undone from inside a SELECT" \
  "$(login pooler_admin "set role sql_reader; select set_config('role','pooler_admin',false); select current_user")" 'pooler_admin$'
check "T21 sql_reader can execute no function in our schemas except the retrieval function (0010)" \
  "$(as_pg "select coalesce(string_agg(n.nspname||'.'||p.proname, ','), 'none') from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('ops','core','policy','api','semantic') and has_function_privilege('sql_reader', p.oid, 'execute')")" '^(none|semantic\.search_chunks)$'
check "T22 INFO: functions sql_reader can run outside pg_catalog (extensions schema)" \
  "$(as_pg "select 'n=' || count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema','ops','core','policy','api','semantic') and has_schema_privilege('sql_reader', n.oid, 'usage') and has_function_privilege('sql_reader', p.oid, 'execute')")" '^n=[0-9]+$'

# --- jobs ------------------------------------------------------------------------
check "T23 job_writer cannot delete observations (append-only)" \
  "$(as_role job_writer "delete from core.observation")" 'permission denied for table observation'
check "T24 a changed value is logged, not lost" \
  "$(as_role job_writer "update core.observation set value = 9.99 where obs_date = '2026-09-16' and series_id = (select id from core.series where key='fred.DGS2'); select count(*) || ':' || max(new_value) from core.observation_revision")" '^UPDATE 1.*1:9\.99$|^1:9\.99$'
check "T25 job_writer cannot rewrite the revision log" \
  "$(as_role job_writer "update core.observation_revision set new_value = 0")" 'permission denied'
check "T26 an AI output cannot be published without a verified citation" \
  "$(as_role job_writer "insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256) select 'doc_summary', id, '{}', 'x', 'published', 'm', 'p', 'v1', sha256('c'::bytea) from core.document where doc_type='fomc_statement'")" 'cannot be published without a verified citation'
check "T27 only one published output per task and target" \
  "$(as_role job_writer "insert into core.ai_output (task, event_id, content, status, model, prompt_key, prompt_version, input_sha256) select 'stance_extraction', id, '{}', 'published', 'm', 'p', 'v2', sha256('d'::bytea) from core.event where slug='fomc-2026-09-16'")" 'duplicate key value violates unique constraint "ai_output_one_published"'
check "T28 one entity per identifier at a time" \
  "$(as_role job_writer "insert into core.entity_identifier (entity_id, scheme, value, valid) values (1,'ticker','XYZ','[2020-01-01,)'), (2,'ticker','XYZ','[2024-01-01,)')")" 'conflicting key value violates exclusion constraint'
check "T29 every company carries market and currency" \
  "$(as_pg "insert into core.entity (kind, slug, name) values ('company','acme','Acme')")" 'violates check constraint'

# --- catalogue checks --------------------------------------------------------------
check "T30 RLS is on for every table in ops, core, policy" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind='r' and n.nspname in ('ops','core','policy') and not c.relrowsecurity")" '^0$'
check "T31 every api view runs as the caller" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='api' and c.relkind='v' and not coalesce('security_invoker=true' = any(c.reloptions), false)")" '^0$'
check "T32 every semantic view is owned by semantic_owner" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='semantic' and c.relkind='v' and pg_get_userbyid(c.relowner) <> 'semantic_owner'")" '^0$'
check "T33 semantic_owner is not a table owner and has no BYPASSRLS" \
  "$(as_pg "select rolbypassrls::text || ',' || (select count(*) from pg_class where relowner = r.oid and relkind='r') from pg_roles r where rolname='semantic_owner'")" '^false,0$'
check "T34 service_role can file a refresh request" \
  "$(as_role service_role "insert into ops.refresh_request (task_key, requested_by, status) values ('policy.market_series','admin','dispatched') returning status")" 'dispatched'

# --- regressions for the independent review's findings (20 Oct) ----------------------------
check "T35 hidden field can't be probed through an error (cast)" \
  "$(login sql_reader "begin read only; set local statement_timeout='3s'; select count(*) from (select meeting_date from policy_meetings where (hawk_dove_score::text||' <-hidden')::int > 0) q; rollback;")" '^BEGIN.*0.*ROLLBACK$|^0$'
check "T36 hidden field can't be probed through an error (division)" \
  "$(login sql_reader "select count(*) from policy_meetings where 1/(hawk_dove_score+1) is not null")" '^0$'
check "T37 a hidden AI task's rows are invisible even on the base table" \
  "$(as_role anon "select count(*) from core.ai_output where task='hawk_dove_score'")" '^0$'
check "T38 job can't un-verify, move or add citations once an output is published" \
  "$(as_role job_writer "update core.ai_citation set verified=false"; as_role job_writer "insert into core.ai_citation select id, 9, '/x', 'event', null,null,null,null,null,event_id, true from core.ai_output where task='stance_extraction'")" 'permission denied for table ai_citation.*citations of a non-draft ai_output are fixed'
check "T39 job can't switch off the citation requirement" \
  "$(as_role job_writer "update core.ai_task set requires_citations=false")" 'permission denied for table ai_task'
check "T40 a citation outside the document's text (or in an unpublished document) doesn't count" \
  "$(as_role job_writer "insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256) select 'doc_summary', id, '{}', 'x', 'draft', 'm','p','v1', sha256('e'::bytea) from core.document where doc_type='fomc_statement'; insert into core.ai_citation select max(o.id), 1, '/x', 'document_span', d.id, 100000, 100001, null, null, null, true from core.ai_output o, core.document d where d.doc_type='fomc_minutes' group by d.id; update core.ai_output set status='published' where task='doc_summary'")" 'cannot be published without a verified citation'
check "T41 a published output can't be edited, only superseded or rejected" \
  "$(as_role job_writer "update core.ai_output set body='REWRITTEN', label='hawkish' where task='stance_extraction'"; as_role job_writer "update core.ai_output set status='superseded', status_reason='test' where task='stance_extraction' returning status")" 'can only move to superseded or rejected.*superseded'
as_pg "update core.ai_output set status='published', status_reason=null where task='stance_extraction'" >/dev/null 2>&1 || true
check "T42 job can't flip editorial switches (field policy, suppression, settings)" \
  "$(as_role job_writer "update core.field_policy set status='shown'"; as_role job_writer "update core.source set is_suppressed=false"; as_role job_writer "update ops.setting set value='true'")" 'permission denied for table field_policy.*permission denied for table source.*permission denied for table setting'
check "T43 admin route (service_role) can flip a field policy and a source switch" \
  "$(as_role service_role "update core.field_policy set updated_at=now() where field_key='ai.doc_summary' returning field_key; update core.source set is_suppressed=false where key='rbi' returning key")" '^ai\.doc_summary.rbi$'
check "T44 an AI-extracted forecast whose output isn't published is hidden" \
  "$(as_role job_writer "insert into core.ai_output (task, entity_id, content, status, model, prompt_key, prompt_version, input_sha256) values ('projection_extraction', 2, '{}', 'draft','m','p','v1', sha256('f'::bytea)); insert into core.forecast (series_id, vintage_date, target_start, target_end, target_label, value, method, document_id, char_start, char_end, ai_output_id, task_run_id) select (select id from core.series where key='rbi.proj_cpi'), '2026-10-01', '2027-01-01', '2027-03-31', 'Q4 2026-27', 4.2, 'ai_extracted', (select id from core.document where doc_type='fomc_statement'), 0, 5, (select max(id) from core.ai_output where task='projection_extraction'), (select max(id) from ops.task_run)" >/dev/null; as_role anon "select count(*) from api.forecasts")" '^1$'
check "T45 re-chunking a document is allowed if the text is unchanged" \
  "$(as_role job_writer "begin; delete from core.chunk where document_id=(select id from core.document where doc_type='fomc_statement'); insert into core.chunk (document_id, chunk_no, char_start, char_end, body) select id, 0, 0, char_count, 'The Committee decided to lower the target range by 1/4 point. Inflation remains somewhat elevated.' from core.document where doc_type='fomc_statement'; set constraints all immediate; select 'rechunk-ok'; rollback;")" 'rechunk-ok'
check "T46 changing a published document's text under its citations is refused" \
  "$(as_role job_writer "begin; delete from core.chunk where document_id=(select id from core.document where doc_type='fomc_statement') and chunk_no=1; insert into core.chunk (document_id, chunk_no, char_start, char_end, body) select id, 1, 61, 61+char_length(' Inflation is now low!!!!!!!!!!!!!!'), ' Inflation is now low!!!!!!!!!!!!!!' from core.document where doc_type='fomc_statement'; set constraints all immediate; rollback;"; as_role job_writer "update core.document set text_sha256=sha256('z'::bytea) where doc_type='fomc_statement'")" 'do not reproduce its text exactly.*immutable here'
check "T47 observation keys can't move, and the job can't forge the revision log" \
  "$(as_role job_writer "update core.observation set obs_date='2030-01-01' where obs_date='2026-09-14'"; as_role job_writer "insert into core.observation_revision values (1, '2026-09-14', 1, 2, now(), 1)")" 'observation keys are fixed.*permission denied for table observation_revision'
check "T48 every semantic view is a security barrier" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='semantic' and c.relkind='v' and not coalesce('security_barrier=true' = any(c.reloptions), false)")" '^0$'

echo; echo "passed $pass, failed $fail"
