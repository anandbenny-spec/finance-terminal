// The three company panels that link a stored filing URL (#367, R4; Review 02a F1-05): a URL that isn't https:// renders as
// text, not as a link, and an https:// one still links. The other eight places that link a stored URL make the same check.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { action, financial, page, promoter } from "@/tests/company-rows";

const BAD = ["http://example.test/plain", "javascript:alert(1)", "data:text/html,x", "//example.test/protocol-relative", "ftp://example.test/file"];

const html = (data: ReturnType<typeof page>) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={data} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

const financials = (sourceUrl: string | null, promoterRows = [promoter("2026-06-30")]) =>
  page({
    tab: "financials",
    overview: null,
    financials: { financials: [financial("quarter", "2026-06-30", { source_url: sourceUrl })], metrics: [], promoter: promoterRows },
  });

const hrefs = (markup: string) => [...markup.matchAll(/href="([^"]*)"/g)].map((m) => m[1]);

describe("the statement table's filing link", () => {
  it("links an https:// filing", () => {
    const out = html(financials("https://example.test/filing/2026-06-30"));
    expect(hrefs(out)).toContain("https://example.test/filing/2026-06-30");
  });
  it.each(BAD)("shows %s as text, with no link", (url) => {
    const out = html(financials(url, []));
    expect(hrefs(out).filter((h) => h.includes("example.test") || h.startsWith("javascript:") || h.startsWith("data:"))).toEqual([]);
    expect(out).toContain("Filed 30 Jun 2026");
  });
  it("shows a missing URL as text", () => {
    expect(html(financials(null, []))).toContain("Filed 30 Jun 2026");
  });
});

describe("the promoter panel's filing link", () => {
  const corrected = (url: string | null) =>
    promoter("2021-12-31", { promoter_pct: 66.13, correction: "percent_for_fraction", label_kind: "derived", filed_pct: 6613, share_ratio_pct: 66.13, nse_pct: 66.1, source_url: url });
  it("links an https:// filing, on the date and on the correction note", () => {
    const out = html(financials("https://example.test/filing/2026-06-30", [corrected("https://example.test/sh/2021-12-31")]));
    expect(hrefs(out).filter((h) => h === "https://example.test/sh/2021-12-31")).toHaveLength(2);
  });
  it.each(BAD)("shows %s as text, with no link, and keeps the note's words", (url) => {
    const out = html(financials("https://example.test/filing/2026-06-30", [corrected(url)]));
    expect(hrefs(out)).not.toContain(url);
    expect(out).not.toContain(`href="${url}"`);
    expect(out).toContain("Filed as 6,613%");
    expect(out).toContain("31 Dec 2021");
  });
});

describe("the corporate actions panel's filing link", () => {
  const withUrl = (url: string | null) => page({ tab: "financials", overview: null, financials: { financials: [], metrics: [], promoter: [] }, actions: [action({ subject: "Dividend - Rs 11 Per Share", source_url: url })] });
  it("links an https:// filing on the exchange's wording", () => {
    const out = html(withUrl("https://example.test/action/1"));
    expect(hrefs(out)).toContain("https://example.test/action/1");
  });
  it.each(BAD)("shows %s as text, with no link", (url) => {
    const out = html(withUrl(url));
    expect(out).not.toContain(`href="${url}"`);
    expect(out).toContain("Dividend - Rs 11 Per Share");
  });
});
