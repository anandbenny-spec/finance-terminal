// /policy/rbi/[date]: an MPC meeting page (C4; Spec 01 §4.3). The date is the decision date; the tab is in the URL
// (?tab=…) so every tab can be linked (Design 01 §4.2). Rendered on request inside Suspense (the params are only
// known then), from cached reads (lib/queries/meeting.ts).
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { StateNote } from "@/components/data/StateNote";
import { MeetingView, tabOf } from "@/components/policy/MeetingView";
import { formatDate } from "@/lib/format";
import { slugFor } from "@/lib/policy/meeting";

type Props = PageProps<"/policy/rbi/[date]">;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { date } = await params;
  if (!slugFor("RBI", date)) return { title: "Not found · Finance Terminal" };
  return { title: `MPC meeting, ${formatDate(date)} · Finance Terminal` };
}

async function Meeting({ params, searchParams }: Props) {
  const [{ date }, query] = await Promise.all([params, searchParams]);
  const slug = slugFor("RBI", date);
  if (!slug) notFound();
  const doc = typeof query.doc === "string" ? query.doc : undefined;
  return <MeetingView bank="RBI" slug={slug} date={date} tab={tabOf(query.tab)} doc={doc} />;
}

export default function Page(props: Props) {
  return (
    <Suspense fallback={<StateNote kind="empty">Loading the meeting…</StateNote>}>
      <Meeting {...props} />
    </Suspense>
  );
}
