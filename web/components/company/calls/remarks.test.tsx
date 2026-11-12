// The Earnings calls tab's Opening remarks panel and the calls table's "Opening's similarity" column (2B-19d, #388; Spec 02B §5.13, §8):
// held back while equity.remarks_change is hidden (no figure, no sentence), the share unchanged, the word counts, the new-speaker note,
// at most five pairs with each side at most 400 characters, the states for no comparison and a failed read, no colour, and every
// figure through lib/format.ts. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MAX_SIDE, newSpeakerNote, remarksKeyChanges, remarksSource, similarityText, wordsChangedText } from "@/lib/equity/remarks";
import type { RemarksChangeRow } from "@/lib/queries/api";
import type { CallsTabData } from "@/lib/queries/calls";
import { bannedCallWordsIn } from "@/tests/banned-words";
import { SHOWN, call, calls } from "@/tests/call-rows";
import { page } from "@/tests/company-rows";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => undefined }) }));

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (c: CallsTabData | null) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "calls", overview: null, calls: c })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

const RELEASED = [...SHOWN, { field_key: "equity.remarks_change", status: "labelled" as const, label: "derived" }];
// A stored pair side after the scoring task's verification (remarks-3, #414): a quote of the stored transcript by code-point range.
const vside = (text: string, over: Record<string, unknown> = {}) => ({ text, document_id: 501, start: 100, end: 100 + text.length, page: 7, page_last: 7, verified: true, ...over });
const pair = (i: number, over: Record<string, unknown> = {}) => ({
  kind: "changed",
  changed_words: 10 - i,
  old: vside(`Old sentence number ${i}.`, { document_id: 500 }),
  new: vside(`New sentence number ${i}.`),
  ...over,
});
const SOURCES = { now: remarksSource(call(), "Q2 FY27"), before: remarksSource(call({ call_slug: "q1-fy27", fiscal_label: "Q1 FY27", transcript_document_id: 500, official_url: "https://example.test/tcs-q1.pdf" }), "Q1 FY27") };
function row(over: Partial<RemarksChangeRow> = {}): RemarksChangeRow {
  return {
    company_slug: "tcs", call_slug: "q2-fy27", fiscal_label: "Q2 FY27", period_end: "2026-09-30", filed_at: "2026-10-12T09:00:00+05:30", exchange: "NSE",
    base_call_slug: "q1-fy27", base_fiscal_label: "Q1 FY27", base_period_end: "2026-06-30", words_base: 200, words_new: 210, words_added: 40,
    words_removed: 30, similarity: 0.8293, new_speakers: [], label: null, pairs: [pair(1), pair(2)], policy_label: "derived", ...over,
  };
}
const tab = (over: Partial<CallsTabData> = {}) =>
  calls({ policies: RELEASED, remarks: [row()], calls: [call(), call({ call_slug: "q1-fy27", fiscal_label: "Q1 FY27", transcript_document_id: 500, official_url: "https://example.test/tcs-q1.pdf", is_newest: false, is_latest_stored: false, is_default: false })], ...over });

describe("the Opening remarks panel", () => {
  it("is held back while the field is hidden: the note, no figure and no sentence", () => {
    const out = html(calls({ remarks: [] }));
    expect(out).toContain("panel-call-remarks");
    expect(text(out)).toContain("Opening-remarks changes held back until they pass review");
    expect(out).not.toContain("Wording unchanged");
    expect(out).not.toContain("Opening&#x27;s similarity");
  });

  it("shows the share unchanged, the word counts and the sentence pairs once released", () => {
    const t = text(html(tab()));
    expect(t).toContain("Wording unchanged");
    expect(t).toContain("82.9%");
    expect(t).toContain("40 words added, 30 removed");
    expect(t).toContain("against the Q1 FY27 opening remarks");
    expect(t).toContain("Old sentence number 1.");
    expect(t).toContain("New sentence number 2.");
    expect(t).toContain("derived");
  });

  it("shows at most five pairs, each side cut to 400 characters by code point", () => {
    const long = "é".repeat(450);
    const many = Array.from({ length: 7 }, (_, i) => pair(i, i === 0 ? { new: vside(long, { end: 100 + 400 }) } : {}));
    const model = remarksKeyChanges(many, SOURCES);
    expect(model.changedSentences).toBe(5);
    const side = model.groups[0].rows[0].now?.ops[0].text ?? "";
    expect(Array.from(side).length).toBe(MAX_SIDE);
    expect(side.endsWith("…")).toBe(true);
  });

  it("reads an added and a removed sentence as such", () => {
    const m = remarksKeyChanges([pair(1, { old: null }), pair(2, { new: null }), { kind: "same" }, "nonsense", null], SOURCES);
    expect(m.groups[0].rows.map((r) => r.kind)).toEqual(["added", "removed"]);
    expect(remarksKeyChanges("not an array", SOURCES).groups).toEqual([]);
  });

  it("names a new speaker, and says nothing when there is none", () => {
    expect(newSpeakerNote({ new_speakers: ["A. Speaker", "B. Other"] })).toBe("New speaker in the opening remarks: A. Speaker, B. Other");
    expect(newSpeakerNote({ new_speakers: [] })).toBeNull();
    expect(text(html(tab({ remarks: [row({ new_speakers: ["A. Speaker"] })] })))).toContain("New speaker in the opening remarks: A. Speaker");
    expect(text(html(tab()))).not.toContain("New speaker");
  });

  it("says so when the call has no previous call to compare with", () => {
    expect(text(html(tab({ remarks: [] })))).toContain("No comparison with an earlier call's opening remarks is published for this call.");
  });

  it("says so when its own read failed, and the rest of the tab renders", () => {
    const out = html(tab({ remarks: null }));
    expect(text(out)).toContain("Couldn't load this panel. Try again.");
    expect(out).toContain("panel-call-summary");
  });

  it("adds the Opening's similarity column to the calls table only once released", () => {
    expect(text(html(tab()))).toContain("Opening's similarity");
    expect(text(html(calls()))).not.toContain("Opening's similarity");
  });

  it("formats through lib/format.ts, carries no colour and uses none of the banned words", () => {
    expect(similarityText({ similarity: 1 })).toBe("100.0%");
    expect(similarityText({ similarity: 0.05 })).toBe("5.0%");
    expect(wordsChangedText({ words_added: 1, words_removed: 1200 })).toBe("1 word added, 1,200 removed");
    const out = html(tab());
    const panel = out.slice(out.indexOf("panel-call-remarks"), out.indexOf("panel-call-guidance"));
    expect(panel).not.toMatch(/text-(gain|loss)|bg-(gain|loss)|#[0-9a-f]{3,6}\b/i);
    expect(bannedCallWordsIn(text(panel))).toEqual([]);
  });
});

describe("verified quotes (#414)", () => {
  it("shows each verified side as a quote with its citation chip and a page link at the source", () => {
    const out = html(tab());
    const panel = out.slice(out.indexOf("panel-call-remarks"), out.indexOf("panel-call-guidance"));
    expect(text(panel)).toContain("p. 7 ↗");
    expect(panel).toContain('href="https://example.test/tcs-q2.pdf#page=7"');
    expect(panel).toContain('href="https://example.test/tcs-q1.pdf#page=7"');
    expect(panel).toContain("Source 1: show the cited passage");
    expect(panel).toContain("Source 4: show the cited passage");
  });

  it("shows nothing for a side that is not verified, or that sits outside the limits", () => {
    const m = remarksKeyChanges(
      [
        pair(1, { old: { text: "Older, unverified words.", spans: [[0, 10]] } }),       // stored before #414: text and spans only
        pair(2, { old: vside("Not verified.", { verified: false }), new: vside("Over the limit.", { end: 100 + 401 }) }),
        pair(3, { old: vside("No document.", { document_id: null }), new: vside("Negative start.", { start: -1 }) }),
      ],
      SOURCES,
    );
    expect(m.groups).toHaveLength(1);
    expect(m.groups[0].rows).toHaveLength(1);
    expect(m.groups[0].rows[0].before).toBeNull();
    expect(m.groups[0].rows[0].kind).toBe("added");
    expect(text(html(tab({ remarks: [row({ pairs: [pair(1, { old: { text: "Leaked free text.", spans: [[0, 5]] }, new: { text: "Leaked free text too.", spans: [[0, 5]] } })] })] })))).not.toContain("Leaked free text");
  });

  it("links a page only for the call's own transcript, and not where the address is a company-website page", () => {
    const other = remarksKeyChanges([pair(1, { new: vside("From another document.", { document_id: 999 }) })], SOURCES);
    expect(other.groups[0].rows[0].now?.cite?.page).toBeNull();
    const site = remarksSource(call({ status: "on_company_website", official_url: "https://example.test/ir" }), "Q2 FY27");
    expect(site.url).toBeNull();
    expect(remarksKeyChanges([pair(1)], { now: site, before: SOURCES.before }).groups[0].rows[0].now?.cite?.page).toBeNull();
    const insecure = remarksSource(call({ official_url: "http://example.test/t.pdf" }), "Q2 FY27");
    expect(insecure.url).toBeNull();
  });

  it("keeps no transcript text beyond the quotes: every quote is at most 400 characters", () => {
    const rows = remarksKeyChanges(Array.from({ length: 5 }, (_, i) => pair(i)), SOURCES).groups[0].rows;
    for (const r of rows) for (const s of [r.before, r.now]) expect(Array.from(s?.ops[0].text ?? "").length).toBeLessThanOrEqual(MAX_SIDE);
  });
});

describe("the company's ChangeStrip row in the Opening remarks panel (2B-19f, #395)", () => {
  const two = [
    row(),
    row({ call_slug: "q1-fy27", fiscal_label: "Q1 FY27", period_end: "2026-06-30", base_call_slug: "q4-fy26", base_fiscal_label: "Q4 FY26", words_base: 100, words_new: 200, words_added: 20, words_removed: 10 }),
  ];
  const panelOf = (out: string) => out.slice(out.indexOf("panel-call-remarks"), out.indexOf("panel-call-guidance"));

  it("is held back with the panel: no strip markup, no bar, no count", () => {
    const out = panelOf(html(calls({ remarks: two })));
    expect(out).not.toContain("Opening remarks, call by call");
    expect(out).not.toContain("role=\"group\"");
    expect(out).not.toContain("share of words, derived");
  });

  it("reads the newest bar from the view's counts, naming the call and what it was compared with", () => {
    const out = panelOf(html(tab({ remarks: two })));
    const t = text(out);
    expect(t).toContain("Opening remarks, call by call");
    expect(t).toContain("share of words, derived");
    expect(t).toContain("Q2 FY27, against Q1 FY27");
    expect(t).toContain("+19.0% added (40 of 210 words)");
    expect(t).toContain("−15.0% removed (30 of 200 words)");
    expect(t).toContain("Open this call");
    expect(out).toContain('href="/companies/tcs/calls/q2-fy27"');
    expect(out).toContain('aria-label="View as table"');
  });

  it("keeps module 1's words out of the company's row", () => {
    const out = panelOf(html(tab({ remarks: two })));
    const strip = out.slice(out.indexOf("Opening remarks, call by call") - 400);
    expect(text(strip)).not.toMatch(/\b(Fed|RBI|FOMC|MPC|meeting|statement)s?\b/i);
    expect(out).not.toMatch(/text-(gain|loss)|bg-(gain|loss)|#[0-9a-f]{3,6}\b|text-\[|w-\[\d+px\]/i);
  });

  it("shows its one bar for a company with one comparison", () => {
    const t = text(html(tab()));
    expect(t).toContain("Opening remarks, call by call");
    expect(t).toContain("Q2 FY27, against Q1 FY27");
    expect(t).not.toContain("no bar");
  });

  it("leaves out and counts a row whose counts don't add up", () => {
    const bad = row({ call_slug: "q1-fy27", fiscal_label: "Q1 FY27", period_end: "2026-06-30", words_base: 100, words_new: 50, words_added: 90 });
    const t = text(html(tab({ remarks: [row(), bad] })));
    expect(t).toContain("1 of 2 calls has no bar");
    expect(t).not.toContain("90 of 50");
  });
});
