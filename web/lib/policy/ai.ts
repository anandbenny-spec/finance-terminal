// web/lib/policy/ai.ts: AI outputs as the pages show them (Design 01 §1.10, §4.5; Arch 03 §1.2). Pure.
//
// - Only published outputs reach the page (RLS and api.ai_outputs), and the database refuses to publish one with
//   an uncited point (Arch 01 §1.4). Still, a bullet whose citations aren't among the verified ones the view
//   returns is dropped here, never shown uncited (CLAUDE.md §1: "No uncited AI claim").
// - A quote is rebuilt from our stored text by api.ai_citations, never taken from model output; it's cut to at
//   most 280 characters at a sentence boundary, with "…" (Design 01 §4.5, CitationPopover).
// - "Official source ↗" links only where the source allows linking: never for the RBI (Anand, 24 Oct 2026; Brief
//   §6.2), whose documents are named instead.
import type { AiCitationRow, AiOutputRow } from "@/lib/queries/api";

export const QUOTE_MAX = 280;

export type Citation = {
  no: number;
  quote: string;
  documentTitle: string | null;
  /** "Open in document": the document page with the cited span (C5's /documents/[id]). */
  href: string | null;
  /** "Official source ↗", or null where the source isn't linked (RBI). */
  officialUrl: string | null;
};

export type SummaryBullet = { text: string; citations: Citation[] };

export type AiBlockData = {
  label: string;
  model: string;
  createdAt: string;
  bullets: SummaryBullet[];
};

/** A quote of at most `max` characters, cut at the last sentence end before the limit (else a word), with "…". */
export function cutQuote(quote: string, max = QUOTE_MAX): string {
  const text = quote.replace(/\s+/g, " ").trim();
  if ([...text].length <= max) return text;
  const head = [...text].slice(0, max - 1).join("");
  const sentence = Math.max(head.lastIndexOf(". "), head.lastIndexOf("? "), head.lastIndexOf("! "));
  if (sentence > max / 3) return `${head.slice(0, sentence + 1)} …`;
  const space = head.lastIndexOf(" ");
  return `${space > 0 ? head.slice(0, space) : head} …`;
}

/** The document page for a cited span: /documents/12?span=340-512#cited (offsets in code points, as stored). */
export function documentHref(c: Pick<AiCitationRow, "document_id" | "char_start" | "char_end">): string | null {
  if (c.document_id === null) return null;
  const span = c.char_start !== null && c.char_end !== null ? `?span=${c.char_start}-${c.char_end}` : "";
  return `/documents/${c.document_id}${span}${span ? "#cited" : ""}`;
}

/** A verified citation as the chip shows it; null if the view returned no quote for it. */
export function toCitation(c: AiCitationRow, linkOfficial: boolean): Citation | null {
  if (!c.quote) return null;
  return {
    no: c.citation_no,
    quote: cutQuote(c.quote),
    documentTitle: c.document_title,
    href: documentHref(c),
    officialUrl: linkOfficial && c.source_url?.startsWith("https://") ? c.source_url : null,
  };
}

type RawBullet = { text: string; citations: number[] };

function bulletsOf(content: unknown): RawBullet[] | null {
  if (typeof content !== "object" || content === null || !Array.isArray((content as { bullets?: unknown }).bullets)) return null;
  const out: RawBullet[] = [];
  for (const b of (content as { bullets: unknown[] }).bullets) {
    if (typeof b !== "object" || b === null) return null;
    const { text, citations } = b as { text?: unknown; citations?: unknown };
    if (typeof text !== "string" || !Array.isArray(citations) || !citations.every((n) => Number.isInteger(n))) return null;
    out.push({ text, citations: citations as number[] });
  }
  return out;
}

/**
 * A summary output (doc_summary, statement_diff_summary) as its block: each bullet with its verified citations.
 * Null when the content isn't the summaries' shape, or no bullet has a verified citation.
 */
export function summaryBlock(
  output: AiOutputRow,
  citations: readonly AiCitationRow[],
  { linkOfficial, label }: { linkOfficial: boolean; label: string },
): AiBlockData | null {
  const raw = bulletsOf(output.content);
  if (!raw) return null;
  const byNo = new Map(citations.filter((c) => c.ai_output_id === output.id).map((c) => [c.citation_no, c]));
  const bullets: SummaryBullet[] = [];
  for (const b of raw) {
    const cites = b.citations
      .map((n) => byNo.get(n))
      .map((c) => (c ? toCitation(c, linkOfficial) : null))
      .filter((c): c is Citation => c !== null);
    if (cites.length > 0 && b.text.trim()) bullets.push({ text: b.text.trim(), citations: cites });
  }
  if (bullets.length === 0) return null;
  return { label: output.display_label ?? label, model: output.model, createdAt: output.created_at, bullets };
}

/** The stance output's quote chip(s): its citations on "/quote". */
export function stanceCitations(output: AiOutputRow, citations: readonly AiCitationRow[]): Citation[] {
  return citations
    .filter((c) => c.ai_output_id === output.id && c.claim_path === "/quote")
    .map((c) => toCitation(c, false))
    .filter((c): c is Citation => c !== null);
}
