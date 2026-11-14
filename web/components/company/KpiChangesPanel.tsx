// "Metrics missing from a release (pilot)" on the Financials tab (DD-06, #514; Plan 05 §1.3, D7, D8): for the 7 pilot companies
// only, the metrics a company's results release (press release, fact sheet or data sheet) reported in earlier quarters and doesn't
// carry in a later one, from api.kpi_changes (0047, #512; flags by DD-05, #513). The view returns a flag only once a judge has
// confirmed it against both documents (DD-07, #515), and nothing while `equity.kpi_changes` is hidden: the panel then shows the
// held note and nothing else. Words, never colour (CLAUDE.md §5: nothing here is a market move), never a motive (D8). "Reason
// stated" and "an analyst asked" are verified quotes into the quarter's stored call transcript, at most 400 characters
// (CLAUDE.md §1). A pilot, labelled as one, with the line that it isn't a complete list. Server-rendered; the table is the
// client part (DataTable). Sits after DD-02's Segment changes panel.
import { Gated, fieldPolicy, type FieldPolicies } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { KpiChangesTable } from "@/components/company/KpiChangesTable";
import { Foot } from "@/components/policy/PanelFoot";
import { sourceLineFor } from "@/lib/equity/company";
import { KPI_FIELD, KPI_HELD, kpiModel, type KpiModel } from "@/lib/equity/kpi";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import { formatDate } from "@/lib/format";
import type { EarningsCallRow, KpiChangeRow, TaskFreshnessRow } from "@/lib/queries/api";

const TASKS = PREVIEW_FEEDS.kpiChanges;

/** The line under the rows (the card's words), and the method's known miss (Data Audit 10, K6). */
export const KPI_PILOT_LINE = "A pilot for seven companies. These are the changes found and confirmed, not a complete list.";
export const KPI_METHOD_LINE =
  "The metrics are AI-extracted from each release, and the reading can miss one, so a change can go unflagged: the pilot's audit found one such miss, TCS's workforce figure in Q2 FY26.";

function Body({ model }: { model: Exclude<KpiModel, { kind: "held" }> }) {
  switch (model.kind) {
    case "error":
      return <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>;
    case "none":
      return <StateNote kind="empty">No missing or dropped metric is confirmed for this company in the pilot&apos;s quarters.</StateNote>;
    case "flags":
      return (
        <>
          <p className="mb-3 text-meta text-ink-2">
            Each quarter&apos;s results release against the four quarters before it, the metrics as printed. The latest flag is for the quarter ended{" "}
            {formatDate(model.latest)}.
          </p>
          <KpiChangesTable flags={model.flags} caption="Metrics missing from a release, by quarter" />
        </>
      );
  }
}

export function KpiChangesPanel({
  rows,
  calls,
  policies,
  freshness,
  now,
}: {
  /** api.kpi_changes for this company; null when the read failed. */
  rows: readonly KpiChangeRow[] | null | undefined;
  /** The company's calls (api.earnings_calls), to say whether a quarter's call transcript was there to search; null when the read failed. */
  calls: readonly EarningsCallRow[] | null | undefined;
  policies: FieldPolicies | null | undefined;
  freshness: TaskFreshnessRow[] | null;
  now: string;
}) {
  const policy = fieldPolicy(policies ?? [], KPI_FIELD);
  const hidden = !policy || policy.status === "hidden";
  const model = kpiModel(rows, calls, hidden);
  const shown = !hidden ? (rows ?? []) : [];
  // While held back the footer carries the source line only: no data is shown, so no date or run is claimed for it.
  const footer = hidden ? (
    <Foot sources={[sourceLineFor(null)]} tasks={[]} dataTo={null} freshness={null} now={now} />
  ) : (
    <Foot sources={[sourceLineFor(shown[0]?.source_key)]} tasks={TASKS} dataTo={model.kind === "flags" ? model.latest : null} freshness={freshness} now={now} />
  );
  return (
    <Panel
      title="Metrics missing from a release (pilot)"
      titleId="panel-kpi-changes"
      subtitle="Which metrics the company's results release reported in earlier quarters are not in a later quarter's release?"
      qualifier={policy?.status === "labelled" && policy.label ? <Qualifier>{policy.label}</Qualifier> : undefined}
      footer={footer}
    >
      <Gated policies={policies ?? []} field={KPI_FIELD} heldBackNote={KPI_HELD} showLabel={false}>
        {model.kind !== "held" && (
          <>
            <Body model={model} />
            {model.kind !== "error" && (
              <div className="mt-3 flex flex-col gap-1 text-meta text-ink-2">
                <p>{KPI_PILOT_LINE}</p>
                <p>{KPI_METHOD_LINE}</p>
              </div>
            )}
          </>
        )}
      </Gated>
    </Panel>
  );
}
