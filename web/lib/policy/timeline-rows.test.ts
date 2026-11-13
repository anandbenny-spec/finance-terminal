// The language timeline's row rules (#509) on sentences from each era of both banks, as the documents word them: one
// match, no match (a gap) and two matches (a gap). The sentences are the ones the rules' comments cite.
import { describe, expect, it } from "vitest";
import type { RedlinePair } from "@/lib/policy/redline";
import { cellFor, sentenceText, type RowDef } from "@/lib/policy/timeline";
import { TIMELINE_ROWS } from "@/lib/policy/timeline-rows";

const same = (topic: string, text: string): RedlinePair => ({ kind: "same", text, topic, newPara: 1, oldPara: 1 });
const row = (bank: "Fed" | "RBI", key: string): RowDef => TIMELINE_ROWS[bank].find((r) => r.key === key)!;
const FILLER = same("Decision and stance", "The Committee also reviewed the outlook for the economy.");

/** Sentence by era: each must be the row's one match when it is the only sentence of its topic that fits. */
function eras(bank: "Fed" | "RBI", key: string, topic: string, sentences: [era: string, text: string][]) {
  describe(`${bank} ${key}`, () => {
    it.each(sentences)("%s: finds its one sentence", (_era, text) => {
      const cell = cellFor(row(bank, key), [FILLER, same(topic, text), same("Global", "Global growth is uneven.")]);
      expect(cell.kind).toBe("sentence");
      if (cell.kind === "sentence") expect(sentenceText(cell)).toBe(text);
    });
    it.each(sentences)("%s: a second matching sentence makes a gap, not a pick", (_era, text) => {
      const cell = cellFor(row(bank, key), [same(topic, text), same(topic, text)]);
      expect(cell).toEqual({ kind: "gap", reason: "several", matched: 2 });
    });
    it.each(sentences)("%s: the same words under another topic aren't the row", (_era, text) => {
      expect(cellFor(row(bank, key), [same("Global", text)])).toEqual({ kind: "gap", reason: "none", matched: 0 });
    });
  });
}

eras("RBI", "stance", "Decision and stance", [
  ["2019-02", "The MPC also decided to change the monetary policy stance from calibrated tightening to neutral."],
  ["2020-05", "The MPC also decided to continue with the accommodative stance as long as it is necessary to revive growth."],
  ["2022-05", "The MPC also decided to remain accommodative while focusing on withdrawal of accommodation to ensure that inflation remains within the target."],
  ["2026-10", "The MPC also decided to change the stance to calibrated tightening."],
]);

eras("RBI", "decision", "Decision and stance", [
  ["2019-02", "reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points from 6.5 per cent to 6.25 per cent with immediate effect."],
  ["2020-05", "reduce the policy repo rate under the liquidity adjustment facility (LAF) by 40 bps to 4.0 per cent from 4.40 per cent with immediate effect;"],
  ["2022-05", "Increase the policy repo rate under the liquidity adjustment facility (LAF) by 40 basis points to 4.40 per cent with immediate effect."],
  ["2024-08", "Keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.50 per cent."],
  ["2025-04", "2. After assessing the current and evolving macroeconomic situation, the MPC unanimously voted to reduce the policy repo rate by 25 basis points to 6.00 per cent with immediate effect."],
  ["2026-10", "2. After a detailed assessment of the macroeconomic situation, the MPC voted unanimously to increase the policy repo rate under the LAF by 25 bps to 5.50 per cent."],
]);

eras("RBI", "inflation", "Inflation", [
  ["2019-02", "12. Retail inflation, measured by y-o-y change in the CPI, declined from 3.4 per cent in October 2018 to 2.2 per cent in December, the lowest print in the last eighteen months."],
  ["2020-05", "5. Retail inflation, measured by the consumer price index, moderated for the second consecutive month in March 2020 to 5.8 per cent after peaking in January."],
  ["2022-05", "5. In March 2022, headline CPI inflation surged to 7.0 per cent from 6.1 per cent in February, largely reflecting the impact of geopolitical spillovers."],
  ["2023-12", "5. CPI headline inflation fell by about 2 percentage points since the last meeting of the MPC to 4.9 per cent in October 2023 on sharp correction in prices of certain vegetables, deflation in fuel and a broad-based moderation in core inflation."],
  ["2024-08", "5. Headline inflation increased to 5.1 per cent in June 2024 after remaining steady at 4.8 per cent during April-May 2024."],
  ["2026-10", "8. CPI inflation increased to 4.8 per cent in August 2026 from 4.5 per cent in July."],
]);

eras("Fed", "decision", "Decision and guidance", [
  ["2019-03", "In support of these goals, the Committee decided to maintain the target range for the federal funds rate at 2-1/4 to 2-1/2 percent."],
  ["2020-03", "In light of these developments, the Committee decided to lower the target range for the federal funds rate to 0 to 1/4 percent."],
  ["2022-03", "In support of these goals, the Committee decided to raise the target range for the federal funds rate to 1/4 to 1/2 percent and anticipates that ongoing increases in the target range will be appropriate."],
  ["2024-09", "In light of the progress on inflation and the balance of risks, the Committee decided to lower the target range for the federal funds rate by 1/2 percentage point to 4-3/4 to 5 percent."],
  ["2025-12", "In support of its goals and in light of the shift in the balance of risks, the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-1/2 to 3-3/4 percent."],
]);

eras("Fed", "inflation", "Inflation", [
  ["2019-03", "On a 12-month basis, overall inflation has declined and is running below 2 percent."],
  ["2020-04", "Weaker demand and significantly lower oil prices are holding down consumer price inflation."],
  ["2022-03", "Inflation remains elevated, reflecting supply and demand imbalances related to the pandemic."],
  ["2024-07", "Inflation has eased over the past year but remains somewhat elevated."],
  ["2024-09", "Inflation has made further progress toward the Committee's 2 percent objective but remains somewhat elevated."],
  ["2025-01", "Inflation remains somewhat elevated."],
]);

describe("what each row leaves out", () => {
  it("RBI stance: a sentence with no stance word is no match, and a rationale sentence is only the fallback", () => {
    const stance = row("RBI", "stance");
    expect(cellFor(stance, [same("Decision and stance", "The MPC voted to keep the policy repo rate unchanged.")])).toEqual({ kind: "gap", reason: "none", matched: 0 });
    const rationale = same("Rationale", "Moreover, it also decided to change the stance from neutral to accommodative.");
    expect(cellFor(stance, [rationale])).toMatchObject({ kind: "sentence" });
    // Tier 1 decides when it finds a sentence: the fallback never breaks its tie or adds to it.
    const first = same("Decision and stance", "The MPC also decided to remain accommodative.");
    const out = cellFor(stance, [first, rationale]);
    expect(out.kind === "sentence" && sentenceText(out)).toBe("The MPC also decided to remain accommodative.");
    expect(cellFor(stance, [first, first, rationale])).toEqual({ kind: "gap", reason: "several", matched: 2 });
  });

  it("RBI inflation: the outlook, core inflation on its own, 'excluding' and an unnumbered sentence aren't the print", () => {
    const inflation = row("RBI", "inflation");
    for (const text of [
      "6. Headline inflation is projected at 4.5 per cent in 2026-27.",
      "7. Core inflation (CPI excluding food and fuel) remained at 4.2 per cent.",
      "9. CPI inflation excluding vegetables eased to 3.9 per cent in June.",
      "Headline inflation rose to 5.1 per cent in June.",
      "5. Headline inflation is likely to rise to 5.1 per cent in July.",
    ]) {
      expect(cellFor(inflation, [same("Inflation", text)]), text).toEqual({ kind: "gap", reason: "none", matched: 0 });
    }
  });

  it("Fed decision: 'decided' without the target range, or the range without 'decided', is no match", () => {
    const decision = row("Fed", "decision");
    expect(cellFor(decision, [same("Decision and guidance", "The Committee decided to continue its asset purchases.")]).kind).toBe("gap");
    expect(cellFor(decision, [same("Decision and guidance", "The Committee seeks to keep the target range where it is.")]).kind).toBe("gap");
  });

  it("Fed inflation: sentences that only mention inflation on the way to something else are left out", () => {
    const inflation = row("Fed", "inflation");
    for (const text of [
      "Longer-term inflation expectations remain well anchored.",
      "Measures of inflation compensation have moved lower.",
      "Lower energy prices will weigh on inflation.",
      "In recent months, there has been some further progress toward the Committee's 2 percent inflation objective.",
      "In recent months, there has been a lack of further progress toward the Committee's 2 percent inflation objective.",
    ]) {
      expect(cellFor(inflation, [same("Inflation", text)]), text).toEqual({ kind: "gap", reason: "none", matched: 0 });
    }
  });

  it("Fed inflation: in the first half of 2024 the description is the sentence before the progress sentence", () => {
    const out = cellFor(row("Fed", "inflation"), [
      same("Inflation", "Inflation has eased over the past year but remains somewhat elevated."),
      same("Inflation", "In recent months, there has been some further progress toward the Committee's 2 percent inflation objective."),
    ]);
    expect(out.kind === "sentence" && sentenceText(out)).toBe("Inflation has eased over the past year but remains somewhat elevated.");
  });
});

describe("the rules themselves", () => {
  it("never carry the g or y flag, which would make test() remember where the last match stopped", () => {
    for (const rows of Object.values(TIMELINE_ROWS)) {
      for (const r of rows) for (const tier of r.tiers) for (const rx of [...tier.all, ...(tier.none ?? [])]) expect(rx.flags, `${r.key}: ${rx}`).not.toMatch(/[gy]/);
    }
  });

  it("list the RBI's rows first-stance and the Fed's decision first, each key once", () => {
    expect(TIMELINE_ROWS.RBI.map((r) => r.key)).toEqual(["stance", "decision", "inflation"]);
    expect(TIMELINE_ROWS.Fed.map((r) => r.key)).toEqual(["decision", "inflation"]);
  });
});
