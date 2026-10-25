// web/lib/policy/reactions.ts: the "Decision-day moves" table's rows (Spec 01 §4.2; Pass 1b, #70). One row per
// decision in the history table, one column per reaction series (Spec 01 §2): each cell is the move from the last
// observation before the release to the first after it, as policy.reactions stored it (api.market_reactions).
// Nothing is computed here: every figure is a row the view returned, formatted by lib/format.ts. A decision whose
// first value after the release hasn't arrived shows "—" with the §4.5 copy; a series api.series doesn't return
// (unpublished, or its source suppressed: D3's drill) shows "—", "Not available", never "waiting"; a series the
// bank's decisions don't use (the US series at RBI decisions) is left empty.
import { NOT_AVAILABLE, NOT_AVAILABLE_LABEL, formatAxisChange, formatIndexLevel, formatTableDate, formatUsdInr, formatYield, type Market } from "@/lib/format";
import type { HistoryRow } from "@/lib/policy/decisions";
import type { MarketReactionRow, SeriesRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

type Kind = "yield" | "index" | "fx";

/** The reaction series in table order (Spec 01 §2): which bank's decisions use each, and how its values read. */
export const REACTION_SERIES = [
  { key: "fred.DGS2", header: "US 2Y (bps)", banks: ["Fed"], kind: "yield" },
  { key: "fred.DGS10", header: "US 10Y (bps)", banks: ["Fed"], kind: "yield" },
  { key: "yf.^GSPC", header: "S&P 500 (%)", banks: ["Fed"], kind: "index", market: "US" },
  { key: "fred.DEXINUS", header: "USD/INR (%)", banks: ["Fed", "RBI"], kind: "fx" },
  { key: "yf.^NSEI", header: "Nifty 50 (%)", banks: ["Fed", "RBI"], kind: "index", market: "IN" },
  { key: "yf.^NSEBANK", header: "Nifty Bank (%)", banks: ["Fed", "RBI"], kind: "index", market: "IN" },
] as const satisfies readonly { key: string; header: string; banks: readonly Bank[]; kind: Kind; market?: Market }[];

export type ReactionKey = (typeof REACTION_SERIES)[number]["key"];

export const WAITING = "Waiting for the first close after the decision.";

/** A cell: the signed change as shown (the header carries the unit), and the two observations behind it. */
export type ReactionCell =
  | { state: "move"; text: string; change: number; detail: string; surprise: boolean }
  | { state: "waiting" | "unavailable"; text: string; detail: string }
  | { state: "none" };

export type ReactionRow = {
  slug: string;
  href: string;
  date: string;
  bank: Bank;
  decision: string;
  cells: Record<ReactionKey, ReactionCell>;
};

export function reactionLevel(spec: (typeof REACTION_SERIES)[number], value: number): string {
  if (spec.kind === "yield") return formatYield(value);
  if (spec.kind === "fx") return formatUsdInr(value);
  return formatIndexLevel(value, spec.market);
}

export function reactionCell(spec: (typeof REACTION_SERIES)[number], bank: Bank, shown: boolean, r: MarketReactionRow | undefined): ReactionCell {
  if (!(spec.banks as readonly Bank[]).includes(bank)) return { state: "none" };
  if (!shown) return { state: "unavailable", text: NOT_AVAILABLE, detail: `${NOT_AVAILABLE_LABEL}.` };
  if (!r) return { state: "waiting", text: NOT_AVAILABLE, detail: WAITING };
  const text = formatAxisChange(r.change, r.change_unit === "bps" ? 0 : 2);
  const detail = `${formatTableDate(r.pre_obs_date)} ${reactionLevel(spec, r.pre_value)} → ${formatTableDate(r.post_obs_date)} ${reactionLevel(spec, r.post_value)}`;
  return { state: "move", text, change: r.change, detail, surprise: r.is_surprise_measure };
}

/**
 * One row per decision the history table lists (newest first), with its reactions. `series` is what api.series
 * returned for the reaction series: a series missing from it isn't shown.
 */
export function reactionRows(
  decisions: readonly HistoryRow[],
  reactions: readonly MarketReactionRow[],
  series: readonly SeriesRow[],
): ReactionRow[] {
  const shown = new Set(series.map((s) => s.key));
  const bySlug = new Map<string, Map<string, MarketReactionRow>>();
  for (const r of reactions) {
    const m = bySlug.get(r.meeting_slug) ?? new Map<string, MarketReactionRow>();
    m.set(r.series_key, r);
    bySlug.set(r.meeting_slug, m);
  }
  return decisions.map((d) => {
    const got = bySlug.get(d.slug);
    const cells = Object.fromEntries(REACTION_SERIES.map((s) => [s.key, reactionCell(s, d.bank, shown.has(s.key), got?.get(s.key))])) as Record<ReactionKey, ReactionCell>;
    return { slug: d.slug, href: d.href, date: d.date, bank: d.bank, decision: d.decision, cells };
  });
}

/** The source and copyright lines of the shown reaction series, in table order (SourceLine joins and dedupes). */
export function reactionSources(series: readonly SeriesRow[]): (string | null)[] {
  return REACTION_SERIES.map((s) => series.find((x) => x.key === s.key)?.attribution ?? null);
}
