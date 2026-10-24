// The redline's model (C4) from a stored diff (version 2): every word from the row, grouped by paragraph so
// "Changes only" can fold the unchanged ones.
import { describe, expect, it } from "vitest";
import { buildRedline, parsePairs } from "./redline";

const SEGMENTS = {
  version: 2,
  pairs: [
    { kind: "same", text: "Recent indicators suggest growth.", old: [[0, 33]], new: [[0, 33]], old_para: [1], new_para: [1] },
    { kind: "same", text: "Inflation remains elevated.", old: [[35, 62]], new: [[35, 62]], old_para: [2], new_para: [2] },
    { kind: "changed", old_para: [3], new_para: [3], ops: [{ op: "eq", text: "The Committee decided to " }, { op: "del", text: "maintain" }, { op: "ins", text: "lower" }, { op: "eq", text: " the target range." }] },
    { kind: "removed", text: "A sentence that went.", old_para: [3], new_para: [] },
    { kind: "added", text: "A new closing sentence.", old_para: [], new_para: [4] },
  ],
};

describe("parsePairs", () => {
  it("reads a version-2 diff", () => {
    const pairs = parsePairs(SEGMENTS)!;
    expect(pairs.map((p) => p.kind)).toEqual(["same", "same", "changed", "removed", "added"]);
    expect(pairs[3]).toMatchObject({ newPara: null, oldPara: 3 });
  });

  it("refuses anything else rather than guess", () => {
    expect(parsePairs({ version: 1, pairs: [] })).toBeNull();
    expect(parsePairs({ version: 2, pairs: [{ kind: "moved", text: "x" }] })).toBeNull();
    expect(parsePairs({ version: 2, pairs: [{ kind: "changed", ops: [{ op: "swap", text: "x" }] }] })).toBeNull();
    expect(parsePairs(null)).toBeNull();
  });
});

describe("buildRedline", () => {
  it("groups by the new text's paragraphs; a removed sentence stays with its neighbour", () => {
    const r = buildRedline(parsePairs(SEGMENTS)!);
    expect(r.paragraphs.map((p) => [p.para, p.pairs.length, p.changed])).toEqual([[1, 1, false], [2, 1, false], [3, 2, true], [4, 1, true]]);
    expect(r).toMatchObject({ changedParagraphs: 2, unchangedParagraphs: 2 });
  });
});
