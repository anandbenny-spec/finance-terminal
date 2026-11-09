// readCompanyPage (#328): the header (company row, actions, index names) is all-or-nothing; a failed tab read costs that tab,
// not the page: ok: true, the header, the tab's data null and tabFailed set. The guidance record's reads fail on their own,
// inside the Earnings calls tab. A fake Data API stands in for Supabase; a view missing from the store answers 404, so its read throws.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fakeClient, type Row, type Store } from "@/tests/fake-postgrest";
import { call, SHOWN } from "@/tests/call-rows";
import { company } from "@/tests/company-rows";
import type { TabId } from "@/lib/equity/company";

vi.mock("next/cache", () => ({ cacheLife: () => undefined, cacheTag: () => undefined }));
let store: Store = {};
vi.mock("@/lib/supabase/public", () => ({ publicClient: () => fakeClient(store).db }));
vi.spyOn(console, "error").mockImplementation(() => undefined);

const { readCompanyPage } = await import("@/lib/queries/company");
const { readCallsTab } = await import("@/lib/queries/calls");

const HEADER: Store = { companies: [company() as unknown as Row], company_corporate_actions: [], index_members: [] };
const TABS: TabId[] = ["overview", "financials", "valuation", "rate-decisions", "calls"];

beforeEach(() => {
  store = { ...HEADER };
});

describe("the company page's reads", () => {
  it.each(TABS)("keeps the header and marks the %s tab failed when its reads throw", async (tab) => {
    const out = await readCompanyPage("tcs", tab);
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    expect(out.company.slug).toBe("tcs");
    expect(out.tab).toBe(tab);
    expect(out.tabFailed).toBe(true);
    expect([out.overview, out.financials, out.valuation, out.rates, out.calls]).toEqual([null, null, null, null, null]);
  });

  it("is not marked failed when the tab reads", async () => {
    store = { ...HEADER, company_prices: [], company_metrics: [], peer_comps: [] };
    const out = await readCompanyPage("tcs", "overview");
    expect(out.ok && out.tabFailed).toBe(false);
    expect(out.ok && out.overview).toEqual({ prices: [], metrics: [], peers: [] });
  });

  it.each(["companies", "company_corporate_actions", "index_members"])("is { ok: false } when the header's %s read throws", async (view) => {
    store = { ...HEADER, company_prices: [], company_metrics: [], peer_comps: [] };
    delete store[view];
    expect((await readCompanyPage("tcs", "overview")).ok).toBe(false);
  });

  it("is { ok: false } when no company has the slug", async () => {
    store = { ...HEADER, company_prices: [], company_metrics: [], peer_comps: [] };
    expect((await readCompanyPage("nope", "overview")).ok).toBe(false);
  });
});

describe("the Earnings calls tab's guidance record", () => {
  const CALLS: Store = {
    earnings_calls: [call() as unknown as Row],
    field_policy: SHOWN as unknown as Row[],
    ai_outputs: [],
    call_figures: [],
    ai_citations: [],
    citation_pages: [],
  };
  it("is null when its own reads throw, and the rest of the tab still reads", async () => {
    const { db } = fakeClient({ ...CALLS });
    const out = await readCallsTab(db, "tcs", undefined);
    expect(out.guidance).toBeNull();
    expect(out.calls).toHaveLength(1);
    expect(out.selected?.call_slug).toBe("q2-fy27");
  });
  it("is the record when its reads succeed, without a request for api.earnings_latest when a call is selected (#342)", async () => {
    const { db, seen } = fakeClient({ ...CALLS, call_guidance: [], guidance_hit_rates: [] });
    const out = await readCallsTab(db, "tcs", undefined);
    expect(out.guidance).toEqual({ rows: [], rate: null, unattributed: 0, citations: [], pages: [] });
    expect(seen.some((s) => s.view === "earnings_latest")).toBe(false);
  });
  it("takes the count left out from the calls it read: their counts summed, a null as 0, once the extraction field is open (#342)", async () => {
    const rows = [call({ call_slug: "q2-fy27", unattributed_targets: 1 }), call({ call_slug: "q1-fy27", unattributed_targets: null, is_newest: false, is_default: false }), call({ call_slug: "q4-fy26", unattributed_targets: 2, is_newest: false, is_default: false })];
    const { db } = fakeClient({ ...CALLS, earnings_calls: rows as unknown as Row[], call_guidance: [], guidance_hit_rates: [] });
    expect((await readCallsTab(db, "tcs", undefined)).guidance?.unattributed).toBe(3);
  });
  it("leaves the count null while the extraction field is held back, as the view did (#342)", async () => {
    const held = [{ field_key: "ai.call_summary", status: "hidden", label: null }, { field_key: "ai.guidance_extraction", status: "hidden", label: null }];
    const { db } = fakeClient({ ...CALLS, field_policy: held as unknown as Row[], call_guidance: [], guidance_hit_rates: [] });
    expect((await readCallsTab(db, "tcs", undefined)).guidance?.unattributed).toBeNull();
  });
  it("gives 0 for a company with no calls once the field is open, and still reads the discovery window for the empty tab (#342)", async () => {
    const { db, seen } = fakeClient({ ...CALLS, earnings_calls: [], call_guidance: [], guidance_hit_rates: [], earnings_latest: [] });
    const out = await readCallsTab(db, "tcs", undefined);
    expect(out.guidance?.unattributed).toBe(0);
    expect(out.selected).toBeNull();
    expect(seen.filter((s) => s.view === "earnings_latest")).toHaveLength(1);
  });
  it("still throws when the calls list itself can't be read", async () => {
    const { db } = fakeClient({ field_policy: SHOWN as unknown as Row[], call_guidance: [], guidance_hit_rates: [], earnings_latest: [] });
    await expect(readCallsTab(db, "tcs", undefined)).rejects.toThrow();
  });
});
