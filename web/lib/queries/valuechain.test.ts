// The value chain card's reads (VC-06, #191): each list is read whole and checked against the exact count; a read that fails
// costs its part only (null), never the page; the Overview's reads and the sub-page's are the views 0034 and 0040 define. A fake Data
// API stands in for Supabase; a view missing from the store answers 404, so its read throws (view 0034 not pushed yet).
import { describe, expect, it, vi } from "vitest";
import { fakeClient, type Row, type Store } from "@/tests/fake-postgrest";
import { FY25, FY26, card, groupCompany, peerRow, product, rpt, segment } from "@/tests/value-chain-rows";

vi.mock("next/cache", () => ({ cacheLife: () => undefined, cacheTag: () => undefined }));
vi.spyOn(console, "error").mockImplementation(() => undefined);

const { loadValueChainPage, readValueChain, readValueChainOverview, readRelatedPartyTop } = await import("@/lib/queries/valuechain");

const rows = (list: object[]) => list as unknown as Row[];
const POLICY = [{ field_key: "equity.brsr_values", status: "labelled", label: "As reported by the company" }];

describe("readValueChain", () => {
  it("reads the company's years in year then card order, and only its own", async () => {
    const other = card(FY26).map((r) => ({ ...r, company_slug: "tcs" }));
    const { db } = fakeClient({ value_chain: rows([...card(FY26), ...card(FY25), ...other]) });
    const out = await readValueChain(db, "mandm");
    expect(out).toHaveLength(26);
    expect(out[0].fy_label).toBe("FY 2024-25");
    expect(out[13].fy_label).toBe("FY 2025-26");
    expect(out.slice(0, 13).map((r) => r.field_no)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
    expect(out.every((r) => r.company_slug === "mandm")).toBe(true);
  });

  it("reads a list longer than the Data API's cap whole, page by page, checked against the exact count", async () => {
    const many = Array.from({ length: 1500 }, (_, i) => ({ ...card(FY26)[0], field_no: i }));
    const { db } = fakeClient({ value_chain: rows(many) }, 400);
    expect((await readValueChain(db, "mandm")).length).toBe(1500);
  });
});

describe("readRelatedPartyTop", () => {
  it("orders by year, type and rank", async () => {
    const { db } = fakeClient({ related_party_top: rows([rpt("Sale of goods or services", 2, "B", 1), rpt("Purchase of goods or services", 1, "C", 3), rpt("Sale of goods or services", 1, "A", 2)]) });
    expect((await readRelatedPartyTop(db, "mandm")).map((r) => r.counterparty)).toEqual(["C", "A", "B"]);
  });
});

describe("readValueChainOverview", () => {
  it("reads the years, the products and the field policies", async () => {
    const { db } = fakeClient({ value_chain: rows(card(FY26)), value_chain_products: rows([product(FY26, 1, "Automotive", 70)]), field_policy: rows(POLICY) });
    const out = await readValueChainOverview(db, "mandm");
    expect(out.rows).toHaveLength(13);
    expect(out.products).toHaveLength(1);
    expect(out.policies).toEqual(POLICY);
  });

  it("makes a part null when its view can't be read, and leaves the others (0034 not pushed yet)", async () => {
    const { db } = fakeClient({ field_policy: rows(POLICY) });
    const out = await readValueChainOverview(db, "mandm");
    expect(out).toEqual({ rows: null, products: null, policies: POLICY });
  });

  it("makes the policies null when they can't be read, which holds the panel back", async () => {
    const { db } = fakeClient({ value_chain: rows(card(FY26)), value_chain_products: rows([]) });
    expect((await readValueChainOverview(db, "mandm")).policies).toBeNull();
  });
});

describe("loadValueChainPage", () => {
  it("reads the five views of the sub-page for the company", async () => {
    const store: Store = {
      value_chain: rows(card(FY26)),
      value_chain_products: rows([product(FY26, 1, "Automotive", 70)]),
      value_chain_group: rows([groupCompany(1, "Mahindra Lifespace Developers Ltd.", 51)]),
      related_party_top: rows([rpt("Sale of goods or services", 1, "A", 1)]),
      value_chain_peers: rows([peerRow("mandm", "within_india_share", 90)]),
      segments: rows([segment(1, "Automotive", 1, 100)]),
      field_policy: rows(POLICY),
    };
    const out = await loadValueChainPage(fakeClient(store).db, "mandm");
    expect(out.group).toHaveLength(1);
    expect(out.rpt).toHaveLength(1);
    expect(out.peers).toHaveLength(1);
    expect(out.segments).toHaveLength(1);
  });

  it.each(["value_chain_group", "related_party_top", "value_chain_peers", "segments"])("costs only its own part when %s can't be read", async (view) => {
    const store: Store = {
      value_chain: rows(card(FY26)), value_chain_products: rows([]), value_chain_group: rows([]), related_party_top: rows([]), value_chain_peers: rows([]), segments: rows([]), field_policy: rows(POLICY),
    };
    delete store[view];
    const out = await loadValueChainPage(fakeClient(store).db, "mandm");
    const key = { value_chain_group: "group", related_party_top: "rpt", value_chain_peers: "peers", segments: "segments" }[view] as "group" | "rpt" | "peers" | "segments";
    expect(out[key]).toBeNull();
    expect(out.rows).toHaveLength(13);
  });
});
