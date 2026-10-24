// web/lib/policy/redline.ts: the full redline's model (Design 01 §1.16 layer 3, §4.5 Redline) from the stored diff
// (core.document_diff.segments, diff version 2: py ft/engines/document/diff.py). Pure. The redline renders from
// that row alone: every word shown is a word it stores; nothing is re-diffed in the browser.
//
// Pairs are grouped into paragraphs of the new text (a removed sentence goes with the paragraph it was in, placed
// where the differ put it), so "Changes only" can fold the paragraphs where nothing changed.

export type Op = { op: "eq" | "del" | "ins"; text: string };

export type RedlinePair =
  | { kind: "same" | "added" | "removed"; text: string; newPara: number | null; oldPara: number | null }
  | { kind: "changed"; ops: Op[]; newPara: number | null; oldPara: number | null };

export type RedlineParagraph = { key: string; para: number | null; pairs: RedlinePair[]; changed: boolean };

export type Redline = { paragraphs: RedlineParagraph[]; changedParagraphs: number; unchangedParagraphs: number };

function first(v: unknown): number | null {
  return Array.isArray(v) && Number.isInteger(v[0]) ? (v[0] as number) : null;
}

function isOp(o: unknown): o is Op {
  return typeof o === "object" && o !== null && ["eq", "del", "ins"].includes((o as Op).op) && typeof (o as Op).text === "string";
}

/** The pairs of a version-2 diff, or null when the segments aren't that shape (the page then says so). */
export function parsePairs(segments: unknown): RedlinePair[] | null {
  if (typeof segments !== "object" || segments === null) return null;
  const s = segments as { version?: unknown; pairs?: unknown };
  if (s.version !== 2 || !Array.isArray(s.pairs)) return null;
  const out: RedlinePair[] = [];
  for (const raw of s.pairs) {
    if (typeof raw !== "object" || raw === null) return null;
    const p = raw as { kind?: unknown; text?: unknown; ops?: unknown; new_para?: unknown; old_para?: unknown };
    const newPara = first(p.new_para);
    const oldPara = first(p.old_para);
    if (p.kind === "changed") {
      if (!Array.isArray(p.ops) || !p.ops.every(isOp)) return null;
      out.push({ kind: "changed", ops: p.ops, newPara, oldPara });
    } else if (p.kind === "same" || p.kind === "added" || p.kind === "removed") {
      if (typeof p.text !== "string") return null;
      out.push({ kind: p.kind, text: p.text, newPara, oldPara });
    } else return null;
  }
  return out;
}

/** Pairs grouped by paragraph of the new text, in order. */
export function buildRedline(pairs: readonly RedlinePair[]): Redline {
  const paragraphs: RedlineParagraph[] = [];
  let current: RedlineParagraph | null = null;
  for (const p of pairs) {
    // A removed sentence has no paragraph in the new text: it stays with the paragraph it sits beside.
    if (current === null || (p.newPara !== null && current.para !== null && p.newPara !== current.para)) {
      current = { key: `${paragraphs.length}`, para: p.newPara, pairs: [], changed: false };
      paragraphs.push(current);
    } else if (current.para === null && p.newPara !== null) {
      current.para = p.newPara;
    }
    current.pairs.push(p);
    if (p.kind !== "same") current.changed = true;
  }
  const changedParagraphs = paragraphs.filter((p) => p.changed).length;
  return { paragraphs, changedParagraphs, unchangedParagraphs: paragraphs.length - changedParagraphs };
}
