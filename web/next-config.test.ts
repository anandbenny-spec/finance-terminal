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

// Policy Day's bank moved from `?bank=` to the path on 12 Nov 2026 so the page can be prerendered
// (lib/policy/policyday.ts). A link shared before then must still reach the bank it named.
describe("next.config redirects()", () => {
  it("sends an old ?bank= link to the bank's own page, temporarily, and nothing else on that path", async () => {
    const policyDay = (await nextConfig.redirects!()).filter((r) => r.source === "/workspace/policy-day");
    expect(policyDay).toEqual([
      { source: "/workspace/policy-day", has: [{ type: "query", key: "bank", value: "fed" }], destination: "/workspace/policy-day/fed", permanent: false },
      { source: "/workspace/policy-day", has: [{ type: "query", key: "bank", value: "rbi" }], destination: "/workspace/policy-day/rbi", permanent: false },
    ]);
  });
  it("lands on the paths the page links to", async () => {
    const { policyDayHref } = await import("./lib/policy/policyday");
    const destinations = (await nextConfig.redirects!()).filter((r) => r.source === "/workspace/policy-day").map((r) => r.destination);
    expect(destinations).toEqual([policyDayHref("Fed"), policyDayHref("RBI")]);
  });
});

// The company page's tab and view moved from `?tab=`, `?basis=`, `?window=` and `?call=` to the path on 12 Nov 2026
// (lib/equity/company.ts `companyHref`; R05-9b). Next's own matcher isn't importable here, so this one knows only the one
// source these rules use (`/companies/:slug`) and the `has` query values as anchored regular expressions with named groups,
// and fails loudly on anything else. The first rule that matches wins, as in Next.
async function companyRedirect(path: string, query: Record<string, string>): Promise<string | null> {
  const slug = path.match(/^\/companies\/([^/]+)$/)?.[1];
  for (const rule of await nextConfig.redirects!()) {
    if (rule.source === "/workspace/policy-day") continue;
    if (rule.source !== "/companies/:slug") throw new Error(`This test understands only /companies/:slug, not ${rule.source}`);
    if (!slug) continue;
    const groups: Record<string, string> = { slug };
    let all = true;
    for (const h of rule.has ?? []) {
      if (h.type !== "query" || h.value === undefined) throw new Error("This test understands only query conditions with a value");
      const m = (query[h.key] ?? "").match(new RegExp(`^(?:${h.value})$`));
      if (!m) {
        all = false;
        break;
      }
      Object.assign(groups, m.groups);
    }
    if (all) return rule.destination.replace(/:(\w+)/g, (_, key) => groups[key] ?? `:${key}`);
  }
  return null;
}

describe("next.config redirects() for the company page's old query links", () => {
  it.each([
    ["financials", "/companies/tcs/financials"],
    ["valuation", "/companies/tcs/valuation"],
    ["rate-decisions", "/companies/tcs/rate-decisions"],
    ["calls", "/companies/tcs/calls"],
  ])("sends ?tab=%s to %s", async (tab, to) => {
    expect(await companyRedirect("/companies/tcs", { tab })).toBe(to);
  });
  it("sends each tab's view to the view's path", async () => {
    expect(await companyRedirect("/companies/tcs", { tab: "financials", basis: "standalone" })).toBe("/companies/tcs/financials/standalone");
    expect(await companyRedirect("/companies/tcs", { tab: "rate-decisions", window: "d5" })).toBe("/companies/tcs/rate-decisions/d5");
    expect(await companyRedirect("/companies/tcs", { tab: "calls", call: "q2-fy27" })).toBe("/companies/tcs/calls/q2-fy27");
  });
  it("sends a view's old parameter on another tab, or a value the tab doesn't take, to the plain tab", async () => {
    expect(await companyRedirect("/companies/tcs", { tab: "valuation", basis: "standalone" })).toBe("/companies/tcs/valuation");
    expect(await companyRedirect("/companies/tcs", { tab: "financials", basis: "consolidated" })).toBe("/companies/tcs/financials");
    expect(await companyRedirect("/companies/tcs", { tab: "rate-decisions", window: "d1" })).toBe("/companies/tcs/rate-decisions");
    expect(await companyRedirect("/companies/tcs", { tab: "calls", call: "../x" })).toBe("/companies/tcs/calls");
  });
  it("leaves a link with no tab, `overview` or a tab that isn't one alone: the Overview ignores the query, and a redirect to itself would loop", async () => {
    expect(await companyRedirect("/companies/tcs", {})).toBeNull();
    expect(await companyRedirect("/companies/tcs", { basis: "standalone" })).toBeNull();
    expect(await companyRedirect("/companies/tcs", { tab: "overview" })).toBeNull();
    expect(await companyRedirect("/companies/tcs", { tab: "nonsense" })).toBeNull();
    expect(await companyRedirect("/companies/tcs/valuation", { tab: "valuation" })).toBeNull();
  });
  it("is temporary, and lands on the addresses the page links to", async () => {
    const { companyHref } = await import("./lib/equity/company");
    const rules = (await nextConfig.redirects!()).filter((r) => r.source === "/companies/:slug");
    expect(rules).toHaveLength(4);
    for (const r of rules) expect(r.permanent).toBe(false);
    expect(await companyRedirect("/companies/tcs", { tab: "calls", call: "q1-fy27" })).toBe(companyHref("tcs", "calls", "q1-fy27"));
    expect(await companyRedirect("/companies/tcs", { tab: "financials", basis: "standalone" })).toBe(companyHref("tcs", "financials", "standalone"));
  });
});
