// web/lib/equity/guidance.ts: the Guidance record panel's rules, pure (2B-19b, #267; Spec 02B §8, §5.10; Plan 04b P12; 02B.1 C6).
// Every figure is a row an `api` view returned (api.call_guidance, api.guidance_hit_rates, api.earnings_calls,
// api.citation_pages); nothing here computes an outcome or a rate (the database scored them); the one sum, the count left out,
// adds counts api.earnings_calls returned (unattributedAll, #342). An outcome is words and its
// relation, never a colour and never a verdict on management. Words never used: tests/banned-words.ts.
import { figureText, citationOf, pageIndex } from "@/lib/equity/calls";
import { NOT_AVAILABLE, formatCount, formatDate, formatFraction, formatStatedValue } from "@/lib/format";
import { pageLink, type Citation, type CitationPage } from "@/lib/policy/ai";
import type { AiCitationRow, CallGuidanceRow, CitationPageRow, EarningsCallRow, FieldPolicyRow, GuidanceHitRateRow } from "@/lib/queries/api";

export const GUIDANCE_HELD = "Guidance record held back until it passes review";
export const MIN_SCORED = 5;

/**
 * The hit rates' own key (migration 0048, #532; Spec 02B §3.5 as amended by Anand's option A, 13 Nov 2026): outcomes show once the
 * extraction and scoring checks pass, but a rate, "in the rate?", first stated and the revision labels need a call's list of targets
 * to be complete, so they wait for the recall check too. While it is hidden the views give those columns as null and no rate row.
 */
export const HIT_RATE_FIELD = "equity.guidance_hit_rate";
export const HIT_RATE_HELD = "Hit rate held back: the AI extraction may not list every target on a call yet.";
/** The Guidance record's subtitle adds this once the record shows: the targets are AI-extracted and a call's list may be partial. */
export const GUIDANCE_INCOMPLETE = "Targets are AI-extracted, and a call's list may not be complete.";

/** Whether the hit rates' key is shown or labelled (no row means hidden: field policy fails closed). */
export function guidanceHitRateOpen(policies: ReadonlyArray<FieldPolicyRow>): boolean {
  return policies.some((p) => p.field_key === HIT_RATE_FIELD && (p.status === "shown" || p.status === "labelled"));
}

const BASIS = { consolidated: "Consolidated", standalone: "Standalone" } as const;
const REVISION = { first: "First stated", raised: "Raised", cut: "Cut", reiterated: "Reiterated", restated: "Restated" } as const;
const RELATION = {
  within: "within the range",
  above: "above the range",
  below: "below the range",
  at_or_past: "at or past the figure",
  short_of: "short of the figure",
  moved_as_said: "moved as stated",
  moved_against: "moved the other way",
} as const;

/** "Delivered (above the range)", "Missed (below the range)", "Pending", "Not scored: <the stored reason>". */
export function outcomeText(r: Pick<CallGuidanceRow, "outcome" | "relation" | "reason" | "not_scored_reason">): string {
  const relation = r.relation ? ` (${RELATION[r.relation] ?? r.relation})` : "";
  if (r.outcome === "delivered") return `Delivered${relation}`;
  if (r.outcome === "missed") return `Missed${relation}`;
  if (r.outcome === "pending") return "Pending";
  return `Not scored: ${r.reason ?? r.not_scored_reason ?? "no reason stored"}`;
}

export type GuidanceRow = {
  key: string;
  metric: string;
  figure: string;
  period: string;
  basis: string;
  firstStated: string;
  latest: string;
  actual: string;
  outcome: string;
  inRate: string;
  citation: Citation | null;
  page: CitationPage | null;
};

export type GuidanceGroups = { scored: GuidanceRow[]; other: GuidanceRow[] };

function row(r: CallGuidanceRow, i: number, citations: readonly AiCitationRow[], pages: Map<string, CitationPageRow>): GuidanceRow {
  const cite = citations.find((c) => c.ai_output_id === r.ai_output_id && c.citation_no === r.citation_no);
  const pg = pages.get(`${r.ai_output_id}:${r.citation_no}`);
  const page = pg ? pageLink(pg) : null;
  const name = r.metric_label ?? r.metric;
  const revision = r.revision ? REVISION[r.revision] : "Stated";
  return {
    key: `${r.call_slug}-${r.metric}-${r.citation_no}-${i}`,
    metric: r.scope_text ? `${name} (${r.scope_text})` : name,
    figure: figureText({ ...r, kind: "target", figure_kind: r.kind, metric_text: null }),
    period: r.period_text ?? "Period not stated",
    basis: r.basis ? (BASIS[r.basis as keyof typeof BASIS] ?? r.basis) : "Not stated",
    firstStated: r.first_stated_on ? formatDate(r.first_stated_on) : NOT_AVAILABLE,
    latest: r.vintage_date ? `${revision}, ${r.fiscal_label} call, ${formatDate(r.vintage_date)}` : `${revision}, ${r.fiscal_label} call`,
    actual: r.actual === null ? NOT_AVAILABLE : formatStatedValue(r.actual, r.unit),
    outcome: outcomeText(r),
    inRate: r.counted === null ? NOT_AVAILABLE : r.counted ? "Yes" : "No",
    citation: cite ? citationOf(cite, pages) : null,
    page: page && page.url.startsWith("https://") ? page : null,
  };
}

/** The table's rows in two groups: scored targets first (delivered or missed, latest period first), then pending and not-scored ones. */
export function guidanceGroups(rows: readonly CallGuidanceRow[], citations: readonly AiCitationRow[], pages: readonly CitationPageRow[]): GuidanceGroups {
  const index = pageIndex(pages);
  const byPeriod = (a: CallGuidanceRow, b: CallGuidanceRow) =>
    (b.target_end ?? "").localeCompare(a.target_end ?? "") || a.metric.localeCompare(b.metric) || (a.vintage_date ?? "").localeCompare(b.vintage_date ?? "");
  const sorted = [...rows].sort(byPeriod);
  const scored = sorted.filter((r) => r.outcome === "delivered" || r.outcome === "missed");
  const other = sorted.filter((r) => !(r.outcome === "delivered" || r.outcome === "missed"));
  return {
    scored: scored.map((r, i) => row(r, i, citations, index)),
    other: other.map((r, i) => row(r, i, citations, index)),
  };
}

export type HitRate = { kind: "rate"; value: string; context: string; asOf: string } | { kind: "few"; note: string };

/** The company's hit rate as the view gives it, with n and the window; below 5 scored targets the rate is null and the note says so. */
export function hitRate(row: GuidanceHitRateRow | null): HitRate {
  const n = row?.n ?? 0;
  if (!row || row.rate === null || n < MIN_SCORED) {
    return { kind: "few", note: `Too few scored targets for a hit rate (n = ${formatCount(n)}; needs ${MIN_SCORED})` };
  }
  const window =
    row.first_stated_from && row.periods_to
      ? `Stated from ${formatDate(row.first_stated_from)}, for periods to ${formatDate(row.periods_to)}`
      : "Window not available";
  return {
    kind: "rate",
    value: formatFraction(row.rate, 0),
    context: `Delivered ${formatCount(row.delivered)} of ${formatCount(row.n)} scored targets; missed ${formatCount(row.missed)}`,
    asOf: window,
  };
}

/**
 * Whether the extraction field is shown or labelled: the `fig_ok` that api.earnings_calls and api.earnings_latest test before they
 * give a count (otherwise their counts are null).
 */
export function guidanceFiguresOpen(policies: ReadonlyArray<FieldPolicyRow>): boolean {
  return policies.some((p) => p.field_key === "ai.guidance_extraction" && (p.status === "shown" || p.status === "labelled"));
}

/**
 * api.earnings_latest.unattributed_targets_all, rebuilt from the company's calls the tab has already read (#342; #329, recommendation
 * 2a), because that view builds every call of every company to answer it. It is the view's own rule: null while the extraction field
 * is held back; otherwise the sum of the calls' `unattributed_targets` with a null counted as 0 (`coalesce(sum(...), 0)`), so a
 * company with no calls gives 0. Each addend is a count the database returned.
 */
export function unattributedAll(calls: ReadonlyArray<Pick<EarningsCallRow, "unattributed_targets">>, figuresOpen: boolean): number | null {
  if (!figuresOpen) return null;
  return calls.reduce((sum, c) => sum + (c.unattributed_targets ?? 0), 0);
}

/** The line for statements by speakers whose role couldn't be read (Spec 02B.1 C6); null while the count is null (gated) or 0. */
export function unattributedLine(count: number | null | undefined): string | null {
  if (count === null || count === undefined || count <= 0) return null;
  const tail = "whose role couldn't be read";
  return count === 1
    ? `1 forward-looking statement by a speaker ${tail} is listed on its call and left out of this record.`
    : `${formatCount(count, "IN")} forward-looking statements by speakers ${tail} are listed on their calls and left out of this record.`;
}
