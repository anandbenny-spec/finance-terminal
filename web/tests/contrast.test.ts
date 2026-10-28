// WCAG 2.2 contrast of the colour tokens, computed from globals.css (Design 01 §8.1, as revised 29 Oct 2026), so a
// later colour change can't slip under the floor unnoticed.
import { describe, expect, it } from "vitest";
import { readWeb, resolveVar, rootVariables } from "./css";

type RGBA = [number, number, number, number];
const vars = rootVariables(readWeb("app/globals.css"));

function parse(value: string): RGBA {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const fn = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+))?\s*\)$/i.exec(value);
  if (fn) return [Number(fn[1]), Number(fn[2]), Number(fn[3]), fn[4] === undefined ? 1 : Number(fn[4])];
  throw new Error(`cannot parse colour: ${value}`);
}
const colour = (name: string): RGBA => parse(resolveVar(vars, name));

/** `top` painted over an opaque `under`. */
function over(top: RGBA, under: RGBA): RGBA {
  const a = top[3];
  return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat(1) as RGBA;
}
function luminance([r, g, b]: RGBA): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function ratio(a: RGBA, b: RGBA): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const SURFACES = ["--canvas", "--panel", "--raised", "--active"];

describe("text and mark contrast (Design 01 §8.1)", () => {
  for (const text of ["--ink-1", "--ink-2", "--ink-3", "--amber", "--up", "--down", "--warn"]) {
    for (const surface of SURFACES) {
      it(`${text} on ${surface} is at least 4.5:1`, () => {
        expect(ratio(colour(text), colour(surface))).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  for (const base of ["--panel", "--raised"]) {
    it(`--ins-ink and --del-ink on their washes over ${base} are at least 4.5:1`, () => {
      const b = colour(base);
      expect(ratio(colour("--ins-ink"), over(colour("--ins-wash"), b))).toBeGreaterThanOrEqual(4.5);
      expect(ratio(colour("--del-ink"), over(colour("--del-wash"), b))).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("--ink-1 on --amber-wash over --panel is at least 4.5:1", () => {
    expect(ratio(colour("--ink-1"), over(colour("--amber-wash"), colour("--panel")))).toBeGreaterThanOrEqual(4.5);
  });

  it("--amber-ink on --amber is at least 4.5:1", () => {
    expect(ratio(colour("--amber-ink"), colour("--amber"))).toBeGreaterThanOrEqual(4.5);
  });

  for (const surface of ["--panel", "--raised"]) {
    it(`--line-input on ${surface} is at least 3:1`, () => {
      expect(ratio(colour("--line-input"), colour(surface))).toBeGreaterThanOrEqual(3);
    });
  }

  for (const s of ["--series-1", "--series-2", "--series-3"]) {
    it(`${s} on --panel is at least 3:1`, () => {
      expect(ratio(colour(s), colour("--panel"))).toBeGreaterThanOrEqual(3);
    });
  }

  it("matches Design 01 §8.1's recorded lowest pairs", () => {
    expect(ratio(colour("--ink-3"), colour("--active"))).toBeCloseTo(4.92, 1);
    expect(ratio(colour("--line-input"), colour("--raised"))).toBeCloseTo(3.32, 1);
  });
});
