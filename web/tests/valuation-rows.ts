// Test rows shaped like the valuation views (test data, not real figures), shared by the Valuation tab's tests.
import type { CompanyValuationRow, FieldPolicyRow, ForecastRow, ValuationScenarioRow } from "@/lib/queries/api";

const input = (value: string | null, unit: string, asOf: string | null, source: string, extra: object = {}) => ({ value, unit, as_of: asOf, source, ...extra });

export const OPERATING_INPUTS = {
  company: {
    revenue_ttm: input("2500000000000", "money", "2026-06-30", "revenue over the last four quarters (filings)"),
    ebit_3fy: input("1500000000000", "money", "2026-03-31", "derived EBIT over the last three financial years (filings)"),
    tax_3fy: input("400000000000", "money", "2026-03-31", "tax expense over the last three financial years (filings)"),
    opening_capital_3fy: input("3000000000000", "money", "2026-03-31", "invested capital at the start of each year (filings)"),
    finance_cost_fy: input("20000000000", "money", "2026-03-31", "finance costs in the latest financial year (filings)"),
    market_cap: input("14000000000000", "money", "2026-10-30", "market capitalisation at the latest close"),
    beta: input("0.9", "beta", "2026-10-30", "bottom-up beta: Damodaran's India industry beta relevered at market D/E", { detail: { industry: "Software (System and Application)" } }),
    shares: input("3600000000", "shares", "2026-10-30", "shares outstanding today"),
  },
  market: {
    risk_free: input("0.0531", "fraction", "2026-09-01", "India 10-year government bond yield (FRED, OECD, monthly) less India's sovereign default spread (Aswath Damodaran, NYU Stern, ctryprem)", { detail: { india_10y_pct: "7.06", default_spread_pct: "1.75", spread_edition: "2026-07-01" } }),
    erp: input("0.0721", "fraction", "2026-10-01", "Implied equity risk premium of a mature market plus India's country risk premium (Aswath Damodaran, NYU Stern)", { detail: { mature_erp_pct: "4.30", india_crp_pct: "2.91", crp_edition: "2026-07-01" } }),
    marginal_tax: input("0.25168", "fraction", "2026-04-01", "Income-tax Act 2025, section 200"),
  },
  price: input("3456.7", "money", "2026-10-30", "closing price on NSE"),
  delivered: input("0.0912", "fraction", "2026-03-31", "revenue growth a year over the last five financial years", { detail: { years: 5, basis: "consolidated" } }),
  drivers: { margin: "0.2412", tax_effective: "0.2511", tax_marginal: "0.25168", sales_to_capital: "1.8", wacc: "0.1021", terminal_growth: "0.0531" },
  capital: { cost_of_equity: "0.1181", cost_of_debt: "0.0731", cost_of_debt_bounded: "floor", weight_equity: "0.98", weight_debt: "0.02", wacc: "0.1021" },
  notes: { tax_rate_years_1_5: "The three-year effective rate" },
  gap: "0.0301",
};

export function run(kind: CompanyValuationRow["kind"], over: Partial<CompanyValuationRow> = {}): CompanyValuationRow {
  return {
    slug: "tcs", name: "TCS", market: "IN", currency: "INR", valuation_family: "operating", kind, as_of: "2026-10-30", status: "solved", state: "value", reason: null,
    price: 3456.7, result: 0.1213, result_label: "model-implied", delivered: 0.0912, delivered_reason: null, inputs: OPERATING_INPUTS, model_version: "dcf-1", source_key: "nse", ...over,
  };
}

export function lenderRun(over: Partial<CompanyValuationRow> = {}): CompanyValuationRow {
  return run("implied_roe", {
    valuation_family: "lender", result: 0.153, delivered: 0.141, inputs: {
      lender: { pb: input("2.4", "multiple", "2026-10-30", "price-to-book"), beta: input("1.05", "beta", "2026-10-30", "the industry's levered beta"), price: input("1700", "money", "2026-10-30", "closing price on NSE"), delivered_roe: input("0.141", "fraction", "2026-03-31", "average return on equity", { detail: { n: 3 } }) },
      market: OPERATING_INPUTS.market, model: { cost_of_equity: "0.1286", terminal_growth: "0.0531" }, gap: "0.012",
    }, ...over,
  });
}

export function cell(growth: number, margin: number, wacc: number, value: number | null, over: Partial<ValuationScenarioRow> = {}): ValuationScenarioRow {
  return { slug: "tcs", name: "TCS", market: "IN", currency: "INR", as_of: "2026-10-30", growth, ebit_margin: margin, wacc, value_per_share: value, state: value === null ? "not_applicable" : "value", reason: value === null ? "No value exists at these assumptions." : null, label: "model-implied", ...over };
}

/** A small grid: 3 growth steps × 3 margins × 3 WACCs; each cell's value encodes its position so a test can tell which was read. */
export function grid(): ValuationScenarioRow[] {
  const out: ValuationScenarioRow[] = [];
  [0.05, 0.075, 0.1].forEach((g, i) => [0.2112, 0.2412, 0.2712].forEach((m, j) => [0.0921, 0.1021, 0.1121].forEach((w, k) => out.push(cell(g, m, w, 1000 + i * 100 + j * 10 + k)))));
  return out;
}

export const POLICIES: FieldPolicyRow[] = [
  { field_key: "equity.implied_growth", status: "labelled", label: "model-implied" },
  { field_key: "equity.implied_roe", status: "labelled", label: "model-implied" },
  { field_key: "equity.dcf_scenario", status: "labelled", label: "model-implied" },
];

export function forecast(key: string, vintage: string, year: string, value: number | null): ForecastRow {
  return { series_key: key, series_name: key, vintage_date: vintage, target_label: year, target_start: `${year}-01-01`, target_end: `${year}-12-31`, value, value_low: null, value_high: null, label: null, document_id: null, char_start: null, char_end: null };
}
