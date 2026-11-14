"use client";

// The KPI-table pilot's flags as a table (DD-06, #514): quarter · metric · change · last reported · reason stated · an analyst
// asked · documents. Every cell arrives as words lib/equity/kpi.ts built from the view's rows; the quotes are verified quotes into
// the quarter's stored call transcript (CitationChip), never a transcript's or a release's text. No colour: nothing here is a
// market move. A long list scrolls inside its panel (#145).
import { CitationChip } from "@/components/ai/CitationChip";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import type { KpiFlagView, QuoteCell } from "@/lib/equity/kpi";

function Quote({ cell }: { cell: QuoteCell }) {
  if (!cell.citation) return <span className="text-ink-2">{cell.text}</span>;
  return (
    <span className="text-ink-1">
      {cell.text} <CitationChip citation={cell.citation} />
    </span>
  );
}

function Documents({ row }: { row: KpiFlagView }) {
  const n = row.earlierUrls.length;
  return (
    <span className="flex flex-col gap-0.5">
      {row.currentUrl ? (
        <a href={row.currentUrl} className="text-ink-1 underline" rel="noreferrer" target="_blank" aria-label={`${row.quarter} release: official source`}>
          {row.quarter}: Official source ↗
        </a>
      ) : (
        <span className="text-ink-3">{row.quarter}: not linked</span>
      )}
      {n > 0 && (
        <span className="text-ink-2">
          Earlier:{" "}
          {row.earlierUrls.map((url, i) => (
            <span key={url}>
              <a href={url} className="text-ink-1 underline" rel="noreferrer" target="_blank" aria-label={`Earlier release ${i + 1} of ${n}: official source`}>
                {i + 1} ↗
              </a>
              {i < n - 1 ? " " : ""}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

export function KpiChangesTable({ flags, caption }: { flags: KpiFlagView[]; caption: string }) {
  const columns: Column<KpiFlagView>[] = [
    { id: "quarter", header: "Quarter", cell: (r) => r.quarter, sortValue: (r) => r.periodEnd, alignTop: true },
    { id: "metric", header: "Metric", cell: (r) => r.metric, wrapCell: true, alignTop: true },
    {
      id: "change",
      header: "Change",
      wrapCell: true,
      alignTop: true,
      cell: (r) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold text-ink-1">{r.title}</span>
          <span className="text-ink-2">{r.sentence}</span>
          {r.note && <span className="text-meta text-ink-2">{r.note}</span>}
        </span>
      ),
    },
    { id: "last", header: "Last reported", cell: (r) => r.last, wrapCell: true, alignTop: true },
    { id: "reason", header: "Reason stated", cell: (r) => <Quote cell={r.reason} />, wrapCell: true, alignTop: true },
    { id: "asked", header: "An analyst asked", cell: (r) => <Quote cell={r.asked} />, wrapCell: true, alignTop: true },
    { id: "documents", header: "Documents", cell: (r) => <Documents row={r} />, wrapCell: true, alignTop: true },
  ];
  return (
    <DataTable
      columns={columns}
      rows={flags}
      rowKey={(r) => r.key}
      caption={caption}
      scroll
      focusable
      empty={<StateNote kind="empty">No missing or dropped metric is confirmed for this company in the pilot&apos;s quarters.</StateNote>}
    />
  );
}
