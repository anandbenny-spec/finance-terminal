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
  it("is the record when its reads succeed", async () => {
    const { db } = fakeClient({ ...CALLS, call_guidance: [], guidance_hit_rates: [], earnings_latest: [] });
    const out = await readCallsTab(db, "tcs", undefined);
    expect(out.guidance).toEqual({ rows: [], rate: null, unattributed: null, citations: [], pages: [] });
  });
  it("still throws when the calls list itself can't be read", async () => {
    const { db } = fakeClient({ field_policy: SHOWN as unknown as Row[], call_guidance: [], guidance_hit_rates: [], earnings_latest: [] });
    await expect(readCallsTab(db, "tcs", undefined)).rejects.toThrow();
  });
});
