// The company page's tab bar brings its current tab into view on a phone (#483, polish pass 2): the arithmetic, the effect
// on a fake bar (it moves the bar's scrollLeft and never calls scrollIntoView, which could move the page), and that only
// a Tabs asked for it carries the client part.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Tabs } from "@/components/data/Tabs";
import { bringCurrentTabIntoView, scrollLeftFor } from "@/components/data/ScrollCurrentTab";

const bar = { left: 0, right: 320 };

describe("scrollLeftFor", () => {
  it("leaves the bar alone when the tab is already inside it", () => {
    expect(scrollLeftFor(bar, { left: 10, right: 90 }, 0)).toBe(0);
  });
  it("scrolls right so a tab past the right edge ends inside it (Earnings calls at 352 to 439 px on a 320 px bar)", () => {
    const next = scrollLeftFor(bar, { left: 352, right: 439 }, 0);
    expect(next).toBeGreaterThanOrEqual(439 - 320);
    expect(439 - next).toBeLessThanOrEqual(320);
  });
  it("scrolls back left for a tab before the left edge, never below 0", () => {
    expect(scrollLeftFor(bar, { left: -50, right: 30 }, 200)).toBeLessThan(200);
    expect(scrollLeftFor(bar, { left: -5, right: 60 }, 3)).toBe(0);
  });
});

describe("bringCurrentTabIntoView", () => {
  it("sets the bar's scrollLeft from the current tab's position, not the page's", () => {
    const tab = { getBoundingClientRect: () => ({ left: 352, right: 439 }) };
    const scrollIntoView = vi.fn();
    const nav = {
      scrollLeft: 0,
      getBoundingClientRect: () => bar,
      querySelector: (sel: string) => (sel === '[aria-current="page"]' ? { ...tab, scrollIntoView } : null),
    } as unknown as HTMLElement;
    bringCurrentTabIntoView(nav);
    expect(nav.scrollLeft).toBeGreaterThan(0);
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
  it("does nothing when no tab is current", () => {
    const nav = { scrollLeft: 7, getBoundingClientRect: () => bar, querySelector: () => null } as unknown as HTMLElement;
    bringCurrentTabIntoView(nav);
    expect(nav.scrollLeft).toBe(7);
  });
});

describe("Tabs scrollCurrent", () => {
  const items = [{ id: "a", label: "A", href: "/a" }, { id: "b", label: "B", href: "/b" }];
  it("carries the client part only when asked, and marks the current tab as before", () => {
    const plain = renderToStaticMarkup(<Tabs label="x" items={items} current="b" />);
    const asked = renderToStaticMarkup(<Tabs label="x" items={items} current="b" scrollCurrent />);
    expect(plain).not.toContain("<span hidden");
    expect(asked).toContain("<span hidden");
    expect(asked.replace("<span hidden=\"\"></span>", "")).toBe(plain);
    expect(asked).toContain('aria-current="page"');
  });
});
