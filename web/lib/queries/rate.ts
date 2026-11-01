// web/lib/queries/rate.ts: what the Rate decisions tab and the sector view read (2A-25; Spec 02A §5.10, §8), from `api`
// views with the browser key only, through lib/queries/read.ts's guarded reads (CLAUDE.md §6: the Data API cuts a list
// at 1,000 rows without an error, so every list asks for an exact count and throws if it can't hold that many rows).
// Nothing here computes a statistic: the views return them (null below five moves, and while `equity.rate_sensitivity`
// is hidden).
import type { SupabaseClient } from "@supabase/supabase-js";
import { cacheLife, cacheTag } from "next/cache";
import { NIFTY_SERIES } from "@/lib/equity/rate";
import type {
  ApiDatabase,
  CompanyRateCycleRow,
  FieldPolicyRow,
  RateCycleRow,
  RateDecisionMoveRow,
  RateDecisionStatRow,
  ScreenerRow,
} from "@/lib/queries/api";
import { readScreener } from "@/lib/queries/equity";
import { readFieldPolicy, readSeriesInfo, type PolicyDb } from "@/lib/queries/policy";
import { readAll } from "@/lib/queries/read";
import { publicClient } from "@/lib/supabase/public";

export type RateDb = SupabaseClient<ApiDatabase, "api">;

/** One company's statistics (bank × decision × window), or every peer group's. */
export async function readRateStats(db: RateDb, kind: "company" | "peer_group", key?: string): Promise<RateDecisionStatRow[]> {
  return readAll<RateDecisionStatRow>(`api.rate_decision_stats ${kind}`, (from, to) => {
    let q = db.from("rate_decision_stats").select("*", { count: "exact" }).eq("subject_kind", kind);
    if (key !== undefined) q = q.eq("subject_key", key);
    return q.order("subject_key").order("bank").order("decision").order("window_key").range(from, to);
  });
}

/** One company's decision-day moves, every decision since 1 Jan 2019 in both windows. */
export async function readRateMoves(db: RateDb, slug: string): Promise<RateDecisionMoveRow[]> {
  return readAll<RateDecisionMoveRow>("api.rate_decision_moves", (from, to) =>
    db
      .from("rate_decision_moves")
      .select("*", { count: "exact" })
      .eq("subject_kind", "company")
      .eq("subject_key", slug)
      .order("bank")
      .order("meeting_date")
      .order("window_key")
      .range(from, to),
  );
}

export async function readCompanyCycles(db: RateDb, slug: string): Promise<CompanyRateCycleRow[]> {
  return readAll<CompanyRateCycleRow>("api.company_rate_cycles", (from, to) =>
    db.from("company_rate_cycles").select("*", { count: "exact" }).eq("company_slug", slug).order("bank").order("cycle_slug").range(from, to),
  );
}

export async function readRateCycles(db: RateDb): Promise<RateCycleRow[]> {
  return readAll<RateCycleRow>("api.rate_cycles", (from, to) =>
    db.from("rate_cycles").select("*", { count: "exact" }).order("bank").order("cycle_slug").range(from, to),
  );
}

/** The Nifty 50 series' own attribution line, or null when it can't be read (the footer then names the other sources). */
export async function readNiftyAttribution(db: RateDb): Promise<string | null> {
  try {
    return (await readSeriesInfo(db as PolicyDb, [NIFTY_SERIES]))[0]?.attribution ?? null;
  } catch (error) {
    console.error(`[rate] Nifty 50 attribution read failed: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

export type RateTabData = {
  stats: RateDecisionStatRow[];
  moves: RateDecisionMoveRow[];
  cycles: CompanyRateCycleRow[];
  cycleInfo: RateCycleRow[];
  policies: FieldPolicyRow[];
  niftyAttribution: string | null;
};

/** The Rate decisions tab's reads for one company, each whole or not at all (the Nifty 50's line aside). */
export async function readRateTab(db: RateDb, slug: string): Promise<RateTabData> {
  const [stats, moves, cycles, cycleInfo, policies, niftyAttribution] = await Promise.all([
    readRateStats(db, "company", slug),
    readRateMoves(db, slug),
    readCompanyCycles(db, slug),
    readRateCycles(db),
    readFieldPolicy(db as PolicyDb),
    readNiftyAttribution(db),
  ]);
  return { stats, moves, cycles, cycleInfo, policies, niftyAttribution };
}

/** A peer group and its members, from the latest screener snapshot (names for the sector view's links). */
export type PeerGroupMembers = { key: string; label: string; members: { slug: string; name: string }[] };

export function peerGroupsOf(rows: readonly ScreenerRow[]): PeerGroupMembers[] {
  const groups = new Map<string, PeerGroupMembers>();
  for (const r of rows) {
    if (!r.peer_group_key) continue;
    const g = groups.get(r.peer_group_key) ?? { key: r.peer_group_key, label: r.peer_group_label ?? r.peer_group_key, members: [] };
    g.members.push({ slug: r.slug, name: r.short_name ?? r.name });
    groups.set(r.peer_group_key, g);
  }
  return [...groups.values()]
    .map((g) => ({ ...g, members: g.members.sort((a, b) => a.name.localeCompare(b.name)) }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export type SectorPageData =
  | { ok: true; readAt: string; stats: RateDecisionStatRow[]; groups: PeerGroupMembers[]; policies: FieldPolicyRow[]; niftyAttribution: string | null }
  | { ok: false; readAt: string };

/** Everything /screener/rate-decisions renders; each read whole or not at all. */
export async function readSectorPage(): Promise<SectorPageData> {
  "use cache";
  cacheLife("minutes");
  cacheTag("equity", "rate-sensitivity");
  const readAt = new Date().toISOString(); // read inside the cache, as lib/queries/equity.ts does
  const db = publicClient();
  if (!db) {
    console.error("[rate] sector view read failed: NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
    return { ok: false, readAt };
  }
  try {
    const [stats, screener, policies, niftyAttribution] = await Promise.all([
      readRateStats(db, "peer_group"),
      readScreener(db),
      readFieldPolicy(db as PolicyDb),
      readNiftyAttribution(db),
    ]);
    return { ok: true, readAt, stats, groups: peerGroupsOf(screener), policies, niftyAttribution };
  } catch (error) {
    console.error(`[rate] sector view read failed: ${error instanceof Error ? error.message : String(error)}`);
    return { ok: false, readAt };
  }
}
