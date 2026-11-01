// Test rows shaped like the rate-sensitivity views (test data, not real figures), shared by the 2A-25 tests.
import type { CompanyRateCycleRow, FieldPolicyRow, RateCycleRow, RateDecisionMoveRow, RateDecisionStatRow } from "@/lib/queries/api";
import type { RateTabData } from "@/lib/queries/rate";

export const SHOWN: FieldPolicyRow[] = [{ field_key: "equity.rate_sensitivity", status: "shown", label: null }];
export const HIDDEN: FieldPolicyRow[] = [{ field_key: "equity.rate_sensitivity", status: "hidden", label: null }];

export function stat(over: Partial<RateDecisionStatRow> = {}): RateDecisionStatRow {
  return {
    subject_kind: "company", subject_key: "tcs", subject_name: "TCS", bank: "rbi", decision: "hold", window_key: "d1", n: 30,
    mean_excess_pct: 0.11, median_excess_pct: 0.07, p25_excess_pct: -0.4, p75_excess_pct: 0.6, share_positive: 0.567, attribution: "Source: NSE", ...over,
  };
}

/** A group with too few decisions: the view leaves the statistics null. */
export function fewStat(over: Partial<RateDecisionStatRow> = {}): RateDecisionStatRow {
  return stat({ bank: "fed", decision: "hike", n: 3, mean_excess_pct: null, median_excess_pct: null, p25_excess_pct: null, p75_excess_pct: null, share_positive: null, ...over });
}

export function move(over: Partial<RateDecisionMoveRow> = {}): RateDecisionMoveRow {
  return {
    subject_kind: "company", subject_key: "tcs", subject_name: "TCS", currency: "INR", bank: "fed", event_slug: "fomc-2022-06-15", meeting_date: "2022-06-15",
    decision: "hike", policy_change_bps: 75, window_key: "d1", pre_date: "2022-06-15", post_date: "2022-06-16", own_change_pct: -1.25, nifty_change_pct: -0.75,
    excess_pct: -0.5, members: null, attribution: "Source: NSE", ...over,
  };
}

export function cycleMove(over: Partial<CompanyRateCycleRow> = {}): CompanyRateCycleRow {
  return {
    company_slug: "tcs", company_name: "TCS", currency: "INR", bank: "rbi", cycle_slug: "rbi-easing-2025-02", direction: "easing", status: "ongoing", is_ongoing: true,
    pre_date: "2025-02-06", post_date: "2026-10-30", own_change_pct: 12.5, nifty_change_pct: 9.5, excess_pct: 3, attribution: "Source: NSE", ...over,
  };
}

export function cycle(over: Partial<RateCycleRow> = {}): RateCycleRow {
  return {
    bank: "rbi", bank_name: "Reserve Bank of India", cycle_slug: "rbi-easing-2025-02", direction: "easing", first_decision_on: "2025-02-07", last_decision_on: "2025-12-05",
    status: "ongoing", moves: 4, total_change_bps: -125, rule_version: 1, ...over,
  };
}

export function rates(over: Partial<RateTabData> = {}): RateTabData {
  return {
    stats: [stat(), stat({ decision: "cut", n: 12, median_excess_pct: -0.3, mean_excess_pct: -0.2, share_positive: 0.4167 }), fewStat(), stat({ window_key: "d5", median_excess_pct: 0.91 })],
    moves: [move(), move({ event_slug: "fomc-2023-07-26", meeting_date: "2023-07-26", own_change_pct: 0.5, nifty_change_pct: 0.25, excess_pct: 0.25 }), move({ window_key: "d5", own_change_pct: 2.5, excess_pct: 1 })],
    cycles: [cycleMove()], cycleInfo: [cycle()], policies: SHOWN, niftyAttribution: "Source: Yahoo Finance (^NSEI)", ...over,
  };
}
