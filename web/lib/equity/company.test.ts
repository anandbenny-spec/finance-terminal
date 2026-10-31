// The company page's selection rules (2A-19): which tab and basis the URL names, which stored rows each panel shows,
// when the day's change and the earlier prices are held back, and how corporate actions are grouped.
import { describe, expect, it } from "vitest";
import {
  basisOf, chooseBasis, dayChange, metricByPeriod, periodsOf, PERIODS, pickMetric, priceData, sourceLineFor, splitActions, tabOf,
} from "@/lib/equity/company";
import { action, company, financial, metric, price } from "@/tests/company-rows";

describe("tab and basis", () => {
  it("reads the tab and basis from the URL, defaulting to Overview and consolidated", () => {
    expect(tabOf(undefined)).toBe("overview");
    expect(tabOf("financials")).toBe("financials");
    expect(tabOf("valuation")).toBe("valuation");
    expect(tabOf("rates")).toBe("overview");
    expect(tabOf(["financials", "x"])).toBe("overview");
    expect(basisOf("standalone")).toBe("standalone");
    expect(basisOf("x")).toBe("consolidated");
  });
  it("names the source from the stored key, NSE when it is none we know", () => {
    expect(sourceLineFor("nse")).toBe("Source: NSE");
    expect(sourceLineFor("bse")).toBe("Source: BSE");
    expect(sourceLineFor(null)).toBe("Source: NSE");
  });
});

describe("priceData", () => {
  it("draws the adjusted closes oldest first and names the date earlier prices are held back to", () => {
    const d = priceData([price("2026-10-02", 110, 110), price("2026-09-29", 100, null), price("2026-09-30", 101, null), price("2026-10-01", 105, 105)]);
    expect(d.points.map((p) => p.time)).toEqual(["2026-10-01", "2026-10-02"]);
    expect(d.heldTo).toBe("2026-09-30");
    expect(d.rows[0].trade_date).toBe("2026-10-02"); // the table is newest first
    expect(d.from).toBe("2026-10-01");
    expect(d.to).toBe("2026-10-02");
  });
  it("has no held date when every price is adjusted, and no points when none is", () => {
    expect(priceData([price("2026-10-01", 1, 1)]).heldTo).toBeNull();
    expect(priceData([]).points).toEqual([]);
  });
});

describe("pickMetric", () => {
  it("takes the preferred period kind, the latest period end, and consolidated before standalone", () => {
    const rows = [
      metric("ebit_margin", { period_kind: "year", period_end: "2026-03-31", value: 0.2 }),
      metric("ebit_margin", { period_kind: "ttm", period_end: "2026-06-30", value: 0.25, basis: "standalone" }),
      metric("ebit_margin", { period_kind: "ttm", period_end: "2026-06-30", value: 0.26, basis: "consolidated" }),
      metric("ebit_margin", { period_kind: "ttm", period_end: "2026-03-31", value: 0.24 }),
    ];
    expect(pickMetric(rows, "ebit_margin", ["ttm", "year"])?.value).toBe(0.26);
    expect(pickMetric(rows, "ebit_margin", ["year"])?.value).toBe(0.2);
    expect(pickMetric(rows, "net_margin", ["ttm"])).toBeNull();
  });
  it("keys a bank metric's rows by period end for one basis", () => {
    const rows = [metric("roa", { period_kind: "quarter", basis: "standalone", period_end: "2026-06-30" }), metric("roa", { period_kind: "quarter", basis: "consolidated", period_end: "2026-06-30" })];
    expect(metricByPeriod(rows, "roa", "quarter", "standalone").size).toBe(1);
  });
});

describe("dayChange", () => {
  it("is the ratio of the two closes the view returns", () => {
    const d = dayChange(company(), []);
    expect(d.kind).toBe("value");
    if (d.kind === "value") expect(d.ratio).toBeCloseTo(3456.7 / 3400 - 1, 12);
  });
  it("is held when a split, bonus or consolidation went ex between the two sessions, but not for a dividend or an earlier split", () => {
    expect(dayChange(company(), [action({ kind: "split", ex_date: "2026-10-30" })]).kind).toBe("held");
    expect(dayChange(company(), [action({ kind: "bonus", ex_date: "2026-10-30" })]).kind).toBe("held");
    expect(dayChange(company(), [action({ kind: "dividend", ex_date: "2026-10-30" })]).kind).toBe("value");
    expect(dayChange(company(), [action({ kind: "split", ex_date: "2026-10-29" })]).kind).toBe("value");
  });
  it("is absent without two closes", () => {
    expect(dayChange(company({ previous_close: null }), []).kind).toBe("none");
    expect(dayChange(company({ last_close_date: null }), []).kind).toBe("none");
  });
});

describe("periods and basis", () => {
  const rows = [
    ...["2025-12-31", "2026-03-31", "2026-06-30", "2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30", "2024-06-30"].map((e) => financial("quarter", e)),
    financial("quarter", "2026-06-30", { basis: "standalone" }),
    financial("half_year", "2025-09-30", { cfo: 5 }),
    financial("year", "2026-03-31", { cfo: 9 }),
    financial("year", "2025-03-31", { cfo: null }),
  ];
  it("takes the newest eight quarters on the basis, newest first", () => {
    const q = periodsOf(rows, "consolidated", PERIODS.quarterly);
    expect(q).toHaveLength(8);
    expect(q[0].period_end).toBe("2026-06-30");
    expect(q.every((r) => r.basis === "consolidated")).toBe(true);
  });
  it("keeps only the half-years and years that carry a cash flow", () => {
    expect(periodsOf(rows, "consolidated", PERIODS.cashFlow, "cfo").map((r) => r.period_end)).toEqual(["2026-03-31", "2025-09-30"]);
  });
  it("falls back to the basis that has rows, and says so", () => {
    const only = [financial("quarter", "2026-06-30", { basis: "standalone" })];
    expect(chooseBasis(only, "consolidated")).toEqual({ basis: "standalone", available: ["standalone"], fellBack: true });
    expect(chooseBasis(rows, "standalone")).toMatchObject({ basis: "standalone", fellBack: false });
    expect(chooseBasis([], "consolidated")).toMatchObject({ basis: "consolidated", fellBack: false });
  });
});

describe("splitActions", () => {
  it("puts announced actions with an ex-date ahead apart from the rest, and drops withdrawn ones", () => {
    const list = [
      action({ status: "announced", ex_date: "2026-11-20", subject: "Dividend A" }),
      action({ status: "announced", ex_date: "2026-10-01", subject: "Dividend B" }),
      action({ status: "effective", ex_date: "2026-07-01", subject: "Dividend C" }),
      action({ status: "withdrawn", ex_date: "2026-12-01", subject: "Dividend D" }),
    ];
    const { upcoming, past } = splitActions(list, "2026-10-31");
    expect(upcoming.map((a) => a.subject)).toEqual(["Dividend A"]);
    expect(past.map((a) => a.subject)).toEqual(["Dividend B", "Dividend C"]);
  });
});
