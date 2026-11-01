// The rate-sensitivity reads keep the row-limit guard (CLAUDE.md §6): each list is read whole against its exact count
// or not at all, only the asked company's rows come back, and a peer group's members are grouped from the screener rows.
import { describe, expect, it } from "vitest";
import { IncompleteReadError } from "@/lib/queries/read";
import { peerGroupsOf, readCompanyCycles, readRateCycles, readRateMoves, readRateStats } from "@/lib/queries/rate";
import { fakeClient, type Row } from "@/tests/fake-postgrest";
import { cycle, cycleMove, fewStat, move, stat } from "@/tests/rate-rows";
import { row } from "@/tests/screener-rows";

const as = (rows: object[]) => rows as unknown as Row[];

describe("rate-sensitivity reads", () => {
  it("reads one company's statistics and moves, never another's", async () => {
    const { db } = fakeClient({
      rate_decision_stats: as([stat(), fewStat(), stat({ subject_key: "infy" }), stat({ subject_kind: "peer_group", subject_key: "it" })]),
      rate_decision_moves: as([move(), move({ subject_key: "infy" }), move({ subject_kind: "peer_group" })]),
    });
    expect((await readRateStats(db, "company", "tcs")).map((s) => s.subject_key)).toEqual(["tcs", "tcs"]);
    expect(await readRateMoves(db, "tcs")).toHaveLength(1);
  });
  it("reads every peer group's statistics when no key is asked for", async () => {
    const { db } = fakeClient({ rate_decision_stats: as([stat(), stat({ subject_kind: "peer_group", subject_key: "it" }), stat({ subject_kind: "peer_group", subject_key: "banks" })]) });
    expect((await readRateStats(db, "peer_group")).map((s) => s.subject_key)).toEqual(["banks", "it"]);
  });
  it("pages a list past the Data API's 1,000-row cap and checks it against the exact count", async () => {
    const rows = Array.from({ length: 1200 }, (_, i) => move({ event_slug: `e${String(i).padStart(4, "0")}`, meeting_date: "2022-06-15" }));
    const { db, seen } = fakeClient({ rate_decision_moves: as(rows) });
    expect(await readRateMoves(db, "tcs")).toHaveLength(1200);
    expect(seen.length).toBe(2);
    expect(seen.every((s) => s.countExact)).toBe(true);
  });
  it("reads a company's cycles and every bank's cycles", async () => {
    const { db } = fakeClient({ company_rate_cycles: as([cycleMove(), cycleMove({ company_slug: "infy" })]), rate_cycles: as([cycle()]) });
    expect(await readCompanyCycles(db, "tcs")).toHaveLength(1);
    expect(await readRateCycles(db)).toHaveLength(1);
  });
  it("throws, never returns a cut list, when a view can't be read", async () => {
    const { db } = fakeClient({});
    await expect(readRateStats(db, "company", "tcs")).rejects.toBeInstanceOf(IncompleteReadError);
    await expect(readCompanyCycles(db, "tcs")).rejects.toBeInstanceOf(IncompleteReadError);
  });
});

describe("peerGroupsOf", () => {
  it("groups the screener's rows by peer group, members by name, groups by label, skipping companies with none", () => {
    const groups = peerGroupsOf([
      row("wipro", { short_name: "Wipro" }),
      row("tcs", { short_name: "TCS" }),
      row("hdfc", { peer_group_key: "banks", peer_group_label: "Private banks", short_name: "HDFC" }),
      row("odd", { peer_group_key: null, peer_group_label: null }),
    ]);
    expect(groups.map((g) => g.label)).toEqual(["IT services", "Private banks"]);
    expect(groups[0].members.map((m) => m.name)).toEqual(["TCS", "Wipro"]);
    expect(groups.flatMap((g) => g.members.map((m) => m.slug))).not.toContain("odd");
  });
});
