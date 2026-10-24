// The document page's rules (C5): strict URL parsing, the source line (the RBI's named, never linked), and "Cited
// by" only for a span a published citation points at exactly. Test rows, not real documents.
import { describe, expect, it } from "vitest";
import type { AiCitationRow, AiOutputRow, DocumentRow } from "@/lib/queries/api";
import { bankOf, citedBy, documentDate, documentSource, documentTitle, meetingOf, officialUrl, parseDocumentId, parseSpan, pressReleaseLabel, pressReleaseNo } from "./document";

const doc = (over: Partial<DocumentRow> = {}): DocumentRow => ({
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
  ...over,
});

const rbiDoc = doc({
  id: 40,
  doc_type: "mpc_resolution",
  doc_type_label: "MPC resolution",
  entity_slug: "rbi",
  event_slug: "mpc-2026-10-07",
  title: "Monetary Policy Statement, 2026-27 Resolution of the Monetary Policy Committee",
  source_url: "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1",
  attribution: "Source: Reserve Bank of India",
  published_at: "2026-10-07T04:30:00Z",
});

const output = (over: Partial<AiOutputRow> = {}): AiOutputRow => ({
  id: 7,
  task: "doc_summary",
  document_id: 12,
  event_slug: "fomc-2026-09-16",
  scope: "",
  body: null,
  output_label: null,
  content: { bullets: [] },
  display_label: "AI summary",
  model: "claude-haiku-5-5",
  created_at: "2026-09-16T18:20:00Z",
  ...over,
});

const cite = (over: Partial<AiCitationRow> = {}): AiCitationRow => ({
  ai_output_id: 7,
  citation_no: 1,
  claim_path: "/bullets/1",
  document_id: 12,
  document_title: "FOMC statement",
  source_url: null,
  char_start: 100,
  char_end: 160,
  quote: "decided to lower the target range",
  ...over,
});

describe("parseDocumentId", () => {
  it("takes a positive integer only", () => {
    expect(parseDocumentId("12")).toBe(12);
    for (const bad of ["0", "-1", "12a", "1.5", "", "012", " 12", "99999999999999999"]) expect(parseDocumentId(bad)).toBeNull();
  });
});

describe("parseSpan", () => {
  it("takes start-end with start before end", () => {
    expect(parseSpan("340-512")).toEqual([340, 512]);
    expect(parseSpan(["0-5", "9-10"])).toEqual([0, 5]);
    for (const bad of [undefined, "", "5-5", "9-3", "a-b", "1-2-3", "-1-4", "1e3-2e3"]) expect(parseSpan(bad)).toBeNull();
  });
});

describe("the document's bank, date and source", () => {
  it("knows the two banks and nothing else", () => {
    expect(bankOf(doc())).toBe("Fed");
    expect(bankOf(rbiDoc)).toBe("RBI");
    expect(bankOf(doc({ entity_slug: "nifty-50" }))).toBeNull();
  });

  it("dates a document in its bank's own zone, else by its meeting", () => {
    // 21:30 ET on 16 Sep is 01:30 UTC on 17 Sep: still the 16th in New York.
    expect(documentDate(doc({ published_at: "2026-09-17T01:30:00Z" }), "Fed")).toBe("2026-09-16");
    // 20:00 UTC on 6 Oct is 01:30 IST on 7 Oct.
    expect(documentDate(doc({ published_at: "2026-10-06T20:00:00Z" }), "RBI")).toBe("2026-10-07");
    expect(documentDate(doc({ published_at: null }), "Fed")).toBe("2026-09-16");
    expect(documentDate(doc({ published_at: null, event_slug: null }), "Fed")).toBeNull();
  });

  it("names the meeting from its slug", () => {
    expect(meetingOf("mpc-2026-10-07")).toEqual({ bank: "RBI", date: "2026-10-07", label: "MPC meeting, 7 Oct 2026", href: "/policy/rbi/2026-10-07" });
    expect(meetingOf("fomc-2026-09-16")?.href).toBe("/policy/fed/2026-09-16");
    expect(meetingOf("ipo-2026-10-07")).toBeNull();
    expect(meetingOf(null)).toBeNull();
  });

  it("links the Fed's document and names the RBI's (never a link)", () => {
    expect(documentSource(doc(), "Fed")).toBe("Source: Federal Reserve Board");
    expect(officialUrl(doc(), "Fed")).toMatch(/^https:\/\/www\.federalreserve\.gov\//);
    expect(officialUrl(doc({ source_url: "http://example.com" }), "Fed")).toBeNull();
    expect(documentSource(rbiDoc, "RBI")).toBe("Source: Reserve Bank of India · MPC resolution · 7 Oct 2026");
    expect(officialUrl(rbiDoc, "RBI")).toBeNull();
  });

  // Brief §6.2 (Anand, 24 Oct 2026): RBI's documents are found on rbi.org.in by their press-release number, which
  // policy.rbi_documents stores at the end of the title (B6 part 2): the source line carries it, the title drops it.
  it("names the RBI's press-release number in the source line, not in the title", () => {
    const numbered = { ...rbiDoc, title: "MPC resolution, 7 Oct 2026 · Press Release: 2026-2027/1264" };
    expect(pressReleaseNo(numbered)).toBe("2026-2027/1264");
    expect(pressReleaseLabel(numbered)).toBe("Press Release: 2026-2027/1264");
    expect(documentTitle(numbered)).toBe("MPC resolution, 7 Oct 2026");
    expect(documentSource(numbered, "RBI")).toBe(
      "Source: Reserve Bank of India · MPC resolution · 7 Oct 2026 · Press Release: 2026-2027/1264");
    // A title without one (the Fed's, or an RBI page that has none) is shown as stored, with no number.
    expect(pressReleaseNo(doc())).toBeNull();
    expect(documentTitle(doc({ title: "FOMC statement, 16 Sep 2026" }))).toBe("FOMC statement, 16 Sep 2026");
    expect(documentSource(rbiDoc, "RBI")).not.toContain("Press Release");
    // Only the stored shape counts: a title that merely mentions a press release keeps it.
    expect(pressReleaseNo({ title: "Press Release: 2026-2027/1264 and more" })).toBeNull();
  });
});

describe("citedBy", () => {
  const span = [100, 160] as const;

  it("names the output and the point (claim path /bullets/1 is point 2)", () => {
    expect(citedBy(12, span, [cite()], [output()])).toEqual([{ text: "AI summary, point 2", href: null }]);
  });

  it("links outputs shown elsewhere: What changed on its meeting's tab, the stance on its meeting page", () => {
    const outputs = [
      output({ id: 8, task: "statement_diff_summary", document_id: 13, event_slug: "fomc-2026-10-28" }),
      output({ id: 9, task: "stance_extraction", document_id: null, event_slug: "mpc-2026-10-07" }),
    ];
    const cites = [cite({ ai_output_id: 8, claim_path: "/bullets/0" }), cite({ ai_output_id: 9, claim_path: "/quote" })];
    expect(citedBy(12, span, cites, outputs)).toEqual([
      { text: "What changed, point 1", href: "/policy/fed/2026-10-28?tab=changes" },
      { text: "AI-extracted stance", href: "/policy/rbi/2026-10-07" },
    ]);
  });

  it("ignores other spans, other documents, quote-less rows and outputs the view didn't return", () => {
    const cites = [
      cite({ char_start: 101 }),
      cite({ char_end: 161 }),
      cite({ document_id: 99 }),
      cite({ quote: null }),
      cite({ ai_output_id: 70 }), // unpublished or held back: api.ai_outputs didn't return it
    ];
    expect(citedBy(12, span, cites, [output()])).toEqual([]);
    expect(citedBy(12, span, [cite()], [])).toEqual([]);
    expect(citedBy(12, span, [cite()], [output({ task: "hawk_dove_score" })])).toEqual([]);
  });

  it("lists each point once", () => {
    expect(citedBy(12, span, [cite(), cite({ citation_no: 2 }), cite({ citation_no: 3, claim_path: "/bullets/3" })], [output()])).toEqual([
      { text: "AI summary, point 2", href: null },
      { text: "AI summary, point 4", href: null },
    ]);
  });
});
