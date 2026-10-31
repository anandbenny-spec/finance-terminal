// web/lib/equity/screener.ts: the screener's filtering and its cell states (2A-18; Spec 02A §8). Pure. Filtering is
// client-side over the snapshot (at most 200 rows). Nothing here computes a figure for display: a filter compares the
// stored value, converted only to the unit its input box is labelled in (a stored fraction 0.18 against "18" per cent,
// full rupees against crore), and a column that is null for a row never matches an active filter on it.
import type { ScreenerNote, ScreenerRow } from "@/lib/queries/api";

export type FilterKey = "marketCap" | "pe" | "pb" | "roe" | "cagr3" | "ebitMargin" | "debtToEquity" | "promoter" | "dividendYield";

export type RangeFilter = {
  key: FilterKey;
  label: string;
  /** What the min and max boxes are in (named in the label, so the box needs no extra explanation). */
  unit: string;
  /** The row's stored value, in the box's unit; null when the row has none. */
  value: (row: ScreenerRow) => number | null;
};

/** A ratio stored as a fraction, in per cent, rounded so 0.07 reads 7 and never 7.000000000000001. */
const pct = (fraction: number | null): number | null => (fraction === null ? null : Math.round(fraction * 1e8) / 1e6);

export const RANGE_FILTERS: readonly RangeFilter[] = [
  { key: "marketCap", label: "Market cap", unit: "₹ crore", value: (r) => (r.market_cap === null ? null : r.market_cap / 10_000_000) },
  { key: "pe", label: "P/E", unit: "times", value: (r) => r.pe_ttm },
  { key: "pb", label: "P/B", unit: "times", value: (r) => r.pb },
  { key: "roe", label: "ROE", unit: "%", value: (r) => pct(r.roe) },
  { key: "cagr3", label: "Revenue CAGR, 3 years", unit: "%", value: (r) => pct(r.revenue_cagr_3y) },
  { key: "ebitMargin", label: "EBIT margin", unit: "%", value: (r) => pct(r.ebit_margin) },
  { key: "debtToEquity", label: "Debt/equity", unit: "times", value: (r) => r.debt_to_equity },
  { key: "promoter", label: "Promoter holding", unit: "%", value: (r) => r.promoter_pct },
  { key: "dividendYield", label: "Dividend yield", unit: "%", value: (r) => pct(r.dividend_yield) },
];

export type Bounds = { min: string; max: string };

export type Filters = {
  /** Index slugs; a company matches when it is in any chosen index (none chosen: all). */
  indexes: readonly string[];
  /** A peer group's key; null: all. */
  peerGroup: string | null;
  ranges: Partial<Record<FilterKey, Bounds>>;
};

export const NO_FILTERS: Filters = { indexes: [], peerGroup: null, ranges: {} };

/** The number a box holds, or null when it is empty or not a number (then the bound is ignored). */
export function parseBound(text: string | undefined): number | null {
  if (text === undefined || text.trim() === "") return null;
  const n = Number(text.replace(/,/g, "").trim());
  return Number.isFinite(n) ? n : null;
}

export function isActive(filters: Filters): boolean {
  return (
    filters.indexes.length > 0 ||
    filters.peerGroup !== null ||
    RANGE_FILTERS.some((f) => parseBound(filters.ranges[f.key]?.min) !== null || parseBound(filters.ranges[f.key]?.max) !== null)
  );
}

export function applyFilters(rows: readonly ScreenerRow[], filters: Filters): ScreenerRow[] {
  return rows.filter((row) => {
    if (filters.indexes.length > 0 && !filters.indexes.some((slug) => row.index_slugs?.includes(slug))) return false;
    if (filters.peerGroup !== null && row.peer_group_key !== filters.peerGroup) return false;
    for (const f of RANGE_FILTERS) {
      const min = parseBound(filters.ranges[f.key]?.min);
      const max = parseBound(filters.ranges[f.key]?.max);
      if (min === null && max === null) continue;
      const v = f.value(row);
      if (v === null) return false;
      if (min !== null && v < min) return false;
      if (max !== null && v > max) return false;
    }
    return true;
  });
}

/** The peer groups present in the rows (key and label), by label; a group whose label is hidden by field policy is left out. */
export function peerGroups(rows: readonly ScreenerRow[]): { key: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const r of rows) if (r.peer_group_key && r.peer_group_label && !seen.has(r.peer_group_key)) seen.set(r.peer_group_key, r.peer_group_label);
  return [...seen].map(([key, label]) => ({ key, label })).sort((a, b) => a.label.localeCompare(b.label));
}

export type CellState =
  | { kind: "value" }
  | { kind: "na"; reason: string }
  | { kind: "held"; reason: string }
  | { kind: "missing" };

/**
 * How an empty cell reads (Brief §6.3; Spec 02A §8): a stored reason starting "N/A" is "doesn't apply"; any other
 * stored reason is "Held" with the reason in a tooltip; no reason is a plain "not available". A column with a value
 * is a value whatever its note says.
 */
export function cellState(row: ScreenerRow, column: string, value: number | null): CellState {
  if (value !== null) return { kind: "value" };
  const reason = row.notes?.[column]?.reason ?? null;
  if (!reason) return { kind: "missing" };
  return /^n\/a\b/i.test(reason) ? { kind: "na", reason } : { kind: "held", reason };
}

/** A column's label ("derived", "annualised", …) as the snapshot stores it: the first row that carries one. */
export function columnLabel(rows: readonly ScreenerRow[], column: string): string | null {
  for (const r of rows) {
    const note: ScreenerNote | undefined = r.notes?.[column];
    if (note?.label) return note.label;
  }
  return null;
}
