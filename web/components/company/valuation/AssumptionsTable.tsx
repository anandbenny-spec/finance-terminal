"use client";

// The assumptions table (2A-20; Spec 02A §8): every input a run stored, with its value, as-of date and source, in the
// order the model uses them. A held input says "Held" and why. A figure the model derived says so (a Qualifier) and how
// in one sentence. Every number goes through lib/format.ts; nothing is recalculated here.
import { useMemo } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { EmptyFigure, amount } from "@/components/company/cells";
import type { AssumptionRow } from "@/lib/equity/valuation";
import { sharesText } from "@/lib/equity/valuation";
import { NOT_AVAILABLE, formatAxisNumber, formatFraction, formatMultiple, formatPrice, formatTableDate, type Currency, type Market } from "@/lib/format";

function valueOf(r: AssumptionRow, currency: Currency, market: Market): string {
  if (r.value === null) return NOT_AVAILABLE;
  switch (r.kind) {
    case "fraction":
      return r.value2 !== undefined && r.value2 !== null ? `${formatFraction(r.value)} equity, ${formatFraction(r.value2)} debt` : formatFraction(r.value, 2);
    case "price":
      return formatPrice(r.value, currency);
    case "money":
      return amount(r.value, currency);
    case "shares":
      return sharesText(r.value, market);
    case "beta":
      return formatAxisNumber(r.value, 2);
    case "multiple":
      return formatMultiple(r.value, market, 2);
  }
}

export function AssumptionsTable({ rows, currency, market, caption }: { rows: AssumptionRow[]; currency: Currency; market: Market; caption: string }) {
  const columns = useMemo<Column<AssumptionRow>[]>(
    () => [
      { id: "label", header: "Assumption", cell: (r) => <span className="text-ink-1">{r.label}</span> },
      {
        id: "value",
        header: "Value",
        align: "right",
        cell: (r) => (r.held ? <EmptyFigure state="held" reason={r.held} /> : valueOf(r, currency, market)),
      },
      { id: "as_of", header: "As of", cell: (r) => (r.asOf ? <span className="font-mono">{formatTableDate(r.asOf)}</span> : <span title="Not available">{NOT_AVAILABLE}</span>) },
      {
        id: "source",
        header: "Source",
        cell: (r) => (
          <span className="whitespace-normal">
            {r.derived && <Qualifier className="mr-1.5">derived</Qualifier>}
            {r.source}
            {r.note && <span className="block text-meta text-ink-3">{r.note}</span>}
          </span>
        ),
      },
    ],
    [currency, market],
  );
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.id}
      caption={caption}
      dense
      empty={<StateNote kind="empty">No assumptions are stored for this run.</StateNote>}
    />
  );
}
