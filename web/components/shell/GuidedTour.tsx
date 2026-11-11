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
import { Compass } from "lucide-react";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  atRoute,
  clampBox,
  cycleFocus,
  dimRects,
  keyAction,
  MOBILE_MAX,
  PANEL_WIDTH,
  placePanel,
  scrollBehavior,
  scrollTarget,
  targetSelector,
  TOUR_IDLE,
  TOUR_STOPS,
  tourReducer,
  type Box,
  type TourAction,
  type TourState,
} from "@/lib/tour";

/** How long a stop waits for its page and target before the tour ends with a note (a company page can take a few seconds). */
const WAIT_MS = 12_000;

type TourContextValue = { shown: boolean; start: (trigger: HTMLElement | null) => void };

const TourContext = createContext<TourContextValue>({ shown: false, start: () => {} });

export function TourProvider({ shown, children }: { shown: boolean; children: ReactNode }) {
  const [state, dispatch] = useReducer((s: TourState, a: TourAction) => tourReducer(s, a), TOUR_IDLE);
  const trigger = useRef<HTMLElement | null>(null);

  const start = useCallback(
    (from: HTMLElement | null) => {
      if (!shown) return;
      trigger.current = from;
      dispatch({ type: "start" });
    },
    [shown],
  );

  const end = useCallback(() => {
    dispatch({ type: "end" });
    // Focus goes back to the button that started the tour; when that button is gone (the page changed, or the drawer
    // closed), to the page's main, so a keyboard user is never left on nothing.
    requestAnimationFrame(() => {
      const from = trigger.current;
      if (from?.isConnected) from.focus();
      else document.getElementById("main")?.focus();
    });
  }, []);

  const next = useCallback(() => (state.active && state.index + 1 >= TOUR_STOPS.length ? end() : dispatch({ type: "next" })), [state, end]);
  const back = useCallback(() => dispatch({ type: "back" }), []);
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
