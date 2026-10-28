// web/lib/queries/latest.ts: the read and the redirect behind /policy/fed/latest and /policy/rbi/latest (#147).
//
// Deliberately NOT cached ('use cache' is absent): the route must name the newest held meeting as of this request,
// not as of the build, and a decision stored by `watch` has to be reachable at once. Under Cache Components a GET
// handler stops prerendering when it reads the request or makes a network call (Next 16 docs, Route Handlers, "With
// Cache Components"); this one does both, and the build output lists it as dynamic. The read is one row from
// `api.policy_meetings` with the browser key, the same read /policy's cards use (readLatestDecision).
import type { NextRequest } from "next/server";
import { latestTarget } from "@/lib/policy/latest";
import { readLatestDecision, type Bank } from "@/lib/queries/policy";
import { publicClient } from "@/lib/supabase/public";

/** The date of the bank's newest held rate decision; null when none is held, this build has no Data API, or the read failed
 * (the reason goes to the server log only: visitors never see errors, Arch 02). */
export async function readLatestDecisionDate(bank: Bank): Promise<string | null> {
  const db = publicClient();
  if (!db) return null;
  try {
    return (await readLatestDecision(db, bank))?.decision_date ?? null;
  } catch (error) {
    console.error(`[policy] ${bank} latest decision date read failed: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

/** A 307 to the newest held meeting on the requested tab (or to the Latest decisions section when there is none).
 * The Location is a path, valid for any host (a preview, the production domain); `no-store` keeps any cache from
 * freezing the answer. */
export async function latestRedirect(bank: Bank, request: NextRequest): Promise<Response> {
  const date = await readLatestDecisionDate(bank);
  const location = latestTarget(bank, date, request.nextUrl.searchParams.get("tab"));
  return new Response(null, { status: 307, headers: { Location: location, "Cache-Control": "no-store" } });
}
