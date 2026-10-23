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

type View<Row> = { Row: Row; Relationships: [] };

/** The slice of the `api` schema typed so far, for supabase-js (A1 adds the page queries' views). */
export type ApiDatabase = {
  api: {
    Tables: { [_ in never]: never };
    Views: {
      series_latest: View<SeriesLatestRow>;
      series: View<SeriesRow>;
      next_meetings: View<NextMeetingRow>;
      task_freshness: View<TaskFreshnessRow>;
    };
    Functions: { [_ in never]: never };
  };
};
