"use client";

// "Corporate actions" (2A-19; Spec 02A §8; the note from #216): the company's splits, bonus issues, dividends and the rest
// from `api.company_corporate_actions`. Announced actions whose ex-date is still ahead are listed apart ("Announced"); with
// none, the panel says so. Each action's effect on earlier prices is told in words: applied, under review (with the stored
// reason), or no adjustment. The subject is the exchange's own wording, quoted as filed. All dates and amounts go through
// lib/format.ts.
import { useMemo, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { FACTOR_WORDS, KIND_LABEL } from "@/lib/equity/company";
import { NOT_AVAILABLE, NOT_AVAILABLE_LABEL, formatPrice, formatTableDate } from "@/lib/format";
import type { CorporateActionRow } from "@/lib/queries/api";

const key = (a: CorporateActionRow) => `${a.kind}|${a.ex_date}|${a.subject ?? ""}|${a.exchange}`;

function columns(upcoming: boolean): Column<CorporateActionRow>[] {
  return [
    {
      id: "ex",
      header: "Ex-date",
      cell: (a) => (a.ex_date ? <span className="font-mono">{formatTableDate(a.ex_date)}</span> : <span title={NOT_AVAILABLE_LABEL}>{NOT_AVAILABLE}</span>),
      sortValue: (a) => a.ex_date,
    },
    { id: "kind", header: "Kind", cell: (a) => KIND_LABEL[a.kind] ?? a.kind, sortValue: (a) => a.kind },
    {
      id: "subject",
      header: "As filed",
      cell: (a) =>
        a.source_url ? (
          <a href={a.source_url} target="_blank" rel="noopener noreferrer" className="text-ink-1 underline hover:text-ink-2">
            {a.subject ?? a.title ?? "Filing"}
          </a>
        ) : (
          (a.subject ?? a.title ?? NOT_AVAILABLE)
        ),
    },
    {
      id: "amount",
      header: "Dividend per share",
      align: "right",
      cell: (a) => (a.dividend_per_share === null ? <span title="Not a dividend">—</span> : formatPrice(a.dividend_per_share, a.currency)),
      sortValue: (a) => a.dividend_per_share,
    },
    {
      id: "effect",
      header: upcoming ? "Status" : "Effect on earlier prices",
      cell: (a) =>
        upcoming ? (
          "Announced"
        ) : (
          <span title={a.factor_status === "held" ? (a.factor_reason ?? undefined) : undefined}>
            {FACTOR_WORDS[a.factor_status]}
            {a.factor_status === "held" && a.factor_reason ? <span className="sr-only">: {a.factor_reason}</span> : null}
          </span>
        ),
    },
  ];
}

export function ActionsPanel({ upcoming, past, footer }: { upcoming: CorporateActionRow[]; past: CorporateActionRow[]; footer: ReactNode }) {
  const upcomingColumns = useMemo(() => columns(true), []);
  const pastColumns = useMemo(() => columns(false), []);
  return (
    <Panel
      title="Corporate actions"
      titleId="panel-actions"
      subtitle="What splits, bonus issues and dividends has the company announced, and which adjust earlier prices?"
      variant="flush"
      footer={footer}
    >
      <div className="border-b border-line px-3 py-2 text-data font-semibold text-ink-1">Upcoming</div>
      {upcoming.length === 0 ? (
        <div className="px-3 py-2">
          <StateNote kind="empty">No corporate action is announced with an ex-date ahead.</StateNote>
        </div>
      ) : (
        <DataTable
          columns={upcomingColumns}
          rows={upcoming}
          rowKey={key}
          caption="Announced corporate actions whose ex-date is ahead, soonest first"
          initialSort={{ id: "ex", desc: false }}
          dense
          empty={<StateNote kind="empty">Nothing to show.</StateNote>}
        />
      )}
      <div className="border-y border-line px-3 py-2 text-data font-semibold text-ink-1">Past</div>
      {past.length === 0 ? (
        <div className="px-3 py-2">
          <StateNote kind="unavailable">No past corporate actions are stored for this company yet.</StateNote>
        </div>
      ) : (
        <DataTable
          columns={pastColumns}
          rows={past}
          rowKey={key}
          caption="Past corporate actions, newest first, with their effect on earlier prices"
          initialSort={{ id: "ex", desc: true }}
          dense
          scroll
          empty={<StateNote kind="empty">Nothing to show.</StateNote>}
        />
      )}
    </Panel>
  );
}
