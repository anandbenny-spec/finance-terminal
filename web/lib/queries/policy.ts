// web/lib/queries/policy.ts: every chart and table query the policy pages use (A1, #6; Review 01, N2), each a named
// function with its date range.
//
// The Data API returns at most 1,000 rows per request (supabase/config.toml `max_rows`) and cuts a longer list
// without an error. So every list here goes through `readAll` (an exact count, then `.range()` pages until it holds
// that many rows, or a thrown IncompleteReadError), and no query reads a long daily series whole when a smaller view
// answers it (Arch 01 §3, "Row limit"):
// - the Fed target range comes from `api.observation_steps` (FRED DFEDTARU/L as the days they changed: a few dozen
//   rows since 2019, not ~2,850 each from `api.observations`);
// - the Fed StatRow reads both bounds from `api.policy_meetings` (F22); the ticker tape reads `api.series_latest`
//   (F24; lib/queries/shell.ts, C2);
// - a daily market series (the long-rates chart, Pass 1b) is paged in full for its range by `readDailySeries`.
//
// The functions take the client, so pages pass the browser-key client and tests pass one whose fetch stands in for
// PostgREST. They throw on any failed or incomplete read; the page shows its error state (StateNote) and the reason
// goes to the server log only. Nothing here computes a figure: every number is a row the database returned.
import type { SupabaseClient } from "@supabase/supabase-js";
import type {
  ObservationRow,
  ObservationStepRow,
  ApiDatabase,
  PolicyMeetingRow,
  PolicyRateSettingRow,
} from "@/lib/queries/api";
import { IncompleteReadError, readAll, type PageResult } from "@/lib/queries/read";

export type PolicyDb = SupabaseClient<ApiDatabase, "api">;

/** An inclusive range of calendar dates, YYYY-MM-DD at both ends. */
export type DateRange = { from: string; to: string };

/** The tracker's whole span (Spec 01 §2: every meeting from 1 Jan 2019, plus each bank's last 2018 meeting). */
export const POLICY_START = "2019-01-01";

/** The two FRED series the Fed target range is drawn from (Spec 01 §4.2, "Policy rates since 2019"). */
export const FED_TARGET_SERIES = { upper: "fred.DFEDTARU", lower: "fred.DFEDTARL" } as const;

/** The RBI instruments the repo step and the corridor band are drawn from: SDF from Apr 2022, reverse repo before. */
export const RBI_CORRIDOR_INSTRUMENTS = ["repo", "reverse_repo", "sdf", "msf"] as const;

export type Bank = "Fed" | "RBI";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function checkRange(what: string, range: DateRange): void {
  if (!ISO_DATE.test(range.from) || !ISO_DATE.test(range.to)) {
    throw new RangeError(`${what}: dates must be YYYY-MM-DD, got ${range.from} … ${range.to}`);
  }
  if (range.from > range.to) throw new RangeError(`${what}: range starts after it ends (${range.from} … ${range.to})`);
}

/** At most one row (a `.limit(1)` read): the row, or null when there is none; throws on an error. */
async function readFirst<T>(what: string, query: PromiseLike<PageResult<T>>): Promise<T | null> {
  const page = await query;
  if (page.error) throw new IncompleteReadError(what, page.error.message);
  const data = page.data ?? [];
  if (data.length > 1) throw new IncompleteReadError(what, `expected at most one row, got ${data.length}`);
  return data[0] ?? null;
}

// ------------------------------------------------------------------ Policy rates since 2019 (the chart)

const STEP_COLUMNS = "series_key, obs_date, value, is_latest";

/** One step series for a range: the step in force when the range opens (if any), then every change in it. */
export type StepSeries = { seriesKey: string; steps: ObservationStepRow[] };

async function readSteps(db: PolicyDb, seriesKey: string, range: DateRange): Promise<StepSeries> {
  const what = `api.observation_steps ${seriesKey} ${range.from}…${range.to}`;
  const [carryIn, inRange] = await Promise.all([
    readFirst<ObservationStepRow>(
      `${what} (in force on ${range.from})`,
      db
        .from("observation_steps")
        .select(STEP_COLUMNS)
        .eq("series_key", seriesKey)
        .lt("obs_date", range.from)
        .order("obs_date", { ascending: false })
        .limit(1),
    ),
    readAll<ObservationStepRow>(what, (from, to) =>
      db
        .from("observation_steps")
        .select(STEP_COLUMNS, { count: "exact" })
        .eq("series_key", seriesKey)
        .gte("obs_date", range.from)
        .lte("obs_date", range.to)
        .order("obs_date")
        .range(from, to),
    ),
  ]);
  return { seriesKey, steps: carryIn ? [carryIn, ...inRange] : inRange };
}

/**
 * The Fed target range band: FRED DFEDTARU and DFEDTARL from `api.observation_steps`, never `api.observations`
 * (N2). Each series starts with the step in force when the range opens, so the band is drawn from its first day.
 */
export async function readFedTargetSteps(
  db: PolicyDb,
  range: DateRange,
): Promise<{ upper: StepSeries; lower: StepSeries }> {
  checkRange("readFedTargetSteps", range);
  const [upper, lower] = await Promise.all([
    readSteps(db, FED_TARGET_SERIES.upper, range),
    readSteps(db, FED_TARGET_SERIES.lower, range),
  ]);
  return { upper, lower };
}

const SETTING_COLUMNS = "meeting_slug, bank, decision_date, instrument, value_pct, change_bps";

/**
 * The RBI repo step and corridor band: the rates set at each MPC meeting (`api.policy_rate_settings`), the
 * settings in force when the range opens first (the last decision before it), then every decision in the range.
 */
export async function readRbiRateSettings(db: PolicyDb, range: DateRange): Promise<PolicyRateSettingRow[]> {
  checkRange("readRbiRateSettings", range);
  const what = `api.policy_rate_settings RBI ${range.from}…${range.to}`;
  const settings = (from: string, to: string) =>
    readAll<PolicyRateSettingRow>(`${what} (${from}…${to})`, (start, end) =>
      db
        .from("policy_rate_settings")
        .select(SETTING_COLUMNS, { count: "exact" })
        .eq("bank", "RBI")
        .in("instrument", [...RBI_CORRIDOR_INSTRUMENTS])
        .gte("decision_date", from)
        .lte("decision_date", to)
        .order("decision_date")
        .order("instrument")
        .range(start, end),
    );
  const before = await readFirst<Pick<PolicyRateSettingRow, "decision_date">>(
    `${what} (in force on ${range.from})`,
    db
      .from("policy_rate_settings")
      .select("decision_date")
      .eq("bank", "RBI")
      .in("instrument", [...RBI_CORRIDOR_INSTRUMENTS])
      .lt("decision_date", range.from)
      .order("decision_date", { ascending: false })
      .limit(1),
  );
  const [carryIn, inRange] = await Promise.all([
    before ? settings(before.decision_date, before.decision_date) : Promise.resolve([]),
    settings(range.from, range.to),
  ]);
  return [...carryIn, ...inRange];
}

const MEETING_COLUMNS =
  "slug, bank, status, decision_date, decision_at, kind, is_rate_decision, policy_rate_pct, policy_rate_change_bps, stance, stance_display_label, attribution, fed_target_lower_pct, fed_target_upper_pct";

/**
 * Every held rate decision in the range, both banks, oldest first: the chart's decision markers and the history
 * table ("Every decision since 2019"). Optionally one bank only.
 */
export async function readDecisions(db: PolicyDb, range: DateRange, bank?: Bank): Promise<PolicyMeetingRow[]> {
  checkRange("readDecisions", range);
  return readAll<PolicyMeetingRow>(`api.policy_meetings ${bank ?? "both banks"} ${range.from}…${range.to}`, (from, to) => {
    let query = db
      .from("policy_meetings")
      .select(MEETING_COLUMNS, { count: "exact" })
      .eq("status", "held")
      .eq("is_rate_decision", true)
      .gte("decision_date", range.from)
      .lte("decision_date", range.to);
    if (bank) query = query.eq("bank", bank);
    return query.order("decision_date").order("slug").range(from, to);
  });
}

// ------------------------------------------------------------------ Where rates stand (the StatRow)

/**
 * A bank's latest held rate decision, for the StatRow: the Fed's target range is both bounds from
 * `api.policy_meetings` (`fed_target_lower_pct`, `fed_target_upper_pct`: F22), the RBI's repo rate is
 * `policy_rate_pct`. Null when the bank has no held decision yet; the panel then shows its empty state.
 */
export async function readLatestDecision(db: PolicyDb, bank: Bank): Promise<PolicyMeetingRow | null> {
  return readFirst<PolicyMeetingRow>(
    `api.policy_meetings latest ${bank} decision`,
    db
      .from("policy_meetings")
      .select(MEETING_COLUMNS)
      .eq("bank", bank)
      .eq("status", "held")
      .eq("is_rate_decision", true)
      .order("decision_date", { ascending: false })
      .order("slug", { ascending: false })
      .limit(1),
  );
}

// ------------------------------------------------------------------ Daily market series (paged, never cut)

/**
 * A daily series for a range, every row, from `api.observations`, paged with `.range()` against an exact count
 * (the long-rates chart, Pass 1b, joins here). Step series don't come through here: use `readFedTargetSteps`.
 */
export async function readDailySeries(db: PolicyDb, seriesKey: string, range: DateRange): Promise<ObservationRow[]> {
  checkRange("readDailySeries", range);
  return readAll<ObservationRow>(`api.observations ${seriesKey} ${range.from}…${range.to}`, (from, to) =>
    db
      .from("observations")
      .select("series_key, obs_date, value, label", { count: "exact" })
      .eq("series_key", seriesKey)
      .gte("obs_date", range.from)
      .lte("obs_date", range.to)
      .order("obs_date")
      .range(from, to),
  );
}
