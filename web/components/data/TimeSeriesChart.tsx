"use client";

// TimeSeriesChart (Design 01 §1.5, §2.3, §4.4): the one wrapper around Lightweight Charts 5, so an upgrade touches
// this file (and band-series.ts). Series kinds: `step` (policy rates), `line`, `band` (a range between two values,
// a custom series). At most three series, one y-axis on the right, horizontal gridlines in --line, axis text in
// Plex Mono 12 px --ink-3, the crosshair a vertical hairline that snaps to the nearest day, no floating tooltip:
// the parent's legend row turns into the readout through `onHover`. Colours follow the entity, read once from the
// tokens at mount (lib/tokens.ts). The TradingView logo stays off; the footer carries the licence's link.
// Every label goes through lib/format.ts. Browser only: load it without server rendering.
import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  LineType,
  TickMarkType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { BandSeries, type BandData, type BandOptions } from "@/components/data/band-series";
import { formatAxisDate, formatAxisNumber, formatDate } from "@/lib/format";
import { CHART, bandFill, chartTokens, entityColour, type ChartEntity } from "@/lib/tokens";

export type ChartSeries =
  | { id: string; kind: "band"; entity: ChartEntity; data: { time: string; low: number; high: number }[]; lowerEdge?: boolean }
  | { id: string; kind: "step" | "line"; entity: ChartEntity; data: { time: string; value: number }[] };

export type ChartMarker = { time: string; seriesId: string; price: number };

/** A chart time back to YYYY-MM-DD (the library hands back strings, business days or timestamps). */
export function timeToDate(time: Time): string {
  if (typeof time === "string") return time;
  if (typeof time === "number") return new Date(time * 1000).toISOString().slice(0, 10);
  return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
}

function tick(time: Time, type: TickMarkType): string | null {
  const date = timeToDate(time);
  if (type === TickMarkType.Year) return formatAxisDate(date, "year");
  if (type === TickMarkType.Month) return formatAxisDate(date, "month");
  if (type === TickMarkType.DayOfMonth) return formatAxisDate(date, "day");
  return null;
}

export default function TimeSeriesChart({
  series,
  markers = [],
  visibleFrom,
  label,
  onHover,
}: {
  series: ChartSeries[];
  markers?: ChartMarker[];
  /** The first day in view (a range preset); the whole series when undefined. */
  visibleFrom?: string;
  /** The chart's accessible name; "View as table" carries the values. */
  label: string;
  onHover?: (date: string | null) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const hover = useRef(onHover);
  useEffect(() => {
    hover.current = onHover;
  }, [onHover]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    if (series.length > CHART.maxSeries) throw new Error(`TimeSeriesChart: at most ${CHART.maxSeries} series (Design 01 §2.3)`);
    const t = chartTokens();
    const c = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: t.surface },
        textColor: t.axisText,
        fontFamily: t.fontMono,
        fontSize: CHART.axisFontSize,
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: t.grid, style: LineStyle.Solid } },
      rightPriceScale: { borderColor: t.baseline },
      timeScale: { borderColor: t.baseline, tickMarkFormatter: tick, fixLeftEdge: true, fixRightEdge: true },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: t.baseline, width: 1, style: LineStyle.Solid, labelVisible: false },
        horzLine: { visible: false, labelVisible: false },
      },
      localization: { priceFormatter: (p: number) => formatAxisNumber(p), timeFormatter: (time: Time) => formatDate(timeToDate(time)) },
      handleScroll: { vertTouchDrag: false },
    });
    chart.current = c;

    const byId = new Map<string, ReturnType<IChartApi["addSeries"]> | ReturnType<IChartApi["addCustomSeries"]>>();
    for (const s of series) {
      const colour = entityColour(t, s.entity);
      if (s.kind === "band") {
        const band = c.addCustomSeries<BandData, BandOptions>(new BandSeries(), {
          fillColor: bandFill(t, s.entity),
          edgeColor: colour,
          upperWidth: s.lowerEdge === false ? 0 : CHART.lineWidth,
          lowerWidth: s.lowerEdge === false ? 0 : 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        band.setData(s.data);
        byId.set(s.id, band);
      } else {
        const line = c.addSeries(LineSeries, {
          color: colour,
          lineWidth: CHART.lineWidth,
          lineType: s.kind === "step" ? LineType.WithSteps : LineType.Simple,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        line.setData(s.data);
        byId.set(s.id, line);
      }
    }
    for (const [id, target] of byId) {
      const own = markers.filter((m) => m.seriesId === id);
      if (own.length === 0) continue;
      const s = series.find((x) => x.id === id)!;
      createSeriesMarkers(
        target,
        own.map((m) => ({ time: m.time, position: "atPriceMiddle" as const, price: m.price, shape: "circle" as const, color: entityColour(t, s.entity), size: 0.5 })),
      );
    }

    c.subscribeCrosshairMove((param) => hover.current?.(param.time ? timeToDate(param.time) : null));
    return () => {
      chart.current = null;
      c.remove();
    };
  }, [series, markers]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const days = series.flatMap((s) => s.data.map((d) => d.time));
    if (days.length === 0) return;
    const last = days.reduce((a, b) => (a > b ? a : b));
    if (visibleFrom) c.timeScale().setVisibleRange({ from: visibleFrom, to: last });
    else c.timeScale().fitContent();
  }, [series, visibleFrom]);

  return <div ref={box} role="img" aria-label={label} className="h-80 w-full" />;
}
