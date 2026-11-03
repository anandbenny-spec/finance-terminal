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
