// web/lib/policy/show-latest.ts: how many meetings a long /policy panel shows before "Show all" (#575; Anand, 15 Nov 2026,
// 17:15 IST). Votes over time and How the wording moved drew every meeting since 2019 on load: about 6,850 of /policy's
// 14,900 elements on 15 Nov. They now draw the latest 12 (Design 01's "about 12 rows", #145) and the rest on request.
// Pure: the rows are newest first, as both panels' data already are; nothing is re-ordered, filtered or changed.

/** The meetings a panel shows before the reader asks for all of them. */
export const LATEST_MEETINGS = 12;

export type LatestMeetings<T> = {
  /** The rows to draw, newest first. */
  shown: readonly T[];
  /** Every row, for the button's count. */
  total: number;
  /** Whether some rows are held back until "Show all". */
  trimmed: boolean;
};

/** The newest `LATEST_MEETINGS` of `rows` (already newest first), or all of them when `all` is set. */
export function latestMeetings<T>(rows: readonly T[], all: boolean): LatestMeetings<T> {
  const trimmed = !all && rows.length > LATEST_MEETINGS;
  return { shown: trimmed ? rows.slice(0, LATEST_MEETINGS) : rows, total: rows.length, trimmed };
}

/** The button's words: "Show all 57 meetings" while trimmed, "Show the latest 12 only" once expanded. */
export function showAllLabel(total: number, trimmed: boolean, count: (n: number) => string): string {
  return trimmed ? `Show all ${count(total)} meetings` : `Show the latest ${count(LATEST_MEETINGS)} only`;
}
