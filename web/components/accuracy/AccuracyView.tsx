// The /accuracy page's body (#135; Plan 02 §4): "How often does this site hold something back, and do its checks
// work?" One section per module that has rows in api.quality_summary, for the period in the URL. Counts only, all of
// them the database's (never computed here); no task names, run ids or error text (CLAUDE.md §4). Each panel carries
// SourceLine (the site's own records) and Freshness (the projection's `as_of`, and its task's freshness).
import type { ReactNode } from "react";
import { Freshness } from "@/components/data/Freshness";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { Section } from "@/components/data/SectionHeading";
import { SourceLine } from "@/components/data/SourceLine";
import { Stat, StatRow } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import { Tabs } from "@/components/data/Tabs";
import { EvalTable } from "@/components/accuracy/EvalTable";
import {
  countOf,
  dataTo,
  evalRunLines,
  moduleSections,
  onTimeShare,
  periodHref,
  periodLabel,
  PERIODS,
  type ModuleSection,
  type Period,
} from "@/lib/accuracy/summary";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import { formatTableDate } from "@/lib/format";
import type { EvalSummaryRow, QualitySummaryRow, TaskFreshnessRow } from "@/lib/queries/api";
import { panelFreshness } from "@/lib/shell/freshness";

export const OWN_RECORDS = "Source: Finance Terminal's own run and check records";

export type AccuracyData = {
  period: Period;
  quality: { ok: true; rows: QualitySummaryRow[] } | { ok: false };
  evals: { ok: true; rows: EvalSummaryRow[] } | { ok: false };
  /** null when the freshness read failed. */
  freshness: TaskFreshnessRow[] | null;
  now: string;
};

function Footer({ data, to }: { data: AccuracyData; to: string | null }) {
  return (
    <>
      <SourceLine lines={[OWN_RECORDS]} />
      <Freshness freshness={panelFreshness(data.freshness, PREVIEW_FEEDS.accuracy, to, data.now)} />
    </>
  );
}

function Counts({ section, items }: { section: ModuleSection; items: [label: string, measure: string][] }) {
  return (
    <StatRow>
      {items.map(([label, measure]) => (
        <Stat key={measure} label={label} value={countOf(section, measure)} />
      ))}
    </StatRow>
  );
}

function ModuleBlock({ section, data, to }: { section: ModuleSection; data: AccuracyData; to: string | null }) {
  const period = periodLabel(data.period);
  const footer = <Footer data={data} to={to} />;
  const share = onTimeShare(section);
  const drillsRun = section.measures.get("drills_run")?.value;
  return (
    <Section id={`module-${section.module}`} title={section.label} note={period}>
      <PanelGrid>
        <Panel title="AI outputs" titleId={`ai-${section.module}`} subtitle="How many AI-written outputs were published, and how many a check stopped?" footer={footer}>
          <Counts
            section={section}
            items={[
              ["Published", "ai_published"],
              ["Held for review", "ai_held_review"],
              ["Stopped: citation check", "ai_rejected_citation"],
              ["Stopped: numbers check", "ai_rejected_numbers"],
              ["Stopped: format check", "ai_rejected_format"],
              ["Stopped early", "ai_rejected_stopped"],
              ["Stopped: other", "ai_rejected_other"],
              ["All outputs", "ai_outputs"],
            ]}
          />
        </Panel>
        <Panel title="Loads" titleId={`loads-${section.module}`} subtitle="How often did a data load fail a check, and was the last good data kept?" span={6} footer={footer}>
          <Counts
            section={section}
            items={[
              ["Loads run", "loads"],
              ["Rolled back by a check", "loads_rolled_back"],
              ["Failed", "loads_failed"],
            ]}
          />
        </Panel>
        <Panel
          title="Freshness"
          titleId={`freshness-${section.module}`}
          qualifier={<Qualifier>derived</Qualifier>}
          subtitle="On how many task-days was the data on time?"
          span={6}
          footer={footer}
        >
          <StatRow>
            <Stat label="Task-days on time" value={share.value} context={share.context} />
          </StatRow>
        </Panel>
        <Panel title="Drills" titleId={`drills-${section.module}`} subtitle="Do the site's safety switches work when tested?" span={6} footer={footer}>
          {drillsRun === 0 ? (
            <StateNote kind="empty">{data.period === "all" ? "No drills have been run yet." : `No drills run in the ${period}.`}</StateNote>
          ) : (
            <StatRow>
              <Stat label="Drills run" value={countOf(section, "drills_run")} />
              <Stat label="Drills passed" value={countOf(section, "drills_passed")} />
            </StatRow>
          )}
        </Panel>
        <Panel title="Evaluation gates" titleId={`gates-${section.module}`} subtitle="Has each AI score passed its test before being shown?" span={6} footer={footer}>
          <Counts
            section={section}
            items={[
              ["Passed", "eval_gates_passed"],
              ["Pending", "eval_gates_pending"],
            ]}
          />
        </Panel>
        <Panel title="Fields held back" titleId={`fields-${section.module}`} subtitle="How many fields are hidden or labelled until they are checked?" span={6} footer={footer}>
          <Counts
            section={section}
            items={[
              ["Hidden", "fields_hidden"],
              ["Shown with a label", "fields_labelled"],
            ]}
          />
        </Panel>
      </PanelGrid>
    </Section>
  );
}

function ErrorNote({ children }: { children: ReactNode }) {
  return <StateNote kind="error">{children}</StateNote>;
}

export function AccuracyView({ data }: { data: AccuracyData }) {
  const { period, quality, evals } = data;
  const sections = quality.ok ? moduleSections(quality.rows, period) : [];
  const to = quality.ok ? dataTo(quality.rows) : null;
  const footer = <Footer data={data} to={to} />;
  return (
    <>
      <PageHeader
        crumbs={["About", "Accuracy"]}
        title="Accuracy"
        description="How often does this site hold something back, and do its checks work? Counts only, by module."
        meta={<span>Informational only, not investment advice.</span>}
      />
      <Tabs
        label="Period"
        items={PERIODS.map((p) => ({ id: p.id, label: p.label, href: periodHref(p.id) }))}
        current={period}
      />

      {!quality.ok ? (
        <Section id="counts" title="Counts by module">
          <PanelGrid>
            <Panel title="Counts" titleId="counts-title" footer={footer}>
              <ErrorNote>Couldn&apos;t load the counts.</ErrorNote>
            </Panel>
          </PanelGrid>
        </Section>
      ) : sections.length === 0 ? (
        <Section id="counts" title="Counts by module">
          <PanelGrid>
            <Panel title="Counts" titleId="counts-title" footer={footer}>
              <StateNote kind="empty">No counts have been recorded for the {periodLabel(period)} yet.</StateNote>
            </Panel>
          </PanelGrid>
        </Section>
      ) : (
        sections.map((s) => <ModuleBlock key={s.module} section={s} data={data} to={to} />)
      )}

      <Section id="evaluations" title="Passing evaluation runs" note="all modules">
        <PanelGrid>
          <Panel
            title="Evaluation runs"
            titleId="evals-title"
            subtitle="Which tests did the AI tasks pass, on how many items, and when?"
            variant="flush"
            footer={footer}
          >
            {!evals.ok ? (
              <div className="p-3">
                <ErrorNote>Couldn&apos;t load the evaluation runs.</ErrorNote>
              </div>
            ) : (
              <EvalTable rows={evalRunLines(evals.rows, formatTableDate)} />
            )}
          </Panel>
        </PanelGrid>
      </Section>
    </>
  );
}
