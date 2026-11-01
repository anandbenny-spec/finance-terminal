// web/lib/equity/rate.ts: the rate-decision views' reading rules (2A-25; Spec 02A §5.10, §8). Pure. Nothing here
// computes a statistic: the median, mean, quartiles and share positive come from `api.rate_decision_stats`, and a
// move is a row `api.rate_decision_moves` returned. This file only picks which stored rows a panel shows (a window,
// a bank, a decision class), tells a group with fewer than five moves from one with statistics (the view leaves the
// statistics null below five), and orders rows.
import type { CompanyRateCycleRow, RateCycleRow, RateDecisionMoveRow, RateDecisionStatRow } from "@/lib/queries/api";

/** The field policy that gates every figure on these views (hidden until 2A-26's checks and Anand's look). */
export const RATE_GATE = "equity.rate_sensitivity";

/** The line under every rate-sensitivity panel (Spec 02A §5.10). */
export const NOT_A_FORECAST = "Moves on decision days, not a forecast.";

/** Below this many moves a group shows its moves, not statistics (Spec 02A §5.10). The view enforces it; this only labels it. */
export const MIN_MOVES = 5;

export type WindowKey = "d1" | "d5";
export type Bank = "rbi" | "fed";
export type Decision = "cut" | "hold" | "hike";

export const WINDOWS: readonly { id: WindowKey; label: string; note: string }[] = [
  { id: "d1", label: "1 day", note: "From the last close before the decision to the first close after it." },
  { id: "d5", label: "5 days", note: "From the last close before the decision to the fifth close after it." },
];

/** The window in the URL (?window=…); one day unless five is asked for. */
export function windowOf(value: string | string[] | undefined): WindowKey {
  return value === "d5" ? "d5" : "d1";
}

/** India first, as module 1 shows it; the RBI is named, never linked (CLAUDE.md §1). */
export const BANKS: readonly { id: Bank; label: string; market: "IN" | "US" }[] = [
  { id: "rbi", label: "RBI (MPC)", market: "IN" },
  { id: "fed", label: "Fed (FOMC)", market: "US" },
];

export const DECISIONS: readonly { id: Decision; label: string }[] = [
  { id: "cut", label: "Cut" },
  { id: "hold", label: "Hold" },
  { id: "hike", label: "Hike" },
];

export const bankLabel = (bank: Bank): string => BANKS.find((b) => b.id === bank)?.label ?? bank;
export const decisionLabel = (d: Decision): string => DECISIONS.find((x) => x.id === d)?.label ?? d;

const bankOrder = (b: Bank): number => BANKS.findIndex((x) => x.id === b);
const decisionOrder = (d: Decision): number => DECISIONS.findIndex((x) => x.id === d);

/** One bank × decision cell of a table: the stored statistics, or the moves when there are too few to summarise. */
export type StatRow = {
  key: string;
  subjectKey: string;
  subjectName: string;
  bank: Bank;
  decision: Decision;
  n: number;
  /** True when n < 5: the statistics are null and `moves` lists the decisions. */
  fewMoves: boolean;
  stat: RateDecisionStatRow;
};

/** The statistics rows of one window, ordered bank then decision then subject. Rows with no count (held back) are dropped. */
export function statRows(stats: readonly RateDecisionStatRow[], window: WindowKey): StatRow[] {
  return stats
    .filter((s) => s.window_key === window && s.n !== null)
    .map((s) => ({
      key: `${s.subject_kind}:${s.subject_key}:${s.bank}:${s.decision}:${s.window_key}`,
      subjectKey: s.subject_key,
      subjectName: s.subject_name,
      bank: s.bank,
      decision: s.decision,
      n: s.n as number,
      fewMoves: (s.n as number) < MIN_MOVES || s.median_excess_pct === null,
      stat: s,
    }))
    .sort((a, b) => bankOrder(a.bank) - bankOrder(b.bank) || decisionOrder(a.decision) - decisionOrder(b.decision) || a.subjectName.localeCompare(b.subjectName));
}

/** The individual moves of one bank × decision × window, newest first (the list shown when n < 5). */
export function movesOf(moves: readonly RateDecisionMoveRow[], bank: Bank, decision: Decision, window: WindowKey): RateDecisionMoveRow[] {
  return moves
    .filter((m) => m.bank === bank && m.decision === decision && m.window_key === window && m.excess_pct !== null)
    .sort((a, b) => (a.meeting_date < b.meeting_date ? 1 : a.meeting_date > b.meeting_date ? -1 : 0));
}

/** A rate cycle with its decisions' dates (from `api.rate_cycles`) and the company's move over it. */
export type CycleRow = {
  key: string;
  bank: Bank;
  direction: "easing" | "tightening";
  firstOn: string | null;
  lastOn: string | null;
  moves: number | null;
  ongoing: boolean;
  move: CompanyRateCycleRow;
};

/** The company's cycles, ordered India first and oldest first within a bank. Cycles with no stored move are dropped. */
export function cycleRows(moves: readonly CompanyRateCycleRow[], cycles: readonly RateCycleRow[]): CycleRow[] {
  const info = new Map(cycles.map((c) => [`${c.bank}:${c.cycle_slug}`, c]));
  return moves
    .filter((m) => m.direction !== null && m.own_change_pct !== null)
    .map((m) => {
      const c = info.get(`${m.bank}:${m.cycle_slug}`);
      return {
        key: `${m.bank}:${m.cycle_slug}`,
        bank: m.bank,
        direction: m.direction as "easing" | "tightening",
        firstOn: c?.first_decision_on ?? null,
        lastOn: c?.last_decision_on ?? null,
        moves: c?.moves ?? null,
        ongoing: m.is_ongoing === true,
        move: m,
      };
    })
    .sort((a, b) => bankOrder(a.bank) - bankOrder(b.bank) || (a.firstOn ?? a.move.pre_date ?? "").localeCompare(b.firstOn ?? b.move.pre_date ?? ""));
}

export const DIRECTION_LABEL = { easing: "Easing", tightening: "Tightening" } as const;

/** The Nifty 50 series every excess move is measured against (its own attribution is read from `api.series`). */
export const NIFTY_SERIES = "yf.^NSEI";

/** The decisions' sources, named and never linked for the RBI (CLAUDE.md §1). */
export const DECISION_SOURCES = ["Source: Federal Reserve Board", "Source: Reserve Bank of India"] as const;

/** The footer's source lines: NSE for the stocks, the Nifty 50 series' own line when it was read, then the decisions'. */
export function rateSources(niftyAttribution: string | null | undefined): string[] {
  return ["Source: NSE", ...(niftyAttribution ? [niftyAttribution] : []), ...DECISION_SOURCES];
}
