"use client";

// DataTable (Design 01 §1.6, §4.4): the only file that imports TanStack Table (pinned at v8.21; v9 postdates the
// build model's training), so an upgrade touches this file alone. A sticky header on raised (meta 500, ink-2, units
// in the header), 32 px rows (28 dense), data size, hairline dividers, hover raised. Numbers right-aligned, text
// left. Sorting shown by an icon and aria-sort. A row that links has a real anchor in its first cell. Below md the
// table scrolls sideways inside its panel, with the first column sticky. Cells arrive formatted (lib/format.ts):
// this component never formats a number.
// `scroll` (Anand, 28 Oct 2026: the page shouldn't run on for screens): a long table stops at --table-max-h and the
// rest of its rows scroll inside the panel, under the sticky header (Design 01 §5.4 already scrolls panels inside
// themselves). The scroller is a named, focusable region, so a keyboard can scroll it too (WCAG 2.1.1); its focus
// ring sits inside the edge, since the panel clips anything outside.
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { cn } from "cn";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

export type Column<Row> = {
  id: string;
  header: string;
  cell: (row: Row) => ReactNode;
  /** Numbers right-aligned (Design 01 §4.4). */
  align?: "left" | "right";
  /** A value to sort by; columns without one don't sort. */
  sortValue?: (row: Row) => string | number | null;
};

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  caption,
  initialSort,
  dense = false,
  scroll = false,
  empty,
}: {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** Read by screen readers; the panel title says the same on screen. */
  caption: string;
  initialSort?: { id: string; desc: boolean };
  dense?: boolean;
  /** Cap the height at --table-max-h; the other rows scroll inside the panel under the sticky header. */
  scroll?: boolean;
  /** What to show when no row matches (a StateNote). */
  empty: ReactNode;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort ? [initialSort] : []);
  const defs = useMemo<ColumnDef<Row>[]>(
    () =>
      columns.map((c) => ({
        id: c.id,
        header: c.header,
        accessorFn: (row: Row) => c.sortValue?.(row) ?? null,
        enableSorting: Boolean(c.sortValue),
        sortUndefined: "last",
        cell: (ctx) => c.cell(ctx.row.original),
      })),
    [columns],
  );
  const table = useReactTable({
    data: rows,
    columns: defs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: rowKey,
  });
  const align = new Map(columns.map((c) => [c.id, c.align ?? "left"]));

  return (
    <div
      className={cn(scroll ? "max-h-(--table-max-h) overflow-auto focus-visible:-outline-offset-2" : "overflow-x-auto")}
      {...(scroll && { role: "region", "aria-label": caption, tabIndex: 0 })}
    >
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-10 bg-raised">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header, i) => {
                const sorted = header.column.getIsSorted();
                const right = align.get(header.column.id) === "right";
                const label = flexRender(header.column.columnDef.header, header.getContext());
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                    className={cn(
                      "h-8 border-b border-line-strong px-3 text-meta font-medium whitespace-nowrap text-ink-2",
                      right ? "text-right" : "text-left",
                      i === 0 && "sticky left-0 z-10 bg-raised",
                    )}
                  >
                    {header.column.getCanSort() ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className={cn("inline-flex items-center gap-1 hover:text-ink-1", right && "flex-row-reverse")}
                      >
                        {label}
                        {sorted === "asc" ? (
                          <ArrowUp aria-hidden className="size-3" />
                        ) : sorted === "desc" ? (
                          <ArrowDown aria-hidden className="size-3" />
                        ) : (
                          <ArrowUpDown aria-hidden className="size-3 text-ink-3" />
                        )}
                      </button>
                    ) : (
                      label
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="group border-b border-line last:border-b-0 hover:bg-raised">
              {row.getVisibleCells().map((cell, i) => (
                <td
                  key={cell.id}
                  className={cn(
                    "px-3 whitespace-nowrap text-ink-1",
                    dense ? "h-7" : "h-8",
                    align.get(cell.column.id) === "right" ? "text-right" : "text-left",
                    i === 0 && "sticky left-0 bg-panel group-hover:bg-raised",
                  )}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div className="p-3">{empty}</div>}
    </div>
  );
}
