// web/lib/policy/reaction.ts: a meeting page's Market reaction tab (Spec 01 §4.3, §4.5; Pass 1b, #75). For one decision:
// the reaction table (each series' move from the last value fixed before the release to the first fixed after it, as
// policy.reactions stored it) and one chart per series around the decision, 10 sessions before the "before" value
// and 10 from the "after" value on, with the first value after the decision marked. Nothing is computed here but the
// window: every figure is a row api.market_reactions or api.observations returned, formatted by lib/format.ts.
import { formatTableDate } from "@/lib/format";
import { REACTION_SERIES, reactionCell, reactionLevel, type ReactionCell, type ReactionKey } from "@/lib/policy/reactions";
import type { MarketReactionRow, ObservationRow, SeriesRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";
import type { ChartEntity } from "@/lib/tokens";

/** Sessions shown on each side of the decision (Spec 01 §4.3). */
export const SESSIONS = 10;

/** Calendar days to read either side of a reaction's two dates: ten sessions plus weekends and holidays. */
export const READ_DAYS = 40;

/** A decision this old with no stored move isn't "waiting" any more: the figure is simply not available. */
const WAITING_DAYS = 10;

/** The Fed's surprise measure keeps its §4.3 heading. */
export const SURPRISE_TITLE = "US 2-year, 1-day change (surprise measure)";

export type ReactionPoint = { time: string; value: number };

export type ReactionChart = {
  data: ReactionPoint[];
  /** The first value after the decision, marked on the chart. */
  marker: { time: string; price: number };
  entity: ChartEntity;
  /** Table view rows: date, value as shown, and which side of the decision it sits on. */
  table: { date: string; value: string; side: "before" | "after" }[];
  /** Hover readout formatter input: date → value as shown. */
  shown: Record<string, string>;
};

export type ReactionEntry = {
  key: ReactionKey;
  header: string;
  title: string;
  cell: ReactionCell;
  /** Before and after, as shown ("17 Sep 2024 3.61%"), for a move. */
  before: string | null;
  after: string | null;
  chart: ReactionChart | null;
  /** Why there is no chart, for a move whose window couldn't be read. */
  chartNote: string | null;
};

export type MeetingReaction = { entries: ReactionEntry[]; sources: (string | null)[]; dataTo: string | null };

/** YYYY-MM-DD ± days, calendar arithmetic in UTC (the dates are plain dates). */
export function shiftDate(date: string, days: number): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

/** The dates to read a series' observations over: both reaction dates, widened by READ_DAYS. */
export function readRange(r: Pick<MarketReactionRow, "pre_obs_date" | "post_obs_date">): { from: string; to: string } {
  return { from: shiftDate(r.pre_obs_date, -READ_DAYS), to: shiftDate(r.post_obs_date, READ_DAYS) };
}

function entityOf(spec: (typeof REACTION_SERIES)[number], bank: Bank): ChartEntity {
  if (spec.key === "fred.DEXINUS") return bank === "Fed" ? "US" : "IN"; // a currency pair: coloured by the deciding bank
  return "market" in spec ? spec.market : "US"; // yields: the US series
}

/** The window of observations around a reaction, or null if the stored dates aren't among them. */
export function windowAround(obs: readonly ObservationRow[], pre: string, post: string): ObservationRow[] | null {
  const days = [...obs].sort((a, b) => a.obs_date.localeCompare(b.obs_date));
  const i = days.findIndex((o) => o.obs_date === pre);
  const j = days.findIndex((o) => o.obs_date === post);
  if (i < 0 || j < 0 || j <= i) return null;
  return days.slice(Math.max(0, i - SESSIONS + 1), j + SESSIONS);
}

function title(spec: (typeof REACTION_SERIES)[number], bank: Bank, name: string | undefined): string {
  if (bank === "Fed" && spec.key === "fred.DGS2") return SURPRISE_TITLE;
  return name ?? spec.header.replace(/ \(.*\)$/, "");
}

/**
 * The tab's model. `reactions` are this meeting's api.market_reactions rows, `series` what api.series returned for the
 * reaction series, `observations` each series' observations around its reaction (by series key). A series api.series
 * doesn't return is "Not available"; one with no stored move is "waiting" until the decision is a few days old.
 */
export function meetingReaction(input: {
  bank: Bank;
  decisionDate: string;
  today: string;
  reactions: readonly MarketReactionRow[];
  series: readonly SeriesRow[];
  observations: Readonly<Record<string, readonly ObservationRow[]>>;
}): MeetingReaction {
  const { bank, decisionDate, today, reactions, series, observations } = input;
  const shown = new Set(series.map((s) => s.key));
  const stale = shiftDate(decisionDate, WAITING_DAYS) < today;
  const entries: ReactionEntry[] = [];
  for (const spec of REACTION_SERIES) {
    if (!(spec.banks as readonly Bank[]).includes(bank)) continue;
    const r = reactions.find((x) => x.series_key === spec.key);
    let cell = reactionCell(spec, bank, shown.has(spec.key), r);
    // Past the waiting period a missing move is not available, not "waiting for the first close".
    if (cell.state === "waiting" && stale) cell = { state: "unavailable", text: cell.text, detail: "Not available." };
    const head = { key: spec.key, header: spec.header, title: title(spec, bank, r?.series_name), cell };
    if (!r || cell.state !== "move") {
      entries.push({ ...head, before: null, after: null, chart: null, chartNote: null });
      continue;
    }
    const before = `${formatTableDate(r.pre_obs_date)} ${reactionLevel(spec, r.pre_value)}`;
    const after = `${formatTableDate(r.post_obs_date)} ${reactionLevel(spec, r.post_value)}`;
    const win = windowAround(observations[spec.key] ?? [], r.pre_obs_date, r.post_obs_date);
    if (!win) {
      entries.push({ ...head, before, after, chart: null, chartNote: "The values around this decision couldn't be loaded." });
      continue;
    }
    const text = (v: number) => reactionLevel(spec, v);
    entries.push({
      ...head,
      before,
      after,
      chartNote: null,
      chart: {
        entity: entityOf(spec, bank),
        data: win.map((o) => ({ time: o.obs_date, value: o.value })),
        marker: { time: r.post_obs_date, price: r.post_value },
        table: win.map((o) => ({ date: o.obs_date, value: text(o.value), side: o.obs_date < r.post_obs_date ? "before" : "after" })),
        shown: Object.fromEntries(win.map((o) => [o.obs_date, text(o.value)])),
      },
    });
  }
  const posts = reactions.map((r) => r.post_obs_date).sort();
  return {
    entries,
    sources: REACTION_SERIES.map((s) => (shown.has(s.key) ? (series.find((x) => x.key === s.key)?.attribution ?? null) : null)),
    dataTo: posts.at(-1) ?? null,
  };
}
