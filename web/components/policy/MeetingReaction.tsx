"use client";

// The meeting page's Market reaction tab (Spec 01 §4.3, §4.5; Design 01 §5.3; Pass 1b, #75): a table of how each
// market moved from the last value before the release to the first after it (the Fed's US 2-year first, labelled as
// its surprise measure), then one chart per series around the decision: 10 sessions up to the "before" value and 10
// from the "after" value on, the first value after the decision marked. A move is a market move, so it shows as a
// Delta; the charts take their colour from the market (cyan US, violet India). Each chart has "View as table".
// Derived figures, labelled as such. Everything arrives formatted from lib/policy/reaction.ts.
import dynamic from "next/dynamic";
import { ChartLine, Table2 } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Delta } from "@/components/data/Delta";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import type { ChartMarker, ChartSeries } from "@/components/data/TimeSeriesChart";
import { changeOf } from "@/lib/delta";
import { formatTableDate } from "@/lib/format";
import type { MeetingReaction, ReactionChart, ReactionEntry } from "@/lib/policy/reaction";

const TimeSeriesChart = dynamic(() => import("@/components/data/TimeSeriesChart"), {
  ssr: false,
  loading: () => <div className="h-80 w-full" aria-hidden />,
});

const TH = "h-8 border-b border-line-strong px-3 text-meta font-medium text-ink-2";

function Move({ e }: { e: ReactionEntry }) {
  const c = e.cell;
  if (c.state === "none") return null;
  if (c.state !== "move") {
    return (
      <span title={c.detail}>
        <span aria-hidden>{c.text}</span>
        <span className="sr-only">{c.detail}</span>
      </span>
    );
  }
  return <Delta change={changeOf(c.text)} />;
}

function ChartPanel({ e, chart, label, footer }: { e: ReactionEntry; chart: ReactionChart; label: string; footer: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  const onHover = useCallback((d: string | null) => setHovered(d), []);
  const series = useMemo<ChartSeries[]>(() => [{ id: e.key, kind: "line", entity: chart.entity, data: chart.data }], [e.key, chart]);
  const markers = useMemo<ChartMarker[]>(() => [{ time: chart.marker.time, seriesId: e.key, price: chart.marker.price }], [e.key, chart]);
  const read = hovered ? chart.shown[hovered] : undefined;

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
      title={e.title}
      titleId={`panel-reaction-${e.key.replace(/[^a-z0-9]/gi, "-")}`}
      subtitle={`Around the decision: the ${chart.table.filter((r) => r.side === "before").length} sessions up to the last value before it and ${chart.table.filter((r) => r.side === "after").length} from the first value after it.`}
      actions={toggle}
      variant="flush"
      span={6}
      footer={footer}
    >
      {asTable ? (
        <div className="max-h-80 overflow-auto">
          <table className="w-full border-collapse text-data">
            <caption className="sr-only">{label}</caption>
            <thead className="sticky top-0 bg-raised">
              <tr>
                <th scope="col" className={`${TH} text-left`}>Date</th>
                <th scope="col" className={`${TH} text-right`}>Value</th>
                <th scope="col" className={`${TH} text-left`}>Side</th>
              </tr>
            </thead>
            <tbody>
              {chart.table.map((r) => (
                <tr key={r.date} className="h-8 border-b border-line last:border-b-0">
                  <th scope="row" className="px-3 text-left font-mono font-normal text-ink-1">{formatTableDate(r.date)}</th>
                  <td className="px-3 text-right whitespace-nowrap">{r.value}</td>
                  <td className="px-3 text-ink-2">{r.side === "before" ? "Before the decision" : "After the decision"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <p className="min-h-6 px-3 pt-2.5 text-data text-ink-2" aria-live="off">
            {hovered && read ? (
              <>
                <span className="font-mono text-ink-3">{formatTableDate(hovered)}</span> <b className="font-semibold text-ink-1">{read}</b>
              </>
            ) : (
              <>
                Before {e.before} · After {e.after} · Marker: first value after the decision
              </>
            )}
          </p>
          <div className="px-3 pt-2 pb-1">
            <TimeSeriesChart series={series} markers={markers} onHover={onHover} label={label} />
          </div>
        </>
      )}
    </Panel>
  );
}

export function MeetingReactionView({
  model,
  footer,
  refresh,
  note,
}: {
  model: MeetingReaction;
  footer: ReactNode;
  refresh?: readonly string[];
  note?: string;
}) {
  const moves = model.entries.filter((e) => e.chart || e.chartNote);
  return (
    <div className="mt-5 flex flex-col gap-4">
      <Panel title="Market reaction" titleId="panel-reaction" subtitle="How did markets move when the decision came out?" variant="flush" footer={footer} refresh={refresh}>
        {note && <p className="border-b border-line px-3 py-2 text-meta text-ink-2">{note}</p>}
        <p className="border-b border-line px-3 py-2 text-meta text-ink-2">
          Derived: each move runs from the series&apos; last value fixed before the decision&apos;s release to its first value fixed after it. The US
          2-year&apos;s move is the Fed&apos;s surprise measure. Yields in basis points, the rest in percent.
        </p>
        {model.entries.length === 0 ? (
          <div className="p-3"><StateNote kind="empty">No market series are tracked for this decision.</StateNote></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-data">
              <caption className="sr-only">How each market moved around this decision</caption>
              <thead className="bg-raised">
                <tr>
                  <th scope="col" className={`${TH} text-left`}>Series</th>
                  <th scope="col" className={`${TH} text-right`}>Before</th>
                  <th scope="col" className={`${TH} text-right`}>After</th>
                  <th scope="col" className={`${TH} text-right`}>Move</th>
                </tr>
              </thead>
              <tbody>
                {model.entries.map((e) => (
                  <tr key={e.key} className="border-b border-line last:border-b-0">
                    <th scope="row" className="px-3 py-1.5 text-left align-top font-normal text-ink-1">
                      {e.title}
                      {e.cell.state === "waiting" && <span className="block text-meta text-ink-3">{e.cell.detail}</span>}
                      {e.cell.state === "unavailable" && <span className="block text-meta text-ink-3">{e.cell.detail}</span>}
                    </th>
                    <td className="px-3 py-1.5 text-right align-top whitespace-nowrap">{e.before ?? ""}</td>
                    <td className="px-3 py-1.5 text-right align-top whitespace-nowrap">{e.after ?? ""}</td>
                    <td className="px-3 py-1.5 text-right align-top whitespace-nowrap">
                      <Move e={e} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {moves.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
          {moves.map((e) =>
            e.chart ? (
              <ChartPanel
                key={e.key}
                e={e}
                chart={e.chart}
                label={`${e.title}: values around the decision. View as table for the values.`}
                footer={footer}
              />
            ) : (
              <Panel key={e.key} title={e.title} span={6} footer={footer}>
                <StateNote kind="error">{e.chartNote}</StateNote>
              </Panel>
            ),
          )}
        </div>
      )}
    </div>
  );
}
