// web/lib/queries/equity.ts: what /screener reads (2A-18; Spec 02A §8), from `api` views with the browser key only
// (lib/supabase/public.ts), through lib/queries/read.ts's guarded reads (CLAUDE.md §6: the Data API cuts a list at
// 1,000 rows without an error, so every list asks for an exact count and throws if it can't hold that many rows).
//
// Cached for minutes ('use cache'), like the shell: the snapshot is written weekly. A failed read leaves the page in
// its error state (StateNote); the reason goes to the server log only (Arch 02). Nothing here computes a figure.
import type { SupabaseClient } from "@supabase/supabase-js";
import { cacheLife, cacheTag } from "next/cache";
import type { ApiDatabase, FieldPolicyRow, IndexMemberRow, PricedInRow, ScreenerRow } from "@/lib/queries/api";
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

/** An index the screener's chips offer; `market` is its members' market (US-06, #405), absent where a caller doesn't need it. */
export type ScreenerIndex = { slug: string; name: string; market?: "IN" | "US" };

export type ScreenerPageData =
  | { ok: true; readAt: string; rows: ScreenerRow[]; indexes: ScreenerIndex[]; policies: FieldPolicyRow[] }
  | { ok: false; readAt: string };

/** The distinct indexes of a member list, by slug. */
export function indexesOf(members: readonly IndexMemberRow[]): { slug: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const m of members) if (!seen.has(m.index_slug)) seen.set(m.index_slug, m.index_name);
  return [...seen].map(([slug, name]) => ({ slug, name })).sort((a, b) => a.slug.localeCompare(b.slug, "en", { numeric: true }));
}

/** The distinct indexes of a member list with their members' market (an index's members share one), by slug (US-06, #405). */
export function indexesWithMarket(members: readonly IndexMemberRow[]): Required<ScreenerIndex>[] {
  const market = new Map<string, "IN" | "US">();
  for (const m of members) if (!market.has(m.index_slug)) market.set(m.index_slug, m.market);
  return indexesOf(members).map((i) => ({ ...i, market: market.get(i.slug) ?? "IN" }));
}

export type PricedInPageData = { ok: true; readAt: string; operating: PricedInRow[]; lenders: PricedInRow[] } | { ok: false; readAt: string };

/** Both "What's priced in" lists (api.priced_in_operating, api.priced_in_lenders), each checked against its exact count. */
export async function readPricedIn(db: EquityDb): Promise<{ operating: PricedInRow[]; lenders: PricedInRow[] }> {
  const [operating, lenders] = await Promise.all([
    readAll<PricedInRow>("api.priced_in_operating", (from, to) =>
      db.from("priced_in_operating").select("*", { count: "exact" }).order("slug").range(from, to),
    ),
    readAll<PricedInRow>("api.priced_in_lenders", (from, to) =>
      db.from("priced_in_lenders").select("*", { count: "exact" }).order("slug").range(from, to),
    ),
  ]);
  return { operating, lenders };
}

/** Everything /screener?view=priced-in renders besides the policies; read only when the view is shown. */
export async function readPricedInPage(): Promise<PricedInPageData> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", "screener");
  const readAt = new Date().toISOString();
  const db = publicClient();
  if (!db) {
    console.error("[equity] priced-in read failed: NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
    return { ok: false, readAt };
  }
  try {
    return { ok: true, readAt, ...(await readPricedIn(db)) };
  } catch (error) {
    console.error(`[equity] priced-in read failed: ${error instanceof Error ? error.message : String(error)}`);
    return { ok: false, readAt };
  }
}

/** Everything /screener renders; each read whole or not at all. */
export async function readScreenerPage(): Promise<ScreenerPageData> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", "screener");
  const readAt = new Date().toISOString(); // read inside the cache, as lib/queries/overview.ts does, so a prerender never sees `new Date()`
  const db = publicClient();
  if (!db) {
    console.error("[equity] screener read failed: NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
    return { ok: false, readAt };
  }
  try {
    const [rows, members, policies] = await Promise.all([readScreener(db), readIndexMembers(db), readFieldPolicy(db as PolicyDb)]);
    return { ok: true, readAt, rows, indexes: indexesWithMarket(members), policies };
  } catch (error) {
    console.error(`[equity] screener read failed: ${error instanceof Error ? error.message : String(error)}`);
    return { ok: false, readAt };
  }
}
