// Policy Day (#90; Spec 01 §4.4; Design 01 §5.4): the most recent decision of one bank on a page, with a Fed / RBI
// switch in the URL (?bank=). The decision card, the countdown to the bank's next meeting, what the decision changed
// and how markets moved around it. Every panel is a component and a read the meeting pages already use; nothing here
// computes a figure. Informational only, like the rest of the site.
import { Freshness } from "@/components/data/Freshness";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Tabs } from "@/components/data/Tabs";
import { PolicyDayMeeting } from "@/components/policy/MeetingView";
import { ErrorPanel, Foot } from "@/components/policy/PanelFoot";
import { PolicyCard } from "@/components/policy/PolicyCard";
import { NextStat } from "@/components/policy/RateStats";
import { StatRow } from "@/components/data/Stat";
import { FEEDS } from "@/lib/feeds";
import { policyDayHref } from "@/lib/policy/policyday";
import { slugFor } from "@/lib/policy/meeting";
import { readNextMeetings, readFreshness } from "@/lib/queries/shell";
import type { PolicyOverview } from "@/lib/queries/overview";
import type { Bank } from "@/lib/queries/policy";
import { panelFreshness } from "@/lib/shell/freshness";

const SWITCH = [
  { id: "Fed", label: "Fed" },
  { id: "RBI", label: "RBI" },
] as const;

export async function PolicyDay({ bank, overview }: { bank: Bank; overview: PolicyOverview }) {
  const [next, fresh] = await Promise.all([readNextMeetings(), readFreshness()]);
  const freshness = fresh.ok ? fresh.tasks : null;
  const now = overview.readAt;
  const latest = bank === "Fed" ? overview.fed : overview.rbi;
  const decision = latest.ok ? latest : null;
  const m = decision?.meeting ?? null;
  const policies = overview.fieldPolicy.ok ? overview.fieldPolicy.rows : null;
  const tasks = bank === "Fed" ? FEEDS.policyLatestFed : FEEDS.policyLatestRbi;
  const source = m?.attribution ?? (bank === "Fed" ? "Source: Federal Reserve Board" : "Source: Reserve Bank of India");
  const slug = m ? slugFor(bank, m.decision_date) : null;
  const pageFresh = panelFreshness(freshness, tasks, m?.decision_date ?? null, now);
  const foot = (dataTo: string | null) => <Foot sources={[source]} tasks={tasks} dataTo={dataTo} freshness={freshness} now={now} />;

  return (
    <>
      <PageHeader
        crumbs={["Workspace", "Policy Day"]}
        title="Policy Day"
        description="The latest decision of one bank on a page: what it decided, what it changed and how markets moved."
        meta={
          <>
            <span>Sources: Federal Reserve · Reserve Bank of India · FRED</span>
            <Freshness freshness={pageFresh} />
          </>
        }
      />
      <Tabs label="Bank" items={SWITCH.map((b) => ({ id: b.id, label: b.label, href: policyDayHref(b.id) }))} current={bank} />
      <div className="mt-5 flex flex-col gap-4">
        <PanelGrid>
          <PolicyCard bank={bank} latest={decision} policies={policies} now={now} footer={foot(m?.decision_date ?? null)} />
          <Panel
            title={bank === "Fed" ? "Next FOMC meeting" : "Next MPC meeting"}
            titleId="panel-next-meeting"
            subtitle="When is the next decision?"
            market={bank === "Fed" ? "US" : "IN"}
            span={6}
            variant="flush"
            footer={<Foot sources={[source]} tasks={FEEDS.statusBarCountdown} dataTo={null} freshness={freshness} now={now} />}
          >
            <StatRow>
              <NextStat bank={bank} meetings={next.ok ? next.meetings : null} />
            </StatRow>
          </Panel>
        </PanelGrid>
        {m && slug ? (
          <PolicyDayMeeting bank={bank} slug={slug} date={m.decision_date} />
        ) : (
          <PanelGrid>
            <ErrorPanel title="What changed" id="panel-changes" market={bank === "Fed" ? "US" : "IN"} footer={foot(null)} />
          </PanelGrid>
        )}
      </div>
    </>
  );
}
