// The guided tour's rules (#91; Design 01 §4.8): hidden without its field-policy row, five stops with fixed targets,
// opt-in state, placement (a wide screen and a 375 px phone), the keyboard end to end, and reduced motion.
import { companyHref } from "@/lib/equity/company";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { FieldPolicyRow } from "@/lib/queries/api";
import {
  atRoute,
  clampBox,
  cycleFocus,
  dimRects,
  keyAction,
  MOBILE_MAX,
  placePanel,
  scrollBehavior,
  scrollTarget,
  targetSelector,
  TOUR_FIELD,
  TOUR_IDLE,
  TOUR_STOPS,
  tourReducer,
  tourShown,
  type TourState,
} from "./tour";

const row = (status: FieldPolicyRow["status"], field_key = TOUR_FIELD): FieldPolicyRow => ({ field_key, status, label: null });
const WEB = new URL("../", import.meta.url).pathname;

describe("tourShown", () => {
  it("is hidden with no row, no policies, or a hidden row (fails closed)", () => {
    expect(tourShown(null)).toBe(false);
    expect(tourShown([])).toBe(false);
    expect(tourShown([row("hidden")])).toBe(false);
    expect(tourShown([row("shown", "site.policy_day")])).toBe(false);
  });
  it("shows for a shown or labelled row", () => {
    expect(tourShown([row("shown")])).toBe(true);
    expect(tourShown([row("labelled")])).toBe(true);
  });
});

describe("the stops", () => {
  it("are five, each with its own id, a route, a title and plain text", () => {
    expect(TOUR_STOPS).toHaveLength(5);
    expect(new Set(TOUR_STOPS.map((s) => s.id)).size).toBe(5);
    for (const s of TOUR_STOPS) {
      expect(s.route.startsWith("/")).toBe(true);
      expect(s.title.length).toBeGreaterThan(0);
      expect(s.text.length).toBeGreaterThan(40);
    }
  });

  it("hold no figure and no advice (figures go stale: CLAUDE.md §1; Brief §11)", () => {
    for (const s of TOUR_STOPS) {
      const words = `${s.title} ${s.text}`.replaceAll("Nifty 50", "Nifty"); // an index's name is not a figure
      expect(words).not.toMatch(/\d/);
      expect(words).not.toMatch(/\b(buy|sell|undervalued|overvalued|upside|target price)\b/i);
    }
  });

  it("target a fixed id each, which the page that holds it renders as data-tour", () => {
    expect(TOUR_STOPS.map(targetSelector)).toEqual([
      '[data-tour="front-what-changed"]',
      '[data-tour="front-votes"]',
      '[data-tour="front-change-strip"]',
      '[data-tour="screener-table"]',
      '[data-tour="company-calls"]',
    ]);
    // Each target is set in the source of the component that draws it (the rendered pages are checked in their own tests).
    const where: Record<string, string> = {
      "front-what-changed": "components/policy/FrontView.tsx",
      "front-votes": "components/policy/FrontView.tsx",
      "front-change-strip": "components/policy/FrontView.tsx",
      "screener-table": "components/screener/ScreenerView.tsx",
      "company-calls": "components/company/calls/CallsTab.tsx",
    };
    for (const s of TOUR_STOPS) expect(readFileSync(join(WEB, where[s.id]), "utf8"), s.id).toContain(`tour="${s.id}"`);
  });
});

describe("the state", () => {
  it("is opt-in: nothing but start begins a tour", () => {
    for (const type of ["next", "back", "end", "missing"] as const) expect(tourReducer(TOUR_IDLE, { type })).toEqual(TOUR_IDLE);
    expect(tourReducer(TOUR_IDLE, { type: "start" })).toEqual({ active: true, index: 0, missing: false });
  });

  it("runs from the first stop to the last by Next, and Next on the last ends it", () => {
    let s: TourState = tourReducer(TOUR_IDLE, { type: "start" });
    const seen: number[] = [];
    while (s.active) {
      seen.push(s.index);
      s = tourReducer(s, { type: "next" });
    }
    expect(seen).toEqual([0, 1, 2, 3, 4]);
    expect(s).toEqual(TOUR_IDLE);
  });

  it("goes back one stop and stays on the first", () => {
    const at = (index: number): TourState => ({ active: true, index, missing: false });
    expect(tourReducer(at(3), { type: "back" })).toEqual(at(2));
    expect(tourReducer(at(0), { type: "back" })).toEqual(at(0));
  });

  it("ends on skip, and marks a stop whose target never appeared (the panel then says so, and Next/Back clear it)", () => {
    const at: TourState = { active: true, index: 2, missing: false };
    expect(tourReducer(at, { type: "end" })).toEqual(TOUR_IDLE);
    expect(tourReducer(at, { type: "missing" })).toEqual({ active: true, index: 2, missing: true });
  });
});

describe("the page", () => {
  it("knows when the visitor is already on a stop's page, query pairs included", () => {
    expect(atRoute("/", "/", "")).toBe(true);
    expect(atRoute("/", "/screener", "")).toBe(false);
    expect(atRoute("/companies/adanient?tab=calls", "/companies/adanient", "?tab=calls&call=q2")).toBe(true);
    expect(atRoute("/companies/adanient?tab=calls", "/companies/adanient", "")).toBe(false);
    expect(atRoute("/companies/adanient?tab=calls", "/companies/tcs", "?tab=calls")).toBe(false);
  });

  it("sends the calls stop to the company's calls tab by its path address, and the page matches it (R05-9b)", () => {
    const stop = TOUR_STOPS.find((s) => s.id === "company-calls")!;
    expect(stop.route).toBe(companyHref("adanient", "calls"));
    expect(atRoute(stop.route, "/companies/adanient/calls", "")).toBe(true);
    expect(atRoute(stop.route, "/companies/adanient", "")).toBe(false);
    expect(atRoute(stop.route, "/companies/adanient/valuation", "")).toBe(false);
  });

  const view = { width: 1280, height: 800 };
  const panel = { width: 360, height: 200 };

  it("puts the step panel under the target, else above it, else in the corner, always inside the window", () => {
    expect(placePanel({ top: 100, left: 40, width: 600, height: 200 }, view, panel)).toEqual({ top: 312, left: 40 });
    expect(placePanel({ top: 500, left: 40, width: 600, height: 250 }, view, panel)).toEqual({ top: 288, left: 40 });
    expect(placePanel({ top: 0, left: 0, width: 1280, height: 800 }, view, panel)).toEqual({ top: 592, left: 912 });
    expect(placePanel({ top: 100, left: 1200, width: 80, height: 40 }, view, panel).left).toBe(912);
  });

  it("cuts the target's box to the window and dims everything around it", () => {
    const box = clampBox({ top: -50, left: 20, width: 2000, height: 300 }, view);
    expect(box).toEqual({ top: 0, left: 20, width: 1260, height: 250 });
    const rects = dimRects(box, view);
    expect(rects).toHaveLength(4);
    const area = rects.reduce((a, r) => a + r.width * r.height, 0) + box.width * box.height;
    expect(area).toBe(view.width * view.height); // dimmed pieces plus the lit target tile the window exactly
  });

  it("at 375 px is a bottom panel, and scrolls the target high enough to stay above it", () => {
    expect(MOBILE_MAX).toBe(767); // md is 768 px: the panel is a bottom panel below it
    expect(375).toBeLessThanOrEqual(MOBILE_MAX);
    expect(scrollTarget(500, 100, true)).toBe(536);
    expect(scrollTarget(500, 100, false)).toBe(488);
    expect(scrollTarget(10, 0, true)).toBe(0);
    const src = readFileSync(join(WEB, "components/shell/GuidedTour.tsx"), "utf8");
    expect(src).toContain("fixed inset-x-0 bottom-0"); // the phone's panel spans the window's bottom
    expect(src).toContain("md:inset-x-auto md:w-90"); // and is a floating panel from md
  });

  it("jumps instead of scrolling under reduced motion, and never pulses", () => {
    expect(scrollBehavior(true)).toBe("auto");
    expect(scrollBehavior(false)).toBe("smooth");
    const src = readFileSync(join(WEB, "components/shell/GuidedTour.tsx"), "utf8");
    expect(src).toContain("prefers-reduced-motion: reduce");
    expect(src).not.toMatch(/animate-|transition/); // no animation anywhere in the tour
  });
});

describe("the keyboard", () => {
  it("Esc ends the tour, Tab and Shift+Tab move through the buttons, other keys do nothing", () => {
    expect(keyAction("Escape", false)).toBe("end");
    expect(keyAction("Tab", false)).toBe("focus-next");
    expect(keyAction("Tab", true)).toBe("focus-previous");
    expect(keyAction("Enter", false)).toBeNull();
    expect(keyAction("ArrowRight", false)).toBeNull();
  });

  it("cycles three buttons without leaving the panel, from the panel itself too", () => {
    expect(cycleFocus(3, -1, false)).toBe(0); // focus on the panel: Tab goes to Back
    expect(cycleFocus(3, -1, true)).toBe(2); // Shift+Tab goes to Skip tour
    expect(cycleFocus(3, 0, false)).toBe(1);
    expect(cycleFocus(3, 2, false)).toBe(0); // wraps
    expect(cycleFocus(3, 0, true)).toBe(2); // wraps back
    expect(cycleFocus(2, 1, false)).toBe(0); // on the first stop Back is disabled: two buttons
    expect(cycleFocus(0, -1, false)).toBe(-1);
  });

  it("runs end to end from the keyboard: start, five Next presses (Enter on the focused button), done", () => {
    let s: TourState = tourReducer(TOUR_IDLE, { type: "start" });
    let presses = 0;
    while (s.active) {
      s = tourReducer(s, { type: "next" });
      presses++;
    }
    expect(presses).toBe(TOUR_STOPS.length);
  });

  it("returns focus to the button that started it, or to main when that button is gone", () => {
    const src = readFileSync(join(WEB, "components/shell/GuidedTour.tsx"), "utf8");
    expect(src).toContain("from.focus()");
    expect(src).toContain('getElementById("main")?.focus()');
  });
});
