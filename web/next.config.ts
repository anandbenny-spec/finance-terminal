import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  // Cache profiles for every `'use cache'` read (#263, 3 Nov 2026). They refresh like the built-in `minutes` and `hours`
  // profiles (a request after `revalidate` is served the cached copy while a fresh one is built), but `expire` is a
  // year, not an hour or a day. On Vercel a page whose cached copy has expired is served from the build output of the
  // last deploy instead, so after a quiet hour the first visitor saw data as old as that deploy (days, once). The
  // route's expiry is the shortest of the reads it uses, so every read uses one of these; lib/queries/cache-profiles.test.ts
  // fails on a read that doesn't.
  cacheLife: {
    minutesNoExpire: { stale: 300, revalidate: 60, expire: 31_536_000 },
    hoursNoExpire: { stale: 300, revalidate: 3600, expire: 31_536_000 },
  },
  // /favicon.ico answered 404; the icon route (app/icon.tsx) is the site's icon (1 Nov 2026, #228).
  async rewrites() {
    return [{ source: "/favicon.ico", destination: "/icon" }];
  },
  // Policy Day's bank moved from `?bank=` to the path on 12 Nov 2026 (lib/policy/policyday.ts), so the page can be
  // prerendered. Links shared before then still work: they are sent on to the bank's own page. Temporary (307), so a
  // browser never remembers it. The query string rides along, unused. web/next-config.test.ts covers this.
  //
  // The company page's tab and view moved from `?tab=`, `?basis=`, `?window=` and `?call=` to the path the same day
  // (lib/equity/company.ts `companyHref`; R05-9b). The first rule that matches wins, so the views come before the plain tab.
  // A `tab` that isn't one, or `overview`, needs no rule: /companies/:slug ignores its query and shows the Overview, and a
  // redirect to itself would loop. A named group in a `has` value is used in the destination (`:tab`, `:call`) and then
  // dropped from the query.
  //
  // A meeting's tab and the Governor's statement moved from `?tab=` and `?doc=governor` to the path the same day
  // (lib/policy/decisions.ts `meetingTabHref`; R05-9a). The date is matched as a date so /policy/fed/latest?tab=changes still
  // reaches its own route, which sends the reader on. The Governor's statement comes first (RBI only); `overview` and a `tab`
  // that isn't one need no rule: the bare date ignores its query and shows the Overview, and a redirect to itself would loop.
  async redirects() {
    const policyDay = ["fed", "rbi"].map((bank) => ({
      source: "/workspace/policy-day",
      has: [{ type: "query" as const, key: "bank", value: bank }],
      destination: `/workspace/policy-day/${bank}`,
      permanent: false,
    }));
    const company = (has: { key: string; value: string }[], destination: string) => ({
      source: "/companies/:slug",
      has: has.map((h) => ({ type: "query" as const, ...h })),
      destination,
      permanent: false,
    });
    const meetingDate = ":date(\\d{4}-\\d{2}-\\d{2})";
    const meeting = (source: string, has: { key: string; value: string }[], destination: string) => ({
      source,
      has: has.map((h) => ({ type: "query" as const, ...h })),
      destination,
      permanent: false,
    });
    return [
      ...policyDay,
      meeting(`/policy/rbi/${meetingDate}`, [{ key: "tab", value: "statement" }, { key: "doc", value: "governor" }], "/policy/rbi/:date/statement/governor"),
      meeting(`/policy/:bank(fed|rbi)/${meetingDate}`, [{ key: "tab", value: "(?<tab>changes|statement|minutes|press|reaction)" }], "/policy/:bank/:date/:tab"),
      company([{ key: "tab", value: "financials" }, { key: "basis", value: "standalone" }], "/companies/:slug/financials/standalone"),
      company([{ key: "tab", value: "rate-decisions" }, { key: "window", value: "d5" }], "/companies/:slug/rate-decisions/d5"),
      company([{ key: "tab", value: "calls" }, { key: "call", value: "(?<call>[a-z0-9-]{1,40})" }], "/companies/:slug/calls/:call"),
      company([{ key: "tab", value: "(?<tab>financials|valuation|rate-decisions|calls)" }], "/companies/:slug/:tab"),
    ];
  },
  // Security headers on every route (#367, R4; Review 02a F1-05). `DENY` and not `SAMEORIGIN`: nothing on the site frames
  // its own pages, and the admin page's session is kept in the browser, so framing it is the realistic gap. There is no
  // Content-Security-Policy on purpose: a real one needs nonces for Next's inline scripts and ships report-only first
  // (Review 02a I-5, after 18 Nov). Vercel adds Strict-Transport-Security by itself. web/next-config.test.ts covers this.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
        ],
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
