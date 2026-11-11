// Test rows shaped like the value chain views (0034, 0040): test data, not real figures, shared by the value chain tests.
import type {
  RelatedPartyTopRow,
  SegmentRow,
  ValueChainGroupRow,
  ValueChainPeerRow,
  ValueChainProductRow,
  ValueChainRow,
  ValueChainState,
} from "@/lib/queries/api";

export const FIELDS: { key: string; no: number; label: string; ref: string; kind: "share" | "count" }[] = [
  { key: "within_india_share", no: 1, label: "Inputs sourced directly from within India", ref: "P8-EI-4, Within India", kind: "share" },
  { key: "msme_share", no: 2, label: "Inputs sourced directly from MSMEs and small producers", ref: "P8-EI-4, MSMEs", kind: "share" },
  { key: "trading_house_share", no: 3, label: "Purchases from trading houses, share of purchases", ref: "P1-EI-9, Purchases (a)", kind: "share" },
  { key: "trading_houses", no: 4, label: "Trading houses purchased from", ref: "P1-EI-9, Purchases (b)", kind: "count" },
  { key: "top10_trading_share", no: 5, label: "Top 10 trading houses, share of purchases from trading houses", ref: "P1-EI-9, Purchases (c)", kind: "share" },
  { key: "dealer_share", no: 6, label: "Sales to dealers and distributors, share of sales", ref: "P1-EI-9, Sales (a)", kind: "share" },
  { key: "dealers", no: 7, label: "Dealers and distributors sold to", ref: "P1-EI-9, Sales (b)", kind: "count" },
  { key: "top10_dealer_share", no: 8, label: "Top 10 dealers and distributors, share of sales to dealers", ref: "P1-EI-9, Sales (c)", kind: "share" },
  { key: "exports_share", no: 9, label: "Exports, share of turnover", ref: "A-III-19(b)", kind: "share" },
  { key: "states_served", no: 10, label: "States served", ref: "A-III-19(a), States", kind: "count" },
  { key: "countries_served", no: 11, label: "Countries served", ref: "A-III-19(a), Countries", kind: "count" },
  { key: "rpt_purchases_share", no: 12, label: "Purchases from related parties, share of purchases", ref: "P1-EI-9, RPTs (a)", kind: "share" },
  { key: "rpt_sales_share", no: 13, label: "Sales to related parties, share of sales", ref: "P1-EI-9, RPTs (b)", kind: "share" },
];

/** Filed values for a year (percent for shares), the numbers an example company might file. */
export const VALUES: Record<string, number> = {
  within_india_share: 90.4, msme_share: 9.7, trading_house_share: 0.07, trading_houses: 12, top10_trading_share: 61.5, dealer_share: 79.33,
  dealers: 4480, top10_dealer_share: 13.9, exports_share: 4.1, states_served: 28, countries_served: 46, rpt_purchases_share: 9.8, rpt_sales_share: 13.2,
};

export type Over = { state?: ValueChainState; reason?: string | null; value?: number | null; restated?: boolean; next?: number | null; change?: number | null; changeReason?: ValueChainRow["change_reason"] };

export function card(
  fy: { label: string; start: string; end: string },
  opts: { boundary?: "standalone" | "consolidated"; months?: number; family?: string | null; exchange?: "NSE" | "BSE"; assured?: "reasonable" | "limited" | null; fields?: Record<string, Over>; filed?: string } = {},
): ValueChainRow[] {
  const { boundary = "standalone", months = 12, family = "operating", exchange = "NSE", assured = null, fields = {}, filed = "2026-07-04T10:00:00+00:00" } = opts;
  return FIELDS.map((f) => {
    const o = fields[f.key] ?? {};
    const state: ValueChainState = o.state ?? "ok";
    const value = state === "ok" ? (o.value ?? VALUES[f.key]) : state === "zero" ? 0 : null;
    const openness = f.ref.startsWith("P1-EI-9");
    return {
      company_slug: "mandm", company_name: "Mahindra & Mahindra Ltd.", short_name: "M&M", market: "IN", currency: "INR", valuation_family: family,
      fy_label: fy.label, fy_start: fy.start, fy_end: fy.end, field_key: f.key, field_no: f.no, label: f.label, item_ref: f.ref, kind: f.kind,
      value, state, state_reason: state === "held" ? (o.reason ?? "outside 0–100% as filed") : state === "absent" ? "not_reported" : null,
      restated: state === "ok" || state === "zero" ? (o.restated ?? false) : null, next_comparative: o.restated ? (o.next ?? 9.5) : null,
      assured: openness ? (assured !== null) : null, assurance: openness ? assured : null,
      change: o.change ?? null, change_reason: o.change === undefined ? (o.changeReason ?? "no_previous_year") : null,
      boundary, format_version: "2026-02-28", months, exchange, source_key: exchange.toLowerCase(), filed_at: filed, list_url: "https://example.test/brsr/mandm",
      values_label: "As reported by the company",
    } satisfies ValueChainRow;
  });
}

export const FY26 = { label: "FY 2025-26", start: "2025-04-01", end: "2026-03-31" };
export const FY25 = { label: "FY 2024-25", start: "2024-04-01", end: "2025-03-31" };
export const FY24 = { label: "FY 2023-24", start: "2023-04-01", end: "2024-03-31" };

export function product(fy: { label: string; start: string; end: string }, no: number, description: string, share: number): ValueChainProductRow {
  return {
    company_slug: "mandm", fy_label: fy.label, fy_start: fy.start, fy_end: fy.end, boundary: "standalone", exchange: "NSE", source_key: "nse",
    filed_at: "2026-07-04T10:00:00+00:00", list_url: "https://example.test/brsr/mandm", line_no: no, description, nic_code: "29101", share_of_turnover_pct: share,
  };
}

export function groupCompany(no: number, name: string, share: number): ValueChainGroupRow {
  return {
    company_slug: "mandm", fy_label: FY26.label, fy_start: FY26.start, fy_end: FY26.end, boundary: "standalone", exchange: "NSE", source_key: "nse",
    filed_at: "2026-07-04T10:00:00+00:00", list_url: "https://example.test/brsr/mandm", line_no: no, name, relation: "Subsidiary", share_held_pct: share,
  };
}

export function rpt(type: RelatedPartyTopRow["txn_type"], rank: number, counterparty: string, value: number, over: Partial<RelatedPartyTopRow> = {}): RelatedPartyTopRow {
  return {
    company_slug: "mandm", company_name: "Mahindra & Mahindra Ltd.", currency: "INR", fy_label: "FY 2025-26", fy_end_year: 2026, basis: "standalone", txn_type: type,
    rank, counterparty, relationship: "Subsidiary", value, period_from: "2025-04-01", period_to: "2026-03-31", source_keys: ["nse"], ...over,
  };
}

export function peerRow(peer: string, key: string, value: number | null, over: Partial<ValueChainPeerRow> = {}): ValueChainPeerRow {
  const f = FIELDS.find((x) => x.key === key) as (typeof FIELDS)[number];
  return {
    company_slug: "mandm", peer_slug: peer, peer_name: `${peer} Ltd.`, peer_short_name: peer.toUpperCase(), is_self: peer === "mandm", fy_label: "FY 2025-26",
    boundary: "standalone", same_basis: true, field_key: key, field_no: f.no, label: f.label, item_ref: f.ref, kind: f.kind, value,
    state: value === null ? "absent" : "ok", median_n: 4, median: 80.5, ...over,
  };
}

export function segment(no: number, description: string, revenue: number, share: number | null, over: Partial<SegmentRow> = {}): SegmentRow {
  return {
    company_slug: "mandm", company_name: "Mahindra & Mahindra Ltd.", currency: "INR", fy_label: "FY 2025-26", period_start: "2025-04-01", period_end: "2026-03-31",
    line_no: no, reporting: "multi", state: "ok", description, revenue, share_of_total_pct: share, source_key: "nse", ...over,
  };
}
