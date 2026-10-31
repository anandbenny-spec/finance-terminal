// The screener's reads keep the row-limit guard (CLAUDE.md §6): a list is read whole against its exact count or not at all.
import { describe, expect, it } from "vitest";
import { indexesOf, readIndexMembers, readScreener } from "@/lib/queries/equity";
import { IncompleteReadError } from "@/lib/queries/read";
import { fakeClient, type Row } from "@/tests/fake-postgrest";

const company = (i: number): Row => ({ slug: `co-${String(i).padStart(4, "0")}`, name: `Co ${i}`, market: "IN", currency: "INR", snapshot_date: "2026-10-30" });
const member = (index: string, i: number): Row => ({
  index_slug: index, index_name: index === "nifty-50" ? "Nifty 50" : "Nifty Next 50", snapshot_date: "2026-10-30", slug: `co-${i}`,
  name: `Co ${i}`, short_name: null, nse_symbol: `C${i}`, market: "IN", currency: "INR",
});

describe("screener reads", () => {
  it("reads every row whole when the view holds more than one request's cap (1,200 rows)", async () => {
    const { db, seen } = fakeClient({ screener: Array.from({ length: 1200 }, (_, i) => company(i)) });
    const rows = await readScreener(db);
    expect(rows).toHaveLength(1200);
    expect(new Set(rows.map((r) => r.slug)).size).toBe(1200);
    expect(seen.every((s) => s.countExact)).toBe(true);
    expect(seen.length).toBeGreaterThan(1);
  });
  it("reads the index members and names each index once", async () => {
    const { db } = fakeClient({ index_members: [member("nifty-50", 1), member("nifty-50", 2), member("nifty-next-50", 3)] });
    const members = await readIndexMembers(db);
    expect(members).toHaveLength(3);
    expect(indexesOf(members)).toEqual([{ slug: "nifty-50", name: "Nifty 50" }, { slug: "nifty-next-50", name: "Nifty Next 50" }]);
  });
  it("throws, never returns a cut list, when the view can't be read", async () => {
    const { db } = fakeClient({});
    await expect(readScreener(db)).rejects.toBeInstanceOf(IncompleteReadError);
  });
});
