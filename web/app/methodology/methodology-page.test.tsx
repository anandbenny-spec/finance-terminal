// /methodology (#201): the page renders, claims no SEBI registration, compliance or endorsement, never links to RBI,
// shows no figures beyond the allowed constants, and every internal link points at a route that exists.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MethodologyPage from "./page";

const markup = renderToStaticMarkup(<MethodologyPage />);
const text = markup.replace(/<[^>]+>/g, " ").replace(/&#x27;|&#39;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

describe("/methodology", () => {
  it("renders every section", () => {
    for (const h of ["What this site is", "Where the data comes from", "How AI is used", "Human oversight and accountability", "Kill switches", "Source traceability", "Testing and monitoring", "Limitations", "Accountability and contact"]) {
      expect(text).toContain(h);
    }
  });

  it("carries the alignment note and claims no compliance, registration or endorsement", () => {
    expect(text).toContain("claims no compliance or endorsement");
    expect(text).toContain("not a SEBI-registered research analyst or investment adviser");
    expect(text).not.toMatch(/SEBI[- ](registered|approved|compliant|endorsed)(?! research)/i);
  });

  it("is informational only and gives no verdicts", () => {
    expect(text).toContain("informational only, not investment advice");
    expect(text).not.toMatch(/\b(buy rating|sell rating|price target|upside|undervalued|overvalued)\b/i);
  });

  it("links to no outside page, and RBI is never linked (CLAUDE.md §1)", () => {
    expect(markup).not.toMatch(/href="https?:/);
    expect(markup).not.toMatch(/rbi\.org\.in/i);
  });

  it("shows no figures: no digit except the allowed constants", () => {
    const stripped = text.replace(/400 characters|20 June 2025|10-year|Fed vs RBI|Nifty 50/g, "");
    expect(stripped).not.toMatch(/\d/);
  });

  it("every internal link resolves to a route that exists", () => {
    const hrefs = [...markup.matchAll(/href="(\/[^"#]*)(#[^"]*)?"/g)].map((m) => m[1]);
    expect(hrefs.length).toBeGreaterThan(0);
    for (const h of hrefs) {
      const dir = fileURLToPath(new URL(`../${h.replace(/^\//, "")}/page.tsx`, import.meta.url));
      expect(existsSync(dir), h).toBe(true);
    }
  });

  it("carries module 2's shipped limits and the link to /accuracy", () => {
    expect(text).toContain("Nifty 50 only");
    expect(text).toContain("excluding lease liabilities");
    expect(text).toContain("derived from the filed lines");
    expect(markup).toContain('href="/accuracy"');
    expect(text).not.toMatch(/earnings call/i);
  });
});
