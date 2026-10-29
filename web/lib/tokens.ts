// web/lib/tokens.ts: the design tokens for canvas charts (Design 01 §1.5, §2.3). Lightweight Charts takes its
// colours in JavaScript, not CSS, so TimeSeriesChart reads them from globals.css's variables once at mount through
// `chartTokens()`; the canvas then can't drift from the tokens. Nothing here holds a colour value of its own.
//
// Rules from Design 01 §2.3: colour follows the entity, never its order on a chart (cyan for the US and the Fed,
// violet for India and the RBI, magenta for a third series); at most three series; lines 2 px; markers at least
// 8 px with a 2 px ring in --panel; one y-axis, on the right; horizontal gridlines in --line; axis text --ink-3 in
// Plex Mono 12 px; baseline and crosshair --line-strong; text never wears a series colour.

/** What a chart's series is about. Market codes as `core` stores them; banks as the jobs name them. */
export type ChartEntity = "US" | "IN" | "fed" | "rbi" | "third";

export const CHART = {
  maxSeries: 3,
  lineWidth: 2,
  markerSize: 8, // decision-day markers: at least 8 px
  markerRing: 2, // in --panel
  axisFontSize: 12, // Plex Mono, --ink-3
  height: { panel: 240, lead: 320 }, // Design 01 §4.4
} as const;

export type ChartTokens = {
  series: { 1: string; 2: string; 3: string };
  bandAlpha: number;
  surface: string; // --panel: the chart surface, and the markers' ring
  grid: string; // --line
  baseline: string; // --line-strong: axis baselines and the crosshair
  axisText: string; // --ink-3
  legendText: string; // --ink-2: series names in the legend row
  valueText: string; // --ink-1: values in the legend readout
  ins: string; // --ins-ink: ChangeStrip's words added
  del: string; // --del-ink: ChangeStrip's words removed
  fontMono: string; // the Plex Mono stack, as globals.css's --font-mono builds it
};

/** The part of CSSStyleDeclaration this module needs (getComputedStyle's result, or a stand-in in tests). */
export type StyleSource = { getPropertyValue(name: string): string };

function read(style: StyleSource, name: string): string {
  const value = style.getPropertyValue(name).trim();
  if (!value) throw new Error(`Design token ${name} is missing (web/app/globals.css)`);
  return value;
}

// The stylesheet's minifier writes `#000000` as `#000` (and `#ffffff` as `#fff`), so the browser hands back the short
// form: it is read as the same colour and returned as #rrggbb. (#154's all-black canvas and panel made every chart throw
// on the shortened --panel until this was added; found while building ChangeStrip, #89.)
function hex(style: StyleSource, name: string): string {
  let value = read(style, name).toLowerCase();
  if (/^#[0-9a-f]{3}$/.test(value)) value = `#${[...value.slice(1)].map((c) => c + c).join("")}`;
  if (!/^#[0-9a-f]{6}$/.test(value)) throw new Error(`Design token ${name} should be #rrggbb, got "${value}"`);
  return value;
}

/** Reads the chart tokens from a computed style. Throws if one is missing, so a chart never draws in a default. */
export function readChartTokens(style: StyleSource): ChartTokens {
  const bandAlpha = Number(read(style, "--band-alpha"));
  if (!(bandAlpha > 0 && bandAlpha < 1)) throw new Error(`Design token --band-alpha should be in (0, 1), got ${bandAlpha}`);
  return {
    series: { 1: hex(style, "--series-1"), 2: hex(style, "--series-2"), 3: hex(style, "--series-3") },
    bandAlpha,
    surface: hex(style, "--panel"),
    grid: hex(style, "--line"),
    baseline: hex(style, "--line-strong"),
    axisText: hex(style, "--ink-3"),
    legendText: hex(style, "--ink-2"),
    valueText: hex(style, "--ink-1"),
    ins: hex(style, "--ins-ink"),
    del: hex(style, "--del-ink"),
    // next/font sets --font-plex-mono on <html> (app/fonts.ts); the fallbacks are globals.css's --font-mono's.
    fontMono: `${read(style, "--font-plex-mono")}, ui-monospace, "SFMono-Regular", monospace`,
  };
}

/** The chart tokens from the page's root element. Browser only: call it once when a chart mounts. */
export function chartTokens(): ChartTokens {
  return readChartTokens(getComputedStyle(document.documentElement));
}

const ENTITY_SERIES: Record<ChartEntity, 1 | 2 | 3> = { US: 1, fed: 1, IN: 2, rbi: 2, third: 3 };

/** The series colour for an entity: cyan for the US and the Fed, violet for India and the RBI, magenta otherwise. */
export function entityColour(tokens: ChartTokens, entity: ChartEntity): string {
  return tokens.series[ENTITY_SERIES[entity]];
}

/** A range band's fill (the Fed's target range, the RBI's corridor): the series colour at --band-alpha. */
export function bandFill(tokens: ChartTokens, entity: ChartEntity): string {
  const c = entityColour(tokens, entity);
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${tokens.bandAlpha})`;
}
