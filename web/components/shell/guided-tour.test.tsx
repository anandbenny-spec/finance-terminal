// The "Take the tour" buttons (#91): nothing renders while the tour is hidden (no site.guided_tour row), and the tour
// never starts by itself when it is published.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { restoreFocus, TakeTourButton, TourProvider } from "@/components/shell/GuidedTour";
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

// ---- focus after the tour ends (#466) ----
//
// On production on 12 Nov 2026, Done at 375 px left focus on the page body. The starting button was in the drawer, which
// had closed, so the old code's `main.focus()` ran in a requestAnimationFrame callback, and a page that is hidden runs no
// frame: nothing moved focus after the panel (which held it) left the page. It was reproduced in Chrome by switching the
// frame callback off. restoreFocus now runs at once, and checks where focus landed. These tests give it a stand-in page.

type Focusable = { name: string; isConnected: boolean; focus(): void };

function fakePage() {
  const page: { activeElement: unknown; getElementById(id: string): Focusable | null } = {
    activeElement: "body",
    getElementById: (id) => (id === "main" ? main : null),
  };
  /** An element that takes focus when it can; a hidden or inert one (`canFocus` false) ignores focus(), as a real one does. */
  const element = (name: string, isConnected = true, canFocus = true): Focusable => ({
    name,
    isConnected,
    focus() {
      if (canFocus) page.activeElement = this;
    },
  });
  const main = element("main");
  return { page, main, element };
}

describe("focus after the tour ends", () => {
  it("goes back to the starting button when it is connected and takes focus (the sidebar's button on a wide screen)", () => {
    const { page, element } = fakePage();
    const button = element("sidebar button");
    restoreFocus(button, page);
    expect(page.activeElement).toBe(button);
  });

  it("falls to the page's main when the starting button is gone (the drawer's button, after the drawer closed)", () => {
    const { page, main, element } = fakePage();
    restoreFocus(element("drawer button", false), page);
    expect(page.activeElement).toBe(main);
  });

  it("falls to the page's main when the button is connected but can't take focus (hidden or inert)", () => {
    const { page, main, element } = fakePage();
    restoreFocus(element("button in a closed drawer", true, false), page);
    expect(page.activeElement).toBe(main);
  });

  it("goes to the page's main when there is no starting button at all (a tour resumed after a full page load)", () => {
    const { page, main } = fakePage();
    restoreFocus(null, page);
    expect(page.activeElement).toBe(main);
  });

  it("never leaves focus on the body whenever the page has a main (the 375 px Done, in each of these cases)", () => {
    const cases = (e: ReturnType<typeof fakePage>["element"]) => [e("a", true, true), e("b", false, true), e("c", true, false), null];
    for (let i = 0; i < 4; i++) {
      const { page, element } = fakePage();
      restoreFocus(cases(element)[i], page);
      expect(page.activeElement, `case ${i}`).not.toBe("body");
    }
  });

  it("does nothing worse than before on a page without a main", () => {
    const page = { activeElement: "body" as unknown, getElementById: () => null };
    expect(() => restoreFocus(null, page)).not.toThrow();
    expect(page.activeElement).toBe("body");
  });

  it("is run at once when the tour ends, after the panel has left the page, and not from a frame callback", () => {
    const src = readFileSync(join(new URL("../../", import.meta.url).pathname, "components/shell/GuidedTour.tsx"), "utf8");
    const end = src.slice(src.indexOf("const end = useCallback"), src.indexOf("const next = useCallback"));
    expect(end).not.toContain("requestAnimationFrame"); // a hidden page runs none
    expect(end.indexOf('flushSync(() => dispatch({ type: "end" }))')).toBeGreaterThan(-1);
    expect(end.indexOf("flushSync(")).toBeLessThan(end.indexOf("restoreFocus(trigger.current)"));
  });
});
