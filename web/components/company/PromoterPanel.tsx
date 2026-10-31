"use client";

// "Promoter holding" (2A-19; #116, Anand's decision of 1 Nov 2026): the promoters' share per quarter from
// `api.company_promoter_holding`, as stored. A quarter whose filing gave the figure in percent where a fraction is
// expected carries a "derived" label and a note in words: what was filed, what is shown, and what confirmed it (the
// filing's own share counts and NSE's figure where NSE lists one), linked to the filing. A quarter with no stored figure
// is held back, never guessed. All numbers go through lib/format.ts.
import { useMemo, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { EmptyFigure } from "@/components/company/cells";
import { formatDate, formatPercentLevel, formatShare, formatTableDate, type Market } from "@/lib/format";
import type { PromoterHoldingRow } from "@/lib/queries/api";

function Filing({ row, children }: { row: PromoterHoldingRow; children: ReactNode }) {
  if (!row.source_url) return <>{children}</>;
  return (
    <a href={row.source_url} target="_blank" rel="noopener noreferrer" className="text-ink-1 underline hover:text-ink-2">
      {children}
    </a>
  );
}

function note(row: PromoterHoldingRow, market: Market): ReactNode {
  if (row.correction !== "percent_for_fraction" || row.filed_pct === null || row.share_ratio_pct === null) return null;
  const nse = row.nse_pct !== null ? ` and NSE's figure (${formatPercentLevel(row.nse_pct, market)})` : "";
  return (
    <Filing row={row}>
      Filed as {formatPercentLevel(row.filed_pct, market, 0)}, shown as {formatPercentLevel(row.promoter_pct, market)}; the filing&apos;s own share
      counts give {formatPercentLevel(row.share_ratio_pct, market)}
      {nse}
    </Filing>
  );
}

export function PromoterPanel({ rows, market, footer }: { rows: PromoterHoldingRow[] | null; market: Market; footer: ReactNode }) {
  const shown = useMemo(() => (rows ?? []).slice(0, 8), [rows]); // newest first, the last 8 quarters
  const columns = useMemo<Column<PromoterHoldingRow>[]>(
    () => [
      { id: "as_of", header: "As of", cell: (r) => <span className="font-mono">{formatTableDate(r.as_of)}</span>, sortValue: (r) => r.as_of },
      {
        id: "pct",
        header: "Promoter holding (%)",
        align: "right",
        cell: (r) =>
          r.promoter_pct === null ? (
            <EmptyFigure state="held" reason="No promoter holding is stored for this quarter." />
          ) : (
            <span>
              {formatShare(r.promoter_pct, 2)} {r.label_kind && <Qualifier>{r.label_kind}</Qualifier>}
            </span>
          ),
        sortValue: (r) => r.promoter_pct,
      },
      { id: "filed", header: "Filed", cell: (r) => (r.filed_at ? <Filing row={r}>{formatDate(r.filed_at.slice(0, 10))}</Filing> : "—") },
      { id: "note", header: "Note", cell: (r) => note(r, market) ?? "" },
    ],
    [market],
  );
  return (
    <Panel
      title="Promoter holding"
      titleId="panel-promoter"
      subtitle="How much of the company do its promoters hold, quarter by quarter?"
      market={market}
      variant="flush"
      footer={footer}
    >
      {rows === null ? (
        <div className="p-3">
          <StateNote kind="unavailable">Promoter holding isn&apos;t published yet.</StateNote>
        </div>
      ) : shown.length === 0 ? (
        <div className="p-3">
          <StateNote kind="unavailable">No shareholding pattern has been published for this company yet.</StateNote>
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={shown}
          rowKey={(r) => r.as_of}
          caption="Promoter and promoter-group holding at the end of each of the last eight quarters, newest first"
          initialSort={{ id: "as_of", desc: true }}
          dense
          empty={<StateNote kind="empty">Nothing to show.</StateNote>}
        />
      )}
    </Panel>
  );
}
