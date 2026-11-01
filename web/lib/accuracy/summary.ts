// web/lib/accuracy/summary.ts: the /accuracy page's model (#135; Plan 02 §4). Pure: it only arranges the rows
// api.quality_summary returned (counts by module and period) and formats them. It computes no figure: a count is the
// database's, a missing measure shows as "Not available" rather than 0, and the one share the projection holds
// (freshness_on_time_share, 0 to 1) is only shown as a percentage.
import { formatAxisNumber, formatCount, formatShare, NOT_AVAILABLE } from "@/lib/format";
import type { EvalSummaryRow, QualitySummaryRow } from "@/lib/queries/api";

export type Period = QualitySummaryRow["period"];

export const PERIODS: readonly { id: Period; label: string }[] = [
  { id: "30d", label: "Last 30 days" },
  { id: "90d", label: "Last 90 days" },
  { id: "all", label: "All time" },
];

export const DEFAULT_PERIOD: Period = "30d";

/** The period named by `?period=`; anything else (missing, repeated, unknown) is the default. */
export function parsePeriod(raw: string | string[] | undefined): Period {
  return PERIODS.find((p) => p.id === raw)?.id ?? DEFAULT_PERIOD;
}

export function periodHref(period: Period): string {
  return `/accuracy?period=${period}`;
}

export function periodLabel(period: Period): string {
  return PERIODS.find((p) => p.id === period)!.label.toLowerCase();
}

// Module keys to their names on the site (the modules of py/config/quality.yaml). The first module shown is module 1's.
const MODULE_LABELS: Record<string, string> = {
  policy: "Fed vs RBI policy tracker",
  equity: "Equity screener and valuation",
};

/** A module's name: the site's own where it has one, else its key in plain words. */
export function moduleLabel(key: string): string {
  return MODULE_LABELS[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export type ModuleSection = {
  module: string;
  label: string;
  /** One row per measure, for the selected period. */
  measures: ReadonlyMap<string, QualitySummaryRow>;
};

/** The modules that have rows for `period`, "policy" first and the rest by name. */
export function moduleSections(rows: readonly QualitySummaryRow[], period: Period): ModuleSection[] {
  const byModule = new Map<string, Map<string, QualitySummaryRow>>();
  for (const r of rows) {
    if (r.period !== period) continue;
    if (!byModule.has(r.module)) byModule.set(r.module, new Map());
    byModule.get(r.module)!.set(r.measure, r);
  }
  return [...byModule.entries()]
    .map(([module, measures]) => ({ module, label: moduleLabel(module), measures }))
    .sort((a, b) => (a.module === "policy" ? -1 : b.module === "policy" ? 1 : a.label.localeCompare(b.label)));
}

/** A count as the page shows it: grouped, or "—" when the projection holds no row (never a made-up 0). */
export function countOf(section: ModuleSection, measure: string): string {
  const row = section.measures.get(measure);
  return row ? formatCount(Math.round(row.value)) : NOT_AVAILABLE;
}

/** The on-time share as a percentage, with the task-days it is out of; "—" without a row or without days. */
export function onTimeShare(section: ModuleSection): { value: string; context: string | null } {
  const row = section.measures.get("freshness_on_time_share");
  if (!row) return { value: NOT_AVAILABLE, context: null };
  return {
    value: formatShare(row.value * 100, 1),
    context: row.n === null ? null : `out of ${formatCount(row.n)} task-days`,
  };
}

/** The latest `as_of` among the rows shown, as a date (YYYY-MM-DD in UTC), for the panels' "Data to". */
export function dataTo(rows: readonly QualitySummaryRow[]): string | null {
  const latest = rows.map((r) => r.as_of).sort().at(-1);
  return latest ? latest.slice(0, 10) : null;
}

export type EvalRunLine = { key: string; evalSet: string; metric: string; value: string; items: string; date: string };

/** "policy_hawk_dove" → "Policy hawk dove": the stored name in plain words. */
export function plainName(name: string): string {
  return name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** The passing runs for the site-wide table, newest first. Cells are text; dates come from `formatTableDate`'s caller. */
export function evalRunLines(rows: readonly EvalSummaryRow[], formatDay: (isoDate: string) => string): EvalRunLine[] {
  return [...rows]
    .sort((a, b) => b.run_at.localeCompare(a.run_at) || a.eval_set.localeCompare(b.eval_set))
    .map((r) => ({
      key: `${r.eval_set}|${r.model}|${r.metric}`,
      evalSet: plainName(r.eval_set),
      metric: plainName(r.metric),
      value: formatAxisNumber(r.value, 2),
      items: r.n_items === null ? NOT_AVAILABLE : formatCount(r.n_items),
      date: formatDay(r.run_at.slice(0, 10)),
    }));
}

/** Task keys such as `policy.fed_documents` or `site.quality_summary`: never shown to a visitor (CLAUDE.md §4). */
export const TASK_KEY_PATTERN = /\b(?:policy|site|core|ops|equity|crypto|ipo)\.[a-z][a-z0-9_]+\b/;
