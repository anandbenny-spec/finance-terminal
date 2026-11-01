// PREVIEW_FEEDS (#124): module 2's surfaces stay out of the status bar's count until 2A-26 moves them into FEEDS.
import { describe, expect, it } from "vitest";
import { FEEDS, liveTasks, PREVIEW_FEEDS } from "@/lib/feeds";

describe("PREVIEW_FEEDS", () => {
  it("names the screener's tasks, and liveTasks() ignores them", () => {
    expect(PREVIEW_FEEDS.screener.length).toBeGreaterThan(0);
    const live = liveTasks();
    for (const task of PREVIEW_FEEDS.screener) expect(live).not.toContain(task);
    expect(live.every((t) => t.startsWith("policy."))).toBe(true);
    expect(Object.keys(FEEDS).some((k) => k === "screener")).toBe(false);
  });
});
