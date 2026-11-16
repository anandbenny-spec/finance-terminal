// /workspace/policy-day/fed and /workspace/policy-day/rbi: Policy Day for one bank (#90; 12 Nov 2026). The bank is a
// path segment so both pages are prerendered (generateStaticParams names the two) and refreshed in the background; the
// page beside this one, /workspace/policy-day, picks the bank whose decision is newer. Any other segment answers 404,
// and so does either page while `site.policy_day` isn't published (the same gate, read from the same cached overview).
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PolicyDay } from "@/components/policy/PolicyDay";
import { POLICY_DAY_BANKS, bankParam, policyDayShown } from "@/lib/policy/policyday";
import { readPolicyOverview } from "@/lib/queries/overview";

export const metadata: Metadata = {
  title: "Policy Day · Finance Terminal",
  description: "The latest Fed or RBI decision on one page: what it changed and how markets moved. Informational only, not investment advice.",
  robots: { index: false, follow: false },
};

export function generateStaticParams(): { bank: string }[] {
  return POLICY_DAY_BANKS.map((bank) => ({ bank }));
}

export default async function Page({ params }: PageProps<"/workspace/policy-day/[bank]">) {
  const bank = bankParam((await params).bank);
  if (!bank) notFound();
  const overview = await readPolicyOverview();
  if (!policyDayShown(overview.fieldPolicy.ok ? overview.fieldPolicy.rows : null)) notFound();
  return <PolicyDay bank={bank} overview={overview} />;
}
