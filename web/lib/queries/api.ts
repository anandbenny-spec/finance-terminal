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

/** The slice of the `api` schema typed so far, for supabase-js: the shell's views (C2) and the policy pages' (A1). */
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
      policy_rate_settings: View<PolicyRateSettingRow>;
    };
    Functions: { [_ in never]: never };
  };
};
