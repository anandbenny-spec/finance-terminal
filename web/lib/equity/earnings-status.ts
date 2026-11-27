import type { EarningsListRow } from "@/lib/queries/api";

export const SUMMARY_HELD = "AI summary held back until it passes review";
export const SUMMARY_NOT_YET = "Summary not generated yet";
export const WEBSITE = "Transcript on the company's website";
export const SCANNED = "Transcript is a scanned image; not analysed";
export const NO_TRANSCRIPT = "No transcript found";
export const SUMMARY_AND_FIGURES = "Summary and figures";

/** A list row's status in words (Spec 02B §13.7), plus " · roles partly read" on a role-held default call. */
export function listStatusText(r: Pick<EarningsListRow, "status" | "summary_state" | "roles_partial">): string {
  let text: string;
  if (r.status === "none_found") return NO_TRANSCRIPT;
  if (r.status === "on_company_website") text = WEBSITE;
  else if (r.status === "unreadable") text = SCANNED;
  else if (r.status !== "stored") text = "Transcript not analysed";
  else if (r.summary_state === "available") text = SUMMARY_AND_FIGURES;
  else if (r.summary_state === "held_back") text = SUMMARY_HELD;
  else text = SUMMARY_NOT_YET;
  return r.roles_partial ? text + " · roles partly read" : text;
}
