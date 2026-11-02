// FEEDS and PREVIEW_FEEDS (#124, #132): module 2's surfaces stayed out of the status bar's count until 2A-26 moved them
// into FEEDS, once production's runs had left a freshness row for each of their tasks.
import { describe, expect, it } from "vitest";
import { FEEDS, liveTasks, PREVIEW_FEEDS } from "@/lib/feeds";

describe("module 2's surfaces (2A-26, #132)", () => {
  it("has the four 2A surfaces live: each names its tasks, and the status bar counts them", () => {
    const live = liveTasks();
    for (const surface of ["screener", "company", "valuation", "rateSensitivity"] as const) {
      expect(FEEDS[surface].length).toBeGreaterThan(0);
      for (const task of FEEDS[surface]) expect(live).toContain(task);
    }
    expect(FEEDS.screener).toEqual(["equity.universe", "equity.prices", "equity.statements", "equity.metrics", "equity.beta"]);
    expect(FEEDS.rateSensitivity).toEqual(["equity.rate_reactions", "policy.rate_cycles"]);
  });

  it("leaves nothing in PREVIEW_FEEDS, so no surface hides a task from the count", () => {
    expect(Object.keys(PREVIEW_FEEDS)).toEqual([]);
  });

  it("counts only tasks that exist: module 1's, the accuracy projection and module 2's data tasks", () => {
    expect(liveTasks().every((t) => t.startsWith("policy.") || t.startsWith("equity.") || t === "site.quality_summary")).toBe(true);
  });
});

describe("/accuracy (ACC-3, #135)", () => {
  it("has its task counting in the status bar, and is no longer a preview", () => {
    expect(FEEDS.accuracy).toEqual(["site.quality_summary"]);
    expect(liveTasks()).toContain("site.quality_summary");
    expect(Object.keys(PREVIEW_FEEDS)).not.toContain("accuracy");
  });
});
