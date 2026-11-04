// Panel's question line: a flush panel's table header starts right under it, so it gets space below; a default panel's
// body padding already does (#290).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Panel } from "@/components/data/Panel";

const subtitleClass = (out: string): string => out.match(/<p class="([^"]*)"/)?.[1] ?? "";

describe("Panel subtitle", () => {
  it("has space below in a flush panel", () => {
    expect(subtitleClass(renderToStaticMarkup(<Panel title="T" subtitle="Q?" variant="flush">x</Panel>))).toMatch(/\bpb-2\b/);
  });
  it("has none extra in a default panel", () => {
    expect(subtitleClass(renderToStaticMarkup(<Panel title="T" subtitle="Q?">x</Panel>))).not.toMatch(/\bpb-2\b/);
  });
  it("draws no market edge unless asked", () => {
    expect(renderToStaticMarkup(<Panel title="T">x</Panel>)).not.toMatch(/border-t-series/);
  });
});
