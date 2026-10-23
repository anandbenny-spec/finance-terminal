// web/lib/queries/api-policy.ts: the `api` views the policy pages' charts and tables read (A1, #6), as their rows
// arrive from the Data API (PostgREST JSON: `numeric` as a number, `date` as YYYY-MM-DD, `timestamptz` as ISO 8601
// with an offset). Written from the migrations (0006 and 0011); the views are the contract, so a column renamed
// there is renamed here. Only the columns the queries select are listed.
//
// The shell's views (C2, #19) are typed in lib/queries/api.ts; once both are on main, `PolicyViews` joins its
// `ApiDatabase` so one client type covers every page.

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
  policy_rate_pct: number | null;
  policy_rate_change_bps: number | null;
  stance: string | null;
  stance_display_label: string | null;
  attribution: string | null;
  fed_target_lower_pct: number | null;
  fed_target_upper_pct: number | null;
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

export type PolicyViews = {
  observation_steps: View<ObservationStepRow>;
  observations: View<ObservationRow>;
  policy_meetings: View<PolicyMeetingRow>;
  policy_rate_settings: View<PolicyRateSettingRow>;
};

/** The `api` schema as the policy queries see it, for supabase-js. */
export type PolicyApiDatabase = {
  api: {
    Tables: { [_ in never]: never };
    Views: PolicyViews;
    Functions: { [_ in never]: never };
  };
};
