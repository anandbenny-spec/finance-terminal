// Security headers (#367, R4; Review 02a F1-05): next.config.ts sends four headers on every route and keeps the favicon
// rewrite. No Content-Security-Policy (it needs nonces; Review 02a I-5, after 18 Nov).
import { describe, expect, it } from "vitest";
import nextConfig from "./next.config";

const EXPECTED = {
  "x-frame-options": "DENY",
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
  "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
};

/** Next's own matcher isn't typed, so this one knows only the catch-all `/:path*` (Next's docs: it matches every path, `/`
 * included) and fails loudly on any other pattern, so a changed rule can't pass by accident. */
function matches(source: string, path: string): boolean {
  if (source !== "/:path*") throw new Error(`This test understands only the catch-all source, not ${source}`);
  return path.startsWith("/");
}

async function headersFor(path: string): Promise<Record<string, string>> {
  const rules = await nextConfig.headers!();
  const found: Record<string, string> = {};
  for (const rule of rules) {
    if (!matches(rule.source, path)) continue;
    for (const h of rule.headers) found[h.key.toLowerCase()] = h.value; // a later rule overrides an earlier one (Next's docs)
  }
  return found;
}

describe("next.config headers()", () => {
  it.each(["/", "/screener", "/admin", "/companies/tcs", "/feed.xml", "/opengraph-image"])("sends the four headers for %s", async (path) => {
    expect(await headersFor(path)).toEqual(EXPECTED);
  });
  it("has one rule for every route, so a new page is covered without an edit", async () => {
    const rules = await nextConfig.headers!();
    expect(rules).toHaveLength(1);
    expect(rules[0].source).toBe("/:path*");
  });
  it("denies framing outright and adds no Content-Security-Policy", async () => {
    const found = await headersFor("/admin");
    expect(found["x-frame-options"]).toBe("DENY");
    expect(Object.keys(found)).not.toContain("content-security-policy");
    expect(Object.keys(found)).not.toContain("content-security-policy-report-only");
  });
  it("keeps the favicon rewrite", async () => {
    expect(await nextConfig.rewrites!()).toEqual([{ source: "/favicon.ico", destination: "/icon" }]);
  });
});
