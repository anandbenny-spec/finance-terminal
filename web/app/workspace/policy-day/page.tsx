// /workspace/policy-day: Policy Day, the first workspace preset (#90; Spec 01 §4.4; Design 01 §5.4): the latest
// decision of one bank (Fed / RBI switch in ?bank=), what it changed, how markets moved, and the countdown.
//
// HIDDEN until Anand flips it (tracker #30, 8 Nov 2026): the page answers 404 unless `api.field_policy` has a row
// `site.policy_day` with status `shown` or `labelled` (fails closed, like every gated field), and it is never indexed.
// The 404 is decided BEFORE the Suspense boundary, because once a response streams its status is fixed at 200
// (Next 16 docs, streaming guide), from the same cached overview read /policy uses.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { StateNote } from "@/components/data/StateNote";
import { PolicyDay } from "@/components/policy/PolicyDay";
import { bankParam, pickBank, policyDayShown } from "@/lib/policy/policyday";
import { readPolicyOverview } from "@/lib/queries/overview";

export const metadata: Metadata = {
  title: "Policy Day · Finance Terminal",
  description: "The latest Fed or RBI decision on one page: what it changed and how markets moved. Informational only, not investment advice.",
  robots: { index: false, follow: false },
};

async function Body({ searchParams, overview }: { searchParams: PageProps<"/workspace/policy-day">["searchParams"]; overview: Awaited<ReturnType<typeof readPolicyOverview>> }) {
  const query = await searchParams;
  const bank = pickBank(
    bankParam(query.bank),
    overview.fed.ok ? (overview.fed.meeting?.decision_date ?? null) : null,
    overview.rbi.ok ? (overview.rbi.meeting?.decision_date ?? null) : null,
  );
  return <PolicyDay bank={bank} overview={overview} />;
}

export default async function Page({ searchParams }: PageProps<"/workspace/policy-day">) {
  const overview = await readPolicyOverview();
  if (!policyDayShown(overview.fieldPolicy.ok ? overview.fieldPolicy.rows : null)) notFound();
  return (
    <Suspense fallback={<StateNote kind="empty">Loading Policy Day…</StateNote>}>
      <Body searchParams={searchParams} overview={overview} />
    </Suspense>
  );
}
