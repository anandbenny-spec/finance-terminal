// The admin RefreshButton's states (C6; Design 01 §4.7) from the refresh route's answers (A3; Arch 02 §1.6).
import { describe, expect, it } from "vitest";
import type { AdminDeps } from "@/lib/admin/api";
import { GIVE_UP_MS, afterPoll, afterStart, initialState, pollRefresh, startRefresh, type RefreshState } from "@/lib/admin/refresh";

const NOW = "2026-10-24T05:00:00.000Z";
const running: Extract<RefreshState, { kind: "running" }> = { kind: "running", requestId: 7, runUrl: null, since: NOW };

describe("afterStart", () => {
  it("a dispatched request is running, with its run link", () => {
    expect(afterStart(202, { request_id: 7, status: "dispatched", run_url: "https://github.com/x/actions/runs/1" }, NOW)).toEqual({
      kind: "running", requestId: 7, runUrl: "https://github.com/x/actions/runs/1", since: NOW,
    });
    expect(afterStart(202, { request_id: 8, status: "dispatched" }, NOW)).toMatchObject({ kind: "running", runUrl: null });
  });

  it("the route's rejections become the cooldown or a reason", () => {
    expect(afterStart(429, { request_id: 1, status: "rejected_cooldown" }, NOW)).toEqual({ kind: "cooldown", until: null });
    expect(afterStart(429, { request_id: 1, status: "rejected_nse" }, NOW)).toMatchObject({ kind: "rejected", reason: expect.stringContaining("NSE") });
    expect(afterStart(409, { request_id: 1, status: "rejected_disabled" }, NOW)).toMatchObject({ reason: "This task is turned off in tasks.yaml." });
    expect(afterStart(404, null, NOW)).toMatchObject({ reason: "This task doesn't exist." });
    expect(afterStart(502, { status: "failed" }, NOW)).toMatchObject({ reason: "GitHub didn't start the run. Try again." });
    for (const s of [401, 403]) expect(afterStart(s, null, NOW)).toMatchObject({ reason: "Your admin session has ended. Log in again." });
    for (const s of [0, 500, 503]) expect(afterStart(s, null, NOW)).toMatchObject({ reason: "Couldn't reach the refresh service. Try again." });
  });

  it("a 202 without a request id isn't trusted", () => {
    expect(afterStart(202, {}, NOW).kind).toBe("rejected");
  });
});

describe("afterPoll", () => {
  const status = (job: unknown, extra: object = {}) => ({ request_id: 7, task: "policy.calendar", status: "dispatched", github_run_id: 5, run_url: "https://r/5", job_run: job, ...extra });

  it("keeps running until the run links itself and finishes, picking up the run link", () => {
    expect(afterPoll(running, 200, status(null), NOW)).toEqual({ ...running, runUrl: "https://r/5" });
    expect(afterPoll(running, 200, status({ id: 1, status: "running", started_at: NOW, finished_at: null }), NOW).kind).toBe("running");
  });

  it("a finished run is done (success or partial) or rejected (failed)", () => {
    const fin = "2026-10-24T05:03:00Z";
    expect(afterPoll(running, 200, status({ id: 1, status: "success", started_at: NOW, finished_at: fin }), NOW)).toEqual({ kind: "done", at: fin });
    expect(afterPoll(running, 200, status({ id: 1, status: "partial", started_at: NOW, finished_at: fin }), NOW).kind).toBe("done");
    expect(afterPoll(running, 200, status({ id: 1, status: "failed", started_at: NOW, finished_at: fin }), NOW)).toEqual({ kind: "rejected", reason: "The run failed: see the health page." });
  });

  it("a status check that fails keeps waiting, an expired session stops, and it gives up after 75 minutes", () => {
    expect(afterPoll(running, 503, null, NOW)).toEqual(running);
    expect(afterPoll(running, 401, null, NOW).kind).toBe("rejected");
    const late = new Date(Date.parse(NOW) + GIVE_UP_MS + 1000).toISOString();
    expect(afterPoll(running, 200, status(null), late)).toMatchObject({ kind: "rejected", reason: "No result after 75 minutes: check the run." });
  });
});

describe("initialState", () => {
  it("is the cooldown while the health page says so, idle otherwise", () => {
    expect(initialState("2026-10-24T05:30:00Z", NOW)).toEqual({ kind: "cooldown", until: "2026-10-24T05:30:00Z" });
    expect(initialState("2026-10-24T04:30:00Z", NOW)).toEqual({ kind: "idle" });
    expect(initialState(null, NOW)).toEqual({ kind: "idle" });
    expect(initialState(undefined, NOW)).toEqual({ kind: "idle" });
  });
});

describe("startRefresh and pollRefresh", () => {
  it("send the admin's token to the refresh routes", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const deps: AdminDeps = {
      token: async () => "admin-token",
      fetch: (async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return new Response(JSON.stringify(url.endsWith("/refresh") ? { request_id: 9, status: "dispatched" } : { request_id: 9, status: "dispatched", job_run: null }), { status: url.endsWith("/refresh") ? 202 : 200 });
      }) as typeof fetch,
    };
    const started = await startRefresh("policy.calendar", () => NOW, deps);
    expect(started).toMatchObject({ kind: "running", requestId: 9 });
    await pollRefresh(started as typeof running, () => NOW, deps);
    expect(calls.map((c) => [c.url, c.init.method])).toEqual([["/api/admin/refresh", "POST"], ["/api/admin/refresh/9", "GET"]]);
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ task: "policy.calendar" });
    for (const c of calls) expect((c.init.headers as Record<string, string>).Authorization).toBe("Bearer admin-token");
  });
});
