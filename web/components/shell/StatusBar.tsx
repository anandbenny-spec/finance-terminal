// StatusBar (Design 01 §4.1; Spec 01 §4.1): 28 px, Plex Mono 12, ink-2. Left: the freshness summary (a dot and
// "All data current", or a clock and "1 source delayed"), from api.task_freshness for the tasks behind live parts of
// the site (lib/feeds.ts). Middle: "Next FOMC {date} · in {n} days" and "Next MPC …" from api.next_meetings, which
// become "FOMC decision today, 14:00 ET (23:30 IST)" on the day (Countdowns). Right: "Not investment advice".
// Fixed at the bottom from md; in the page flow, last, below md.
import { Clock } from "lucide-react";
import { StateNote } from "@/components/data/StateNote";
import { Countdowns } from "@/components/shell/Countdowns";
import { readFreshness, readNextMeetings } from "@/lib/queries/shell";
import { liveTasks } from "@/lib/feeds";
import { summarise, type FreshnessSummary } from "@/lib/shell/freshness";
import { initialLines, toUpcoming, type UpcomingMeeting } from "@/lib/shell/meetings";

function Summary({ summary }: { summary: FreshnessSummary | null }) {
  if (!summary) return <StateNote kind="error">Freshness unavailable</StateNote>;
  return (
    <span className="inline-flex items-center gap-1.5">
      {summary.state === "current" ? (
        <span aria-hidden className="size-2 shrink-0 rounded-full bg-ok" />
      ) : (
        <Clock aria-hidden className="size-3.5 shrink-0 text-warn" />
      )}
      {summary.text}
    </span>
  );
}

/** The bar itself, from already-read data (kept apart from the reads so it can be rendered in tests). */
export function StatusBarView({
  summary,
  meetings,
}: {
  summary: FreshnessSummary | null;
  meetings: UpcomingMeeting[] | null;
}) {
  return (
    <footer className="z-20 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line bg-panel px-3 py-2 font-mono text-meta text-ink-2 md:sticky md:bottom-0 md:h-(--statusbar-h) md:flex-nowrap md:gap-6 md:overflow-x-auto md:py-0 md:whitespace-nowrap">
      <Summary summary={summary} />
      {meetings === null ? (
        <StateNote kind="error">Meeting dates unavailable</StateNote>
      ) : (
        <Countdowns meetings={meetings} initial={initialLines(meetings)} />
      )}
      <span className="text-ink-3 md:ml-auto">Not investment advice</span>
    </footer>
  );
}

export async function StatusBar() {
  const [freshness, next] = await Promise.all([readFreshness(), readNextMeetings()]);
  const summary = freshness.ok ? summarise(freshness.tasks, liveTasks(), freshness.readAt) : null;
  const meetings = next.ok ? toUpcoming(next.meetings) : null;
  return <StatusBarView summary={summary} meetings={meetings} />;
}
