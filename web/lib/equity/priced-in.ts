// "What's priced in" (2A-21; Spec 02A §5.9): the pure rules the view uses. The ranking is the database's (`gap_rank`);
// this only splits a list into the ranked rows and the rows listed below with their reasons, and decides whether the view
// shows at all. Nothing here computes a figure.
import { fieldPolicy, type FieldPolicies } from "@/components/data/Gated";
import type { PricedInRow } from "@/lib/queries/api";

/** The switch for the whole view: no row means hidden (fail closed). Anand inserts the row after reviewing the view. */
export const PRICED_IN_GATE = "site.priced_in";

/** The view shows only where `site.priced_in` is `shown` or `labelled`. */
export function pricedInVisible(policies: FieldPolicies): boolean {
  const status = fieldPolicy(policies, PRICED_IN_GATE)?.status;
  return status === "shown" || status === "labelled";
}

/** Which view `?view=` names: "priced-in" only when the view is visible, else the ordinary screener. */
export function viewOf(value: string | string[] | undefined, visible: boolean): "screener" | "priced-in" {
  return visible && value === "priced-in" ? "priced-in" : "screener";
}

/** Ranked rows (a gap and a rank), by rank; the rest, by company name, each with the reason it has no rank. */
export function splitRanked(rows: readonly PricedInRow[]): { ranked: PricedInRow[]; unranked: PricedInRow[] } {
  const ranked = rows
    .filter((r) => r.state === "value" && r.gap !== null && r.gap_rank !== null)
    .sort((a, b) => (a.gap_rank ?? 0) - (b.gap_rank ?? 0) || a.slug.localeCompare(b.slug));
  const unranked = rows.filter((r) => !ranked.includes(r)).sort((a, b) => (a.short_name ?? a.name).localeCompare(b.short_name ?? b.name));
  return { ranked, unranked };
}

/** Why a row has no rank: a held or N/A row's reason, or (a solved row with no delivered figure) the delivered figure's reason. */
export function unrankedReason(row: PricedInRow): string {
  if (row.state === "value") return row.delivered_reason ?? "No delivered figure to compare with.";
  return row.reason ?? "Held back until its checks pass.";
}
