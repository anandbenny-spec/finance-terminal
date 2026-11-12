-- 0002_vc06_nse_suppression_drill: the NSE suppression drill for the value chain card (issue #191, VC-06 part 2, step 3;
-- Spec 02B §12.11 item 6). DEV ONLY, through the dev-sql workflow. Anand's OK for this one drill: his instruction to the
-- part 2 session of 12 Nov 2026 ("Do the NSE suppression drill"), recorded on #191 (CLAUDE.md §7: a drill flips a source
-- suppression).
--
-- Nothing this file does is ever committed. The workflow runs it in one transaction and the file ends with ROLLBACK, and an
-- error anywhere rolls the transaction back too, so dev's visitors never see a switch move and dev's rows are as before:
--   1. the four value-chain field policies are set to 'shown' for the length of the transaction (on dev they are hidden, so
--      a visitor sees no value-chain row to hide);
--   2. dev holds no related-party or segment rows until its egress hold ends (21 Nov 2026), so one temporary related-party
--      line and one temporary segment row are inserted on dev's newest published NSE results filing, to put those two tables'
--      policies under the switch as well;
--   3. block A counts what a visitor (role anon) sees; block B suppresses nse and counts again; block C clears it and counts
--      again;
--   4. the checks raise an error unless: every NSE-sourced count is 0 in block B, every BSE-sourced count and the module 1
--      control are unchanged in block B, and block C equals block A.
-- run_tests_0034.sh (V21, V22, R09, M07) proves the same on the test database, including a BSE-filed company's related-party
-- lines staying; dev has no BSE results filing, so that half is not repeated here.
--
-- Each block's line holds eleven counts, in this order:
--   BRSR values on NSE filings, on BSE filings · products NSE, BSE · group companies NSE, BSE · api.value_chain rows NSE, BSE ·
--   related-party lines (the temporary NSE one) · segment rows (the temporary NSE one) · api.policy_meetings (the control).
-- Counts only, never rows. The last statement before ROLLBACK prints the three lines as one GitHub notice.
set local role service_role;

do $$
begin
  if exists (select 1 from core.source where key in ('nse', 'bse') and is_suppressed) then
    raise exception 'drill not started: nse or bse is already suppressed on dev';
  end if;
end $$;

update core.field_policy set status = 'shown'
where field_key in ('equity.brsr_values', 'equity.group_companies', 'equity.rpt_counterparties', 'equity.segments');

set local role job_writer;
insert into equity.rpt_line (results_event_id, basis, line_no, counterparty, relationship, txn_type, entering_party, entered_by,
                             value_in_period, period_start, period_end)
select f.event_id, f.basis, 1, 'Drill row (rolled back)', 'drill', 'Sale of goods or services', 'drill', 'company',
       1, f.period_end - 182, f.period_end
from equity.filing f
join core.event ev on ev.id = f.event_id
where f.exchange = 'NSE' and ev.is_published and ev.status = 'filed'
  and not exists (select 1 from equity.rpt_line r where r.results_event_id = f.event_id)
order by f.event_id desc, f.basis
limit 1;
insert into equity.segment_revenue (results_event_id, basis, line_no, reporting, description, revenue, period_start, period_end)
select r.results_event_id, r.basis, 1, 'multi', 'Drill row (rolled back)', 1, r.period_end - 364, r.period_end
from equity.rpt_line r
where r.counterparty = 'Drill row (rolled back)'
  and not exists (select 1 from equity.segment_revenue s where s.results_event_id = r.results_event_id);

-- Block A: the four fields shown, nothing suppressed.
set local role anon;
select set_config('drill.a',
  (select count(*) from equity.brsr_value v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_value v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from equity.brsr_product v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_product v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from equity.brsr_group_company v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_group_company v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from api.value_chain where exchange = 'NSE') || ',' ||
  (select count(*) from api.value_chain where exchange = 'BSE') || ',' ||
  (select count(*) from equity.rpt_line) || ',' ||
  (select count(*) from equity.segment_revenue) || ',' ||
  (select count(*) from api.policy_meetings), true) as block_a;

-- Block B: nse suppressed.
set local role service_role;
update core.source set is_suppressed = true where key = 'nse';
set local role anon;
select set_config('drill.b',
  (select count(*) from equity.brsr_value v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_value v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from equity.brsr_product v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_product v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from equity.brsr_group_company v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_group_company v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from api.value_chain where exchange = 'NSE') || ',' ||
  (select count(*) from api.value_chain where exchange = 'BSE') || ',' ||
  (select count(*) from equity.rpt_line) || ',' ||
  (select count(*) from equity.segment_revenue) || ',' ||
  (select count(*) from api.policy_meetings), true) as block_b;

-- Block C: nse cleared.
set local role service_role;
update core.source set is_suppressed = false where key = 'nse';
set local role anon;
select set_config('drill.c',
  (select count(*) from equity.brsr_value v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_value v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from equity.brsr_product v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_product v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from equity.brsr_group_company v join equity.brsr_filing f using (event_id) where f.exchange = 'NSE') || ',' ||
  (select count(*) from equity.brsr_group_company v join equity.brsr_filing f using (event_id) where f.exchange = 'BSE') || ',' ||
  (select count(*) from api.value_chain where exchange = 'NSE') || ',' ||
  (select count(*) from api.value_chain where exchange = 'BSE') || ',' ||
  (select count(*) from equity.rpt_line) || ',' ||
  (select count(*) from equity.segment_revenue) || ',' ||
  (select count(*) from api.policy_meetings), true) as block_c;

-- The checks. An error here fails the run and rolls everything back.
set local role service_role;
do $$
declare
  a text[] := string_to_array(current_setting('drill.a'), ',');
  b text[] := string_to_array(current_setting('drill.b'), ',');
  c text[] := string_to_array(current_setting('drill.c'), ',');
begin
  if a[1]::int = 0 or a[2]::int = 0 or a[3]::int = 0 or a[5]::int = 0 or a[7]::int = 0 or a[8]::int = 0 then
    raise exception 'block A: a visitor sees no NSE- or BSE-filed value-chain rows with the policies shown: %', current_setting('drill.a');
  end if;
  if a[9]::int <> 1 or a[10]::int <> 1 then
    raise exception 'block A: the temporary related-party or segment row is not visible to a visitor: %', current_setting('drill.a');
  end if;
  if b[1]::int <> 0 or b[3]::int <> 0 or b[5]::int <> 0 or b[7]::int <> 0 or b[9]::int <> 0 or b[10]::int <> 0 then
    raise exception 'block B: an NSE-sourced row is still visible while nse is suppressed: %', current_setting('drill.b');
  end if;
  if b[2] <> a[2] or b[4] <> a[4] or b[6] <> a[6] or b[8] <> a[8] then
    raise exception 'block B: the BSE-filed rows changed while nse was suppressed: % against %', current_setting('drill.b'), current_setting('drill.a');
  end if;
  if b[11] <> a[11] then
    raise exception 'block B: the module 1 control changed: % against %', b[11], a[11];
  end if;
  if c <> a then
    raise exception 'block C: clearing nse did not restore block A: % against %', current_setting('drill.c'), current_setting('drill.a');
  end if;
  if exists (select 1 from core.source where key in ('nse', 'bse') and is_suppressed) then
    raise exception 'block C: a source is still suppressed';
  end if;
end $$;

copy (select '::notice title=VC-06 NSE suppression drill on dev: pass::A (shown) ' || current_setting('drill.a')
             || ' | B (nse suppressed) ' || current_setting('drill.b')
             || ' | C (cleared) ' || current_setting('drill.c')
             || ' | order: values NSE,BSE; products NSE,BSE; group NSE,BSE; api.value_chain NSE,BSE; related-party lines; segment rows; policy_meetings') to stdout;

rollback;
