// adminFetch and readHealth (C6): the admin's token on every call, and nothing sent without one.
import { describe, expect, it } from "vitest";
import { adminFetch, readHealth, type AdminDeps } from "@/lib/admin/api";

const deps = (token: string | null, respond: () => Response | Promise<Response>, seen: string[] = []): AdminDeps => ({
  token: async () => token,
  fetch: (async (url: string) => {
    seen.push(url);
    return respond();
  }) as typeof fetch,
});

describe("adminFetch", () => {
  it("sends nothing without a session: a 401", async () => {
    const seen: string[] = [];
    expect(await adminFetch("/api/admin/health", {}, deps(null, () => new Response("{}"), seen))).toEqual({ ok: false, status: 401, body: null });
    expect(seen).toEqual([]);
  });

  it("a network failure is status 0, and a body that isn't JSON is null", async () => {
    expect(await adminFetch("/api/admin/health", {}, deps("t", () => Promise.reject(new TypeError("offline"))))).toEqual({ ok: false, status: 0, body: null });
    expect(await adminFetch("/api/admin/health", {}, deps("t", () => new Response("not json", { status: 503 })))).toEqual({ ok: false, status: 503, body: null });
  });
});

describe("readHealth", () => {
  it("returns the health body when its shape is right", async () => {
    const body = { as_of: "2026-10-24T05:00:00Z", open_alert_count: 0, tasks: [], alerts: [], runs: [] };
    expect(await readHealth(deps("t", () => new Response(JSON.stringify(body))))).toEqual({ health: body, status: 200 });
  });

  it("refuses a body of the wrong shape, and passes a refusal's status on", async () => {
    expect(await readHealth(deps("t", () => new Response(JSON.stringify({ tasks: [] }))))).toEqual({ health: null, status: 502 });
    expect(await readHealth(deps("t", () => new Response("{}", { status: 403 })))).toEqual({ health: null, status: 403 });
  });
});
