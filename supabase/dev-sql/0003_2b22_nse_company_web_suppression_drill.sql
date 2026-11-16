-- 0003_2b22_nse_company_web_suppression_drill: the NSE suppression drill for release 2B-lite (issue #185, 2B-22, step 5;
-- Spec 02B §13.10 item 6), with company_web suppressed together with nse (Anand's decision on #168, 1 Nov 2026, 02:23 IST,
-- option (a): that is the procedure when NSE objects, because a company-hosted transcript's document has company_web as its
-- source and the core AI views follow the document's source). DEV ONLY, through the dev-sql workflow, and only with Anand's
-- OK for this drill recorded on #185 (CLAUDE.md §7: a drill flips a source suppression).
--
-- Nothing this file does is ever committed. The workflow runs it in one transaction and the file ends with ROLLBACK; an error
-- anywhere rolls it back too. So dev's visitors never see a switch move and dev's rows are as before:
--   1. ai.call_summary, ai.guidance_extraction and ai.call_tone are set to 'shown' for the length of the transaction (on dev
--      they are hidden, so a visitor would see no summary, figure, tone or quote to hide);
--   2. block A counts what a visitor (role anon) sees; block B suppresses nse and company_web together and counts again;
--      block C clears both and counts again;
--   3. the checks raise an error unless: every earnings-call count is 0 in block B (calls on every route, the latest-call list,
--      figures, guidance, page links, the three tasks' outputs, transcript quotes, transcript documents, and the base tables a
--      visitor can select), the module 1 controls are unchanged in block B, and block C equals block A.
-- Every call on dev was discovered on NSE (checked 12 Nov 2026 through the read-only connector: 331 calls, exchange NSE, event
-- source nse; documents nse or company_web), so no BSE-filed call is expected to stay in block B.
--
-- Each block's line holds sixteen counts, in this order:
--   1 api.earnings_calls · 2 of them route exchange_pdf · 3 route company_link or curated_link · 4 api.earnings_latest rows
--   with a call · 5 api.call_figures · 6 api.call_guidance · 7 api.citation_pages · 8 api.ai_outputs of call_summary,
--   guidance_extraction and call_tone · 9 api.ai_citations into private-text documents · 10 api.documents of type
--   call_transcript or call_cover_letter · 11 equity.earnings_call · 12 equity.guidance and equity.stated_figure ·
--   13 equity.transcript_page · 14 equity.call_tone · 15 api.policy_meetings (control) · 16 api.ai_outputs of every other
--   task (control: the Fed and RBI summaries).
-- Counts only, never rows. The last statement before ROLLBACK prints the three lines as one GitHub notice.
set local role service_role;

do $$
begin
  if exists (select 1 from core.source where key in ('nse', 'bse', 'company_web') and is_suppressed) then
    raise exception 'drill not started: nse, bse or company_web is already suppressed on dev';
  end if;
end $$;

update core.field_policy set status = 'shown'
where field_key in ('ai.call_summary', 'ai.guidance_extraction', 'ai.call_tone');

-- Block A: the three fields shown, nothing suppressed.
set local role anon;
select set_config('drill.a',
  (select count(*) from api.earnings_calls) || ',' ||
  (select count(*) from api.earnings_calls where route = 'exchange_pdf') || ',' ||
  (select count(*) from api.earnings_calls where route in ('company_link', 'curated_link')) || ',' ||
  (select count(*) from api.earnings_latest where call_slug is not null) || ',' ||
  (select count(*) from api.call_figures) || ',' ||
  (select count(*) from api.call_guidance) || ',' ||
  (select count(*) from api.citation_pages) || ',' ||
  (select count(*) from api.ai_outputs where task in ('call_summary', 'guidance_extraction', 'call_tone')) || ',' ||
  (select count(*) from api.ai_citations where text_public = false) || ',' ||
  (select count(*) from api.documents where doc_type in ('call_transcript', 'call_cover_letter')) || ',' ||
  (select count(*) from equity.earnings_call) || ',' ||
  ((select count(*) from equity.guidance) + (select count(*) from equity.stated_figure)) || ',' ||
  (select count(*) from equity.transcript_page) || ',' ||
  (select count(*) from equity.call_tone) || ',' ||
  (select count(*) from api.policy_meetings) || ',' ||
  (select count(*) from api.ai_outputs where task not in ('call_summary', 'guidance_extraction', 'call_tone')), true) as block_a;

-- Block B: nse and company_web suppressed together.
set local role service_role;
update core.source set is_suppressed = true where key in ('nse', 'company_web');
set local role anon;
select set_config('drill.b',
  (select count(*) from api.earnings_calls) || ',' ||
  (select count(*) from api.earnings_calls where route = 'exchange_pdf') || ',' ||
  (select count(*) from api.earnings_calls where route in ('company_link', 'curated_link')) || ',' ||
  (select count(*) from api.earnings_latest where call_slug is not null) || ',' ||
  (select count(*) from api.call_figures) || ',' ||
  (select count(*) from api.call_guidance) || ',' ||
  (select count(*) from api.citation_pages) || ',' ||
  (select count(*) from api.ai_outputs where task in ('call_summary', 'guidance_extraction', 'call_tone')) || ',' ||
  (select count(*) from api.ai_citations where text_public = false) || ',' ||
  (select count(*) from api.documents where doc_type in ('call_transcript', 'call_cover_letter')) || ',' ||
  (select count(*) from equity.earnings_call) || ',' ||
  ((select count(*) from equity.guidance) + (select count(*) from equity.stated_figure)) || ',' ||
  (select count(*) from equity.transcript_page) || ',' ||
  (select count(*) from equity.call_tone) || ',' ||
  (select count(*) from api.policy_meetings) || ',' ||
  (select count(*) from api.ai_outputs where task not in ('call_summary', 'guidance_extraction', 'call_tone')), true) as block_b;

-- Block C: both cleared.
set local role service_role;
update core.source set is_suppressed = false where key in ('nse', 'company_web');
set local role anon;
select set_config('drill.c',
  (select count(*) from api.earnings_calls) || ',' ||
  (select count(*) from api.earnings_calls where route = 'exchange_pdf') || ',' ||
  (select count(*) from api.earnings_calls where route in ('company_link', 'curated_link')) || ',' ||
  (select count(*) from api.earnings_latest where call_slug is not null) || ',' ||
  (select count(*) from api.call_figures) || ',' ||
  (select count(*) from api.call_guidance) || ',' ||
  (select count(*) from api.citation_pages) || ',' ||
  (select count(*) from api.ai_outputs where task in ('call_summary', 'guidance_extraction', 'call_tone')) || ',' ||
  (select count(*) from api.ai_citations where text_public = false) || ',' ||
  (select count(*) from api.documents where doc_type in ('call_transcript', 'call_cover_letter')) || ',' ||
  (select count(*) from equity.earnings_call) || ',' ||
  ((select count(*) from equity.guidance) + (select count(*) from equity.stated_figure)) || ',' ||
  (select count(*) from equity.transcript_page) || ',' ||
  (select count(*) from equity.call_tone) || ',' ||
  (select count(*) from api.policy_meetings) || ',' ||
  (select count(*) from api.ai_outputs where task not in ('call_summary', 'guidance_extraction', 'call_tone')), true) as block_c;

-- The checks. An error here fails the run and rolls everything back.
set local role service_role;
do $$
declare
  a text[] := string_to_array(current_setting('drill.a'), ',');
  b text[] := string_to_array(current_setting('drill.b'), ',');
  c text[] := string_to_array(current_setting('drill.c'), ',');
  i int;
begin
  -- Block A must have something to hide on every route and in every list the drill watches.
  for i in 1..14 loop
    if a[i]::int = 0 then
      raise exception 'block A: count % is 0 with the fields shown and nothing suppressed, so the drill would prove nothing: %', i, current_setting('drill.a');
    end if;
  end loop;
  for i in 1..14 loop
    if b[i]::int <> 0 then
      raise exception 'block B: count % is still % while nse and company_web are suppressed: %', i, b[i], current_setting('drill.b');
    end if;
  end loop;
  if b[15] <> a[15] or b[16] <> a[16] then
    raise exception 'block B: a module 1 control changed: % against %', current_setting('drill.b'), current_setting('drill.a');
  end if;
  if c <> a then
    raise exception 'block C: clearing nse and company_web did not restore block A: % against %', current_setting('drill.c'), current_setting('drill.a');
  end if;
  if exists (select 1 from core.source where key in ('nse', 'bse', 'company_web') and is_suppressed) then
    raise exception 'block C: a source is still suppressed';
  end if;
end $$;

copy (select '::notice title=2B-22 NSE and company_web suppression drill on dev: pass::A (fields shown) ' || current_setting('drill.a')
             || ' | B (nse and company_web suppressed) ' || current_setting('drill.b')
             || ' | C (cleared) ' || current_setting('drill.c')
             || ' | order: calls all, exchange_pdf, company or curated link; latest with a call; figures; guidance; page links;'
             || ' call AI outputs; transcript citations; transcript documents; earnings_call; guidance and stated figures;'
             || ' transcript pages; call_tone; policy_meetings (control); other AI outputs (control)') to stdout;

rollback;
