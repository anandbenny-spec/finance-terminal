// web/lib/equity/calls.ts: the Earnings calls tab's rules, pure (2B-19, #182; Spec 02B §8, §13.3, §13.4, §13.7; 02B.1 C6).
// Every figure is a row an `api` view returned (api.earnings_calls, api.call_figures, api.citation_pages); the words for a
// status are Spec 02B §5.2's, quoted on the card. Nothing here computes a number. Words never used here (Spec 02B §8):
// tests/banned-words.ts scans the tab's own text for them.
import { dateInZone, formatDate, formatFraction, formatStatedValue } from "@/lib/format";
import { pageLink, toCitation, wholeQuote, type Citation, type CitationPage } from "@/lib/policy/ai";
import type { AiCitationRow, AiOutputRow, CallFigureRow, CitationPageRow, EarningsCallRow, EarningsListRow } from "@/lib/queries/api";

/** The call the tab shows: the one in the URL (?call=q2-fy27) if it is one of the company's, else the default. */
export function selectCall(calls: readonly EarningsCallRow[], slug: string | undefined | string[]): EarningsCallRow | null {
  const wanted = typeof slug === "string" ? slug : undefined;
  return calls.find((c) => c.call_slug === wanted) ?? calls.find((c) => c.is_default) ?? null;
}

/** The call in the URL (?call=q2-fy27): a slug-shaped string only, else undefined (the default call). */
export function callOf(value: string | string[] | undefined): string | undefined {
  return typeof value === "string" && /^[a-z0-9-]{1,40}$/.test(value) ? value : undefined;
}

/** The calls newest first, by the quarter they cover (never by season: a late call can be stored in a later season; #285). */
export function newestFirst(calls: readonly EarningsCallRow[]): EarningsCallRow[] {
  return [...calls].sort((a, b) => (b.period_end ?? "").localeCompare(a.period_end ?? "") || (b.filed_at ?? "").localeCompare(a.filed_at ?? ""));
}

export const SUMMARY_HELD = "AI summary held back until it passes review";
export const SUMMARY_NOT_YET = "Summary not generated yet";
/** The line under each summary block's heading (Anand, 10 Nov 2026): the bullet is a reading, the quote under it is the fact. */
export const SUMMARY_LABEL = "AI paraphrase; the quote is the speaker's words.";
export const WEBSITE = "Transcript on the company's website";
export const SCANNED = "Transcript is a scanned image; not analysed";
export const NO_TRANSCRIPT = "No transcript found";
export const AI_EXTRACTED = "AI-extracted";
export const AS_STATED = "As stated by management on the call";
export const UNKNOWN_ROLE_HEADING = "Said by participants whose role couldn't be read";

/** A call's status in words: the transcript's own status first, else its summary's state. */
export function statusText(c: Pick<EarningsCallRow, "status" | "summary_state" | "roles_partial">): string {
  let text: string;
  if (c.status === "on_company_website") text = WEBSITE;
  else if (c.status === "unreadable") text = SCANNED;
  else if (c.status !== "stored") text = "Transcript not analysed";
  else if (c.summary_state === "available") text = "AI summary available";
  else if (c.summary_state === "held_back") text = SUMMARY_HELD;
  else text = SUMMARY_NOT_YET;
  return c.roles_partial ? `${text} · roles partly read` : text;
}

/** The call date, or the day its transcript was filed when the call's own date isn't known. */
export function callDateText(c: Pick<EarningsCallRow, "call_date" | "filed_at">): string {
  if (c.call_date) return formatDate(c.call_date);
  if (c.filed_at) return `Transcript filed on ${formatDate(dateInZone(c.filed_at, "Asia/Kolkata"))}`;
  return "Date not known";
}

/** "Source: NSE" or "Source: BSE", plus the company's website where that is the transcript's route (Spec 02B §8). */
export function callSourceLines(c: Pick<EarningsCallRow, "exchange" | "route" | "status"> | null): string[] {
  const exchange = c?.exchange?.toUpperCase() === "BSE" ? "Source: BSE" : "Source: NSE";
  const viaWebsite = c !== null && (c.route === "company_link" || c.route === "curated_link" || c.status === "on_company_website");
  return viaWebsite ? [exchange, "the company's website, linked from its exchange filing"] : [exchange];
}

/** The partial-roles line under a role-held call's heading (Spec 02B.1 C6), the share as a whole percent. */
export function partialLine(share: number | null): string {
  const pct = share === null ? "part" : formatFraction(share, 0);
  return `Partial: speaker roles couldn't be read for ${pct} of this call's words. What those speakers said is listed separately and isn't attributed to management or scored.`;
}

// ---------------------------------------------------------------------------------------------------------------
// The summary's lists

export type SummaryList = { key: "management" | "analysts" | "unknown" | null; heading: string; bullets: { text: string; citations: Citation[] }[] };

const HEADING = { management: "What management said", analysts: "What analysts asked about", unknown: UNKNOWN_ROLE_HEADING } as const;
const ORDER = ["management", "analysts", "unknown"] as const;

/** The pages by output and citation number. */
export function pageIndex(rows: readonly CitationPageRow[]): Map<string, CitationPageRow> {
  return new Map(rows.map((p) => [`${p.ai_output_id}:${p.citation_no}`, p]));
}

/** A citation as the chip shows it, with its PDF page when the view has a row for it. */
export function citationOf(row: AiCitationRow, pages: Map<string, CitationPageRow>): Citation | null {
  return toCitation(row, true, pages.get(`${row.ai_output_id}:${row.citation_no}`) ?? null);
}

/** A call summary's content ({bullets: [{text, citations, list}]}) as its lists; a bullet with no verified citation is dropped. */
export function summaryLists(output: AiOutputRow, citations: readonly AiCitationRow[], pages: readonly CitationPageRow[]): SummaryList[] {
  const content = output.content as { bullets?: unknown } | null;
  if (typeof content !== "object" || content === null || !Array.isArray(content.bullets)) return [];
  const index = pageIndex(pages);
  const byNo = new Map(citations.filter((c) => c.ai_output_id === output.id).map((c) => [c.citation_no, c]));
  const groups = new Map<string, SummaryList>();
  for (const raw of content.bullets as unknown[]) {
    if (typeof raw !== "object" || raw === null) continue;
    const { text, citations: nos, list } = raw as { text?: unknown; citations?: unknown; list?: unknown };
    if (typeof text !== "string" || !text.trim() || !Array.isArray(nos)) continue;
    // Each citation also carries its whole quote for the inline display under the bullet (#371); the chip's popover keeps the cut one.
    const cites = nos
      .map((n) => byNo.get(n as number))
      .map((c): Citation | null => {
        const cite = c ? citationOf(c, index) : null;
        return c && cite ? { ...cite, fullQuote: wholeQuote(c.quote ?? "") } : null;
      })
      .filter((c): c is Citation => c !== null);
    if (cites.length === 0) continue;
    const key = ORDER.find((k) => k === list) ?? null;
    const id = key ?? "none";
    const group = groups.get(id) ?? { key, heading: key ? HEADING[key] : "AI summary", bullets: [] };
    group.bullets.push({ text: text.trim(), citations: cites });
    groups.set(id, group);
  }
  return [...ORDER, "none" as const].map((k) => groups.get(k)).filter((g): g is SummaryList => g !== undefined);
}

// ---------------------------------------------------------------------------------------------------------------
// The figures

export type FigureRow = {
  key: string;
  metric: string;
  figure: string;
  period: string;
  basis: string;
  citation: Citation | null;
  page: CitationPage | null;
};

const BASIS = { consolidated: "Consolidated", standalone: "Standalone" } as const;
const GROWTH = { yoy: "year on year", qoq: "quarter on quarter", cagr: "annual compound rate", level: "" } as const;
const DIRECTION = { increase: "Increase", decrease: "Decrease", maintain: "Maintain" } as const;
const COMPARISON = { previous_period: "against the previous period", year_ago: "against a year ago" } as const;

/** The figure as stated: "At least 10%", "12% to 14%", "Increase against a year ago"; the growth basis follows it. */
export function figureText(
  r: Pick<CallFigureRow, "kind" | "figure_kind" | "value" | "low" | "high" | "unit" | "direction" | "comparison" | "growth_basis" | "metric_text">,
): string {
  let body: string;
  if (r.kind === "target" && r.figure_kind !== "direction" && r.value === null && r.low === null && r.high === null) body = "Stated in words (see the quote)";
  else if (r.kind === "stated" || r.figure_kind === "point" || r.figure_kind === null) body = formatStatedValue(r.value, r.unit);
  else if (r.figure_kind === "range") body = `${formatStatedValue(r.low, r.unit)} to ${formatStatedValue(r.high, r.unit)}`;
  else if (r.figure_kind === "floor") body = `At least ${formatStatedValue(r.value, r.unit)}`;
  else if (r.figure_kind === "ceiling") body = `At most ${formatStatedValue(r.value, r.unit)}`;
  else {
    const dir = r.direction ? (DIRECTION[r.direction as keyof typeof DIRECTION] ?? r.direction) : "Direction only";
    const cmp = r.comparison ? COMPARISON[r.comparison as keyof typeof COMPARISON] : null;
    body = cmp ? `${dir} ${cmp}` : dir;
  }
  const growth = r.growth_basis ? GROWTH[r.growth_basis as keyof typeof GROWTH] : "";
  return growth ? `${body}, ${growth}` : body;
}

/** One figure as the table shows it; its quote comes from its output's verified citations. */
export function figureRow(r: CallFigureRow, i: number, citations: readonly AiCitationRow[], pages: Map<string, CitationPageRow>): FigureRow {
  const cite = citations.find((c) => c.ai_output_id === r.ai_output_id && c.citation_no === r.citation_no);
  const name = r.metric_label ?? r.metric_text ?? r.metric;
  const page = r.page_first !== null && r.page_url ? pageLink({ page_first: r.page_first, page_last: r.page_last ?? r.page_first, page_url: r.page_url }) : null;
  return {
    key: `${r.kind}-${r.speaker_role}-${i}`,
    metric: r.scope_text ? `${name} (${r.scope_text})` : name,
    figure: figureText(r),
    period: r.period_text ?? "Period not stated",
    basis: r.basis ? (BASIS[r.basis as keyof typeof BASIS] ?? r.basis) : "Not stated",
    citation: cite ? citationOf(cite, pages) : null,
    page: page && page.url.startsWith("https://") ? page : null,
  };
}

export type FigureSets = { targets: FigureRow[]; stated: FigureRow[]; unattributed: FigureRow[] };

/** The three tables' rows: management's targets, what management stated, and targets from speakers whose role couldn't be read. */
export function figureSets(rows: readonly CallFigureRow[], citations: readonly AiCitationRow[], pages: readonly CitationPageRow[]): FigureSets {
  const index = pageIndex(pages);
  const make = (pick: (r: CallFigureRow) => boolean) => rows.filter(pick).map((r, i) => figureRow(r, i, citations, index));
  return {
    targets: make((r) => r.kind === "target" && r.speaker_role === "management"),
    stated: make((r) => r.kind === "stated"),
    unattributed: make((r) => r.kind === "target" && r.speaker_role === "unknown"),
  };
}

// ---------------------------------------------------------------------------------------------------------------
// The /earnings list (2B-20, #183; Spec 02B §13.7; 02B.1 C6)

export const SUMMARY_AND_FIGURES = "Summary and figures";
const WEBSITE_ROUTES = ["company_link", "curated_link"];

/** A list row's status in words (Spec 02B §13.7), plus " · roles partly read" on a role-held default call. */
export function listStatusText(r: Pick<EarningsListRow, "status" | "summary_state" | "roles_partial">): string {
  let text: string;
  if (r.status === "none_found") return NO_TRANSCRIPT;
  if (r.status === "on_company_website") text = WEBSITE;
  else if (r.status === "unreadable") text = SCANNED;
  else if (r.status !== "stored") text = "Transcript not analysed";
  else if (r.summary_state === "available") text = SUMMARY_AND_FIGURES;
  else if (r.summary_state === "held_back") text = SUMMARY_HELD;
  else text = SUMMARY_NOT_YET;
  return r.roles_partial ? text + " · roles partly read" : text;
}

/** For a "No transcript found" row: the window the view returned, never a fixed date. */
export function noTranscriptNote(r: Pick<EarningsListRow, "status" | "window_from">): string | null {
  if (r.status !== "none_found" || !r.window_from) return null;
  return NO_TRANSCRIPT + " among filings since " + formatDate(r.window_from);
}

/** A list row's call date text; null for a company with no call. */
export function listDateText(r: Pick<EarningsListRow, "status" | "call_date" | "filed_at">): string | null {
  if (r.status === "none_found") return null;
  return callDateText({ call_date: r.call_date, filed_at: r.filed_at });
}

/** The date column's sort key: the call date, else the day its transcript was filed; null (sorted last) with no call. */
export function listDateKey(r: Pick<EarningsListRow, "call_date" | "filed_at">): string | null {
  return r.call_date ?? (r.filed_at ? dateInZone(r.filed_at, "Asia/Kolkata") : null);
}

/** The page's source lines: "Source: NSE" (BSE where a row's exchange is BSE), plus the company-website line where any row's route is one. */
export function listSourceLines(rows: readonly Pick<EarningsListRow, "exchange" | "route" | "status" | "call_slug">[]): string[] {
  const lines = ["Source: NSE"];
  if (rows.some((r) => r.call_slug && r.exchange?.toUpperCase() === "BSE")) lines.push("Source: BSE");
  if (rows.some((r) => (r.route !== null && WEBSITE_ROUTES.includes(r.route)) || r.status === "on_company_website"))
    lines.push("the company's website, linked from its exchange filing");
  return lines;
}

/** The latest day any row's call was filed, for "Data to …" (null with none). */
export function listDataTo(rows: readonly Pick<EarningsListRow, "filed_at">[]): string | null {
  return rows.map((r) => (r.filed_at ? dateInZone(r.filed_at, "Asia/Kolkata") : null)).filter((d): d is string => d !== null).sort().at(-1) ?? null;
}
