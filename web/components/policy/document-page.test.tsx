// /documents/[id] rendered to HTML as the server sends it (C5), with the reads stubbed: the text rebuilt from the
// chunks with ¶ anchors, the cited span in <mark> with "Cited by" above it only when a published citation points at
// exactly that span, the AI summary collapsed above the text, the RBI's document named but never linked, and every
// panel's source line and freshness. Plus the span and output reads against the row-limited fake Data API. Test
// rows, not real documents.
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AiCitationRow, AiOutputRow, DocumentChunkRow, DocumentRow, FieldPolicyRow } from "@/lib/queries/api";
import type { DocumentPage as PageData } from "@/lib/queries/document";
import { fakeClient, type Row } from "@/tests/fake-postgrest";
import { TooltipProvider } from "@/components/ui/tooltip";

const reads = vi.hoisted(() => ({
  page: null as unknown as PageData,
  span: null as null | { ok: true; citations: AiCitationRow[]; outputs: AiOutputRow[] } | { ok: false },
  spanCalls: [] as [number, number, number][],
}));

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

vi.mock("@/lib/queries/document", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/queries/document")>()),
  readDocumentPage: async () => reads.page,
  readSpanCitations: async (id: number, start: number, end: number) => {
    reads.spanCalls.push([id, start, end]);
    return reads.span;
  },
}));

vi.mock("@/lib/queries/shell", () => ({
  readFreshness: async () => ({
    ok: true,
    tasks: [
      { task_key: "policy.fed_documents", last_checked_at: "2026-10-20T01:11:00Z", last_success_at: "2026-10-20T01:11:00Z", status: "ok" },
      { task_key: "policy.rbi_documents", last_checked_at: "2026-10-20T01:11:00Z", last_success_at: "2026-10-20T01:11:00Z", status: "ok" },
      { task_key: "policy.ai_batch", last_checked_at: "2026-10-20T01:11:00Z", last_success_at: "2026-10-20T01:11:00Z", status: "ok" },
    ],
  }),
}));

const { DocumentPage } = await import("@/components/policy/DocumentPage");
const { readCitationsOfSpan, readOutputs } = await import("@/lib/queries/document");

const TEXT = "The Committee decided to lower the target range by 1/4 point.\n\nInflation has moved up — ₹ and 𝑥 count once.\n\nVoting for the action were A and B.";

function chunksOf(text: string, size: number): DocumentChunkRow[] {
  const cps = [...text];
  const out: DocumentChunkRow[] = [];
  for (let at = 0, n = 0; at < cps.length; at += size, n++) {
    const body = cps.slice(at, at + size).join("");
    out.push({ document_id: 12, chunk_no: n, char_start: at, char_end: at + [...body].length, section: null, speaker: null, body });
  }
  return out;
}

const FED_DOC: DocumentRow = {
  id: 12,
  doc_type: "fomc_statement",
  doc_type_label: "FOMC statement",
  entity_slug: "fed",
  event_slug: "fomc-2026-09-16",
  title: "Federal Reserve issues FOMC statement",
  source_url: "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
  attribution: "Source: Federal Reserve Board",
  published_at: "2026-09-16T18:00:00Z",
  version_no: 1,
};

const RBI_DOC: DocumentRow = {
  ...FED_DOC,
  id: 40,
  doc_type: "mpc_resolution",
  doc_type_label: "MPC resolution",
  entity_slug: "rbi",
  event_slug: "mpc-2026-10-07",
  title: "Resolution of the Monetary Policy Committee",
  source_url: "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1",
  attribution: "Source: Reserve Bank of India",
  published_at: "2026-10-07T04:30:00Z",
};

const POLICIES: FieldPolicyRow[] = [{ field_key: "ai.doc_summary", status: "labelled", label: "AI summary" }];

// "Inflation has moved up" is paragraph 2; its span in code points.
const P2 = [..."The Committee decided to lower the target range by 1/4 point.\n\n"].length;
const SPAN: [number, number] = [P2, P2 + "Inflation has moved up".length];

const SUMMARY: AiOutputRow = {
  id: 7,
  task: "doc_summary",
  document_id: 12,
  event_slug: "fomc-2026-09-16",
  scope: "",
  body: null,
  output_label: null,
  content: { bullets: [{ text: "The Committee cut the range.", citations: [1] }, { text: "Inflation moved up.", citations: [2] }] },
  display_label: "AI summary",
  model: "claude-haiku-5-5",
  created_at: "2026-09-16T18:20:00Z",
};

const CITES: AiCitationRow[] = [
  { ai_output_id: 7, citation_no: 1, claim_path: "/bullets/0", document_id: 12, document_title: "FOMC statement", source_url: FED_DOC.source_url, char_start: 4, char_end: 40, quote: "Committee decided to lower the target" },
  { ai_output_id: 7, citation_no: 2, claim_path: "/bullets/1", document_id: 12, document_title: "FOMC statement", source_url: FED_DOC.source_url, char_start: SPAN[0], char_end: SPAN[1], quote: "Inflation has moved up" },
];

function page(over: Partial<PageData> = {}, doc: DocumentRow = FED_DOC): PageData {
  return {
    readAt: "2026-10-20T03:00:00Z",
    document: { ok: true, row: doc },
    chunks: { ok: true, rows: chunksOf(TEXT, 30).map((c) => ({ ...c, document_id: doc.id })) },
    fieldPolicy: { ok: true, rows: POLICIES },
    summary: { ok: true, cited: { output: SUMMARY, citations: CITES } },
    ...over,
  };
}

const render = async (id: number, span: [number, number] | null) =>
  renderToStaticMarkup(<TooltipProvider>{await DocumentPage({ id, span })}</TooltipProvider>);
const words = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

beforeEach(() => {
  reads.page = page();
  reads.span = null;
  reads.spanCalls = [];
});

describe("the document page", () => {
  it("rebuilds the text from the chunks with a ¶ anchor and link per paragraph", async () => {
    const out = await render(12, null);
    for (const n of [1, 2, 3]) {
      expect(out).toContain(`id="p-${n}"`);
      expect(out).toContain(`href="#p-${n}"`);
    }
    expect(words(out)).toContain("Inflation has moved up — ₹ and 𝑥 count once. ¶2");
    expect(out).not.toContain("<mark");
    expect(reads.spanCalls).toEqual([]);
  });

  it("shows the title, the bank, the date, the meeting crumb, the source line, freshness and the Fed's official link", async () => {
    const out = await render(12, null);
    const text = words(out);
    expect(text).toContain("Federal Reserve issues FOMC statement");
    expect(text).toContain("Federal Reserve · FOMC statement · 16 Sep 2026");
    expect(out).toContain('href="/policy/fed/2026-09-16"');
    expect(text).toContain("FOMC meeting, 16 Sep 2026");
    expect(text).toContain("Source: Federal Reserve Board");
    expect(text).toMatch(/Data to 16 Sep 2026/);
    expect(out).toContain(`href="${FED_DOC.source_url}"`);
    expect(text).toContain("Official source ↗");
  });

  it("puts the AI summary, collapsed, above the text", async () => {
    const out = await render(12, null);
    expect(out).toMatch(/<details[^>]*>/);
    expect(out).not.toMatch(/<details[^>]* open/);
    expect(out.indexOf("<details")).toBeLessThan(out.indexOf('id="p-1"'));
    expect(words(out)).toMatch(/Show the AI summary .*· 2 points/);
    expect(words(out)).toContain("Inflation moved up.");
    // Each point ends in its chip; the chip's passage (and its "Open in document" link) opens on activation.
    expect(out).toContain("Source 2: show the cited passage");
  });

  it("marks a cited span and says who cited it, above its paragraph", async () => {
    reads.span = { ok: true, citations: [CITES[1]], outputs: [SUMMARY] };
    const out = await render(12, SPAN);
    expect(reads.spanCalls).toEqual([[12, SPAN[0], SPAN[1]]]);
    expect(out).toContain('<mark id="cited"><span>Inflation has moved up</span></mark>');
    expect(words(out)).toContain("Cited by: AI summary, point 2");
    expect(out.indexOf("Cited by")).toBeGreaterThan(out.indexOf('id="p-1"'));
    expect(out.indexOf("Cited by")).toBeLessThan(out.indexOf('id="p-2"'));
  });

  it("highlights nothing for a span no published citation points at, or past the text's end", async () => {
    reads.span = { ok: true, citations: [], outputs: [] };
    expect(await render(12, [5, 9])).not.toContain("<mark");
    reads.span = { ok: true, citations: [{ ...CITES[1], char_start: 9000, char_end: 9010 }], outputs: [SUMMARY] };
    expect(await render(12, [9000, 9010])).not.toContain("<mark");
    reads.span = { ok: false };
    const out = await render(12, SPAN);
    expect(out).not.toContain("<mark");
    expect(out).not.toContain("Cited by");
  });

  it("names the RBI's document and date and never links to RBI's pages", async () => {
    reads.page = page({ summary: { ok: true, cited: null } }, RBI_DOC);
    const out = await render(40, null);
    expect(words(out)).toContain("Source: Reserve Bank of India · MPC resolution · 7 Oct 2026");
    expect(out).not.toContain("rbi.org.in");
    expect(out).not.toContain("Official source");
    expect(out).toContain('href="/policy/rbi/2026-10-07"');
    expect(words(out)).toContain("No AI summary of this document yet.");
  });

  it("hides the summary when its field policy is missing (fail closed)", async () => {
    reads.page = page({ fieldPolicy: { ok: true, rows: [] } });
    const out = await render(12, null);
    expect(out).not.toContain("<details");
    expect(out).not.toContain("Inflation moved up.");
  });

  it("never leaves a panel blank: failed reads show the error note", async () => {
    reads.page = page({ chunks: { ok: false } });
    expect(words(await render(12, null))).toContain("Couldn't load this panel. Try again.");
    reads.page = page({ chunks: { ok: true, rows: [{ ...chunksOf(TEXT, 30)[0], char_start: 3 }] } });
    expect(words(await render(12, null))).toContain("Couldn't load this panel. Try again.");
    reads.page = page({ summary: { ok: false } });
    expect(words(await render(12, null))).toContain("Couldn't load the AI summary.");
    reads.page = page({ document: { ok: false } });
    expect(words(await render(12, null))).toContain("Couldn't load this panel. Try again.");
  });

  it("answers 404 for a missing document or another entity's", async () => {
    reads.page = page({ document: { ok: true, row: null } });
    await expect(render(12, null)).rejects.toThrow("NEXT_NOT_FOUND");
    reads.page = page({}, { ...FED_DOC, entity_slug: "nifty-50" });
    await expect(render(12, null)).rejects.toThrow("NEXT_NOT_FOUND");
  });
});

describe("the span and output reads (N2)", () => {
  const citationRows = (n: number): Row[] =>
    Array.from({ length: n }, (_, i) => ({ ...CITES[1], ai_output_id: i + 1, citation_no: 1 }));

  it("reads every citation of exactly that span of that document, past the 1,000-row cut", async () => {
    const store = {
      ai_citations: [...citationRows(1200), { ...CITES[1], char_start: SPAN[0] + 1 }, { ...CITES[1], document_id: 99 }],
    };
    const { db, seen } = fakeClient(store);
    const rows = await readCitationsOfSpan(db, 12, SPAN);
    expect(rows).toHaveLength(1200);
    expect(seen.every((r) => r.countExact)).toBe(true);
  });

  it("reads outputs by id, each once, and refuses an unbounded list", async () => {
    const { db } = fakeClient({ ai_outputs: [{ ...SUMMARY, content: null } as Row, { ...SUMMARY, id: 8, content: null } as Row] });
    expect((await readOutputs(db, [7, 7, 8])).map((o) => o.id)).toEqual([7, 8]);
    expect(await readOutputs(db, [])).toEqual([]);
    await expect(readOutputs(db, Array.from({ length: 101 }, (_, i) => i + 1))).rejects.toThrow(/asked for 101 ids/);
  });
});
