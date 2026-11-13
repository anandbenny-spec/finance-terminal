// web/lib/policy/votes-over-time.ts: what "How members voted over time" shows on /policy (#508; Design 01 §9, Pass 2).
// Pure: the held rate decisions, their votes and the field-policy rows in, one bank's strips and member grid out.
//
// - Hidden until Anand inserts a `site.votes_over_time` row in core.field_policy with status `shown` or `labelled`
//   (the Policy Day pattern, lib/policy/policyday.ts): no row means hidden, and the page then reads nothing for it.
// - One strip per held rate decision, newest first. A strip is exactly what `dotPlot` (votedots.ts) draws for that
//   meeting, so the positions, the stacking and the hollow dots are the meeting page's. The bank's strips share one
//   axis (the widest vote shown, ticks every 25 bps, at least ±50).
// - A meeting whose per-member votes aren't all stored (the RBI's before its minutes; a Fed tally statement) shows
//   the tally's own words and no dots. It never borrows an older meeting's votes.
// - The member × meeting grid takes its cells from `voteRows` (the meeting page's table), so the wording is the same.
import { dissentLine, meetingHref, tally } from "@/lib/policy/decisions";
import { voteRows, votesAbsentNote, type VoteRow } from "@/lib/policy/meeting";
import { axisFor, dotPlot, type Dot } from "@/lib/policy/votedots";
import type { FieldPolicyRow, PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

/** The field policy that shows the section. No row means hidden: the database fails closed, and so does the page. */
export const VOTES_OVER_TIME_FIELD = "site.votes_over_time";

/** Whether the section is published: a row for the field with status `shown` or `labelled`. */
export function votesOverTimeShown(policies: readonly FieldPolicyRow[] | null): boolean {
  const row = policies?.find((p) => p.field_key === VOTES_OVER_TIME_FIELD) ?? null;
  return row !== null && (row.status === "shown" || row.status === "labelled");
}

export type StripRow =
  | { kind: "dots"; slug: string; href: string; date: string; decision: number; dots: Dot[]; maxLevel: number; summary: string }
  /** No dots: the tally's words, or why the votes aren't shown (the §4.5 copy). */
  | { kind: "note"; slug: string; href: string; date: string; decision: number | null; text: string };

export type GridMember = { key: string; name: string };
export type GridMeeting = { slug: string; href: string; date: string };

export type BankVotes = {
  bank: Bank;
  /** Newest first. */
  rows: StripRow[];
  axis: { min: number; max: number; ticks: number[] };
  members: GridMember[];
  /** Newest first: the grid's columns. Only meetings with at least one stored vote. */
  meetings: GridMeeting[];
  /** meeting slug → member key → that member's vote at the meeting, as the meeting page words it. */
  cells: Map<string, Map<string, VoteRow>>;
  /** The RBI's grid carries the stance vote beside the rate vote. */
  stance: boolean;
  /** The latest decision date shown, for the panel's "Data to". */
  latest: string | null;
};

function note(m: PolicyMeetingRow, votes: readonly PolicyVoteRow[]): StripRow {
  const base = { slug: m.slug, href: meetingHref(m.bank as Bank, m.decision_date), date: m.decision_date, decision: m.policy_rate_change_bps };
  const t = tally(m, votes);
  if (t.kind === "pending") {
    return { kind: "note", ...base, text: t.dissents?.length ? `${dissentLine(t.dissents)}. ${t.note}` : t.note };
  }
  if (t.kind === "tally") return { kind: "note", ...base, text: t.line };
  return { kind: "note", ...base, text: votesAbsentNote(m) };
}

/** One bank's strips and grid from every held rate decision and every stored vote. Nothing is guessed or filled in. */
export function votesOverTime(bank: Bank, decisions: readonly PolicyMeetingRow[], votes: readonly PolicyVoteRow[]): BankVotes {
  const bySlug = new Map<string, PolicyVoteRow[]>();
  for (const v of votes) bySlug.set(v.meeting_slug, [...(bySlug.get(v.meeting_slug) ?? []), v]);
  const held = decisions
    .filter((m) => m.bank === bank && m.status === "held" && m.is_rate_decision)
    .sort((a, b) => b.decision_date.localeCompare(a.decision_date) || b.slug.localeCompare(a.slug));

  const rows: StripRow[] = [];
  const meetings: GridMeeting[] = [];
  const cells = new Map<string, Map<string, VoteRow>>();
  const people = new Map<string, string>();
  const reach: number[] = [];

  for (const m of held) {
    const own = bySlug.get(m.slug) ?? [];
    const href = meetingHref(bank, m.decision_date);
    const plot = tally(m, own).kind === "tally" ? dotPlot(m, own) : null;
    if (plot) {
      rows.push({ kind: "dots", slug: m.slug, href, date: m.decision_date, decision: plot.decision, dots: plot.dots, maxLevel: plot.maxLevel, summary: plot.summary });
      reach.push(plot.decision, ...plot.dots.map((d) => d.at));
    } else {
      rows.push(note(m, own));
    }
    if (own.length > 0) {
      meetings.push({ slug: m.slug, href, date: m.decision_date });
      cells.set(m.slug, new Map(voteRows(m, own).map((r) => [r.key, r])));
      for (const v of own) people.set(v.member_slug, v.member_name);
    }
  }

  return {
    bank,
    rows,
    axis: axisFor(reach),
    members: [...people].map(([key, name]) => ({ key, name })).sort((a, b) => a.name.localeCompare(b.name)),
    meetings,
    cells,
    stance: bank === "RBI",
    latest: held.at(0)?.decision_date ?? null,
  };
}

/**
 * How a dot looks while a member is picked out (hover, focus or the Member select): that member's dots are ringed in
 * the focus colour and every other dot is dimmed. With nobody picked out, every dot is plain.
 */
export function dotEmphasis(active: string | null, key: string): "plain" | "mine" | "dim" {
  if (active === null) return "plain";
  return active === key ? "mine" : "dim";
}
