// The body shared by a meeting's routes (/policy/{fed,rbi}/[date], …/[date]/[tab], and the RBI's …/statement/governor; R05-9a,
// 12 Nov 2026). The tab (and the Governor view) come from the route's params only, never the query string, so every tab is
// built ahead and cached like Policy Day; every read is a cached one (lib/queries/meeting.ts).
//
// A missing meeting answers 404 (#21, item 4) from here, before anything renders: once a response streams its status is fixed
// at 200 (Next 16 docs, streaming guide, "Status codes"). A read that can't tell (null) renders the page's error state.
import { notFound } from "next/navigation";
import { MeetingView } from "@/components/policy/MeetingView";
import type { TabId } from "@/lib/policy/decisions";
import { slugFor } from "@/lib/policy/meeting";
import { readMeetingExists } from "@/lib/queries/meeting";

type Bank = "Fed" | "RBI";

export async function renderMeeting(bank: Bank, date: string, tab: TabId, doc?: "governor") {
  const slug = slugFor(bank, date);
  if (!slug || (await readMeetingExists(bank, slug)) === false) notFound();
  return <MeetingView bank={bank} slug={slug} date={date} tab={tab} doc={doc} />;
}
