import { describe, expect, it } from "vitest";
import { nextUnlabelledTone, parseToneItems, toneExport, toneStorageKey, validToneLabel } from "./tone";

const ok = {
  set: "earnings_tone",
  count: 2,
  items: [
    { id: "earnings_tone:1-2", section: "opening", text: "One." },
    { id: "earnings_tone:3-4", section: "qa", text: "Two." },
  ],
};

describe("parseToneItems", () => {
  it("accepts the route's reply", () => {
    expect(parseToneItems(ok)?.items).toHaveLength(2);
  });
  it("refuses anything else", () => {
    for (const bad of [null, "x", {}, { ...ok, set: "policy_stance" }, { ...ok, items: "x" }, { ...ok, items: [{ id: 1 }] }, { ...ok, items: [{ id: "a", text: "t", section: "x" }] }]) {
      expect(parseToneItems(bad)).toBeNull();
    }
  });
});

describe("validToneLabel", () => {
  it("needs a rubric score and a one-line reason", () => {
    expect(validToneLabel("+1", "on track, risks outweighed")).toBe(true);
    expect(validToneLabel("-2", " ok ")).toBe(true);
    expect(validToneLabel("3", "x")).toBe(false);
    expect(validToneLabel("0", "   ")).toBe(false);
    expect(validToneLabel("0", "two\nlines")).toBe(false);
    expect(validToneLabel("0", "x".repeat(201))).toBe(false);
  });
});

describe("export and position", () => {
  const items = parseToneItems(ok)!.items;
  it("lists every item, labelled or not, without any passage text", () => {
    const labels = { [items[0].id]: { label: "+1", reason: "r", saved_at: "t" } };
    const out = toneExport(items, labels, "2026-11-08T00:00:00Z");
    expect(out.complete).toBe(false);
    expect(out.labels).toEqual([
      { id: "earnings_tone:1-2", section: "opening", label: "+1", reason: "r" },
      { id: "earnings_tone:3-4", section: "qa", label: null, reason: null },
    ]);
    expect(JSON.stringify(out)).not.toContain("One.");
  });
  it("starts at the first unlabelled item, else the last", () => {
    expect(nextUnlabelledTone(items, {})).toBe(0);
    const l = { [items[0].id]: { label: "0", reason: "r", saved_at: "t" } };
    expect(nextUnlabelledTone(items, l)).toBe(1);
    expect(nextUnlabelledTone(items, { ...l, [items[1].id]: { label: "0", reason: "r", saved_at: "t" } })).toBe(1);
  });
  it("keeps its labels apart from the policy sets'", () => {
    expect(toneStorageKey()).toBe("ft-labels:earnings_tone:v1");
  });
});
