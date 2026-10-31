// /companies/[slug] rendered to HTML as the server sends it (2A-19): the header, the held-back prices note, the key
// figures (including a lender's N/A figures and told-apart held states), the peers table, the statements with their
// dated filing links, the basis toggle, the promoter and corporate-action panels, and the banned-words scan over the
// site's own text (Spec 02A §5.9). Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { action, company, financial, metric, page, peer, price, promoter } from "@/tests/company-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const FRESH: TaskFreshnessRow[] | null = null;
const html = (data: ReturnType<typeof page>, basis: "consolidated" | "standalone" = "consolidated") =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={data} basis={basis} freshness={FRESH} />
    </TooltipProvider>,
  );

describe("the header", () => {
  const out = html(page());
  it("names the company, its identifiers, curated peer group and index chips", () => {
    const t = text(out);
    expect(t).toContain("Tata Consultancy Services Ltd.");
    expect(t).toContain("TCS");
    expect(t).toContain("INE467B01029");
    expect(t).toContain("532540");
    expect(t).toContain("IT services curated");
    expect(t).toContain("Nifty 50");
  });
  it("shows the last close with its date and the day's change with a sign and a triangle", () => {
    const t = text(out);
    expect(t).toContain("₹3,456.70");
    expect(t).toContain("Close on 30 Oct 2026");
    expect(t).toContain("+1.67%");
    expect(out).toContain("up 1.67 percent");
    expect(out).toContain("<svg");
  });
  it("holds the day's change back across a split and says why", () => {
    const t = text(html(page({ actions: [action({ kind: "split", ex_date: "2026-10-30" })] })));
    expect(t).not.toContain("+1.67%");
    expect(t).toContain("Day's change held back");
  });
  it("offers only the Overview and Financials tabs, the current one marked", () => {
    expect(out).toContain('aria-current="page"');
    expect(out).toContain('href="/companies/tcs?tab=financials"');
    expect(text(out)).not.toContain("Valuation");
    expect(text(out)).not.toContain("Rate decisions");
  });
  it("says Source: NSE and carries no link to module 1's pages", () => {
    expect(text(out)).toContain("Source: NSE");
    expect(out).not.toContain('href="/policy');
  });
});

describe("the Overview", () => {
  it("names the date earlier prices are held back to", () => {
    const t = text(html(page({ overview: { prices: [price("2026-09-30", 100, null), price("2026-10-01", 105, 105)], metrics: [], peers: [] } })));
    expect(t).toContain("Prices up to 30 Sep 2026 are held back while a corporate action is checked.");
    expect(t).toContain("Price, excluding dividends");
  });
  it("says there are no prices yet when none are stored", () => {
    expect(text(html(page()))).toContain("No prices are published for this company yet.");
  });
  it("shows each key figure as stored, with its label, and told-apart held and N/A states", () => {
    const metrics = [
      metric("pe_ttm", { value: 25.43 }),
      metric("pb", { value: 9.1, period_end: "2026-10-30" }),
      metric("ev_ebit_ttm", { state: "held", value: null, held_reason: "The balance sheet and the last four quarters are on different bases." }),
      metric("dividend_yield", { value: 0.0123, basis: "none" }),
      metric("roe", { period_kind: "year", period_end: "2026-03-31", value: 0.4512 }),
      metric("ebit_margin", { period_kind: "ttm", period_end: "2026-06-30", value: 0.2412, label_kind: "derived" }),
      metric("net_margin", { period_kind: "ttm", state: "not_applicable", value: null, held_reason: "not meaningful for a lender" }),
      metric("beta_regression", { value: 0.845 }),
      metric("beta_bottom_up", { value: 0.91, label_kind: "bottom-up, relevered" }),
    ];
    const out = html(page({ overview: { prices: [], metrics, peers: [] } }));
    const t = text(out);
    expect(t).toContain("25.4×");
    expect(t).toContain("9.1×");
    expect(t).toContain("1.23%");
    expect(t).toContain("45.1%");
    expect(t).toContain("24.1% derived");
    expect(t).toContain("0.85");
    expect(t).toContain("bottom-up, relevered");
    expect(t).toContain("Held The balance sheet and the last four quarters are on different bases.");
    expect(t).toContain("N/A not meaningful for a lender");
    expect(t).toContain("Source: Aswath Damodaran, NYU Stern");
  });
  it("reads a lender's EV/EBIT as N/A and the peers' EV columns too", () => {
    const lender = page({
      company: company({ valuation_family: "lender", slug: "hdfcbank", name: "HDFC Bank Ltd." }),
      overview: { prices: [], metrics: [], peers: [peer("icicibank", { ev_ebit: null, ev_sales: null })] },
    });
    const t = text(html(lender));
    expect(t).toContain("N/A Not meaningful for a lender or an insurer.");
    expect(t.match(/N\/A/g)!.length).toBeGreaterThanOrEqual(4);
  });
  it("lists the peers with a median row, or the stored note when there are too few", () => {
    const peers = [peer("infy"), peer("wipro", { pe_ttm: null }), { ...peer("x"), row_kind: "median" as const, peer_slug: null, peer_name: null, peer_short_name: null, pe_ttm: 23.1, peer_count: 4 }];
    const t = text(html(page({ overview: { prices: [], metrics: [], peers } })));
    expect(t).toContain("INFY");
    expect(t).toContain("Median of the other members (4)");
    expect(t).toContain("23.1×");
    expect(t).toContain("Not available");
    const few = [peer("infy"), { ...peer("x"), row_kind: "median" as const, peer_slug: null, pe_ttm: null, peer_count: 1, median_note: "Too few peers in the Nifty 50 for a median" }];
    expect(text(html(page({ overview: { prices: [], metrics: [], peers: few } })))).toContain("Too few peers in the Nifty 50 for a median.");
    expect(html(page({ overview: { prices: [], metrics: [], peers } }))).toContain('href="/companies/infy"');
  });
  it("says so when the company has no peer group", () => {
    const t = text(html(page({ company: company({ peer_group_label: null, peer_group_key: null }) })));
    expect(t).toContain("No peer group is published for this company.");
  });
});

describe("the Financials tab", () => {
  const quarters = ["2026-06-30", "2026-03-31", "2025-12-31"].map((e) => financial("quarter", e, { ebit_derived: e === "2026-03-31" ? null : 1.1e11, ebit_derived_state: e === "2026-03-31" ? "held" : "value", ebit_derived_held_reason: e === "2026-03-31" ? "Held back until its checks pass." : null }));
  const base = (over: Partial<ReturnType<typeof page>> = {}) =>
    page({
      tab: "financials",
      overview: null,
      financials: { financials: [...quarters, financial("year", "2026-03-31"), financial("instant", "2026-03-31", { total_assets: 1.5e12, borrowings_current: 1e9 }), financial("half_year", "2025-09-30", { cfo: 8e10 })], metrics: [], promoter: [] },
      ...over,
    });
  it("shows the statements in crore with dated columns linked to their filings, EBIT labelled derived", () => {
    const out = html(base());
    const t = text(out);
    expect(t).toContain("Quarterly results");
    expect(t).toContain("Annual results");
    expect(t).toContain("Balance sheet");
    expect(t).toContain("Cash flow");
    expect(t).toContain("30 Jun 2026");
    expect(t).toContain("₹65,000 cr");
    expect(t).toContain("EBIT derived");
    expect(t).toContain("Held: Held back until its checks pass.");
    expect(out).toContain('href="https://example.test/filing/2026-06-30"');
    expect(t).toContain("Filed 30 Jun 2026");
    expect(t).toContain("Borrowings exclude lease liabilities (not in results filings).");
  });
  it("renders the basis toggle in the URL and falls back, with a note, to the basis that has rows", () => {
    const out = html(base());
    expect(out).toContain('href="/companies/tcs?tab=financials&amp;basis=standalone"');
    const standaloneOnly = base({ financials: { financials: [financial("quarter", "2026-06-30", { basis: "standalone" })], metrics: [], promoter: [] } });
    const t = text(html(standaloneOnly, "consolidated"));
    expect(t).toContain("No consolidated statements are published for this company; showing standalone.");
  });
  it("shows a lender's bank ratios on the standalone basis, ROA annualised and the NIM proxy labelled", () => {
    const bankRows = [financial("quarter", "2026-06-30", { basis: "standalone", format: "bank", interest_earned: 9e11, gnpa_ratio: 0.0124, nnpa_ratio: 0.0031, cet1_ratio: 0.1725 })];
    const metrics = [
      metric("roa", { period_kind: "quarter", basis: "standalone", period_end: "2026-06-30", value: 0.0112, label_kind: "annualised" }),
      metric("nim_proxy", { period_kind: "quarter", basis: "standalone", period_end: "2026-06-30", value: 0.031, label_kind: "proxy, annualised" }),
    ];
    const out = html(page({ tab: "financials", overview: null, company: company({ valuation_family: "lender" }), financials: { financials: bankRows, metrics, promoter: [] } }));
    const t = text(out);
    expect(t).toContain("Bank ratios");
    expect(t).toContain("Gross NPA ratio");
    expect(t).toContain("1.24%");
    expect(t).toContain("17.25%");
    expect(t).toContain("Return on assets annualised");
    expect(t).toContain("1.12%");
    expect(t).toContain("Net interest margin proxy, annualised");
    expect(t).toContain("3.10%");
    expect(t).toContain("Interest earned");
  });
  it("has no bank ratios panel for a company that isn't a lender", () => {
    expect(text(html(base()))).not.toContain("Bank ratios");
  });
  it("tells the promoter note for a corrected quarter in words, linked to the filing, and a missing view", () => {
    const rows = [promoter("2021-12-31", { promoter_pct: 66.13, correction: "percent_for_fraction", label_kind: "derived", filed_pct: 6613, share_ratio_pct: 66.13, nse_pct: 66.1 }), promoter("2026-06-30")];
    const withNote = html(base({ financials: { financials: quarters, metrics: [], promoter: rows } }));
    const t = text(withNote);
    expect(t).toContain("Filed as 6,613%");
    expect(t).toContain("shown as 66.13%");
    expect(t).toContain("66.13% derived");
    expect(t).toContain("72.30%");
    expect(withNote).toContain('href="https://example.test/sh/2021-12-31"');
    expect(text(html(base({ financials: { financials: quarters, metrics: [], promoter: null } })))).toContain("Promoter holding isn't published yet.");
  });
  it("lists upcoming corporate actions apart, says when none are announced, and tells each factor's status in words", () => {
    const list = [
      action({ status: "announced", ex_date: "2026-11-20", subject: "Dividend - Rs 11 Per Share" }),
      action({ kind: "bonus", ex_date: "2024-06-01", subject: "Bonus 1:1", factor_status: "applied", price_factor: 0.5, dividend_per_share: null }),
      action({ kind: "split", ex_date: "2023-06-01", subject: "Face Value Split", factor_status: "held", factor_reason: "Under review: the ratio disagrees.", dividend_per_share: null }),
    ];
    const t = text(html(base({ actions: list })));
    expect(t).toContain("Upcoming");
    expect(t).toContain("20 Nov 2026");
    expect(t).toContain("Announced");
    expect(t).toContain("Applied to earlier prices");
    expect(t).toContain("Under review");
    expect(t).toContain("Bonus issue");
    expect(text(html(base()))).toContain("No corporate action is announced with an ex-date ahead.");
  });
});

describe("the banned-words scan", () => {
  it("finds none in the site's own text on either tab, even with a corporate action quoted as filed", () => {
    const overview = html(page({ overview: { prices: [price("2026-10-01", 1, 1)], metrics: [metric("pe_ttm")], peers: [peer("infy")] } }));
    const financials = html(
      page({
        tab: "financials",
        overview: null,
        financials: { financials: [financial("quarter", "2026-06-30")], metrics: [], promoter: [promoter("2026-06-30")] },
        actions: [action({ kind: "buyback", subject: "Buy Back of Shares" })],
      }),
    );
    expect(bannedWordsIn(text(overview))).toEqual([]);
    // The one quoted subject is source text and is skipped, exactly as the scan is defined (Spec 02A §5.9).
    expect(bannedWordsIn(text(financials).replace("Buy Back of Shares", ""))).toEqual([]);
  });
});
