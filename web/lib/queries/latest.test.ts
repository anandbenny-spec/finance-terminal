// /policy/fed/latest and /policy/rbi/latest (#147): a 307 to the newest held meeting, keeping a valid ?tab=, read on
// every request, and never a raw error. The routes are the real route modules; only the database read is a stand-in.
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const readLatest = vi.fn<(db: unknown, bank: string) => Promise<{ decision_date: string } | null>>();
const client = vi.fn<() => object | null>();
vi.mock("@/lib/queries/policy", () => ({ readLatestDecision: (db: unknown, bank: string) => readLatest(db, bank) }));
vi.mock("@/lib/supabase/public", () => ({ publicClient: () => client() }));

const fed = await import("@/app/policy/fed/latest/route");
const rbi = await import("@/app/policy/rbi/latest/route");

const ask = (route: { GET: (r: NextRequest) => Promise<Response> }, query = "") =>
  route.GET(new NextRequest(`https://finance-terminal.test/policy/x/latest${query}`));

describe.each([
  { bank: "Fed", route: fed, newest: "2026-09-16", base: "/policy/fed/2026-09-16" },
  { bank: "RBI", route: rbi, newest: "2026-10-07", base: "/policy/rbi/2026-10-07" },
])("$bank latest route", ({ bank, route, newest, base }) => {
  beforeEach(() => {
    readLatest.mockReset();
    client.mockReset();
    client.mockReturnValue({});
    readLatest.mockResolvedValue({ decision_date: newest });
  });
  afterEach(() => vi.restoreAllMocks());

  it("answers 307 to the newest held meeting, with the What changed tab kept", async () => {
    const res = await ask(route, "?tab=changes");
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(`${base}?tab=changes`);
    expect(readLatest).toHaveBeenCalledTimes(1);
    expect(readLatest.mock.calls[0][1]).toBe(bank);
  });

  it("lands on the Overview with no tab, and drops a bad tab or anything else in the query", async () => {
    expect((await ask(route)).headers.get("location")).toBe(base);
    expect((await ask(route, "?tab=nonsense")).headers.get("location")).toBe(base);
    expect((await ask(route, "?tab=changes&doc=governor&next=https://evil.test")).headers.get("location")).toBe(`${base}?tab=changes`);
  });

  it("is never cached: it reads on every request, so a new decision is reached at once", async () => {
    const first = await ask(route, "?tab=changes");
    readLatest.mockResolvedValue({ decision_date: "2026-12-09" });
    const second = await ask(route, "?tab=changes");
    expect(readLatest).toHaveBeenCalledTimes(2);
    expect(first.headers.get("location")).toBe(`${base}?tab=changes`);
    expect(second.headers.get("location")).toBe(`${base.replace(newest, "2026-12-09")}?tab=changes`);
    expect(second.headers.get("cache-control")).toBe("no-store");
  });

  it("answers 307 to the Latest decisions section when no meeting has been held", async () => {
    readLatest.mockResolvedValue(null);
    const res = await ask(route, "?tab=changes");
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("/policy#latest-decisions");
  });

  it("answers the same, not an error, when the read fails (the reason goes to the log only)", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    readLatest.mockRejectedValue(new Error("PostgREST 503"));
    const res = await ask(route, "?tab=changes");
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("/policy#latest-decisions");
    expect(await res.text()).toBe("");
    expect(log).toHaveBeenCalledTimes(1);
    expect(String(log.mock.calls[0][0])).toContain("PostgREST 503");
  });

  it("answers the same when this build has no Data API, without a read", async () => {
    client.mockReturnValue(null);
    const res = await ask(route);
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("/policy#latest-decisions");
    expect(readLatest).not.toHaveBeenCalled();
  });
});
