#!/bin/bash
# Tests for 0025 (the labelled correction on equity.shareholding and api.company_promoter_holding; issue #116).
# Run after the other scripts on the same fresh database. The script makes its own rows (entity eq25-a) and removes them.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
cleanup() {
  as_pg "delete from equity.shareholding where entity_id in (select id from core.entity where slug like 'eq25%');
         delete from core.document where external_key like 'eq25%';
         delete from core.entity where slug like 'eq25%';
         update core.source set is_suppressed = false where key = 'nse';" >/dev/null
}
cleanup
psql $H -U postgres -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || echo "FAIL  setup of the test rows"
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company','eq25-a','Eq25 Alpha Ltd','Alpha','IN','INR',true);
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
select 'shareholding_xbrl', e.id, 'eq25 ' || v.k, (select id from core.source where key = 'nse'), 'eq25-' || v.k, 'https://example.invalid/' || v.k
from core.entity e, (values ('1'),('2'),('3')) v(k) where e.slug = 'eq25-a';
-- 1: an ordinary row; 2: a corrected row for the next quarter; 3: a revision of row 2's quarter (filed later, ordinary)
insert into equity.shareholding (document_id, entity_id, as_of, promoter_pct, promoter_shares, total_shares, filed_at, source_url, exchange)
select d.id, d.entity_id, '2021-06-30', 60.8, 608, 1000, '2021-07-15', d.source_url, 'NSE' from core.document d where d.external_key = 'eq25-1';
insert into equity.shareholding (document_id, entity_id, as_of, promoter_pct, promoter_shares, total_shares, filed_at, source_url, exchange,
                                 correction, filed_pct, share_ratio_pct, nse_pct)
select d.id, d.entity_id, '2021-09-30', 66.13, 6613, 10000, '2021-10-05', d.source_url, 'NSE', 'percent_for_fraction', 6613, 66.13, 66.13
from core.document d where d.external_key = 'eq25-2';
insert into equity.shareholding (document_id, entity_id, as_of, promoter_pct, filed_at, source_url, exchange)
select d.id, d.entity_id, '2021-09-30', 66.2, '2021-11-01', d.source_url, 'NSE' from core.document d where d.external_key = 'eq25-3';
SQL

check "B1 a corrected row needs its evidence (filed value and share-count ratio)" \
  "$(as_pg "update equity.shareholding set filed_pct = null where correction is not null")" 'violates check constraint "shareholding_correction_evidence"'
check "B2 an ordinary row cannot carry correction evidence" \
  "$(as_pg "update equity.shareholding set filed_pct = 1 where correction is null and as_of = '2021-06-30'")" 'violates check constraint "shareholding_correction_evidence"'
check "B3 only the one correction kind is allowed" \
  "$(as_pg "update equity.shareholding set correction = 'guess' where as_of = '2021-06-30'")" 'violates check constraint'
check "B4 promoter_pct stays between 0 and 100" \
  "$(as_pg "update equity.shareholding set promoter_pct = 6613 where as_of = '2021-06-30'")" 'violates check constraint'
check "B5 job_writer can write the new columns" \
  "$(as_role job_writer "update equity.shareholding set nse_pct = null where as_of = '2021-06-30' and source_url like 'https://example.invalid/%' returning as_of")" '^2021-06-30$'
check "B6 the view shows the latest filed pattern per company and quarter" \
  "$(as_role anon "select string_agg(as_of||' '||promoter_pct, ',' order by as_of) from api.company_promoter_holding where slug = 'eq25-a'")" '^2021-06-30 60.8,2021-09-30 66.2$'
as_pg "delete from equity.shareholding where document_id = (select id from core.document where external_key = 'eq25-3')" >/dev/null
check "B7 a corrected row reaches visitors with its label and evidence" \
  "$(as_role authenticated "select promoter_pct||' '||label_kind||' '||filed_pct||' '||share_ratio_pct||' '||nse_pct||' '||source_key from api.company_promoter_holding where slug = 'eq25-a' and correction is not null")" '^66.13 derived 6613 66.13 66.13 nse$'
check "B8 an ordinary row has no label and no evidence" \
  "$(as_role anon "select coalesce(label_kind,'none')||' '||coalesce(filed_pct::text,'none') from api.company_promoter_holding where slug = 'eq25-a' and correction is null")" '^none none$'
check "B9 the semantic reader has no access to the api view" \
  "$(as_role sql_reader "select count(*) from api.company_promoter_holding")" 'permission denied'
as_pg "update core.source set is_suppressed = true where key = 'nse'" >/dev/null
check "B10 NSE's switch hides the rows" \
  "$(as_role anon "select count(*) from api.company_promoter_holding where slug = 'eq25-a'")" '^0$'
as_pg "update core.source set is_suppressed = false where key = 'nse'" >/dev/null
as_pg "update core.entity set is_published = false where slug = 'eq25-a'" >/dev/null
check "B11 an unpublished company's rows are hidden" \
  "$(as_role anon "select count(*) from api.company_promoter_holding where slug = 'eq25-a'")" '^0$'
check "B12 the view is a security_invoker view" \
  "$(as_pg "select reloptions::text from pg_class where relname = 'company_promoter_holding'")" 'security_invoker=true'
cleanup
check "B13 the test rows are gone" \
  "$(as_pg "select (select count(*) from core.entity where slug like 'eq25%')+(select count(*) from core.document where external_key like 'eq25%')")" '^0$'
echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
