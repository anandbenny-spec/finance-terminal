// Policy Day's two pages are hidden behind site.policy_day (#90): no row, a hidden row, or a failed read answers 404
// from the page itself; a shown or labelled row renders the page; neither page is ever indexed.
//
// 12 Nov 2026: the bank is a path segment (/workspace/policy-day/fed, /rbi), not `?bank=`, and neither page reads the
// request, so both are prerendered. /workspace/policy-day shows the bank whose latest decision is newer.
import { isValidElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const overview = vi.fn();
const PolicyDay = vi.fn(() => null);
vi.mock("@/lib/queries/overview", () => ({ readPolicyOverview: () => overview() }));
vi.mock("@/components/policy/PolicyDay", () => ({ PolicyDay }));

const page = await import("@/app/workspace/policy-day/page");
const bankPage = await import("@/app/workspace/policy-day/[bank]/page");
const NOT_FOUND = { digest: expect.stringContaining("404") };
const withPolicy = (rows: { field_key: string; status: string; label: string | null }[], dates: { fed?: string; rbi?: string } = {}) => ({
  readAt: "2026-11-07T12:00:00Z",
  fieldPolicy: { ok: true, rows },
  fed: dates.fed ? { ok: true, meeting: { decision_date: dates.fed } } : { ok: false },
  rbi: dates.rbi ? { ok: true, meeting: { decision_date: dates.rbi } } : { ok: false },
});
const SHOWN = [{ field_key: "site.policy_day", status: "shown", label: null }];
const bankProps = (bank: string) => ({ params: Promise.resolve({ bank }) }) as Parameters<typeof bankPage.default>[0];
const bankOf = (out: unknown) => (isValidElement(out) && out.type === PolicyDay ? (out.props as { bank: string }).bank : null);

describe("Policy Day page", () => {
  beforeEach(() => overview.mockReset());

  it("answers 404 when no site.policy_day row exists", async () => {
    overview.mockResolvedValue(withPolicy([{ field_key: "ai.doc_summary", status: "shown", label: null }]));
    await expect(page.default()).rejects.toMatchObject(NOT_FOUND);
  });

  it("answers 404 for a hidden row and when the field policy can't be read (fails closed)", async () => {
    overview.mockResolvedValue(withPolicy([{ field_key: "site.policy_day", status: "hidden", label: null }]));
    await expect(page.default()).rejects.toMatchObject(NOT_FOUND);
    overview.mockResolvedValue({ ...withPolicy([]), fieldPolicy: { ok: false } });
    await expect(page.default()).rejects.toMatchObject(NOT_FOUND);
  });

  it("renders the page once the row is shown or labelled", async () => {
    for (const status of ["shown", "labelled"]) {
      overview.mockResolvedValue(withPolicy([{ field_key: "site.policy_day", status, label: null }]));
      expect(bankOf(await page.default())).toBe("Fed");
    }
  });

  it("shows the bank whose latest decision is newer, the Fed when neither is known", async () => {
    overview.mockResolvedValue(withPolicy(SHOWN, { fed: "2026-09-16", rbi: "2026-10-07" }));
    expect(bankOf(await page.default())).toBe("RBI");
    overview.mockResolvedValue(withPolicy(SHOWN, { fed: "2026-10-28", rbi: "2026-10-07" }));
    expect(bankOf(await page.default())).toBe("Fed");
    overview.mockResolvedValue(withPolicy(SHOWN));
    expect(bankOf(await page.default())).toBe("Fed");
  });

  it("takes no props, so it can't read the query string and stays prerendered", () => {
    expect(page.default).toHaveLength(0);
  });

  it("is never indexed", () => {
    expect(page.metadata.robots).toEqual({ index: false, follow: false });
  });
});

describe("Policy Day page for one bank (/workspace/policy-day/[bank])", () => {
  beforeEach(() => overview.mockReset());

  it("prerenders the two banks", () => {
    expect(bankPage.generateStaticParams()).toEqual([{ bank: "fed" }, { bank: "rbi" }]);
  });

  it("shows the bank its path names, whichever decision is newer", async () => {
    overview.mockResolvedValue(withPolicy(SHOWN, { fed: "2026-10-28", rbi: "2026-10-07" }));
    expect(bankOf(await bankPage.default(bankProps("rbi")))).toBe("RBI");
    expect(bankOf(await bankPage.default(bankProps("fed")))).toBe("Fed");
  });

  it("answers 404 for any other segment, without a read", async () => {
    for (const bank of ["FED", "ecb", "latest", ""]) {
      await expect(bankPage.default(bankProps(bank))).rejects.toMatchObject(NOT_FOUND);
    }
    expect(overview).not.toHaveBeenCalled();
  });

  it("is behind the same gate: 404 with no row, a hidden row or a failed read (fails closed)", async () => {
    overview.mockResolvedValue(withPolicy([{ field_key: "ai.doc_summary", status: "shown", label: null }]));
    await expect(bankPage.default(bankProps("fed"))).rejects.toMatchObject(NOT_FOUND);
    overview.mockResolvedValue(withPolicy([{ field_key: "site.policy_day", status: "hidden", label: null }]));
    await expect(bankPage.default(bankProps("rbi"))).rejects.toMatchObject(NOT_FOUND);
    overview.mockResolvedValue({ ...withPolicy([]), fieldPolicy: { ok: false } });
    await expect(bankPage.default(bankProps("fed"))).rejects.toMatchObject(NOT_FOUND);
  });

  it("is never indexed", () => {
    expect(bankPage.metadata.robots).toEqual({ index: false, follow: false });
  });
});
