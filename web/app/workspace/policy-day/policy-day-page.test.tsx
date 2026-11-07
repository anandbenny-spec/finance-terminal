// /workspace/policy-day is hidden behind site.policy_day (#90): no row, a hidden row, or a failed read answers 404 from
// the page itself, above its Suspense boundary; a shown or labelled row renders the page; the page is never indexed.
import { Suspense, isValidElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const overview = vi.fn();
vi.mock("@/lib/queries/overview", () => ({ readPolicyOverview: () => overview() }));
vi.mock("@/components/policy/PolicyDay", () => ({ PolicyDay: () => null }));

const page = await import("@/app/workspace/policy-day/page");
const NOT_FOUND = { digest: expect.stringContaining("404") };
const props = { searchParams: Promise.resolve({}) } as Parameters<typeof page.default>[0];
const withPolicy = (rows: { field_key: string; status: string; label: string | null }[]) => ({
  readAt: "2026-11-07T12:00:00Z",
  fieldPolicy: { ok: true, rows },
  fed: { ok: false },
  rbi: { ok: false },
});

describe("Policy Day page", () => {
  beforeEach(() => overview.mockReset());

  it("answers 404 when no site.policy_day row exists", async () => {
    overview.mockResolvedValue(withPolicy([{ field_key: "ai.doc_summary", status: "shown", label: null }]));
    await expect(page.default(props)).rejects.toMatchObject(NOT_FOUND);
  });

  it("answers 404 for a hidden row and when the field policy can't be read (fails closed)", async () => {
    overview.mockResolvedValue(withPolicy([{ field_key: "site.policy_day", status: "hidden", label: null }]));
    await expect(page.default(props)).rejects.toMatchObject(NOT_FOUND);
    overview.mockResolvedValue({ ...withPolicy([]), fieldPolicy: { ok: false } });
    await expect(page.default(props)).rejects.toMatchObject(NOT_FOUND);
  });

  it("renders inside Suspense once the row is shown or labelled", async () => {
    for (const status of ["shown", "labelled"]) {
      overview.mockResolvedValue(withPolicy([{ field_key: "site.policy_day", status, label: null }]));
      const out = await page.default(props);
      expect(isValidElement(out) && out.type).toBe(Suspense);
    }
  });

  it("is never indexed", () => {
    expect(page.metadata.robots).toEqual({ index: false, follow: false });
  });
});
