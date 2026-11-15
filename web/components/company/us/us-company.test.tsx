// A US company's page (US-06, #405; Spec 02A.1 §7), rendered to HTML as the server sends it: the header (ticker, exchange, CIK,
// curated peer group, the Dow chip, the last close in dollars with its Delta); only the Overview, Financials and Valuation tabs,
// a deep link to another saying why; statements from SEC EDGAR with each filing's sec.gov link, in dollar millions; prices from
// Yahoo Finance; the valuation's US sources; no market edge (one market per page); no rupee anywhere; the banned-words scan.
// India's page is the other tests' (company.test.tsx and the rest), unchanged. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { ValueChainNotCovered } from "@/components/company/us/ValueChainNotCovered";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TabId } from "@/lib/equity/company";
import type { CompanyRow, FieldPolicyRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { action, company, financial, metric, page, peer, price } from "@/tests/company-rows";
import { OPERATING_INPUTS, grid, run } from "@/tests/valuation-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (data: ReturnType<typeof page>, basis: "consolidated" | "standalone" = "consolidated") =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={data} basis={basis} freshness={null} />
    </TooltipProvider>,
  );

const US = { market: "US", currency: "USD", source_key: "sec" } as const;
const AAPL: Partial<CompanyRow> = {
  slug: "us-aapl", name: "Apple Inc.", short_name: "Apple", market: "US", currency: "USD", isin: null, nse_symbol: null, bse_code: null,
  nse_industry: null, ticker: "AAPL", cik: "320193", peer_group_key: "us-tech", peer_group_label: "US technology hardware", price_exchange: "NASDAQ",
  fy_end_month: 9, index_slugs: ["dow-jones"], last_close: 228.52, previous_close: 225, last_close_date: "2026-11-06", previous_close_date: "2026-11-05",
  price_source_key: "yfinance",
};
const usPage = (over: Parameters<typeof page>[0] = {}) =>
  page({ company: company(AAPL), indexNames: { "dow-jones": "Dow Jones Industrial Average" }, today: "2026-11-08", ...over });
const sec = (end: string) => `https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-${end}.htm`;
const usFin = (kind: Parameters<typeof financial>[0], end: string, over: Parameters<typeof financial>[2] = {}) =>
  financial(kind, end, {
    ...US, slug: "us-aapl", format: "us_gaap", source_url: sec(end), attribution: "Source: SEC EDGAR", ebit_derived_label: "operating income as filed",
    revenue: 94_930_000_000, ebit_derived: 29_591_000_000, finance_cost: null, pbt: 29_610_000_000, tax: 4_528_000_000, pat_owners: 25_082_000_000, pat: null,
    eps_basic: 1.65, eps_diluted: 1.64, ...over,
  });
const FINANCIALS = {
  financials: [
    usFin("quarter", "2026-06-27"),
    usFin("quarter", "2026-09-26"),
    usFin("year", "2026-09-26", { revenue: 416_161_000_000, cfo: 118_254_000_000, cfi: -9_000_000_000, cff: -110_000_000_000, capex: 9_447_000_000 }),
    usFin("nine_months", "2026-06-27"),
    usFin("instant", "2026-09-26", { revenue: null, total_assets: 364_980_000_000, cash: 29_943_000_000, total_equity: 56_950_000_000, equity_owners: 56_950_000_000, borrowings_current: 10_912_000_000, borrowings_noncurrent: 85_750_000_000 }),
  ],
  metrics: [],
  promoter: null,
};
const POLICIES: FieldPolicyRow[] = [
  { field_key: "equity.implied_growth", status: "labelled", label: "model-implied" },
  { field_key: "equity.dcf_scenario", status: "labelled", label: "Model-implied value under selected assumptions" },
];
const US_INPUTS = {
  ...OPERATING_INPUTS,
  market: {
    risk_free: { value: "0.0412", unit: "fraction", as_of: "2026-11-06", source: "10-year Treasury yield (Federal Reserve Board, H.15, via FRED), not netted of a US default spread", detail: { us_10y_pct: "4.12" } },
    erp: { value: "0.042", unit: "fraction", as_of: "2026-11-01", source: "Implied equity risk premium of a mature market, with no country risk premium (Aswath Damodaran, NYU Stern: ERPbymonth)", detail: { mature_erp_pct: "4.20" } },
    marginal_tax: { value: "0.25", unit: "fraction", as_of: "2026-01-01", source: "US federal and state marginal rate (Damodaran)" },
  },
  price: { value: "228.52", unit: "money", as_of: "2026-11-06", source: "the latest close (Source: Yahoo Finance; equity.metrics)" },
  company: {
    ...OPERATING_INPUTS.company,
    beta: { value: "1.12", unit: "beta", as_of: "2026-11-06", source: "bottom-up beta: Damodaran's US industry beta relevered at market D/E (equity.beta)", detail: { industry: "Computers/Peripherals" } },
  },
  capital: { ...OPERATING_INPUTS.capital, cost_of_debt_bounded: null },
};
const usRun = (kind: "reverse_dcf" | "dcf_default") => run(kind, { ...US, slug: "us-aapl", name: "Apple", price: 228.52, inputs: US_INPUTS });
const VALUATION = {
  runs: [usRun("reverse_dcf"), usRun("dcf_default")],
  scenarios: grid().map((c) => ({ ...c, slug: "us-aapl", market: "US" as const, currency: "USD" as const })),
  policies: POLICIES,
  imf: null,
  series: [{ key: "fred.DGS10", name: "US 10-year Treasury yield", market: "US" as const, attribution: "Source: U.S. Treasury, via Federal Reserve Board (H.15) and FRED" }],
};
const OVERVIEW = {
  prices: [price("2026-11-05", 225, 225, { ...US, slug: "us-aapl", exchange: "NASDAQ", source_key: "yfinance" }), price("2026-11-06", 228.52, 228.52, { ...US, slug: "us-aapl", exchange: "NASDAQ", source_key: "yfinance" })],
  metrics: [
    metric("pe_ttm", { ...US, value: 34.7 }),
    metric("ebit_margin", { ...US, period_kind: "ttm", value: 0.3151, label_kind: "operating income as filed" }),
    metric("beta_bottom_up", { ...US, value: 1.12, label_kind: "bottom-up, relevered" }),
  ],
  peers: [
    peer("us-msft", { ...US, company_slug: "us-aapl", peer_nse_symbol: null, peer_ticker: "MSFT", peer_name: "Microsoft Corp.", peer_short_name: "Microsoft" }),
    peer("median", { ...US, company_slug: "us-aapl", row_kind: "median", peer_slug: null, peer_name: null, peer_short_name: null, peer_nse_symbol: null, median_note: "Too few peers in the Dow 30 for a median" }),
  ],
};
const DIVIDEND = action({ ...US, slug: "us-aapl", exchange: "NASDAQ", subject: "Dividend (Yahoo Finance) 0.26 per share", dividend_per_share: 0.26, ex_date: "2026-08-11", source_key: "yfinance", attribution: "Source: Yahoo Finance" });

const overview = html(usPage({ overview: OVERVIEW, actions: [DIVIDEND] }));
const financials = html(usPage({ tab: "financials", overview: null, financials: FINANCIALS, actions: [DIVIDEND] }));
const valuation = html(usPage({ tab: "valuation", overview: null, valuation: VALUATION }));
const ALL = [overview, financials, valuation];

describe("a US company's header", () => {
  const t = text(overview);
  it("shows the ticker, listing exchange and SEC CIK, not India's identifiers", () => {
    expect(t).toContain("Ticker AAPL");
    expect(t).toContain("Exchange NASDAQ");
    expect(t).toContain("SEC CIK 320193");
    expect(t).not.toMatch(/NSE symbol|ISIN|BSE code/);
  });
  it("shows the curated peer group, the Dow chip and the last close in dollars with its date and Delta", () => {
    expect(t).toContain("US technology hardware curated");
    expect(t).toContain("Dow Jones Industrial Average");
    expect(t).toContain("$228.52");
    expect(t).toContain("Close on 6 Nov 2026");
    expect(t).toContain("+1.56%");
    expect(overview).toContain("up 1.56 percent");
  });
  it("names its sources, SEC EDGAR and Yahoo Finance, and never NSE", () => {
    for (const out of ALL) {
      expect(text(out)).toContain("Source: SEC EDGAR");
      expect(text(out)).toContain("Source: Yahoo Finance");
      expect(text(out)).not.toContain("Source: NSE");
    }
  });
  it("links its breadcrumb to the screener's US view", () => {
    expect(overview).toContain('href="/screener?market=us"');
  });
});

describe("a US company's tabs", () => {
  it("offers Overview, Financials and Valuation only", () => {
    expect(overview).toContain('href="/companies/us-aapl"');
    expect(overview).toContain('href="/companies/us-aapl/financials"');
    expect(overview).toContain('href="/companies/us-aapl/valuation"');
    expect(overview).not.toContain('href="/companies/us-aapl/calls"');
    expect(overview).not.toContain('href="/companies/us-aapl/rate-decisions"');
    expect(text(overview)).not.toMatch(/Earnings calls|Rate decisions/);
  });
  it.each<[TabId, string]>([
    ["calls", "US earnings calls aren't covered: no free transcript source."],
    ["rate-decisions", "Rate decisions aren't covered for US companies yet"],
  ])("answers a deep link to %s with a StateNote saying why, and nothing else", (tab, reason) => {
    const t = text(html(usPage({ tab, overview: null })));
    expect(t).toContain(reason);
    expect(t).not.toMatch(/Couldn't load|Opening remarks|decision days/);
  });
  it("shows the value chain's sub-page as not covered, with the reason", () => {
    const t = text(renderToStaticMarkup(<TooltipProvider><ValueChainNotCovered company={company(AAPL)} actions={[]} indexNames={{}} /></TooltipProvider>));
    expect(t).toContain("The value chain isn't covered for US companies");
    expect(t).toContain("Ticker AAPL");
    expect(t).not.toContain("Source: NSE");
  });
});

describe("a US company's Overview", () => {
  const t = text(overview);
  it("has no value chain panel (an Indian disclosure)", () => {
    expect(t).not.toMatch(/Value chain|BRSR/);
  });
  it("shows key figures with the US label and peers within the Dow 30, with the stored note", () => {
    expect(t).toContain("34.7×");
    expect(t).toContain("31.5%");
    expect(t).toContain("operating income as filed");
    expect(t).toContain("Microsoft");
    expect(overview).toContain('href="/companies/us-msft"');
    expect(t).toContain("Too few peers in the Dow 30 for a median.");
  });
});

describe("a US company's Financials", () => {
  const t = text(financials);
  it("shows fiscal quarters and years in dollar millions with US grouping, never a rupee", () => {
    expect(t).toContain("$94,930 mn");
    expect(t).toContain("$416,161 mn");
    expect(t).toContain("$1.65");
    for (const out of ALL) expect(text(out)).not.toMatch(/₹| cr\b|crore|\bINR\b|\blakh/);
  });
  it("reads EBIT as operating income as filed, and names US GAAP lines", () => {
    expect(t).toContain("EBIT operating income as filed");
    expect(t).toContain("$29,591 mn");
    expect(t).toMatch(/Income before income taxes.*Net income attributable to the company.*Earnings per share, diluted/);
    expect(t).not.toMatch(/Other income|Finance costs|Profit for the period|Trade receivables/);
  });
  it("links each column to its filing on sec.gov", () => {
    expect(financials).toContain(`href="${sec("2026-09-26")}"`);
    expect(financials).toContain(`href="${sec("2026-06-27")}"`);
  });
  it("shows balance sheets by quarter end and cash flow by fiscal year, with the derived fourth quarter and the debt said in words", () => {
    expect(t).toContain("What does it own and owe at each quarter end?");
    expect(t).toContain("$364,980 mn");
    expect(t).toContain("$85,750 mn");
    expect(t).toContain("$118,254 mn");
    expect(t).toContain("−$9,000 mn");
    expect(t).toContain("A fourth quarter is the fiscal year less the first nine months");
    expect(t).toContain("Debt is as filed; lease liabilities aren't counted.");
    expect(t).not.toContain("Nine months");
  });
  it("has no basis toggle, promoter, bank ratio or segment panel, and lists Yahoo Finance's dividends", () => {
    expect(t).not.toMatch(/Statements on a|Standalone|Promoter|Bank ratios|Segment changes/);
    expect(t).toContain("As listed");
    expect(t).toContain("$0.26");
  });
  it("says it shows consolidated statements when a standalone link is followed", () => {
    expect(text(html(usPage({ tab: "financials", overview: null, financials: FINANCIALS }), "standalone"))).toContain(
      "No standalone statements are published for this company; showing consolidated.",
    );
  });
});

describe("a US company's Valuation", () => {
  const t = text(valuation);
  it("names the US inputs' sources: Damodaran and the 10-year Treasury via FRED; no IMF panel for India", () => {
    expect(t).toContain("Source: Aswath Damodaran, NYU Stern");
    expect(t).toContain("Source: U.S. Treasury, via Federal Reserve Board (H.15) and FRED");
    expect(t).toContain("10-year Treasury yield (Federal Reserve Board, H.15, via FRED)");
    expect(t).not.toMatch(/IMF|India/);
  });
  it("shows the assumptions in dollars, the debt weights in US words, and no upside or verdict", () => {
    expect(t).toContain("$228.52");
    expect(t).toContain("$2,500,000 mn");
    expect(t).toContain("Market capitalisation and debt as filed; lease liabilities aren't counted");
    expect(t).toContain("Interest expense over average debt");
    expect(t).not.toContain("results filings");
  });
  it("shows no internal task name or file key in the Assumptions table's Source column (Review 05, finding 3)", () => {
    expect(t).not.toMatch(/\b(equity|us|policy|core|ops|site|ai|semantic|api)\.[a-z_]+\b/);
    expect(t).not.toMatch(/ERPbymonth|ctryprem/);
  });
});

describe("every US tab", () => {
  it("uses none of the banned words", () => {
    for (const out of [...ALL, html(usPage({ tab: "calls", overview: null }))]) expect(bannedWordsIn(text(out))).toEqual([]);
  });
  it("carries no market edge: the page is about one market (CLAUDE.md §5)", () => {
    for (const out of ALL) expect(out).not.toMatch(/border-t-series-[12]/);
  });
  it("keeps clear of the sideways scroll at 320 px: the header's US items can shrink, and nothing in them is kept on one line", () => {
    // The US items only (ticker, exchange, CIK); the peer group's Qualifier keeps to one line, as every Qualifier does.
    const items = overview.slice(overview.indexOf("<dl"), overview.indexOf("Peer group"));
    expect(items).toContain("Ticker");
    expect(items.match(/min-w-0/g)?.length).toBeGreaterThanOrEqual(3);
    expect(items).not.toMatch(/whitespace-nowrap|(?<![\w-])w-\d/);
  });
});
