// FEEDS and PREVIEW_FEEDS (#124, #132): module 2's surfaces stayed out of the status bar's count until 2A-26 moved them
// into FEEDS, once production's runs had left a freshness row for each of their tasks.
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

  // 2B-19 (#182) adds the card's one preview surface, `calls`, until 2B-22 moves it into FEEDS; DD-02 (#410) adds `segmentChanges`
  // until its task has a freshness row on production. Nothing else may sit there, and no preview task counts in the status bar.
  it("holds only the Earnings calls and segment-changes previews in PREVIEW_FEEDS, out of the status bar's count", () => {
    expect(Object.keys(PREVIEW_FEEDS)).toEqual(["calls", "segmentChanges"]);
    for (const task of [...PREVIEW_FEEDS.calls, ...PREVIEW_FEEDS.segmentChanges]) expect(liveTasks()).not.toContain(task);
    expect(PREVIEW_FEEDS.segmentChanges).toEqual(["equity.segment_changes"]);
  });

  // VC-06 (#191): moved in on 12 Nov 2026, once production's runs of both tasks had left a freshness row.
  it("counts the value chain's two tasks in the status bar, and it is no longer a preview", () => {
    expect(FEEDS.valueChain).toEqual(["equity.brsr", "equity.related_parties"]);
    for (const task of FEEDS.valueChain) expect(liveTasks()).toContain(task);
    expect(Object.keys(PREVIEW_FEEDS)).not.toContain("valueChain");
  });

  it("counts only tasks that exist: module 1's, the accuracy projection and module 2's data tasks", () => {
    expect(liveTasks().every((t) => t.startsWith("policy.") || t.startsWith("equity.") || t === "site.quality_summary")).toBe(true);
  });
});

describe("model-implied figures (#355)", () => {
  it("counts equity.valuation in the Valuation tab, and it and equity.snapshot in the screener's implied parts", () => {
    expect(FEEDS.valuation).toContain("equity.valuation");
    expect(FEEDS.screenerImplied).toEqual([...FEEDS.screener, "equity.valuation", "equity.snapshot"]);
    expect(FEEDS.screener).not.toContain("equity.valuation");
    expect(liveTasks()).toEqual(expect.arrayContaining(["equity.valuation", "equity.snapshot"]));
  });
});

describe("/accuracy (ACC-3, #135)", () => {
  it("has its task counting in the status bar, and is no longer a preview", () => {
    expect(FEEDS.accuracy).toEqual(["site.quality_summary"]);
    expect(liveTasks()).toContain("site.quality_summary");
    expect(Object.keys(PREVIEW_FEEDS)).not.toContain("accuracy");
  });
});
