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
    };
    Functions: { [_ in never]: never };
  };
};
