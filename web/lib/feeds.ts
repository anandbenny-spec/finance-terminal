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
  | "policy.reactions";

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
} as const satisfies Record<string, readonly TaskKey[]>;

export type Surface = keyof typeof FEEDS;

/** Every task that feeds something live, each once, in a stable order. */
export function liveTasks(feeds: Record<string, readonly TaskKey[]> = FEEDS): TaskKey[] {
  return [...new Set(Object.values(feeds).flat())].sort();
}
