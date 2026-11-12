// PageLoading (Anand, 12 Nov 2026): what a page shows while its content is built on the server. The pages whose
// content depends on the URL's query string (a meeting's tab, a company's tab, the screener's filters) can't be
// prerendered whole: the frame arrives at once and the body streams in a moment later, longer when the server
// has been idle. Until then this holds the body's place: a row of bars that light up in turn, like a tape
// ticking, and the page's own "Loading …" sentence with a terminal cursor.
//
// Built from the tokens and the animation utilities the site already ships (Tailwind's `animate-pulse`, and
// tw-animate-css's `animate-caret-blink` and `delay-*`): no new colour, keyframe or dependency. The bars are grey
// and white, never green or red (those mean a market move: CLAUDE.md §5). globals.css stops every animation for
// visitors who ask for reduced motion, so they see the bars still and the sentence. The sentence sits in a
// `role="status"` region, so a screen reader announces it; the bars and the cursor are decoration.
import { cn } from "cn";
import type { ReactNode } from "react";

// Each bar's height and when its pulse starts (ms after the one before it): a wave that runs left to right.
const BARS = [
  ["h-3", "delay-0"],
  ["h-5", "delay-100"],
  ["h-4", "delay-200"],
  ["h-7", "delay-300"],
  ["h-6", "delay-400"],
  ["h-9", "delay-500"],
  ["h-5", "delay-600"],
  ["h-8", "delay-700"],
  ["h-4", "delay-800"],
  ["h-6", "delay-900"],
  ["h-3", "delay-1000"],
  ["h-5", "delay-1100"],
] as const;

export function PageLoading({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      data-loading
      className="flex min-h-80 flex-col items-center justify-center gap-4 rounded-md border border-line-strong bg-panel p-6"
    >
      <div aria-hidden className="flex h-9 items-end gap-1">
        {BARS.map(([height, delay], i) => (
          <span key={i} className={cn("w-1.5 animate-pulse rounded-sm", i % 3 === 2 ? "bg-highlight" : "bg-ink-3", height, delay)} />
        ))}
      </div>
      <p className="flex items-center gap-1.5 font-mono text-meta text-ink-2">
        <span>{children}</span>
        <span aria-hidden className="h-3.5 w-1.5 animate-caret-blink bg-highlight" />
      </p>
    </div>
  );
}
