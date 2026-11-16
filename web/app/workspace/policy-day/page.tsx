// /workspace/policy-day: Policy Day, the first workspace preset (#90; Spec 01 §4.4; Design 01 §5.4): the latest
// decision of one bank, what it changed, how markets moved, and the countdown. This address shows the bank whose
// latest decision is newer; /workspace/policy-day/fed and /rbi (the [bank] page beside this one) show one bank each,
// and the Fed / RBI switch links to them.
//
// Prerendered (12 Nov 2026): nothing here reads the request, and every read is cached (`'use cache'`), so the page is
// built ahead and refreshed in the background like /policy. It used to read `?bank=`, which made every visit wait for
// a server function to build the body (lib/policy/policyday.ts has the story); old `?bank=` links are redirected by
// next.config.ts.
//
// HIDDEN until Anand flips it (tracker #30, 8 Nov 2026): the page answers 404 unless `api.field_policy` has a row
// `site.policy_day` with status `shown` or `labelled` (fails closed, like every gated field), and it is never indexed.
// The check reads the same cached overview /policy uses, so a flip reaches the page with the next refresh.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PolicyDay } from "@/components/policy/PolicyDay";
import { pickBank, policyDayShown } from "@/lib/policy/policyday";
import { readPolicyOverview } from "@/lib/queries/overview";

export const metadata: Metadata = {
  title: "Policy Day · Finance Terminal",
  description: "The latest Fed or RBI decision on one page: what it changed and how markets moved. Informational only, not investment advice.",
  robots: { index: false, follow: false },
};

export default async function Page() {
  const overview = await readPolicyOverview();
  if (!policyDayShown(overview.fieldPolicy.ok ? overview.fieldPolicy.rows : null)) notFound();
  const bank = pickBank(
    null,
    overview.fed.ok ? (overview.fed.meeting?.decision_date ?? null) : null,
    overview.rbi.ok ? (overview.rbi.meeting?.decision_date ?? null) : null,
  );
  return <PolicyDay bank={bank} overview={overview} />;
}
