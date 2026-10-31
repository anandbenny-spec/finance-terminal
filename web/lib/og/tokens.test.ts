import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { readOgTokens } from "./tokens";

describe("readOgTokens", () => {
  it("reads every colour from the real globals.css", () => {
    const css = readFileSync(path.join(process.cwd(), "app/globals.css"), "utf8");
    const t = readOgTokens(css);
    for (const value of Object.values(t)) expect(value).toMatch(/^#[0-9a-f]{6}$/);
    // all black since 29 Oct 2026: canvas and panel are both #000000, so the distinctness check uses raised
    expect(t.canvas).not.toBe(t.raised);
    expect(t.highlight).toBe("#ffffff");
    expect(t.highlight).not.toBe(t.canvas);
  });
  it("throws when a token is missing, so the image never draws in a default", () => {
    expect(() => readOgTokens(":root { --canvas: #000000; }")).toThrow(/--panel/);
  });
});
