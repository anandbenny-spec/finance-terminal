"use client";

// The browser's clock for countdowns, to the minute (Design 01 §3: whole days, no ticking seconds). The server
// can't know the visitor's clock, so it renders without one (null) and the browser fills it in after first render,
// re-checking a few times a minute and when the tab comes back into view.
import { useSyncExternalStore } from "react";

const MINUTE = 60_000;

function subscribe(onChange: () => void) {
  const timer = setInterval(onChange, 15_000);
  const onVisible = () => document.visibilityState === "visible" && onChange();
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
  };
}

const clientMinute = () => Math.floor(Date.now() / MINUTE) * MINUTE;
const serverMinute = () => null;

/** The current minute as epoch ms in the browser; null on the server and in the first render. */
export function useMinute(): number | null {
  return useSyncExternalStore(subscribe, clientMinute, serverMinute);
}
