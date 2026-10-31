// PendingVote (#159; Spec 01 §4.5, "votes not yet assignable"): the vote line while a meeting's votes wait for its
// minutes. A Fed tally statement (from Jun 2026) names only its dissenters, so the line shows them first, as the tally
// line writes them ("Dissent: Lisa D. Cook (preferred +25 bps), …"), then the minutes copy; with no dissenters (the
// RBI, or a unanimous Fed statement) it is the copy alone. No count: "10–2" isn't a number the database returned.
// Shared by the front page, the /policy card and VotesPanel (the meeting page), so they never drift apart.
import { StateNote } from "@/components/data/StateNote";
import { dissentLine, type Tally } from "@/lib/policy/decisions";

export function PendingVote({ t }: { t: Extract<Tally, { kind: "pending" }> }) {
  if (!t.dissents?.length) return <StateNote kind="unavailable">{t.note}</StateNote>;
  return (
    <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-data">
      <span className="text-ink-1">{dissentLine(t.dissents)}</span>
      <span aria-hidden className="text-ink-3">
        ·
      </span>
      <StateNote kind="unavailable">{t.note}</StateNote>
    </span>
  );
}
