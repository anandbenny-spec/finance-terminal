// web/lib/queries/timeline.ts: everything "How the wording moved" reads (#509; Design 01 §1.16, Pass 2), from `api` views
// with the browser key only (lib/supabase/public.ts), through lib/queries/policy.ts's guarded reads.
//
// The page calls this only when `site.language_timeline` is shown or labelled (lib/policy/timeline.ts): hidden, nothing
// is read. Cached for minutes like the rest of /policy ('use cache', the `minutesNoExpire` profile), and what is cached is
// the grids' cells, never the pairs they were cut from: a bank's pairs are the diffs' sentences, tens of kilobytes a
// meeting, and each bank's are read, cut and dropped inside `readBankTimeline`. Each bank is read on its own, so one
// failing leaves only its panel in the error state (StateNote) and the reason goes to the server log only.
import { cacheLife, cacheTag } from "next/cache";
import { dateInZone } from "@/lib/format";
import { latestBySlug, type StripDocument } from "@/lib/policy/changestrip";
import { buildBankTimeline, type BankTimeline } from "@/lib/policy/timeline";
import { TIMELINE_ROWS } from "@/lib/policy/timeline-rows";
import { attempt, failed } from "@/lib/queries/overview";
import {
  POLICY_START,
  readDecisionDiffPairs,
  readDecisions,
  readStatementChanges,
  type Bank,
  type DateRange,
  type PolicyDb,
} from "@/lib/queries/policy";
import type { Read } from "@/lib/queries/shell";
import { publicClient } from "@/lib/supabase/public";

/** One bank's grid: its held rate decisions, their decision documents' stored pairs (a page at a time), cut to cells. */
export async function readBankTimeline(
  db: PolicyDb,
  bank: Bank,
  documents: readonly StripDocument[],
  range: DateRange,
): Promise<{ timeline: BankTimeline }> {
  const decisions = await readDecisions(db, range, bank);
  const latest = latestBySlug(documents);
  const ids = decisions.flatMap((d) => {
    const doc = latest.get(d.slug);
    return doc ? [doc.id] : [];
  });
  const diffs = await readDecisionDiffPairs(db, ids);
  return { timeline: buildBankTimeline(bank, TIMELINE_ROWS[bank], decisions, documents, diffs) };
}

export type LanguageTimeline = {
  rbi: Read<{ timeline: BankTimeline }>;
  fed: Read<{ timeline: BankTimeline }>;
};

const UNCONFIGURED = "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set";

export async function readLanguageTimeline(): Promise<LanguageTimeline> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("policy", "policy-timeline");
  const readAt = new Date().toISOString();
  const db = publicClient();
  if (!db) {
    const no = failed("language timeline", UNCONFIGURED);
    return { rbi: no, fed: no };
  }
  // Decisions are dated in each bank's zone; the range runs to today in IST, which is never behind New York's date.
  const range = { from: POLICY_START, to: dateInZone(readAt, "Asia/Kolkata") };
  const documents = await attempt("decision documents", async () => ({ documents: (await readStatementChanges(db)).documents }));
  const [rbi, fed] = documents.ok
    ? await Promise.all([
        attempt("RBI language timeline", () => readBankTimeline(db, "RBI", documents.documents, range)),
        attempt("Fed language timeline", () => readBankTimeline(db, "Fed", documents.documents, range)),
      ])
    : [documents, documents];
  if (!rbi.ok || !fed.ok) cacheLife("failedRead");
  return { rbi, fed };
}
