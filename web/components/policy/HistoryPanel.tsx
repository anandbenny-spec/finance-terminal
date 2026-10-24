"use client";

// "Every decision since 2019" (Spec 01 §4.2, History): a DataTable of every held rate decision since 1 Jan 2019
// (the 2018 baselines stay out of "since 2019" lists), filtered by bank, year and cut / hold / hike, each row
// linking to its meeting page. The RBI stance column is AI-extracted (its qualifier in the header). Cells arrive
// formatted (lib/policy/decisions.ts). Mixes both markets, so no market edge.
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { NOT_AVAILABLE, formatAxisNumber, formatTableDate } from "@/lib/format";
import type { Direction, HistoryRow } from "@/lib/policy/decisions";

const SELECT =
  "h-8 rounded-md border border-line-input bg-raised px-2 text-data text-ink-1 hover:border-ink-3";

function Filter({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="flex items-center gap-1.5 text-meta text-ink-2">
      {label}
      <select className={SELECT} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

export function HistoryPanel({ rows, stanceLabel, footer, refresh }: { rows: HistoryRow[]; stanceLabel: string | null; footer: ReactNode; refresh?: readonly string[] }) {
  const [bank, setBank] = useState("all");
  const [year, setYear] = useState("all");
  const [move, setMove] = useState<"all" | Direction>("all");
  const years = useMemo(() => [...new Set(rows.map((r) => r.year))].sort().reverse(), [rows]);
  const shown = useMemo(
    () => rows.filter((r) => (bank === "all" || r.bank === bank) && (year === "all" || r.year === year) && (move === "all" || r.direction === move)),
    [rows, bank, year, move],
  );

  const columns = useMemo<Column<HistoryRow>[]>(
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
      { id: "kind", header: "Kind", cell: (r) => r.kind },
      { id: "decision", header: "Decision", cell: (r) => r.decision },
      { id: "rate", header: "Rate after (%)", align: "right", cell: (r) => r.rateAfter },
      { id: "change", header: "Change (bps)", align: "right", sortValue: (r) => r.changeBps, cell: (r) => r.change },
      { id: "vote", header: "Vote", cell: (r) => (r.voteNote ? <span title={r.voteNote}>{NOT_AVAILABLE}</span> : r.vote) },
      { id: "dissents", header: "Dissents", align: "right", sortValue: (r) => r.dissents, cell: (r) => formatAxisNumber(r.dissents, 0) },
      {
        id: "stance",
        header: stanceLabel ? `Stance, RBI (${stanceLabel})` : "Stance, RBI",
        cell: (r) => (r.bank === "RBI" ? r.stance ?? NOT_AVAILABLE : ""),
      },
    ],
    [stanceLabel],
  );

  return (
    <Panel
      title="Every decision since 2019"
      titleId="panel-history"
      subtitle="When did each bank move, and by how much?"
      variant="flush"
      footer={footer}
      refresh={refresh}
    >
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-b border-line px-3 py-2">
        <Filter label="Bank" value={bank} onChange={setBank} options={[["all", "Both"], ["Fed", "Fed"], ["RBI", "RBI"]]} />
        <Filter label="Year" value={year} onChange={setYear} options={[["all", "All years"], ...years.map((y): [string, string] => [y, y])]} />
        <Filter
          label="Decision"
          value={move}
          onChange={(v) => setMove(v as "all" | Direction)}
          options={[["all", "All"], ["cut", "Cut"], ["hold", "Hold"], ["hike", "Hike"]]}
        />
        <span className="ml-auto self-center text-meta text-ink-3" aria-live="polite">
          {shown.length === 1 ? "1 decision" : `${shown.length} decisions`}
        </span>
      </div>
      <DataTable
        columns={columns}
        rows={shown}
        rowKey={(r) => r.slug}
        caption="Every FOMC and MPC rate decision since 2019"
        initialSort={{ id: "date", desc: true }}
        empty={<StateNote kind="empty">No decisions match these filters.</StateNote>}
      />
    </Panel>
  );
}
