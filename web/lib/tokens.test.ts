// lib/tokens.ts reads the chart tokens from globals.css's variables (Design 01 §1.5, §2.3). The stand-in style
// below is globals.css's own :root, resolved as getComputedStyle resolves custom properties, plus the font
// variable next/font sets on <html>.
import { describe, expect, it } from "vitest";
import { readWeb, resolveVar, rootVariables } from "../tests/css";
import { CHART, bandFill, entityColour, readChartTokens, type StyleSource } from "./tokens";

const vars = rootVariables(readWeb("app/globals.css"));
const PLEX_MONO = "'IBM Plex Mono', 'IBM Plex Mono Fallback'";

function style(overrides: Record<string, string> = {}): StyleSource {
  return {
    getPropertyValue: (name) =>
      name in overrides ? overrides[name] : name === "--font-plex-mono" ? PLEX_MONO : resolveVar(vars, name),
  };
}

describe("readChartTokens", () => {
  const t = readChartTokens(style());

  it("reads the series palette and band opacity from globals.css (Design 01 §2.3)", () => {
    expect(t.series).toEqual({ 1: "#1aa3b5", 2: "#9085e9", 3: "#d55181" });
    expect(t.bandAlpha).toBe(0.12);
  });

  it("reads the chart chrome: surface, grid, baseline, axis and legend text", () => {
    expect(t).toMatchObject({
      surface: "#12161b",
      grid: "#252a31",
      baseline: "#343b45",
      axisText: "#8c939e",
      legendText: "#afb5be",
      valueText: "#e6e8eb",
      ins: "#8fdda4",
      del: "#f59a9a",
    });
    expect(t.fontMono).toBe(`${PLEX_MONO}, ui-monospace, "SFMono-Regular", monospace`);
  });

  it("fails loudly when a token is missing or malformed, so a chart never draws in a default colour", () => {
    expect(() => readChartTokens(style({ "--series-2": "" }))).toThrow(/--series-2 is missing/);
    expect(() => readChartTokens(style({ "--font-plex-mono": " " }))).toThrow(/--font-plex-mono is missing/);
    expect(() => readChartTokens(style({ "--series-1": "cyan" }))).toThrow(/#rrggbb/);
    expect(() => readChartTokens(style({ "--band-alpha": "12" }))).toThrow(/--band-alpha/);
  });
});

describe("colour follows the entity, never its order on a chart", () => {
  const t = readChartTokens(style());

  it("gives the US and the Fed cyan, India and the RBI violet, a third series magenta", () => {
    expect(entityColour(t, "US")).toBe("#1aa3b5");
    expect(entityColour(t, "fed")).toBe("#1aa3b5");
    expect(entityColour(t, "IN")).toBe("#9085e9");
    expect(entityColour(t, "rbi")).toBe("#9085e9");
    expect(entityColour(t, "third")).toBe("#d55181");
  });

  it("fills a range band with its series colour at --band-alpha", () => {
    expect(bandFill(t, "fed")).toBe("rgba(26, 163, 181, 0.12)");
    expect(bandFill(t, "rbi")).toBe("rgba(144, 133, 233, 0.12)");
  });

  it("keeps Design 01 §2.3's chart rules as constants", () => {
    expect(CHART).toEqual({
      maxSeries: 3,
      lineWidth: 2,
      markerSize: 8,
      markerRing: 2,
      axisFontSize: 12,
      height: { panel: 240, lead: 320 },
    });
  });
});
