// /policy/rbi/[date]: an MPC meeting page (C4; Spec 01 §4.3). The date is the decision date; the tab is in the URL
// (?tab=…) so every tab can be linked (Design 01 §4.2). The tab's content renders inside Suspense (the search params
// are only known on request), from cached reads (lib/queries/meeting.ts).
//
// A missing meeting answers 404 (#21, item 4): the page looks the meeting up BEFORE its Suspense boundary, because
// once the response streams its status is fixed at 200 (Next 16 docs, streaming guide, "Status codes"). Reading the
// date there needs it known when the route prerenders, so generateStaticParams names one sample: the 2018 baseline
// (Spec 01 §2), a meeting that never changes. Every other date renders on its first request, then is cached.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { StateNote } from "@/components/data/StateNote";
import { MeetingView, tabOf } from "@/components/policy/MeetingView";
import { formatDate } from "@/lib/format";
import { slugFor } from "@/lib/policy/meeting";
import { readMeetingExists } from "@/lib/queries/meeting";

type Props = PageProps<"/policy/rbi/[date]">;

export function generateStaticParams(): { date: string }[] {
  return [{ date: "2018-12-05" }];
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { date } = await params;
  if (!slugFor("RBI", date)) return { title: "Not found · Finance Terminal" };
  return { title: `MPC meeting, ${formatDate(date)} · Finance Terminal` };
}

async function Meeting({ slug, date, searchParams }: { slug: string; date: string; searchParams: Props["searchParams"] }) {
  const query = await searchParams;
  const doc = typeof query.doc === "string" ? query.doc : undefined;
  return <MeetingView bank="RBI" slug={slug} date={date} tab={tabOf(query.tab)} doc={doc} />;
}

export default async function Page({ params, searchParams }: Props) {
  const { date } = await params;
  const slug = slugFor("RBI", date);
  if (!slug || (await readMeetingExists("RBI", slug)) === false) notFound();
  return (
    <Suspense fallback={<StateNote kind="empty">Loading the meeting…</StateNote>}>
      <Meeting slug={slug} date={date} searchParams={searchParams} />
    </Suspense>
  );
}
