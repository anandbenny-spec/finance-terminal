// The meeting pages answer 404 for a missing meeting before they stream (#21, item 4). The page function itself calls
// notFound(), above its Suspense boundary, and only when the existence read says the meeting is missing: a read that
// can't tell (null: a failed read, or no Data API in this build) renders the page and its error state instead. The
// route's build sample is each bank's 2018 baseline (Spec 01 §2), so the date is known before the boundary.
import { isValidElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const exists = vi.fn<(bank: string, slug: string) => Promise<boolean | null>>();
vi.mock("@/lib/queries/meeting", () => ({ readMeetingExists: (bank: string, slug: string) => exists(bank, slug) }));
vi.mock("@/components/policy/MeetingView", () => ({ MeetingView: () => null }));

const fed = await import("@/app/policy/fed/[date]/page");
const rbi = await import("@/app/policy/rbi/[date]/page");

// The page's props as Next passes them: both promises (PageProps).
const props = <P,>(date: string) => ({ params: Promise.resolve({ date }) }) as P;
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

  it("renders the meeting's Overview when it exists, with nothing read from the query string", async () => {
    exists.mockResolvedValue(true);
    const out = await render(held);
    expect(isValidElement(out) && out.props).toMatchObject({ bank, slug: expect.any(String), date: held, tab: "overview" });
  });

  it("renders the page (and its error state) when the read can't tell", async () => {
    exists.mockResolvedValue(null);
    const out = await render(held);
    expect(isValidElement(out)).toBe(true);
  });

  it("prerenders the 2018 baseline as its sample", () => {
    expect(samples()).toEqual([{ date: baseline }]);
  });
});

// The tab is a path segment (R05-9a, 12 Nov 2026): /policy/fed/2026-09-16/changes. `overview` is the bare date's page, so that
// segment answers 404, as does any other that is not a tab, and so does a missing meeting.
const fedTab = await import("@/app/policy/fed/[date]/[tab]/page");
const rbiTab = await import("@/app/policy/rbi/[date]/[tab]/page");
const governor = await import("@/app/policy/rbi/[date]/statement/governor/page");

describe.each([
  { bank: "Fed", page: fedTab, held: "2026-09-16", baseline: "2018-12-19" },
  { bank: "RBI", page: rbiTab, held: "2026-10-07", baseline: "2018-12-05" },
] as const)("$bank meeting tab page", ({ bank, page, held, baseline }) => {
  const render = (date: string, tab: string) => Promise.resolve((page.default as (p: unknown) => unknown)({ params: Promise.resolve({ date, tab }) }));
  beforeEach(() => exists.mockReset());

  it.each(["changes", "statement", "minutes", "press", "reaction"])("renders the %s tab from the path alone", async (tab) => {
    exists.mockResolvedValue(true);
    const out = await render(held, tab);
    expect(isValidElement(out) && out.props).toMatchObject({ bank, date: held, tab });
  });

  it.each(["overview", "governor", "nonsense", ""])("answers 404 for the segment '%s', without a read", async (tab) => {
    await expect(render(held, tab)).rejects.toMatchObject(NOT_FOUND);
    expect(exists).not.toHaveBeenCalled();
  });

  it("answers 404 for a missing meeting or a date that isn't one", async () => {
    exists.mockResolvedValue(false);
    await expect(render(held, "changes")).rejects.toMatchObject(NOT_FOUND);
    await expect(render("abc", "changes")).rejects.toMatchObject(NOT_FOUND);
  });

  it("prerenders the 2018 baseline's What changed tab as its sample", () => {
    expect(page.generateStaticParams()).toEqual([{ date: baseline, tab: "changes" }]);
  });
});

describe("the RBI's Governor's statement page", () => {
  const render = (date: string) => governor.default({ params: Promise.resolve({ date }) } as Parameters<typeof governor.default>[0]);
  beforeEach(() => exists.mockReset());

  it("renders the Statement tab with the Governor's document", async () => {
    exists.mockResolvedValue(true);
    const out = await render("2026-10-07");
    expect(isValidElement(out) && out.props).toMatchObject({ bank: "RBI", date: "2026-10-07", tab: "statement", doc: "governor" });
  });

  it("answers 404 for a missing meeting", async () => {
    exists.mockResolvedValue(false);
    await expect(render("2026-10-08")).rejects.toMatchObject(NOT_FOUND);
  });
});
