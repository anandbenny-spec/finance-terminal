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

// Prose keeps the reading width (Design 01 §5.2). `prose` caps the body, for a long document. `columns` (12 Nov 2026)
// is for short copy in a full-width panel: two columns from xl, so the panel isn't half empty; `prose` below xl.
const bodyClass = (out: string): string[] => out.match(/<div class="(min-w-0 flex-1[^"]*)"/)?.[1].split(" ") ?? [];

describe("Panel's prose variants", () => {
  it("caps a prose body at the reading width at every size", () => {
    const body = bodyClass(renderToStaticMarkup(<Panel title="T" variant="prose">x</Panel>));
    expect(body).toContain("max-w-(--reading-width)");
    expect(body.join(" ")).not.toMatch(/columns-/);
  });
  it("runs a columns body in two columns from xl, and caps it at the reading width below", () => {
    const body = bodyClass(renderToStaticMarkup(<Panel title="T" variant="columns">x</Panel>));
    expect(body).toEqual(expect.arrayContaining(["xl:columns-2", "xl:gap-8", "max-xl:max-w-(--reading-width)"]));
    expect(body).not.toContain("max-w-(--reading-width)");
  });
  it("leaves a default panel's body uncapped", () => {
    expect(bodyClass(renderToStaticMarkup(<Panel title="T">x</Panel>)).join(" ")).not.toMatch(/reading-width|columns-/);
  });
});

