// The Financials tab's "Segment changes" panel (DD-02, #410; Plan 05 §1.2, D8) from api.segment_changes (0038, #409): held back
// while equity.segment_changes is hidden (the held note and nothing else), each kind in words with its own neutral sentence, the
// segments before and after as filed with each side linked to its filing, the three states through StateNote, no colour, the
// banned-words scan and no motive words, and nothing that can push the page sideways at 320 px. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { KIND_SENTENCE, KIND_TITLE, SEGMENT_HELD, segmentModel } from "@/components/company/SegmentChangesPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FieldPolicyRow, SegmentChangeRow, TaskFreshnessRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { financial, page } from "@/tests/company-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

const SHOWN: FieldPolicyRow[] = [{ field_key: "equity.segment_changes", status: "labelled", label: "As filed" }];
const HIDDEN: FieldPolicyRow[] = [{ field_key: "equity.segment_changes", status: "hidden", label: "As filed" }];

function seg(over: Partial<SegmentChangeRow> = {}): SegmentChangeRow {
  return {
    company_slug: "tcs", company_name: "Tata Consultancy Services Ltd.", period_end: "2020-12-31", state: "changed", state_reason: null,
    quarters_compared: 4, change_no: 1, kind: "merged", segments_before: ["Petrochemicals", "Refining"], segments_after: ["Oil to Chemicals (O2C)"],
    revenue_ratio: 0.913, previous_period_end: "2020-09-30", latest_filing_url: "https://example.test/filing/latest",
    latest_filed_at: "2021-01-22T13:05:00+00:00", previous_filing_url: "https://example.test/filing/previous", previous_filed_at: "2020-10-30T12:00:00+00:00",
    rule_version: 1, source_key: "nse", ...over,
  };
}
const state = (s: SegmentChangeRow["state"], over: Partial<SegmentChangeRow> = {}) =>
  seg({ state: s, change_no: null, kind: null, segments_before: null, segments_after: null, revenue_ratio: null, previous_period_end: null, previous_filing_url: null, previous_filed_at: null, ...over });

const FRESH: TaskFreshnessRow[] = [
  { task_key: "equity.segment_changes", last_success_at: "2021-01-25T03:00:00Z", last_run_at: "2021-01-25T03:00:00Z", last_status: "succeeded", overdue_after: "10 days" } as unknown as TaskFreshnessRow,
];

const html = (segmentChanges: SegmentChangeRow[] | null | undefined, policies: FieldPolicyRow[] | null | undefined, freshness: TaskFreshnessRow[] | null = null) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView
        data={page({ tab: "financials", overview: null, financials: { financials: [financial("quarter", "2026-06-30")], metrics: [], promoter: [], segmentChanges, policies } })}
        basis="consolidated"
        freshness={freshness}
      />
    </TooltipProvider>,
  );

/** The panel's own markup: from its section's opening tag to the section's close. */
function panel(out: string): string {
  const start = out.lastIndexOf("<section", out.indexOf('id="panel-segment-changes"'));
  expect(start).toBeGreaterThanOrEqual(0);
  return out.slice(start, out.indexOf("</section>", start) + "</section>".length);
}

describe("the Segment changes panel while equity.segment_changes is hidden", () => {
  it.each([
    ["no policy row (fails closed)", undefined],
    ["the policy read failed", null],
    ["the policy is hidden", HIDDEN],
  ])("shows the held note and nothing else: %s", (_, policies) => {
    const p = panel(html([seg(), seg({ change_no: 2, kind: "dropped", segments_before: ["Retail"], segments_after: [] })], policies));
    const t = text(p);
    expect(t).toContain("Segment changes");
    expect(t).toContain(SEGMENT_HELD);
    for (const word of ["Petrochemicals", "Oil to Chemicals", "Retail", "Before", "After", "Merged", "Quarter ended", "Latest filing", "Data to", "Update delayed", "As filed"]) {
      expect(t).not.toContain(word);
    }
    expect(p).not.toContain("example.test/filing");
    expect(p).toContain('data-state-kind="held"');
    expect(t).toContain("Source: NSE");
  });
  it("leaves the other panels of the tab as they are", () => {
    const t = text(html(undefined, undefined));
    expect(t).toContain("Quarterly results");
    expect(t).toContain("Promoter holding");
    expect(t).toContain("Corporate actions");
  });
});

describe("the Segment changes panel once released", () => {
  it("shows a merge: the kind in words, its sentence, the segments before and after as filed, each side linked to its filing", () => {
    const p = panel(html([seg()], SHOWN, FRESH));
    const t = text(p);
    expect(t).toContain("As filed");
    expect(t).toContain("The results for the quarter ended 31 Dec 2020 against the four quarters before it: consolidated, segments as filed.");
    expect(t).toContain("Merged Reported as one segment in the latest results; reported separately in the previous four.");
    expect(t).toContain("Before Petrochemicals Refining Quarter ended 30 Sep 2020, filed 30 Oct 2020");
    expect(t).toContain("After Oil to Chemicals (O2C) Quarter ended 31 Dec 2020, filed 22 Jan 2021");
    expect(p).toContain('href="https://example.test/filing/previous"');
    expect(p).toContain('href="https://example.test/filing/latest"');
    expect(t).toContain("Source: NSE");
    expect(t).toContain("Latest filing 22 Jan 2021");
    expect(t).toContain("Data to 31 Dec 2020");
    expect(t).not.toContain(SEGMENT_HELD);
  });

  it.each(Object.keys(KIND_TITLE) as (keyof typeof KIND_TITLE)[])("renders the %s kind with its own sentence", (kind) => {
    const sides: Record<string, Pick<SegmentChangeRow, "segments_before" | "segments_after">> = {
      dropped: { segments_before: ["Retail"], segments_after: [] },
      merged: { segments_before: ["Petrochemicals", "Refining"], segments_after: ["Oil to Chemicals (O2C)"] },
      renamed: { segments_before: ["Digital services"], segments_after: ["Digital and media"] },
      multi_to_single: { segments_before: ["Cement", "Chemicals"], segments_after: [] },
      single_to_multi: { segments_before: [], segments_after: ["Mining", "Power"] },
      other: { segments_before: ["Cement", "Chemicals"], segments_after: ["Building materials", "Paints"] },
    };
    const t = text(panel(html([seg({ kind, revenue_ratio: kind === "merged" ? 0.9 : null, ...sides[kind] })], SHOWN)));
    expect(t).toContain(`${KIND_TITLE[kind]} ${KIND_SENTENCE[kind]}`);
    for (const s of [...(sides[kind].segments_before ?? []), ...(sides[kind].segments_after ?? [])]) expect(t).toContain(s);
  });

  it("says what an empty side means: not reported after a drop, one reportable segment on the single side", () => {
    expect(text(panel(html([seg({ kind: "dropped", segments_before: ["Retail"], segments_after: [] })], SHOWN)))).toContain("After Not reported");
    expect(text(panel(html([seg({ kind: "multi_to_single", segments_before: ["A", "B"], segments_after: [] })], SHOWN)))).toContain("After One reportable segment");
    expect(text(panel(html([seg({ kind: "single_to_multi", segments_before: [], segments_after: ["A", "B"] })], SHOWN)))).toContain("Before One reportable segment");
  });

  it("lists several flags in the view's order", () => {
    const rows = [seg({ change_no: 2, kind: "dropped", segments_before: ["Second"], segments_after: [] }), seg({ change_no: 1, kind: "dropped", segments_before: ["First"], segments_after: [] })];
    const t = text(panel(html(rows, SHOWN)));
    expect(t.indexOf("First")).toBeLessThan(t.indexOf("Second"));
    expect(t.match(/Dropped/g)).toHaveLength(2);
  });

  it("links a filing only when its address is https", () => {
    const p = panel(html([seg({ previous_filing_url: "javascript:alert(1)", latest_filing_url: null })], SHOWN));
    expect(p).not.toContain("javascript:");
    expect(p).not.toContain("<a ");
    expect(text(p)).toContain("Quarter ended 30 Sep 2020, filed 30 Oct 2020");
  });

  it("says one reportable segment for a single-segment filer", () => {
    const p = panel(html([state("single_segment", { period_end: "2026-09-30" })], SHOWN));
    expect(text(p)).toContain("One reportable segment, as filed in the results for the quarter ended 30 Sep 2026.");
    expect(p).toContain('data-state-kind="empty"');
  });

  it("says none is flagged when the latest quarter keeps every segment of the four before", () => {
    const t = text(panel(html([state("none_flagged", { period_end: "2026-09-30" })], SHOWN)));
    expect(t).toContain("No segment flagged: each segment reported in the four quarters before is in the results for the quarter ended 30 Sep 2026.");
  });

  it("says when the quarters weren't compared, with how many of the four are on record", () => {
    const t = text(panel(html([state("not_compared", { period_end: "2026-09-30", quarters_compared: 3, state_reason: "3 of the 4 earlier quarters have a line-up" })], SHOWN)));
    expect(t).toContain("Not compared: segments are on record for 3 of the 4 quarters before the quarter ended 30 Sep 2026, and the comparison needs all four.");
  });

  it("says nothing is published for a company with no line-up, and couldn't load when the read failed", () => {
    expect(text(panel(html([], SHOWN)))).toContain("No segments from the consolidated results are published for this company yet.");
    expect(text(panel(html(null, SHOWN)))).toContain("Couldn't load this panel. Try again.");
    expect(segmentModel(undefined, SHOWN)).toEqual({ kind: "error" });
  });

  it("names BSE when the latest filing came from BSE", () => {
    expect(text(panel(html([seg({ source_key: "bse" })], SHOWN)))).toContain("Source: BSE");
  });
});

describe("words, never colour", () => {
  const all = (Object.keys(KIND_TITLE) as (keyof typeof KIND_TITLE)[]).map((kind, i) => seg({ change_no: i + 1, kind, revenue_ratio: kind === "merged" ? 0.9 : null }));
  const outs = [
    html(all, SHOWN, FRESH),
    html([state("single_segment")], SHOWN),
    html([state("none_flagged")], SHOWN),
    html([state("not_compared", { quarters_compared: 2 })], SHOWN),
    html([], SHOWN),
    html(null, SHOWN),
    html(all, HIDDEN),
  ].map(panel);

  it("gives no flag or state a market colour", () => {
    for (const p of outs) expect(p).not.toMatch(/text-(up|down)|bg-(up|down)|border-(up|down)|#[0-9a-f]{3,6}\b/i);
  });

  it("uses none of the banned words and claims no motive in the panel's own text", () => {
    const own = outs.map((p) => text(p).replace(/Petrochemicals|Refining|Oil to Chemicals \(O2C\)/g, "")).join(" ");
    expect(bannedWordsIn(own)).toEqual([]);
    expect(own).not.toMatch(/\b(hid|hide|hiding|conceal\w*|quietly|bur(y|ied)|obscur\w*|avoid\w*|motive|because|so as to|in order to|to stop)\b/i);
  });
});

describe("at 320 px", () => {
  it("lets every segment name and filing line wrap inside the panel: no nowrap, no fixed width wider than the term column", () => {
    const long = "A very long reportable segment description as filed by the company with no natural break".replace(/ /g, "_");
    const p = panel(html([seg({ segments_before: [long], segments_after: [long] })], SHOWN));
    // The body only: the header's short "As filed" tag keeps to one line, as every Qualifier does.
    const body = p.slice(p.indexOf('<div class="min-w-0 flex-1'), p.lastIndexOf('<div class="mt-auto'));
    expect(body).toContain(long);
    expect(body).not.toContain("whitespace-nowrap");
    expect(body).not.toMatch(/(?<![\w-])w-(?!14\b)(\d+|\[)/);
    expect(body).not.toMatch(/(?<![\w-])min-w-(?!0\b)/);
    const dds = p.match(/<dd class="[^"]*"/g) ?? [];
    expect(dds).toHaveLength(2);
    for (const dd of dds) {
      expect(dd).toContain("min-w-0");
      expect(dd).toContain("break-words");
    }
  });
});
