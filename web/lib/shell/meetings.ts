// web/lib/shell/meetings.ts: the status bar's countdowns and its decision-day switch (Spec 01 §4.1; Design 01
// §4.1, §10.5). Pure: the rows of api.next_meetings and a clock in, the lines out.
//
// - Days count in each bank's own zone (New York for the FOMC, Kolkata for the MPC), as formatCountdown and the
//   jobs' decision-day rules do (ft/jobs/due.py), so "today" begins at midnight in that zone and lasts its day.
// - A meeting stays "next" through its decision day. Once its decision is stored the meeting is `held` and leaves
//   api.next_meetings; a meeting still listed after its day (the decision not stored yet, or a cached read) is
//   passed over for the bank's next one, never counted down as if it were still ahead.
// - The server sends each bank's next decision date only (it can't know the visitor's clock: Design 01 §3); the
//   countdown and the switch are worked out in the browser after first render.
import type { NextMeetingRow } from "@/lib/queries/api";
import { dateInZone, formatCountdown, formatDate, formatShortDate, type EventZone, type Instant } from "@/lib/format";

export type Bank = "FOMC" | "MPC";

export type UpcomingMeeting = { bank: Bank; zone: EventZone; decisionDate: string; decisionAt: string | null };

export type CountdownLine = { bank: Bank; text: string; today: boolean };

const BANKS: { bank: Bank; prefix: string; zone: EventZone }[] = [
  { bank: "FOMC", prefix: "fomc-", zone: "America/New_York" },
  { bank: "MPC", prefix: "mpc-", zone: "Asia/Kolkata" },
];

/** The view's rows as meetings, by their slug (`fomc-YYYY-MM-DD`, `mpc-YYYY-MM-DD`), soonest first. */
export function toUpcoming(rows: readonly NextMeetingRow[]): UpcomingMeeting[] {
  const meetings: UpcomingMeeting[] = [];
  for (const row of rows) {
    const bank = BANKS.find((b) => row.slug.startsWith(b.prefix));
    if (!bank) continue;
    meetings.push({ bank: bank.bank, zone: bank.zone, decisionDate: row.decision_date, decisionAt: row.decision_at });
  }
  return meetings.sort((a, b) => a.decisionDate.localeCompare(b.decisionDate));
}

/** What the server renders: each bank's next decision date, in full ("Next FOMC 9 Dec 2026"). */
export function initialLines(meetings: readonly UpcomingMeeting[]): CountdownLine[] {
  return BANKS.flatMap(({ bank }): CountdownLine[] => {
    const next = meetings.find((m) => m.bank === bank);
    return next ? [{ bank, text: `Next ${bank} ${formatDate(next.decisionDate)}`, today: false }] : [];
  });
}

/** What the browser shows at `now`: "Next FOMC 9 Dec · in 50 days", or "FOMC decision today, 14:00 ET (23:30 IST)". */
export function countdownLines(meetings: readonly UpcomingMeeting[], now: Instant): CountdownLine[] {
  return BANKS.flatMap(({ bank, zone }): CountdownLine[] => {
    const today = dateInZone(now, zone);
    for (const m of meetings) {
      if (m.bank !== bank || m.decisionDate < today) continue; // its day has passed in the bank's zone
      if (!m.decisionAt) return [{ bank, text: `Next ${bank} ${formatShortDate(m.decisionDate, now, zone)}`, today: false }];
      if (dateInZone(m.decisionAt, zone) !== m.decisionDate) continue; // date and time disagree: not shown as fact
      const countdown = formatCountdown(m.decisionAt, zone, now);
      if (countdown.startsWith("today")) return [{ bank, text: `${bank} decision ${countdown}`, today: true }];
      return [{ bank, text: `Next ${bank} ${formatShortDate(m.decisionDate, now, zone)} · ${countdown}`, today: false }];
    }
    return [];
  });
}
