// /policy/fed/[date]: the Overview of an FOMC meeting (C4; Spec 01 §4.3). The date is the decision date; every other tab is its own
// path (/policy/fed/2026-10-28/changes; R05-9a, 12 Nov 2026), so no meeting page reads the query string and each is cached like Policy Day.
// Old ?tab= links are redirected in next.config.ts. components/policy/MeetingRoute.tsx holds the body and the 404 for a missing meeting.
//
// generateStaticParams names one sample, the 2018 baseline (Spec 01 §2), a meeting that never changes: no more build-time
// reads of production. Every other date renders on its first request, then is cached.
import type { Metadata } from "next";
import { renderMeeting } from "@/components/policy/MeetingRoute";
import { formatDate } from "@/lib/format";
import { slugFor } from "@/lib/policy/meeting";

type Props = PageProps<"/policy/fed/[date]">;

export function generateStaticParams(): { date: string }[] {
  return [{ date: "2018-12-19" }];
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { date } = await params;
  if (!slugFor("Fed", date)) return { title: "Not found · Finance Terminal" };
  return { title: `FOMC meeting, ${formatDate(date)} · Finance Terminal` };
}

export default async function Page({ params }: Props) {
  const { date } = await params;
  return renderMeeting("Fed", date, "overview");
}
