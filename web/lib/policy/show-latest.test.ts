// How many meetings a long /policy panel draws before "Show all" (#575).
import { describe, expect, it } from "vitest";
import { LATEST_MEETINGS, latestMeetings, showAllLabel } from "./show-latest";

const rows = (n: number) => Array.from({ length: n }, (_, i) => `m${i}`); // newest first, as the panels' data are

describe("latestMeetings", () => {
  it("keeps the newest 12 of a longer list, in their order, and says how many there are", () => {
    expect(LATEST_MEETINGS).toBe(12);
    const r = latestMeetings(rows(57), false);
    expect(r.shown).toEqual(rows(12));
    expect(r.total).toBe(57);
    expect(r.trimmed).toBe(true);
  });

  it("returns every row, unchanged, when asked for all of them or when there are 12 or fewer", () => {
    const all = rows(57);
    expect(latestMeetings(all, true)).toEqual({ shown: all, total: 57, trimmed: false });
    expect(latestMeetings(rows(12), false)).toEqual({ shown: rows(12), total: 12, trimmed: false });
    expect(latestMeetings([], false)).toEqual({ shown: [], total: 0, trimmed: false });
  });
});

describe("showAllLabel", () => {
  it("offers the full count while trimmed and the way back once expanded", () => {
    expect(showAllLabel(57, true, String)).toBe("Show all 57 meetings");
    expect(showAllLabel(57, false, String)).toBe("Show the latest 12 only");
  });
});
