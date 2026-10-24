"use client";

// VoteDotPlot (Design 01 §1.14, §4.5; F23): every voting member at their vote. The axis is in bps with "Hold" in the
// middle, "← cut" and "hike →" under its ends, the decision's tick in ink-1 with "decision" under it. One 8 px dot per
// member (2 px panel ring, 10 px vertical pitch), stacked upward at its value, in the bank's series colour (cyan Fed,
// violet RBI): position carries the meaning. A dissent that isn't about the rate is a hollow dot at the decision.
// Up to three dissenters are labelled by name. Each dot is a button with a 24 px hit area; activating it (click, tap,
// Enter) opens the member's vote and a link to the cited passage. "View as table" swaps in VoteGrid. Plain HTML/CSS,
// positioned by percentages, so it reflows to 320 px without sideways scroll. The vote's colour is never the meaning.
import { Table2, ChartScatter } from "lucide-react";
import { useState } from "react";
import { VoteGrid } from "@/components/ai/VoteGrid";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatAxisChange } from "@/lib/format";
import type { DotPlot } from "@/lib/policy/votedots";
import type { VoteRow } from "@/lib/policy/meeting";

const PITCH = 10; // px between stacked dots
const HIT = 24; // px hit area

function tickLabel(bps: number): string {
  return bps === 0 ? "Hold" : formatAxisChange(bps);
}

export function VoteDotPlot({
  plot,
  bank,
  rows,
  stance,
  caption,
}: {
  plot: DotPlot;
  bank: "Fed" | "RBI";
  rows: VoteRow[];
  stance: boolean;
  caption: string;
}) {
  const [asTable, setAsTable] = useState(false);
  const span = plot.max - plot.min;
  const pos = (bps: number) => `${((bps - plot.min) / span) * 100}%`;
  const fill = bank === "Fed" ? "bg-series-1" : "bg-series-2";
  const ring = bank === "Fed" ? "border-series-1" : "border-series-2";
  const named = plot.dots.filter((d) => d.dissent).length <= 3;
  const height = (plot.maxLevel + 1) * PITCH + HIT;

  return (
    <div>
      <div className="flex items-start justify-between gap-3 px-3 py-2.5">
        <p className="text-data text-ink-1">{plot.summary}</p>
        <button
          type="button"
          onClick={() => setAsTable((v) => !v)}
          aria-pressed={asTable}
          aria-label={asTable ? "View as chart" : "View as table"}
          title={asTable ? "View as chart" : "View as table"}
          className="grid size-7 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-active hover:text-ink-1"
        >
          {asTable ? <ChartScatter aria-hidden className="size-4" /> : <Table2 aria-hidden className="size-4" />}
        </button>
      </div>
      {asTable ? (
        <VoteGrid rows={rows} stance={stance} caption={caption} />
      ) : (
        <div className="px-6 pb-3" role="group" aria-label={`${caption}: ${plot.summary}`}>
          <div className="mx-auto max-w-120">
            <div className="relative" style={{ height }}>
              {plot.dots.map((d) => (
                <Popover key={d.key}>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      aria-label={`${d.member}: ${d.lines.join("; ")}${d.hollow ? " (hollow dot: dissent not on the rate)" : ""}`}
                      className="absolute grid -translate-x-1/2 place-items-center rounded-sm"
                      style={{ left: pos(d.at), bottom: d.level * PITCH, width: HIT, height: HIT }}
                    >
                      <span
                        aria-hidden
                        className={`block size-2 rounded-full ring-2 ring-panel ${d.hollow ? `border-2 ${ring} bg-panel` : fill}`}
                      />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto max-w-90">
                    <p className="text-data font-medium text-ink-1">{d.member}</p>
                    {d.lines.map((l) => (
                      <p key={l} className="text-meta text-ink-2">{l}</p>
                    ))}
                    {d.hollow && <p className="text-meta text-ink-2">Hollow dot: a dissent that isn&apos;t about the rate.</p>}
                    {d.href && (
                      <p className="mt-2 text-meta">
                        <a href={d.href} className="text-ink-1 underline">Open the cited passage</a>
                      </p>
                    )}
                  </PopoverContent>
                </Popover>
              ))}
              {named &&
                plot.dots
                  .filter((d) => d.dissent)
                  .map((d) => (
                    <span
                      key={`n-${d.key}`}
                      aria-hidden
                      className="pointer-events-none absolute -translate-y-1/2 pl-3.5 text-meta whitespace-nowrap text-ink-2"
                      style={{ left: pos(d.at), bottom: d.level * PITCH + HIT / 2 - 4 }}
                    >
                      {d.member}
                    </span>
                  ))}
            </div>
            <div className="relative h-px bg-line-strong">
              {plot.ticks.map((t) => (
                <span key={t} aria-hidden className="absolute top-0 h-1.5 w-px -translate-x-1/2 bg-line-strong" style={{ left: pos(t) }} />
              ))}
              <span aria-hidden className="absolute top-0 h-2 w-0.5 -translate-x-1/2 bg-ink-1" style={{ left: pos(plot.decision) }} />
            </div>
            <div className="relative mt-2 h-4 text-meta text-ink-3" aria-hidden>
              {plot.ticks
                .filter((t) => t !== plot.decision)
                .map((t) => (
                  <span key={t} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: pos(t) }}>
                    {tickLabel(t)}
                  </span>
                ))}
              <span className="absolute -translate-x-1/2 whitespace-nowrap font-medium text-ink-1" style={{ left: pos(plot.decision) }}>
                {tickLabel(plot.decision)}
              </span>
            </div>
            <div className="relative h-4 text-meta text-ink-1" aria-hidden>
              <span className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: pos(plot.decision) }}>decision</span>
            </div>
            <div className="mt-1 flex justify-between text-meta text-ink-3" aria-hidden>
              <span>← cut</span>
              <span>hike →</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
