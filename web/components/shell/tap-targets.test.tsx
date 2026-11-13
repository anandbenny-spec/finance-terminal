// Target size (WCAG 2.2 SC 2.5.8, #437): the footer's links and the top bar's wordmark are at least 24 × 24 px.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Footer } from "@/components/shell/Footer";
import { TopBar } from "@/components/shell/TopBar";

describe("tap targets (#437)", () => {
  it("gives every footer link a 24 px minimum in both directions", () => {
    const markup = renderToStaticMarkup(<Footer />);
    const links = markup.match(/<a [^>]*>/g) ?? [];
    expect(links.length).toBe(5);
    for (const link of links) {
      expect(link).toContain("min-h-6");
      expect(link).toContain("min-w-6");
    }
  });

  it("gives the wordmark a 24 px minimum height", () => {
    const markup = renderToStaticMarkup(<TopBar />);
    const wordmark = (markup.match(/<a [^>]*href="\/"[^>]*>/) ?? [""])[0];
    expect(wordmark).toContain("min-h-6");
  });
});
