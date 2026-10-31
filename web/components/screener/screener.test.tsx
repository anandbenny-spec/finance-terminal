// /screener's table and filters rendered to HTML as the server sends them (2A-18): every empty cell says something,
// the held-back model columns follow api.field_policy (a missing row hides them), figures are formatted by the figure's
// market, and no verdict wording appears (Spec 02A §5.9). Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ScreenerView } from "@/components/screener/ScreenerView";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FieldPolicyRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { row } from "@/tests/screener-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const rows = [
  row("tcs", { short_name: "TCS", nse_symbol: "TCS", market_cap: 1.2345e13, price: 3456.7, pe_ttm: 25.43, return_1y: -0.0412 }),
  row("loss", {
    short_name: "LOSS", nse_symbol: "LOSS", pe_ttm: null, ev_ebit: null, debt_to_equity: null,
    notes: { pe_ttm: { label: null, reason: "Profit is not positive." }, debt_to_equity: { label: null, reason: "N/A: not meaningful for a lender" } },
  }),
];
const SHOWN: FieldPolicyRow[] = [
  { field_key: "equity.implied_growth", status: "labelled", label: "model-implied" },
  { field_key: "equity.implied_roe", status: "labelled", label: "model-implied" },
];
const html = (policies: FieldPolicyRow[], data = rows) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <ScreenerView rows={data} indexes={[{ slug: "nifty-50", name: "Nifty 50" }]} policies={policies} footer={<span>Source: NSE</span>} />
    </TooltipProvider>,
  );

describe("ScreenerView", () => {
  it("formats each figure by its market: rupees in crore with Indian grouping, true minus, a Delta with a triangle", () => {
    const out = html([]);
    expect(out).toContain("₹12,34,500 cr");
    expect(out).toContain("₹3,456.70");
    expect(out).toContain("25.4×");
    expect(out).toContain("−4.1%");
    expect(out).toContain("down 4.1 percent");
    expect(out).toContain('href="/companies/tcs"');
  });
  it("reads an empty cell as Held with its reason, N/A with its reason, or a dash with a label, never blank", () => {
    const out = html([]);
    expect(out).toContain('title="Profit is not positive."');
    expect(text(out)).toContain("Held: Profit is not positive.");
    expect(text(out)).toContain("N/A: N/A: not meaningful for a lender");
    expect(out).toContain('title="Not available"');
  });
  it("hides the model-implied columns when their policy is hidden or missing, and says so", () => {
    for (const policies of [[], [{ field_key: "equity.implied_growth", status: "hidden", label: null } as FieldPolicyRow]]) {
      const out = text(html(policies));
      expect(out).not.toContain("Implied growth");
      expect(out).not.toContain("Implied ROE");
      expect(out).toContain("model-implied columns (implied growth, implied ROE) are held back");
    }
  });
  it("shows them, labelled in the header, once their policies show them", () => {
    const out = text(html(SHOWN, [row("a", { implied_growth: 0.12, delivered_growth: 0.08, growth_gap: 0.04, implied_growth_label: "model-implied" })]));
    expect(out).toContain("Implied growth (model-implied)");
    expect(out).toContain("12.0%");
    expect(out).toContain("+4.0 pts");
    expect(out).not.toContain("are held back");
  });
  it("carries the snapshot date, the count, the filters, the source line and a caption, and no blank state", () => {
    const out = text(html([]));
    expect(out).toContain("Weekly snapshot of 30 Oct 2026");
    expect(out).toContain("2 of 2 companies");
    expect(out).toContain("Nifty 50");
    expect(out).toContain("Source: NSE");
    expect(out).toContain("Clear filters");
    expect(html([], [])).toContain("No company matches these filters.");
  });
  it("uses no verdict wording on the page (Spec 02A §5.9)", () => {
    expect(bannedWordsIn(text(html(SHOWN)))).toEqual([]);
    expect(bannedWordsIn("An overvalued stock with upside: buy")).toEqual(["overvalued", "upside", "buy"]);
  });
});
