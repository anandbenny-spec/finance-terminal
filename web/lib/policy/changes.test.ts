// lib/policy/changes.ts (#152): What changed assembled once for the meeting page's tab and the front page. Test rows,
// not real decisions.
import { describe, expect, it } from "vitest";
import { changesModel, docDateOf, numbersModel } from "./changes";
import type { AiCitationRow, AiOutputRow, DocumentDiffRow, DocumentRow, PolicyMeetingRow, PolicyRateSettingDetailRow } from "@/lib/queries/api";

const doc = (id: number, published_at: string, event_slug: string | null = "fomc-2026-09-17"): DocumentRow => ({
  id, doc_type: "fomc_statement", doc_type_label: "FOMC statement", entity_slug: "fed", event_slug, title: "FOMC statement",
  source_url: "https://www.federalreserve.gov/x.htm", attribution: "Source: Federal Reserve Board", published_at, version_no: 1,
});
const NOW_DOC = doc(2, "2026-09-17T18:00:00+00:00");
const BASE_DOC = doc(1, "2026-07-29T18:00:00+00:00", "fomc-2026-07-29");
const M: PolicyMeetingRow = {
  slug: "fomc-2026-09-17", bank: "Fed", status: "held", decision_date: "2026-09-17", decision_at: "2026-09-17T18:00:00+00:00",
  kind: "scheduled", is_rate_decision: true, meeting_no: null, minutes_due_on: null, policy_rate_pct: 3.5, policy_rate_change_bps: -25,
  stance: null, stance_display_label: null, dissent_count: 1, attribution: "Source: Federal Reserve Board", fed_target_lower_pct: 3.25, fed_target_upper_pct: 3.5,
};
const DIFF: DocumentDiffRow = {
  document_id: 2, base_document_id: 1, words_added: 4, words_removed: 3,
  segments: {
    version: 2,
    pairs: [
      { kind: "same", topic: "Inflation", text: "Unchanged.", old_para: [1], new_para: [1] },
      { kind: "changed", topic: "Decision and guidance", old_para: [2], new_para: [2], ops: [{ op: "eq", text: "Decided to " }, { op: "del", text: "hold" }, { op: "ins", text: "cut" }, { op: "eq", text: "." }] },
    ],
  },
};
const SUMMARY = {
  output: { id: 9, task: "statement_diff_summary", document_id: 2, event_slug: null, scope: "", body: "", output_label: null, display_label: "What changed", model: "m", created_at: "2026-09-17T18:31:00+00:00", content: { bullets: [{ text: "The Committee moved from hold to cut.", citations: [1] }] } } as AiOutputRow,
  citations: [{ ai_output_id: 9, citation_no: 1, claim_path: "/bullets/0", document_id: 2, document_title: "FOMC statement", source_url: "https://www.federalreserve.gov/x.htm", char_start: 0, char_end: 10, quote: "Decided to cut" }] as AiCitationRow[],
};

describe("docDateOf", () => {
  it("is the day the document was published in the bank's zone, else the meeting's date", () => {
    expect(docDateOf("Fed", M, { ...NOW_DOC, published_at: "2026-09-18T01:30:00+00:00" })).toBe("2026-09-17"); // 21:30 New York the day before
    expect(docDateOf("RBI", M, { ...NOW_DOC, published_at: "2026-09-17T20:00:00+00:00" })).toBe("2026-09-18"); // 01:30 IST the next day
    expect(docDateOf("Fed", M, { ...NOW_DOC, published_at: null })).toBe("2026-09-17");
    expect(docDateOf("Fed", M, null)).toBe("2026-09-17");
  });
});

describe("changesModel", () => {
  const model = changesModel({ bank: "Fed", m: M, decisionDoc: NOW_DOC, diff: DIFF, base: BASE_DOC, summary: SUMMARY });

  it("names the two documents and their dates, and links both", () => {
    expect(model.baseLabel).toBe("FOMC statement of 29 Jul 2026");
    expect(model.qualifier).toBe("17 Sep 2026 vs 29 Jul 2026");
    expect(model.before).toEqual({ href: "/documents/1", date: "29 Jul 2026" });
    expect(model.now).toEqual({ href: "/documents/2", date: "17 Sep 2026" });
  });

  it("builds the cited summary, Key changes and the redline from the stored diff, with the stored word counts", () => {
    expect(model.summary?.bullets[0].text).toBe("The Committee moved from hold to cut.");
    expect(model.keys?.changedSentences).toBe(1);
    expect(model.keys?.groups[0].topic).toBe("Decision and guidance");
    expect(model.redline?.unchangedParagraphs).toBe(1);
    expect([model.wordsAdded, model.wordsRemoved]).toEqual([4, 3]);
  });

  it("copes with no base document, no summary and segments it can't read", () => {
    const bare = changesModel({ bank: "Fed", m: M, decisionDoc: NOW_DOC, diff: { ...DIFF, segments: "nope" }, base: null, summary: null });
    expect(bare.baseLabel).toBe("the previous one");
    expect(bare.qualifier).toBe("17 Sep 2026");
    expect(bare.before.date).toBe("before");
    expect([bare.summary, bare.pairs, bare.keys, bare.redline]).toEqual([null, null, null, null]);
  });
});

describe("numbersModel", () => {
  const stance = { show: false, label: null };
  const rows = { settings: [], votes: [], projections: [] };

  it("is none without a base document's meeting, and an error when a read failed", () => {
    expect(numbersModel({ m: M, base: { ...BASE_DOC, event_slug: null }, current: rows, earlier: null, stance }).kind).toBe("none");
    expect(numbersModel({ m: M, base: BASE_DOC, current: null, earlier: { meeting: M, ...rows }, stance }).kind).toBe("error");
    expect(numbersModel({ m: M, base: BASE_DOC, current: rows, earlier: null, stance }).kind).toBe("error");
  });

  it("is none when the earlier meeting isn't there", () => {
    expect(numbersModel({ m: M, base: BASE_DOC, current: rows, earlier: { meeting: null, ...rows }, stance }).kind).toBe("none");
  });

  it("carries the two decisions' dates when the numbers can be compared (the RBI's rates)", () => {
    const rbi = (slug: string, date: string, rate: number) => ({ ...M, slug, bank: "RBI" as const, decision_date: date, policy_rate_pct: rate });
    const setting = (slug: string, value: number) => ({ meeting_slug: slug, bank: "RBI", decision_date: "2026-10-07", instrument: "repo", value_pct: value, change_bps: 0 }) as unknown as PolicyRateSettingDetailRow;
    const cur = rbi("mpc-2026-10-07", "2026-10-07", 5.25);
    const out = numbersModel({
      m: cur,
      base: { ...BASE_DOC, event_slug: "mpc-2026-08-06" },
      current: { settings: [setting(cur.slug, 5.25)], votes: [], projections: [] },
      earlier: { meeting: rbi("mpc-2026-08-06", "2026-08-06", 5.5), settings: [setting("mpc-2026-08-06", 5.5)], votes: [], projections: [] },
      stance,
    });
    expect(out.kind).toBe("items");
    if (out.kind === "items") {
      expect([out.previousDate, out.newDate]).toEqual(["2026-08-06", "2026-10-07"]);
      expect(out.items.find((x) => x.key === "repo")?.change).toBe("−25 bps");
    }
  });

  it("says the numbers can't be compared yet when no rate is stored for either meeting", () => {
    expect(numbersModel({ m: M, base: BASE_DOC, current: rows, earlier: { meeting: { ...M, slug: "x", decision_date: "2026-07-29" }, ...rows }, stance }).kind).toBe("unavailable");
  });
});
