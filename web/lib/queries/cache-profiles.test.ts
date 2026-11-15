// Every `'use cache'` read uses one of next.config.ts's no-expiry profiles (#263, 3 Nov 2026).
//
// On Vercel a page's cached copy expires after the shortest `expire` of the reads it uses; the next visitor is then
// served the build output of the last deploy (data as old as that deploy) while a fresh copy is built. The built-in
// `minutes` profile expires after an hour, so the first visitor after a quiet hour saw old data and the second saw
// current data. These profiles refresh as `minutes` and `hours` do but never expire in a quiet spell. One read on a
// built-in profile would bring the one-hour expiry back for every page that uses it, so this fails on it.
//
// The one exception is `failedRead` (#499, 13 Nov 2026): a read that failed, kept as a value (`{ ok: false }`, a held part),
// is cached for seconds and dropped after minutes, so the next visitor gets a fresh read instead of the failure. A good read
// keeps its no-expiry profile; the scan below checks that `failedRead` is called only where a read failed, and wherever one can.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { MIN_PRERENDERABLE_EXPIRE, MIN_SHELL_STALE } from "next/dist/server/use-cache/constants";
import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config";

const WEB = new URL("../../", import.meta.url).pathname;
const profiles = nextConfig.cacheLife ?? {};
const FAILED_READ = "failedRead";

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
      // Widened for `failedRead` only, Anand's OK, 13 Nov 2026 (#499): a failed read must not be kept like a good one.
      // Its own limits are the next test's; every other profile keeps the 30-day rule.
      if (name !== FAILED_READ) expect(p.expire, `${name} expire`).toBeGreaterThanOrEqual(30 * 24 * 3600);
      expect(p.revalidate, `${name} revalidate`).toBeLessThanOrEqual(3600);
      expect(p.expire!, `${name}: expire must exceed revalidate`).toBeGreaterThan(p.revalidate!);
    }
  });

  it("keep a failed read for seconds, drop it within minutes, and prerender it as today (#499)", () => {
    const p = profiles[FAILED_READ];
    expect(p, "next.config.ts has no failedRead profile").toBeDefined();
    // A request this many seconds after the failure starts a fresh read.
    expect(p.revalidate!).toBeGreaterThan(0);
    expect(p.revalidate!).toBeLessThanOrEqual(60);
    // An unvisited failure is dropped within minutes, not a year.
    expect(p.expire!).toBeLessThanOrEqual(10 * 60);
    // Below these Next leaves the read out of the prerendered page (expire) or its shell (stale), which would need a
    // Suspense boundary (next/dist/docs/01-app/03-api-reference/04-functions/cacheLife.md, "Prerendering behavior").
    expect(p.expire!).toBeGreaterThanOrEqual(MIN_PRERENDERABLE_EXPIRE);
    expect(p.stale!).toBeGreaterThanOrEqual(MIN_SHELL_STALE);
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

// The source scan (#499). A top-level function of lib/queries/, from its `function` line to its closing brace at column 0.
type Fn = { file: string; name: string; body: string; start: number };

function queryFunctions(): Fn[] {
  const dir = join(WEB, "lib/queries");
  return readdirSync(dir)
    .filter((f) => /\.ts$/.test(f) && !/\.(test|devcheck)\.ts$/.test(f))
    .flatMap((f) => {
      const text = readFileSync(join(dir, f), "utf8");
      return [...text.matchAll(/^(?:export )?(?:async )?function (\w+)[\s\S]*?^\}$/gm)].map((m) => ({
        file: `lib/queries/${f}`,
        name: m[1],
        body: m[0],
        start: m.index,
      }));
    });
}

const USE_CACHE = /^\s*["']use cache["'];?\s*$/m;
const FAILED_CALL = `cacheLife("${FAILED_READ}")`;

/** Whether a function keeps a failed read as a value: it catches (`catch`, `.catch`), or calls a lib/queries function that does. */
function keepsFailure(fns: Map<string, Fn[]>, fn: Fn, seen = new Set<string>()): boolean {
  if (/\bcatch\b/.test(fn.body)) return true;
  seen.add(fn.name);
  const called = new Set([...fn.body.matchAll(/\b(\w+)\(/g)].map((m) => m[1]));
  return [...called].some((name) => !seen.has(name) && (fns.get(name) ?? []).some((g) => keepsFailure(fns, g, seen)));
}

/** The `if (!db) …` statement of a cached read: one line, or a block to its closing brace at the same indent. */
function noClientBranch(body: string): string {
  const m = /^( *)if \(!db\)(.*)$/m.exec(body);
  if (!m) return "";
  if (!m[2].trimEnd().endsWith("{")) return m[0];
  const end = body.indexOf(`\n${m[1]}}`, m.index);
  return body.slice(m.index, end + m[1].length + 2);
}

describe("failed reads (#499)", () => {
  const fns = queryFunctions();
  const byName = new Map<string, Fn[]>();
  for (const fn of fns) byName.set(fn.name, [...(byName.get(fn.name) ?? []), fn]);
  const cached = fns.filter((fn) => USE_CACHE.test(fn.body));

  it("finds every cached read", () => {
    const directives = sources().reduce((n, { text }) => n + text.split("\n").filter((line) => USE_CACHE.test(line)).length, 0);
    expect(cached.length).toBeGreaterThan(0);
    expect(cached.length, "a `'use cache'` directive outside a top-level function of lib/queries/").toBe(directives);
  });

  it("each cached read that can keep a failure calls failedRead, and no other does", () => {
    for (const fn of cached) {
      const keeps = keepsFailure(byName, fn);
      expect(fn.body.includes(FAILED_CALL), `${fn.file} ${fn.name}: ${keeps ? "keeps a failed read but never calls" : "keeps no failed read, yet calls"} ${FAILED_CALL}`).toBe(keeps);
    }
  });

  it("calls failedRead only in a failure branch, after the read's own profile, and never for a missing client", () => {
    let calls = 0;
    for (const { file, text } of sources()) {
      for (const m of text.matchAll(/cacheLife\(\s*["']failedRead["']\s*\)/g)) {
        calls++;
        const fn = cached.find((c) => c.file === file && m.index >= c.start && m.index < c.start + c.body.length);
        expect(fn, `${file}: ${FAILED_CALL} outside a cached read of lib/queries/`).toBeDefined();
        const lines = text.slice(0, m.index).split("\n");
        const line = lines[lines.length - 1] + text.slice(m.index).split("\n")[0];
        const before = lines.slice(0, -1).reverse().find((l) => l.trim() !== "") ?? "";
        // The first statement of a catch block, or a guard on a part that failed: a `!x.ok`, or a `…ReadFailed(…)` check of the
        // parts a loader holds back on their own.
        const inCatch = /\bcatch \(\w+\) \{\s*$/.test(before) && /^\s*cacheLife\(/.test(line);
        const guarded = /^\s*if \((?=.*(?:!\w+\.ok\b|ReadFailed\()).*\) cacheLife\(["']failedRead["']\);\s*$/.test(line);
        expect(inCatch || guarded, `${file} ${fn?.name}: ${FAILED_CALL} isn't in a failure branch: ${line.trim()}`).toBe(true);
        const own = fn!.body.search(/cacheLife\(["'](?:minutesNoExpire|hoursNoExpire)["']\)/);
        expect(own >= 0 && own < m.index - fn!.start, `${file} ${fn?.name}: the read's own profile must come first`).toBe(true);
      }
    }
    expect(calls).toBeGreaterThan(0);
    // No Data API in this build (CI's, a fresh clone): the client is fixed when the build is, so reading again can't heal it,
    // and those reads keep their own profile, which leaves CI's build as it was.
    for (const fn of cached) expect(noClientBranch(fn.body).includes("failedRead"), `${fn.file} ${fn.name}: the !db branch`).toBe(false);
  });
});
