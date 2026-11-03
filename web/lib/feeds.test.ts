// PREVIEW_FEEDS (#124): module 2's surfaces stay out of the status bar's count until 2A-26 moves them into FEEDS.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { DAILY_TASKS, FEEDS, liveTasks, PREVIEW_FEEDS } from "@/lib/feeds";

/** Each task's workflows in py/config/tasks.yaml (`workflows: {daily: …, watch: …}` lines), read without a YAML library. */
function taskWorkflows(): Map<string, string[]> {
  const out = new Map<string, string[]>();
  let task: string | null = null;
  for (const line of readFileSync(new URL("../../py/config/tasks.yaml", import.meta.url), "utf8").split("\n")) {
    const key = /^([a-z_]+\.[a-z_]+):\s*$/.exec(line);
    if (key) task = key[1];
    const workflows = /^\s+workflows:\s*\{(.*)\}/.exec(line);
    if (workflows && task) out.set(task, workflows[1].split(",").map((w) => w.split(":")[0].trim()));
  }
  return out;
}

describe("DAILY_TASKS (#263)", () => {
  const workflows = taskWorkflows();

  it("reads every live task's workflows from tasks.yaml", () => {
    for (const task of liveTasks()) expect(workflows.get(task), task).toBeDefined();
  });

  it("is exactly the live tasks that run in the daily workflow, so the status bar's checked time follows the data", () => {
    const dailyLive = liveTasks().filter((t) => workflows.get(t)?.includes("daily"));
    expect([...DAILY_TASKS].sort()).toEqual(dailyLive);
    expect(DAILY_TASKS).not.toContain("policy.calendar");
  });
});

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
