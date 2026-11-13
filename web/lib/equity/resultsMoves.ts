// The results-day move shown under an earnings call (Spec 02B §5.12, Spec 02A §5.10; #471). Every figure is a stored value
// of `api.results_moves` (migration 0044); nothing is computed here. The words a visitor reads for a window that was left out,
// and the line saying which close counts. A market move goes through `Delta`; nobody is told what it means for the future.
import type { TaskKey } from "@/lib/feeds";
import { dateInZone, formatDate, formatReleaseTime } from "@/lib/format";
import type { ResultsMoveRow } from "@/lib/queries/api";

/** The field policy that gates the panel and its read (hidden at first: Anand flips it after the re-judge, #471 part 3). */
export const RESULTS_FIELD = "equity.results_move";

export const RESULTS_NOT_A_FORECAST = "Moves after results, not a forecast";

/** The task behind the panel's Freshness. */
export const RESULTS_TASKS = ["equity.results_reactions"] as const satisfies readonly TaskKey[];

/** A linked results filing always has its two windows (a left-out one has its reason), so no rows means no filing is linked. */
export const NO_RESULTS_LINK = "No results filing from 2025 is linked to this call, so there is no move to show.";

export type LeftOutReason = NonNullable<ResultsMoveRow["left_out_reason"]>;

/** A left-out window in the words a visitor reads (no table, task or file names). */
export const LEFT_OUT_TEXT: Record<LeftOutReason, string> = {
  waiting: "Waiting for the closing price",
  held_factor: "Left out: a corporate action's price adjustment is still under review",
  dividend: "Left out: an ex-dividend date falls in the window",
  no_price: "Left out: no usable closing price for the window",
  dates_differ: "Left out: the Nifty 50's dates differ from the company's",
  no_index: "Left out: no Nifty 50 move for these dates",
};

const WINDOW_LABEL: Record<ResultsMoveRow["window_key"], string> = {
  d1: "First close after the release",
  d5: "Fifth close after the release",
};

export type MoveLine = {
  key: string;
  window: string;
  /** The two closes the move runs between, or null for a left-out window. */
  closes: string | null;
  own: number | null;
  nifty: number | null;
  excess: number | null;
  /** The reason in words when the window is left out. */
  leftOut: string | null;
};

/** The rows of one results filing, d1 then d5. */
export function moveLines(rows: readonly ResultsMoveRow[]): MoveLine[] {
  const order = { d1: 0, d5: 1 } as const;
  return [...rows]
    .sort((a, b) => order[a.window_key] - order[b.window_key])
    .map((r) => ({
      key: `${r.results_slug}:${r.window_key}`,
      window: WINDOW_LABEL[r.window_key],
      closes: r.left_out_reason === null && r.pre_date && r.post_date ? `${formatDate(r.pre_date)} to ${formatDate(r.post_date)}` : null,
      own: r.own_change_pct,
      nifty: r.nifty_change_pct,
      excess: r.excess_pct,
      leftOut: r.left_out_reason === null ? null : LEFT_OUT_TEXT[r.left_out_reason],
    }));
}

/** The call's results rows: the ones whose results filing is the selected call's. */
export function rowsOfCall(rows: readonly ResultsMoveRow[], callSlug: string): ResultsMoveRow[] {
  return rows.filter((r) => r.call_slug === callSlug);
}

/** How many windows are left out, as a line; null when none are. */
export function leftOutLine(lines: readonly MoveLine[]): string | null {
  const out = lines.filter((l) => l.leftOut !== null).length;
  if (out === 0) return null;
  return `${out} of ${lines.length} ${lines.length === 1 ? "window is" : "windows are"} left out; the reason is given in the row.`;
}

/**
 * The release time in IST and which close counts as the first after it. The first close after a release in market hours is that
 * day's own (the move includes the part of the day before the release); an evening or weekend release reads the next session.
 * Said only when the first window has its dates.
 */
export function releaseNote(rows: readonly ResultsMoveRow[]): string | null {
  const first = rows[0];
  if (!first) return null;
  const when = `Released ${formatDate(dateInZone(first.occurs_at, "Asia/Kolkata"))}, ${formatReleaseTime(first.occurs_at, "Asia/Kolkata")}.`;
  const d1 = rows.find((r) => r.window_key === "d1");
  if (!d1 || d1.left_out_reason !== null || !d1.post_date) return when;
  const sameDay = dateInZone(first.occurs_at, "Asia/Kolkata") === d1.post_date;
  return sameDay
    ? `${when} Released during market hours, so the first close after it is that day's, and the move includes the part of the day before the release.`
    : `${when} The first close after it is ${formatDate(d1.post_date)}.`;
}

export function resultsSources(rows: readonly ResultsMoveRow[]): string[] {
  const named = rows.find((r) => r.attribution)?.attribution;
  return [named ?? "Source: NSE"];
}
