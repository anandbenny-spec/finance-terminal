// The decision-day moves table's rows (Pass 1b, #70; Spec 01 §4.2): one row per decision, one cell per reaction
// series, the waiting copy (§4.5) and "not available" for a series api.series doesn't return. Test rows, not real
// figures.
import { describe, expect, it } from "vitest";
import type { HistoryRow } from "@/lib/policy/decisions";
import { REACTION_SERIES, WAITING, reactionRows, reactionSources } from "@/lib/policy/reactions";
import type { MarketReactionRow, SeriesRow } from "@/lib/queries/api";

const decision = (slug: string, bank: "Fed" | "RBI", date: string): HistoryRow => ({
  slug, href: `/policy/${bank.toLowerCase()}/${date}`, date, year: date.slice(0, 4), bank, kind: "Scheduled", direction: "cut",
  decision: "Cut", rateAfter: "", change: "", changeBps: -25, vote: "", voteNote: null, dissents: 0, stance: null,
});
const reaction = (meeting_slug: string, series_key: string, pre: number, post: number, change: number, unit: "bps" | "pct", surprise = false): MarketReactionRow => ({
  meeting_slug, series_key, series_name: series_key, pre_obs_date: "2024-09-17", post_obs_date: "2024-09-18", pre_value: pre,
  post_value: post, change, change_unit: unit, is_surprise_measure: surprise, attribution: `Source: ${series_key}`,
});
const series = (keys: readonly string[]): SeriesRow[] => keys.map((key) => ({ key, name: key, market: null, attribution: `Source: ${key}` }));
const ALL = REACTION_SERIES.map((s) => s.key);

describe("reactionRows", () => {
  const fed = decision("fomc-2024-09-18", "Fed", "2024-09-18");
  const rbi = decision("mpc-2024-10-09", "RBI", "2024-10-09");
  const reactions = [
    reaction("fomc-2024-09-18", "fred.DGS2", 3.61, 3.52, -9, "bps", true),
    reaction("fomc-2024-09-18", "yf.^GSPC", 5634.58, 5618.26, -0.2896, "pct"),
    reaction("fomc-2024-09-18", "yf.^NSEI", 25418.55, 25415.8, -0.0108, "pct"),
    reaction("mpc-2024-10-09", "yf.^NSEI", 25013.15, 24981.95, -0.1247, "pct"),
  ];

  it("gives each decision its moves, signed, with the two values behind each", () => {
    const [f, r] = reactionRows([fed, rbi], reactions, series(ALL));
    expect(f.cells["fred.DGS2"]).toEqual({ state: "move", text: "−9", change: -9, surprise: true, detail: "17 Sep 2024 3.61% → 18 Sep 2024 3.52%" });
    expect(f.cells["yf.^GSPC"]).toMatchObject({ state: "move", text: "−0.29", detail: "17 Sep 2024 5,634.58 → 18 Sep 2024 5,618.26" });
    expect(f.cells["yf.^NSEI"]).toMatchObject({ text: "−0.01", detail: "17 Sep 2024 25,418.55 → 18 Sep 2024 25,415.80" });
    expect(r.cells["yf.^NSEI"]).toMatchObject({ state: "move", text: "−0.12", surprise: false });
  });

  it("waits for a move not stored yet, and leaves the US series empty at RBI decisions", () => {
    const [f, r] = reactionRows([fed, rbi], reactions, series(ALL));
    expect(f.cells["fred.DEXINUS"]).toEqual({ state: "waiting", text: "—", detail: WAITING });
    expect(r.cells["fred.DGS2"]).toEqual({ state: "none" });
    expect(r.cells["yf.^GSPC"]).toEqual({ state: "none" });
    expect(r.cells["fred.DEXINUS"]).toMatchObject({ state: "waiting" });
  });

  it("shows a series api.series doesn't return (suppressed or unpublished) as not available, never waiting", () => {
    const [f] = reactionRows([fed], [], series(ALL.filter((k) => !k.startsWith("fred."))));
    expect(f.cells["fred.DGS2"]).toEqual({ state: "unavailable", text: "—", detail: "Not available." });
    expect(f.cells["yf.^GSPC"]).toMatchObject({ state: "waiting" });
  });

  it("names the shown series' sources in table order", () => {
    expect(reactionSources(series(["yf.^NSEI", "fred.DGS2"]))).toEqual([
      "Source: fred.DGS2", null, null, null, "Source: yf.^NSEI", null,
    ]);
  });
});
