// The Financials tab's KPI-table pilot panel (DD-06, #514; Plan 05 §1.3, D8) from api.kpi_changes (0047, #512): only on the 7
// pilot companies' tabs; held back while equity.kpi_changes is hidden (the held note and nothing else); each flag's kind in words
// with a sentence the rule makes true, the last value as printed with its quarter, "reason stated" and "an analyst asked" as
// verified quotes or the "no" words that say whether a call transcript was there to search, both documents linked; the pilot line
// and the method's known miss; no colour; the banned-words scan (Spec 02A §5.9, Spec 02B §8) and no motive words; nothing that can
// push the page sideways at 320 px. Test rows, not real figures.
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { KPI_METHOD_LINE, KPI_PILOT_LINE } from "@/components/company/KpiChangesPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { KPI_HELD, KPI_KIND_TITLE, KPI_PILOT, isKpiPilot, kpiModel, kpiSentence } from "@/lib/equity/kpi";
import type { CompanyFinancialRow, EarningsCallRow, FieldPolicyRow, KpiChangeRow, TaskFreshnessRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { call } from "@/tests/call-rows";
import { company, financial, page } from "@/tests/company-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

const SHOWN: FieldPolicyRow[] = [{ field_key: "equity.kpi_changes", status: "labelled", label: "Pilot" }];
const HIDDEN: FieldPolicyRow[] = [{ field_key: "equity.kpi_changes", status: "hidden", label: "Pilot" }];

function flag(over: Partial<KpiChangeRow> = {}): KpiChangeRow {
  return {
    company_slug: "tcs", company_name: "Tata Consultancy Services Ltd.", period_end: "2025-09-30", fiscal_label: "Q2 FY26",
    metric_label: "Attrition, LTM", dict_key: "attrition_ltm", kind: "missing", last_value: 13.8, last_value_unit: "%",
    last_value_quarter: "Q1 FY26", corporate_action_note: null, reason_stated: false, reason_quote: null, reason_page: null,
    analyst_asked: true, analyst_quote: "Could you say something about attrition this quarter?", analyst_page: 7,
    current_document_url: "https://example.test/kpi/q2fy26.pdf", current_filed_at: "2025-10-09T12:00:00+00:00", current_quarter: "Q2 FY26",
    previous_document_urls: ["https://example.test/kpi/q2fy25.pdf", "https://example.test/kpi/q3fy25.pdf", "https://example.test/kpi/q4fy25.pdf", "https://example.test/kpi/q1fy26.pdf"],
    rule_version: 1, source_key: "nse", ...over,
  };
}

const CALLS: EarningsCallRow[] = [call({ fiscal_label: "Q2 FY26", call_slug: "q2-fy26", period_end: "2025-09-30", official_url: "https://example.test/tcs-q2fy26.pdf" })];

const FRESH: TaskFreshnessRow[] = [
  { task_key: "equity.kpi_tables", last_success_at: "2026-11-15T03:00:00Z", last_run_at: "2026-11-15T03:00:00Z", last_status: "succeeded", overdue_after: "10 days" } as unknown as TaskFreshnessRow,
];
const STATEMENTS: CompanyFinancialRow[] = [financial("quarter", "2026-09-30")];

const html = (
  kpiChanges: KpiChangeRow[] | null | undefined,
  policies: FieldPolicyRow[] | null | undefined,
  kpiCalls: EarningsCallRow[] | null | undefined = CALLS,
  slug = "tcs",
) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView
        data={page({
          tab: "financials",
          company: company({ slug }),
          overview: null,
          financials: { financials: STATEMENTS, metrics: [], promoter: [], segmentChanges: [], policies, kpiChanges, kpiCalls },
        })}
        basis="consolidated"
        freshness={FRESH}
      />
    </TooltipProvider>,
  );

/** The panel's own markup: from its section's opening tag to the section's close. */
function panel(out: string): string {
  const start = out.lastIndexOf("<section", out.indexOf('id="panel-kpi-changes"'));
  expect(start).toBeGreaterThanOrEqual(0);
  return out.slice(start, out.indexOf("</section>", start) + "</section>".length);
}

describe("the pilot's companies", () => {
  it("are the 7 the task reads, the same as py/src/ft/equity/kpi/documents.py's COMPANIES", () => {
    const py = readFileSync(new URL("../../../py/src/ft/equity/kpi/documents.py", import.meta.url), "utf8");
    const m = /^COMPANIES = \(([^)]*)\)/m.exec(py);
    expect(m).not.toBeNull();
    expect([...KPI_PILOT]).toEqual([...(m?.[1] ?? "").matchAll(/"([a-z&-]+)"/g)].map((x) => x[1]));
    expect(KPI_PILOT).toHaveLength(7);
    expect(isKpiPilot("axisbank")).toBe(true);
    expect(isKpiPilot("wipro")).toBe(false);
  });

  it("show the panel; any other company's tab has none, and its other panels are as they were", () => {
    expect(html([flag()], SHOWN)).toContain('id="panel-kpi-changes"');
    const other = html([flag({ company_slug: "reliance" })], SHOWN, CALLS, "reliance");
    expect(other).not.toContain('id="panel-kpi-changes"');
    expect(other).not.toContain("Metrics missing from a release");
    const t = text(other);
    for (const name of ["Quarterly results", "Segment changes", "Promoter holding", "Corporate actions"]) expect(t).toContain(name);
  });
});

describe("the panel while equity.kpi_changes is hidden", () => {
  it.each([
    ["no policy row (fails closed)", undefined],
    ["the policy read failed", null],
    ["the policy is hidden", HIDDEN],
  ])("shows the held note and nothing else: %s", (_, policies) => {
    const p = panel(html([flag()], policies));
    const t = text(p);
    expect(t).toContain("Metrics missing from a release (pilot)");
    expect(t).toContain(KPI_HELD);
    for (const word of ["Attrition", "Q2 FY26", "Missing this quarter", "13.8%", "Official source", "Data to", "Update delayed", KPI_PILOT_LINE]) {
      expect(t).not.toContain(word);
    }
    expect(p).not.toContain("example.test/kpi");
    expect(p).toContain('data-state-kind="held"');
    expect(t).toContain("Source: NSE");
  });
});

describe("the panel once released", () => {
  it("shows a flag: its quarter, the metric, the kind in words with its sentence, the last value as printed, and both documents", () => {
    const p = panel(html([flag()], SHOWN));
    const t = text(p);
    expect(t).toContain("Pilot");
    expect(t).toContain("The latest flag is for the quarter ended 30 Sep 2025.");
    expect(t).toContain("Q2 FY26 Attrition, LTM Missing this quarter Not in this quarter's release; reported in 4 of the four quarters before.");
    expect(t).toContain("13.8% in Q1 FY26");
    expect(t).toContain("Q2 FY26: Official source ↗");
    expect(p).toContain('href="https://example.test/kpi/q2fy26.pdf"');
    for (const q of ["q2fy25", "q3fy25", "q4fy25", "q1fy26"]) expect(p).toContain(`href="https://example.test/kpi/${q}.pdf"`);
    expect(t).toContain("Earlier: 1 ↗ 2 ↗ 3 ↗ 4 ↗");
    expect(t).toContain(KPI_PILOT_LINE);
    expect(t).toContain(KPI_METHOD_LINE);
    expect(t).toContain("Source: NSE");
    expect(t).toContain("Data to 30 Sep 2025");
    expect(t).not.toContain(KPI_HELD);
  });

  it("says each kind in its own words, never D8's 'latest' or 'previous four' where the rule doesn't make it true", () => {
    expect(KPI_KIND_TITLE).toEqual({ missing: "Missing this quarter", dropped: "Dropped", renamed_or_redefined: "Renamed or redefined" });
    const three = flag({ previous_document_urls: ["https://example.test/a.pdf", "https://example.test/b.pdf", "https://example.test/c.pdf"] });
    expect(kpiSentence(three)).toBe("Not in this quarter's release; reported in 3 of the four quarters before.");
    expect(kpiSentence(flag({ kind: "dropped" }))).toBe("Not in this quarter's release, nor in the release where it first went missing.");
    expect(kpiSentence({ ...three, kind: "renamed_or_redefined" })).toBe(
      "Not in this quarter's release, which reports a new metric in the same unit; reported in 3 of the four quarters before.",
    );
    const t = text(panel(html([flag({ kind: "dropped" }), flag({ kind: "renamed_or_redefined", dict_key: "headcount", metric_label: "Workforce" })], SHOWN)));
    expect(t).toContain("Dropped");
    expect(t).toContain("Renamed or redefined");
    expect(t).not.toMatch(/latest release|previous four/i);
  });

  it("shows a verified quote as a chip with the transcript, its quarter and page, linked to the call's official source", () => {
    const p = panel(html([flag()], SHOWN));
    expect(text(p)).toContain("An analyst asked");
    expect(p).toContain("Source 1: show the cited passage");
    const m = kpiModel([flag({ reason_stated: true, reason_quote: "We have moved this disclosure to the annual report.", reason_page: 4 })], CALLS, false);
    expect(m.kind).toBe("flags");
    if (m.kind !== "flags") return;
    const [f] = m.flags;
    expect(f.reason.text).toBe("Yes");
    expect(f.reason.citation).toMatchObject({ quote: "We have moved this disclosure to the annual report.", documentTitle: "Earnings call transcript, Q2 FY26, p. 4", href: null, officialUrl: "https://example.test/tcs-q2fy26.pdf", textPublic: false });
    expect(f.asked.text).toBe("Yes");
    expect(f.asked.citation?.documentTitle).toBe("Earnings call transcript, Q2 FY26, p. 7");
  });

  it("says no only where the quarter's call transcript is stored, and that none is stored otherwise", () => {
    const none = flag({ analyst_asked: false, analyst_quote: null, analyst_page: null });
    const stored = text(panel(html([none], SHOWN)));
    expect(stored).toContain("No reason found in the release or the call");
    expect(stored).toMatch(/An analyst asked .* No /);
    const missing = text(panel(html([none], SHOWN, [call({ fiscal_label: "Q1 FY26" }), call({ fiscal_label: "Q2 FY26", status: "not_found", transcript_document_id: null })])));
    expect(missing).toContain("No reason found in the release; no call transcript stored");
    expect(missing).toContain("No call transcript stored");
    expect(missing).not.toContain("No reason found in the release or the call");
  });

  it("names no official source for a quote when the quarter has two stored calls, but still says the call was searched", () => {
    const two = [call({ fiscal_label: "Q2 FY26", call_slug: "a" }), call({ fiscal_label: "Q2 FY26", call_slug: "b" })];
    const m = kpiModel([flag({ reason_stated: false })], two, false);
    if (m.kind !== "flags") throw new Error(m.kind);
    expect(m.flags[0].asked.citation?.officialUrl).toBeNull();
    expect(m.flags[0].reason.text).toBe("No reason found in the release or the call");
  });

  it("says the value is held back when the view gives none, and shows a corporate-action note as filed", () => {
    const t = text(panel(html([flag({ last_value: null, corporate_action_note: "Demerger effective 1 Jan 2026: the commercial vehicles business" })], SHOWN)));
    expect(t).toContain("Q1 FY26; the value is held back");
    expect(t).toContain("Demerger effective 1 Jan 2026: the commercial vehicles business");
  });

  it("orders the flags newest quarter first, then by metric", () => {
    const m = kpiModel(
      [flag({ dict_key: "b", metric_label: "Workforce" }), flag({ dict_key: "c", period_end: "2026-06-30", fiscal_label: "Q1 FY27" }), flag({ dict_key: "a", metric_label: "Active clients" })],
      [...CALLS, call({ fiscal_label: "Q1 FY27" })],
      false,
    );
    if (m.kind !== "flags") throw new Error(m.kind);
    expect(m.flags.map((f) => `${f.quarter} ${f.metric}`)).toEqual(["Q1 FY27 Attrition, LTM", "Q2 FY26 Active clients", "Q2 FY26 Workforce"]);
    expect(m.latest).toBe("2026-06-30");
  });

  it("links a document only when its address is https", () => {
    const p = panel(html([flag({ current_document_url: "http://example.test/x.pdf", previous_document_urls: ["javascript:alert(1)", "https://example.test/ok.pdf"] })], SHOWN));
    expect(p).not.toContain("http://example.test/x.pdf");
    expect(p).not.toContain("javascript:");
    expect(text(p)).toContain("Q2 FY26: not linked");
    expect(text(p)).toContain("Earlier: 1 ↗");
  });

  it("says none is confirmed when the view returns no flag, and couldn't load when a read failed or a row can't be worded truly", () => {
    expect(text(panel(html([], SHOWN)))).toContain("No missing or dropped metric is confirmed for this company in the pilot's quarters.");
    for (const out of [
      html(null, SHOWN),
      html([flag()], SHOWN, null),
      html([flag({ kind: "other" as KpiChangeRow["kind"] })], SHOWN),
      html([flag({ reason_stated: true, reason_quote: null })], SHOWN),
    ]) {
      const p = panel(out);
      expect(text(p)).toContain("Couldn't load this panel.");
      expect(p).not.toContain("example.test/kpi");
    }
  });
});

describe("words, never colour", () => {
  const all = (Object.keys(KPI_KIND_TITLE) as KpiChangeRow["kind"][]).map((kind, i) => flag({ kind, dict_key: `k${i}`, analyst_asked: i === 0, analyst_quote: i === 0 ? "A question?" : null, analyst_page: i === 0 ? 3 : null }));
  const outs = [html(all, SHOWN), html([], SHOWN), html(null, SHOWN), html(all, HIDDEN), html(all, SHOWN, [])].map(panel);

  it("gives no flag or state a market colour", () => {
    for (const p of outs) expect(p).not.toMatch(/text-(up|down)|bg-(up|down)|border-(up|down)|#[0-9a-f]{3,6}\b/i);
  });

  it("uses none of the banned words and claims no motive in the panel's own text", () => {
    const own = outs.map((p) => text(p).replace(/Attrition, LTM|A question\?/g, "")).join(" ");
    expect(bannedWordsIn(own)).toEqual([]);
    expect(own).not.toMatch(/\b(hid|hide|hiding|conceal\w*|quietly|bur(y|ied)|obscur\w*|avoid\w*|motive|because|so as to|in order to|to stop|given)\b/i);
  });
});

describe("at 320 px", () => {
  it("lets the long cells wrap inside the panel's own scroller: no fixed width, and the table scrolls inside the panel", () => {
    const long = "A very long metric name as printed in the release with no natural break".replace(/ /g, "_");
    const p = panel(html([flag({ metric_label: long })], SHOWN));
    expect(p).toContain(long);
    expect(p).toContain("overflow-auto");
    const body = p.slice(p.indexOf('<div class="min-w-0 flex-1'), p.lastIndexOf('<div class="mt-auto'));
    expect(body).not.toMatch(/(?<![\w-])w-(\d+|\[)/);
    expect(body).not.toMatch(/(?<![\w-])min-w-(?!0\b|6\b)/);
  });
});
