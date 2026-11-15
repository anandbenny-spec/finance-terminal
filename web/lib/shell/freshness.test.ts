// The status bar's freshness summary (C2; Design 01 §1.8, §4.1; Review 01, F17): only the tasks behind live
// parts of the site count, "stale" shows (D4's check), and nothing is called current without a success.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import { DAILY_TASKS, FEEDS, liveTasks } from "@/lib/feeds";
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

  // #263 (Anand, 3 Nov 2026, 15:12 IST): the status bar's "checked" time counts the daily tasks only.
  describe("with the daily tasks as the checked set", () => {
    const live = ["policy.calendar", ...DAILY_TASKS];
    const daily = (at: string) => DAILY_TASKS.map((t) => row(t, "ok", at));

    it("lets a weekly task's old success stay out of the checked time", () => {
      const rows = [row("policy.calendar", "ok", "2026-10-20T01:11Z"), ...daily("2026-10-23T22:40Z")];
      expect(summarise(rows, live, NOW, DAILY_TASKS)).toEqual({
        state: "current",
        text: "All data current · checked 24 Oct, 04:10 IST",
        delayed: 0,
        stale: 0,
      });
      // without the checked set the old weekly success still sets it (the default, as before)
      expect(summarise(rows, live, NOW).text).toBe("All data current · checked 20 Oct, 06:41 IST");
    });

    it("shows the oldest of the daily tasks' last successes", () => {
      const rows = [
        row("policy.calendar", "ok", "2026-10-23T22:40Z"),
        ...DAILY_TASKS.map((t, i) => row(t, "ok", i === 2 ? "2026-10-22T10:00Z" : "2026-10-23T22:40Z")),
      ];
      expect(summarise(rows, live, NOW, DAILY_TASKS).text).toBe("All data current · checked 22 Oct, 15:30 IST");
    });

    it("still counts an overdue weekly task as stale, and a failed daily task as delayed", () => {
      const stale = [row("policy.calendar", "stale", "2026-09-01T01:11Z"), ...daily("2026-10-23T22:40Z")];
      expect(summarise(stale, live, NOW, DAILY_TASKS)).toEqual({ state: "behind", text: "1 source stale", delayed: 0, stale: 1 });
      const failed = [row("policy.calendar", "ok", "2026-10-20T01:11Z"), ...DAILY_TASKS.map((t) => row(t, t === "policy.market_series" ? "failed" : "ok", "2026-10-23T22:40Z"))];
      expect(summarise(failed, live, NOW, DAILY_TASKS).text).toBe("1 source delayed");
    });

    it("falls back to every task given when none of them is in the checked set", () => {
      expect(summarise([row("policy.calendar", "ok", "2026-10-20T01:11Z")], ["policy.calendar"], NOW, DAILY_TASKS).text).toBe(
        "All data current · checked 20 Oct, 06:41 IST",
      );
    });

    it("keeps the no-live-tasks answer", () => {
      expect(summarise([], [], NOW, DAILY_TASKS).text).toBe("All data current");
    });
  });
});

describe("feeds", () => {
  it("has the shell read the tape's and the countdown's tasks, each once", () => {
    expect(FEEDS.tickerTape).toEqual(["policy.market_series"]);
    expect(FEEDS.statusBarCountdown).toEqual(["policy.calendar"]);
    // /policy's panels (C3) bring in the document and AI tasks; the decision-day moves (Pass 1b, #70) bring in
    // policy.reactions, live once enabled in tasks.yaml.
    expect(FEEDS.policyReactions).toEqual(["policy.reactions"]);
    // Module 2's four surfaces joined FEEDS at 2A-26 (#132): their tasks count, each once, with policy.rate_cycles.
    // The value chain's two tasks joined on 12 Nov 2026 (VC-06, #191), once production's runs had left their freshness rows.
    expect(liveTasks()).toEqual([
      "equity.beta",
      "equity.brsr",
      "equity.metrics",
      "equity.prices",
      "equity.rate_reactions",
      "equity.related_parties",
      "equity.snapshot",
      "equity.statements",
      "equity.universe",
      "equity.valuation",
      "equity.valuation_inputs",
      "policy.ai_batch",
      "policy.calendar",
      "policy.fed_documents",
      "policy.market_series",
      "policy.rate_cycles",
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

describe("panelFreshness with checkedTasks (#440: /policy's header and \"Where rates stand\")", () => {
  // Test rows: the calendar last succeeded long ago (it runs every 25 days), the daily tasks last night.
  const tasks = ["policy.calendar", "policy.fed_documents", "policy.ai_batch"];
  const daily = ["policy.fed_documents", "policy.ai_batch"];
  const CAL = "2026-10-01T10:00Z";
  const DAY = "2026-10-23T22:40Z";
  const rows = [row("policy.calendar", "ok", CAL), row("policy.fed_documents", "ok", DAY), row("policy.ai_batch", "ok", DAY)];

  it("takes 'checked' from the daily tasks, not the calendar", () => {
    expect(panelFreshness(rows, tasks, "2026-09-17", NOW, daily)).toEqual({
      text: "Data to 17 Sep 2026 · checked 24 Oct, 04:10 IST",
      delayed: false,
    });
  });

  it("is unchanged without the argument (the calendar's old success is the oldest)", () => {
    expect(panelFreshness(rows, tasks, null, NOW).text).toBe("checked 1 Oct, 15:30 IST");
  });

  it("is still delayed when the calendar failed or went stale", () => {
    const failed = [row("policy.calendar", "failed", CAL), ...rows.slice(1)];
    expect(panelFreshness(failed, tasks, null, NOW, daily).delayed).toBe(true);
    const stale = [row("policy.calendar", "stale", CAL), ...rows.slice(1)];
    expect(panelFreshness(stale, tasks, null, NOW, daily).delayed).toBe(true);
  });

  it("counts every task when none of them is in the checked set", () => {
    expect(panelFreshness(rows, tasks, null, NOW, ["policy.market_series"]).text).toBe("checked 1 Oct, 15:30 IST");
  });

  it("shows no 'checked' part when a task has no success ever, as today", () => {
    const none = [row("policy.calendar", "ok", null), ...rows.slice(1)];
    const f = panelFreshness(none, tasks, "2026-09-17", NOW, daily);
    expect(f.text).toBe("Data to 17 Sep 2026");
    expect(f.delayed).toBe(true);
  });
});

describe("/policy passes the daily tasks as the checked set (#440)", () => {
  // The page is an async server component with live reads, so this reads its source, as meeting-view-layout.test.ts does.
  const src = readFileSync(join(__dirname, "../../app/policy/page.tsx"), "utf8");
  it("gives the header and the Where rates stand footer DAILY_TASKS", () => {
    expect(src).toContain("panelFreshness(freshness, allTasks, null, now, DAILY_TASKS)");
    expect(src).toMatch(/FEEDS\.policyRatesStand,[^\n]*, DAILY_TASKS\)/);
  });
});
