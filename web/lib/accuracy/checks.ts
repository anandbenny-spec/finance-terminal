// web/lib/accuracy/checks.ts: the one-time go-live checks /accuracy records (US-09, #408; Plan 05 §5, "The record").
// A hidden feature goes live when a judge session passes it; the judge is named by model wherever /accuracy or
// /methodology says who checked, and the site never says Anand labelled or reviewed something a model judged.
// Each entry is the record of a check that ran once, copied from its verdict on the issue: not data the site loads, so
// it never changes once written (as /methodology's dated constants and the tone panel's TONE_CHECK). Its numbers and
// its date still go through format.ts.
import { formatCount, formatDate } from "@/lib/format";

export type GoLiveCheck = {
  id: string;
  /** The panel's title: what was checked. */
  title: string;
  /** The question the panel answers (Design 01 §4). */
  question: string;
  /** The day the check ran (YYYY-MM-DD). */
  on: string;
  line: string;
};

// US-08's final verdict (#407, Sat 14 Nov 2026, seed us08-20261114-2): 30 of 30 sampled figures (3 companies × 2
// periods × 5 figures) equal to their 10-K or 10-Q on sec.gov, 2 of 2 valuation runs recomputed to the cent; round 1
// (seed us08-20261114) failed on American Express's revenue, fixed at the cause by #558 before the new draw.
const US = { on: "2026-11-14", judge: "Claude Opus 5.5", figures: 30, sampled: 30, companies: 3, valuations: 2, recomputed: 2 };

export const US_SLICE_CHECK: GoLiveCheck = {
  id: "us-slice",
  title: "US companies (Dow 30)",
  question: "Who checked the US companies' figures before they were published, and what passed?",
  on: US.on,
  line:
    `Checked on ${formatDate(US.on)} by an AI model, ${US.judge}, in a separate session that built none of the US slice: ` +
    `${formatCount(US.figures)} of ${formatCount(US.sampled)} sampled figures from ${formatCount(US.companies)} companies ` +
    `matched their 10-K and 10-Q filings on sec.gov, and ${formatCount(US.valuations)} of ${formatCount(US.recomputed)} ` +
    "valuations recomputed from their stored inputs to the cent. An earlier sample failed on one company's revenue; the " +
    "rule was fixed for every company and a new sample was drawn.",
};

/** The go-live checks shown on /accuracy, newest first. */
export const GO_LIVE_CHECKS: readonly GoLiveCheck[] = [US_SLICE_CHECK];
