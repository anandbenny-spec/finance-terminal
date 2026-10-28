"use client";

// TimeSeriesChart (Design 01 §1.5, §2.3, §4.4): the one wrapper around Lightweight Charts 5, so an upgrade touches
// this file (and band-series.ts). Series kinds: `step` (policy rates), `line`, `band` (a range between two values,
// a custom series). At most three series, one y-axis on the right, horizontal gridlines in --line, axis text in
// Plex Mono 12 px --ink-3, the crosshair a vertical hairline that snaps to the nearest day, no floating tooltip:
// the parent's legend row turns into the readout through `onHover`. Colours follow the entity, read once from the
// tokens at mount (lib/tokens.ts). The TradingView logo stays off; the footer carries the licence's link.
// Every label goes through lib/format.ts. Browser only: load it without server rendering.
//
// The range in view (Anand's report, 28 Oct 2026: Long rates fell back to a few months whenever the pointer moved):
// - the chart is built once per `series` and `markers`, so both must keep their identity between renders (the hover
//   readout re-renders the parent on every crosshair move). A missing `markers` is one shared empty list: a fresh
//   `[]` default rebuilt the chart on each move, and the rebuilt chart showed the library's default few months;
// - every build applies the range preset (`visibleFrom`, or the whole series), so no rebuild can drop it;
// - a preset click re-applies its range even when it's already selected (`rangeKey`), the way back from a drag;
// - an up/down wheel or trackpad scroll over the chart scrolls the page, not the chart's zoom (Decided by Claude,
//   standing delegation, 28 Oct 2026: Design 01 §2.3 makes the presets the zoom control, and a chart that swallowed
//   the page's scroll zoomed in by surprise). Sideways swipes and drags still pan; dragging an axis still zooms.
//   Double-clicking the date axis no longer jumps to the library's default few months; the presets reset the view.
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

/** The default `markers`: one list for every render, so leaving markers out never rebuilds the chart. */
const NO_MARKERS: ChartMarker[] = [];

/** Shows the range preset: from `visibleFrom` to the last day of any series, or every day when it's undefined. */
export function showRange(c: Pick<IChartApi, "timeScale">, series: ChartSeries[], visibleFrom: string | undefined): void {
  const days = series.flatMap((s) => s.data.map((d) => d.time));
  if (days.length === 0) return;
  const last = days.reduce((a, b) => (a > b ? a : b));
  if (visibleFrom) c.timeScale().setVisibleRange({ from: visibleFrom, to: last });
  else c.timeScale().fitContent();
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
  markers = NO_MARKERS,
  visibleFrom,
  rangeKey = 0,
  label,
  onHover,
}: {
  /** Keep its identity between renders (useMemo): a new array rebuilds the chart. */
  series: ChartSeries[];
  /** Keep its identity between renders (useMemo), as `series`. */
  markers?: ChartMarker[];
  /** The first day in view (a range preset); the whole series when undefined. */
  visibleFrom?: string;
  /** Bump it on every preset click, so the click re-applies the range even when the preset is already selected. */
  rangeKey?: number;
  /** The chart's accessible name; "View as table" carries the values. */
  label: string;
  onHover?: (date: string | null) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const hover = useRef(onHover);
  const from = useRef(visibleFrom);
  useEffect(() => {
    hover.current = onHover;
    from.current = visibleFrom;
  }, [onHover, visibleFrom]);

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
      // Up/down wheel and trackpad scrolls go to the page; double-clicking the date axis doesn't reset the zoom.
      handleScale: { mouseWheel: false, axisDoubleClickReset: { time: false, price: true } },
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

    showRange(c, series, from.current);
    c.subscribeCrosshairMove((param) => hover.current?.(param.time ? timeToDate(param.time) : null));
    return () => {
      chart.current = null;
      c.remove();
    };
  }, [series, markers]);

  // A preset click (a new `visibleFrom`, or the same one with a new `rangeKey`) moves the existing chart's view.
  useEffect(() => {
    if (chart.current) showRange(chart.current, series, visibleFrom);
  }, [series, visibleFrom, rangeKey]);

  return <div ref={box} role="img" aria-label={label} className="h-80 w-full" />;
}
