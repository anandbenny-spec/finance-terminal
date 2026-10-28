"use client";

// "Decision-day moves" (Spec 01 §4.2, History; Pass 1b, #70): a DataTable of how markets moved around each decision
// since 2019, one column per reaction series (Spec 01 §2), with the Fed's surprise measure (the US 2-year) first.
// Each move is a market move, so it shows as a Delta (sign, triangle, colour); hover or focus gives the two closes
// behind it. Derived figures, labelled as such in the note. Cells arrive formatted (lib/policy/reactions.ts). Mixes
// both markets, so no market edge.
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Delta } from "@/components/data/Delta";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { changeOf } from "@/lib/delta";
import { formatTableDate } from "@/lib/format";
import { REACTION_SERIES, type ReactionCell, type ReactionRow } from "@/lib/policy/reactions";

const SELECT =
  "h-8 rounded-md border border-line-input bg-raised px-2 text-data text-ink-1 hover:border-ink-3";

function Cell({ c }: { c: ReactionCell }) {
  if (c.state === "none") return null;
  if (c.state === "waiting" || c.state === "unavailable") {
    return (
      <span title={c.detail}>
        <span aria-hidden>{c.text}</span>
        <span className="sr-only">{c.detail}</span>
      </span>
    );
  }
  return (
    <span title={c.detail}>
      <Delta change={changeOf(c.text)} />
      <span className="sr-only"> ({c.detail})</span>
    </span>
  );
}

export function ReactionsPanel({ rows, footer, refresh }: { rows: ReactionRow[]; footer: ReactNode; refresh?: readonly string[] }) {
  const [bank, setBank] = useState("all");
  const shown = useMemo(() => rows.filter((r) => bank === "all" || r.bank === bank), [rows, bank]);

  const columns = useMemo<Column<ReactionRow>[]>(
    () => [
      {
        id: "date",
        header: "Date",
        sortValue: (r) => r.date,
        cell: (r) => (
          <Link href={r.href} className="font-mono text-ink-1 underline">
            {formatTableDate(r.date)}
          </Link>
        ),
      },
      {
        id: "bank",
        header: "Bank",
        sortValue: (r) => r.bank,
        cell: (r) => (
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden className={`inline-block size-2 rounded-full ${r.bank === "Fed" ? "bg-series-1" : "bg-series-2"}`} />
            {r.bank}
          </span>
        ),
      },
      { id: "decision", header: "Decision", cell: (r) => r.decision },
      ...REACTION_SERIES.map(
        (s): Column<ReactionRow> => ({
          id: s.key,
          header: s.header,
          align: "right",
          sortValue: (r) => {
            const c = r.cells[s.key];
            return c.state === "move" ? c.change : null;
          },
          cell: (r) => <Cell c={r.cells[s.key]} />,
        }),
      ),
    ],
    [],
  );

  return (
    <Panel
      title="Decision-day moves"
      titleId="panel-reactions"
      subtitle="How did markets react to each decision?"
      variant="flush"
      footer={footer}
      refresh={refresh}
    >
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-b border-line px-3 py-2">
        <label className="flex items-center gap-1.5 text-meta text-ink-2">
          Bank
          <select className={SELECT} value={bank} onChange={(e) => setBank(e.target.value)}>
            <option value="all">Both</option>
            <option value="Fed">Fed</option>
            <option value="RBI">RBI</option>
          </select>
        </label>
        <span className="ml-auto self-center text-meta text-ink-3" aria-live="polite">
          {shown.length === 1 ? "1 decision" : `${shown.length} decisions`}
        </span>
      </div>
      <p className="border-b border-line px-3 py-2 text-meta text-ink-2">
        Derived: each move runs from each market&apos;s last value fixed before the decision&apos;s release to its first value fixed after it. The US 2-year&apos;s
        move is the Fed&apos;s surprise measure. Yields in basis points, the rest in percent.
      </p>
      <DataTable
        columns={columns}
        rows={shown}
        rowKey={(r) => r.slug}
        caption="Market moves around each FOMC and MPC rate decision since 2019"
        initialSort={{ id: "date", desc: true }}
        scroll
        empty={<StateNote kind="empty">No decisions match this filter.</StateNote>}
      />
    </Panel>
  );
}
