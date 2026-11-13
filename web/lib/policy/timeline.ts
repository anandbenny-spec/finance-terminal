// web/lib/policy/timeline.ts: what "How the wording moved" shows on /policy (#509; Design 01 §1.16, Pass 2). Pure: the
// decisions, the decision documents and the stored diffs' pairs in, one bank's rows × meetings grid of cells out.
//
// - Hidden until Anand inserts a `site.language_timeline` row in core.field_policy with status `shown` or `labelled`
//   (the Policy Day pattern, lib/policy/policyday.ts): no row means hidden, and the page then reads nothing for it.
// - One row is one key phrase (lib/policy/timeline-rows.ts), one column one held rate decision, newest first. A cell is
//   the bank's own sentence for that phrase, as the stored diff (core.document_diff.segments, version 2) holds it: the
//   "Now" side of the pair, so its words are the meeting's own with the ones that are new since the previous meeting
//   marked (lib/policy/keychanges.ts marks the same words). Nothing is re-diffed here and no AI is involved.
// - A cell is a sentence only when exactly one sentence of the right topic matches the row's rule. None, or more than
//   one, is a gap that says so: a row never guesses (the accuracy rule, CLAUDE.md §1). So is a meeting with no decision
//   document, no stored diff (the first of a series, or the document type changed) or a diff this code can't read.
// - Only the cells leave this module's reader (lib/queries/timeline.ts caches them); the pairs, tens of kilobytes
//   a meeting, are dropped the moment the cells are cut from them.
import { meetingTabHref } from "@/lib/policy/decisions";
import { latestBySlug, type StripDocument } from "@/lib/policy/changestrip";
import { parsePairs, type Op, type RedlinePair } from "@/lib/policy/redline";
import type { FieldPolicyRow, PolicyMeetingRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

/** The field policy that shows the section. No row means hidden: the database fails closed, and so does the page. */
export const LANGUAGE_TIMELINE_FIELD = "site.language_timeline";

/** Whether the section is published: a row for the field with status `shown` or `labelled`. */
export function languageTimelineShown(policies: readonly FieldPolicyRow[] | null): boolean {
  const row = policies?.find((p) => p.field_key === LANGUAGE_TIMELINE_FIELD) ?? null;
  return row !== null && (row.status === "shown" || row.status === "labelled");
}

// ------------------------------------------------------------------------------------------------ the rules

/** One way of finding a row's sentence: its pair topic, the patterns it must hold and the patterns it must not. */
export type RowTier = {
  topic: string;
  all: readonly RegExp[];
  none?: readonly RegExp[];
};

/** A row: tiers are tried in order, and the first that matches any sentence decides the cell. */
export type RowDef = { key: string; label: string; tiers: readonly RowTier[] };

// ------------------------------------------------------------------------------------------------ the cells

/** Why a cell holds no sentence. `none` and `several` are the row's own rule; the rest are about the meeting. */
export type GapReason = "none" | "several" | "no_document" | "no_diff" | "unreadable";

export type SentenceCell = {
  kind: "sentence";
  /** The sentence's words as the meeting's document holds them: `eq` words, and `ins` words that are new since the previous meeting. */
  parts: Op[];
  /** `same`: nothing changed. `changed`: some words are new. `added`: the stored diff found no counterpart before it. */
  how: "same" | "changed" | "added";
  /** The paragraph of the meeting's document the sentence sits in (the ¶ link), or null. */
  para: number | null;
};

export type GapCell = { kind: "gap"; reason: GapReason; matched: number };

export type TimelineCell = SentenceCell | GapCell;

/** A cell's sentence as plain text. */
export function sentenceText(cell: SentenceCell): string {
  return cell.parts.map((p) => p.text).join("");
}

/** What a gap cell says: its headline and the reason beneath it. */
export function gapNote(cell: GapCell): { title: string; reason: string } {
  switch (cell.reason) {
    case "none":
      return { title: "No single matching sentence", reason: "None of this meeting's sentences matches." };
    case "several":
      return { title: "No single matching sentence", reason: `${cell.matched} sentences match, so none is chosen.` };
    case "no_document":
      return { title: "Not shown", reason: "No decision document is stored for this meeting." };
    case "no_diff":
      return { title: "Not shown", reason: "There is no earlier document to compare it with." };
    case "unreadable":
      return { title: "Not shown", reason: "The stored comparison couldn't be read." };
  }
}

/** The words a pair holds for the meeting it comes from (its "Now" side); null for a sentence the meeting dropped. */
function nowSide(pair: RedlinePair): { parts: Op[]; how: SentenceCell["how"] } | null {
  switch (pair.kind) {
    case "removed":
      return null;
    case "added":
      return { parts: [{ op: "ins", text: pair.text }], how: "added" };
    case "same":
      return { parts: [{ op: "eq", text: pair.text }], how: "same" };
    case "changed": {
      const parts = pair.ops.filter((o) => o.op !== "del");
      return { parts, how: parts.some((o) => o.op === "ins") ? "changed" : "same" };
    }
  }
}

function textOf(parts: readonly Op[]): string {
  return parts.map((p) => p.text).join("");
}

/** One row's cell for one meeting's pairs. */
export function cellFor(row: RowDef, pairs: readonly RedlinePair[]): TimelineCell {
  const now = pairs.flatMap((pair) => {
    const side = nowSide(pair);
    return side ? [{ pair, ...side, text: textOf(side.parts) }] : [];
  });
  for (const tier of row.tiers) {
    const found = now.filter(
      (s) => s.pair.topic === tier.topic && tier.all.every((rx) => rx.test(s.text)) && !(tier.none ?? []).some((rx) => rx.test(s.text)),
    );
    if (found.length === 1) return { kind: "sentence", parts: found[0].parts, how: found[0].how, para: found[0].pair.newPara };
    if (found.length > 1) return { kind: "gap", reason: "several", matched: found.length };
  }
  return { kind: "gap", reason: "none", matched: 0 };
}

/** Every row's cell for one meeting, keyed by the row. */
export function cellsFor(rows: readonly RowDef[], pairs: readonly RedlinePair[]): Record<string, TimelineCell> {
  return Object.fromEntries(rows.map((row) => [row.key, cellFor(row, pairs)]));
}

function allGap(rows: readonly RowDef[], reason: GapReason): Record<string, TimelineCell> {
  return Object.fromEntries(rows.map((row) => [row.key, { kind: "gap", reason, matched: 0 } satisfies GapCell]));
}

// ------------------------------------------------------------------------------------------------ the grid

/** What the read returns of a diff: its version and its pairs (`segments->version`, `segments->pairs`). */
export type DiffPairs = { document_id: number; base_document_id: number; version: unknown; pairs: unknown };

export type TimelineColumn = {
  slug: string;
  /** The decision date, YYYY-MM-DD. */
  date: string;
  /** The meeting's Statement tab: a ¶n link is this path with `#p-n`. */
  href: string;
  cells: Record<string, TimelineCell>;
};

export type BankTimeline = {
  bank: Bank;
  rows: { key: string; label: string }[];
  /** Newest first. */
  columns: TimelineColumn[];
  /** The newest meeting's date: the data-to of the panel's freshness. */
  latest: string | null;
};

/**
 * One bank's grid. `decisions` are the held rate decisions the History table lists (so the grid and the table name the
 * same meetings), `documents` the decision documents (every version; the highest wins, as ChangeStrip's does) and
 * `diffs` the stored diffs' pairs.
 */
export function buildBankTimeline(
  bank: Bank,
  rows: readonly RowDef[],
  decisions: readonly Pick<PolicyMeetingRow, "slug" | "bank" | "decision_date">[],
  documents: readonly StripDocument[],
  diffs: readonly DiffPairs[],
): BankTimeline {
  const doc = latestBySlug(documents);
  const diffOf = new Map(diffs.map((d) => [d.document_id, d]));
  const own = decisions
    .filter((d) => d.bank === bank)
    .sort((a, b) => b.decision_date.localeCompare(a.decision_date) || b.slug.localeCompare(a.slug));
  const columns = own.map((m): TimelineColumn => {
    const d = doc.get(m.slug);
    const diff = d ? diffOf.get(d.id) : undefined;
    const pairs = diff ? parsePairs({ version: diff.version, pairs: diff.pairs }) : null;
    const cells = !d ? allGap(rows, "no_document") : !diff ? allGap(rows, "no_diff") : !pairs ? allGap(rows, "unreadable") : cellsFor(rows, pairs);
    return { slug: m.slug, date: m.decision_date, href: meetingTabHref(bank, m.decision_date, "statement"), cells };
  });
  return { bank, rows: rows.map((r) => ({ key: r.key, label: r.label })), columns, latest: columns[0]?.date ?? null };
}
