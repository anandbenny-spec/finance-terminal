"use client";

// StatementTable (2A-19; Spec 02A §8, the Financials tab): one statement as lines down and periods across, in the
// company's reporting currency (rupees in crore), each column dated by its period end and linked, in the last row, to the
// filing it came from (the stored source_url). EBIT is the derived figure from `api.company_financials` and says so; where
// it is held back or doesn't apply, the stored reason is shown. Nothing here computes a figure.
import { useMemo, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { EmptyFigure, lineValue } from "@/components/company/cells";
import { KIND_WORDS, metricByPeriod, type LineSpec } from "@/lib/equity/company";
import { formatDate, formatFraction, formatTableDate, type Currency } from "@/lib/format";
import type { CompanyFinancialRow, CompanyMetricRow } from "@/lib/queries/api";

type Line = { id: string; label: ReactNode; cells: Map<string, ReactNode> };

const colId = (r: CompanyFinancialRow) => `${r.period_kind}-${r.period_end}`;

function FilingLink({ row }: { row: CompanyFinancialRow }) {
  const text = row.filed_at ? `Filed ${formatDate(row.filed_at.slice(0, 10))}` : "Filing";
  if (!row.source_url) return <span>{text}</span>;
  return (
    <a
      href={row.source_url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`${text}: the filing for the period ending ${formatDate(row.period_end)}`}
      className="text-ink-1 underline hover:text-ink-2"
    >
      {text}
    </a>
  );
}

/** A line read from `api.company_metrics` instead of the filing (a bank's ROA and NIM proxy), by metric key, for the quarter. */
export type MetricLine = { id: string; label: string; key: string };

export function StatementTable({
  title,
  titleId,
  subtitle,
  rows,
  lines,
  metricLines = [],
  metrics = [],
  currency,
  footer,
  note,
  emptyText,
}: {
  title: string;
  titleId: string;
  subtitle: string;
  /** The periods to show, newest first. */
  rows: CompanyFinancialRow[];
  lines: LineSpec[];
  metricLines?: MetricLine[];
  metrics?: CompanyMetricRow[];
  currency: Currency;
  footer: ReactNode;
  note?: ReactNode;
  emptyText: string;
}) {
  const { columns, data } = useMemo(() => {
    const data: Line[] = [
      { id: "period", label: "Period", cells: new Map(rows.map((r) => [colId(r), KIND_WORDS[r.period_kind] ?? r.period_kind])) },
      ...lines.map<Line>((spec) => ({
        id: spec.id,
        label:
          spec.id === "ebit_derived" ? (
            <span>
              {spec.label} <Qualifier>{rows.find((r) => r.ebit_derived_label)?.ebit_derived_label ?? "derived"}</Qualifier>
            </span>
          ) : (
            spec.label
          ),
        cells: new Map(
          rows.map<[string, ReactNode]>((r) => {
            const value = r[spec.id];
            if (spec.id === "ebit_derived" && value === null) {
              const state = r.ebit_derived_state === "not_applicable" ? "not_applicable" : r.ebit_derived_state === "held" ? "held" : "missing";
              return [colId(r), <EmptyFigure key={colId(r)} state={state} reason={r.ebit_derived_held_reason} />];
            }
            return [colId(r), value === null ? <EmptyFigure key={colId(r)} state="missing" /> : lineValue(spec, value, currency)];
          }),
        ),
      })),
      ...metricLines.map<Line>((x) => {
        const stored = metricByPeriod(metrics, x.key, "quarter", "standalone");
        const labelKind = [...stored.values()].find((m) => m.label_kind)?.label_kind;
        return {
          id: x.id,
          label: (
            <span>
              {x.label} {labelKind && <Qualifier>{labelKind}</Qualifier>}
            </span>
          ),
          cells: new Map(
            rows.map<[string, ReactNode]>((r) => {
              const m = stored.get(r.period_end);
              if (!m) return [colId(r), <EmptyFigure key={colId(r)} state="missing" />];
              if (m.state === "value" && m.value !== null) return [colId(r), formatFraction(m.value, 2)];
              return [colId(r), <EmptyFigure key={colId(r)} state={m.state === "not_applicable" ? "not_applicable" : "held"} reason={m.held_reason} />];
            }),
          ),
        };
      }),
      { id: "filing", label: "Filing", cells: new Map(rows.map((r) => [colId(r), <FilingLink key={colId(r)} row={r} />])) },
    ];
    const columns: Column<Line>[] = [
      { id: "line", header: "Line", cell: (l) => <span className="text-ink-1">{l.label}</span> },
      ...rows.map<Column<Line>>((r) => ({
        id: colId(r),
        header: formatTableDate(r.period_end),
        align: "right",
        cell: (l) => <span className={l.id === "period" || l.id === "filing" ? "text-ink-2" : undefined}>{l.cells.get(colId(r))}</span>,
      })),
    ];
    return { columns, data };
  }, [rows, lines, metricLines, metrics, currency]);

  return (
    <Panel title={title} titleId={titleId} subtitle={subtitle} variant="flush" footer={footer}>
      {rows.length === 0 ? (
        <div className="p-3">
          <StateNote kind="unavailable">{emptyText}</StateNote>
        </div>
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={data}
            rowKey={(l) => l.id}
            caption={`${title}: ${[...lines.map((l) => l.label), ...metricLines.map((x) => x.label)].join(", ")} for each period shown, newest first, with each period's filing`}
            dense
            empty={<StateNote kind="empty">Nothing to show.</StateNote>}
          />
          {note && <div className="border-t border-line px-3 py-2 text-meta text-ink-2">{note}</div>}
        </>
      )}
    </Panel>
  );
}
