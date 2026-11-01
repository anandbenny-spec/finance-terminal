// The screener's row check against the dev project (2A-18; same idea as lib/queries/policy.devcheck.ts). Not part of
// `npm test`: run it with `npm run check:dev-rows` once 2A-17's views are pushed to dev and 2A-15 has written a snapshot,
// with NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY set to dev's URL and browser key (public by design).
// It only reads `api` views. A missing setting or an empty snapshot fails the check; it never skips.
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import type { ApiDatabase } from "@/lib/queries/api";
import { readCompany, readCompanyMetrics, readCompanyPrices, readFinancials, readImfForecasts, readPeers, readScenarios, readValuationRuns } from "@/lib/queries/company";
import { readIndexMembers, readScreener, type EquityDb } from "@/lib/queries/equity";

function client(): EquityDb {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error("set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to the dev project's");
  return createClient<ApiDatabase, "api">(url, key, { db: { schema: "api" }, auth: { persistSession: false, autoRefreshToken: false } });
}

async function held(query: PromiseLike<{ count: number | null; error: { message: string } | null }>): Promise<number> {
  const { count, error } = await query;
  if (error) throw new Error(error.message);
  if (count === null) throw new Error("no count");
  return count;
}

describe("the dev project (2A-18)", () => {
  const db = client();

  it("readScreener returns every company the database holds, one row each", async () => {
    const rows = await readScreener(db);
    const count = await held(db.from("screener").select("slug", { count: "exact", head: true }));
    expect(rows.length).toBe(count);
    expect(rows.length, "has 2A-15 written a screener snapshot on dev?").toBeGreaterThan(0);
    expect(new Set(rows.map((r) => r.slug)).size).toBe(rows.length);
    expect(rows.every((r) => r.market && r.currency)).toBe(true);
  });

  it("readIndexMembers returns every member of every index", async () => {
    const members = await readIndexMembers(db);
    const count = await held(db.from("index_members").select("slug", { count: "exact", head: true }));
    expect(members.length).toBe(count);
    expect(members.length, "has 2A-01 stored the Nifty 50's members on dev?").toBeGreaterThan(0);
  });
});

describe("the dev project (2A-19, the company page)", () => {
  const db = client();

  it("reads one company's header row, and null for a slug nobody has", async () => {
    const rows = await readScreener(db);
    expect(rows.length, "has 2A-15 written a screener snapshot on dev?").toBeGreaterThan(0);
    const company = await readCompany(db, rows[0].slug);
    expect(company?.slug).toBe(rows[0].slug);
    expect(company?.market && company?.currency).toBeTruthy();
    expect(await readCompany(db, "no-such-company")).toBeNull();
  });

  it("reads a company's whole price history against the exact count (two pages)", async () => {
    const slug = (await readScreener(db))[0].slug;
    const prices = await readCompanyPrices(db, slug);
    const count = await held(db.from("company_prices").select("slug", { count: "exact", head: true }).eq("slug", slug));
    expect(prices.length).toBe(count);
    expect(prices.length, "has 2A-05 stored prices on dev?").toBeGreaterThan(0);
    expect(new Set(prices.map((p) => p.trade_date)).size).toBe(prices.length);
  });

  it("reads a company's metrics, peers and filings against their exact counts", async () => {
    const slug = (await readScreener(db))[0].slug;
    const [metrics, peers, financials] = await Promise.all([readCompanyMetrics(db, slug), readPeers(db, slug), readFinancials(db, slug)]);
    expect(metrics.length).toBe(await held(db.from("company_metrics").select("slug", { count: "exact", head: true }).eq("slug", slug)));
    expect(peers.length).toBe(await held(db.from("peer_comps").select("company_slug", { count: "exact", head: true }).eq("company_slug", slug)));
    expect(financials.length).toBe(await held(db.from("company_financials").select("slug", { count: "exact", head: true }).eq("slug", slug)));
  });
});

describe("the dev project (2A-20, the Valuation tab)", () => {
  const db = client();

  it("reads a company's valuation runs and its stored grid against their exact counts", async () => {
    const slug = (await readScreener(db))[0].slug;
    const [runs, scenarios] = await Promise.all([readValuationRuns(db, slug), readScenarios(db, slug)]);
    expect(runs.length).toBe(await held(db.from("company_valuation").select("slug", { count: "exact", head: true }).eq("slug", slug)));
    expect(scenarios.length).toBe(await held(db.from("valuation_scenarios").select("slug", { count: "exact", head: true }).eq("slug", slug)));
    expect(scenarios.length).toBeLessThanOrEqual(375);
  });

  it("reads the IMF forecasts of India's growth and inflation whole", async () => {
    const rows = await readImfForecasts(db);
    expect(rows.length, "has 2A-05 stored the IMF series on dev?").toBeGreaterThan(0);
    expect(rows.every((r) => r.series_key.startsWith("imf."))).toBe(true);
  });
});
