// web/lib/feeds.ts: which refresh tasks feed each live part of the site (Design 01 §1.8; Spec 01 §4).
//
// A panel's freshness is computed from the tasks that feed it, never typed. The status bar's summary counts only
// the tasks behind something live, so a task that isn't built yet (disabled in tasks.yaml, as policy.reactions was
// until Pass 1b) never shows as "stale" to visitors (Prompt 01, A2). Each page job adds its panels here as they go
// live (C3: /policy, C4: the meeting pages, C5: /documents); the keys are ops.task's (py/config/tasks.yaml).

export type TaskKey =
  | "policy.calendar"
  | "policy.market_series"
  | "policy.fed_documents"
  | "policy.rbi_documents"
  | "policy.ai_batch"
  | "policy.reactions"
  | "equity.universe"
  | "equity.valuation_inputs"
  | "equity.prices"
  | "equity.statements"
  | "equity.metrics"
  | "equity.beta"
  | "equity.valuation"
  | "equity.snapshot"
  | "equity.rate_reactions"
  | "policy.rate_cycles"
  | "equity.call_transcripts"
  | "equity.call_text"
  | "equity.calls_ai"
  | "equity.segment_changes"
  | "site.quality_summary";

/** Live surfaces and their feeding tasks. */
export const FEEDS = {
  // Shell (C2; Spec 01 §4.1)
  tickerTape: ["policy.market_series"],
  statusBarCountdown: ["policy.calendar"],
  // /policy (C3; Spec 01 §4.2, the S rows). The stance is AI-extracted (policy.ai_batch); summaries too.
  policyRatesStand: ["policy.calendar", "policy.fed_documents", "policy.rbi_documents", "policy.ai_batch"],
  policyRatesChart: ["policy.market_series", "policy.rbi_documents"],
  // Pass 1b (#71): the US and India 10-year yields, from the stored series.
  policyLongRates: ["policy.market_series"],
  policyLatestFed: ["policy.fed_documents", "policy.ai_batch"],
  policyLatestRbi: ["policy.rbi_documents", "policy.ai_batch"],
  policyHistory: ["policy.fed_documents", "policy.rbi_documents", "policy.ai_batch"],
  // Pass 1b (#89): the change strip counts words in the stored statements and their diffs (document engine, no AI).
  policyChanges: ["policy.fed_documents", "policy.rbi_documents"],
  // Pass 1b (#70): the decision-day moves, computed by policy.reactions from the stored series.
  policyReactions: ["policy.reactions"],
  // The meeting pages (C4; Spec 01 §4.3): each bank's documents and their AI outputs.
  meetingFed: ["policy.fed_documents", "policy.ai_batch"],
  meetingRbi: ["policy.rbi_documents", "policy.ai_batch"],
  // /accuracy (ACC-3, #135): the site's own run and check records, projected daily by site.quality_summary.
  accuracy: ["site.quality_summary"],
  // Module 2, release 2A (2A-26, #132; moved in from PREVIEW_FEEDS once production's runs left a freshness row for each task).
  // /screener (2A-18): the latest weekly snapshot, built from the metrics, prices, statements and beta.
  screener: ["equity.universe", "equity.prices", "equity.statements", "equity.metrics", "equity.beta"],
  // The screener's model-implied parts (#355): the priced-in view and any implied column come from the stored valuation runs,
  // which equity.snapshot copies into the weekly snapshot. A failed valuation run leaves the snapshot on an older run, so both count.
  screenerImplied: ["equity.universe", "equity.prices", "equity.statements", "equity.metrics", "equity.beta", "equity.valuation", "equity.snapshot"],
  // /companies/[slug] (2A-19): prices, filings, derived metrics and betas; the header and chips come from the universe.
  company: ["equity.universe", "equity.prices", "equity.statements", "equity.metrics", "equity.beta"],
  // The Valuation tab (2A-20): the stored runs and grid, built from the same filings, prices and betas plus the rates inputs.
  valuation: ["equity.valuation_inputs", "equity.valuation", "equity.prices", "equity.statements", "equity.metrics", "equity.beta"],
  // The Rate decisions tab and the sector view (2A-25): decision-day moves and rate cycles, from the stored reactions.
  rateSensitivity: ["equity.rate_reactions", "policy.rate_cycles"],
} as const satisfies Record<string, readonly TaskKey[]>;

export type Surface = keyof typeof FEEDS;

/**
 * The live tasks that run in the daily workflow (`workflows: {daily: …}` in py/config/tasks.yaml). The status bar's
 * "checked" time is the oldest last success among these only (Anand, 3 Nov 2026, #263), so a weekly or longer task
 * (`policy.calendar` runs every 25 days) can't hold the bar at an old date while the daily data is current. "Delayed"
 * and "stale" still count every live task, so an overdue weekly task still shows. lib/feeds.test.ts checks this list
 * against tasks.yaml, so a live task that becomes daily has to be added here.
 */
export const DAILY_TASKS = [
  "policy.market_series",
  "policy.fed_documents",
  "policy.rbi_documents",
  "policy.ai_batch",
  "policy.reactions",
  "policy.rate_cycles",
  "site.quality_summary",
] as const satisfies readonly TaskKey[];

/** Every task that feeds something live, each once, in a stable order. */
export function liveTasks(feeds: Record<string, readonly TaskKey[]> = FEEDS): TaskKey[] {
  return [...new Set(Object.values(feeds).flat())].sort();
}

/**
 * Surfaces still being built stay here, out of FEEDS, so the status bar doesn't count a task that has no freshness row
 * yet (a missing row counts as stale). A job that ships a surface moves it into FEEDS in the pull request that links
 * it. Empty from 2A-26 (#132) until 2B-19 (#182) added the Earnings calls tab's `calls` surface: it moves into FEEDS in 2B-22
 * (#185), once production's runs have left a freshness row for each of its tasks.
 */
export const PREVIEW_FEEDS = {
  // The company page's Earnings calls tab (2B-19): transcripts found, their text and turns, and the AI summary and figures.
  calls: ["equity.call_transcripts", "equity.call_text", "equity.calls_ai"],
  // The Financials tab's "Segment changes" panel (DD-02, #410), hidden behind equity.segment_changes until DD-03's judge passes it
  // and Anand flips it; it moves into FEEDS once production's run of equity.segment_changes has left a freshness row.
  segmentChanges: ["equity.segment_changes"],
} as const satisfies Record<string, readonly TaskKey[]>;

export type PreviewSurface = keyof typeof PREVIEW_FEEDS;
