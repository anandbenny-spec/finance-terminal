// DecisionLine (Design 01 §4.5): "Cut 25 bps to 5.25%" · "Held at 4.00–4.25%". Words and numbers only, ink-1: no
// colour and no triangle, since a policy decision isn't a market move (Design 01 §1.2). The text comes from
// lib/policy/decisions.ts, its numbers from lib/format.ts.
import { cn } from "cn";

export function DecisionLine({ text, className }: { text: string; className?: string }) {
  return <p className={cn("text-read font-semibold text-ink-1", className)}>{text}</p>;
}
