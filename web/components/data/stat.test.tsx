// StatRow finishes a last row its stats don't fill (12 Nov 2026). The dividers between stats are the row's own
// background (the line colour) showing through 1 px gaps, so a row with places left over showed that background as a
// grey slab: one stat on Policy Day's "Next meeting" panel, one and three on /accuracy. One empty cell on the panel
// surface now takes the places left, sized for four across (from md) and two across (below).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Stat, StatRow } from "@/components/data/Stat";

const row = (n: number) =>
  renderToStaticMarkup(
    <StatRow>
      {Array.from({ length: n }, (_, i) => (
        <Stat key={i} label={`Stat ${i}`} value="1" />
      ))}
    </StatRow>,
  );
const rest = (out: string): string[] | null => out.match(/<div aria-hidden="true" data-stat-rest="true" class="([^"]*)"/)?.[1].split(" ") ?? null;

describe("StatRow", () => {
  it("adds nothing when the stats fill their rows at both widths", () => {
    expect(rest(row(4))).toBeNull();
    expect(rest(row(8))).toBeNull();
  });

  it.each([
    [1, "md:col-span-3", "block"],
    [2, "md:col-span-2", "hidden"],
    [3, "md:col-span-1", "block"],
    [5, "md:col-span-3", "block"],
    [6, "md:col-span-2", "hidden"],
    [9, "md:col-span-3", "block"],
  ])("fills the places %i stats leave: %s from md, %s below", (n, span, below) => {
    const classes = rest(row(n));
    expect(classes).toContain("bg-panel");
    expect(classes).toContain("md:block");
    expect(classes).toContain(span);
    expect(classes).toContain(below);
  });

  it("keeps every stat, in order, ahead of the empty cell", () => {
    const out = row(3);
    expect(out.match(/Stat \d/g)).toEqual(["Stat 0", "Stat 1", "Stat 2"]);
    expect(out.indexOf("Stat 2")).toBeLessThan(out.indexOf("data-stat-rest"));
  });

  it("hides the empty cell from assistive technology", () => {
    expect(row(1)).toContain('<div aria-hidden="true" data-stat-rest="true"');
  });
});
