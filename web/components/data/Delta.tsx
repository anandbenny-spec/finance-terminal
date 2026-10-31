// Delta (Design 01 §4.3): a market change with its sign, a triangle and its colour; flat has no triangle. Colour is
// the third cue, never the only one (green and red are nearly identical under deuteranopia: Design 01 §8.2).
// Market moves only get colour; tone="neutral" keeps the sign and the triangle in the ink colour for policy and
// forecast changes (Anand, 1 Nov 2026). DecisionLine never uses it. The triangle is an inline SVG: no font served
// carries ▲▼ in the subsets used (Design 01 §1.3).
import { cn } from "cn";
import type { Change } from "@/lib/delta";

const COLOUR = { up: "text-up", down: "text-down", flat: "text-flat" } as const;

export function Delta({ change, tone = "market", className }: { change: Change; tone?: "market" | "neutral"; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap", tone === "neutral" ? "text-ink-1" : COLOUR[change.direction], className)}>
      {change.direction !== "flat" && (
        <svg
          viewBox="0 0 8 8"
          aria-hidden
          className={cn("size-1.5 shrink-0 fill-current", change.direction === "down" && "rotate-180")}
        >
          <path d="M4 0.5 7.6 7.5H0.4Z" />
        </svg>
      )}
      <span aria-hidden>{change.text}</span>
      <span className="sr-only">{change.spoken}</span>
    </span>
  );
}
