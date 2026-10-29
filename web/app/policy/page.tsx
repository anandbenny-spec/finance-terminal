// /policy: the Fed vs RBI overview (C3; Spec 01 §4.2, the S rows; Design 01 §5.2). Where rates stand (StatRow and
// the rates chart), the latest decisions (a PolicyCard per bank: D13) and every decision since 2019. Every figure is
// a row an `api` view returned with the browser key (lib/queries/overview.ts), formatted by lib/format.ts; every
// panel says the question it answers and carries SourceLine and Freshness; no panel is ever blank (StateNote).
// No hawk/dove UI (D9). Pass 1b adds the decision-day moves (#70), long rates (#71) and the change strip (#89).
import type { Metadata } from "next";
import { Freshness } from "@/components/data/Freshness";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Section } from "@/components/data/SectionHeading";
import { ChangeStrip } from "@/components/ai/ChangeStrip";
import { HistoryPanel } from "@/components/policy/HistoryPanel";
import { ErrorPanel, Foot, latestOf } from "@/components/policy/PanelFoot";
import { PolicyCard } from "@/components/policy/PolicyCard";
import { RateStats } from "@/components/policy/RateStats";
import { ReactionsPanel } from "@/components/policy/ReactionsPanel";
import { LongRatesPanel } from "@/components/policy/LongRatesPanel";
import { RatesPanel } from "@/components/policy/RatesPanel";
import { FEEDS, type TaskKey } from "@/lib/feeds";
import { formatDate } from "@/lib/format";
import { buildChangeStrips, stripScale } from "@/lib/policy/changestrip";
import { historyRows } from "@/lib/policy/decisions";
import { pressReleaseLabel } from "@/lib/policy/document";
import { reactionRows, reactionSources } from "@/lib/policy/reactions";
import { POLICY_SECTIONS } from "@/lib/policy/sections";
import { readPolicyOverview } from "@/lib/queries/overview";
import { POLICY_START } from "@/lib/queries/policy";
import { readFreshness, readNextMeetings } from "@/lib/queries/shell";
import { panelFreshness } from "@/lib/shell/freshness";

export const metadata: Metadata = {
  title: "Fed vs RBI policy tracker · Finance Terminal",
  description:
    "Every FOMC and MPC decision since 2019: what each bank decided, what changed in its statement and how its members voted. Informational only, not investment advice.",
};

const RBI_SOURCE = "Source: Reserve Bank of India";

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
  const strips = o.changes.ok && o.history.ok ? buildChangeStrips(o.history.decisions, o.changes.documents, o.changes.diffs) : null;

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

      <Section {...POLICY_SECTIONS.whereRatesStand}>
        <PanelGrid>
          <Panel
            label="Where the two policy rates stand now, and the next meetings"
            variant="flush"
            footer={foot([fedSource, rbiSource], FEEDS.policyRatesStand, latestOf(fed?.meeting?.decision_date, rbi?.meeting?.decision_date))}
          >
            <RateStats fed={fed} rbi={rbi} decisions={decisions} policies={policies} meetings={next.ok ? next.meetings : null} />
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
          {o.longRates.ok ? (
            <LongRatesPanel
              us={o.longRates.us}
              india={o.longRates.india}
              from={POLICY_START}
              refresh={FEEDS.policyLongRates}
              footer={foot(
                o.longRates.series.map((s) => s.attribution),
                FEEDS.policyLongRates,
                latestOf(o.longRates.us.at(-1)?.obs_date, o.longRates.india.at(-1)?.obs_date),
              )}
            />
          ) : (
            <ErrorPanel title="Long rates: US vs India 10-year" id="panel-long-rates" refresh={FEEDS.policyLongRates} />
          )}
        </PanelGrid>
      </Section>

      <Section {...POLICY_SECTIONS.latestDecisions}>
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

      <Section id="statement-changes" title="How much each statement changed">
        <PanelGrid>
          {o.changes.ok && o.history.ok ? (
            <ChangeStrip
              strips={strips!}
              scale={stripScale(strips!)}
              refresh={FEEDS.policyChanges}
              footer={foot(
                // The counts are of the banks' own documents; the shares are ours, derived from the counts.
                [fedSource, rbiSource],
                FEEDS.policyChanges,
                latestOf(...strips!.flatMap((s) => s.bars.map((b) => b.date))),
              )}
            />
          ) : (
            <ErrorPanel title="How much each statement changed" id="panel-change-strip" refresh={FEEDS.policyChanges} />
          )}
        </PanelGrid>
      </Section>

      <Section {...POLICY_SECTIONS.history}>
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
          {o.history.ok && o.reactions.ok ? (
            <ReactionsPanel
              rows={reactionRows(historyRows(o.history.decisions, o.history.votes), o.reactions.rows, o.reactions.series)}
              footer={foot(
                reactionSources(o.reactions.series),
                FEEDS.policyReactions,
                latestOf(...o.reactions.rows.map((r) => r.post_obs_date)),
              )}
              refresh={FEEDS.policyReactions}
            />
          ) : (
            <ErrorPanel title="Decision-day moves" id="panel-reactions" refresh={FEEDS.policyReactions} />
          )}
        </PanelGrid>
      </Section>
    </>
  );
}
