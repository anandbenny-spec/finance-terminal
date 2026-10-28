// web/lib/policy/decisions.ts: what the policy pages say about a decision, in words and numbers (Spec 01 §4.2–§4.3;
// Design 01 §4.5). Pure: rows the database returned in, text out, every number through lib/format.ts.
//
// - DecisionLine: "Cut 25 bps to 5.25%", "Hike 25 bps to 5.50%", "Held at 4.00–4.25%". Words only, never a colour
//   or a triangle: a policy decision isn't a market move (Design 01 §1.2).
// - Tally lines (Design 01 §1.14, §4.5; F23): Fed "Vote 11–1 · Dissent: name (preferred −50 bps)"; RBI
//   "Rate 4–2 · Stance 5–1", the rate and the stance counted separately. A tally is shown only when every voter's
//   row is there; otherwise the page shows Spec 01 §4.5's copy (RBI) or nothing (absent, not guessed).
// - Nothing is inferred that the rows don't state: a stance tally needs every member's stance vote, and the adopted
//   stance is the one a strict majority of them voted for (the MPC decides by majority).
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";
import {
  NOT_AVAILABLE,
  formatAxisChange,
  formatAxisNumber,
  formatAxisRange,
  formatBps,
  formatBpsChange,
  formatDate,
  formatRate,
  formatRateRange,
  formatTally,
} from "@/lib/format";

export type Direction = "cut" | "hold" | "hike";

/** The MPC has six members (RBI Act, s.45ZB); every 2019→ meeting had six (B6, #15). */
export const MPC_MEMBERS = 6;

/** A decision's direction from its change in bps, or null when the change isn't known. */
export function direction(changeBps: number | null): Direction | null {
  if (changeBps === null) return null;
  return changeBps < 0 ? "cut" : changeBps > 0 ? "hike" : "hold";
}

const WORD: Record<Direction, string> = { cut: "Cut", hold: "Hold", hike: "Hike" };

/** The decision word for tables: "Cut", "Hold", "Hike"; "—" when the change isn't known. */
export function decisionWord(changeBps: number | null): string {
  const d = direction(changeBps);
  return d ? WORD[d] : NOT_AVAILABLE;
}

/** The policy rate after a decision: the Fed's target range (both bounds: F22), the RBI's repo rate. */
export function rateAfter(m: Pick<PolicyMeetingRow, "bank" | "policy_rate_pct" | "fed_target_lower_pct" | "fed_target_upper_pct">): string {
  return m.bank === "Fed" ? formatRateRange(m.fed_target_lower_pct, m.fed_target_upper_pct) : formatRate(m.policy_rate_pct);
}

/** DecisionLine's text: "Cut 25 bps to 5.25%", "Held at 4.00–4.25%"; null when the rate or its change is missing. */
export function decisionLine(m: PolicyMeetingRow): string | null {
  const level = rateAfter(m);
  const d = direction(m.policy_rate_change_bps);
  if (d === null || level === NOT_AVAILABLE) return null;
  if (d === "hold") return `Held at ${level}`;
  return `${WORD[d]} ${formatBps(Math.abs(m.policy_rate_change_bps!))} to ${level}`;
}

/** A change in words with its date, for a stat's context line: "Cut 25 bps on 17 Sep 2026", "Held on 7 Oct 2026". */
export function changeInWords(m: PolicyMeetingRow): string | null {
  const d = direction(m.policy_rate_change_bps);
  if (d === null) return null;
  const on = `on ${formatDate(m.decision_date)}`;
  return d === "hold" ? `Held ${on}` : `${WORD[d]} ${formatBps(Math.abs(m.policy_rate_change_bps!))} ${on}`;
}

/** The most recent decision that moved the rate, from decisions sorted oldest first; null if none moved it. */
export function lastMove(decisions: readonly PolicyMeetingRow[]): PolicyMeetingRow | null {
  for (let i = decisions.length - 1; i >= 0; i--) {
    const c = decisions[i].policy_rate_change_bps;
    if (c !== null && c !== 0) return decisions[i];
  }
  return null;
}

/** A stance label as a sentence starts: "neutral" → "Neutral" (the MPC's own phrase otherwise kept as is). */
export function stanceText(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** The meeting page's path: /policy/fed/2026-09-17, /policy/rbi/2026-10-07 (C4's routes). */
export function meetingHref(bank: Bank, decisionDate: string): string {
  return `/policy/${bank === "Fed" ? "fed" : "rbi"}/${decisionDate}`;
}

// ------------------------------------------------------------------ Meeting page tabs (the tab is in the URL)

/** The meeting page's tabs, in order (Design 01 §5.3). Moved here from MeetingView in #147: the /latest routes and
 * PolicyCard are its second and third users, and neither should import MeetingView's whole page to read a tab. */
export const TABS = [
  { id: "overview", label: "Overview" },
  { id: "changes", label: "What changed" },
  { id: "statement", label: "Statement" },
  { id: "minutes", label: "Minutes" },
  { id: "press", label: "Press conference" },
  { id: "reaction", label: "Market reaction" },
] as const;
export type TabId = (typeof TABS)[number]["id"];

/** The tab a `?tab=` value names; anything else (or nothing) is the Overview. */
export function tabOf(value: string | string[] | undefined): TabId {
  const v = Array.isArray(value) ? value[0] : value;
  return TABS.some((t) => t.id === v) ? (v as TabId) : "overview";
}

/**
 * A meeting page's link on a given tab: /policy/fed/2026-09-17 for the Overview, ?tab=changes for the rest. `extra` is
 * one more query parameter ("doc=governor"). Previous / Next and the Latest decisions cards use it so a reader
 * stays on the tab they were reading (#147).
 */
export function meetingTabHref(bank: Bank, decisionDate: string, tab: TabId, extra = ""): string {
  const base = meetingHref(bank, decisionDate);
  if (tab === "overview") return extra ? `${base}?${extra}` : base;
  return `${base}?tab=${tab}${extra ? `&${extra}` : ""}`;
}

// ------------------------------------------------------------------ Tallies

export type Tally =
  | { kind: "tally"; line: string; short: string; dissents: string[] }
  /** RBI: not every member's vote is stored yet (D4: the minutes are the record); show §4.5's copy. */
  | { kind: "pending"; note: string }
  /** No rows, or rows that don't add up: say nothing rather than guess. */
  | { kind: "absent" };

function preferred(bps: number): string {
  return bps === 0 ? "preferred to hold" : `preferred ${formatBpsChange(bps)}`;
}

function fedTally(m: PolicyMeetingRow, votes: readonly PolicyVoteRow[]): Tally {
  if (votes.length === 0) return { kind: "absent" };
  const against = votes.filter((v) => v.is_dissent);
  const tally = formatTally(votes.length - against.length, against.length);
  const dissents = against.map(
    (v) => `${v.member_name} (${v.voted_change_bps === null ? "dissented, not on the rate" : preferred(v.voted_change_bps)})`,
  );
  const line = `Vote ${tally}${dissents.length ? ` · Dissent: ${dissents.join(", ")}` : ""}`;
  return { kind: "tally", line, short: tally, dissents };
}

/** The stance a strict majority of the members voted for, when every member's stance vote is stored. */
function adoptedStance(votes: readonly PolicyVoteRow[]): string | null {
  if (votes.some((v) => v.stance_vote === null)) return null;
  const counts = new Map<string, number>();
  for (const v of votes) counts.set(v.stance_vote!, (counts.get(v.stance_vote!) ?? 0) + 1);
  for (const [label, n] of counts) if (label !== "against" && n * 2 > votes.length) return label;
  return null;
}

function minutesNote(m: PolicyMeetingRow): string {
  // Spec 01 §4.5, "RBI votes not yet assignable (D4)".
  return m.minutes_due_on
    ? `Each member's vote is published in the minutes, due ${formatDate(m.minutes_due_on)}.`
    : "Each member's vote is published in the minutes.";
}

function rbiTally(m: PolicyMeetingRow, votes: readonly PolicyVoteRow[]): Tally {
  const decision = m.policy_rate_change_bps;
  if (votes.length < MPC_MEMBERS || votes.some((v) => v.voted_change_bps === null)) {
    return { kind: "pending", note: minutesNote(m) };
  }
  if (votes.length !== MPC_MEMBERS || decision === null) return { kind: "absent" };
  const rateFor = votes.filter((v) => v.voted_change_bps === decision).length;
  const stance = adoptedStance(votes);
  const stanceFor = stance === null ? null : votes.filter((v) => v.stance_vote === stance).length;
  const rate = formatTally(rateFor, MPC_MEMBERS - rateFor);
  const stancePart = stanceFor === null ? null : formatTally(stanceFor, MPC_MEMBERS - stanceFor);
  const dissents: string[] = [];
  for (const v of votes) {
    const why: string[] = [];
    if (v.voted_change_bps !== decision) why.push(preferred(v.voted_change_bps!));
    // A stance dissent: a stance vote other than the adopted one; with the adopted stance unknown (not every
    // member's stance vote is stored), only a stored dissent whose rate vote matched the decision is one.
    const stanceDissent =
      v.stance_vote !== null &&
      (stance !== null ? v.stance_vote !== stance : v.is_dissent && v.voted_change_bps === decision);
    if (stanceDissent) why.push(v.stance_vote === "against" ? "voted against the stance" : `stance: voted ${v.stance_vote}`);
    if (why.length || v.is_dissent) dissents.push(`${v.member_name} (${why.join("; ") || "dissented"})`);
  }
  const line = `Rate ${rate}${stancePart ? ` · Stance ${stancePart}` : ""}${dissents.length ? ` · Dissent: ${dissents.join(", ")}` : ""}`;
  const short = stancePart ? `${rate} · stance ${stancePart}` : rate;
  return { kind: "tally", line, short, dissents };
}

/** The meeting's tally line from its votes (only that meeting's rows). */
export function tally(m: PolicyMeetingRow, votes: readonly PolicyVoteRow[]): Tally {
  const own = votes.filter((v) => v.meeting_slug === m.slug);
  return m.bank === "Fed" ? fedTally(m, own) : rbiTally(m, own);
}

// ------------------------------------------------------------------ History table rows

const KIND: Record<string, string> = { scheduled: "Scheduled", unscheduled: "Unscheduled", special: "Special" };

export type HistoryRow = {
  slug: string;
  href: string;
  date: string; // YYYY-MM-DD, for sorting and the year filter
  year: string;
  bank: Bank;
  kind: string;
  direction: Direction | null;
  decision: string;
  rateAfter: string; // "4.00–4.25", "5.50": the column header carries the unit
  change: string; // "−25", "+25", "0": the header carries "bps"
  changeBps: number | null;
  vote: string;
  voteNote: string | null; // the tooltip when the vote isn't shown (§4.5 copy)
  dissents: number;
  stance: string | null; // RBI only: the AI-extracted stance, as the view's field policy allows it
};

/** One row per decision, newest first, for the history table (Spec 01 §4.2, "Every decision since 2019"). */
export function historyRows(decisions: readonly PolicyMeetingRow[], votes: readonly PolicyVoteRow[]): HistoryRow[] {
  const bySlug = new Map<string, PolicyVoteRow[]>();
  for (const v of votes) bySlug.set(v.meeting_slug, [...(bySlug.get(v.meeting_slug) ?? []), v]);
  return decisions
    .map((m): HistoryRow => {
      const bank = m.bank as Bank;
      const t = tally(m, bySlug.get(m.slug) ?? []);
      return {
        slug: m.slug,
        href: meetingHref(bank, m.decision_date),
        date: m.decision_date,
        year: m.decision_date.slice(0, 4),
        bank,
        kind: KIND[m.kind] ?? m.kind,
        direction: direction(m.policy_rate_change_bps),
        decision: decisionWord(m.policy_rate_change_bps),
        rateAfter: bank === "Fed" ? formatAxisRange(m.fed_target_lower_pct, m.fed_target_upper_pct) : formatAxisNumber(m.policy_rate_pct),
        change: formatAxisChange(m.policy_rate_change_bps),
        changeBps: m.policy_rate_change_bps,
        vote: t.kind === "tally" ? t.short : NOT_AVAILABLE,
        voteNote: t.kind === "pending" ? t.note : null,
        dissents: m.dissent_count,
        stance: bank === "RBI" && m.stance ? stanceText(m.stance) : null,
      };
    })
    .sort((a, b) => b.date.localeCompare(a.date) || b.slug.localeCompare(a.slug));
}
