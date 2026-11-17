import { afterEach, describe, expect, it, vi } from "vitest";
import { distinctFetch } from "./public";

describe("distinctFetch (#309)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the same URL twice with different headers, keeping the callers headers", async () => {
    const seen: Headers[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: unknown, init?: RequestInit) => {
      seen.push(new Headers(init?.headers));
      return new Response("[]");
    }));
    await distinctFetch("https://x.test/rest/v1/v", { headers: { apikey: "k" } });
    await distinctFetch("https://x.test/rest/v1/v", { headers: { apikey: "k" } });
    expect(seen[0].get("apikey")).toBe("k");
    expect(seen[0].get("x-ft-attempt")).not.toBe(seen[1].get("x-ft-attempt"));
  });
});

describe("limitedFetch: at most MAX_IN_FLIGHT Data API requests at once per server instance (#620)", () => {
  afterEach(() => vi.unstubAllGlobals());

  /** A fake network that holds each request until the test releases it, recording the order they reached it. */
  function heldNetwork() {
    const reached: string[] = [];
    const open: Array<{ url: string; finish: (r: Response | Error) => void }> = [];
    vi.stubGlobal("fetch", vi.fn((u: unknown) => {
      const url = String(u);
      reached.push(url);
      return new Promise<Response>((resolve, reject) => {
        open.push({ url, finish: (r) => (r instanceof Error ? reject(r) : resolve(r)) });
      });
    }));
    return { reached, open };
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));

  it("is two, and the client every page reads with goes through it", async () => {
    const { MAX_IN_FLIGHT, publicClient, limiterState } = await import("./public");
    expect(MAX_IN_FLIGHT).toBe(2);
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://x.test");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "anon-key-for-tests");
    const net = heldNetwork();
    const db = publicClient()!;
    const reads = ["companies", "earnings_calls", "call_figures"].map((v) => db.from(v as "companies").select("*").then((r) => r));
    await tick(); await tick();
    expect(net.reached).toHaveLength(2);
    expect(limiterState()).toEqual({ inFlight: 2, waiting: 1 });
    for (let i = 0; i < 3; i++) {
      net.open[i]?.finish(new Response("[]", { headers: { "content-type": "application/json" } }));
      await tick(); await tick();
    }
    await Promise.all(reads);
    expect(net.reached).toHaveLength(3);
    expect(limiterState()).toEqual({ inFlight: 0, waiting: 0 });
    vi.unstubAllEnvs();
  });

  it("sends two at once and holds the rest in the order they came, each going out as a slot frees", async () => {
    const { limitedFetch, limiterState } = await import("./public");
    const net = heldNetwork();
    const all = ["a", "b", "c", "d", "e"].map((v) => limitedFetch(`https://x.test/rest/v1/${v}`));
    await tick();
    expect(net.reached.map((u) => u.split("/").pop())).toEqual(["a", "b"]);
    expect(limiterState()).toEqual({ inFlight: 2, waiting: 3 });
    net.open[1].finish(new Response("[]"));
    await tick(); await tick();
    expect(net.reached.map((u) => u.split("/").pop())).toEqual(["a", "b", "c"]);
    net.open[0].finish(new Response("[]"));
    await tick(); await tick();
    expect(net.reached.map((u) => u.split("/").pop())).toEqual(["a", "b", "c", "d"]);
    for (const o of net.open.slice(2)) o.finish(new Response("[]"));
    await tick(); await tick();
    net.open[4]?.finish(new Response("[]"));
    await Promise.all(all);
    expect(limiterState()).toEqual({ inFlight: 0, waiting: 0 });
  });

  it("frees the slot when a request fails, so the next one still goes", async () => {
    const { limitedFetch, limiterState } = await import("./public");
    const net = heldNetwork();
    const a = limitedFetch("https://x.test/rest/v1/a");
    const b = limitedFetch("https://x.test/rest/v1/b");
    const c = limitedFetch("https://x.test/rest/v1/c");
    await tick();
    net.open[0].finish(new Error("network down"));
    await expect(a).rejects.toThrow("network down");
    await tick();
    expect(net.reached).toHaveLength(3);
    net.open[1].finish(new Response("[]"));
    net.open[2].finish(new Response("[]"));
    await Promise.all([b, c]);
    expect(limiterState()).toEqual({ inFlight: 0, waiting: 0 });
  });

  it("returns the database's answer whole: status, headers and body, read inside the slot", async () => {
    const { limitedFetch } = await import("./public");
    vi.stubGlobal("fetch", vi.fn(async () => new Response('[{"x":1}]', { status: 206, statusText: "Partial Content", headers: { "content-range": "0-0/7" } })));
    const res = await limitedFetch("https://x.test/rest/v1/v");
    expect(res.status).toBe(206);
    expect(res.headers.get("content-range")).toBe("0-0/7");
    expect(await res.json()).toEqual([{ x: 1 }]);
  });
});
