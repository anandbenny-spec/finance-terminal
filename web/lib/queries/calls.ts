// web/lib/queries/calls.ts: what the company page's Earnings calls tab reads (2B-19, #182; Spec 02B §13.6, §13.7), from the
// `api` views of migration 0029 with the browser key only. Every list goes through readAll (an exact count against the Data
// API's 1,000-row cap, CLAUDE.md §6). Nothing here reads a transcript's text: a quote arrives only as a verified citation's
// (api.ai_citations), at most 400 characters. A failed read leaves the tab in its error state; the reason goes to the log.
import type { SupabaseClient } from "@supabase/supabase-js";
import { cacheLife, cacheTag } from "next/cache";
import { selectCall } from "@/lib/equity/calls";
import type { AiCitationRow, AiOutputRow, ApiDatabase, CallFigureRow, CallGuidanceRow, CitationPageRow, GuidanceHitRateRow, UnattributedRow, EarningsCallRow, EarningsLatestRow, EarningsListRow, FieldPolicyRow } from "@/lib/queries/api";
import { readFieldPolicy } from "@/lib/queries/policy";
import { readAll } from "@/lib/queries/read";
import { publicClient } from "@/lib/supabase/public";

type Db = SupabaseClient<ApiDatabase, "api">;

const CALL_COLUMNS =
  "company_slug, company_name, short_name, call_slug, fiscal_label, period_end, season_key, call_date, call_date_source, filed_at, exchange, route, status, official_url, transcript_document_id, summary_output_id, summary_state, figures_output_id, target_count, stated_figure_count, unknown_role_share, roles_partial, unattributed_targets, is_newest, is_latest_stored, is_default";
const FIGURE_COLUMNS =
  "kind, company_slug, call_slug, fiscal_label, metric, metric_label, metric_text, figure_kind, value, low, high, unit, direction, comparison, period_text, period_kind, period_start, period_end, basis, growth_basis, scope_text, speaker_role, ai_output_id, citation_no, page_first, page_last, page_url, extraction_label";
const OUTPUT_COLUMNS = "id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at";
const CITATION_COLUMNS = "ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote, text_public";

const GUIDANCE_COLUMNS =
  "company_slug, company_name, short_name, call_slug, fiscal_label, call_date, metric, metric_label, kind, value, low, high, unit, direction, comparison, period_text, period_kind, target_start, target_end, basis, growth_basis, scope_text, vintage_date, ai_output_id, citation_no, not_scored_reason, extraction_label, outcome_label, is_first, revision, counted, outcome, relation, actual, reason, first_call_slug, first_stated_on";

/** What the Guidance record panel reads (2B-19b, #267): the company's guidance rows, its hit-rate row, and the count left out. */
export type GuidanceRecordData = {
  rows: CallGuidanceRow[];
  rate: GuidanceHitRateRow | null;
  /** api.earnings_latest.unattributed_targets_all: null while the figures are held back. */
  unattributed: number | null;
  citations: AiCitationRow[];
  pages: CitationPageRow[];
};

export type CallsTabData = {
  calls: EarningsCallRow[];
  selected: EarningsCallRow | null;
  policies: FieldPolicyRow[];
  /** The selected call's summary output, when the view gives one (its field is shown or labelled). */
  summary: AiOutputRow | null;
  figures: CallFigureRow[];
  /** The verified citations of the summary's and the figures' outputs. */
  citations: AiCitationRow[];
  pages: CitationPageRow[];
  /** The Guidance record panel's rows (company-wide, not the selected call's). */
  guidance: GuidanceRecordData;
  /** With no call: the company's earnings_latest row, whose window says what the transcript discovery covered. */
  latest: EarningsLatestRow | null;
};

export async function readCompanyCalls(db: Db, slug: string): Promise<EarningsCallRow[]> {
  return readAll<EarningsCallRow>("api.earnings_calls", (from, to) =>
    db
      .from("earnings_calls")
      .select(CALL_COLUMNS, { count: "exact" })
      .eq("company_slug", slug)
      .order("period_end", { ascending: false })
      .order("call_slug")
      .range(from, to),
  );
}

export async function readCallFigures(db: Db, slug: string, call: string): Promise<CallFigureRow[]> {
  return readAll<CallFigureRow>("api.call_figures", (from, to) =>
    db
      .from("call_figures")
      .select(FIGURE_COLUMNS, { count: "exact" })
      .eq("company_slug", slug)
      .eq("call_slug", call)
      .order("kind")
      .order("period_end")
      .order("metric")
      .order("citation_no")
      .range(from, to),
  );
}

async function readOutput(db: Db, id: number): Promise<AiOutputRow | null> {
  const rows = await readAll<AiOutputRow>(`api.ai_outputs ${id}`, (from, to) =>
    db.from("ai_outputs").select(OUTPUT_COLUMNS, { count: "exact" }).eq("id", id).order("id").range(from, to),
  );
  return rows[0] ?? null;
}

async function readOutputCitations(db: Db, ids: readonly number[]): Promise<AiCitationRow[]> {
  if (ids.length === 0) return [];
  return readAll<AiCitationRow>(`api.ai_citations of outputs ${ids.join(",")}`, (from, to) =>
    db
      .from("ai_citations")
      .select(CITATION_COLUMNS, { count: "exact" })
      .in("ai_output_id", [...ids])
      .order("ai_output_id")
      .order("citation_no")
      .range(from, to),
  );
}

async function readOutputPages(db: Db, ids: readonly number[]): Promise<CitationPageRow[]> {
  if (ids.length === 0) return [];
  return readAll<CitationPageRow>(`api.citation_pages of outputs ${ids.join(",")}`, (from, to) =>
    db
      .from("citation_pages")
      .select("ai_output_id, citation_no, page_first, page_last, page_url", { count: "exact" })
      .in("ai_output_id", [...ids])
      .order("ai_output_id")
      .order("citation_no")
      .range(from, to),
  );
}

/** The company's guidance rows, hit rate and left-out count with their quotes' citations and pages (#267). Every list is read whole against the exact count. */
export async function readGuidanceRecord(db: Db, slug: string): Promise<GuidanceRecordData> {
  const [rows, rates, latest] = await Promise.all([
    readAll<CallGuidanceRow>("api.call_guidance", (from, to) =>
      db
        .from("call_guidance")
        .select(GUIDANCE_COLUMNS, { count: "exact" })
        .eq("company_slug", slug)
        .order("target_end", { ascending: false })
        .order("metric")
        .order("call_slug")
        .order("citation_no")
        .range(from, to),
    ),
    readAll<GuidanceHitRateRow>("api.guidance_hit_rates", (from, to) =>
      db
        .from("guidance_hit_rates")
        .select("scope, scope_key, scope_label, delivered, missed, n, rate, first_stated_from, periods_to, outcome_label", { count: "exact" })
        .eq("scope", "company")
        .eq("scope_key", slug)
        .order("scope_key")
        .range(from, to),
    ),
    readAll<UnattributedRow>("api.earnings_latest unattributed", (from, to) =>
      db.from("earnings_latest").select("company_slug, unattributed_targets_all", { count: "exact" }).eq("company_slug", slug).order("company_slug").range(from, to),
    ),
  ]);
  const ids = [...new Set(rows.map((r) => r.ai_output_id))].sort((a, b) => a - b);
  const [citations, pages] = await Promise.all([readOutputCitations(db, ids), readOutputPages(db, ids)]);
  return { rows, rate: rates[0] ?? null, unattributed: latest[0]?.unattributed_targets_all ?? null, citations, pages };
}

/** The tab's reads: the company's calls, the selected one's summary, figures, citations and pages (§13.7). */
export async function readCallsTab(db: Db, slug: string, call: string | undefined): Promise<CallsTabData> {
  const [calls, policies, guidance] = await Promise.all([readCompanyCalls(db, slug), readFieldPolicy(db), readGuidanceRecord(db, slug)]);
  const selected = selectCall(calls, call);
  if (!selected) {
    const latest = await readAll<EarningsLatestRow>("api.earnings_latest", (from, to) =>
      db
        .from("earnings_latest")
        .select("company_slug, status, window_from, window_to", { count: "exact" })
        .eq("company_slug", slug)
        .order("company_slug")
        .range(from, to),
    );
    return { calls, selected, policies, summary: null, figures: [], citations: [], pages: [], guidance, latest: latest[0] ?? null };
  }
  const outputIds = [selected.summary_output_id, selected.figures_output_id].filter((n): n is number => n !== null);
  const [summary, figures, citations, pages] = await Promise.all([
    selected.summary_output_id !== null ? readOutput(db, selected.summary_output_id) : Promise.resolve(null),
    selected.figures_output_id !== null ? readCallFigures(db, slug, selected.call_slug) : Promise.resolve([]),
    readOutputCitations(db, outputIds),
    readOutputPages(db, outputIds),
  ]);
  return { calls, selected, policies, summary, figures, citations, pages, guidance, latest: null };
}

const LIST_COLUMNS =
  "company_slug, company_name, short_name, call_slug, fiscal_label, call_date, filed_at, exchange, route, status, summary_state, roles_partial, window_from, window_to";

/** Every company's row of api.earnings_latest (about 50), read whole against the exact count (CLAUDE.md §6). */
export async function readEarningsList(db: Db): Promise<EarningsListRow[]> {
  return readAll<EarningsListRow>("api.earnings_latest", (from, to) =>
    db.from("earnings_latest").select(LIST_COLUMNS, { count: "exact" }).order("company_slug").range(from, to),
  );
}

export type EarningsListData = { ok: true; readAt: string; rows: EarningsListRow[] } | { ok: false; readAt: string };

/** What /earnings renders: the list whole or not at all; a failed read leaves the page in its error state. */
export async function readEarningsListPage(): Promise<EarningsListData> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("equity", "earnings");
  const readAt = new Date().toISOString();
  const db = publicClient();
  if (!db) {
    console.error("[earnings] list read failed: NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
    return { ok: false, readAt };
  }
  try {
    return { ok: true, readAt, rows: await readEarningsList(db) };
  } catch (error) {
    console.error("[earnings] list read failed: " + (error instanceof Error ? error.message : String(error)));
    return { ok: false, readAt };
  }
}
