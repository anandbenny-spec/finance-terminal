// Delta's tones (Design 01 §4.3; neutral tone: Anand, 1 Nov 2026): market moves get colour, policy changes only the ink colour.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Delta } from "@/components/data/Delta";
import { changeOf } from "@/lib/delta";

describe("Delta tone", () => {
  it("market (the default) keeps its green, red and flat colours", () => {
    expect(renderToStaticMarkup(<Delta change={changeOf("+3 bps")} />)).toContain("text-up");
    expect(renderToStaticMarkup(<Delta change={changeOf("−3 bps")} />)).toContain("text-down");
    expect(renderToStaticMarkup(<Delta change={changeOf("0 bps")} />)).toContain("text-flat");
  });

  it.each([["+25 bps", true, false], ["−25 bps", true, true], ["0 bps", false, false]])("neutral %s: ink colour, triangle %s", (text, svg, rotated) => {
    const out = renderToStaticMarkup(<Delta change={changeOf(text)} tone="neutral" />);
    expect(out).toContain("text-ink-1");
    expect(out).not.toMatch(/text-up|text-down|text-flat/);
    expect(out.includes("<svg")).toBe(svg);
    expect(out.includes("rotate-180")).toBe(rotated);
  });
});
