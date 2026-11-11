// The Earnings calls tab's Opening remarks panel and the calls table's "Opening's similarity" column (2B-19d, #388; Spec 02B §5.13, §8):
// held back while equity.remarks_change is hidden (no figure, no sentence), the share unchanged, the word counts, the new-speaker note,
// at most five pairs with each side at most 400 characters, the states for no comparison and a failed read, no colour, and every
// figure through lib/format.ts. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MAX_SIDE, newSpeakerNote, remarksKeyChanges, similarityText, wordsChangedText } from "@/lib/equity/remarks";
import type { RemarksChangeRow } from "@/lib/queries/api";
import type { CallsTabData } from "@/lib/queries/calls";
import { bannedCallWordsIn } from "@/tests/banned-words";
import { SHOWN, calls } from "@/tests/call-rows";
import { page } from "@/tests/company-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (c: CallsTabData | null) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "calls", overview: null, calls: c })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

const RELEASED = [...SHOWN, { field_key: "equity.remarks_change", status: "labelled" as const, label: "derived" }];
const pair = (i: number, over: Record<string, unknown> = {}) => ({
  kind: "changed",
  changed_words: 10 - i,
  old: { text: `Old sentence number ${i}.`, spans: [[0, 10]] },
  new: { text: `New sentence number ${i}.`, spans: [[0, 10]] },
  ...over,
});
function row(over: Partial<RemarksChangeRow> = {}): RemarksChangeRow {
  return {
    company_slug: "tcs", call_slug: "q2-fy27", fiscal_label: "Q2 FY27", period_end: "2026-09-30", filed_at: "2026-10-12T09:00:00+05:30", exchange: "NSE",
    base_call_slug: "q1-fy27", base_fiscal_label: "Q1 FY27", base_period_end: "2026-06-30", words_base: 200, words_new: 210, words_added: 40,
    words_removed: 30, similarity: 0.8293, new_speakers: [], label: null, pairs: [pair(1), pair(2)], policy_label: "derived", ...over,
  };
}
const tab = (over: Partial<CallsTabData> = {}) => calls({ policies: RELEASED, remarks: [row()], ...over });

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
    const many = Array.from({ length: 7 }, (_, i) => pair(i, i === 0 ? { new: { text: long } } : {}));
    const model = remarksKeyChanges(many);
    expect(model.changedSentences).toBe(5);
    const side = model.groups[0].rows[0].now?.ops[0].text ?? "";
    expect(Array.from(side).length).toBe(MAX_SIDE);
    expect(side.endsWith("…")).toBe(true);
  });

  it("reads an added and a removed sentence as such", () => {
    const m = remarksKeyChanges([pair(1, { old: null }), pair(2, { new: null }), { kind: "same" }, "nonsense", null]);
    expect(m.groups[0].rows.map((r) => r.kind)).toEqual(["added", "removed"]);
    expect(remarksKeyChanges("not an array").groups).toEqual([]);
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
