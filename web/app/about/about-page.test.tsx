// /about (#86): the data ownership statement comes first, RBI is named and never linked, and the page promises
// only what is true for this link (no cookies, no analytics, the command bar not live). Also the root redirect.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config";
import AboutPage from "./page";

const markup = renderToStaticMarkup(<AboutPage />);
const text = markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();

describe("/about", () => {
  it("opens with the data ownership statement, before anything else on the page", () => {
    const statement = "Every piece of data on this site belongs to its owner";
    const at = text.indexOf(statement);
    expect(at).toBeGreaterThan(-1);
    for (const later of ["What is logged", "Where the figures come from", "If you own data shown here"]) {
      expect(text.indexOf(later)).toBeGreaterThan(at);
    }
    expect(text).toContain("it earns no money, and it sells, licenses or exports no data");
  });

  it("names RBI as a source but never links to it (CLAUDE.md §1)", () => {
    expect(text).toContain("Source: Reserve Bank of India");
    expect(markup).not.toMatch(/href="[^"]*rbi\.org\.in/i);
    expect(markup).not.toMatch(/href="https?:/);
  });

  it("carries the source lines and copyright owners of the index series", () => {
    for (const s of ["S&P Dow Jones Indices LLC", "Nasdaq, Inc.", "NSE Indices", "BSE Index Services", "via FRED"]) {
      expect(text.replace(/&amp;/g, "&")).toContain(s);
    }
  });

  it("states what is logged for this link and nothing about stored questions", () => {
    expect(text).toContain("no cookies for visitors");
    expect(text).toContain("runs no analytics");
    expect(text).toContain("command bar is not live on this link");
  });

  it("shows no figures: no digit in the body except in index and release names", () => {
    expect(text.replace(/&amp;/g, "&").replace(/Nifty 50|S&P 500|Nasdaq-100|10-year|H\.10|H\.15/g, "")).not.toMatch(/\d/);
  });
});

describe("the root route", () => {
  // #86 sent / to /policy until the front page existed; #152 replaced the redirect with app/page.tsx.
  it("no longer redirects /: the front page answers it (#152)", async () => {
    const rules = nextConfig.redirects ? await nextConfig.redirects() : [];
    expect(rules.filter((r) => r.source === "/")).toEqual([]);
    expect(existsSync(fileURLToPath(new URL("./../page.tsx", import.meta.url)))).toBe(true);
  });
});
