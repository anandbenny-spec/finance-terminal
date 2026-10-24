// The meeting pages answer 404 for a missing meeting before they stream (#21, item 4). The page function itself calls
// notFound(), above its Suspense boundary, and only when the existence read says the meeting is missing: a read that
// can't tell (null: a failed read, or no Data API in this build) renders the page and its error state instead. The
// route's build sample is each bank's 2018 baseline (Spec 01 §2), so the date is known before the boundary.
import { Suspense, isValidElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const exists = vi.fn<(bank: string, slug: string) => Promise<boolean | null>>();
vi.mock("@/lib/queries/meeting", () => ({ readMeetingExists: (bank: string, slug: string) => exists(bank, slug) }));
vi.mock("@/components/policy/MeetingView", () => ({ MeetingView: () => null, tabOf: () => "overview" }));

const fed = await import("@/app/policy/fed/[date]/page");
const rbi = await import("@/app/policy/rbi/[date]/page");

// The page's props as Next passes them: both promises (PageProps).
const props = <P,>(date: string) => ({ params: Promise.resolve({ date }), searchParams: Promise.resolve({}) }) as P;
const NOT_FOUND = { digest: expect.stringContaining("404") };

const routes = [
  { bank: "Fed", render: (d: string) => fed.default(props<Parameters<typeof fed.default>[0]>(d)), samples: fed.generateStaticParams, missing: "2026-09-17", held: "2026-09-16", slug: "fomc-2026-09-17", baseline: "2018-12-19" },
  { bank: "RBI", render: (d: string) => rbi.default(props<Parameters<typeof rbi.default>[0]>(d)), samples: rbi.generateStaticParams, missing: "2026-10-08", held: "2026-10-07", slug: "mpc-2026-10-08", baseline: "2018-12-05" },
] as const;

describe.each(routes)("$bank meeting page", ({ bank, render, samples, missing, held, slug, baseline }) => {
  beforeEach(() => exists.mockReset());

  it("answers 404 from the page itself when no such meeting exists", async () => {
    exists.mockResolvedValue(false);
    await expect(render(missing)).rejects.toMatchObject(NOT_FOUND);
    expect(exists).toHaveBeenCalledWith(bank, slug);
  });

  it("answers 404 for a date that isn't one, without a read", async () => {
    for (const date of ["2026-02-30", "26-09-16", "abc"]) {
      await expect(render(date)).rejects.toMatchObject(NOT_FOUND);
    }
    expect(exists).not.toHaveBeenCalled();
  });

  it("renders the meeting inside Suspense when it exists", async () => {
    exists.mockResolvedValue(true);
    const out = await render(held);
    expect(isValidElement(out) && out.type).toBe(Suspense);
  });

  it("renders the page (and its error state) when the read can't tell", async () => {
    exists.mockResolvedValue(null);
    const out = await render(held);
    expect(isValidElement(out) && out.type).toBe(Suspense);
  });

  it("prerenders the 2018 baseline as its sample", () => {
    expect(samples()).toEqual([{ date: baseline }]);
  });
});
