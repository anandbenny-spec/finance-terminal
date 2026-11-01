// web/lib/delta.ts: a market change as Delta shows it (Design 01 §4.3). Green and red mean only that a market
// number rose or fell, always with a sign and a triangle; never a policy decision (CLAUDE.md §5).

/** A change as shown: the signed string, which way the rounded number moved, and how a screen reader says it. */
export type Change = { text: string; direction: "up" | "down" | "flat"; spoken: string };

/**
 * `opts.unit` names the unit when the text carries none (a column whose header holds it: "basis points").
 * The change a formatted string shows. format.ts signs every non-zero rounded value ("+0.42%", "−3 bps") and
 * leaves one that rounds to zero unsigned ("0.00%"), so the direction follows what the reader sees: a move too
 * small to show is flat, never a coloured "0.00%".
 */
export function changeOf(text: string, opts?: { unit?: string }): Change {
  const direction = text.startsWith("+") ? "up" : text.startsWith("−") ? "down" : "flat";
  const magnitude = text.replace(/^[+−]/, "").replace(/%$/, " percent").replace(/ bps$/, " basis points").replace(/ pp$/, " percentage points");
  const spokenMagnitude = opts?.unit ? `${magnitude} ${opts.unit}` : magnitude;
  const spoken = direction === "flat" ? `unchanged, ${spokenMagnitude}` : `${direction} ${spokenMagnitude}`;
  return { text, direction, spoken };
}
