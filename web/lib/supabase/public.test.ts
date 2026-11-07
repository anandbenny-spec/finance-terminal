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
