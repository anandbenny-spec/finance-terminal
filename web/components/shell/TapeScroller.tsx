"use client";

// The tape's sideways scroll below lg (Design 01 §1.7, §4.1); from lg the tape wraps instead and nothing overflows
// (1 Nov 2026, #227). It never moves by itself; when the items overflow, the strip
// scrolls under touch, trackpad or the keyboard (each item is focusable), and an edge fades where more items lie.
// The scroller is `relative` so the items' screen-reader text (sr-only, absolutely positioned) stays inside it
// instead of widening the page.
import { useEffect, useRef, useState, type ReactNode } from "react";

export function TapeScroller({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [fade, setFade] = useState({ start: false, end: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const start = el.scrollLeft > 1;
      const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
      setFade((f) => (f.start === start && f.end === end ? f : { start, end }));
    };
    const observer = new ResizeObserver(update); // also runs once on observe
    observer.observe(el);
    el.addEventListener("scroll", update, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", update);
    };
  }, []);

  return (
    <div className="relative min-w-0 flex-1">
      <div ref={ref} className="relative overflow-x-auto overflow-y-hidden lg:overflow-x-visible lg:overflow-y-visible">
        {children}
      </div>
      {fade.start && (
        <div aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-6 bg-linear-to-r from-panel to-transparent" />
      )}
      {fade.end && (
        <div aria-hidden className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-linear-to-l from-panel to-transparent" />
      )}
    </div>
  );
}
