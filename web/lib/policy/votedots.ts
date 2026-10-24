// web/lib/policy/votedots.ts: what VoteDotPlot draws (Design 01 §1.14, §4.5; F23). Pure: one meeting's vote rows in,
// the axis, the dots and the summary line out; every number through lib/format.ts.
//
// - The axis is continuous in bps, ticks every 25, symmetric and at least ±50, widening to fit (the Fed's −100 in
//   March 2020). A dot sits at the member's exact vote; dots at the same value stack upward, one per member.
// - A dissent that isn't about the rate (a Fed guidance dissent; an RBI member who voted with the rate decision but
//   against the stance) is a hollow dot at the decision. Shape, not colour. A rate dissent is solid at its own value.
// - Nothing is guessed: a vote without a rate value and without a stored dissent has no place on the axis, and the
//   chart then stays out (the page shows the tally and the table).
import { formatBps, formatBpsChange, formatCount } from "@/lib/format";
import { MPC_MEMBERS, direction } from "@/lib/policy/decisions";
import { documentHref } from "@/lib/policy/ai";
import { voteRows } from "@/lib/policy/meeting";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";

export const TICK_BPS = 25;
export const MIN_HALF_RANGE_BPS = 50;

export type Dot = {
  key: string;
  member: string;
  /** Where it sits on the axis, in bps. */
  at: number;
  /** 0 for the first dot at this value, 1 for the next one up, … */
  level: number;
  hollow: boolean;
  /** The words under the member's name when the dot is opened. */
  lines: string[];
  /** "Open the cited passage": the document with the span, or null. */
  href: string | null;
  /** Dissenters are labelled by name when there are at most three. */
  dissent: boolean;
};

export type DotPlot = {
  min: number;
  max: number;
  ticks: number[];
  decision: number;
  maxLevel: number;
  dots: Dot[];
  summary: string;
};

/** "a 25 bps cut", "a 25 bps hike", "holding": a rate move in words, for the summary line. */
function moveWords(bps: number): string {
  const d = direction(bps);
  if (d === "hold") return "holding";
  return `a ${formatBps(Math.abs(bps))} ${d}`;
}

function stanceWords(v: PolicyVoteRow): string | null {
  if (v.stance_vote === null) return null;
  return v.stance_vote === "against" ? "voted against the stance" : `stance vote: ${v.stance_vote}`;
}

/** The axis for the given values and the decision: ticks every 25 bps, symmetric, at least ±50. */
export function axisFor(values: readonly number[]): { min: number; max: number; ticks: number[] } {
  const reach = Math.max(MIN_HALF_RANGE_BPS, ...values.map((v) => Math.abs(v)));
  const half = Math.ceil(reach / TICK_BPS) * TICK_BPS;
  const ticks: number[] = [];
  for (let t = -half; t <= half; t += TICK_BPS) ticks.push(t);
  return { min: -half, max: half, ticks };
}

/**
 * The plot for one held meeting, or null when it can't be drawn truthfully: no decision, no rows, a vote that is
 * neither on the rate nor a stored dissent, or an RBI meeting without all six members' votes (the page then shows
 * the tally only).
 */
export function dotPlot(m: PolicyMeetingRow, votes: readonly PolicyVoteRow[]): DotPlot | null {
  const decision = m.policy_rate_change_bps;
  const own = votes.filter((v) => v.meeting_slug === m.slug);
  if (decision === null || own.length === 0) return null;
  if (m.bank === "RBI" && (own.length < MPC_MEMBERS || own.some((v) => v.voted_change_bps === null))) return null;
  if (own.some((v) => v.voted_change_bps === null && !v.is_dissent)) return null;

  const words = new Map(voteRows(m, own).map((r) => [r.key, r.dissent]));
  const placed = own.map((v) => {
    const hollow = v.is_dissent && (v.voted_change_bps === null || v.voted_change_bps === decision);
    return { v, at: v.voted_change_bps ?? decision, hollow };
  });
  placed.sort((a, b) => a.at - b.at || Number(a.hollow) - Number(b.hollow) || a.v.member_name.localeCompare(b.v.member_name));

  const next = new Map<number, number>();
  const dots: Dot[] = placed.map(({ v, at, hollow }) => {
    const level = next.get(at) ?? 0;
    next.set(at, level + 1);
    const lines: string[] = [
      v.voted_change_bps === null
        ? "No rate vote recorded"
        : v.voted_change_bps === 0
          ? "Voted to hold"
          : `Voted ${formatBpsChange(v.voted_change_bps)}`,
    ];
    if (m.bank === "RBI") lines.push(stanceWords(v) ?? "No stance vote recorded");
    const why = words.get(v.member_slug);
    if (hollow) lines.push(why && why !== "dissented" ? `Dissent, not on the rate: ${why}` : "Dissent, not on the rate");
    else if (why) lines.push(`Dissent: ${why}`);
    else if (v.is_dissent) lines.push("Dissent");
    return {
      key: v.member_slug,
      member: v.member_name,
      at,
      level,
      hollow,
      lines,
      href: documentHref({ document_id: v.document_id ?? null, char_start: v.char_start ?? null, char_end: v.char_end ?? null }),
      dissent: v.is_dissent || (v.voted_change_bps !== null && v.voted_change_bps !== decision),
    };
  });

  const axis = axisFor([...placed.map((p) => p.at), decision]);
  return { ...axis, decision, maxLevel: Math.max(...dots.map((d) => d.level)), dots, summary: summaryLine(decision, own) };
}

/** "11 for a 25 bps cut · 1 preferred a 50 bps cut · 1 dissented, not on the rate". */
export function summaryLine(decision: number, votes: readonly PolicyVoteRow[]): string {
  let hollow = 0;
  const counts = new Map<number, number>();
  for (const v of votes) {
    if (v.voted_change_bps === null || (v.is_dissent && v.voted_change_bps === decision)) hollow++;
    else counts.set(v.voted_change_bps, (counts.get(v.voted_change_bps) ?? 0) + 1);
  }
  const parts = [`${formatCount(counts.get(decision) ?? 0)} for ${moveWords(decision)}`];
  for (const [bps, n] of [...counts].filter(([k]) => k !== decision).sort((a, b) => a[0] - b[0])) {
    parts.push(`${formatCount(n)} preferred ${bps === 0 ? "to hold" : moveWords(bps)}`);
  }
  if (hollow > 0) parts.push(`${formatCount(hollow)} dissented, not on the rate`);
  return parts.join(" · ");
}
