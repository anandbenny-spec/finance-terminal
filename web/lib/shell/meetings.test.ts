// The status bar's countdowns and the decision-day switch flip at the right moments in IST and ET (C2's done-when;
// Design 01 §10.5). Days count in each bank's own zone: midnight in New York for the FOMC, in Kolkata for the MPC.
// The dates are the banks' published 2026 schedules (the FOMC's 28 Oct and 9 Dec decisions; the MPC's 4 Dec
// decision is a sample date).
import { describe, expect, it } from "vitest";
import type { NextMeetingRow } from "@/lib/queries/api";
import { countdownLines, initialLines, toUpcoming } from "./meetings";

const ROWS: NextMeetingRow[] = [
  { bank: "RBI", slug: "mpc-2026-12-04", decision_date: "2026-12-04", decision_at: "2026-12-04T04:30:00+00:00" },
  { bank: "Fed", slug: "fomc-2026-10-28", decision_date: "2026-10-28", decision_at: "2026-10-28T18:00:00+00:00" },
  { bank: "Fed", slug: "fomc-2026-12-09", decision_date: "2026-12-09", decision_at: "2026-12-09T19:00:00+00:00" },
];
const meetings = toUpcoming(ROWS);

function lineAt(now: string, bank: "FOMC" | "MPC") {
  return countdownLines(meetings, now).find((l) => l.bank === bank);
}

describe("toUpcoming", () => {
  it("reads the bank from the slug and sorts soonest first", () => {
    expect(meetings.map((m) => [m.bank, m.zone, m.decisionDate])).toEqual([
      ["FOMC", "America/New_York", "2026-10-28"],
      ["MPC", "Asia/Kolkata", "2026-12-04"],
      ["FOMC", "America/New_York", "2026-12-09"],
    ]);
  });

  it("ignores a row that isn't an FOMC or MPC meeting", () => {
    expect(toUpcoming([{ bank: "X", slug: "ipo-2026-11-01", decision_date: "2026-11-01", decision_at: null }])).toEqual([]);
  });
});

describe("initialLines (what the server sends)", () => {
  it("names each bank's next decision date, without a countdown (the server can't know the visitor's clock)", () => {
    expect(initialLines(meetings)).toEqual([
      { bank: "FOMC", text: "Next FOMC 28 Oct 2026", today: false },
      { bank: "MPC", text: "Next MPC 4 Dec 2026", today: false },
    ]);
  });
});

describe("countdownLines", () => {
  it("counts whole days and shows the date without its year in the current year", () => {
    expect(countdownLines(meetings, "2026-10-20T12:00Z")).toEqual([
      { bank: "FOMC", text: "Next FOMC 28 Oct · in 8 days", today: false },
      { bank: "MPC", text: "Next MPC 4 Dec · in 45 days", today: false },
    ]);
  });

  it("switches the FOMC to today at midnight in New York (09:30 IST in October)", () => {
    // 28 Oct 09:29 IST = 27 Oct 23:59 EDT
    expect(lineAt("2026-10-28T03:59Z", "FOMC")).toEqual({ bank: "FOMC", text: "Next FOMC 28 Oct · tomorrow", today: false });
    // 28 Oct 09:30 IST = 28 Oct 00:00 EDT
    expect(lineAt("2026-10-28T04:00Z", "FOMC")).toEqual({
      bank: "FOMC",
      text: "FOMC decision today, 14:00 ET (23:30 IST)",
      today: true,
    });
  });

  it("keeps the FOMC's day through its release, and moves on at the next midnight in New York", () => {
    // 23:30 IST, the release; then 23:59 EDT (09:29 IST the next morning), still the decision day in New York
    expect(lineAt("2026-10-28T18:00Z", "FOMC")?.today).toBe(true);
    expect(lineAt("2026-10-29T03:59Z", "FOMC")?.today).toBe(true);
    // 29 Oct 00:00 EDT: the meeting's day has passed; still listed (its decision not stored yet), so it's passed over
    expect(lineAt("2026-10-29T04:00Z", "FOMC")).toEqual({ bank: "FOMC", text: "Next FOMC 9 Dec · in 41 days", today: false });
  });

  it("handles the winter decision whose IST release falls on the next day (14:00 EST = 00:30 IST)", () => {
    // 9 Dec 10:29 IST = 8 Dec 23:59 EST
    expect(lineAt("2026-12-09T04:59Z", "FOMC")?.text).toBe("Next FOMC 9 Dec · tomorrow");
    // 9 Dec 10:30 IST = 9 Dec 00:00 EST
    expect(lineAt("2026-12-09T05:00Z", "FOMC")).toEqual({
      bank: "FOMC",
      text: "FOMC decision today, 14:00 ET (00:30 IST, 10 Dec)",
      today: true,
    });
  });

  it("switches the MPC to today at midnight in Kolkata", () => {
    // 3 Dec 23:59 IST
    expect(lineAt("2026-12-03T18:29Z", "MPC")).toEqual({ bank: "MPC", text: "Next MPC 4 Dec · tomorrow", today: false });
    // 4 Dec 00:00 IST
    expect(lineAt("2026-12-03T18:30Z", "MPC")).toEqual({ bank: "MPC", text: "MPC decision today, 10:00 IST", today: true });
    // 5 Dec 00:00 IST: past, and no later MPC listed, so no MPC line (absent, not a wrong date)
    expect(lineAt("2026-12-04T18:30Z", "MPC")).toBeUndefined();
  });

  it("adds the year to a date in the next year", () => {
    const next = toUpcoming([{ bank: "Fed", slug: "fomc-2027-01-27", decision_date: "2027-01-27", decision_at: "2027-01-27T19:00:00+00:00" }]);
    expect(countdownLines(next, "2026-12-10T12:00Z")).toEqual([
      { bank: "FOMC", text: "Next FOMC 27 Jan 2027 · in 48 days", today: false },
    ]);
  });

  it("shows only the date when the meeting has no release time", () => {
    const dateOnly = toUpcoming([{ bank: "RBI", slug: "mpc-2027-02-05", decision_date: "2027-02-05", decision_at: null }]);
    expect(countdownLines(dateOnly, "2027-01-10T12:00Z")).toEqual([{ bank: "MPC", text: "Next MPC 5 Feb", today: false }]);
  });

  it("passes over a meeting whose date and release time disagree, rather than counting down to either", () => {
    const odd = toUpcoming([
      { bank: "Fed", slug: "fomc-2026-10-28", decision_date: "2026-10-28", decision_at: "2026-10-27T18:00:00+00:00" },
      { bank: "Fed", slug: "fomc-2026-12-09", decision_date: "2026-12-09", decision_at: "2026-12-09T19:00:00+00:00" },
    ]);
    expect(countdownLines(odd, "2026-10-20T12:00Z")).toEqual([{ bank: "FOMC", text: "Next FOMC 9 Dec · in 50 days", today: false }]);
  });

  it("shows nothing for a bank with no scheduled meeting", () => {
    expect(countdownLines([], "2026-10-20T12:00Z")).toEqual([]);
  });
});
