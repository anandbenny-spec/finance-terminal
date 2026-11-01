"use client";

// The passing evaluation runs (api.eval_summary, site-wide: the view has no module column). Cells arrive formatted
// (lib/accuracy/summary.ts); "View as table" is the table itself, there is no chart (Design 01 §4.4).
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import type { EvalRunLine } from "@/lib/accuracy/summary";

const COLUMNS: Column<EvalRunLine>[] = [
  { id: "evalSet", header: "Evaluation set", sortValue: (r) => r.evalSet, cell: (r) => r.evalSet },
  { id: "metric", header: "Measure", sortValue: (r) => r.metric, cell: (r) => r.metric },
  { id: "value", header: "Result", align: "right", cell: (r) => <span className="font-mono">{r.value}</span> },
  { id: "items", header: "Items checked", align: "right", cell: (r) => <span className="font-mono">{r.items}</span> },
  { id: "date", header: "Run on", cell: (r) => <span className="font-mono">{r.date}</span> },
];

export function EvalTable({ rows }: { rows: EvalRunLine[] }) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(r) => r.key}
      caption="Passing evaluation runs"
      dense
      scroll
      empty={<StateNote kind="empty">No evaluation run has passed yet.</StateNote>}
    />
  );
}
