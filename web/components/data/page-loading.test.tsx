// PageLoading (12 Nov 2026): what a page shows while its body is built on the server. It says what is loading in a
// status region, and its motion is decoration only: bars and a cursor hidden from assistive technology, in grey and
// white (green and red mean a market move: CLAUDE.md §5), built from animation utilities the site already ships.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PageLoading } from "@/components/data/PageLoading";

const WEB = new URL("../../", import.meta.url).pathname;
const out = renderToStaticMarkup(<PageLoading>Loading the screener…</PageLoading>);

describe("PageLoading", () => {
  it("announces what is loading in a status region", () => {
    expect(out).toMatch(/^<div role="status" data-loading="true"/);
    expect(out).toContain("<span>Loading the screener…</span>");
  });

  it("hides the bars and the cursor from assistive technology", () => {
    const bars = out.match(/<div aria-hidden="true" class="[^"]*">(.*?)<\/div>/)?.[1] ?? "";
    expect(bars.match(/<span /g)).toHaveLength(12);
    expect(out).toMatch(/<span aria-hidden="true" class="[^"]*animate-caret-blink[^"]*"><\/span>/);
  });

  it("pulses each bar a step after the one before it, so the wave runs left to right", () => {
    const delays = [...out.matchAll(/animate-pulse[^"]*\bdelay-(\d+)\b/g)].map((m) => Number(m[1]));
    expect(delays).toEqual([0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100]);
  });

  it("uses grey and white only: never the market colours", () => {
    expect(out).not.toMatch(/\b(bg|text|border)-(up|down|ins-ink|del-ink|series-\d|warn|error|ok)\b/);
    expect(out).toMatch(/bg-ink-3/);
    expect(out).toMatch(/bg-highlight/);
  });

  it("holds room for the body, so the footer doesn't jump when the content arrives", () => {
    expect(out).toMatch(/^<div [^>]*class="[^"]*\bmin-h-80\b/);
  });

  it("is what every page that streams its body shows meanwhile", () => {
    const pages = (readdirSync(join(WEB, "app"), { recursive: true }) as string[]).filter((f) => /page\.tsx$/.test(f));
    const streaming = pages.filter((f) => /<Suspense\b/.test(readFileSync(join(WEB, "app", f), "utf8")));
    expect(streaming.length).toBeGreaterThanOrEqual(5); // 8 until the company page (R05-9b) left Suspense for the cache; 7 pages stream now
    for (const f of streaming) {
      const fallbacks = [...readFileSync(join(WEB, "app", f), "utf8").matchAll(/<Suspense fallback=\{<(\w+)/g)].map((m) => m[1]);
      expect(fallbacks, f).not.toHaveLength(0);
      for (const name of fallbacks) expect(name, f).toBe("PageLoading");
    }
  });
});
