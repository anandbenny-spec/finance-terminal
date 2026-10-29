// Key changes' model (Design 01 §1.16 layer 2) from a stored diff: grouped by topic in the document's order with
// "Other" last, Before = eq + del words, Now = eq + ins words, whole added/removed sentences with the other side empty.
import { describe, expect, it } from "vitest";
import { buildKeyChanges, previewKeyChanges } from "./keychanges";
import { parsePairs } from "./redline";

const SEGMENTS = {
  version: 2,
  pairs: [
    { kind: "same", topic: "Economy and labour market", text: "Same.", old_para: [1], new_para: [1] },
    { kind: "changed", topic: "Votes", old_para: [5], new_para: [5], ops: [{ op: "eq", text: "Voting for " }, { op: "del", text: "A" }, { op: "ins", text: "B" }] },
    { kind: "changed", topic: "Decision and guidance", old_para: [3], new_para: [3], ops: [{ op: "eq", text: "Decided to " }, { op: "del", text: "hold" }, { op: "ins", text: "cut" }, { op: "eq", text: "." }] },
    { kind: "added", topic: null, text: "A new sentence.", old_para: [], new_para: [4] },
    { kind: "removed", topic: "Monitoring", text: "A gone sentence.", old_para: [4], new_para: [] },
  ],
};

describe("buildKeyChanges", () => {
  const k = buildKeyChanges(parsePairs(SEGMENTS)!);

  it("orders the topics as the document does, Other last, and skips unchanged sentences", () => {
    expect(k.groups.map((g) => g.topic)).toEqual(["Decision and guidance", "Monitoring", "Votes", "Other"]);
    expect(k.changedSentences).toBe(4);
    expect(k.otherSentences).toBe(1);
  });

  it("splits a changed pair into its Before and Now words", () => {
    const row = k.groups[0].rows[0];
    expect(row.before!.ops.map((o) => o.text).join("")).toBe("Decided to hold.");
    expect(row.now!.ops.map((o) => o.text).join("")).toBe("Decided to cut.");
    expect([row.before!.para, row.now!.para]).toEqual([3, 3]);
  });

  it("leaves the other side empty for an added or removed sentence", () => {
    const removed = k.groups[1].rows[0];
    expect([removed.kind, removed.now, removed.before!.para]).toEqual(["removed", null, 4]);
    const added = k.groups[3].rows[0];
    expect([added.kind, added.before, added.now!.para]).toEqual(["added", null, 4]);
  });
});

describe("previewKeyChanges (the front page's first rows, #152)", () => {
  const k = buildKeyChanges(parsePairs(SEGMENTS)!);

  it("takes the first n rows in Key changes' own order and counts the others", () => {
    const p = previewKeyChanges(k, 3);
    expect(p.groups.map((g) => g.topic)).toEqual(["Decision and guidance", "Monitoring", "Votes"]);
    expect([p.shown, p.others]).toEqual([3, 1]);
  });

  it("cuts inside a topic when ties across topics fill the limit", () => {
    const two = buildKeyChanges(
      parsePairs({
        version: 2,
        pairs: [
          { kind: "added", topic: "Inflation", text: "One.", old_para: [], new_para: [1] },
          { kind: "added", topic: "Inflation", text: "Two.", old_para: [], new_para: [2] },
          { kind: "added", topic: "Growth", text: "Three.", old_para: [], new_para: [3] },
        ],
      })!,
    );
    const p = previewKeyChanges(two, 1);
    expect(p.groups.map((g) => [g.topic, g.rows.length])).toEqual([["Inflation", 1]]);
    expect(p.others).toBe(2);
    const q = previewKeyChanges(two, 2);
    expect(q.groups.map((g) => [g.topic, g.rows.length])).toEqual([["Inflation", 2]]);
    expect(q.others).toBe(1);
  });

  it("shows everything, with no others, when there are fewer rows than n", () => {
    const p = previewKeyChanges(k, 10);
    expect([p.shown, p.others]).toEqual([4, 0]);
    expect(previewKeyChanges(buildKeyChanges([]), 3)).toEqual({ groups: [], shown: 0, others: 0 });
  });
});
