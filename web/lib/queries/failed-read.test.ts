// A failed read isn't cached like a good one (#499): each `'use cache'` read calls cacheLife("failedRead") after its own profile
// when a read failed and its result keeps the failure as a value (`{ ok: false }`, a held part, a null from a catch), and never
// when every read succeeded or when this build has no Data API (the !db branch keeps its profile: reading again can't heal it).
// `next/cache` is replaced by a recorder; a fake Data API stands in for Supabase, and a view missing from it answers 404.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fakeClient, type Row, type Store } from "@/tests/fake-postgrest";
import { company } from "@/tests/company-rows";
import type { TabId } from "@/lib/equity/company";

const state = vi.hoisted(() => ({ profiles: [] as unknown[], store: {} as Record<string, unknown>, client: true }));
vi.mock("next/cache", () => ({ cacheLife: (p: unknown) => state.profiles.push(p), cacheTag: () => undefined }));
vi.mock("@/lib/supabase/public", () => ({ publicClient: () => (state.client ? fakeClient(state.store as Store).db : null) }));
vi.spyOn(console, "error").mockImplementation(() => undefined);
vi.spyOn(console, "warn").mockImplementation(() => undefined);

const shell = await import("@/lib/queries/shell");
const accuracy = await import("@/lib/queries/accuracy");
const feed = await import("@/lib/queries/feed");
const doc = await import("@/lib/queries/document");
const meeting = await import("@/lib/queries/meeting");
const rate = await import("@/lib/queries/rate");
const calls = await import("@/lib/queries/calls");
const co = await import("@/lib/queries/company");
const equity = await import("@/lib/queries/equity");
const overview = await import("@/lib/queries/overview");
const vc = await import("@/lib/queries/valuechain");

/** A Data API where every view answers, empty unless given; the views named in `missing` answer 404. */
function views(given: Store = {}, missing: string[] = []): Store {
  return new Proxy(given, { get: (t, view: string) => (missing.includes(view) ? undefined : (t[view] ?? [])) });
}

const COMPANY: Store = { companies: [company() as unknown as Row] };
const US_COMPANY: Store = { companies: [company({ market: "US", currency: "USD" }) as unknown as Row] };

/** Every cached read whose result can hold a failure, with the call that makes it. */
const READS: [string, () => Promise<unknown>][] = [
  ["readTape", () => shell.readTape()],
  ["readNextMeetings", () => shell.readNextMeetings()],
  ["readFreshness", () => shell.readFreshness()],
  ["readTourShown", () => shell.readTourShown()],
  ["readQualitySummary", () => accuracy.readQualitySummary()],
  ["readEvalSummary", () => accuracy.readEvalSummary()],
  ["readFeedDecisions", () => feed.readFeedDecisions()],
  ["readDocumentPage", () => doc.readDocumentPage(7)],
  ["readSpanCitations", () => doc.readSpanCitations(7, 0, 40)],
  ["readMeetingExists", () => meeting.readMeetingExists("Fed", "fomc-2026-01-28")],
  ["readMeetingPage", () => meeting.readMeetingPage("Fed", "fomc-2026-01-28", "2026-01-28")],
  ["readDocumentChunks", () => meeting.readDocumentChunks(7)],
  ["readMeetingDiff", () => meeting.readMeetingDiff(7)],
  ["readNumbersBase", () => meeting.readNumbersBase("RBI", "mpc-2026-02-06")],
  ["readMeetingReaction", () => meeting.readMeetingReaction("Fed", "fomc-2026-01-28")],
  ["readSectorPage", () => rate.readSectorPage()],
  ["readEarningsListPage", () => calls.readEarningsListPage()],
  ["readCompanyExists", () => co.readCompanyExists("tcs")],
  ["readCompanyName", () => co.readCompanyName("tcs")],
  ["readCompanyMarket", () => co.readCompanyMarket("tcs")],
  ["readPricedInPage", () => equity.readPricedInPage()],
  ["readScreenerPage", () => equity.readScreenerPage()],
  ["readPolicyOverview", () => overview.readPolicyOverview()],
];

const failedRead = () => state.profiles.filter((p) => p === "failedRead").length;

beforeEach(() => {
  state.profiles = [];
  state.client = true;
  state.store = {};
});

describe("a cached read that failed", () => {
  it.each(READS)("%s: a good read keeps its own profile only", async (_, read) => {
    state.store = views(COMPANY);
    await read();
    expect(state.profiles.length).toBeGreaterThan(0);
    expect(state.profiles.every((p) => p === "minutesNoExpire" || p === "hoursNoExpire")).toBe(true);
  });

  it.each(READS)("%s: a failed read is kept on failedRead, after its own profile", async (_, read) => {
    state.store = {};
    await read();
    expect(failedRead()).toBe(1);
    expect(state.profiles[0]).toMatch(/^(minutes|hours)NoExpire$/);
    expect(state.profiles.at(-1)).toBe("failedRead");
  });

  it.each(READS)("%s: no Data API in this build keeps its own profile (reading again can't heal it)", async (_, read) => {
    state.client = false;
    await read();
    expect(failedRead()).toBe(0);
  });

  it("a page with one part failed is kept on failedRead (the policy overview's market reactions)", async () => {
    state.store = views({}, ["market_reactions"]);
    const out = await overview.readPolicyOverview();
    expect(out.reactions.ok).toBe(false);
    expect(out.history.ok).toBe(true);
    expect(failedRead()).toBe(1);
  });
});

describe("the company page's tab (readTab through readCompanyPage)", () => {
  const TABS: TabId[] = ["overview", "financials", "valuation", "rate-decisions", "calls"];

  it.each(TABS)("%s: a good tab keeps its own profile only", async (tab) => {
    state.store = views(COMPANY);
    const out = await co.readCompanyPage("tcs", tab);
    expect(out.ok && !out.tabFailed).toBe(true);
    expect(failedRead()).toBe(0);
  });

  it.each([
    ["overview", "value_chain"],
    ["overview", "value_chain_products"],
    ["overview", "field_policy"],
    ["financials", "company_promoter_holding"],
    ["financials", "segment_changes"],
    ["financials", "field_policy"],
    ["valuation", "forecasts"],
    ["valuation", "series"],
    ["calls", "call_guidance"],
    ["calls", "remarks_changes"],
  ] as [TabId, string][])("%s: a part that failed on its own (%s) keeps the tab on failedRead", async (tab, view) => {
    state.store = views(COMPANY, [view]);
    const out = await co.readCompanyPage("tcs", tab);
    expect(out.ok && !out.tabFailed).toBe(true);
    expect(failedRead()).toBe(1);
  });

  it("a tab whose own reads throw isn't stored at all, so it needs no profile (#328)", async () => {
    state.store = views(COMPANY, ["company_prices"]);
    const out = await co.readCompanyPage("tcs", "overview");
    expect(out.ok && out.tabFailed).toBe(true);
    expect(failedRead()).toBe(0);
  });

  it.each([
    ["financials", "company_promoter_holding"],
    ["valuation", "forecasts"],
  ] as [TabId, string][])("a US company's %s tab doesn't read %s, so its null is not a failure", async (tab, view) => {
    state.store = views(US_COMPANY, [view]);
    const out = await co.readCompanyPage("tcs", tab);
    expect(out.ok && !out.tabFailed).toBe(true);
    expect(failedRead()).toBe(0);
  });

  it("a US company's valuation tab with its 10-year's source line failed is kept on failedRead", async () => {
    state.store = views(US_COMPANY, ["series"]);
    await co.readCompanyPage("tcs", "valuation");
    expect(failedRead()).toBe(1);
  });
});

describe("tabReadFailed", () => {
  const none = { overview: null, financials: null, valuation: null, rates: null, calls: null };
  const callsTab = (over: Partial<NonNullable<Parameters<typeof co.tabReadFailed>[0]["calls"]>>) =>
    ({
      ...none,
      calls: {
        calls: [], selected: null, policies: [], summary: null, figures: [], citations: [], pages: [],
        guidance: { rows: [], rate: null, citations: [], pages: [], unattributed: null },
        results: { state: "hidden" }, tone: { state: "hidden" }, remarks: [], latest: null, ...over,
      },
    }) as Parameters<typeof co.tabReadFailed>[0];

  it("counts the calls tab's own failures: the guidance record, the remarks, the results moves and the tone", () => {
    expect(co.tabReadFailed(callsTab({}), "IN")).toBe(false);
    expect(co.tabReadFailed(callsTab({ results: { state: "ok", rows: [] }, tone: { state: "ok", rows: [], output: null, citations: [], pages: [] } }), "IN")).toBe(false);
    expect(co.tabReadFailed(callsTab({ guidance: null }), "IN")).toBe(true);
    expect(co.tabReadFailed(callsTab({ remarks: null }), "IN")).toBe(true);
    expect(co.tabReadFailed(callsTab({ results: { state: "error" } }), "IN")).toBe(true);
    expect(co.tabReadFailed(callsTab({ tone: { state: "error" } }), "IN")).toBe(true);
  });

  it("doesn't count the Rate decisions tab's Nifty 50 source line, which is null when absent too", () => {
    const rates = { stats: [], moves: [], cycles: [], cycleInfo: [], policies: [], niftyAttribution: null };
    expect(co.tabReadFailed({ ...none, rates }, "IN")).toBe(false);
  });
});

describe("the value chain sub-page (readValueChainPage)", () => {
  it("keeps its own profile when every part reads", async () => {
    state.store = views();
    await vc.readValueChainPage("tcs");
    expect(failedRead()).toBe(0);
  });

  it.each(["value_chain", "value_chain_products", "field_policy", "value_chain_group", "related_party_top", "segments", "value_chain_peers"])(
    "is kept on failedRead when %s failed on its own",
    async (view) => {
      state.store = views({}, [view]);
      const out = await vc.readValueChainPage("tcs");
      expect(Object.values(out).filter((part) => part === null)).toHaveLength(1);
      expect(failedRead()).toBe(1);
    },
  );

  it("valueChainReadFailed is false only when no part is null", () => {
    const page = { rows: [], products: [], policies: [], group: [], rpt: [], segments: [], peers: [] };
    expect(vc.valueChainReadFailed(page)).toBe(false);
    for (const key of Object.keys(page)) expect(vc.valueChainReadFailed({ ...page, [key]: null })).toBe(true);
  });
});
