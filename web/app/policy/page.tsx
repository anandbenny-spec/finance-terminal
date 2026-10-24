// /policy: the Fed vs RBI overview (C3; Spec 01 §4.2, the S rows; Design 01 §5.2). Where rates stand (StatRow and
// the rates chart), the latest decisions (a PolicyCard per bank: D13) and every decision since 2019. Every figure is
// a row an `api` view returned with the browser key (lib/queries/overview.ts), formatted by lib/format.ts; every
// panel says the question it answers and carries SourceLine and Freshness; no panel is ever blank (StateNote).
// No hawk/dove UI (D9). The Pass 1b panels (change strip, long rates, decision-day moves) join later.
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Countdown } from "@/components/data/Countdown";
import { Freshness } from "@/components/data/Freshness";
import { Gated } from "@/components/data/Gated";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Section } from "@/components/data/SectionHeading";
import { SourceLine } from "@/components/data/SourceLine";
import { Stat, StatRow } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import { HistoryPanel } from "@/components/policy/HistoryPanel";
import { PolicyCard } from "@/components/policy/PolicyCard";
import { RatesPanel } from "@/components/policy/RatesPanel";
import { FEEDS, type TaskKey } from "@/lib/feeds";
import { NOT_AVAILABLE, formatAxisNumber, formatAxisRange, formatDate, formatReleaseTime, type EventZone } from "@/lib/format";
import { changeInWords, historyRows, lastMove, stanceText } from "@/lib/policy/decisions";
import { pressReleaseLabel } from "@/lib/policy/document";
import type { NextMeetingRow, PolicyMeetingRow, TaskFreshnessRow } from "@/lib/queries/api";
import { readPolicyOverview, type LatestDecision } from "@/lib/queries/overview";
import { POLICY_START, type Bank } from "@/lib/queries/policy";
import { readFreshness, readNextMeetings } from "@/lib/queries/shell";
import { panelFreshness } from "@/lib/shell/freshness";

export const metadata: Metadata = {
  title: "Fed vs RBI policy tracker · Finance Terminal",
  description:
    "Every FOMC and MPC decision since 2019: what each bank decided, what changed in its statement and how its members voted. Informational only, not investment advice.",
};

const ZONE: Record<Bank, EventZone> = { Fed: "America/New_York", RBI: "Asia/Kolkata" };
const RBI_SOURCE = "Source: Reserve Bank of India";

function latestOf(...dates: (string | null | undefined)[]): string | null {
  return dates.filter((d): d is string => Boolean(d)).sort().at(-1) ?? null;
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function Foot({ sources, tasks, dataTo, freshness, now }: { sources: (string | null | undefined)[]; tasks: readonly TaskKey[]; dataTo: string | null; freshness: TaskFreshnessRow[] | null; now: string }) {
  return (
    <>
      <SourceLine lines={sources} />
      <Freshness freshness={panelFreshness(freshness, tasks, dataTo, now)} />
    </>
  );
}

function ErrorPanel({ title, id, span, market, footer, refresh }: { title: string; id: string; span?: 12 | 6; market?: "US" | "IN"; footer?: ReactNode; refresh?: readonly TaskKey[] }) {
  return (
    <Panel title={title} titleId={id} span={span} market={market} footer={footer} refresh={refresh}>
      <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
    </Panel>
  );
}

function RateStat({ bank, latest, decisions, policies }: { bank: Bank; latest: LatestDecision | null; decisions: PolicyMeetingRow[] | null; policies: Parameters<typeof Gated>[0]["policies"] | null }) {
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

function NextStat({ bank, meetings }: { bank: Bank; meetings: NextMeetingRow[] | null }) {
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

export default async function PolicyPage() {
  const [o, fresh, next] = await Promise.all([readPolicyOverview(), readFreshness(), readNextMeetings()]);
  const now = o.readAt;
  const freshness = fresh.ok ? fresh.tasks : null;
  const policies = o.fieldPolicy.ok ? o.fieldPolicy.rows : null;
  const decisions = o.history.ok ? o.history.decisions : null;
  const fed = o.fed.ok ? o.fed : null;
  const rbi = o.rbi.ok ? o.rbi : null;
  const foot = (sources: (string | null | undefined)[], tasks: readonly TaskKey[], dataTo: string | null) => (
    <Foot sources={sources} tasks={tasks} dataTo={dataTo} freshness={freshness} now={now} />
  );
  const fedSource = fed?.meeting?.attribution ?? "Source: Federal Reserve Board";
  const rbiSource = rbi?.meeting?.attribution ?? RBI_SOURCE;
  const allTasks = [...new Set(Object.entries(FEEDS).filter(([k]) => k.startsWith("policy")).flatMap(([, t]) => t))];
  const pageFresh = panelFreshness(freshness, allTasks, null, now);
  const stanceLabel = policies?.find((p) => p.field_key === "ai.stance_extraction" && p.status === "labelled")?.label ?? null;
  const rbiDoc = rbi?.document;

  return (
    <>
      <PageHeader
        crumbs={["Macro and policy", "Fed vs RBI"]}
        title="Fed vs RBI policy tracker"
        description="Every FOMC and MPC decision since 2019: what each bank decided, what changed in its statement and how its members voted."
        meta={
          <>
            <span>Sources: Federal Reserve · Reserve Bank of India · FRED</span>
            <Freshness freshness={pageFresh} />
          </>
        }
      />

      <Section id="where-rates-stand" title="Where rates stand">
        <PanelGrid>
          <Panel
            label="Where the two policy rates stand now, and the next meetings"
            variant="flush"
            footer={foot([fedSource, rbiSource], FEEDS.policyRatesStand, latestOf(fed?.meeting?.decision_date, rbi?.meeting?.decision_date))}
          >
            <StatRow>
              <RateStat bank="Fed" latest={fed} decisions={decisions} policies={policies} />
              <RateStat bank="RBI" latest={rbi} decisions={decisions} policies={policies} />
              <NextStat bank="Fed" meetings={next.ok ? next.meetings : null} />
              <NextStat bank="RBI" meetings={next.ok ? next.meetings : null} />
            </StatRow>
          </Panel>
          {o.rates.ok ? (
            <RatesPanel
              fed={o.rates.fed}
              rbi={o.rates.rbi}
              decisions={decisions ?? []}
              from={POLICY_START}
              refresh={FEEDS.policyRatesChart}
              footer={foot(
                [...o.rates.series.map((s) => s.attribution), RBI_SOURCE],
                FEEDS.policyRatesChart,
                latestOf(...o.rates.fed.upper.steps.map((s) => s.obs_date), ...o.rates.rbi.map((s) => s.decision_date)),
              )}
            />
          ) : (
            <ErrorPanel title="Policy rates since 2019" id="panel-rates" refresh={FEEDS.policyRatesChart} />
          )}
        </PanelGrid>
      </Section>

      <Section id="latest-decisions" title="Latest decisions">
        <PanelGrid>
          <PolicyCard
            bank="Fed"
            latest={fed}
            policies={policies}
            now={now}
            footer={foot([fedSource], FEEDS.policyLatestFed, fed?.meeting?.decision_date ?? null)}
          />
          <PolicyCard
            bank="RBI"
            latest={rbi}
            policies={policies}
            now={now}
            footer={foot(
              // RBI documents are named, never linked (Anand, 24 Oct 2026): the source line names the document, its date
              // and RBI's press-release number (Brief §6.2).
              [rbiDoc && rbi?.meeting
                ? [rbiSource, rbiDoc.doc_type_label, formatDate(rbi.meeting.decision_date), pressReleaseLabel(rbiDoc)]
                    .filter(Boolean).join(" · ")
                : rbiSource],
              FEEDS.policyLatestRbi,
              rbi?.meeting?.decision_date ?? null,
            )}
          />
        </PanelGrid>
      </Section>

      <Section id="history" title="History">
        <PanelGrid>
          {o.history.ok ? (
            <HistoryPanel
              rows={historyRows(o.history.decisions, o.history.votes)}
              stanceLabel={stanceLabel}
              footer={foot([fedSource, rbiSource], FEEDS.policyHistory, latestOf(...o.history.decisions.map((d) => d.decision_date)))}
              refresh={FEEDS.policyHistory}
            />
          ) : (
            <ErrorPanel title="Every decision since 2019" id="panel-history" refresh={FEEDS.policyHistory} />
          )}
        </PanelGrid>
      </Section>
    </>
  );
}
