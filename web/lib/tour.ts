// The guided tour's rules (#91; Design 01 §4.8, as changed on 11 Nov 2026): five stops over the pages that are live,
// each with a fixed target (`data-tour="<id>"`), and the pure parts of the tour: whether it is published, which stop
// comes next, where the step panel goes, how focus cycles, and how a scroll behaves under reduced motion. The
// component (components/shell/GuidedTour.tsx) only wires these to the page.
//
// HIDDEN until Anand flips it (tracker #30, 8 Nov 2026): no `site.guided_tour` row in core.field_policy means no "Take
// the tour" button anywhere, and so no tour (the database fails closed, and so does the site).
import type { FieldPolicyRow } from "@/lib/queries/api";

/** The field policy that publishes the tour. No row means hidden. */
export const TOUR_FIELD = "site.guided_tour";

/** Whether the tour is published: a row for the field with status `shown` or `labelled`. */
export function tourShown(policies: readonly FieldPolicyRow[] | null): boolean {
  const row = policies?.find((p) => p.field_key === TOUR_FIELD) ?? null;
  return row !== null && (row.status === "shown" || row.status === "labelled");
}

export type TourStop = {
  /** The stop's id, and its `data-tour` value on the page. */
  id: string;
  /** The page it sits on (path and query), which the tour opens when it isn't already there. */
  route: string;
  title: string;
  /** One or two plain sentences: what the reader sees. No figure (figures go stale: CLAUDE.md §1) and no advice. */
  text: string;
};

/**
 * The five stops. A company's Earnings calls tab uses the first company of the Nifty 50 list (the page's own static
 * example, app/companies/[slug]/page.tsx); its calls panel is drawn whether or not a call is published, so the stop
 * never lands on a held-back panel.
 */
export const TOUR_STOPS: readonly TourStop[] = [
  {
    id: "front-what-changed",
    route: "/",
    title: "What changed in the latest statements",
    text: "Each bank's latest decision, set against the one before it: the rate decision, how the vote went, and what the wording changed.",
  },
  {
    id: "front-votes",
    route: "/",
    title: "How the members voted",
    text: "One dot per member, placed at the change that member voted for, so a split committee shows as a spread. Where a vote isn't published yet, the panel says so.",
  },
  {
    id: "front-change-strip",
    route: "/",
    title: "How much each statement changed",
    text: "One bar per meeting: the share of the statement's words added above the line and removed below it, against the statement before. Move along a row with the arrow keys.",
  },
  {
    id: "screener-table",
    route: "/screener",
    title: "The Nifty 50 screener",
    text: "Nifty 50 companies side by side with their valuation measures, each in its own reporting currency. Informational only, not investment advice.",
  },
  {
    id: "company-calls",
    route: "/companies/adanient?tab=calls",
    title: "A company's earnings calls",
    text: "The earnings calls published for a company, newest first, with the status of each one. AI-written lines about a call are marked as AI and cite the passage they came from.",
  },
];

/** The CSS selector of a stop's target. */
export const targetSelector = (stop: TourStop): string => `[data-tour="${stop.id}"]`;

// ---- the tour's state ----

export type TourState = { active: false } | { active: true; index: number; missing: boolean };

export type TourAction = { type: "start" } | { type: "next" } | { type: "back" } | { type: "end" } | { type: "missing" };

export const TOUR_IDLE: TourState = { active: false };

/** Opt-in: only `start` begins a tour. Next on the last stop ends it; Back on the first stays put. */
export function tourReducer(state: TourState, action: TourAction, count: number = TOUR_STOPS.length): TourState {
  if (action.type === "start") return { active: true, index: 0, missing: false };
  if (!state.active) return state;
  switch (action.type) {
    case "next":
      return state.index + 1 >= count ? TOUR_IDLE : { active: true, index: state.index + 1, missing: false };
    case "back":
      return { active: true, index: Math.max(0, state.index - 1), missing: false };
    case "missing":
      return { ...state, missing: true };
    case "end":
      return TOUR_IDLE;
  }
}

// ---- the page ----

/** Whether the page the visitor is on already shows a stop's route (path equal; every query pair of the route present). */
export function atRoute(route: string, pathname: string, search: string): boolean {
  const want = new URL(route, "http://x");
  if (want.pathname !== pathname) return false;
  const have = new URLSearchParams(search);
  for (const [key, value] of want.searchParams) if (have.get(key) !== value) return false;
  return true;
}

export type Box = { top: number; left: number; width: number; height: number };

/** Below `md` (768 px) the step is a bottom panel (Design 01 §4.8). */
export const MOBILE_MAX = 767;
export const PANEL_WIDTH = 360;
const GAP = 12;
const EDGE = 8;

/** Where the step panel goes on a wide screen: under the target, else above it, else inside the viewport's corner. */
export function placePanel(target: Box, viewport: { width: number; height: number }, panel: { width: number; height: number }): { top: number; left: number } {
  const left = Math.min(Math.max(EDGE, target.left), Math.max(EDGE, viewport.width - panel.width - EDGE));
  const below = target.top + target.height + GAP;
  if (below + panel.height + EDGE <= viewport.height) return { top: below, left };
  const above = target.top - GAP - panel.height;
  if (above >= EDGE) return { top: above, left };
  return { top: Math.max(EDGE, viewport.height - panel.height - EDGE), left: Math.max(EDGE, viewport.width - panel.width - EDGE) };
}

/** The target's box cut to the viewport, so the outline and the dimming never run off the screen. */
export function clampBox(box: Box, viewport: { width: number; height: number }): Box {
  const top = Math.max(0, box.top);
  const left = Math.max(0, box.left);
  const bottom = Math.min(viewport.height, box.top + box.height);
  const right = Math.min(viewport.width, box.left + box.width);
  return { top, left, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}

/** The four dimming rectangles around a box: the page is dimmed except for the target. */
export function dimRects(box: Box, viewport: { width: number; height: number }): Box[] {
  const bottom = box.top + box.height;
  const right = box.left + box.width;
  return [
    { top: 0, left: 0, width: viewport.width, height: box.top },
    { top: bottom, left: 0, width: viewport.width, height: Math.max(0, viewport.height - bottom) },
    { top: box.top, left: 0, width: box.left, height: box.height },
    { top: box.top, left: right, width: Math.max(0, viewport.width - right), height: box.height },
  ];
}

/** Reduced motion: the page jumps to the target instead of scrolling to it (Design 01 §4.8). */
export const scrollBehavior = (reducedMotion: boolean): "auto" | "smooth" => (reducedMotion ? "auto" : "smooth");

/** The scroll position that puts a target just under the top bar and tape (a wide screen) or high enough for the bottom panel (a phone). */
export function scrollTarget(targetTop: number, scrollY: number, mobile: boolean): number {
  return Math.max(0, targetTop + scrollY - (mobile ? 64 : 112));
}

// ---- the keyboard ----

export type KeyAction = "end" | "focus-next" | "focus-previous" | null;

/** Esc ends the tour; Tab cycles the step's buttons (never leaves the panel). */
export function keyAction(key: string, shift: boolean): KeyAction {
  if (key === "Escape") return "end";
  if (key === "Tab") return shift ? "focus-previous" : "focus-next";
  return null;
}

/**
 * The button to focus after Tab: `current` is the focused button's index among `count` buttons, or -1 when focus is on
 * the panel itself (or outside it). Forward from the panel goes to the first button, backward to the last.
 */
export function cycleFocus(count: number, current: number, shift: boolean): number {
  if (count <= 0) return -1;
  if (current < 0) return shift ? count - 1 : 0;
  return (current + (shift ? -1 : 1) + count) % count;
}
