// The KPI-table pilot's panel (DD-06, #514; Plan 05 §1.3, D7, D8): the 7 pilot companies, and the view's rows (api.kpi_changes,
// 0047) read into what the Financials tab shows. Pure: every figure, quarter and quote is a row the view returned, formatted by
// lib/format.ts; the browser derives no flag. The sentences say what the documents show, never why (D8): "found", never "given".
//
// Decided by Claude (standing delegation, 24 Oct 2026; reasons and alternatives on #514), each tightening D8's wording to what the
// rule (py/src/ft/equity/kpi/flags.py) makes true:
//   1. A flag can be of any quarter the pilot read, not only the latest, so no sentence says "the latest release"; each row names
//      its quarter. Alternative: D8's "Not in the latest release", false for an older quarter's flag.
//   2. "Reported in the previous four" becomes the count the view returns ("in 3 of the four quarters before"): the rule needs
//      three of four. A "dropped" flag is the second release without the metric, so it says that instead.
//   3. "No reason found in the release or the call" and "An analyst asked: no" are said only when a call transcript of that
//      quarter is stored (api.earnings_calls), since the call is searched only then (#513's open point 1). Otherwise the cells say
//      no transcript is stored. Alternative: D8's words for every flag, untrue for a quarter with no stored call.
import { cutQuote, type Citation } from "@/lib/policy/ai";
import { formatCount, formatPageRange, formatPrintedValue } from "@/lib/format";
import type { EarningsCallRow, KpiChangeRow } from "@/lib/queries/api";

/** The field policy that gates the panel (0047, seeded hidden; DD-07's judge, then Anand's flip). */
export const KPI_FIELD = "equity.kpi_changes";
export const KPI_HELD = "Metrics missing from a release held back until each flag passes review";

/**
 * The pilot's 7 companies (slugs): Data Audit 10's eight IT and bank names less Wipro, which isn't in the company master. The same
 * tuple as py/src/ft/equity/kpi/documents.py's COMPANIES (py/tests/test_equity_kpi.py checks the two agree). The panel shows on
 * these companies' Financials tabs only.
 */
export const KPI_PILOT = ["tcs", "infy", "hcltech", "hdfcbank", "icicibank", "sbin", "axisbank"] as const;

export function isKpiPilot(slug: string): boolean {
  return (KPI_PILOT as readonly string[]).includes(slug);
}

type Kind = KpiChangeRow["kind"];

/** Each kind in words (D8): the flag's heading. */
export const KPI_KIND_TITLE: Record<Kind, string> = {
  missing: "Missing this quarter",
  dropped: "Dropped",
  renamed_or_redefined: "Renamed or redefined",
};

const KINDS = new Set<string>(Object.keys(KPI_KIND_TITLE));

/** "in 3 of the four quarters before": the earlier documents the metric was reported in, as the view counts them. */
function reportedIn(row: KpiChangeRow): string {
  const n = (row.previous_document_urls ?? []).length;
  return n > 0 ? `; reported in ${formatCount(n)} of the four quarters before` : "";
}

/** Each kind's neutral sentence: what the documents show, never why (D8). */
export function kpiSentence(row: KpiChangeRow): string {
  switch (row.kind) {
    case "missing":
      return `Not in this quarter's release${reportedIn(row)}.`;
    case "dropped":
      return "Not in this quarter's release, nor in the release where it first went missing.";
    case "renamed_or_redefined":
      return `Not in this quarter's release, which reports a new metric in the same unit${reportedIn(row)}.`;
  }
}

/** One cell's words and, when it has one, its verified quote. */
export type QuoteCell = { text: string; citation: Citation | null };

export type KpiFlagView = {
  key: string;
  quarter: string;
  periodEnd: string;
  metric: string;
  title: string;
  sentence: string;
  note: string | null;
  last: string;
  reason: QuoteCell;
  asked: QuoteCell;
  /** The release that omits the metric ("Official source ↗"), https only. */
  currentUrl: string | null;
  /** The earlier releases the metric was reported in, oldest first, https only. */
  earlierUrls: string[];
};

/** The panel's reading of the view's rows: held back, couldn't load, nothing confirmed, or the flags. */
export type KpiModel =
  | { kind: "held" }
  | { kind: "error" }
  | { kind: "none" }
  | { kind: "flags"; flags: KpiFlagView[]; latest: string };

const https = (url: string | null | undefined): url is string => typeof url === "string" && url.startsWith("https://");

/** The quarter's stored call, when exactly one is stored: its official address names the transcript a quote is from. */
function storedCalls(calls: readonly EarningsCallRow[]): Map<string, EarningsCallRow | null> {
  const out = new Map<string, EarningsCallRow | null>();
  for (const c of calls) {
    if (c.status !== "stored" || c.transcript_document_id === null) continue;
    out.set(c.fiscal_label, out.has(c.fiscal_label) ? null : c);
  }
  return out;
}

function quoteCitation(quote: string | null, page: number | null, quarter: string, call: EarningsCallRow | null | undefined): Citation | null {
  if (!quote || quote.trim() === "") return null;
  const where = page !== null && page !== undefined ? `, ${formatPageRange(page)}` : "";
  return {
    no: 1,
    quote: cutQuote(quote),
    documentTitle: `Earnings call transcript, ${quarter}${where}`,
    href: null,
    officialUrl: call && https(call.official_url) ? call.official_url : null,
    textPublic: false,
  };
}

/** The last value as printed with its quarter, or the quarter alone when the value is held back. */
function lastReported(row: KpiChangeRow): string {
  const quarter = row.last_value_quarter;
  if (row.last_value === null || row.last_value === undefined) {
    return quarter ? `${quarter}; the value is held back` : "The value is held back";
  }
  const value = formatPrintedValue(row.last_value, row.last_value_unit);
  return quarter ? `${value} in ${quarter}` : value;
}

function flagView(row: KpiChangeRow, calls: Map<string, EarningsCallRow | null>): KpiFlagView {
  const searched = calls.has(row.fiscal_label);
  const call = calls.get(row.fiscal_label);
  const reasonQuote = row.reason_stated ? quoteCitation(row.reason_quote, row.reason_page, row.fiscal_label, call) : null;
  const askedQuote = row.analyst_asked ? quoteCitation(row.analyst_quote, row.analyst_page, row.fiscal_label, call) : null;
  return {
    key: `${row.dict_key}|${row.period_end}`,
    quarter: row.fiscal_label,
    periodEnd: row.period_end,
    metric: row.metric_label,
    title: KPI_KIND_TITLE[row.kind],
    sentence: kpiSentence(row),
    note: row.corporate_action_note?.trim() ? row.corporate_action_note.trim() : null,
    last: lastReported(row),
    reason: reasonQuote
      ? { text: "Yes", citation: reasonQuote }
      : { text: searched ? "No reason found in the release or the call" : "No reason found in the release; no call transcript stored", citation: null },
    asked: askedQuote ? { text: "Yes", citation: askedQuote } : { text: searched ? "No" : "No call transcript stored", citation: null },
    currentUrl: https(row.current_document_url) ? row.current_document_url : null,
    earlierUrls: (row.previous_document_urls ?? []).filter(https),
  };
}

/**
 * The panel's model. `rows` null: the read failed; `calls` null: the company's calls couldn't be read, so the "no" cells can't say
 * whether a call was searched, and the panel says it couldn't load rather than guess. A flag of an unknown kind, or one whose
 * "yes" has no quote, is a row the panel can't show truthfully: the panel says it couldn't load.
 */
export function kpiModel(
  rows: readonly KpiChangeRow[] | null | undefined,
  calls: readonly EarningsCallRow[] | null | undefined,
  hidden: boolean,
): KpiModel {
  if (hidden) return { kind: "held" };
  if (rows === null || rows === undefined) return { kind: "error" };
  if (rows.length === 0) return { kind: "none" };
  if (calls === null || calls === undefined) return { kind: "error" };
  if (rows.some((r) => !KINDS.has(r.kind))) return { kind: "error" };
  const byQuarter = storedCalls(calls);
  const flags = rows.map((r) => flagView(r, byQuarter));
  if (flags.some((f, i) => (rows[i].reason_stated && !f.reason.citation) || (rows[i].analyst_asked && !f.asked.citation))) return { kind: "error" };
  flags.sort((a, b) => (a.periodEnd === b.periodEnd ? a.metric.localeCompare(b.metric, "en") : a.periodEnd < b.periodEnd ? 1 : -1));
  return { kind: "flags", flags, latest: flags[0].periodEnd };
}
