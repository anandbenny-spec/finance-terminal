// web/lib/queries/api.ts: the `api` views the site reads, as their rows arrive from the Data API (PostgREST JSON:
// `numeric` as a number, `date` as YYYY-MM-DD, `timestamptz` as ISO 8601 with an offset). One type per view,
// written from the migrations (Arch 01a: 0006 and 0011); the views are the contract, so a column renamed there is
// renamed here. Only the columns pages select are listed.

/** api.series_latest (0011, F24): each published series' last and previous observation. */
export type SeriesLatestRow = {
  series_key: string;
  name: string;
  unit: string;
  frequency: string;
  last_date: string;
  last_value: number;
  prev_date: string | null;
  prev_value: number | null;
  change: number | null;
};

/** api.series (0006): a published series and its source line (the series' own attribution, else its source's). */
export type SeriesRow = {
  key: string;
  name: string;
  market: "IN" | "US" | "GLOBAL" | null;
  attribution: string | null;
};

/** api.next_meetings (0006): scheduled meetings from today (the database's date) on. */
export type NextMeetingRow = {
  bank: string;
  slug: string;
  decision_date: string;
  decision_at: string | null;
};

/** api.task_freshness (0011, F17): a task's last check and success; `stale` once runs stop, by itself. */
export type TaskFreshnessRow = {
  task_key: string;
  last_checked_at: string | null;
  last_success_at: string | null;
  status: "ok" | "stale" | "failed";
};

/** api.observation_steps (0011, N2): a step series as the days its value changed, plus its latest day. */
export type ObservationStepRow = {
  series_key: string;
  obs_date: string;
  value: number;
  is_latest: boolean;
};

/** api.observations (0006): one series × date; `label` is the series' label in force that day, if any. */
export type ObservationRow = {
  series_key: string;
  obs_date: string;
  value: number;
  label: string | null;
};

/** api.policy_meetings (0006, both Fed bounds from 0011: F22). */
export type PolicyMeetingRow = {
  slug: string;
  bank: string;
  status: string;
  decision_date: string;
  decision_at: string | null;
  kind: string;
  is_rate_decision: boolean;
  meeting_no: number | null;
  /** Read by the meeting page only (C4); other reads leave it out. */
  has_press_conference?: boolean | null;
  minutes_due_on: string | null;
  policy_rate_pct: number | null;
  policy_rate_change_bps: number | null;
  stance: string | null;
  stance_display_label: string | null;
  dissent_count: number;
  attribution: string | null;
  fed_target_lower_pct: number | null;
  fed_target_upper_pct: number | null;
};

/** api.policy_rate_settings with its citation and cross-check note, for the meeting page's rates table (C4). */
export type PolicyRateSettingDetailRow = PolicyRateSettingRow & {
  crosscheck: string | null;
  document_id: number;
  char_start: number | null;
  char_end: number | null;
};

/** api.market_reactions (0006): one market's move around one decision (policy.reactions, Pass 1b). `change` is in
 * basis points for yields and in percent otherwise (`change_unit`); the two observations it comes from are given. */
export type MarketReactionRow = {
  meeting_slug: string;
  series_key: string;
  series_name: string;
  pre_obs_date: string;
  post_obs_date: string;
  pre_value: number;
  post_value: number;
  change: number;
  change_unit: "bps" | "pct";
  is_surprise_measure: boolean;
  attribution: string | null;
};

/** api.document_chunks (0006): a document's text in order; the chunks reproduce it exactly (code-point offsets). */
export type DocumentChunkRow = {
  document_id: number;
  chunk_no: number;
  char_start: number;
  char_end: number;
  section: string | null;
  speaker: string | null;
  body: string;
};

/** api.document_diffs (0006): the sentence-matched diff against the previous statement (segments: diff version 2). */
export type DocumentDiffRow = {
  document_id: number;
  base_document_id: number;
  segments: unknown;
  words_added: number;
  words_removed: number;
};

/** api.forecasts (0006): a projection with its vintage and target period; `label` "AI-extracted" when it is. */
export type ForecastRow = {
  series_key: string;
  series_name: string;
  vintage_date: string;
  target_label: string;
  target_start: string | null;
  target_end: string | null;
  value: number | null;
  value_low: number | null;
  value_high: number | null;
  label: string | null;
  document_id: number | null;
  char_start: number | null;
  char_end: number | null;
};

/** api.policy_votes (0006): one row per voter per meeting, each cited to its passage. */
export type PolicyVoteRow = {
  meeting_slug: string;
  member_slug: string;
  member_name: string;
  voted_change_bps: number | null;
  stance_vote: string | null;
  is_dissent: boolean;
  dissent_text: string | null;
  /** The passage the vote was parsed from (VoteDotPlot's "Open the cited passage"); read with every vote row. */
  document_id?: number | null;
  char_start?: number | null;
  char_end?: number | null;
};

/** api.documents (0006): every published document version. */
export type DocumentRow = {
  id: number;
  doc_type: string;
  doc_type_label: string;
  entity_slug: string;
  event_slug: string | null;
  title: string;
  source_url: string | null;
  attribution: string | null;
  published_at: string | null;
  version_no: number;
};

/** api.ai_outputs (0006): published AI outputs whose field policy is shown or labelled (RLS: published only). */
export type AiOutputRow = {
  id: number;
  task: string;
  document_id: number | null;
  event_slug: string | null;
  scope: string;
  body: string | null;
  output_label: string | null;
  content: unknown;
  display_label: string | null;
  model: string;
  created_at: string;
};

/** api.ai_citations (0006): verified citations only, each quote rebuilt from our stored chunks. */
export type AiCitationRow = {
  ai_output_id: number;
  citation_no: number;
  claim_path: string;
  document_id: number | null;
  document_title: string | null;
  source_url: string | null;
  char_start: number | null;
  char_end: number | null;
  quote: string | null;
};

/** api.field_policy (0006): how a gated field shows; no row means hidden (fail closed). */
export type FieldPolicyRow = {
  field_key: string;
  status: "shown" | "labelled" | "hidden";
  label: string | null;
};

/** api.policy_rate_settings (0006): every rate in force after a decision, one row per instrument. */
export type PolicyRateSettingRow = {
  meeting_slug: string;
  bank: string;
  decision_date: string;
  instrument: "fed_target_lower" | "fed_target_upper" | "repo" | "reverse_repo" | "sdf" | "msf" | "bank_rate" | "crr";
  value_pct: number;
  change_bps: number;
};

/** api.quality_summary (0021, ACC-1): counts by module and period, no task names, run ids or error text. `value` is a
 * count, or a share from 0 to 1 for `freshness_on_time_share` (`n` = the task-days it is out of; `label` "derived"). */
export type QualitySummaryRow = {
  module: string;
  period: "30d" | "90d" | "all";
  measure: string;
  value: number;
  n: number | null;
  label: "derived" | null;
  as_of: string;
};

/** api.eval_summary (0004): the passing evaluation runs, site-wide (no module column). */
export type EvalSummaryRow = {
  eval_set: string;
  model: string;
  metric: string;
  value: number;
  n_items: number | null;
  run_at: string;
};

/** One column's note on a snapshot row (api.screener `notes`): its label and the reason it is held back, either may be null. */
export type ScreenerNote = { label: string | null; reason: string | null };

/** api.screener (0024): one row per company from its latest weekly snapshot. Ratios are fractions; money in full units of `currency`. */
export type ScreenerRow = {
  slug: string;
  name: string;
  short_name: string | null;
  nse_symbol: string;
  market: "IN" | "US";
  currency: "INR" | "USD";
  peer_group_key: string | null;
  peer_group_label: string | null;
  snapshot_date: string;
  price: number | null;
  price_date: string | null;
  market_cap: number | null;
  shares: number | null;
  pe_ttm: number | null;
  pb: number | null;
  ev: number | null;
  ev_ebit: number | null;
  ev_sales: number | null;
  dividend_yield: number | null;
  roe: number | null;
  ebit_margin: number | null;
  revenue_cagr_3y: number | null;
  revenue_cagr_5y: number | null;
  debt_to_equity: number | null;
  promoter_pct: number | null;
  beta: number | null;
  return_1y: number | null;
  traded_value_median_3m: number | null;
  implied_growth: number | null;
  delivered_growth: number | null;
  growth_gap: number | null;
  implied_roe: number | null;
  delivered_roe: number | null;
  roe_gap: number | null;
  valuation_family: string | null;
  notes: Record<string, ScreenerNote> | null;
  ebit_label: string | null;
  beta_label: string | null;
  implied_growth_label: string | null;
  implied_roe_label: string | null;
  index_slugs: string[] | null;
  source_key: string;
};

/** api.index_members (0024): each index's latest member set. */
export type IndexMemberRow = {
  index_slug: string;
  index_name: string;
  snapshot_date: string;
  slug: string;
  name: string;
  short_name: string | null;
  nse_symbol: string;
  market: "IN" | "US";
  currency: "INR" | "USD";
};

type Num = number | null;

/** api.companies (0024): one row per company: its identifiers, peer group, index memberships and the last two closes. */
export type CompanyRow = {
  slug: string;
  name: string;
  short_name: string | null;
  market: "IN" | "US";
  currency: "INR" | "USD";
  isin: string | null;
  nse_symbol: string;
  bse_code: string | null;
  nse_industry: string | null;
  peer_group_key: string | null;
  peer_group_label: string | null;
  peer_group_label_kind: string | null;
  valuation_family: string | null;
  family_note: string | null;
  fy_end_month: number | null;
  price_exchange: string;
  listed_on: string | null;
  index_slugs: string[] | null;
  index_as_of: string | null;
  last_close: Num;
  last_close_date: string | null;
  previous_close: Num;
  previous_close_date: string | null;
  price_source_key: string | null;
};

/** api.company_prices (0024): one row per session; `adj_close` is null while a held corporate action lies after it. */
export type CompanyPriceRow = {
  slug: string;
  market: "IN" | "US";
  currency: "INR" | "USD";
  trade_date: string;
  exchange: string;
  close: number;
  volume: Num;
  traded_value: Num;
  adj_close: Num;
  adj_held: boolean;
  source_key: string;
};

/** api.company_metrics (0024): one row per metric, basis, period kind and period end. `state` tells a held value from a not-applicable one. */
export type CompanyMetricRow = {
  slug: string;
  name: string;
  market: "IN" | "US";
  currency: "INR" | "USD";
  metric_key: string;
  label: string;
  unit: string | null;
  basis: string;
  period_kind: string;
  period_end: string;
  as_of: string;
  value: Num;
  label_kind: string | null;
  held_reason: string | null;
  state: "value" | "held" | "not_applicable";
  source_key: string;
};

/** api.peer_comps (0024): a company's peers (row_kind 'peer') and the median of those peers (row_kind 'median'). */
export type PeerCompRow = {
  company_slug: string;
  market: "IN" | "US";
  currency: "INR" | "USD";
  peer_group_key: string;
  row_kind: "peer" | "median";
  peer_slug: string | null;
  peer_name: string | null;
  peer_short_name: string | null;
  peer_nse_symbol: string | null;
  snapshot_date: string | null;
  pe_ttm: Num;
  pb: Num;
  ev_ebit: Num;
  ev_sales: Num;
  roe: Num;
  ebit_margin: Num;
  revenue_cagr_3y: Num;
  peer_count: Num;
  median_n: Record<string, number> | null;
  median_note: string | null;
  source_key: string;
};

/** The statement lines api.company_financials carries (full units of the reporting currency; ratios as fractions). */
export const FINANCIAL_LINES = [
  "revenue", "other_income", "total_income", "employee_cost", "dna", "finance_cost", "total_expenses", "pbt", "tax", "pat",
  "pat_owners", "eps_basic", "eps_diluted", "total_assets", "cash", "bank_balances_other", "current_investments",
  "noncurrent_investments", "trade_receivables", "inventories", "total_equity", "equity_owners", "borrowings_current",
  "borrowings_noncurrent", "trade_payables", "cfo", "cfi", "cff", "capex", "dividends_paid", "interest_earned",
  "interest_expended", "operating_profit", "provisions", "gnpa_ratio", "nnpa_ratio", "cet1_ratio", "deposits", "advances",
  "gross_premium", "net_premium", "ebit_derived",
] as const;
export type FinancialLine = (typeof FINANCIAL_LINES)[number];

/** api.company_financials (0024): one row per basis, period kind and period end (the latest filing for it). */
export type CompanyFinancialRow = {
  slug: string;
  market: "IN" | "US";
  currency: "INR" | "USD";
  basis: "consolidated" | "standalone";
  period_kind: "quarter" | "half_year" | "nine_months" | "year" | "instant";
  period_start: string | null;
  period_end: string;
  ebit_derived_label: string | null;
  ebit_derived_held_reason: string | null;
  ebit_derived_state: "value" | "held" | "not_applicable" | null;
  format: string | null;
  audited: boolean | null;
  filed_at: string | null;
  source_url: string | null;
  source_key: string;
  attribution: string | null;
} & Record<FinancialLine, Num>;

/** api.company_corporate_actions (0024; status from 2A-08b): a company's corporate actions with each price factor's status. */
export type CorporateActionRow = {
  slug: string;
  market: "IN" | "US";
  currency: "INR" | "USD";
  kind: string;
  ex_date: string | null;
  record_date: string | null;
  subject: string | null;
  ratio_new: Num;
  ratio_old: Num;
  face_value_old: Num;
  face_value_new: Num;
  dividend_per_share: Num;
  rights_price: Num;
  exchange: string;
  status: string;
  title: string | null;
  source_url: string | null;
  factor_status: "applied" | "held" | "none";
  price_factor: Num;
  factor_reason: string | null;
  source_key: string;
  attribution: string | null;
};

/** api.company_promoter_holding (0025): the latest filed promoter holding per company and quarter. */
export type PromoterHoldingRow = {
  slug: string;
  as_of: string;
  promoter_pct: Num;
  promoter_shares: Num;
  total_shares: Num;
  filed_at: string | null;
  source_url: string | null;
  source_key: string;
  correction: "percent_for_fraction" | null;
  label_kind: string | null;
  filed_pct: Num;
  share_ratio_pct: Num;
  nse_pct: Num;
};

type View<Row> = { Row: Row; Relationships: [] };

/** The slice of the `api` schema typed so far, for supabase-js: the shell's views (C2), the policy pages' (A1, C3, C4, Pass 1b). */
export type ApiDatabase = {
  api: {
    Tables: { [_ in never]: never };
    Views: {
      series_latest: View<SeriesLatestRow>;
      series: View<SeriesRow>;
      next_meetings: View<NextMeetingRow>;
      task_freshness: View<TaskFreshnessRow>;
      observation_steps: View<ObservationStepRow>;
      observations: View<ObservationRow>;
      policy_meetings: View<PolicyMeetingRow>;
      policy_rate_settings: View<PolicyRateSettingDetailRow>;
      document_chunks: View<DocumentChunkRow>;
      document_diffs: View<DocumentDiffRow>;
      forecasts: View<ForecastRow>;
      policy_votes: View<PolicyVoteRow>;
      documents: View<DocumentRow>;
      ai_outputs: View<AiOutputRow>;
      ai_citations: View<AiCitationRow>;
      field_policy: View<FieldPolicyRow>;
      market_reactions: View<MarketReactionRow>;
      quality_summary: View<QualitySummaryRow>;
      eval_summary: View<EvalSummaryRow>;
      screener: View<ScreenerRow>;
      index_members: View<IndexMemberRow>;
      companies: View<CompanyRow>;
      company_prices: View<CompanyPriceRow>;
      company_metrics: View<CompanyMetricRow>;
      peer_comps: View<PeerCompRow>;
      company_financials: View<CompanyFinancialRow>;
      company_corporate_actions: View<CorporateActionRow>;
      company_promoter_holding: View<PromoterHoldingRow>;
    };
    Functions: { [_ in never]: never };
  };
};
