// `npm run check:dev-rows`: the row-limit check against the dev project (A1, #6; lib/queries/policy.devcheck.ts).
// Kept out of `npm test`, which runs offline in CI. Same alias as vitest.config.mts; only the files differ.
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: [{ find: /^@\//, replacement: fileURLToPath(new URL("./", import.meta.url)) }],
  },
  test: {
    include: ["**/*.devcheck.ts"],
    exclude: ["node_modules/**", ".next/**"],
    environment: "node",
    testTimeout: 30_000,
  },
});
