// The Guidance record panel rendered to HTML as the server sends it (2B-19b, #267; Spec 02B §8, §5.10; Plan 04b P12; 02B.1 C6):
// held back while its fields are hidden, "Too few scored targets" below 5, the rate with n and the window from 5, pending and
// not-scored rows in their own group, outcomes in words (no colour), the quote chip and page link, the left-out line for 0, 1
// and 12 statements, the banned-words scan and the exact-count read. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { guidanceFiguresOpen, guidanceGroups, guidanceHitRateOpen, hitRate, outcomeText, unattributedAll, unattributedLine } from "@/lib/equity/guidance";
import type { CallGuidanceRow, GuidanceHitRateRow } from "@/lib/queries/api";
import { readGuidanceRecord, type CallsTabData, type GuidanceRecordData } from "@/lib/queries/calls";
import { bannedCallWordsIn } from "@/tests/banned-words";
import { HELD, SHOWN, calls, citation, pageRow } from "@/tests/call-rows";
import { page } from "@/tests/company-rows";
import { fakeClient, type Row } from "@/tests/fake-postgrest";

const text = (markup: string) =>
  markup.replace(/<span class="sr-only">[^<]*<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (c: CallsTabData) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "calls", overview: null, calls: c })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

function g(over: Partial<CallGuidanceRow> = {}): CallGuidanceRow {
  return {
    company_slug: "tcs", company_name: "Tata Consultancy Services Ltd.", short_name: "TCS", call_slug: "q2-fy27", fiscal_label: "Q2 FY27", call_date: "2026-10-09",
    metric: "ebit_margin", metric_label: "EBIT margin", kind: "range", value: null, low: 26, high: 28, unit: "pct", direction: null, comparison: null,
    period_text: "FY27", period_kind: "year", target_start: "2026-04-01", target_end: "2027-03-31", basis: "consolidated", growth_basis: "level", scope_text: null,
    vintage_date: "2026-10-09", ai_output_id: 9002, citation_no: 1, not_scored_reason: null, extraction_label: "AI-extracted", outcome_label: "derived",
    is_first: true, revision: "first", counted: true, outcome: "delivered", relation: "within", actual: 27, reason: null, first_call_slug: "q2-fy27", first_stated_on: "2026-10-09", ...over,
  };
}
const rate = (over: Partial<GuidanceHitRateRow> = {}): GuidanceHitRateRow => ({
  scope: "company", scope_key: "tcs", scope_label: "TCS", delivered: 4, missed: 2, n: 6, rate: 4 / 6, first_stated_from: "2025-04-10", periods_to: "2026-03-31", outcome_label: "derived", ...over,
});
const record = (over: Partial<GuidanceRecordData> = {}): GuidanceRecordData => ({
  rows: [g()], rate: rate(), unattributed: 0, citations: [citation(1, { ai_output_id: 9002, quote: "Margin words." })], pages: [pageRow(1, 7, 7, { ai_output_id: 9002 })], ...over,
});
const tab = (over: Partial<GuidanceRecordData> = {}, policies = SHOWN) => calls({ policies, guidance: record(over) });

describe("the panel's states", () => {
  it("is held back while the outcome or the extraction field is hidden", () => {
    const outcomeHidden = SHOWN.map((p) => (p.field_key === "equity.guidance_outcome" ? { ...p, status: "hidden" as const } : p));
    for (const policies of [outcomeHidden, HELD]) {
      const t = text(html(tab({}, policies)));
      expect(t).toContain("Guidance record held back until it passes review");
      expect(t).not.toContain("Delivered (within the range)");
    }
  });

  it("says too few scored targets below 5, with n from the view, and still lists the targets", () => {
    const t = text(html(tab({ rate: rate({ n: 3, delivered: 2, missed: 1, rate: null }) })));
    expect(t).toContain("Too few scored targets for a hit rate (n = 3; needs 5)");
    expect(t).toContain("EBIT margin 26% to 28%");
  });

  it("shows the rate with n and the window from the view's own columns from 5 targets", () => {
    const t = text(html(tab()));
    expect(t).toContain("Delivered of scored targets");
    expect(t).toContain("67%");
    expect(t).toContain("Delivered 4 of 6 scored targets; missed 2");
    expect(t).toContain("Stated from 10 Apr 2025, for periods to 31 Mar 2026");
  });

  it("treats a missing hit-rate row as n = 0", () => {
    expect(text(html(tab({ rate: null })))).toContain("(n = 0; needs 5)");
  });
});

describe("the rows", () => {
  const pending = g({ outcome: "pending", relation: null, actual: null, counted: false, metric: "revenue", metric_label: "Revenue", period_text: "FY28", target_end: "2028-03-31" });
  const notScored = g({ outcome: "not_scored", relation: null, actual: null, counted: false, reason: "no filed figure for the period", metric: "capex", metric_label: "Capex", target_end: "2027-03-31" });
  const missed = g({ outcome: "missed", relation: "below", actual: 24.5, metric: "margin_b", metric_label: "Operating margin", target_end: "2026-03-31", revision: "raised", is_first: false });

  it("lists scored rows first and pending and not-scored rows after, in their own table", () => {
    const groups = guidanceGroups([pending, notScored, missed, g()], [], []);
    expect(groups.scored.map((r) => r.metric)).toEqual(["EBIT margin", "Operating margin"]);
    expect(groups.other.map((r) => r.metric)).toEqual(["Revenue", "Capex"]);
    const out = html(tab({ rows: [pending, notScored, missed, g()] }));
    expect(out).toContain("guidance targets scored as delivered or missed");
    expect(out).toContain("guidance targets pending or not scored");
  });

  it("writes outcomes in words with their relation, and the actual, revision and whether it is in the rate", () => {
    expect(outcomeText(g())).toBe("Delivered (within the range)");
    expect(outcomeText(missed)).toBe("Missed (below the range)");
    expect(outcomeText(pending)).toBe("Pending");
    expect(outcomeText(notScored)).toBe("Not scored: no filed figure for the period");
    const t = text(html(tab({ rows: [missed, notScored] })));
    expect(t).toContain("Missed (below the range)");
    expect(t).toContain("24.5%");
    expect(t).toContain("Raised, Q2 FY27 call, 9 Oct 2026");
    expect(t).toContain("Not scored: no filed figure for the period");
    expect(t).toMatch(/Operating margin .* Yes/);
    expect(t).toMatch(/Capex .* No/);
  });

  it("uses no colour for an outcome and no Delta", () => {
    const out = html(tab({ rows: [missed, g()] }));
    const panel = out.slice(out.indexOf("panel-guidance-record"));
    expect(panel).not.toMatch(/text-(up|down|positive|negative|green|red)|bg-(up|down)|<svg/);
  });

  it("shows the quote chip and the page link from the page view", () => {
    const out = html(tab());
    const panel = text(out.slice(out.indexOf("panel-guidance-record")));
    expect(panel).toContain("p. 7 ↗");
    expect(out).toContain('href="https://example.test/tcs-q2.pdf#page=7"');
    expect(out).toContain("Page 7 of the PDF at the source");
  });

  it("shows no page and an em dash where the views have no page row or citation", () => {
    const out = html(tab({ citations: [], pages: [] }));
    const t = text(out.slice(out.indexOf("panel-guidance-record")));
    expect(t).toContain("—");
    expect(t).not.toContain("p. 7");
  });
});

describe("the left-out line (02B.1 C6)", () => {
  it("is hidden for 0 and for null (gated)", () => {
    expect(unattributedLine(0)).toBeNull();
    expect(unattributedLine(null)).toBeNull();
    expect(text(html(tab({ unattributed: 0 })))).not.toContain("left out of this record");
    expect(text(html(tab({ unattributed: null })))).not.toContain("left out of this record");
  });
  it("is singular for 1 and plural for 12, and sits under the rate and under the n < 5 note", () => {
    expect(unattributedLine(1)).toBe("1 forward-looking statement by a speaker whose role couldn't be read is listed on its call and left out of this record.");
    expect(unattributedLine(12)).toBe("12 forward-looking statements by speakers whose role couldn't be read are listed on their calls and left out of this record.");
    expect(text(html(tab({ unattributed: 1 })))).toContain("1 forward-looking statement by a speaker");
    expect(text(html(tab({ unattributed: 12, rate: rate({ n: 2, rate: null }) })))).toContain("12 forward-looking statements by speakers");
  });
});

describe("the count left out, rebuilt from the calls (#342)", () => {
  const withCount = (n: number | null) => ({ unattributed_targets: n });
  it("is null while the extraction field is held back, whatever the calls hold", () => {
    expect(unattributedAll([withCount(2), withCount(null)], false)).toBeNull();
    expect(unattributedAll([], false)).toBeNull();
  });
  it("is the sum of the calls' counts once the field is open, a null counting as 0", () => {
    expect(unattributedAll([withCount(1), withCount(null), withCount(2)], true)).toBe(3);
    expect(unattributedAll([withCount(null), withCount(null)], true)).toBe(0);
  });
  it("is 0, not null, for a company with no calls once the field is open (the view's coalesce)", () => {
    expect(unattributedAll([], true)).toBe(0);
  });
  it("reads the extraction field's policy as the views' fig_ok does: shown or labelled, not hidden, not another field", () => {
    expect(guidanceFiguresOpen(SHOWN)).toBe(true);
    expect(guidanceFiguresOpen([{ field_key: "ai.guidance_extraction", status: "shown", label: null }])).toBe(true);
    expect(guidanceFiguresOpen(HELD)).toBe(false);
    expect(guidanceFiguresOpen([{ field_key: "ai.call_summary", status: "shown", label: null }])).toBe(false);
    expect(guidanceFiguresOpen([])).toBe(false);
  });
  it("gives the line the panel shows: 3 from the calls reads as the plural line, none as no line", () => {
    expect(unattributedLine(unattributedAll([withCount(1), withCount(2)], true))).toContain("3 forward-looking statements by speakers");
    expect(unattributedLine(unattributedAll([withCount(0)], true))).toBeNull();
    expect(unattributedLine(unattributedAll([withCount(2)], false))).toBeNull();
  });
});

describe("the hit rate and the words", () => {
  it("writes the rate as a whole percent from the stored fraction, and the note below 5", () => {
    expect(hitRate(rate())).toMatchObject({ kind: "rate", value: "67%" });
    expect(hitRate(rate({ rate: null, n: 4 }))).toEqual({ kind: "few", note: "Too few scored targets for a hit rate (n = 4; needs 5)" });
  });
  it("never uses the banned words in the panel's own text", () => {
    const rows = [g(), g({ outcome: "missed", relation: "short_of" }), g({ outcome: "pending", relation: null }), g({ outcome: "not_scored", relation: null, reason: "x" })];
    expect(bannedCallWordsIn(text(html(tab({ rows, unattributed: 12 }))))).toEqual([]);
    expect(bannedCallWordsIn(text(html(tab({}, HELD))))).toEqual([]);
  });
});

describe("the read", () => {
  const many = (n: number): Row[] => Array.from({ length: n }, (_, i) => ({ ...g({ ai_output_id: 9002, citation_no: (i % 9) + 1, metric: "m" + String(i).padStart(4, "0") }) }));
  it("reads the guidance list whole against the exact count, past one request's cap", async () => {
    const { db, seen } = fakeClient({
      call_guidance: many(1200),
      guidance_hit_rates: [rate() as unknown as Row],
      ai_citations: [],
      citation_pages: [],
    });
    const got = await readGuidanceRecord(db, "tcs");
    expect(got.rows).toHaveLength(1200);
    expect(got.rate?.n).toBe(6);
    expect(seen.every((s) => s.countExact)).toBe(true);
    expect(seen.filter((s) => s.view === "call_guidance").length).toBeGreaterThan(1);
  });
  it("no longer asks api.earnings_latest for the count left out, which builds every call of every company (#342)", async () => {
    // earnings_latest is absent from the store, so a request for it would answer 404 and the read would throw.
    const { db, seen } = fakeClient({ call_guidance: [], guidance_hit_rates: [], ai_citations: [], citation_pages: [] });
    const got = await readGuidanceRecord(db, "tcs");
    // Its quotes' citations and pages are read with the selected call's, in one read each (#516).
    expect(Object.keys(got).sort()).toEqual(["rate", "rows"]);
    expect(seen.some((s) => s.view === "earnings_latest")).toBe(false);
  });
});

// 0048 (#532; Anand's option A, 13 Nov 2026): outcomes show per target once equity.guidance_outcome does; the hit rate, first stated,
// the latest statement's revision and "in the rate?" wait for equity.guidance_hit_rate (the recall check). The view gives those
// columns as null meanwhile, so the rows here are what it returns then.
describe("outcomes shown, hit rate held back (#532)", () => {
  const withoutKey = SHOWN.filter((p) => p.field_key !== "equity.guidance_hit_rate");
  const keyHidden = SHOWN.map((p) => (p.field_key === "equity.guidance_hit_rate" ? { ...p, status: "hidden" as const } : p));
  const gated = (over: Partial<CallGuidanceRow> = {}) => g({ is_first: null, revision: null, counted: null, first_call_slug: null, first_stated_on: null, ...over });
  const missed = gated({ outcome: "missed", relation: "below", actual: 24.5, metric: "margin_b", metric_label: "Operating margin", target_end: "2026-03-31" });
  const pending = gated({ outcome: "pending", relation: null, actual: null, metric: "revenue", metric_label: "Revenue", period_text: "FY28", target_end: "2028-03-31" });
  const panelOf = (out: string) => out.slice(out.indexOf("panel-guidance-record"));
  const held = (policies = withoutKey) => html(tab({ rows: [gated(), missed, pending], rate: null }, policies));

  it("reads the key as the views do: shown or labelled, not hidden, no row means hidden", () => {
    expect(guidanceHitRateOpen(SHOWN)).toBe(true);
    expect(guidanceHitRateOpen([{ field_key: "equity.guidance_hit_rate", status: "shown", label: null }])).toBe(true);
    expect(guidanceHitRateOpen(keyHidden)).toBe(false);
    expect(guidanceHitRateOpen(withoutKey)).toBe(false);
    expect(guidanceHitRateOpen([{ field_key: "equity.guidance_outcome", status: "labelled", label: "derived" }])).toBe(false);
  });

  it("says the hit rate is held back, and shows no rate, n or window, with the key hidden or without its row", () => {
    for (const policies of [withoutKey, keyHidden]) {
      // a rate row passed in anyway (the read skips it while the key is hidden) is still not shown
      for (const r of [null, rate()]) {
        const t = text(panelOf(html(tab({ rows: [gated(), missed, pending], rate: r }, policies))));
        expect(t).toContain("Hit rate held back: the AI extraction may not list every target on a call yet.");
        expect(t).not.toContain("Delivered of scored targets");
        expect(t).not.toContain("Too few scored targets");
        expect(t).not.toContain("67%");
        expect(t).not.toContain("Delivered 4 of 6");
        expect(t).not.toContain("Stated from");
      }
    }
  });

  it("still lists each target with its outcome, relation, actual and quote", () => {
    const out = panelOf(held());
    const t = text(out);
    expect(t).toContain("EBIT margin 26% to 28%");
    expect(t).toContain("Delivered (within the range)");
    expect(t).toContain("Missed (below the range)");
    expect(t).toContain("24.5%");
    expect(t).toContain("Pending");
    expect(t).toContain("p. 7 ↗");
    expect(out).toContain("guidance targets scored as delivered or missed");
    expect(out).toContain("guidance targets pending or not scored");
  });

  it("leaves out the first-stated, latest-statement and in-the-rate columns, and every value they would hold", () => {
    const t = text(panelOf(held()));
    for (const header of ["Metric", "Figure as stated", "Period", "Basis", "Actual", "Outcome", "Quote", "Page"]) expect(t).toContain(header);
    for (const header of ["First stated", "Latest statement", "In the rate?"]) expect(t).not.toContain(header);
    expect(t).not.toMatch(/\b(Yes|No|Raised|Cut|Reiterated|Restated|Stated, Q2 FY27 call)\b/);
  });

  it("adds the subtitle's line once the record shows, in both states, and not while the record is held back", () => {
    const line = "Targets are AI-extracted, and a call's list may not be complete.";
    expect(text(panelOf(held()))).toContain(line);
    expect(text(panelOf(html(tab())))).toContain(line);
    expect(text(panelOf(html(tab({}, HELD))))).not.toContain(line);
  });

  it("shows the rate and every column once both keys show", () => {
    const t = text(panelOf(html(tab())));
    expect(t).toContain("Delivered of scored targets");
    expect(t).not.toContain("Hit rate held back");
    for (const header of ["First stated", "Latest statement", "In the rate?"]) expect(t).toContain(header);
  });

  it("uses no colour and none of the banned words while the hit rate is held back", () => {
    // the held note's info icon is the one svg (StateNote's); no Delta triangle, no up or down colour
    const out = panelOf(held()).replace(/<svg[^>]*lucide-info[\s\S]*?<\/svg>/g, "");
    expect(out).not.toMatch(/text-(up|down|positive|negative|green|red)|bg-(up|down)|<svg/);
    expect(bannedCallWordsIn(text(out))).toEqual([]);
  });

  it("writes an em dash, not No, for in the rate when the view gives null", () => {
    expect(guidanceGroups([gated()], [], []).scored[0].inRate).toBe("—");
    expect(guidanceGroups([g({ counted: false })], [], []).scored[0].inRate).toBe("No");
  });

  it("doesn't ask for the hit rate while its key is hidden, and asks once it shows", async () => {
    const { db, seen } = fakeClient({ call_guidance: [g() as unknown as Row], guidance_hit_rates: [rate() as unknown as Row] });
    const got = await readGuidanceRecord(db, "tcs", false);
    expect(got.rate).toBeNull();
    expect(got.rows).toHaveLength(1);
    expect(seen.some((s) => s.view === "guidance_hit_rates")).toBe(false);
    const both = fakeClient({ call_guidance: [g() as unknown as Row], guidance_hit_rates: [rate() as unknown as Row] });
    expect((await readGuidanceRecord(both.db, "tcs", true)).rate?.n).toBe(6);
    expect(both.seen.some((s) => s.view === "guidance_hit_rates")).toBe(true);
  });
});
