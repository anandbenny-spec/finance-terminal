"use client";

// A meeting's countdown in a stat (Spec 01 §4.2, "Where rates stand"): "in 46 days", "tomorrow", "today, 14:00 ET
// (23:30 IST)", counted in the bank's own zone (lib/format.ts). Shown only in the browser, after first render: the
// server sends the date alone. A meeting whose day has passed shows nothing (it's stale until its decision is
// stored), and a release time on a different day from the decision date isn't shown as fact.
import { dateInZone, formatCountdown, type EventZone } from "@/lib/format";
import { useMinute } from "@/lib/clock";

export function Countdown({ decisionDate, decisionAt, zone }: { decisionDate: string; decisionAt: string | null; zone: EventZone }) {
  const minute = useMinute();
  if (minute === null || !decisionAt) return null;
  const now = new Date(minute);
  if (decisionDate < dateInZone(now, zone) || dateInZone(decisionAt, zone) !== decisionDate) return null;
  return <span>{formatCountdown(decisionAt, zone, now)}</span>;
}
