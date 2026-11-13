"use client";

// Polish pass 2, fix 1 (#483): on a phone the company page's tab bar scrolls sideways and the current tab can sit wholly
// past the right edge. After mount this scrolls the bar (and only the bar: never the page, which is why not
// `scrollIntoView`) so the tab marked aria-current="page" is in view. Renders nothing.
import { useEffect, useRef } from "react";

/** The scrollLeft that brings a tab fully into its scroller, or the current one when it already is (all in viewport px). */
export function scrollLeftFor(scroller: { left: number; right: number }, tab: { left: number; right: number }, scrollLeft: number, gap = 16): number {
  if (tab.right > scroller.right) return scrollLeft + (tab.right - scroller.right) + gap;
  if (tab.left < scroller.left) return Math.max(0, scrollLeft - (scroller.left - tab.left) - gap);
  return scrollLeft;
}

export function bringCurrentTabIntoView(nav: HTMLElement): void {
  const tab = nav.querySelector<HTMLElement>('[aria-current="page"]');
  if (!tab) return;
  const next = scrollLeftFor(nav.getBoundingClientRect(), tab.getBoundingClientRect(), nav.scrollLeft);
  if (next !== nav.scrollLeft) nav.scrollLeft = next;
}

export function ScrollCurrentTab() {
  const marker = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const nav = marker.current?.closest("nav");
    if (nav) bringCurrentTabIntoView(nav);
  }, []);
  return <span ref={marker} hidden />;
}
