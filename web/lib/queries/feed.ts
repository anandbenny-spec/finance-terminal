// web/lib/queries/feed.ts: what /feed.xml reads (#252): about three years of held rate decisions and their votes, from
// `api` views with the browser key only, through lib/queries/policy.ts's guarded reads. Cached for hours and tagged
// "policy", like the policy pages. A failed read, or no Data API configured, returns a failure the route answers with
// a 503: never an empty feed, which would tell readers there is nothing new. The reason goes to the server log only.
import { cacheLife, cacheTag } from "next/cache";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import { readDecisions, readVotes } from "@/lib/queries/policy";
import type { Read } from "@/lib/queries/shell";
import { publicClient } from "@/lib/supabase/public";

/** How far back the feed reads (the feed shows the latest 20; three years leaves room for left-out meetings). */
const YEARS_BACK = 3;

function failed(error: unknown): { ok: false } {
  console.error(`[feed] decisions read failed: ${error instanceof Error ? error.message : String(error)}`);
  return { ok: false };
}

export async function readFeedDecisions(): Promise<Read<{ decisions: PolicyMeetingRow[]; votes: PolicyVoteRow[]; readAt: string }>> {
  "use cache";
  cacheLife("hours");
  cacheTag("policy");
  const db = publicClient();
  if (!db) return failed("NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  try {
    const now = new Date();
    const to = now.toISOString().slice(0, 10);
    const from = `${now.getUTCFullYear() - YEARS_BACK}${to.slice(4)}`;
    const decisions = await readDecisions(db, { from, to });
    const votes = await readVotes(db, decisions.map((m) => m.slug));
    return { ok: true, decisions, votes, readAt: now.toISOString() };
  } catch (error) {
    return failed(error);
  }
}
