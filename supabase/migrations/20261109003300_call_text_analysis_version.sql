-- 0033: the analysis version of an earnings call's transcript (issue #341, job 2B-09b; Spec 02B §5.3, §13.3; Supabase egress,
-- 9 Nov 2026; approved by Anand on 9 Nov 2026, 15:28 IST: option 1 on #341).
--
-- Supabase counts every byte read out of Storage as egress, and `equity.call_text` re-read every analysed call's stored PDF on
-- every run (to check its text against `core.document.text_sha256` and to re-derive its turns, roles and chunks, so that a new
-- role rule reaches calls already published). On dev that was about 0.3 to 0.4 GB a pass, enough to push the organisation past
-- its 5 GB free quota on 9 Nov 2026. The task now skips a call whose transcript was last analysed under the rules it still runs
-- under, and re-reads one rotating quarter of those calls in each run as the integrity check.
--
-- This migration adds the one column that makes the skip possible: the version of the rules (a hash of the source files that
-- decide the turns, roles and chunks, and of the PDF parser's version; `ft.jobs.tasks.equity_call_text.analysis_version`) a
-- call's transcript was last analysed under. Existing rows stay null, so the first run after the migration reads every file once
-- and writes the versions (the same cost as one run today); a changed rule changes the version and makes every call re-read once.
--
-- No new table, grant, policy or view. `job_writer` already holds update on `equity.earnings_call`; like `parser_version`, the
-- column is in no `api` or `semantic` view, so visitors never see it.

alter table equity.earnings_call add column analysis_version text;

comment on column equity.earnings_call.analysis_version is
  'The version of the transcript-analysis rules (a hash of the sources that decide its turns, roles and chunks, with the PDF parser''s version) this call''s transcript was last analysed under; set by equity.call_text, null until then. A call analysed under the current version is not re-read from storage in routine runs.';
