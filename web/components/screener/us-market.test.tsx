// The screener's market control (US-06, #405; Spec 02A.1 §7). While the views return no US row (the US companies are
// unpublished: Spec 02A.1 §3) the page has no market control and renders exactly as before, whatever `?market=` says; once
// they do, India's view is unchanged but for the control, and `?market=us` shows the Dow 30 alone, in dollars with US grouping,
// with its own sources (SEC EDGAR, Yahoo Finance), never NSE's, and never a rupee. Test rows, not real figures.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketChips, marketOf, screenerHref } from "@/components/screener/MarketChips";
import { ScreenerView } from "@/components/screener/ScreenerView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { applyFilters, indexesIn, NO_FILTERS, RANGE_FILTERS, rangeFiltersFor, US_RANGE_FILTERS } from "@/lib/equity/screener";
import type { FieldPolicyRow, PricedInRow, ScreenerRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { row } from "@/tests/screener-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

const usRow = (slug: string, over: Partial<ScreenerRow> = {}): ScreenerRow =>
  row(slug, {
    name: `US company ${slug}`, short_name: null, nse_symbol: null, ticker: slug.slice(3).toUpperCase(), market: "US", currency: "USD",
    peer_group_key: "us-tech", peer_group_label: "US technology", price: 1234.5, market_cap: 3.9124e12, promoter_pct: null,
    index_slugs: ["dow-jones"], source_key: "sec", ...over,
  });
const IN_ROWS = [row("tcs", { market_cap: 1.2345e13 }), row("infy")];
const US_ROWS = [usRow("us-aapl"), usRow("us-msft", { market_cap: 3.1e12, return_1y: -0.05 })];
const INDEXES = [
  { slug: "dow-jones", name: "Dow Jones Industrial Average", market: "US" as const },
  { slug: "nifty-50", name: "Nifty 50", market: "IN" as const },
];

const pricedRow = (slug: string, market: "IN" | "US", over: Partial<PricedInRow> = {}): PricedInRow => ({
  slug, name: `Company ${slug}`, short_name: null, nse_symbol: market === "IN" ? slug.toUpperCase() : null, ticker: market === "US" ? slug.slice(3).toUpperCase() : null,
  market, currency: market === "US" ? "USD" : "INR", peer_group_key: null, valuation_family: "operating", as_of: "2026-11-06", state: "value", reason: null,
  implied: 0.12, delivered: 0.08, gap: 0.04, delivered_years: 5, delivered_reason: null, label: "model-implied", gap_rank: 1, source_key: market === "US" ? "sec" : "nse", ...over,
});

const state = vi.hoisted(() => ({ rows: [] as ScreenerRow[], policies: [] as FieldPolicyRow[], priced: [] as PricedInRow[] }));
vi.mock("@/lib/queries/shell", () => ({ readFreshness: () => Promise.resolve({ ok: false }) }));
vi.mock("@/lib/queries/equity", () => ({
  readScreenerPage: () =>
    Promise.resolve({ ok: true, readAt: "2026-11-08T00:00:00Z", rows: state.rows, indexes: INDEXES.filter((i) => state.rows.some((r) => r.market === i.market)), policies: state.policies }),
  readPricedInPage: () => Promise.resolve({ ok: true, readAt: "2026-11-08T00:00:00Z", operating: state.priced, lenders: [] }),
}));

async function render(query: Record<string, string> = {}): Promise<string> {
  const { default: Page } = await import("@/app/screener/page");
  const el = Page({ searchParams: Promise.resolve(query), params: Promise.resolve({}) } as Parameters<typeof Page>[0]);
  const inner = (el.props as { children: { type: (p: unknown) => Promise<ReactNode>; props: unknown } }).children;
  return renderToStaticMarkup(<TooltipProvider>{await inner.type(inner.props)}</TooltipProvider>);
}
const withoutControl = (html: string) => html.replace(/<nav aria-label="Market"[\s\S]*?<\/nav>/, "");
/** The market control's link for a label: its href and whether it is the current market. */
function chip(html: string, label: string): { href: string | null; current: boolean } {
  const tag = html.match(new RegExp(`<a ([^>]*)>${label}</a>`))?.[1] ?? "";
  return { href: tag.match(/href="([^"]*)"/)?.[1] ?? null, current: tag.includes('aria-current="page"') };
}
const PRICED_IN: FieldPolicyRow[] = [
  { field_key: "site.priced_in", status: "shown", label: null },
  { field_key: "equity.implied_growth", status: "labelled", label: "model-implied" },
];

describe("/screener while no US row is returned (the US companies unpublished)", () => {
  beforeEach(() => {
    state.rows = IN_ROWS;
    state.policies = [];
    state.priced = [];
  });
  it("has no market control, and ?market=us changes nothing", async () => {
    const plain = await render();
    expect(plain).not.toContain('aria-label="Market"');
    expect(await render({ market: "us" })).toBe(plain);
    expect(await render({ market: "US" })).toBe(plain);
    expect(text(plain)).toContain("Nifty 50 screener");
    expect(text(plain)).toContain("Source: NSE");
  });
  it("keeps the priced-in view's links and explainer as they were", async () => {
    state.policies = PRICED_IN;
    state.priced = [pricedRow("tcs", "IN")];
    const out = await render({ view: "priced-in" });
    expect(out).toContain('href="/screener?view=priced-in"');
    expect(out).toContain('href="/screener"');
    expect(text(out)).toContain("the risk-free rate in rupees");
    expect(await render({ view: "priced-in", market: "us" })).toBe(out);
  });
});

describe("/screener once the views return US rows", () => {
  beforeEach(() => {
    state.policies = [];
    state.priced = [];
  });
  it("leaves India's view unchanged but for the market control, India current", async () => {
    state.rows = IN_ROWS;
    const before = await render();
    state.rows = [...IN_ROWS, ...US_ROWS];
    const after = await render();
    expect(withoutControl(after)).toBe(before);
    expect(after).toContain('aria-label="Market"');
    expect(chip(after, "India")).toEqual({ href: "/screener", current: true });
    expect(chip(after, "US")).toEqual({ href: "/screener?market=us", current: false });
    expect(text(after)).not.toContain("US company");
  });
  it("shows the Dow 30 alone for ?market=us: dollars in millions with US grouping, tickers, no promoter holding, no rupee", async () => {
    state.rows = [...IN_ROWS, ...US_ROWS];
    const out = await render({ market: "us" });
    const t = text(out);
    expect(t).toContain("Dow 30 screener");
    expect(t).toContain("Which Dow 30 companies fit my filters");
    expect(t).toContain("2 of 2 companies");
    expect(t).toContain("$3,912,400 mn");
    expect(t).toContain("$1,234.50");
    expect(t).toContain("AAPL");
    expect(out).toContain('href="/companies/us-aapl"');
    expect(t).toContain("Market cap ($ million)");
    expect(t).not.toContain("Promoter holding");
    expect(t).toContain("Dow Jones Industrial Average");
    expect(t).not.toContain("TCS");
    expect(t).not.toMatch(/₹| cr\b|\bINR\b|crore/);
    expect(t).toContain("Source: SEC EDGAR");
    expect(t).toContain("Source: Yahoo Finance");
    expect(t).not.toContain("Source: NSE");
    expect(chip(out, "US")).toEqual({ href: "/screener?market=us", current: true });
    expect(chip(out, "India")).toEqual({ href: "/screener", current: false });
    expect(bannedWordsIn(t)).toEqual([]);
  });
  it("keeps the market across the views, and ranks only the market's own rows in What's priced in", async () => {
    state.rows = [...IN_ROWS, ...US_ROWS];
    state.policies = PRICED_IN;
    state.priced = [pricedRow("tcs", "IN"), pricedRow("us-aapl", "US")];
    const out = await render({ view: "priced-in", market: "us" });
    const t = text(out);
    expect(out).toContain('href="/screener?market=us&amp;view=priced-in"');
    expect(out).toContain('href="/screener?market=us"');
    expect(out).toContain('href="/companies/us-aapl/valuation"');
    expect(out).not.toContain('href="/companies/tcs/valuation"');
    expect(t).toContain("the risk-free rate in dollars");
    expect(t).toContain("the latest balance sheet's book value to owners");
    expect(t).not.toContain("Source: NSE");
    const india = text(await render({ view: "priced-in" }));
    expect(india).toContain("TCS");
    expect(india).not.toContain("AAPL");
  });
});

describe("the market control's parts", () => {
  it("reads ?market=us only when the US view exists; India otherwise", () => {
    expect(marketOf("us", true)).toBe("US");
    expect(marketOf("us", false)).toBe("IN");
    expect(marketOf(undefined, true)).toBe("IN");
    expect(marketOf(["us"], true)).toBe("IN");
    expect(marketOf("in", true)).toBe("IN");
  });
  it("builds each address with only the parameters it needs", () => {
    expect(screenerHref("IN", "screener")).toBe("/screener");
    expect(screenerHref("IN", "priced-in")).toBe("/screener?view=priced-in");
    expect(screenerHref("US", "screener")).toBe("/screener?market=us");
    expect(screenerHref("US", "priced-in")).toBe("/screener?market=us&view=priced-in");
  });
  it("wraps at 320 px: a flex-wrap row of short links, no fixed width, nothing kept on one line", () => {
    const out = renderToStaticMarkup(<MarketChips current="US" view="screener" />);
    expect(out).toContain("flex-wrap");
    expect(out).not.toMatch(/whitespace-nowrap|(?<![\w-])w-\d|min-w-(?!0\b)/);
  });
  it("offers the US view's filter boxes: market cap in dollar millions, no promoter holding", () => {
    expect(rangeFiltersFor("IN")).toBe(RANGE_FILTERS);
    expect(US_RANGE_FILTERS.map((f) => f.key)).toEqual(RANGE_FILTERS.map((f) => f.key).filter((k) => k !== "promoter"));
    expect(US_RANGE_FILTERS.find((f) => f.key === "marketCap")?.unit).toBe("$ million");
    const cap = (min: string) => applyFilters(US_ROWS, { ...NO_FILTERS, ranges: { marketCap: { min, max: "" } } }, US_RANGE_FILTERS).map((r) => r.slug);
    expect(cap("3500000")).toEqual(["us-aapl"]);
    expect(cap("3000000")).toEqual(["us-aapl", "us-msft"]);
  });
  it("offers each market's own index chips", () => {
    expect(indexesIn(INDEXES, "US")).toEqual([{ slug: "dow-jones", name: "Dow Jones Industrial Average" }]);
    expect(indexesIn(INDEXES, "IN")).toEqual([{ slug: "nifty-50", name: "Nifty 50" }]);
    expect(indexesIn([{ slug: "nifty-50", name: "Nifty 50" }], "IN")).toEqual([{ slug: "nifty-50", name: "Nifty 50" }]);
  });
  it("renders a US table with no rupee and no promoter column, and India's exactly as before when not told the market", () => {
    const us = text(renderToStaticMarkup(<TooltipProvider><ScreenerView rows={US_ROWS} indexes={[]} policies={[]} footer={null} market="US" /></TooltipProvider>));
    expect(us).toContain("$3,100,000 mn");
    expect(us).not.toMatch(/₹|Promoter/);
    const india = (props: { market?: "IN" }) => renderToStaticMarkup(<TooltipProvider><ScreenerView rows={IN_ROWS} indexes={[]} policies={[]} footer={null} {...props} /></TooltipProvider>);
    expect(india({ market: "IN" })).toBe(india({}));
  });
});
