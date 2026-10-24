// web/lib/policy/text.ts: a document's canonical text as the pages show it (Spec 01 §1.1; py ft/engines/document/
// text.py). Pure.
//
// - The text is its chunks' bodies in order: the database checks they reproduce it exactly (core.chunk), so the
//   page never shows text the database didn't store. Chunks that don't join end to start are refused here too.
// - Offsets count Unicode code points (Postgres char_length, Python len); JavaScript strings count UTF-16 units, so
//   every offset goes through these helpers, never through String.slice on a stored offset (CLAUDE.md §3).
// - ¶ numbers are the text's blocks, separated by one blank line ("\n\n"), counted from 1, exactly as the differ's
//   old_para / new_para and the ¶ links count them (text.py's paragraph_starts).
import type { DocumentChunkRow } from "@/lib/queries/api";

export type Paragraph = { n: number; start: number; end: number; text: string };

/** The document's text from its chunks, or an error if they don't join up (start at 0, each where the last ended). */
export function textFromChunks(chunks: readonly DocumentChunkRow[]): string {
  const sorted = [...chunks].sort((a, b) => a.chunk_no - b.chunk_no);
  let at = 0;
  for (const c of sorted) {
    if (c.char_start !== at) throw new Error(`chunks of document ${c.document_id} don't join at ${at} (chunk ${c.chunk_no} starts at ${c.char_start})`);
    const length = [...c.body].length;
    if (c.char_end - c.char_start !== length) throw new Error(`chunk ${c.chunk_no} of document ${c.document_id} isn't as long as its offsets say`);
    at = c.char_end;
  }
  return sorted.map((c) => c.body).join("");
}

/** The text's blocks with their ¶ numbers and code-point spans. */
export function paragraphs(text: string): Paragraph[] {
  const cps = [...text];
  const out: Paragraph[] = [];
  let start = 0;
  for (let i = 0; i <= cps.length; i++) {
    const end = i === cps.length || (cps[i] === "\n" && cps[i + 1] === "\n");
    if (!end) continue;
    out.push({ n: out.length + 1, start, end: i, text: cps.slice(start, i).join("") });
    start = i + 2;
    i++;
  }
  return out;
}

/** The paragraph a code-point offset falls in, or null. */
export function paragraphAt(paras: readonly Paragraph[], offset: number | null): Paragraph | null {
  if (offset === null) return null;
  return paras.find((p) => offset >= p.start && offset < p.end) ?? null;
}

/** Code points `start`…`end` of `text`. */
export function sliceCodePoints(text: string, start: number, end: number): string {
  return [...text].slice(start, end).join("");
}
