// A range band for Lightweight Charts 5 (Design 01 §2.3, §10.2): the area between a low and a high value, drawn as
// steps (a policy range holds its value until the day it changes), filled in the series colour at --band-alpha, with
// its upper edge at 2 px and its lower edge at 1 px. One custom series (`addCustomSeries`), so the chart keeps one
// series per band and at most three in all. Points come one per calendar day (lib/policy/rates.ts), so a step's
// corner falls on the day the value changed.
import {
  customSeriesDefaultOptions,
  type CustomData,
  type CustomSeriesOptions,
  type CustomSeriesPricePlotValues,
  type CustomSeriesWhitespaceData,
  type ICustomSeriesPaneRenderer,
  type ICustomSeriesPaneView,
  type PaneRendererCustomData,
  type PriceToCoordinateConverter,
  type Time,
} from "lightweight-charts";

export type BandData = CustomData<Time> & { low: number; high: number };

export type BandOptions = CustomSeriesOptions & {
  fillColor: string;
  edgeColor: string;
  upperWidth: number;
  lowerWidth: number;
};

type Pt = { x: number; hi: number; lo: number };
/** The canvas target the library hands a renderer (fancy-canvas's type, taken from the interface it's used in). */
type CanvasRenderingTarget2D = Parameters<ICustomSeriesPaneRenderer["draw"]>[0];

function isBand(d: BandData | CustomSeriesWhitespaceData<Time>): d is BandData {
  return typeof (d as BandData).low === "number" && typeof (d as BandData).high === "number";
}

class BandRenderer implements ICustomSeriesPaneRenderer {
  data: PaneRendererCustomData<Time, BandData> | null = null;
  options: BandOptions | null = null;

  draw(target: CanvasRenderingTarget2D, toY: PriceToCoordinateConverter): void {
    const data = this.data;
    const opts = this.options;
    if (!data || !opts || !data.visibleRange) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      const from = Math.max(0, data.visibleRange!.from - 1);
      const to = Math.min(data.bars.length, data.visibleRange!.to + 1);
      let seg: Pt[] = [];
      const paint = () => {
        if (seg.length === 0) return;
        // Top edge left to right as steps, then the bottom edge back.
        const top = new Path2D();
        const bottom = new Path2D();
        const fill = new Path2D();
        top.moveTo(seg[0].x, seg[0].hi);
        fill.moveTo(seg[0].x, seg[0].hi);
        for (let k = 1; k < seg.length; k++) {
          top.lineTo(seg[k].x, seg[k - 1].hi);
          top.lineTo(seg[k].x, seg[k].hi);
          fill.lineTo(seg[k].x, seg[k - 1].hi);
          fill.lineTo(seg[k].x, seg[k].hi);
        }
        const n = seg.length - 1;
        bottom.moveTo(seg[n].x, seg[n].lo);
        fill.lineTo(seg[n].x, seg[n].lo);
        for (let k = n; k >= 1; k--) {
          bottom.lineTo(seg[k].x, seg[k - 1].lo);
          bottom.lineTo(seg[k - 1].x, seg[k - 1].lo);
          fill.lineTo(seg[k].x, seg[k - 1].lo);
          fill.lineTo(seg[k - 1].x, seg[k - 1].lo);
        }
        fill.closePath();
        ctx.fillStyle = opts.fillColor;
        ctx.fill(fill);
        ctx.strokeStyle = opts.edgeColor;
        ctx.lineJoin = "round";
        ctx.lineWidth = opts.upperWidth * vr;
        ctx.stroke(top);
        if (opts.lowerWidth > 0) {
          ctx.lineWidth = opts.lowerWidth * vr;
          ctx.stroke(bottom);
        }
        seg = [];
      };
      for (let i = from; i < to; i++) {
        const bar = data.bars[i];
        const d = bar.originalData;
        const hi = isBand(d) ? toY(d.high) : null;
        const lo = isBand(d) ? toY(d.low) : null;
        if (hi === null || lo === null) {
          paint();
          continue;
        }
        seg.push({ x: bar.x * hr, hi: hi * vr, lo: lo * vr });
      }
      paint();
    });
  }
}

export class BandSeries implements ICustomSeriesPaneView<Time, BandData, BandOptions> {
  private readonly view = new BandRenderer();

  renderer(): ICustomSeriesPaneRenderer {
    return this.view;
  }

  update(data: PaneRendererCustomData<Time, BandData>, options: BandOptions): void {
    this.view.data = data;
    this.view.options = options;
  }

  priceValueBuilder(row: BandData): CustomSeriesPricePlotValues {
    return [row.low, row.high, row.high];
  }

  isWhitespace(row: BandData | CustomSeriesWhitespaceData<Time>): row is CustomSeriesWhitespaceData<Time> {
    return !isBand(row);
  }

  defaultOptions(): BandOptions {
    // Placeholders only: TimeSeriesChart always passes the token colours (lib/tokens.ts), never these.
    return { ...customSeriesDefaultOptions, fillColor: "transparent", edgeColor: "transparent", upperWidth: 2, lowerWidth: 1 };
  }

  destroy(): void {
    this.view.data = null;
    this.view.options = null;
  }
}
