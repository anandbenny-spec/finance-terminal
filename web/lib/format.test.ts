// lib/format.ts: every example in Design 01 §3 is a test here (C1's done-when check), one row each, in the
// table's order and with its "Kind" as the name. Edge cases follow: what a wrong input does, and that no output
// depends on the viewer's locale or zone.
import { afterEach, describe, expect, it } from "vitest";
import {
  EN_DASH,
  MINUS,
  NOT_APPLICABLE,
  NOT_AVAILABLE,
  NOT_AVAILABLE_LABEL,
  formatAxisChange,
  formatAxisDate,
  formatAxisNumber,
  formatAxisRange,
  formatBps,
  formatBpsChange,
  formatCheckedTime,
  formatCountdown,
  formatDate,
  formatIndexLevel,
  formatMoney,
  formatMonth,
  formatPctChange,
  formatProjection,
  formatRate,
  formatRateRange,
  formatRelativeTime,
  formatReleaseTime,
  formatShortDate,
  dateInZone,
  formatTableDate,
  formatTally,
  formatUsdInr,
  formatYield,
  formatYieldChange,
} from "./format";

// Design 01 §3, row by row: [Kind, what the formatter returned, the Output column].
const ROWS: [kind: string, actual: () => string, expected: string][] = [
  ["Policy rate", () => formatRate(5.5), "5.50%"],
  ["Fed target range", () => formatRateRange(4.0, 4.25), "4.00–4.25%"],
  ["Rate change", () => formatBpsChange(-25), "−25 bps"],
  ["Rate change (a rise)", () => formatBpsChange(25), "+25 bps"],
  ["Rate change (no change)", () => formatBpsChange(0), "0 bps"],
  ["Yield level", () => formatYield(4.0812), "4.08%"],
  ["Yield change", () => formatYieldChange(0.03), "+3 bps"],
  ["Index level, India", () => formatIndexLevel(25104.3, "IN"), "25,104.30"],
  ["Index level, India, ≥ 1 lakh", () => formatIndexLevel(123456.7, "IN"), "1,23,456.70"],
  ["Index level, US", () => formatIndexLevel(6812.4, "US"), "6,812.40"],
  ["Index or FX % change", () => formatPctChange(-0.4213), "−0.42%"],
  ["Index or FX % change (no change)", () => formatPctChange(0), "0.00%"],
  ["USD/INR", () => formatUsdInr(88.2731), "88.27"],
  ["Money (INR 12345 crore)", () => formatMoney(12345, "INR", { unit: "crore" }), "₹12,345 cr"],
  ["Projection range", () => formatProjection(3.5, 3.8), "3.5–3.8%"],
  ["Vote tally", () => formatTally(11, 1), "11–1"],
  ["Not available", () => formatRate(null), "—"],
  ["Doesn't apply", () => NOT_APPLICABLE, "N/A"],
  ["Date in prose and headers", () => formatDate("2026-10-01"), "1 Oct 2026"],
  ["Date in table columns", () => formatTableDate("2026-10-01"), "01 Oct 2026"],
  ["Month (monthly series)", () => formatMonth("2026-09"), "Sep 2026"],
  ["Release time, RBI", () => formatReleaseTime("2026-10-01T04:30Z", "Asia/Kolkata"), "10:00 IST"],
  ["Release time, Fed", () => formatReleaseTime("2026-09-17T18:00Z", "America/New_York"), "14:00 ET (23:30 IST)"],
  [
    "Release time, Fed, winter",
    () => formatReleaseTime("2026-12-09T19:00Z", "America/New_York"),
    "14:00 ET (00:30 IST, 10 Dec)",
  ],
  ["Checked time", () => formatCheckedTime("2026-10-20T01:11Z", "2026-10-23T12:00Z"), "20 Oct, 06:41 IST"],
  ["Relative time (hours)", () => formatRelativeTime("2026-10-20T01:11Z", "2026-10-20T04:20Z"), "3 h ago"],
  ["Relative time (days)", () => formatRelativeTime("2026-10-18T01:11Z", "2026-10-20T04:20Z"), "2 d ago"],
  // Design 01 §4.1's status bar: "Next FOMC 9 Dec · in 50 days", "Next MPC 3 Dec · in 44 days", seen on 20 Oct.
  ["Countdown (days)", () => formatCountdown("2026-12-09T19:00Z", "America/New_York", "2026-10-20T12:00Z"), "in 50 days"],
  ["Countdown (days, MPC)", () => formatCountdown("2026-12-03T04:30Z", "Asia/Kolkata", "2026-10-20T12:00Z"), "in 44 days"],
  ["Countdown (tomorrow)", () => formatCountdown("2026-09-17T18:00Z", "America/New_York", "2026-09-16T15:00Z"), "tomorrow"],
  [
    "Countdown (today)",
    () => formatCountdown("2026-09-17T18:00Z", "America/New_York", "2026-09-17T13:00Z"),
    "today, 14:00 ET (23:30 IST)",
  ],
];

describe("Design 01 §3: every example", () => {
  it.each(ROWS)("%s", (_kind, actual, expected) => {
    expect(actual()).toBe(expected);
  });

  it("uses the characters §3 names: a true minus, an en dash for ranges and tallies, an em dash for missing", () => {
    expect(MINUS).toBe("−");
    expect(EN_DASH).toBe("–");
    expect(NOT_AVAILABLE).toBe("—");
    expect(NOT_AVAILABLE_LABEL).toBe("Not available");
    expect(formatBpsChange(-25).codePointAt(0)).toBe(0x2212);
    expect(formatPctChange(-0.4213)).not.toContain("-");
    expect(formatRateRange(4, 4.25)).toContain("–");
    expect(formatTally(11, 1)).toContain("–");
  });
});

describe("numbers: signs and rounding", () => {
  it("never shows a sign on a value that rounds to zero", () => {
    expect(formatPctChange(-0.004)).toBe("0.00%");
    expect(formatPctChange(0.004)).toBe("0.00%");
    expect(formatYieldChange(-0.004)).toBe("0 bps");
    expect(formatRate(-0.001)).toBe("0.00%");
    expect(formatMoney(-0.4, "INR")).toBe("₹0");
  });

  it("signs rises with + and falls with a true minus", () => {
    expect(formatPctChange(0.4213)).toBe("+0.42%");
    expect(formatYieldChange(-0.125)).toBe("−13 bps");
    expect(formatYieldChange(0.1)).toBe("+10 bps");
    expect(formatRate(-0.1)).toBe("−0.10%");
    expect(formatIndexLevel(-1234.5, "US")).toBe("−1,234.50");
    expect(formatMoney(-12345, "INR", { unit: "crore" })).toBe("−₹12,345 cr");
  });

  it("groups by the figure's market, not the viewer's locale", () => {
    expect(formatIndexLevel(1234567.891, "IN")).toBe("12,34,567.89");
    expect(formatIndexLevel(1234567.891, "US")).toBe("1,234,567.89");
    expect(formatMoney(123456, "INR", { unit: "crore" })).toBe("₹1,23,456 cr");
    expect(formatMoney(1234567, "USD")).toBe("$1,234,567");
  });

  it("formats a single published projection and an RBI range as published", () => {
    expect(formatProjection(7)).toBe("7.0%");
    expect(formatProjection(7, 7)).toBe("7.0%");
    expect(formatProjection(7, null)).toBe("7.0%");
    expect(formatProjection(3.5, 3.8, 2)).toBe("3.50–3.80%");
  });

  it("shows — for every missing value", () => {
    for (const out of [
      formatRate(undefined),
      formatYield(null),
      formatRateRange(4, null),
      formatRateRange(undefined, 4.25),
      formatBpsChange(null),
      formatYieldChange(null),
      formatIndexLevel(null, "IN"),
      formatPctChange(undefined),
      formatUsdInr(null),
      formatMoney(null, "INR"),
      formatProjection(null, 3.8),
      formatDate(null),
      formatTableDate(undefined),
      formatMonth(null),
    ]) {
      expect(out).toBe(NOT_AVAILABLE);
    }
  });

  it("throws on a value that can't be right, instead of showing it", () => {
    expect(() => formatRate(Number.NaN)).toThrow(RangeError);
    expect(() => formatIndexLevel(Number.POSITIVE_INFINITY, "US")).toThrow(RangeError);
    expect(() => formatBpsChange(12.5)).toThrow(RangeError); // policy-rate changes are whole bps
    expect(() => formatRateRange(4.25, 4)).toThrow(RangeError);
    expect(() => formatProjection(3.8, 3.5)).toThrow(RangeError);
    expect(() => formatTally(11, -1)).toThrow(RangeError);
    expect(() => formatTally(1.5, 1)).toThrow(RangeError);
    expect(() => formatMoney(100, "USD", { unit: "crore" })).toThrow(RangeError);
  });
});

describe("dates", () => {
  it("names every month from the fixed table (never Intl's \"Sept\")", () => {
    const months = Array.from({ length: 12 }, (_, i) => formatMonth(`2026-${String(i + 1).padStart(2, "0")}`));
    expect(months).toEqual([
      "Jan 2026", "Feb 2026", "Mar 2026", "Apr 2026", "May 2026", "Jun 2026",
      "Jul 2026", "Aug 2026", "Sep 2026", "Oct 2026", "Nov 2026", "Dec 2026",
    ]);
    expect(formatDate("2026-09-17")).toBe("17 Sep 2026");
  });

  it("reads a monthly series' stored date as its month", () => {
    expect(formatMonth("2026-09-01")).toBe("Sep 2026");
  });

  it("zero-pads the day only in table columns", () => {
    expect(formatDate("2019-01-09")).toBe("9 Jan 2019");
    expect(formatTableDate("2019-01-09")).toBe("09 Jan 2019");
    expect(formatTableDate("2026-12-31")).toBe("31 Dec 2026");
  });

  it("rejects anything but a real YYYY-MM-DD date", () => {
    for (const bad of ["2026-02-30", "2026-13-01", "1 Oct 2026", "2026-10-01T00:00Z", "2026-9-1", ""]) {
      expect(() => formatDate(bad)).toThrow(RangeError);
    }
    expect(formatDate("2024-02-29")).toBe("29 Feb 2024");
  });
});

describe("instants", () => {
  it("handles US daylight saving through the IANA zone (\"ET\" for both EDT and EST)", () => {
    // 2026: EDT until 1 Nov. 14:00 EDT is 18:00Z; 14:00 EST is 19:00Z.
    expect(formatReleaseTime("2026-10-28T18:00Z", "America/New_York")).toBe("14:00 ET (23:30 IST)");
    expect(formatReleaseTime("2027-01-27T19:00Z", "America/New_York")).toBe("14:00 ET (00:30 IST, 28 Jan)");
  });

  it("accepts Postgres timestamptz output and Date objects", () => {
    expect(formatReleaseTime("2026-10-01T04:30:00+00:00", "Asia/Kolkata")).toBe("10:00 IST");
    expect(formatReleaseTime("2026-10-01T10:00:00+05:30", "Asia/Kolkata")).toBe("10:00 IST");
    expect(formatReleaseTime(new Date("2026-09-17T18:00:00.000Z"), "America/New_York")).toBe("14:00 ET (23:30 IST)");
  });

  it("refuses an instant without an offset (it would be read in the viewer's zone)", () => {
    expect(() => formatReleaseTime("2026-10-01T04:30", "Asia/Kolkata")).toThrow(RangeError);
    expect(() => formatReleaseTime("2026-10-01", "Asia/Kolkata")).toThrow(RangeError);
    expect(() => formatReleaseTime(new Date(Number.NaN), "Asia/Kolkata")).toThrow(RangeError);
  });

  it("adds the year to a checked time only when it isn't the current year in IST", () => {
    expect(formatCheckedTime("2025-10-20T01:11Z", "2026-10-23T12:00Z")).toBe("20 Oct 2025, 06:41 IST");
    // 31 Dec 2025 19:00Z is already 1 Jan 2026, 00:30 in IST: the current year there.
    expect(formatCheckedTime("2025-12-31T19:00Z", "2026-01-02T00:00Z")).toBe("1 Jan, 00:30 IST");
  });

  it("counts relative time down to minutes, and never into the future", () => {
    expect(formatRelativeTime("2026-10-20T04:08Z", "2026-10-20T04:20Z")).toBe("12 min ago");
    expect(formatRelativeTime("2026-10-20T04:19:30Z", "2026-10-20T04:20Z")).toBe("just now");
    expect(formatRelativeTime("2026-10-20T04:25Z", "2026-10-20T04:20Z")).toBe("just now");
    expect(formatRelativeTime("2026-10-19T04:21Z", "2026-10-20T04:20Z")).toBe("23 h ago");
  });

  it("counts countdown days in the event's own zone", () => {
    const fedWinter = "2026-12-09T19:00Z"; // 14:00 ET on 9 Dec is 00:30 IST on 10 Dec
    // 9 Dec, 09:30 IST is still 8 Dec (23:00) in New York, so the Fed decision is tomorrow there.
    expect(formatCountdown(fedWinter, "America/New_York", "2026-12-09T04:00Z")).toBe("tomorrow");
    // 9 Dec, 00:00 in New York (10:30 IST): decision day begins.
    expect(formatCountdown(fedWinter, "America/New_York", "2026-12-09T05:00Z")).toBe(
      "today, 14:00 ET (00:30 IST, 10 Dec)",
    );
    // 9 Dec, 06:00 in New York: decision day.
    expect(formatCountdown(fedWinter, "America/New_York", "2026-12-09T11:00Z")).toBe(
      "today, 14:00 ET (00:30 IST, 10 Dec)",
    );
    // After the release, still that day in New York: still "today".
    expect(formatCountdown(fedWinter, "America/New_York", "2026-12-09T22:00Z")).toBe(
      "today, 14:00 ET (00:30 IST, 10 Dec)",
    );
    expect(formatCountdown("2026-12-03T04:30Z", "Asia/Kolkata", "2026-12-02T18:29Z")).toBe("tomorrow");
    expect(formatCountdown("2026-12-03T04:30Z", "Asia/Kolkata", "2026-12-02T18:30Z")).toBe("today, 10:00 IST");
    expect(() => formatCountdown("2026-12-03T04:30Z", "Asia/Kolkata", "2026-12-04T00:00Z")).toThrow(RangeError);
  });

  it("drops a short date's year only when it's the current year in the event's zone (C2's status bar)", () => {
    expect(formatShortDate("2026-12-09", "2026-10-20T12:00Z", "America/New_York")).toBe("9 Dec");
    expect(formatShortDate("2027-01-27", "2026-12-10T12:00Z", "America/New_York")).toBe("27 Jan 2027");
    // 31 Dec 2026, 20:00 in New York is already 2027 in IST: the year is read in the zone asked for.
    expect(formatShortDate("2027-01-27", "2027-01-01T01:00Z", "America/New_York")).toBe("27 Jan 2027");
    expect(formatShortDate("2027-02-05", "2027-01-01T01:00Z", "Asia/Kolkata")).toBe("5 Feb");
    expect(() => formatShortDate("2026-13-01", "2026-10-20T12:00Z")).toThrow(RangeError);
  });

  it("gives an instant's calendar date in a zone, to compare with a stored date", () => {
    expect(dateInZone("2026-10-28T03:59Z", "America/New_York")).toBe("2026-10-27");
    expect(dateInZone("2026-10-28T04:00Z", "America/New_York")).toBe("2026-10-28");
    expect(dateInZone("2026-12-03T18:30Z", "Asia/Kolkata")).toBe("2026-12-04");
  });
});

describe("no output depends on the viewer's zone", () => {
  const original = process.env.TZ;
  afterEach(() => {
    process.env.TZ = original;
  });

  it.each(["Pacific/Kiritimati", "America/Los_Angeles", "Asia/Kolkata", "UTC"])("TZ=%s", (tz) => {
    process.env.TZ = tz;
    for (const [, actual, expected] of ROWS) expect(actual()).toBe(expected);
  });
});

describe("C3 additions: unsigned bps and chart axes", () => {
  it("writes a size in bps without a sign, for prose that names the direction", () => {
    expect(formatBps(25)).toBe("25 bps");
    expect(formatBps(100)).toBe("100 bps");
    expect(formatBps(null)).toBe("—");
    expect(() => formatBps(-25)).toThrow(RangeError);
    expect(() => formatBps(12.5)).toThrow(RangeError);
  });

  it("writes axis values with fixed decimals, a true minus and no grouping", () => {
    expect(formatAxisNumber(4.25)).toBe("4.25");
    expect(formatAxisNumber(0)).toBe("0.00");
    expect(formatAxisNumber(-0.5)).toBe("−0.50");
    expect(formatAxisNumber(1234.5, 1)).toBe("1234.5");
  });

  it("writes a signed change for a column whose header carries the unit", () => {
    expect(formatAxisChange(-25)).toBe("−25");
    expect(formatAxisChange(25)).toBe("+25");
    expect(formatAxisChange(0)).toBe("0");
    expect(formatAxisChange(null)).toBe("—");
  });

  it("writes a range for a column whose header carries the unit", () => {
    expect(formatAxisRange(4, 4.25)).toBe("4.00–4.25");
    expect(formatAxisRange(null, 4.25)).toBe("—");
    expect(() => formatAxisRange(4.5, 4.25)).toThrow(RangeError);
  });

  it("writes axis dates from the fixed month table", () => {
    expect(formatAxisDate("2024-09-18", "year")).toBe("2024");
    expect(formatAxisDate("2024-09-18", "month")).toBe("Sep");
    expect(formatAxisDate("2024-09-08", "day")).toBe("8 Sep");
    expect(() => formatAxisDate("2024-13-01", "month")).toThrow(RangeError);
  });
});
