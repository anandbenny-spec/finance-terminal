// web/lib/policy/timeline-rows.ts: the language timeline's rows (#509; Design 01 §1.16, Pass 2). One row is one key
// phrase of a decision document, found in each meeting by a rule: the pair's topic (py/config/topics.yaml fills it
// when the diff is stored) plus words the sentence must hold (and, for some rows, words it must not). A meeting where
// the rule finds no sentence, or more than one, shows a gap, never a guess (lib/policy/timeline.ts).
//
// Written from reading the stored statements and resolutions across their eras (the saved pages in data/fixtures,
// diffed with the repo's own differ). Each rule cites one sentence per era as the document words it; a sentence is
// quoted only up to where the rule has what it needs. The tests (timeline-rows.test.ts) run these sentences through
// the rules, and a guard there keeps every pattern free of the g and y flags, which would make `test()` remember
// where it stopped.
//
// A rule's tiers are tried in order and the first tier that finds a sentence decides: a later tier is a fallback for
// documents that word the phrase elsewhere, and is never used to break a tie in an earlier one.
import type { RowDef } from "@/lib/policy/timeline";
import type { Bank } from "@/lib/queries/policy";

// ------------------------------------------------------------------------------------------------ RBI

/**
 * The stance. Every resolution since 2019 states it in the "Decision and stance" paragraphs, in the words of its day:
 *   2019-02  "The MPC also decided to change the monetary policy stance from calibrated tightening to neutral."
 *   2020-05  "...continue with the accommodative stance as long as it is necessary..."
 *   2022-05  "...remain accommodative while focusing on withdrawal of accommodation..."
 *   2026-10  "The MPC also decided to change the stance to calibrated tightening."
 * Tier 2 is for the resolution whose stance sits in the rationale paragraph:
 *   2025-04  "Moreover, it also decided to change the stance from neutral to accommodative."
 */
const RBI_STANCE: RowDef = {
  key: "stance",
  label: "Stance",
  tiers: [
    { topic: "Decision and stance", all: [/stance|accommodat|neutral|tightening/i] },
    { topic: "Rationale", all: [/\b(decided|voted)\b[^.]*\bstance\b/i] },
  ],
};

/**
 * The rate decision, always the sentence that names the policy repo rate:
 *   2019-02  "reduce the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points from 6.5 per
 *            cent to 6.25 per cent with immediate effect."
 *   2020-05  "reduce the policy repo rate under the liquidity adjustment facility (LAF) by 40 bps to 4.0 per cent from
 *            4.40 per cent with immediate effect;"
 *   2022-05  "Increase the policy repo rate under the liquidity adjustment facility (LAF) by 40 basis points to 4.40 per
 *            cent with immediate effect."
 *   2024-08  "Keep the policy repo rate under the liquidity adjustment facility (LAF) unchanged at 6.50 per cent."
 *   2025-04  "2. After assessing the current and evolving macroeconomic situation, the MPC unanimously voted to reduce
 *            the policy repo rate by 25 basis points to 6.00 per cent with immediate effect."
 *   2026-10  "2. After a detailed assessment ..., the MPC voted unanimously to increase the policy repo rate under the
 *            LAF by 25 bps to 5.50 per cent."
 */
const RBI_DECISION: RowDef = {
  key: "decision",
  label: "Rate decision",
  tiers: [{ topic: "Decision and stance", all: [/policy repo rate/i] }],
};

/**
 * How inflation is described: the numbered paragraph that reports the latest CPI print ("headline", "retail" or "CPI"
 * inflation, with a figure in per cent). The "(?:[^.]|\.\d)*" runs read over decimal points ("5.1"), which a plain
 * "[^.]*" would stop at. Sentences about the outlook ("projected", "expected", "likely", "forecast") and "inflation
 * excluding ..." are not this row. (A print sentence that mentions core inflation in passing, as 2023-12's does, is.)
 *   2019-02  "12. Retail inflation, measured by y-o-y change in the CPI, declined from 3.4 per cent in October 2018 to
 *            2.2 per cent in December, ..."
 *   2020-05  "5. Retail inflation, measured by the consumer price index, moderated for the second consecutive month in
 *            March 2020 to 5.8 per cent after peaking in January."
 *   2022-05  "5. In March 2022, headline CPI inflation surged to 7.0 per cent from 6.1 per cent in February, ..."
 *   2023-12  "5. CPI headline inflation fell by about 2 percentage points since the last meeting of the MPC to 4.9 per cent
 *            in October 2023 on sharp correction in prices of certain vegetables, ..."
 *   2024-08  "5. Headline inflation increased to 5.1 per cent in June 2024 after remaining steady at 4.8 per cent during
 *            April-May 2024."
 *   2026-10  "8. CPI inflation increased to 4.8 per cent in August 2026 from 4.5 per cent in July."
 */
const RBI_INFLATION: RowDef = {
  key: "inflation",
  label: "How inflation is described",
  tiers: [
    {
      topic: "Inflation",
      all: [
        /^\d+\.\s+(?:[^.]|\.\d)*\b(?:(?:headline|retail)\s+(?:cpi\s+)?inflation|cpi\s+(?:headline\s+)?inflation)\b(?!\s+excluding)(?:[^.]|\.\d)*\bper cent\b/i,
      ],
      none: [/project|expected|likely|forecast|anticipate/i],
    },
  ],
};

// ------------------------------------------------------------------------------------------------ Fed

/**
 * The decision and its guidance: the one sentence that says the Committee "decided" and names the target range.
 *   2019-03  "In support of these goals, the Committee decided to maintain the target range for the federal funds rate
 *            at 2-1/4 to 2-1/2 percent."
 *   2020-03  "In light of these developments, the Committee decided to lower the target range for the federal funds
 *            rate to 0 to 1/4 percent."
 *   2022-03  "In support of these goals, the Committee decided to raise the target range for the federal funds rate to
 *            1/4 to 1/2 percent and anticipates that ongoing increases in the target range will be appropriate."
 *   2024-09  "In light of the progress on inflation and the balance of risks, the Committee decided to lower the target
 *            range for the federal funds rate by 1/2 percentage point to 4-3/4 to 5 percent."
 *   2025-12  "In support of its goals and in light of the shift in the balance of risks, the Committee decided to lower
 *            the target range for the federal funds rate by 1/4 percentage point to 3-1/2 to 3-3/4 percent."
 */
const FED_DECISION: RowDef = {
  key: "decision",
  label: "Decision and guidance",
  tiers: [{ topic: "Decision and guidance", all: [/\bdecided\b/i, /target range/i] }],
};

/**
 * How inflation is described: the statement's sentence about inflation itself. Sentences that only mention inflation
 * on the way to something else (expectations, compensation, pressure, "weigh on") are not it. Nor is the separate
 * sentence the first half of 2024 added about progress ("there has been ... progress toward" the goal, or "a lack of"
 * it): that statement's description of inflation is the sentence before it. (From September 2024 one sentence does both,
 * "Inflation has made progress toward the Committee's 2 percent objective but remains somewhat elevated.", and is the row.)
 * Nor is the sentence about the policy stance that 28 Oct 2026's statement added under "Inflation" (#530). That statement came
 * after the saved pages, and under that topic it holds two sentences that fit the rule:
 *   2026-10  "Inflation remains elevated."   (the row)
 *   2026-10  "The accumulation of more information will help the Committee determine the policy stance appropriate to ensure a
 *            timely return of inflation to the Committee's 2 percent goal."   (about the stance, so left out)
 * Without the exclusion those two make a gap on the Fed panel's newest column.
 *   2019-03  "On a 12-month basis, overall inflation has declined..."
 *   2020-04  "Weaker demand and significantly lower oil prices are holding down consumer price inflation."
 *   2022-03  "Inflation remains elevated, reflecting supply and demand imbalances..."
 *   2024-07  "Inflation has eased over the past year but remains somewhat elevated."
 *   2024-09  "Inflation has made further progress toward the Committee's 2 percent objective but remains somewhat elevated."
 *   2025-01  "Inflation remains somewhat elevated."
 */
const FED_INFLATION: RowDef = {
  key: "inflation",
  label: "How inflation is described",
  tiers: [
    {
      topic: "Inflation",
      all: [/inflation/i],
      none: [
        /inflation (compensation|expectations)|pressure on inflation|weigh on|(there has been|lack of)[^.]*progress toward/i,
        /\bpolicy stance\b|\bstance of monetary policy\b/i,
      ],
    },
  ],
};

/** Each bank's rows, in the order the panel lists them. */
export const TIMELINE_ROWS: Record<Bank, readonly RowDef[]> = {
  RBI: [RBI_STANCE, RBI_DECISION, RBI_INFLATION],
  Fed: [FED_DECISION, FED_INFLATION],
};
