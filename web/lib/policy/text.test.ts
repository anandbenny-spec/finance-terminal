// Canonical text on the page (C4): rebuilt from the stored chunks, ¶ numbers as the differ counts them, and offsets
// in code points, never UTF-16 units (CLAUDE.md §3).
import { describe, expect, it } from "vitest";
import type { DocumentChunkRow } from "@/lib/queries/api";
import { paragraphAt, paragraphs, sliceCodePoints, textFromChunks } from "./text";

const chunk = (chunk_no: number, char_start: number, body: string): DocumentChunkRow => ({
  document_id: 1, chunk_no, char_start, char_end: char_start + [...body].length, section: null, speaker: null, body,
});

describe("textFromChunks", () => {
  it("joins the chunks in order", () => {
    expect(textFromChunks([chunk(2, 6, "World"), chunk(1, 0, "Hello\n")])).toBe("Hello\nWorld");
  });

  it("counts offsets in code points: ₹ and 𝑥 (two UTF-16 units) are one each", () => {
    expect(textFromChunks([chunk(1, 0, "₹𝑥 "), chunk(2, 3, "ok")])).toBe("₹𝑥 ok");
  });

  it("refuses chunks with a gap or the wrong length", () => {
    expect(() => textFromChunks([chunk(1, 0, "ab"), chunk(2, 3, "c")])).toThrow(/don't join/);
    expect(() => textFromChunks([{ ...chunk(1, 0, "ab"), char_end: 5 }])).toThrow(/isn't as long/);
  });
});

describe("paragraphs", () => {
  const text = "First block.\n\nSecond 𝑥 block,\nline two.\n\nThird.";
  const paras = paragraphs(text);

  it("numbers blank-line blocks from 1 with code-point spans", () => {
    expect(paras.map((p) => [p.n, p.text])).toEqual([[1, "First block."], [2, "Second 𝑥 block,\nline two."], [3, "Third."]]);
    expect(paras[2].start).toBe([..."First block.\n\nSecond 𝑥 block,\nline two.\n\n"].length);
    expect(sliceCodePoints(text, paras[1].start, paras[1].end)).toBe(paras[1].text);
  });

  it("finds the paragraph holding an offset", () => {
    expect(paragraphAt(paras, 0)?.n).toBe(1);
    expect(paragraphAt(paras, paras[2].start + 1)?.n).toBe(3);
    expect(paragraphAt(paras, null)).toBeNull();
  });
});
