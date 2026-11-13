// web/lib/queries/company.ts: what /companies/[slug] reads (2A-19; Spec 02A §8), from `api` views with the browser key
// only (lib/supabase/public.ts), through lib/queries/read.ts's guarded reads (CLAUDE.md §6: the Data API cuts a list at
// 1,000 rows without an error, so every list asks for an exact count and throws if it can't hold that many rows; the
// price history, about 1,950 rows, is two pages joined). Cached for minutes like the screener. A failed header read leaves the
// page in its error state (StateNote); a failed tab read holds back that tab only (#328); the reason goes to the server log only (Arch 02). Nothing here computes a figure.
//
// The promoter-holding read (view 0025, pushed by Anand) is allowed to fail on its own: until the view
// exists, the Financials tab says that holding isn't published yet and the rest of the page is unaffected. The segment-changes
// reads (DD-02, #410: view 0038 and the field policy) fail on their own the same way: before 0038 is pushed, or when either read
// fails, the panel holds back (no policy read is a hidden field) or says it couldn't load, and the rest of the tab is unaffected.
import type { SupabaseClient } from "@supabase/supabase-js";
import { cacheLife, cacheTag } from "next/cache";
import { dateInZone } from "@/lib/format";
import type {
  ApiDatabase,
  CompanyFinancialRow,
  CompanyMetricRow,
  CompanyPriceRow,
  CompanyRow,
  CompanyValuationRow,
  CorporateActionRow,
  FieldPolicyRow,
  ForecastRow,
  PeerCompRow,
  PromoterHoldingRow,
  SegmentChangeRow,
  SeriesRow,
  ValuationScenarioRow,
} from "@/lib/queries/api";
import { indexesOf, readIndexMembers } from "@/lib/queries/equity";
import { readFieldPolicy, readSeriesInfo, type PolicyDb } from "@/lib/queries/policy";
import { readCallsTab, type CallsTabData } from "@/lib/queries/calls";
import { readRateTab, type RateTabData } from "@/lib/queries/rate";
import { readAll } from "@/lib/queries/read";
import { readValueChainOverview, type ValueChainOverviewData } from "@/lib/queries/valuechain";
import { publicClient } from "@/lib/supabase/public";
import type { TabId } from "@/lib/equity/company";
import { IMF_CPI, IMF_GDP } from "@/lib/equity/valuation";
import { IN_10Y, US_10Y } from "@/lib/policy/longrates";

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

/** The company's segment line-up changes (0038, #409): one row per flag, or one row with the state; none while the field is hidden. */
export async function readSegmentChanges(db: CompanyDb, slug: string): Promise<SegmentChangeRow[]> {
  return readAll<SegmentChangeRow>("api.segment_changes", (from, to) =>
    db.from("segment_changes").select("*", { count: "exact" }).eq("company_slug", slug).order("change_no").range(from, to),
  );
}

export async function readValuationRuns(db: CompanyDb, slug: string): Promise<CompanyValuationRow[]> {
  return readAll<CompanyValuationRow>("api.company_valuation", (from, to) =>
    db.from("company_valuation").select("*", { count: "exact" }).eq("slug", slug).order("kind").range(from, to),
  );
}

/** The latest default run's stored grid: at most 375 cells (growth × EBIT margin × WACC). */
export async function readScenarios(db: CompanyDb, slug: string): Promise<ValuationScenarioRow[]> {
  return readAll<ValuationScenarioRow>("api.valuation_scenarios", (from, to) =>
    db
      .from("valuation_scenarios")
      .select("*", { count: "exact" })
      .eq("slug", slug)
      .order("growth")
      .order("ebit_margin")
      .order("wacc")
      .range(from, to),
  );
}

/** The IMF's forecasts of India's real GDP growth and CPI inflation, every edition stored. */
export async function readImfForecasts(db: CompanyDb): Promise<ForecastRow[]> {
  return readAll<ForecastRow>("api.forecasts imf", (from, to) =>
    db
      .from("forecasts")
      .select("series_key, series_name, vintage_date, target_label, target_start, target_end, value, value_low, value_high, label, document_id, char_start, char_end", { count: "exact" })
      .in("series_key", [IMF_GDP, IMF_CPI])
      .order("series_key")
      .order("vintage_date")
      .order("target_label")
      .range(from, to),
  );
}

export type OverviewData = {
  prices: CompanyPriceRow[];
  metrics: CompanyMetricRow[];
  peers: PeerCompRow[];
  /** The value chain panel's reads (VC-06, #191); each part null when its read failed (view 0034 not pushed yet). Absent: not read. */
  valueChain?: ValueChainOverviewData;
};
export type FinancialsData = {
  financials: CompanyFinancialRow[];
  metrics: CompanyMetricRow[];
  /** Null when the promoter-holding view can't be read (not pushed yet). */
  promoter: PromoterHoldingRow[] | null;
  /** api.segment_changes for this company (DD-02, #410); null when its read failed (view 0038 not pushed yet). Absent: not read. */
  segmentChanges?: SegmentChangeRow[] | null;
  /** The field policies, for the segment-changes panel's gate; null or absent when not read, which holds the panel back. */
  policies?: FieldPolicyRow[] | null;
};

export type ValuationData = {
  runs: CompanyValuationRow[];
  scenarios: ValuationScenarioRow[];
  policies: FieldPolicyRow[];
  /** Null when the IMF forecasts can't be read; the rest of the tab doesn't depend on them. */
  imf: ForecastRow[] | null;
  /** The IMF series' and the India 10-year's source lines; null when they can't be read. */
  series: SeriesRow[] | null;
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
      valuation: ValuationData | null;
      rates: RateTabData | null;
      /** The Earnings calls tab's reads (2B-19), only on that tab. */
      calls: CallsTabData | null;
      /** True when the shown tab's own reads failed (#328): the header and the other tabs still work, and the tab body says so. */
      tabFailed: boolean;
    }
  | { ok: false; readAt: string };

function logFailure(what: string, error: unknown): void {
  console.error(`[company] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
}

/** Whether a company has this slug: true or false, or null when the read failed (the page then shows its error state, not a 404). */
export async function readCompanyExists(slug: string): Promise<boolean | null> {
  "use cache";
  cacheLife("minutesNoExpire");
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

/** The company's name, for the browser tab's title (12 Nov 2026): null when no company has the slug or the read
 * fails, and the page then keeps its plain title. */
export async function readCompanyName(slug: string): Promise<string | null> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", `company-${slug}`);
  const db = publicClient();
  if (!db) return null;
  try {
    return (await readCompany(db, slug))?.name ?? null;
  } catch (error) {
    logFailure(`${slug} name`, error);
    return null;
  }
}

/** The company's market, for the page's description (US-06, #405): null when no company has the slug or the read fails. */
export async function readCompanyMarket(slug: string): Promise<"IN" | "US" | null> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", `company-${slug}`);
  const db = publicClient();
  if (!db) return null;
  try {
    return (await readCompany(db, slug))?.market ?? null;
  } catch (error) {
    logFailure(`${slug} market`, error);
    return null;
  }
}

/** What one tab reads; exactly one field is set, the rest null. */
export type TabData = {
  overview: OverviewData | null;
  financials: FinancialsData | null;
  valuation: ValuationData | null;
  rates: RateTabData | null;
  calls: CallsTabData | null;
};

/** The header's reads, which the page cannot do without: the company row (null for an unknown slug), its actions and the index names. Throws on a failed read. */
export async function loadHeader(db: CompanyDb, slug: string) {
  const [company, actions, members] = await Promise.all([readCompany(db, slug), readActions(db, slug), readIndexMembers(db)]);
  const indexNames = Object.fromEntries(indexesOf(members).map((i) => [i.slug, i.name]));
  return { company, actions, indexNames };
}

/** The reads of one tab, each whole or not at all. Throws on a failed read, except the few that may fail on their own (promoter holding, IMF).
 * A US company (US-06, #405; Spec 02A.1 §7) reads only what its page shows: no calls or rate-decision reads (those tabs don't apply),
 * no value chain, promoter, segment-change or IMF India reads (Indian disclosures), and the 10-year Treasury's source line. */
export async function loadTab(db: CompanyDb, slug: string, tab: TabId, call?: string, market: "IN" | "US" = "IN"): Promise<TabData> {
  const data: TabData = { overview: null, financials: null, valuation: null, rates: null, calls: null };
  if (market === "US") return loadUsTab(db, slug, tab, data);
  if (tab === "calls") {
    data.calls = await readCallsTab(db, slug, call);
  } else if (tab === "rate-decisions") {
    data.rates = await readRateTab(db, slug);
  } else if (tab === "valuation") {
    const [runs, scenarios, policies, imf, series] = await Promise.all([
      readValuationRuns(db, slug),
      readScenarios(db, slug),
      readFieldPolicy(db as PolicyDb),
      readImfForecasts(db).catch((error: unknown) => {
        logFailure(`${slug} IMF forecasts`, error);
        return null;
      }),
      readSeriesInfo(db as PolicyDb, [IMF_GDP, IMF_CPI, IN_10Y]).catch((error: unknown) => {
        logFailure(`${slug} valuation series`, error);
        return null;
      }),
    ]);
    data.valuation = { runs, scenarios, policies, imf, series };
  } else if (tab === "overview") {
    const [prices, metrics, peers, valueChain] = await Promise.all([
      readCompanyPrices(db, slug),
      readCompanyMetrics(db, slug),
      readPeers(db, slug),
      readValueChainOverview(db, slug),
    ]);
    data.overview = { prices, metrics, peers, valueChain };
  } else {
    const [rows, metrics, promoter, segmentChanges, policies] = await Promise.all([
      readFinancials(db, slug),
      readCompanyMetrics(db, slug),
      readPromoterHolding(db, slug).catch((error: unknown) => {
        logFailure(`${slug} promoter holding`, error);
        return null;
      }),
      readSegmentChanges(db, slug).catch((error: unknown) => {
        logFailure(`${slug} segment changes`, error);
        return null;
      }),
      readFieldPolicy(db as PolicyDb).catch((error: unknown) => {
        logFailure(`${slug} field policy`, error);
        return null;
      }),
    ]);
    data.financials = { financials: rows, metrics, promoter, segmentChanges, policies };
  }
  return data;
}

/** A US company's tab reads (Spec 02A.1 §7). Earnings calls and rate decisions read nothing: the page says they don't apply. */
async function loadUsTab(db: CompanyDb, slug: string, tab: TabId, data: TabData): Promise<TabData> {
  if (tab === "overview") {
    const [prices, metrics, peers] = await Promise.all([readCompanyPrices(db, slug), readCompanyMetrics(db, slug), readPeers(db, slug)]);
    data.overview = { prices, metrics, peers };
  } else if (tab === "financials") {
    const [rows, metrics] = await Promise.all([readFinancials(db, slug), readCompanyMetrics(db, slug)]);
    data.financials = { financials: rows, metrics, promoter: null };
  } else if (tab === "valuation") {
    const [runs, scenarios, policies, series] = await Promise.all([
      readValuationRuns(db, slug),
      readScenarios(db, slug),
      readFieldPolicy(db as PolicyDb),
      readSeriesInfo(db as PolicyDb, [US_10Y]).catch((error: unknown) => {
        logFailure(`${slug} valuation series`, error);
        return null;
      }),
    ]);
    data.valuation = { runs, scenarios, policies, imf: null, series };
  }
  return data;
}

// The page is two cached reads and an uncached join (#328). A read that throws out of a `'use cache'` function is not stored
// (Next 16), so a failure never outlives the database's recovery, and neither cached read needs a shorter cache profile
// (lib/queries/cache-profiles.test.ts allows only the no-expiry ones). The header stays all-or-nothing; a failed tab read
// costs that tab, not the page.
export async function readHeader(slug: string) {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", `company-${slug}`);
  const readAt = new Date().toISOString(); // read inside the cache, as lib/queries/overview.ts does
  const db = publicClient();
  if (!db) throw new Error("NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  return { readAt, ...(await loadHeader(db, slug)) };
}

async function readTab(slug: string, tab: TabId, call: string | undefined, market: "IN" | "US"): Promise<TabData> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", `company-${slug}`);
  const db = publicClient();
  if (!db) throw new Error("NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  return loadTab(db, slug, tab, call, market);
}

/** The page's reads for one tab. The header (company row, actions, index names) is all-or-nothing: if it fails, or no company has the slug, the result is `{ ok: false }`. A failed tab read gives `ok: true` with that tab's data null and `tabFailed` set. */
export async function readCompanyPage(slug: string, tab: TabId, call?: string): Promise<CompanyPageData> {
  let header: Awaited<ReturnType<typeof readHeader>>;
  try {
    header = await readHeader(slug);
  } catch (error) {
    logFailure(slug, error);
    return { ok: false, readAt: new Date().toISOString() };
  }
  const { readAt, company, actions, indexNames } = header;
  if (!company) return { ok: false, readAt };
  let data: TabData = { overview: null, financials: null, valuation: null, rates: null, calls: null };
  let tabFailed = false;
  try {
    data = await readTab(slug, tab, call, company.market);
  } catch (error) {
    logFailure(`${slug} ${tab} tab`, error);
    tabFailed = true;
  }
  return { ok: true, readAt, today: dateInZone(readAt, "Asia/Kolkata"), company, actions, indexNames, tab, ...data, tabFailed };
}
