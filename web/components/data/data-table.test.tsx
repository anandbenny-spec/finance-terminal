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

  it("positions the scroller, so cells' sr-only text stays inside it instead of stretching the page", () => {
    // The 28 Oct 2026 bug: `.sr-only` is absolutely positioned. With no positioned ancestor it escapes the scroller's
    // clip and sits at its row's offset in the page, so rows scrolled out of view left black space below the footer
    // (974 such spans on /policy: the page was 5,385 px tall against 2,618 px of content). Found in a browser; a
    // unit test can't measure layout, so this holds the cause: the scroller is `relative` (class order aside).
    const withSrOnly = renderToStaticMarkup(
      <DataTable
        columns={[{ id: "value", header: "Value", cell: (r: Row) => <span className="sr-only">{r.value}</span> }]}
        rows={ROWS}
        rowKey={(r) => r.id}
        caption="Test rows"
        scroll
        empty="None"
      />,
    );
    const scroller = withSrOnly.match(/^<div class="([^"]*)"/)?.[1].split(" ") ?? [];
    expect(withSrOnly).toContain('class="sr-only"');
    expect(scroller).toContain("relative");
    expect(scroller).toContain("overflow-auto");
  });

  it("positions the sideways-only scroller too, so a wide table's sr-only caption can't stretch the page at 320 px", () => {
    // 3 Nov 2026, found in a browser on /companies/sbin?tab=financials (#132): the same cause as the 28 Oct bug above, in
    // the variant without `scroll`. The caption is `.sr-only` (absolute) at the table's static position; with no
    // positioned ancestor it escaped the 286 px scroller and set the page's width (899 px at 320 px). Holds the cause.
    const out = table();
    const scroller = out.match(/^<div class="([^"]*)"/)?.[1].split(" ") ?? [];
    expect(out).toContain('<caption class="sr-only">');
    expect(scroller).toContain("relative");
    expect(scroller).toContain("overflow-x-auto");
  });

  it("keeps a table without it at full height, scrolling only sideways, and out of the tab order", () => {
    const out = table();
    expect(out).toMatch(/^<div class="relative overflow-x-auto">/);
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

// 12 Nov 2026, found in a browser at 375 px on /screener: the first column is sticky, and a long company name made it
// 359 px wide where the table had 341 px, so every other column scrolled under it and the screener showed only names.
// A unit test can't measure layout, so this holds the cause: below md the first column is capped and its text wraps.
describe("DataTable's sticky first column on a phone", () => {
  const WIDE: Column<Row>[] = [
    { id: "name", header: "Company", cell: (r) => r.value },
    { id: "price", header: "Price", align: "right", cell: (r) => r.id },
  ];
  const out = renderToStaticMarkup(<DataTable columns={WIDE} rows={ROWS.slice(0, 2)} rowKey={(r) => r.id} caption="Test rows" empty="None" />);
  const classesOf = (tag: "th" | "td") => [...out.matchAll(new RegExp(`<${tag}(?=[\\s>])[^>]* class="([^"]*)"`, "g"))].map((m) => m[1].split(" "));

  it("caps the first column's width below md and lets it wrap, header and cells alike", () => {
    for (const cells of [classesOf("th"), classesOf("td")]) {
      const first = cells.filter((c) => c.includes("sticky"));
      expect(first.length).toBeGreaterThan(0);
      for (const c of first) expect(c).toEqual(expect.arrayContaining(["left-0", "max-md:max-w-44", "max-md:whitespace-normal"]));
    }
  });
  it("leaves every other column as it was: never capped, never wrapped", () => {
    const others = [...classesOf("th"), ...classesOf("td")].filter((c) => !c.includes("sticky"));
    expect(others.length).toBeGreaterThan(0);
    for (const c of others) expect(c.join(" ")).not.toMatch(/max-md:/);
  });
});

