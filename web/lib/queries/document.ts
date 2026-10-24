// web/lib/queries/document.ts: what /documents/[id] reads (C5; Spec 01 §4.4), from `api` views with the browser key
// only. Lists go through readAll (an exact count, then pages until it has every row, or a thrown error: N2), so a
// long minutes or transcript is never cut at the Data API's 1,000 rows. Cached for minutes like the rest of the
// site. A failed read leaves its part in the error state (StateNote); the reason goes to the server log only.
import { cacheLife, cacheTag } from "next/cache";
import type { AiCitationRow, AiOutputRow, DocumentChunkRow, DocumentRow, FieldPolicyRow } from "@/lib/queries/api";
import { readAiOutput, readChunks, readCitations, readDocument, readFieldPolicy, type PolicyDb } from "@/lib/queries/policy";
import { IncompleteReadError, readAll } from "@/lib/queries/read";
import type { Read } from "@/lib/queries/shell";
import { publicClient } from "@/lib/supabase/public";

const UNCONFIGURED = "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set";
const OUTPUT_COLUMNS = "id, task, document_id, event_slug, scope, body, output_label, content, display_label, model, created_at";
const CITATION_COLUMNS = "ai_output_id, citation_no, claim_path, document_id, document_title, source_url, char_start, char_end, quote";

function failed(what: string, error: unknown): { ok: false } {
  console.error(`[document] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
  return { ok: false };
}

async function attempt<T extends object>(what: string, read: () => Promise<T>): Promise<Read<T>> {
  try {
    return { ok: true, ...(await read()) };
  } catch (error) {
    return failed(what, error);
  }
}

export type CitedOutput = { output: AiOutputRow; citations: AiCitationRow[] };

/** Every published, verified citation of exactly `span` of a document (usually one or two). */
export async function readCitationsOfSpan(db: PolicyDb, documentId: number, span: readonly [number, number]): Promise<AiCitationRow[]> {
  return readAll<AiCitationRow>(`api.ai_citations of document ${documentId} ${span[0]}-${span[1]}`, (from, to) =>
    db
      .from("ai_citations")
      .select(CITATION_COLUMNS, { count: "exact" })
      .eq("document_id", documentId)
      .eq("char_start", span[0])
      .eq("char_end", span[1])
      .order("ai_output_id")
      .order("citation_no")
      .range(from, to),
  );
}

/** Published AI outputs by id (the view returns only those whose field policy is shown or labelled). */
export async function readOutputs(db: PolicyDb, ids: readonly number[]): Promise<AiOutputRow[]> {
  const unique = [...new Set(ids)];
  if (unique.length === 0) return [];
  if (unique.length > 100) throw new IncompleteReadError("api.ai_outputs by id", `asked for ${unique.length} ids at once`);
  return readAll<AiOutputRow>(`api.ai_outputs ${unique.join(",")}`, (from, to) =>
    db.from("ai_outputs").select(OUTPUT_COLUMNS, { count: "exact" }).in("id", unique).order("id").range(from, to),
  );
}

export type DocumentPage = {
  readAt: string;
  /** Null when no published document has this id (the page answers 404). */
  document: Read<{ row: DocumentRow | null }>;
  chunks: Read<{ rows: DocumentChunkRow[] }>;
  fieldPolicy: Read<{ rows: FieldPolicyRow[] }>;
  /** The document's own AI summary (doc_summary) with its verified citations, or null. */
  summary: Read<{ cited: CitedOutput | null }>;
};

/** The document, its text (its chunks), the field policies and its AI summary. */
export async function readDocumentPage(id: number): Promise<DocumentPage> {
  "use cache";
  cacheLife("minutes");
  cacheTag("documents", `document-${id}`);
  const readAt = new Date().toISOString();
  const db = publicClient();
  if (!db) {
    const no = failed(`document ${id}`, UNCONFIGURED);
    return { readAt, document: no, chunks: no, fieldPolicy: no, summary: no };
  }
  const [document, chunks, fieldPolicy, summary] = await Promise.all([
    attempt(`document ${id}`, async () => ({ row: await readDocument(db, id) })),
    attempt(`document ${id} chunks`, async () => ({ rows: await readChunks(db, id) })),
    attempt("field policy", async () => ({ rows: await readFieldPolicy(db) })),
    attempt(`document ${id} summary`, async () => {
      const output = await readAiOutput(db, "doc_summary", { documentId: id });
      return { cited: output ? { output, citations: await readCitations(db, output.id) } : null };
    }),
  ]);
  return { readAt, document, chunks, fieldPolicy, summary };
}

/** The citations of one span of a document and the published outputs they belong to ("Cited by: …"). */
export async function readSpanCitations(
  id: number,
  start: number,
  end: number,
): Promise<Read<{ citations: AiCitationRow[]; outputs: AiOutputRow[] }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("documents", `document-${id}`);
  const db = publicClient();
  if (!db) return failed(`document ${id} span`, UNCONFIGURED);
  return attempt(`document ${id} span ${start}-${end}`, async () => {
    const citations = await readCitationsOfSpan(db, id, [start, end]);
    return { citations, outputs: await readOutputs(db, citations.map((c) => c.ai_output_id)) };
  });
}
