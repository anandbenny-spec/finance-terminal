import { describe, expect, it } from "vitest";
import { formatTableDate } from "@/lib/format";
import type { EvalSummaryRow, QualitySummaryRow } from "@/lib/queries/api";
import { countOf, dataTo, evalRunLines, moduleLabel, moduleSections, onTimeShare, parsePeriod, periodHref } from "./summary";

const row = (module: string, period: QualitySummaryRow["period"], measure: string, value: number, n: number | null = null): QualitySummaryRow => ({
  module,
  period,
  measure,
  value,
  n,
  label: measure === "freshness_on_time_share" ? "derived" : null,
  as_of: "2026-10-30T02:15:00+00:00",
});

describe("parsePeriod", () => {
  it("accepts the three periods and falls back to 30 days for anything else", () => {
    expect(parsePeriod("90d")).toBe("90d");
    expect(parsePeriod("all")).toBe("all");
    expect(parsePeriod("30d")).toBe("30d");
    expect(parsePeriod(undefined)).toBe("30d");
    expect(parsePeriod("7d")).toBe("30d");
    expect(parsePeriod(["90d", "all"])).toBe("30d");
  });
  it("builds the tab's link", () => expect(periodHref("90d")).toBe("/accuracy?period=90d"));
});

describe("moduleSections", () => {
  const rows = [
    row("equity", "30d", "loads", 4),
    row("policy", "30d", "loads", 12),
    row("policy", "90d", "loads", 30),
    row("crypto_x", "30d", "loads", 1),
  ];
  it("keeps only the period's rows, module 1 first and the rest by name", () => {
    expect(moduleSections(rows, "30d").map((s) => s.module)).toEqual(["policy", "crypto_x", "equity"]);
    expect(moduleSections(rows, "90d").map((s) => s.module)).toEqual(["policy"]);
    expect(moduleSections(rows, "all")).toEqual([]);
  });
  it("names modules in the site's words, and an unknown one in plain words", () => {
    expect(moduleLabel("policy")).toBe("Fed vs RBI policy tracker");
    expect(moduleLabel("crypto_x")).toBe("Crypto x");
  });
  it("shows a count grouped, and a measure with no row as not available, never 0", () => {
    const [s] = moduleSections([row("policy", "all", "loads", 1234)], "all");
    expect(countOf(s, "loads")).toBe("1,234");
    expect(countOf(s, "loads_failed")).toBe("—");
  });
});

describe("onTimeShare", () => {
  it("shows the stored share as a percentage with the task-days it is out of", () => {
    const [s] = moduleSections([row("policy", "30d", "freshness_on_time_share", 0.9873, 1200)], "30d");
    expect(onTimeShare(s)).toEqual({ value: "98.7%", context: "out of 1,200 task-days" });
  });
  it("is not available without a row", () => {
    const [s] = moduleSections([row("policy", "30d", "loads", 1)], "30d");
    expect(onTimeShare(s)).toEqual({ value: "—", context: null });
  });
});

describe("dataTo", () => {
  it("is the latest as_of's date, or null without rows", () => {
    expect(dataTo([row("policy", "30d", "loads", 1), { ...row("policy", "all", "loads", 1), as_of: "2026-10-31T01:00:00+00:00" }])).toBe("2026-10-31");
    expect(dataTo([])).toBeNull();
  });
});

describe("evalRunLines", () => {
  const evals: EvalSummaryRow[] = [
    { eval_set: "policy_stance", model: "m", metric: "accuracy", value: 0.9333, n_items: 60, run_at: "2026-10-20T10:00:00+00:00" },
    { eval_set: "retrieval", model: "m", metric: "recall_at_8", value: 0.91, n_items: 40, run_at: "2026-10-25T10:00:00+00:00" },
  ];
  it("lists the newest run first, in plain words, with formatted cells", () => {
    expect(evalRunLines(evals, formatTableDate)).toEqual([
      { key: "retrieval|m|recall_at_8", evalSet: "Retrieval", metric: "Recall at 8", value: "0.91", items: "40", date: "25 Oct 2026" },
      { key: "policy_stance|m|accuracy", evalSet: "Policy stance", metric: "Accuracy", value: "0.93", items: "60", date: "20 Oct 2026" },
    ]);
  });
});
