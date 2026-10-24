// The rates chart's data (C3): steps expanded to one point per day, each the value the database returned for the
// day it was in force; the RBI corridor's floor switches from the reverse repo to the SDF; the table lists changes.
import { describe, expect, it } from "vitest";
import type { ObservationStepRow, PolicyMeetingRow, PolicyRateSettingRow } from "@/lib/queries/api";
import { buildRateChart, calendarDays, dayAt, yearsBefore } from "./rates";

const step = (series_key: string, obs_date: string, value: number, is_latest = false): ObservationStepRow => ({ series_key, obs_date, value, is_latest });
const setting = (decision_date: string, instrument: PolicyRateSettingRow["instrument"], value_pct: number): PolicyRateSettingRow => ({
  meeting_slug: `mpc-${decision_date}`, bank: "RBI", decision_date, instrument, value_pct, change_bps: 0,
});

const FED = {
  upper: { seriesKey: "fred.DFEDTARU", steps: [step("fred.DFEDTARU", "2022-03-20", 0.25), step("fred.DFEDTARU", "2022-04-03", 0.5), step("fred.DFEDTARU", "2022-04-12", 0.5, true)] },
  lower: { seriesKey: "fred.DFEDTARL", steps: [step("fred.DFEDTARL", "2022-03-20", 0), step("fred.DFEDTARL", "2022-04-03", 0.25), step("fred.DFEDTARL", "2022-04-12", 0.25, true)] },
};
const RBI = [
  setting("2022-02-10", "repo", 4), setting("2022-02-10", "reverse_repo", 3.35), setting("2022-02-10", "msf", 4.25),
  setting("2022-04-08", "repo", 4), setting("2022-04-08", "sdf", 3.75), setting("2022-04-08", "msf", 4.25),
];
const DECISIONS = [
  { slug: "fomc-2022-03-16", bank: "Fed", decision_date: "2022-03-16", fed_target_upper_pct: 0.5, policy_rate_pct: 0.5 },
  { slug: "mpc-2022-04-08", bank: "RBI", decision_date: "2022-04-08", fed_target_upper_pct: null, policy_rate_pct: 4 },
] as PolicyMeetingRow[];

describe("buildRateChart", () => {
  const data = buildRateChart(FED, RBI, DECISIONS, "2022-04-01");

  it("has one point per calendar day from the range's first day to the last day the data runs to", () => {
    expect(data.days[0].date).toBe("2022-04-01");
    expect(data.days.at(-1)!.date).toBe("2022-04-12");
    expect(data.days).toHaveLength(12);
    expect(data.fedDataTo).toBe("2022-04-12");
    expect(data.rbiDataTo).toBe("2022-04-08");
  });

  it("carries each value from the day it took effect, and only values the database returned", () => {
    expect(dayAt(data.days, "2022-04-02")).toMatchObject({ fedLower: 0, fedUpper: 0.25, repo: 4, floor: 3.35, floorKind: "reverse_repo", msf: 4.25 });
    expect(dayAt(data.days, "2022-04-03")).toMatchObject({ fedLower: 0.25, fedUpper: 0.5 });
    expect(dayAt(data.days, "2022-04-08")).toMatchObject({ floor: 3.75, floorKind: "sdf" });
    expect(data.fedBand[0]).toEqual({ time: "2022-04-01", low: 0, high: 0.25 });
    expect(data.rbiRepo.every((p) => p.value === 4)).toBe(true);
  });

  it("draws the Fed band only to its latest observation", () => {
    const short = buildRateChart(
      { upper: { ...FED.upper, steps: [step("u", "2022-03-20", 0.25, true)] }, lower: { ...FED.lower, steps: [step("l", "2022-03-20", 0, true)] } },
      RBI,
      [],
      "2022-03-01",
    );
    expect(short.fedBand.map((p) => p.time)).toEqual(["2022-03-20"]);
    expect(short.days.at(-1)!.date).toBe("2022-04-08");
  });

  it("marks decisions in range at the rate they set", () => {
    expect(data.markers).toEqual([{ time: "2022-04-08", bank: "RBI", price: 4 }]);
  });

  it("lists the first day, each day any rate changed and the last day for the table", () => {
    expect(data.changes.map((d) => d.date)).toEqual(["2022-04-01", "2022-04-03", "2022-04-08", "2022-04-12"]);
  });

  it("is empty when nothing is published", () => {
    const empty = buildRateChart({ upper: { seriesKey: "u", steps: [] }, lower: { seriesKey: "l", steps: [] } }, [], [], "2019-01-01");
    expect(empty.days).toEqual([]);
    expect(dayAt(empty.days, null)).toBeNull();
  });
});

describe("dates", () => {
  it("counts calendar days and steps back whole years", () => {
    expect(calendarDays("2024-02-27", "2024-03-01")).toEqual(["2024-02-27", "2024-02-28", "2024-02-29", "2024-03-01"]);
    expect(yearsBefore("2026-10-24", 3)).toBe("2023-10-24");
    expect(yearsBefore("2024-02-29", 1)).toBe("2023-02-28");
  });
});
