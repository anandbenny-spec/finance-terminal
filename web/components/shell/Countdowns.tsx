"use client";

// The status bar's countdowns and decision-day switch (Design 01 §4.1, §10.5). The server sends each bank's next
// decision date; after first render the browser adds the countdown from its own clock, checked a few times a
// minute, so "tomorrow" turns into "FOMC decision today, 14:00 ET (23:30 IST)" at midnight in the bank's zone
// (lib/shell/meetings.ts). No ticking seconds (Design 01 §3).
import { cn } from "cn";
import { useMinute } from "@/lib/clock";
import { countdownLines, type CountdownLine, type UpcomingMeeting } from "@/lib/shell/meetings";

export function Countdowns({ meetings, initial }: { meetings: UpcomingMeeting[]; initial: CountdownLine[] }) {
  const minute = useMinute();
  const lines = minute === null ? initial : countdownLines(meetings, new Date(minute));
  return (
    <>
      {lines.map((line) => (
        <span key={line.bank} className={cn("inline-flex items-center gap-1.5", line.today && "text-ink-1")}>
          {line.today && <span aria-hidden className="size-2 shrink-0 rounded-full bg-amber" />}
          {line.text}
        </span>
      ))}
    </>
  );
}
