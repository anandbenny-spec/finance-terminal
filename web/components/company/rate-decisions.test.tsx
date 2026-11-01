// The Rate decisions tab and the sector view rendered to HTML as the server sends them (2A-25): the bank × decision
// table from stored rows, the one-by-one list below five decisions, the cycles with "ongoing" labelled, decisions in
// words with no colour, the line under every panel, the window in the URL, the gate, the empty states and the
// banned-words scan (Spec 02A §5.9). Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NOT_A_FORECAST } from "@/lib/equity/rate";
import type { RateTabData } from "@/lib/queries/rate";
import { bannedWordsIn } from "@/tests/banned-words";
import { page } from "@/tests/company-rows";
import { HIDDEN, cycleMove, rates, stat } from "@/tests/rate-rows";

const text = (markup: string) => markup.replace(/<span class="sr-only">[^<]*<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (r: RateTabData | null, window: "d1" | "d5" = "d1") =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "rate-decisions", overview: null, rates: r })} basis="consolidated" window={window} freshness={null} />
    </TooltipProvider>,
  );

describe("the Rate decisions tab", () => {
  it("is a tab in the URL, marked current", () => {
    const out = html(rates());
    expect(out).toContain('href="/companies/tcs?tab=rate-decisions"');
    expect(out).toMatch(/aria-current="page"[^>]*>Rate decisions</);
  });

  it("shows the bank × decision table from stored rows, with n, median, mean, middle half and share positive", () => {
    const t = text(html(rates()));
    expect(t).toContain("RBI (MPC) · Cut");
    expect(t).toContain("RBI (MPC) · Hold");
    expect(t).toContain("Decisions (n)");
    expect(t).toMatch(/RBI \(MPC\) · Hold 30 \+0\.07% \+0\.11% −0\.40% to \+0\.60% 56\.7%/);
    expect(t).toContain("RBI (MPC) · Cut 12 −0.30%");
  });

  it("says why a group with fewer than five decisions has no statistics, and lists its moves one by one", () => {
    const out = html(rates());
    const t = text(out);
    expect(t).toContain("Fed (FOMC) · Hike 3");
    expect(out).toContain("Fewer than 5 decisions: no statistics, the moves are listed instead.");
    expect(t).toContain("Decisions listed one by one");
    expect(t).toContain("15 Jun 2022");
    expect(t).toContain("26 Jul 2023");
    expect(t).toContain("−1.25%");
    expect(t).toContain("−0.50%");
  });

  it("reads the five-day window from the URL and leaves the one-day moves out of it", () => {
    const t = text(html(rates(), "d5"));
    expect(t).toContain("+0.91");
    expect(t).not.toContain("−0.30");
    expect(html(rates(), "d5")).toMatch(/aria-current="page"[^>]*href="\/companies\/tcs\?tab=rate-decisions&amp;window=d5"/);
  });

  it("lists the cycles with their dates, n and the stock's, the Nifty 50's and the excess, an ongoing one labelled", () => {
    const t = text(html(rates()));
    expect(t).toContain("Rate cycles");
    expect(t).toContain("RBI (MPC) Easing (ongoing) 7 Feb 2025 to 5 Dec 2025 4 +12.50% +9.50% +3.00%");
  });

  it("gives decisions and cycle directions no colour, and a stock's move a sign and a triangle", () => {
    const out = html(rates());
    expect(out).toContain('<span class="text-ink-1">RBI (MPC) · Cut</span>');
    expect(out).toContain('<span class="text-ink-1">Easing<span');
    expect(out.match(/<span class="text-ink-1">(RBI|Fed)[^<]*<\/span>/g)?.join("")).not.toMatch(/text-(up|down)/);
    expect(out).toMatch(/text-down[^>]*>[\s\S]{0,200}−0\.30/);
    expect(out).toContain("<svg"); // Delta's triangle
  });

  it("puts the not-a-forecast line, a source line and a freshness under every panel", () => {
    const out = html(rates());
    expect((out.match(/<section aria-labelledby/g) ?? []).length).toBe(3);
    expect((out.match(/Data to /g) ?? []).length).toBeGreaterThanOrEqual(3);
    expect(text(out).split(NOT_A_FORECAST).length - 1).toBe(3);
    expect(text(out)).toContain("Source: NSE");
    expect(text(out)).toContain("Source: Federal Reserve Board");
    expect(text(out)).toContain("Source: Reserve Bank of India");
    expect(out).not.toMatch(/href="https?:\/\/[^"]*rbi\.org/);
  });

  it("holds every panel back while the field policy is hidden, with no figure", () => {
    const t = text(html(rates({ policies: HIDDEN })));
    expect(t).toContain("Rate-sensitivity figures are held back until their checks pass.");
    expect(t).not.toContain("+0.07");
    expect(t).not.toContain("+12.50%");
  });

  it("tells an empty company in words, not a blank panel", () => {
    const t = text(html(rates({ stats: [], moves: [], cycles: [], cycleInfo: [] })));
    expect(t).toContain("No decision-day moves are published for this company yet.");
    expect(t).toContain("No rate cycles are published for this company yet.");
  });

  it("has no verdict or forecast wording", () => {
    const t = text(html(rates()));
    expect(bannedWordsIn(t)).toEqual([]);
    expect(t).not.toMatch(/significan|predict|will (rise|fall)|expected to/i);
  });

  it("shows a company with only a lone cycle row and no dates from the cycle view without crashing", () => {
    const t = text(html(rates({ cycleInfo: [], cycles: [cycleMove()] })));
    expect(t).toContain("6 Feb 2025 to 30 Oct 2026");
  });

  it("keeps the stored statistics out of the page when n is held back", () => {
    const t = text(html(rates({ stats: [stat({ n: null, median_excess_pct: null })] })));
    expect(t).toContain("No decision-day moves are published for this company yet.");
  });
});
