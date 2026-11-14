// /methodology (#201): the page renders, claims no SEBI registration, compliance or endorsement, never links to RBI,
// shows no figures beyond the allowed constants, and every internal link points at a route that exists.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { bannedWordsIn } from "@/tests/banned-words";
import MethodologyPage from "./page";

const markup = renderToStaticMarkup(<MethodologyPage />);
const text = markup.replace(/<[^>]+>/g, " ").replace(/&#x27;|&#39;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

describe("/methodology", () => {
  it("renders every section", () => {
    for (const h of ["What this site is", "Where the data comes from", "How AI is used", "Human oversight and accountability", "Kill switches", "Source traceability", "Testing and monitoring", "The US slice (Dow 30)", "Limitations", "Accountability and contact"]) {
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
    const stripped = text.replace(/400 characters|20 June 2025|August 2026|10-year|Fed vs RBI|Nifty 50|against 30 passages|Dow 30|10-K|10-Q|S&P 500|rate is 25%|Claude Opus 5\.5/g, "");
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
    expect(text).toContain("leaves out dividends");
    expect(markup).toContain('href="/accuracy"');
    expect(text).toContain("labelled AI-extracted");
    expect(text).toContain("Management's tone on each call is scored by AI and labelled AI-scored");
    expect(text).toContain("checked against 30 passages labelled by a separate AI model (Claude Fable)");
    expect(text).toContain("is shown only once that check has passed");
    expect(text).not.toContain("no tone or sentiment is read");
    expect(text).toContain("Transcript text stays private");
    expect(text).toContain("the source it comes from is removed promptly");
    expect(text).toContain("The daily and watch workflows can also be started with AI switched off");
    expect(text).toContain("final guidelines SEBI said in August 2026 were coming");
    expect(text).not.toContain("announced final guidelines");
    expect(text).toContain("A missed target is a fact about a figure, not a verdict on management");
    expect(text).toContain("at least five scored targets");
    expect(text).not.toContain("not scored against what a company later delivered");
    expect(text).not.toMatch(/\b(hit rate|delivered on guidance|beat guidance|missed guidance)\b/i);
  });
});

describe("/methodology: the US slice (US-09, #408)", () => {
  // the section's own text: from its heading to the next section's
  const section = markup.match(/<h2 id="us"[\s\S]*?(?=<h2 id=")/)?.[0] ?? "";
  const us = section.replace(/<[^>]+>/g, " ").replace(/&#x27;|&#39;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

  it("names its sources, each with what it gives", () => {
    expect(us).toContain("10-K and 10-Q filings, read from SEC EDGAR");
    expect(us).toContain("Yahoo Finance, used for this non-commercial project");
    expect(us).toContain("the 10-year Treasury yield, via FRED");
    expect(us).toContain("Aswath Damodaran of NYU Stern");
    expect(us).toContain("never compared, ranked or summed with an Indian one");
    expect(us).not.toMatch(/\bIMF\b|GICS/);
  });

  it("states the build rules: EDGAR's, split-adjusted prices, no country risk premium, a 25% marginal tax rate", () => {
    expect(us).toContain("where a company files its total revenue beside a narrower revenue line, the total is used");
    expect(us).toContain("left empty rather than worked out from other lines");
    expect(us).toContain("the fourth quarter is the fiscal year less its first nine months");
    expect(us).toContain("a later restatement doesn't rewrite an earlier filing");
    expect(us).toContain("operating income is the line as filed, never derived");
    expect(us).toContain("adjusts once, by its own factor, for each split after the first stored price");
    expect(us).toContain("adjusted for splits, without dividends");
    expect(us).toContain("with no country risk premium added");
    expect(us).toContain("The marginal tax rate is 25%");
  });

  it("says what isn't covered", () => {
    expect(us).toContain("Not covered for US companies: earnings calls (there is no free transcript source), guidance, the value chain and rate sensitivity");
    expect(us).toContain("not the rest of the S&P 500");
  });

  it("names the judge by model, never Anand, and points to the counts on /accuracy (Plan 05 §5)", () => {
    expect(us).toContain("checked by an AI model, Claude Opus 5.5, in a separate session that built none of it");
    expect(us).not.toMatch(/Anand|reviewed by|labelled by (the owner|a person)/i);
    expect(section).toContain('href="/accuracy"');
  });

  it("uses no banned word (Spec 02A §5.9)", () => {
    expect(us.length).toBeGreaterThan(0);
    expect(bannedWordsIn(us)).toEqual([]);
  });

  it("scopes the India-only limits to India now that the Dow 30 is shown", () => {
    expect(text).toContain("For India the screener and company pages cover the Nifty 50 only, and for the US the Dow 30 only.");
    expect(text).toContain("For Indian companies, debt is borrowings only");
    expect(text).not.toContain("The screener and company pages cover the Nifty 50 only.");
  });
});

describe("/methodology panel titles (R05-10)", () => {
  // each section's heading (the h2) and its panel's title (the h3 "<id>-title") say different words
  const headings = [...markup.matchAll(/<h2 id="([^"]+)"[^>]*>([\s\S]*?)<\/h2>/g)];
  it("no panel title repeats its section's heading, and the section ids are unchanged", () => {
    expect(headings.map((m) => m[1])).toEqual(["alignment", "what", "data", "ai", "oversight", "switches", "trace", "testing", "us", "limits", "contact"]);
    for (const [, id, inner] of headings) {
      const heading = inner.replace(/<[^>]+>/g, "").trim();
      const panel = markup.match(new RegExp(`<h3 id="${id}-title"[^>]*>([\\s\\S]*?)</h3>`))?.[1].replace(/<[^>]+>/g, "").trim();
      expect(panel, id).toBeTruthy();
      expect(panel, id).not.toBe(heading);
    }
  });
});
