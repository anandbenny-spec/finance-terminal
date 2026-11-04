"use client";

// The tables of the Earnings calls tab (2B-19, #182; Spec 02B §13.7): the company's calls, and the figures of the selected call
// (guidance given, figures management stated, and what speakers of unread role said). Every cell is text a view returned,
// formatted by lib/format.ts through lib/equity/calls.ts; the quotes are verified citations' (CitationChip), never a
// transcript's text. No colour: nothing here is a market move, and a call's status is words.
import Link from "next/link";
import { CitationChip } from "@/components/ai/CitationChip";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import { NOT_AVAILABLE } from "@/lib/format";
import { callDateText, statusText, type FigureRow } from "@/lib/equity/calls";
import type { GuidanceRow } from "@/lib/equity/guidance";
import type { EarningsCallRow } from "@/lib/queries/api";

export function callHref(companySlug: string, callSlug: string): string {
  return `/companies/${companySlug}?tab=calls&call=${callSlug}`;
}

export function PageLink({ page }: { page: NonNullable<FigureRow["page"]> }) {
  return (
    <a href={page.url} title={page.title} className="text-ink-1 underline" rel="noreferrer" target="_blank">
      {page.label} ↗
    </a>
  );
}

/** The company's published calls, newest first; a row's first cell is a real link that selects the call. */
export function CallsTable({ rows, companySlug, selected, caption }: { rows: EarningsCallRow[]; companySlug: string; selected: string | null; caption: string }) {
  const columns: Column<EarningsCallRow>[] = [
    {
      id: "quarter",
      header: "Quarter",
      cell: (r) => (
        <>
          <Link href={callHref(companySlug, r.call_slug)} aria-current={r.call_slug === selected ? "true" : undefined} className="text-ink-1 underline">
            {r.fiscal_label}
          </Link>
          {r.call_slug === selected && <span className="ml-2 text-meta text-ink-3">Shown below</span>}
        </>
      ),
    },
    { id: "date", header: "Call date", cell: (r) => callDateText(r) },
    { id: "status", header: "Status", cell: (r) => statusText(r) },
    {
      id: "source",
      header: "Official source",
      cell: (r) =>
        r.official_url?.startsWith("https://") ? (
          <a href={r.official_url} className="text-ink-1 underline" rel="noreferrer" target="_blank">
            Official source ↗
          </a>
        ) : (
          <span className="text-ink-3">Not linked</span>
        ),
    },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.call_slug} caption={caption} focusable empty={<StateNote kind="empty">No calls are published for this company yet.</StateNote>} />;
}

/** A figures table: metric · figure as stated · period · basis · quote chip · page link. */
export function FiguresTable({ rows, caption, figureHeader, empty }: { rows: FigureRow[]; caption: string; figureHeader: string; empty: string }) {
  const columns: Column<FigureRow>[] = [
    { id: "metric", header: "Metric", cell: (r) => r.metric },
    { id: "figure", header: figureHeader, cell: (r) => r.figure },
    { id: "period", header: "Period", cell: (r) => r.period },
    { id: "basis", header: "Basis", cell: (r) => r.basis },
    { id: "quote", header: "Quote", cell: (r) => (r.citation ? <CitationChip citation={r.citation} /> : <span className="text-ink-3">{NOT_AVAILABLE}</span>) },
    { id: "page", header: "Page", cell: (r) => (r.page ? <PageLink page={r.page} /> : <span className="text-ink-3">{NOT_AVAILABLE}</span>) },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption={caption} focusable empty={<StateNote kind="empty">{empty}</StateNote>} />;
}

/** The guidance record's table (Spec 02B §8; #267): metric · figure as stated · period · basis · first stated · latest · actual · outcome (words) · in the rate · quote chip · page. */
export function GuidanceTable({ rows, caption }: { rows: GuidanceRow[]; caption: string }) {
  const columns: Column<GuidanceRow>[] = [
    { id: "metric", header: "Metric", cell: (r) => r.metric },
    { id: "figure", header: "Figure as stated", cell: (r) => r.figure },
    { id: "period", header: "Period", cell: (r) => r.period },
    { id: "basis", header: "Basis", cell: (r) => r.basis },
    { id: "first", header: "First stated", cell: (r) => r.firstStated },
    { id: "latest", header: "Latest statement", cell: (r) => r.latest },
    { id: "actual", header: "Actual", cell: (r) => r.actual },
    { id: "outcome", header: "Outcome", cell: (r) => r.outcome },
    { id: "rate", header: "In the rate?", cell: (r) => r.inRate },
    { id: "quote", header: "Quote", cell: (r) => (r.citation ? <CitationChip citation={r.citation} /> : <span className="text-ink-3">{NOT_AVAILABLE}</span>) },
    { id: "page", header: "Page", cell: (r) => (r.page ? <PageLink page={r.page} /> : <span className="text-ink-3">{NOT_AVAILABLE}</span>) },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption={caption} focusable empty={<StateNote kind="empty">No targets in this group</StateNote>} />;
}
