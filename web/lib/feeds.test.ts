// PREVIEW_FEEDS (#124): module 2's surfaces stay out of the status bar's count until 2A-26 moves them into FEEDS.
import { describe, expect, it } from "vitest";
import { FEEDS, liveTasks, PREVIEW_FEEDS } from "@/lib/feeds";

describe("PREVIEW_FEEDS", () => {
  it("names the screener's tasks, and liveTasks() ignores them", () => {
    expect(PREVIEW_FEEDS.screener.length).toBeGreaterThan(0);
    const live = liveTasks();
    for (const task of PREVIEW_FEEDS.screener) expect(live).not.toContain(task);
    // Module 1's tasks and the accuracy projection are the only live ones until 2A-26.
    expect(live.every((t) => t.startsWith("policy.") || t === "site.quality_summary")).toBe(true);
    expect(Object.keys(FEEDS).some((k) => k === "screener")).toBe(false);
  });

  it("has /accuracy live (ACC-3, #135): its task counts in the status bar, and is no longer a preview", () => {
    expect(FEEDS.accuracy).toEqual(["site.quality_summary"]);
    expect(liveTasks()).toContain("site.quality_summary");
    expect(Object.keys(PREVIEW_FEEDS)).not.toContain("accuracy");
  });
});
