// The Earnings calls tab's reads in waves (#516): a cold render fired up to about ten reads at once into a cold free-plan database,
// and on 13 Nov 2026 they timed out together, the calls list's retry too, so Reliance's tab answered 500 twice with the call
// figures shown (#185). These tests pin what is read when: the calls list and the policies first; then only the panels whose
// field is released; then one read each of the outputs, citations and pages the page quotes. Also what a failed quotes read
// costs. A fake Data API stands in for Supabase (every request is held a few milliseconds, so requests in one wave overlap and
// the most in flight at once is measured); a view missing from the store answers 404, so its read throws.
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it, vi } from "vitest";
import { fakePostgrest, type Row, type Store } from "@/tests/fake-postgrest";
import { call, citation, output, SHOWN, HELD } from "@/tests/call-rows";
import type { ApiDatabase, FieldPolicyRow } from "@/lib/queries/api";

vi.mock("next/cache", () => ({ cacheLife: () => undefined, cacheTag: () => undefined }));
vi.spyOn(console, "error").mockImplementation(() => undefined);

const { readCallsTab, guidanceRecordOpen } = await import("@/lib/queries/calls");

/** A client on the fake whose every request waits `ms` before it answers, with the order requests started in and the most in flight at once. */
function slowClient(store: Store, ms = 5) {
  const fake = fakePostgrest(store);
  const started: string[] = [];
  let inFlight = 0;
  let peak = 0;
  const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    started.push(url.pathname.replace(/^\/rest\/v1\//, ""));
    inFlight++;
    peak = Math.max(peak, inFlight);
    try {
      await new Promise((resolve) => setTimeout(resolve, ms));
      return await fake.fetch(input, init);
    } finally {
      inFlight--;
    }
  };
  const db = createClient<ApiDatabase, "api">("http://fake.local", "anon-key-for-tests", {
    db: { schema: "api" },
    auth: { persistSession: false, autoRefreshToken: false },
    global: { fetch },
  });
  return { db, seen: fake.seen, started, peak: () => peak };
}

const policy = (field_key: string, status: FieldPolicyRow["status"] = "labelled", label: string | null = null): FieldPolicyRow => ({ field_key, status, label });
// Production after #516's re-flips: the summary hidden, the figures and tone labelled, the opening remarks labelled, the outcomes hidden.
const PRODUCTION: FieldPolicyRow[] = [
  policy("ai.call_summary", "hidden"),
  policy("ai.guidance_extraction", "labelled", "AI-extracted"),
  policy("equity.guidance_outcome", "hidden"),
  policy("equity.remarks_change", "labelled", "derived"),
  policy("ai.call_tone", "labelled", "AI-scored"),
];
// Production on 17 Nov 2026 (#620): the outcomes labelled since 14 Nov (#532) and the results-day moves since 16 Nov (#577); the hit
// rates and the summary hidden.
const TODAY: FieldPolicyRow[] = [
  policy("ai.call_summary", "hidden"),
  policy("ai.guidance_extraction", "labelled", "AI-extracted"),
  policy("equity.guidance_outcome", "labelled", "derived"),
  policy("equity.guidance_hit_rate", "hidden"),
  policy("equity.remarks_change", "labelled", "derived"),
  policy("ai.call_tone", "labelled", "AI-scored"),
  policy("equity.results_move", "labelled", "derived"),
];
const EVERY: FieldPolicyRow[] = [...SHOWN, policy("equity.remarks_change", "labelled", "derived"), policy("ai.call_tone", "labelled", "AI-scored"), policy("equity.results_move", "labelled", "derived")];

const toneRow = (over: Record<string, unknown> = {}) => ({
  company_slug: "tcs", call_slug: "q2-fy27", fiscal_label: "Q2 FY27", period_end: "2026-09-30", call_date: null, filed_at: null, exchange: "NSE",
  ai_output_id: 9100, n: 18, mean: 0.8, sd: 0.9, opening_n: 6, opening_mean: 1, answers_n: 12, answers_mean: 0.7, prev_call_slug: null,
  prev_fiscal_label: null, delta: null, se_delta: null, change: null, held_reason: null, rule_version: "r1", policy_label: "AI-scored", ...over,
});
const GUIDANCE_KEYS =
  "company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on";
const guidanceRow = (ai_output_id: number) => ({
  ...Object.fromEntries(GUIDANCE_KEYS.split(", ").map((k) => [k, null])),
  company_slug: "tcs", call_slug: "q1-fy27", ai_output_id, citation_no: 1, target_end: "2027-03-31", metric: "revenue",
});
const citationRow = (ai_output_id: number) => citation(1, { ai_output_id });
const pageRow = (ai_output_id: number) => ({ ai_output_id, citation_no: 1, page_first: 3, page_last: 3, page_url: "https://example.test/p.pdf#page=3" });

function store(policies: FieldPolicyRow[], over: Store = {}): Store {
  return {
    earnings_calls: [call({ summary_output_id: null })] as unknown as Row[],
    field_policy: policies as unknown as Row[],
    call_figures: [],
    remarks_changes: [],
    call_guidance: [guidanceRow(8001)] as unknown as Row[],
    guidance_hit_rates: [],
    results_moves: [],
    series: [{ key: "yf.^NSEI", name: "Nifty 50", market: "IN", attribution: "Source: NSE" }] as unknown as Row[],
    call_tone: [toneRow()] as unknown as Row[],
    ai_outputs: [output({ id: 9100, task: "call_tone" })] as unknown as Row[],
    ai_citations: [citationRow(9002), citationRow(9100), citationRow(8001)] as unknown as Row[],
    citation_pages: [pageRow(9002), pageRow(9100), pageRow(8001)] as unknown as Row[],
    ...over,
  };
}

const WAVE1 = ["earnings_calls", "field_policy"];
const WAVE3 = ["ai_outputs", "ai_citations", "citation_pages"];

describe("the calls tab's waves (#516), one read at a time (#620)", () => {
  // #620 (17 Nov 2026): a cold tab's reads cost about 0.45 to 0.75 s of database time as a visitor, and wave 2 fired five of the
  // dearest at once, so several tabs first-loaded together timed out. The tab now reads one view at a time, in the waves' order.
  it("reads the calls list and the policies first, then the panels, then the quotes, one at a time (production's fields of 13 Nov)", async () => {
    const { db, started, peak } = slowClient(store(PRODUCTION));
    const out = await readCallsTab(db, "tcs", undefined);
    expect(started).toEqual([...WAVE1, "call_figures", "remarks_changes", "call_tone", ...WAVE3]);
    expect(peak()).toBe(1);
    expect(out.tone.state).toBe("ok");
    expect(out.remarks).toEqual([]);
  });

  it("with production's fields of 17 Nov (outcomes and results-day moves shown, hit rates hidden) the guidance record is read, one at a time", async () => {
    const { db, started, peak } = slowClient(store(TODAY));
    const out = await readCallsTab(db, "tcs", undefined);
    expect(started).toEqual([...WAVE1, "call_figures", "remarks_changes", "call_guidance", "results_moves", "series", "call_tone", ...WAVE3]);
    expect(peak()).toBe(1);
    expect(out.guidance?.rows).toHaveLength(1);
    expect(out.guidance?.rate).toBeNull();
    expect(out.results.state).toBe("ok");
  });

  it("with every field open, every read still goes alone: never two in flight, where wave 2 used to hold six", async () => {
    const { db, started, peak } = slowClient(store(EVERY));
    await readCallsTab(db, "tcs", undefined);
    // The Nifty 50's attribution (one `series` read) follows the results rows inside that panel's slot.
    expect(started).toEqual([...WAVE1, "call_figures", "remarks_changes", "call_guidance", "guidance_hit_rates", "results_moves", "series", "call_tone", ...WAVE3]);
    expect(peak()).toBe(1);
  });

  it("stops at a failed figures read: the tab fails and no other panel's read starts", async () => {
    const { db, started } = slowClient(store(EVERY, { call_figures: undefined as unknown as Row[] }));
    await expect(readCallsTab(db, "tcs", undefined)).rejects.toThrow();
    expect(started).toEqual([...WAVE1, "call_figures"]);
  });

  it("a panel's failed read holds back only that panel, and the reads after it still run in turn", async () => {
    const { db, started, peak } = slowClient(store(TODAY, { call_guidance: undefined as unknown as Row[] }));
    const out = await readCallsTab(db, "tcs", undefined);
    expect(out.guidance).toBeNull();
    expect(out.results.state).toBe("ok");
    expect(out.tone.state).toBe("ok");
    expect(started).toEqual([...WAVE1, "call_figures", "remarks_changes", "call_guidance", "results_moves", "series", "call_tone", ...WAVE3]);
    expect(peak()).toBe(1);
  });

  it("reads each of the outputs, citations and pages once, for every output the page quotes, and gives each panel its own", async () => {
    const { db, seen } = slowClient(store(EVERY, { earnings_calls: [call()] as unknown as Row[], ai_outputs: [output(), output({ id: 9100, task: "call_tone" })] as unknown as Row[] }));
    const out = await readCallsTab(db, "tcs", undefined);
    for (const view of WAVE3) expect(seen.filter((s) => s.view === view)).toHaveLength(1);
    expect(seen.find((s) => s.view === "ai_outputs")?.params.get("id")).toBe("in.(9001,9100)");
    expect(seen.find((s) => s.view === "ai_citations")?.params.get("ai_output_id")).toBe("in.(8001,9001,9002,9100)");
    expect(out.summary?.id).toBe(9001);
    expect(out.citations.map((c) => c.ai_output_id)).toEqual([9002]);
    expect(out.pages.map((p) => p.ai_output_id)).toEqual([9002]);
    expect(out.guidance?.citations.map((c) => c.ai_output_id)).toEqual([8001]);
    expect(out.guidance?.pages.map((p) => p.ai_output_id)).toEqual([8001]);
    expect(out.tone.state === "ok" && out.tone.output?.id).toBe(9100);
    expect(out.tone.state === "ok" && out.tone.citations.map((c) => c.ai_output_id)).toEqual([9100]);
  });

  it("with no published call reads only the calls list, the policies and the discovery window", async () => {
    const { db, started } = slowClient(store(EVERY, { earnings_calls: [], earnings_latest: [] }));
    const out = await readCallsTab(db, "tcs", undefined);
    expect([...started].sort()).toEqual(["earnings_calls", "earnings_latest", "field_policy"]);
    expect(out.selected).toBeNull();
    // Nothing failed: these are not the failure values tabReadFailed looks for.
    expect(out.guidance).not.toBeNull();
    expect(out.remarks).toEqual([]);
  });
});

describe("the guidance record's read (#516)", () => {
  it("is open only with the figures and their outcomes both released", () => {
    expect(guidanceRecordOpen(SHOWN)).toBe(true);
    expect(guidanceRecordOpen(PRODUCTION)).toBe(false);
    expect(guidanceRecordOpen(HELD)).toBe(false);
    expect(guidanceRecordOpen([])).toBe(false);
  });

  it("makes no request while the outcomes are hidden, and gives the held record (empty, not a failure)", async () => {
    const { db, seen } = slowClient(store(PRODUCTION, { call_guidance: undefined as unknown as Row[], guidance_hit_rates: undefined as unknown as Row[] }));
    const out = await readCallsTab(db, "tcs", undefined);
    expect(seen.some((s) => s.view === "call_guidance" || s.view === "guidance_hit_rates")).toBe(false);
    expect(out.guidance).toEqual({ rows: [], rate: null, citations: [], pages: [], unattributed: 0 });
  });
});

describe("a failed read of the quotes (#516)", () => {
  it("fails the tab when the call has a summary or figures, as a failed citations read always did", async () => {
    const { db } = slowClient(store(EVERY, { ai_citations: undefined as unknown as Row[] }));
    await expect(readCallsTab(db, "tcs", undefined)).rejects.toThrow();
  });

  it("holds back only the record and the tone when the call has neither", async () => {
    const bare = [call({ summary_output_id: null, figures_output_id: null })] as unknown as Row[];
    const { db } = slowClient(store(EVERY, { earnings_calls: bare, ai_citations: undefined as unknown as Row[] }));
    const out = await readCallsTab(db, "tcs", undefined);
    expect(out.guidance).toBeNull();
    expect(out.tone).toEqual({ state: "error" });
    expect(out.calls).toHaveLength(1);
    expect(out.remarks).toEqual([]);
  });

  it("leaves the tone panel working when the call has no tone row, even if the quotes read fails", async () => {
    const bare = [call({ summary_output_id: null, figures_output_id: null })] as unknown as Row[];
    const { db } = slowClient(store(PRODUCTION, { earnings_calls: bare, call_tone: [], ai_citations: undefined as unknown as Row[] }));
    const out = await readCallsTab(db, "tcs", undefined);
    // Nothing to quote at all: no quotes read is made, so nothing fails.
    expect(out.tone).toEqual({ state: "ok", rows: [], output: null, citations: [], pages: [] });
  });
});
