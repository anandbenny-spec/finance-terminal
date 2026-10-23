// Guards the versions A0 installed (Design 01 §8.4; CLAUDE.md §6). Upgrading one is a named, deliberate change:
// edit the version, the lock and this test in the same commit, never as a side effect of an install.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

type Pkg = {
  engines: { node: string };
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
};

const pkg: Pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const direct = { ...pkg.dependencies, ...pkg.devDependencies };

describe("web/package.json", () => {
  it("runs on Node 24", () => {
    expect(pkg.engines.node).toBe("24.x");
  });

  it("pins every direct dependency to an exact version", () => {
    const ranged = Object.entries(direct).filter(([, v]) => !/^\d+\.\d+\.\d+$/.test(v));
    expect(ranged).toEqual([]);
  });

  it("keeps TanStack Table on 8.21 (v9 postdates the build model's training: Design 01 §1.6)", () => {
    expect(pkg.dependencies["@tanstack/react-table"]).toMatch(/^8\.21\.\d+$/);
  });

  it.each([
    ["next", /^16\./],
    ["react", /^19\./],
    ["react-dom", /^19\./],
    ["lightweight-charts", /^5\./],
    ["@supabase/supabase-js", /^2\./],
    ["lucide-react", /^1\./],
  ])("keeps %s on the major version Design 01 §8.4 names", (name, major) => {
    expect(pkg.dependencies[name]).toMatch(major);
  });

  it("keeps Tailwind on 4 (the token file was compiled against 4.3.3: Design 01 §2.6)", () => {
    expect(pkg.devDependencies.tailwindcss).toMatch(/^4\./);
  });
});
