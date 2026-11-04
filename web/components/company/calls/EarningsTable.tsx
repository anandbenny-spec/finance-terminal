"use client";

// The /earnings table (2B-20, #183; Spec 02B §13.7): a row per Nifty 50 company with its newest published call. Cells are text
// api.earnings_latest returned, worded by lib/equity/calls.ts; the first cell is the company's link to its Earnings calls tab.
// Sortable by company and call date, newest call first by default. No colour: a call's status is words.
import Link from "next/link";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import { listDateKey, listDateText, listStatusText, noTranscriptNote } from "@/lib/equity/calls";
import type { EarningsListRow } from "@/lib/queries/api";

export function callsTabHref(companySlug: string): string {
  return "/companies/" + companySlug + "?tab=calls";
}

export function EarningsTable({ rows }: { rows: EarningsListRow[] }) {
  const columns: Column<EarningsListRow>[] = [
    {
      id: "company",
      header: "Company",
      cell: (r) => (
        <Link href={callsTabHref(r.company_slug)} className="text-ink-1 underline">
          {r.short_name ?? r.company_name}
        </Link>
      ),
      sortValue: (r) => (r.short_name ?? r.company_name).toLowerCase(),
    },
    { id: "quarter", header: "Quarter", cell: (r) => r.fiscal_label ?? "Not available" },
    { id: "date", header: "Call date", cell: (r) => listDateText(r) ?? "Not available", sortValue: (r) => listDateKey(r) },
    {
      id: "status",
      header: "Status",
      cell: (r) => {
        const note = noTranscriptNote(r);
        return (
          <>
            <span>{listStatusText(r)}</span>
            {note && <span className="block text-meta text-ink-3">{note}</span>}
          </>
        );
      },
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.company_slug}
      caption="Nifty 50 companies: the latest earnings call of each"
      initialSort={{ id: "date", desc: true }}
      scroll
      focusable
      empty={<StateNote kind="empty">No companies to list yet.</StateNote>}
    />
  );
}
