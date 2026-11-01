// /screener/rate-decisions (2A-25): the sector table from stored peer-group rows (the median excess with n, each group
// linked to its members), the window in the URL, the gate, the error state and no verdict wording. The page is a
// server component whose data comes from lib/queries/rate.ts; here the read is stubbed with test rows.
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RateSectorTable } from "@/components/company/RateDecisionTables";
import { NOT_A_FORECAST } from "@/lib/equity/rate";
import type { SectorPageData } from "@/lib/queries/rate";
import { bannedWordsIn } from "@/tests/banned-words";
import { HIDDEN, SHOWN, fewStat, stat } from "@/tests/rate-rows";

let data: SectorPageData;
vi.mock("@/lib/queries/rate", async (orig) => ({ ...(await orig<typeof import("@/lib/queries/rate")>()), readSectorPage: () => Promise.resolve(data) }));
vi.mock("@/lib/queries/shell", () => ({ readFreshness: () => Promise.resolve({ ok: false }) }));

const route = await import("@/app/screener/rate-decisions/page");
const text = (markup: string) => markup.replace(/<span class="sr-only">[^<]*<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const group = { key: "it", label: "IT services", members: [{ slug: "tcs", name: "TCS" }, { slug: "wipro", name: "Wipro" }] };
const ok = (over: Partial<Extract<SectorPageData, { ok: true }>> = {}): SectorPageData => ({
  ok: true, readAt: "2026-10-31T09:00:00.000Z", groups: [group], policies: SHOWN, niftyAttribution: "Source: Yahoo Finance (^NSEI)",
  stats: [
    stat({ subject_kind: "peer_group", subject_key: "it", subject_name: "IT services", median_excess_pct: -0.2, share_positive: 0.4 }),
    fewStat({ subject_kind: "peer_group", subject_key: "it", subject_name: "IT services" }),
    stat({ subject_kind: "peer_group", subject_key: "it", subject_name: "IT services", window_key: "d5", median_excess_pct: 1.5 }),
    stat({ subject_key: "tcs" }), // a company row never reaches the sector view
  ],
  ...over,
});
// The page renders inside Suspense; resolve its async child by calling it, as the Next runtime would.
const render = async (searchParams: { window?: string } = {}): Promise<string> => {
  const el = route.default({ params: Promise.resolve({}), searchParams: Promise.resolve(searchParams) } as Parameters<typeof route.default>[0]);
  const child = (el as { props: { children: { type: (p: unknown) => Promise<unknown>; props: unknown } } }).props.children;
  return renderToStaticMarkup((await child.type(child.props)) as React.ReactElement);
};

describe("the sector view", () => {
  beforeEach(() => {
    data = ok();
  });

  it("lists each peer group by bank and decision with n and the median excess, linked to its members", async () => {
    const out = await render();
    const t = text(out).replace(/ ,/g, ",");
    expect(t).toContain("IT services TCS, Wipro RBI (MPC) · Hold 30 −0.20%");
    expect(out).toContain('href="/companies/tcs?tab=rate-decisions"');
    expect(out).toContain('href="/companies/wipro?tab=rate-decisions"');
    expect(t).not.toContain("+0.07"); // the company row
  });
  it("shows no statistic for a group with fewer than five decisions, and says so", async () => {
    const out = await render();
    expect(out).toContain("Fewer than 5 decisions: no statistics.");
    expect(out).not.toContain("the moves are listed instead");
  });
  it("reads the window from the URL", async () => {
    const t = text(await render({ window: "d5" }));
    expect(t).toContain("+1.50%");
    expect(t).not.toContain("−0.20%");
  });
  it("carries the not-a-forecast line, the sources and a freshness line", async () => {
    const t = text(await render());
    expect(t).toContain(NOT_A_FORECAST);
    expect(t).toContain("Source: NSE");
    expect(t).toContain("Source: Reserve Bank of India");
    expect(bannedWordsIn(t)).toEqual([]);
  });
  it("holds the table back while the field policy is hidden", async () => {
    data = ok({ policies: HIDDEN });
    const t = text(await render());
    expect(t).toContain("Rate-sensitivity figures are held back until their checks pass.");
    expect(t).not.toContain("−0.20%");
  });
  it("tells a failed read as an error state and an empty table in words", async () => {
    data = { ok: false, readAt: "2026-10-31T09:00:00.000Z" };
    expect(text(await render())).toContain("Couldn't load this panel. Try again.");
    data = ok({ stats: [] });
    expect(text(await render())).toContain("No peer group has decision-day moves published yet.");
  });
  it("renders the table component alone for a group with no known members", () => {
    const out = renderToStaticMarkup(<RateSectorTable rows={[{ key: "k", subjectKey: "x", subjectName: "Metals", bank: "fed", decision: "cut", n: 6, fewMoves: false, stat: stat({ subject_kind: "peer_group", subject_name: "Metals", bank: "fed", decision: "cut", n: 6 }), group: null }]} caption="c" />);
    expect(text(out)).toContain("Metals Fed (FOMC) · Cut 6");
  });
});
