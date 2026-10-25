// The meeting page's Market reaction tab model (Pass 1b, #75; Spec 01 §4.3, §4.5): the table's cells, the ±10-session
// window around the decision, the surprise-measure heading, "waiting" vs "not available". Test rows, not real figures.
import { describe, expect, it } from "vitest";
import { READ_DAYS, SESSIONS, SURPRISE_TITLE, meetingReaction, readRange, shiftDate, windowAround } from "@/lib/policy/reaction";
import { REACTION_SERIES, WAITING } from "@/lib/policy/reactions";
import type { MarketReactionRow, ObservationRow, SeriesRow } from "@/lib/queries/api";

const reaction = (key: string, pre: string, post: string, preV: number, postV: number, change: number, unit: "bps" | "pct", name = key): MarketReactionRow => ({
  meeting_slug: "fomc-2024-09-18", series_key: key, series_name: name, pre_obs_date: pre, post_obs_date: post, pre_value: preV,
  post_value: postV, change, change_unit: unit, is_surprise_measure: key === "fred.DGS2", attribution: `Source: ${key}`,
});
const series = (keys: readonly string[]): SeriesRow[] => keys.map((key) => ({ key, name: key, market: null, attribution: `Source: ${key}` }));
const ALL = REACTION_SERIES.map((s) => s.key);

/** Consecutive days from `start` (calendar days: the model counts observations, not weekdays). */
const days = (key: string, start: string, n: number): ObservationRow[] =>
  Array.from({ length: n }, (_, i) => ({ series_key: key, obs_date: shiftDate(start, i), value: 100 + i, label: null }));

describe("windowAround", () => {
  const obs = days("x", "2024-08-01", 60); // 1 Aug … 29 Sep
  it("keeps 10 sessions up to the before value and 10 from the after value on", () => {
    const win = windowAround(obs, "2024-09-17", "2024-09-18")!;
    expect(win).toHaveLength(2 * SESSIONS);
    expect(win[SESSIONS - 1].obs_date).toBe("2024-09-17");
    expect(win[SESSIONS].obs_date).toBe("2024-09-18");
    expect(win[0].obs_date).toBe("2024-09-08");
    expect(win.at(-1)!.obs_date).toBe("2024-09-27");
  });
  it("stops at the data's edges", () => {
    const win = windowAround(obs, "2024-08-03", "2024-08-04")!;
    expect(win[0].obs_date).toBe("2024-08-01");
    expect(windowAround(obs.slice(0, 12), "2024-08-05", "2024-08-06")!.at(-1)!.obs_date).toBe("2024-08-12");
  });
  it("is null when the stored dates aren't among the observations, or aren't in order", () => {
    expect(windowAround(obs, "2023-01-01", "2023-01-02")).toBeNull();
    expect(windowAround(obs, "2024-09-18", "2024-09-17")).toBeNull();
    expect(windowAround([], "2024-09-17", "2024-09-18")).toBeNull();
  });
  it("sorts first", () => {
    expect(windowAround([...obs].reverse(), "2024-09-17", "2024-09-18")).toHaveLength(2 * SESSIONS);
  });
});

describe("readRange", () => {
  it("widens both dates by the read margin", () => {
    expect(readRange({ pre_obs_date: "2024-09-17", post_obs_date: "2024-09-18" })).toEqual({
      from: shiftDate("2024-09-17", -READ_DAYS),
      to: shiftDate("2024-09-18", READ_DAYS),
    });
    expect(shiftDate("2024-03-01", -1)).toBe("2024-02-29");
  });
});

describe("meetingReaction", () => {
  const base = { decisionDate: "2024-09-18", today: "2024-09-20" };
  const reactions = [
    reaction("fred.DGS2", "2024-09-17", "2024-09-18", 3.61, 3.52, -9, "bps", "US 2-year Treasury yield"),
    reaction("yf.^NSEI", "2024-09-17", "2024-09-18", 25418.55, 25415.8, -0.0108, "pct", "Nifty 50"),
  ];
  const observations = { "fred.DGS2": days("fred.DGS2", "2024-08-20", 50), "yf.^NSEI": days("yf.^NSEI", "2024-08-20", 50) };

  it("lists the Fed's six series in table order, the surprise measure first and headed as such", () => {
    const m = meetingReaction({ ...base, bank: "Fed", reactions, series: series(ALL), observations });
    expect(m.entries.map((e) => e.key)).toEqual(ALL);
    expect(m.entries[0].title).toBe(SURPRISE_TITLE);
    expect(m.entries[0].cell).toMatchObject({ state: "move", text: "−9", surprise: true });
    expect(m.entries[0].before).toBe("17 Sep 2024 3.61%");
    expect(m.entries[0].after).toBe("18 Sep 2024 3.52%");
    expect(m.entries[4].title).toBe("Nifty 50");
  });

  it("gives a chart only to a series with a stored move, marked at the first value after the decision", () => {
    const m = meetingReaction({ ...base, bank: "Fed", reactions, series: series(ALL), observations });
    const charts = m.entries.filter((e) => e.chart).map((e) => e.key);
    expect(charts).toEqual(["fred.DGS2", "yf.^NSEI"]);
    const c = m.entries[0].chart!;
    expect(c.data).toHaveLength(2 * SESSIONS);
    expect(c.marker).toEqual({ time: "2024-09-18", price: 3.52 });
    expect(c.table.filter((r) => r.side === "before")).toHaveLength(SESSIONS);
    expect(c.entity).toBe("US");
    expect(m.entries[4].chart!.entity).toBe("IN");
  });

  it("uses only the RBI's three series, coloured by the deciding bank for the currency pair", () => {
    const r = [reaction("fred.DEXINUS", "2024-10-08", "2024-10-09", 83.9, 83.8, -0.12, "pct")];
    const m = meetingReaction({
      bank: "RBI", decisionDate: "2024-10-09", today: "2024-10-10", reactions: r, series: series(ALL),
      observations: { "fred.DEXINUS": days("fred.DEXINUS", "2024-09-15", 40) },
    });
    expect(m.entries.map((e) => e.key)).toEqual(["fred.DEXINUS", "yf.^NSEI", "yf.^NSEBANK"]);
    expect(m.entries[0].chart!.entity).toBe("IN");
    expect(m.entries[0].title).toBe("fred.DEXINUS");
  });

  it("waits for a recent decision with no stored move, and calls an old one not available", () => {
    const recent = meetingReaction({ ...base, bank: "Fed", reactions: [], series: series(ALL), observations: {} });
    expect(recent.entries.every((e) => e.cell.state === "waiting" && e.chart === null)).toBe(true);
    expect(recent.entries[0].cell).toMatchObject({ detail: WAITING });
    const old = meetingReaction({ ...base, today: "2024-10-30", bank: "Fed", reactions: [], series: series(ALL), observations: {} });
    expect(old.entries[0].cell).toEqual({ state: "unavailable", text: "—", detail: "Not available." });
  });

  it("shows a series api.series doesn't return as not available, with no source line", () => {
    const m = meetingReaction({ ...base, bank: "Fed", reactions, series: series(["yf.^NSEI"]), observations });
    expect(m.entries[0].cell).toMatchObject({ state: "unavailable" });
    expect(m.entries[0].chart).toBeNull();
    expect(m.sources).toEqual([null, null, null, null, "Source: yf.^NSEI", null]);
  });

  it("notes a move whose window couldn't be read, still showing the move", () => {
    const m = meetingReaction({ ...base, bank: "Fed", reactions, series: series(ALL), observations: {} });
    expect(m.entries[0].cell).toMatchObject({ state: "move" });
    expect(m.entries[0].chart).toBeNull();
    expect(m.entries[0].chartNote).toMatch(/couldn't be loaded/);
  });

  it("reports the latest 'after' date as the data's end", () => {
    const m = meetingReaction({ ...base, bank: "Fed", reactions, series: series(ALL), observations });
    expect(m.dataTo).toBe("2024-09-18");
    expect(meetingReaction({ ...base, bank: "Fed", reactions: [], series: series(ALL), observations: {} }).dataTo).toBeNull();
  });
});
