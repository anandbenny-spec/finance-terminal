// A US company's reads (US-06, #405; Spec 02A.1 §7): the page reads only what it shows. No earnings-call or rate-decision view
// (those tabs don't apply), no value chain, promoter, segment-change or IMF India view (Indian disclosures), and the 10-year
// Treasury's source line instead of India's 10-year. The Overview and Valuation tabs also read the filed period ends (US-06b,
// #564), for their dates. India's reads are company-page.test.ts's, unchanged. A fake Data API
// answers every view (empty unless given rows) and records which ones were asked for.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fakeClient, type Row, type Store } from "@/tests/fake-postgrest";
import { company } from "@/tests/company-rows";
import type { TabId } from "@/lib/equity/company";

vi.mock("next/cache", () => ({ cacheLife: () => undefined, cacheTag: () => undefined }));
let given: Store = {};
let asked: string[] = [];
const store = new Proxy({} as Store, {
  get: (_, view: string) => {
    asked.push(view);
    return given[view] ?? [];
  },
});
vi.mock("@/lib/supabase/public", () => ({ publicClient: () => fakeClient(store).db }));
vi.spyOn(console, "error").mockImplementation(() => undefined);

const { readCompanyMarket, readCompanyPage } = await import("@/lib/queries/company");

const HEADER = ["companies", "company_corporate_actions", "index_members"];
const AAPL = company({ slug: "us-aapl", name: "Apple Inc.", market: "US", currency: "USD", nse_symbol: null, isin: null, ticker: "AAPL", cik: "320193", price_exchange: "NASDAQ" });
const TCS = company();

beforeEach(() => {
  given = { companies: [AAPL as unknown as Row, TCS as unknown as Row] };
  asked = [];
});

const tabViews = () => [...new Set(asked.filter((v) => !HEADER.includes(v)))].sort();

describe("a US company's reads", () => {
  it.each<[TabId, string[]]>([
    ["overview", ["company_financials", "company_metrics", "company_prices", "peer_comps"]],
    ["financials", ["company_financials", "company_metrics"]],
    ["valuation", ["company_financials", "company_valuation", "field_policy", "series", "valuation_scenarios"]],
    ["calls", []],
    ["rate-decisions", []],
  ])("reads only what the %s tab shows", async (tab, views) => {
    const out = await readCompanyPage("us-aapl", tab);
    expect(out.ok && out.tabFailed).toBe(false);
    expect(tabViews()).toEqual(views);
  });

  it("leaves out the Indian parts of each tab's data", async () => {
    const overview = await readCompanyPage("us-aapl", "overview");
    expect(overview.ok && overview.overview).toEqual({ prices: [], metrics: [], peers: [], filed: [] });
    const financials = await readCompanyPage("us-aapl", "financials");
    expect(financials.ok && financials.financials).toEqual({ financials: [], metrics: [], promoter: null });
    const valuation = await readCompanyPage("us-aapl", "valuation");
    expect(valuation.ok && valuation.valuation?.imf).toBeNull();
    for (const tab of ["calls", "rate-decisions"] as const) {
      const out = await readCompanyPage("us-aapl", tab);
      expect(out.ok && [out.overview, out.financials, out.valuation, out.rates, out.calls]).toEqual([null, null, null, null, null]);
    }
  });

  it("reads the filed ends of quarters, years and balance sheets only, for the Overview's and Valuation's dates (US-06b, #564)", async () => {
    const end = (period_kind: string, period_end: string) => ({ slug: "us-aapl", period_kind, period_end }) as unknown as Row;
    given = { ...given, company_financials: [end("quarter", "2026-09-26"), end("year", "2026-09-26"), end("instant", "2026-09-26"), end("nine_months", "2026-06-27")] };
    const overview = await readCompanyPage("us-aapl", "overview");
    expect(overview.ok && overview.overview?.filed?.map((r) => r.period_kind).sort()).toEqual(["instant", "quarter", "year"]);
    const valuation = await readCompanyPage("us-aapl", "valuation");
    expect(valuation.ok && valuation.valuation?.filed?.length).toBe(3);
  });

  it("asks the series view for the 10-year Treasury, not India's 10-year or the IMF's India forecasts", async () => {
    given = { ...given, series: [{ key: "fred.DGS10", name: "US 10-year Treasury yield", market: "US", attribution: "Source: FRED" }] };
    const out = await readCompanyPage("us-aapl", "valuation");
    expect(out.ok && out.valuation?.series?.map((s) => s.key)).toEqual(["fred.DGS10"]);
    expect(asked).not.toContain("forecasts");
  });

  it("still reads every India view for an Indian company (India unchanged)", async () => {
    await readCompanyPage("tcs", "overview");
    expect(tabViews()).toEqual(expect.arrayContaining(["company_metrics", "company_prices", "peer_comps"]));
    expect(tabViews().length).toBeGreaterThan(3); // and the value chain panel's views
  });

  it("tells the page each company's market, or null for an unknown slug", async () => {
    expect(await readCompanyMarket("us-aapl")).toBe("US");
    expect(await readCompanyMarket("tcs")).toBe("IN");
    expect(await readCompanyMarket("nope")).toBeNull();
  });
});
