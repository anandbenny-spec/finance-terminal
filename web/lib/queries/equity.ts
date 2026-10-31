// web/lib/queries/equity.ts: what /screener reads (2A-18; Spec 02A §8), from `api` views with the browser key only
// (lib/supabase/public.ts), through lib/queries/read.ts's guarded reads (CLAUDE.md §6: the Data API cuts a list at
// 1,000 rows without an error, so every list asks for an exact count and throws if it can't hold that many rows).
//
// Cached for minutes ('use cache'), like the shell: the snapshot is written weekly. A failed read leaves the page in
// its error state (StateNote); the reason goes to the server log only (Arch 02). Nothing here computes a figure.
import type { SupabaseClient } from "@supabase/supabase-js";
import { cacheLife, cacheTag } from "next/cache";
import type { ApiDatabase, FieldPolicyRow, IndexMemberRow, ScreenerRow } from "@/lib/queries/api";
import { readFieldPolicy, type PolicyDb } from "@/lib/queries/policy";
import { readAll } from "@/lib/queries/read";
import { publicClient } from "@/lib/supabase/public";

export type EquityDb = SupabaseClient<ApiDatabase, "api">;

/** Every company on the screener (one row each, from its latest snapshot), checked against the exact count. */
export async function readScreener(db: EquityDb): Promise<ScreenerRow[]> {
  return readAll<ScreenerRow>("api.screener", (from, to) =>
    db.from("screener").select("*", { count: "exact" }).order("slug").range(from, to),
  );
}

/** Every index's latest member set (the index chips' names). */
export async function readIndexMembers(db: EquityDb): Promise<IndexMemberRow[]> {
  return readAll<IndexMemberRow>("api.index_members", (from, to) =>
    db
      .from("index_members")
      .select("index_slug, index_name, snapshot_date, slug, name, short_name, nse_symbol, market, currency", { count: "exact" })
      .order("index_slug")
      .order("slug")
      .range(from, to),
  );
}

export type ScreenerPageData =
  | { ok: true; readAt: string; rows: ScreenerRow[]; indexes: { slug: string; name: string }[]; policies: FieldPolicyRow[] }
  | { ok: false; readAt: string };

/** The distinct indexes of a member list, by slug. */
export function indexesOf(members: readonly IndexMemberRow[]): { slug: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const m of members) if (!seen.has(m.index_slug)) seen.set(m.index_slug, m.index_name);
  return [...seen].map(([slug, name]) => ({ slug, name })).sort((a, b) => a.slug.localeCompare(b.slug));
}

/** Everything /screener renders; each read whole or not at all. */
export async function readScreenerPage(): Promise<ScreenerPageData> {
  "use cache";
  cacheLife("minutes");
  cacheTag("equity", "screener");
  const readAt = new Date().toISOString(); // read inside the cache, as lib/queries/overview.ts does, so a prerender never sees `new Date()`
  const db = publicClient();
  if (!db) {
    console.error("[equity] screener read failed: NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
    return { ok: false, readAt };
  }
  try {
    const [rows, members, policies] = await Promise.all([readScreener(db), readIndexMembers(db), readFieldPolicy(db as PolicyDb)]);
    return { ok: true, readAt, rows, indexes: indexesOf(members), policies };
  } catch (error) {
    console.error(`[equity] screener read failed: ${error instanceof Error ? error.message : String(error)}`);
    return { ok: false, readAt };
  }
}
