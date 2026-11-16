-- 0052: cmdbar_semantic (issue #579, job K-03; Spec 07 §5, N1). The command bar's data layer, built hidden. Approved: Plan 06
-- §5.3 names it, and Anand's brief of 15 Nov 2026, 18:38 IST asked for its number (claimed on #30), the daily cap of 100 and "the
-- kill switch off by default".
--
-- What changes (no table, column, index, policy, field_policy row or api view changes; no existing view's columns or rows):
--  * semantic.earnings_calls: one row per published earnings call of a Nifty 50 company (India only, as every company-level
--    semantic view: Spec 02A.1 §6; US rows are K-03b, #580). Its quarter, call date, filing and transcript status, and the call's
--    tone only while field policy ai.call_tone is shown or labelled. Never transcript text, summary text or a quote.
--  * semantic.call_guidance: one row per guidance target management stated on a published call, as api.call_guidance (0048)
--    lists them, with its outcome only while equity.guidance_outcome is shown or labelled. The hit rates, and the columns that wait
--    for equity.guidance_hit_rate (is_first, revision, horizon_share, counted, the first statement), are left out (Spec 07 §5).
--  * Both follow the semantic pattern (0007, 0024): owned by semantic_owner (no table, no BYPASSRLS, so the base tables' visitor
--    policies decide publication and the source switches), security_barrier, gated columns nulled by a CASE on core.field_policy
--    inside the view (Arch 01 §1.4), a COMMENT on the view and every column, select for sql_reader only.
--  * COMMENTs on the existing semantic views (the text-to-SQL prompt's schema description: Arch 01 §3), made complete and true
--    (Spec 07 §5 item 3): the 35 columns of module 1's four views that had none; policy_meetings' range ("since 1 Jan 2019": its
--    rows start with each bank's December 2018 meeting); policy_projections' row grain; market_daily's list of series (it left out
--    Damodaran's three valuation inputs, which the view returns). Comments only: each view's definition is untouched.
--  * ops.setting (N1): cmdbar_ai_enabled to false (the route answers no AI question while it is false) and cmdbar_daily_cap to 100.
--    On dev and production both held true and 300, the 0002 seeds.
--  * core.ai_task: no rows (Spec 07 §5 item 5). ft/ai/client.py writes no row that names a task: ops.ai_request, the one table keyed
--    on core.ai_task, is written by the batch path (ft/ai/batch.py) only, and the bar logs to ops.cmdbar_log and ops.eval_run.

------------------------------------------------------------------ semantic.earnings_calls
-- The rows api.earnings_calls returns for an Indian company: the call's event and company visible (their policies bring
-- publication and the event source's switch) and, where it has a transcript, that document visible (published, its source not
-- suppressed). The previous call's tone change shows only where that call is visible too (as api.call_tone).
create view semantic.earnings_calls with (security_barrier = true) as
with g as (   -- field policy, read once per query; a missing row is hidden (fails closed, Arch 01 §1.4)
  select coalesce(bool_or(status in ('shown','labelled')), false) as tone,
         max(label) filter (where status in ('shown','labelled')) as tone_label
  from core.field_policy
  where field_key = 'ai.call_tone'
)
select e.name as company, c.nse_symbol, e.market, e.currency,
       ec.fiscal_label as fiscal_quarter, ec.period_end as quarter_end, ec.call_date, ec.filed_at, ec.exchange,
       ev.status as transcript_status,
       case when g.tone then t.mean end as tone_mean,
       case when g.tone and pv.event_id is not null then t.change end as tone_change,
       g.tone_label
from equity.earnings_call ec
cross join g
join core.event ev on ev.id = ec.event_id
join core.entity e on e.id = ec.entity_id
join equity.company c on c.entity_id = ec.entity_id
left join core.document d on d.id = ec.transcript_document_id
left join equity.call_tone t on t.event_id = ec.event_id
left join lateral (   -- the previous call, only where it is visible with its transcript
  select p.event_id
  from equity.earnings_call p
  join core.event pe on pe.id = p.event_id
  join core.document pd on pd.id = p.transcript_document_id
  where p.event_id = t.prev_event_id
) pv on true
where e.market = 'IN'
  and (ec.transcript_document_id is null or d.id is not null);

------------------------------------------------------------------ semantic.call_guidance
-- api.call_guidance's rows (0048: a published guidance_extraction output, management turns only, duplicates left out) for an
-- Indian company, and its columns less the ones that need equity.guidance_hit_rate, the identifiers the site uses (slugs, output
-- and citation numbers) and the peer group. No transcript text: metric_text (the words as said) stays out, as in api.call_guidance.
create view semantic.call_guidance with (security_barrier = true) as
with g as (   -- field policy, read once per query; a missing row is hidden (fails closed, Arch 01 §1.4)
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.guidance_extraction'), false) as ai_ok,
         max(label) filter (where field_key = 'ai.guidance_extraction' and status in ('shown','labelled')) as ai_label,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false) as out_ok,
         max(label) filter (where field_key = 'equity.guidance_outcome' and status in ('shown','labelled')) as out_label
  from core.field_policy
  where field_key in ('ai.guidance_extraction','equity.guidance_outcome')
)
select e.name as company, c.nse_symbol, e.market, e.currency,
       ec.fiscal_label as fiscal_quarter, ec.call_date,
       x.metric, x.metric_label, x.kind, x.value, x.low, x.high, x.unit, x.direction, x.comparison, x.growth_basis, x.basis,
       x.period_text, x.period_kind, x.target_start, x.target_end, x.scope_text, x.vintage_date as stated_on,
       x.not_scored_reason,
       g.ai_label as extraction_label,
       case when g.out_ok then o.outcome end  as outcome,
       case when g.out_ok then o.relation end as relation,
       case when g.out_ok then o.actual end   as actual,
       case when g.out_ok then o.base end     as base,
       case when g.out_ok then o.reason end   as outcome_reason,
       case when g.out_ok then g.out_label end as outcome_label
from equity.guidance x
cross join g
join core.ai_output ai on ai.id = x.ai_output_id and ai.task = 'guidance_extraction' and ai.status = 'published'
join equity.earnings_call ec on ec.event_id = x.call_event_id
join core.event ev on ev.id = ec.event_id
join core.entity e on e.id = x.entity_id
join equity.company c on c.entity_id = x.entity_id
left join equity.guidance_outcome o on o.guidance_id = x.id
where g.ai_ok and x.duplicate_of is null and x.speaker_role = 'management' and e.market = 'IN';

------------------------------------------------------------------ owner and grants (sql_reader only)
alter view semantic.earnings_calls owner to semantic_owner;
alter view semantic.call_guidance  owner to semantic_owner;
revoke all on semantic.earnings_calls, semantic.call_guidance from public, anon, authenticated, service_role, job_writer;
grant select on semantic.earnings_calls, semantic.call_guidance to sql_reader;

------------------------------------------------------------------ comments: the two new views
comment on view semantic.earnings_calls is
  'One row per published earnings call of a Nifty 50 company (Indian companies only): the fiscal quarter it discusses, the call date, when and where its transcript was filed, whether the transcript is stored here, and the call''s AI-scored tone only once that score is released. Join to semantic.call_guidance on company and fiscal_quarter. Never transcript text or summaries. Informational only, not investment advice.';
comment on column semantic.earnings_calls.company is
  'Company name as listed (as semantic.companies.company).';
comment on column semantic.earnings_calls.nse_symbol is
  'NSE trading symbol (current).';
comment on column semantic.earnings_calls.market is
  'IN (India): every row is an Indian company.';
comment on column semantic.earnings_calls.currency is
  'Reporting currency of the company (INR).';
comment on column semantic.earnings_calls.fiscal_quarter is
  'The fiscal quarter whose results the call discusses, such as ''Q2 FY27'' (Indian fiscal years run April to March and are named by the year they end: Q2 FY27 is July to September 2026); null while it could not be read.';
comment on column semantic.earnings_calls.quarter_end is
  'Last day of that fiscal quarter; null when fiscal_quarter is null.';
comment on column semantic.earnings_calls.call_date is
  'Date the call was held, from the transcript or the company''s call notice; null when neither states it.';
comment on column semantic.earnings_calls.filed_at is
  'When the company''s transcript filing was disseminated by the exchange (timestamp with time zone).';
comment on column semantic.earnings_calls.exchange is
  'NSE or BSE: the exchange whose filing carried the transcript or its link.';
comment on column semantic.earnings_calls.transcript_status is
  'stored (the transcript is stored and read here), on_company_website (the filing links to the company''s website, the transcript is not stored here) or unreadable.';
comment on column semantic.earnings_calls.tone_mean is
  'AI-scored tone of management''s remarks on the call: the mean of its passage scores on a -2 (most negative) to +2 (most positive) scale; null while the tone score is held back, or for a call it could not score.';
comment on column semantic.earnings_calls.tone_change is
  'Tone against the company''s previous call: more_positive, more_negative or little_changed; null while held back, or where there is no visible previous call.';
comment on column semantic.earnings_calls.tone_label is
  'Label to show with tone_mean and tone_change (''AI-scored''); null while the tone score is held back.';

comment on view semantic.call_guidance is
  'One row per guidance target management stated on a published earnings call of a Nifty 50 company (Indian companies only); a target stated again on a later call has a row for each call. AI-extracted from the transcript (extraction_label) and, once released, scored against the company''s later filed results (outcome, labelled derived). value, low, high, actual and base are in the unit column''s unit: pct = percent or percentage points, inr = rupees in crore (1 crore = 10 million rupees), other = another currency or scale, never comparable with the rest. Never add or compare inr amounts with another currency. No hit rates. A missed target is a fact about a figure, not a verdict on management. Informational only, not investment advice.';
comment on column semantic.call_guidance.company is
  'Company name as listed (as semantic.companies.company).';
comment on column semantic.call_guidance.nse_symbol is
  'NSE trading symbol (current).';
comment on column semantic.call_guidance.market is
  'IN (India): every row is an Indian company.';
comment on column semantic.call_guidance.currency is
  'Reporting currency of the company (INR); unit inr amounts are in it.';
comment on column semantic.call_guidance.fiscal_quarter is
  'Fiscal quarter of the call the target was stated on, such as ''Q2 FY27'' (as semantic.earnings_calls.fiscal_quarter); null while it could not be read.';
comment on column semantic.call_guidance.call_date is
  'Date of the call the target was stated on; null when not stated.';
comment on column semantic.call_guidance.metric is
  'Metric key: revenue, revenue_growth, ebitda_margin, ebit_margin, pat (profit after tax), pat_growth, capex, net_debt, tax_rate, advances_growth (loan growth), deposits_growth, gnpa, nnpa, cet1, or other for a metric outside that list (never scored).';
comment on column semantic.call_guidance.metric_label is
  'Display name of the metric, such as ''Revenue growth'' (''Other'' for other).';
comment on column semantic.call_guidance.kind is
  'point (value), range (low to high), floor (at least value), ceiling (at most value) or direction (direction, no figure).';
comment on column semantic.call_guidance.value is
  'The target figure for point, floor and ceiling, in unit; null otherwise.';
comment on column semantic.call_guidance.low is
  'Lower end of a range target, in unit; null otherwise.';
comment on column semantic.call_guidance.high is
  'Upper end of a range target, in unit; null otherwise.';
comment on column semantic.call_guidance.unit is
  'pct (percent or percentage points), inr (rupees in crore) or other (another currency or scale); null for a direction with no figure.';
comment on column semantic.call_guidance.direction is
  'For a direction target: increase, decrease or maintain; null otherwise.';
comment on column semantic.call_guidance.comparison is
  'For a direction target, what it is measured against: previous_period or year_ago; null when not stated.';
comment on column semantic.call_guidance.growth_basis is
  'yoy (growth against the same period a year earlier), qoq (against the previous quarter), cagr (a compound annual rate over several years) or level (the figure itself, not a growth rate); null when not stated.';
comment on column semantic.call_guidance.basis is
  'consolidated or standalone, where management said which; null when not stated.';
comment on column semantic.call_guidance.period_text is
  'The period as management named it, such as ''FY27'' or ''next two years''.';
comment on column semantic.call_guidance.period_kind is
  'quarter, half_year, nine_months, year, multi_year or undated.';
comment on column semantic.call_guidance.target_start is
  'First day of the period the target is for; null for an undated target.';
comment on column semantic.call_guidance.target_end is
  'Last day of the period the target is for; null for an undated target.';
comment on column semantic.call_guidance.scope_text is
  'The segment, subsidiary or region the target is limited to, if any; null for the whole company.';
comment on column semantic.call_guidance.stated_on is
  'Date the target was stated: the call date, else the transcript filing date.';
comment on column semantic.call_guidance.not_scored_reason is
  'Why the target can''t be scored, as decided from the statement alone: no_filed_figure (a metric outside the list), currency (an amount in another currency), scope (limited to a segment or region), undated or vague_figure; null otherwise.';
comment on column semantic.call_guidance.extraction_label is
  'Label to show with the target (''AI-extracted'').';
comment on column semantic.call_guidance.outcome is
  'delivered, missed, pending (its period is not yet reported) or not_scored; derived from the company''s filed figures. Null while outcomes are held back.';
comment on column semantic.call_guidance.relation is
  'How the filed figure stands against the target: within, above or below (point and range), at_or_past or short_of (floor and ceiling), moved_as_said or moved_against (direction); null while held back or not scored.';
comment on column semantic.call_guidance.actual is
  'The filed figure the target was scored against, in unit (for a growth target, the growth in percent); null while held back, pending or not scored.';
comment on column semantic.call_guidance.base is
  'For a direction target, the earlier period''s filed figure it was measured against, in unit; null otherwise or while held back.';
comment on column semantic.call_guidance.outcome_reason is
  'For a not_scored outcome, why: the not_scored_reason values, or structural_break (a merger or demerger in the period), definition (the company''s own figures for the metric differ from the filed ones) or filed_figures_unavailable; null otherwise or while held back.';
comment on column semantic.call_guidance.outcome_label is
  'Label to show with the outcome columns (''derived''); null while outcomes are held back.';

------------------------------------------------------------------ comments: module 1's views, made complete and true
comment on view semantic.policy_meetings is
  'One row per Fed (FOMC) or RBI (MPC) meeting, from each bank''s December 2018 meeting onward, including scheduled future meetings (status = ''scheduled'', no decision yet). Rates are in percent; changes in basis points.';
comment on column semantic.policy_meetings.decision_released_at is
  'When the decision was released (or, for a scheduled meeting, is due to be): an exact instant, timestamp with time zone.';
comment on column semantic.policy_meetings.status is
  'held (the decision is stored), scheduled (on the published calendar, no decision yet) or cancelled.';
comment on column semantic.policy_meetings.meeting_kind is
  'scheduled (on the published calendar), unscheduled (an inter-meeting decision, such as March 2020) or special.';
comment on column semantic.policy_meetings.is_rate_decision is
  'True when the meeting decides the policy rate; false for a meeting held for another purpose.';
comment on column semantic.policy_meetings.rbi_mpc_meeting_no is
  'RBI only: the MPC''s own meeting number; null for the Fed.';
comment on column semantic.policy_meetings.fed_target_lower_pct is
  'Fed only: lower bound of the federal funds target range after the decision, percent; null for RBI meetings and meetings not yet held.';
comment on column semantic.policy_meetings.fed_target_upper_pct is
  'Fed only: upper bound of the federal funds target range after the decision, percent; null for RBI meetings and meetings not yet held.';
comment on column semantic.policy_meetings.rbi_repo_pct is
  'RBI only: policy repo rate after the decision, percent; null for Fed meetings and meetings not yet held.';
comment on column semantic.policy_meetings.rbi_reverse_repo_pct is
  'RBI only: fixed reverse repo rate after the decision, percent.';
comment on column semantic.policy_meetings.rbi_msf_pct is
  'RBI only: marginal standing facility rate (the corridor ceiling) after the decision, percent.';
comment on column semantic.policy_meetings.title is
  'Display title of the meeting, such as ''FOMC meeting''.';

comment on view semantic.policy_projections is
  'One row per RBI projection of CPI inflation or real GDP growth for a period, as published in each MPC resolution. meeting_date is when the projection was made; period_start/period_end is the period it is for. Some projections are ranges (low_pct/high_pct).';
comment on column semantic.policy_projections.meeting_date is
  'Decision date of the MPC meeting whose resolution published the projection (as semantic.policy_meetings.meeting_date).';
comment on column semantic.policy_projections.variable is
  '''CPI inflation'' or ''Real GDP growth''.';
comment on column semantic.policy_projections.target_period is
  'The period as RBI labels it, such as ''Q3 2021-22'', ''H2 2019-20'' or ''FY 2021-22'' (Indian fiscal years run April to March).';
comment on column semantic.policy_projections.period_start is
  'First day of the period the projection is for.';
comment on column semantic.policy_projections.period_end is
  'Last day of the period the projection is for.';
comment on column semantic.policy_projections.value_pct is
  'Projected value, percent (year-on-year); null for a projection published as a range.';
comment on column semantic.policy_projections.low_pct is
  'Lower end of a projection published as a range, percent; null for a single value.';
comment on column semantic.policy_projections.high_pct is
  'Upper end of a projection published as a range, percent; null for a single value.';
comment on column semantic.policy_projections.extraction is
  'How the figure was read from the resolution: parsed (by rules) or AI-extracted (by AI, cited).';

comment on column semantic.policy_votes.bank is
  'Fed or RBI';
comment on column semantic.policy_votes.meeting_date is
  'Decision date of the meeting (as semantic.policy_meetings.meeting_date).';
comment on column semantic.policy_votes.member_name is
  'The member''s name.';
comment on column semantic.policy_votes.member_role is
  'Role on the meeting date: fed_chair, fed_vice_chair, fed_governor, fed_bank_president, rbi_governor, rbi_deputy_governor, rbi_executive_director or mpc_external_member.';
comment on column semantic.policy_votes.affiliation is
  'Fed: Board of Governors, or the member''s Reserve Bank (such as FRB New York). RBI: Reserve Bank of India, or an external member''s institution.';
comment on column semantic.policy_votes.stance_vote is
  'RBI only: the stance the member voted for, as the minutes word it (such as neutral or withdrawal of accommodation), or against; null for the Fed.';
comment on column semantic.policy_votes.dissent_text is
  'The dissent as the statement or minutes word it; null for a member who did not dissent.';

comment on view semantic.market_daily is
  'Market and macro series used by the policy tracker and the valuations, one row per series per observation date: S&P 500, Nasdaq Composite, Nasdaq-100 and Dow Jones Industrial Average (US, USD, end-of-day closes); Nifty 50, Nifty Bank and Sensex (India, INR, end-of-day closes); US 2-year and 10-year Treasury yields; India 10-year yield (monthly); USD/INR; dollar index; Fed target range; effective fed funds rate; Damodaran''s implied equity risk premium (monthly), India country risk premium and India sovereign default spread (per edition). USD/INR has two series that are never combined: fred.DEXINUS (daily values, published weekly) for history and changes over time, and yf.INR=X (Yahoo Finance) for the latest day only. Never compare levels across currencies.';
comment on column semantic.market_daily.series_name is
  'Display name of the series, such as ''Nifty 50'' or ''US 2-year Treasury yield''.';
comment on column semantic.market_daily.series_key is
  'Stable key of the series: fred.<FRED series id>, yf.<Yahoo Finance symbol> or damodaran.<name>, such as yf.^NSEI (Nifty 50), fred.DGS2 or damodaran.ERP_IMPLIED.';
comment on column semantic.market_daily.date is
  'Observation date: the trading day for daily series, the first day of the month for monthly series, the edition''s date for the per-edition ones.';
comment on column semantic.market_daily.value is
  'The value, in unit.';
comment on column semantic.market_daily.unit is
  'index_points (index levels), percent (yields, rates and premiums) or inr_per_usd (rupees per US dollar).';
comment on column semantic.market_daily.market is
  'IN (India), US, or GLOBAL (USD/INR and the mature-market equity risk premium).';
comment on column semantic.market_daily.currency is
  'Currency of an index level: INR for the Indian indices, USD for the US ones; null for yields, rates, premiums and exchange rates.';
comment on column semantic.market_daily.source is
  'Source line to show with the figures (its source and, for the US indices, the copyright holder).';

------------------------------------------------------------------ settings (N1)
update ops.setting
   set value = 'false'::jsonb,
       description = 'Kill switch: false until the command bar''s go-live (Spec 07 §3). While false the command bar answers no AI question (plain search only).',
       updated_at = now(), updated_by = 'migration 0052'
 where key = 'cmdbar_ai_enabled';
update ops.setting
   set value = '100'::jsonb,
       description = 'AI questions per IST day across all visitors (Anand, 15 Nov 2026).',
       updated_at = now(), updated_by = 'migration 0052'
 where key = 'cmdbar_daily_cap';
