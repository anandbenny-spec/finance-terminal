// web/lib/queries/company.ts: what /companies/[slug] reads (2A-19; Spec 02A §8), from `api` views with the browser key
// only (lib/supabase/public.ts), through lib/queries/read.ts's guarded reads (CLAUDE.md §6: the Data API cuts a list at
// 1,000 rows without an error, so every list asks for an exact count and throws if it can't hold that many rows; the
// price history, about 1,950 rows, is two pages joined). Cached for minutes like the screener. A failed read leaves the
// page in its error state (StateNote); the reason goes to the server log only (Arch 02). Nothing here computes a figure.
//
// The promoter-holding read (view 0025, pushed by Anand) is the one read allowed to fail on its own: until the view
// exists, the Financials tab says that holding isn't published yet and the rest of the page is unaffected.
import type { SupabaseClient } from "@supabase/supabase-js";
import { cacheLife, cacheTag } from "next/cache";
import { dateInZone } from "@/lib/format";
import type {
  ApiDatabase,
  CompanyFinancialRow,
  CompanyMetricRow,
  CompanyPriceRow,
  CompanyRow,
  CorporateActionRow,
  PeerCompRow,
  PromoterHoldingRow,
} from "@/lib/queries/api";
import { indexesOf, readIndexMembers } from "@/lib/queries/equity";
import { readAll } from "@/lib/queries/read";
import { publicClient } from "@/lib/supabase/public";
import type { TabId } from "@/lib/equity/company";

export type CompanyDb = SupabaseClient<ApiDatabase, "api">;

/** One company's row, or null when no company has the slug. */
export async function readCompany(db: CompanyDb, slug: string): Promise<CompanyRow | null> {
  const { data, error } = await db.from("companies").select("*").eq("slug", slug).limit(1);
  if (error) throw new Error(`api.companies: ${error.message}`);
  return data?.[0] ?? null;
}

/** Every session's prices, oldest first, checked against the exact count (about 1,950 rows: two pages). */
export async function readCompanyPrices(db: CompanyDb, slug: string): Promise<CompanyPriceRow[]> {
  return readAll<CompanyPriceRow>("api.company_prices", (from, to) =>
    db.from("company_prices").select("*", { count: "exact" }).eq("slug", slug).order("trade_date").range(from, to),
  );
}

export async function readCompanyMetrics(db: CompanyDb, slug: string): Promise<CompanyMetricRow[]> {
  return readAll<CompanyMetricRow>("api.company_metrics", (from, to) =>
    db
      .from("company_metrics")
      .select("*", { count: "exact" })
      .eq("slug", slug)
      .order("metric_key")
      .order("period_kind")
      .order("basis")
      .order("period_end")
      .range(from, to),
  );
}

export async function readPeers(db: CompanyDb, slug: string): Promise<PeerCompRow[]> {
  return readAll<PeerCompRow>("api.peer_comps", (from, to) =>
    db.from("peer_comps").select("*", { count: "exact" }).eq("company_slug", slug).order("row_kind").order("peer_slug").range(from, to),
  );
}

export async function readFinancials(db: CompanyDb, slug: string): Promise<CompanyFinancialRow[]> {
  return readAll<CompanyFinancialRow>("api.company_financials", (from, to) =>
    db
      .from("company_financials")
      .select("*", { count: "exact" })
      .eq("slug", slug)
      .order("basis")
      .order("period_kind")
      .order("period_end")
      .range(from, to),
  );
}

export async function readActions(db: CompanyDb, slug: string): Promise<CorporateActionRow[]> {
  return readAll<CorporateActionRow>("api.company_corporate_actions", (from, to) =>
    db
      .from("company_corporate_actions")
      .select("*", { count: "exact" })
      .eq("slug", slug)
      .order("ex_date", { ascending: false })
      .order("kind")
      .order("subject")
      .range(from, to),
  );
}

export async function readPromoterHolding(db: CompanyDb, slug: string): Promise<PromoterHoldingRow[]> {
  return readAll<PromoterHoldingRow>("api.company_promoter_holding", (from, to) =>
    db.from("company_promoter_holding").select("*", { count: "exact" }).eq("slug", slug).order("as_of", { ascending: false }).range(from, to),
  );
}

export type OverviewData = { prices: CompanyPriceRow[]; metrics: CompanyMetricRow[]; peers: PeerCompRow[] };
export type FinancialsData = {
  financials: CompanyFinancialRow[];
  metrics: CompanyMetricRow[];
  /** Null when the promoter-holding view can't be read (not pushed yet). */
  promoter: PromoterHoldingRow[] | null;
};

export type CompanyPageData =
  | {
      ok: true;
      readAt: string;
      today: string;
      company: CompanyRow;
      actions: CorporateActionRow[];
      indexNames: Record<string, string>;
      tab: TabId;
      overview: OverviewData | null;
      financials: FinancialsData | null;
    }
  | { ok: false; readAt: string };

function logFailure(what: string, error: unknown): void {
  console.error(`[company] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
}

/** Whether a company has this slug: true or false, or null when the read failed (the page then shows its error state, not a 404). */
export async function readCompanyExists(slug: string): Promise<boolean | null> {
  "use cache";
  cacheLife("minutes");
  cacheTag("equity", `company-${slug}`);
  const db = publicClient();
  if (!db) {
    logFailure(`${slug} existence`, "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
    return null;
  }
  try {
    return (await readCompany(db, slug)) !== null;
  } catch (error) {
    logFailure(`${slug} existence`, error);
    return null;
  }
}

/** The page's reads for one tab, each whole or not at all (the header's company row and actions are on both tabs). */
export async function readCompanyPage(slug: string, tab: TabId): Promise<CompanyPageData> {
  "use cache";
  cacheLife("minutes");
  cacheTag("equity", `company-${slug}`);
  const readAt = new Date().toISOString(); // read inside the cache, as lib/queries/overview.ts does
  const db = publicClient();
  if (!db) {
    logFailure(slug, "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
    return { ok: false, readAt };
  }
  try {
    const [company, actions, members] = await Promise.all([readCompany(db, slug), readActions(db, slug), readIndexMembers(db)]);
    if (!company) return { ok: false, readAt };
    const indexNames = Object.fromEntries(indexesOf(members).map((i) => [i.slug, i.name]));
    let overview: OverviewData | null = null;
    let financials: FinancialsData | null = null;
    if (tab === "overview") {
      const [prices, metrics, peers] = await Promise.all([readCompanyPrices(db, slug), readCompanyMetrics(db, slug), readPeers(db, slug)]);
      overview = { prices, metrics, peers };
    } else {
      const [rows, metrics, promoter] = await Promise.all([
        readFinancials(db, slug),
        readCompanyMetrics(db, slug),
        readPromoterHolding(db, slug).catch((error: unknown) => {
          logFailure(`${slug} promoter holding`, error);
          return null;
        }),
      ]);
      financials = { financials: rows, metrics, promoter };
    }
    return { ok: true, readAt, today: dateInZone(readAt, "Asia/Kolkata"), company, actions, indexNames, tab, overview, financials };
  } catch (error) {
    logFailure(slug, error);
    return { ok: false, readAt };
  }
}
