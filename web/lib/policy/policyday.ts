// Policy Day's rules (#90; Spec 01 §4.4; Design 01 §5.4): the page is hidden until Anand flips `site.policy_day` in
// core.field_policy, and it shows one bank's latest decision at a time. Pure: the field-policy rows and the two banks'
// latest decision dates in, the answer out.
import type { FieldPolicyRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

/** The field policy that shows the page. No row means hidden: the database fails closed, and so does the page. */
export const POLICY_DAY_FIELD = "site.policy_day";

/** Whether Policy Day is published: a row for the field with status `shown` or `labelled`. */
export function policyDayShown(policies: readonly FieldPolicyRow[] | null): boolean {
  const row = policies?.find((p) => p.field_key === POLICY_DAY_FIELD) ?? null;
  return row !== null && (row.status === "shown" || row.status === "labelled");
}

export function bankParam(value: string | string[] | undefined): Bank | null {
  const v = Array.isArray(value) ? value[0] : value;
  return v === "fed" ? "Fed" : v === "rbi" ? "RBI" : null;
}

/** The bank shown: the one asked for in the URL, else the bank whose latest decision is newer (the Fed on a tie). */
export function pickBank(asked: Bank | null, fedDate: string | null, rbiDate: string | null): Bank {
  if (asked) return asked;
  if (rbiDate && (!fedDate || rbiDate > fedDate)) return "RBI";
  return "Fed";
}

export const policyDayHref = (bank: Bank) => `/workspace/policy-day?bank=${bank === "Fed" ? "fed" : "rbi"}`;
