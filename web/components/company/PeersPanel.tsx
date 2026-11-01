"use client";

// "Peers" (2A-19; Spec 02A §5.11, §8): the company's curated peer group in the same market, each peer's multiples and
// returns from the latest weekly snapshot, and the median of the other members when there are at least three (else the
// stored note, "Too few peers in the Nifty 50 for a median", with the peers still listed). Every figure is a stored value
// from `api.peer_comps`; a peer with no value shows a dash labelled "Not available". For a lender or an insurer the EV
// columns read N/A (Brief §6.3). Comps stay within one market and one currency (CLAUDE.md §1).
import Link from "next/link";
import { useMemo, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { EmptyFigure } from "@/components/company/cells";
import { formatCount, formatDate, formatFraction, formatMultiple, type Market } from "@/lib/format";
import type { PeerCompRow } from "@/lib/queries/api";

type Figure = "pe_ttm" | "pb" | "ev_ebit" | "ev_sales" | "roe" | "ebit_margin" | "revenue_cagr_3y";

const NOT_FOR_LENDERS = "Not meaningful for a lender or an insurer.";

export function PeersPanel({
  peers,
  market,
  peerGroup,
  lender,
  footer,
}: {
  peers: PeerCompRow[];
  market: Market;
  peerGroup: string | null;
  lender: boolean;
  footer: ReactNode;
}) {
  const rows = useMemo(() => {
    const list = peers.filter((p) => p.row_kind === "peer").sort((a, b) => (a.peer_name ?? "").localeCompare(b.peer_name ?? ""));
    const median = peers.find((p) => p.row_kind === "median");
    return median ? [...list, median] : list;
  }, [peers]);
  const median = rows.find((r) => r.row_kind === "median") ?? null;
  const asOf = rows.map((r) => r.snapshot_date).filter((d): d is string => Boolean(d)).sort().at(-1) ?? null;

  const figure = (key: Figure, format: (v: number) => string, evColumn = false): ((r: PeerCompRow) => ReactNode) => {
    function cell(r: PeerCompRow): ReactNode {
      if (evColumn && lender) return <EmptyFigure state="not_applicable" reason={NOT_FOR_LENDERS} />;
      const v = r[key];
      return v === null ? <EmptyFigure state="missing" /> : format(v);
    }
    return cell;
  };

  const columns: Column<PeerCompRow>[] = [
    {
      id: "company",
      header: "Company",
      cell: (r) =>
        r.row_kind === "median" ? (
          <span className="font-semibold text-ink-1">
            Median of the other members{r.peer_count !== null ? ` (${formatCount(r.peer_count)})` : ""}
          </span>
        ) : (
          <Link href={`/companies/${r.peer_slug}`} className="text-ink-1 underline hover:text-ink-2">
            {r.peer_short_name ?? r.peer_name}
          </Link>
        ),
    },
    { id: "pe", header: "P/E (×)", align: "right", cell: figure("pe_ttm", (v) => formatMultiple(v, market)) },
    { id: "pb", header: "P/B (×)", align: "right", cell: figure("pb", (v) => formatMultiple(v, market)) },
    { id: "ev_ebit", header: "EV/EBIT (×)", align: "right", cell: figure("ev_ebit", (v) => formatMultiple(v, market), true) },
    { id: "ev_sales", header: "EV/sales (×)", align: "right", cell: figure("ev_sales", (v) => formatMultiple(v, market), true) },
    { id: "roe", header: "ROE (%)", align: "right", cell: figure("roe", (v) => formatFraction(v)) },
    { id: "ebit_margin", header: "EBIT margin (%)", align: "right", cell: figure("ebit_margin", (v) => formatFraction(v), true) },
    { id: "cagr", header: "Revenue CAGR, 3 years (%)", align: "right", cell: figure("revenue_cagr_3y", (v) => formatFraction(v)) },
  ];

  return (
    <Panel
      title="Peers"
      titleId="panel-peers"
      subtitle={
        peerGroup
          ? `How do the multiples compare with the others in ${peerGroup}${asOf ? `, as of the ${formatDate(asOf)} snapshot` : ""}?`
          : "How do the multiples compare with the company's peer group?"
      }
      qualifier={peerGroup ? <Qualifier>curated</Qualifier> : undefined}
      market={market}
      variant="flush"
      footer={footer}
    >
      {rows.length === 0 ? (
        <div className="p-3">
          <StateNote kind="unavailable">
            {peerGroup ? "No peers in this group have a published snapshot yet." : "No peer group is published for this company."}
          </StateNote>
        </div>
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(r) => r.peer_slug ?? "median"}
            caption={`Peers in ${peerGroup ?? "the peer group"} and the median of the other members`}
            dense
            scroll
            empty={<StateNote kind="empty">No peers to show.</StateNote>}
          />
          {median?.median_note && (
            <div className="border-t border-line px-3 py-2">
              <StateNote kind="empty">{median.median_note}.</StateNote>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
