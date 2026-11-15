// The meeting page's layout rules that a browser found and a unit test can't measure (12 Nov 2026; Anand: "half of the
// table is black"). MeetingView's panels are async server components over cached reads, so these hold the causes in
// the source, as data-table.test.tsx does for its layout bugs.
//
// 1. "What changed" capped its whole body at the reading width (Panel's `prose`), the Before / Now table and Numbers
//    that changed included: on a 1,160 px panel they sat in 547 px beside an empty half, and the RBI's tab was
//    11,865 px tall. Now only the AI summary keeps the reading measure; from xl it sits beside Numbers that changed, and
//    Key changes runs the panel's width.
// 2. Statement, Minutes and Press conference spanned the page with their text in the left half. Now the document is
//    the main column with the Key facts rail beside it from xl (Design 01 §5.3), as on the Overview tab.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./MeetingView.tsx", import.meta.url), "utf8");
const between = (from: string, to: string): string => {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start + from.length);
  expect(start, from).toBeGreaterThan(-1);
  expect(end, to).toBeGreaterThan(start);
  return source.slice(start, end);
};

describe("the What changed panel", () => {
  const changes = between("async function Changes(", "\nasync function NumbersPart(");

  it("doesn't cap its whole body at the reading width", () => {
    expect(changes).toContain('titleId="panel-changes"');
    expect(changes).not.toContain('variant="prose"');
  });

  it("puts the AI summary and Numbers that changed side by side from xl, stacked below", () => {
    const top = between("<div data-changes-top", "</div>\n          {model.keys");
    expect(top).toMatch(/className="grid grid-cols-1 [^"]*\bxl:grid-cols-2\b/);
    expect(top).toContain("<AIBlock");
    expect(top).toContain("<NumbersPart");
  });

  it("holds only the AI summary to the reading width", () => {
    const top = between("<div data-changes-top", "</div>\n          {model.keys");
    const capped = top.slice(top.indexOf('<div className="max-w-(--reading-width)">'), top.indexOf("<NumbersPart"));
    expect(capped).toContain("<AIBlock");
    expect(capped).not.toContain("<NumbersPart");
    expect(changes.match(/max-w-\(--reading-width\)/g)).toHaveLength(1);
  });

  it("gives Key changes' table the panel's width, outside the two-column block", () => {
    const afterTop = changes.slice(changes.indexOf("</div>\n          {model.keys"));
    expect(afterTop).toContain("<KeyChanges");
    expect(afterTop).not.toContain("max-w-(--reading-width)");
  });
});

describe("Policy Day's What changed panel shows a preview and doesn't scroll inside itself (R05-6, #441)", () => {
  const changes = between("async function Changes(", "\nasync function NumbersPart(");
  const keys = between("{model.keys && model.redline ? (", "The redline isn&apos;t available");

  it("has no fixed height or inner scroll, flat or not", () => {
    expect(changes).not.toContain("max-h-96");
    expect(changes).not.toContain("overflow-auto");
  });

  it("when flat, Key changes is the preview: three rows and a link to the meeting's What changed tab", () => {
    const flat = keys.slice(keys.indexOf("flat ? ("), keys.indexOf(") : ("));
    expect(flat).toContain("limit={3}");
    expect(flat).toContain('seeAllHref={meetingTabHref(ctx.bank, ctx.m.decision_date, "changes")}');
    expect(flat).not.toContain("redline");
    expect(flat).not.toContain("unchangedParagraphs");
  });

  it("otherwise keeps the redline and the unchanged count, with no limit", () => {
    const full = keys.slice(keys.indexOf(") : (", keys.indexOf("flat ? (")));
    expect(full).toContain("redline={<Redline");
    expect(full).toContain("unchangedParagraphs={model.redline.unchangedParagraphs}");
    expect(full).not.toContain("limit=");
  });
});

describe("the Statement, Minutes and Press conference tabs", () => {
  const tab = between("async function DocumentTab(", "\nfunction StatementSwitch(");

  it("show the document in the main column with the rail beside it from xl", () => {
    expect(tab).toMatch(/className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-12"/);
    expect(tab).toMatch(/className="[^"]*\bxl:col-span-8\b[^"]*">\s*\{switcher\}/);
    expect(tab).toMatch(/className="hidden [^"]*\bxl:col-span-4\b[^"]*\bxl:block\b[^"]*">\s*<Rail ctx=\{ctx\} \/>/);
  });

  it("no longer cap the panel's body: the document keeps its own measure (DocumentView)", () => {
    expect(tab).not.toContain('variant="prose"');
    expect(tab).toContain("<DocumentView");
  });
});
