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
  AiCitationRow,
  AiOutputRow,
  ApiDatabase,
  DocumentRow,
  FieldPolicyRow,
  ObservationRow,
  ObservationStepRow,
  PolicyMeetingRow,
  PolicyRateSettingRow,
  PolicyVoteRow,
  SeriesRow,
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
  "slug, bank, status, decision_date, decision_at, kind, is_rate_decision, meeting_no, minutes_due_on, policy_rate_pct, policy_rate_change_bps, stance, stance_display_label, dissent_count, attribution, fed_target_lower_pct, fed_target_upper_pct";

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

// ------------------------------------------------------------------ Votes, documents and AI outputs (C3)

const VOTE_COLUMNS = "meeting_slug, member_slug, member_name, voted_change_bps, stance_vote, is_dissent, dissent_text";

/** Slugs per request: keeps each `in.(…)` filter's URL short (a meeting slug is 15 characters). */
const SLUGS_PER_REQUEST = 40;

/**
 * Every vote at the given meetings (api.policy_votes), by meeting then member. The slugs go in batches of 40, each
 * batch read whole (readAll), so ~110 meetings × up to 12 voters never meets the 1,000-row cut unseen.
 */
export async function readVotes(db: PolicyDb, slugs: readonly string[]): Promise<PolicyVoteRow[]> {
  const unique = [...new Set(slugs)].sort();
  const batches: string[][] = [];
  for (let i = 0; i < unique.length; i += SLUGS_PER_REQUEST) batches.push(unique.slice(i, i + SLUGS_PER_REQUEST));
  const pages = await Promise.all(
    batches.map((batch, i) =>
      readAll<PolicyVoteRow>(`api.policy_votes (batch ${i + 1} of ${batches.length})`, (from, to) =>
        db
          .from("policy_votes")
          .select(VOTE_COLUMNS, { count: "exact" })
          .in("meeting_slug", batch)
          .order("meeting_slug")
          .order("member_slug")
          .range(from, to),
      ),
    ),
  );
  return pages.flat();
}

/** The decision document of each bank: the FOMC statement, the MPC resolution. */
export const DECISION_DOC_TYPES = { Fed: "fomc_statement", RBI: "mpc_resolution" } as const;

const DOCUMENT_COLUMNS = "id, doc_type, doc_type_label, entity_slug, event_slug, title, source_url, attribution, published_at, version_no";

/** A meeting's decision document, its latest version (a changed text is a new version), or null if none is stored. */
export async function readDecisionDocument(db: PolicyDb, slug: string, bank: Bank): Promise<DocumentRow | null> {
  return readFirst<DocumentRow>(
    `api.documents decision document of ${slug}`,
    db
      .from("documents")
      .select(DOCUMENT_COLUMNS)
      .eq("event_slug", slug)
      .eq("doc_type", DECISION_DOC_TYPES[bank])
      .order("version_no", { ascending: false })
      .limit(1),
  );
}

const OUTPUT_COLUMNS = "id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at";

/**
 * The published output of an AI task for one target (a document, or a meeting), or null. The view lists only
 * published outputs whose field policy is shown or labelled, and the database keeps one per task and target.
 */
export async function readAiOutput(
  db: PolicyDb,
  task: string,
  target: { documentId: number } | { eventSlug: string },
): Promise<AiOutputRow | null> {
  let query = db.from("ai_outputs").select(OUTPUT_COLUMNS).eq("task", task).eq("scope", "");
  query = "documentId" in target ? query.eq("document_id", target.documentId) : query.eq("event_slug", target.eventSlug);
  return readFirst<AiOutputRow>(`api.ai_outputs ${task} ${JSON.stringify(target)}`, query.order("id").limit(1));
}

const CITATION_COLUMNS = "ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote";

/** Every verified citation of an output, by number; each quote is rebuilt from our stored text by the view. */
export async function readCitations(db: PolicyDb, outputId: number): Promise<AiCitationRow[]> {
  return readAll<AiCitationRow>(`api.ai_citations of output ${outputId}`, (from, to) =>
    db
      .from("ai_citations")
      .select(CITATION_COLUMNS, { count: "exact" })
      .eq("ai_output_id", outputId)
      .order("citation_no")
      .range(from, to),
  );
}

/** Every field policy row (a few dozen): what <Gated> decides from. A field without a row is hidden. */
export async function readFieldPolicy(db: PolicyDb): Promise<FieldPolicyRow[]> {
  return readAll<FieldPolicyRow>("api.field_policy", (from, to) =>
    db.from("field_policy").select("field_key, status, label", { count: "exact" }).order("field_key").range(from, to),
  );
}

/** Published series by key, with their source lines (the series' own attribution, else its source's). */
export async function readSeriesInfo(db: PolicyDb, keys: readonly string[]): Promise<SeriesRow[]> {
  return readAll<SeriesRow>(`api.series ${keys.join(", ")}`, (from, to) =>
    db
      .from("series")
      .select("key, name, market, attribution", { count: "exact" })
      .in("key", [...keys])
      .order("key")
      .range(from, to),
  );
}
