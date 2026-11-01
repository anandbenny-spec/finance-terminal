"use client";

// The tables of the Rate decisions tab and the sector view (2A-25; Spec 02A §8, §5.10). Every figure is a stored
// value from `api.rate_decision_stats`, `api.rate_decision_moves` or `api.company_rate_cycles`, formatted by
// lib/format.ts; nothing is computed here. Decisions (cut, hold, hike) and cycle directions are words, never colour;
// a stock's move or its excess over the Nifty 50 is a market number, so it goes through `Delta` (a sign and a
// triangle; green and red). Nobody is told what the numbers mean for the future: no significance, no forecast.
import Link from "next/link";
import type { ReactNode } from "react";
import { EmptyFigure } from "@/components/company/cells";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Delta } from "@/components/data/Delta";
import { StateNote } from "@/components/data/StateNote";
import { changeOf } from "@/lib/delta";
import {
  DIRECTION_LABEL,
  bankLabel,
  decisionLabel,
  type CycleRow,
  type StatRow,
} from "@/lib/equity/rate";
import { formatCount, formatDate, formatFraction, formatPctChange, formatTableDate } from "@/lib/format";
import type { RateDecisionMoveRow } from "@/lib/queries/api";

type Num = number | null;
import type { PeerGroupMembers } from "@/lib/queries/rate";

/** A stored percentage-point move as a `Delta`; a missing one is a labelled dash. */
export function Move({ value }: { value: Num }) {
  return value === null ? <EmptyFigure state="missing" /> : <Delta change={changeOf(formatPctChange(value))} />;
}

const FEW_LISTED = "Fewer than 5 decisions: no statistics, the moves are listed instead.";
const FEW_ONLY = "Fewer than 5 decisions: no statistics.";

/** A statistic of a group with too few moves: a dash that says why. */
function Few({ listed = true }: { listed?: boolean }) {
  const why = listed ? FEW_LISTED : FEW_ONLY;
  return (
    <span title={why}>
      <span aria-hidden>—</span>
      <span className="sr-only">{why}</span>
    </span>
  );
}

const stat = (r: StatRow, value: Num, listed = true): ReactNode => (r.fewMoves ? <Few listed={listed} /> : <Move value={value} />);

const decisionCell = (r: { bank: StatRow["bank"]; decision: StatRow["decision"] }): ReactNode => (
  <span className="text-ink-1">
    {bankLabel(r.bank)} · {decisionLabel(r.decision)}
  </span>
);

const middleHalf = (r: StatRow): ReactNode =>
  r.fewMoves || r.stat.p25_excess_pct === null || r.stat.p75_excess_pct === null ? (
    <Few />
  ) : (
    `${formatPctChange(r.stat.p25_excess_pct)} to ${formatPctChange(r.stat.p75_excess_pct)}`
  );

/** The company's bank × decision table: n, median, mean, the middle half and the share positive, for one window. */
export function RateStatsTable({ rows, caption }: { rows: StatRow[]; caption: string }) {
  const columns: Column<StatRow>[] = [
    { id: "decision", header: "Decision", cell: decisionCell },
    { id: "n", header: "Decisions (n)", align: "right", cell: (r) => formatCount(r.n), sortValue: (r) => r.n },
    { id: "median", header: "Median excess (%)", align: "right", cell: (r) => stat(r, r.stat.median_excess_pct) },
    { id: "mean", header: "Mean excess (%)", align: "right", cell: (r) => stat(r, r.stat.mean_excess_pct) },
    { id: "range", header: "Middle half (%)", align: "right", cell: middleHalf },
    {
      id: "positive",
      header: "Share with a positive excess (%)",
      align: "right",
      cell: (r) => (r.fewMoves ? <Few /> : formatFraction(r.stat.share_positive)),
    },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption={caption} empty={<StateNote kind="empty">No decisions in this window.</StateNote>} />;
}

/** The individual moves of the groups with fewer than five decisions (date, decision, the stock's move, the Nifty 50's, the excess). */
export function RateMovesTable({ rows, caption }: { rows: RateDecisionMoveRow[]; caption: string }) {
  const columns: Column<RateDecisionMoveRow>[] = [
    {
      id: "date",
      header: "Decision date",
      cell: (r) => formatTableDate(r.meeting_date),
      sortValue: (r) => r.meeting_date,
    },
    { id: "decision", header: "Decision", cell: decisionCell },
    { id: "own", header: "Stock (%)", align: "right", cell: (r) => <Move value={r.own_change_pct} /> },
    { id: "nifty", header: "Nifty 50 (%)", align: "right", cell: (r) => <Move value={r.nifty_change_pct} /> },
    { id: "excess", header: "Excess (%)", align: "right", cell: (r) => <Move value={r.excess_pct} /> },
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.event_slug}:${r.window_key}`}
      caption={caption}
      initialSort={{ id: "date", desc: true }}
      dense
      empty={<StateNote kind="empty">No moves to list.</StateNote>}
    />
  );
}

/** A cycle's direction in words, with "ongoing" labelled. */
const directionCell = (r: CycleRow): ReactNode => (
  <span className="text-ink-1">
    {DIRECTION_LABEL[r.direction]}
    {r.ongoing ? <span className="ml-1.5 text-ink-2">(ongoing)</span> : null}
  </span>
);

const cycleDates = (r: CycleRow): string =>
  r.firstOn && r.lastOn ? `${formatDate(r.firstOn)} to ${formatDate(r.lastOn)}` : `${formatDate(r.move.pre_date)} to ${formatDate(r.move.post_date)}`;

/** The company's move over each rate cycle: the stock's, the Nifty 50's and the excess. */
export function RateCyclesTable({ rows, caption }: { rows: CycleRow[]; caption: string }) {
  const columns: Column<CycleRow>[] = [
    { id: "bank", header: "Bank", cell: (r) => <span className="text-ink-1">{bankLabel(r.bank)}</span> },
    { id: "direction", header: "Cycle", cell: directionCell },
    { id: "dates", header: "Decisions", cell: cycleDates },
    { id: "moves", header: "Moves (n)", align: "right", cell: (r) => (r.moves === null ? <EmptyFigure state="missing" /> : formatCount(r.moves)) },
    { id: "own", header: "Stock (%)", align: "right", cell: (r) => <Move value={r.move.own_change_pct} /> },
    { id: "nifty", header: "Nifty 50 (%)", align: "right", cell: (r) => <Move value={r.move.nifty_change_pct} /> },
    { id: "excess", header: "Excess (%)", align: "right", cell: (r) => <Move value={r.move.excess_pct} /> },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption={caption} empty={<StateNote kind="empty">No rate cycles are published yet.</StateNote>} />;
}

/** One row of the sector view: a peer group's stored statistics for one bank × decision. */
export type SectorRow = StatRow & { group: PeerGroupMembers | null };

/** The sector view: peer group × bank × decision, the median excess with n, each group linked to its members' pages. */
export function RateSectorTable({ rows, caption }: { rows: SectorRow[]; caption: string }) {
  const columns: Column<SectorRow>[] = [
    {
      id: "group",
      header: "Peer group",
      sortValue: (r) => r.subjectName,
      cell: (r) => (
        <span>
          <span className="text-ink-1">{r.subjectName}</span>
          {r.group && r.group.members.length > 0 && (
            <span className="ml-2 text-meta text-ink-2">
              {r.group.members.map((m, i) => (
                <span key={m.slug}>
                  {i > 0 && ", "}
                  <Link href={`/companies/${m.slug}?tab=rate-decisions`} className="underline hover:text-ink-1">
                    {m.name}
                  </Link>
                </span>
              ))}
            </span>
          )}
        </span>
      ),
    },
    { id: "decision", header: "Decision", cell: decisionCell },
    { id: "n", header: "Decisions (n)", align: "right", sortValue: (r) => r.n, cell: (r) => formatCount(r.n) },
    { id: "median", header: "Median excess (%)", align: "right", sortValue: (r) => r.stat.median_excess_pct, cell: (r) => stat(r, r.stat.median_excess_pct, false) },
    { id: "positive", header: "Share with a positive excess (%)", align: "right", cell: (r) => (r.fewMoves ? <Few listed={false} /> : formatFraction(r.stat.share_positive)) },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption={caption} scroll empty={<StateNote kind="empty">No peer group has decision-day moves yet.</StateNote>} />;
}
