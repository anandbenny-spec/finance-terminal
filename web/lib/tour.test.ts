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
  clearTourStop,
  cycleFocus,
  dimRects,
  encodeTourStop,
  keyAction,
  loadTourStop,
  MOBILE_MAX,
  persistTour,
  placePanel,
  resumeTour,
  saveTourStop,
  scrollBehavior,
  scrollTarget,
  targetSelector,
  TOUR_FIELD,
  TOUR_IDLE,
  TOUR_RESUME_MS,
  TOUR_STOPS,
  TOUR_STORAGE_KEY,
  tourReducer,
  tourShown,
  tourStorage,
  type TourAction,
  type TourState,
  type TourStore,
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

// ---- the stored stop (#466): the tour survives a full page load ----

const NOW = 1_800_000_000_000;
const stored = (index: number, at: number = NOW) => encodeTourStop(index, at);
/** A stop's route as the browser reports it: [pathname, search]. */
const locationOf = (index: number): [string, string] => {
  const url = new URL(TOUR_STOPS[index].route, "http://x");
  return [url.pathname, url.search];
};
const resumeAt = (raw: string | null, pathname: string, search: string, now: number = NOW + 1000, shown = true) => resumeTour(raw, now, pathname, search, shown);

/** A stand-in for sessionStorage. */
function fakeStore(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  const store: TourStore = {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
  };
  return { store, data };
}
/** Storage that refuses every call, as blocked storage does. */
const blocked: TourStore = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
  removeItem: () => {
    throw new Error("blocked");
  },
};
const keyOf = (data: Map<string, string>) => (data.has(TOUR_STORAGE_KEY) ? JSON.parse(data.get(TOUR_STORAGE_KEY)!) : null);

describe("resumeTour", () => {
  it("keeps one key in sessionStorage and stores the version, the stop and the time", () => {
    expect(TOUR_STORAGE_KEY).toBe("ft.tour");
    expect(TOUR_RESUME_MS).toBe(10 * 60 * 1000);
    expect(JSON.parse(encodeTourStop(2, NOW))).toEqual({ v: 1, index: 2, at: NOW });
  });

  it("resumes a fresh stop at its own route, for every stop", () => {
    for (let i = 0; i < TOUR_STOPS.length; i++) {
      const [path, search] = locationOf(i);
      expect(resumeAt(stored(i), path, search), TOUR_STOPS[i].id).toEqual({ active: true, index: i, missing: false });
    }
  });

  it("resumes up to ten minutes after the stop was reached, and not a moment later", () => {
    const [path, search] = locationOf(3);
    expect(resumeAt(stored(3), path, search, NOW + TOUR_RESUME_MS)).toEqual({ active: true, index: 3, missing: false });
    expect(resumeAt(stored(3), path, search, NOW + TOUR_RESUME_MS + 1)).toEqual(TOUR_IDLE);
  });

  it("gives idle off the stop's own route (another page, or the right page without the stop's query)", () => {
    expect(resumeAt(stored(3), "/", "")).toEqual(TOUR_IDLE); // the screener stop, on the front page
    expect(resumeAt(stored(4), "/companies/adanient", "")).toEqual(TOUR_IDLE); // the calls stop, on the Overview
    expect(resumeAt(stored(4), "/companies/tcs/calls", "")).toEqual(TOUR_IDLE); // the calls stop, on another company
    expect(resumeAt(stored(0), "/screener", "")).toEqual(TOUR_IDLE);
  });

  it("gives idle for an index outside the stops, or not a whole number", () => {
    const [path, search] = locationOf(0);
    for (const index of [-1, TOUR_STOPS.length, 99, 1.5, "2", null, NaN]) {
      expect(resumeAt(JSON.stringify({ v: 1, index, at: NOW }), path, search), String(index)).toEqual(TOUR_IDLE);
    }
  });

  it("gives idle for a value that doesn't parse, isn't an object, or isn't version 1", () => {
    const [path, search] = locationOf(0);
    for (const raw of ["", "{", "not json", "5", "null", "[]", '"text"', JSON.stringify({ index: 0, at: NOW }), JSON.stringify({ v: 2, index: 0, at: NOW }), JSON.stringify({ v: "1", index: 0, at: NOW })]) {
      expect(resumeAt(raw, path, search), raw).toEqual(TOUR_IDLE);
    }
  });

  it("gives idle for a time that is missing, not a number, in the future, or more than ten minutes old", () => {
    const [path, search] = locationOf(0);
    for (const at of [undefined, null, "now", NaN, NOW + 5000, NOW - TOUR_RESUME_MS - 1000]) {
      expect(resumeAt(JSON.stringify({ v: 1, index: 0, at }), path, search), String(at)).toEqual(TOUR_IDLE);
    }
  });

  it("gives idle while the tour is hidden (no site.guided_tour row: no resume either), and when nothing is stored", () => {
    const [path, search] = locationOf(0);
    expect(resumeAt(stored(0), path, search, NOW + 1000, false)).toEqual(TOUR_IDLE);
    expect(resumeAt(null, path, search)).toEqual(TOUR_IDLE);
  });
});

describe("the stored stop is read once, and kept or cleared", () => {
  it("resumes a fresh stop and leaves the key in place", () => {
    const { store, data } = fakeStore({ [TOUR_STORAGE_KEY]: stored(2) });
    const [path, search] = locationOf(2);
    expect(loadTourStop(store, NOW + 5000, path, search, true)).toEqual({ active: true, index: 2, missing: false });
    expect(keyOf(data)).toEqual({ v: 1, index: 2, at: NOW });
  });

  it("gives idle and clears the key for each thing that stops a resume", () => {
    const [p3, s3] = locationOf(3);
    const cases: [string, string, string, string, number, boolean][] = [
      ["off its route", stored(3), "/", "", NOW + 1000, true],
      ["older than ten minutes", stored(3), p3, s3, NOW + TOUR_RESUME_MS + 1, true],
      ["an index out of range", JSON.stringify({ v: 1, index: 7, at: NOW }), p3, s3, NOW + 1000, true],
      ["unparsable", "{oops", p3, s3, NOW + 1000, true],
      ["a version other than 1", JSON.stringify({ v: 2, index: 3, at: NOW }), p3, s3, NOW + 1000, true],
      ["hidden (shown is false)", stored(3), p3, s3, NOW + 1000, false],
    ];
    for (const [label, raw, path, search, now, shown] of cases) {
      const { store, data } = fakeStore({ [TOUR_STORAGE_KEY]: raw });
      expect(loadTourStop(store, now, path, search, shown), label).toEqual(TOUR_IDLE);
      expect(data.has(TOUR_STORAGE_KEY), label).toBe(false);
    }
  });

  it("writes nothing when nothing is stored", () => {
    const { store, data } = fakeStore();
    expect(loadTourStop(store, NOW, "/", "", true)).toEqual(TOUR_IDLE);
    expect(data.size).toBe(0);
  });

  it("starts, moves and ends as it does today when storage throws or is absent", () => {
    const [path, search] = locationOf(0);
    expect(loadTourStop(blocked, NOW, path, search, true)).toEqual(TOUR_IDLE);
    expect(loadTourStop(null, NOW, path, search, true)).toEqual(TOUR_IDLE);
    for (const store of [blocked, null]) {
      expect(() => saveTourStop(store, 1, NOW)).not.toThrow();
      expect(() => clearTourStop(store)).not.toThrow();
      let s: TourState = TOUR_IDLE;
      for (const action of [{ type: "start" }, { type: "next" }, { type: "back" }, { type: "next" }, { type: "end" }] as TourAction[]) {
        expect(() => persistTour(store, tourReducer(s, action), NOW)).not.toThrow();
        s = tourReducer(s, action);
      }
      expect(s).toEqual(TOUR_IDLE);
    }
  });

  it("finds sessionStorage or nothing, and never throws", () => {
    const found = tourStorage();
    expect(found === null || typeof found.getItem === "function").toBe(true);
  });
});

describe("the provider keeps the stored stop in step with the tour", () => {
  /** What TourProvider does: a move saves the stop it leads to (the reducer stays pure); "missing" is never stored. */
  function run(store: TourStore, actions: TourAction[], from: TourState = TOUR_IDLE): TourState {
    let s = from;
    for (const action of actions) {
      if (action.type !== "missing") persistTour(store, tourReducer(s, action), NOW);
      s = tourReducer(s, action);
    }
    return s;
  }
  const next: TourAction = { type: "next" };

  it("writes the stop on start, Next and Back", () => {
    const { store, data } = fakeStore();
    run(store, [{ type: "start" }]);
    expect(keyOf(data)).toEqual({ v: 1, index: 0, at: NOW });
    run(store, [next, next], { active: true, index: 0, missing: false });
    expect(keyOf(data)?.index).toBe(2);
    run(store, [{ type: "back" }], { active: true, index: 2, missing: false });
    expect(keyOf(data)?.index).toBe(1);
  });

  it("start, Next, Next leaves stop 3 stored", () => {
    const { store, data } = fakeStore();
    const end = run(store, [{ type: "start" }, next, next]);
    expect(end).toEqual({ active: true, index: 2, missing: false });
    expect(keyOf(data)?.index).toBe(2);
  });

  it("clears the key on every end: Esc, Skip tour, Done and Close", () => {
    const last = TOUR_STOPS.length - 1;
    const ends: [string, TourState, TourAction][] = [
      ["Esc", { active: true, index: 2, missing: false }, { type: "end" }],
      ["Skip tour", { active: true, index: 0, missing: false }, { type: "end" }],
      ["Done", { active: true, index: last, missing: false }, next],
      ["Close (the note)", { active: true, index: 3, missing: true }, { type: "end" }],
    ];
    for (const [label, from, action] of ends) {
      const { store, data } = fakeStore({ [TOUR_STORAGE_KEY]: stored(from.active ? from.index : 0) });
      expect(run(store, [action], from), label).toEqual(TOUR_IDLE);
      expect(data.has(TOUR_STORAGE_KEY), label).toBe(false);
    }
  });

  it("keeps the missing note out of the key: a reload during the note resumes the stop, waiting again", () => {
    const { store, data } = fakeStore();
    const s = run(store, [{ type: "start" }, next, next, next, { type: "missing" }]);
    expect(s).toEqual({ active: true, index: 3, missing: true });
    expect(keyOf(data)).toEqual({ v: 1, index: 3, at: NOW }); // no "missing" in it
    const [path, search] = locationOf(3);
    expect(loadTourStop(store, NOW + 1000, path, search, true)).toEqual({ active: true, index: 3, missing: false });
  });

  it("shows stop 3 of 5 after a remount with the key present at stop 3's route", () => {
    const { store } = fakeStore({ [TOUR_STORAGE_KEY]: stored(2) });
    const [path, search] = locationOf(2);
    const resumed = loadTourStop(store, NOW + 60_000, path, search, true);
    expect(resumed.active && `${resumed.index + 1} of ${TOUR_STOPS.length}`).toBe("3 of 5");
    expect(tourReducer(TOUR_IDLE, { type: "resume", index: 2 })).toEqual(resumed);
  });

  it("resumes only into an idle tour, and only to a stop that exists", () => {
    expect(tourReducer(TOUR_IDLE, { type: "resume", index: 4 })).toEqual({ active: true, index: 4, missing: false });
    for (const index of [-1, TOUR_STOPS.length, 0.5]) expect(tourReducer(TOUR_IDLE, { type: "resume", index })).toEqual(TOUR_IDLE);
    const running: TourState = { active: true, index: 1, missing: false };
    expect(tourReducer(running, { type: "resume", index: 3 })).toEqual(running); // a running tour is never moved by a resume
  });

  it("is wired that way in the provider: save in the move, clear first on end, resume once on mount, never the long-lived store", () => {
    const src = readFileSync(join(WEB, "components/shell/GuidedTour.tsx"), "utf8");
    const lib = readFileSync(join(WEB, "lib/tour.ts"), "utf8");
    const move = src.slice(src.indexOf("const move = useCallback"), src.indexOf("const start = useCallback"));
    expect(move.indexOf("persistTour(")).toBeGreaterThan(-1);
    expect(move.indexOf("persistTour(")).toBeLessThan(move.indexOf("dispatch(action)"));
    const end = src.slice(src.indexOf("const end = useCallback"), src.indexOf("const next = useCallback"));
    expect(end.indexOf("clearTourStop(")).toBeGreaterThan(-1);
    expect(end.indexOf("clearTourStop(")).toBeLessThan(end.indexOf('dispatch({ type: "end" })'));
    expect(src).toContain("const missing = useCallback(() => dispatch({ type: \"missing\" }), [])"); // the note is never stored
    expect(src).toContain("loadTourStop(tourStorage(), Date.now(), window.location.pathname, window.location.search, shown)");
    expect(src).toContain('dispatch({ type: "resume", index: stop.index })');
    expect(src).toContain("resumed.current"); // read once
    for (const text of [src, lib]) {
      expect(text).not.toContain("localStorage"); // the tour belongs to one tab and one visit
      expect(text).not.toContain("document.cookie");
    }
  });
});
