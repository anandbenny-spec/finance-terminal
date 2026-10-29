// RateStats (Spec 01 §4.2 "Where rates stand"; Design 01 §4.2 StatRow): the two policy rates, each with its last
// change (and, for the RBI, its gated stance), and the next two meetings with their countdowns. Shared by /policy
// and the front page (#152, the second use), so both read the same rows the same way. Every figure is a row an `api`
// view returned; a failed or empty read says so in the stat's own StateNote.
import { Countdown } from "@/components/data/Countdown";
import { Gated } from "@/components/data/Gated";
import { Stat, StatRow } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import { NOT_AVAILABLE, formatAxisNumber, formatAxisRange, formatDate, formatReleaseTime } from "@/lib/format";
import { changeInWords, lastMove, stanceText } from "@/lib/policy/decisions";
import { ZONE } from "@/lib/policy/meeting";
import type { NextMeetingRow, PolicyMeetingRow } from "@/lib/queries/api";
import type { LatestDecision } from "@/lib/queries/overview";
import type { Bank } from "@/lib/queries/policy";

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

export function RateStat({ bank, latest, decisions, policies }: { bank: Bank; latest: LatestDecision | null; decisions: PolicyMeetingRow[] | null; policies: Parameters<typeof Gated>[0]["policies"] | null }) {
  const fed = bank === "Fed";
  const label = fed ? "Fed funds target range" : "RBI repo rate";
  const market = fed ? "US" : "IN";
  const m = latest?.meeting ?? null;
  if (latest === null) return <Stat label={label} value={NOT_AVAILABLE} context={<StateNote kind="error">Couldn&apos;t load this rate.</StateNote>} market={market} />;
  if (m === null) return <Stat label={label} value={NOT_AVAILABLE} context={<StateNote kind="unavailable">No decision is published yet.</StateNote>} market={market} />;
  const value = fed ? formatAxisRange(m.fed_target_lower_pct, m.fed_target_upper_pct) : formatAxisNumber(m.policy_rate_pct);
  const moved = decisions ? lastMove(decisions.filter((d) => d.bank === bank)) : null;
  const lastChange = moved && moved.slug !== m.slug ? changeInWords(moved) : null;
  return (
    <Stat
      label={label}
      value={value}
      unit={value === NOT_AVAILABLE ? undefined : "%"}
      context={changeInWords(m)}
      asOf={
        <>
          {lastChange && <div>Last change: {lowerFirst(lastChange)}</div>}
          {!fed && m.stance && policies && (
            <div>
              Stance:{" "}
              <Gated policies={policies} field="ai.stance_extraction">
                {stanceText(m.stance)}
              </Gated>
            </div>
          )}
        </>
      }
      market={market}
    />
  );
}

export function NextStat({ bank, meetings }: { bank: Bank; meetings: NextMeetingRow[] | null }) {
  const label = bank === "Fed" ? "Next FOMC" : "Next MPC";
  const market = bank === "Fed" ? "US" : "IN";
  if (meetings === null) return <Stat label={label} value={NOT_AVAILABLE} context={<StateNote kind="error">Couldn&apos;t load meeting dates.</StateNote>} market={market} />;
  const next = meetings.filter((n) => n.bank === bank).sort((a, b) => a.decision_date.localeCompare(b.decision_date))[0];
  if (!next) return <Stat label={label} value={NOT_AVAILABLE} context={<StateNote kind="unavailable">The next meeting isn&apos;t scheduled yet.</StateNote>} market={market} />;
  return (
    <Stat
      label={label}
      value={formatDate(next.decision_date)}
      context={next.decision_at ? `Decision ${formatReleaseTime(next.decision_at, ZONE[bank])}` : "Decision time not published yet"}
      asOf={<Countdown decisionDate={next.decision_date} decisionAt={next.decision_at} zone={ZONE[bank]} />}
      market={market}
    />
  );
}

export function RateStats({
  fed,
  rbi,
  decisions,
  policies,
  meetings,
}: {
  fed: LatestDecision | null;
  rbi: LatestDecision | null;
  decisions: PolicyMeetingRow[] | null;
  policies: Parameters<typeof Gated>[0]["policies"] | null;
  meetings: NextMeetingRow[] | null;
}) {
  return (
    <StatRow>
      <RateStat bank="Fed" latest={fed} decisions={decisions} policies={policies} />
      <RateStat bank="RBI" latest={rbi} decisions={decisions} policies={policies} />
      <NextStat bank="Fed" meetings={meetings} />
      <NextStat bank="RBI" meetings={meetings} />
    </StatRow>
  );
}
