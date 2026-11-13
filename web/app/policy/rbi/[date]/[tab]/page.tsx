// /policy/rbi/[date]/[tab]: one tab of an MPC meeting (R05-9a, 12 Nov 2026): changes, statement, minutes, press, reaction.
// The tab is a path segment, so the page is built ahead and cached like Policy Day. `overview` is the bare date's page, and any
// other segment answers 404, as does a missing meeting (components/policy/MeetingRoute.tsx).
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { renderMeeting } from "@/components/policy/MeetingRoute";
import { formatDate } from "@/lib/format";
import { TABS, tabSegment } from "@/lib/policy/decisions";
import { slugFor } from "@/lib/policy/meeting";

type Props = PageProps<"/policy/rbi/[date]/[tab]">;

// One sample for the build: the 2018 baseline's What changed tab. Every other meeting and tab renders on its first request, then is cached.
export function generateStaticParams(): { date: string; tab: string }[] {
  return [{ date: "2018-12-05", tab: "changes" }];
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { date, tab } = await params;
  const t = tabSegment(tab);
  if (!t || !slugFor("RBI", date)) return { title: "Not found · Finance Terminal" };
  return { title: `${TABS.find((x) => x.id === t)?.label}: MPC meeting, ${formatDate(date)} · Finance Terminal` };
}

export default async function Page({ params }: Props) {
  const { date, tab } = await params;
  const t = tabSegment(tab);
  if (!t) notFound();
  return renderMeeting("RBI", date, t);
}
