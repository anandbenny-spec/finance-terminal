// web/lib/policy/timeline.ts: what "How the wording moved" shows on /policy (#509, marks reworked in #530; Design 01 §1.16,
// Pass 2). Pure: the decisions, the decision documents and the stored diffs' pairs in, one bank's rows × meetings grid of
// cells out.
//
// - Hidden until Anand inserts a `site.language_timeline` row in core.field_policy with status `shown` or `labelled`
//   (the Policy Day pattern, lib/policy/policyday.ts): no row means hidden, and the page then reads nothing for it.
// - One row is one key phrase (lib/policy/timeline-rows.ts), one column one held rate decision, newest first. A cell is
//   the bank's own sentence for that phrase, found in the stored diff's pairs (the "Now" side of each pair: the meeting's
//   own words). Nothing is re-diffed in the browser and no AI is involved.
// - A cell is a sentence only when exactly one sentence of the right topic matches the row's rule. None, or more than
//   one, is a gap that says so: a row never guesses (the accuracy rule, CLAUDE.md §1). So is a meeting with no decision
//   document, no stored diff (the first of a series, or the document type changed) or a diff this code can't read.
// - The marks are NOT the stored pair's: a cell is marked against the previous column's sentence in the same row
//   (`compareSentences`: a word comparison, here, server-side). New words are underlined, words the previous column had
//   and this one hasn't are struck, and the label says Unchanged (the same words), Changed (any word new or removed) or
//   New sentence (only where the previous column is a gap). The oldest column is marked against the sentence the same row
//   rule finds on the base side of its own stored diff. The stored pairs pair sentences across the whole document, so a
//   reworded sentence was left unpaired ("added") and a removal-only pair read as unchanged (#530).
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

// ------------------------------------------------------------------------------------------------ the comparison

// A word is a run of letters or digits joined by an internal hyphen, slash, point, comma or apostrophe, so "2-1/2", "5.3",
// "1,000" and "Committee's" are one token each; every other mark is a token of its own. The two hyphen variants (U+2010 and
// U+2011) join a word like the plain hyphen does. Whitespace, whatever its kind (the no-break space U+00A0 and the narrow
// one U+202F included), is never a token and so is never compared.
const TOKEN = /(\s+)|([\p{L}\p{N}\p{M}]+(?:[‐‑\-\/.,'’][\p{L}\p{N}\p{M}]+)*)|(\S)/gu;

/** A token, with the whitespace that sat before it in its own sentence (so a sentence is its tokens' `lead + text`). */
type Token = { lead: string; text: string; key: string };

/**
 * What is compared: the token as written, with U+2010 and U+2011 folded to the plain hyphen. Nothing else is folded: not
 * case, not quotes, not dashes (an en dash in "10–2" is a real character).
 */
function keyOf(text: string): string {
  return text.replace(/[‐‑]/g, "-");
}

function tokenize(sentence: string): Token[] {
  const out: Token[] = [];
  let lead = "";
  for (const m of sentence.matchAll(TOKEN)) {
    if (m[1] !== undefined) lead = m[1];
    else {
      const text = m[2] ?? m[3];
      out.push({ lead: out.length === 0 ? "" : lead, text, key: keyOf(text) });
      lead = "";
    }
  }
  return out;
}

/** A sentence this long, in tokens squared, is not compared word by word (a sentence in these documents is under 150 tokens). */
const COMPARE_LIMIT = 4_000_000;

/**
 * The words of `current` against the sentence before it, as the redline reads: `eq` runs, then each changed stretch as one
 * `del` run (the previous sentence's words) followed by one `ins` run (the current sentence's words), in order.
 *
 * - `eq` and `ins` carry the current sentence's own characters, so `eq` + `ins` rebuilds it exactly (ends trimmed). `del`
 *   carries the previous sentence's own characters, so `eq` + `del` rebuilds the previous sentence word for word; only the
 *   whitespace between words may differ, because whitespace is never compared.
 * - A word is a token (`TOKEN` above). The comparison is a longest common subsequence over tokens' keys, and a tie is broken
 *   the same way every time: a word only the previous sentence has is taken as removed before a word only the current one has.
 * - Identical sentences are one `eq`. Sentences that differ in a hyphen variant or a kind of space only are one `eq`.
 */
export function compareSentences(previous: string, current: string): Op[] {
  const a = tokenize(previous);
  const b = tokenize(current);
  const n = a.length;
  const m = b.length;
  const edits: { op: Op["op"]; text: string }[] = [];
  if (n * m > COMPARE_LIMIT) {
    for (const t of a) edits.push({ op: "del", text: t.lead + t.text });
    for (const t of b) edits.push({ op: "ins", text: t.lead + t.text });
  } else {
    // lcs[i][j]: how many tokens a[i..] and b[j..] share, in order.
    const lcs = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i][j] = a[i].key === b[j].key ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i].key === b[j].key) {
        edits.push({ op: "eq", text: b[j].lead + b[j].text });
        i++;
        j++;
      } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
        edits.push({ op: "del", text: a[i].lead + a[i].text });
        i++;
      } else {
        edits.push({ op: "ins", text: b[j].lead + b[j].text });
        j++;
      }
    }
    for (; i < n; i++) edits.push({ op: "del", text: a[i].lead + a[i].text });
    for (; j < m; j++) edits.push({ op: "ins", text: b[j].lead + b[j].text });
  }
  // One `eq` run per stretch of equal words; each changed stretch is one `del` followed by one `ins`.
  const ops: Op[] = [];
  let eq = "";
  let del = "";
  let ins = "";
  const flushStretch = () => {
    if (del) ops.push({ op: "del", text: del });
    if (ins) ops.push({ op: "ins", text: ins });
    del = "";
    ins = "";
  };
  const flushEq = () => {
    if (eq) ops.push({ op: "eq", text: eq });
    eq = "";
  };
  for (const e of edits) {
    if (e.op === "eq") {
      flushStretch();
      eq += e.text;
    } else {
      flushEq();
      if (e.op === "del") del += e.text;
      else ins += e.text;
    }
  }
  flushStretch();
  flushEq();
  return ops;
}

// ------------------------------------------------------------------------------------------------ the cells

/** Why a cell holds no sentence. `none` and `several` are the row's own rule; the rest are about the meeting. */
export type GapReason = "none" | "several" | "no_document" | "no_diff" | "unreadable";

export type SentenceCell = {
  kind: "sentence";
  /**
   * The sentence as the meeting's document holds it, marked against the previous column's sentence in the same row: `eq`
   * words, `ins` words that are new, and `del` words the previous column had and this one hasn't (struck). `eq` + `ins` is
   * the meeting's sentence. A "new" cell is one `ins`: every word underlined.
   */
  parts: Op[];
  /**
   * `same`: the same words as the previous column. `changed`: any word new or removed. `new`: the previous column is a gap
   * (or, for the oldest column, its base document has no single matching sentence), so there is nothing to compare with.
   */
  how: "same" | "changed" | "new";
  /** The paragraph of the meeting's document the sentence sits in (the ¶ link), or null. */
  para: number | null;
};

export type GapCell = { kind: "gap"; reason: GapReason; matched: number };

export type TimelineCell = SentenceCell | GapCell;

/** A row's sentence in one meeting, found by the row's rule and not yet marked against the meeting before. */
export type FoundCell = { kind: "found"; text: string; para: number | null };

/** A cell's sentence as plain text: its `eq` and `ins` words (the meeting's own), never its struck ones. */
export function sentenceText(cell: SentenceCell): string {
  return cell.parts.filter((p) => p.op !== "del").map((p) => p.text).join("");
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

/** A sentence of one side of a pair, with the pair's topic and (for the meeting's own side) paragraph. */
type Candidate = { topic: string | null; text: string; para: number | null };

function joined(ops: readonly Op[], skip: Op["op"]): string {
  return ops.filter((o) => o.op !== skip).map((o) => o.text).join("");
}

/** The meeting's own words from each pair (its "Now" side: `eq` + `ins`); a sentence the meeting dropped has none. */
function nowSide(pairs: readonly RedlinePair[]): Candidate[] {
  return pairs.flatMap((pair): Candidate[] => {
    switch (pair.kind) {
      case "removed":
        return [];
      case "added":
      case "same":
        return [{ topic: pair.topic, text: pair.text, para: pair.newPara }];
      case "changed":
        return [{ topic: pair.topic, text: joined(pair.ops, "del"), para: pair.newPara }];
    }
  });
}

/** The words each pair holds for the document the diff was made against (its "Before" side: `eq` + `del`); an added sentence has none. */
function baseSide(pairs: readonly RedlinePair[]): Candidate[] {
  return pairs.flatMap((pair): Candidate[] => {
    switch (pair.kind) {
      case "added":
        return [];
      case "removed":
      case "same":
        return [{ topic: pair.topic, text: pair.text, para: pair.oldPara }];
      case "changed":
        return [{ topic: pair.topic, text: joined(pair.ops, "ins"), para: pair.oldPara }];
    }
  });
}

/** The row's rule over one side's sentences: tiers in order, the first that finds any sentence decides. */
function findIn(row: RowDef, side: readonly Candidate[]): FoundCell | GapCell {
  for (const tier of row.tiers) {
    const found = side.filter((s) => s.topic === tier.topic && tier.all.every((rx) => rx.test(s.text)) && !(tier.none ?? []).some((rx) => rx.test(s.text)));
    if (found.length === 1) return { kind: "found", text: found[0].text, para: found[0].para };
    if (found.length > 1) return { kind: "gap", reason: "several", matched: found.length };
  }
  return { kind: "gap", reason: "none", matched: 0 };
}

/** One row's sentence for one meeting's pairs, on the meeting's own side (unmarked), or the gap that says why there is none. */
export function cellFor(row: RowDef, pairs: readonly RedlinePair[]): FoundCell | GapCell {
  return findIn(row, nowSide(pairs));
}

/** Every row's sentence for one meeting, keyed by the row. */
export function cellsFor(rows: readonly RowDef[], pairs: readonly RedlinePair[]): Record<string, FoundCell | GapCell> {
  return Object.fromEntries(rows.map((row) => [row.key, cellFor(row, pairs)]));
}

/**
 * The sentence the same row rule finds on the base side of a stored diff: what the document the diff was made against said.
 * The oldest column's "previous column". Null unless exactly one sentence matches.
 */
export function baseSentenceFor(row: RowDef, pairs: readonly RedlinePair[]): string | null {
  const found = findIn(row, baseSide(pairs));
  return found.kind === "found" ? found.text : null;
}

/** A found sentence, marked against the previous column's sentence (null when there is none to compare with). */
export function markCell(found: FoundCell, previous: string | null): SentenceCell {
  if (previous === null) return { kind: "sentence", parts: [{ op: "ins", text: found.text }], how: "new", para: found.para };
  const parts = compareSentences(previous, found.text);
  return { kind: "sentence", parts, how: parts.every((p) => p.op === "eq") ? "same" : "changed", para: found.para };
}

/** The previous column's sentence for a row, or null where that column holds a gap. */
function sentenceOf(cell: TimelineCell): string | null {
  return cell.kind === "sentence" ? sentenceText(cell) : null;
}

function allGap(rows: readonly RowDef[], reason: GapReason): Record<string, GapCell> {
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
 *
 * Each row is walked from the oldest column to the newest, and each sentence is marked against the previous column's
 * (`markCell`). The oldest column has no earlier column: its previous sentence is the one the row rule finds on the base
 * side of its own diff, and a base side with no single match leaves it "new", as after a gap.
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
  const oldest = own.length - 1;
  const found = own.map((m, index) => {
    const d = doc.get(m.slug);
    const diff = d ? diffOf.get(d.id) : undefined;
    const pairs = diff ? parsePairs({ version: diff.version, pairs: diff.pairs }) : null;
    const cells: Record<string, FoundCell | GapCell> = !d ? allGap(rows, "no_document") : !diff ? allGap(rows, "no_diff") : !pairs ? allGap(rows, "unreadable") : cellsFor(rows, pairs);
    const base = index === oldest && pairs ? Object.fromEntries(rows.map((row) => [row.key, baseSentenceFor(row, pairs)])) : null;
    return { cells, base };
  });
  const marked: Record<string, TimelineCell>[] = new Array(own.length);
  for (let i = oldest; i >= 0; i--) {
    marked[i] = Object.fromEntries(
      rows.map((row): [string, TimelineCell] => {
        const here = found[i].cells[row.key];
        if (here.kind === "gap") return [row.key, here];
        const previous = i === oldest ? (found[i].base?.[row.key] ?? null) : sentenceOf(marked[i + 1][row.key]);
        return [row.key, markCell(here, previous)];
      }),
    );
  }
  const columns = own.map((m, i): TimelineColumn => ({ slug: m.slug, date: m.decision_date, href: meetingTabHref(bank, m.decision_date, "statement"), cells: marked[i] }));
  return { bank, rows: rows.map((r) => ({ key: r.key, label: r.label })), columns, latest: columns[0]?.date ?? null };
}
