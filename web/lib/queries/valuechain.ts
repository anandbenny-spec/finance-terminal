// web/lib/queries/valuechain.ts: what the value chain card reads (VC-06, #191; Spec 02B §12.6), from `api` views with the browser key
// only, through lib/queries/read.ts's guarded reads (the Data API cuts a list at 1,000 rows without an error, CLAUDE.md §6). The
// views are gated by their field policies (0034): while a policy is hidden the view returns null columns and the panel holds
// back, so a read that succeeds is not a release. Every read here fails on its own: before 0034 is pushed, or when one view can't
// be read, that part is null and the rest of the page is unaffected (the panel says it couldn't load). Nothing here computes a figure.
import type { SupabaseClient } from "@supabase/supabase-js";
import { cacheLife, cacheTag } from "next/cache";
import type {
  ApiDatabase,
  FieldPolicyRow,
  RelatedPartyTopRow,
  SegmentRow,
  ValueChainGroupRow,
  ValueChainPeerRow,
  ValueChainProductRow,
  ValueChainRow,
} from "@/lib/queries/api";
import { readFieldPolicy } from "@/lib/queries/policy";
import { readAll } from "@/lib/queries/read";
import { publicClient } from "@/lib/supabase/public";

export type ValueChainDb = SupabaseClient<ApiDatabase, "api">;

/** Every field of every shown year for the company: at most 13 fields × 3 years, in year then card order. */
export async function readValueChain(db: ValueChainDb, slug: string): Promise<ValueChainRow[]> {
  return readAll<ValueChainRow>("api.value_chain", (from, to) =>
    db.from("value_chain").select("*", { count: "exact" }).eq("company_slug", slug).order("fy_end").order("field_no").range(from, to),
  );
}

export async function readValueChainProducts(db: ValueChainDb, slug: string): Promise<ValueChainProductRow[]> {
  return readAll<ValueChainProductRow>("api.value_chain_products", (from, to) =>
    db.from("value_chain_products").select("*", { count: "exact" }).eq("company_slug", slug).order("fy_end").order("line_no").range(from, to),
  );
}

export async function readValueChainGroup(db: ValueChainDb, slug: string): Promise<ValueChainGroupRow[]> {
  return readAll<ValueChainGroupRow>("api.value_chain_group", (from, to) =>
    db.from("value_chain_group").select("*", { count: "exact" }).eq("company_slug", slug).order("fy_end").order("line_no").range(from, to),
  );
}

/** The company's own top five counterparties per fiscal year and type: at most 5 × 2 × years rows. */
export async function readRelatedPartyTop(db: ValueChainDb, slug: string): Promise<RelatedPartyTopRow[]> {
  return readAll<RelatedPartyTopRow>("api.related_party_top", (from, to) =>
    db
      .from("related_party_top")
      .select("*", { count: "exact" })
      .eq("company_slug", slug)
      .order("fy_end_year")
      .order("txn_type")
      .order("rank")
      .range(from, to),
  );
}

/** The company's peer group's latest shown year: the peers × 13 fields, about 150 rows for the largest group. Asked for one company, since all companies would pass the cap. */
export async function readValueChainPeers(db: ValueChainDb, slug: string): Promise<ValueChainPeerRow[]> {
  return readAll<ValueChainPeerRow>("api.value_chain_peers", (from, to) =>
    db.from("value_chain_peers").select("*", { count: "exact" }).eq("company_slug", slug).order("peer_slug").order("field_no").range(from, to),
  );
}

export async function readSegments(db: ValueChainDb, slug: string): Promise<SegmentRow[]> {
  return readAll<SegmentRow>("api.segments", (from, to) =>
    db.from("segments").select("*", { count: "exact" }).eq("company_slug", slug).order("line_no").range(from, to),
  );
}

function logFailure(what: string, error: unknown): void {
  console.error(`[value-chain] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
}

const orNull = <T,>(what: string) => (error: unknown): T | null => {
  logFailure(what, error);
  return null;
};

/** What the Overview panel reads: the company's years, its products and the field policies (the gate). Each part is null when its read failed. */
export type ValueChainOverviewData = {
  rows: ValueChainRow[] | null;
  products: ValueChainProductRow[] | null;
  policies: FieldPolicyRow[] | null;
};

export async function readValueChainOverview(db: ValueChainDb, slug: string): Promise<ValueChainOverviewData> {
  const [rows, products, policies] = await Promise.all([
    readValueChain(db, slug).catch(orNull<ValueChainRow[]>(`${slug} value chain`)),
    readValueChainProducts(db, slug).catch(orNull<ValueChainProductRow[]>(`${slug} value chain products`)),
    readFieldPolicy(db).catch(orNull<FieldPolicyRow[]>(`${slug} field policy`)),
  ]);
  return { rows, products, policies };
}

/** What the sub-page reads: the overview's reads plus the group, the related parties, the segments and the peers. */
export type ValueChainPageData = ValueChainOverviewData & {
  group: ValueChainGroupRow[] | null;
  rpt: RelatedPartyTopRow[] | null;
  segments: SegmentRow[] | null;
  peers: ValueChainPeerRow[] | null;
};

export async function loadValueChainPage(db: ValueChainDb, slug: string): Promise<ValueChainPageData> {
  const [base, group, rpt, segments, peers] = await Promise.all([
    readValueChainOverview(db, slug),
    readValueChainGroup(db, slug).catch(orNull<ValueChainGroupRow[]>(`${slug} value chain group`)),
    readRelatedPartyTop(db, slug).catch(orNull<RelatedPartyTopRow[]>(`${slug} related party top`)),
    readSegments(db, slug).catch(orNull<SegmentRow[]>(`${slug} segments`)),
    readValueChainPeers(db, slug).catch(orNull<ValueChainPeerRow[]>(`${slug} value chain peers`)),
  ]);
  return { ...base, group, rpt, segments, peers };
}

/** The sub-page's reads, cached for minutes like the company page's tabs; a read that throws out of a `'use cache'` function is not stored. */
export async function readValueChainPage(slug: string): Promise<ValueChainPageData> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", `company-${slug}`);
  const db = publicClient();
  if (!db) throw new Error("NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  return loadValueChainPage(db, slug);
}
