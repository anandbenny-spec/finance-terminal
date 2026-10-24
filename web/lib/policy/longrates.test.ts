import { describe, expect, it } from "vitest";
import type { ObservationRow } from "@/lib/queries/api";
import { IN_10Y, US_10Y, buildLongRates, readoutAt } from "./longrates";

const obs = (series_key: string, obs_date: string, value: number): ObservationRow => ({ series_key, obs_date, value, label: null });

const US = [obs(US_10Y, "2019-01-03", 2.56), obs(US_10Y, "2019-01-02", 2.66), obs(US_10Y, "2018-12-31", 2.69), obs(US_10Y, "2019-02-01", 2.7)];
const IN = [obs(IN_10Y, "2019-02-01", 7.5), obs(IN_10Y, "2019-01-01", 7.6)];

describe("buildLongRates", () => {
  const data = buildLongRates(US, IN, "2019-01-01");

  it("sorts by date, keeps only the range and never invents a point", () => {
    expect(data.us.map((p) => p.time)).toEqual(["2019-01-02", "2019-01-03", "2019-02-01"]);
    expect(data.india.map((p) => p.time)).toEqual(["2019-01-01", "2019-02-01"]);
    expect(data.usTo).toBe("2019-02-01");
    expect(data.indiaTo).toBe("2019-02-01");
  });

  it("makes a table of every date either series has, newest first, with nulls where one has none", () => {
    expect(data.rows).toEqual([
      { date: "2019-02-01", us: 2.7, india: 7.5 },
      { date: "2019-01-03", us: 2.56, india: null },
      { date: "2019-01-02", us: 2.66, india: null },
      { date: "2019-01-01", us: null, india: 7.6 },
    ]);
  });

  it("drops non-finite values and keeps the later row for a repeated date", () => {
    const d = buildLongRates([obs(US_10Y, "2019-03-01", Number.NaN), obs(US_10Y, "2019-03-04", 1), obs(US_10Y, "2019-03-04", 2)], [], "2019-01-01");
    expect(d.us).toEqual([{ time: "2019-03-04", value: 2 }]);
    expect(d.indiaTo).toBeNull();
  });

  it("is empty, not an error, when nothing is published", () => {
    const d = buildLongRates([], [], "2019-01-01");
    expect(d.rows).toEqual([]);
    expect(readoutAt(d, null)).toBeNull();
  });
});

describe("readoutAt", () => {
  const data = buildLongRates(US, IN, "2019-01-01");

  it("shows the latest US day and the month India's latest reading belongs to", () => {
    expect(readoutAt(data, null)).toEqual({ date: "2019-02-01", us: 2.7, india: { time: "2019-02-01", value: 7.5 } });
  });

  it("carries India's monthly reading across the month and shows no US value on a day without a close", () => {
    expect(readoutAt(data, "2019-01-03")).toEqual({ date: "2019-01-03", us: 2.56, india: { time: "2019-01-01", value: 7.6 } });
    expect(readoutAt(data, "2019-01-05")).toEqual({ date: "2019-01-05", us: null, india: { time: "2019-01-01", value: 7.6 } });
  });

  it("has no India value before its first month", () => {
    expect(readoutAt(data, "2018-12-31")?.india).toBeNull();
  });
});
