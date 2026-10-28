// Where /policy/fed/latest and /policy/rbi/latest send a visitor (#147): the newest held meeting on the requested
// tab, a bad tab dropped, and the Latest decisions section when there is no meeting.
import { describe, expect, it } from "vitest";
import { LATEST_FALLBACK, latestTarget } from "./latest";

describe("latestTarget", () => {
  it("keeps a valid tab", () => {
    expect(latestTarget("Fed", "2026-09-16", "changes")).toBe("/policy/fed/2026-09-16?tab=changes");
    expect(latestTarget("RBI", "2026-10-07", "reaction")).toBe("/policy/rbi/2026-10-07?tab=reaction");
  });

  it("drops a bad tab, and anything that isn't one, landing on the Overview", () => {
    expect(latestTarget("Fed", "2026-09-16", "nonsense")).toBe("/policy/fed/2026-09-16");
    expect(latestTarget("Fed", "2026-09-16", "")).toBe("/policy/fed/2026-09-16");
    expect(latestTarget("RBI", "2026-10-07", "changes&doc=governor")).toBe("/policy/rbi/2026-10-07");
    expect(latestTarget("RBI", "2026-10-07", "https://evil.test/")).toBe("/policy/rbi/2026-10-07");
  });

  it("goes to the Overview with no tab", () => {
    expect(latestTarget("Fed", "2026-09-16", null)).toBe("/policy/fed/2026-09-16");
    expect(latestTarget("RBI", "2026-10-07", "overview")).toBe("/policy/rbi/2026-10-07");
  });

  it("goes to the Latest decisions section when no meeting has been held, whatever the tab", () => {
    expect(LATEST_FALLBACK).toBe("/policy#latest-decisions");
    expect(latestTarget("Fed", null, "changes")).toBe("/policy#latest-decisions");
    expect(latestTarget("RBI", null, null)).toBe("/policy#latest-decisions");
  });
});
