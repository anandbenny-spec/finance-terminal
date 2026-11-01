// VotesPanel (Spec 01 §4.3; Design 01 §4.2): "How members voted" for one meeting: the tally line, then the dot plot
// (or the grid when the votes aren't a rate tally), with "View as table" inside the chart. Shared by the meeting
// page's Overview and the front page (#152, the second use); the meeting page's markup is unchanged. When a bank's
// per-member votes aren't recorded yet (the RBI's come with the minutes) the panel says so in the §4.5 words and
// never swaps in an older meeting.
import type { ReactNode } from "react";
import { VoteDotPlot } from "@/components/ai/VoteDotPlot";
import { VoteGrid } from "@/components/ai/VoteGrid";
import { Panel, type PanelSpan } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { PendingVote } from "@/components/policy/PendingVote";
import { tally } from "@/lib/policy/decisions";
import { meetingTitle, voteRows, votesAbsentNote } from "@/lib/policy/meeting";
import { dotPlot } from "@/lib/policy/votedots";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

const ERROR = "Couldn't load this panel. Try again.";

export function VotesPanel({
  bank,
  m,
  votes,
  footer,
  title = "How members voted",
  titleId = "panel-votes",
  subtitle = "Who voted for what, and who dissented?",
  qualifier,
  span,
  refresh,
}: {
  bank: Bank;
  m: PolicyMeetingRow;
  /** The meeting's votes, or null when the read failed. */
  votes: readonly PolicyVoteRow[] | null;
  footer: ReactNode;
  title?: string;
  titleId?: string;
  subtitle?: ReactNode;
  qualifier?: ReactNode;
  span?: PanelSpan;
  refresh?: readonly string[];
}) {
  const fed = bank === "Fed";
  const t = votes ? tally(m, votes) : null;
  const rows = voteRows(m, votes ?? []);
  const plot = votes && t?.kind === "tally" ? dotPlot(m, votes) : null;
  return (
    <Panel title={title} titleId={titleId} subtitle={subtitle} qualifier={qualifier} market={fed ? "US" : "IN"} variant="flush" span={span} footer={footer} refresh={refresh}>
      {t === null ? (
        <div className="p-3"><StateNote kind="error">{ERROR}</StateNote></div>
      ) : t.kind === "pending" ? (
        <div className="p-3"><PendingVote t={t} /></div>
      ) : t.kind === "absent" ? (
        <div className="p-3"><StateNote kind="unavailable">{votesAbsentNote(m)}</StateNote></div>
      ) : (
        <>
          <p className="px-3 py-2.5 text-data text-ink-1">{t.line}</p>
          {!fed && t.line.includes("Stance") && (
            // The stance tally's label (B6 part 1's decision, #21): how members the resolution doesn't name are counted.
            <p className="px-3 pb-2.5 text-meta text-ink-3">
              Stance tally from the resolution: members it doesn&apos;t name as preferring another stance are counted with the decision.
            </p>
          )}
          {plot ? (
            <VoteDotPlot plot={plot} bank={fed ? "Fed" : "RBI"} rows={rows} stance={!fed} caption={`Votes at the ${meetingTitle(m)}`} />
          ) : (
            <VoteGrid rows={rows} stance={!fed} caption={`Votes at the ${meetingTitle(m)}`} />
          )}
        </>
      )}
    </Panel>
  );
}
