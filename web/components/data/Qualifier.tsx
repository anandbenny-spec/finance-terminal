// Qualifier (Design 01 §4.3): a small dashed tag for a correct but derived or approximate figure ("monthly",
// "AI-extracted", "proxy", …), in sentence case. The sentence explaining it goes in the tooltip of the item it
// tags (the tape's, a Stat's): the tag never stands in for the explanation.
import { cn } from "cn";
import type { ReactNode } from "react";

export function Qualifier({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block rounded-sm border border-dashed border-line-strong px-1.5 font-sans text-meta whitespace-nowrap text-ink-2",
        className,
      )}
    >
      {children}
    </span>
  );
}
