// /companies/[slug] answers 404 for an unknown company before it streams (as the meeting pages do): the page itself
// calls notFound(), above its Suspense boundary, and only when the existence read says the slug is missing. A read that
// can't tell (null: a failed read, or no Data API in this build) renders the error state, never a 404. The build sample
// is the first Nifty 50 slug (py/config/equity/companies.yaml). The page reads no query string and holds no Suspense boundary
// (R05-9b, 12 Nov 2026): it is built ahead and cached, so its params are all it takes. The tabs are tested in tab-page.test.tsx.
import { isValidElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const exists = vi.fn<(slug: string) => Promise<boolean | null>>();
const readPage = vi.fn<(slug: string, tab: string, call?: string) => Promise<{ ok: false }>>();
const name = vi.fn<(slug: string) => Promise<string | null>>();
const market = vi.fn<(slug: string) => Promise<"IN" | "US" | null>>(() => Promise.resolve("IN"));
vi.mock("@/lib/queries/company", () => ({
  readCompanyExists: (slug: string) => exists(slug),
  readCompanyName: (slug: string) => name(slug),
  readCompanyMarket: (slug: string) => market(slug),
  readCompanyPage: (slug: string, tab: string, call?: string) => readPage(slug, tab, call),
}));
vi.mock("@/lib/queries/shell", () => ({ readFreshness: () => Promise.resolve({ ok: false }) }));

const route = await import("@/app/companies/[slug]/page");
const props = (slug: string) => ({ params: Promise.resolve({ slug }) }) as Parameters<typeof route.default>[0];
const NOT_FOUND = { digest: expect.stringContaining("404") };

describe("the company page", () => {
  beforeEach(() => {
    exists.mockReset();
    readPage.mockReset().mockResolvedValue({ ok: false });
  });

  it("answers 404 from the page itself when no company has the slug", async () => {
    exists.mockResolvedValue(false);
    await expect(route.default(props("nope"))).rejects.toMatchObject(NOT_FOUND);
    expect(exists).toHaveBeenCalledWith("nope");
  });
  it("reads the Overview from its params alone, with no Suspense boundary to stream behind", async () => {
    exists.mockResolvedValue(true);
    const out = await route.default(props("tcs"));
    expect(isValidElement(out)).toBe(true);
    expect(readPage).toHaveBeenCalledWith("tcs", "overview", undefined);
  });
  it("renders the error state, not a 404, when the read can't tell", async () => {
    exists.mockResolvedValue(null);
    const out = await route.default(props("tcs"));
    expect(isValidElement(out)).toBe(true);
    expect(readPage).not.toHaveBeenCalled();
  });
  it("names a sample slug for the build", () => {
    expect(route.generateStaticParams()).toEqual([{ slug: "adanient" }]);
  });
});

// 12 Nov 2026: every company page had the same tab title, "Company · Finance Terminal".
describe("the company page's title", () => {
  beforeEach(() => name.mockReset());

  it("names the company", async () => {
    name.mockResolvedValue("Tata Consultancy Services Ltd.");
    expect((await route.generateMetadata(props("tcs"))).title).toBe("Tata Consultancy Services Ltd. · Finance Terminal");
    expect(name).toHaveBeenCalledWith("tcs");
  });
  it("keeps the plain title when the name can't be read or no company has the slug", async () => {
    name.mockResolvedValue(null);
    expect((await route.generateMetadata(props("nope"))).title).toBe("Company · Finance Terminal");
  });
});
