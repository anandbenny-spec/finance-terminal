"use client";

// "Long rates: US vs India 10-year" (Spec 01 §4.2; Pass 1b, #71): the US 10-year Treasury yield, daily (cyan), and
// India's 10-year government bond yield, monthly (violet, labelled "monthly"), on one axis, with range presets and
// "View as table". Both series are read whole (readDailySeries: N2). The legend row is the crosshair readout. It mixes
// both markets, so no market edge (Design 01 §1.15).
import dynamic from "next/dynamic";
import { Table2, ChartLine } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import type { ChartSeries } from "@/components/data/TimeSeriesChart";
import { NOT_AVAILABLE, formatAxisNumber, formatMonth, formatTableDate, formatYield } from "@/lib/format";
import { buildLongRates, readoutAt, type LongRatesRow } from "@/lib/policy/longrates";
import { yearsBefore } from "@/lib/policy/rates";
import type { ObservationRow } from "@/lib/queries/api";

const TimeSeriesChart = dynamic(() => import("@/components/data/TimeSeriesChart"), {
  ssr: false,
  loading: () => <div className="h-80 w-full" aria-hidden />,
});

const PRESETS = [
  { id: "1y", label: "1Y", years: 1 },
  { id: "3y", label: "3Y", years: 3 },
  { id: "5y", label: "5Y", years: 5 },
  { id: "all", label: "Since 2019", years: null },
] as const;

const COLUMNS: Column<LongRatesRow>[] = [
  { id: "date", header: "Date", cell: (r) => <span className="font-mono">{formatTableDate(r.date)}</span>, sortValue: (r) => r.date },
  { id: "us", header: "US 10-year, daily (%)", align: "right", cell: (r) => formatAxisNumber(r.us), sortValue: (r) => r.us },
  { id: "india", header: "India 10-year, monthly (%)", align: "right", cell: (r) => formatAxisNumber(r.india), sortValue: (r) => r.india },
];

function Key({ series, children }: { series: "1" | "2"; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i aria-hidden className={`inline-block h-0.5 w-3.5 ${series === "1" ? "bg-series-1" : "bg-series-2"}`} />
      {children}
    </span>
  );
}

export function LongRatesPanel({
  us,
  india,
  from,
  footer,
  refresh,
}: {
  us: ObservationRow[];
  india: ObservationRow[];
  from: string;
  footer: ReactNode;
  refresh?: readonly string[];
}) {
  const data = useMemo(() => buildLongRates(us, india, from), [us, india, from]);
  const [asTable, setAsTable] = useState(false);
  const [preset, setPreset] = useState<(typeof PRESETS)[number]["id"]>("all");
  const [hovered, setHovered] = useState<string | null>(null);
  const onHover = useCallback((d: string | null) => setHovered(d), []);

  const series = useMemo<ChartSeries[]>(
    () => [
      { id: "us-10y", kind: "line", entity: "fed", data: data.us },
      { id: "in-10y", kind: "line", entity: "rbi", data: data.india },
    ],
    [data],
  );
  const p = PRESETS.find((x) => x.id === preset)!;
  const last = [data.usTo, data.indiaTo].filter((d): d is string => d !== null).sort().at(-1) ?? null;
  const visibleFrom = last && p.years !== null ? yearsBefore(last, p.years) : undefined;
  const read = readoutAt(data, hovered);
  const empty = data.rows.length === 0;

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
      title="Long rates: US vs India 10-year"
      titleId="panel-long-rates"
      subtitle="How have long-term yields moved in each market? The US yield is daily; India's is one reading a month."
      actions={empty ? undefined : toggle}
      variant="flush"
      footer={footer}
      refresh={refresh}
    >
      {empty ? (
        <div className="p-3">
          <StateNote kind="unavailable">No long-term yields are published yet.</StateNote>
        </div>
      ) : asTable ? (
        <DataTable
          columns={COLUMNS}
          rows={data.rows}
          rowKey={(r) => r.date}
          caption="US 10-year yield (daily) and India 10-year yield (monthly) on each date either has a value, newest first"
          initialSort={{ id: "date", desc: true }}
          dense
          scroll
          empty={<StateNote kind="empty">No yields in this range.</StateNote>}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 pt-2.5">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-data text-ink-2" aria-live="off">
              {read && (
                <>
                  <span className="font-mono text-ink-3">{formatTableDate(read.date)}</span>
                  <Key series="1">
                    <b className="font-semibold text-ink-1">{read.us === null ? NOT_AVAILABLE : formatYield(read.us)}</b> US 10-year
                  </Key>
                  <Key series="2">
                    <b className="font-semibold text-ink-1">{read.india === null ? NOT_AVAILABLE : formatYield(read.india.value)}</b> India 10-year
                    <Qualifier>monthly{read.india ? `, ${formatMonth(read.india.time)}` : ""}</Qualifier>
                  </Key>
                </>
              )}
            </div>
            <div role="group" aria-label="Range" className="ml-auto flex overflow-hidden rounded-md border border-line">
              {PRESETS.map((x) => (
                <button
                  key={x.id}
                  type="button"
                  aria-pressed={preset === x.id}
                  onClick={() => setPreset(x.id)}
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
              onHover={onHover}
              label="Long rates since 2019: the US 10-year Treasury yield, daily, and India's 10-year government bond yield, monthly. View as table for the values."
            />
          </div>
        </>
      )}
    </Panel>
  );
}
