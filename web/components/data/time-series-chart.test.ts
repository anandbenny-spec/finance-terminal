// TimeSeriesChart's range in view (Anand's report, 28 Oct 2026: Long rates fell back to a few months whenever the
// pointer moved over it). The chart was rebuilt on every crosshair move, because Long rates passes no `markers` and
// the default was a fresh `[]` each render, and the rebuilt chart skipped the range preset. These tests hold the
// fix: the range helper, a stable default, the range applied on every build, preset clicks that always re-apply,
// and wheel scrolls left to the page. (Checked by hand in a headless browser too: one build, the range kept.)
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { showRange, type ChartSeries } from "@/components/data/TimeSeriesChart";

const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const chartSource = read("./TimeSeriesChart.tsx");

function fakeChart() {
  const calls: string[] = [];
  const timeScale = () => ({
    fitContent: () => calls.push("fit"),
    setVisibleRange: (r: { from: string; to: string }) => calls.push(`range ${r.from} → ${r.to}`),
  });
  return { chart: { timeScale } as unknown as Parameters<typeof showRange>[0], calls };
}

const SERIES: ChartSeries[] = [
  { id: "us", kind: "line", entity: "fed", data: [{ time: "2019-01-02", value: 2.66 }, { time: "2026-10-27", value: 4.1 }] },
  { id: "in", kind: "line", entity: "rbi", data: [{ time: "2019-01-01", value: 7.4 }, { time: "2026-10-01", value: 6.4 }] },
];

describe("showRange", () => {
  it("shows every day when no preset narrows it (Since 2019)", () => {
    const { chart, calls } = fakeChart();
    showRange(chart, SERIES, undefined);
    expect(calls).toEqual(["fit"]);
  });

  it("shows a preset from its first day to the latest day of any series", () => {
    const { chart, calls } = fakeChart();
    showRange(chart, SERIES, "2025-10-27");
    expect(calls).toEqual(["range 2025-10-27 → 2026-10-27"]);
  });

  it("leaves an empty chart alone", () => {
    const { chart, calls } = fakeChart();
    showRange(chart, [{ ...SERIES[0], data: [] } as ChartSeries], "2025-10-27");
    expect(calls).toEqual([]);
  });
});

describe("TimeSeriesChart keeps the range in view", () => {
  it("defaults `markers` to one shared list, so leaving them out never rebuilds the chart", () => {
    expect(chartSource).toMatch(/^const NO_MARKERS: ChartMarker\[\] = \[\];$/m);
    expect(chartSource).toMatch(/^\s+markers = NO_MARKERS,$/m);
    expect(chartSource).not.toMatch(/markers = \[\]/);
  });

  it("applies the range preset on every build, and again on every preset click", () => {
    const build = chartSource.slice(chartSource.indexOf("const c = createChart("), chartSource.indexOf("}, [series, markers]);"));
    expect(build).toContain("showRange(c, series, from.current);");
    expect(chartSource).toMatch(/showRange\(chart\.current, series, visibleFrom\);\n\s+\}, \[series, visibleFrom, rangeKey\]\);/);
  });

  it("leaves up/down wheel scrolls to the page and keeps the zoom on a date-axis double-click", () => {
    expect(chartSource).toContain("handleScale: { mouseWheel: false, axisDoubleClickReset: { time: false, price: true } }");
  });

  it.each(["RatesPanel", "LongRatesPanel"])("%s re-applies a preset on every click, even the selected one", (file) => {
    const panel = read(`../policy/${file}.tsx`);
    expect(panel).toMatch(/onClick=\{\(\) => \{\s+setPreset\(x\.id\);\s+setRangeKey\(\(k\) => k \+ 1\);\s+\}\}/);
    expect(panel).toContain("rangeKey={rangeKey}");
  });
});
