// What "How the wording moved" reads (#509, marks reworked in #530): the diffs' pairs a dozen at a time against an exact count,
// refused when a page grows past its byte limit, and cut to cells (a sentence and its marks) before anything is cached. A fake Data API stands in for Supabase
// (tests/fake-postgrest.ts); its rows are test data shaped like the views, not the banks' documents.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IncompleteReadError } from "@/lib/queries/read";
import { TIMELINE_PAGE_MAX_BYTES, TIMELINE_PAGE_ROWS, readDecisionDiffPairs } from "@/lib/queries/policy";
import { fakeClient, type Row, type Store } from "@/tests/fake-postgrest";

const state = vi.hoisted(() => ({ profiles: [] as unknown[], store: {} as Record<string, unknown> }));
vi.mock("next/cache", () => ({ cacheLife: (p: unknown) => state.profiles.push(p), cacheTag: () => undefined }));
vi.mock("@/lib/supabase/public", () => ({ publicClient: () => fakeClient(state.store as Store).db }));
vi.spyOn(console, "error").mockImplementation(() => undefined);
vi.spyOn(console, "warn").mockImplementation(() => undefined);

const { readBankTimeline, readLanguageTimeline } = await import("@/lib/queries/timeline");

const UNMATCHED = "SENTINEL-UNMATCHED-SENTENCE about global growth.";
const DELETED = "SENTINEL-DELETED-WORDS";

const pair = (kind: string, topic: string, extra: Row): Row => ({ kind, topic, new_para: [1], old_para: [1], ...extra });
const pairsOf = (stanceText: string): Row[] => [
  pair("changed", "Decision and stance", { ops: [{ op: "eq", text: "The MPC also decided to remain " }, { op: "del", text: DELETED }, { op: "ins", text: stanceText }] }),
  pair("same", "Global", { text: UNMATCHED }),
  pair("removed", "Decision and stance", { text: "The MPC dropped this stance sentence.", new_para: [], old_para: [2] }),
];
const diff = (document_id: number, pairs: Row[]): Row => ({
  document_id, base_document_id: document_id - 1, segments: { version: 2, words: { old: 400, new: 420 }, pairs }, words_added: 12, words_removed: 8,
});
const meeting = (bank: "Fed" | "RBI", date: string): Row => ({
  slug: `${bank === "Fed" ? "fomc" : "mpc"}-${date}`, bank, status: "held", decision_date: date, decision_at: `${date}T04:30:00+00:00`, kind: "scheduled",
  is_rate_decision: true, meeting_no: null, minutes_due_on: null, policy_rate_pct: null, policy_rate_change_bps: 0, stance: null, stance_display_label: null,
  dissent_count: 0, attribution: null, fed_target_lower_pct: null, fed_target_upper_pct: null,
});
const doc = (id: number, doc_type: string, event_slug: string): Row => ({ id, doc_type, event_slug, version_no: 1 });

describe("readDecisionDiffPairs", () => {
  const many = Array.from({ length: 30 }, (_, k) => diff(k + 1, [pair("same", "T", { text: `sentence ${k + 1}` })]));

  it("reads the pairs a dozen diffs at a time, each page against an exact count, in document order", async () => {
    expect(TIMELINE_PAGE_ROWS).toBe(12);
    const { db, seen } = fakeClient({ document_diffs: [...many].reverse() });
    const rows = await readDecisionDiffPairs(db, many.map((d) => Number(d.document_id)));
    expect(rows.map((r) => r.document_id)).toEqual(many.map((d) => d.document_id));
    const reads = seen.filter((r) => r.view === "document_diffs");
    expect(reads.map((r) => r.returned)).toEqual([12, 12, 6]);
    expect(reads.every((r) => r.countExact && r.params.get("limit") === "12")).toBe(true);
  });

  it("selects the version and the pairs, and neither the whole of `segments` nor the counts", async () => {
    const { db, seen } = fakeClient({ document_diffs: many });
    const rows = await readDecisionDiffPairs(db, [1, 2]);
    expect(seen[0].params.get("select")).toBe("document_id,base_document_id,version:segments->version,pairs:segments->pairs");
    expect(rows[0]).toEqual({ document_id: 1, base_document_id: 0, version: 2, pairs: [{ kind: "same", topic: "T", new_para: [1], old_para: [1], text: "sentence 1" }] });
    expect(Object.keys(rows[0])).not.toContain("words_added");
  });

  it("reads only the documents it is given, and nothing at all for none", async () => {
    const { db, seen } = fakeClient({ document_diffs: many });
    expect((await readDecisionDiffPairs(db, [5, 7, 9])).map((r) => r.document_id)).toEqual([5, 7, 9]);
    const none = fakeClient({ document_diffs: many });
    expect(await readDecisionDiffPairs(none.db, [])).toEqual([]);
    expect(none.seen).toHaveLength(0);
    expect(seen.length).toBeGreaterThan(0);
  });

  it("refuses a page over the byte limit instead of passing it on", async () => {
    const big = "x".repeat(Math.ceil(TIMELINE_PAGE_MAX_BYTES / TIMELINE_PAGE_ROWS) + 1000);
    const heavy = Array.from({ length: 12 }, (_, k) => diff(k + 1, [pair("same", "T", { text: big })]));
    const { db } = fakeClient({ document_diffs: heavy });
    const read = readDecisionDiffPairs(db, heavy.map((d) => Number(d.document_id)));
    await expect(read).rejects.toBeInstanceOf(IncompleteReadError);
    await expect(read).rejects.toThrow(/over the 2000000-byte limit/);
  });

  it("fails loudly when the view can't be read", async () => {
    const { db } = fakeClient({});
    await expect(readDecisionDiffPairs(db, [1])).rejects.toBeInstanceOf(IncompleteReadError);
  });
});

describe("readBankTimeline and readLanguageTimeline", () => {
  const store = (): Store => ({
    policy_meetings: [meeting("RBI", "2026-10-07"), meeting("RBI", "2026-08-06"), meeting("Fed", "2026-09-16")],
    documents: [doc(2, "mpc_resolution", "mpc-2026-10-07"), doc(1, "mpc_resolution", "mpc-2026-08-06"), doc(3, "fomc_statement", "fomc-2026-09-16"), doc(4, "mpc_minutes", "mpc-2026-10-07")],
    document_diffs: [diff(2, pairsOf("new stance words.")), diff(3, pairsOf("fed words stance."))],
  });
  beforeEach(() => {
    state.profiles = [];
    state.store = store();
  });

  it("returns cells only: the matching sentence and its marks, none of the pairs it was cut from", async () => {
    const { db } = fakeClient(store());
    const { timeline } = await readBankTimeline(db, "RBI", [{ id: 1, event_slug: "mpc-2026-08-06", version_no: 1 }, { id: 2, event_slug: "mpc-2026-10-07", version_no: 1 }], { from: "2019-01-01", to: "2026-11-13" });
    expect(timeline.columns.map((c) => c.slug)).toEqual(["mpc-2026-10-07", "mpc-2026-08-06"]);
    // The meeting before has no diff (a gap), so there is no previous sentence to compare with: every word of this one is new.
    expect(timeline.columns[0].cells.stance).toEqual({
      kind: "sentence",
      parts: [{ op: "ins", text: "The MPC also decided to remain new stance words." }],
      how: "new",
      para: 1,
    });
    // The older meeting has no diff (the first in its series): a gap with its reason, for every row.
    expect(Object.values(timeline.columns[1].cells).every((c) => c.kind === "gap" && c.reason === "no_diff")).toBe(true);
    const json = JSON.stringify(timeline);
    for (const left of [UNMATCHED, DELETED, "dropped this stance", `"pairs"`, `"ops"`, `"removed"`, `"topic"`]) expect(json).not.toContain(left);
  });

  it("caches each bank's grid, newest first, holding no pairs, and keeps a good read on its own profile", async () => {
    const out = await readLanguageTimeline();
    expect(out.rbi.ok && out.fed.ok).toBe(true);
    if (!out.rbi.ok || !out.fed.ok) return;
    expect(out.rbi.timeline.bank).toBe("RBI");
    expect(out.fed.timeline.bank).toBe("Fed");
    expect(out.fed.timeline.columns.map((c) => c.slug)).toEqual(["fomc-2026-09-16"]);
    expect(out.rbi.timeline.latest).toBe("2026-10-07");
    const json = JSON.stringify(out);
    for (const left of [UNMATCHED, DELETED, `"pairs"`, `"ops"`, `"segments"`]) expect(json).not.toContain(left);
    // Three rows by two meetings of cells is a few kilobytes, not the diffs' tens of kilobytes each.
    expect(json.length).toBeLessThan(3000);
    expect(state.profiles).toEqual(["minutesNoExpire"]);
  });

  it("keeps a failed read on failedRead, after its own profile", async () => {
    state.store = {};
    const out = await readLanguageTimeline();
    expect(out.rbi.ok || out.fed.ok).toBe(false);
    expect(state.profiles).toEqual(["minutesNoExpire", "failedRead"]);
  });
});
