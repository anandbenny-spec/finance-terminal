// US period dates as filed (US-06b, #564; US-08's final verdict, judgment call 4). A 52/53-week filer's statement rows are
// keyed by the nearest month end (py/src/ft/equity/us_inputs.py, `nearest_month_end`), and the Overview's key figures and the
// Valuation tab's assumptions now show the filed end the Financials tab shows: Home Depot's quarter to 2 Aug 2026 and year to
// 1 Feb 2026, not 31 Jul and 31 Jan. The ends are dev's filed rows (14 Nov 2026); the figures are test rows. No figure moves;
// a date with no filed row is "not available", never the key; India's dates are shown as stored.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { ASSUMPTION_STATEMENT, filedDates, metricDate, nearestMonthEnd, withFiledDates, type FiledEnd } from "@/components/company/us/filedDates";
import { TooltipProvider } from "@/components/ui/tooltip";
import { assumptionRows } from "@/lib/equity/valuation";
import type { CompanyRow, FieldPolicyRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { company, metric, page } from "@/tests/company-rows";
import { OPERATING_INPUTS, lenderRun, run } from "@/tests/valuation-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (data: ReturnType<typeof page>) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={data} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

const ends = (kind: FiledEnd["period_kind"], ...dates: string[]): FiledEnd[] => dates.map((period_end) => ({ period_kind: kind, period_end }));

// Each company's filed ends on dev (api.company_financials, 14 Nov 2026), the latest few of each kind.
const HD = [...ends("quarter", "2026-02-01", "2026-05-03", "2026-08-02"), ...ends("year", "2025-02-02", "2026-02-01"), ...ends("instant", "2026-02-01", "2026-08-02"), ...ends("nine_months", "2025-11-02")];
const AAPL = [...ends("quarter", "2026-06-27", "2026-09-26"), ...ends("year", "2025-09-27", "2026-09-26"), ...ends("instant", "2026-09-26")];
const JNJ = [...ends("quarter", "2025-12-28", "2026-09-27"), ...ends("year", "2024-12-29", "2025-12-28"), ...ends("instant", "2026-09-27")];
const KO = [...ends("quarter", "2026-07-03", "2026-10-02"), ...ends("year", "2025-12-31"), ...ends("instant", "2026-10-02")];
const MSFT = [...ends("quarter", "2026-06-30", "2026-09-30"), ...ends("year", "2026-06-30"), ...ends("instant", "2026-09-30")];

describe("the month key, as the US tasks compute it", () => {
  it.each([
    ["2026-08-02", "2026-07-31"], // Home Depot's quarter, a Sunday
    ["2026-02-01", "2026-01-31"], // its year
    ["2025-12-28", "2025-12-31"], // Johnson & Johnson's year
    ["2026-10-02", "2026-09-30"], // Coca-Cola's quarter, a Friday
    ["2026-09-26", "2026-09-30"], // Apple's year
    ["2026-07-26", "2026-07-31"], // Nvidia's quarter
    ["2026-09-30", "2026-09-30"], // a month end is its own key (Microsoft)
    ["2021-01-23", "2021-01-23"], // eight days off: no month end within a week, so the end is its own key (Cisco)
  ])("keys %s by %s", (end, key) => {
    expect(nearestMonthEnd(end)).toBe(key);
  });
});

describe("a US company's dates resolved to its filed ends", () => {
  it("dates Home Depot's quarter to 2 Aug 2026, its year to 1 Feb 2026 and its last four quarters to 2 Aug 2026", () => {
    const dates = filedDates(HD);
    expect(dates("2026-07-31", "quarter")).toBe("2026-08-02");
    expect(dates("2026-01-31", "year")).toBe("2026-02-01");
    expect(dates("2026-07-31", "instant")).toBe("2026-08-02");
    expect(metricDate(dates, { period_kind: "ttm", period_end: "2026-07-31" })).toBe("2026-08-02");
    expect(metricDate(dates, { period_kind: "year", period_end: "2026-01-31" })).toBe("2026-02-01");
  });
  it("dates Apple's latest quarter, Johnson & Johnson's year and a Coca-Cola quarter to their filed rows", () => {
    expect(filedDates(AAPL)("2026-09-30", "quarter")).toBe("2026-09-26");
    expect(filedDates(JNJ)("2025-12-31", "year")).toBe("2025-12-28");
    expect(filedDates(KO)("2026-09-30", "quarter")).toBe("2026-10-02");
    expect(filedDates(KO)("2025-12-31", "year")).toBe("2025-12-31");
  });
  it("leaves Microsoft's month ends as they are", () => {
    const dates = filedDates(MSFT);
    expect(dates("2026-09-30", "quarter")).toBe("2026-09-30");
    expect(dates("2026-06-30", "year")).toBe("2026-06-30");
  });
  it("keeps a market figure's date: the price date is right as it is", () => {
    expect(metricDate(filedDates(HD), { period_kind: "market", period_end: "2026-11-13" })).toBe("2026-11-13");
  });
  it("gives no date, never the key, when no filed row of that kind matches, or two do", () => {
    expect(filedDates(HD)("2026-10-31", "quarter")).toBeNull();
    expect(filedDates(HD)("2026-07-31", "year")).toBeNull(); // a quarter end isn't a year end
    expect(filedDates([])("2026-07-31", "quarter")).toBeNull();
    expect(filedDates(HD)("2025-10-31", "quarter")).toBeNull(); // a nine-month row isn't a quarter
    expect(filedDates(ends("quarter", "2026-07-27", "2026-08-02"))("2026-07-31", "quarter")).toBeNull();
    expect(metricDate(filedDates([]), { period_kind: "ttm", period_end: "2026-07-31" })).toBeNull();
  });
});

// Home Depot's stored inputs as the US tasks date them: the last four quarters by the quarter's key, the three-year sums and
// the latest year by the year's key, market figures by the price date.
const HD_INPUTS = {
  ...OPERATING_INPUTS,
  company: Object.fromEntries(
    Object.entries(OPERATING_INPUTS.company).map(([k, v]) => [k, { ...v, as_of: k === "revenue_ttm" ? "2026-07-31" : ["market_cap", "beta", "shares"].includes(k) ? "2026-11-13" : "2026-01-31" }]),
  ),
  price: { ...OPERATING_INPUTS.price, as_of: "2026-11-13" },
};

describe("the valuation assumptions' dates", () => {
  const rows = assumptionRows(run("reverse_dcf", { market: "US", currency: "USD", inputs: HD_INPUTS }), "US");
  const asOf = (out: ReturnType<typeof withFiledDates>) => Object.fromEntries(out.map((r) => [r.id, r.asOf]));

  it("dates revenue over the last four quarters to the filed quarter, and the three-year inputs and the cost of debt to the filed year", () => {
    const out = asOf(withFiledDates(rows, filedDates(HD)));
    expect(out.revenue).toBe("2026-08-02");
    for (const id of ["margin", "tax-near", "s2c", "kd"]) expect(out[id]).toBe("2026-02-01");
  });
  it("keeps the price, market, rate and share dates as stored", () => {
    const before = asOf(rows);
    const out = asOf(withFiledDates(rows, filedDates(HD)));
    for (const id of ["price", "rf", "erp", "beta", "ke", "weights", "wacc", "gt", "shares", "tax-far"]) expect(out[id]).toBe(before[id]);
  });
  it("changes no value, label, source or note, only the statement dates", () => {
    const out = withFiledDates(rows, filedDates(HD));
    const undated = (r: (typeof rows)[number]) => ({ ...r, asOf: null });
    expect(out.map(undated)).toEqual(rows.map(undated));
    expect(Object.keys(ASSUMPTION_STATEMENT).filter((id) => rows.some((r) => r.id === id)).sort()).toEqual(["kd", "margin", "revenue", "s2c", "tax-near"]);
  });
  it("dates a lender's delivered return on equity to its filed year, and holds the date back where none matches", () => {
    const lender = assumptionRows(lenderRun({ market: "US", currency: "USD" }), "US");
    expect(asOf(withFiledDates(lender, filedDates(ends("year", "2026-03-31")))).delivered).toBe("2026-03-31");
    expect(asOf(withFiledDates(lender, filedDates(ends("year", "2026-03-28")))).delivered).toBe("2026-03-28");
    expect(asOf(withFiledDates(lender, filedDates([]))).delivered).toBeNull();
    expect(asOf(withFiledDates(rows, filedDates([]))).revenue).toBeNull();
  });
});

describe("Home Depot's pages", () => {
  const US = { market: "US", currency: "USD", source_key: "sec" } as const;
  const HOME_DEPOT: Partial<CompanyRow> = {
    slug: "us-hd", name: "The Home Depot, Inc.", short_name: "Home Depot", market: "US", currency: "USD", isin: null, nse_symbol: null, bse_code: null, nse_industry: null,
    ticker: "HD", cik: "354950", peer_group_key: "us-retail", peer_group_label: "US retail", price_exchange: "NYSE", fy_end_month: 1, index_slugs: ["dow-jones"],
    last_close: 348.69, previous_close: 345, last_close_date: "2026-11-13", previous_close_date: "2026-11-12", price_source_key: "yfinance",
  };
  const metrics = [
    metric("pe_ttm", { ...US, slug: "us-hd", period_end: "2026-11-13", value: 24.5 }),
    metric("roe", { ...US, slug: "us-hd", period_kind: "year", period_end: "2026-01-31", value: 1.455 }),
    metric("ebit_margin", { ...US, slug: "us-hd", period_kind: "ttm", period_end: "2026-07-31", value: 0.124, label_kind: "operating income as filed" }),
    metric("net_margin", { ...US, slug: "us-hd", period_kind: "ttm", period_end: "2026-07-31", value: 0.084 }),
  ];
  const hdPage = (over: Parameters<typeof page>[0]) => page({ company: company(HOME_DEPOT), indexNames: { "dow-jones": "Dow Jones Industrial Average" }, today: "2026-11-15", ...over });
  const overview = (filed?: FiledEnd[]) => html(hdPage({ overview: { prices: [], metrics, peers: [], filed } }));
  const POLICIES: FieldPolicyRow[] = [{ field_key: "equity.implied_growth", status: "labelled", label: "model-implied" }];
  const HD_RUN = run("reverse_dcf", { ...US, slug: "us-hd", name: "Home Depot", inputs: HD_INPUTS });
  const valuation = (filed?: FiledEnd[]) => html(hdPage({ tab: "valuation", overview: null, valuation: { runs: [HD_RUN], scenarios: [], policies: POLICIES, imf: null, series: null, filed } }));

  it("shows the key figures' statement dates as filed: the year to 1 Feb 2026 and the last four quarters to 2 Aug 2026", () => {
    const t = text(overview(HD));
    expect(t).toContain("Return on equity 145.5% As of 1 Feb 2026");
    expect(t).toContain("EBIT margin 12.4% operating income as filed As of 2 Aug 2026");
    expect(t).toContain("Net margin 8.4% As of 2 Aug 2026");
    expect(t).toContain("P/E, last four quarters 24.5× As of 13 Nov 2026");
    expect(t).not.toMatch(/31 Jul 2026|31 Jan 2026/);
  });
  it("says a key figure's date isn't available, never the month key, when no filed row matches", () => {
    const t = text(overview([]));
    expect(t).toContain("EBIT margin 12.4% operating income as filed Date not available");
    expect(t).not.toMatch(/31 Jul 2026|31 Jan 2026/);
    expect(text(overview(undefined))).not.toMatch(/31 Jul 2026|31 Jan 2026/);
  });
  it("shows the assumptions' statement dates as filed", () => {
    const t = text(valuation(HD));
    expect(t).toContain("Revenue, last four quarters $2,500,000 mn 02 Aug 2026");
    expect(t).toContain("EBIT margin, last three financial years 24.12% 01 Feb 2026");
    expect(t).toContain("Tax rate, years 1 to 5 25.11% 01 Feb 2026");
    expect(t).toContain("Sales to capital 1.80× 01 Feb 2026");
    expect(t).toContain("Cost of debt, before tax 7.31% 01 Feb 2026");
    expect(t).toContain("Latest close $3,456.70 13 Nov 2026");
    expect(t).not.toMatch(/31 Jul 2026|31 Jan 2026/);
    const none = text(valuation([]));
    expect(none).toContain("Revenue, last four quarters $2,500,000 mn — ");
    expect(none).not.toMatch(/31 Jul 2026|31 Jan 2026/);
  });
  it("uses none of the banned words", () => {
    for (const out of [overview(HD), overview([]), valuation(HD)]) expect(bannedWordsIn(text(out))).toEqual([]);
  });
});

describe("an Indian company's dates", () => {
  it("are shown as stored: the resolver is for US companies only", () => {
    const t = text(
      html(
        page({
          overview: {
            prices: [],
            metrics: [metric("ebit_margin", { period_kind: "ttm", period_end: "2026-09-30", value: 0.25 }), metric("roe", { period_kind: "year", period_end: "2026-03-31", value: 0.5 })],
            peers: [],
          },
        }),
      ),
    );
    expect(t).toContain("EBIT margin 25.0% As of 30 Sep 2026");
    expect(t).toContain("Return on equity 50.0% As of 31 Mar 2026");
    expect(t).not.toContain("Date not available");
  });
});
