// The status bar's freshness summary (C2; Design 01 §1.8, §4.1; Review 01, F17): only the tasks behind live
// parts of the site count, "stale" shows (D4's check), and nothing is called current without a success.
import { describe, expect, it } from "vitest";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import { FEEDS, liveTasks } from "@/lib/feeds";
import { panelFreshness, summarise, taskState } from "./freshness";

const NOW = "2026-10-24T03:00Z";

function row(task_key: string, status: TaskFreshnessRow["status"], last_success_at: string | null = "2026-10-23T01:11Z"): TaskFreshnessRow {
  return { task_key, status, last_success_at, last_checked_at: last_success_at };
}

describe("taskState", () => {
  it("reads ok as current, failed as delayed, stale as stale", () => {
    expect(taskState(row("a", "ok"))).toBe("current");
    expect(taskState(row("a", "failed"))).toBe("delayed");
    expect(taskState(row("a", "stale"))).toBe("stale");
  });

  it("fails closed: no row, or no success ever, isn't current", () => {
    expect(taskState(undefined)).toBe("stale");
    expect(taskState(row("a", "ok", null))).toBe("stale");
  });
});

describe("summarise", () => {
  const tasks = ["policy.calendar", "policy.market_series"];

  it("says all data is current, checked at the oldest of the last successes", () => {
    const rows = [row("policy.calendar", "ok", "2026-10-20T01:11Z"), row("policy.market_series", "ok", "2026-10-23T22:40Z")];
    expect(summarise(rows, tasks, NOW)).toEqual({
      state: "current",
      text: "All data current · checked 20 Oct, 06:41 IST",
      delayed: 0,
      stale: 0,
    });
  });

  it("counts delayed and stale tasks without naming them", () => {
    expect(summarise([row("policy.calendar", "ok"), row("policy.market_series", "failed")], tasks, NOW).text).toBe("1 source delayed");
    expect(summarise([row("policy.calendar", "stale"), row("policy.market_series", "ok")], tasks, NOW).text).toBe("1 source stale");
    expect(summarise([row("policy.calendar", "stale"), row("policy.market_series", "stale")], tasks, NOW).text).toBe("2 sources stale");
    expect(summarise([row("policy.calendar", "stale"), row("policy.market_series", "failed")], tasks, NOW)).toEqual({
      state: "behind",
      text: "1 source delayed, 1 stale",
      delayed: 1,
      stale: 1,
    });
  });

  it("ignores tasks outside the live list it is given", () => {
    const rows = [row("policy.calendar", "ok"), row("policy.market_series", "ok"), row("policy.reactions", "stale", null)];
    expect(summarise(rows, tasks, NOW).state).toBe("current");
  });

  it("counts a live task with no freshness row as stale", () => {
    expect(summarise([row("policy.calendar", "ok")], tasks, NOW).text).toBe("1 source stale");
  });
});

describe("feeds", () => {
  it("has the shell read the tape's and the countdown's tasks, each once", () => {
    expect(FEEDS.tickerTape).toEqual(["policy.market_series"]);
    expect(FEEDS.statusBarCountdown).toEqual(["policy.calendar"]);
    // /policy's panels (C3) bring in the document and AI tasks; the decision-day moves (Pass 1b, #70) bring in
    // policy.reactions, live once enabled in tasks.yaml.
    expect(FEEDS.policyReactions).toEqual(["policy.reactions"]);
    expect(liveTasks()).toEqual([
      "policy.ai_batch",
      "policy.calendar",
      "policy.fed_documents",
      "policy.market_series",
      "policy.rbi_documents",
      "policy.reactions",
      "site.quality_summary",
    ]);
    expect(liveTasks({ a: ["policy.ai_batch", "policy.calendar"], b: ["policy.calendar"] })).toEqual(["policy.ai_batch", "policy.calendar"]);
  });
});

describe("panelFreshness (C3: each panel's footer)", () => {
  const tasks = ["policy.fed_documents", "policy.ai_batch"];

  it("gives the data's date and the oldest of the panel's last successes", () => {
    const rows = [row("policy.fed_documents", "ok", "2026-10-23T01:11Z"), row("policy.ai_batch", "ok", "2026-10-22T20:00Z")];
    expect(panelFreshness(rows, tasks, "2026-09-17", NOW)).toEqual({
      text: "Data to 17 Sep 2026 · checked 23 Oct, 01:30 IST",
      delayed: false,
    });
  });

  it("is delayed when a feeding task failed or went stale, and keeps the data's date", () => {
    expect(panelFreshness([row("policy.fed_documents", "failed"), row("policy.ai_batch", "ok")], tasks, "2026-09-17", NOW).delayed).toBe(true);
    expect(panelFreshness([row("policy.fed_documents", "ok")], tasks, "2026-09-17", NOW)).toEqual({
      text: "Data to 17 Sep 2026",
      delayed: true,
    });
  });

  it("claims only the data's date when freshness couldn't be read", () => {
    expect(panelFreshness(null, tasks, "2026-09-17", NOW)).toEqual({ text: "Data to 17 Sep 2026", delayed: false });
    expect(panelFreshness(null, tasks, null, NOW)).toEqual({ text: "", delayed: false });
  });
});
