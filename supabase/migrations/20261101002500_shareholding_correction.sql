-- 0025: the labelled correction for promoter holdings filed in percent (job 2A-10, issue #116; Anand's decision of
-- 1 Nov 2026 on #116). Some shareholding-pattern files (about 6 Oct 2021 to 18 Jan 2022, and BHARTIARTL's 30 Sep 2020)
-- give the promoter figure in percent where the taxonomy wants a fraction, so it reads 100 times too large. The task
-- stores such a figure divided by 100 only when the file's own share-count ratio and NSE's figure (where NSE lists one)
-- both confirm it, and keeps the evidence on the row so the company page can say so. Additive only: three nullable
-- columns, one check, one `api` view. No policy, grant or RLS change on the table (job_writer's grants cover new columns).

alter table equity.shareholding
  add column correction       text    check (correction = 'percent_for_fraction'),
  add column filed_pct        numeric,
  add column share_ratio_pct  numeric,
  add column nse_pct          numeric,
  add constraint shareholding_correction_evidence check (
    (correction is null and filed_pct is null and share_ratio_pct is null)
    or (correction is not null and filed_pct is not null and share_ratio_pct is not null));

comment on column equity.shareholding.correction is
  'Null for a figure stored as filed. ''percent_for_fraction'': the file gave the promoter holding in percent where a fraction is expected; promoter_pct is the filed value divided by 100, confirmed by the file''s own share counts and NSE''s figure.';
comment on column equity.shareholding.filed_pct is
  'Only on a corrected row: the figure as filed, after the usual times 100 (for example 6613 for a holding of 66.13 percent).';
comment on column equity.shareholding.share_ratio_pct is
  'Only on a corrected row: the file''s own promoter shares divided by its total shares, times 100.';
comment on column equity.shareholding.nse_pct is
  'Only on a corrected row: NSE''s own promoter figure for the quarter, where NSE lists one.';

------------------------------------------------------------------ api.company_promoter_holding
-- One row per company and quarter: the latest filed pattern (ties: the greatest document id). A page reads a corrected
-- row's `filed_pct`, `share_ratio_pct` and `nse_pct` to say, in its own number formatting, for example "Filed as 6,613
-- percent, shown as 66.13 percent, matching the filing's share counts and NSE", linked to `source_url`.
create view api.company_promoter_holding with (security_invoker = true) as
select distinct on (s.entity_id, s.as_of)
       e.slug, s.as_of, s.promoter_pct, s.promoter_shares, s.total_shares, s.filed_at, s.source_url,
       lower(s.exchange) as source_key,
       s.correction, case when s.correction is not null then 'derived' end as label_kind,
       s.filed_pct, s.share_ratio_pct, s.nse_pct
from equity.shareholding s
join core.entity e on e.id = s.entity_id
order by s.entity_id, s.as_of, s.filed_at desc nulls last, s.document_id desc;

comment on view api.company_promoter_holding is
  'Promoter and promoter-group holding per company and quarter, the latest filed pattern. A corrected row carries the filed value and the evidence.';

grant select on api.company_promoter_holding to anon, authenticated, service_role;
