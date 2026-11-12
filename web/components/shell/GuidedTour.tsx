"use client";

// GuidedTour (#91; Design 01 §4.8, as changed on 11 Nov 2026): an opt-in tour of five stops over the live pages. It never
// starts by itself: "Take the tour" (on Start here and in the sidebar's About group) starts it, and both buttons render
// only when `site.guided_tour` is published (lib/tour.ts; no row, no button, no tour). Each stop is a step panel
// anchored to a fixed target (`data-tour="<id>"`) with "2 of 5", Back, Next and Skip tour; the target gets the 2 px
// white ring and the rest of the page is dimmed (layer 50, with the dialog and the sheet). Below md the panel is a
// bottom panel and the target is scrolled high enough to stay above it. Focus moves into the step, Tab cycles its
// buttons, Esc ends the tour and focus goes back to the button (or to the page's main when that button is gone).
// Reduced motion: the page jumps to a target, and nothing pulses. A stop on another page opens that page and waits
// for its target; a target that never appears ends the tour with one line, never a blank overlay. No tour library:
// the placement, the keyboard and the state are lib/tour.ts's pure parts.
// A full page load (Next falls back to one when a page's render fails) remounts this provider, so the tour keeps its
// stop in sessionStorage (#466; lib/tour.ts "the stored stop") and resumes it once on mount, when it is fresh and the
// page is that stop's own. The key is cleared on every end, and storage that throws changes nothing.
import { Compass } from "lucide-react";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { Button } from "@/components/ui/button";
import {
  atRoute,
  clampBox,
  clearTourStop,
  cycleFocus,
  dimRects,
  keyAction,
  loadTourStop,
  MOBILE_MAX,
  PANEL_WIDTH,
  persistTour,
  placePanel,
  scrollBehavior,
  scrollTarget,
  targetSelector,
  TOUR_IDLE,
  TOUR_STOPS,
  tourReducer,
  tourStorage,
  type Box,
  type TourAction,
  type TourState,
} from "@/lib/tour";

/** How long a stop waits for its page and target before the tour ends with a note (a company page can take a few seconds). */
const WAIT_MS = 12_000;

type TourContextValue = { shown: boolean; start: (trigger: HTMLElement | null) => void };

const TourContext = createContext<TourContextValue>({ shown: false, start: () => {} });

/** What `restoreFocus` needs of the page (a stand-in in tests). */
type FocusPage = {
  readonly activeElement: unknown;
  getElementById(id: string): { focus(): void } | null;
};

/**
 * Puts focus back when the tour ends: on the button that started it, or on the page's main when that button is gone or
 * can't take focus. It checks where focus landed, because `focus()` does nothing on a button that is connected but
 * hidden or inert (one in a drawer that has closed), and a keyboard user must never be left on the body. With no
 * starting button at all (a resumed tour), focus goes to the main.
 */
export function restoreFocus(from: { readonly isConnected: boolean; focus(): void } | null, page: FocusPage = document): void {
  if (from?.isConnected) from.focus();
  if (!from || page.activeElement !== from) page.getElementById("main")?.focus();
}

export function TourProvider({ shown, children }: { shown: boolean; children: ReactNode }) {
  const [state, dispatch] = useReducer((s: TourState, a: TourAction) => tourReducer(s, a), TOUR_IDLE);
  const trigger = useRef<HTMLElement | null>(null);
  const resumed = useRef(false);

  // After a full page load the state starts at idle: resume the stored stop, once. This waits for the mount (not the
  // first render), so the markup the server sent and the client's first render still agree.
  useEffect(() => {
    if (resumed.current) return;
    resumed.current = true;
    const stop = loadTourStop(tourStorage(), Date.now(), window.location.pathname, window.location.search, shown);
    if (stop.active) dispatch({ type: "resume", index: stop.index });
  }, [shown]);

  // A move saves the stop it leads to. The reducer is pure (React may run it twice), so the save sits here, beside it.
  const move = useCallback(
    (action: TourAction) => {
      persistTour(tourStorage(), tourReducer(state, action), Date.now());
      dispatch(action);
    },
    [state],
  );

  const start = useCallback(
    (from: HTMLElement | null) => {
      if (!shown) return;
      trigger.current = from;
      move({ type: "start" });
    },
    [shown, move],
  );

  const end = useCallback(() => {
    clearTourStop(tourStorage()); // first, so that whatever happens next, a reload never resumes a tour that was ended
    // The panel holds focus and leaves the page now (not on the next frame: a page that is hidden runs no frame, and the
    // body kept focus after Done on 12 Nov 2026), and then focus goes back at once.
    flushSync(() => dispatch({ type: "end" }));
    restoreFocus(trigger.current);
    trigger.current = null;
  }, []);

  const next = useCallback(() => (state.active && state.index + 1 >= TOUR_STOPS.length ? end() : move({ type: "next" })), [state, end, move]);
  const back = useCallback(() => move({ type: "back" }), [move]);
  const missing = useCallback(() => dispatch({ type: "missing" }), []);

  return (
    <TourContext.Provider value={{ shown, start }}>
      {children}
      {shown && state.active && <TourStep key={state.index} index={state.index} missing={state.missing} onNext={next} onBack={back} onEnd={end} onMissing={missing} />}
    </TourContext.Provider>
  );
}

/** "Take the tour": renders nothing unless the tour is published. `nav` is the sidebar's row, `page` the Start here button. */
export function TakeTourButton({ variant, onNavigate }: { variant: "page" | "nav"; onNavigate?: () => void }) {
  const { shown, start } = useContext(TourContext);
  if (!shown) return null;
  const go = (el: HTMLElement) => {
    start(el);
    onNavigate?.();
  };
  if (variant === "nav") {
    return (
      <button
        type="button"
        onClick={(e) => go(e.currentTarget)}
        className="flex h-8 w-full items-center gap-2.5 rounded-md border-l-2 border-transparent px-2.5 text-left text-ui text-ink-2 hover:bg-raised hover:text-ink-1"
      >
        <Compass aria-hidden className="size-4 shrink-0" />
        <span>Take the tour</span>
      </button>
    );
  }
  return (
    <Button type="button" size="lg" variant="outline" onClick={(e) => go(e.currentTarget)}>
      <Compass aria-hidden />
      Take the tour
    </Button>
  );
}

const viewport = () => ({ width: window.innerWidth, height: window.innerHeight });
const boxOf = (el: Element): Box => {
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
};

function TourStep({
  index,
  missing,
  onNext,
  onBack,
  onEnd,
  onMissing,
}: {
  index: number;
  missing: boolean;
  onNext: () => void;
  onBack: () => void;
  onEnd: () => void;
  onMissing: () => void;
}) {
  const stop = TOUR_STOPS[index];
  const router = useRouter();
  const panel = useRef<HTMLDivElement>(null);
  const target = useRef<Element | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const [view, setView] = useState(viewport);
  const [size, setSize] = useState({ width: PANEL_WIDTH, height: 180 });
  const last = index === TOUR_STOPS.length - 1;
  const mobile = view.width <= MOBILE_MAX;

  // Open the stop's page if needed, then wait for its target, scroll to it and measure it.
  useEffect(() => {
    if (missing) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    if (!atRoute(stop.route, window.location.pathname, window.location.search)) router.push(stop.route);
    const find = () => {
      if (cancelled) return;
      // A target counts only on the stop's own page: the previous page's elements may still be there while it loads.
      const el = atRoute(stop.route, window.location.pathname, window.location.search) ? document.querySelector(targetSelector(stop)) : null;
      if (el) {
        target.current = el;
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        window.scrollTo({ top: scrollTarget(el.getBoundingClientRect().top, window.scrollY, window.innerWidth <= MOBILE_MAX), behavior: scrollBehavior(reduce) });
        setView(viewport());
        setBox(boxOf(el));
        return;
      }
      if (Date.now() - started > WAIT_MS) onMissing();
      else timer = setTimeout(find, 100);
    };
    find();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      target.current = null;
    };
  }, [stop, missing, router, onMissing]);

  // Keep the ring on the target while the page scrolls, resizes or re-renders.
  useEffect(() => {
    if (missing) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      let el = target.current;
      if (el && !el.isConnected) el = target.current = document.querySelector(targetSelector(stop));
      if (!el) return;
      setView(viewport());
      setBox(boxOf(el));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    window.addEventListener("scroll", schedule, { passive: true, capture: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule, { capture: true });
      window.removeEventListener("resize", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [stop, missing]);

  // Focus moves into the step; Tab cycles its buttons; Esc ends the tour.
  useEffect(() => {
    panel.current?.focus({ preventScroll: true });
  }, [index, missing]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action = keyAction(e.key, e.shiftKey);
      if (!action) return;
      e.preventDefault();
      if (action === "end") return onEnd();
      const buttons = [...(panel.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
      buttons[cycleFocus(buttons.length, buttons.indexOf(document.activeElement as HTMLButtonElement), action === "focus-previous")]?.focus();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onEnd]);

  useLayoutEffect(() => {
    const r = panel.current?.getBoundingClientRect();
    if (r && (Math.round(r.width) !== Math.round(size.width) || Math.round(r.height) !== Math.round(size.height))) setSize({ width: r.width, height: r.height });
  }, [index, missing, view, size]);

  const ring = box && !missing ? clampBox(box, view) : null;
  const where = mobile ? undefined : placePanel(box ?? { top: view.height, left: 0, width: 0, height: 0 }, view, size);
  const px = (b: Box) => ({ top: b.top, left: b.left, width: b.width, height: b.height });

  return (
    <>
      {ring ? (
        <>
          {dimRects(ring, view).map((r, i) => (
            <div key={i} aria-hidden className="fixed z-50 bg-canvas/70" style={px(r)} />
          ))}
          <div aria-hidden className="pointer-events-none fixed z-50 rounded-sm ring-2 ring-highlight" style={px(ring)} />
        </>
      ) : (
        !missing && <div aria-hidden className="fixed inset-0 z-50 bg-canvas/70" />
      )}
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        aria-describedby="tour-text"
        tabIndex={-1}
        className="fixed inset-x-0 bottom-0 z-50 border border-line-strong bg-raised p-3 text-ink-1 outline-none max-md:rounded-t-md md:inset-x-auto md:w-90 md:rounded-md"
        style={where ? { top: where.top, left: where.left, bottom: "auto" } : undefined}
      >
        {missing ? (
          <>
            <h2 id="tour-title" className="text-data font-semibold">
              The tour has ended
            </h2>
            <p id="tour-text" className="mt-1 text-ui text-ink-2">
              This part of the page isn&apos;t available right now.
            </p>
            <div className="mt-3 flex justify-end">
              <Button type="button" onClick={onEnd}>
                Close
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-meta text-ink-3">
              {index + 1} of {TOUR_STOPS.length}
            </p>
            <h2 id="tour-title" className="mt-0.5 text-data font-semibold">
              {stop.title}
            </h2>
            <p id="tour-text" className="mt-1 text-ui text-ink-2">
              {stop.text}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button type="button" variant="outline" onClick={onBack} disabled={index === 0}>
                Back
              </Button>
              <Button type="button" onClick={onNext}>
                {last ? "Done" : "Next"}
              </Button>
              <Button type="button" variant="ghost" onClick={onEnd} className="ml-auto">
                Skip tour
              </Button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
