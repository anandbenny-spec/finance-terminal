// remarksBars (2B-19f, #395): the Opening remarks strip's bars from the view's own counts. Test rows, not real figures.
import { describe, expect, it } from "vitest";
import { remarksBars } from "@/lib/equity/remarks";
import type { RemarksChangeRow } from "@/lib/queries/api";

const row = (over: Partial<RemarksChangeRow> = {}): RemarksChangeRow => ({
  company_slug: "tcs", call_slug: "q2", fiscal_label: "Q2", period_end: "2026-09-30", filed_at: null, exchange: "NSE", base_call_slug: "q1", base_fiscal_label: "Q1",
  base_period_end: "2026-06-30", words_base: 200, words_new: 400, words_added: 40, words_removed: 30, similarity: 0.8, new_speakers: [], label: null, pairs: [], policy_label: null, ...over,
});
const href = (slug: string) => `/c?call=${slug}`;

describe("remarksBars", () => {
  it("divides the stored counts, oldest first, with the call's own link and labels", () => {
    const out = remarksBars([row(), row({ call_slug: "q1", fiscal_label: "Q1", period_end: "2026-06-30", base_fiscal_label: "Q4" })], href);
    expect(out.bars.map((b) => b.date)).toEqual(["2026-06-30", "2026-09-30"]);
    expect(out.bars[1]).toMatchObject({ addedPct: 10, removedPct: 15, wordsNew: 400, wordsOld: 200, href: "/c?call=q2", label: "Q2, against Q1", callSlug: "q2" });
    expect(out).toMatchObject({ withoutBar: 0, total: 2 });
  });

  it("holds back and counts counts that can't be a share, a missing period end and a repeated date", () => {
    const out = remarksBars(
      [row(), row({ call_slug: "a", period_end: null }), row({ call_slug: "b", period_end: "2026-03-31", words_added: 500 }), row({ call_slug: "z" }), row({ call_slug: "d", period_end: "2026-01-31", words_base: 0 })],
      href,
    );
    expect(out.bars.map((b) => b.callSlug)).toEqual(["q2"]);
    expect(out).toMatchObject({ withoutBar: 4, total: 5 });
  });

  it("gives nothing for no rows", () => {
    expect(remarksBars([], href)).toEqual({ bars: [], withoutBar: 0, total: 0 });
  });
});
