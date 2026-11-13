// Test rows shaped like the earnings views of migration 0029 (test data, not real figures), for the Earnings calls tab's tests.
import type { AiCitationRow, AiOutputRow, CallFigureRow, CitationPageRow, EarningsCallRow, FieldPolicyRow } from "@/lib/queries/api";
import type { CallsTabData } from "@/lib/queries/calls";

export function call(over: Partial<EarningsCallRow> = {}): EarningsCallRow {
  return {
    company_slug: "tcs", company_name: "Tata Consultancy Services Ltd.", short_name: "TCS", call_slug: "q2-fy27", fiscal_label: "Q2 FY27",
    period_end: "2026-09-30", season_key: "2026-q2", call_date: "2026-10-09", call_date_source: "text", filed_at: "2026-10-12T09:00:00+05:30",
    exchange: "NSE", route: "exchange_pdf", status: "stored", official_url: "https://example.test/tcs-q2.pdf", transcript_document_id: 501,
    summary_output_id: 9001, summary_state: "available", figures_output_id: 9002, target_count: 1, stated_figure_count: 1,
    unknown_role_share: 0.02, roles_partial: false, unattributed_targets: 0, is_newest: true, is_latest_stored: true, is_default: true, ...over,
  };
}

export const SHOWN: FieldPolicyRow[] = [
  { field_key: "ai.call_summary", status: "labelled", label: "AI summary" },
  { field_key: "ai.guidance_extraction", status: "labelled", label: "AI-extracted" },
  { field_key: "equity.guidance_outcome", status: "labelled", label: "derived" },
];
export const HELD: FieldPolicyRow[] = [
  { field_key: "ai.call_summary", status: "hidden", label: "AI summary" },
  { field_key: "ai.guidance_extraction", status: "hidden", label: "AI-extracted" },
];

export function output(over: Partial<AiOutputRow> = {}): AiOutputRow {
  return {
    id: 9001, task: "call_summary", document_id: 501, event_slug: "q2-fy27", scope: "", body: null, output_label: null,
    content: {
      bullets: [
        { text: "Management described demand as steady across regions.", citations: [1], list: "management" },
        { text: "Analysts asked about hiring plans.", citations: [2], list: "analysts" },
      ],
    },
    display_label: "AI summary", model: "claude-haiku-5-5", created_at: "2026-10-30T09:00:00+00:00", ...over,
  };
}

export function citation(no: number, over: Partial<AiCitationRow> = {}): AiCitationRow {
  return {
    ai_output_id: 9001, citation_no: no, claim_path: `/bullets/${no - 1}`, document_id: 501, document_title: "Q2 FY27 earnings call transcript",
    source_url: "https://example.test/tcs-q2.pdf", char_start: 100 * no, char_end: 100 * no + 60, quote: `Quoted words number ${no}.`, text_public: false, ...over,
  };
}

export function pageRow(no: number, first: number, last = first, over: Partial<CitationPageRow> = {}): CitationPageRow {
  return { ai_output_id: 9001, citation_no: no, page_first: first, page_last: last, page_url: `https://example.test/tcs-q2.pdf#page=${first}`, ...over };
}

export function figure(kind: "target" | "stated", over: Partial<CallFigureRow> = {}): CallFigureRow {
  return {
    kind, company_slug: "tcs", call_slug: "q2-fy27", fiscal_label: "Q2 FY27", metric: "ebit_margin", metric_label: "EBIT margin", metric_text: "operating margin",
    figure_kind: kind === "target" ? "range" : null, value: kind === "target" ? null : 24.5, low: kind === "target" ? 26 : null, high: kind === "target" ? 28 : null,
    unit: "pct", direction: null, comparison: null, period_text: "FY27", period_kind: "year", period_start: null, period_end: null, basis: "consolidated",
    growth_basis: "level", scope_text: null, speaker_role: "management", ai_output_id: 9002, citation_no: 1, page_first: 7, page_last: 7,
    page_url: "https://example.test/tcs-q2.pdf#page=7", extraction_label: "AI-extracted", ...over,
  };
}

/** A tab's data with one published call, its summary, a target and a stated figure, every field shown. */
export function calls(over: Partial<CallsTabData> = {}): CallsTabData {
  const c = call();
  return {
    calls: [c], selected: c, policies: SHOWN, summary: output(),
    figures: [figure("target"), figure("stated", { citation_no: 2, page_first: 8, page_last: 8, page_url: "https://example.test/tcs-q2.pdf#page=8" })],
    citations: [citation(1), citation(2), citation(1, { ai_output_id: 9002, quote: "Margin words." }), citation(2, { ai_output_id: 9002, quote: "Stated words." })],
    pages: [pageRow(1, 3), pageRow(2, 4, 5), pageRow(1, 7, 7, { ai_output_id: 9002 }), pageRow(2, 8, 8, { ai_output_id: 9002 })],
    guidance: { rows: [], rate: null, unattributed: 0, citations: [], pages: [] }, tone: { state: "hidden" }, remarks: [], latest: null, ...over,
  };
}
