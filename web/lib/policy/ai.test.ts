// AI outputs as the pages show them (C3): only cited bullets, quotes from stored text cut at a sentence, and no
// official link for the RBI (Anand, 24 Oct 2026).
import { describe, expect, it } from "vitest";
import type { AiCitationRow, AiOutputRow } from "@/lib/queries/api";
import { cutQuote, documentHref, stanceCitations, summaryBlock } from "./ai";

const output = (over: Partial<AiOutputRow> = {}): AiOutputRow => ({
  id: 7, task: "doc_summary", document_id: 12, event_slug: null, scope: "", body: "", output_label: null,
  content: { bullets: [{ text: "The Committee lowered the range.", citations: [1] }, { text: "Uncited claim.", citations: [9] }] },
  display_label: "AI summary", model: "claude-haiku-5-5", created_at: "2026-09-17T18:32:00+00:00", ...over,
});
const cite = (over: Partial<AiCitationRow> = {}): AiCitationRow => ({
  ai_output_id: 7, citation_no: 1, claim_path: "/bullets/0", document_id: 12, document_title: "FOMC statement",
  source_url: "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm", char_start: 340, char_end: 412,
  quote: "the Committee decided to lower the target range", ...over,
});

describe("summaryBlock", () => {
  it("keeps only bullets with a verified citation", () => {
    const block = summaryBlock(output(), [cite()], { linkOfficial: true, label: "AI summary" })!;
    expect(block.bullets).toHaveLength(1);
    expect(block.bullets[0].text).toBe("The Committee lowered the range.");
    expect(block.bullets[0].citations[0]).toEqual({
      no: 1,
      quote: "the Committee decided to lower the target range",
      documentTitle: "FOMC statement",
      href: "/documents/12?span=340-412#cited",
      officialUrl: "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm",
    });
  });

  it("never links the RBI's pages", () => {
    const block = summaryBlock(output(), [cite({ source_url: "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1" })], { linkOfficial: false, label: "AI summary" })!;
    expect(block.bullets[0].citations[0].officialUrl).toBeNull();
  });

  it("is null for content that isn't the summaries' shape, or with no cited bullet", () => {
    expect(summaryBlock(output({ content: { bullets: "x" } }), [cite()], { linkOfficial: true, label: "x" })).toBeNull();
    expect(summaryBlock(output(), [], { linkOfficial: true, label: "x" })).toBeNull();
    expect(summaryBlock(output(), [cite({ quote: null })], { linkOfficial: true, label: "x" })).toBeNull();
  });
});

describe("quotes", () => {
  it("cuts a long quote at a sentence boundary, with an ellipsis", () => {
    const long = `${"A first sentence of the passage. ".repeat(6)}${"word ".repeat(40)}`;
    const cut = cutQuote(long);
    expect([...cut].length).toBeLessThanOrEqual(282);
    expect(cut.endsWith(". …")).toBe(true);
    expect(cutQuote("Short.")).toBe("Short.");
  });

  it("links the document with the cited span, in code points as stored", () => {
    expect(documentHref({ document_id: 3, char_start: 0, char_end: 10 })).toBe("/documents/3?span=0-10#cited");
    expect(documentHref({ document_id: 3, char_start: null, char_end: null })).toBe("/documents/3");
    expect(documentHref({ document_id: null, char_start: 0, char_end: 1 })).toBeNull();
  });

  it("takes the stance's quote chip from its /quote citation, unlinked", () => {
    const stance = output({ id: 8, task: "stance_extraction", content: { stance: "neutral", quote: "q" } });
    const chips = stanceCitations(stance, [cite({ ai_output_id: 8, claim_path: "/quote", source_url: "https://rbi.org.in/x" }), cite()]);
    expect(chips).toHaveLength(1);
    expect(chips[0].officialUrl).toBeNull();
  });
});
