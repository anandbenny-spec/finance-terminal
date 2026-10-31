// web/lib/equity/valuation.ts: the Valuation tab's reading of the stored runs (2A-20; Spec 02A §8, §5.7, §5.9). Pure.
// Nothing here computes a valuation: the reverse DCF, the implied ROE and the scenario grid are solved in Python and
// stored (CLAUDE.md §1: no number the database didn't return). This file only picks which run a company shows, reads
// the inputs a run stored (each with its value, unit, as-of date and source) into the assumptions table's rows, and
// finds the grid's centre and the cell a visitor's three choices name.
import { formatCount, formatDate, formatShare, type Market } from "@/lib/format";
import type { CompanyValuationRow, ForecastRow, ValuationScenarioRow } from "@/lib/queries/api";

type Json = Record<string, unknown>;

const rec = (v: unknown): Json | null => (v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Json) : null);

/** A stored decimal (the engine writes them as strings) or number as a number; null for anything else. */
export function num(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);

/** One input the engine stored: a value with its unit, as-of date and source, or the reason it is held. */
export type StoredInput = { value: number | null; unit: string; asOf: string | null; source: string; held: string | null; detail: Json };

export function storedInput(v: unknown): StoredInput | null {
  const r = rec(v);
  if (!r) return null;
  return {
    value: num(r.value),
    unit: text(r.unit) ?? "fraction",
    asOf: text(r.as_of),
    source: text(r.source) ?? "",
    held: text(r.held),
    detail: rec(r.detail) ?? {},
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Which run

export type RunKind = "reverse_dcf" | "implied_roe";

/** A lender is read through the return on equity its price implies, everyone else through the reverse DCF. */
export function leadKind(family: string | null | undefined): RunKind {
  return family === "lender" ? "implied_roe" : "reverse_dcf";
}

export function runOf(rows: readonly CompanyValuationRow[], kind: CompanyValuationRow["kind"]): CompanyValuationRow | null {
  return rows.find((r) => r.kind === kind) ?? null;
}

/** The field policy that gates a run's figures (Spec 02A §3.3). */
export const GATE: Record<CompanyValuationRow["kind"], string> = {
  reverse_dcf: "equity.implied_growth",
  implied_roe: "equity.implied_roe",
  dcf_default: "equity.dcf_scenario",
};

// ---------------------------------------------------------------------------------------------------------------
// The lead panel

export type Lead = {
  kind: RunKind;
  /** solved, no_solution or not_applicable; null while the model fields are hidden. */
  status: CompanyValuationRow["status"];
  implied: number | null;
  delivered: number | null;
  /** The gap the engine stored (implied − delivered, in fractions); null when either side is missing. */
  gap: number | null;
  /** How many years the delivered figure covers, where the engine stored it. */
  years: number | null;
  reason: string | null;
  deliveredReason: string | null;
  label: string | null;
  asOf: string;
};

export function leadOf(run: CompanyValuationRow): Lead {
  const inputs = rec(run.inputs) ?? {};
  const delivered = storedInput(run.kind === "implied_roe" ? rec(inputs.lender)?.delivered_roe : inputs.delivered);
  const years = num(delivered?.detail.years) ?? num(delivered?.detail.n);
  return {
    kind: run.kind === "implied_roe" ? "implied_roe" : "reverse_dcf",
    status: run.status,
    implied: run.result,
    delivered: run.delivered,
    gap: num(inputs.gap),
    years,
    reason: run.reason,
    deliveredReason: run.delivered_reason,
    label: run.result_label,
    asOf: run.as_of,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// The assumptions table

export type AssumptionKind = "fraction" | "money" | "price" | "shares" | "beta" | "multiple";

export type AssumptionRow = {
  id: string;
  label: string;
  kind: AssumptionKind;
  value: number | null;
  /** A figure shown as two parts (the weights of equity and debt), pre-split. */
  value2?: number | null;
  held: string | null;
  asOf: string | null;
  source: string;
  derived: boolean;
  /** One sentence of how it was built, from the stored detail. */
  note: string | null;
};

const DRIVER_SOURCE = "Derived by the model from the filings";

function fromInput(id: string, label: string, kind: AssumptionKind, input: StoredInput | null, note: string | null = null): AssumptionRow | null {
  if (!input) return null;
  return { id, label, kind, value: input.value, held: input.held, asOf: input.asOf, source: input.source, derived: false, note };
}

function derived(
  id: string,
  label: string,
  kind: AssumptionKind,
  value: unknown,
  asOf: string | null,
  source: string,
  note: string | null = null,
): AssumptionRow | null {
  const v = num(value);
  if (v === null) return null;
  return { id, label, kind, value: v, held: null, asOf, source, derived: true, note };
}

const pct = (v: unknown, decimals = 2): string | null => {
  const n = num(v);
  return n === null ? null : formatShare(n, decimals);
};

/** A stored edition label: a date is shown as one, anything else as stored. */
function edition(v: unknown): string | null {
  const t = text(v);
  if (!t) return null;
  return /^\d{4}-\d{2}-\d{2}$/.test(t) ? formatDate(t) : t;
}

function riskFreeNote(d: Json): string | null {
  const y = pct(d.india_10y_pct);
  const s = pct(d.default_spread_pct);
  if (!y || !s) return null;
  const ed = edition(d.spread_edition);
  return `10-year government yield ${y} less the default spread ${s}${ed ? ` (Damodaran edition of ${ed})` : ""}`;
}

function erpNote(d: Json): string | null {
  const m = pct(d.mature_erp_pct);
  const c = pct(d.india_crp_pct);
  if (!m || !c) return null;
  const ed = edition(d.crp_edition);
  return `Implied mature-market premium ${m} plus India's country risk premium ${c}${ed ? ` (Damodaran edition of ${ed})` : ""}`;
}

/** Every input a run stored, as the assumptions table's rows (value, as-of date, source), in the order they enter the model. */
export function assumptionRows(run: CompanyValuationRow): AssumptionRow[] {
  const inputs = rec(run.inputs);
  if (!inputs) return [];
  const market = rec(inputs.market) ?? {};
  const rows: (AssumptionRow | null)[] = [];
  const rf = storedInput(market.risk_free);
  const erp = storedInput(market.erp);
  if (run.kind === "implied_roe") {
    const lender = rec(inputs.lender) ?? {};
    const model = rec(inputs.model) ?? {};
    const bookDate = storedInput(lender.pb)?.asOf ?? null;
    rows.push(
      fromInput("price", "Latest close", "price", storedInput(lender.price)),
      fromInput("pb", "Price to book", "multiple", storedInput(lender.pb)),
      fromInput("rf", "Risk-free rate", "fraction", rf, rf?.detail ? riskFreeNote(rf.detail) : null),
      fromInput("erp", "Equity risk premium", "fraction", erp, erp?.detail ? erpNote(erp.detail) : null),
      fromInput("beta", "Industry beta, as published", "beta", storedInput(lender.beta)),
      derived("ke", "Cost of equity", "fraction", model.cost_of_equity, bookDate, DRIVER_SOURCE, "Risk-free rate plus beta times the equity risk premium"),
      derived("g", "Terminal growth", "fraction", model.terminal_growth, rf?.asOf ?? null, DRIVER_SOURCE, "Set equal to the risk-free rate"),
      fromInput("delivered", "Delivered return on equity, average", "fraction", storedInput(lender.delivered_roe)),
    );
    return rows.filter((r): r is AssumptionRow => r !== null);
  }
  const company = rec(inputs.company) ?? {};
  const drivers = rec(inputs.drivers) ?? {};
  const capital = rec(inputs.capital) ?? {};
  const notes = rec(inputs.notes) ?? {};
  const ebit3 = storedInput(company.ebit_3fy);
  const tax3 = storedInput(company.tax_3fy);
  const cap3 = storedInput(company.opening_capital_3fy);
  const beta = storedInput(company.beta);
  const industry = text(beta?.detail.industry) ?? text(beta?.detail.damodaran_industry);
  const fin = storedInput(company.finance_cost_fy);
  const mcap = storedInput(company.market_cap);
  const bound = text(capital.cost_of_debt_bounded);
  rows.push(
    fromInput("price", "Latest close", "price", storedInput(inputs.price)),
    fromInput("revenue", "Revenue, last four quarters", "money", storedInput(company.revenue_ttm)),
    derived("margin", "EBIT margin, last three financial years", "fraction", drivers.margin, ebit3?.asOf ?? null, DRIVER_SOURCE, "EBIT divided by revenue, summed over the three years"),
    derived("tax-near", "Tax rate, years 1 to 5", "fraction", drivers.tax_effective, tax3?.asOf ?? null, DRIVER_SOURCE, text(notes.tax_rate_years_1_5)),
    derived("tax-far", "Tax rate, year 10 on", "fraction", drivers.tax_marginal, storedInput(market.marginal_tax)?.asOf ?? null, storedInput(market.marginal_tax)?.source ?? DRIVER_SOURCE, "The marginal rate; the rate moves to it in a straight line"),
    derived("s2c", "Sales to capital", "multiple", drivers.sales_to_capital, cap3?.asOf ?? null, DRIVER_SOURCE, "Revenue divided by invested capital at the start of each year, over three years"),
    fromInput("rf", "Risk-free rate", "fraction", rf, rf?.detail ? riskFreeNote(rf.detail) : null),
    fromInput("erp", "Equity risk premium", "fraction", erp, erp?.detail ? erpNote(erp.detail) : null),
    fromInput("beta", "Beta, bottom-up", "beta", beta, industry ? `Industry: ${industry}` : null),
    derived("ke", "Cost of equity", "fraction", capital.cost_of_equity, rf?.asOf ?? null, DRIVER_SOURCE, "Risk-free rate plus beta times the equity risk premium"),
    derived(
      "kd",
      "Cost of debt, before tax",
      "fraction",
      capital.cost_of_debt,
      fin?.asOf ?? null,
      DRIVER_SOURCE,
      bound === "floor" ? "Bounded up to the gross 10-year yield" : bound === "cap" ? "Bounded down to that yield plus the spread cap" : "Finance costs over average borrowings",
    ),
  );
  const we = num(capital.weight_equity);
  const wd = num(capital.weight_debt);
  if (we !== null && wd !== null) {
    rows.push({ id: "weights", label: "Weights, equity and debt", kind: "fraction", value: we, value2: wd, held: null, asOf: mcap?.asOf ?? null, source: DRIVER_SOURCE, derived: true, note: "Market capitalisation and borrowings; borrowings exclude lease liabilities (not in results filings)" });
  }
  rows.push(
    derived("wacc", "Cost of capital (WACC)", "fraction", capital.wacc ?? drivers.wacc, rf?.asOf ?? null, DRIVER_SOURCE),
    derived("gt", "Terminal growth", "fraction", drivers.terminal_growth, rf?.asOf ?? null, DRIVER_SOURCE, "Set equal to the risk-free rate"),
    fromInput("shares", "Shares outstanding today", "shares", storedInput(company.shares)),
  );
  return rows.filter((r): r is AssumptionRow => r !== null);
}

// ---------------------------------------------------------------------------------------------------------------
// The scenario grid

export type GridOptions = { growth: number[]; margin: number[]; wacc: number[] };
export type GridChoice = { growth: number; margin: number; wacc: number };

const sortedUnique = (values: readonly (number | null)[]): number[] => [...new Set(values.filter((v): v is number => v !== null))].sort((a, b) => a - b);

/** The distinct growth, margin and WACC values the stored grid holds. */
export function gridOptions(rows: readonly ValuationScenarioRow[]): GridOptions {
  return {
    growth: sortedUnique(rows.map((r) => r.growth)),
    margin: sortedUnique(rows.map((r) => r.ebit_margin)),
    wacc: sortedUnique(rows.map((r) => r.wacc)),
  };
}

function nearest(values: readonly number[], hint: number | null): number {
  if (hint === null) return values[Math.floor((values.length - 1) / 2)];
  return values.reduce((best, v) => (Math.abs(v - hint) < Math.abs(best - hint) ? v : best));
}

/** The grid's centre: the 3-year margin, the model's WACC and the growth step nearest the default run's, else the middle value. */
export function gridCentre(options: GridOptions, defaultRun: CompanyValuationRow | null): GridChoice | null {
  if (options.growth.length === 0 || options.margin.length === 0 || options.wacc.length === 0) return null;
  const inputs = rec(defaultRun?.inputs);
  return {
    growth: nearest(options.growth, num(rec(inputs?.growth)?.used)),
    margin: nearest(options.margin, num(rec(inputs?.drivers)?.margin)),
    wacc: nearest(options.wacc, num(rec(inputs?.drivers)?.wacc)),
  };
}

export const cellKey = (g: number | null, m: number | null, w: number | null): string => `${g}|${m}|${w}`;

/** The stored cell a choice names, or null when the grid skipped that combination. */
export function cellOf(rows: readonly ValuationScenarioRow[], choice: GridChoice): ValuationScenarioRow | null {
  const key = cellKey(choice.growth, choice.margin, choice.wacc);
  return rows.find((r) => cellKey(r.growth, r.ebit_margin, r.wacc) === key) ?? null;
}

// ---------------------------------------------------------------------------------------------------------------
// The IMF context

export type ImfYear = { year: string; gdp: number | null; cpi: number | null };
export type ImfContext = { vintage: string; years: ImfYear[] } | null;

export const IMF_GDP = "imf.NGDP_RPCH.IND";
export const IMF_CPI = "imf.PCPIPCH.IND";

/** The newest edition's forecasts of India's real GDP growth and CPI inflation for the next five years, side by side; no arithmetic on them. */
export function imfContext(rows: readonly ForecastRow[], today: string): ImfContext {
  const firstYear = today.slice(0, 4);
  const newest = (key: string): string | null => rows.filter((r) => r.series_key === key).map((r) => r.vintage_date).sort().at(-1) ?? null;
  const gdpV = newest(IMF_GDP);
  const cpiV = newest(IMF_CPI);
  const vintage = [gdpV, cpiV].filter((v): v is string => v !== null).sort().at(-1) ?? null;
  if (!vintage) return null;
  const pick = (key: string, v: string | null) => new Map(rows.filter((r) => r.series_key === key && r.vintage_date === v).map((r) => [r.target_label, r.value]));
  const gdp = pick(IMF_GDP, gdpV);
  const cpi = pick(IMF_CPI, cpiV);
  const years = [...new Set([...gdp.keys(), ...cpi.keys()])].filter((y) => y >= firstYear).sort().slice(0, 5);
  return { vintage, years: years.map((year) => ({ year, gdp: gdp.get(year) ?? null, cpi: cpi.get(year) ?? null })) };
}

// ---------------------------------------------------------------------------------------------------------------

/** A stored count of shares for the table: whole shares, grouped by the company's market. */
export function sharesText(value: number, market: Market): string {
  return formatCount(Math.round(value), market);
}
