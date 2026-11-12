// web/lib/equity/company.ts: the company page's selection rules (2A-19; Spec 02A §8). Pure. Nothing here computes a
// figure for display beyond what the spec asks (the day's change from the two closes `api.companies` returns): it
// picks which stored rows and periods a panel shows, and what words a state is told in.
import { callOf } from "@/lib/equity/calls";
import type { WindowKey } from "@/lib/equity/rate";
import type { Market } from "@/lib/format";
import type {
  CompanyFinancialRow,
  CompanyMetricRow,
  CompanyPriceRow,
  CompanyRow,
  CorporateActionRow,
  FinancialLine,
} from "@/lib/queries/api";

export type Basis = "consolidated" | "standalone";
export type TabId = "overview" | "financials" | "valuation" | "rate-decisions" | "calls";

export const TABS: readonly { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "financials", label: "Financials" },
  { id: "valuation", label: "Valuation" },
  { id: "rate-decisions", label: "Rate decisions" },
  { id: "calls", label: "Earnings calls" },
];

/** The tab in a path segment (/companies/tcs/valuation); anything else is the Overview, so a caller that wants a 404 uses `tabParam`. */
export function tabOf(value: string | string[] | undefined): TabId {
  return value === "financials" || value === "valuation" || value === "rate-decisions" || value === "calls" ? value : "overview";
}

/** The basis in a path segment (/companies/tcs/financials/standalone); consolidated unless standalone is asked for. */
export function basisOf(value: string | string[] | undefined): Basis {
  return value === "standalone" ? "standalone" : "consolidated";
}

// ---- the company page's addresses (R05-9b, 12 Nov 2026) ----
// The tab and its view are path segments, never query strings, so each page is built ahead and cached like Policy Day:
//   /companies/tcs                           the Overview (no segment)
//   /companies/tcs/valuation                 a tab
//   /companies/tcs/financials/standalone     a tab's view: the basis,
//   /companies/tcs/rate-decisions/d5         the rate-decision window,
//   /companies/tcs/calls/q2-fy27             or a call. The default view has no segment.
// /companies/tcs/value-chain is its own static route and is not a tab.

/** A tab with its own segment: every tab but the Overview. */
export type PathTab = Exclude<TabId, "overview">;

/** The tab a path segment names, or null (the route answers 404): `overview` and `value-chain` are not tab segments. */
export function tabParam(segment: string | undefined): PathTab | null {
  return segment === "financials" || segment === "valuation" || segment === "rate-decisions" || segment === "calls" ? segment : null;
}

/** What a tab's view segment selects: the basis, the rate-decision window or a call's slug. */
export type CompanyViewSelection = { basis: Basis; window: WindowKey; call: string | undefined };

/** A tab's default view: consolidated, one day, the default call. */
export const DEFAULT_VIEW: CompanyViewSelection = { basis: "consolidated", window: "d1", call: undefined };

/**
 * The view a tab's second segment names; null when the tab doesn't take that value (the route answers 404). No segment is the
 * tab's default view (consolidated, one day, the default call). A call slug is any slug-shaped string (`callOf`); one the
 * company doesn't have shows the default call, as the old query link did.
 */
export function viewParam(tab: PathTab, segment: string | undefined): CompanyViewSelection | null {
  const base = DEFAULT_VIEW;
  if (segment === undefined) return base;
  if (tab === "financials") return segment === "standalone" ? { ...base, basis: "standalone" } : null;
  if (tab === "rate-decisions") return segment === "d5" ? { ...base, window: "d5" } : null;
  if (tab === "calls") {
    const call = callOf(segment);
    return call ? { ...base, call } : null;
  }
  return null;
}

/** The tab and view a catch-all path (`["financials", "standalone"]`) names, or null when it names none (the route answers 404). */
export function pathParam(path: string[] | undefined): { tab: PathTab; view: CompanyViewSelection } | null {
  if (!path || path.length < 1 || path.length > 2) return null;
  const tab = tabParam(path[0]);
  const view = tab ? viewParam(tab, path[1]) : null;
  return tab && view ? { tab, view } : null;
}

/**
 * The path of a company's tab and, for a tab that has views, its view (`standalone`, `d5` or a call's slug). The one builder
 * of company links: a view the tab doesn't take is left out, so a link never points at a 404. The Overview is the bare slug.
 */
export function companyHref(slug: string, tab: TabId, view?: string): string {
  const base = `/companies/${slug}`;
  if (tab === "overview") return base;
  const selection = viewParam(tab, view);
  return `${base}/${tab}${view !== undefined && selection ? `/${view}` : ""}`;
}

export const SOURCE_LINE: Record<string, string> = {
  nse: "Source: NSE",
  bse: "Source: BSE",
  // The US slice (US-06, #405; Spec 02A.1 §7): prices and corporate actions from Yahoo Finance, statements from SEC EDGAR.
  yfinance: "Source: Yahoo Finance",
  sec: "Source: SEC EDGAR",
};

/** The source line for a stored `source_key` (NSE when it names none we know: the posture's default). */
export function sourceLineFor(key: string | null | undefined): string {
  return SOURCE_LINE[(key ?? "nse").toLowerCase()] ?? SOURCE_LINE.nse;
}

export const DAMODARAN = "Source: Aswath Damodaran, NYU Stern";
export const SEC_EDGAR = SOURCE_LINE.sec;
export const YAHOO = SOURCE_LINE.yfinance;

// ---------------------------------------------------------------------------------------------------------------
// The US slice (US-06, #405; Spec 02A.1 §1, §7): a US company's page in dollars, with the tabs that apply to it

/** The index a market's companies are screened in, as the page names it. */
export const UNIVERSE: Record<Market, string> = { IN: "Nifty 50", US: "Dow 30" };

/** The tabs a US company's page shows: no earnings calls (no free transcript source) and no rate decisions (rate sensitivity
 * for US stocks comes after 18 Nov). Their reasons, for a deep link to one (Spec 02A.1 §7). */
export const US_TABS: readonly TabId[] = ["overview", "financials", "valuation"];
export const NOT_COVERED_US: Partial<Record<TabId, string>> = {
  calls: "US earnings calls aren't covered: no free transcript source.",
  "rate-decisions": "Rate decisions aren't covered for US companies yet: the decision-day moves are measured for the Nifty 50 only.",
};
export const VALUE_CHAIN_NOT_COVERED_US = "The value chain isn't covered for US companies: it is read from Indian filings (BRSR and related-party disclosures).";

/** The tabs a company's page shows, in order: every tab for India, the three that apply for the US. */
export function tabsFor(market: Market): readonly { id: TabId; label: string }[] {
  return market === "US" ? TABS.filter((t) => US_TABS.includes(t.id)) : TABS;
}

/** The symbol shown beside a company's name: the ticker for a US company, the NSE symbol for an Indian one. */
export function symbolOf(row: { market: Market; nse_symbol: string | null; ticker?: string | null }): string | null {
  return row.market === "US" ? (row.ticker ?? null) : row.nse_symbol;
}

// ---------------------------------------------------------------------------------------------------------------
// Overview: the price series

export type PricePoint = { time: string; value: number };

export type PriceData = {
  /** Adjusted closes, oldest first, for the chart. */
  points: PricePoint[];
  /** Every session, newest first, for "View as table" (raw close and the adjusted one or null). */
  rows: CompanyPriceRow[];
  /** The last session whose adjusted close is held back; earlier prices are held back to this date. Null: none held. */
  heldTo: string | null;
  from: string | null;
  to: string | null;
};

/** Splits the paged price rows into the chart's points and the held-back note's date. */
export function priceData(rows: readonly CompanyPriceRow[]): PriceData {
  const ordered = [...rows].sort((a, b) => (a.trade_date < b.trade_date ? -1 : a.trade_date > b.trade_date ? 1 : 0));
  const points: PricePoint[] = [];
  let heldTo: string | null = null;
  for (const r of ordered) {
    if (r.adj_close !== null && !r.adj_held) points.push({ time: r.trade_date, value: r.adj_close });
    else heldTo = r.trade_date;
  }
  return {
    points,
    rows: ordered.reverse(),
    heldTo,
    from: points[0]?.time ?? null,
    to: points.at(-1)?.time ?? null,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Overview: the key figures

export type StatKey =
  | "pe_ttm"
  | "pb"
  | "ev_ebit_ttm"
  | "roe"
  | "ebit_margin"
  | "net_margin"
  | "dividend_yield"
  | "beta_regression"
  | "beta_bottom_up";

/** Which period kinds a figure comes from, in order of preference (the metric task's own: py/src/ft/equity/metrics.py). */
export const STAT_PERIODS: Record<StatKey, readonly string[]> = {
  pe_ttm: ["market"],
  pb: ["market"],
  ev_ebit_ttm: ["market"],
  roe: ["year"],
  ebit_margin: ["ttm", "year"],
  net_margin: ["ttm", "year"],
  dividend_yield: ["market"],
  beta_regression: ["market"],
  beta_bottom_up: ["market"],
};

const BASIS_RANK: Record<string, number> = { consolidated: 0, standalone: 1, none: 2 };

/** The metric's row to show: its preferred period kind, the latest period end, consolidated before standalone. */
export function pickMetric(rows: readonly CompanyMetricRow[], key: string, kinds: readonly string[]): CompanyMetricRow | null {
  for (const kind of kinds) {
    const own = rows.filter((r) => r.metric_key === key && r.period_kind === kind);
    if (own.length === 0) continue;
    return own.reduce((best, r) => {
      if (r.period_end !== best.period_end) return r.period_end > best.period_end ? r : best;
      return (BASIS_RANK[r.basis] ?? 3) < (BASIS_RANK[best.basis] ?? 3) ? r : best;
    });
  }
  return null;
}

/** A metric's rows for a bank-ratio line: one period kind, one basis, newest first. */
export function metricByPeriod(rows: readonly CompanyMetricRow[], key: string, kind: string, basis: string): Map<string, CompanyMetricRow> {
  const out = new Map<string, CompanyMetricRow>();
  for (const r of rows) if (r.metric_key === key && r.period_kind === kind && r.basis === basis) out.set(r.period_end, r);
  return out;
}

/** True where a family's EV-based figures do not apply (Brief §6.3; the views also say so per row). */
export function isLender(family: string | null | undefined): boolean {
  return family === "lender" || family === "insurer";
}

// ---------------------------------------------------------------------------------------------------------------
// The header's day change

/** The unadjusted close-to-close change the header shows, or why it is held back (a split or bonus between the two sessions). */
export type DayChange = { kind: "value"; ratio: number } | { kind: "held" } | { kind: "none" };

const PRICE_EVENTS = new Set(["split", "bonus", "consolidation"]);

/** `api.companies` returns the two latest closes; the day's change is held when a share action went ex between them. */
export function dayChange(company: CompanyRow, actions: readonly CorporateActionRow[]): DayChange {
  const { last_close: last, previous_close: prev, last_close_date: lastDate, previous_close_date: prevDate } = company;
  if (last === null || prev === null || prev === 0 || !lastDate || !prevDate) return { kind: "none" };
  const between = actions.some(
    (a) => PRICE_EVENTS.has(a.kind) && a.status !== "withdrawn" && a.ex_date !== null && a.ex_date > prevDate && a.ex_date <= lastDate,
  );
  if (between) return { kind: "held" };
  return { kind: "value", ratio: last / prev - 1 };
}

// ---------------------------------------------------------------------------------------------------------------
// Financials

export type FamilyKind = "operating" | "lender" | "insurer";

export function familyKind(family: string | null | undefined): FamilyKind {
  return family === "lender" ? "lender" : family === "insurer" ? "insurer" : "operating";
}

export type LineSpec = { id: FinancialLine; label: string; kind: "money" | "price" | "ratio" };

const PROFIT: LineSpec[] = [
  { id: "pbt", label: "Profit before tax", kind: "money" },
  { id: "tax", label: "Tax", kind: "money" },
  { id: "pat", label: "Profit for the period", kind: "money" },
  { id: "eps_basic", label: "Earnings per share, basic", kind: "price" },
];

export const PNL_LINES: Record<FamilyKind, LineSpec[]> = {
  operating: [
    { id: "revenue", label: "Revenue from operations", kind: "money" },
    { id: "other_income", label: "Other income", kind: "money" },
    { id: "finance_cost", label: "Finance costs", kind: "money" },
    { id: "ebit_derived", label: "EBIT", kind: "money" },
    ...PROFIT,
  ],
  lender: [
    { id: "interest_earned", label: "Interest earned", kind: "money" },
    { id: "interest_expended", label: "Interest expended", kind: "money" },
    { id: "operating_profit", label: "Operating profit", kind: "money" },
    { id: "provisions", label: "Provisions", kind: "money" },
    ...PROFIT,
  ],
  insurer: [
    { id: "gross_premium", label: "Gross premium", kind: "money" },
    { id: "net_premium", label: "Net premium", kind: "money" },
    { id: "total_income", label: "Total income", kind: "money" },
    ...PROFIT,
  ],
};

export const BALANCE_LINES: Record<FamilyKind, LineSpec[]> = {
  operating: [
    { id: "total_assets", label: "Total assets", kind: "money" },
    { id: "cash", label: "Cash and cash equivalents", kind: "money" },
    { id: "bank_balances_other", label: "Other bank balances", kind: "money" },
    { id: "current_investments", label: "Current investments", kind: "money" },
    { id: "noncurrent_investments", label: "Non-current investments", kind: "money" },
    { id: "trade_receivables", label: "Trade receivables", kind: "money" },
    { id: "inventories", label: "Inventories", kind: "money" },
    { id: "total_equity", label: "Total equity", kind: "money" },
    { id: "borrowings_current", label: "Borrowings, current", kind: "money" },
    { id: "borrowings_noncurrent", label: "Borrowings, non-current", kind: "money" },
    { id: "trade_payables", label: "Trade payables", kind: "money" },
  ],
  lender: [
    { id: "total_assets", label: "Total assets", kind: "money" },
    { id: "deposits", label: "Deposits", kind: "money" },
    { id: "advances", label: "Advances", kind: "money" },
    { id: "total_equity", label: "Total equity", kind: "money" },
  ],
  insurer: [
    { id: "total_assets", label: "Total assets", kind: "money" },
    { id: "total_equity", label: "Total equity", kind: "money" },
  ],
};

/** A US company's statements (Spec 02A.1 §5.2): the lines US-02's adapter fills from SEC EDGAR, one set for every family
 * (10-K and 10-Q filings carry no bank or insurer schedule the adapter reads). EBIT is operating income as filed. */
export const US_PNL_LINES: LineSpec[] = [
  { id: "revenue", label: "Revenue", kind: "money" },
  { id: "ebit_derived", label: "EBIT", kind: "money" },
  { id: "finance_cost", label: "Interest expense", kind: "money" },
  { id: "pbt", label: "Income before income taxes", kind: "money" },
  { id: "tax", label: "Income tax", kind: "money" },
  { id: "pat_owners", label: "Net income attributable to the company", kind: "money" },
  { id: "eps_basic", label: "Earnings per share, basic", kind: "price" },
  { id: "eps_diluted", label: "Earnings per share, diluted", kind: "price" },
];

export const US_BALANCE_LINES: LineSpec[] = [
  { id: "total_assets", label: "Total assets", kind: "money" },
  { id: "cash", label: "Cash and cash equivalents", kind: "money" },
  { id: "total_equity", label: "Total equity", kind: "money" },
  { id: "equity_owners", label: "Stockholders' equity", kind: "money" },
  { id: "borrowings_current", label: "Current debt", kind: "money" },
  { id: "borrowings_noncurrent", label: "Long-term debt", kind: "money" },
];

export const US_CASH_FLOW_LINES: LineSpec[] = [
  { id: "cfo", label: "Cash from operations", kind: "money" },
  { id: "cfi", label: "Cash from investing", kind: "money" },
  { id: "cff", label: "Cash from financing", kind: "money" },
  { id: "capex", label: "Capital expenditure", kind: "money" },
];

/** What a US quarter's figures are (Spec 02A.1 §5.2): a 10-K reports the year, so the fourth quarter is derived. */
export const US_QUARTER_NOTE = "A fourth quarter is the fiscal year less the first nine months: a 10-K reports only the full year.";
/** What the US debt lines are (us_inputs.py's label, Spec 02A.1 §5.4). */
export const US_DEBT_NOTE = "Debt is as filed; lease liabilities aren't counted.";

export const CASH_FLOW_LINES: LineSpec[] = [
  { id: "cfo", label: "Cash from operations", kind: "money" },
  { id: "cfi", label: "Cash from investing", kind: "money" },
  { id: "cff", label: "Cash from financing", kind: "money" },
  { id: "capex", label: "Capital expenditure", kind: "money" },
  { id: "dividends_paid", label: "Dividends paid", kind: "money" },
];

/** What the borrowings lines mean (Anand, 30 Oct 2026, on #117). */
export const DEBT_NOTE = "Borrowings exclude lease liabilities (not in results filings).";

export type PeriodSpec = { kinds: readonly CompanyFinancialRow["period_kind"][]; count: number };

export const PERIODS = {
  quarterly: { kinds: ["quarter"], count: 8 },
  annual: { kinds: ["year"], count: 5 },
  balance: { kinds: ["instant"], count: 6 },
  cashFlow: { kinds: ["half_year", "year"], count: 6 },
} as const satisfies Record<string, PeriodSpec>;

/** A US company's periods (Spec 02A.1 §7: fiscal years and quarters): balance sheets at each quarter end, and cash flow by
 * fiscal year only, as filed (a quarter's cash flow would be a difference of two year-to-date figures). */
export const US_PERIODS = {
  quarterly: { kinds: ["quarter"], count: 8 },
  annual: { kinds: ["year"], count: 5 },
  balance: { kinds: ["instant"], count: 8 },
  cashFlow: { kinds: ["year"], count: 5 },
} as const satisfies Record<string, PeriodSpec>;

export const KIND_WORDS: Record<string, string> = {
  quarter: "Quarter",
  half_year: "Half-year",
  nine_months: "Nine months",
  year: "Year",
  instant: "Balance sheet",
};

/** The newest `count` rows of the given kinds on one basis, newest first; cash flow keeps only rows that carry a cash flow. */
export function periodsOf(
  rows: readonly CompanyFinancialRow[],
  basis: Basis,
  spec: PeriodSpec,
  needs?: FinancialLine,
): CompanyFinancialRow[] {
  return rows
    .filter((r) => r.basis === basis && spec.kinds.includes(r.period_kind) && (needs === undefined || r[needs] !== null))
    .sort((a, b) => (a.period_end < b.period_end ? 1 : a.period_end > b.period_end ? -1 : 0))
    .slice(0, spec.count);
}

/** Which bases the company has filed, and the one to show: the asked-for one, else the other when it is all there is. */
export function chooseBasis(rows: readonly CompanyFinancialRow[], asked: Basis): { basis: Basis; available: Basis[]; fellBack: boolean } {
  const available = (["consolidated", "standalone"] as const).filter((b) => rows.some((r) => r.basis === b));
  if (available.includes(asked) || available.length === 0) return { basis: asked, available: [...available], fellBack: false };
  return { basis: available[0], available: [...available], fellBack: true };
}

// ---------------------------------------------------------------------------------------------------------------
// Corporate actions

export const FACTOR_WORDS: Record<CorporateActionRow["factor_status"], string> = {
  applied: "Applied to earlier prices",
  held: "Under review",
  none: "No price adjustment",
};

/** Announced actions whose ex-date is still ahead (the card's note from #216), apart from the rest; each newest first. */
export function splitActions(actions: readonly CorporateActionRow[], today: string): { upcoming: CorporateActionRow[]; past: CorporateActionRow[] } {
  const live = actions.filter((a) => a.status !== "withdrawn");
  const isUpcoming = (a: CorporateActionRow) => a.status === "announced" && (a.ex_date === null || a.ex_date >= today);
  const byDate = (a: CorporateActionRow, b: CorporateActionRow) => (a.ex_date ?? "") < (b.ex_date ?? "") ? 1 : (a.ex_date ?? "") > (b.ex_date ?? "") ? -1 : 0;
  return {
    upcoming: live.filter(isUpcoming).sort((a, b) => -byDate(a, b)),
    past: live.filter((a) => !isUpcoming(a)).sort(byDate),
  };
}

export const KIND_LABEL: Record<string, string> = {
  split: "Split",
  bonus: "Bonus issue",
  consolidation: "Consolidation",
  dividend: "Dividend",
  rights: "Rights issue",
  demerger: "Demerger",
  merger: "Merger",
  buyback: "Share repurchase",
  other: "Other",
};
