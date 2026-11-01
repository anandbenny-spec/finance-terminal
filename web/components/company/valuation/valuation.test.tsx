// The Valuation tab rendered to HTML as the server sends it (2A-20): the lead panel for each status, the assumptions
// table with each input's date and source, the scenario read from the stored cell (no arithmetic, no price beside it),
// the gating (hidden until 2A-26), the IMF context and the banned-words scan (Spec 02A §5.9). Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { ScenarioControls } from "@/components/company/valuation/ScenarioControls";
import { TooltipProvider } from "@/components/ui/tooltip";
import { gridOptions, IMF_CPI, IMF_GDP } from "@/lib/equity/valuation";
import type { ValuationData } from "@/lib/queries/company";
import { bannedWordsIn } from "@/tests/banned-words";
import { company, page } from "@/tests/company-rows";
import { POLICIES, cell, forecast, grid, lenderRun, run } from "@/tests/valuation-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const valuation = (over: Partial<ValuationData> = {}): ValuationData => ({
  runs: [run("reverse_dcf"), run("dcf_default", { inputs: { growth: { used: "0.0812" }, drivers: { margin: "0.2412", wacc: "0.1021" } }, result: 1111 })],
  scenarios: grid(), policies: POLICIES, imf: [forecast(IMF_GDP, "2026-10-13", "2027", 6.5), forecast(IMF_CPI, "2026-10-13", "2027", 4.1)],
  series: [{ key: IMF_GDP, name: "GDP", market: "IN", attribution: "Source: IMF, World Economic Outlook" }, { key: "fred.INDIRLTLT01STM", name: "India 10-year", market: "IN", attribution: "Source: OECD, via FRED" }], ...over,
});
const html = (v: ValuationData | null, over: Parameters<typeof page>[0] = {}) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "valuation", overview: null, valuation: v, ...over })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

describe("the Valuation tab", () => {
  it("is a tab in the URL, marked current", () => {
    const out = html(valuation());
    expect(out).toContain('href="/companies/tcs?tab=valuation"');
    expect(text(out)).toContain("What growth does today's price imply?");
  });

  describe("the lead panel", () => {
    it("shows the implied growth, the delivered growth, the stored gap and the years, labelled model-implied", () => {
      const t = text(html(valuation()));
      expect(t).toContain("Implied revenue growth a year, years 1 to 5 12.1% model-implied");
      expect(t).toContain("Delivered revenue growth, 5-year CAGR 9.1%");
      expect(t).toContain("Gap, implied less delivered +3.0 pts a year");
      expect(t).toContain("Years of history 5");
      expect(t).toContain("It describes what the price implies, not what will happen.");
    });
    it("reads a lender through its implied return on equity", () => {
      const def = run("dcf_default", { valuation_family: "lender", status: "not_applicable", state: "not_applicable", result: null, inputs: null, reason: "A lender's debt is its raw material, so an operating-cash-flow model doesn't apply." });
      const t = text(html(valuation({ runs: [lenderRun(), def], scenarios: [] }), { company: company({ valuation_family: "lender" }) }));
      expect(t).toContain("What return on equity does the price imply?");
      expect(t).toContain("Implied return on equity 15.3% model-implied");
      expect(t).toContain("Delivered return on equity, average 14.1%");
      expect(t).toContain("Years of history 3");
      expect(t).toContain("A lender's debt is its raw material, so an operating-cash-flow model doesn't apply.");
    });
    it("tells a no-solution and a not-applicable run in the stored sentence, with no figures", () => {
      const none = text(html(valuation({ runs: [run("reverse_dcf", { status: "no_solution", state: "held", result: null, reason: "At its 3-year return on capital of 9%, below its cost of capital of 10%, faster growth lowers the model's value, so no growth rate matches the price." })] })));
      expect(none).toContain("faster growth lowers the model's value, so no growth rate matches the price.");
      expect(none).not.toContain("Implied revenue growth");
      const na = text(html(valuation({ runs: [run("reverse_dcf", { status: "not_applicable", state: "not_applicable", result: null, delivered: null, reason: "Book value doesn't capture an insurer's value; embedded value is v2." })] }), { company: company({ valuation_family: "insurer" }) }));
      expect(na).toContain("Book value doesn't capture an insurer's value; embedded value is v2.");
    });
    it("shows a held delivered figure as Held with its reason", () => {
      const t = text(html(valuation({ runs: [run("reverse_dcf", { delivered: null, delivered_reason: "A merger falls inside the window.", inputs: { ...run("reverse_dcf").inputs, gap: undefined, delivered: { value: null, unit: "fraction", as_of: null, source: "s", held: "A merger falls inside the window." } } })] })));
      expect(t).toContain("Delivered revenue growth, 5-year CAGR Held A merger falls inside the window.");
      expect(t).toContain("Gap, implied less delivered —");
    });
    it("says so when no run is published", () => {
      expect(text(html(valuation({ runs: [] })))).toContain("No valuation has been published for this company yet.");
    });
  });

  describe("gating", () => {
    it("holds every model-implied part back while the policies hide them, or have no row", () => {
      for (const policies of [[], POLICIES.map((p) => ({ ...p, status: "hidden" as const }))]) {
        const t = text(html(valuation({ policies })));
        expect(t).toContain("Model-implied figures are held back until their checks pass.");
        expect(t).not.toContain("Implied revenue growth a year");
        expect(t).not.toContain("Latest close");
        expect(t).not.toContain("Model-implied value under selected assumptions");
      }
    });
  });

  describe("the assumptions table", () => {
    const t = text(html(valuation()));
    it("shows each input with its value, date and source", () => {
      expect(t).toContain("Latest close ₹3,456.70 30 Oct 2026 closing price on NSE");
      expect(t).toContain("Revenue, last four quarters ₹2,50,000 cr 30 Jun 2026");
      expect(t).toContain("Risk-free rate 5.31% 01 Sep 2026");
      expect(t).toContain("India 10-year government bond yield (FRED, OECD, monthly)");
      expect(t).toContain("10-year government yield 7.06% less the default spread 1.75%");
      expect(t).toContain("Equity risk premium 7.21% 01 Oct 2026");
      expect(t).toContain("Beta, bottom-up 0.90");
      expect(t).toContain("Industry: Software (System and Application)");
      expect(t).toContain("Cost of debt, before tax 7.31%");
      expect(t).toContain("Bounded up to the gross 10-year yield");
      expect(t).toContain("98.0% equity, 2.0% debt");
      expect(t).toContain("Cost of capital (WACC) 10.21%");
      expect(t).toContain("Terminal growth 5.31%");
      expect(t).toContain("Shares outstanding today 3,60,00,00,000");
      expect(t).toContain("borrowings exclude lease liabilities (not in results filings)");
    });
    it("marks the derived figures", () => {
      expect(t).toContain("derived Derived by the model from the filings");
    });
    it("carries the source lines for NSE, Damodaran and FRED", () => {
      expect(t).toContain("Source: NSE · Source: Aswath Damodaran, NYU Stern · Source: OECD, via FRED");
    });
  });

  describe("the IMF context", () => {
    it("shows the stored forecasts side by side with the edition named, and the series' own source line", () => {
      const t = text(html(valuation()));
      expect(t).toContain("World Economic Outlook, released 13 Oct 2026");
      expect(t).toContain("2027 6.5 4.1");
      expect(t).toContain("Source: IMF, World Economic Outlook");
    });
    it("says so when the forecasts can't be read, and the rest of the tab stands", () => {
      const t = text(html(valuation({ imf: null, series: null })));
      expect(t).toContain("The IMF's forecasts for India aren't published here yet.");
      expect(t).toContain("Implied revenue growth");
    });
  });

  describe("set your own scenario", () => {
    const options = gridOptions(grid());
    const centre = { growth: 0.075, margin: 0.2412, wacc: 0.1021 };
    const scenario = (initial = centre, rows = grid()) => text(renderToStaticMarkup(<ScenarioControls rows={rows} options={options} initial={initial} currency="INR" />));
    it("shows the stored cell's value under the required label, exactly as stored", () => {
      const t = scenario();
      expect(t).toContain("Model-implied value under selected assumptions");
      expect(t).toContain("₹1,111.00 per share");
      expect(scenario({ growth: 0.1, margin: 0.2112, wacc: 0.0921 })).toContain("₹1,200.00 per share");
    });
    it("never shows the price, a comparison or an upside", () => {
      const t = scenario();
      expect(t).not.toContain("3,456.70");
      expect(bannedWordsIn(t)).toEqual([]);
      expect(t).not.toMatch(/%\s*(above|below)|versus|vs\./i);
    });
    it("gives the value no colour", () => {
      const out = renderToStaticMarkup(<ScenarioControls rows={grid()} options={options} initial={centre} currency="INR" />);
      expect(out).not.toMatch(/text-(up|down)\b/);
    });
    it("shows a null cell's stored reason, and says so for a combination the grid skipped", () => {
      const rows = [...grid().filter((r) => !(r.growth === 0.075 && r.ebit_margin === 0.2412 && r.wacc === 0.1021)), cell(0.075, 0.2412, 0.1021, null, { reason: "The cost of capital isn't above the terminal growth rate, so the model has no terminal value." })];
      expect(scenario(centre, rows)).toContain("The cost of capital isn't above the terminal growth rate");
      expect(scenario(centre, grid().filter((r) => r.growth !== 0.075))).toContain("The stored grid has no value for this combination.");
    });
    it("names its three controls and starts from the grid's centre in the tab", () => {
      const t = text(html(valuation()));
      for (const label of ["Revenue growth a year, years 1 to 5", "EBIT margin", "Cost of capital (WACC)"]) expect(t).toContain(label);
      expect(t).toContain("₹1,111.00 per share");
    });
    it("says why there is no grid for a company the model doesn't run on", () => {
      const t = text(html(valuation({ scenarios: [], runs: [run("reverse_dcf"), run("dcf_default", { status: "not_applicable", state: "not_applicable", reason: "EBIT over the last four quarters is zero or negative.", result: null, inputs: null })] })));
      expect(t).toContain("EBIT over the last four quarters is zero or negative.");
    });
  });

  it("finds no banned word in the site's own text, for either kind of company", () => {
    expect(bannedWordsIn(text(html(valuation())))).toEqual([]);
    expect(bannedWordsIn(text(html(valuation({ runs: [lenderRun()] }), { company: company({ valuation_family: "lender" }) })))).toEqual([]);
  });
});
