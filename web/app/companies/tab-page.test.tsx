// /companies/[slug]/[...path] (R05-9b, 12 Nov 2026): the tab and its view are path segments. Any path that names no tab and
// view answers 404 from the page itself, before a read: an unknown tab, `overview` (the Overview is the bare slug),
// `value-chain` (its own static route), a view the tab doesn't take, a third segment. An unknown company answers 404 too,
// and a read that can't tell is the error state. The page reads no query string, so it is built ahead and cached.
import { beforeEach, describe, expect, it, vi } from "vitest";

const exists = vi.fn<(slug: string) => Promise<boolean | null>>();
const name = vi.fn<(slug: string) => Promise<string | null>>();
const market = vi.fn<(slug: string) => Promise<"IN" | "US" | null>>(() => Promise.resolve("IN"));
const readPage = vi.fn<(slug: string, tab: string, call?: string) => Promise<{ ok: false }>>();
vi.mock("@/lib/queries/company", () => ({
  readCompanyExists: (slug: string) => exists(slug),
  readCompanyName: (slug: string) => name(slug),
  readCompanyMarket: (slug: string) => market(slug),
  readCompanyPage: (slug: string, tab: string, call?: string) => readPage(slug, tab, call),
}));
vi.mock("@/lib/queries/shell", () => ({ readFreshness: () => Promise.resolve({ ok: false }) }));

const route = await import("@/app/companies/[slug]/[...path]/page");
const props = (slug: string, path: string[]) => ({ params: Promise.resolve({ slug, path }) }) as Parameters<typeof route.default>[0];
const NOT_FOUND = { digest: expect.stringContaining("404") };

describe("the company tab routes", () => {
  beforeEach(() => {
    exists.mockReset().mockResolvedValue(true);
    readPage.mockReset().mockResolvedValue({ ok: false });
  });

  it.each([
    [["financials"], "financials", undefined],
    [["valuation"], "valuation", undefined],
    [["rate-decisions"], "rate-decisions", undefined],
    [["calls"], "calls", undefined],
    [["financials", "standalone"], "financials", undefined],
    [["rate-decisions", "d5"], "rate-decisions", undefined],
    [["calls", "q2-fy27"], "calls", "q2-fy27"],
  ])("reads %j as the %s tab", async (path, tab, call) => {
    await route.default(props("tcs", path));
    expect(readPage).toHaveBeenCalledWith("tcs", tab, call);
  });

  it.each([
    [["overview"]],
    [["value-chain"]],
    [["value-chain", "x"]],
    [["nonsense"]],
    [["valuation", "standalone"]],
    [["financials", "consolidated"]],
    [["financials", "d5"]],
    [["rate-decisions", "d1"]],
    [["calls", "../x"]],
    [["calls", "Q2_FY27"]],
    [["calls", "q2-fy27", "extra"]],
    [[]],
  ])("answers 404 for %j before any read", async (path) => {
    await expect(route.default(props("tcs", path))).rejects.toMatchObject(NOT_FOUND);
    expect(exists).not.toHaveBeenCalled();
    expect(readPage).not.toHaveBeenCalled();
  });

  it("answers 404 for a valid tab of a company that doesn't exist", async () => {
    exists.mockResolvedValue(false);
    await expect(route.default(props("nope", ["valuation"]))).rejects.toMatchObject(NOT_FOUND);
  });
  it("renders the error state, not a 404, when the existence read can't tell", async () => {
    exists.mockResolvedValue(null);
    await expect(route.default(props("tcs", ["valuation"]))).resolves.toBeTruthy();
    expect(readPage).not.toHaveBeenCalled();
  });
  it("names one sample for the build, the Overview's company", () => {
    expect(route.generateStaticParams()).toEqual([{ slug: "adanient", path: ["valuation"] }]);
  });
  it("titles a real tab with the company's name and a bad path plainly", async () => {
    name.mockResolvedValue("Tata Consultancy Services Ltd.");
    expect((await route.generateMetadata(props("tcs", ["calls"]))).title).toBe("Tata Consultancy Services Ltd. · Finance Terminal");
    expect((await route.generateMetadata(props("tcs", ["overview"]))).title).toBe("Company · Finance Terminal");
  });
});
