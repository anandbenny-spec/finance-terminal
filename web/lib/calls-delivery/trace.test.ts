import { channel } from "node:diagnostics_channel";
import { expect, it, vi } from "vitest";
import { installReadTrace } from "./trace";

it("keeps concurrent visitor reads attributed and strips query parameters", async () => {
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://trace.example");
  vi.stubEnv("CALLS_BUILD_ID", "trace-test");
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  try {
    const trace = installReadTrace();
    const reads = channel("undici:request:create");
    await Promise.all(["first", "second"].map((requestId) => trace.run({ requestId, phase: "visitor" }, async () => {
      await Promise.resolve();
      reads.publish({ request: { origin: "https://trace.example", path: "/rest/v1/companies?slug=eq.private" } });
    })));
    expect(log.mock.calls.map(([line]) => JSON.parse(line))).toEqual(["first", "second"].map((requestId) => ({
      phase: "calls-api-outbound", build: "trace-test", origin: "visitor", requestId, view: "/rest/v1/companies",
    })));
  } finally {
    log.mockRestore();
    vi.unstubAllEnvs();
  }
});
