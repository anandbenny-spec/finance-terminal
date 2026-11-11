// ChangeStripRow and changeStripColumns as a second user places them (#394, 2B-19e): own heading, links, nouns and date
// labels; none of module 1's words; the table part lists the custom columns newest first. Test rows, not real figures.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { changeStripColumns, ChangeStripRow, type ChangeStripRowCopy } from "@/components/ai/ChangeStrip";
import { DataTable } from "@/components/data/DataTable";
import { TooltipProvider } from "@/components/ui/tooltip";
import { barAt, countsToShares, stepBar, type RowBar } from "@/lib/policy/changestrip";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => undefined }) }));

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();

const copy: ChangeStripRowCopy = {
  heading: "Test Co (remarks)",
  emptyNote: <>No call has opening remarks to compare yet.</>,
  groupLabel: "Test Co: share of each call's remarks changed, by call. Left and right choose a call; Enter opens it.",
  chartLabel: "Test Co: remarks added and removed, by call.",
  linkText: "Open the call",
  noBarNote: (n, total) => <>{n} of {total} calls {n === 1 ? "has" : "have"} no bar.</>,
};
const bar = (date: string, label: string, added: number, removed: number): RowBar => ({
  date,
  label,
  addedPct: (100 * added) / 200,
  removedPct: (100 * removed) / 100,
  wordsAdded: added,
  wordsRemoved: removed,
  wordsNew: 200,
  wordsOld: 100,
  href: `/companies/test-co?call=${date}`,
});
const bars = [bar("2026-01-10", "Q3 FY26", 20, 10), bar("2026-04-10", "Q4 FY26", 40, 30)];

describe("ChangeStripRow for a second user", () => {
  const markup = html(<ChangeStripRow id="test-co" bars={bars} withoutBar={1} total={3} scale={60} copy={copy} />);
  const t = text(markup);

  it("shows its own heading, label, link words and counts, and none of module 1's words", () => {
    expect(t).toContain("Test Co (remarks)");
    expect(t).toContain("Q4 FY26");
    expect(t).toContain("Open the call");
    expect(t).toContain("+20.0% added (40 of 200 words)");
    expect(t).toContain("−30.0% removed (30 of 100 words)");
    expect(t).toContain("1 of 3 calls has no bar.");
    expect(markup).toContain('href="/companies/test-co?call=2026-04-10"');
    expect(t).not.toMatch(/Fed|RBI|FOMC|MPC|meeting|statement/);
  });

  it("is a focusable group with the second user's aria label", () => {
    expect(markup).toContain("tabindex=\"0\"");
    expect(markup).toContain('aria-label="Test Co: share of each call&#x27;s remarks changed, by call. Left and right choose a call; Enter opens it."');
  });

  it("says its own note when it has no bar, and draws no group", () => {
    const empty = html(<ChangeStripRow id="test-co" bars={[]} withoutBar={0} total={0} scale={60} copy={copy} />);
    expect(text(empty)).toContain("No call has opening remarks to compare yet.");
    expect(empty).not.toContain('role="group"');
  });

  it("moves between bars the way the bank rows do", () => {
    expect(stepBar({ bars }, null, -1)?.date).toBe("2026-04-10"); // none chosen: the newest
    expect(stepBar({ bars }, "2026-04-10", -1)?.date).toBe("2026-01-10");
    expect(stepBar({ bars }, "2026-04-10", 1)?.date).toBe("2026-04-10"); // clamped at the end
    expect(barAt({ bars }, "2026-01-10")?.label).toBe("Q3 FY26");
  });
});

describe("changeStripColumns", () => {
  const columns = changeStripColumns<RowBar & { company: string }>({ groupHeader: "Company", group: (r) => r.company, dateHeader: "Call", dateCell: (r) => r.label ?? r.date });
  const rows = bars.map((b) => ({ ...b, company: "Test Co" }));
  const out = html(<DataTable columns={columns} rows={rows} rowKey={(r) => r.date} caption="Test table" initialSort={{ id: "meeting", desc: true }} dense scroll empty={<span>No rows.</span>} />);

  it("lists the custom group and date columns, newest first, each date with its link", () => {
    const t = text(out);
    expect(t).toContain("Company");
    expect(t).toContain("Call");
    expect(t).not.toMatch(/Bank|Meeting/);
    expect(out.indexOf("Q4 FY26")).toBeGreaterThan(-1);
    expect(out.indexOf("Q4 FY26")).toBeLessThan(out.indexOf("Q3 FY26"));
    expect(out).toContain('href="/companies/test-co?call=2026-01-10"');
  });
});

describe("countsToShares", () => {
  it("gives shares of the stored counts", () => {
    expect(countsToShares({ wordsAdded: 50, wordsRemoved: 20, wordsOld: 400, wordsNew: 500 })).toEqual({ addedPct: 10, removedPct: 5, wordsNew: 500, wordsOld: 400 });
  });
  it("holds back counts that can't be a share", () => {
    expect(countsToShares({ wordsAdded: 10, wordsRemoved: 5, wordsOld: 0, wordsNew: 100 })).toBeNull();
    expect(countsToShares({ wordsAdded: 101, wordsRemoved: 5, wordsOld: 100, wordsNew: 100 })).toBeNull();
    expect(countsToShares({ wordsAdded: 1.5, wordsRemoved: 5, wordsOld: 100, wordsNew: 100 })).toBeNull();
    expect(countsToShares({ wordsAdded: -1, wordsRemoved: 5, wordsOld: 100, wordsNew: 100 })).toBeNull();
  });
});
