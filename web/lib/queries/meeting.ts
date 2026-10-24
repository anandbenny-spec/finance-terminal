// web/lib/queries/meeting.ts: what a meeting page reads (C4; Spec 01 §4.3), from `api` views with the browser key
// only, through lib/queries/policy.ts's guarded reads (N2). Cached for minutes like the rest of the site; a tab's
// document text and the redline are read only for the tab shown. A failed read leaves its panel in the error state
// (StateNote); the reason goes to the server log only.
import { cacheLife, cacheTag } from "next/cache";
import { dateInZone } from "@/lib/format";
import type {
  AiCitationRow,
  AiOutputRow,
  DocumentChunkRow,
  DocumentDiffRow,
  DocumentRow,
  FieldPolicyRow,
  ForecastRow,
  PolicyMeetingRow,
  PolicyRateSettingDetailRow,
  PolicyVoteRow,
} from "@/lib/queries/api";
import {
  readAdjacentMeetings,
  readAiOutput,
  readChunks,
  readCitations,
  readDiff,
  readDocument,
  readFieldPolicy,
  readMeeting,
  readMeetingDocuments,
  readMeetingSettings,
  readProjections,
  readVotes,
  type Bank,
  type PolicyDb,
} from "@/lib/queries/policy";
import type { Read } from "@/lib/queries/shell";
import { publicClient } from "@/lib/supabase/public";
import { latestByType } from "@/lib/policy/meeting";

export type CitedOutput = { output: AiOutputRow; citations: AiCitationRow[] };

function failed(what: string, error: unknown): { ok: false } {
  console.error(`[meeting] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
  return { ok: false };
}

async function attempt<T extends object>(what: string, read: () => Promise<T>): Promise<Read<T>> {
  try {
    return { ok: true, ...(await read()) };
  } catch (error) {
    return failed(what, error);
  }
}

const UNCONFIGURED = "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set";

/** A published AI output for a target with its verified citations, or null. */
async function cited(db: PolicyDb, task: string, target: { documentId: number } | { eventSlug: string }): Promise<CitedOutput | null> {
  const output = await readAiOutput(db, task, target);
  return output ? { output, citations: await readCitations(db, output.id) } : null;
}

export type MeetingPage = {
  readAt: string;
  /** Today in the bank's own zone (the minutes' and transcripts' copy depends on it). */
  today: string;
  /** Null when no meeting has this slug (the page answers 404). */
  meeting: Read<{ row: PolicyMeetingRow | null }>;
  adjacent: Read<{ previous: PolicyMeetingRow | null; next: PolicyMeetingRow | null }>;
  fieldPolicy: Read<{ rows: FieldPolicyRow[] }>;
  votes: Read<{ rows: PolicyVoteRow[] }>;
  settings: Read<{ rows: PolicyRateSettingDetailRow[] }>;
  documents: Read<{ rows: DocumentRow[] }>;
  projections: Read<{ rows: ForecastRow[] }>;
  /** The meeting's AI outputs: the stance (RBI), and each document's summary by document id. */
  ai: Read<{ stance: CitedOutput | null; summaries: Record<number, CitedOutput> }>;
};

/** Everything a meeting page shows above its document texts. */
export async function readMeetingPage(bank: Bank, slug: string, date: string): Promise<MeetingPage> {
  "use cache";
  cacheLife("minutes");
  cacheTag("policy", `meeting-${slug}`);
  const readAt = new Date().toISOString();
  const today = dateInZone(readAt, bank === "Fed" ? "America/New_York" : "Asia/Kolkata");
  const db = publicClient();
  if (!db) {
    const no = failed(slug, UNCONFIGURED);
    return { readAt, today, meeting: no, adjacent: no, fieldPolicy: no, votes: no, settings: no, documents: no, projections: no, ai: no };
  }
  const documents = await attempt(`${slug} documents`, async () => ({ rows: await readMeetingDocuments(db, slug) }));
  const [meeting, adjacent, fieldPolicy, votes, settings, projections, ai] = await Promise.all([
    attempt(`${slug} meeting`, async () => ({ row: await readMeeting(db, slug) })),
    attempt(`${slug} adjacent meetings`, () => readAdjacentMeetings(db, bank, date)),
    attempt("field policy", async () => ({ rows: await readFieldPolicy(db) })),
    attempt(`${slug} votes`, async () => ({ rows: await readVotes(db, [slug]) })),
    attempt(`${slug} rates`, async () => ({ rows: await readMeetingSettings(db, slug) })),
    attempt(`${slug} projections`, async () => ({ rows: bank === "RBI" ? await readProjections(db, date) : [] })),
    attempt(`${slug} AI outputs`, async () => {
      // Each document's latest version is the one its summary is for.
      const latest = latestByType(documents.ok ? documents.rows : []);
      const [stance, ...pairs] = await Promise.all([
        bank === "RBI" ? cited(db, "stance_extraction", { eventSlug: slug }) : Promise.resolve(null),
        ...[...latest.values()].map(async (d) => [d.id, await cited(db, "doc_summary", { documentId: d.id })] as const),
      ]);
      const summaries: Record<number, CitedOutput> = {};
      for (const [id, out] of pairs as (readonly [number, CitedOutput | null])[]) if (out) summaries[id] = out;
      return { stance: stance as CitedOutput | null, summaries };
    }),
  ]);
  return { readAt, today, meeting, adjacent, fieldPolicy, votes, settings, documents, projections, ai };
}

/** A document's chunks (its text, in order), for the Statement, Minutes and Press conference tabs. */
export async function readDocumentChunks(id: number): Promise<Read<{ chunks: DocumentChunkRow[] }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("documents", `document-${id}`);
  const db = publicClient();
  if (!db) return failed(`document ${id}`, UNCONFIGURED);
  return attempt(`document ${id} chunks`, async () => ({ chunks: await readChunks(db, id) }));
}

/** The What changed tab: the diff against the previous statement, its base document and the cited AI summary. */
export async function readMeetingDiff(
  documentId: number,
): Promise<Read<{ diff: DocumentDiffRow | null; base: DocumentRow | null; summary: CitedOutput | null }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("documents", `diff-${documentId}`);
  const db = publicClient();
  if (!db) return failed(`diff of ${documentId}`, UNCONFIGURED);
  return attempt(`diff of ${documentId}`, async () => {
    const [diff, summary] = await Promise.all([
      readDiff(db, documentId),
      cited(db, "statement_diff_summary", { documentId }),
    ]);
    return { diff, base: diff ? await readDocument(db, diff.base_document_id) : null, summary };
  });
}
