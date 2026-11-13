// web/lib/equity/tone.ts: the Earnings calls tab's "Tone" panel (2B-12b, #482; Spec 02B §5.7, §5.8, §8) from api.call_tone (0045). Pure.
// The browser derives no figure: the mean, the counts, the change and its label are the view's (the scoring task's, §5.7's rule);
// this only words them. The evidence quotes are the tone output's verified citations (api.ai_citations, at most 400 characters
// each), taken only from `content.evidence` (passages scored above or below neutral): when the output fell back to a citation of
// its first passage (`content.fallback_citation`), there is no evidence and the fallback citation is never shown. Tone is not a
// market move: words and numbers, no colour and no Delta (Design 01 §1.2; CLAUDE.md §5). Banned words: tests/banned-words.ts.
import { formatAxisChange, formatAxisNumber, formatCount } from "@/lib/format";
import { citationOf, pageIndex } from "@/lib/equity/calls";
import { wholeQuote, type Citation } from "@/lib/policy/ai";
import type { AiCitationRow, AiOutputRow, CallToneRow, CitationPageRow, EarningsCallRow, FieldPolicyRow } from "@/lib/queries/api";

export const TONE_FIELD = "ai.call_tone";
export const TONE_LABEL = "AI-scored";
export const TONE_HELD = "Tone held back until it passes review";
export const TONE_NOT_YET = "Tone not scored yet";
export const TONE_ROLES = "Speaker roles couldn't be read, so tone isn't scored";
export const NO_EVIDENCE = "No passage was scored above or below neutral";
export const TONE_SCALE = "on a −2 to +2 scale";
export const TONE_CHECK = "Checked against 30 passages labelled by a separate AI model (Claude Fable): 25 of 30 agreed (see Accuracy).";
export const MAX_EVIDENCE = 3;

/** True when the tone field is shown or labelled; the tab reads api.call_tone only then (a hidden field reads nothing). */
export function toneOpen(policies: readonly FieldPolicyRow[]): boolean {
  const p = policies.find((x) => x.field_key === TONE_FIELD);
  return p !== undefined && p.status !== "hidden";
}

const passages = (n: number | null): string => (n === null ? "" : `${formatCount(n)} ${n === 1 ? "passage" : "passages"}`);

/** The tone as the stat shows it: "+0.6" (a sign always, one decimal); null for a held call. */
export function toneValue(row: Pick<CallToneRow, "mean">): string | null {
  return row.mean === null ? null : formatAxisChange(row.mean, 1);
}

/** "Too little management text to score (5 passages)": the stored reason as a sentence, with the count of passages scored. */
export function heldText(row: Pick<CallToneRow, "held_reason" | "n">): string | null {
  if (row.held_reason === null) return null;
  const reason = row.held_reason.charAt(0).toUpperCase() + row.held_reason.slice(1);
  return row.n === null ? reason : `${reason} (${passages(row.n)})`;
}

const CHANGE_WORDS = { more_positive: "More positive than", more_negative: "More negative than", little_changed: "Little changed from" } as const;

/**
 * The change against the previous call, in words with both figures and both counts (§5.7): "More positive than Q1 FY27 (+0.2,
 * 21 passages)". Null when the view gives no label (no previous call, either call held, or the previous call not published).
 * The previous call's figures come from its own api.call_tone row.
 */
export function changeLine(row: CallToneRow, rows: readonly CallToneRow[]): string | null {
  if (row.change === null || row.prev_fiscal_label === null) return null;
  const prev = rows.find((r) => r.call_slug === row.prev_call_slug);
  const figures = prev && prev.mean !== null ? ` (${formatAxisChange(prev.mean, 1)}, ${passages(prev.n)})` : "";
  return `${CHANGE_WORDS[row.change]} ${row.prev_fiscal_label}${figures}`;
}

/** "Change in the mean +0.63, standard error 0.30": the numbers the label rests on; null where there is no label. */
export function changeDetail(row: Pick<CallToneRow, "change" | "delta" | "se_delta">): string | null {
  if (row.change === null || row.delta === null || row.se_delta === null) return null;
  return `Change in the mean ${formatAxisChange(row.delta, 2)}, standard error ${formatAxisNumber(row.se_delta, 2)}`;
}

/** The small stat for the opening remarks or the answers: "+1.0" and "6 passages"; null where the call had none of that kind. */
export function partValue(mean: number | null, n: number | null): { value: string; context: string } | null {
  if (mean === null || n === null || n === 0) return null;
  return { value: formatAxisChange(mean, 1), context: passages(n) };
}

// ---------------------------------------------------------------------------------------------------------------
// Evidence

export type EvidenceItem = { no: number; text: string; citation: Citation };
export type Evidence = { items: EvidenceItem[]; fallback: boolean };

const SECTION = { opening: "in the opening remarks", qa: "in an answer", closing: "in the closing remarks" } as const;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

/**
 * The evidence of a tone output: up to three quotes, each one the output's verified citation (never the stored quote text alone), with the
 * score its passage got and where it was said. Only `content.evidence` is read: an output that fell back to a citation of its first passage
 * (`content.fallback_citation`) has no evidence, and that citation is not shown. An evidence item without a verified citation is dropped.
 */
export function toneEvidence(output: AiOutputRow | null, citations: readonly AiCitationRow[], pages: readonly CitationPageRow[]): Evidence {
  const content = output?.content;
  if (!output || !isRecord(content)) return { items: [], fallback: false };
  const fallback = content.fallback_citation === true;
  if (fallback || !Array.isArray(content.evidence)) return { items: [], fallback };
  const scores = new Map<number, { score: number; section: string | null }>();
  if (Array.isArray(content.scores)) {
    for (const s of content.scores) {
      if (isRecord(s) && Number.isInteger(s.p) && Number.isInteger(s.score)) scores.set(s.p as number, { score: s.score as number, section: typeof s.section === "string" ? s.section : null });
    }
  }
  const byNo = new Map(citations.filter((c) => c.ai_output_id === output.id).map((c) => [c.citation_no, c]));
  const index = pageIndex(pages);
  const items: EvidenceItem[] = [];
  for (const raw of content.evidence) {
    if (!isRecord(raw) || !Number.isInteger(raw.citation_no) || !Number.isInteger(raw.p)) continue;
    const row = byNo.get(raw.citation_no as number);
    const cite = row ? citationOf(row, index) : null;
    const got = scores.get(raw.p as number);
    // Only a passage scored above or below neutral is evidence (§5.7); anything else in the list is not shown.
    if (!row || !cite || !got || got.score === 0) continue;
    const where = got.section && got.section in SECTION ? ` ${SECTION[got.section as keyof typeof SECTION]}` : "";
    items.push({ no: cite.no, text: `Passage scored ${formatAxisChange(got.score, 0)}${where}`, citation: { ...cite, fullQuote: wholeQuote(row.quote ?? "") } });
    if (items.length === MAX_EVIDENCE) break;
  }
  return { items, fallback: false };
}

// ---------------------------------------------------------------------------------------------------------------
// History across the company's calls

export type ToneHistory = { quarter: string; callSlug: string; mean: string; n: string; change: string };

const CHANGE_SHORT = { more_positive: "More positive", more_negative: "More negative", little_changed: "Little changed" } as const;

/** The chart's points (one per call with a mean and a quarter end, oldest first) and the table's rows (every call, newest first). */
export function toneHistory(rows: readonly CallToneRow[]): { points: { time: string; value: number }[]; table: ToneHistory[]; without: number } {
  const oldest = [...rows].sort((a, b) => (a.period_end ?? "").localeCompare(b.period_end ?? "") || a.call_slug.localeCompare(b.call_slug));
  const seen = new Set<string>();
  const points: { time: string; value: number }[] = [];
  for (const r of oldest) {
    if (r.mean === null || r.period_end === null || seen.has(r.period_end)) continue;
    seen.add(r.period_end);
    points.push({ time: r.period_end, value: r.mean });
  }
  const table = [...oldest].reverse().map((r) => ({
    quarter: r.fiscal_label,
    callSlug: r.call_slug,
    mean: r.mean === null ? "Held" : formatAxisChange(r.mean, 1),
    n: r.n === null ? "" : formatCount(r.n),
    change: r.change === null ? "" : CHANGE_SHORT[r.change],
  }));
  return { points, table, without: rows.length - points.length };
}

// ---------------------------------------------------------------------------------------------------------------
// The panel's state

export type ToneState =
  | { kind: "hidden" }
  | { kind: "error" }
  | { kind: "roles" }
  | { kind: "none" }
  | { kind: "held"; row: CallToneRow }
  | { kind: "scored"; row: CallToneRow };

/** What the panel shows for a call: the field's gate first, then a failed read, then the call's own state. */
export function toneState(open: boolean, read: "ok" | "error", call: Pick<EarningsCallRow, "call_slug" | "roles_partial">, rows: readonly CallToneRow[]): ToneState {
  if (!open) return { kind: "hidden" };
  if (read === "error") return { kind: "error" };
  const row = rows.find((r) => r.call_slug === call.call_slug) ?? null;
  if (row) return row.held_reason !== null || row.mean === null ? { kind: "held", row } : { kind: "scored", row };
  return call.roles_partial === true ? { kind: "roles" } : { kind: "none" };
}

// ---------------------------------------------------------------------------------------------------------------
// The explainer (one click away): the rubric and the rules

export const RUBRIC: { score: string; means: string; signs: string }[] = [
  { score: formatAxisChange(-2, 0), means: "Strongly negative", signs: "Clear deterioration in demand, margins or the outlook, stated without an offset; an outlook cut; “very difficult”, “significant pressure”." },
  { score: formatAxisChange(-1, 0), means: "Mildly negative", signs: "Caution, headwinds named, softer demand, delays, “challenging”, expectations lowered with a mitigation." },
  { score: formatAxisChange(0, 0), means: "Neutral", signs: "Figures recited without judgement; balanced statements; procedure, thanks, definitions." },
  { score: formatAxisChange(1, 0), means: "Mildly positive", signs: "Improvement with qualifiers; “on track”, “steady”, “encouraging”; risks noted but outweighed." },
  { score: formatAxisChange(2, 0), means: "Strongly positive", signs: "Confident, unqualified improvement; an outlook raised; “strong momentum across”, “record”." },
];

export const RULES: string[] = [
  "Each passage of at least 40 words in management's own turns (opening remarks, answers and closing remarks) gets one whole-number score by the rubric above. Analysts and the moderator are never scored.",
  "A call's tone is the mean of its passage scores. It is held when fewer than 8 passages were scored.",
  "The change from the previous call is labelled “more positive” or “more negative” when it is more than twice its standard error, otherwise “little changed”. Both means and both passage counts are always shown.",
  "The score is a reading by an AI model, not a measure of anything the company filed. The quotes are the speakers' words.",
];
