// web/lib/queries/overview.ts: everything /policy reads (C3; Spec 01 §4.2, the S rows), from `api` views with the
// browser key only (lib/supabase/public.ts), through lib/queries/policy.ts's guarded reads (N2).
//
// Cached for minutes ('use cache', the `minutes` profile), like the shell, so the page is prerendered and a decision
// stored by `watch` reaches it within a couple of minutes. Each panel's data is read on its own: a failed read
// leaves only that panel in its error state (StateNote), and the reason goes to the server log only (visitors
// never see errors: Arch 02). Nothing here computes a figure; it returns rows.
import { cacheLife, cacheTag } from "next/cache";
import { dateInZone } from "@/lib/format";
import type {
  AiCitationRow,
  AiOutputRow,
  DocumentRow,
  FieldPolicyRow,
  PolicyMeetingRow,
  PolicyRateSettingRow,
  PolicyVoteRow,
  SeriesRow,
} from "@/lib/queries/api";
import {
  FED_TARGET_SERIES,
  POLICY_START,
  readAiOutput,
  readCitations,
  readDecisionDocument,
  readDecisions,
  readFedTargetSteps,
  readFieldPolicy,
  readLatestDecision,
  readRbiRateSettings,
  readSeriesInfo,
  readVotes,
  type Bank,
  type PolicyDb,
  type StepSeries,
} from "@/lib/queries/policy";
import type { Read } from "@/lib/queries/shell";
import { publicClient } from "@/lib/supabase/public";

function failed(what: string, error: unknown): { ok: false } {
  console.error(`[policy] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
  return { ok: false };
}

async function attempt<T extends object>(what: string, read: () => Promise<T>): Promise<Read<T>> {
  try {
    return { ok: true, ...(await read()) };
  } catch (error) {
    return failed(what, error);
  }
}

/** A bank's latest decision with what its card shows: the votes, the decision document, the AI outputs. */
export type LatestDecision = {
  meeting: PolicyMeetingRow | null;
  votes: PolicyVoteRow[];
  document: DocumentRow | null;
  summary: { output: AiOutputRow; citations: AiCitationRow[] } | null;
  stance: { output: AiOutputRow; citations: AiCitationRow[] } | null;
};

async function readLatest(db: PolicyDb, bank: Bank): Promise<LatestDecision> {
  const meeting = await readLatestDecision(db, bank);
  if (!meeting) return { meeting: null, votes: [], document: null, summary: null, stance: null };
  const [votes, document, stanceOutput] = await Promise.all([
    readVotes(db, [meeting.slug]),
    readDecisionDocument(db, meeting.slug, bank),
    bank === "RBI" ? readAiOutput(db, "stance_extraction", { eventSlug: meeting.slug }) : Promise.resolve(null),
  ]);
  const summaryOutput = document ? await readAiOutput(db, "doc_summary", { documentId: document.id }) : null;
  const [summaryCitations, stanceCitations] = await Promise.all([
    summaryOutput ? readCitations(db, summaryOutput.id) : Promise.resolve([]),
    stanceOutput ? readCitations(db, stanceOutput.id) : Promise.resolve([]),
  ]);
  return {
    meeting,
    votes,
    document,
    summary: summaryOutput ? { output: summaryOutput, citations: summaryCitations } : null,
    stance: stanceOutput ? { output: stanceOutput, citations: stanceCitations } : null,
  };
}

export type PolicyOverview = {
  /** When the rows were read (UTC): the checked times' year and the history's range end are worked out from it. */
  readAt: string;
  fieldPolicy: Read<{ rows: FieldPolicyRow[] }>;
  /** Every held rate decision since 2019, both banks, oldest first, with every vote at them. */
  history: Read<{ decisions: PolicyMeetingRow[]; votes: PolicyVoteRow[] }>;
  fed: Read<LatestDecision>;
  rbi: Read<LatestDecision>;
  rates: Read<{
    fed: { upper: StepSeries; lower: StepSeries };
    rbi: PolicyRateSettingRow[];
    series: SeriesRow[];
  }>;
};

const UNCONFIGURED = "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set";

export async function readPolicyOverview(): Promise<PolicyOverview> {
  "use cache";
  cacheLife("minutes");
  cacheTag("policy", "policy-overview");
  const readAt = new Date().toISOString();
  const db = publicClient();
  if (!db) {
    const no = failed("overview", UNCONFIGURED);
    return { readAt, fieldPolicy: no, history: no, fed: no, rbi: no, rates: no };
  }
  // Decisions are dated in each bank's zone; the range runs to today in IST, which is never behind New York's date.
  const range = { from: POLICY_START, to: dateInZone(readAt, "Asia/Kolkata") };
  const [fieldPolicy, history, fed, rbi, rates] = await Promise.all([
    attempt("field policy", async () => ({ rows: await readFieldPolicy(db) })),
    attempt("history", async () => {
      const decisions = await readDecisions(db, range);
      return { decisions, votes: await readVotes(db, decisions.map((d) => d.slug)) };
    }),
    attempt("latest Fed decision", () => readLatest(db, "Fed")),
    attempt("latest RBI decision", () => readLatest(db, "RBI")),
    attempt("policy rates", async () => {
      const [fedSteps, rbiSettings, series] = await Promise.all([
        readFedTargetSteps(db, range),
        readRbiRateSettings(db, range),
        readSeriesInfo(db, [FED_TARGET_SERIES.upper, FED_TARGET_SERIES.lower]),
      ]);
      return { fed: fedSteps, rbi: rbiSettings, series };
    }),
  ]);
  return { readAt, fieldPolicy, history, fed, rbi, rates };
}
