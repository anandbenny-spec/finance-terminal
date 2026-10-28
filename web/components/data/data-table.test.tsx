// DataTable's height cap (Anand, 28 Oct 2026): with `scroll`, a long table stops at --table-max-h and scrolls inside
// its panel under the sticky header, as a named region a keyboard can focus and scroll (WCAG 2.1.1). Without it, the
// table keeps its full height and scrolls only sideways. /policy's four long tables use it.
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataTable, type Column } from "@/components/data/DataTable";

type Row = { id: string; value: string };
const ROWS: Row[] = Array.from({ length: 40 }, (_, i) => ({ id: `r${i}`, value: `Row ${i}` }));
const COLUMNS: Column<Row>[] = [{ id: "value", header: "Value", sortValue: (r) => r.value, cell: (r) => r.value }];

const table = (scroll?: boolean) =>
  renderToStaticMarkup(
    <DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} caption="Test rows" scroll={scroll} empty="None" />,
  );

describe("DataTable's scroll option", () => {
  it("caps the height and scrolls inside a focusable region named by the caption", () => {
    const out = table(true);
    expect(out).toMatch(/^<div class="[^"]*max-h-\(--table-max-h\) overflow-auto[^"]*" role="region" aria-label="Test rows" tabindex="0">/);
    expect(out).toContain('<thead class="sticky top-0 z-10 bg-raised">');
    expect(out.match(/<tr[ >]/g)?.length).toBe(41); // every row is still rendered: nothing is cut, only scrolled
  });

  it("keeps a table without it at full height, scrolling only sideways, and out of the tab order", () => {
    const out = table();
    expect(out).toMatch(/^<div class="overflow-x-auto">/);
    expect(out).not.toContain("max-h-");
    expect(out).not.toContain('role="region"');
    expect(out).not.toContain("tabindex");
  });

  it("is on for every long table on /policy", () => {
    for (const file of ["HistoryPanel", "ReactionsPanel", "RatesPanel", "LongRatesPanel"]) {
      const source = readFileSync(new URL(`../policy/${file}.tsx`, import.meta.url), "utf8");
      const call = source.slice(source.indexOf("<DataTable"), source.indexOf("/>", source.indexOf("<DataTable")));
      expect(call, file).toMatch(/^\s*scroll$/m);
    }
  });
});
