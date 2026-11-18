// /earnings lists India companies only (#636): api.earnings_latest builds a row for every published company, so after the Dow 30
// were published (#407) it held 30 US rows with "No transcript found", although US calls are out of scope and were never searched.
// readEarningsList keeps only the slugs api.companies gives with market 'IN'; a failed read of either view fails the list whole.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fakeClient, type Row, type Store } from "@/tests/fake-postgrest";
import { company } from "@/tests/company-rows";
import type { EarningsListRow } from "@/lib/queries/api";

const state = vi.hoisted(() => ({ store: {} as Record<string, unknown> }));
vi.mock("next/cache", () => ({ cacheLife: () => undefined, cacheTag: () => undefined }));
vi.mock("@/lib/supabase/public", () => ({ publicClient: () => fakeClient(state.store as Store).db }));
vi.spyOn(console, "error").mockImplementation(() => undefined);

const { readEarningsList, readEarningsListPage } = await import("@/lib/queries/calls");

/** A row of api.earnings_latest with every column /earnings selects. */
function latest(slug: string, over: Partial<EarningsListRow> = {}): Row {
  const row: EarningsListRow = {
    company_slug: slug, company_name: slug, short_name: null, call_slug: `${slug}-call-q2-fy27`, fiscal_label: "Q2 FY27",
    call_date: "2026-10-09", filed_at: "2026-10-14T12:00:00+05:30", exchange: "NSE", route: "full_text", status: "found",
    summary_state: "held_back", roles_partial: false, window_from: null, window_to: null, ...over,
  };
  return row as unknown as Row;
}

const NONE_FOUND = { call_slug: null, fiscal_label: null, call_date: null, filed_at: null, exchange: null, route: null, status: "none_found", summary_state: null, roles_partial: null, window_from: "2025-01-13", window_to: "2026-11-14" };

const STORE: Store = {
  companies: [
    company({ slug: "tcs" }), company({ slug: "sbi", name: "State Bank of India" }),
    company({ slug: "apple", name: "Apple Inc.", market: "US", currency: "USD" }),
    company({ slug: "visa", name: "Visa Inc.", market: "US", currency: "USD" }),
  ] as unknown as Row[],
  earnings_latest: [latest("apple", NONE_FOUND), latest("sbi"), latest("tcs", NONE_FOUND), latest("visa", NONE_FOUND)],
};

describe("readEarningsList (#636)", () => {
  it("keeps the India companies' rows, in company order, and drops every US company's", async () => {
    const { db } = fakeClient(STORE);
    const rows = await readEarningsList(db);
    expect(rows.map((r) => r.company_slug)).toEqual(["sbi", "tcs"]);
    expect(rows[1]).toMatchObject({ status: "none_found", window_from: "2025-01-13" }); // an India company with no call keeps its row
  });

  it("asks api.companies for market IN only, with the exact count, alongside the whole earnings list", async () => {
    const { db, seen } = fakeClient(STORE);
    await readEarningsList(db);
    const companies = seen.filter((s) => s.view === "companies");
    expect(companies).toHaveLength(1);
    expect(companies[0].params.get("market")).toBe("eq.IN");
    expect(companies[0].countExact).toBe(true);
    const list = seen.filter((s) => s.view === "earnings_latest");
    expect(list).toHaveLength(1);
    expect(list[0].countExact).toBe(true);
  });

  it("fails whole when api.companies can't be read, never listing the US rows", async () => {
    const { db } = fakeClient({ earnings_latest: STORE.earnings_latest });
    await expect(readEarningsList(db)).rejects.toThrow();
  });
});

describe("readEarningsListPage (#636)", () => {
  beforeEach(() => {
    state.store = {};
  });

  it("gives the India rows when both reads succeed", async () => {
    state.store = STORE;
    const page = await readEarningsListPage();
    expect(page.ok && page.rows.map((r) => r.company_slug)).toEqual(["sbi", "tcs"]);
  });

  it("is the error state when api.companies fails", async () => {
    state.store = { earnings_latest: STORE.earnings_latest };
    expect((await readEarningsListPage()).ok).toBe(false);
  });
});
