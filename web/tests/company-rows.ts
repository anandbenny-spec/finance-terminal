// Test rows shaped like the company views (test data, not real figures), shared by the company page tests.
import { FINANCIAL_LINES, type CompanyFinancialRow, type CompanyMetricRow, type CompanyPriceRow, type CompanyRow, type CorporateActionRow, type PeerCompRow, type PromoterHoldingRow } from "@/lib/queries/api";
import type { CompanyPageData } from "@/lib/queries/company";

export function company(over: Partial<CompanyRow> = {}): CompanyRow {
  return {
    slug: "tcs", name: "Tata Consultancy Services Ltd.", short_name: "TCS", market: "IN", currency: "INR", isin: "INE467B01029",
    nse_symbol: "TCS", bse_code: "532540", nse_industry: "Information Technology", peer_group_key: "it", peer_group_label: "IT services",
    peer_group_label_kind: "curated", valuation_family: "operating", family_note: null, fy_end_month: 3, price_exchange: "NSE",
    listed_on: "1995-01-01", index_slugs: ["nifty-50"], index_as_of: "2026-10-30", last_close: 3456.7, last_close_date: "2026-10-30",
    previous_close: 3400, previous_close_date: "2026-10-29", price_source_key: "nse", ...over,
  };
}

export function price(date: string, close: number, adj: number | null, over: Partial<CompanyPriceRow> = {}): CompanyPriceRow {
  return { slug: "tcs", market: "IN", currency: "INR", trade_date: date, exchange: "NSE", close, volume: 1000, traded_value: 1e6, adj_close: adj, adj_held: adj === null, source_key: "nse", ...over };
}

export function metric(key: string, over: Partial<CompanyMetricRow> = {}): CompanyMetricRow {
  return {
    slug: "tcs", name: "TCS", market: "IN", currency: "INR", metric_key: key, label: key, unit: null, basis: "consolidated", period_kind: "market",
    period_end: "2026-10-30", as_of: "2026-10-30", value: 1, label_kind: null, held_reason: null, state: "value", source_key: "nse", ...over,
  };
}

export function financial(kind: CompanyFinancialRow["period_kind"], end: string, over: Partial<CompanyFinancialRow> = {}): CompanyFinancialRow {
  const lines = Object.fromEntries(FINANCIAL_LINES.map((l) => [l, null])) as Record<(typeof FINANCIAL_LINES)[number], number | null>;
  return {
    slug: "tcs", market: "IN", currency: "INR", basis: "consolidated", period_kind: kind, period_start: null, period_end: end,
    ebit_derived_label: "derived", ebit_derived_held_reason: null, ebit_derived_state: "value", format: "ind_as", audited: false,
    filed_at: `${end}T10:00:00+00:00`, source_url: `https://example.test/filing/${end}`, source_key: "nse", attribution: "Source: NSE",
    ...lines, revenue: 6.5e11, pbt: 1.2e11, pat: 9e10, ebit_derived: 1.1e11, ...over,
  };
}

export function peer(slug: string, over: Partial<PeerCompRow> = {}): PeerCompRow {
  return {
    company_slug: "tcs", market: "IN", currency: "INR", peer_group_key: "it", row_kind: "peer", peer_slug: slug, peer_name: `Company ${slug}`,
    peer_short_name: slug.toUpperCase(), peer_nse_symbol: slug.toUpperCase(), snapshot_date: "2026-10-30", pe_ttm: 22.5, pb: 6, ev_ebit: 16, ev_sales: 4,
    roe: 0.2, ebit_margin: 0.25, revenue_cagr_3y: 0.1, peer_count: null, median_n: null, median_note: null, source_key: "nse", ...over,
  };
}

export function action(over: Partial<CorporateActionRow> = {}): CorporateActionRow {
  return {
    slug: "tcs", market: "IN", currency: "INR", kind: "dividend", ex_date: "2026-07-01", record_date: "2026-07-02", subject: "Dividend - Rs 10 Per Share",
    ratio_new: null, ratio_old: null, face_value_old: null, face_value_new: null, dividend_per_share: 10, rights_price: null, exchange: "NSE", status: "effective",
    title: null, source_url: null, factor_status: "none", price_factor: null, factor_reason: null, source_key: "nse", attribution: "Source: NSE", ...over,
  };
}

export function promoter(asOf: string, over: Partial<PromoterHoldingRow> = {}): PromoterHoldingRow {
  return {
    slug: "tcs", as_of: asOf, promoter_pct: 72.3, promoter_shares: 2.6e9, total_shares: 3.6e9, filed_at: `${asOf}T10:00:00+00:00`, source_url: `https://example.test/sh/${asOf}`,
    source_key: "nse", correction: null, label_kind: null, filed_pct: null, share_ratio_pct: null, nse_pct: null, ...over,
  };
}

type Ok = Extract<CompanyPageData, { ok: true }>;
export function page(over: Partial<Ok> = {}): Ok {
  return {
    ok: true, readAt: "2026-10-31T09:00:00.000Z", today: "2026-10-31", company: company(), actions: [], indexNames: { "nifty-50": "Nifty 50" },
    tab: "overview", overview: { prices: [], metrics: [], peers: [] }, financials: null, valuation: null, rates: null, ...over,
  };
}
