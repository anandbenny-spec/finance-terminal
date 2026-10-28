// web/lib/policy/latest.ts: where /policy/fed/latest and /policy/rbi/latest send a visitor (#147; Design 01 §1.11,
// §5.6). Pure: the newest held meeting's date and the requested `?tab=` in, the path out.
import { meetingTabHref, tabOf } from "@/lib/policy/decisions";
import { POLICY_SECTIONS, policySectionHref } from "@/lib/policy/sections";
import type { Bank } from "@/lib/queries/policy";

/** Where a visitor lands when there is no meeting to send them to: the Latest decisions section of /policy, which
 * shows its own empty and error states. */
export const LATEST_FALLBACK = policySectionHref(POLICY_SECTIONS.latestDecisions);

/**
 * The newest held meeting's page on the requested tab. `tab` is kept only when the meeting page accepts it
 * (`tabOf`); a bad or missing one is dropped, which lands on the Overview. With no meeting (none held yet, or the
 * read couldn't tell) the answer is LATEST_FALLBACK, never an error.
 */
export function latestTarget(bank: Bank, decisionDate: string | null, tab: string | null): string {
  if (!decisionDate) return LATEST_FALLBACK;
  return meetingTabHref(bank, decisionDate, tabOf(tab ?? undefined));
}
