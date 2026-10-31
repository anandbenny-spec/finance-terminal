// web/lib/og/tokens.ts: the design tokens for the preview image. next/og (Satori) can't read CSS variables, so the
// image reads its colours from globals.css itself at build time: nothing here holds a colour of its own, the same
// rule as lib/tokens.ts for the charts (CLAUDE.md §5: tokens only).

export type OgTokens = { canvas: string; panel: string; raised: string; lineStrong: string; ink1: string; ink2: string; ink3: string; highlight: string; series1: string; series2: string };

function hexOf(css: string, name: string): string {
  const m = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\b`).exec(css);
  if (!m) throw new Error(`Design token ${name} should be #rrggbb in web/app/globals.css`);
  return m[1].toLowerCase();
}

/** Reads the colours the preview image uses from globals.css's text. Throws if one is missing. */
export function readOgTokens(css: string): OgTokens {
  return {
    canvas: hexOf(css, "--canvas"),
    panel: hexOf(css, "--panel"),
    raised: hexOf(css, "--raised"),
    lineStrong: hexOf(css, "--line-strong"),
    ink1: hexOf(css, "--ink-1"),
    ink2: hexOf(css, "--ink-2"),
    ink3: hexOf(css, "--ink-3"),
    highlight: hexOf(css, "--highlight"),
    series1: hexOf(css, "--series-1"),
    series2: hexOf(css, "--series-2"),
  };
}
