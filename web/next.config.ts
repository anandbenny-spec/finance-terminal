import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  // Until the "Start here" landing page exists, the root goes to the Fed vs RBI tracker. Temporary (307), since the
  // landing page replaces it (#86).
  async redirects() {
    return [{ source: "/", destination: "/policy", permanent: false }];
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
