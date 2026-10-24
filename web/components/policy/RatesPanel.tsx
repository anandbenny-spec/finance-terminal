"use client";

// "Policy rates since 2019" (Spec 01 §4.2; Design 01 §2.3, §4.4): the Fed's target range as a cyan band (FRED
// DFEDTARU/L from api.observation_steps: N2), the RBI repo rate as a violet step and its corridor as a violet band
// (qualifier "as set at MPC meetings": D5), decision markers, range presets and "View as table". The legend row is
// the crosshair readout. Mixes both markets, so no market edge (Design 01 §1.15).
import dynamic from "next/dynamic";
import { Table2, ChartLine } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import type { ChartMarker, ChartSeries } from "@/components/data/TimeSeriesChart";
import { NOT_APPLICABLE, formatAxisNumber, formatAxisRange, formatRate, formatRateRange, formatTableDate } from "@/lib/format";
import { buildRateChart, dayAt, yearsBefore, type RateDay } from "@/lib/policy/rates";
import type { PolicyMeetingRow, PolicyRateSettingRow } from "@/lib/queries/api";
import type { StepSeries } from "@/lib/queries/policy";

const TimeSeriesChart = dynamic(() => import("@/components/data/TimeSeriesChart"), {
  ssr: false,
  loading: () => <div className="h-80 w-full" aria-hidden />,
});

/** The day the SDF replaced the reverse repo as the corridor's floor (Spec 01 §2). */
const SDF_FROM = "2022-04-08";

const PRESETS = [
  { id: "1y", label: "1Y", years: 1 },
  { id: "3y", label: "3Y", years: 3 },
  { id: "5y", label: "5Y", years: 5 },
  { id: "all", label: "Since 2019", years: null },
] as const;

function Key({ series, children }: { series: "1" | "2"; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i aria-hidden className={`inline-block h-0.5 w-3.5 ${series === "1" ? "bg-series-1" : "bg-series-2"}`} />
      {children}
    </span>
  );
}

function Readout({ day }: { day: RateDay | null }) {
  if (!day) return <span className="text-ink-3">No rates on this day</span>;
  return (
    <>
      <span className="font-mono text-ink-3">{formatTableDate(day.date)}</span>
      <Key series="1">
        <b className="font-semibold text-ink-1">{formatRateRange(day.fedLower, day.fedUpper)}</b> Fed target range
      </Key>
      <Key series="2">
        <b className="font-semibold text-ink-1">{formatRate(day.repo)}</b> RBI repo
      </Key>
      <Key series="2">
        <b className="font-semibold text-ink-1">{formatRateRange(day.floor, day.msf)}</b> RBI corridor (
        {day.floorKind === "sdf" ? "SDF" : "reverse repo"}–MSF)
        <Qualifier>as set at MPC meetings</Qualifier>
      </Key>
    </>
  );
}

const COLUMNS: Column<RateDay>[] = [
  { id: "date", header: "Date", cell: (d) => <span className="font-mono">{formatTableDate(d.date)}</span>, sortValue: (d) => d.date },
  { id: "fed", header: "Fed target range (%)", align: "right", cell: (d) => formatAxisRange(d.fedLower, d.fedUpper) },
  { id: "repo", header: "RBI repo (%)", align: "right", cell: (d) => formatAxisNumber(d.repo) },
  {
    id: "reverse",
    header: "Reverse repo (%)",
    align: "right",
    cell: (d) => (d.repo !== null && d.date >= SDF_FROM ? <span title="The SDF replaced the reverse repo as the corridor's floor on 8 Apr 2022">{NOT_APPLICABLE}</span> : formatAxisNumber(d.floorKind === "reverse_repo" ? d.floor : null)),
  },
  {
    id: "sdf",
    header: "SDF (%)",
    align: "right",
    cell: (d) => (d.repo !== null && d.date < SDF_FROM ? <span title="The SDF began on 8 Apr 2022">{NOT_APPLICABLE}</span> : formatAxisNumber(d.floorKind === "sdf" ? d.floor : null)),
  },
  { id: "msf", header: "MSF (%)", align: "right", cell: (d) => formatAxisNumber(d.msf) },
];

export function RatesPanel({
  fed,
  rbi,
  decisions,
  from,
  footer,
}: {
  fed: { upper: StepSeries; lower: StepSeries };
  rbi: PolicyRateSettingRow[];
  decisions: PolicyMeetingRow[];
  from: string;
  footer: ReactNode;
}) {
  const data = useMemo(() => buildRateChart(fed, rbi, decisions, from), [fed, rbi, decisions, from]);
  const [asTable, setAsTable] = useState(false);
  const [preset, setPreset] = useState<(typeof PRESETS)[number]["id"]>("all");
  const [hovered, setHovered] = useState<string | null>(null);
  const onHover = useCallback((d: string | null) => setHovered(d), []);

  const series = useMemo<ChartSeries[]>(
    () => [
      { id: "rbi-corridor", kind: "band", entity: "rbi", data: data.rbiCorridor, lowerEdge: false },
      { id: "fed-range", kind: "band", entity: "fed", data: data.fedBand },
      { id: "rbi-repo", kind: "step", entity: "rbi", data: data.rbiRepo },
    ],
    [data],
  );
  const markers = useMemo<ChartMarker[]>(
    () => data.markers.map((m) => ({ time: m.time, price: m.price, seriesId: m.bank === "Fed" ? "fed-range" : "rbi-repo" })),
    [data],
  );
  const last = data.days.at(-1)?.date ?? null;
  const p = PRESETS.find((x) => x.id === preset)!;
  const visibleFrom = last && p.years !== null ? yearsBefore(last, p.years) : undefined;
  const rows = useMemo(() => [...data.changes].reverse(), [data]);

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
      title="Policy rates since 2019"
      titleId="panel-rates"
      subtitle="How far each bank has moved, decision by decision. Bands: the Fed's target range and the RBI's corridor."
      actions={data.days.length > 0 ? toggle : undefined}
      variant="flush"
      footer={footer}
    >
      {data.days.length === 0 ? (
        <div className="p-3">
          <StateNote kind="unavailable">No policy rates are published yet.</StateNote>
        </div>
      ) : asTable ? (
        <DataTable
          columns={COLUMNS}
          rows={rows}
          rowKey={(d) => d.date}
          caption="Policy rates on each day any of them changed, newest first"
          initialSort={{ id: "date", desc: true }}
          dense
          empty={<StateNote kind="empty">No rates in this range.</StateNote>}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 pt-2.5">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-data text-ink-2" aria-live="off">
              <Readout day={dayAt(data.days, hovered)} />
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
              markers={markers}
              visibleFrom={visibleFrom}
              onHover={onHover}
              label="Policy rates since 2019: the Fed's target range and the RBI's repo rate and corridor. View as table for the values."
            />
          </div>
        </>
      )}
    </Panel>
  );
}
