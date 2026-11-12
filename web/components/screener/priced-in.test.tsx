// "What's priced in" (2A-21; Spec 02A §5.9, §8): the ranking, the two tables never sharing a row, held and N/A rows listed
// with their reasons, the model-implied gates, the banned words over the whole rendered view (explainer included), and the
// switch: no `site.priced_in` row means no tab and the ordinary screener. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PricedInView } from "@/components/screener/PricedInView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { pricedInVisible, splitRanked, viewOf } from "@/lib/equity/priced-in";
import type { FieldPolicyRow, PricedInRow, TaskFreshnessRow } from "@/lib/queries/api";
import { bannedWordsIn } from "@/tests/banned-words";
import { row as screenerRow } from "@/tests/screener-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

function priced(slug: string, over: Partial<PricedInRow> = {}): PricedInRow {
  return {
    slug, name: "Company " + slug, short_name: slug.toUpperCase(), nse_symbol: slug.toUpperCase(), market: "IN", currency: "INR", peer_group_key: "it",
    valuation_family: "operating", as_of: "2026-10-30", state: "value", reason: null, implied: 0.12, delivered: 0.08, gap: 0.04, delivered_years: 5,
    delivered_reason: null, label: "model-implied", gap_rank: 1, source_key: "nse", ...over,
  };
}

const NONE = { implied: null, delivered: null, gap: null, delivered_years: null, gap_rank: null };
const operating = [
  priced("low", { implied: 0.09, delivered: 0.08, gap: 0.01, gap_rank: 3 }),
  priced("top", { implied: 0.2, delivered: 0.1, gap: 0.1, gap_rank: 1 }),
  priced("mid", { implied: 0.13, delivered: 0.1, gap: 0.03, gap_rank: 2, delivered_years: 4 }),
  priced("nosol", { ...NONE, state: "held", reason: "At its 3-year return on capital, faster growth lowers the model's value, so no growth rate matches the price." }),
  priced("insur", { ...NONE, state: "not_applicable", valuation_family: "insurer", reason: "book value doesn't capture an insurer's value" }),
  priced("nodel", { implied: 0.11, delivered: null, gap: null, delivered_reason: "Delivered growth is held back: a structural break.", gap_rank: null }),
];
const lenders = [
  priced("bankb", { valuation_family: "lender", implied: 0.15, delivered: 0.13, gap: 0.02, gap_rank: 2 }),
  priced("banka", { valuation_family: "lender", implied: 0.16, delivered: 0.12, gap: 0.04, gap_rank: 1 }),
  priced("bankh", { ...NONE, valuation_family: "lender", state: "held", reason: "Held back: July 2023 merger." }),
];
const SHOWN: FieldPolicyRow[] = [
  { field_key: "equity.implied_growth", status: "labelled", label: "model-implied" },
  { field_key: "equity.implied_roe", status: "labelled", label: "model-implied" },
];
const html = (policies: FieldPolicyRow[] = SHOWN, op = operating, ln = lenders) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <PricedInView operating={op} lenders={ln} policies={policies} footer={<span>Source: NSE</span>} />
    </TooltipProvider>,
  );

describe("PricedInView", () => {
  it("ranks each table by gap, largest first, from the view's rank", () => {
    const t = text(html());
    expect(t.indexOf("TOP")).toBeLessThan(t.indexOf("MID"));
    expect(t.indexOf("MID")).toBeLessThan(t.indexOf("LOW"));
    expect(t.indexOf("BANKA")).toBeLessThan(t.indexOf("BANKB"));
    expect(splitRanked(operating).ranked.map((r) => r.slug)).toEqual(["top", "mid", "low"]);
  });
  it("formats implied, delivered, the gap in points and the years", () => {
    const t = text(html());
    expect(t).toContain("20.0%");
    expect(t).toContain("+10.0 pts");
    expect(t).toContain("Gap (points a year)");
    expect(t).toContain("Delivered 5-year revenue CAGR");
    expect(t).toContain("Delivered ROE (5-FY average)");
  });
  it("never shares a row between the two tables", () => {
    const out = html();
    const split = out.indexOf("priced-in-lenders");
    const first = out.slice(0, split);
    const second = out.slice(split);
    for (const r of operating) expect(second).not.toContain("/companies/" + r.slug + "?tab=valuation");
    for (const r of lenders) expect(first).not.toContain("/companies/" + r.slug + "?tab=valuation");
  });
  it("links each row to its Valuation tab", () => {
    expect(html()).toContain('href="/companies/top?tab=valuation"');
  });
  it("underlines each company name as a link, with its Valuation-tab target unchanged (R05-7)", () => {
    expect(html()).toMatch(/<a[^>]*class="[^"]*\bunderline\b[^"]*"[^>]*href="\/companies\/top\?tab=valuation"|<a[^>]*href="\/companies\/top\?tab=valuation"[^>]*class="[^"]*\bunderline\b/);
  });
  it("lists held, N/A and no-delivered-figure rows below, each with its reason, and gives them no rank", () => {
    const t = text(html());
    expect(t).toContain("With no figure");
    expect(t).toContain("faster growth lowers the model's value, so no growth rate matches the price");
    expect(t).toContain("book value doesn't capture an insurer's value");
    expect(t).toContain("Delivered growth is held back: a structural break.");
    expect(t).toContain("Held back: July 2023 merger.");
    expect(splitRanked(operating).unranked.map((r) => r.slug).sort()).toEqual(["insur", "nodel", "nosol"]);
  });
  it("labels each table model-implied and explains the method", () => {
    const t = text(html());
    expect(t).toContain("model-implied");
    expect(t).toContain("not forecasts");
    expect(t).toContain("(ROE − g) ÷ (Ke − g)");
    expect(t).toContain("Informational only, not investment advice.");
    expect(t).toContain("Source: NSE");
  });
  it("hides a table's figures, with a held-back note, when its field policy is hidden or missing", () => {
    for (const policies of [[], [{ field_key: "equity.implied_growth", status: "hidden", label: null } as FieldPolicyRow]]) {
      const t = text(html(policies));
      expect(t).not.toContain("+10.0 pts");
      expect(t).not.toContain("+4.0 pts");
      expect(t).toContain("Lenders, by ROE gap is held back until its checks pass.");
    }
  });
  it("says so for an empty list, never blank", () => {
    const t = text(html(SHOWN, [], []));
    expect(t).toContain("No operating company has a stored run yet.");
    expect(t).toContain("No lender has a stored run yet.");
  });
  it("uses none of the banned words anywhere in the rendered view, explainer included", () => {
    expect(bannedWordsIn(text(html()))).toEqual([]);
    expect(bannedWordsIn(text(html([])))).toEqual([]);
  });
  it("gives the gap no colour and no triangle: it isn't a market move", () => {
    const out = html();
    expect(out).not.toMatch(/text-(up|down|positive|negative)/);
    expect(out).not.toContain("<svg viewBox");
  });
});

describe("the view's switch", () => {
  it("is hidden with no site.priced_in row, or a hidden one, and shown for shown or labelled", () => {
    expect(pricedInVisible([])).toBe(false);
    expect(pricedInVisible([{ field_key: "site.priced_in", status: "hidden", label: null }])).toBe(false);
    expect(pricedInVisible([{ field_key: "site.priced_in", status: "shown", label: null }])).toBe(true);
    expect(pricedInVisible([{ field_key: "site.priced_in", status: "labelled", label: "x" }])).toBe(true);
  });
  it("opens priced-in only when visible; otherwise the ordinary screener", () => {
    expect(viewOf("priced-in", false)).toBe("screener");
    expect(viewOf("priced-in", true)).toBe("priced-in");
    expect(viewOf(["priced-in"], true)).toBe("screener");
    expect(viewOf(undefined, true)).toBe("screener");
  });
});

// The page: with no site.priced_in row there is no tab and ?view=priced-in shows the screener; with it, the tab and both tables.
const page = vi.hoisted(() => ({ policies: [] as FieldPolicyRow[], tasks: null as TaskFreshnessRow[] | null }));
vi.mock("@/lib/queries/shell", () => ({
  readFreshness: () => Promise.resolve(page.tasks ? { ok: true, tasks: page.tasks } : { ok: false }),
}));
vi.mock("@/lib/queries/equity", () => ({
  readScreenerPage: () => Promise.resolve({ ok: true, readAt: "2026-11-08T00:00:00Z", rows: [screenerRow("tcs")], indexes: [], policies: page.policies }),
  readPricedInPage: () => Promise.resolve({ ok: true, readAt: "2026-11-08T00:00:00Z", operating, lenders }),
}));

describe("/screener", () => {
  beforeEach(() => {
    page.policies = [];
    page.tasks = null;
  });
  const render = async (view?: string) => {
    const { default: Page } = await import("@/app/screener/page");
    const el = Page({ searchParams: Promise.resolve(view ? { view } : {}), params: Promise.resolve({}) } as Parameters<typeof Page>[0]);
    expect(el.type).toBe(Suspense);
    const inner = (el.props as { children: { type: (p: unknown) => Promise<ReactNode>; props: unknown } }).children;
    const out = await inner.type(inner.props);
    return text(renderToStaticMarkup(<TooltipProvider>{out}</TooltipProvider>));
  };
  it("has no tab, and shows the screener for ?view=priced-in, while no site.priced_in row exists", async () => {
    const t = await render("priced-in");
    expect(t).not.toContain("What's priced in");
    expect(t).toContain("Nifty 50 screener");
  });
  it("shows the tab and both tables once the row is shown", async () => {
    page.policies = [{ field_key: "site.priced_in", status: "shown", label: null }, ...SHOWN];
    const t = await render("priced-in");
    expect(t).toContain("What's priced in");
    expect(t).toContain("Operating companies, by growth gap");
    expect(t).toContain("Lenders, by ROE gap");
    expect(await render()).toContain("Nifty 50 screener");
  });
  // #355: a failed equity.valuation run leaves the snapshot on an older run, so the model-implied parts must read "Update delayed".
  const FEED_TASKS = ["equity.universe", "equity.prices", "equity.statements", "equity.metrics", "equity.beta", "equity.valuation", "equity.snapshot"];
  const fresh = (failed: string[] = []): TaskFreshnessRow[] =>
    FEED_TASKS.map((task_key) => ({
      task_key,
      last_checked_at: "2026-11-08T10:48:00Z",
      last_success_at: "2026-11-08T10:48:00Z",
      status: failed.includes(task_key) ? "failed" : "ok",
    }));
  it("says Update delayed on the priced-in view while equity.valuation's last run failed, and not when it is ok", async () => {
    page.policies = [{ field_key: "site.priced_in", status: "shown", label: null }, ...SHOWN];
    page.tasks = fresh(["equity.valuation"]);
    expect(await render("priced-in")).toContain("Update delayed");
    page.tasks = fresh();
    expect(await render("priced-in")).not.toContain("Update delayed");
  });
  it("counts equity.snapshot too, and says nothing on the plain table while its implied columns are hidden", async () => {
    page.policies = [{ field_key: "site.priced_in", status: "shown", label: null }];
    page.tasks = fresh(["equity.snapshot"]);
    expect(await render("priced-in")).toContain("Update delayed");
    page.policies = [];
    page.tasks = fresh(["equity.valuation", "equity.snapshot"]);
    expect(await render()).not.toContain("Update delayed");
  });
  it("says Update delayed on the plain table once an implied column is shown and equity.valuation failed", async () => {
    page.policies = SHOWN;
    page.tasks = fresh(["equity.valuation"]);
    expect(await render()).toContain("Update delayed");
  });
});
