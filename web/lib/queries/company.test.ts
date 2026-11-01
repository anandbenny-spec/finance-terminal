// The company page's reads keep the row-limit guard (CLAUDE.md §6): the price history (about 1,950 rows, two pages) is read
// whole against its exact count or not at all; another company's rows never come back; an unknown slug is null.
import { describe, expect, it } from "vitest";
import { IncompleteReadError } from "@/lib/queries/read";
import { readActions, readCompany, readCompanyMetrics, readCompanyPrices, readFinancials, readPeers, readPromoterHolding } from "@/lib/queries/company";
import { fakeClient, type Row } from "@/tests/fake-postgrest";
import { action, company, financial, metric, peer, price, promoter } from "@/tests/company-rows";

const day = (i: number) => new Date(Date.UTC(2019, 0, 1) + i * 86_400_000).toISOString().slice(0, 10);

describe("company reads", () => {
  it("joins the price history's two pages and checks the rows against the exact count", async () => {
    const rows = Array.from({ length: 1950 }, (_, i) => price(day(i), 100 + i, 100 + i)) as unknown as Row[];
    const { db, seen } = fakeClient({ company_prices: [...rows, { ...price("2026-01-01", 1, 1), slug: "other" } as unknown as Row] });
    const out = await readCompanyPrices(db, "tcs");
    expect(out).toHaveLength(1950);
    expect(new Set(out.map((r) => r.trade_date)).size).toBe(1950);
    expect(out.every((r) => r.slug === "tcs")).toBe(true);
    expect(out[0].trade_date < out[1949].trade_date).toBe(true);
    expect(seen.length).toBe(2);
    expect(seen.every((s) => s.countExact)).toBe(true);
  });
  it("reads one company's row, and null for a slug nobody has", async () => {
    const { db } = fakeClient({ companies: [company() as unknown as Row, company({ slug: "infy" }) as unknown as Row] });
    expect((await readCompany(db, "infy"))?.slug).toBe("infy");
    expect(await readCompany(db, "nope")).toBeNull();
  });
  it("reads each list for its company only", async () => {
    const { db } = fakeClient({
      company_metrics: [metric("pb"), { ...metric("pb"), slug: "other" }] as unknown as Row[],
      peer_comps: [peer("infy"), { ...peer("wipro"), company_slug: "other" }] as unknown as Row[],
      company_financials: [financial("quarter", "2026-06-30"), { ...financial("quarter", "2026-06-30"), slug: "other" }] as unknown as Row[],
      company_corporate_actions: [action(), { ...action(), slug: "other" }] as unknown as Row[],
      company_promoter_holding: [promoter("2026-06-30"), promoter("2026-03-31"), { ...promoter("2026-06-30"), slug: "other" }] as unknown as Row[],
    });
    expect(await readCompanyMetrics(db, "tcs")).toHaveLength(1);
    expect(await readPeers(db, "tcs")).toHaveLength(1);
    expect(await readFinancials(db, "tcs")).toHaveLength(1);
    expect(await readActions(db, "tcs")).toHaveLength(1);
    expect((await readPromoterHolding(db, "tcs")).map((p) => p.as_of)).toEqual(["2026-06-30", "2026-03-31"]);
  });
  it("throws, never returns a cut list, when a view can't be read", async () => {
    const { db } = fakeClient({});
    await expect(readCompanyPrices(db, "tcs")).rejects.toBeInstanceOf(IncompleteReadError);
    await expect(readPromoterHolding(db, "tcs")).rejects.toBeInstanceOf(IncompleteReadError);
    await expect(readCompany(db, "tcs")).rejects.toThrow();
  });
});
