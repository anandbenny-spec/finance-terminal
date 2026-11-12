// /companies/[slug] answers 404 for an unknown company before it streams (as the meeting pages do): the page itself
// calls notFound(), above its Suspense boundary, and only when the existence read says the slug is missing. A read that
// can't tell (null: a failed read, or no Data API in this build) renders the error state, never a 404. The build sample
// is the first Nifty 50 slug (py/config/equity/companies.yaml).
import { Suspense, isValidElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const exists = vi.fn<(slug: string) => Promise<boolean | null>>();
const name = vi.fn<(slug: string) => Promise<string | null>>();
vi.mock("@/lib/queries/company", () => ({
  readCompanyExists: (slug: string) => exists(slug),
  readCompanyName: (slug: string) => name(slug),
  readCompanyPage: () => Promise.resolve({ ok: false }),
}));
vi.mock("@/lib/queries/shell", () => ({ readFreshness: () => Promise.resolve({ ok: false }) }));

const route = await import("@/app/companies/[slug]/page");
const props = (slug: string) => ({ params: Promise.resolve({ slug }), searchParams: Promise.resolve({}) }) as Parameters<typeof route.default>[0];
const NOT_FOUND = { digest: expect.stringContaining("404") };

describe("the company page", () => {
  beforeEach(() => exists.mockReset());

  it("answers 404 from the page itself when no company has the slug", async () => {
    exists.mockResolvedValue(false);
    await expect(route.default(props("nope"))).rejects.toMatchObject(NOT_FOUND);
    expect(exists).toHaveBeenCalledWith("nope");
  });
  it("renders the company inside Suspense when it exists", async () => {
    exists.mockResolvedValue(true);
    const out = await route.default(props("tcs"));
    expect(isValidElement(out) && out.type).toBe(Suspense);
  });
  it("renders the error state, not a 404, when the read can't tell", async () => {
    exists.mockResolvedValue(null);
    const out = await route.default(props("tcs"));
    expect(isValidElement(out) && out.type).not.toBe(Suspense);
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
