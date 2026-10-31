// Test rows shaped like api.screener (test data, not real figures), shared by the screener tests.
import type { ScreenerRow } from "@/lib/queries/api";

export function row(slug: string, over: Partial<ScreenerRow> = {}): ScreenerRow {
  return {
    slug, name: `Company ${slug}`, short_name: slug.toUpperCase(), nse_symbol: slug.toUpperCase(), market: "IN", currency: "INR",
    peer_group_key: "it", peer_group_label: "IT services", snapshot_date: "2026-10-30", price: 1500, price_date: "2026-10-30",
    market_cap: 5e12, shares: 3e9, pe_ttm: 20, pb: 5, ev: 5e12, ev_ebit: 15, ev_sales: 4, dividend_yield: 0.02, roe: 0.18,
    ebit_margin: 0.25, revenue_cagr_3y: 0.1, revenue_cagr_5y: 0.09, debt_to_equity: 0.2, promoter_pct: 50, beta: 0.9, return_1y: 0.12,
    traded_value_median_3m: 1e9, implied_growth: null, delivered_growth: null, growth_gap: null, implied_roe: null, delivered_roe: null,
    roe_gap: null, valuation_family: "operating", notes: null, ebit_label: "derived", beta_label: "bottom-up, relevered",
    implied_growth_label: null, implied_roe_label: null, index_slugs: ["nifty-50"], source_key: "nse", ...over,
  };
}
