// web/lib/equity/remarks.ts: the Earnings calls tab's "Opening remarks" panel (2B-19d, #388; Spec 02B §5.13, §8) from
// api.remarks_changes (0036). Pure. The browser derives no figure: the share unchanged, the word counts and the speakers are
// the view's; this only reads the stored sentence pairs into Key changes' model. A pair side is a sentence (or a few) as filed,
// at most 400 characters; it is cut again here by code point (JavaScript counts UTF-16) in case a stored side were longer.
// Words, never colour: nothing here is a market move (CLAUDE.md §5).
import { formatCount, formatFraction } from "@/lib/format";
import type { KeyChanges, KeyGroup, KeyRow } from "@/lib/policy/keychanges";
import type { RemarksChangeRow } from "@/lib/queries/api";

export const REMARKS_FIELD = "equity.remarks_change";
export const REMARKS_HELD = "Opening-remarks changes held back until they pass review";
export const REMARKS_TOPIC = "Opening remarks";
export const MAX_SIDE = 400;
export const MAX_PAIRS = 5;

const cap = (text: string): string => {
  const points = Array.from(text);
  return points.length <= MAX_SIDE ? text : points.slice(0, MAX_SIDE - 1).join("").trimEnd() + "…";
};

function sideText(v: unknown): string | null {
  if (typeof v !== "object" || v === null) return null;
  const t = (v as { text?: unknown }).text;
  return typeof t === "string" && t.trim() !== "" ? cap(t) : null;
}

/** The stored pairs as Key changes' model: one group, each row a changed, added or removed sentence, plain text on each side and no ¶ links. */
export function remarksKeyChanges(pairs: unknown): KeyChanges {
  const rows: KeyRow[] = [];
  if (Array.isArray(pairs)) {
    pairs.slice(0, MAX_PAIRS).forEach((raw, i) => {
      if (typeof raw !== "object" || raw === null) return;
      const p = raw as { old?: unknown; new?: unknown };
      const before = sideText(p.old);
      const now = sideText(p.new);
      if (before === null && now === null) return;
      rows.push({
        key: `${i}`,
        kind: before === null ? "added" : now === null ? "removed" : "changed",
        before: before === null ? null : { ops: [{ op: "eq", text: before }], para: null },
        now: now === null ? null : { ops: [{ op: "eq", text: now }], para: null },
      });
    });
  }
  const groups: KeyGroup[] = rows.length === 0 ? [] : [{ topic: REMARKS_TOPIC, rows }];
  return { groups, changedSentences: rows.length, otherSentences: 0 };
}

/** "83.0%": the share of the opening's wording unchanged against the previous quarter's (numbers masked), from the view's fraction. */
export function similarityText(r: Pick<RemarksChangeRow, "similarity">): string {
  return formatFraction(r.similarity);
}

/** "40 words added, 30 removed". */
export function wordsChangedText(r: Pick<RemarksChangeRow, "words_added" | "words_removed">): string {
  return `${formatCount(r.words_added)} ${r.words_added === 1 ? "word" : "words"} added, ${formatCount(r.words_removed)} removed`;
}

/** The new-speaker note, or null: "New speaker in the opening remarks: A, B". */
export function newSpeakerNote(r: Pick<RemarksChangeRow, "new_speakers">): string | null {
  const names = r.new_speakers.filter((n) => n.trim() !== "");
  return names.length === 0 ? null : `New speaker in the opening remarks: ${names.join(", ")}`;
}

/** The comparison of a call, by its slug. */
export function remarksOf(rows: readonly RemarksChangeRow[] | null, callSlug: string): RemarksChangeRow | null {
  return rows?.find((r) => r.call_slug === callSlug) ?? null;
}
