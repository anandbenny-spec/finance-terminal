// ChangeStrip's bars (Pass 1b, #89; Design 01 §1.16 layer 1): shares of the stored word counts, a held-back bar when
// the stored numbers can't be a share, and each bar's link to its meeting's What changed tab. Test data shaped like
// the views, not real figures.
import { describe, expect, it } from "vitest";
import { barAt, buildChangeStrips, parseLengths, shares, stepBar, stripScale, stripSeries, stripTableRows, type StripDiff } from "./changestrip";

const decisions = [
  { slug: "fomc-2026-03-18", bank: "Fed" as const, decision_date: "2026-03-18" },
  { slug: "fomc-2026-01-28", bank: "Fed" as const, decision_date: "2026-01-28" },
  { slug: "mpc-2026-02-06", bank: "RBI" as const, decision_date: "2026-02-06" },
  { slug: "mpc-2026-04-08", bank: "RBI" as const, decision_date: "2026-04-08" },
];
const documents = [
  { id: 1, event_slug: "fomc-2026-01-28", version_no: 1 },
  { id: 2, event_slug: "fomc-2026-03-18", version_no: 1 },
  { id: 3, event_slug: "fomc-2026-03-18", version_no: 2 }, // a changed text is a new version: this one counts
  { id: 4, event_slug: "mpc-2026-02-06", version_no: 1 },
  { id: 5, event_slug: "mpc-2026-04-08", version_no: 1 },
  { id: 6, event_slug: null, version_no: 1 },
];
const diff = (document_id: number, words_added: number, words_removed: number, old: number, nw: number): StripDiff => ({
  document_id,
  words_added,
  words_removed,
  words: { old, new: nw },
});

describe("shares: a bar is a share of the statement, not a count", () => {
  it("divides the added words by the new text and the removed words by the old one", () => {
    expect(shares(diff(1, 50, 20, 400, 500))).toEqual({ addedPct: 10, removedPct: 5, wordsNew: 500, wordsOld: 400 });
  });

  it("holds back a diff whose stored numbers can't be a share", () => {
    expect(shares(diff(1, 10, 5, 0, 100))).toBeNull(); // a text of no words
    expect(shares(diff(1, 101, 5, 100, 100))).toBeNull(); // more words added than the new text has
    expect(shares(diff(1, 5, 101, 100, 100))).toBeNull(); // more words removed than the old text had
    expect(shares(diff(1, -1, 5, 100, 100))).toBeNull();
    expect(shares(diff(1, 1.5, 5, 100, 100))).toBeNull();
    expect(shares({ document_id: 1, words_added: 1, words_removed: 1, words: null })).toBeNull();
    expect(shares({ document_id: 1, words_added: 1, words_removed: 1, words: { old: "10", new: 10 } })).toBeNull();
  });

  it("reads the lengths only from two positive whole counts", () => {
    expect(parseLengths({ old: 3, new: 4 })).toEqual({ old: 3, new: 4 });
    expect(parseLengths({ old: 3 })).toBeNull();
    expect(parseLengths([3, 4])).toBeNull();
  });
});

describe("buildChangeStrips", () => {
  const diffs = [diff(1, 40, 20, 400, 400), diff(3, 100, 50, 400, 500), diff(4, 30, 30, 600, 600), diff(5, 90, 30, 600, 700)];
  const [fed, rbi] = buildChangeStrips(decisions, documents, diffs);

  it("lists each bank's meetings oldest first, on the latest version's diff", () => {
    expect(fed.bars.map((b) => [b.date, b.addedPct, b.removedPct])).toEqual([
      ["2026-01-28", 10, 5],
      ["2026-03-18", 20, 12.5],
    ]);
    expect(rbi.bars.map((b) => b.date)).toEqual(["2026-02-06", "2026-04-08"]);
    expect(fed.bars[1]).toMatchObject({ slug: "fomc-2026-03-18", wordsAdded: 100, wordsRemoved: 50, wordsNew: 500, wordsOld: 400 });
  });

  it("links each bar to its meeting's What changed tab", () => {
    expect(fed.bars[0].href).toBe("/policy/fed/2026-01-28?tab=changes");
    expect(rbi.bars[1].href).toBe("/policy/rbi/2026-04-08?tab=changes");
  });

  it("counts a meeting with no stored comparison instead of drawing a zero", () => {
    const [f] = buildChangeStrips(decisions, documents, [diff(1, 40, 20, 400, 400)]);
    expect(f.bars.map((b) => b.date)).toEqual(["2026-01-28"]);
    expect(f).toMatchObject({ withoutBar: 1, meetings: 2 });
  });

  it("counts a meeting whose diff is held back", () => {
    const [f] = buildChangeStrips(decisions, documents, [diff(1, 40, 20, 400, 400), diff(3, 900, 0, 400, 500)]);
    expect(f).toMatchObject({ withoutBar: 1, meetings: 2 });
  });

  it("gives an empty strip for a bank with no decisions", () => {
    const [f, r] = buildChangeStrips([], documents, diffs);
    expect([f.bars, r.bars, f.meetings]).toEqual([[], [], 0]);
  });
});

describe("the scale, the chart data and the table rows", () => {
  const strips = buildChangeStrips(decisions, documents, [diff(1, 40, 20, 400, 400), diff(3, 100, 50, 400, 500), diff(4, 30, 30, 600, 600), diff(5, 90, 30, 600, 700)]);

  it("is ±60% unless a bar needs more, then the next 20 up, and never past 100", () => {
    expect(stripScale(strips)).toBe(60);
    const wide = buildChangeStrips(decisions, documents, [diff(1, 280, 20, 400, 400)]);
    expect(stripScale(wide)).toBe(80);
    expect(stripScale(buildChangeStrips(decisions, documents, [diff(1, 400, 400, 400, 400)]))).toBe(100);
    expect(stripScale([])).toBe(60);
  });

  it("draws added words above the zero line and removed words below it", () => {
    expect(stripSeries(strips[0])).toEqual({
      added: [{ time: "2026-01-28", value: 10 }, { time: "2026-03-18", value: 20 }],
      removed: [{ time: "2026-01-28", value: -5 }, { time: "2026-03-18", value: -12.5 }],
    });
  });

  it("lists both banks' meetings newest first for the table", () => {
    expect(stripTableRows(strips).map((r) => `${r.bank} ${r.date}`)).toEqual(["RBI 2026-04-08", "Fed 2026-03-18", "RBI 2026-02-06", "Fed 2026-01-28"]);
  });

  it("finds a bar by date and steps to its neighbours, clamped at the ends", () => {
    const fed = strips[0];
    expect(barAt(fed, "2026-03-18")?.slug).toBe("fomc-2026-03-18");
    expect(barAt(fed, "2026-03-19")).toBeUndefined();
    expect(barAt(fed, null)).toBeUndefined();
    expect(stepBar(fed, null, -1)?.date).toBe("2026-03-18"); // nothing selected: the newest
    expect(stepBar(fed, "2026-03-18", -1)?.date).toBe("2026-01-28");
    expect(stepBar(fed, "2026-01-28", -1)?.date).toBe("2026-01-28");
    expect(stepBar(fed, "2026-03-18", 1)?.date).toBe("2026-03-18");
    expect(stepBar({ ...fed, bars: [] }, null, 1)).toBeUndefined();
  });
});
