"use client";

// "Price, excluding dividends" (2A-19; Spec 02A §8, Overview): the company's adjusted close (split-, bonus- and
// consolidation-adjusted; dividends are not added back), one line in the company's market colour, with range presets and
// "View as table" (the raw close beside the adjusted one). Earlier prices held back behind a corporate action that hasn't
// been checked are not drawn; a StateNote names the date they are held back to. The legend row is the crosshair readout.
import dynamic from "next/dynamic";
import { ChartLine, Table2 } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import type { ChartSeries } from "@/components/data/TimeSeriesChart";
import type { PricePoint } from "@/lib/equity/company";
import { NOT_AVAILABLE, formatDate, formatPrice, formatTableDate, type Currency } from "@/lib/format";
import { yearsBefore } from "@/lib/policy/rates";
import type { CompanyPriceRow } from "@/lib/queries/api";

const TimeSeriesChart = dynamic(() => import("@/components/data/TimeSeriesChart"), {
  ssr: false,
  loading: () => <div className="h-80 w-full" aria-hidden />,
});

const PRESETS = [
  { id: "1y", label: "1Y", years: 1 },
  { id: "3y", label: "3Y", years: 3 },
  { id: "5y", label: "5Y", years: 5 },
  { id: "all", label: "All", years: null },
] as const;

export function PriceChartPanel({
  points,
  rows,
  heldTo,
  currency,
  market,
  name,
  footer,
}: {
  points: PricePoint[];
  /** Every session, newest first. */
  rows: CompanyPriceRow[];
  heldTo: string | null;
  currency: Currency;
  market: "IN" | "US";
  name: string;
  footer: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  const [preset, setPreset] = useState<(typeof PRESETS)[number]["id"]>("3y");
  const [rangeKey, setRangeKey] = useState(0);
  const [hovered, setHovered] = useState<string | null>(null);
  const onHover = useCallback((d: string | null) => setHovered(d), []);

  const series = useMemo<ChartSeries[]>(() => [{ id: "adj-close", kind: "line", entity: market, data: points }], [points, market]);
  const byDate = useMemo(() => new Map(points.map((p) => [p.time, p.value])), [points]);
  const last = points.at(-1)?.time ?? null;
  const p = PRESETS.find((x) => x.id === preset)!;
  const visibleFrom = last && p.years !== null ? yearsBefore(last, p.years) : undefined;
  const readDate = hovered ?? last;
  const readValue = readDate ? byDate.get(readDate) ?? null : null;

  const columns = useMemo<Column<CompanyPriceRow>[]>(
    () => [
      { id: "date", header: "Date", cell: (r) => <span className="font-mono">{formatTableDate(r.trade_date)}</span>, sortValue: (r) => r.trade_date },
      { id: "close", header: `Close (${currency})`, align: "right", cell: (r) => formatPrice(r.close, currency), sortValue: (r) => r.close },
      {
        id: "adj",
        header: `Adjusted close (${currency})`,
        align: "right",
        cell: (r) => (r.adj_close === null ? <span title="Held back while a corporate action is checked">Held</span> : formatPrice(r.adj_close, currency)),
        sortValue: (r) => r.adj_close,
      },
    ],
    [currency],
  );

  const empty = points.length === 0;
  const toggle = (
    <button
      type="button"
      onClick={() => setAsTable((v) => !v)}
      aria-pressed={asTable}
      aria-label={asTable ? "View as chart" : "View as table"}
      title={asTable ? "View as chart" : "View as table"}
      className="grid size-7 place-items-center rounded-md text-ink-2 hover:bg-active hover:text-ink-1"
    >
      {asTable ? <ChartLine aria-hidden className="size-4" /> : <Table2 aria-hidden className="size-4" />}
    </button>
  );

  return (
    <Panel
      title="Price, excluding dividends"
      titleId="panel-price"
      subtitle="How has the share price moved? Closing prices adjusted for splits and bonus issues; dividends are not added back."
      market={market}
      actions={rows.length === 0 ? undefined : toggle}
      variant="flush"
      footer={footer}
    >
      {heldTo && (
        <div className="border-b border-line px-3 py-2">
          <StateNote kind="held">
            Prices up to {formatDate(heldTo)} are held back while a corporate action is checked.
          </StateNote>
        </div>
      )}
      {rows.length === 0 ? (
        <div className="p-3">
          <StateNote kind="unavailable">No prices are published for this company yet.</StateNote>
        </div>
      ) : asTable ? (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.trade_date}
          caption={`${name}: closing price and adjusted closing price on each session, newest first`}
          initialSort={{ id: "date", desc: true }}
          dense
          scroll
          empty={<StateNote kind="empty">No prices in this range.</StateNote>}
        />
      ) : empty ? (
        <div className="p-3">
          <StateNote kind="held">Every price is held back while a corporate action is checked.</StateNote>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 pt-2.5">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-data text-ink-2" aria-live="off">
              {readDate && (
                <>
                  <span className="font-mono text-ink-3">{formatTableDate(readDate)}</span>
                  <span className="inline-flex items-center gap-1.5">
                    <i aria-hidden className={`inline-block h-0.5 w-3.5 ${market === "US" ? "bg-series-1" : "bg-series-2"}`} />
                    <b className="font-semibold text-ink-1">{readValue === null ? NOT_AVAILABLE : formatPrice(readValue, currency)}</b> adjusted close
                    <Qualifier>excluding dividends</Qualifier>
                  </span>
                </>
              )}
            </div>
            <div role="group" aria-label="Range" className="ml-auto flex overflow-hidden rounded-md border border-line">
              {PRESETS.map((x) => (
                <button
                  key={x.id}
                  type="button"
                  aria-pressed={preset === x.id}
                  onClick={() => {
                    setPreset(x.id);
                    setRangeKey((k) => k + 1);
                  }}
                  className="border-r border-line px-2.5 py-1 text-meta text-ink-2 last:border-r-0 hover:text-ink-1 aria-pressed:bg-active aria-pressed:text-ink-1"
                >
                  {x.label}
                </button>
              ))}
            </div>
          </div>
          <div className="px-3 pt-2 pb-1">
            <TimeSeriesChart
              series={series}
              visibleFrom={visibleFrom}
              rangeKey={rangeKey}
              onHover={onHover}
              label={`${name}: adjusted closing price, excluding dividends. View as table for the values.`}
            />
          </div>
        </>
      )}
    </Panel>
  );
}
