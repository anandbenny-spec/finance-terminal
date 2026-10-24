// web/lib/policy/document.ts: what /documents/[id] shows around a document's text (C5; Spec 01 §4.4; Design 01
// §4.5, DocumentView). Pure.
//
// - The id and the cited span come from the URL, so both are parsed strictly: a malformed one is ignored (no mark),
//   never passed on to a query as is.
// - A span is highlighted only when a published, verified citation points at exactly that span of this document
//   (api.ai_citations). A hand-made ?span= highlights nothing, so the page never marks text as "cited" when no AI
//   output cited it (CLAUDE.md §1: anything a visitor sees as fact is right, labelled or absent).
// - "Cited by: AI summary, point 2" names the output's kind by its task, and the point by the citation's claim path
//   ("/bullets/1" is point 2; "/quote" is the stance's quote).
// - Source lines (CLAUDE.md §1; Anand, 24 Oct 2026): the Fed's document carries its attribution and keeps its
//   official link; the RBI's names the document, its date and RBI's press-release number, and is never linked
//   (Brief §6.2). policy.rbi_documents stores the number at the end of the document's title ("MPC minutes, 7 Oct 2026
//   · Press Release: 2026-2027/1361": issue #15, B6 part 2), so the title is shown without it and the source line
//   carries it. The number is RBI's reference, quoted as the page prints it, not a figure for lib/format.ts.
import { dateInZone, formatDate, type EventZone } from "@/lib/format";
import { meetingHref } from "@/lib/policy/decisions";
import type { AiCitationRow, AiOutputRow, DocumentRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

/** Each bank's entity slug (core.entity) and zone (its documents' dates are its own calendar days). */
const BANK_OF_ENTITY: Record<string, Bank> = { fed: "Fed", rbi: "RBI" };
export const BANK_ZONE: Record<Bank, EventZone> = { Fed: "America/New_York", RBI: "Asia/Kolkata" };
export const BANK_NAME: Record<Bank, string> = { Fed: "Federal Reserve", RBI: "Reserve Bank of India" };
const DEFAULT_SOURCE: Record<Bank, string> = { Fed: "Source: Federal Reserve Board", RBI: "Source: Reserve Bank of India" };

/** A document id from the URL: a positive integer that fits a bigint the browser can hold exactly, else null. */
export function parseDocumentId(raw: string): number | null {
  if (!/^[1-9]\d{0,15}$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : null;
}

/** A cited span from the URL (?span=340-512, code points, end exclusive), else null. */
export function parseSpan(raw: string | string[] | undefined): [number, number] | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const m = value ? /^(\d{1,9})-(\d{1,9})$/.exec(value) : null;
  if (!m) return null;
  const start = Number(m[1]);
  const end = Number(m[2]);
  return start < end ? [start, end] : null;
}

/** The bank a document belongs to, or null for any other entity (module 1's page shows the two banks' only). */
export function bankOf(doc: Pick<DocumentRow, "entity_slug">): Bank | null {
  return BANK_OF_ENTITY[doc.entity_slug] ?? null;
}

/** The day the document was published, in the bank's zone; else the decision date in its meeting's slug. */
export function documentDate(doc: Pick<DocumentRow, "published_at" | "event_slug">, bank: Bank): string | null {
  if (doc.published_at) return dateInZone(doc.published_at, BANK_ZONE[bank]);
  return meetingOf(doc.event_slug)?.date ?? null;
}

/** The meeting a document belongs to, from its slug (fomc-YYYY-MM-DD, mpc-YYYY-MM-DD), or null. */
export function meetingOf(slug: string | null): { bank: Bank; date: string; label: string; href: string } | null {
  const m = slug ? /^(fomc|mpc)-(\d{4}-\d{2}-\d{2})$/.exec(slug) : null;
  if (!m) return null;
  const bank: Bank = m[1] === "fomc" ? "Fed" : "RBI";
  const date = m[2];
  return { bank, date, label: `${bank === "Fed" ? "FOMC" : "MPC"} meeting, ${formatDate(date)}`, href: meetingHref(bank, date) };
}

const PRESS_RELEASE = / · Press Release: (\d{4}-\d{4}\/\d+)$/;

/** RBI's press-release number stored at the end of a document's title ("2026-2027/1361"), else null. */
export function pressReleaseNo(doc: Pick<DocumentRow, "title">): string | null {
  return PRESS_RELEASE.exec(doc.title)?.[1] ?? null;
}

/** The document's title as the page shows it: without the press-release number (the source line carries that). */
export function documentTitle(doc: Pick<DocumentRow, "title">): string {
  return doc.title.replace(PRESS_RELEASE, "");
}

/** "Press Release: 2026-2027/1264", the way RBI's page prints it, or null where the document has no number. */
export function pressReleaseLabel(doc: Pick<DocumentRow, "title">): string | null {
  const no = pressReleaseNo(doc);
  return no ? `Press Release: ${no}` : null;
}

/** The document's source line: the Fed's attribution; the RBI's names the document, its date and RBI's press-release
 * number (never a link). */
export function documentSource(doc: DocumentRow, bank: Bank): string {
  const attribution = doc.attribution ?? DEFAULT_SOURCE[bank];
  if (bank === "Fed") return attribution;
  const date = documentDate(doc, bank);
  return [attribution, doc.doc_type_label, date ? formatDate(date) : null, pressReleaseLabel(doc)].filter(Boolean).join(" · ");
}

/** "Official source ↗": the Fed's own page only (https), never the RBI's (Anand, 24 Oct 2026). */
export function officialUrl(doc: DocumentRow, bank: Bank): string | null {
  return bank === "Fed" && doc.source_url?.startsWith("https://") ? doc.source_url : null;
}

/** Each AI task's kind as its block names it (Design 01 §1.10). */
const KIND: Record<string, string> = {
  doc_summary: "AI summary",
  statement_diff_summary: "What changed",
  stance_extraction: "AI-extracted stance",
  projection_extraction: "AI-extracted projection",
};

export type CitedBy = {
  /** "AI summary, point 2" */
  text: string;
  /** Where that output is shown, when it isn't this page's own summary; else null. */
  href: string | null;
};

/**
 * Where an output is shown: null for this page's own summary; "What changed" on its meeting's What changed tab; the
 * stance on its meeting page; another document's summary on that document's page.
 */
function shownAt(o: AiOutputRow, documentId: number): string | null {
  if (o.task === "doc_summary") return o.document_id === documentId ? null : o.document_id !== null ? `/documents/${o.document_id}` : null;
  const meeting = meetingOf(o.event_slug);
  if (o.task === "statement_diff_summary") {
    if (meeting) return `${meeting.href}?tab=changes`;
    return o.document_id !== null && o.document_id !== documentId ? `/documents/${o.document_id}` : null;
  }
  return meeting?.href ?? null;
}

function pointOf(claimPath: string): number | null {
  const m = /^\/bullets\/(\d+)$/.exec(claimPath);
  return m ? Number(m[1]) + 1 : null;
}

/**
 * Who cited `span` of document `documentId`: one entry per published output citing exactly that span, in the
 * outputs' order. Citations of other spans or documents, and citations of outputs the view didn't return
 * (unpublished or held back), count for nothing; an empty list means the span isn't highlighted.
 */
export function citedBy(
  documentId: number,
  span: readonly [number, number],
  citations: readonly AiCitationRow[],
  outputs: readonly AiOutputRow[],
): CitedBy[] {
  const out: CitedBy[] = [];
  const seen = new Set<string>();
  for (const o of [...outputs].sort((a, b) => a.id - b.id)) {
    const kind = KIND[o.task];
    if (!kind) continue;
    const mine = citations.filter(
      (c) => c.ai_output_id === o.id && c.document_id === documentId && c.char_start === span[0] && c.char_end === span[1] && c.quote,
    );
    const points = [...new Set(mine.map((c) => pointOf(c.claim_path)))].sort((a, b) => (a ?? 0) - (b ?? 0));
    for (const p of points) {
      const text = p === null ? kind : `${kind}, point ${p}`;
      const href = shownAt(o, documentId);
      const key = `${text}|${href}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ text, href });
    }
  }
  return out;
}
