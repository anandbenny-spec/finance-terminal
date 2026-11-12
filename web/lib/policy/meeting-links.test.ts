// R05-9a (12 Nov 2026): every link the site builds to a meeting tab goes through `meetingTabHref`, which gives a path. A
// source scan, as meeting-view-layout.test.ts does: no code builds a meeting URL with `?tab=` or `?doc=` any more. The
// /latest menu entries (`/policy/fed/latest?tab=changes`) are the one allowed shape: that route turns the query into the
// path. next.config.ts holds the redirects that take old links, and the company pages (R05-9b) have their own rule.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (name === "node_modules" || name === ".next") return [];
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

describe("meeting links", () => {
  it("leaves no meeting URL built with ?tab= or ?doc=", () => {
    const offenders: string[] = [];
    for (const dir of ["app", "components", "lib"]) {
      for (const file of sources(join(__dirname, "..", "..", dir))) {
        if (/[\\/](equity|company|companies)[\\/]|company/i.test(file)) continue;
        readFileSync(file, "utf8")
          .split("\n")
          .forEach((line, i) => {
            const code = line.replace(/^\s*(\/\/|\*|\/\*).*$/, ""); // comments may name the old form
            if (/(meetingHref|policy\/(fed|rbi)\/)[^\n]*\?(tab|doc)=/.test(code) && !/\/latest\?tab=/.test(code)) offenders.push(`${file}:${i + 1}`);
            if (/`\$\{base\}\?(tab|doc)=/.test(code)) offenders.push(`${file}:${i + 1}`);
          });
      }
    }
    expect(offenders).toEqual([]);
  });
});
