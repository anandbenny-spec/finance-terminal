"use client";

// The results-day moves of one call (#471; Spec 02B §5.12). Stored values from api.results_moves, formatted by lib/format.ts.
// A company's move, the Nifty 50's and the excess are market numbers, so each goes through `Delta` (a sign and a triangle;
// green and red only for these). A window that was left out has no figure; the row says why in words.
import { EmptyFigure } from "@/components/company/cells";
import { Move } from "@/components/company/RateDecisionTables";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import type { MoveLine } from "@/lib/equity/resultsMoves";

const figure = (line: MoveLine, value: number | null) =>
  line.leftOut !== null || value === null ? <EmptyFigure state="missing" /> : <Move value={value} />;

export function ResultsMoveTable({ lines, caption }: { lines: MoveLine[]; caption: string }) {
  const columns: Column<MoveLine>[] = [
    {
      id: "window",
      header: "Window",
      cell: (l) => (
        <span className="text-ink-1">
          {l.window}
          {l.leftOut !== null && <span className="block text-meta text-ink-2">{l.leftOut}</span>}
        </span>
      ),
    },
    { id: "closes", header: "Closes compared", cell: (l) => l.closes ?? <EmptyFigure state="missing" /> },
    { id: "own", header: "Company (%)", align: "right", cell: (l) => figure(l, l.own) },
    { id: "nifty", header: "Nifty 50 (%)", align: "right", cell: (l) => figure(l, l.nifty) },
    { id: "excess", header: "Excess (%)", align: "right", cell: (l) => figure(l, l.excess) },
  ];
  return <DataTable columns={columns} rows={lines} rowKey={(l) => l.key} caption={caption} dense empty={<StateNote kind="empty">No moves to list.</StateNote>} />;
}
