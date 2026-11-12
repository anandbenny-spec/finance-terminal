// R05-9b (12 Nov 2026): no company link is built with a query string. Every link to a company tab or view goes through
// `companyHref` (lib/equity/company.ts), so a source scan finds no `?tab=`, `basis=`, `window=` or `call=` on a /companies/ path,
// and none of the old parameters at all in the company and equity code. The redirects in next.config.ts are the one place
// that names them, and the meeting pages' own `?tab=` (components/policy, lane A) is not a company URL.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = new URL("../../", import.meta.url).pathname;
const SKIP = new Set(["node_modules", ".next", "tests", "design"]);

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (SKIP.has(name)) return [];
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

const all = ["app", "components", "lib"].flatMap((d) => sources(join(WEB, d)));
const OLD = /[?&](tab|basis|window|call)=/;

describe("company links", () => {
  it("scans the site's source", () => {
    expect(all.length).toBeGreaterThan(100);
  });
  it("builds no /companies/ URL with a query string for the old parameters", () => {
    const hits = all.flatMap((f) =>
      readFileSync(f, "utf8")
        .split("\n")
        .filter((line) => /\/companies\/[^\s"'`]*\?(tab|basis|window|call)=/.test(line))
        .map((line) => `${f.slice(WEB.length)}: ${line.trim()}`),
    );
    expect(hits).toEqual([]);
  });
  it("leaves no old parameter in the company, screener's priced-in and equity code", () => {
    const own = all.filter((f) => /\/(components\/company|lib\/equity)\//.test(f) || f.endsWith("components/screener/PricedInView.tsx") || f.endsWith("app/companies/[slug]/page.tsx"));
    expect(own.length).toBeGreaterThan(20);
    const hits = own.flatMap((f) =>
      readFileSync(f, "utf8")
        .split("\n")
        .filter((line) => OLD.test(line))
        .map((line) => `${f.slice(WEB.length)}: ${line.trim()}`),
    );
    expect(hits).toEqual([]);
  });
});
