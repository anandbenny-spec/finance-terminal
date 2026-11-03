// Every `'use cache'` read uses one of next.config.ts's no-expiry profiles (#263, 3 Nov 2026).
//
// On Vercel a page's cached copy expires after the shortest `expire` of the reads it uses; the next visitor is then
// served the build output of the last deploy (data as old as that deploy) while a fresh copy is built. The built-in
// `minutes` profile expires after an hour, so the first visitor after a quiet hour saw old data and the second saw
// current data. These profiles refresh as `minutes` and `hours` do but never expire in a quiet spell. One read on a
// built-in profile would bring the one-hour expiry back for every page that uses it, so this fails on it.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config";

const WEB = new URL("../../", import.meta.url).pathname;
const profiles = nextConfig.cacheLife ?? {};

function sources(): { file: string; text: string }[] {
  return ["lib", "app", "components"].flatMap((dir) =>
    (readdirSync(join(WEB, dir), { recursive: true }) as string[])
      .filter((f) => /\.tsx?$/.test(f) && !/\.(test|devcheck)\.tsx?$/.test(f))
      .map((f) => ({ file: `${dir}/${f}`, text: readFileSync(join(WEB, dir, f), "utf8") })),
  );
}

describe("cache profiles", () => {
  it("never expire inside a quiet spell, and still refresh at least hourly", () => {
    expect(Object.keys(profiles)).toEqual(expect.arrayContaining(["minutesNoExpire", "hoursNoExpire"]));
    for (const [name, p] of Object.entries(profiles)) {
      expect(p.expire, `${name} expire`).toBeGreaterThanOrEqual(30 * 24 * 3600);
      expect(p.revalidate, `${name} revalidate`).toBeLessThanOrEqual(3600);
      expect(p.expire!, `${name}: expire must exceed revalidate`).toBeGreaterThan(p.revalidate!);
    }
  });

  it("are the only ones the reads use", () => {
    const calls = sources().flatMap(({ file, text }) =>
      [...text.matchAll(/\bcacheLife\(\s*([^)]*?)\s*\)/g)].map((m) => ({ file, arg: m[1] })),
    );
    expect(calls.length).toBeGreaterThan(0);
    for (const { file, arg } of calls) {
      const name = /^["']([A-Za-z]+)["']$/.exec(arg)?.[1];
      expect(name !== undefined && name in profiles, `${file}: cacheLife(${arg}) isn't one of ${Object.keys(profiles).join(", ")}`).toBe(true);
    }
  });
});
