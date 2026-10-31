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

/** OKLab (Björn Ottosson): sRGB to linear, to LMS, cube root, to Lab. */
function oklab([r, g, b]: RGBA): [number, number, number] {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const [R, G, B] = [lin(r), lin(g), lin(b)];
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
  const sx = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * sx,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * sx,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * sx,
  ];
}
function deltaE100(a: RGBA, b: RGBA): number {
  const [x, y] = [oklab(a), oklab(b)];
  return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}

const SURFACES = ["--canvas", "--panel", "--raised", "--active"];

describe("text and mark contrast (Design 01 §8.1)", () => {
  for (const text of ["--ink-1", "--ink-2", "--ink-3", "--highlight", "--up", "--down", "--warn"]) {
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

  it("--ink-1 on --highlight-wash over --panel is at least 4.5:1", () => {
    expect(ratio(colour("--ink-1"), over(colour("--highlight-wash"), colour("--panel")))).toBeGreaterThanOrEqual(4.5);
  });

  it("--ink-1 on --highlight-wash over --raised is at least 4.5:1", () => {
    expect(ratio(colour("--ink-1"), over(colour("--highlight-wash"), colour("--raised")))).toBeGreaterThanOrEqual(4.5);
  });

  it("the citation chip's [n] (--highlight) on --raised is at least 4.5:1", () => {
    expect(ratio(colour("--highlight"), colour("--raised"))).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the accent apart from body text: OKLab ΔE × 100 between --highlight and --ink-1 is at least 6.5", () => {
    expect(deltaE100(colour("--highlight"), colour("--ink-1"))).toBeGreaterThanOrEqual(6.5);
  });

  it("--highlight-ink on --highlight is at least 4.5:1", () => {
    expect(ratio(colour("--highlight-ink"), colour("--highlight"))).toBeGreaterThanOrEqual(4.5);
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
