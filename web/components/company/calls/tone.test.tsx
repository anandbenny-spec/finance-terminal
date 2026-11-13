// The Earnings calls tab's Tone panel (2B-12b, #482; Spec 02B §5.7, §8): held back while ai.call_tone is hidden (no figure, no quote),
// the stat in words with its scale and passage count, the change line in each of its three labels and none, the opening and answers
// stats, evidence only from `content.evidence` (a fallback output shows the "No passage…" note and never its citation), the held,
// role-held and not-scored states, the history's table, no colour and no Delta, the explainer's wording (never "hand-labelled"), and
// every number through lib/format.ts. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { RUBRIC, changeDetail, changeLine, heldText, partValue, toneEvidence, toneHistory, toneOpen, toneState, toneValue } from "@/lib/equity/tone";
import type { AiOutputRow, CallToneRow } from "@/lib/queries/api";
import type { CallsTabData, ToneRead } from "@/lib/queries/calls";
import { bannedCallWordsIn } from "@/tests/banned-words";
import { SHOWN, call, calls, citation, pageRow } from "@/tests/call-rows";
import { page } from "@/tests/company-rows";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => undefined }) }));

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();
const html = (c: CallsTabData | null) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "calls", overview: null, calls: c })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

const OPEN = [...SHOWN, { field_key: "ai.call_tone", status: "labelled" as const, label: "AI-scored" }];
const OUT = 9100;

function row(over: Partial<CallToneRow> = {}): CallToneRow {
  return {
    company_slug: "tcs", call_slug: "q2-fy27", fiscal_label: "Q2 FY27", period_end: "2026-09-30", call_date: null, filed_at: null, exchange: "NSE",
    ai_output_id: OUT, n: 18, mean: 0.6, sd: 0.9, opening_n: 6, opening_mean: 1, answers_n: 12, answers_mean: 0.4, prev_call_slug: "q1-fy27",
    prev_fiscal_label: "Q1 FY27", delta: 0.4, se_delta: 0.15, change: "more_positive", held_reason: null, rule_version: "r1", policy_label: "AI-scored", ...over,
  };
}
const prevRow = (over: Partial<CallToneRow> = {}) =>
  row({ call_slug: "q1-fy27", fiscal_label: "Q1 FY27", period_end: "2026-06-30", n: 21, mean: 0.2, sd: 0.8, prev_call_slug: null, prev_fiscal_label: null, delta: null, se_delta: null, change: null, ...over });

function toneOutput(content: Record<string, unknown>): AiOutputRow {
  return { id: OUT, task: "call_tone", document_id: 501, event_slug: "q2-fy27", scope: "", body: null, output_label: null, content, display_label: "AI-scored", model: "claude-haiku-5-5", created_at: "2026-11-10T09:00:00+00:00" };
}
const SCORES = [
  { p: 1, score: 2, section: "opening", speaker: "A", words: 80 },
  { p: 2, score: 0, section: "qa", speaker: "B", words: 60 },
  { p: 3, score: -1, section: "qa", speaker: "C", words: 55 },
];
const EVIDENCE = [
  { p: 1, quote: "Stored evidence quote one.", span: [100, 126], citation_no: 1 },
  { p: 3, quote: "Stored evidence quote two.", span: [300, 326], citation_no: 2 },
];
const CITES = [
  citation(1, { ai_output_id: OUT, quote: "Verified quote one from the view.", claim_path: "/evidence/0" }),
  citation(2, { ai_output_id: OUT, quote: "Verified quote two from the view.", claim_path: "/evidence/1" }),
];
const PAGES = [pageRow(1, 7, 7, { ai_output_id: OUT }), pageRow(2, 9, 10, { ai_output_id: OUT, page_url: "https://example.test/tcs-q2.pdf#page=9" })];

function tone(over: Partial<Extract<ToneRead, { state: "ok" }>> = {}): ToneRead {
  return { state: "ok", rows: [row(), prevRow()], output: toneOutput({ scores: SCORES, evidence: EVIDENCE, fallback_citation: false }), citations: CITES, pages: PAGES, ...over };
}
const tab = (t: ToneRead, over: Partial<CallsTabData> = {}) => calls({ policies: OPEN, tone: t, ...over });
const panelOf = (out: string) => out.slice(out.indexOf("panel-call-tone"), out.indexOf("panel-call-remarks"));

describe("the Tone panel", () => {
  it("is held back while the field is hidden: the note, no figure and no quote", () => {
    const out = html(calls());
    expect(out).toContain("panel-call-tone");
    const t = text(panelOf(out));
    expect(t).toContain("Tone held back until it passes review");
    expect(t).not.toContain("on a −2 to +2 scale");
    expect(t).not.toContain("quote");
  });

  it("shows the tone with its scale and passage count, the change in words with both figures, and the two small stats", () => {
    const t = text(panelOf(html(tab(tone()))));
    expect(t).toContain("+0.6");
    expect(t).toContain("on a −2 to +2 scale");
    expect(t).toContain("18 passages");
    expect(t).toContain("More positive than Q1 FY27 (+0.2, 21 passages)");
    expect(t).toContain("Change in the mean +0.40, standard error 0.15");
    expect(t).toContain("Opening remarks");
    expect(t).toContain("+1.0");
    expect(t).toContain("6 passages");
    expect(t).toContain("Answers");
    expect(t).toContain("+0.4");
    expect(t).toContain("12 passages");
    expect(t).toContain("AI-scored");
  });

  it.each([
    ["more_negative", "More negative than Q1 FY27 (+0.2, 21 passages)"],
    ["little_changed", "Little changed from Q1 FY27 (+0.2, 21 passages)"],
  ] as const)("words the %s change", (change, expected) => {
    expect(text(panelOf(html(tab(tone({ rows: [row({ change }), prevRow()] })))))).toContain(expected);
  });

  it("has no change line when there is no previous call or either call is held", () => {
    const t = text(panelOf(html(tab(tone({ rows: [row({ change: null, prev_call_slug: null, prev_fiscal_label: null, delta: null, se_delta: null })] })))));
    expect(t).not.toContain("More positive than");
    expect(t).not.toContain("Little changed from");
    expect(t).not.toContain("Change in the mean");
    expect(changeLine(row({ change: null }), [row()])).toBeNull();
    expect(changeDetail(row({ delta: null }))).toBeNull();
  });

  it("shows the evidence from content.evidence as verified quotes with their page links, scored above or below neutral", () => {
    const out = panelOf(html(tab(tone())));
    const t = text(out);
    expect(t).toContain("Passage scored +2 in the opening remarks");
    expect(t).toContain("Passage scored −1 in an answer");
    expect(t).toContain("Verified quote one from the view.");
    expect(t).toContain("Verified quote two from the view.");
    expect(t).not.toContain("Stored evidence quote");
    expect(t).toContain("p. 7 ↗");
    expect(t).toContain("pp. 9–10 ↗");
    expect(out).toContain('href="https://example.test/tcs-q2.pdf#page=7"');
  });

  it("shows at most three evidence quotes and skips a quote of a passage scored 0 or without a verified citation", () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ p: 1, quote: `q${i}`, span: [0, 2], citation_no: i + 1 }));
    const cites = Array.from({ length: 5 }, (_, i) => citation(i + 1, { ai_output_id: OUT, quote: `Quote ${i + 1}.` }));
    const e = toneEvidence(toneOutput({ scores: SCORES, evidence: many, fallback_citation: false }), cites, []);
    expect(e.items).toHaveLength(3);
    const zero = toneEvidence(toneOutput({ scores: SCORES, evidence: [{ p: 2, quote: "x", span: [0, 1], citation_no: 1 }], fallback_citation: false }), CITES, []);
    expect(zero.items).toEqual([]);
    const unverified = toneEvidence(toneOutput({ scores: SCORES, evidence: [{ p: 1, quote: "x", span: [0, 1], citation_no: 9 }], fallback_citation: false }), CITES, []);
    expect(unverified.items).toEqual([]);
  });

  it("shows the 'No passage…' note for an output that fell back to a citation, and never that citation", () => {
    const fallback = toneOutput({ scores: SCORES.map((s) => ({ ...s, score: 0 })), evidence: [], fallback_citation: true });
    const long = citation(1, { ai_output_id: OUT, claim_path: "/scores/0", quote: "x".repeat(400) });
    const out = panelOf(html(tab(tone({ output: fallback, citations: [long], pages: [] }))));
    expect(text(out)).toContain("No passage was scored above or below neutral");
    expect(out).not.toContain("xxxxxxxxxx");
    expect(toneEvidence(fallback, [long], []).fallback).toBe(true);
  });

  it("says a held call's reason, with no stat", () => {
    const held = row({ mean: null, n: 5, held_reason: "too little management text to score", change: null, delta: null, se_delta: null });
    const t = text(panelOf(html(tab(tone({ rows: [held] })))));
    expect(t).toContain("Too little management text to score (5 passages)");
    expect(t).not.toContain("on a −2 to +2 scale 5");
    expect(heldText(held)).toBe("Too little management text to score (5 passages)");
    expect(toneValue(held)).toBeNull();
  });

  it("says a role-held call's tone isn't scored, and a call with no row says it isn't scored yet", () => {
    const roleHeld = call({ roles_partial: true, unknown_role_share: 0.4 });
    expect(text(panelOf(html(tab(tone({ rows: [] }), { selected: roleHeld, calls: [roleHeld] }))))).toContain("Speaker roles couldn't be read, so tone isn't scored");
    expect(text(panelOf(html(tab(tone({ rows: [] })))))).toContain("Tone not scored yet");
  });

  it("holds back only this panel when its read failed", () => {
    const out = html(tab({ state: "error" }));
    expect(text(panelOf(out))).toContain("Couldn't load this panel. Try again.");
    expect(out).toContain("panel-call-summary");
  });

  it("lists the history across the calls as a table when there are fewer than two points", () => {
    const t = text(panelOf(html(tab(tone()))));
    expect(t).toContain("Tone of each call on a −2 to +2 scale, by quarter end");
    const h = toneHistory([row(), prevRow()]);
    expect(h.points).toEqual([{ time: "2026-06-30", value: 0.2 }, { time: "2026-09-30", value: 0.6 }]);
    expect(h.table.map((r) => [r.quarter, r.mean, r.n, r.change])).toEqual([["Q2 FY27", "+0.6", "18", "More positive"], ["Q1 FY27", "+0.2", "21", ""]]);
    const single = toneHistory([row()]);
    expect(single.points).toHaveLength(1);
    expect(text(panelOf(html(tab(tone({ rows: [row()] })))))).toContain("Passages");
  });

  it("carries the explainer: the rubric, the rules and the check, never 'hand-labelled'", () => {
    const t = text(panelOf(html(tab(tone()))));
    expect(t).toContain("How tone is scored");
    expect(t).toContain("Strongly negative");
    expect(t).toContain("Strongly positive");
    expect(t).toContain("fewer than 8 passages");
    expect(t).toContain("more than twice its standard error");
    expect(t).toContain("Checked against 30 passages labelled by a separate AI model (Claude Fable): 25 of 30 agreed (see Accuracy).");
    expect(t).not.toMatch(/hand-labelled/i);
    expect(RUBRIC.map((r) => r.score)).toEqual(["−2", "−1", "0", "+1", "+2"]);
  });

  it("carries no colour, no Delta triangle and none of the banned words", () => {
    const out = panelOf(html(tab(tone())));
    expect(out).not.toMatch(/text-(gain|loss)|bg-(gain|loss)|#[0-9a-f]{3,6}\b|data-delta|aria-label="(up|down)/i);
    expect(out).not.toMatch(/<svg[^>]*polygon/i);
    expect(bannedCallWordsIn(text(out.replace(/<blockquote[\s\S]*?<\/blockquote>/g, " ")))).toEqual([]);
  });
});

describe("the tone rules", () => {
  it("opens only for a shown or labelled field, and reads the call's own row", () => {
    expect(toneOpen(SHOWN)).toBe(false);
    expect(toneOpen([{ field_key: "ai.call_tone", status: "hidden", label: null }])).toBe(false);
    expect(toneOpen(OPEN)).toBe(true);
    expect(toneState(false, "ok", call(), [row()]).kind).toBe("hidden");
    expect(toneState(true, "error", call(), []).kind).toBe("error");
    expect(toneState(true, "ok", call(), [row()]).kind).toBe("scored");
    expect(toneState(true, "ok", call(), [row({ held_reason: "x", mean: null })]).kind).toBe("held");
  });

  it("formats through lib/format.ts: a sign always, one decimal, a true minus", () => {
    expect(toneValue(row({ mean: -1.25 }))).toBe("−1.3");
    expect(toneValue(row({ mean: 0 }))).toBe("0.0");
    expect(partValue(null, 3)).toBeNull();
    expect(partValue(0.5, 0)).toBeNull();
    expect(partValue(-0.5, 1)).toEqual({ value: "−0.5", context: "1 passage" });
  });
});
