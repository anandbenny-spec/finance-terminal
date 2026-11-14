// The filed period end of a US figure's date (US-06b, #564; US-08's final verdict, judgment call 4). The US tasks key each
// statement row by the month end nearest its filed end (py/src/ft/equity/us_inputs.py, `nearest_month_end` and `by_month`:
// a 52/53-week filer's quarter to Sat 2 Aug 2026 is keyed 31 Jul 2026), so India's month arithmetic finds a period's
// quarters, year-earlier period and opening balance sheet. The metrics and the valuation inputs carry that key as their
// date, while the Financials tab shows the filed end. This maps a key back to the filed end, on the page only: the
// company's `api.company_financials` row of the matching kind whose own key is that key. No row, or two rows with different
// ends, gives no date, never the key (CLAUDE.md §1: a date a visitor sees is right, labelled or absent). India's periods end
// on month ends and are never resolved: the resolver is for `market = 'US'` only, and India's pages don't call it.
// Decided by Claude (standing delegation, 24 Oct 2026; the card): a display-side fix that keeps the tasks' keying and every
// stored row. Nothing here changes a figure.
import type { AssumptionRow } from "@/lib/equity/valuation";
import type { CompanyFinancialRow, CompanyMetricRow } from "@/lib/queries/api";

/** A 52-53-week year ends within a week of a month end (us_inputs.MONTH_SLACK). */
export const MONTH_SLACK = 7;

/** The statement kinds a key can come from. */
export type StatementKind = "quarter" | "year" | "instant";

/** What the resolver reads: a filed row's kind and end (`api.company_financials`). */
export type FiledEnd = Pick<CompanyFinancialRow, "period_kind" | "period_end">;

/** A key and its statement kind to the filed end, or null when no single filed row has that key. */
export type FiledDates = (key: string, kind: StatementKind) => string | null;

const DAY = 86_400_000;
const utc = (iso: string): number => Date.parse(`${iso}T00:00:00Z`);
const iso = (ms: number): string => new Date(ms).toISOString().slice(0, 10);

/** The last day of the month `months` after the month of `ms` (metrics.month_end). */
function monthEnd(ms: number, months: number): number {
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months + 1, 0);
}

/** us_inputs.nearest_month_end: the month end within MONTH_SLACK days of `date` (the nearer one, the earlier on a tie), else `date`. */
export function nearestMonthEnd(date: string): string {
  const d = utc(date);
  const [before, own] = [monthEnd(d, -1), monthEnd(d, 0)];
  const best = Math.abs(own - d) < Math.abs(before - d) ? own : before;
  return Math.abs(best - d) <= MONTH_SLACK * DAY ? iso(best) : date;
}

/** The resolver over a company's filed rows: a key maps to the one filed end of that kind whose own key it is. */
export function filedDates(rows: readonly FiledEnd[]): FiledDates {
  const ends = new Map<string, Set<string>>();
  for (const r of rows) {
    const at = `${r.period_kind}|${nearestMonthEnd(r.period_end)}`;
    ends.set(at, (ends.get(at) ?? new Set()).add(r.period_end));
  }
  return (key, kind) => {
    const found = ends.get(`${kind}|${key}`);
    return found && found.size === 1 ? [...found][0] : null;
  };
}

/** A metric's period kind to the statement it is dated by; a market figure (price, market value, beta) keeps its date. */
const METRIC_STATEMENT: Partial<Record<string, StatementKind>> = { quarter: "quarter", ttm: "quarter", year: "year", instant: "instant" };

/** A metric row's date as a visitor sees it: the filed end for a statement period, the stored date for a market figure. */
export function metricDate(dates: FiledDates, row: Pick<CompanyMetricRow, "period_kind" | "period_end">): string | null {
  const kind = METRIC_STATEMENT[row.period_kind];
  return kind ? dates(row.period_end, kind) : row.period_end;
}

/**
 * The assumptions whose date is a statement key (lib/equity/valuation.ts, `assumptionRows`, by row id): revenue over the
 * last four quarters (the latest quarter), the three-year EBIT margin, tax rate and sales to capital and the cost of debt
 * (the latest financial year), and a lender's delivered return on equity (its latest financial year). The others are
 * dated by the price, a market series or a rate's year in force, and keep their dates.
 */
export const ASSUMPTION_STATEMENT: Readonly<Record<string, StatementKind>> = {
  revenue: "quarter",
  margin: "year",
  "tax-near": "year",
  s2c: "year",
  kd: "year",
  delivered: "year",
};

/** The assumptions table's rows with each statement date as filed (no date where no filed row matches). */
export function withFiledDates(rows: readonly AssumptionRow[], dates: FiledDates): AssumptionRow[] {
  return rows.map((r) => {
    const kind = ASSUMPTION_STATEMENT[r.id];
    return kind && r.asOf !== null ? { ...r, asOf: dates(r.asOf, kind) } : r;
  });
}
