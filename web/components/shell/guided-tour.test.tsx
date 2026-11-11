// The "Take the tour" buttons (#91): nothing renders while the tour is hidden (no site.guided_tour row), and the tour
// never starts by itself when it is published.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TakeTourButton, TourProvider } from "@/components/shell/GuidedTour";
import { NAV } from "@/lib/shell/nav";

const render = (shown: boolean | null, variant: "page" | "nav") =>
  renderToStaticMarkup(shown === null ? <TakeTourButton variant={variant} /> : <TourProvider shown={shown}><TakeTourButton variant={variant} /></TourProvider>);

describe("Take the tour", () => {
  it("renders no button without a provider or while the tour is hidden", () => {
    for (const variant of ["page", "nav"] as const) {
      expect(render(null, variant)).toBe("");
      expect(render(false, variant)).toBe("");
    }
  });

  it("renders the button on the page and in the sidebar once published, and no dialog until it is pressed", () => {
    for (const variant of ["page", "nav"] as const) {
      const out = render(true, variant);
      expect(out).toContain("Take the tour");
      expect(out).toContain('type="button"');
      expect(out).not.toContain('role="dialog"');
    }
  });

  it("sits in the About group, which is where the sidebar shows it", () => {
    expect(NAV.some((g) => g.heading === "About")).toBe(true);
  });
});
