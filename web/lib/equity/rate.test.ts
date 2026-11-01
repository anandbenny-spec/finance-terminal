// The rate-decision views' reading rules (2A-25; Spec 02A §5.10): which window, which stored rows, which groups have
// too few decisions for statistics, and how cycles are ordered. Nothing is computed from the rows.
import { describe, expect, it } from "vitest";
import { MIN_MOVES, bankLabel, cycleRows, decisionLabel, movesOf, rateSources, statRows, windowOf } from "@/lib/equity/rate";
import { cycle, cycleMove, fewStat, move, stat } from "@/tests/rate-rows";

describe("windowOf", () => {
  it("is one day unless five is asked for", () => {
    expect(windowOf(undefined)).toBe("d1");
    expect(windowOf("d5")).toBe("d5");
    expect(windowOf("d9")).toBe("d1");
    expect(windowOf(["d5"])).toBe("d1");
  });
});

describe("statRows", () => {
  it("keeps one window, India first and cut before hold before hike", () => {
    const rows = statRows([fewStat(), stat({ bank: "fed", decision: "cut" }), stat({ decision: "hold" }), stat({ decision: "cut", bank: "rbi" }), stat({ window_key: "d5" })], "d1");
    expect(rows.map((r) => `${r.bank}:${r.decision}`)).toEqual(["rbi:cut", "rbi:hold", "fed:cut", "fed:hike"]);
  });
  it("marks fewer than five decisions as moves-only, whatever the view returned", () => {
    expect(MIN_MOVES).toBe(5);
    const [ok, few] = statRows([fewStat(), stat({ n: 5 })], "d1"); // India first
    expect([ok.fewMoves, few.fewMoves]).toEqual([false, true]);
    expect(statRows([stat({ n: 4 })], "d1")[0].fewMoves).toBe(true);
    // a median the view left null counts as too few even if n says otherwise: never show a figure the view didn't return
    expect(statRows([stat({ n: 9, median_excess_pct: null })], "d1")[0].fewMoves).toBe(true);
  });
  it("drops rows with no count (held back)", () => {
    expect(statRows([stat({ n: null, median_excess_pct: null })], "d1")).toEqual([]);
  });
});

describe("movesOf", () => {
  it("lists one bank, decision and window newest first, skipping moves with no excess", () => {
    const rows = movesOf(
      [move(), move({ event_slug: "a", meeting_date: "2023-07-26" }), move({ window_key: "d5" }), move({ decision: "cut" }), move({ event_slug: "b", meeting_date: "2024-01-01", excess_pct: null })],
      "fed",
      "hike",
      "d1",
    );
    expect(rows.map((r) => r.meeting_date)).toEqual(["2023-07-26", "2022-06-15"]);
  });
});

describe("cycleRows", () => {
  it("joins the decision dates and counts, labels an ongoing cycle and orders India first, oldest first", () => {
    const rows = cycleRows(
      [cycleMove({ bank: "fed", cycle_slug: "fed-tightening-2022-03", direction: "tightening", is_ongoing: false }), cycleMove(), cycleMove({ cycle_slug: "rbi-tightening-2022-05", direction: "tightening", is_ongoing: false })],
      [cycle(), cycle({ bank: "fed", cycle_slug: "fed-tightening-2022-03", first_decision_on: "2022-03-16", last_decision_on: "2023-07-26", moves: 11 }), cycle({ cycle_slug: "rbi-tightening-2022-05", first_decision_on: "2022-05-04", last_decision_on: "2023-02-08", moves: 6 })],
    );
    expect(rows.map((r) => r.key)).toEqual(["rbi:rbi-tightening-2022-05", "rbi:rbi-easing-2025-02", "fed:fed-tightening-2022-03"]);
    expect(rows[1]).toMatchObject({ ongoing: true, moves: 4, firstOn: "2025-02-07" });
    expect(rows[0].ongoing).toBe(false);
  });
  it("drops a cycle with no stored move", () => {
    expect(cycleRows([cycleMove({ own_change_pct: null })], [cycle()])).toEqual([]);
  });
});

describe("words and sources", () => {
  it("names the banks and decisions in words, the RBI without a link", () => {
    expect(bankLabel("rbi")).toBe("RBI (MPC)");
    expect(bankLabel("fed")).toBe("Fed (FOMC)");
    expect(decisionLabel("hike")).toBe("Hike");
    expect(rateSources("Source: Yahoo Finance (^NSEI)")).toEqual(["Source: NSE", "Source: Yahoo Finance (^NSEI)", "Source: Federal Reserve Board", "Source: Reserve Bank of India"]);
    expect(rateSources(null)).toEqual(["Source: NSE", "Source: Federal Reserve Board", "Source: Reserve Bank of India"]);
  });
});
