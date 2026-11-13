"use client";

// The value chain sub-page's tables (VC-06, #191; Spec 02B §12.7), on DataTable (the one TanStack wrapper). Every cell arrives as a string
// lib/equity/valuechain.ts built from an `api` row, so nothing here formats or computes a figure. No column sorts and none ranks:
// the order is the company's, never a share's (§12.10: nothing ranks or colours companies by a share). Footnotes sit under the table
// they belong to. Numbers are right-aligned, as DataTable does.
import { useMemo, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import type { FieldsRow, FieldsTableModel, PeersModel, PeerRowModel, RptRow } from "@/lib/equity/valuechain";

export type GroupRow = { key: string; name: string; relation: string; share: string };
export type SegmentTableRow = { key: string; segment: string; revenue: string; share: string };

export function FieldsTable({ model }: { model: FieldsTableModel }) {
  const columns = useMemo<Column<FieldsRow>[]>(
    () => [
      { id: "field", header: "Field", wrapCell: true, cell: (r) => <span className="text-ink-1">{r.label}</span> },
      ...model.years.map(
        (y, i): Column<FieldsRow> => ({
          id: `y${i}`,
          header: y,
          align: "right",
          cell: (r) => {
            const c = r.cells[i];
            return (
              <span>
                {c.text}
                {c.marks.map((m) => (
                  // text-meta: a superscript's default size would fall under the 12 px floor. The mark is read as "note 1", not as a digit of the figure.
                  <sup key={m} className="ml-0.5 text-meta text-ink-3">
                    <span className="sr-only"> (note </span>
                    {m}
                    <span className="sr-only">)</span>
                  </sup>
                ))}
              </span>
            );
          },
        }),
      ),
      { id: "ref", header: "BRSR item", wrapCell: true, cell: (r) => <span className="text-ink-2">{r.itemRef}</span> },
    ],
    [model.years],
  );
  return (
    <>
      <DataTable
        columns={columns}
        rows={model.rows}
        rowKey={(r) => r.key}
        caption="Every value chain field in each fiscal year's BRSR"
        scroll
        focusable
        empty={<StateNote kind="empty">No BRSR filing is published for this company yet.</StateNote>}
      />
      {model.footnotes.length > 0 && (
        <ol className="list-decimal border-t border-line px-3 py-2 pl-7 text-meta text-ink-2">
          {model.footnotes.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ol>
      )}
    </>
  );
}

export function RptTable({ rows, caption, empty }: { rows: RptRow[]; caption: string; empty: ReactNode }) {
  const columns = useMemo<Column<RptRow>[]>(
    () => [
      { id: "rank", header: "#", align: "right", cell: (r) => r.rank },
      { id: "counterparty", header: "Counterparty, as filed", wrapCell: true, cell: (r) => <span className="text-ink-1">{r.counterparty}</span> },
      { id: "relationship", header: "Relationship, as filed", wrapCell: true, cell: (r) => <span className="text-ink-2">{r.relationship}</span> },
      { id: "value", header: "Value", align: "right", cell: (r) => r.value },
    ],
    [],
  );
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption={caption} dense focusable empty={empty} />;
}

export function GroupTable({ rows, empty }: { rows: GroupRow[]; empty: ReactNode }) {
  const columns = useMemo<Column<GroupRow>[]>(
    () => [
      { id: "name", header: "Group company", wrapCell: true, cell: (r) => <span className="text-ink-1">{r.name}</span> },
      { id: "relation", header: "Relation, as filed", wrapCell: true, cell: (r) => <span className="text-ink-2">{r.relation}</span> },
      { id: "share", header: "Share held", align: "right", cell: (r) => r.share },
    ],
    [],
  );
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption="Group companies and the share held, as the BRSR lists them" scroll focusable empty={empty} />;
}

export function SegmentsTable({ rows, empty }: { rows: SegmentTableRow[]; empty: ReactNode }) {
  const columns = useMemo<Column<SegmentTableRow>[]>(
    () => [
      { id: "segment", header: "Segment, as filed", wrapCell: true, cell: (r) => <span className="text-ink-1">{r.segment}</span> },
      { id: "revenue", header: "Revenue", align: "right", cell: (r) => r.revenue },
      { id: "share", header: "Share of the segments' total", align: "right", wrapHeader: true, cell: (r) => r.share },
    ],
    [],
  );
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption="Revenue by reportable segment, consolidated" focusable empty={empty} />;
}

export function PeersTable({ model }: { model: PeersModel }) {
  const columns = useMemo<Column<PeerRowModel>[]>(
    () => [
      {
        id: "company",
        header: "Company",
        wrapCell: true,
        cell: (r) => (
          <span className="text-ink-1">
            {r.company}
            {r.self && <span className="ml-1 text-meta text-ink-3">(this company)</span>}
          </span>
        ),
      },
      { id: "boundary", header: "Boundary", cell: (r) => <span className="text-ink-2">{r.boundary}</span> },
      ...model.fields.map((f): Column<PeerRowModel> => ({ id: f.key, header: f.label, align: "right", wrapHeader: true, cell: (r) => r.cells[f.key] })),
    ],
    [model.fields],
  );
  const medianRow = model.median;
  return (
    <>
      <DataTable
        columns={columns}
        rows={model.rows}
        rowKey={(r) => r.key}
        caption="The peer group's value chain figures in the latest filed year, as each company reported them"
        scroll
        focusable
        empty={<StateNote kind="unavailable">No peers have a BRSR filing published yet.</StateNote>}
      />
      {medianRow && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-line px-3 py-2 text-meta text-ink-2 sm:grid-cols-3">
          <div className="col-span-full text-ink-1">Median of the other peers on this company&apos;s boundary (n = {medianRow.n} at most)</div>
          {model.fields.map((f) => (
            <div key={f.key}>
              <dt className="inline">{f.label}: </dt>
              <dd className="inline text-ink-1">{medianRow.cells[f.key]}</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}
