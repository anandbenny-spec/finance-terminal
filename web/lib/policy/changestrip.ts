// web/lib/policy/changestrip.ts: the bars of ChangeStrip, "How much each statement changed" (Design 01 §1.16 layer 1,
// §4.5; Spec 01 §4.2; Pass 1b, #89). Pure: rows the database returned in, bars out, nothing re-diffed in the browser.
//
// A bar is a share, not a count, so a 400-word FOMC statement and a multi-page RBI resolution sit on one scale:
//   added   = words_added   ÷ words in the new statement   (above the line)
//   removed = words_removed ÷ words in the previous one    (below the line)
// Both counts and both lengths are stored with the diff (`core.document_diff.words_added`, `words_removed` and the
// diff's own `words: {old, new}`); the browser divides, nothing is re-counted. A bar whose stored numbers don't add
// up (a count above its text's length, a length of zero, a value that isn't a whole number) is held back, not drawn
// (the accuracy rule: right, labelled or absent). Each share is derived, so the panel says so.
//
// A decision's document is its latest version (a changed text is a new version, CLAUDE.md §3), and the bar is that
// version's diff against the statement before it. A meeting with no stored diff (the first statement has nothing
// before it, or the comparison isn't stored yet) has no bar; the panel counts them rather than drawing a zero.
import { meetingTabHref } from "@/lib/policy/decisions";
import type { PolicyMeetingRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

/** What the strip reads of a decision document (api.documents). */
export type StripDocument = { id: number; event_slug: string | null; version_no: number };

/** What it reads of a diff (api.document_diffs): the stored counts and `segments->words`. */
export type StripDiff = { document_id: number; words_added: number; words_removed: number; words: unknown };

export type StripBar = {
  bank: Bank;
  slug: string;
  /** The decision date, YYYY-MM-DD: the bar's place on the time axis. */
  date: string;
  /** Words added as a percent of the new statement's words. */
  addedPct: number;
  /** Words removed as a percent of the previous statement's words. */
  removedPct: number;
  wordsAdded: number;
  wordsRemoved: number;
  wordsNew: number;
  wordsOld: number;
  /** The meeting's What changed tab. */
  href: string;
};

export type BankStrip = {
  bank: Bank;
  bars: StripBar[];
  /** Meetings of this bank with no bar: nothing before them to compare, or no stored comparison. */
  withoutBar: number;
  meetings: number;
};

function wholeCount(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v) && v >= 0;
}

/** `segments->words` of a version-2 diff, or null when it isn't two positive whole counts. */
export function parseLengths(words: unknown): { old: number; new: number } | null {
  if (typeof words !== "object" || words === null) return null;
  const w = words as { old?: unknown; new?: unknown };
  if (!wholeCount(w.old) || !wholeCount(w.new) || w.old === 0 || w.new === 0) return null;
  return { old: w.old, new: w.new };
}

/** One diff as a bar's numbers, or null when what is stored can't be a share (held back, not guessed). */
export function shares(diff: StripDiff): { addedPct: number; removedPct: number; wordsNew: number; wordsOld: number } | null {
  const len = parseLengths(diff.words);
  if (!len || !wholeCount(diff.words_added) || !wholeCount(diff.words_removed)) return null;
  if (diff.words_added > len.new || diff.words_removed > len.old) return null;
  return {
    addedPct: (100 * diff.words_added) / len.new,
    removedPct: (100 * diff.words_removed) / len.old,
    wordsNew: len.new,
    wordsOld: len.old,
  };
}

/** Each meeting's latest decision document by event slug (the highest version wins). */
function latestBySlug(documents: readonly StripDocument[]): Map<string, StripDocument> {
  const latest = new Map<string, StripDocument>();
  for (const d of documents) {
    if (d.event_slug === null) continue;
    const have = latest.get(d.event_slug);
    if (!have || d.version_no > have.version_no) latest.set(d.event_slug, d);
  }
  return latest;
}

/**
 * The strip of each bank, oldest meeting first: `decisions` are the held rate decisions the History table lists
 * (so the strip and the table name the same meetings), `documents` the decision documents (every version) and
 * `diffs` their diffs.
 */
export function buildChangeStrips(
  decisions: readonly Pick<PolicyMeetingRow, "slug" | "bank" | "decision_date">[],
  documents: readonly StripDocument[],
  diffs: readonly StripDiff[],
): BankStrip[] {
  const doc = latestBySlug(documents);
  const diffOf = new Map(diffs.map((d) => [d.document_id, d]));
  return (["Fed", "RBI"] as const).map((bank) => {
    const own = decisions.filter((d) => d.bank === bank).sort((a, b) => a.decision_date.localeCompare(b.decision_date) || a.slug.localeCompare(b.slug));
    const bars: StripBar[] = [];
    for (const m of own) {
      const d = doc.get(m.slug);
      const diff = d ? diffOf.get(d.id) : undefined;
      const s = diff ? shares(diff) : null;
      if (!diff || !s) continue;
      bars.push({
        bank,
        slug: m.slug,
        date: m.decision_date,
        ...s,
        wordsAdded: diff.words_added,
        wordsRemoved: diff.words_removed,
        href: meetingTabHref(bank, m.decision_date, "changes"),
      });
    }
    return { bank, bars, withoutBar: own.length - bars.length, meetings: own.length };
  });
}

/** The scale both rows share: ±60% unless a bar needs more, then the next 20 up (never past 100). */
export function stripScale(strips: readonly BankStrip[]): number {
  const biggest = Math.max(0, ...strips.flatMap((s) => s.bars.flatMap((b) => [b.addedPct, b.removedPct])));
  return Math.min(100, Math.max(60, Math.ceil(biggest / 20) * 20));
}

/** The chart data of one bank: added above the zero line, removed below it (so negative values). */
export function stripSeries(strip: BankStrip): { added: { time: string; value: number }[]; removed: { time: string; value: number }[] } {
  return {
    added: strip.bars.map((b) => ({ time: b.date, value: b.addedPct })),
    removed: strip.bars.map((b) => ({ time: b.date, value: -b.removedPct })),
  };
}

/** The bar at a date, or undefined. */
export function barAt(strip: BankStrip, date: string | null): StripBar | undefined {
  return date === null ? undefined : strip.bars.find((b) => b.date === date);
}

/** The bar next to the one at `date` (`step` ±1), clamped to the ends; the newest bar when none is selected. */
export function stepBar(strip: BankStrip, date: string | null, step: -1 | 1): StripBar | undefined {
  if (strip.bars.length === 0) return undefined;
  const i = date === null ? -1 : strip.bars.findIndex((b) => b.date === date);
  if (i === -1) return strip.bars[strip.bars.length - 1];
  return strip.bars[Math.min(strip.bars.length - 1, Math.max(0, i + step))];
}

/** One row of "View as table": a meeting of either bank, newest first. */
export type StripTableRow = StripBar;

export function stripTableRows(strips: readonly BankStrip[]): StripTableRow[] {
  return strips.flatMap((s) => s.bars).sort((a, b) => b.date.localeCompare(a.date) || a.bank.localeCompare(b.bank));
}
